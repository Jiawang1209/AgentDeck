"""Bounded, immutable human-facing facts for Product rendering."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Final

from agentdeck.kernel.diagnostics import Diagnostic


_MAX_TEXT_BYTES: Final = 2_048
_MAX_ITEMS: Final = 64
_LOWER_HEX: Final = frozenset("0123456789abcdef")
_SESSION_STATES: Final = frozenset(
    {
        "setup", "ready", "drafting", "awaiting_confirmation", "running",
        "awaiting_approval", "paused", "needs_attention", "completed", "failed",
        "cancelled",
    }
)
_PERMISSIONS: Final = frozenset(
    {"ask-for-approval", "approve-for-me", "full-access"}
)
_PROHIBITED_TEXT: Final = re.compile(
    r"(?i)(?:"
    r"\b(?:authorization|cookie|set-cookie)\s*[:=]\s*\S+"
    r"|\bbearer\s+\S+"
    r"|\b(?:password|secret|token|api[_ -]?key)\s*[:=]\s*\S+"
    r"|[\"'][A-Za-z0-9_-]*(?:(?:api|private|ssh)[_ -]?key|authorization"
    r"|(?:set-)?cookie|credentials?|password|passphrase|secret|token)"
    r"[\"']\s*:\s*(?!(?:null|none)\s*(?:[,}]|$))(?=\S)"
    r"|-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----"
    r"|(?<![A-Za-z0-9])sk-(?:[A-Za-z0-9]{48}|(?:proj|ant)-[A-Za-z0-9_-]{16,})"
    r"|(?<![A-Za-z0-9])(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}"
    r"|(?<![A-Za-z0-9])AKIA[A-Z0-9]{16}"
    r"|(?<![A-Za-z0-9])AIza[A-Za-z0-9_-]{20,}"
    r"|\bssh-ed25519\s+[A-Za-z0-9+/]{32,}={0,3}"
    r")"
)
# Absolute filesystem paths, only when not embedded inside a URL or other
# non-whitespace-delimited token (so "acp://host/path" is left alone).
_ABSOLUTE_PATH: Final = re.compile(r"(?<!\S)/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+")
_RAW_STDERR: Final = re.compile(r"(?i)raw stderr")


def _human_text(value: object, field: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field} must be human-readable text")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        raise ValueError(f"{field} must be bounded human-readable text") from None
    if (
        not value.strip()
        or not value.isprintable()
        or len(encoded) > _MAX_TEXT_BYTES
    ):
        raise ValueError(f"{field} must be bounded human-readable text")
    if value.lstrip().startswith(("{", "[")) or _PROHIBITED_TEXT.search(value):
        raise ValueError("prohibited presentation text")
    return value


def _human_items(
    value: object, field: str, *, nonempty: bool = False
) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{field} must be a tuple")
    if len(value) > _MAX_ITEMS or (nonempty and not value):
        raise ValueError(f"{field} must be a bounded tuple")
    return tuple(_human_text(item, f"{field} item") for item in value)


def _bounded_integer(value: object, field: str, *, positive: bool = False) -> int:
    if type(value) is not int:
        raise TypeError(f"{field} must be an int")
    minimum = 1 if positive else 0
    if not minimum <= value <= 1_000_000:
        raise ValueError(f"{field} must be bounded")
    return value


@dataclass(frozen=True)
class SetupPresentation:
    project: str
    leaders: tuple[str, ...]
    permission: str

    def __post_init__(self) -> None:
        _human_text(self.project, "project")
        object.__setattr__(self, "leaders", _human_items(self.leaders, "leaders"))
        _human_text(self.permission, "permission")
        if self.permission not in _PERMISSIONS:
            raise ValueError("permission is unsupported")


@dataclass(frozen=True)
class StatusPresentation:
    state: str
    agents: tuple[str, ...]

    def __post_init__(self) -> None:
        _human_text(self.state, "state")
        if self.state not in _SESSION_STATES:
            raise ValueError("state is unsupported")
        object.__setattr__(self, "agents", _human_items(self.agents, "agents"))


@dataclass(frozen=True)
class MissionPreviewPresentation:
    objective: str
    scope: str
    leader_backend: str
    leader_model: str
    workers: tuple[str, ...]
    tasks: tuple[str, ...]
    task_dependencies: tuple[str, ...]
    acp_routes: tuple[str, ...]
    permission: str
    project_boundary: str
    acceptance_criteria: tuple[str, ...]
    retry_budget: int
    revision_budget: int
    non_goals: tuple[str, ...]
    risks: tuple[str, ...]
    preview_id: str
    version: int
    content_hash: str
    leader_adapter: str = "acp"
    leader_version: str = "unreported"
    additional_budgets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field in (
            "objective", "scope", "leader_backend", "leader_model", "permission",
            "project_boundary", "leader_adapter", "leader_version",
        ):
            _human_text(getattr(self, field), field)
        for field in (
            "workers", "tasks", "task_dependencies", "acp_routes",
            "acceptance_criteria", "non_goals", "risks",
        ):
            object.__setattr__(
                self, field, _human_items(getattr(self, field), field, nonempty=True)
            )
        object.__setattr__(
            self, "additional_budgets",
            _human_items(self.additional_budgets, "additional_budgets"),
        )
        if self.permission not in _PERMISSIONS:
            raise ValueError("permission is unsupported")
        _bounded_integer(self.retry_budget, "retry_budget")
        _bounded_integer(self.revision_budget, "revision_budget")
        _bounded_integer(self.version, "version", positive=True)
        _derived_identity(self.preview_id, "preview_id", "prv_", 24)
        _derived_identity(self.content_hash, "content_hash", "", 64)


@dataclass(frozen=True)
class RunningPresentation:
    mission_id: str
    stage: str
    agents: tuple[str, ...]
    completed_tasks: int
    total_tasks: int

    def __post_init__(self) -> None:
        _human_text(self.mission_id, "mission_id")
        _human_text(self.stage, "stage")
        object.__setattr__(self, "agents", _human_items(self.agents, "agents"))
        completed = _bounded_integer(self.completed_tasks, "completed_tasks")
        total = _bounded_integer(self.total_tasks, "total_tasks", positive=True)
        if completed > total:
            raise ValueError("completed_tasks must not exceed total_tasks")


@dataclass(frozen=True)
class ApprovalPresentation:
    actor: str
    effect: str
    risk: str
    reason: str

    def __post_init__(self) -> None:
        for field in ("actor", "effect", "risk", "reason"):
            _human_text(getattr(self, field), field)


@dataclass(frozen=True)
class DiagnosisPresentation:
    diagnostic: Diagnostic

    def __post_init__(self) -> None:
        if type(self.diagnostic) is not Diagnostic:
            raise TypeError("diagnostic must be a Diagnostic")
        fact = self.diagnostic
        for field in (
            "code", "stage", "actor", "summary", "cause", "impact", "protection",
            "occurred_at",
        ):
            _human_text(getattr(fact, field), f"diagnostic.{field}")
        for field in ("mission_id", "task_id", "attempt_id", "trace_id"):
            value = getattr(fact, field)
            if value is not None:
                _human_text(value, f"diagnostic.{field}")
        _human_items(
            fact.recovery_actions, "diagnostic.recovery_actions", nonempty=True
        )


@dataclass(frozen=True)
class ErrorCard:
    """A complete, human-facing Error Card derived from one Diagnostic."""

    what_happened: str
    why: str
    completed: str
    not_completed: str
    protection: str
    recovery_actions: tuple[str, ...]
    identity: str

    def __post_init__(self) -> None:
        for field in (
            "what_happened", "why", "completed", "not_completed",
            "protection", "identity",
        ):
            _human_text(getattr(self, field), field)
        object.__setattr__(
            self, "recovery_actions",
            _human_items(self.recovery_actions, "recovery_actions", nonempty=True),
        )


def present_diagnostic(fact: Diagnostic) -> ErrorCard:
    """Derive the complete Error Card for one Diagnostic fact."""

    if type(fact) is not Diagnostic:
        raise TypeError("fact must be a Diagnostic")
    completed = (
        "AgentDeck recorded a definite outcome for the affected step."
        if fact.outcome_known
        else (
            "AgentDeck completed a safe stop and preserved the affected "
            "step for reconciliation."
        )
    )
    identity_parts = [
        f"{label} {value}"
        for label, value in (
            ("mission", fact.mission_id),
            ("task", fact.task_id),
            ("attempt", fact.attempt_id),
            ("trace", fact.trace_id),
        )
        if value
    ]
    identity = (
        ", ".join(identity_parts) if identity_parts
        else "No mission, task, attempt, or trace identity was bound."
    )
    return ErrorCard(
        what_happened=fact.summary,
        why=fact.cause,
        completed=completed,
        not_completed=fact.impact,
        protection=fact.protection,
        recovery_actions=fact.recovery_actions,
        identity=identity,
    )


DIAGNOSTIC_SCHEMA_VERSION: Final = "diagnostic/v1"
DIAGNOSTIC_JSON_FIELDS: Final = frozenset(
    {
        "schema_version", "code", "stage", "severity", "actor", "summary",
        "cause", "impact", "protection", "recovery_actions", "retryable",
        "outcome_known", "occurred_at", "mission_id", "task_id",
        "attempt_id", "trace_id",
    }
)


def _redact_text(value: str) -> str:
    redacted = _PROHIBITED_TEXT.sub("[redacted]", value)
    redacted = _ABSOLUTE_PATH.sub("[redacted]", redacted)
    redacted = _RAW_STDERR.sub("[redacted]", redacted)
    return redacted


def _redact_optional(value: str | None) -> str | None:
    return None if value is None else _redact_text(value)


def diagnostic_json(fact: Diagnostic) -> str:
    """Render the redacted, closed-field JSON contract for one Diagnostic."""

    if type(fact) is not Diagnostic:
        raise TypeError("fact must be a Diagnostic")
    payload: dict[str, object] = {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "code": _redact_text(fact.code),
        "stage": _redact_text(fact.stage),
        "severity": fact.severity.value,
        "actor": _redact_text(fact.actor),
        "summary": _redact_text(fact.summary),
        "cause": _redact_text(fact.cause),
        "impact": _redact_text(fact.impact),
        "protection": _redact_text(fact.protection),
        "recovery_actions": tuple(
            _redact_text(action) for action in fact.recovery_actions
        ),
        "retryable": fact.retryable,
        "outcome_known": fact.outcome_known,
        "occurred_at": fact.occurred_at,
        "mission_id": _redact_optional(fact.mission_id),
        "task_id": _redact_optional(fact.task_id),
        "attempt_id": _redact_optional(fact.attempt_id),
        "trace_id": _redact_optional(fact.trace_id),
    }
    if set(payload) != DIAGNOSTIC_JSON_FIELDS:
        raise ValueError("diagnostic json payload fields are not the closed set")
    return json.dumps(payload, sort_keys=True)


@dataclass(frozen=True)
class ExitPresentation:
    summary: str
    active_attempts: tuple[str, ...]
    requires_confirmation: bool
    request_id: str | None = None
    attempt_hash: str | None = None

    def __post_init__(self) -> None:
        _human_text(self.summary, "summary")
        object.__setattr__(
            self, "active_attempts", _human_items(self.active_attempts, "active_attempts")
        )
        if type(self.requires_confirmation) is not bool:
            raise TypeError("requires_confirmation must be a bool")
        if self.requires_confirmation is not bool(self.active_attempts):
            raise ValueError("active attempts and confirmation must agree")
        if (self.request_id is None) is not (self.attempt_hash is None):
            raise ValueError("exit request identity and hash must be a closed pair")
        if self.request_id is not None:
            if not self.requires_confirmation:
                raise ValueError("idle exit cannot carry request authority")
            _derived_identity(self.request_id, "request_id", "xrt_", 32)
            _derived_identity(self.attempt_hash, "attempt_hash", "", 64)


@dataclass(frozen=True)
class FinalPresentation:
    state: str
    summary: str
    completed: tuple[str, ...]
    incomplete: tuple[str, ...]
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.state not in {"completed", "failed", "cancelled"}:
            raise ValueError("final state is unsupported")
        _human_text(self.state, "state")
        _human_text(self.summary, "summary")
        for field in ("completed", "incomplete", "evidence"):
            object.__setattr__(
                self, field, _human_items(getattr(self, field), field)
            )


def _derived_identity(
    value: object, field: str, prefix: str, suffix_length: int
) -> str:
    text = _human_text(value, field)
    suffix = text.removeprefix(prefix)
    if text != prefix + suffix or len(suffix) != suffix_length:
        raise ValueError(f"{field} is malformed")
    if any(character not in _LOWER_HEX for character in suffix):
        raise ValueError(f"{field} is malformed")
    return text
