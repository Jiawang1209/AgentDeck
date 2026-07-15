from __future__ import annotations

from copy import deepcopy
import json
import pytest

from agentdeck import cli
from agentdeck.contracts import (
    CONTRACT_INDEX_SPECS,
    conversation_runtime_example,
    leader_backend_example,
    validate_conversation_runtime_contract,
    validate_leader_backend_contract,
    validate_worker_transport_contract,
    worker_transport_example,
)


@pytest.mark.parametrize(
    ("name", "filename"),
    [
        ("conversation-runtime", "conversation-runtime-schema.md"),
        ("leader-backend", "leader-backend-schema.md"),
        ("worker-transport", "worker-transport-schema.md"),
    ],
)
def test_m1_contract_is_registered_in_index(name: str, filename: str) -> None:
    matches = [item for item in CONTRACT_INDEX_SPECS if item[0] == name]

    assert matches == [
        (
            name,
            f"agentdeck contract {name}",
            f"agentdeck contract {name} --example",
            filename,
        )
    ]


@pytest.mark.parametrize(
    ("example", "validator", "version"),
    [
        (conversation_runtime_example, validate_conversation_runtime_contract, "conversation-runtime/v1"),
        (leader_backend_example, validate_leader_backend_contract, "leader-backend/v1"),
        (worker_transport_example, validate_worker_transport_contract, "worker-transport/v1"),
    ],
)
def test_m1_contract_examples_validate(example, validator, version: str) -> None:
    payload = example()

    assert payload["contract_version"] == version
    assert validator(payload) == {"ok": True, "errors": []}


def test_conversation_runtime_rejects_unsafe_or_contradictory_controls() -> None:
    payload = conversation_runtime_example()
    payload["state"] = "closed"
    payload["controls"][0].update(
        {"kind": "execute", "safety": "inspect", "enabled": True, "blocker": None}
    )

    validation = validate_conversation_runtime_contract(payload)

    assert "conversation_runtime.state is inconsistent with active_turn" in validation["errors"]
    assert "conversation_runtime controls cannot enable execute as inspect" in validation["errors"]


def test_conversation_runtime_exposes_exact_semantic_clarification_card() -> None:
    payload = conversation_runtime_example()
    card = payload["semantic_clarification_card"]

    assert set(card) == {
        "schema_version", "authority_hash", "unresolved_count", "question", "controls"
    }
    assert {control["kind"] for control in card["controls"]} == {"clarify", "inspect"}
    assert all(control["kind"] not in {"execute", "dispatch", "confirm"} for control in card["controls"])
    assert validate_conversation_runtime_contract(payload) == {"ok": True, "errors": []}

    unsafe = deepcopy(payload)
    unsafe["semantic_clarification_card"]["controls"].append(
        {"kind": "confirm", "label": "Confirm", "command": "yes", "safety": "explicit_user", "enabled": True, "blocker": None}
    )
    assert validate_conversation_runtime_contract(unsafe)["ok"] is False

    hostile = deepcopy(payload)
    hostile["semantic_clarification_card"]["question"] = "\ud800"
    assert validate_conversation_runtime_contract(hostile)["ok"] is False


def test_leader_backend_rejects_ready_backend_with_blockers_and_silent_fallback() -> None:
    payload = leader_backend_example()
    payload["readiness"] = "ready"
    payload["blockers"] = ["adapter missing"]
    payload["fallback"] = {"automatic": True, "transport": "cli_subprocess"}

    validation = validate_leader_backend_contract(payload)

    assert "leader_backend ready state cannot have blockers" in validation["errors"]
    assert "leader_backend automatic fallback is forbidden" in validation["errors"]


def test_worker_transport_rejects_silent_reroute_and_unsafe_takeover() -> None:
    payload = worker_transport_example()
    payload["configured_transport"] = "acp"
    payload["effective_transport"] = "tmux"
    takeover = next(item for item in payload["controls"] if item["kind"] == "takeover")
    takeover.update({"enabled": True, "blocker": None, "safety": "inspect"})

    validation = validate_worker_transport_contract(payload)

    assert "worker_transport effective transport cannot silently differ" in validation["errors"]
    assert "worker_transport takeover must require explicit_user safety" in validation["errors"]


@pytest.mark.parametrize(
    "name",
    ["conversation-runtime", "leader-backend", "worker-transport"],
)
def test_m1_contract_cli_emits_one_valid_json_document(
    name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["contract", name, "--example"]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["example"] is True
    assert payload["example_validation"] == {"ok": True, "errors": []}
    assert captured.out.count("\n{") == 0


def test_contract_mutation_missing_required_field_is_rejected() -> None:
    payload = deepcopy(conversation_runtime_example())
    payload.pop("ownership")

    assert validate_conversation_runtime_contract(payload)["errors"] == [
        "missing conversation_runtime field: ownership"
    ]


@pytest.mark.parametrize(
    ("example", "validator", "label"),
    [
        (conversation_runtime_example, validate_conversation_runtime_contract, "conversation_runtime"),
        (leader_backend_example, validate_leader_backend_contract, "leader_backend"),
        (worker_transport_example, validate_worker_transport_contract, "worker_transport"),
    ],
)
def test_m1_contracts_reject_unknown_top_level_fields(example, validator, label: str) -> None:
    payload = example()
    payload["uncontracted"] = True

    assert f"{label} has unexpected field: uncontracted" in validator(payload)["errors"]
