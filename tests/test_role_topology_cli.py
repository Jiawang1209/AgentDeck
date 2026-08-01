from __future__ import annotations

import json
from pathlib import Path

from agentdeck import cli
from agentdeck.contracts import (
    ROLE_TOPOLOGY_CARD_FIELDS,
    ROLE_TOPOLOGY_CONTROL_FIELDS,
    ROLE_TOPOLOGY_ROLE_FIELDS,
    role_topology_contract_payload,
    role_topology_contract_response,
    role_topology_example,
    validate_role_topology_contract,
)
from agentdeck.config import write_default_config
from agentdeck.role_topology import (
    ROLE_BINDING_KINDS,
    ROLE_BINDING_STATUSES,
    ROLE_LIFECYCLES,
    ROLE_TOPOLOGY_LAYERS,
)
from agentdeck.state import StateStore


def prepare_project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    monkeypatch.chdir(root)
    return root


def append_config(root: Path, block: str) -> None:
    path = root / ".agentdeck" / "config.toml"
    path.write_text(path.read_text(encoding="utf-8") + "\n" + block, encoding="utf-8")


def run_roles(capsys) -> dict[str, object]:
    exit_code = cli.main(["roles"])
    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    return json.loads(captured.out)


def roles_by_name(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    return {str(role["role"]): role for role in payload["roles"]}


def test_roles_command_projects_the_six_north_star_layers(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)

    payload = run_roles(capsys)

    assert payload["mode"] == "role_topology"
    assert payload["source_command"] == "agentdeck roles"
    assert [role["role"] for role in payload["roles"]] == [
        "frontdesk",
        "planner",
        "orchestrator",
        "coder",
        "code_reviewer",
        "round_reviewer",
    ]
    assert payload["layer_count"] == 6
    assert set(payload) == set(ROLE_TOPOLOGY_CARD_FIELDS)
    for role in payload["roles"]:
        assert set(role) == set(ROLE_TOPOLOGY_ROLE_FIELDS)
        assert role["layer"] in ROLE_TOPOLOGY_LAYERS
        assert role["binding_kind"] in ROLE_BINDING_KINDS
        assert role["binding_status"] in ROLE_BINDING_STATUSES
        assert role["lifecycle"] in ROLE_LIFECYCLES
        assert role["controls"], "every role needs at least one inspect control"
        for control in role["controls"]:
            assert set(control) == set(ROLE_TOPOLOGY_CONTROL_FIELDS)
            assert control["safety"] == "inspect"
    assert validate_role_topology_contract(payload)["ok"]


def test_roles_command_binds_default_workers_and_reports_missing_round_reviewer(
    tmp_path, monkeypatch, capsys
) -> None:
    prepare_project(tmp_path, monkeypatch)

    payload = run_roles(capsys)
    roles = roles_by_name(payload)

    assert roles["coder"]["binding_status"] == "bound"
    assert roles["coder"]["agent_id"] == "coder"
    assert roles["coder"]["provider"] == "codex"
    assert roles["coder"]["blocker"] is None
    assert roles["code_reviewer"]["binding_status"] == "bound"
    assert roles["code_reviewer"]["agent_id"] == "reviewer"
    assert roles["round_reviewer"]["binding_status"] == "unbound"
    assert roles["round_reviewer"]["agent_id"] is None
    assert roles["round_reviewer"]["blocker"]
    assert "round_reviewer" in str(roles["round_reviewer"]["blocker"])
    assert payload["bound_count"] == 5
    assert payload["unbound_count"] == 1
    assert payload["ambiguous_count"] == 0


def test_roles_command_keeps_logical_leader_layers_pane_free(tmp_path, monkeypatch, capsys) -> None:
    """`logical_leader` 不是 pane —— pane 字段必然为 null。"""
    prepare_project(tmp_path, monkeypatch)

    payload = run_roles(capsys)
    roles = roles_by_name(payload)

    for name in ("planner", "orchestrator"):
        role = roles[name]
        assert role["binding_kind"] == "logical_leader"
        assert role["binding_status"] == "bound"
        assert role["runtime_status"] is None
        assert role["pane_id"] is None
        assert role["agent_id"] is None
        assert role["provider"] == "deepseek"
        assert role["model"] == "deepseek-chat"
        assert role["backend"] == "api"
        assert role["transport"] == "http"
    frontdesk = roles["frontdesk"]
    assert frontdesk["binding_kind"] == "command"
    assert frontdesk["provider"] is None
    assert frontdesk["model"] is None
    assert frontdesk["backend"] is None
    assert frontdesk["transport"] is None
    assert frontdesk["runtime_status"] is None
    assert frontdesk["pane_id"] is None
    assert payload["split_enabled"] is False


def test_roles_command_reports_worker_runtime_from_the_project_view(
    tmp_path, monkeypatch, capsys
) -> None:
    prepare_project(tmp_path, monkeypatch)

    payload = run_roles(capsys)
    roles = roles_by_name(payload)

    assert roles["coder"]["runtime_status"] == "configured"
    assert roles["coder"]["pane_id"] is None
    terminal = next(
        control for control in roles["coder"]["controls"] if control["kind"] == "terminal"
    )
    assert terminal["enabled"] is False
    assert terminal["blocker"] == "agent is not running"


def test_roles_command_honours_the_split_leader_backends(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    append_config(
        root,
        '[leader.planner]\nprovider = "codex-cli"\nmodel = "gpt-5-codex"\n',
    )

    payload = run_roles(capsys)
    roles = roles_by_name(payload)

    assert payload["split_enabled"] is True
    assert roles["planner"]["provider"] == "codex-cli"
    assert roles["planner"]["model"] == "gpt-5-codex"
    assert roles["planner"]["backend"] == "cli"
    assert roles["planner"]["transport"] == "subprocess"
    # orchestrator still inherits `[leader]`
    assert roles["orchestrator"]["provider"] == "deepseek"
    assert roles["orchestrator"]["model"] == "deepseek-chat"


def test_roles_command_binds_the_configured_review_group_head(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    append_config(root, '[review]\nreviewers = ["reviewer"]\nround_reviewer = "planner"\n')

    payload = run_roles(capsys)
    roles = roles_by_name(payload)

    assert roles["code_reviewer"]["binding_status"] == "bound"
    assert roles["code_reviewer"]["agent_id"] == "reviewer"
    assert roles["round_reviewer"]["binding_status"] == "bound"
    assert roles["round_reviewer"]["agent_id"] == "planner"
    assert roles["round_reviewer"]["blocker"] is None
    assert payload["bound_count"] == 6
    assert payload["unbound_count"] == 0


def test_roles_command_reports_ambiguous_binding_and_never_picks_one(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    append_config(
        root,
        "[[agents]]\n"
        'agent_id = "coder_two"\n'
        'role = "implementation"\n'
        'provider = "codex"\n'
        'command = "codex"\n'
        'workspace_mode = "shared"\n',
    )

    payload = run_roles(capsys)
    roles = roles_by_name(payload)

    assert roles["coder"]["binding_status"] == "ambiguous"
    assert roles["coder"]["agent_id"] is None
    assert roles["coder"]["candidates"] == ["coder", "coder_two"]
    assert roles["coder"]["blocker"]
    assert payload["ambiguous_count"] == 1


def test_roles_command_writes_no_state_and_appends_no_event(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    events_path = root / ".agentdeck" / "state" / "events.jsonl"
    before_state = StateStore(root).load()
    before_events = events_path.read_bytes()

    run_roles(capsys)

    assert StateStore(root).load() == before_state
    assert events_path.read_bytes() == before_events


def test_roles_command_refuses_a_half_broken_card(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)
    original = cli._role_topology_card

    def broken(config, project_view):
        payload = original(config, project_view)
        payload.pop("split_enabled", None)
        return payload

    monkeypatch.setattr(cli, "_role_topology_card", broken)

    exit_code = cli.main(["roles"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "Role topology contract validation failed" in captured.err


def test_validator_rejects_pane_fields_outside_worker_agents() -> None:
    payload = role_topology_example()
    planner = next(role for role in payload["roles"] if role["role"] == "planner")
    planner["pane_id"] = "%1"
    planner["runtime_status"] = "running"

    errors = validate_role_topology_contract(payload)["errors"]

    assert any("pane_id" in error for error in errors)
    assert any("runtime_status" in error for error in errors)


def test_validator_rejects_unbound_roles_without_a_blocker() -> None:
    payload = role_topology_example()
    round_reviewer = next(role for role in payload["roles"] if role["role"] == "round_reviewer")
    assert round_reviewer["binding_status"] == "unbound"
    round_reviewer["blocker"] = None

    errors = validate_role_topology_contract(payload)["errors"]

    assert any("blocker" in error for error in errors)


def test_validator_rejects_ambiguous_roles_without_candidates_and_silent_picks() -> None:
    payload = role_topology_example()
    coder = next(role for role in payload["roles"] if role["role"] == "coder")
    coder["binding_status"] = "ambiguous"
    coder["blocker"] = "several agents share an implementation role"

    errors = validate_role_topology_contract(payload)["errors"]

    assert any("candidates" in error for error in errors)
    assert any("agent_id" in error for error in errors)


def test_validator_rejects_count_drift() -> None:
    payload = role_topology_example()
    payload["bound_count"] = payload["bound_count"] + 1

    errors = validate_role_topology_contract(payload)["errors"]

    assert errors


def test_validator_rejects_non_inspect_controls() -> None:
    payload = role_topology_example()
    payload["controls"][0]["safety"] = "explicit_runtime"

    errors = validate_role_topology_contract(payload)["errors"]

    assert any("safety" in error for error in errors)


def test_role_topology_example_matches_the_contract() -> None:
    payload = role_topology_example()

    assert validate_role_topology_contract(payload)["ok"]
    assert list(payload) == list(ROLE_TOPOLOGY_CARD_FIELDS)
    assert list(payload["roles"][0]) == list(ROLE_TOPOLOGY_ROLE_FIELDS)


def test_workbench_embeds_the_same_role_topology_card(tmp_path, monkeypatch, capsys) -> None:
    """同一个 builder 的回归钉:两面必须逐字段相同。"""
    prepare_project(tmp_path, monkeypatch)

    roles_payload = run_roles(capsys)
    assert cli.main(["workbench"]) == 0
    workbench = json.loads(capsys.readouterr().out)

    assert workbench["roles_card"] == roles_payload
    assert validate_role_topology_contract(workbench["roles_card"])["ok"]


def test_workbench_contract_discovers_the_role_topology_card(capsys) -> None:
    assert cli.main(["contract", "workbench"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert "roles_card" in payload["snapshot_fields"]
    assert payload["roles_card_fields"] == list(ROLE_TOPOLOGY_CARD_FIELDS)
    assert payload["roles_card_role_fields"] == list(ROLE_TOPOLOGY_ROLE_FIELDS)


def test_workbench_validator_rejects_a_broken_role_topology_card() -> None:
    from agentdeck.contracts import validate_workbench_contract, workbench_example

    payload = workbench_example()
    assert validate_workbench_contract(payload)["ok"]
    payload["roles_card"]["roles"][1]["pane_id"] = "%9"

    errors = validate_workbench_contract(payload)["errors"]

    assert any(error.startswith("roles_card: ") for error in errors)


def test_contract_role_topology_is_discoverable(tmp_path, capsys) -> None:
    contract_path = tmp_path / "role-topology-schema.md"
    contract_path.write_text("# role topology\n", encoding="utf-8")

    payload = role_topology_contract_payload(contract_path)

    assert payload["name"] == "role-topology"
    assert payload["roles_command"] == "agentdeck roles"
    assert payload["card_fields"] == list(ROLE_TOPOLOGY_CARD_FIELDS)
    assert payload["role_fields"] == list(ROLE_TOPOLOGY_ROLE_FIELDS)
    assert payload["layers"] == list(ROLE_TOPOLOGY_LAYERS)
    assert payload["binding_kinds"] == list(ROLE_BINDING_KINDS)
    assert payload["binding_statuses"] == list(ROLE_BINDING_STATUSES)
    assert payload["lifecycles"] == list(ROLE_LIFECYCLES)
    assert payload["safety"] == "inspect"
    assert payload["requires_explicit_user"] is False
    assert payload["contract_exists"] is True

    response = role_topology_contract_response(contract_path, include_example=True)
    assert response["example"] is True
    assert validate_role_topology_contract(response["example_role_topology"])["ok"]

    exit_code = cli.main(["contract", "role-topology", "--example"])
    assert exit_code == 0
    live = json.loads(capsys.readouterr().out)
    assert live["name"] == "role-topology"
    assert live["contract_path"].endswith("docs/contracts/role-topology-schema.md")
    assert validate_role_topology_contract(live["example_role_topology"])["ok"]
