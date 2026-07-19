from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
from hmac import compare_digest
import json

from agentdeck.kernel.events import normalize_occurred_at
from agentdeck.kernel.execution import AttemptState


class SessionState(StrEnum):
    SETUP = "setup"
    READY = "ready"
    DRAFTING = "drafting"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    PAUSED = "paused"
    NEEDS_ATTENTION = "needs_attention"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TransitionError(ValueError):
    """Raised when a ProductSession is asked to make an undeclared transition."""


_TRANSITIONS: frozenset[tuple[SessionState, SessionState]] = frozenset(
    {
        (SessionState.SETUP, SessionState.READY),
        (SessionState.SETUP, SessionState.CANCELLED),
        (SessionState.READY, SessionState.DRAFTING),
        (SessionState.READY, SessionState.CANCELLED),
        (SessionState.DRAFTING, SessionState.AWAITING_CONFIRMATION),
        (SessionState.DRAFTING, SessionState.FAILED),
        (SessionState.AWAITING_CONFIRMATION, SessionState.DRAFTING),
        (SessionState.AWAITING_CONFIRMATION, SessionState.RUNNING),
        (SessionState.RUNNING, SessionState.AWAITING_APPROVAL),
        (SessionState.RUNNING, SessionState.PAUSED),
        (SessionState.RUNNING, SessionState.NEEDS_ATTENTION),
        (SessionState.RUNNING, SessionState.COMPLETED),
        (SessionState.RUNNING, SessionState.FAILED),
        (SessionState.RUNNING, SessionState.CANCELLED),
        (SessionState.AWAITING_APPROVAL, SessionState.RUNNING),
        (SessionState.AWAITING_APPROVAL, SessionState.FAILED),
        (SessionState.AWAITING_APPROVAL, SessionState.CANCELLED),
        (SessionState.PAUSED, SessionState.RUNNING),
        (SessionState.PAUSED, SessionState.CANCELLED),
        (SessionState.NEEDS_ATTENTION, SessionState.RUNNING),
        (SessionState.NEEDS_ATTENTION, SessionState.FAILED),
        (SessionState.NEEDS_ATTENTION, SessionState.CANCELLED),
    }
)


def _transition_is_allowed(
    source: SessionState,
    target: SessionState,
    authority: frozenset[tuple[SessionState, SessionState]] = _TRANSITIONS,
) -> bool:
    return (source, target) in authority


def _require_nonempty_string(value: object, field: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field} must be a string")
    if not value.strip():
        raise ValueError(f"{field} must not be empty")
    return value


_EXIT_ACTIVE_STATES = frozenset({
    AttemptState.RUNNING,
    AttemptState.AWAITING_APPROVAL,
    AttemptState.HUMAN_CONTROLLED,
})
_LOWER_HEX = frozenset("0123456789abcdef")
_SQLITE_MAX_INTEGER = 2**63 - 1


def _bounded_exit_text(
    value: object, field: str, maximum: int, *, prefix: str | None = None,
) -> str:
    text = _require_nonempty_string(value, field)
    try:
        encoded = text.encode("utf-8", "strict")
    except UnicodeEncodeError as error:
        raise ValueError(f"{field} must be strict UTF-8") from error
    if len(encoded) > maximum:
        raise ValueError(f"{field} is too large")
    if prefix is not None and (
        not text.startswith(prefix)
        or not text.removeprefix(prefix)
        or any(character.isspace() for character in text)
    ):
        raise ValueError(f"{field} must be a typed identity")
    return text


def _lower_hex(value: object, field: str, length: int) -> str:
    if (
        type(value) is not str
        or len(value) != length
        or any(character not in _LOWER_HEX for character in value)
    ):
        raise ValueError(f"{field} must be {length} lowercase hex")
    return value


@dataclass(frozen=True)
class ExitAttemptSnapshot:
    attempt_id: str
    task_id: str
    agent_instance_id: str | None
    ordinal: int
    state: AttemptState
    acp_session_id: str | None
    effect_observed: bool
    durable_fingerprint: str | None

    def __post_init__(self) -> None:
        _bounded_exit_text(self.attempt_id, "attempt_id", 255, prefix="att_")
        _bounded_exit_text(self.task_id, "task_id", 255, prefix="tsk_")
        if self.agent_instance_id is not None:
            _bounded_exit_text(
                self.agent_instance_id, "agent_instance_id", 255, prefix="agt_"
            )
        if type(self.ordinal) is not int:
            raise TypeError("ordinal must be an exact integer")
        if not 1 <= self.ordinal <= _SQLITE_MAX_INTEGER:
            raise ValueError("ordinal must be a positive SQLite signed integer")
        if type(self.state) is not AttemptState or self.state not in _EXIT_ACTIVE_STATES:
            raise ValueError("state must be active for exit")
        if self.acp_session_id is not None:
            _bounded_exit_text(self.acp_session_id, "acp_session_id", 255)
        if type(self.effect_observed) is not bool:
            raise TypeError("effect_observed must be a bool")
        if self.durable_fingerprint is not None:
            _lower_hex(self.durable_fingerprint, "durable_fingerprint", 64)
        self.canonical_bytes()

    def canonical_facts(self) -> dict[str, object]:
        return {
            "attempt_id": self.attempt_id,
            "task_id": self.task_id,
            "agent_instance_id": self.agent_instance_id,
            "ordinal": self.ordinal,
            "state": self.state.value,
            "acp_session_id": self.acp_session_id,
            "effect_observed": self.effect_observed,
            "durable_fingerprint": self.durable_fingerprint,
        }

    def canonical_bytes(self) -> bytes:
        encoded = json.dumps(
            self.canonical_facts(), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8", "strict")
        if not encoded or len(encoded) > 4_096:
            raise ValueError("canonical exit attempt facts are too large")
        return encoded

    @property
    def content_hash(self) -> str:
        return sha256(self.canonical_bytes()).hexdigest()


@dataclass(frozen=True)
class ExitRequest:
    request_id: str
    attempt: ExitAttemptSnapshot
    attempt_hash: str
    requested_at: str

    def __post_init__(self) -> None:
        request_id = _bounded_exit_text(
            self.request_id, "request_id", 36, prefix="xrt_"
        )
        _lower_hex(request_id[4:], "request_id suffix", 32)
        if type(self.attempt) is not ExitAttemptSnapshot:
            raise TypeError("attempt must be an ExitAttemptSnapshot")
        attempt_hash = _lower_hex(self.attempt_hash, "attempt_hash", 64)
        if not compare_digest(attempt_hash, self.attempt.content_hash):
            raise ValueError("attempt_hash does not match the exit attempt snapshot")
        requested_at = _bounded_exit_text(
            self.requested_at, "requested_at", 64
        )
        object.__setattr__(self, "requested_at", normalize_occurred_at(requested_at))


@dataclass(frozen=True)
class ProductSession:
    session_id: str
    project_root: str
    state: SessionState
    pending_goal: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty_string(self.session_id, "session_id")
        _require_nonempty_string(self.project_root, "project_root")
        if type(self.state) is not SessionState:
            raise TypeError("state must be a SessionState")
        if self.pending_goal is not None:
            _require_nonempty_string(self.pending_goal, "pending_goal")

    @classmethod
    def new(cls, session_id: str, project_root: str) -> "ProductSession":
        return cls(session_id, project_root, SessionState.SETUP)

    def retain_goal(self, goal: str) -> "ProductSession":
        if self.state is not SessionState.SETUP:
            raise TransitionError("goal retention requires setup state")
        _require_nonempty_string(goal, "goal")
        return replace(self, pending_goal=goal)

    def transition(self, target: SessionState) -> "ProductSession":
        if type(target) is not SessionState:
            raise TypeError("target must be a SessionState")
        if not _transition_is_allowed(self.state, target):
            raise TransitionError(
                f"illegal session transition: {self.state}->{target}"
            )
        return replace(self, state=target)
