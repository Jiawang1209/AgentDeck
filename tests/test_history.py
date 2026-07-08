from agentdeck.config import write_default_config
from agentdeck.models import EventRecord
from agentdeck.state import StateStore


def _init_project(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    return root


def test_all_events_returns_full_ledger(tmp_path):
    root = _init_project(tmp_path)
    store = StateStore(root)
    for i in range(25):
        store.append_event(EventRecord.create("task_dispatched", {"agent_id": f"a{i}"}))

    events = store.all_events()

    # list_events(default 20) would cap; all_events returns everything, oldest-first
    assert len(events) == 25
    assert events[0]["payload"]["agent_id"] == "a0"
    assert events[-1]["payload"]["agent_id"] == "a24"


def test_humanize_event_maps_milestones_and_skips_noise():
    from agentdeck.history import _humanize_event

    assert (
        _humanize_event({"event_type": "leader_plan_created", "payload": {"plan_id": "pln_1"}})
        == "Plan created · pln_1"
    )
    assert (
        _humanize_event(
            {"event_type": "approval_decided", "payload": {"status": "approved", "approval_id": "apv_1"}}
        )
        == "Approval approved · apv_1"
    )
    assert _humanize_event({"event_type": "project_initialized", "payload": {}}) == "Project initialized"
    assert (
        _humanize_event({"event_type": "round_released", "payload": {"round": 1}})
        == "Round released · round 1"
    )
    # noise (chat turns) and unknown event types are skipped
    assert _humanize_event({"event_type": "leader_chat_turn", "payload": {}}) is None
    assert _humanize_event({"event_type": "some_future_event", "payload": {}}) is None


def test_render_history_markdown_is_newest_first_grouped_by_date():
    from agentdeck.history import render_history_markdown

    events = [
        {"event_type": "project_initialized", "created_at": "2026-07-07T09:00:00+00:00", "payload": {}},
        {"event_type": "leader_chat_turn", "created_at": "2026-07-07T09:05:00+00:00", "payload": {}},
        {"event_type": "leader_plan_created", "created_at": "2026-07-08T10:00:00+00:00", "payload": {"plan_id": "pln_1"}},
        {"event_type": "round_released", "created_at": "2026-07-08T11:00:00.5+00:00", "payload": {"round": 1}},
    ]

    md = render_history_markdown(events, "demo")

    assert md.startswith("# AgentDeck History — demo")
    # newest date first
    assert md.index("## 2026-07-08") < md.index("## 2026-07-07")
    # within the newest date, newest event first
    assert md.index("Round released · round 1") < md.index("Plan created · pln_1")
    # timestamps rendered as HH:MM:SS
    assert "11:00:00 · Round released · round 1" in md
    # noise skipped, milestone kept
    assert "leader_chat_turn" not in md
    assert "Project initialized" in md
    # deterministic
    assert render_history_markdown(events, "demo") == md


def test_render_history_markdown_handles_empty_ledger():
    from agentdeck.history import render_history_markdown

    md = render_history_markdown([], "demo")

    assert md.startswith("# AgentDeck History — demo")
    assert "_No recorded activity yet._" in md
