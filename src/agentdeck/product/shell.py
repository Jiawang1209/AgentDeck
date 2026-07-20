"""Single-loop foreground ProductSession conversation shell."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import signal
from typing import Final

from agentdeck.application.async_exit_coordinator import AsyncExitCoordinator
from agentdeck.application.execution_resume import (
    ExecutionResumePlan, ExecutionResumePlanner,
    ExecutionResumeProjectionError, ExecutionResumeSnapshot,
)
from agentdeck.application.execution_service import ExecutionService
from agentdeck.application.exit_records import ExitResult
from agentdeck.application.mission_service import (
    MissionResult, MissionService, MissionServiceError,
)
from agentdeck.application.project_lifecycle_service import ProjectLifecycleService
from agentdeck.application.recovery_service import RecoveryService
from agentdeck.application.session_service import SessionService, SessionServiceError
from agentdeck.kernel.permissions import PermissionScope
from agentdeck.kernel.session import SessionState
from agentdeck.product.presenter import (
    DiagnosisPresentation, ExitPresentation, SetupPresentation, StatusPresentation,
)
from agentdeck.product.renderer import render
from agentdeck.product.shell_async import (
    AsyncTerminalReader,
    collect_iteration_tasks,
    settle_owned_after_caller_cancel,
)
from agentdeck.product.shell_projection import (
    EXECUTION_ADAPTER_UNAVAILABLE,
    HELP_TEXT,
    confirmation_authority,
    copy_available_leaders,
    display_permission,
    is_supported_permission,
    preview_presentation,
    resume_point_text,
    validate_mission_preview,
)
from agentdeck.product.slash_commands import CommandKind, SlashCommand, parse_command


_DEFAULT_PERMISSION: Final = "approve-for-me"


class ProductShell:
    """Own input and rendering; Application services own every mutation."""

    def __init__(
        self, *, session_service: SessionService,
        exit_coordinator: AsyncExitCoordinator,
        recovery_service: RecoveryService,
        lifecycle: ProjectLifecycleService,
        available_leaders: Mapping[str, tuple[str, ...]],
        read_line: Callable[[str], Awaitable[str]],
        write_line: Callable[[str], object], close: Callable[[], object],
        mission_service: MissionService | None = None,
        execution_service: ExecutionService | None = None,
        resume_snapshot_loader: Callable[[], ExecutionResumeSnapshot] | None = None,
        resume_planner: ExecutionResumePlanner | None = None,
        default_permission: str = _DEFAULT_PERMISSION,
        render_text: Callable[[object], str] = render,
    ) -> None:
        for dependency, methods, label in (
            (session_service, ("accept_text", "configure", "resume", "current"),
             "session_service"),
            (exit_coordinator, ("request_exit", "decline", "confirm", "input_closed"),
             "exit_coordinator"),
            (recovery_service, ("reconcile",), "recovery_service"),
            (lifecycle, ("resume",), "lifecycle"),
        ):
            if any(not callable(getattr(dependency, name, None)) for name in methods):
                raise TypeError(f"{label} does not satisfy the Product Shell")
        if mission_service is not None and any(
            not callable(getattr(mission_service, name, None))
            for name in ("propose", "revise", "confirm", "current_preview")):
            raise TypeError("mission_service does not satisfy the Product Shell")
        if execution_service is not None and not callable(getattr(
            execution_service, "run_confirmed_mission", None)):
            raise TypeError("execution_service does not satisfy the Product Shell")
        for dependency, label in (
            (read_line, "read_line"), (write_line, "write_line"),
            (close, "close"), (render_text, "render_text")):
            if not callable(dependency):
                raise TypeError(f"{label} must be callable")
        if resume_snapshot_loader is not None and not callable(resume_snapshot_loader):
            raise TypeError("resume_snapshot_loader must be callable or None")
        if not is_supported_permission(default_permission):
            raise ValueError("default_permission is unsupported")
        self._service, self._exit = session_service, exit_coordinator
        self._recovery, self._lifecycle = recovery_service, lifecycle
        self._available_leaders = copy_available_leaders(available_leaders)
        self._read_line, self._write_line = read_line, write_line
        self._close, self._render = close, render_text
        self._mission, self._execution = mission_service, execution_service
        self._load_resume = resume_snapshot_loader
        self._resume_planner = resume_planner or ExecutionResumePlanner()
        view = self._service.current()
        self._leader, self._model = view.leader_backend, view.model
        self._permission = display_permission(view.permission, default_permission)
        self._resume_snapshot: ExecutionResumeSnapshot | None = None
        self._resume_plan: ExecutionResumePlan | None = None
        self._resume_blocker: str | None = None
        self._mission_child: asyncio.Task | None = None
        self._closed = False

    async def run_async(self) -> int:
        loop = asyncio.get_running_loop()
        signal_installed = False
        caller_cancelled: asyncio.CancelledError | None = None
        interrupted = asyncio.Event()
        try:
            await self._recovery.reconcile()
            self._service.resume()
            self._restore_resume_projection()
            self._show_initial_state()
            loop.add_signal_handler(signal.SIGINT, interrupted.set)
            signal_installed = True
            while True:
                read_task = asyncio.create_task(self._read_line("agentdeck> "))
                signal_task = asyncio.create_task(interrupted.wait())
                iteration_cancelled: asyncio.CancelledError | None = None
                try:
                    done, _ = await asyncio.wait(
                        (read_task, signal_task),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if signal_task in done:
                        read_task.cancel()
                        interrupted.clear()
                        if await self._present_exit(await self._exit.request_exit()):
                            await self._finish_child_for_exit()
                            return 0
                        continue
                    signal_task.cancel()
                    try:
                        line = read_task.result()
                    except EOFError:
                        return await self._handle_input_closed()
                    if type(line) is not str:
                        self._emit("Input was not accepted. Use /help.")
                        continue
                    if await self._accept_line(line):
                        await self._finish_child_for_exit()
                        return 0
                except asyncio.CancelledError as error:
                    iteration_cancelled = error
                    raise
                finally:
                    await collect_iteration_tasks(
                        read_task, signal_task,
                        caller_cancelled=iteration_cancelled,
                    )
        except asyncio.CancelledError as error:
            caller_cancelled = error
            raise
        finally:
            try:
                if signal_installed:
                    loop.remove_signal_handler(signal.SIGINT)
                if caller_cancelled is not None:
                    await settle_owned_after_caller_cancel(
                        self._mission_child, caller_cancelled,
                    )
                else:
                    await self._settle_owned_child()
            finally:
                self._close_once()

    def _restore_resume_projection(self) -> None:
        self._resume_snapshot = self._resume_plan = None
        self._resume_blocker = None
        if self._service.current().state is not SessionState.PAUSED:
            return
        if self._load_resume is None:
            self._resume_blocker = "execution_adapter_unavailable"
            return
        try:
            snapshot = self._load_resume()
            plan = self._resume_planner.materialize(snapshot)
        except ExecutionResumeProjectionError as error:
            self._resume_blocker = error.code
        except (TypeError, ValueError, RuntimeError):
            self._resume_blocker = "resume_projection_malformed"
        else:
            self._resume_snapshot, self._resume_plan = snapshot, plan

    async def _accept_line(self, text: str) -> bool:
        command = parse_command(text)
        if command is not None:
            return await self._accept_command(command)
        if text.lstrip().startswith("/"):
            self._emit("Command not recognized. Use /help.")
            return False
        confirmation = confirmation_authority(text)
        if confirmation is not None:
            await self._confirm_mission(confirmation)
            return False
        if self._mission is not None:
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
        self._emit(
            "I saved your goal while setup completes."
            if result.mode == "setup_required"
            else "The ProductSession cannot accept a new goal in this state."
        )
        return False

    async def _accept_command(self, command: SlashCommand) -> bool:
        kind = command.kind
        if kind is CommandKind.EXIT:
            if command.argument is None:
                result = await self._exit.request_exit()
            elif command.argument == "confirm":
                result = await self._exit.confirm(
                    command.request_id, command.content_hash
                )
            else:
                result = await self._exit.decline(
                    command.request_id, command.content_hash
                )
            return await self._present_exit(result)
        if kind is CommandKind.RESUME:
            await self._resume_project()
            return False
        if kind is CommandKind.TAKEOVER:
            operation = None if self._execution is None else getattr(self._execution, "takeover", None)
            if not callable(operation):
                self._emit("No active Mission can accept takeover.")
            else:
                result = await operation(command.argument)
                self._emit(f"Human control recorded for Attempt {command.argument}." if result.accepted else self._render(DiagnosisPresentation(result.diagnostic)))
            return False
        if kind is CommandKind.HELP:
            self._emit(HELP_TEXT)
        elif kind is CommandKind.STATUS:
            self._show_status()
        elif kind is CommandKind.SETUP:
            self._confirm_setup() if command.argument == "confirm" else self._show_setup()
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
        else:
            self._emit("No active Mission can accept that command.")
        return False

    async def _confirm_mission(self, authority: tuple[str, str]) -> None:
        if self._mission is None:
            self._emit("No Mission Preview is available for confirmation.")
            return
        preview = self._mission.current_preview()
        result = self._mission.confirm(*authority)
        self._show_mission_result(result)
        confirmed = result.mission
        if preview is None or confirmed is None or (
            confirmed.version != preview.version
            or confirmed.content_hash != preview.content_hash
            or confirmed.canonical_content != preview.preview.canonical_content
        ):
            return
        await self._start_mission_child(
            confirmed, preview.draft,
            PermissionScope.for_profile(preview.draft.permission_profile), None,
        )

    async def _resume_project(self) -> None:
        view = self._service.resume()
        child = self._mission_child
        if view.mode == "running":
            self._emit(
                "The Mission is already running."
                if child is not None and not child.done()
                else "The project is already running without resumable authority."
            )
            return
        if self._service.current().state is not SessionState.PAUSED:
            self._emit("No paused Mission can be resumed.")
            return
        if self._resume_snapshot is None or self._resume_plan is None:
            self._emit(f"Resume blocked: {self._resume_blocker or 'resume_projection_malformed'}.")
            return
        if not self._execution_available():
            return
        try:
            result = await self._lifecycle.resume(self._resume_snapshot)
        except (TypeError, ValueError, RuntimeError):
            self._emit("Resume blocked: resume_authority_changed.")
            return
        if not result.should_start:
            self._emit("Resume blocked: resume_authority_changed.")
            return
        self._service.resume()
        plan = self._resume_plan
        await self._start_mission_child(
            plan.confirmed, plan.draft,
            PermissionScope.for_profile(plan.draft.permission_profile), plan,
        )

    async def _start_mission_child(
        self, confirmed, draft, permission_scope, resume_plan,
    ) -> None:
        if self._mission_child is not None and not self._mission_child.done():
            self._emit("The Mission is already running.")
            return
        if not self._execution_available():
            return
        self._mission_child = asyncio.create_task(
            self._execution.run_confirmed_mission(
                session_id=self._service.current().session_id,
                confirmed=confirmed, draft=draft,
                permission_scope=permission_scope, resume_plan=resume_plan,
            )
        )

    def _execution_available(self) -> bool:
        if self._execution is None:
            self._emit(EXECUTION_ADAPTER_UNAVAILABLE)
        return self._execution is not None

    async def _handle_input_closed(self) -> int:
        result = await self._exit.input_closed()
        should_exit = await self._present_exit(result)
        child = self._mission_child
        if child is not None and not child.done():
            await self._await_child(child)
            if not should_exit:
                await self._present_exit(await self._exit.input_closed())
        return 0

    async def _present_exit(self, result: ExitResult) -> bool:
        if type(result) is not ExitResult:
            raise TypeError("exit coordinator returned an invalid result")
        if result.diagnostic is not None:
            self._emit(self._render(DiagnosisPresentation(result.diagnostic)))
            return False
        if result.request is not None:
            request = result.request
            self._emit(self._render(ExitPresentation(
                summary="The active Attempt must be interrupted before exit.",
                active_attempts=(request.attempt.attempt_id,),
                requires_confirmation=True, request_id=request.request_id,
                attempt_hash=request.attempt_hash,
            )))
            return False
        if result.mode == "exit_declined":
            self._emit("Exit request declined. The active Attempt continues.")
            return False
        if result.mode in {"exit_ready", "project_paused"} and result.should_exit:
            self._emit(self._render(ExitPresentation(
                summary="The ProductSession is persisted.", active_attempts=(),
                requires_confirmation=False,
            )))
            return True
        raise ValueError("exit coordinator returned an unsupported result")

    async def _finish_child_for_exit(self) -> None:
        if self._mission_child is not None and not self._mission_child.done():
            await self._await_child(self._mission_child)

    async def _settle_owned_child(self) -> None:
        if self._mission_child is not None:
            await self._await_child(self._mission_child)

    async def _await_child(self, child: asyncio.Task) -> None:
        try:
            await child
        except asyncio.CancelledError:
            raise
        except Exception:
            self._emit("Mission execution stopped with a safe diagnostic.")

    def _confirm_setup(self) -> None:
        if self._leader is None or self._model is None:
            self._emit("Setup is incomplete. Select a Leader and model.")
            self._show_setup()
            return
        try:
            result = self._service.configure(
                leader=self._leader, model=self._model,
                permission=self._permission.replace("-", "_"),
            )
        except (SessionServiceError, TypeError, ValueError):
            self._emit("Setup could not be applied safely.")
            return
        if not result.accepted:
            self._emit(
                "Setup could not be applied safely."
                if result.diagnostic is None
                else self._render(DiagnosisPresentation(result.diagnostic))
            )
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
                project=goal, leaders=tuple(self._available_leaders),
                permission=self._permission,
            ))
        except (TypeError, ValueError):
            self._emit("Goal ready. The retained goal is not displayed.")
            return
        self._emit(f"Goal ready: {goal}")

    def _show_initial_state(self) -> None:
        view = self._service.current()
        if view.reentry_diagnostic is not None:
            self._emit(self._render(DiagnosisPresentation(view.reentry_diagnostic)))
        preview = None if self._mission is None else self._mission.current_preview()
        if preview is not None:
            self._emit(self._render(preview_presentation(preview)))
        elif view.state is SessionState.SETUP:
            self._show_setup()
        else:
            self._show_status()
        if self._resume_snapshot is not None and self._resume_plan is not None:
            self._emit(resume_point_text(self._resume_snapshot, self._resume_plan))
        elif self._resume_blocker is not None:
            self._emit(f"Resume blocked: {self._resume_blocker}.")

    def _show_setup(self) -> None:
        view = self._service.current()
        self._emit(self._render(SetupPresentation(
            project=view.project_root, leaders=tuple(self._available_leaders),
            permission=self._permission,
        )))

    def _show_status(self) -> None:
        view = self._service.current()
        self._emit(self._render(StatusPresentation(
            state=view.state.value, agents=(),
        )))

    def _show_mission_result(self, result: MissionResult) -> None:
        self._service.resume()
        if result.preview is not None:
            self._emit(self._render(preview_presentation(result.preview)))
        elif result.mission is not None:
            self._emit(
                f"Mission confirmed: {result.mission.mission_id} "
                f"v{result.mission.version}."
            )
        elif result.diagnostic is not None:
            self._emit(self._render(DiagnosisPresentation(result.diagnostic)))
        else:
            self._emit("The Mission request could not be applied safely.")

    def _emit(self, text: str) -> None:
        if type(text) is not str:
            raise TypeError("Product Shell output must be text")
        self._write_line(text)

    def _close_once(self) -> None:
        if not self._closed:
            self._closed = True
            self._close()


__all__ = ["AsyncTerminalReader", "ProductShell", "validate_mission_preview"]
