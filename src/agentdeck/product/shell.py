"""Foreground, deterministic ProductSession conversation shell."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Final

from agentdeck.application.exit_service import ExitResult, ExitService
from agentdeck.application.session_service import SessionService, SessionServiceError
from agentdeck.application.mission_service import (
    MissionPreviewView,
    MissionResult,
    MissionService,
    MissionServiceError,
)
from agentdeck.product.presenter import (
    DiagnosisPresentation,
    ExitPresentation,
    MissionPreviewPresentation,
    SetupPresentation,
    StatusPresentation,
)
from agentdeck.product.renderer import render
from agentdeck.product.slash_commands import CommandKind, SlashCommand, parse_command


_DEFAULT_PERMISSION: Final = "approve-for-me"
_PERMISSIONS: Final = frozenset(
    {"ask-for-approval", "approve-for-me", "full-access"}
)
_HELP = """AgentDeck commands
/help
/status
/setup [confirm]
Select Leader with /leader <name>.
Select Model with /model <name>.
/agents
Select Permission with /permissions <profile>.
/mission
/pause
/resume
/takeover <attempt>
/diagnose [--json]
Exit safely with /exit."""


class ProductShell:
    """Own foreground input/output while Application owns session mutations."""

    def __init__(
        self,
        *,
        session_service: SessionService,
        exit_service: ExitService,
        available_leaders: Mapping[str, tuple[str, ...]],
        read_line: Callable[[str], str],
        write_line: Callable[[str], object],
        close: Callable[[], object],
        mission_service: MissionService | None = None,
        restored_exit: ExitResult | None = None,
        default_permission: str = _DEFAULT_PERMISSION,
        render_text: Callable[[object], str] = render,
    ) -> None:
        for dependency, method, label in (
            (session_service, "accept_text", "session_service"),
            (session_service, "configure", "session_service"),
            (session_service, "resume", "session_service"),
            (session_service, "current", "session_service"),
        ):
            if not callable(getattr(dependency, method, None)):
                raise TypeError(f"{label} does not satisfy the Product Shell")
        if any(
            not callable(getattr(exit_service, method, None))
            for method in ("request_exit", "decline", "confirm", "input_closed")
        ):
            raise TypeError("exit_service does not satisfy the Product Shell")
        if restored_exit is not None and type(restored_exit) is not ExitResult:
            raise TypeError("restored_exit must be an ExitResult or None")
        for dependency, label in (
            (read_line, "read_line"),
            (write_line, "write_line"),
            (close, "close"),
            (render_text, "render_text"),
        ):
            if not callable(dependency):
                raise TypeError(f"{label} must be callable")
        self._available_leaders = _copy_available_leaders(available_leaders)
        if default_permission not in _PERMISSIONS:
            raise ValueError("default_permission is unsupported")
        self._service = session_service
        self._exit = exit_service
        self._restored_exit = restored_exit
        if mission_service is not None and any(
            not callable(getattr(mission_service, method, None))
            for method in ("propose", "revise", "confirm", "current_preview")
        ):
            raise TypeError("mission_service does not satisfy the Product Shell")
        self._mission = mission_service
        self._read_line = read_line
        self._write_line = write_line
        self._close = close
        self._render = render_text
        view = self._service.current()
        self._leader = view.leader_backend
        self._model = view.model
        self._permission = (
            view.permission.replace("_", "-")
            if view.permission is not None
            else default_permission
        )
        self._closed = False

    def run(self) -> int:
        """Run until explicit exit or input EOF, then release the foreground Store."""

        try:
            self._show_initial_state()
            while True:
                try:
                    text = self._read_line("agentdeck> ")
                except KeyboardInterrupt:
                    if self._show_exit_result(self._exit.request_exit()):
                        break
                    continue
                except EOFError:
                    result = self._exit.input_closed()
                    if result.diagnostic is not None:
                        self._emit(self._render(DiagnosisPresentation(
                            result.diagnostic
                        )))
                    break
                if type(text) is not str:
                    self._emit("Input was not accepted. Use /help.")
                    continue
                if self._accept_line(text):
                    break
        finally:
            self._close_once()
        return 0

    def _accept_line(self, text: str) -> bool:
        command = parse_command(text)
        if command is not None:
            return self._accept_command(command)
        if text.lstrip().startswith("/"):
            self._emit("Command not recognized. Use /help.")
            return False
        confirmation = _confirmation(text)
        if confirmation is not None:
            if self._mission is None:
                self._emit("No Mission Preview is available for confirmation.")
            else:
                self._show_mission_result(self._mission.confirm(*confirmation))
            return False
        if self._mission is not None and self._service.current().state is not None:
            state = self._service.resume().mode
            if state in {"ready", "goal_ready", "awaiting_confirmation"}:
                try:
                    operation = (
                        self._mission.revise
                        if self._mission.current_preview() is not None
                        else self._mission.propose
                    )
                    self._show_mission_result(operation(text))
                except (MissionServiceError, TypeError, ValueError):
                    self._emit("The Mission request could not be applied safely.")
                return False
        try:
            result = self._service.accept_text(text)
        except (SessionServiceError, TypeError, ValueError):
            self._emit("The request could not be applied safely.")
            return False
        if result.mode == "setup_required":
            self._emit("I saved your goal while setup completes.")
        else:
            self._emit("The ProductSession cannot accept a new goal in this state.")
        return False

    def _accept_command(self, command: SlashCommand) -> bool:
        kind = command.kind
        if kind is CommandKind.HELP:
            self._emit(_HELP)
        elif kind is CommandKind.STATUS:
            self._show_status()
        elif kind is CommandKind.SETUP:
            if command.argument == "confirm":
                self._confirm_setup()
            else:
                self._show_setup()
        elif kind is CommandKind.LEADER:
            self._leader = command.argument
            self._emit("Leader selection staged.")
        elif kind is CommandKind.MODEL:
            self._model = command.argument
            self._emit("Model selection staged.")
        elif kind is CommandKind.PERMISSIONS:
            self._permission = command.argument or _DEFAULT_PERMISSION
            self._emit("Permission selection staged.")
        elif kind is CommandKind.AGENTS:
            self._emit("No Agent Instances are active.")
        elif kind is CommandKind.MISSION:
            self._emit("No Mission is active.")
        elif kind is CommandKind.DIAGNOSE:
            self._emit("No ProductSession diagnostic is active.")
        elif kind is CommandKind.EXIT:
            if command.argument is None:
                result = self._exit.request_exit()
            elif command.argument == "confirm":
                result = self._exit.confirm(
                    command.request_id, command.content_hash
                )
            else:
                result = self._exit.decline(
                    command.request_id, command.content_hash
                )
            return self._show_exit_result(result)
        else:
            self._emit("No active Mission can accept that command.")
        return False

    def _confirm_setup(self) -> None:
        if self._leader is None or self._model is None:
            self._emit("Setup is incomplete. Select a Leader and model.")
            self._show_setup()
            return
        try:
            result = self._service.configure(
                leader=self._leader,
                model=self._model,
                permission=self._permission.replace("-", "_"),
            )
        except (SessionServiceError, TypeError, ValueError):
            self._emit("Setup could not be applied safely.")
            return
        if not result.accepted:
            if result.diagnostic is None:
                self._emit("Setup could not be applied safely.")
            else:
                self._emit(self._render(DiagnosisPresentation(result.diagnostic)))
            return
        resumed = self._service.resume()
        if resumed.goal is None:
            self._emit("AgentDeck setup is ready.")
        else:
            self._show_resumed_goal(resumed.goal)
            if self._mission is not None:
                try:
                    self._show_mission_result(self._mission.propose(resumed.goal))
                except (MissionServiceError, TypeError, ValueError):
                    self._emit("The retained goal could not become a Mission safely.")

    def _show_resumed_goal(self, goal: str) -> None:
        try:
            self._render(SetupPresentation(
                project=goal,
                leaders=tuple(self._available_leaders),
                permission=self._permission,
            ))
        except (TypeError, ValueError):
            self._emit("Goal ready. The retained goal is not displayed.")
            return
        self._emit(f"Goal ready: {goal}")

    def _show_initial_state(self) -> None:
        view = self._service.current()
        if view.reentry_diagnostic is not None:
            self._emit(self._render(DiagnosisPresentation(
                view.reentry_diagnostic
            )))
        if self._restored_exit is not None:
            self._show_exit_result(self._restored_exit)
        preview = None if self._mission is None else self._mission.current_preview()
        if preview is not None:
            self._emit(self._render(_preview_presentation(preview)))
        elif view.state.value == "setup":
            self._show_setup()
        else:
            self._show_status()

    def _show_exit_result(self, result: ExitResult) -> bool:
        if type(result) is not ExitResult:
            raise TypeError("ExitService returned an invalid result")
        if result.diagnostic is not None:
            self._emit(self._render(DiagnosisPresentation(result.diagnostic)))
            return False
        if result.request is not None:
            request = result.request
            self._emit(self._render(ExitPresentation(
                summary="The active Attempt must be interrupted before exit.",
                active_attempts=(request.attempt.attempt_id,),
                requires_confirmation=True,
                request_id=request.request_id,
                attempt_hash=request.attempt_hash,
            )))
            return False
        if result.mode == "exit_declined":
            self._emit("Exit request declined. The active Attempt continues.")
            return False
        if result.mode == "exit_ready" and result.should_exit:
            self._emit(self._render(ExitPresentation(
                summary="The ProductSession is persisted.",
                active_attempts=(),
                requires_confirmation=False,
            )))
            return True
        raise ValueError("ExitService returned an unsupported result")

    def _show_setup(self) -> None:
        view = self._service.current()
        self._emit(self._render(SetupPresentation(
            project=view.project_root,
            leaders=tuple(self._available_leaders),
            permission=self._permission,
        )))

    def _show_status(self) -> None:
        view = self._service.current()
        self._emit(self._render(StatusPresentation(
            state=view.state.value,
            agents=(),
        )))

    def _emit(self, text: str) -> None:
        if type(text) is not str:
            raise TypeError("Product Shell output must be text")
        self._write_line(text)

    def _show_mission_result(self, result: MissionResult) -> None:
        self._service.resume()
        if result.preview is not None:
            self._emit(self._render(_preview_presentation(result.preview)))
        elif result.mission is not None:
            self._emit(
                f"Mission confirmed: {result.mission.mission_id} "
                f"v{result.mission.version}."
            )
        elif result.diagnostic is not None:
            self._emit(self._render(DiagnosisPresentation(result.diagnostic)))
        else:
            self._emit("The Mission request could not be applied safely.")

    def _close_once(self) -> None:
        if not self._closed:
            self._closed = True
            self._close()


def _copy_available_leaders(
    value: Mapping[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    if type(value) is not dict:
        raise TypeError("available_leaders must be a plain mapping")
    copied: dict[str, tuple[str, ...]] = {}
    for leader, models in value.items():
        if (
            type(leader) is not str
            or not leader.strip()
            or type(models) is not tuple
            or not models
            or any(type(model) is not str or not model.strip() for model in models)
        ):
            raise ValueError("available_leaders is invalid")
        copied[leader] = tuple(models)
    return dict(sorted(copied.items()))


def _confirmation(text: str) -> tuple[str, str] | None:
    if type(text) is not str:
        return None
    parts = text.strip().split()
    if len(parts) == 3 and parts[0].casefold() == "confirm":
        return parts[1], parts[2]
    return None


def _preview_presentation(value: MissionPreviewView) -> MissionPreviewPresentation:
    draft, preview = value.draft, value.preview
    budgets = dict(draft.budgets)
    return MissionPreviewPresentation(
        objective=draft.objective, scope=draft.scope,
        leader_backend=draft.leader_backend, leader_model=draft.leader_model,
        workers=tuple(
            f"{task.agent_instance_id}: {task.role.value} via {task.backend}"
            for task in draft.tasks
        ),
        tasks=tuple(task.name for task in draft.tasks),
        task_dependencies=tuple(
            f"{task.name}: {', '.join(task.dependencies) if task.dependencies else 'none'}"
            for task in draft.tasks
        ),
        acp_routes=tuple(task.acp_route for task in draft.tasks),
        permission=draft.permission_profile.value.replace("_", "-"),
        project_boundary=draft.project_root,
        acceptance_criteria=draft.acceptance_criteria,
        retry_budget=budgets["max_attempts"],
        revision_budget=budgets["max_revision_cycles"],
        non_goals=draft.non_goals, risks=draft.risks,
        preview_id=preview.preview_id, version=preview.version,
        content_hash=preview.content_hash,
        leader_adapter=draft.leader_adapter, leader_version=draft.leader_version,
        additional_budgets=(
            f"Leader schema repairs: {budgets['max_leader_schema_repairs']}",
            f"ACP reconnects: {budgets['max_acp_reconnects']}",
            f"Final acceptance attempts: {budgets['max_final_acceptance_attempts']}",
        ),
    )


def validate_mission_preview(value: MissionPreviewView) -> None:
    """Require the complete Preview to pass the same human renderer pre-write."""

    if type(value) is not MissionPreviewView:
        raise TypeError("Preview validator requires MissionPreviewView")
    text = render(_preview_presentation(value))
    if len(text.encode("utf-8", "strict")) > 65_536:
        raise ValueError("rendered Mission Preview exceeds its human display bound")


__all__ = ["ProductShell", "validate_mission_preview"]
