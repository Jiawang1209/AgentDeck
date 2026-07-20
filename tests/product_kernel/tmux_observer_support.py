from __future__ import annotations

import importlib
from typing import Any

import pytest


ROLES = ("implementer", "reviewer", "reviser", "acceptance_reviewer")


def runtime_api() -> tuple[Any, Any]:
    try:
        port = importlib.import_module("agentdeck.ports.runtime")
        adapter = importlib.import_module("agentdeck.adapters.tmux_observer")
    except ModuleNotFoundError:
        pytest.fail("Task 27 Observer Runtime modules are missing", pytrace=False)
    return port, adapter


def four_instances() -> tuple[Any, ...]:
    port, _ = runtime_api()
    return tuple(
        port.ObserverInstance(
            instance_id=f"agt_{role}", session_id=f"ses_{role}", role=role,
        )
        for role in ROLES
    )


class RecordingRunner:
    def __init__(self, *, fail_at: int | None = None, returncode: int = 0) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fail_at = fail_at
        self.returncode = returncode

    def __call__(self, argv: tuple[str, ...]) -> object:
        assert type(argv) is tuple
        assert all(type(argument) is str for argument in argv)
        self.calls.append(argv)
        if self.fail_at == len(self.calls):
            raise RuntimeError("secret task output")
        return type("Result", (), {"returncode": self.returncode})()


class StatefulWorkspaceRunner:
    def __init__(self, *, initial: str | None = None, cleanup_fails: bool = False) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.exists = initial is not None
        self.initial = initial
        self.cleanup_fails = cleanup_fails
        self.later_failure_pending = initial is None

    def __call__(self, argv: tuple[str, ...]) -> object:
        self.calls.append(argv)
        if "new-session" in argv:
            if self.initial == "raise":
                raise RuntimeError("pre-existing secret")
            if self.initial == "nonzero" or self.exists:
                return type("Result", (), {"returncode": 7})()
            self.exists = True
        elif "kill-session" in argv:
            if self.cleanup_fails:
                raise RuntimeError("cleanup secret")
            self.exists = False
        elif self.later_failure_pending:
            self.later_failure_pending = False
            return type("Result", (), {"returncode": 9})()
        return type("Result", (), {"returncode": 0})()


def observer(runner: object | None = None) -> Any:
    _, adapter = runtime_api()
    return adapter.TmuxObserver(runner=runner or RecordingRunner())
