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


def test_render_workbench_dashboard_shows_control_mode_and_autonomous_commands() -> None:
    payload = workbench_example()
    snapshot = copy.deepcopy(payload)

    text = render_workbench_dashboard(payload)

    # the ask/approve/autonomous authorization gradient is visible at a glance
    assert "Control mode" in text
    assert "mode: ask" in text
    # the autonomous commands are surfaced as read-only hints
    assert "agentdeck approval auto --confirm" in text
    assert "agentdeck run-loop --plan-id <id> --confirm" in text
    # the disabled ones show their blocker (example is not in autonomous mode)
    assert "autonomous mode is not enabled" in text
    # rendering is read-only
    assert payload == snapshot


def test_render_workbench_dashboard_summarizes_control_palette_by_scope() -> None:
    payload = workbench_example()
    total = len(payload["control_registry"])

    text = render_workbench_dashboard(payload)

    assert "Command palette" in text
    assert f"{total} controls" in text
    # per-scope summary preserves enabled/blocked accounting (leader: 5 enabled, 2 blocked)
    assert "leader" in text
    assert "5 enabled" in text
    assert "2 blocked" in text
    assert "role_topology" in text
    # drill-down pointer to the full read-only palette
    assert "agentdeck controls" in text


def test_render_workbench_dashboard_shows_release_and_ledger_from_contract() -> None:
    payload = workbench_example()

    text = render_workbench_dashboard(payload)

    # release preview reflects the blocked gate reason from the contract
    assert "Release" in text
    assert "blocked" in text
    # ledger summary is derived from the ledger card counts
    assert "Ledger" in text
    assert "messages" in text


def test_render_workbench_dashboard_shows_ready_release_command() -> None:
    payload = workbench_example()
    card = payload["release_preview_card"]
    card["status"] = "ready"
    card["can_release"] = True
    card["reason"] = None
    card["release_command"] = "agentdeck release --confirm"
    card["next_command"] = "agentdeck release --confirm"

    text = render_workbench_dashboard(payload)

    assert "agentdeck release --confirm" in text


def test_render_workbench_dashboard_shows_worker_activity_detail() -> None:
    payload = workbench_example()

    text = render_workbench_dashboard(payload)

    assert "Worker activity" in text
    # per-worker lifecycle stage + active task detail from worker_lifecycle_card
    assert "planner" in text
    assert "inbox_pending" in text
    assert "inbox:1" in text


def test_render_workbench_dashboard_shows_run_progress_guide() -> None:
    payload = workbench_example()
    card = payload["run_progress_card"]

    text = render_workbench_dashboard(payload)

    # assisted run flow: read-only guide showing the plan, step statuses, and next command
    assert "Run progress" in text
    assert card["plan_id"] in text
    assert "planned" in text
    assert "pending" in text
    # the single explicit next command is surfaced verbatim from the contract
    assert card["next_command"] in text


def test_render_workbench_dashboard_omits_run_progress_when_no_plan() -> None:
    payload = workbench_example()
    payload["run_progress_card"] = None

    text = render_workbench_dashboard(payload)

    assert "Run progress" not in text


def test_render_workbench_dashboard_shows_learning_layer_suggestions() -> None:
    payload = workbench_example()

    text = render_workbench_dashboard(payload)

    assert "Learning layer" in text
    assert "skill suggestions" in text
    assert "incident-review" in text
    assert "memory suggestions" in text
    # memory suggestions expose the explicit apply command verbatim (read-only display)
    assert "agentdeck memory apply --suggestion-id mem_example --confirm" in text


def test_render_workbench_dashboard_shows_learning_review_when_ready() -> None:
    payload = workbench_example()

    text = render_workbench_dashboard(payload)

    # when the latest plan is summarize-ready, the learning review follow-ups show up
    assert "learning review" in text
    assert "pln_example" in text
    assert "--source learn-review" in text


def test_render_workbench_dashboard_omits_learning_review_when_null() -> None:
    payload = workbench_example()
    payload["learning_review_card"] = None

    text = render_workbench_dashboard(payload)

    assert "learning review" not in text


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


def test_dashboard_command_watch_renders_bounded_iterations_without_mutating_state(
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

    exit_code = cli.main(["dashboard", "--watch", "--iterations", "2", "--interval", "0"])

    assert exit_code == 0
    out = capsys.readouterr().out
    # each iteration re-renders the whole dashboard
    assert out.count("Role topology") == 2
    state_after = StateStore(root).load()
    assert state_after == state_before


def test_dashboard_command_rejects_non_positive_iterations(tmp_path, monkeypatch, capsys) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    from agentdeck.config import write_default_config

    write_default_config(root)
    monkeypatch.chdir(root)

    exit_code = cli.main(["dashboard", "--watch", "--iterations", "0"])

    assert exit_code == 1


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
