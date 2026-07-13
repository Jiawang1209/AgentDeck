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
