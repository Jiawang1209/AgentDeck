from __future__ import annotations

import json
from pathlib import Path

from agentdeck import cli
from agentdeck.config import write_default_config
from agentdeck.state import StateStore


class FakeTmuxBackend:
    """In-memory runtime backend: records sends, returns a scripted pane capture."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.output = ""

    def send_input(self, _config, pane_id: str, text: str) -> None:
        self.sent.append((pane_id, text))

    def capture_output(self, _config, pane_id: str, lines: int = 200) -> str:
        return self.output


def _prepare_fake_leader_project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    config_path = root / ".agentdeck" / "config.toml"
    text = config_path.read_text(encoding="utf-8")
    text = text.replace('provider = "deepseek"', 'provider = "fake"', 1)
    text = text.replace('model = "deepseek-chat"', 'model = "fake-plan"', 1)
    config_path.write_text(text, encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def _bind_running(root: Path, agent_id: str, pane_id: str) -> None:
    store = StateStore(root)
    state = store.load()
    state["agents"][agent_id] = {
        "agent_id": agent_id,
        "pane_id": pane_id,
        "session_name": "agentdeck",
        "cwd": str(root),
        "status": "running",
    }
    store.save(state)


def _run(argv: list[str], capsys) -> dict:
    exit_code = cli.main(argv)
    out = capsys.readouterr().out
    assert exit_code == 0, out
    return json.loads(out)


def test_copilot_line1_loop_locks_end_to_end(tmp_path, monkeypatch, capsys) -> None:
    root = _prepare_fake_leader_project(tmp_path, monkeypatch)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    # 1) Leader (fake) plans in text -> per-agent plan.
    plan = _run(["leader", "plan", "--task", "add a small util with tests"], capsys)
    plan_id = plan["plan_id"]
    assert plan_id.startswith("pln_")
    steps = plan["plan"]["steps"]
    assert [s["agent_id"] for s in steps] == ["planner", "coder", "reviewer"]
    assert all(s["requires_approval"] is True for s in steps)

    # 2) Human turns the plan into pending approvals (no auto-dispatch).
    created = _run(["approval", "create-from-plan", "--plan-id", plan_id], capsys)
    assert created["count"] == 3

    # 3) Human approves the first step's (planner) approval.
    listing = _run(["approval", "list"], capsys)
    first = next(a for a in listing["approvals"] if a["agent_id"] == "planner")
    assert first["status"] == "pending"
    _run(["approval", "approve", "--approval-id", first["approval_id"]], capsys)

    # 4) Dispatch requires a running pane (real runs spawn tmux).
    _bind_running(root, "planner", "%1")

    # 5) Dispatch: worker prompt reaches its terminal.
    dispatched = _run(["approval", "dispatch", "--approval-id", first["approval_id"]], capsys)
    message_id = dispatched["message_id"]
    assert dispatched["agent_id"] == "planner"
    assert dispatched["trace_command"] == f"agentdeck trace --id {message_id}"
    assert len(fake.sent) == 1  # exactly one task sent to the pane

    # 6) Worker produces a structured reply in its pane; human captures it.
    fake.output = "status: completed\nsummary: util added with tests\n"
    reply = _run(["capture-reply", "--agent", "planner", "--message-id", message_id], capsys)
    assert reply["trace_command"].startswith("agentdeck trace --id ")

    # 7) leader review advances the loop and NEVER auto-dispatches.
    review = _run(["leader", "review", "--plan-id", plan_id], capsys)
    assert review["next_action"] in {
        "dispatch_approved",
        "wait_for_approval",
        "wait_for_reply",
        "summarize",
    }
    assert len(fake.sent) == 1  # review is read-only, no new dispatch
