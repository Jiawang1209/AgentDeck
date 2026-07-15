from __future__ import annotations

from typing import cast

from agentdeck.semantic_authority import (
    SEMANTIC_AUTHORITY_SCHEMA_VERSION,
    SEMANTIC_OPERATIONS,
    SEMANTIC_PROPOSED_EFFECTS_MAX,
    SemanticAuthorityError,
    semantic_authority_hash,
    semantic_text_contains_sensitive_value,
    validate_semantic_authority,
)
from agentdeck.semantic_planning import semantic_context_text_is_safe


SEMANTIC_LEADER_PLAN_SCHEMA_VERSION = "leader-semantic-plan/v1"

_MAX_STEPS = 64
_MAX_TEXT_LENGTH = 4096
_PHASE_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}"


class SemanticPlanSchemaAuthorityError(ValueError):
    """Closed failure used by the provider-neutral schema boundary."""

    def __init__(self) -> None:
        super().__init__("semantic plan schema authority is invalid")


def _fail() -> None:
    raise SemanticPlanSchemaAuthorityError()


def _contains_sensitive_value(value: object) -> bool:
    stack = [value]
    nodes = 0
    while stack:
        current = stack.pop()
        nodes += 1
        if nodes > 20_000:
            _fail()
        if type(current) is dict:
            mapping = cast(dict[object, object], current)
            if any(type(key) is not str for key in mapping):
                _fail()
            if mapping.get("sensitivity") == "secret_ref":
                return True
            stack.extend(mapping.values())
        elif type(current) is list:
            stack.extend(cast(list[object], current))
        elif type(current) is str and semantic_text_contains_sensitive_value(current):
            return True
    return False


def _normalize_authority(
    *,
    semantic_authority: object,
    selected_agent_ids: object,
    roles: object,
    step_count: object,
) -> tuple[dict[str, object], tuple[str, ...], dict[str, str], int, tuple[tuple[str, ...], ...]]:
    if type(semantic_authority) is not dict:
        _fail()
    try:
        authority = cast(dict[str, object], validate_semantic_authority(semantic_authority))
    except SemanticAuthorityError:
        _fail()
    if authority["unresolved"] or authority["proposed_effects"]:
        _fail()
    if _contains_sensitive_value(authority):
        _fail()
    if (
        type(selected_agent_ids) is not tuple
        or type(step_count) is not int
        or step_count < 2
        or step_count > _MAX_STEPS
        or len(selected_agent_ids) < 2
        or any(
            not semantic_context_text_is_safe(agent_id)
            for agent_id in selected_agent_ids
        )
        or type(roles) is not dict
    ):
        _fail()
    agents = cast(tuple[str, ...], selected_agent_ids)
    if len(set(agents)) != len(agents):
        _fail()
    role_values = cast(dict[object, object], roles)
    if (
        any(not semantic_context_text_is_safe(key) for key in role_values)
        or set(role_values) != set(agents)
        or any(not semantic_context_text_is_safe(value) for value in role_values.values())
    ):
        _fail()
    role_map = cast(dict[str, str], role_values)

    requirements = cast(list[dict[str, object]], authority["requirements"])
    phase_agent_pairs: list[tuple[str, str]] = []
    for requirement in requirements:
        pair = cast(str, requirement["phase"]), cast(str, requirement["agent_id"])
        if pair not in phase_agent_pairs:
            phase_agent_pairs.append(pair)
    if requirements:
        if len(phase_agent_pairs) != step_count:
            _fail()
        for index, (_, agent_id) in enumerate(phase_agent_pairs):
            if agent_id != agents[index % len(agents)]:
                _fail()

    phases_by_step: list[tuple[str, ...]] = []
    for index in range(step_count):
        expected_agent = agents[index % len(agents)]
        phases: list[str] = []
        for phase, agent_id in phase_agent_pairs:
            if agent_id == expected_agent and phase not in phases:
                phases.append(phase)
        phases_by_step.append(tuple(phases))
    return authority, agents, role_map, step_count, tuple(phases_by_step)


def semantic_authority_identity(semantic_authority: object) -> tuple[str, str]:
    """Return the closed schema/hash identity for a usable draft authority."""
    if type(semantic_authority) is not dict:
        _fail()
    try:
        authority = validate_semantic_authority(semantic_authority)
        if authority["unresolved"] or authority["proposed_effects"]:
            _fail()
        if _contains_sensitive_value(authority):
            _fail()
        return SEMANTIC_AUTHORITY_SCHEMA_VERSION, semantic_authority_hash(authority)
    except SemanticAuthorityError:
        _fail()


def _phase_schema(phases: tuple[str, ...]) -> dict[str, object]:
    if phases:
        return {"type": "string", "enum": list(phases)}
    return {
        "type": "string",
        "pattern": _PHASE_PATTERN,
        "minLength": 1,
        "maxLength": 128,
    }


def _step_schema(
    *,
    index: int,
    agent_id: str,
    role: str,
    phases: tuple[str, ...],
    requirement_ids: tuple[str, ...],
) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "step": {"type": "integer", "const": index},
            "agent_id": {"type": "string", "const": agent_id},
            "role": {"type": "string", "const": role},
            "phase": _phase_schema(phases),
            "authority_refs": {
                "type": "array",
                "items": {"type": "string", "enum": list(requirement_ids)},
                "minItems": 0,
                "maxItems": len(requirement_ids),
                "uniqueItems": True,
            },
            "proposed_effects": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 1024,
                        },
                        "operation": {
                            "type": "string",
                            "enum": sorted(SEMANTIC_OPERATIONS),
                        },
                        "sensitivity": {
                            "type": "string",
                            "const": "ordinary",
                        },
                    },
                    "required": ["target", "operation", "sensitivity"],
                    "additionalProperties": False,
                },
                "minItems": 0,
                "maxItems": SEMANTIC_PROPOSED_EFFECTS_MAX,
            },
            "verification": {
                "type": "string",
                "minLength": 1,
                "maxLength": _MAX_TEXT_LENGTH,
            },
            "risk": {"type": "string", "const": "low"},
            "requires_approval": {"type": "boolean", "const": True},
        },
        "required": [
            "step",
            "agent_id",
            "role",
            "phase",
            "authority_refs",
            "proposed_effects",
            "verification",
            "risk",
            "requires_approval",
        ],
        "additionalProperties": False,
    }


def build_semantic_leader_plan_schema(
    *,
    semantic_authority: object,
    selected_agent_ids: object,
    roles: object,
    step_count: object,
) -> dict[str, object]:
    """Build a pure, non-executable native schema from closed authority metadata."""
    authority, agents, role_map, count, phases_by_step = _normalize_authority(
        semantic_authority=semantic_authority,
        selected_agent_ids=selected_agent_ids,
        roles=roles,
        step_count=step_count,
    )
    requirement_ids = tuple(
        cast(str, requirement["requirement_id"])
        for requirement in cast(list[dict[str, object]], authority["requirements"])
    )
    prefix_items = [
        _step_schema(
            index=index,
            agent_id=agents[(index - 1) % len(agents)],
            role=role_map[agents[(index - 1) % len(agents)]],
            phases=phases_by_step[index - 1],
            requirement_ids=requirement_ids,
        )
        for index in range(1, count + 1)
    ]
    return {
        "$id": SEMANTIC_LEADER_PLAN_SCHEMA_VERSION,
        "type": "object",
        "properties": {
            "goal": {"type": "string", "minLength": 1, "maxLength": _MAX_TEXT_LENGTH},
            "summary": {"type": "string", "minLength": 1, "maxLength": _MAX_TEXT_LENGTH},
            "steps": {
                "type": "array",
                "prefixItems": prefix_items,
                "items": False,
                "minItems": count,
                "maxItems": count,
            },
        },
        "required": ["goal", "summary", "steps"],
        "additionalProperties": False,
    }
