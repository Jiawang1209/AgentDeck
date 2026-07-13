from __future__ import annotations

import io

import pytest

from agentdeck import cli
from agentdeck.conversation.session import ConversationResponse
from agentdeck.conversation.terminal_ui import TerminalConversationUI


class _Session:
    def __init__(self, *, interrupt: bool = False) -> None:
        self.interrupt = interrupt
        self.handled: list[str] = []
        self.cancelled = 0

    def handle(self, text: str) -> ConversationResponse:
        self.handled.append(text)
        if self.interrupt:
            self.interrupt = False
            raise KeyboardInterrupt
        if text in {"/quit", "退出"}:
            return ConversationResponse("exit", {"closed": True})
        return ConversationResponse("deterministic", {"command": "status"})

    def cancel_current_turn(self) -> None:
        self.cancelled += 1


def _reader(items):
    iterator = iter(items)

    def read(_prompt: str) -> str:
        item = next(iterator)
        if isinstance(item, BaseException):
            raise item
        return item

    return read


def test_terminal_ui_handles_turn_ctrl_c_without_exiting() -> None:
    session = _Session(interrupt=True)
    stdout = io.StringIO()
    stderr = io.StringIO()
    ui = TerminalConversationUI(
        session,
        input_fn=_reader(["work", "/quit"]),
        stdout=stdout,
        stderr=stderr,
    )

    assert ui.run() == 0
    assert session.cancelled == 1
    assert session.handled == ["work", "/quit"]
    assert "Turn cancelled" in stderr.getvalue()


def test_terminal_ui_idle_double_ctrl_c_exits_but_single_clears() -> None:
    session = _Session()
    times = iter([1.0, 1.5])
    ui = TerminalConversationUI(
        session,
        input_fn=_reader([KeyboardInterrupt(), KeyboardInterrupt()]),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        monotonic=lambda: next(times),
    )

    assert ui.run() == 0
    assert session.handled == []


def test_terminal_ui_eof_and_localized_exit_are_safe() -> None:
    assert TerminalConversationUI(
        _Session(), input_fn=_reader([EOFError()]), stdout=io.StringIO(), stderr=io.StringIO()
    ).run() == 0
    localized = _Session()
    assert TerminalConversationUI(
        localized, input_fn=_reader(["退出"]), stdout=io.StringIO(), stderr=io.StringIO()
    ).run() == 0
    assert localized.handled == ["退出"]


def test_bare_non_tty_fails_fast_before_session_or_project_construction(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(
        cli,
        "ConversationSession",
        lambda *_args, **_kwargs: pytest.fail("session must not be constructed"),
        raising=False,
    )
    monkeypatch.setattr(
        cli,
        "StateStore",
        lambda *_args, **_kwargs: pytest.fail("store must not be constructed"),
    )

    assert cli.main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "leader chat --message" in captured.err
    assert len(captured.err.encode("utf-8")) < 512


def test_bare_tty_runs_foreground_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)

    class FakeUI:
        def __init__(self, _session):
            calls.append("ui")

        def run(self):
            calls.append("run")
            return 0

    monkeypatch.setattr(cli, "TerminalConversationUI", FakeUI, raising=False)
    monkeypatch.setattr(cli, "_foreground_conversation_session", lambda: object(), raising=False)

    assert cli.main([]) == 0
    assert calls == ["ui", "run"]
