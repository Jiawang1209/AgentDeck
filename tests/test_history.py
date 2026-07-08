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
