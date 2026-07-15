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
_PHASE_START_PATTERN = r"^[A-Za-z0-9]"
_PHASE_INVALID_PATTERN = r"[^A-Za-z0-9._:-]"


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


def _normalize_selected_context(
    selected_agent_ids: object,
    step_count: object,
) -> tuple[tuple[str, ...], int]:
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
    ):
        _fail()
    agents = cast(tuple[str, ...], selected_agent_ids)
    if len(set(agents)) != len(agents):
        _fail()
    return agents, step_count


def resolve_semantic_leader_plan_context(
    *,
    selected_agent_ids: object,
    step_count: object,
    configured_context: object,
) -> tuple[tuple[str, ...], dict[str, str], int]:
    """Resolve only selected safe config entries without touching unrelated values."""
    agents, count = _normalize_selected_context(selected_agent_ids, step_count)
    if type(configured_context) is not tuple:
        _fail()
    selected = set(agents)
    roles: dict[str, str] = {}
    for entry in configured_context:
        if type(entry) is not tuple or len(entry) != 2:
            _fail()
        agent_id, role = entry
        if type(agent_id) is not str:
            continue
        if not semantic_context_text_is_safe(agent_id):
            continue
        if agent_id not in selected:
            continue
        if not semantic_context_text_is_safe(role) or agent_id in roles:
            _fail()
        roles[agent_id] = cast(str, role)
    if len(roles) != len(agents):
        _fail()
    return agents, {agent_id: roles[agent_id] for agent_id in agents}, count


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
    agents, count = _normalize_selected_context(selected_agent_ids, step_count)
    if type(roles) is not dict:
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
        if len(phase_agent_pairs) != count:
            _fail()
        for index, (_, agent_id) in enumerate(phase_agent_pairs):
            if agent_id != agents[index % len(agents)]:
                _fail()

    phases_by_step: list[tuple[str, ...]] = []
    for index in range(count):
        expected_agent = agents[index % len(agents)]
        phases: list[str] = []
        for phase, agent_id in phase_agent_pairs:
            if agent_id == expected_agent and phase not in phases:
                phases.append(phase)
        phases_by_step.append(tuple(phases))
    return authority, agents, role_map, count, tuple(phases_by_step)


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
        "pattern": _PHASE_START_PATTERN,
        "not": {"pattern": _PHASE_INVALID_PATTERN},
        "minLength": 1,
        "maxLength": 128,
    }


def _step_schema(
    *,
    index: int,
    agent_id: str,
    role: str,
    phase_schema: dict[str, object],
    authority_refs_schema: dict[str, object],
    authority_scope_ref: str | None,
) -> dict[str, object]:
    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "step": {"type": "integer", "const": index},
            "agent_id": {"type": "string", "const": agent_id},
            "role": {"type": "string", "const": role},
            "phase": phase_schema,
            "authority_refs": authority_refs_schema,
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
    if authority_scope_ref is not None:
        schema["allOf"] = [{"$ref": authority_scope_ref}]
    return schema


def _semantic_schema_definitions(
    *,
    authority: dict[str, object],
    agents: tuple[str, ...],
) -> tuple[dict[str, object], dict[str, str], dict[str, str]]:
    empty_name = "authority_refs_empty"
    definitions: dict[str, object] = {
        empty_name: {
            "type": "array",
            "items": False,
            "minItems": 0,
            "maxItems": 0,
            "uniqueItems": True,
        }
    }
    requirements = cast(list[dict[str, object]], authority["requirements"])
    grouped: dict[tuple[str, str], list[str]] = {}
    for requirement in requirements:
        key = cast(str, requirement["agent_id"]), cast(str, requirement["phase"])
        grouped.setdefault(key, []).append(cast(str, requirement["requirement_id"]))

    refs_names: dict[tuple[str, str], str] = {}
    for index, (key, requirement_ids) in enumerate(grouped.items(), start=1):
        name = f"authority_refs_{index:04d}"
        refs_names[key] = name
        definitions[name] = {
            "type": "array",
            "items": {"type": "string", "enum": requirement_ids},
            "minItems": 0,
            "maxItems": len(requirement_ids),
            "uniqueItems": True,
        }

    phase_names: dict[str, str] = {}
    scope_names: dict[str, str] = {}
    for index, agent_id in enumerate(agents, start=1):
        phases = tuple(phase for candidate, phase in grouped if candidate == agent_id)
        if not phases:
            continue
        phase_name = f"phase_{index:04d}"
        scope_name = f"authority_scope_{index:04d}"
        phase_names[agent_id] = phase_name
        scope_names[agent_id] = scope_name
        definitions[phase_name] = {"type": "string", "enum": list(phases)}
        definitions[scope_name] = {
            "allOf": [
                {
                    "if": {
                        "properties": {"phase": {"const": phase}},
                        "required": ["phase"],
                    },
                    "then": {
                        "properties": {
                            "authority_refs": {
                                "$ref": f"#/$defs/{refs_names[(agent_id, phase)]}"
                            }
                        }
                    },
                }
                for phase in phases
            ]
        }
    return definitions, phase_names, scope_names


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
    definitions, phase_names, scope_names = _semantic_schema_definitions(
        authority=authority,
        agents=agents,
    )
    requirement_count = len(cast(list[object], authority["requirements"]))
    prefix_items = [
        _step_schema(
            index=index,
            agent_id=agents[(index - 1) % len(agents)],
            role=role_map[agents[(index - 1) % len(agents)]],
            phase_schema=(
                {"$ref": f"#/$defs/{phase_names[agents[(index - 1) % len(agents)]]}"}
                if phases_by_step[index - 1]
                else _phase_schema(())
            ),
            authority_refs_schema=(
                {
                    "type": "array",
                    "minItems": 0,
                    "maxItems": requirement_count,
                    "uniqueItems": True,
                }
                if phases_by_step[index - 1]
                else {"$ref": "#/$defs/authority_refs_empty"}
            ),
            authority_scope_ref=(
                f"#/$defs/{scope_names[agents[(index - 1) % len(agents)]]}"
                if phases_by_step[index - 1]
                else None
            ),
        )
        for index in range(1, count + 1)
    ]
    return {
        "$id": SEMANTIC_LEADER_PLAN_SCHEMA_VERSION,
        "$defs": definitions,
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
