"""Closed proposal schema and shared Leader proposal field bounds."""

from copy import deepcopy

from agentdeck.kernel.agents import AgentRole
from agentdeck.kernel.permissions import Effect, PermissionProfile


TEXT_MAX_BYTES = 4096
GOAL_MAX_BYTES = 65536
BUDGET_CEILINGS = {
    "max_leader_schema_repairs": 1,
    "max_attempts": 2,
    "max_acp_reconnects": 1,
    "max_revision_cycles": 1,
    "max_final_acceptance_attempts": 1,
}
PROPOSAL_FIELDS = {
    "draft_id", "objective", "scope", "project_root", "leader_backend",
    "leader_adapter", "leader_model", "leader_version", "permission_profile",
    "tasks", "acceptance_criteria", "non_goals", "risks", "budgets",
}
TASK_FIELDS = {
    "task_id", "name", "role", "backend", "agent_instance_id", "acp_route",
    "dependencies", "allowed_effects", "expected_outputs", "acceptance_criteria",
}


def leader_proposal_json_schema() -> dict[str, object]:
    """Return a fresh exact JSON Schema for adapter structured output."""
    string = {"type": "string", "minLength": 1, "maxLength": TEXT_MAX_BYTES}
    string_array = {"type": "array", "items": string, "maxItems": 64}
    task = {
        "type": "object", "additionalProperties": False,
        "required": sorted(TASK_FIELDS),
        "properties": {
            "task_id": string, "name": string,
            "role": {"enum": [role.value for role in AgentRole if role is not AgentRole.LEADER]},
            "backend": string, "agent_instance_id": string, "acp_route": string,
            "dependencies": string_array,
            "allowed_effects": {
                "type": "array", "minItems": 1, "maxItems": 64,
                "items": {"enum": [effect.value for effect in Effect]},
            },
            "expected_outputs": string_array, "acceptance_criteria": string_array,
        },
    }
    schema = {
        "type": "object", "additionalProperties": False,
        "required": sorted(PROPOSAL_FIELDS),
        "properties": {
            "draft_id": string,
            "objective": {"type": "string", "minLength": 1, "maxLength": GOAL_MAX_BYTES},
            "scope": {"const": "project"}, "project_root": string,
            "leader_backend": string, "leader_adapter": string,
            "leader_model": string, "leader_version": string,
            "permission_profile": {"enum": [profile.value for profile in PermissionProfile]},
            "tasks": {"type": "array", "items": task, "minItems": 4, "maxItems": 4},
            "acceptance_criteria": string_array, "non_goals": string_array,
            "risks": string_array,
            "budgets": {
                "type": "object", "additionalProperties": False,
                "required": sorted(BUDGET_CEILINGS),
                "properties": {
                    name: {"type": "integer", "minimum": 1, "maximum": maximum}
                    for name, maximum in BUDGET_CEILINGS.items()
                },
            },
        },
    }
    return deepcopy(schema)
