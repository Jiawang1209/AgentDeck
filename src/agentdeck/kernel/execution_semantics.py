"""Pure review, revision, acceptance, and retry semantics."""

from dataclasses import dataclass
import posixpath
from types import MappingProxyType

from agentdeck.kernel.execution import (
    AcceptanceResult,
    FindingSeverity,
    ResultError,
    ReviewFinding,
)

_MAX_TEXT_BYTES = 64 * 1024
_SQLITE_MAX_INTEGER = 2**63 - 1
_FINDING_FIELDS = {
    "finding_id", "scope", "severity", "summary", "criterion", "evidence_ids"
}
_REVISION_FIELDS = {
    "summary", "base", "head", "diff_hash", "resolved_finding_ids", "evidence_ids"
}
_RETRY_CONDITIONS = frozenset({
    "transport_before_effect", "worker_schema_invalid", "known_test_failure",
    "permission_denied", "outcome_unknown", "project_drift",
    "scope_insufficiency", "login_loss",
})
_RETRY_ON_FIRST_ATTEMPT = frozenset({
    "transport_before_effect", "worker_schema_invalid"
})


def _text(value: object, field: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field} must be a string")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        raise ValueError(f"{field} must be valid UTF-8") from None
    if not value.strip() or len(encoded) > _MAX_TEXT_BYTES:
        raise ValueError(f"{field} must be nonempty and bounded")
    return value


def _identity(value: object, prefix: str, field: str) -> str:
    text = _text(value, field)
    if (
        not text.startswith(prefix)
        or not text.removeprefix(prefix)
        or any(character.isspace() for character in text)
    ):
        raise ValueError(f"{field} must be a typed identity")
    return text


def _strings(
    value: object, field: str, *, nonempty: bool, prefix: str | None = None,
) -> tuple[str, ...]:
    if type(value) not in {list, tuple}:
        raise TypeError(f"{field} must be a list or tuple")
    copied = tuple(
        _identity(item, prefix, field) if prefix else _text(item, field)
        for item in value
    )
    if nonempty and not copied:
        raise ResultError(f"{field} must not be empty")
    if len(copied) != len(set(copied)):
        raise ResultError(f"{field} must not contain duplicates")
    return copied


def _finding_parts(
    value: object,
) -> tuple[str, str, FindingSeverity, str, str, tuple[str, ...]]:
    if type(value) is not dict or set(value) != _FINDING_FIELDS:
        raise ResultError("review finding requires an exact typed payload")
    try:
        return (
            _identity(value["finding_id"], "rfn_", "finding_id"),
            _text(value["scope"], "scope"),
            FindingSeverity(value["severity"]),
            _text(value["summary"], "summary"),
            _text(value["criterion"], "criterion"),
            _strings(
                value["evidence_ids"], "evidence_ids",
                nonempty=False, prefix="ev_",
            ),
        )
    except (KeyError, TypeError, ValueError):
        raise ResultError("review finding is invalid") from None


@dataclass(frozen=True)
class ReviewResult:
    summary: str
    findings: tuple[ReviewFinding, ...]

    def __post_init__(self) -> None:
        _text(self.summary, "review summary")
        if type(self.findings) not in {list, tuple}:
            raise TypeError("review findings must be a list or tuple")
        findings = tuple(self.findings)
        if any(type(item) is not ReviewFinding for item in findings):
            raise TypeError("review findings must contain ReviewFinding values")
        identities = tuple(item.finding_id for item in findings)
        if len(identities) != len(set(identities)):
            raise ResultError("review findings must not duplicate identities")
        object.__setattr__(self, "findings", findings)

    @classmethod
    def from_mapping(cls, value: object) -> "ReviewResult":
        try:
            if type(value) is not dict or set(value) != {"summary", "findings"}:
                raise ResultError
            if type(value["findings"]) is not list:
                raise ResultError
            findings = []
            for raw in value["findings"]:
                parts = _finding_parts(raw)
                if not parts[-1]:
                    raise ResultError
                findings.append(ReviewFinding(*parts))
            return cls(value["summary"], tuple(findings))
        except (KeyError, TypeError, ValueError, ResultError):
            raise ResultError("review result is invalid") from None


@dataclass(frozen=True)
class RevisionResult:
    summary: str
    base: str
    head: str
    diff_hash: str
    resolved_finding_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]

    @classmethod
    def from_mapping(
        cls, value: object, *, accepted_finding_ids: object,
        expected_evidence_ids: object,
    ) -> "RevisionResult":
        try:
            if type(value) is not dict or set(value) != _REVISION_FIELDS:
                raise ResultError
            expected = _strings(
                accepted_finding_ids, "accepted_finding_ids", nonempty=True,
                prefix="rfn_",
            )
            resolved = _strings(
                value["resolved_finding_ids"], "resolved_finding_ids",
                nonempty=True, prefix="rfn_",
            )
            evidence = _strings(
                value["evidence_ids"], "evidence_ids", nonempty=True,
                prefix="ev_",
            )
            expected_evidence = _strings(
                expected_evidence_ids, "expected_evidence_ids", nonempty=True,
                prefix="ev_",
            )
            diff_hash = _text(value["diff_hash"], "diff_hash")
            if len(diff_hash) != 64 or any(
                character not in "0123456789abcdef" for character in diff_hash
            ) or resolved != expected or evidence != expected_evidence:
                raise ResultError
            return cls(
                _text(value["summary"], "summary"),
                _text(value["base"], "base"), _text(value["head"], "head"),
                diff_hash, resolved, evidence,
            )
        except (KeyError, TypeError, ValueError, ResultError):
            raise ResultError("revision result is invalid") from None


@dataclass(frozen=True)
class RejectedFinding:
    finding_id: str
    reason: str

    def __post_init__(self) -> None:
        _identity(self.finding_id, "rfn_", "finding_id")
        if self.reason not in {"outside_confirmed_scope", "typed_evidence_missing"}:
            raise ResultError("rejection reason is not declared")


@dataclass(frozen=True)
class RevisionMaterialization:
    findings: tuple[ReviewFinding, ...]
    rejected: tuple[RejectedFinding, ...]

    def __post_init__(self) -> None:
        if type(self.findings) is not tuple or any(
            type(item) is not ReviewFinding for item in self.findings
        ):
            raise TypeError("findings must be a tuple of ReviewFinding values")
        if type(self.rejected) is not tuple or any(
            type(item) is not RejectedFinding for item in self.rejected
        ):
            raise TypeError("rejected must be a tuple of RejectedFinding values")


def _scope_is_confirmed(scope: str, confirmed_scope: tuple[str, ...]) -> bool:
    normalized = posixpath.normpath(scope)
    return any(
        normalized == allowed or normalized.startswith(allowed.rstrip("/") + "/")
        for allowed in confirmed_scope
    )


def materialize_revision(
    *, findings: object, confirmed_scope: object,
) -> RevisionMaterialization:
    if type(findings) not in {list, tuple}:
        raise TypeError("findings must be a list or tuple")
    scopes = _strings(confirmed_scope, "confirmed_scope", nonempty=True)
    normalized_scopes = tuple(posixpath.normpath(scope) for scope in scopes)
    accepted: list[ReviewFinding] = []
    rejected: list[RejectedFinding] = []
    seen: set[str] = set()
    for raw in findings:
        parts = (
            (
                raw.finding_id, raw.scope, raw.severity, raw.summary,
                raw.criterion, raw.evidence_ids,
            )
            if type(raw) is ReviewFinding else _finding_parts(raw)
        )
        finding_id, scope, severity, summary, criterion, evidence_ids = parts
        if finding_id in seen:
            raise ResultError("revision findings must not duplicate identities")
        seen.add(finding_id)
        if not _scope_is_confirmed(scope, normalized_scopes):
            rejected.append(RejectedFinding(finding_id, "outside_confirmed_scope"))
        elif not evidence_ids:
            rejected.append(RejectedFinding(finding_id, "typed_evidence_missing"))
        else:
            accepted.append(ReviewFinding(
                finding_id, scope, severity, summary, criterion, evidence_ids
            ))
    return RevisionMaterialization(tuple(accepted), tuple(rejected))


def validate_acceptance(
    *, criteria: object, mappings: object, accepted: bool = True,
    failure_reason: str | None = None,
) -> AcceptanceResult:
    checked = _strings(criteria, "criteria", nonempty=True)
    if type(mappings) not in {dict, MappingProxyType}:
        raise TypeError("acceptance mappings must be a mapping")
    try:
        copied = dict(mappings)
    except Exception:
        raise ResultError("acceptance result is invalid") from None
    if any(type(key) is not str for key in copied):
        raise ResultError("acceptance result is invalid")
    for criterion in checked:
        if criterion not in copied:
            raise ResultError(f"criterion {criterion} missing evidence")
        if type(copied[criterion]) not in {list, tuple}:
            raise ResultError("acceptance result is invalid")
        if not copied[criterion]:
            raise ResultError(f"criterion {criterion} missing evidence")
    if set(copied) != set(checked):
        raise ResultError("acceptance has an unexpected evidence criterion")
    return AcceptanceResult.create(
        checked, copied, accepted=accepted, failure_reason=failure_reason
    )


@dataclass(frozen=True)
class RetryDecision:
    condition: str
    ordinal: int
    retry: bool

    def __post_init__(self) -> None:
        if type(self.condition) is not str or self.condition not in _RETRY_CONDITIONS:
            raise ResultError("retry condition is not declared")
        if type(self.ordinal) is not int:
            raise TypeError("ordinal must be an int")
        if not 1 <= self.ordinal <= _SQLITE_MAX_INTEGER:
            raise ValueError("ordinal must be a positive SQLite-safe integer")
        if type(self.retry) is not bool:
            raise TypeError("retry must be a bool")


@dataclass(frozen=True)
class RetryPolicy:
    @classmethod
    def default(cls) -> "RetryPolicy":
        return cls()

    def decision(self, condition: object, *, ordinal: object) -> RetryDecision:
        if type(condition) is not str or condition not in _RETRY_CONDITIONS:
            raise ResultError("retry condition is not declared")
        if type(ordinal) is not int:
            raise TypeError("ordinal must be an int")
        if not 1 <= ordinal <= _SQLITE_MAX_INTEGER:
            raise ValueError("ordinal must be a positive SQLite-safe integer")
        return RetryDecision(
            condition, ordinal,
            ordinal == 1 and condition in _RETRY_ON_FIRST_ATTEMPT,
        )


__all__ = [
    "RejectedFinding", "RetryDecision", "RetryPolicy", "ReviewResult", "RevisionResult",
    "RevisionMaterialization", "materialize_revision", "validate_acceptance",
]
