"""Bounded, immutable human-facing facts for Product rendering."""

from __future__ import annotations

from dataclasses import dataclass
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
class ExitPresentation:
    summary: str
    active_attempts: tuple[str, ...]
    requires_confirmation: bool

    def __post_init__(self) -> None:
        _human_text(self.summary, "summary")
        object.__setattr__(
            self, "active_attempts", _human_items(self.active_attempts, "active_attempts")
        )
        if type(self.requires_confirmation) is not bool:
            raise TypeError("requires_confirmation must be a bool")
        if self.requires_confirmation is not bool(self.active_attempts):
            raise ValueError("active attempts and confirmation must agree")


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
