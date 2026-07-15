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
_FILENAME_TOKEN = r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+"
_SAFE_TARGET_RE = re.compile(
    rf"(?:[A-Za-z0-9_-]+/)*{_FILENAME_TOKEN}\Z"
)
_WINDOWS_FORBIDDEN_TARGET_CHARS = frozenset(':?*"<>|')
_LITERAL_TEXT = r"[A-Za-z0-9](?:[A-Za-z0-9._:-]{0,254}[A-Za-z0-9])?"
_LITERAL_TOKEN = (
    rf"({_LITERAL_TEXT})"
    r"(?=\s*(?:换行|newline|[；;。.，,]|$))"
)
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
_EXPLICIT_VALUE_LEAD_RE = re.compile(
    r"内容为|要求|精确改为|(?:with\s+)?content\s+(?:is\s+)?exactly"
    r"|requires?|to\s+exactly",
    re.IGNORECASE,
)
_CLAUSE_COUNT_MISMATCH = "\x00clause_count_mismatch"
_UNSUPPORTED_LOGIC_RE = re.compile(
    r"不要|如果|除非|\bdo\s+not\b|\bdon't\b|\bif\b|\bunless\b",
    re.IGNORECASE,
)
_AMBIGUOUS_OPERATION_RE = re.compile(
    r"(?:创建|更新|审查|验收|读取)\s*(?:或|并|且)\s*"
    r"(?:创建|更新|审查|验收|读取)"
    r"|\b(?:create|update|review|verify|read)s?\s+(?:or|and)\s+"
    r"(?:create|update|review|verify|read)s?\b",
    re.IGNORECASE,
)
_AGENT_ACTION_TOKEN = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}"
_CHINESE_ACTION_PREFIX = rf"^(?:第一轮|第二轮|第三轮|第四轮)\s+{_AGENT_ACTION_TOKEN}\s+"
_ENGLISH_ACTION_PREFIX = rf"^(?:First|Second|Third|Fourth)\b[:,]?\s+{_AGENT_ACTION_TOKEN}\s+"
_ACTION_PATTERNS = (
    ("create", re.compile(rf"{_CHINESE_ACTION_PREFIX}创建\s", re.IGNORECASE)),
    ("update", re.compile(rf"{_CHINESE_ACTION_PREFIX}将\s+.+?\s+精确改为\s", re.IGNORECASE)),
    ("review", re.compile(rf"{_CHINESE_ACTION_PREFIX}只读审查", re.IGNORECASE)),
    ("verify", re.compile(rf"{_CHINESE_ACTION_PREFIX}只读验收", re.IGNORECASE)),
    ("read", re.compile(rf"{_CHINESE_ACTION_PREFIX}只读读取", re.IGNORECASE)),
    ("create", re.compile(rf"{_ENGLISH_ACTION_PREFIX}creates?\s", re.IGNORECASE)),
    ("update", re.compile(rf"{_ENGLISH_ACTION_PREFIX}(?:updates?|changes?)\s", re.IGNORECASE)),
    (
        "review",
        re.compile(
            rf"{_ENGLISH_ACTION_PREFIX}(?:performs?\s+(?:a\s+)?read-only\s+)?reviews?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "verify",
        re.compile(
            rf"{_ENGLISH_ACTION_PREFIX}(?:performs?\s+read-only\s+)?(?:verification|verif(?:y|ies))\b",
            re.IGNORECASE,
        ),
    ),
    ("read", re.compile(rf"{_ENGLISH_ACTION_PREFIX}reads?\s", re.IGNORECASE)),
)
_EXPLICIT_ACTION_AGENT_PATTERNS = (
    re.compile(
        rf"^(?:第一轮|第二轮|第三轮|第四轮)\s+({_AGENT_ACTION_TOKEN})\s+"
    ),
    re.compile(
        rf"^(?:First|Second|Third|Fourth)\b[:,]?\s+({_AGENT_ACTION_TOKEN})\s+",
        re.IGNORECASE,
    ),
)
_ASSIGNMENT_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"(?P<key>[A-Za-z][A-Za-z0-9_. -]{0,127}?)\s*"
    r"(?P<separator>[:=])\s*"
    r"(?P<value>[^\s；;。,]+)"
)
_SENSITIVE_KEY_COMPONENTS = frozenset(
    {"token", "secret", "password", "passwd", "credential", "credentials"}
)
_SENSITIVE_KEY_PREFIXES = frozenset(
    {"api", "access", "private", "signing", "encryption", "ssh", "client", "secret"}
)
_FUSED_SENSITIVE_KEY_RE = re.compile(
    r"[a-z0-9]*(?:password|passwd|secret|token|credentials?)"
    r"(?:(?:hash|value|id|key|digest|ref|reference))*\Z"
)
_KNOWN_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:sk-[A-Za-z0-9_-]+|gh[pousr]_[A-Za-z0-9_-]+"
    r"|github_pat_[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
_TARGET_SEGMENT_PATTERNS = (
    re.compile(rf"{_CHINESE_ACTION_PREFIX}创建\s+(?P<target>.+?)\s+且内容"),
    re.compile(rf"{_CHINESE_ACTION_PREFIX}将\s+(?P<target>.+?)\s+精确改为"),
    re.compile(
        rf"{_ENGLISH_ACTION_PREFIX}creates?\s+(?P<target>.+?)\s+with\s+content",
        re.IGNORECASE,
    ),
    re.compile(
        rf"{_ENGLISH_ACTION_PREFIX}(?:updates?|changes?)\s+(?P<target>.+?)\s+to\s+exactly",
        re.IGNORECASE,
    ),
    re.compile(rf"{_CHINESE_ACTION_PREFIX}只读读取\s+(?P<target>.+?)。?$"),
    re.compile(
        rf"{_CHINESE_ACTION_PREFIX}只读审查\s+(?P<target>.+?)\s+并要求"
    ),
    re.compile(
        rf"{_CHINESE_ACTION_PREFIX}只读验收\s+(?P<target>.+?)\s+精确字节。?$"
    ),
    re.compile(
        rf"{_ENGLISH_ACTION_PREFIX}reads?\s+(?P<target>.+?)$", re.IGNORECASE
    ),
    re.compile(
        rf"{_ENGLISH_ACTION_PREFIX}reviews?\s+(?P<target>.+?)\s+and\s+requires?",
        re.IGNORECASE,
    ),
    re.compile(
        rf"{_ENGLISH_ACTION_PREFIX}verif(?:y|ies)\s+(?P<target>.+?)\s+exact\s+bytes$",
        re.IGNORECASE,
    ),
)
_OPEN_TARGET_RE = re.compile(r"(?P<target>\S*\.[^\s]*)\s*$")
_SUPPORTED_CLAUSE_PATTERNS = {
    "create": (
        re.compile(
            rf"{_CHINESE_ACTION_PREFIX}创建\s+\S+\s+且内容为\s+{_LITERAL_TEXT}(?:\s*换行)?。?"
        ),
        re.compile(
            rf"{_ENGLISH_ACTION_PREFIX}creates?\s+\S+\s+with\s+content\s+(?:is\s+)?exactly\s+{_LITERAL_TEXT}(?:\s+newline)?",
            re.IGNORECASE,
        ),
    ),
    "update": (
        re.compile(
            rf"{_CHINESE_ACTION_PREFIX}将\s+\S+\s+精确改为\s+{_LITERAL_TEXT}(?:\s*换行)?。?"
        ),
        re.compile(
            rf"{_ENGLISH_ACTION_PREFIX}(?:updates?|changes?)\s+\S+\s+to\s+exactly\s+{_LITERAL_TEXT}(?:\s+newline)?",
            re.IGNORECASE,
        ),
    ),
    "review": (
        re.compile(
            rf"{_CHINESE_ACTION_PREFIX}只读审查并要求\s+{_LITERAL_TEXT}(?:\s*换行)?。?"
        ),
        re.compile(
            rf"{_CHINESE_ACTION_PREFIX}只读审查\s+\S+\s+并要求\s+{_LITERAL_TEXT}(?:\s*换行)?。?"
        ),
        re.compile(
            rf"{_ENGLISH_ACTION_PREFIX}performs?\s+(?:a\s+)?read-only\s+review\s+and\s+requires?\s+{_LITERAL_TEXT}(?:\s+newline)?",
            re.IGNORECASE,
        ),
        re.compile(
            rf"{_ENGLISH_ACTION_PREFIX}reviews?\s+\S+\s+and\s+requires?\s+{_LITERAL_TEXT}(?:\s+newline)?",
            re.IGNORECASE,
        ),
    ),
    "verify": (
        re.compile(
            rf"{_CHINESE_ACTION_PREFIX}只读验收精确字节(?:。共\d+轮)?。?"
        ),
        re.compile(
            rf"{_CHINESE_ACTION_PREFIX}只读验收\s+\S+\s+精确字节。?"
        ),
        re.compile(
            rf"{_ENGLISH_ACTION_PREFIX}performs?\s+read-only\s+verification\s+of\s+the\s+exact\s+bytes(?:\.\s+There\s+are\s+\d+\s+steps)?",
            re.IGNORECASE,
        ),
        re.compile(
            rf"{_ENGLISH_ACTION_PREFIX}verif(?:y|ies)\s+\S+\s+exact\s+bytes",
            re.IGNORECASE,
        ),
    ),
    "read": (
        re.compile(rf"{_CHINESE_ACTION_PREFIX}只读读取\s+\S+。?"),
        re.compile(rf"{_ENGLISH_ACTION_PREFIX}reads?\s+\S+", re.IGNORECASE),
    ),
}


@dataclass(frozen=True)
class _ExtractionInput:
    message: str
    selected_agent_ids: tuple[str, ...]
    step_count: int
    phases: tuple[str, ...] | None


@dataclass(frozen=True)
class _ParsedClause:
    operation: str | None
    explicit_agent_id: str | None
    targets: tuple[str, ...]
    values: tuple[str, ...]
    fully_supported: bool


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
    if (
        type(value) is not str
        or not value
        or "\x00" in value
        or "\\" in value
        or any(character in value for character in _WINDOWS_FORBIDDEN_TARGET_CHARS)
    ):
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
    clauses = tuple(
        clause.strip(" \t\r\n；;.：:")
        for clause in re.split(r"[；;\n]+", message)
        if clause.strip(" \t\r\n；;.：:")
    )
    if len(clauses) > 1 and len(clauses) != step_count:
        return (_CLAUSE_COUNT_MISMATCH,)
    return clauses


def _targets_from_clause(
    clause: str, operation: str | None
) -> tuple[str, ...]:
    for pattern in _TARGET_SEGMENT_PATTERNS:
        match = pattern.search(clause)
        if match is not None:
            segment = match.group("target").strip()
            candidates = tuple(
                candidate.strip()
                for candidate in re.split(r"\s+(?:和|and)\s+", segment)
                if candidate.strip()
            )
            return candidates
    if operation is not None:
        return ()
    match = _OPEN_TARGET_RE.search(clause)
    if match is None:
        return ()
    return (match.group("target").strip("；;。.，,"),)


def _explicit_targets(clause: str) -> tuple[str, ...]:
    operation = _classify_operation(clause)
    return _targets_from_clause(clause, operation)


def _validate_extraction_target(value: str) -> None:
    _validate_target(value)
    if _SAFE_TARGET_RE.fullmatch(value) is None:
        _fail("target_invalid")


def _values_from_clause(clause: str) -> tuple[str, ...]:
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


def _explicit_values(clause: str) -> tuple[str, ...]:
    return _values_from_clause(clause)


def _classify_operation(clause: str) -> str | None:
    for operation, pattern in _ACTION_PATTERNS:
        if pattern.search(clause):
            return operation
    return None


def _clause_logic_kind(clause: str) -> str | None:
    if _UNSUPPORTED_LOGIC_RE.search(clause):
        return "unsupported_clause_logic"
    if _AMBIGUOUS_OPERATION_RE.search(clause):
        return "ambiguous_operation"
    return None


def _explicit_action_agent(clause: str) -> str | None:
    for pattern in _EXPLICIT_ACTION_AGENT_PATTERNS:
        match = pattern.search(clause)
        if match is not None:
            return match.group(1)
    return None


def _parse_clause(clause: str) -> _ParsedClause:
    operation = _classify_operation(clause)
    return _ParsedClause(
        operation=operation,
        explicit_agent_id=_explicit_action_agent(clause),
        targets=_targets_from_clause(clause, operation),
        values=_values_from_clause(clause),
        fully_supported=_clause_is_fully_supported(clause, operation),
    )


def _classify_sensitive(clause: str) -> str:
    return (
        "sensitive_content"
        if semantic_text_contains_sensitive_value(clause)
        else "ordinary"
    )


def _sensitive_key_components(key: str) -> tuple[str, ...]:
    acronym_split = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", key)
    camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", acronym_split)
    return tuple(
        component
        for component in re.split(r"[^A-Za-z0-9]+", camel_split.lower())
        if component
    )


def _is_sensitive_assignment_key(key: str) -> bool:
    components = _sensitive_key_components(key)
    collapsed = "".join(components)
    if any(component in _SENSITIVE_KEY_COMPONENTS for component in components):
        return True
    if any(collapsed.endswith(suffix) for suffix in _SENSITIVE_KEY_COMPONENTS):
        return True
    if _FUSED_SENSITIVE_KEY_RE.fullmatch(collapsed) is not None:
        return True
    return any(
        f"{family}key" in collapsed for family in _SENSITIVE_KEY_PREFIXES
    )


def semantic_text_contains_sensitive_value(text: object) -> bool:
    """Return whether an exact built-in string contains sensitive plaintext."""
    if type(text) is not str:
        return False
    if _KNOWN_TOKEN_RE.search(text) is not None:
        return True
    return any(
        _is_sensitive_assignment_key(match.group("key"))
        for match in _ASSIGNMENT_RE.finditer(text)
    )


def _redact_sensitive_assignments(message: str) -> str:
    pieces: list[str] = []
    position = 0
    for match in _ASSIGNMENT_RE.finditer(message):
        if not _is_sensitive_assignment_key(match.group("key")):
            continue
        pieces.append(message[position : match.start("value")])
        pieces.append("<sensitive-value>")
        position = match.end("value")
    pieces.append(message[position:])
    return _KNOWN_TOKEN_RE.sub("<sensitive-value>", "".join(pieces))


def _source_message_hash(message: str) -> str:
    return _sha256_text(_redact_sensitive_assignments(message))


def _clause_is_fully_supported(clause: str, operation: str | None) -> bool:
    if operation is None:
        return True
    return any(
        pattern.fullmatch(clause) is not None
        for pattern in _SUPPORTED_CLAUSE_PATTERNS[operation]
    )


def _static_clause_rejection_kind(
    *,
    clause: str,
    agent_id: str,
    operation: str | None,
    logic_kind: str | None,
    agent_mismatch: bool,
    sensitivity: str,
    targets: tuple[str, ...],
    values: tuple[str, ...],
    unsafe_target: bool,
    unsupported_value: bool,
    fully_supported: bool,
) -> str | None:
    if sensitivity != "ordinary":
        return "sensitive_content"
    if agent_mismatch:
        return "wrong_or_unknown_agent"
    if logic_kind is not None:
        return logic_kind
    if len(targets) > 1:
        return "ambiguous_target"
    if len(values) > 1:
        return "ambiguous_literal"
    if _UNSUPPORTED_EXACT_VALUE_RE.search(clause) and not values:
        return "unsupported_literal"
    if unsupported_value:
        return "unsupported_value"
    if not fully_supported:
        return "unsupported_clause_logic"
    if unsafe_target:
        return "unsafe_target"
    if operation is None and (targets or values):
        return "unbound_explicit_detail"
    if operation is not None and agent_id == "unassigned":
        return "ambiguous_agent"
    return None


def _opaque_item_id(prefix: str, body: dict[str, object]) -> str:
    return f"{prefix}_{hashlib.sha256(_canonical_bytes(body)).hexdigest()[:12]}"


def _requirements_from_clauses(
    clauses: tuple[str, ...],
    *,
    selected_agent_ids: tuple[str, ...],
    phases: tuple[str, ...] | None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if clauses == (_CLAUSE_COUNT_MISMATCH,):
        body: dict[str, object] = {
            "kind": "clause_count_mismatch",
            "phase": phases[0] if phases is not None else "step-1",
            "agent_id": (
                selected_agent_ids[0]
                if len(selected_agent_ids) == 1
                else "unassigned"
            ),
        }
        return [], [{"unresolved_id": _opaque_item_id("unr", body), **body}]

    analyses: list[dict[str, object]] = []
    for index, clause in enumerate(clauses):
        phase = (
            phases[index]
            if phases is not None and index < len(phases)
            else f"step-{index + 1}"
        )
        parsed = _parse_clause(clause)
        expected_agent_id = selected_agent_ids[index % len(selected_agent_ids)]
        explicit_agent_id = parsed.explicit_agent_id
        agent_mismatch = (
            explicit_agent_id is not None
            and explicit_agent_id != expected_agent_id
        )
        agent_id = expected_agent_id
        operation = parsed.operation
        logic_kind = _clause_logic_kind(clause)
        supported_clause = parsed.fully_supported
        sensitivity = _classify_sensitive(clause)
        if sensitivity != "ordinary":
            analyses.append(
                {
                    "clause": "",
                    "phase": phase,
                    "agent_id": agent_id,
                    "operation": operation,
                    "logic_kind": logic_kind,
                    "agent_mismatch": agent_mismatch,
                    "sensitivity": sensitivity,
                    "targets": (),
                    "values": (),
                    "unsafe_target": False,
                    "unsupported_value": False,
                    "rejection_kind": "sensitive_content",
                    "validated_action_candidate": False,
                }
            )
            continue
        targets = parsed.targets
        values = parsed.values
        safe_targets: list[str] = []
        unsafe_target = False
        for target in targets:
            try:
                _validate_extraction_target(target)
            except SemanticAuthorityError:
                unsafe_target = True
            else:
                safe_targets.append(target)
        safe_targets_tuple = tuple(safe_targets)
        unsupported_value = (
            _EXPLICIT_VALUE_LEAD_RE.search(clause) is not None and not values
        )
        rejection_kind = _static_clause_rejection_kind(
            clause=clause,
            agent_id=agent_id,
            operation=operation,
            logic_kind=logic_kind,
            agent_mismatch=agent_mismatch,
            sensitivity=sensitivity,
            targets=safe_targets_tuple,
            values=values,
            unsafe_target=unsafe_target,
            unsupported_value=unsupported_value,
            fully_supported=supported_clause,
        )
        analyses.append(
            {
                "clause": "" if unsafe_target else clause,
                "phase": phase,
                "agent_id": agent_id,
                "operation": operation,
                "logic_kind": logic_kind,
                "agent_mismatch": agent_mismatch,
                "sensitivity": sensitivity,
                "targets": safe_targets_tuple,
                "values": values,
                "unsafe_target": unsafe_target,
                "unsupported_value": unsupported_value,
                "supported_clause": supported_clause,
                "rejection_kind": rejection_kind,
                "validated_action_candidate": (
                    rejection_kind is None
                    and operation is not None
                    and len(safe_targets_tuple) == 1
                    and len(values) == 1
                ),
            }
        )

    simulated_state_targets: set[str] = set()
    for analysis in analyses:
        analysis["semantic_state_candidate"] = False
        if not analysis["validated_action_candidate"]:
            continue
        operation = analysis["operation"]
        targets = analysis["targets"]
        assert type(targets) is tuple and len(targets) == 1
        target = targets[0]
        assert type(target) is str
        if operation == "create":
            simulated_state_targets.add(target)
        elif operation == "update" and target in simulated_state_targets:
            analysis["semantic_state_candidate"] = True

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

        rejection_kind = analysis["rejection_kind"]
        assert rejection_kind is None or type(rejection_kind) is str
        if rejection_kind is not None:
            add_unresolved(rejection_kind, phase, agent_id)
            continue
        if operation is None:
            continue

        target = targets[0] if targets else None
        if target is None and operation in {"review", "verify"}:
            if len(states) == 1:
                target = next(iter(states))
        if target is None:
            add_unresolved("missing_target", phase, agent_id)
            continue

        literal = values[0] if values else None
        if operation == "review" and literal is not None and not literal.endswith("\n"):
            aligned = [
                later["values"][0]
                for later in analyses[index + 1 :]
                if later["semantic_state_candidate"]
                and later["operation"] == "update"
                and len(later["targets"]) == 1
                and later["targets"][0] == target
                and len(later["values"]) == 1
                and later["values"][0].removesuffix("\n") == literal
            ]
            if len(aligned) == 1:
                literal = aligned[0]
        if (
            literal is None
            and operation in {"read", "verify"}
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
    if len(unresolved) > _MAX_UNRESOLVED_COUNT:
        summary: dict[str, object] = {
            "kind": "unresolved_count_exceeded",
            "phase": phases[0] if phases is not None else "step-1",
            "agent_id": selected_agent_ids[0],
        }
        unresolved = [
            {"unresolved_id": _opaque_item_id("unr", summary), **summary}
        ]
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
            "source_message_hash": _source_message_hash(normalized.message),
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
