from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
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
        "requirement_kind_invalid",
        "requirement_operation_invalid",
        "proposed_effects_invalid",
        "proposed_effects_count_exceeded",
        "proposal_invalid",
        "proposal_fields_invalid",
        "proposal_id_invalid",
        "proposal_ids_not_unique",
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
        "unresolved_kind_invalid",
        "canonicalization_invalid",
        "authority_size_exceeded",
        "compact_state_invalid",
        "compiled_step_count_invalid",
        "blockers_invalid",
        "extraction_message_invalid",
        "extraction_agents_invalid",
        "extraction_step_count_invalid",
        "extraction_phases_invalid",
        "extraction_consistency_invalid",
        "extraction_agent_order_invalid",
    }
)

_EXTRACTION_MESSAGE_UTF8_BYTES_MAX = SEMANTIC_LITERAL_UTF8_BYTES_MAX
_CHINESE_ORDINAL_RE = re.compile(r"(?:第一轮|第二轮|第三轮|第四轮)")
_ENGLISH_ORDINAL_RE = re.compile(
    r"\b(?:First|Second|Third|Fourth)\b[:,]?", re.IGNORECASE
)
_TARGET_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"(?:"
    r"(?:[A-Za-z]:)?(?:[A-Za-z0-9_-]+[\\])+"
    r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
    r"|(?:/|(?:\.\./)+|[A-Za-z]:)?"
    r"(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
    r")"
)
_LITERAL_TOKEN = r"([A-Za-z0-9][A-Za-z0-9._:-]{0,255})"
_VALUE_PATTERNS = (
    re.compile(rf"内容为\s*{_LITERAL_TOKEN}(\s*换行)?", re.IGNORECASE),
    re.compile(rf"要求\s*{_LITERAL_TOKEN}(\s*换行)?", re.IGNORECASE),
    re.compile(rf"精确改为\s*{_LITERAL_TOKEN}(\s*换行)?", re.IGNORECASE),
    re.compile(
        rf"(?:with\s+)?content\s+(?:is\s+)?exactly\s+{_LITERAL_TOKEN}(\s+newline)?",
        re.IGNORECASE,
    ),
    re.compile(rf"requires?\s+{_LITERAL_TOKEN}(\s+newline)?", re.IGNORECASE),
    re.compile(rf"to\s+exactly\s+{_LITERAL_TOKEN}(\s+newline)?", re.IGNORECASE),
)
_UNSUPPORTED_EXACT_VALUE_RE = re.compile(
    r"内容精确匹配|(?:matches?|matching)\s+exactly", re.IGNORECASE
)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"\b(?:"
    r"(?:[A-Za-z][A-Za-z0-9_-]*[_-])?"
    r"(?:api[_-]?key|access[_-]?key|token|secret|password|passwd|credentials?)"
    r"|(?:[A-Za-z][A-Za-z0-9_-]*\s+)?"
    r"(?:token|secret|password|passwd|credentials?)"
    r")\s*[:=]\s*\S+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _ExtractionInput:
    message: str
    selected_agent_ids: tuple[str, ...]
    step_count: int
    phases: tuple[str, ...] | None


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


def _validate_unique_ids(ids: list[str], *, duplicate_code: str) -> None:
    if len(ids) != len(set(ids)):
        _fail(duplicate_code)


def _sha256_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _validate_extraction_inputs(
    message: str,
    selected_agent_ids: tuple[str, ...],
    step_count: int,
    phases: tuple[str, ...] | None,
) -> _ExtractionInput:
    if type(message) is not str or not message:
        _fail("extraction_message_invalid")
    try:
        encoded_message = message.encode("utf-8")
    except UnicodeEncodeError:
        _fail("extraction_message_invalid")
    if (
        len(encoded_message) > _EXTRACTION_MESSAGE_UTF8_BYTES_MAX
        or unicodedata.normalize("NFC", message) != message
    ):
        _fail("extraction_message_invalid")
    if type(selected_agent_ids) is not tuple or not selected_agent_ids:
        _fail("extraction_agents_invalid")
    if any(
        type(agent_id) is not str
        or _ORDINARY_SCALAR_RE.fullmatch(agent_id) is None
        for agent_id in selected_agent_ids
    ):
        _fail("extraction_agents_invalid")
    if len(selected_agent_ids) != len(set(selected_agent_ids)):
        _fail("extraction_agents_invalid")
    if (
        type(step_count) is not int
        or step_count < 1
        or step_count > SEMANTIC_REQUIREMENTS_MAX
    ):
        _fail("extraction_step_count_invalid")
    if len(selected_agent_ids) > step_count:
        _fail("extraction_consistency_invalid")
    if phases is not None:
        if type(phases) is not tuple or any(
            type(phase) is not str or _ORDINARY_SCALAR_RE.fullmatch(phase) is None
            for phase in phases
        ):
            _fail("extraction_phases_invalid")
        if len(phases) != step_count or len(phases) != len(set(phases)):
            _fail("extraction_consistency_invalid")

    mentioned = sorted(
        (
            (message.find(agent_id), agent_id)
            for agent_id in selected_agent_ids
            if message.find(agent_id) >= 0
        ),
        key=lambda item: item[0],
    )
    if mentioned:
        if len(mentioned) != len(selected_agent_ids):
            _fail("extraction_consistency_invalid")
        if tuple(agent_id for _, agent_id in mentioned) != selected_agent_ids:
            _fail("extraction_agent_order_invalid")
    return _ExtractionInput(message, selected_agent_ids, step_count, phases)


def _ordered_clauses(message: str, step_count: int) -> tuple[str, ...]:
    matches = list(_CHINESE_ORDINAL_RE.finditer(message))
    if matches:
        expected = ("第一轮", "第二轮", "第三轮", "第四轮")[:step_count]
        actual = tuple(match.group(0) for match in matches)
        if actual != expected:
            _fail("extraction_consistency_invalid")
    else:
        matches = list(_ENGLISH_ORDINAL_RE.finditer(message))
        if matches:
            expected = ("first", "second", "third", "fourth")[:step_count]
            actual = tuple(
                match.group(0).rstrip("/,: ").lower() for match in matches
            )
            if actual != expected:
                _fail("extraction_consistency_invalid")
    if matches:
        if len(matches) != step_count:
            _fail("extraction_consistency_invalid")
        return tuple(
            message[
                match.start() : (
                    matches[index + 1].start()
                    if index + 1 < len(matches)
                    else len(message)
                )
            ]
            .strip(" \t\r\n；;.：:")
            for index, match in enumerate(matches)
        )
    return tuple(
        clause.strip(" \t\r\n；;.：:")
        for clause in re.split(r"[；;\n]+", message)
        if clause.strip(" \t\r\n；;.：:")
    )


def _explicit_targets(clause: str) -> tuple[str, ...]:
    return tuple(match.group(0) for match in _TARGET_RE.finditer(clause))


def _explicit_values(clause: str) -> tuple[str, ...]:
    values: list[str] = []
    matches: list[tuple[int, str]] = []
    for pattern in _VALUE_PATTERNS:
        for match in pattern.finditer(clause):
            literal = match.group(1)
            if match.group(2):
                literal += "\n"
            matches.append((match.start(), literal))
    for _, literal in sorted(matches, key=lambda item: item[0]):
        if literal not in values:
            values.append(literal)
    return tuple(values)


def _classify_operation(clause: str) -> str | None:
    masked = list(clause)
    spans = [match.span() for match in _TARGET_RE.finditer(clause)]
    for pattern in _VALUE_PATTERNS:
        spans.extend(match.span(1) for match in pattern.finditer(clause))
    for start, end in spans:
        masked[start:end] = " " * (end - start)
    verb_text = "".join(masked)
    if re.search(r"精确改为|\bupdates?\b|\bchanges?\b", verb_text, re.IGNORECASE):
        return "update"
    if re.search(
        r"只读验收|验收|\bverification\b|\bverif(?:y|ies)\b",
        verb_text,
        re.IGNORECASE,
    ):
        return "verify"
    if re.search(r"只读审查|审查|\breviews?\b", verb_text, re.IGNORECASE):
        return "review"
    if re.search(r"创建|\bcreates?\b", verb_text, re.IGNORECASE):
        return "create"
    if re.search(r"只读读取|读取|\breads?\b", verb_text, re.IGNORECASE):
        return "read"
    return None


def _classify_sensitive(clause: str) -> str:
    if _SENSITIVE_ASSIGNMENT_RE.search(clause):
        return "sensitive_content"
    return "ordinary"


def _opaque_item_id(prefix: str, body: dict[str, object]) -> str:
    return f"{prefix}_{hashlib.sha256(_canonical_bytes(body)).hexdigest()[:12]}"


def _requirements_from_clauses(
    clauses: tuple[str, ...],
    *,
    selected_agent_ids: tuple[str, ...],
    phases: tuple[str, ...] | None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    analyses: list[dict[str, object]] = []
    safe_global_targets: list[str] = []
    for index, clause in enumerate(clauses):
        phase = (
            phases[index]
            if phases is not None and index < len(phases)
            else f"step-{index + 1}"
        )
        clause_agents = tuple(
            agent_id for agent_id in selected_agent_ids if agent_id in clause
        )
        agent_id = (
            clause_agents[0]
            if len(clause_agents) == 1
            else selected_agent_ids[0]
            if len(selected_agent_ids) == 1
            else "unassigned"
        )
        operation = _classify_operation(clause)
        sensitivity = _classify_sensitive(clause)
        if sensitivity != "ordinary":
            analyses.append(
                {
                    "clause": "",
                    "phase": phase,
                    "agent_id": agent_id,
                    "operation": operation,
                    "sensitivity": sensitivity,
                    "targets": (),
                    "values": (),
                    "unsafe_target": False,
                }
            )
            continue
        targets = _explicit_targets(clause)
        values = _explicit_values(clause)
        safe_targets: list[str] = []
        unsafe_target = False
        for target in targets:
            try:
                _validate_target(target)
            except SemanticAuthorityError:
                unsafe_target = True
            else:
                safe_targets.append(target)
                if target not in safe_global_targets:
                    safe_global_targets.append(target)
        analyses.append(
            {
                "clause": "" if unsafe_target else clause,
                "phase": phase,
                "agent_id": agent_id,
                "operation": operation,
                "sensitivity": sensitivity,
                "targets": tuple(safe_targets),
                "values": values,
                "unsafe_target": unsafe_target,
            }
        )

    requirements: list[dict[str, object]] = []
    unresolved: list[dict[str, object]] = []
    states: dict[str, str] = {}

    def add_unresolved(kind: str, phase: str, agent_id: str) -> None:
        body: dict[str, object] = {
            "kind": kind,
            "phase": phase,
            "agent_id": agent_id,
        }
        unresolved.append({"unresolved_id": _opaque_item_id("unr", body), **body})

    def add_requirement(body: dict[str, object]) -> None:
        requirements.append({"requirement_id": _opaque_item_id("req", body), **body})

    for index, analysis in enumerate(analyses):
        clause = analysis["clause"]
        phase = analysis["phase"]
        agent_id = analysis["agent_id"]
        operation = analysis["operation"]
        targets = analysis["targets"]
        values = analysis["values"]
        assert type(clause) is str
        assert type(phase) is str
        assert type(agent_id) is str
        assert operation is None or type(operation) is str
        assert type(targets) is tuple
        assert type(values) is tuple

        if analysis["sensitivity"] != "ordinary":
            add_unresolved("sensitive_content", phase, agent_id)
            continue
        if analysis["unsafe_target"]:
            add_unresolved("unsafe_target", phase, agent_id)
            continue
        if len(targets) > 1:
            add_unresolved("ambiguous_target", phase, agent_id)
            continue
        if len(values) > 1:
            add_unresolved("ambiguous_literal", phase, agent_id)
            continue
        if _UNSUPPORTED_EXACT_VALUE_RE.search(clause) and not values:
            add_unresolved("unsupported_literal", phase, agent_id)
            continue
        if operation is None:
            if targets or values:
                add_unresolved("unbound_explicit_detail", phase, agent_id)
            continue
        if agent_id == "unassigned":
            add_unresolved("ambiguous_agent", phase, agent_id)
            continue

        target = targets[0] if targets else None
        if target is None and operation in {"read", "review", "verify"}:
            if len(safe_global_targets) == 1:
                target = safe_global_targets[0]
        if target is None:
            add_unresolved("missing_target", phase, agent_id)
            continue

        literal = values[0] if values else None
        if operation == "review" and literal is not None and not literal.endswith("\n"):
            aligned = {
                later["values"][0]
                for later in analyses[index + 1 :]
                if later["operation"] == "update"
                and len(later["targets"]) == 1
                and later["targets"][0] == target
                and len(later["values"]) == 1
                and later["values"][0].removesuffix("\n") == literal
            }
            if len(safe_global_targets) == 1 and len(aligned) == 1:
                literal = aligned.pop()
        if (
            literal is None
            and operation in {"read", "verify"}
            and len(safe_global_targets) == 1
        ):
            literal = states.get(target)
        if literal is None:
            add_unresolved("missing_literal", phase, agent_id)
            continue

        common: dict[str, object] = {
            "target": target,
            "operation": operation,
            "phase": phase,
            "agent_id": agent_id,
            "sensitivity": "ordinary",
        }
        if operation == "update":
            before = states.get(target)
            if before is None:
                add_unresolved("missing_transition_origin", phase, agent_id)
                continue
            body = {
                "kind": "state_transition",
                **common,
                "before": {"content_equals": before},
                "after": {"content_equals": literal},
            }
            states[target] = literal
        else:
            body = {"kind": operation, **common, "literal": literal}
            if operation == "create":
                states[target] = literal
        add_requirement(body)
    return requirements, unresolved


def extract_semantic_authority(
    message: str,
    *,
    selected_agent_ids: tuple[str, ...],
    step_count: int,
    phases: tuple[str, ...] | None = None,
) -> dict[str, object]:
    normalized = _validate_extraction_inputs(
        message, selected_agent_ids, step_count, phases
    )
    clauses = _ordered_clauses(normalized.message, normalized.step_count)
    requirements, unresolved = _requirements_from_clauses(
        clauses,
        selected_agent_ids=normalized.selected_agent_ids,
        phases=normalized.phases,
    )
    return validate_semantic_authority(
        {
            "schema_version": SEMANTIC_AUTHORITY_SCHEMA_VERSION,
            "source_message_hash": _sha256_text(normalized.message),
            "requirements": requirements,
            "proposed_effects": [],
            "unresolved": unresolved,
        }
    )


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
    _validate_unique_ids(
        requirement_ids,
        duplicate_code="requirement_ids_not_unique",
    )

    proposed_effects = authority["proposed_effects"]
    if not _is_exact_list(proposed_effects):
        _fail("proposed_effects_invalid")
    if len(proposed_effects) > SEMANTIC_PROPOSED_EFFECTS_MAX:
        _fail("proposed_effects_count_exceeded")
    proposal_ids = [_validate_proposal(item) for item in proposed_effects]
    _validate_unique_ids(
        proposal_ids,
        duplicate_code="proposal_ids_not_unique",
    )

    unresolved = authority["unresolved"]
    if not _is_exact_list(unresolved) or len(unresolved) > _MAX_UNRESOLVED_COUNT:
        _fail("unresolved_invalid")
    unresolved_ids = [_validate_unresolved(item) for item in unresolved]
    _validate_unique_ids(
        unresolved_ids,
        duplicate_code="unresolved_ids_not_unique",
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
