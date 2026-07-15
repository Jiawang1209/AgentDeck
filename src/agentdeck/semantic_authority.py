from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
import unicodedata
from typing import Any, NoReturn


SEMANTIC_AUTHORITY_SCHEMA_VERSION = "mission-semantic-authority/v1"
SEMANTIC_REQUIREMENT_KINDS = frozenset(
    {"create", "read", "review", "update", "verify", "state_transition"}
)
SEMANTIC_OPERATIONS = frozenset({"create", "read", "review", "update", "verify"})
SEMANTIC_SENSITIVITY = frozenset({"ordinary", "secret_ref"})
SEMANTIC_INTEGER_MIN = -(2**63)
SEMANTIC_INTEGER_MAX = 2**63 - 1
SEMANTIC_REQUIREMENTS_MAX = 256
SEMANTIC_PROPOSED_EFFECTS_MAX = 256
SEMANTIC_TARGET_UTF8_BYTES_MAX = 1024
SEMANTIC_LITERAL_UTF8_BYTES_MAX = 4096
SEMANTIC_AUTHORITY_CANONICAL_BYTES_MAX = 1_000_000
SEMANTIC_COMPILED_STEP_COUNT_MAX = 1_000_000

_AUTHORITY_FIELDS = frozenset(
    {
        "schema_version",
        "source_message_hash",
        "requirements",
        "proposed_effects",
        "unresolved",
    }
)
_REQUIREMENT_BASE_FIELDS = frozenset(
    {
        "requirement_id",
        "kind",
        "target",
        "operation",
        "phase",
        "agent_id",
        "sensitivity",
    }
)
_REQUIREMENT_FIELDS = {
    kind: _REQUIREMENT_BASE_FIELDS | {"literal"}
    for kind in SEMANTIC_REQUIREMENT_KINDS - {"state_transition"}
}
_REQUIREMENT_FIELDS["state_transition"] = _REQUIREMENT_BASE_FIELDS | {
    "before",
    "after",
}
_PROPOSAL_FIELDS = frozenset(
    {"proposed_effect_id", "target", "operation", "sensitivity"}
)
_UNRESOLVED_FIELDS = frozenset({"unresolved_id", "kind", "phase", "agent_id"})
_COMPACT_STATES = frozenset({"draft", "blocked", "preview", "frozen"})
_MAX_BLOCKER_COUNT = 64
_MAX_UNRESOLVED_COUNT = 64

_HASH_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REQUIREMENT_ID_RE = re.compile(r"req_[0-9a-f]{12}\Z")
_PROPOSAL_ID_RE = re.compile(r"prp_[0-9a-f]{12}\Z")
_UNRESOLVED_ID_RE = re.compile(r"unr_[0-9a-f]{12}\Z")
_ORDINARY_SCALAR_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_BLOCKER_CODE_RE = re.compile(r"[a-z][a-z0-9_]{0,127}\Z")

_ERROR_CODES = frozenset(
    {
        "authority_invalid",
        "authority_fields_invalid",
        "schema_version_invalid",
        "source_message_hash_invalid",
        "requirements_invalid",
        "requirements_count_exceeded",
        "requirement_invalid",
        "requirement_fields_invalid",
        "requirement_id_invalid",
        "requirement_ids_not_unique",
        "requirement_ids_not_ordered",
        "requirement_kind_invalid",
        "requirement_operation_invalid",
        "proposed_effects_invalid",
        "proposed_effects_count_exceeded",
        "proposal_invalid",
        "proposal_fields_invalid",
        "proposal_id_invalid",
        "proposal_ids_not_unique",
        "proposal_ids_not_ordered",
        "proposal_operation_invalid",
        "target_invalid",
        "sensitivity_invalid",
        "ordinary_scalar_invalid",
        "value_constraint_invalid",
        "unicode_scalar_invalid",
        "unicode_normalization_invalid",
        "number_out_of_range",
        "secret_value_not_reference",
        "unresolved_invalid",
        "unresolved_fields_invalid",
        "unresolved_id_invalid",
        "unresolved_ids_not_unique",
        "unresolved_ids_not_ordered",
        "unresolved_kind_invalid",
        "canonicalization_invalid",
        "authority_size_exceeded",
        "compact_state_invalid",
        "compiled_step_count_invalid",
        "blockers_invalid",
    }
)


class SemanticAuthorityError(ValueError):
    """Closed, non-echoing semantic-authority validation failure."""

    def __init__(self, code: str, requirement_id: str | None = None) -> None:
        if type(code) is not str or code not in _ERROR_CODES:
            raise ValueError("semantic_authority_error_code_invalid")
        self.code = code
        self.requirement_id = (
            requirement_id
            if type(requirement_id) is str
            and _REQUIREMENT_ID_RE.fullmatch(requirement_id)
            else None
        )
        super().__init__(code)


def _fail(code: str, requirement_id: object = None) -> NoReturn:
    safe_id = requirement_id if type(requirement_id) is str else None
    raise SemanticAuthorityError(code, safe_id)


def _is_exact_dict(value: object) -> bool:
    return type(value) is dict


def _is_exact_list(value: object) -> bool:
    return type(value) is list


def _has_exact_fields(value: dict[object, object], fields: frozenset[str]) -> bool:
    return all(type(key) is str for key in value) and frozenset(value) == fields


def _validate_target(value: object, *, requirement_id: object = None) -> None:
    if type(value) is not str or not value or "\x00" in value or "\\" in value:
        _fail("target_invalid", requirement_id)
    if len(value) > SEMANTIC_TARGET_UTF8_BYTES_MAX:
        _fail("target_invalid", requirement_id)
    _validate_unicode_scalar(value, requirement_id=requirement_id)
    if any(unicodedata.category(character) == "Cc" for character in value):
        _fail("target_invalid", requirement_id)
    if len(value.encode("utf-8")) > SEMANTIC_TARGET_UTF8_BYTES_MAX:
        _fail("target_invalid", requirement_id)
    if value.startswith("/") or value.endswith("/") or re.match(r"[A-Za-z]:", value):
        _fail("target_invalid", requirement_id)
    segments = value.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        _fail("target_invalid", requirement_id)


def _validate_ordinary_scalar(value: object, *, requirement_id: object = None) -> None:
    if type(value) is not str or _ORDINARY_SCALAR_RE.fullmatch(value) is None:
        _fail("ordinary_scalar_invalid", requirement_id)


def _validate_unicode_scalar(value: str, *, requirement_id: object = None) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        _fail("unicode_scalar_invalid", requirement_id)
    if unicodedata.normalize("NFC", value) != value:
        _fail("unicode_normalization_invalid", requirement_id)


def _validate_json_scalar(value: object, *, requirement_id: object) -> None:
    if value is None:
        return
    if type(value) is str:
        if len(value) > SEMANTIC_LITERAL_UTF8_BYTES_MAX:
            _fail("value_constraint_invalid", requirement_id)
        _validate_unicode_scalar(value, requirement_id=requirement_id)
        if len(value.encode("utf-8")) > SEMANTIC_LITERAL_UTF8_BYTES_MAX:
            _fail("value_constraint_invalid", requirement_id)
        return
    if type(value) is bool:
        return
    if type(value) is int:
        if not SEMANTIC_INTEGER_MIN <= value <= SEMANTIC_INTEGER_MAX:
            _fail("number_out_of_range", requirement_id)
        return
    if type(value) is float:
        if not math.isfinite(value):
            _fail("number_out_of_range", requirement_id)
        return
    _fail("value_constraint_invalid", requirement_id)


def _validate_constraint(
    value: object,
    *,
    sensitivity: str,
    requirement_id: object,
) -> None:
    if not _is_exact_dict(value):
        _fail("value_constraint_invalid", requirement_id)
    if sensitivity == "secret_ref":
        if not _has_exact_fields(value, frozenset({"reference"})):
            _fail("secret_value_not_reference", requirement_id)
        _validate_ordinary_scalar(value["reference"], requirement_id=requirement_id)
        return
    if not _has_exact_fields(value, frozenset({"content_equals"})):
        _fail("value_constraint_invalid", requirement_id)
    _validate_json_scalar(value["content_equals"], requirement_id=requirement_id)


def _validate_literal(
    value: object,
    *,
    sensitivity: str,
    requirement_id: object,
) -> None:
    if sensitivity == "secret_ref":
        if not _is_exact_dict(value) or not _has_exact_fields(
            value, frozenset({"reference"})
        ):
            _fail("secret_value_not_reference", requirement_id)
        _validate_ordinary_scalar(value["reference"], requirement_id=requirement_id)
        return
    _validate_json_scalar(value, requirement_id=requirement_id)


def _validate_requirement(value: object) -> str:
    if not _is_exact_dict(value):
        _fail("requirement_invalid")
    if not all(type(key) is str for key in value):
        _fail("requirement_fields_invalid")
    requirement_id = value.get("requirement_id")
    if type(requirement_id) is not str or _REQUIREMENT_ID_RE.fullmatch(requirement_id) is None:
        _fail("requirement_id_invalid")
    kind = value.get("kind")
    if type(kind) is not str or kind not in SEMANTIC_REQUIREMENT_KINDS:
        _fail("requirement_kind_invalid", requirement_id)
    if not _has_exact_fields(value, _REQUIREMENT_FIELDS[kind]):
        _fail("requirement_fields_invalid", requirement_id)

    operation = value["operation"]
    expected_operation = "update" if kind == "state_transition" else kind
    if (
        type(operation) is not str
        or operation not in SEMANTIC_OPERATIONS
        or operation != expected_operation
    ):
        _fail("requirement_operation_invalid", requirement_id)
    _validate_target(value["target"], requirement_id=requirement_id)
    sensitivity = value["sensitivity"]
    if type(sensitivity) is not str or sensitivity not in SEMANTIC_SENSITIVITY:
        _fail("sensitivity_invalid", requirement_id)
    _validate_ordinary_scalar(value["phase"], requirement_id=requirement_id)
    _validate_ordinary_scalar(value["agent_id"], requirement_id=requirement_id)

    if kind == "state_transition":
        _validate_constraint(
            value["before"], sensitivity=sensitivity, requirement_id=requirement_id
        )
        _validate_constraint(
            value["after"], sensitivity=sensitivity, requirement_id=requirement_id
        )
    else:
        _validate_literal(
            value["literal"], sensitivity=sensitivity, requirement_id=requirement_id
        )
    return requirement_id


def _validate_proposal(value: object) -> str:
    if not _is_exact_dict(value):
        _fail("proposal_invalid")
    if not all(type(key) is str for key in value):
        _fail("proposal_fields_invalid")
    proposal_id = value.get("proposed_effect_id")
    if type(proposal_id) is not str or _PROPOSAL_ID_RE.fullmatch(proposal_id) is None:
        _fail("proposal_id_invalid")
    if not _has_exact_fields(value, _PROPOSAL_FIELDS):
        _fail("proposal_fields_invalid")
    _validate_target(value["target"])
    if (
        type(value["operation"]) is not str
        or value["operation"] not in SEMANTIC_OPERATIONS
    ):
        _fail("proposal_operation_invalid")
    if (
        type(value["sensitivity"]) is not str
        or value["sensitivity"] not in SEMANTIC_SENSITIVITY
    ):
        _fail("sensitivity_invalid")
    return proposal_id


def _validate_unresolved(value: object) -> str:
    if not _is_exact_dict(value) or not _has_exact_fields(value, _UNRESOLVED_FIELDS):
        _fail("unresolved_fields_invalid")
    unresolved_id = value["unresolved_id"]
    if type(unresolved_id) is not str or _UNRESOLVED_ID_RE.fullmatch(unresolved_id) is None:
        _fail("unresolved_id_invalid")
    kind = value["kind"]
    if type(kind) is not str or _BLOCKER_CODE_RE.fullmatch(kind) is None:
        _fail("unresolved_kind_invalid")
    _validate_ordinary_scalar(value["phase"])
    _validate_ordinary_scalar(value["agent_id"])
    return unresolved_id


def _validate_ordered_ids(
    ids: list[str], *, duplicate_code: str, ordered_code: str
) -> None:
    if len(ids) != len(set(ids)):
        _fail(duplicate_code)
    if ids != sorted(ids):
        _fail(ordered_code)


def validate_semantic_authority(authority: object) -> dict[str, Any]:
    """Validate the closed authority domain and return a defensive copy."""
    if not _is_exact_dict(authority):
        _fail("authority_invalid")
    if not _has_exact_fields(authority, _AUTHORITY_FIELDS):
        _fail("authority_fields_invalid")
    if (
        type(authority["schema_version"]) is not str
        or authority["schema_version"] != SEMANTIC_AUTHORITY_SCHEMA_VERSION
    ):
        _fail("schema_version_invalid")
    source_hash = authority["source_message_hash"]
    if type(source_hash) is not str or _HASH_RE.fullmatch(source_hash) is None:
        _fail("source_message_hash_invalid")

    requirements = authority["requirements"]
    if not _is_exact_list(requirements):
        _fail("requirements_invalid")
    if len(requirements) > SEMANTIC_REQUIREMENTS_MAX:
        _fail("requirements_count_exceeded")
    requirement_ids = [_validate_requirement(item) for item in requirements]
    _validate_ordered_ids(
        requirement_ids,
        duplicate_code="requirement_ids_not_unique",
        ordered_code="requirement_ids_not_ordered",
    )

    proposed_effects = authority["proposed_effects"]
    if not _is_exact_list(proposed_effects):
        _fail("proposed_effects_invalid")
    if len(proposed_effects) > SEMANTIC_PROPOSED_EFFECTS_MAX:
        _fail("proposed_effects_count_exceeded")
    proposal_ids = [_validate_proposal(item) for item in proposed_effects]
    _validate_ordered_ids(
        proposal_ids,
        duplicate_code="proposal_ids_not_unique",
        ordered_code="proposal_ids_not_ordered",
    )

    unresolved = authority["unresolved"]
    if not _is_exact_list(unresolved) or len(unresolved) > _MAX_UNRESOLVED_COUNT:
        _fail("unresolved_invalid")
    unresolved_ids = [_validate_unresolved(item) for item in unresolved]
    _validate_ordered_ids(
        unresolved_ids,
        duplicate_code="unresolved_ids_not_unique",
        ordered_code="unresolved_ids_not_ordered",
    )
    if len(_canonical_bytes(authority)) > SEMANTIC_AUTHORITY_CANONICAL_BYTES_MAX:
        _fail("authority_size_exceeded")
    return deepcopy(authority)


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
        _fail("canonicalization_invalid")


def semantic_authority_hash(authority: object) -> str:
    """Return the canonical SHA-256 identity of a validated authority."""
    validated = validate_semantic_authority(authority)
    return f"sha256:{hashlib.sha256(_canonical_bytes(validated)).hexdigest()}"


def compact_semantic_authority(
    authority: object,
    *,
    state: str,
    compiled_step_count: int,
    blockers: list[str],
) -> dict[str, Any]:
    """Project validated authority into an exact, non-literal summary."""
    validated = validate_semantic_authority(authority)
    if type(state) is not str or state not in _COMPACT_STATES:
        _fail("compact_state_invalid")
    if (
        type(compiled_step_count) is not int
        or compiled_step_count < 0
        or compiled_step_count > SEMANTIC_COMPILED_STEP_COUNT_MAX
    ):
        _fail("compiled_step_count_invalid")
    if not _is_exact_list(blockers) or len(blockers) > _MAX_BLOCKER_COUNT or any(
        type(item) is not str or _BLOCKER_CODE_RE.fullmatch(item) is None
        for item in blockers
    ):
        _fail("blockers_invalid")
    compact = {
        "schema_version": SEMANTIC_AUTHORITY_SCHEMA_VERSION,
        "state": state,
        "authority_hash": semantic_authority_hash(validated),
        "requirement_count": len(validated["requirements"]),
        "proposed_effect_count": len(validated["proposed_effects"]),
        "unresolved_count": len(validated["unresolved"]),
        "compiled_step_count": compiled_step_count,
        "blockers": deepcopy(blockers),
    }
    return deepcopy(compact)
