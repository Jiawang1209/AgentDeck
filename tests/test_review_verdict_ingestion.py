from __future__ import annotations

import json
from pathlib import Path

from agentdeck import cli
from agentdeck.config import write_default_config
from agentdeck.state import StateStore


class FakeTmuxBackend:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.output = ""

    def send_input(self, _config, pane_id: str, text: str) -> None:
        self.sent.append((pane_id, text))

    def capture_output(self, _config, pane_id: str, lines: int = 200) -> str:
        return self.output


def prepare_project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    monkeypatch.chdir(root)
    return root


def bind_planner(root: Path) -> None:
    store = StateStore(root)
    state = store.load()
    state["agents"]["planner"] = {
        "agent_id": "planner",
        "pane_id": "%42",
        "session_name": "agentdeck",
        "cwd": str(root),
        "status": "running",
    }
    store.save(state)


def _verdict() -> dict[str, object]:
    return {
        "schema_version": "review-verdict/v1",
        "criteria": [
            {"criterion": "README 包含新命令", "verdict": "pass"},
            {"criterion": "测试全绿", "verdict": "fail"},
        ],
        "overall": "needs_changes",
        "score": 55,
    }


def _dispatch(root: Path, monkeypatch, capsys) -> str:
    bind_planner(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["dispatch", "--from-agent", "coder", "--agent", "planner", "--task", "复核实现"])
    return json.loads(capsys.readouterr().out)["message_id"]


def _events_of_type(store: StateStore, event_type: str) -> list[dict[str, object]]:
    return [
        event
        for event in store.list_events(limit=100)
        if event.get("event_type") == event_type
    ]


def test_reply_with_verdict_line_records_verdict_and_event(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    message_id = _dispatch(root, monkeypatch, capsys)
    text = (
        "status: completed\n"
        "summary: 复核完成\n"
        f"verdict: {json.dumps(_verdict(), ensure_ascii=False)}"
    )

    exit_code = cli.main(["reply", "--agent", "planner", "--message-id", message_id, "--text", text])

    assert exit_code == 0
    capsys.readouterr()
    store = StateStore(root)
    reply = store.load()["replies"][0]
    assert reply["verdict"] == _verdict()
    events = _events_of_type(store, "review_verdict_recorded")
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["reply_id"] == reply["reply_id"]
    assert payload["overall"] == "needs_changes"
    assert payload["criteria_count"] == 2
    assert payload["score"] == 55
    assert _events_of_type(store, "review_verdict_invalid") == []


def test_reply_without_verdict_line_keeps_record_shape(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    message_id = _dispatch(root, monkeypatch, capsys)

    exit_code = cli.main(
        [
            "reply",
            "--agent",
            "planner",
            "--message-id",
            message_id,
            "--text",
            "status: completed\nsummary: 无判定",
        ]
    )

    assert exit_code == 0
    capsys.readouterr()
    store = StateStore(root)
    reply = store.load()["replies"][0]
    assert "verdict" not in reply
    assert _events_of_type(store, "review_verdict_recorded") == []
    assert _events_of_type(store, "review_verdict_invalid") == []


def test_invalid_verdict_never_blocks_reply_ingestion(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    message_id = _dispatch(root, monkeypatch, capsys)
    text = "status: completed\nsummary: 判定坏了\nverdict: {not valid json"

    exit_code = cli.main(["reply", "--agent", "planner", "--message-id", message_id, "--text", text])

    assert exit_code == 0
    capsys.readouterr()
    store = StateStore(root)
    state = store.load()
    reply = state["replies"][0]
    assert "verdict" not in reply
    assert reply["text"] == text
    assert state["messages"][0]["status"] == "replied"
    invalid_events = _events_of_type(store, "review_verdict_invalid")
    assert len(invalid_events) == 1
    assert invalid_events[0]["payload"]["message_id"] == message_id
    assert _events_of_type(store, "review_verdict_recorded") == []


def test_project_view_and_trace_expose_reply_verdict(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    message_id = _dispatch(root, monkeypatch, capsys)
    text = f"status: completed\nverdict: {json.dumps(_verdict(), ensure_ascii=False)}"
    cli.main(["reply", "--agent", "planner", "--message-id", message_id, "--text", text])
    capsys.readouterr()

    assert cli.main(["status"]) == 0
    view = json.loads(capsys.readouterr().out)
    assert view["replies"]["items"][0]["verdict"] == _verdict()

    reply_id = StateStore(root).load()["replies"][0]["reply_id"]
    assert cli.main(["trace", "--id", reply_id]) == 0
    trace = json.loads(capsys.readouterr().out)
    assert trace["replies"][0]["verdict"] == _verdict()


def test_project_view_reply_without_verdict_projects_null(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    message_id = _dispatch(root, monkeypatch, capsys)
    cli.main(["reply", "--agent", "planner", "--message-id", message_id, "--text", "status: completed"])
    capsys.readouterr()

    assert cli.main(["status"]) == 0
    view = json.loads(capsys.readouterr().out)
    item = view["replies"]["items"][0]
    assert "verdict" in item
    assert item["verdict"] is None
