from __future__ import annotations

from dataclasses import replace
from copy import deepcopy
import re

import pytest

from agentdeck.models import AgentSpec, LeaderConfig, ProjectConfig, RuntimeConfig
from agentdeck.providers.base import LeaderPlanRequest
from agentdeck.providers.plan_schema import (
    LEADER_PLAN_SCHEMA_VERSION,
    ProviderPlanValidationError,
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

    assert schema == other_schema
    assert "/private/project-a" not in repr(schema)
    assert "/secret/other-project" not in repr(schema)
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
