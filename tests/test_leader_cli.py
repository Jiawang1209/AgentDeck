from __future__ import annotations

import json
from pathlib import Path

from agentdeck import cli
from agentdeck.config import write_default_config
from agentdeck.state import StateStore


def prepare_project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    monkeypatch.chdir(root)
    return root


def test_leader_plan_creates_structured_plan_without_dispatching(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(["leader", "plan", "--task", "实现自动 reply extraction"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["plan_id"].startswith("pln_")
    assert payload["status"] == "planned"
    assert payload["provider"] == "fake"
    assert payload["dispatch_ready"] is False
    assert payload["plan"]["goal"] == "实现自动 reply extraction"
    assert [step["agent_id"] for step in payload["plan"]["steps"]] == ["planner", "coder", "reviewer"]
    assert all(step["requires_approval"] is True for step in payload["plan"]["steps"])

    state = StateStore(root).load()
    assert state["plans"][0]["plan_id"] == payload["plan_id"]
    assert state["plans"][0]["task"] == "实现自动 reply extraction"
    assert state["plans"][0]["provider"] == "fake"
    assert state["plans"][0]["status"] == "planned"
    assert state["messages"] == []
    assert state["jobs"] == []
    assert state.get("inbox", {}) == {}

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "leader_plan_created"' in events


def test_leader_plan_rejects_unsupported_provider(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(["leader", "plan", "--provider", "deepseek", "--task", "真实 provider 尚未接入"])

    assert exit_code == 1
    assert "unsupported leader provider: deepseek" in capsys.readouterr().err
    state = StateStore(root).load()
    assert state.get("plans", []) == []
