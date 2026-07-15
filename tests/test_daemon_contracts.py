from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from agentdeck.contracts import (
    CLIENT_SESSION_RESPONSE_FIELDS,
    DAEMON_RUNTIME_RESPONSE_FIELDS,
    MISSION_SCHEDULER_RESPONSE_FIELDS,
    client_session_contract_response,
    client_session_example,
    daemon_runtime_contract_response,
    daemon_runtime_example,
    mission_scheduler_contract_response,
    mission_scheduler_example,
    validate_client_session_contract,
    validate_daemon_runtime_contract,
    validate_mission_scheduler_contract,
    project_view_example,
    validate_project_view_contract,
)


@pytest.mark.parametrize(
    ("version", "fields", "example_factory", "validator", "response_factory"),
    [
        ("daemon-runtime/v1", DAEMON_RUNTIME_RESPONSE_FIELDS, daemon_runtime_example, validate_daemon_runtime_contract, daemon_runtime_contract_response),
        ("mission-scheduler/v1", MISSION_SCHEDULER_RESPONSE_FIELDS, mission_scheduler_example, validate_mission_scheduler_contract, mission_scheduler_contract_response),
        ("client-session/v1", CLIENT_SESSION_RESPONSE_FIELDS, client_session_example, validate_client_session_contract, client_session_contract_response),
    ],
)
def test_daemon_contract_examples_are_exact_and_valid(
    tmp_path: Path, version, fields, example_factory, validator, response_factory
) -> None:
    path = tmp_path / "contract.md"
    path.write_text("# contract\n", encoding="utf-8")
    example = example_factory()
    assert tuple(example) == fields
    assert example["schema_version"] == version
    assert validator(example) == {"ok": True, "errors": []}
    response = response_factory(path, include_example=True)
    assert response["contract_version"] == version
    assert response["response_fields"] == list(fields)
    assert response["example"] == example


@pytest.mark.parametrize(
    ("example_factory", "validator"),
    [
        (daemon_runtime_example, validate_daemon_runtime_contract),
        (mission_scheduler_example, validate_mission_scheduler_contract),
        (client_session_example, validate_client_session_contract),
    ],
)
def test_daemon_contract_validators_reject_unknown_missing_and_unsafe_controls(
    example_factory, validator
) -> None:
    example = example_factory()
    for mutate in (
        lambda value: value.pop(next(iter(value))),
        lambda value: value.__setitem__("unknown", True),
        lambda value: value["controls"][0].__setitem__("safety", "authorized"),
    ):
        candidate = deepcopy(example)
        mutate(candidate)
        assert validator(candidate)["ok"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload["controls"][0].__setitem__("kind", ""),
        lambda payload: payload["controls"][0].__setitem__("label", 7),
        lambda payload: payload["controls"][0].__setitem__("command", None),
        lambda payload: payload["controls"][0].__setitem__("enabled", 1),
        lambda payload: payload["controls"][0].__setitem__("blocker", []),
        lambda payload: payload["controls"][0].__setitem__("blocker", "not actually enabled"),
        lambda payload: payload["controls"][1].update(enabled=False, blocker=None),
        lambda payload: payload["controls"][1].__setitem__("blocker", ""),
        lambda payload: payload["controls"][0].__setitem__("extra", True),
    ],
)
def test_daemon_controls_are_strict_gui_controls(mutation) -> None:
    payload = daemon_runtime_example()
    mutation(payload)
    assert validate_daemon_runtime_contract(payload)["ok"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update(compatible=False, write_enabled=True),
        lambda payload: payload.update(role="observer", write_enabled=True),
        lambda payload: payload.update(role="observer", lease_generation=1),
        lambda payload: payload.update(role="observer", client_id=None, write_enabled=False, lease_generation=None),
        lambda payload: payload.update(role="controller", lease_generation=None),
        lambda payload: payload.update(role="none", client_id="client_x"),
        lambda payload: payload.update(client_id="   "),
        lambda payload: payload.update(lease_generation=True),
    ],
)
def test_client_session_role_compatibility_and_lease_combinations_are_strict(mutation) -> None:
    payload = client_session_example()
    mutation(payload)
    assert validate_client_session_contract(payload)["ok"] is False


def test_daemon_stop_control_uses_internal_temporary_controller_flow() -> None:
    payload = daemon_runtime_example()
    stop = next(control for control in payload["controls"] if control["kind"] == "stop")
    assert stop == {
        "kind": "stop",
        "label": "Stop daemon",
        "command": "agentdeck daemon stop --confirm",
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
    }


def test_mission_scheduler_example_discovers_active_background_execution() -> None:
    payload = mission_scheduler_example()
    assert payload["state"] == "running"
    assert payload["active_mission_id"] == "mis_0123456789ab"
    assert payload["active_step"] == "step_2"
    assert payload["next_transition"] == "start_worker"
    assert payload["blockers"] == []


def test_mission_scheduler_rejects_unknown_transition() -> None:
    payload = mission_scheduler_example()
    payload["next_transition"] = "unbounded_background_magic"
    assert validate_mission_scheduler_contract(payload)["ok"] is False


@pytest.mark.parametrize(
    "surface",
    ["plans", "missions"],
)
@pytest.mark.parametrize(
    "mutation",
    [
        lambda card: card.__setitem__("authority_hash", "sha256:BAD"),
        lambda card: card.__setitem__("target", "private.txt"),
        lambda card: card.__setitem__("prompt", "raw prompt"),
        lambda card: card.__setitem__("secret_ref", "env://TOKEN"),
    ],
)
def test_project_view_semantic_authority_is_exact_and_non_leaking(
    surface, mutation
) -> None:
    payload = project_view_example()
    card = {
        "schema_version": "mission-semantic-authority/v1",
        "state": "preview",
        "authority_hash": "sha256:" + "a" * 64,
        "requirement_count": 4,
        "proposed_effect_count": 0,
        "unresolved_count": 0,
        "compiled_step_count": 2,
        "blockers": [],
    }
    payload["plans"]["items"][0]["semantic_authority"] = deepcopy(card)
    payload["missions"]["items"][0]["semantic_authority"] = deepcopy(card)
    mutation(payload[surface]["items"][0]["semantic_authority"])

    assert validate_project_view_contract(payload)["ok"] is False


def test_project_view_semantic_authority_rejects_hostile_card_without_hooks() -> None:
    touched: list[str] = []

    class HostileCard(dict):
        def get(self, _key, _default=None):
            touched.append("get")
            raise RuntimeError("RAW_SEMANTIC_SECRET")

        def __eq__(self, _other):
            touched.append("eq")
            raise RuntimeError("RAW_SEMANTIC_SECRET")

        def __ne__(self, _other):
            touched.append("ne")
            raise RuntimeError("RAW_SEMANTIC_SECRET")

    payload = project_view_example()
    payload["plans"]["items"][0]["semantic_authority"] = HostileCard()
    payload["missions"]["items"][0]["semantic_authority"] = HostileCard()

    result = validate_project_view_contract(payload)

    assert result["ok"] is False
    assert touched == []
    assert "RAW_SEMANTIC_SECRET" not in repr(result)


def test_project_view_bad_missions_shape_returns_contract_error_not_exception() -> None:
    payload = project_view_example()
    payload["missions"] = {"count": 1, "by_status": {}, "latest_id": None, "items": None}

    result = validate_project_view_contract(payload)

    assert result["ok"] is False
