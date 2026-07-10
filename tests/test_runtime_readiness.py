from __future__ import annotations

from collections.abc import Callable
from math import inf, nan

import pytest

from agentdeck.models import AgentSpec, RuntimeConfig
from agentdeck.runtime.readiness import (
    WorkerReadinessBatch,
    classify_worker_readiness,
    wait_for_worker_readiness,
)


CODEX_READY_SCREEN = "\x1b[32mOpenAI Codex\x1b[0m  model: gpt-5\n› Ask Codex anything"
CODEX_STARTING_MCP_SCREEN = "OpenAI Codex\nStarting MCP servers (1/2)\n›"
CODEX_MODEL_INCOMPATIBLE_SCREEN = (
    "› old prompt in scrollback\nConfigured model requires a newer version of Codex"
)
CLAUDE_READY_SCREEN = "Claude Code\n❯ Try ‘review this file’\n92% context left"
CLAUDE_TRUST_SCREEN = (
    "❯ stale prompt\nDo you trust the files in this folder?\nYes, I trust this folder"
)
CLAUDE_LOGIN_SCREEN = "Claude Code\nNot logged in. Run /login to continue."


@pytest.mark.parametrize(
    ("provider", "output", "expected"),
    [
        (" Codex-CLI ", CODEX_READY_SCREEN, "ready"),
        ("CODEX", CODEX_STARTING_MCP_SCREEN, "starting"),
        ("codex", CODEX_MODEL_INCOMPATIBLE_SCREEN, "failed"),
        (" claude-cli ", CLAUDE_READY_SCREEN, "ready"),
        ("CLAUDE", CLAUDE_TRUST_SCREEN, "setup_required"),
        ("claude", CLAUDE_LOGIN_SCREEN, "setup_required"),
        ("deepseek", "› prompt-like marker", "failed"),
        ("codex", "", "starting"),
    ],
)
def test_classify_worker_readiness(provider: str, output: str, expected: str) -> None:
    evidence = classify_worker_readiness(provider, output)

    assert evidence.status == expected
    assert evidence.reason is None or len(evidence.reason) < 120


@pytest.mark.parametrize(
    ("provider", "screen", "expected"),
    [
        ("codex", CODEX_MODEL_INCOMPATIBLE_SCREEN, "failed"),
        ("claude", CLAUDE_TRUST_SCREEN, "setup_required"),
        (
            "claude",
            f"{CLAUDE_READY_SCREEN}\nAuthentication required. Run /login.",
            "setup_required",
        ),
        (
            "codex",
            f"{CODEX_READY_SCREEN}\nSTARTING MCP SERVERS",
            "starting",
        ),
    ],
)
def test_blockers_and_startup_take_priority_over_stale_ready_prompt(
    provider: str, screen: str, expected: str
) -> None:
    assert classify_worker_readiness(provider, screen).status == expected


class FakeBackend:
    def __init__(self) -> None:
        self.outputs: dict[str, list[str | Exception]] = {}
        self.exists: dict[str, list[bool | Exception]] = {}
        self.sent: list[tuple[str, str]] = []
        self.captured: list[str] = []
        self.checked: list[str] = []

    @staticmethod
    def _next(values: list[object], default: object) -> object:
        if not values:
            return default
        if len(values) == 1:
            return values[0]
        return values.pop(0)

    def pane_exists(self, _config: RuntimeConfig, pane_id: str) -> bool:
        self.checked.append(pane_id)
        value = self._next(self.exists.setdefault(pane_id, [True]), True)
        if isinstance(value, Exception):
            raise value
        return bool(value)

    def capture_output(
        self, _config: RuntimeConfig, pane_id: str, lines: int = 200
    ) -> str:
        assert lines == 200
        self.captured.append(pane_id)
        value = self._next(self.outputs.setdefault(pane_id, [""]), "")
        if isinstance(value, Exception):
            raise value
        return str(value)

    def send_input(self, _config: RuntimeConfig, pane_id: str, text: str) -> None:
        self.sent.append((pane_id, text))


@pytest.fixture
def runtime_config() -> RuntimeConfig:
    return RuntimeConfig(session_name="test", socket_name="test-socket")


@pytest.fixture
def codex_agent() -> AgentSpec:
    return AgentSpec("planner", "planning", "codex-cli", "codex")


@pytest.fixture
def claude_agent() -> AgentSpec:
    return AgentSpec("reviewer", "review", "claude", "claude")


def _wait(
    runtime_config: RuntimeConfig,
    backend: FakeBackend,
    selected: tuple[tuple[AgentSpec, str], ...],
    *,
    timeout_seconds: float = 30,
    poll_interval: float = 0,
    monotonic: Callable[[], float] = lambda: 0.0,
    sleeper: Callable[[float], None] = lambda _seconds: None,
) -> WorkerReadinessBatch:
    return wait_for_worker_readiness(
        runtime_config=runtime_config,
        backend=backend,
        selected=selected,
        timeout_seconds=timeout_seconds,
        poll_interval=poll_interval,
        monotonic=monotonic,
        sleeper=sleeper,
    )


def test_wait_for_selected_worker_polls_starting_until_ready(
    runtime_config: RuntimeConfig, codex_agent: AgentSpec
) -> None:
    backend = FakeBackend()
    backend.outputs["%1"] = [CODEX_STARTING_MCP_SCREEN, CODEX_READY_SCREEN]
    sleeps: list[float] = []

    result = _wait(
        runtime_config,
        backend,
        ((codex_agent, "%1"),),
        poll_interval=0.25,
        monotonic=iter((0.0, 0.1)).__next__,
        sleeper=sleeps.append,
    )

    assert result.all_ready is True
    assert result.timed_out is False
    assert [(item.agent_id, item.provider, item.status) for item in result.results] == [
        ("planner", "codex", "ready")
    ]
    assert sleeps == [0.25]
    assert backend.sent == []


def test_wait_returns_pane_lost_without_capture_or_send(
    runtime_config: RuntimeConfig, codex_agent: AgentSpec
) -> None:
    backend = FakeBackend()
    backend.exists["%1"] = [False]

    result = _wait(runtime_config, backend, ((codex_agent, "%1"),))

    assert result.all_ready is False
    assert result.results[0].status == "pane_lost"
    assert backend.captured == []
    assert backend.sent == []


@pytest.mark.parametrize(
    ("screen", "status"),
    [
        (CLAUDE_TRUST_SCREEN, "setup_required"),
        (CODEX_MODEL_INCOMPATIBLE_SCREEN, "failed"),
    ],
)
def test_wait_returns_terminal_blocker_without_sleeping(
    runtime_config: RuntimeConfig,
    codex_agent: AgentSpec,
    screen: str,
    status: str,
) -> None:
    backend = FakeBackend()
    agent = (
        AgentSpec("worker", "work", "claude-cli", "claude")
        if status == "setup_required"
        else codex_agent
    )
    backend.outputs["%1"] = [screen]
    sleeps: list[float] = []

    result = _wait(
        runtime_config,
        backend,
        ((agent, "%1"),),
        poll_interval=1,
        sleeper=sleeps.append,
    )

    assert result.results[0].status == status
    assert sleeps == []
    assert backend.sent == []


@pytest.mark.parametrize("method", ["exists", "outputs"])
def test_backend_exceptions_are_terminal_and_sanitized(
    runtime_config: RuntimeConfig, codex_agent: AgentSpec, method: str
) -> None:
    backend = FakeBackend()
    secret = "sensitive backend detail"
    getattr(backend, method)["%1"] = [RuntimeError(secret)]

    result = _wait(runtime_config, backend, ((codex_agent, "%1"),))

    assert result.results[0].status == "failed"
    assert secret not in (result.results[0].reason or "")
    assert backend.sent == []


def test_timeout_converts_only_starting_workers_and_preserves_order(
    runtime_config: RuntimeConfig,
    codex_agent: AgentSpec,
    claude_agent: AgentSpec,
) -> None:
    backend = FakeBackend()
    backend.outputs["%1"] = [CODEX_READY_SCREEN]
    backend.outputs["%2"] = [""]
    times = iter((0.0, 1.0))

    result = _wait(
        runtime_config,
        backend,
        ((codex_agent, "%1"), (claude_agent, "%2")),
        timeout_seconds=1,
        poll_interval=0.1,
        monotonic=times.__next__,
    )

    assert result.all_ready is False
    assert result.timed_out is True
    assert [(item.agent_id, item.status) for item in result.results] == [
        ("planner", "ready"),
        ("reviewer", "timeout"),
    ]
    assert backend.sent == []


def test_multiple_workers_are_rechecked_in_stable_order_until_all_ready(
    runtime_config: RuntimeConfig,
    codex_agent: AgentSpec,
    claude_agent: AgentSpec,
) -> None:
    backend = FakeBackend()
    backend.outputs["%1"] = [CODEX_READY_SCREEN, CODEX_READY_SCREEN]
    backend.outputs["%2"] = ["", CLAUDE_READY_SCREEN]

    result = _wait(
        runtime_config,
        backend,
        ((codex_agent, "%1"), (claude_agent, "%2")),
        monotonic=iter((0.0, 0.1)).__next__,
    )

    assert result.all_ready is True
    assert [item.agent_id for item in result.results] == ["planner", "reviewer"]
    assert backend.checked == ["%1", "%2", "%1", "%2"]
    assert backend.captured == ["%1", "%2", "%1", "%2"]
    assert backend.sent == []


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("timeout_seconds", True),
        ("timeout_seconds", -1),
        ("timeout_seconds", nan),
        ("timeout_seconds", inf),
        ("poll_interval", False),
        ("poll_interval", -0.1),
        ("poll_interval", nan),
        ("poll_interval", inf),
    ],
)
def test_wait_rejects_invalid_numeric_arguments(
    runtime_config: RuntimeConfig,
    codex_agent: AgentSpec,
    argument: str,
    value: object,
) -> None:
    backend = FakeBackend()
    kwargs = {argument: value}

    with pytest.raises((TypeError, ValueError)):
        _wait(runtime_config, backend, ((codex_agent, "%1"),), **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "selected",
    [
        (),
        (("not-an-agent", "%1"),),
        ((AgentSpec("a", "r", "codex", "codex"), 1),),
        ((AgentSpec("a", "r", "codex", "codex"), ""),),
        ((AgentSpec("a", "r", "codex", "codex"), True),),
        ((AgentSpec("a", "r", "codex", "codex"),),),
    ],
)
def test_wait_rejects_empty_or_invalid_selected(
    runtime_config: RuntimeConfig, selected: object
) -> None:
    with pytest.raises((TypeError, ValueError)):
        _wait(runtime_config, FakeBackend(), selected)  # type: ignore[arg-type]


def test_wait_rejects_invalid_config_backend_and_clock(
    runtime_config: RuntimeConfig, codex_agent: AgentSpec
) -> None:
    selected = ((codex_agent, "%1"),)
    with pytest.raises(TypeError):
        wait_for_worker_readiness(
            runtime_config="invalid",  # type: ignore[arg-type]
            backend=FakeBackend(),
            selected=selected,
            timeout_seconds=1,
            poll_interval=0,
        )
    with pytest.raises(TypeError):
        _wait(runtime_config, object(), selected)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        _wait(runtime_config, FakeBackend(), selected, monotonic=1)  # type: ignore[arg-type]


def test_constant_monotonic_and_zero_poll_are_still_bounded(
    runtime_config: RuntimeConfig, codex_agent: AgentSpec
) -> None:
    backend = FakeBackend()
    sleeps: list[float] = []

    result = _wait(
        runtime_config,
        backend,
        ((codex_agent, "%1"),),
        timeout_seconds=2,
        poll_interval=0,
        monotonic=lambda: 5.0,
        sleeper=sleeps.append,
    )

    assert result.timed_out is True
    assert result.results[0].status == "timeout"
    assert 1 <= len(backend.captured) <= 3
    assert len(sleeps) <= 2
    assert backend.sent == []


def test_decreasing_monotonic_is_still_bounded(
    runtime_config: RuntimeConfig, codex_agent: AgentSpec
) -> None:
    backend = FakeBackend()
    times = iter((5.0, 4.0, 3.0, 2.0))

    result = _wait(
        runtime_config,
        backend,
        ((codex_agent, "%1"),),
        timeout_seconds=2,
        poll_interval=0,
        monotonic=times.__next__,
    )

    assert result.timed_out is True
    assert len(backend.captured) == 3
    assert backend.sent == []


def test_poll_sleep_never_exceeds_remaining_deadline(
    runtime_config: RuntimeConfig, codex_agent: AgentSpec
) -> None:
    backend = FakeBackend()
    sleeps: list[float] = []

    result = _wait(
        runtime_config,
        backend,
        ((codex_agent, "%1"),),
        timeout_seconds=1,
        poll_interval=100,
        monotonic=lambda: 0.0,
        sleeper=sleeps.append,
    )

    assert result.timed_out is True
    assert sleeps == [1.0]
    assert backend.sent == []
