from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import re

import pytest

from agentdeck.models import AgentSpec, LeaderConfig, ProjectConfig, RuntimeConfig
from agentdeck.providers.base import LeaderPlanRequest
from agentdeck.providers.plan_schema import (
    LEADER_PLAN_SCHEMA_VERSION,
    ProviderPlanValidationError,
    build_leader_generation_provenance,
    build_leader_plan_schema,
    canonical_leader_plan_schema_hash,
    leader_plan_authority,
    validate_provider_plan_schema,
)


def _config(root: str = "/private/project-a") -> ProjectConfig:
    return ProjectConfig(
        name="schema-test",
        root=root,
        leader=LeaderConfig(),
        agents=(
            AgentSpec("planner", "planning", "codex-cli", "codex"),
            AgentSpec("reviewer", "review", "claude-cli", "claude"),
            AgentSpec("builder", "implementation", "codex-cli", "codex"),
        ),
        runtime=RuntimeConfig(),
    )


def _request(**changes: object) -> LeaderPlanRequest:
    request = LeaderPlanRequest(
        task="Build a canonical plan",
        config=_config(),
        selected_agent_ids=("reviewer", "planner"),
        step_count=2,
    )
    return replace(request, **changes)


def test_schema_freezes_worker_order_step_count_and_approval() -> None:
    schema = build_leader_plan_schema(_request())

    assert schema["$id"] == LEADER_PLAN_SCHEMA_VERSION == "leader-plan/v1"
    assert schema["required"] == ["goal", "summary", "steps"]
    assert schema["additionalProperties"] is False
    steps = schema["properties"]["steps"]
    assert steps["minItems"] == steps["maxItems"] == 2
    step_schema = steps["items"]
    assert step_schema["required"] == [
        "step",
        "agent_id",
        "role",
        "task",
        "risk",
        "requires_approval",
    ]
    assert step_schema["additionalProperties"] is False
    assert step_schema["properties"]["agent_id"]["enum"] == ["reviewer", "planner"]
    assert step_schema["properties"]["requires_approval"] == {"type": "boolean", "const": True}


def test_schema_hash_is_deterministic_and_project_path_free() -> None:
    request = _request()
    other_path_request = replace(request, config=replace(request.config, root="/secret/other-project"))

    schema = build_leader_plan_schema(request)
    other_schema = build_leader_plan_schema(other_path_request)
    digest = canonical_leader_plan_schema_hash(schema)
    encoded = json.dumps(
        schema,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    expected = f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    assert schema == other_schema
    assert "/private/project-a" not in repr(schema)
    assert "/secret/other-project" not in repr(schema)
    assert digest == expected
    assert digest == canonical_leader_plan_schema_hash(other_schema)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest)


@pytest.mark.parametrize(
    ("selected", "count"),
    [
        (("planner", "reviewer"), None),
        (None, 2),
        ("planner", 2),
        ((["planner"], "reviewer"), 2),
        (("planner",), 2),
        (("planner", "planner"), 2),
        (("planner", "ghost"), 2),
        (("planner", "reviewer"), True),
        (("planner", "reviewer"), 1),
        (("planner", "reviewer"), 65),
    ],
)
def test_invalid_authority_is_reported_without_leaking_type_errors(
    selected: object, count: object
) -> None:
    request = _request(selected_agent_ids=selected, step_count=count)

    with pytest.raises(ProviderPlanValidationError) as raised:
        leader_plan_authority(request)

    assert raised.value.code == "authority_invalid"
    assert str(raised.value) == "provider plan authority is invalid"


def test_default_authority_uses_all_configured_workers() -> None:
    request = _request(selected_agent_ids=None, step_count=None)

    assert leader_plan_authority(request) == (("planner", "reviewer", "builder"), 3)


def _valid_plan() -> dict[str, object]:
    return {
        "goal": "Deliver the mission",
        "summary": "Use only the frozen workers",
        "steps": [
            {
                "step": 1,
                "agent_id": "reviewer",
                "role": "review",
                "task": "Review the requirements",
                "risk": "Human approval remains required",
                "requires_approval": True,
            },
            {
                "step": 2,
                "agent_id": "planner",
                "role": "planning",
                "task": "Prepare the implementation plan",
                "risk": "Human approval remains required",
                "requires_approval": True,
            },
        ],
    }


def _missing_goal(plan: dict[str, object]) -> None:
    plan.pop("goal")


def _empty_goal(plan: dict[str, object]) -> None:
    plan["goal"] = " "


def _empty_steps(plan: dict[str, object]) -> None:
    plan["steps"] = []


def _wrong_step_count(plan: dict[str, object]) -> None:
    plan["steps"] = plan["steps"][:1]


def _unknown_agent(plan: dict[str, object]) -> None:
    plan["steps"][0]["agent_id"] = "builder"


def _wrong_role(plan: dict[str, object]) -> None:
    plan["steps"][0]["role"] = "planning"


def _approval_false(plan: dict[str, object]) -> None:
    plan["steps"][0]["requires_approval"] = False


def _duplicate_step(plan: dict[str, object]) -> None:
    plan["steps"][1]["step"] = 1


def _misnumbered_step(plan: dict[str, object]) -> None:
    plan["steps"][1]["step"] = 3


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (_missing_goal, "missing_required_field"),
        (_empty_goal, "invalid_string_field"),
        (_empty_steps, "invalid_step_count"),
        (_wrong_step_count, "invalid_step_count"),
        (_unknown_agent, "unknown_agent"),
        (_wrong_role, "role_mismatch"),
        (_approval_false, "approval_not_required"),
        (_duplicate_step, "invalid_step_numbering"),
        (_misnumbered_step, "invalid_step_numbering"),
    ],
)
def test_semantic_validation_reports_stable_diagnostic_codes(mutate, code: str) -> None:
    plan = _valid_plan()
    mutate(plan)

    with pytest.raises(ProviderPlanValidationError) as raised:
        validate_provider_plan_schema(
            plan,
            config=_config(),
            selected_agent_ids=("reviewer", "planner"),
            step_count=2,
        )

    assert raised.value.code == code


@pytest.mark.parametrize(
    ("plan", "code"),
    [
        ([], "invalid_top_level_type"),
        ({"goal": "g", "summary": "s", "steps": ["not-an-object", "also-not"]}, "invalid_step_type"),
    ],
)
def test_semantic_validation_rejects_invalid_container_types(plan: object, code: str) -> None:
    with pytest.raises(ProviderPlanValidationError) as raised:
        validate_provider_plan_schema(
            plan,
            config=_config(),
            selected_agent_ids=("reviewer", "planner"),
            step_count=2,
        )

    assert raised.value.code == code


def test_semantic_validation_normalizes_output_envelope() -> None:
    plan = _valid_plan()
    plan["approval_required"] = False
    plan["dispatch_ready"] = True

    normalized = validate_provider_plan_schema(
        plan,
        config=_config(),
        selected_agent_ids=("reviewer", "planner"),
        step_count=2,
    )

    assert normalized is plan
    assert normalized["approval_required"] is True
    assert normalized["dispatch_ready"] is False


def test_legacy_validation_keeps_human_readable_exception_text() -> None:
    plan = deepcopy(_valid_plan())
    plan["steps"][0].pop("agent_id")

    with pytest.raises(ProviderPlanValidationError) as raised:
        validate_provider_plan_schema(plan, config=_config())

    assert raised.value.code == "missing_required_field"
    assert str(raised.value) == "provider plan step 1 missing required field: agent_id"


def _four_step_request() -> LeaderPlanRequest:
    config = _config()
    config = replace(
        config,
        agents=config.agents
        + (AgentSpec("writer", "documentation", "claude-cli", "claude"),),
    )
    return LeaderPlanRequest(
        task="Complete a four-step mission",
        config=config,
        model="leader-model-v1",
        selected_agent_ids=("builder", "reviewer", "planner", "writer"),
        step_count=4,
    )


def test_generation_provenance_normalizes_exact_authority_and_schema() -> None:
    request = _four_step_request()
    schema = build_leader_plan_schema(request)

    assert build_leader_generation_provenance(
        request=request,
        provider="codex-cli",
        constraint_mode="native_json_schema",
        schema=schema,
        attempt_count=1,
    ) == {
        "provider": "codex-cli",
        "model": "leader-model-v1",
        "constraint_mode": "native_json_schema",
        "schema_version": "leader-plan/v1",
        "schema_hash": canonical_leader_plan_schema_hash(schema),
        "attempt_count": 1,
        "regeneration_used": False,
        "selected_agent_ids": ["builder", "reviewer", "planner", "writer"],
        "step_count": 4,
    }


def test_generation_provenance_supports_schema_free_local_legacy_mode() -> None:
    request = LeaderPlanRequest(task="Legacy local plan", config=_config())

    assert build_leader_generation_provenance(
        request=request,
        provider="fake",
        constraint_mode="local",
    ) == {
        "provider": "fake",
        "model": None,
        "constraint_mode": "local",
        "schema_version": None,
        "schema_hash": None,
        "attempt_count": 1,
        "regeneration_used": False,
        "selected_agent_ids": ["planner", "reviewer", "builder"],
        "step_count": 3,
    }


def test_generation_provenance_records_one_regeneration() -> None:
    request = _four_step_request()

    provenance = build_leader_generation_provenance(
        request=request,
        provider="claude-cli",
        constraint_mode="prompt_only",
        attempt_count=2,
    )

    assert provenance["attempt_count"] == 2
    assert provenance["regeneration_used"] is True


class _StringSubclass(str):
    pass


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"provider": ""}, "leader generation provider must be a non-empty string"),
        ({"provider": 7}, "leader generation provider must be a non-empty string"),
        (
            {"provider": _StringSubclass("codex-cli")},
            "leader generation provider must be a non-empty string",
        ),
        ({"constraint_mode": "unknown"}, "leader generation constraint mode is invalid"),
        (
            {"constraint_mode": _StringSubclass("local")},
            "leader generation constraint mode is invalid",
        ),
        ({"schema": []}, "leader generation schema must be a JSON object or null"),
        ({"schema": "mismatch"}, "leader generation schema does not match request authority"),
        ({"attempt_count": 0}, "leader generation attempt count is invalid"),
        ({"attempt_count": 3}, "leader generation attempt count is invalid"),
        ({"attempt_count": True}, "leader generation attempt count is invalid"),
    ],
)
def test_generation_provenance_rejects_invalid_inputs_without_echoing_values(
    overrides: dict[str, object], message: str
) -> None:
    request = _four_step_request()
    schema = build_leader_plan_schema(request)
    values: dict[str, object] = {
        "request": request,
        "provider": "codex-cli",
        "constraint_mode": "native_json_schema",
        "schema": schema,
        "attempt_count": 1,
    }
    if overrides.get("schema") == "mismatch":
        mismatched_schema = deepcopy(schema)
        mismatched_schema["properties"]["steps"]["maxItems"] = 3
        overrides = {"schema": mismatched_schema}
    values.update(overrides)

    with pytest.raises(ValueError) as raised:
        build_leader_generation_provenance(**values)

    assert str(raised.value) == message


@pytest.mark.parametrize(
    "mutation",
    ["boolean_as_integer", "integer_as_float", "non_json_value"],
)
def test_generation_provenance_rejects_noncanonical_schema_types(mutation: str) -> None:
    request = _four_step_request()
    schema = deepcopy(build_leader_plan_schema(request))
    if mutation == "boolean_as_integer":
        schema["properties"]["steps"]["items"]["properties"]["requires_approval"]["const"] = 1
    elif mutation == "integer_as_float":
        schema["properties"]["steps"]["maxItems"] = 4.0
    else:
        schema["properties"]["steps"]["maxItems"] = {4}

    with pytest.raises(ValueError) as raised:
        build_leader_generation_provenance(
            request=request,
            provider="codex-cli",
            constraint_mode="native_json_schema",
            schema=schema,
        )

    assert str(raised.value) == "leader generation schema does not match request authority"
