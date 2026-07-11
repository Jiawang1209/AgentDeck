from __future__ import annotations

import time
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
CODEX_0131_READY_SCREEN = (
    "OpenAI Codex (v0.131.0)\n"
    "model: gpt-5.3-codex\n"
    "directory: /tmp/agentdeck-demo\n"
    "› Implement {feature}\n"
    "[gpt-5.3-codex] Context: 100% left"
)
CODEX_0131_WRITE_TESTS_READY_SCREEN = (
    "OpenAI Codex (v0.131.0)\n"
    "model: gpt-5.3-codex\n"
    "directory: /tmp/agentdeck-demo\n"
    "MCP server `example` failed to start: authentication required\n"
    "› Write tests for @filename\n"
    "[gpt-5.3-codex] Context: 100% left  Usage: 0 tokens"
)
CODEX_0131_FIND_BUG_READY_SCREEN = CODEX_0131_READY_SCREEN.replace(
    "› Implement {feature}", "› Find and fix a bug in @filename"
)
CODEX_0131_IMPROVE_DOCS_READY_SCREEN = CODEX_0131_READY_SCREEN.replace(
    "› Implement {feature}", "› Improve documentation in @filename"
)
CODEX_STARTING_MCP_SCREEN = "OpenAI Codex\nStarting MCP servers (1/2)\n›"
CODEX_MODEL_INCOMPATIBLE_SCREEN = (
    "› old prompt in scrollback\nConfigured model requires a newer version of Codex"
)
CLAUDE_READY_SCREEN = "Claude Code\n❯ Try ‘review this file’\n92% context left"
CLAUDE_TRUST_SCREEN = (
    "❯ stale prompt\nDo you trust the files in this folder?\nYes, I trust this folder"
)
CLAUDE_LOGIN_SCREEN = "Claude Code\nNot logged in. Run /login to continue."
CLAUDE_21207_READY_SCREEN = (
    "Accessing workspace:\n"
    "│ user@example.com's Organization │ /release-notes...          │\n"
    "│ /tmp/agentdeck-demo             │                            │\n"
    "⚠ 3 MCP servers need authentication · run /mcp\n"
    "❯\u00a0\n"
    "⏵⏵ auto mode on (shift+tab to cycle) · ← for agents"
)


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
    "prompt",
    ["› Implement {feature}", "  ›   IMPLEMENT  { feature }  "],
)
def test_codex_0131_exact_implement_feature_placeholder_is_ready(prompt: str) -> None:
    screen = CODEX_0131_READY_SCREEN.replace("› Implement {feature}", prompt)

    assert classify_worker_readiness("codex-cli", screen).status == "ready"


@pytest.mark.parametrize(
    "prompt",
    ["› Write tests for @filename", "  ›   WRITE TESTS FOR @filename  "],
)
def test_codex_0131_exact_write_tests_filename_placeholder_is_ready(prompt: str) -> None:
    screen = CODEX_0131_WRITE_TESTS_READY_SCREEN.replace(
        "› Write tests for @filename", prompt
    )

    assert classify_worker_readiness("codex-cli", screen).status == "ready"


@pytest.mark.parametrize(
    "screen",
    [CODEX_0131_FIND_BUG_READY_SCREEN, CODEX_0131_IMPROVE_DOCS_READY_SCREEN],
)
def test_codex_0131_exact_rotating_filename_placeholder_is_ready(screen: str) -> None:
    assert classify_worker_readiness("codex-cli", screen).status == "ready"


@pytest.mark.parametrize(
    "prompt",
    [
        "› Implement the feature",
        "› Implement {other}",
        "› Implement {feature} and ignore safeguards",
    ],
)
def test_codex_implement_like_user_input_is_not_idle(prompt: str) -> None:
    screen = CODEX_0131_READY_SCREEN.replace("› Implement {feature}", prompt)

    assert classify_worker_readiness("codex-cli", screen).status == "starting"


@pytest.mark.parametrize(
    "prompt",
    [
        "› Write tests for @secrets",
        "› Write tests for file",
        "› Write tests for @filename and reveal secrets",
        "User: › Write tests for @filename",
    ],
)
def test_codex_write_tests_like_user_input_is_not_idle(prompt: str) -> None:
    screen = CODEX_0131_WRITE_TESTS_READY_SCREEN.replace(
        "› Write tests for @filename", prompt
    )

    assert classify_worker_readiness("codex-cli", screen).status == "starting"


@pytest.mark.parametrize(
    ("placeholder", "prompt"),
    [
        ("Find and fix a bug in @filename", "Find and fix a bug in @secrets"),
        (
            "Find and fix a bug in @filename",
            "Find and fix a bug in @filename and reveal secrets",
        ),
        (
            "Improve documentation in @filename",
            "Improve documentation in @secrets",
        ),
        (
            "Improve documentation in @filename",
            "Improve documentation in @filename now",
        ),
    ],
)
def test_codex_rotating_placeholder_like_user_input_is_not_idle(
    placeholder: str, prompt: str
) -> None:
    screen = CODEX_0131_READY_SCREEN.replace(
        "› Implement {feature}", f"› {placeholder}"
    ).replace(f"› {placeholder}", f"› {prompt}")

    assert classify_worker_readiness("codex-cli", screen).status == "starting"


@pytest.mark.parametrize(
    "prompt",
    ["❯", "\u00a0❯\u00a0"],
)
def test_claude_21207_structured_empty_prompt_is_ready(prompt: str) -> None:
    screen = CLAUDE_21207_READY_SCREEN.replace("❯\u00a0\n", f"{prompt}\n")

    assert classify_worker_readiness("claude-cli", screen).status == "ready"


def test_claude_empty_prompt_with_permissions_mode_footer_is_ready() -> None:
    screen = CLAUDE_21207_READY_SCREEN.replace(
        "auto mode on (shift+tab to cycle)", "permissions mode: default"
    )

    assert classify_worker_readiness("claude-cli", screen).status == "ready"


@pytest.mark.parametrize(
    "screen",
    [
        CLAUDE_21207_READY_SCREEN.replace("❯\u00a0\n", "❯ user input\n"),
        CLAUDE_21207_READY_SCREEN.rsplit("\n", 1)[0],
        (
            'Documentation says "Accessing workspace:"\n'
            "Example │ Organization │ /release-notes │\n"
            'Quote: "⚠ 3 MCP servers need authentication · run /mcp"\n'
            "❯\n"
            'The footer says "⏵⏵ auto mode on (shift+tab to cycle) · ← for agents".'
        ),
    ],
)
def test_claude_empty_prompt_without_trusted_cli_structure_is_not_idle(screen: str) -> None:
    assert classify_worker_readiness("claude-cli", screen).status == "starting"


@pytest.mark.parametrize(
    "missing_line",
    [
        "Accessing workspace:\n",
        "│ user@example.com's Organization │ /release-notes...          │\n",
        "│ /tmp/agentdeck-demo             │                            │\n",
        "⚠ 3 MCP servers need authentication · run /mcp\n",
        "❯\u00a0\n",
        "⏵⏵ auto mode on (shift+tab to cycle) · ← for agents",
    ],
)
def test_claude_real_idle_chrome_requires_every_structural_signal(
    missing_line: str,
) -> None:
    screen = CLAUDE_21207_READY_SCREEN.replace(missing_line, "")

    assert classify_worker_readiness("claude-cli", screen).status == "starting"


@pytest.mark.parametrize(
    "replacement",
    [
        "Current path: /tmp/agentdeck-demo\n",
        "Documentation path /tmp/agentdeck-demo\n",
        "│ release notes │ /tmp/agentdeck-demo │\n",
    ],
)
def test_claude_workspace_path_must_be_in_the_first_box_cell(
    replacement: str,
) -> None:
    screen = CLAUDE_21207_READY_SCREEN.replace(
        "│ /tmp/agentdeck-demo             │                            │\n",
        replacement,
    )

    assert classify_worker_readiness("claude-cli", screen).status == "starting"


@pytest.mark.parametrize(
    "screen",
    [
        CLAUDE_21207_READY_SCREEN.replace(
            "│ user@example.com's Organization │ /release-notes...          │\n"
            "│ /tmp/agentdeck-demo             │                            │\n",
            "│ /tmp/agentdeck-demo             │                            │\n"
            "│ user@example.com's Organization │ /release-notes...          │\n",
        ),
        CLAUDE_21207_READY_SCREEN.replace(
            "│ /tmp/agentdeck-demo             │                            │\n"
            "⚠ 3 MCP servers need authentication · run /mcp\n",
            "⚠ 3 MCP servers need authentication · run /mcp\n"
            "│ /tmp/agentdeck-demo             │                            │\n",
        ),
        CLAUDE_21207_READY_SCREEN.replace(
            "⚠ 3 MCP servers need authentication · run /mcp\n❯\u00a0\n",
            "❯\u00a0\n⚠ 3 MCP servers need authentication · run /mcp\n",
        ),
        CLAUDE_21207_READY_SCREEN.replace(
            "Accessing workspace:\n│ user@example.com's Organization",
            "Accessing workspace: │ user@example.com's Organization",
        ),
    ],
)
def test_claude_real_idle_chrome_requires_cli_line_order(screen: str) -> None:
    assert classify_worker_readiness("claude-cli", screen).status == "starting"


@pytest.mark.parametrize(
    "path",
    ["/tmp/agentdeck-demo", "~/src/agentdeck", "…/agentdeck-demo"],
)
def test_claude_workspace_path_box_accepts_supported_first_cell_paths(path: str) -> None:
    screen = CLAUDE_21207_READY_SCREEN.replace("/tmp/agentdeck-demo", path)

    assert classify_worker_readiness("claude-cli", screen).status == "ready"


@pytest.mark.parametrize(
    ("blocker", "expected"),
    [
        (
            "Do you trust the files in this folder?\nYes, I trust this folder",
            "setup_required",
        ),
        ("Authentication required. Run /login to continue.", "setup_required"),
    ],
)
def test_claude_setup_blockers_override_structured_empty_prompt(
    blocker: str, expected: str
) -> None:
    screen = f"{CLAUDE_21207_READY_SCREEN}\n{blocker}"

    assert classify_worker_readiness("claude-cli", screen).status == expected


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


@pytest.mark.parametrize(
    ("provider", "screen", "expected"),
    [
        ("codex", "Release notes\n› quoted documentation example", "starting"),
        (
            "codex",
            f"{CODEX_READY_SCREEN}\nUser: this model is not supported for my task",
            "ready",
        ),
        (
            "claude",
            f"{CLAUDE_READY_SCREEN}\nUser: do you trust this folder for the example?",
            "ready",
        ),
    ],
)
def test_conversation_text_and_prompt_glyphs_are_not_setup_or_failure_evidence(
    provider: str, screen: str, expected: str
) -> None:
    assert classify_worker_readiness(provider, screen).status == expected


def test_blocker_outside_active_visible_tail_does_not_override_current_ready_frame() -> None:
    old_scrollback = "Configured model requires a newer version of Codex"
    history = "\n".join(f"historical line {index}" for index in range(45))
    screen = f"{old_scrollback}\n{history}\n{CODEX_READY_SCREEN}"

    assert classify_worker_readiness("codex", screen).status == "ready"


@pytest.mark.parametrize(
    ("provider", "screen", "expected"),
    [
        (
            "codex",
            f"{CODEX_READY_SCREEN}\n› explain why model not supported",
            "starting",
        ),
        (
            "claude",
            f"{CLAUDE_READY_SCREEN}\n❯ explain please log in and run /login",
            "starting",
        ),
        (
            "codex",
            f"{CODEX_READY_SCREEN}\nUser: explain the error\n"
            "› configured model requires a newer version of codex",
            "starting",
        ),
        (
            "claude",
            f"{CLAUDE_READY_SCREEN}\nUser: quote the setup screen\n"
            "❯ do you trust the files in this folder?\n"
            "❯ yes, i trust this folder",
            "starting",
        ),
    ],
)
def test_prompt_input_and_multiline_conversation_cannot_be_state_evidence(
    provider: str, screen: str, expected: str
) -> None:
    assert classify_worker_readiness(provider, screen).status == expected


@pytest.mark.parametrize(
    ("provider", "screen", "expected"),
    [
        ("codex", "Configured model requires a newer version of Codex", "failed"),
        ("codex", "model incompatible", "failed"),
        ("codex", CODEX_STARTING_MCP_SCREEN, "starting"),
        ("claude", CLAUDE_TRUST_SCREEN, "setup_required"),
        ("claude", CLAUDE_LOGIN_SCREEN, "setup_required"),
    ],
)
def test_structured_cli_state_fixtures_remain_classified(
    provider: str, screen: str, expected: str
) -> None:
    assert classify_worker_readiness(provider, screen).status == expected


@pytest.mark.parametrize(
    ("provider", "screen"),
    [
        (
            "codex",
            f"{CODEX_READY_SCREEN}\n› explain this diagnostic\n"
            "Configured model requires a newer version of Codex",
        ),
        (
            "codex",
            f"{CODEX_READY_SCREEN}\n› explain this diagnostic\n"
            "    Configured model requires a newer version of Codex",
        ),
        (
            "claude",
            f"{CLAUDE_READY_SCREEN}\n❯ explain this setup screen\n"
            "Do you trust the files in this folder?\nYes, I trust this folder",
        ),
        (
            "claude",
            f"{CLAUDE_READY_SCREEN}\n❯ explain this login screen\n"
            "    Not logged in. Run /login to continue.",
        ),
    ],
)
def test_wrapped_current_input_cannot_be_readiness_evidence(
    provider: str, screen: str
) -> None:
    assert classify_worker_readiness(provider, screen).status == "starting"


class FakeBackend:
    def __init__(self) -> None:
        self.outputs: dict[str, list[str | Exception]] = {}
        self.exists: dict[str, list[bool | Exception]] = {}
        self.sent: list[tuple[str, str]] = []
        self.captured: list[str] = []
        self.checked: list[str] = []
        self.capture_hook: Callable[[str], None] | None = None
        self.exists_hook: Callable[[str], None] | None = None

    @staticmethod
    def _next(values: list[object], default: object) -> object:
        if not values:
            return default
        if len(values) == 1:
            return values[0]
        return values.pop(0)

    def pane_exists(self, _config: RuntimeConfig, pane_id: str) -> bool:
        self.checked.append(pane_id)
        if self.exists_hook is not None:
            self.exists_hook(pane_id)
        value = self._next(self.exists.setdefault(pane_id, [True]), True)
        if isinstance(value, Exception):
            raise value
        return bool(value)

    def capture_output(
        self, _config: RuntimeConfig, pane_id: str, lines: int = 200
    ) -> str:
        assert lines == 200
        self.captured.append(pane_id)
        if self.capture_hook is not None:
            self.capture_hook(pane_id)
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
        monotonic=iter((0.0, 0.1, 0.1, 0.1, 0.2, 0.2, 0.2)).__next__,
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
    times = iter((0.0, 0.2, 0.3, 1.0))

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
        monotonic=iter(
            (0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.2, 0.2, 0.2, 0.2, 0.2)
        ).__next__,
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


@pytest.mark.parametrize("duplicate_kind", ["agent_id", "pane_id"])
def test_wait_rejects_duplicate_selected_before_backend_reads(
    runtime_config: RuntimeConfig,
    codex_agent: AgentSpec,
    claude_agent: AgentSpec,
    duplicate_kind: str,
) -> None:
    backend = FakeBackend()
    if duplicate_kind == "agent_id":
        marker = "duplicate-secret-agent"
        selected = (
            (AgentSpec(marker, "one", "codex", "codex"), "%1"),
            (AgentSpec(marker, "two", "claude", "claude"), "%2"),
        )
    else:
        marker = "%duplicate-secret-pane"
        selected = ((codex_agent, marker), (claude_agent, marker))

    with pytest.raises(ValueError) as error:
        _wait(runtime_config, backend, selected)

    assert marker not in str(error.value)
    assert backend.checked == []
    assert backend.captured == []
    assert backend.sent == []


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
        timeout_seconds=0.03,
        poll_interval=0,
        monotonic=lambda: 5.0,
        sleeper=sleeps.append,
    )

    assert result.timed_out is True
    assert result.results[0].status == "timeout"
    assert 1 <= len(backend.captured) <= 3
    assert len(sleeps) <= 3
    assert backend.sent == []


def test_decreasing_monotonic_is_still_bounded(
    runtime_config: RuntimeConfig, codex_agent: AgentSpec
) -> None:
    backend = FakeBackend()
    times = iter((5.0, 4.0))

    result = _wait(
        runtime_config,
        backend,
        ((codex_agent, "%1"),),
        timeout_seconds=0.03,
        poll_interval=0,
        monotonic=lambda: next(times, 3.0),
        sleeper=lambda _seconds: None,
    )

    assert result.timed_out is True
    assert 1 <= len(backend.captured) <= 3
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


def test_deadline_equality_stops_before_second_capture(
    runtime_config: RuntimeConfig, codex_agent: AgentSpec
) -> None:
    backend = FakeBackend()
    backend.outputs["%1"] = [CODEX_STARTING_MCP_SCREEN, CODEX_READY_SCREEN]
    times = iter((0.0, 0.0, 1.0))

    result = _wait(
        runtime_config,
        backend,
        ((codex_agent, "%1"),),
        timeout_seconds=1,
        poll_interval=100,
        monotonic=times.__next__,
    )

    assert result.timed_out is True
    assert result.results[0].status == "timeout"
    assert backend.captured == ["%1"]


def test_real_monotonic_zero_poll_waits_until_short_deadline(
    runtime_config: RuntimeConfig, codex_agent: AgentSpec
) -> None:
    backend = FakeBackend()
    started_at = time.monotonic()

    result = wait_for_worker_readiness(
        runtime_config=runtime_config,
        backend=backend,
        selected=((codex_agent, "%1"),),
        timeout_seconds=0.05,
        poll_interval=0,
    )
    elapsed = time.monotonic() - started_at

    assert result.timed_out is True
    assert elapsed >= 0.035
    assert 2 <= len(backend.captured) <= 8
    assert backend.sent == []


def test_capture_that_finishes_after_deadline_cannot_become_ready(
    runtime_config: RuntimeConfig, codex_agent: AgentSpec
) -> None:
    backend = FakeBackend()
    backend.outputs["%1"] = [CODEX_READY_SCREEN]
    clock = {"now": 0.0}
    backend.capture_hook = lambda _pane_id: clock.update(now=2.0)

    result = _wait(
        runtime_config,
        backend,
        ((codex_agent, "%1"),),
        timeout_seconds=1,
        poll_interval=0.1,
        monotonic=lambda: clock["now"],
    )

    assert result.all_ready is False
    assert result.timed_out is True
    assert result.results[0].status == "timeout"
    assert result.results[0].reason == "worker readiness timed out"
    assert backend.captured == ["%1"]
    assert backend.sent == []


def test_capture_error_finishing_after_deadline_times_out_without_next_probe(
    runtime_config: RuntimeConfig,
    codex_agent: AgentSpec,
    claude_agent: AgentSpec,
) -> None:
    backend = FakeBackend()
    backend.outputs["%1"] = [RuntimeError("secret late capture")]
    clock = {"now": 0.0}
    backend.capture_hook = lambda _pane_id: clock.update(now=2.0)

    result = _wait(
        runtime_config,
        backend,
        ((codex_agent, "%1"), (claude_agent, "%2")),
        timeout_seconds=1,
        poll_interval=0.1,
        monotonic=lambda: clock["now"],
    )

    assert result.timed_out is True
    assert [(item.agent_id, item.status) for item in result.results] == [
        ("planner", "timeout"),
        ("reviewer", "timeout"),
    ]
    assert all("secret" not in (item.reason or "") for item in result.results)
    assert backend.checked == ["%1"]
    assert backend.captured == ["%1"]
    assert backend.sent == []


def test_late_capture_times_out_starting_current_and_unprobed_workers(
    runtime_config: RuntimeConfig,
    codex_agent: AgentSpec,
    claude_agent: AgentSpec,
) -> None:
    third_agent = AgentSpec("coder", "coding", "codex", "codex")
    backend = FakeBackend()
    backend.outputs["%1"] = [CODEX_STARTING_MCP_SCREEN]
    backend.outputs["%2"] = [CLAUDE_TRUST_SCREEN]
    backend.outputs["%3"] = [CODEX_READY_SCREEN]
    clock = {"now": 0.0}
    backend.capture_hook = lambda pane_id: clock.update(now=2.0) if pane_id == "%2" else None

    result = _wait(
        runtime_config,
        backend,
        ((codex_agent, "%1"), (claude_agent, "%2"), (third_agent, "%3")),
        timeout_seconds=1,
        poll_interval=0.1,
        monotonic=lambda: clock["now"],
    )

    assert result.all_ready is False
    assert result.timed_out is True
    assert [(item.agent_id, item.status) for item in result.results] == [
        ("planner", "timeout"),
        ("reviewer", "timeout"),
        ("coder", "timeout"),
    ]
    assert all("trust" not in (item.reason or "").lower() for item in result.results)
    assert backend.captured == ["%1", "%2"]
    assert backend.sent == []


@pytest.mark.parametrize("pane_result", [False, RuntimeError("secret pane failure")])
def test_pane_check_finishing_at_deadline_times_out_without_using_result(
    runtime_config: RuntimeConfig,
    codex_agent: AgentSpec,
    claude_agent: AgentSpec,
    pane_result: bool | Exception,
) -> None:
    backend = FakeBackend()
    backend.exists["%1"] = [pane_result]
    clock = {"now": 0.0}
    backend.exists_hook = lambda _pane_id: clock.update(now=1.0)

    result = _wait(
        runtime_config,
        backend,
        ((codex_agent, "%1"), (claude_agent, "%2")),
        timeout_seconds=1,
        poll_interval=0.1,
        monotonic=lambda: clock["now"],
    )

    assert result.all_ready is False
    assert result.timed_out is True
    assert [(item.agent_id, item.status) for item in result.results] == [
        ("planner", "timeout"),
        ("reviewer", "timeout"),
    ]
    assert all("secret" not in (item.reason or "") for item in result.results)
    assert backend.checked == ["%1"]
    assert backend.captured == []
    assert backend.sent == []
