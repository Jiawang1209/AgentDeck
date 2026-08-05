from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentdeck.models import AgentSpec, RuntimeConfig
from agentdeck.runtime import tmux
from agentdeck.runtime.protocol import TransportCapabilities


def test_tmux_backend_declares_fallback_capabilities() -> None:
    capabilities = tmux.TmuxBackend().capabilities()

    assert capabilities == TransportCapabilities.tmux_fallback()
    assert capabilities.observable_terminal is True
    assert capabilities.structured_sessions is False
    assert capabilities.permission_requests is False


def test_tmux_doctor_reports_compact_timeout_failure(monkeypatch) -> None:
    monkeypatch.setattr(tmux.shutil, "which", lambda _name: "/fake/tmux")
    monkeypatch.setattr(
        tmux.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            tmux.subprocess.TimeoutExpired(["/fake/tmux", "-V"], 5.0)
        ),
    )

    assert tmux.TmuxBackend().doctor() == tmux.RuntimeDoctorResult(
        ok=False, detail="tmux command timed out"
    )


def test_every_tmux_subprocess_call_has_one_bounded_timeout(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        stdout = "%1\n" if "split-window" in command else ""
        if "display-message" in command:
            stdout = "%1\n"
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(tmux.shutil, "which", lambda _name: "/fake/tmux")
    monkeypatch.setattr(tmux.subprocess, "run", fake_run)
    backend = tmux.TmuxBackend()
    config = RuntimeConfig(
        backend="tmux", session_name="demo", socket_name="demo"
    )
    agent = AgentSpec(
        agent_id="planner",
        role="planning",
        provider="codex",
        command="codex",
    )

    backend.doctor()
    backend.create_session(config)
    backend.spawn_agent(config, agent, "/tmp/project")
    backend.capture_output(config, "%1")
    backend.send_input(config, "%1", "prompt")
    backend.kill_pane(config, "%1")
    backend.pane_exists(config, "%1")

    assert len(calls) == 10
    assert {kwargs.get("timeout") for _command, kwargs in calls} == {5.0}


def test_apply_visible_layout_tiles_labels_and_sets_border_status(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)
    config = RuntimeConfig(backend="tmux", session_name="demo", socket_name="demo")

    tmux.TmuxBackend().apply_visible_layout(config, [("%1", "planner"), ("%2", "coder")])

    commands = [command for command, _kwargs in calls]
    assert commands == [
        ["tmux", "-L", "demo", "select-layout", "-t", "demo", "tiled"],
        ["tmux", "-L", "demo", "select-pane", "-t", "%1", "-T", "planner"],
        ["tmux", "-L", "demo", "select-pane", "-t", "%2", "-T", "coder"],
        ["tmux", "-L", "demo", "set-option", "-p", "-t", "%1",
         tmux.PANE_LABEL_OPTION, "planner"],
        ["tmux", "-L", "demo", "set-option", "-p", "-t", "%2",
         tmux.PANE_LABEL_OPTION, "coder"],
        ["tmux", "-L", "demo", "set-option", "-t", "demo",
         "pane-border-format", tmux.PANE_BORDER_FORMAT],
        ["tmux", "-L", "demo", "set-option", "-t", "demo", "pane-border-status", "top"],
    ]
    assert {kwargs.get("timeout") for _command, kwargs in calls} == {5.0}
    assert all(kwargs.get("check") is False for _command, kwargs in calls)


def test_pane_label_carries_agent_role_and_provider() -> None:
    assert tmux.pane_label("coder", "implementation", "codex") == (
        "coder · implementation · codex"
    )


def test_pane_label_falls_back_to_agent_id_when_role_and_provider_missing() -> None:
    assert tmux.pane_label("coder", "", None) == "coder"


def test_apply_visible_layout_labels_survive_program_title_changes(monkeypatch) -> None:
    # codex / Claude Code 会用 OSC 转义序列改自己的终端标题,那会覆盖掉
    # `select-pane -T` 设的 pane_title——标签消失且无人察觉。标签必须落在
    # 程序碰不到的 pane 级用户选项上,再由 pane-border-format 渲染出来。
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)
    config = RuntimeConfig(backend="tmux", session_name="demo", socket_name="demo")

    tmux.TmuxBackend().apply_visible_layout(
        config, [("%1", "planner · planning · codex")]
    )

    commands = [command for command, _kwargs in calls]
    assert ["tmux", "-L", "demo", "set-option", "-p", "-t", "%1",
            "@agentdeck_label", "planner · planning · codex"] in commands
    border_format = next(
        command for command in commands if "pane-border-format" in command
    )
    assert "@agentdeck_label" in border_format[-1]
    # 非 AgentDeck 的 pane 没有该用户选项,必须回退到 pane_title 而不是空白。
    assert "pane_title" in border_format[-1]


def test_create_session_sets_detached_terminal_size(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    tmux.TmuxBackend().create_session(
        RuntimeConfig(backend="tmux", session_name="demo", socket_name="demo")
    )

    assert calls == [[
        "tmux", "-L", "demo", "new-session", "-d", "-x", "160", "-y", "60",
        "-s", "demo", "-n", "control",
    ]]


def test_spawn_agent_uses_legacy_command_even_with_explicit_acp_transport(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="%7\n", stderr="")

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)
    agent = AgentSpec(
        agent_id="planner",
        role="planning",
        provider="codex",
        command="codex --model legacy",
        transport="acp",
        transport_command=("must-not-run", "--flag"),
    )

    pane_id = tmux.TmuxBackend().spawn_agent(
        RuntimeConfig(backend="tmux", session_name="demo", socket_name="demo"),
        agent,
        "/tmp/project",
    )

    assert pane_id == "%7"
    assert calls[0][-1] == "codex --model legacy"
    assert "must-not-run" not in calls[0]


@pytest.mark.parametrize(
    "prompt",
    [
        "short prompt",
        "line one\nline two",
        "百家姓：赵钱孙李\n" * 300,
    ],
)
def test_send_input_uses_private_bracketed_paste_then_one_enter(
    monkeypatch, prompt: str
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    tmux.TmuxBackend().send_input(
        RuntimeConfig(backend="tmux", session_name="demo", socket_name="demo"),
        "%1",
        prompt,
    )

    assert len(calls) == 4
    buffer_name = calls[0][0][-2]
    assert buffer_name.startswith("agentdeck-")
    assert calls == [
        (
            ["tmux", "-L", "demo", "load-buffer", "-b", buffer_name, "-"],
                {
                    "check": True,
                    "input": prompt,
                    "text": True,
                    "timeout": 5.0,
                },
        ),
        (
            [
                "tmux", "-L", "demo", "paste-buffer", "-p",
                "-b", buffer_name, "-t", "%1",
            ],
                {"check": True, "timeout": 5.0},
        ),
        (
            ["tmux", "-L", "demo", "send-keys", "-t", "%1", "Enter"],
                {"check": True, "timeout": 5.0},
        ),
        (
            ["tmux", "-L", "demo", "delete-buffer", "-b", buffer_name],
                {
                    "check": False,
                    "capture_output": True,
                    "text": True,
                    "timeout": 5.0,
                },
        ),
    ]
    assert all(prompt not in command for command, _kwargs in calls)


def test_send_empty_input_submits_one_enter_without_creating_a_buffer(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    tmux.TmuxBackend().send_input(
        RuntimeConfig(backend="tmux", session_name="demo", socket_name="demo"),
        "%1",
        "",
    )

    assert calls == [
        (
            ["tmux", "-L", "demo", "send-keys", "-t", "%1", "Enter"],
            {"check": True, "timeout": 5.0},
        ),
    ]


def test_send_input_cleans_private_buffer_without_masking_paste_failure(
    monkeypatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    paste_error = tmux.subprocess.CalledProcessError(1, ["tmux", "paste-buffer"])

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if "paste-buffer" in command:
            raise paste_error
        if "delete-buffer" in command:
            raise OSError("cleanup failed")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    with pytest.raises(tmux.subprocess.CalledProcessError) as raised:
        tmux.TmuxBackend().send_input(
            RuntimeConfig(backend="tmux", session_name="demo", socket_name="demo"),
            "%1",
            "private prompt",
        )

    assert raised.value is paste_error
    buffer_name = calls[0][0][-2]
    assert calls[-1] == (
        ["tmux", "-L", "demo", "delete-buffer", "-b", buffer_name],
        {
            "check": False,
            "capture_output": True,
            "text": True,
            "timeout": 5.0,
        },
    )
    assert not any(command[-1] == "Enter" for command, _kwargs in calls)


def test_send_input_silently_cleans_after_load_failure_and_preserves_error(
    monkeypatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    load_error = tmux.subprocess.CalledProcessError(1, ["tmux", "load-buffer"])

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if "load-buffer" in command:
            raise load_error
        return SimpleNamespace(returncode=1, stdout="", stderr="unknown buffer")

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    with pytest.raises(tmux.subprocess.CalledProcessError) as raised:
        tmux.TmuxBackend().send_input(
            RuntimeConfig(backend="tmux", session_name="demo", socket_name="demo"),
            "%1",
            "private prompt",
        )

    assert raised.value is load_error
    buffer_name = calls[0][0][-2]
    assert calls[-1] == (
        ["tmux", "-L", "demo", "delete-buffer", "-b", buffer_name],
        {
            "check": False,
            "capture_output": True,
            "text": True,
            "timeout": 5.0,
        },
    )
    assert len(calls) == 2


def test_send_input_rejects_non_string_before_tmux_side_effect(monkeypatch) -> None:
    monkeypatch.setattr(
        tmux.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("tmux must not run"),
    )

    with pytest.raises(TypeError, match="text must be a string"):
        tmux.TmuxBackend().send_input(
            RuntimeConfig(backend="tmux", session_name="demo", socket_name="demo"),
            "%1",
            True,
        )


def test_enter_is_not_pressed_the_instant_the_paste_is_queued(monkeypatch) -> None:
    """粘贴与回车之间必须静置一下。

    2026-08-05 live:提示词被拆断——一半提交、一半留在输入框,那一步永远停在
    `dispatched`。粘贴本身是对的(bracketed paste,换行不会被当成回车),坏在
    粘贴完**立刻**发回车:TUI 还在渲染时回车就到了。它和长度相关,所以短提示
    词侥幸没事,长的就撞上。

    刻意**不去读 pane 确认**:只读探测本身也是可观察行为,加了 `capture-pane`
    会打乱对端预期的交互顺序(一条 daemon 验收测试证明了这点)。这是把窗口关小,
    不是证明粘贴完整——真正的解法是不再靠打字。
    """
    actions: list[str] = []

    def fake_run(command, **kwargs):
        if "load-buffer" in command:
            actions.append("paste_load")
        elif "paste-buffer" in command:
            actions.append("paste")
        elif "send-keys" in command:
            actions.append("enter")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tmux.shutil, "which", lambda _name: "/fake/tmux")
    monkeypatch.setattr(tmux.subprocess, "run", fake_run)
    monkeypatch.setattr(tmux.time, "sleep", lambda seconds: actions.append(f"settle:{seconds}"))

    tmux.TmuxBackend().send_input(
        RuntimeConfig(backend="tmux", session_name="demo", socket_name="demo"),
        "%1",
        "line one\nline two",
    )

    assert actions == [
        "paste_load",
        "paste",
        f"settle:{tmux.TmuxBackend._PASTE_SETTLE_SECONDS}",
        "enter",
    ]
    assert tmux.TmuxBackend._PASTE_SETTLE_SECONDS > 0
