from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, cast

from agentdeck.models import ProjectConfig
from agentdeck.semantic_planning import SEMANTIC_FAILURE_CODES
from .semantic_plan_schema import (
    SEMANTIC_LEADER_PLAN_SCHEMA_VERSION,
    SemanticPlanSchemaAuthorityError,
    build_semantic_leader_plan_schema,
    resolve_semantic_leader_plan_context,
    semantic_authority_identity,
)

if TYPE_CHECKING:
    from .base import LeaderPlanRequest


LEADER_PLAN_SCHEMA_VERSION = "leader-plan/v1"
LEADER_CONSTRAINT_MODES = frozenset(
    {"local", "json_object", "prompt_only", "native_json_schema"}
)
LEADER_PLAN_DIAGNOSTIC_CODES = frozenset(
    {
        "missing_required_field",
        "invalid_top_level_type",
        "invalid_string_field",
        "invalid_step_type",
        "invalid_step_count",
        "invalid_step_numbering",
        "unknown_agent",
        "role_mismatch",
        "approval_not_required",
        "invalid_output_envelope",
        "native_schema_unavailable",
        "authority_invalid",
    }
) | SEMANTIC_FAILURE_CODES
PROVIDER_PLAN_REQUIRED_FIELDS = ("goal", "summary", "steps")
PROVIDER_PLAN_STRING_FIELDS = ("goal", "summary")
PROVIDER_PLAN_STEP_REQUIRED_FIELDS = ("step", "agent_id", "role", "task", "risk", "requires_approval")
PROVIDER_PLAN_STEP_STRING_FIELDS = ("agent_id", "role", "task", "risk")


class ProviderPlanValidationError(RuntimeError):
    def __init__(self, code: str, message: str | None = None) -> None:
        if type(code) is not str or code not in LEADER_PLAN_DIAGNOSTIC_CODES:
            raise ValueError("unknown provider plan diagnostic code")
        self.code = code
        super().__init__(message or "provider plan authority is invalid")


def leader_plan_authority(request: LeaderPlanRequest) -> tuple[tuple[str, ...], int]:
    selected = request.selected_agent_ids
    count = request.step_count
    if (selected is None) != (count is None):
        raise ProviderPlanValidationError("authority_invalid")
    if selected is None:
        selected = tuple(agent.agent_id for agent in request.config.agents)
        count = len(request.config.agents)
    configured = {agent.agent_id for agent in request.config.agents}
    if (
        type(selected) is not tuple
        or type(count) is not int
        or count < 2
        or count > 64
        or len(selected) < 2
        or any(type(item) is not str or not item or item not in configured for item in selected)
        or len(set(selected)) != len(selected)
    ):
        raise ProviderPlanValidationError("authority_invalid")
    return selected, count


def _validate_regeneration_diagnostic(request: LeaderPlanRequest) -> None:
    diagnostic = request.regeneration_diagnostic
    if diagnostic is not None and (
        type(diagnostic) is not str or diagnostic not in SEMANTIC_FAILURE_CODES
    ):
        raise ProviderPlanValidationError("authority_invalid")


def build_leader_plan_schema(request: LeaderPlanRequest) -> dict[str, object]:
    _validate_regeneration_diagnostic(request)
    if request.semantic_authority is not None:
        try:
            selected_agent_ids, selected_roles, step_count = (
                resolve_semantic_leader_plan_context(
                    selected_agent_ids=request.selected_agent_ids,
                    step_count=request.step_count,
                    configured_context=request.config.agents,
                )
            )
            return build_semantic_leader_plan_schema(
                semantic_authority=request.semantic_authority,
                selected_agent_ids=selected_agent_ids,
                roles=selected_roles,
                step_count=step_count,
            )
        except (SemanticPlanSchemaAuthorityError, TypeError, ValueError):
            raise ProviderPlanValidationError("authority_invalid") from None
    selected_agent_ids, step_count = leader_plan_authority(request)
    return {
        "$id": LEADER_PLAN_SCHEMA_VERSION,
        "type": "object",
        "properties": {
            "goal": {"type": "string", "minLength": 1},
            "summary": {"type": "string", "minLength": 1},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "step": {"type": "integer", "minimum": 1},
                        "agent_id": {"type": "string", "enum": list(selected_agent_ids)},
                        "role": {"type": "string", "minLength": 1},
                        "task": {"type": "string", "minLength": 1},
                        "risk": {"type": "string", "minLength": 1},
                        "requires_approval": {"type": "boolean", "const": True},
                    },
                    "required": [
                        "step",
                        "agent_id",
                        "role",
                        "task",
                        "risk",
                        "requires_approval",
                    ],
                    "additionalProperties": False,
                },
                "minItems": step_count,
                "maxItems": step_count,
            },
        },
        "required": ["goal", "summary", "steps"],
        "additionalProperties": False,
    }


def _canonical_leader_plan_schema_bytes(schema: dict[str, object]) -> bytes:
    if type(schema) is not dict:
        raise ValueError("leader plan schema is invalid")
    stack: list[tuple[object, int]] = [(schema, 0)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > 100_000 or depth > 32:
            raise ValueError("leader plan schema is invalid")
        if type(value) is dict:
            mapping = cast(dict[object, object], value)
            if len(mapping) > 10_000 or any(type(key) is not str for key in mapping):
                raise ValueError("leader plan schema is invalid")
            stack.extend((child, depth + 1) for child in mapping.values())
        elif type(value) is list:
            items = cast(list[object], value)
            if len(items) > 10_000:
                raise ValueError("leader plan schema is invalid")
            stack.extend((child, depth + 1) for child in items)
        elif type(value) not in {str, int, bool, type(None)}:
            raise ValueError("leader plan schema is invalid")
    try:
        return json.dumps(
            schema,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, OverflowError):
        raise ValueError("leader plan schema is invalid") from None


def canonical_leader_plan_schema_hash(schema: dict[str, object]) -> str:
    return f"sha256:{hashlib.sha256(_canonical_leader_plan_schema_bytes(schema)).hexdigest()}"


def build_leader_generation_provenance(
    *,
    request: LeaderPlanRequest,
    provider: str,
    constraint_mode: str,
    schema: dict[str, object] | None = None,
    attempt_count: int = 1,
) -> dict[str, object]:
    if type(provider) is not str or not provider.strip():
        raise ValueError("leader generation provider must be a non-empty string")
    if type(constraint_mode) is not str or constraint_mode not in LEADER_CONSTRAINT_MODES:
        raise ValueError("leader generation constraint mode is invalid")
    if schema is not None and type(schema) is not dict:
        raise ValueError("leader generation schema must be a JSON object or null")
    if type(attempt_count) is not int or attempt_count < 1 or attempt_count > 2:
        raise ValueError("leader generation attempt count is invalid")

    try:
        _validate_regeneration_diagnostic(request)
    except ProviderPlanValidationError:
        raise ValueError("leader generation request authority is invalid") from None
    semantic_expected_schema: dict[str, object] | None = None
    if request.semantic_authority is not None:
        try:
            semantic_expected_schema = build_leader_plan_schema(request)
        except ProviderPlanValidationError:
            raise ValueError("leader generation request authority is invalid") from None
        selected_agent_ids = request.selected_agent_ids
        step_count = request.step_count
        if type(selected_agent_ids) is not tuple or type(step_count) is not int:
            raise ValueError("leader generation request authority is invalid")
    else:
        selected_agent_ids, step_count = leader_plan_authority(request)
    expected_schema: dict[str, object] | None = None
    if schema is not None:
        try:
            supplied_schema_bytes = _canonical_leader_plan_schema_bytes(schema)
            expected_schema = semantic_expected_schema or build_leader_plan_schema(request)
            expected_schema_bytes = _canonical_leader_plan_schema_bytes(expected_schema)
        except (ProviderPlanValidationError, TypeError, ValueError):
            raise ValueError(
                "leader generation schema does not match request authority"
            ) from None
        if supplied_schema_bytes != expected_schema_bytes:
            raise ValueError("leader generation schema does not match request authority")

    provenance = {
        "provider": provider,
        "model": request.model,
        "constraint_mode": constraint_mode,
        "schema_version": (
            SEMANTIC_LEADER_PLAN_SCHEMA_VERSION
            if schema is not None and request.semantic_authority is not None
            else LEADER_PLAN_SCHEMA_VERSION if schema is not None else None
        ),
        "schema_hash": (
            canonical_leader_plan_schema_hash(expected_schema)
            if expected_schema is not None
            else None
        ),
        "attempt_count": attempt_count,
        "regeneration_used": attempt_count > 1,
        "selected_agent_ids": list(selected_agent_ids),
        "step_count": step_count,
    }
    if request.semantic_authority is not None:
        try:
            authority_version, authority_hash = semantic_authority_identity(
                request.semantic_authority
            )
        except (SemanticPlanSchemaAuthorityError, TypeError, ValueError):
            raise ValueError("leader generation request authority is invalid") from None
        provenance["semantic_authority_schema_version"] = authority_version
        provenance["semantic_authority_hash"] = authority_hash
    return provenance


def _exact_json_value(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        left_dict = cast(dict[object, object], left)
        right_dict = cast(dict[object, object], right)
        return (
            len(left_dict) == len(right_dict)
            and all(type(key) is str and key in right_dict for key in left_dict)
            and all(
                _exact_json_value(value, right_dict[key])
                for key, value in left_dict.items()
            )
        )
    if type(left) is list:
        left_list = cast(list[object], left)
        right_list = cast(list[object], right)
        return len(left_list) == len(right_list) and all(
            _exact_json_value(left_item, right_item)
            for left_item, right_item in zip(left_list, right_list)
        )
    return type(left) in {str, int, bool, type(None)} and left == right


def validate_leader_generation_provenance(
    *,
    request: LeaderPlanRequest,
    provider: str,
    provenance: object,
) -> dict[str, object]:
    """Reconstruct and exactly validate compact generation provenance."""
    if type(provenance) is not dict:
        raise ValueError("leader generation provenance is invalid")
    candidate = cast(dict[object, object], provenance)
    constraint_mode = candidate.get("constraint_mode")
    attempt_count = candidate.get("attempt_count")
    if type(constraint_mode) is not str or type(attempt_count) is not int:
        raise ValueError("leader generation provenance is invalid")
    try:
        schema = (
            build_leader_plan_schema(request)
            if constraint_mode == "native_json_schema"
            else None
        )
        expected = build_leader_generation_provenance(
            request=request,
            provider=provider,
            constraint_mode=constraint_mode,
            schema=schema,
            attempt_count=attempt_count,
        )
    except (ProviderPlanValidationError, TypeError, ValueError):
        raise ValueError("leader generation provenance is invalid") from None
    if not _exact_json_value(provenance, expected):
        raise ValueError("leader generation provenance is invalid")
    return expected


def _validate_explicit_authority(
    selected_agent_ids: tuple[str, ...] | None,
    step_count: int | None,
    config: ProjectConfig | None,
) -> tuple[tuple[str, ...] | None, int | None]:
    if (selected_agent_ids is None) != (step_count is None):
        raise ProviderPlanValidationError("authority_invalid")
    if selected_agent_ids is None:
        return None, None
    configured = {agent.agent_id for agent in config.agents} if config is not None else None
    if (
        type(selected_agent_ids) is not tuple
        or type(step_count) is not int
        or step_count < 2
        or step_count > 64
        or len(selected_agent_ids) < 2
        or any(
            type(item) is not str
            or not item
            or (configured is not None and item not in configured)
            for item in selected_agent_ids
        )
        or len(set(selected_agent_ids)) != len(selected_agent_ids)
    ):
        raise ProviderPlanValidationError("authority_invalid")
    return selected_agent_ids, step_count


def validate_provider_plan_schema(
    plan: object,
    config: ProjectConfig | None = None,
    selected_agent_ids: tuple[str, ...] | None = None,
    step_count: int | None = None,
) -> dict[str, object]:
    selected_agent_ids, step_count = _validate_explicit_authority(
        selected_agent_ids,
        step_count,
        config,
    )
    if not isinstance(plan, dict):
        raise ProviderPlanValidationError(
            "invalid_top_level_type",
            "provider plan content must be a JSON object",
        )
    for field in PROVIDER_PLAN_REQUIRED_FIELDS:
        if field not in plan:
            raise ProviderPlanValidationError(
                "missing_required_field",
                f"provider plan missing required field: {field}",
            )
    for field in PROVIDER_PLAN_STRING_FIELDS:
        if type(plan.get(field)) is not str or not plan[field].strip():
            raise ProviderPlanValidationError(
                "invalid_string_field",
                f"provider plan field {field} must be a non-empty string",
            )
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ProviderPlanValidationError(
            "invalid_step_count",
            "provider plan must include non-empty steps",
        )
    if step_count is not None and len(steps) != step_count:
        raise ProviderPlanValidationError(
            "invalid_step_count",
            f"provider plan must include exactly {step_count} steps",
        )
    configured_agent_roles = {
        agent.agent_id: agent.role for agent in config.agents
    } if config is not None else None
    allowed_agent_ids = set(selected_agent_ids) if selected_agent_ids is not None else None
    seen_step_numbers: set[int] = set()
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise ProviderPlanValidationError(
                "invalid_step_type",
                f"provider plan step {index} must be an object",
            )
        for field in PROVIDER_PLAN_STEP_REQUIRED_FIELDS:
            if field not in step:
                raise ProviderPlanValidationError(
                    "missing_required_field",
                    f"provider plan step {index} missing required field: {field}",
                )
        if type(step.get("step")) is not int or step["step"] <= 0:
            raise ProviderPlanValidationError(
                "invalid_step_numbering",
                f"provider plan step {index} field step must be a positive integer",
            )
        step_number = step["step"]
        if step_number in seen_step_numbers:
            raise ProviderPlanValidationError(
                "invalid_step_numbering",
                f"provider plan step {index} duplicates step number: {step_number}",
            )
        seen_step_numbers.add(step_number)
        for field in PROVIDER_PLAN_STEP_STRING_FIELDS:
            if type(step.get(field)) is not str or not step[field].strip():
                raise ProviderPlanValidationError(
                    "invalid_string_field",
                    f"provider plan step {index} field {field} must be a non-empty string",
                )
        agent_id = step["agent_id"]
        if allowed_agent_ids is not None and agent_id not in allowed_agent_ids:
            raise ProviderPlanValidationError(
                "unknown_agent",
                f"provider plan step {index} agent_id is not selected by mission authority: {agent_id}",
            )
        if configured_agent_roles is not None and agent_id not in configured_agent_roles:
            raise ProviderPlanValidationError(
                "unknown_agent",
                f"provider plan step {index} agent_id is not configured: {agent_id}",
            )
        if configured_agent_roles is not None:
            expected_role = configured_agent_roles[agent_id]
            if step["role"] != expected_role:
                raise ProviderPlanValidationError(
                    "role_mismatch",
                    f"provider plan step {index} role does not match configured agent role for "
                    f"{agent_id}: expected {expected_role}, got {step['role']}",
                )
        if step.get("requires_approval") is not True:
            raise ProviderPlanValidationError(
                "approval_not_required",
                f"provider plan step {index} must require approval",
            )
    expected_step_numbers = set(range(1, len(steps) + 1))
    if seen_step_numbers != expected_step_numbers:
        raise ProviderPlanValidationError(
            "invalid_step_numbering",
            f"provider plan steps must be numbered 1..{len(steps)} without gaps",
        )
    plan["approval_required"] = True
    plan["dispatch_ready"] = False
    return plan
