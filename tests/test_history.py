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


def test_history_command_prints_markdown_timeline(tmp_path, monkeypatch, capsys):
    import json

    from agentdeck import cli

    root = _init_project(tmp_path)
    monkeypatch.chdir(root)
    store = StateStore(root)
    store.append_event(EventRecord.create("leader_plan_created", {"plan_id": "pln_1"}))
    events_before = store.all_events()

    exit_code = cli.main(["history"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert out.startswith("# AgentDeck History")
    assert "Plan created · pln_1" in out
    # it is a text timeline, not JSON
    try:
        json.loads(out)
        is_json = True
    except json.JSONDecodeError:
        is_json = False
    assert is_json is False
    # read-only: the ledger is unchanged
    assert StateStore(root).all_events() == events_before


def test_history_command_write_materializes_file(tmp_path, monkeypatch, capsys):
    from agentdeck import cli

    root = _init_project(tmp_path)
    monkeypatch.chdir(root)
    store = StateStore(root)
    store.append_event(EventRecord.create("round_released", {"round": 1}))
    events_before = store.all_events()

    exit_code = cli.main(["history", "--write"])

    assert exit_code == 0
    target = root / ".agentdeck" / "HISTORY.md"
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert content.startswith("# AgentDeck History")
    assert "Round released · round 1" in content
    # stdout reports the write rather than dumping the markdown
    out = capsys.readouterr().out
    assert "wrote" in out and "HISTORY.md" in out
    assert not out.startswith("# AgentDeck History")
    # writing the projection leaves the audit ledger unchanged
    assert StateStore(root).all_events() == events_before


def test_humanize_run_started_uses_plan_id():
    from agentdeck.history import _humanize_event

    assert (
        _humanize_event({"event_type": "run_started", "payload": {"plan_id": "pln_9"}})
        == "Run started · pln_9"
    )


def test_render_history_markdown_tolerates_blank_created_at():
    from agentdeck.history import render_history_markdown

    events = [
        {"event_type": "project_initialized", "created_at": "", "payload": {}},
    ]

    md = render_history_markdown(events, "demo")

    # blank date groups under `## unknown`, never a dangling `## ` header
    assert "## unknown" in md
    for line in md.splitlines():
        assert line != "## "
        assert not line.startswith("## ") or line[3:].strip()
    # blank time renders `- <text>` with no leading `  · `
    assert "- Project initialized" in md
    assert "-  · " not in md


def test_history_command_limit_returns_only_recent(tmp_path, monkeypatch, capsys):
    from agentdeck import cli

    root = _init_project(tmp_path)
    monkeypatch.chdir(root)
    store = StateStore(root)
    store.append_event(EventRecord.create("leader_plan_created", {"plan_id": "pln_1"}))
    store.append_event(EventRecord.create("leader_plan_created", {"plan_id": "pln_2"}))
    store.append_event(EventRecord.create("leader_plan_created", {"plan_id": "pln_3"}))

    exit_code = cli.main(["history", "--limit", "1"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Plan created · pln_3" in out
    assert "Plan created · pln_2" not in out
    assert "Plan created · pln_1" not in out


def test_history_command_write_custom_path(tmp_path, monkeypatch, capsys):
    from agentdeck import cli

    root = _init_project(tmp_path)
    monkeypatch.chdir(root)
    store = StateStore(root)
    store.append_event(EventRecord.create("leader_plan_created", {"plan_id": "pln_1"}))
    events_before = store.all_events()

    custom = root / "docs" / "timeline.md"
    exit_code = cli.main(["history", "--write", str(custom)])

    assert exit_code == 0
    assert custom.exists()
    # the default projection was not written
    assert not (root / ".agentdeck" / "HISTORY.md").exists()
    content = custom.read_text(encoding="utf-8")
    assert content.startswith("# AgentDeck History")
    assert "Plan created · pln_1" in content
    out = capsys.readouterr().out
    assert "wrote" in out and str(custom) in out
    # read-only: the audit ledger is unchanged
    assert StateStore(root).all_events() == events_before


def test_render_history_humanizes_autonomous_auto_run():
    from agentdeck.history import render_history_markdown

    events = [{
        "event_type": "approval_auto_completed",
        "created_at": "2026-07-08T12:00:00+00:00",
        "payload": {"auto_approved": 2, "dispatched": 1, "blocked": 0, "skipped": 1},
    }]
    md = render_history_markdown(events, "demo")
    assert "Auto-approve run · 2 approved, 1 dispatched" in md


def test_render_history_distinguishes_autonomous_approval():
    from agentdeck.history import render_history_markdown

    events = [{
        "event_type": "approval_decided",
        "created_at": "2026-07-08T12:00:00+00:00",
        "payload": {"approval_id": "apv_9", "status": "approved", "source": "autonomous"},
    }]
    md = render_history_markdown(events, "demo")
    assert "Approval auto-approved · apv_9" in md


def test_render_history_humanizes_run_loop_advance():
    from agentdeck.history import render_history_markdown

    events = [{
        "event_type": "run_loop_advanced",
        "created_at": "2026-07-08T12:00:00+00:00",
        "payload": {"plan_id": "pln_1", "auto_approved": 1, "dispatched": 1,
                    "blocked": 0, "skipped": 1, "stopped_reason": "waiting_for_reply"},
    }]
    md = render_history_markdown(events, "demo")
    assert "Run-loop advanced · 1 dispatched, stopped: waiting_for_reply" in md
