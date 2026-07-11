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


def test_send_input_waits_between_multiline_paste_and_submit(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_run(command, **_kwargs):
        calls.append(("run", command))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)
    monkeypatch.setattr(
        tmux.time, "sleep", lambda seconds: calls.append(("sleep", seconds))
    )

    tmux.TmuxBackend().send_input(
        RuntimeConfig(backend="tmux", session_name="demo", socket_name="demo"),
        "%1",
        "line one\nline two",
    )

    assert [kind for kind, _value in calls] == ["run", "sleep", "run"]
    assert calls[1] == ("sleep", tmux.input_submit_delay("line one\nline two"))


def test_input_submit_delay_is_bounded_and_scales_with_prompt_length() -> None:
    assert tmux.input_submit_delay("") == tmux.MIN_INPUT_SUBMIT_DELAY_SECONDS
    assert tmux.input_submit_delay("x" * 1000) >= 1.0
    assert tmux.input_submit_delay("x" * 2000) == tmux.MAX_INPUT_SUBMIT_DELAY_SECONDS


@pytest.mark.parametrize("text", [None, True, 42, b"prompt"])
def test_input_submit_delay_rejects_non_string_text(text) -> None:
    with pytest.raises(TypeError, match="text must be a string"):
        tmux.input_submit_delay(text)


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


def test_send_input_gives_long_literal_paste_time_to_settle_before_one_enter(
    monkeypatch,
) -> None:
    prompt = "line\n" * 200
    elapsed = 0.0
    pasted_at: float | None = None
    submitted: list[str] = []
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        nonlocal pasted_at
        commands.append(command)
        if command[-2:] == ["-l", prompt]:
            pasted_at = elapsed
        elif command[-1] == "Enter":
            assert pasted_at is not None
            if elapsed - pasted_at >= 1.0:
                submitted.append(prompt)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_sleep(seconds: float) -> None:
        nonlocal elapsed
        elapsed += seconds

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)
    monkeypatch.setattr(tmux.time, "sleep", fake_sleep)

    tmux.TmuxBackend().send_input(
        RuntimeConfig(backend="tmux", session_name="demo", socket_name="demo"),
        "%1",
        prompt,
    )

    assert submitted == [prompt]
    assert commands == [
        ["tmux", "-L", "demo", "send-keys", "-t", "%1", "-l", prompt],
        ["tmux", "-L", "demo", "send-keys", "-t", "%1", "Enter"],
    ]
