from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from agentdeck.kernel.events import normalize_occurred_at


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Diagnostic:
    code: str
    stage: str
    severity: Severity
    actor: str
    summary: str
    cause: str
    impact: str
    protection: str
    recovery_actions: tuple[str, ...]
    retryable: bool
    outcome_known: bool
    occurred_at: str
    mission_id: str | None = None
    task_id: str | None = None
    attempt_id: str | None = None
    trace_id: str | None = None

    def __post_init__(self) -> None:
        required_strings = (
            self.code,
            self.stage,
            self.actor,
            self.summary,
            self.cause,
            self.impact,
            self.protection,
        )
        optional_strings = (
            self.mission_id,
            self.task_id,
            self.attempt_id,
            self.trace_id,
        )
        if any(type(value) is not str for value in required_strings):
            raise TypeError("diagnostic text fields must be strings")
        if any(
            value is not None and type(value) is not str for value in optional_strings
        ):
            raise TypeError("diagnostic identity fields must be strings or None")
        if type(self.severity) is not Severity:
            raise TypeError("diagnostic severity must be a Severity")
        if type(self.retryable) is not bool or type(self.outcome_known) is not bool:
            raise TypeError("diagnostic flags must be booleans")
        if type(self.recovery_actions) is not tuple or any(
            type(action) is not str for action in self.recovery_actions
        ):
            raise TypeError("diagnostic recovery actions must be a tuple of strings")
        object.__setattr__(
            self, "occurred_at", normalize_occurred_at(self.occurred_at)
        )

    @classmethod
    def create(
        cls,
        *,
        code: str,
        stage: str,
        severity: Severity,
        actor: str,
        summary: str,
        cause: str,
        impact: str,
        protection: str,
        recovery_actions: Iterable[str],
        retryable: bool,
        outcome_known: bool,
        occurred_at: str,
        mission_id: str | None = None,
        task_id: str | None = None,
        attempt_id: str | None = None,
        trace_id: str | None = None,
    ) -> "Diagnostic":
        if isinstance(recovery_actions, (str, bytes)):
            raise TypeError("diagnostic recovery actions must be an iterable of strings")
        return cls(
            code=code,
            stage=stage,
            severity=severity,
            actor=actor,
            summary=summary,
            cause=cause,
            impact=impact,
            protection=protection,
            recovery_actions=tuple(recovery_actions),
            retryable=retryable,
            outcome_known=outcome_known,
            occurred_at=normalize_occurred_at(occurred_at),
            mission_id=mission_id,
            task_id=task_id,
            attempt_id=attempt_id,
            trace_id=trace_id,
        )
