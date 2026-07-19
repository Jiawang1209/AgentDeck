from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentdeck.adapters.discovery import ReadinessState, ToolDiscovery
from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.exit_service import ExitService
from agentdeck.application.session_service import SessionService
from agentdeck.product.shell import ProductShell
from agentdeck.product.bootstrap import build_product_shell
from agentdeck.product.slash_commands import CommandKind

from .fakes import FrozenClock


NOW = datetime(2026, 7, 19, 8, 9, 10, tzinfo=timezone.utc)
AVAILABLE_LEADERS = {
    "codex-cli": ("native-default",),
    "claude-cli": ("native-default",),
}


class ShellHarness:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.output: list[str] = []

    def run(self, lines: list[str]) -> str:
        pending = iter(lines)
        store = SQLiteStore.open(self.root, clock=FrozenClock(NOW))
        service = SessionService(
            store=store,
            clock=FrozenClock(NOW),
            session_id="ses_shell",
            project_root=str(self.root),
            available_leaders=AVAILABLE_LEADERS,
        )
        exit_service = ExitService(
            store=store,
            clock=FrozenClock(NOW),
            session_id="ses_shell",
            request_id_factory=iter(("xrt_" + "1" * 32,)).__next__,
        )

        def read_line(prompt: str) -> str:
            assert prompt == "agentdeck> "
            try:
                return next(pending)
            except StopIteration:
                raise EOFError from None

        shell = ProductShell(
            session_service=service,
            exit_service=exit_service,
            available_leaders=AVAILABLE_LEADERS,
            read_line=read_line,
            write_line=self.output.append,
            close=store.close,
        )
        assert shell.run() == 0
        return "\n".join(self.output)

    def restored(self) -> tuple[SessionService, SQLiteStore]:
        store = SQLiteStore.open(self.root, clock=FrozenClock(NOW))
        return (
            SessionService(
                store=store,
                clock=FrozenClock(NOW),
                session_id="ses_shell",
                project_root=str(self.root),
                available_leaders=AVAILABLE_LEADERS,
            ),
            store,
        )


def test_product_shell_requires_explicit_exit_service(tmp_path: Path) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    service = SessionService(
        store=store,
        clock=FrozenClock(NOW),
        session_id="ses_explicit_exit",
        project_root=str(tmp_path),
        available_leaders=AVAILABLE_LEADERS,
    )
    try:
        with pytest.raises(TypeError, match="exit_service"):
            ProductShell(
                session_service=service,
                available_leaders=AVAILABLE_LEADERS,
                read_line=lambda _: "/exit",
                write_line=lambda _: None,
                close=store.close,
            )
    finally:
        store.close()


def test_first_run_shell_retains_goal_and_resumes_after_setup(
    tmp_path: Path,
) -> None:
    harness = ShellHarness(tmp_path)

    transcript = harness.run([
        "Build an accessible page",
        "/leader codex-cli",
        "/model native-default",
        "/permissions approve-for-me",
        "/setup confirm",
        "/exit",
    ])

    assert "I saved your goal while setup completes." in transcript
    assert "Goal ready: Build an accessible page" in transcript
    assert "Session is safe to exit." in transcript
    assert "{" not in transcript
    restored, store = harness.restored()
    try:
        assert restored.resume().goal == "Build an accessible page"
        assert restored.current().leader_backend == "codex-cli"
        assert restored.current().model == "native-default"
        assert restored.current().permission == "approve_for_me"
    finally:
        store.close()


def test_help_status_and_setup_work_without_llm(tmp_path: Path) -> None:
    harness = ShellHarness(tmp_path)

    transcript = harness.run(["/help", "/status", "/setup", "/exit"])

    assert "Select Leader" in transcript
    assert "AgentDeck is setup.\nAgents: none" in transcript
    assert "AgentDeck setup" in transcript
    assert "Choose a Leader and model, then confirm setup." in transcript
    assert all(f"/{kind.value}" in transcript for kind in CommandKind)
    assert "{" not in transcript


def test_every_declared_control_is_handled_without_becoming_a_goal(
    tmp_path: Path,
) -> None:
    harness = ShellHarness(tmp_path)

    transcript = harness.run([
        "/agents",
        "/mission",
        "/pause",
        "/resume",
        "/takeover att_1",
        "/diagnose",
        "/diagnose --json",
        "/unknown",
        "/exit",
    ])

    assert "No Agent Instances are active." in transcript
    assert "No Mission is active." in transcript
    assert "No ProductSession diagnostic is active." in transcript
    assert "Command not recognized. Use /help." in transcript
    assert "{" not in transcript
    restored, store = harness.restored()
    try:
        assert restored.current().pending_goal is None
        assert restored.current().state.value == "setup"
    finally:
        store.close()


def test_rejected_setup_renders_a_diagnostic_without_selecting_a_fallback(
    tmp_path: Path,
) -> None:
    harness = ShellHarness(tmp_path)

    transcript = harness.run([
        "Build the page",
        "/leader api:deepseek",
        "/model deepseek-chat",
        "/setup confirm",
        "/exit",
    ])

    assert "Diagnosis leader_credential_unavailable [error]" in transcript
    assert "No fallback" not in transcript
    assert "{" not in transcript
    restored, store = harness.restored()
    try:
        assert restored.current().leader_backend is None
        assert restored.current().model is None
        assert restored.current().state.value == "setup"
    finally:
        store.close()


@pytest.mark.parametrize(
    ("goal", "marker"),
    (
        ('{"task":"RAW-JSON-GOAL"}', "RAW-JSON-GOAL"),
        ("Build RAW-CONTROL-GOAL\x1b[31m", "RAW-CONTROL-GOAL"),
        ("RAW-OVERSIZE-GOAL-" + "x" * 2_100, "RAW-OVERSIZE-GOAL"),
    ),
)
def test_setup_resume_never_renders_an_unsafe_retained_goal(
    tmp_path: Path, goal: str, marker: str,
) -> None:
    harness = ShellHarness(tmp_path)

    transcript = harness.run([
        goal,
        "/leader codex-cli",
        "/model native-default",
        "/setup confirm",
        "/exit",
    ])

    assert "Goal ready. The retained goal is not displayed." in transcript
    assert marker not in transcript
    assert "{" not in transcript
    assert "\x1b" not in transcript
    restored, store = harness.restored()
    try:
        assert restored.resume().goal == goal
        assert restored.current().state.value == "ready"
    finally:
        store.close()


def test_bootstrap_uses_injected_factories_without_terminal_provider_or_tmux(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    output: list[str] = []
    pending = iter(("/setup", "/exit"))

    def clock_factory() -> FrozenClock:
        calls.append("clock")
        return FrozenClock(NOW)

    def discovery_factory():
        calls.append("discovery")
        return {
            "codex": ToolDiscovery(
                name="codex",
                command="codex",
                resolved_path="/tools/codex",
                version="codex 1.0",
                authenticated=True,
                acp_available=True,
                readiness=ReadinessState.READY,
                capabilities=("leader", "worker", "acp"),
            )
        }

    def config_factory(**layers):
        calls.append("config")
        assert layers["discovered"] == {"permission": "approve-for-me"}
        assert layers["global_values"] == {}
        assert layers["project_values"] == {}
        assert layers["session_values"] == {}
        return SimpleNamespace(
            resolve=lambda key: SimpleNamespace(value="approve-for-me")
        )

    def store_factory(root: str, *, clock: FrozenClock) -> SQLiteStore:
        calls.append("store")
        assert root == str(tmp_path)
        return SQLiteStore.open(root, clock=clock)

    def shell_factory(**values) -> ProductShell:
        calls.append("shell")
        return ProductShell(**values)

    shell = build_product_shell(
        project_root=str(tmp_path),
        read_line=lambda _: next(pending),
        write_line=output.append,
        clock_factory=clock_factory,
        discovery_factory=discovery_factory,
        config_factory=config_factory,
        store_factory=store_factory,
        shell_factory=shell_factory,
    )

    assert shell.run() == 0
    assert calls == ["clock", "discovery", "config", "store", "shell"]
    assert any("Leaders: codex-cli" in item for item in output)
    assert all("{" not in item for item in output)
