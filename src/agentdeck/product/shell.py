"""Foreground, deterministic ProductSession conversation shell."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Final

from agentdeck.application.session_service import SessionService, SessionServiceError
from agentdeck.product.presenter import (
    DiagnosisPresentation,
    ExitPresentation,
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
        available_leaders: Mapping[str, tuple[str, ...]],
        read_line: Callable[[str], str],
        write_line: Callable[[str], object],
        close: Callable[[], object],
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
                except EOFError:
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
            self._emit(self._render(ExitPresentation(
                summary="The ProductSession is persisted.",
                active_attempts=(),
                requires_confirmation=False,
            )))
            return True
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
        if self._service.current().state.value == "setup":
            self._show_setup()
        else:
            self._show_status()

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


__all__ = ["ProductShell"]
