"""Stable approval values and the independent reviewer Port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentdeck.kernel.events import normalize_occurred_at
from agentdeck.kernel.permissions import Effect
from agentdeck.ports.worker import validate_worker_reason


_MAX_TEXT_BYTES = 2_048


def _text(value: object, field: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field} must be a string")
    invalid_utf8 = False
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        invalid_utf8 = True
        encoded = b""
    if invalid_utf8 or not value.strip() or len(encoded) > _MAX_TEXT_BYTES:
        raise ValueError(f"{field} must be bounded text")
    return value


def _identity(value: object, field: str, prefix: str) -> str:
    text = _text(value, field)
    if not text.startswith(prefix) or not text[len(prefix):] or any(c.isspace() for c in text):
        raise ValueError(f"{field} must be a typed identity")
    return text


def _hash(value: object, field: str) -> str:
    text = _text(value, field)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{field} must be 64 lowercase hex")
    return text


def _timestamp(value: object, field: str) -> str:
    invalid = False
    try:
        normalized = normalize_occurred_at(_text(value, field))
    except (TypeError, ValueError):
        invalid = True
        normalized = ""
    if invalid:
        raise ValueError(f"{field} must be a timezone-aware timestamp")
    return normalized


@dataclass(frozen=True)
class ReviewerVerdict:
    allowed: bool
    reason: str

    def __post_init__(self) -> None:
        if type(self.allowed) is not bool:
            raise TypeError("allowed must be a bool")
        object.__setattr__(self, "reason", validate_worker_reason(self.reason))


class ApprovalReviewer(Protocol):
    reviewer_id: str

    async def review(self, request: "ApprovalRequest") -> ReviewerVerdict: ...


@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    mission_id: str
    mission_version: int
    task_id: str
    attempt_id: str
    agent_id: str
    permission_request_id: str
    effect: Effect
    risk: str
    scope_hash: str
    requested_at: str

    def __post_init__(self) -> None:
        _identity(self.approval_id, "approval_id", "apv_")
        _identity(self.mission_id, "mission_id", "msn_")
        _identity(self.task_id, "task_id", "tsk_")
        _identity(self.attempt_id, "attempt_id", "att_")
        _identity(self.agent_id, "agent_id", "agt_")
        _identity(self.permission_request_id, "permission_request_id", "perm_")
        if type(self.mission_version) is not int or not 1 <= self.mission_version <= 2**31 - 1:
            raise ValueError("mission_version must be a positive integer")
        if type(self.effect) is not Effect:
            raise TypeError("effect must be an Effect")
        object.__setattr__(self, "risk", validate_worker_reason(self.risk))
        _hash(self.scope_hash, "scope_hash")
        object.__setattr__(self, "requested_at", _timestamp(self.requested_at, "requested_at"))


@dataclass(frozen=True)
class ApprovalDecision:
    reviewer_id: str
    allowed: bool
    reason: str
    decided_at: str

    def __post_init__(self) -> None:
        reviewer = validate_worker_reason(_text(self.reviewer_id, "reviewer_id"))
        if reviewer not in {"agentdeck", "human"}:
            _identity(reviewer, "reviewer_id", "agt_")
        if type(self.allowed) is not bool:
            raise TypeError("allowed must be a bool")
        object.__setattr__(self, "reason", validate_worker_reason(self.reason))
        object.__setattr__(self, "decided_at", _timestamp(self.decided_at, "decided_at"))


@dataclass(frozen=True)
class ApprovalRecord:
    request: ApprovalRequest
    state: str
    decision: ApprovalDecision
    diagnostic_code: str | None = None

    def __post_init__(self) -> None:
        if type(self.request) is not ApprovalRequest:
            raise TypeError("request must be an ApprovalRequest")
        if type(self.decision) is not ApprovalDecision:
            raise TypeError("decision must be an ApprovalDecision")
        expected = "approved" if self.decision.allowed else "denied"
        if self.state != expected:
            raise ValueError("approval state and decision must agree")
        if self.diagnostic_code is not None:
            object.__setattr__(
                self, "diagnostic_code", validate_worker_reason(self.diagnostic_code)
            )


__all__ = [
    "ApprovalDecision", "ApprovalRecord", "ApprovalRequest", "ApprovalReviewer",
    "ReviewerVerdict",
]
