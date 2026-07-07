import copy
import json

from agentdeck import cli
from agentdeck.contracts import workbench_example
from agentdeck.dashboard import render_workbench_dashboard


def test_render_workbench_dashboard_consumes_only_the_contract_payload() -> None:
    payload = workbench_example()
    snapshot = copy.deepcopy(payload)

    text = render_workbench_dashboard(payload)

    assert isinstance(text, str)
    # header derives from the contract payload
    assert "agentdeck-example" in text
    assert payload["next_command"] in text
    # role topology (flagship G6 surface) with the at-a-glance summary
    assert "Role topology" in text
    assert "6 roles" in text
    assert "0 blocked" in text
    for role_id in ("frontdesk", "planner", "orchestrator", "reviewer"):
        assert role_id in text
    assert "planning" in text
    assert "reviewed" in text
    # per-role next-step commands come from the card, not invented
    assert "agentdeck leader chat-history" in text
    assert "agentdeck inbox --agent reviewer" in text
    # review gate
    assert "Review gate" in text
    assert "round_reviewer is not configured" in text
    # rendering is read-only: it never mutates the payload
    assert payload == snapshot


def test_render_workbench_dashboard_flags_blocked_roles() -> None:
    payload = workbench_example()
    role = next(
        r for r in payload["role_topology_card"]["roles"] if r["role_id"] == "orchestrator"
    )
    role["status"] = "waiting_for_approval"
    role["blocker"] = "waiting for human approval"
    payload["role_topology_card"]["blocked_count"] = 1

    text = render_workbench_dashboard(payload)

    assert "1 blocked" in text
    assert "waiting_for_approval" in text
    assert "waiting for human approval" in text


def test_dashboard_command_renders_read_only_text_without_mutating_state(
    tmp_path, monkeypatch, capsys
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    from agentdeck.config import write_default_config

    write_default_config(root)
    monkeypatch.chdir(root)
    from agentdeck.state import StateStore

    state_before = StateStore(root).load()

    exit_code = cli.main(["dashboard"])

    assert exit_code == 0
    out = capsys.readouterr().out
    # it is a human-readable text view, not JSON
    try:
        json.loads(out)
        is_json = True
    except json.JSONDecodeError:
        is_json = False
    assert is_json is False
    assert "Role topology" in out
    assert "Review gate" in out
    state_after = StateStore(root).load()
    assert state_after == state_before
