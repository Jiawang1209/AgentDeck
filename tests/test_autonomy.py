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
