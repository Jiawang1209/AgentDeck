from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

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


_DEFAULT_OCCURRED_AT: Final = "1970-01-01T00:00:00+00:00"

# Canonical catalog of every non-success Diagnostic AgentDeck can raise.
# Each template must be a complete, honest Error Card fact set: what
# happened, why, what it did and did not complete, what it protected, and
# at least one concrete recovery action. Unknown codes are refused rather
# than answered with a fabricated generic card.
_CATALOG: Final[dict[str, dict[str, object]]] = {
    "leader_authentication_failed": dict(
        stage="leader",
        severity=Severity.ERROR,
        actor="agentdeck",
        summary="The Leader could not authenticate.",
        cause="The Leader rejected or did not receive valid credentials.",
        impact="No Mission planning was started.",
        protection=(
            "No credentials were stored and no partial Mission was created."
        ),
        recovery_actions=(
            "Authenticate the Leader CLI or set its API key, then rerun setup.",
        ),
        retryable=True,
        outcome_known=True,
    ),
    "acp_protocol_mismatch": dict(
        stage="acp",
        severity=Severity.ERROR,
        actor="agentdeck",
        summary="The Agent spoke an unsupported ACP protocol version.",
        cause=(
            "The Agent adapter reported a protocol version AgentDeck does "
            "not support."
        ),
        impact="No Worker Task was dispatched.",
        protection="No malformed protocol exchange was accepted as authoritative.",
        recovery_actions=(
            "Update the Agent adapter to a supported ACP version, then retry.",
        ),
        retryable=False,
        outcome_known=True,
    ),
    "mission_preview_drift": dict(
        stage="mission_preview",
        severity=Severity.ERROR,
        actor="agentdeck",
        summary="The confirmed Mission Preview no longer matches the current draft.",
        cause=(
            "The Mission Preview content hash changed before confirmation "
            "was applied."
        ),
        impact="The Mission was not confirmed or started.",
        protection="No unconfirmed plan was executed.",
        recovery_actions=(
            "Review and confirm the exact current Mission Preview.",
        ),
        retryable=True,
        outcome_known=True,
    ),
    "worker_outcome_unknown": dict(
        stage="worker_result",
        severity=Severity.ERROR,
        actor="agentdeck",
        summary="The Worker transport closed before a definite result was received.",
        cause=(
            "The Agent connection ended before the Task attempt reported a "
            "definite outcome."
        ),
        impact="The Task result was not recorded as complete.",
        protection=(
            "No unverified result was accepted, and the Attempt was "
            "preserved for reconciliation."
        ),
        recovery_actions=(
            "Inspect and reconcile the durable Attempt before any new action.",
        ),
        retryable=False,
        outcome_known=False,
    ),
    "review_scope_invalid": dict(
        stage="review",
        severity=Severity.ERROR,
        actor="agentdeck",
        summary="The review result fell outside the allowed scope.",
        cause=(
            "The review referenced a Task or Attempt outside the current "
            "review boundary."
        ),
        impact="No review outcome was applied.",
        protection="No out-of-scope review changed Mission authority.",
        recovery_actions=(
            "Re-run the review against the exact Task and Attempt.",
        ),
        retryable=True,
        outcome_known=True,
    ),
    "acceptance_evidence_missing": dict(
        stage="acceptance",
        severity=Severity.ERROR,
        actor="agentdeck",
        summary="Acceptance was requested without the required evidence.",
        cause=(
            "The acceptance request did not include all required evidence "
            "for the Mission."
        ),
        impact="The Mission was not accepted.",
        protection="No Mission was accepted without complete evidence.",
        recovery_actions=(
            "Collect the required acceptance evidence, then re-run acceptance.",
        ),
        retryable=True,
        outcome_known=True,
    ),
    "permission_denied": dict(
        stage="permission",
        severity=Severity.ERROR,
        actor="agentdeck",
        summary="The requested action is not permitted by the current profile.",
        cause="The permission profile does not authorize this action.",
        impact="The action was not performed.",
        protection="Nothing ran outside the approved permission profile.",
        recovery_actions=(
            "Approve the action or widen the permission profile, then retry.",
        ),
        retryable=True,
        outcome_known=True,
    ),
    "storage_recovery_failed": dict(
        stage="storage",
        severity=Severity.ERROR,
        actor="agentdeck",
        summary="The project store could not be opened or reconciled.",
        cause=(
            "The durable project database failed to open or recover to a "
            "consistent state."
        ),
        impact="No new writes were accepted.",
        protection="No partial or corrupt state was treated as authoritative.",
        recovery_actions=(
            "Inspect the project database and restore a consistent backup "
            "before retrying.",
        ),
        retryable=False,
        outcome_known=False,
    ),
    "tmux_observer_degraded": dict(
        stage="observer",
        severity=Severity.WARNING,
        actor="agentdeck",
        summary="The live Agent view is degraded.",
        cause="The tmux Observer session lost its connection to the live pane.",
        impact="The real work stream may not be visible.",
        protection=(
            "The Observer display never controls lifecycle, so Worker "
            "execution is unaffected."
        ),
        recovery_actions=(
            "Reattach or restart the tmux Observer to restore the live view.",
        ),
        retryable=True,
        outcome_known=True,
    ),
}


def diagnostic(
    code: str,
    *,
    occurred_at: str = _DEFAULT_OCCURRED_AT,
    mission_id: str | None = None,
    task_id: str | None = None,
    attempt_id: str | None = None,
    trace_id: str | None = None,
) -> Diagnostic:
    """Build one complete catalog Diagnostic, or refuse an unknown code."""

    try:
        template = _CATALOG[code]
    except KeyError:
        raise ValueError("unknown diagnostic code") from None
    return Diagnostic.create(
        code=code,
        occurred_at=occurred_at,
        mission_id=mission_id,
        task_id=task_id,
        attempt_id=attempt_id,
        trace_id=trace_id,
        **template,
    )
