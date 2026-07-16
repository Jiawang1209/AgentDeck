from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
import unicodedata
from typing import Any, NoReturn

from agentdeck.semantic_authority import (
    SEMANTIC_AUTHORITY_SCHEMA_VERSION,
    SEMANTIC_PROPOSED_EFFECTS_MAX,
    SemanticAuthorityError,
    semantic_text_contains_sensitive_value,
    validate_semantic_authority,
)


SEMANTIC_FAILURE_CODES = frozenset(
    {
        "semantic_authority_unresolved",
        "semantic_authority_sensitive_value",
        "semantic_candidate_missing_requirement",
        "semantic_candidate_duplicate_requirement",
        "semantic_candidate_wrong_phase",
        "semantic_candidate_wrong_worker",
        "semantic_transition_incomplete",
        "semantic_effect_conflict",
        "semantic_required_target_reproposed",
        "semantic_proposal_target_duplicate",
        "semantic_scope_addition_blocked",
        "semantic_candidate_schema_invalid",
        "semantic_compilation_failed",
        "semantic_compilation_drift",
        "semantic_confirmation_stale",
    }
)
SEMANTIC_REGENERABLE_FAILURE_CODES = frozenset(
    {
        "semantic_candidate_missing_requirement",
        "semantic_candidate_duplicate_requirement",
        "semantic_candidate_wrong_phase",
        "semantic_candidate_wrong_worker",
        "semantic_transition_incomplete",
        "semantic_effect_conflict",
        "semantic_required_target_reproposed",
        "semantic_proposal_target_duplicate",
    }
)

_SEMANTIC_REGENERATION_GUIDANCE = {
    "semantic_required_target_reproposed": (
        "Remove every proposed effect whose target is already represented by "
        "required authority. Keep authority_refs unchanged. Do not add a "
        "replacement proposal for that target."
    ),
    "semantic_proposal_target_duplicate": (
        "Each proposed target may appear only once. Return one complete "
        "candidate without repeated proposed targets."
    ),
}


def semantic_regeneration_guidance(code: str) -> tuple[str, ...]:
    if type(code) is not str or code not in SEMANTIC_REGENERABLE_FAILURE_CODES:
        raise ValueError("semantic regeneration diagnostic invalid")
    value = _SEMANTIC_REGENERATION_GUIDANCE.get(code)
    return () if value is None else (value,)


_CANDIDATE_FIELDS = frozenset({"goal", "summary", "steps"})
_CANDIDATE_STEP_FIELDS = frozenset(
    {
        "step",
        "agent_id",
        "role",
        "phase",
        "authority_refs",
        "proposed_effects",
        "verification",
        "risk",
        "requires_approval",
    }
)
_PROPOSAL_FIELDS = frozenset({"target", "operation", "sensitivity"})
_PERSISTED_PROPOSAL_FIELDS = _PROPOSAL_FIELDS | {"proposed_effect_id"}
_PROPOSAL_VALIDATION_ID = "prp_000000000000"
_SEMANTIC_STEP_FIELDS = _CANDIDATE_STEP_FIELDS | frozenset(
    {"required_effects", "semantic_step_hash"}
)
_SEMANTIC_STEP_BODY_FIELDS = _SEMANTIC_STEP_FIELDS - {"semantic_step_hash"}
_SAFE_RISKS = frozenset({"low"})
_MAX_TEXT_BYTES = 4096
_MAX_ITEMS = 256
_MAX_JSON_TREE_DEPTH = 16
_MAX_JSON_TREE_NODES = 10_000
_MAX_JSON_COLLECTION_ITEMS = 512
_HASH_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REQUIREMENT_ID_RE = re.compile(r"req_[0-9a-f]{12}\Z")
_TRANSITION_FRAGMENT_RE = re.compile(
    r"(req_[0-9a-f]{12})[:./][A-Za-z0-9_-][A-Za-z0-9._:-]{0,63}\Z"
)
_AUTHORITY_REF_CHARS_MAX = 81
_ORDINARY_SCALAR_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_INSTRUCTION_RE = re.compile(
    r"(?:ignore\s+(?:all\s+)?previous\s+instructions?"
    r"|authoritative\s+operation\s*:|```|system\s+prompt)",
    re.IGNORECASE,
)


class SemanticPlanningError(ValueError):
    """A closed, non-echoing semantic planning failure."""

    def __init__(
        self,
        code: str,
        *,
        requirement_id: str | None = None,
        step: int | None = None,
    ) -> None:
        if type(code) is not str or code not in SEMANTIC_FAILURE_CODES:
            raise ValueError("semantic_planning_error_code_invalid")
        super().__init__(code)
        self.code = code
        self.requirement_id = (
            requirement_id
            if type(requirement_id) is str
            and _REQUIREMENT_ID_RE.fullmatch(requirement_id) is not None
            else None
        )
        self.step = step if type(step) is int else None


def _fail(
    code: str, *, requirement_id: str | None = None, step: int | None = None
) -> NoReturn:
    raise SemanticPlanningError(code, requirement_id=requirement_id, step=step)


def _exact_dict(value: object) -> bool:
    return type(value) is dict and all(type(key) is str for key in value)


def _exact_list(value: object) -> bool:
    return type(value) is list


def semantic_context_text_is_safe(value: object) -> bool:
    """Return whether an exact string is safe compact semantic context."""
    if type(value) is not str or not value or len(value) > _MAX_TEXT_BYTES:
        return False
    if unicodedata.normalize("NFC", value) != value:
        return False
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        return False
    if len(encoded) > _MAX_TEXT_BYTES:
        return False
    if any(
        unicodedata.category(char) in {"Cc", "Cs", "Zl", "Zp"}
        for char in value
    ):
        return False
    if semantic_text_contains_sensitive_value(value):
        return False
    return _INSTRUCTION_RE.search(value) is None


def _contains_sensitive_authority(value: object) -> bool:
    if type(value) is dict:
        if value.get("sensitivity") == "secret_ref":
            return True
        return any(_contains_sensitive_authority(item) for item in value.values())
    if type(value) is list:
        return any(_contains_sensitive_authority(item) for item in value)
    return semantic_text_contains_sensitive_value(value)


def _validated_authority(authority: object) -> dict[str, Any]:
    try:
        validated = validate_semantic_authority(authority)
    except SemanticAuthorityError:
        _fail("semantic_compilation_failed")
    if validated["unresolved"]:
        _fail("semantic_authority_unresolved")
    if _contains_sensitive_authority(validated):
        _fail("semantic_authority_sensitive_value")
    return validated


def _validate_context(
    selected_agent_ids: object, roles: object, step_count: object
) -> tuple[tuple[str, ...], dict[str, str], int]:
    if type(roles) is not dict:
        _fail("semantic_candidate_schema_invalid")
    if any(
        type(key) is not str
        or type(value) is not str
        or not value
        or len(value) > _MAX_TEXT_BYTES
        for key, value in roles.items()
    ):
        _fail("semantic_candidate_schema_invalid")
    if any(not semantic_context_text_is_safe(value) for value in roles.values()):
        _fail("semantic_candidate_schema_invalid")
    if (
        type(selected_agent_ids) is not tuple
        or not selected_agent_ids
        or len(selected_agent_ids) > _MAX_ITEMS
        or any(
            type(item) is not str or not semantic_context_text_is_safe(item)
            for item in selected_agent_ids
        )
        or len(set(selected_agent_ids)) != len(selected_agent_ids)
        or set(roles) != set(selected_agent_ids)
        or type(step_count) is not int
        or step_count <= 0
        or step_count > _MAX_ITEMS
    ):
        _fail("semantic_candidate_schema_invalid")
    return selected_agent_ids, roles, step_count


def _validate_proposal_shape(
    value: object, *, persisted: bool, step: int | None = None
) -> dict[str, str]:
    fields = _PERSISTED_PROPOSAL_FIELDS if persisted else _PROPOSAL_FIELDS
    schema_code = (
        "semantic_compilation_failed"
        if persisted
        else "semantic_candidate_schema_invalid"
    )
    sensitive_code = (
        "semantic_compilation_failed"
        if persisted
        else "semantic_authority_sensitive_value"
    )
    scope_code = (
        "semantic_compilation_failed"
        if persisted
        else "semantic_scope_addition_blocked"
    )
    if not _exact_dict(value) or set(value) != fields:
        _fail(schema_code, step=step)
    target = value["target"]
    operation = value["operation"]
    sensitivity = value["sensitivity"]
    if (
        type(target) is not str
        or type(operation) is not str
        or type(sensitivity) is not str
        or persisted and type(value["proposed_effect_id"]) is not str
    ):
        _fail(schema_code, step=step)
    if sensitivity != "ordinary":
        _fail(sensitive_code, step=step)
    if semantic_text_contains_sensitive_value(target):
        _fail(sensitive_code, step=step)
    normalized = {"target": target, "operation": operation, "sensitivity": sensitivity}
    proposal_id = value["proposed_effect_id"] if persisted else _PROPOSAL_VALIDATION_ID
    proposal = {"proposed_effect_id": proposal_id, **normalized}
    try:
        validate_semantic_authority(
            {
                "schema_version": SEMANTIC_AUTHORITY_SCHEMA_VERSION,
                "source_message_hash": f"sha256:{'0' * 64}",
                "requirements": [],
                "proposed_effects": [proposal],
                "unresolved": [],
            }
        )
    except SemanticAuthorityError:
        _fail(scope_code, step=step)
    if persisted and proposal_id != _proposal_id(normalized):
        _fail("semantic_compilation_failed", step=step)
    return normalized


def _validate_effect_target_ownership(
    required_effects: list[dict[str, Any]],
    proposed_effects: list[dict[str, str]],
) -> None:
    required_targets = {
        requirement["target"] for requirement in required_effects
    }
    if any(
        proposal["target"] in required_targets
        for proposal in proposed_effects
    ):
        _fail("semantic_required_target_reproposed")
    proposed_targets: set[str] = set()
    for proposal in proposed_effects:
        target = proposal["target"]
        if target in proposed_targets:
            _fail("semantic_proposal_target_duplicate")
        proposed_targets.add(target)


def validate_semantic_candidate(
    authority: object,
    candidate: object,
    *,
    selected_agent_ids: tuple[str, ...],
    roles: dict[str, str],
    step_count: int,
) -> dict[str, object]:
    """Validate an exact Leader semantic candidate and return a defensive copy."""
    validated_authority = _validated_authority(authority)
    if validated_authority["proposed_effects"]:
        _fail("semantic_candidate_schema_invalid")
    agents, role_map, count = _validate_context(selected_agent_ids, roles, step_count)
    if not _exact_dict(candidate) or set(candidate) != _CANDIDATE_FIELDS:
        _fail("semantic_candidate_schema_invalid")
    if not semantic_context_text_is_safe(
        candidate["goal"]
    ) or not semantic_context_text_is_safe(candidate["summary"]):
        _fail("semantic_candidate_schema_invalid")
    steps = candidate["steps"]
    if not _exact_list(steps) or len(steps) != count:
        _fail("semantic_candidate_schema_invalid")
    proposal_count = 0
    for raw_step in steps:
        if not _exact_dict(raw_step) or set(raw_step) != _CANDIDATE_STEP_FIELDS:
            _fail("semantic_candidate_schema_invalid")
        proposals = raw_step["proposed_effects"]
        if not _exact_list(proposals) or len(proposals) > SEMANTIC_PROPOSED_EFFECTS_MAX:
            _fail("semantic_candidate_schema_invalid")
        proposal_count += len(proposals)
        if proposal_count > SEMANTIC_PROPOSED_EFFECTS_MAX:
            _fail("semantic_candidate_schema_invalid")

    requirements = validated_authority["requirements"]
    by_id = {item["requirement_id"]: item for item in requirements}
    transition_ids = {
        item["requirement_id"] for item in requirements if item["kind"] == "state_transition"
    }
    seen: set[str] = set()
    validated_proposals: list[dict[str, str]] = []
    for index, raw_step in enumerate(steps, start=1):
        if not _exact_dict(raw_step) or set(raw_step) != _CANDIDATE_STEP_FIELDS:
            _fail("semantic_candidate_schema_invalid", step=index)
        if type(raw_step["step"]) is not int or raw_step["step"] != index:
            _fail("semantic_candidate_schema_invalid", step=index)
        expected_agent = agents[(index - 1) % len(agents)]
        if type(raw_step["agent_id"]) is not str or raw_step["agent_id"] != expected_agent:
            _fail("semantic_candidate_wrong_worker", step=index)
        if type(raw_step["role"]) is not str or raw_step["role"] != role_map[expected_agent]:
            _fail("semantic_candidate_schema_invalid", step=index)
        phase = raw_step["phase"]
        if (
            type(phase) is not str
            or _ORDINARY_SCALAR_RE.fullmatch(phase) is None
            or not semantic_context_text_is_safe(phase)
        ):
            _fail("semantic_candidate_schema_invalid", step=index)
        refs = raw_step["authority_refs"]
        if not _exact_list(refs) or len(refs) > _MAX_ITEMS:
            _fail("semantic_candidate_schema_invalid", step=index)
        for reference in refs:
            if type(reference) is not str or len(reference) > _AUTHORITY_REF_CHARS_MAX:
                _fail("semantic_candidate_schema_invalid", step=index)
            if _REQUIREMENT_ID_RE.fullmatch(reference) is not None:
                continue
            fragment = _TRANSITION_FRAGMENT_RE.fullmatch(reference)
            if fragment is not None and fragment.group(1) in transition_ids:
                _fail("semantic_transition_incomplete", step=index)
            _fail("semantic_candidate_schema_invalid", step=index)
        for reference in refs:
            if reference not in by_id:
                _fail("semantic_candidate_schema_invalid", requirement_id=reference, step=index)
            if reference in seen:
                if reference in transition_ids:
                    _fail(
                        "semantic_transition_incomplete",
                        requirement_id=reference,
                        step=index,
                    )
                _fail("semantic_candidate_duplicate_requirement", requirement_id=reference, step=index)
            requirement = by_id[reference]
            if requirement["phase"] != phase:
                _fail("semantic_candidate_wrong_phase", requirement_id=reference, step=index)
            if requirement["agent_id"] != expected_agent:
                _fail("semantic_candidate_wrong_worker", requirement_id=reference, step=index)
            seen.add(reference)
        proposals = raw_step["proposed_effects"]
        if not _exact_list(proposals) or len(proposals) > SEMANTIC_PROPOSED_EFFECTS_MAX:
            _fail("semantic_candidate_schema_invalid", step=index)
        for proposal in proposals:
            validated_proposals.append(
                _validate_proposal_shape(proposal, persisted=False, step=index)
            )
        if not semantic_context_text_is_safe(raw_step["verification"]):
            _fail("semantic_candidate_schema_invalid", step=index)
        if type(raw_step["risk"]) is not str or raw_step["risk"] not in _SAFE_RISKS:
            _fail("semantic_candidate_schema_invalid", step=index)
        if type(raw_step["requires_approval"]) is not bool or not raw_step["requires_approval"]:
            _fail("semantic_candidate_schema_invalid", step=index)

    missing = set(by_id) - seen
    if missing & transition_ids:
        _fail("semantic_transition_incomplete")
    if missing:
        _fail("semantic_candidate_missing_requirement")
    _validate_effect_target_ownership(requirements, validated_proposals)
    return deepcopy(candidate)


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, OverflowError):
        _fail("semantic_compilation_failed")


def _bounded_ordinary_scalar(value: object) -> bool:
    return (
        type(value) is str
        and _ORDINARY_SCALAR_RE.fullmatch(value) is not None
        and semantic_context_text_is_safe(value)
    )


def _preflight_exact_json_tree(value: object) -> None:
    stack: list[tuple[object, int]] = [(value, 0)]
    seen_containers: set[int] = set()
    node_count = 0
    while stack:
        item, depth = stack.pop()
        node_count += 1
        if depth > _MAX_JSON_TREE_DEPTH or node_count > _MAX_JSON_TREE_NODES:
            _fail("semantic_compilation_failed")
        if type(item) in {dict, list}:
            identity = id(item)
            if identity in seen_containers:
                _fail("semantic_compilation_failed")
            seen_containers.add(identity)
            if len(item) > _MAX_JSON_COLLECTION_ITEMS:
                _fail("semantic_compilation_failed")
            if type(item) is dict:
                if any(type(key) is not str for key in item):
                    _fail("semantic_compilation_failed")
                stack.extend((child, depth + 1) for child in item.values())
            else:
                stack.extend((child, depth + 1) for child in item)
            continue
        if type(item) is str:
            if len(item) > _MAX_TEXT_BYTES:
                _fail("semantic_compilation_failed")
            try:
                encoded = item.encode("utf-8")
            except UnicodeError:
                _fail("semantic_compilation_failed")
            if (
                len(encoded) > _MAX_TEXT_BYTES
                or unicodedata.normalize("NFC", item) != item
            ):
                _fail("semantic_compilation_failed")
            continue
        if item is None or type(item) is bool:
            continue
        if type(item) is int:
            if not -(2**63) <= item <= 2**63 - 1:
                _fail("semantic_compilation_failed")
            continue
        if type(item) is float:
            if not math.isfinite(item):
                _fail("semantic_compilation_failed")
            continue
        _fail("semantic_compilation_failed")


def _validate_semantic_step(
    value: object, *, require_hash: bool
) -> tuple[dict[str, Any], str | None]:
    _preflight_exact_json_tree(value)
    expected_fields = _SEMANTIC_STEP_FIELDS if require_hash else _SEMANTIC_STEP_BODY_FIELDS
    allowed_fields = {expected_fields} if require_hash else {
        _SEMANTIC_STEP_BODY_FIELDS,
        _SEMANTIC_STEP_FIELDS,
    }
    if not _exact_dict(value) or frozenset(value) not in allowed_fields:
        _fail("semantic_compilation_failed")
    body = {key: item for key, item in value.items() if key != "semantic_step_hash"}
    if (
        type(body["step"]) is not int
        or body["step"] <= 0
        or body["step"] > _MAX_ITEMS
        or not _bounded_ordinary_scalar(body["agent_id"])
        or not semantic_context_text_is_safe(body["role"])
        or not _bounded_ordinary_scalar(body["phase"])
        or not _exact_list(body["authority_refs"])
        or len(body["authority_refs"]) > _MAX_ITEMS
        or any(
            type(reference) is not str
            or _REQUIREMENT_ID_RE.fullmatch(reference) is None
            for reference in body["authority_refs"]
        )
        or len(body["authority_refs"]) != len(set(body["authority_refs"]))
        or not _exact_list(body["proposed_effects"])
        or len(body["proposed_effects"]) > _MAX_ITEMS
        or not _exact_list(body["required_effects"])
        or len(body["required_effects"]) > _MAX_ITEMS
        or not body["required_effects"] and not body["proposed_effects"]
        or not semantic_context_text_is_safe(body["verification"])
        or type(body["risk"]) is not str
        or body["risk"] not in _SAFE_RISKS
        or type(body["requires_approval"]) is not bool
        or not body["requires_approval"]
    ):
        _fail("semantic_compilation_failed")
    required_ids = []
    if _contains_sensitive_authority(body["required_effects"]) or _contains_sensitive_authority(
        body["proposed_effects"]
    ):
        _fail("semantic_compilation_failed")
    for effect in body["required_effects"]:
        if not _exact_dict(effect) or type(effect.get("requirement_id")) is not str:
            _fail("semantic_compilation_failed")
        required_ids.append(effect["requirement_id"])
    if required_ids != body["authority_refs"]:
        _fail("semantic_compilation_failed")
    validated_step_proposals = [
        _validate_proposal_shape(proposal, persisted=True)
        for proposal in body["proposed_effects"]
    ]
    try:
        validated_requirements = validate_semantic_authority(
            {
                "schema_version": SEMANTIC_AUTHORITY_SCHEMA_VERSION,
                "source_message_hash": f"sha256:{'0' * 64}",
                "requirements": body["required_effects"],
                "proposed_effects": [],
                "unresolved": [],
            }
        )
    except SemanticAuthorityError:
        _fail("semantic_compilation_failed")
    _validate_effect_target_ownership(
        validated_requirements["requirements"], validated_step_proposals
    )
    try:
        validated_effects = validate_semantic_authority(
            {
                "schema_version": SEMANTIC_AUTHORITY_SCHEMA_VERSION,
                "source_message_hash": f"sha256:{'0' * 64}",
                "requirements": body["required_effects"],
                "proposed_effects": body["proposed_effects"],
                "unresolved": [],
            }
        )
    except SemanticAuthorityError:
        _fail("semantic_compilation_failed")
    if _contains_sensitive_authority(validated_effects):
        _fail("semantic_compilation_failed")
    _validate_effect_target_ownership(
        validated_effects["requirements"], validated_effects["proposed_effects"]
    )
    if any(
        effect["phase"] != body["phase"]
        or effect["agent_id"] != body["agent_id"]
        for effect in validated_effects["requirements"]
    ):
        _fail("semantic_compilation_failed")
    has_embedded_hash = "semantic_step_hash" in value
    embedded = value["semantic_step_hash"] if has_embedded_hash else None
    if has_embedded_hash and (
        type(embedded) is not str or _HASH_RE.fullmatch(embedded) is None
    ):
        _fail("semantic_compilation_failed")
    try:
        return deepcopy(body), embedded
    except RecursionError:
        _fail("semantic_compilation_failed")


def _semantic_step_body_hash(body: dict[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(body)).hexdigest()}"


def semantic_step_hash(semantic_step: object) -> str:
    """Hash the canonical semantic step body, excluding its own hash field."""
    body, embedded_hash = _validate_semantic_step(semantic_step, require_hash=False)
    actual_hash = _semantic_step_body_hash(body)
    if embedded_hash is not None and embedded_hash != actual_hash:
        _fail("semantic_compilation_drift")
    return actual_hash


def _json_line(value: object) -> str:
    try:
        serialized = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError, UnicodeError, OverflowError):
        _fail("semantic_compilation_failed")
    serialized = (
        serialized.replace("\x85", "\\u0085")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    if len(serialized.splitlines()) != 1:
        _fail("semantic_compilation_failed")
    return serialized


def compile_worker_task(semantic_step: object) -> str:
    """Compile a validated semantic step into a deterministic Worker task."""
    body, embedded_hash = _validate_semantic_step(semantic_step, require_hash=True)
    if embedded_hash != _semantic_step_body_hash(body):
        _fail("semantic_compilation_drift")

    lines = ["Authoritative effects:"]
    for effect in body["required_effects"]:
        lines.extend(
            [
                f"Authoritative operation: {_json_line(effect['operation'])}",
                f"Target: {_json_line(effect['target'])}",
                f"Requirement kind: {_json_line(effect['kind'])}",
            ]
        )
        if effect["kind"] == "state_transition":
            lines.extend(
                [
                    f"Required precondition: {_json_line(effect['before'])}",
                    f"Required postcondition: {_json_line(effect['after'])}",
                ]
            )
        else:
            lines.append(f"Required value: {_json_line(effect['literal'])}")
    for proposal in body["proposed_effects"]:
        lines.extend(
            [
                f"Authoritative operation: {_json_line(proposal['operation'])}",
                f"Target: {_json_line(proposal['target'])}",
                f"Proposed effect ID: {_json_line(proposal['proposed_effect_id'])}",
            ]
        )
    lines.extend(
        [
            "Verification: verify every authoritative effect exactly after the operation.",
            "Forbidden scope: do not modify unrelated targets, broaden scope, or infer permission.",
            f"Non-authoritative verification guidance: {_json_line(body['verification'])}",
        ]
    )
    return "\n".join(lines) + "\n"


def _proposal_id(proposal: dict[str, str]) -> str:
    return f"prp_{hashlib.sha256(_canonical_bytes(proposal)).hexdigest()[:12]}"


def compile_semantic_plan(
    authority: object,
    candidate: object,
    *,
    selected_agent_ids: tuple[str, ...],
    roles: dict[str, str],
    step_count: int,
) -> dict[str, object]:
    """Validate, freeze, and deterministically compile a semantic plan."""
    draft_authority = _validated_authority(authority)
    validated_candidate = validate_semantic_candidate(
        draft_authority,
        candidate,
        selected_agent_ids=selected_agent_ids,
        roles=roles,
        step_count=step_count,
    )
    requirements_by_id = {
        item["requirement_id"]: item for item in draft_authority["requirements"]
    }
    semantic_steps: list[dict[str, Any]] = []
    normalized_proposals: list[dict[str, str]] = []
    for candidate_step in validated_candidate["steps"]:
        step_proposals = []
        for proposal in candidate_step["proposed_effects"]:
            normalized = _validate_proposal_shape(
                proposal, persisted=False, step=candidate_step["step"]
            )
            compiled = {"proposed_effect_id": _proposal_id(normalized), **normalized}
            step_proposals.append(compiled)
            normalized_proposals.append(compiled)
        semantic_body = {
            key: deepcopy(value)
            for key, value in candidate_step.items()
            if key != "proposed_effects"
        }
        semantic_body["proposed_effects"] = step_proposals
        semantic_body["required_effects"] = [
            deepcopy(requirements_by_id[reference])
            for reference in candidate_step["authority_refs"]
        ]
        semantic_body["semantic_step_hash"] = semantic_step_hash(semantic_body)
        semantic_steps.append(semantic_body)

    frozen_authority = deepcopy(draft_authority)
    frozen_authority["proposed_effects"] = normalized_proposals
    frozen_authority = _validated_authority(frozen_authority)
    compatibility_steps = [
        {
            "step": item["step"],
            "agent_id": item["agent_id"],
            "role": item["role"],
            "task": compile_worker_task(item),
            "risk": item["risk"],
            "requires_approval": item["requires_approval"],
        }
        for item in semantic_steps
    ]
    return deepcopy(
        {
            "goal": validated_candidate["goal"],
            "summary": validated_candidate["summary"],
            "steps": compatibility_steps,
            "semantic_authority": frozen_authority,
            "semantic_steps": semantic_steps,
        }
    )
