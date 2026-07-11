from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentdeck.models import RuntimeConfig
from agentdeck.runtime import tmux


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
            {"check": True, "input": prompt, "text": True},
        ),
        (
            [
                "tmux", "-L", "demo", "paste-buffer", "-p", "-d",
                "-b", buffer_name, "-t", "%1",
            ],
            {"check": True},
        ),
        (
            ["tmux", "-L", "demo", "send-keys", "-t", "%1", "Enter"],
            {"check": True},
        ),
        (
            ["tmux", "-L", "demo", "delete-buffer", "-b", buffer_name],
            {"check": False},
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
            {"check": True},
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
        {"check": False},
    )
    assert not any(command[-1] == "Enter" for command, _kwargs in calls)


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
