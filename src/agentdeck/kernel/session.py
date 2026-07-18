from dataclasses import dataclass, replace
from enum import StrEnum


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
