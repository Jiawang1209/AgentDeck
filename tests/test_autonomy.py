from agentdeck.config import load_config, write_default_config


def _init(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    return root


def test_load_config_defaults_autonomous_policy_empty(tmp_path):
    root = _init(tmp_path)
    config = load_config(root)
    assert config.autonomous.allowed_agents == ()
    assert config.autonomous.max_approvals == 0


def test_load_config_parses_autonomous_section(tmp_path):
    root = _init(tmp_path)
    cfg = (root / ".agentdeck" / "config.toml")
    cfg.write_text(
        cfg.read_text(encoding="utf-8")
        + '\n[autonomous]\nallowed_agents = ["planner", "coder"]\nmax_approvals = 3\n',
        encoding="utf-8",
    )
    config = load_config(root)
    assert config.autonomous.allowed_agents == ("planner", "coder")
    assert config.autonomous.max_approvals == 3


def test_update_autonomous_policy_writes_and_reloads(tmp_path):
    from agentdeck.config import update_autonomous_policy

    root = _init(tmp_path)
    update_autonomous_policy(root, ("planner", "coder"), 5)
    config = load_config(root)
    assert config.autonomous.allowed_agents == ("planner", "coder")
    assert config.autonomous.max_approvals == 5


def test_select_auto_approvals_filters_by_allowlist_and_budget():
    from agentdeck.autonomy import select_auto_approvals

    pending = [
        {"approval_id": "apv_1", "agent_id": "planner"},
        {"approval_id": "apv_2", "agent_id": "reviewer"},
        {"approval_id": "apv_3", "agent_id": "coder"},
        {"approval_id": "apv_4", "agent_id": "planner"},
    ]
    selected, skipped = select_auto_approvals(pending, ("planner", "coder"), max_approvals=2)

    # allowlisted in ledger order, capped at 2
    assert [a["approval_id"] for a in selected] == ["apv_1", "apv_3"]
    reasons = {s["approval_id"]: s["reason"] for s in skipped}
    assert reasons["apv_2"] == "agent not in allowlist"
    assert reasons["apv_4"] == "budget exhausted"


def test_select_auto_approvals_empty_allowlist_selects_nothing():
    from agentdeck.autonomy import select_auto_approvals

    pending = [{"approval_id": "apv_1", "agent_id": "planner"}]
    selected, skipped = select_auto_approvals(pending, (), max_approvals=5)
    assert selected == []
    assert skipped[0]["reason"] == "agent not in allowlist"
