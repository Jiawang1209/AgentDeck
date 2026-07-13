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
