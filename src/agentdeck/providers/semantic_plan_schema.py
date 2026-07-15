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
_PHASE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}(?![\s\S])"


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
        if type(entry) is not tuple:
            candidate = (
                entry
                if type(entry) is str
                else entry[0]
                if type(entry) is list and entry
                else None
            )
            if (
                type(candidate) is str
                and semantic_context_text_is_safe(candidate)
                and candidate in selected
            ):
                _fail()
            continue
        if len(entry) != 2:
            if (
                entry
                and type(entry[0]) is str
                and semantic_context_text_is_safe(entry[0])
                and entry[0] in selected
            ):
                _fail()
            continue
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
) -> tuple[dict[str, object], tuple[str, ...], dict[str, str], int]:
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

    return authority, agents, role_map, count


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


def _open_phase_schema() -> dict[str, object]:
    return {
        "type": "string",
        "pattern": _PHASE_PATTERN,
        "minLength": 1,
        "maxLength": 128,
    }


def _step_branch_schema(
    *,
    step_count: int,
    agent_id: str,
    role: str,
    phase_schema: dict[str, object],
    authority_refs_ref: str,
) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "step": {
                "type": "integer",
                "minimum": 1,
                "maximum": step_count,
            },
            "agent_id": {"type": "string", "const": agent_id},
            "role": {"type": "string", "const": role},
            "phase": phase_schema,
            "authority_refs": {"$ref": authority_refs_ref},
            "proposed_effects": {"$ref": "#/$defs/proposed_effects"},
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


def _semantic_schema_definitions(
    *,
    authority: dict[str, object],
    agents: tuple[str, ...],
    roles: dict[str, str],
    step_count: int,
) -> tuple[dict[str, object], tuple[str, ...]]:
    empty_name = "authority_refs_empty"
    definitions: dict[str, object] = {
        empty_name: {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 0,
            "maxItems": 0,
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
        }

    branch_names: list[str] = []
    if grouped:
        for index, (agent_id, phase) in enumerate(grouped, start=1):
            name = f"step_branch_{index:04d}"
            branch_names.append(name)
            definitions[name] = _step_branch_schema(
                step_count=step_count,
                agent_id=agent_id,
                role=roles[agent_id],
                phase_schema={"type": "string", "const": phase},
                authority_refs_ref=f"#/$defs/{refs_names[(agent_id, phase)]}",
            )
    else:
        for index, agent_id in enumerate(agents, start=1):
            name = f"step_branch_{index:04d}"
            branch_names.append(name)
            definitions[name] = _step_branch_schema(
                step_count=step_count,
                agent_id=agent_id,
                role=roles[agent_id],
                phase_schema=_open_phase_schema(),
                authority_refs_ref="#/$defs/authority_refs_empty",
            )
    return definitions, tuple(branch_names)


def build_semantic_leader_plan_schema(
    *,
    semantic_authority: object,
    selected_agent_ids: object,
    roles: object,
    step_count: object,
) -> dict[str, object]:
    """Build a pure, non-executable native schema from closed authority metadata."""
    authority, agents, role_map, count = _normalize_authority(
        semantic_authority=semantic_authority,
        selected_agent_ids=selected_agent_ids,
        roles=roles,
        step_count=step_count,
    )
    definitions, branch_names = _semantic_schema_definitions(
        authority=authority,
        agents=agents,
        roles=role_map,
        step_count=count,
    )
    return {
        "$id": SEMANTIC_LEADER_PLAN_SCHEMA_VERSION,
        "$defs": definitions,
        "type": "object",
        "properties": {
            "goal": {"type": "string", "minLength": 1, "maxLength": _MAX_TEXT_LENGTH},
            "summary": {"type": "string", "minLength": 1, "maxLength": _MAX_TEXT_LENGTH},
            "steps": {
                "type": "array",
                "items": {
                    "anyOf": [
                        {"$ref": f"#/$defs/{name}"}
                        for name in branch_names
                    ]
                },
                "minItems": count,
                "maxItems": count,
            },
        },
        "required": ["goal", "summary", "steps"],
        "additionalProperties": False,
    }
