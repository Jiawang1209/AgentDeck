from __future__ import annotations

from types import SimpleNamespace

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
    assert calls[1] == ("sleep", tmux.INPUT_SUBMIT_DELAY_SECONDS)
