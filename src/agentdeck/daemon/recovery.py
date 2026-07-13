"""Pure crash-recovery classification and bounded startup reconciliation.

Recovery classifies already-persisted facts.  It never contacts a Worker,
dispatches an attempt, or queries a runtime transport.  The daemon service loop
is composed in a later milestone task.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
import re
from typing import Literal, Protocol

from ..conversation.lifecycle import validate_conversation_history
from ..mission import MISSION_STATUSES, is_canonical_mission_id


class RecoveryError(ValueError):
    """Persisted Mission evidence cannot be reconciled safely."""


Classification = Literal[
    "resumable", "waiting_human", "ambiguous", "blocked", "terminal"
]
NextTransition = Literal[
    "prepare_dispatch",
    "dispatch_prepared",
    "await_worker",
    "validate_reply",
    "record_handoff",
    "activate_next",
]
MissionState = Literal[
    "pending_confirmation",
    "preparing",
    "running",
    "completed",
    "stopped",
    "interrupted",
]
AttemptState = Literal[
    "none",
    "prepared",
    "admitting",
    "submitted",
    "running",
    "completed",
    "succeeded",
    "failed",
    "cancelled",
    "interrupted",
    "ambiguous",
]
ReceiptState = Literal["none", "recorded"]
ReplyState = Literal["none", "received", "validated", "invalid"]
HandoffState = Literal["none", "pending", "recorded"]
PermissionState = Literal["none", "pending", "approved", "denied", "expired"]
TransportState = Literal["ready", "missing", "invalid"]
SnapshotState = Literal["valid", "missing", "drift"]
LineageState = Literal["valid", "missing", "conflict"]
OwnershipState = Literal["agentdeck_owned", "human_owned", "conflict"]
ConfiguredTransport = Literal["acp", "tmux", "none"]

_ATTEMPT_ID = re.compile(r"mat_[0-9a-f]{12}")
_FACT_FIELDS = (
    "mission_id",
    "mission_state",
    "attempt_id",
    "attempt_state",
    "receipt_state",
    "reply_state",
    "handoff_state",
    "permission_state",
    "configured_transport",
    "transport_state",
    "snapshot_state",
    "lineage_state",
    "ownership_state",
)
_STATE_DOMAINS = {
    "mission_state": frozenset(MISSION_STATUSES),
    "attempt_state": frozenset(
        {
            "none",
            "prepared",
            "admitting",
            "submitted",
            "running",
            "completed",
            "succeeded",
            "failed",
            "cancelled",
            "interrupted",
            "ambiguous",
        }
    ),
    "receipt_state": frozenset({"none", "recorded"}),
    "reply_state": frozenset({"none", "received", "validated", "invalid"}),
    "handoff_state": frozenset({"none", "pending", "recorded"}),
    "permission_state": frozenset({"none", "pending", "approved", "denied", "expired"}),
    "configured_transport": frozenset({"acp", "tmux", "none"}),
    "transport_state": frozenset({"ready", "missing", "invalid"}),
    "snapshot_state": frozenset({"valid", "missing", "drift"}),
    "lineage_state": frozenset({"valid", "missing", "conflict"}),
    "ownership_state": frozenset({"agentdeck_owned", "human_owned", "conflict"}),
}
_TERMINAL_MISSIONS = frozenset({"completed", "stopped", "interrupted"})
_SUCCESSFUL_ATTEMPTS = frozenset({"completed", "succeeded"})
_FAILED_ATTEMPTS = frozenset({"failed", "cancelled", "interrupted"})
_ALLOWED_RECOVERY_TRANSITIONS = frozenset(
    {
        "prepare_dispatch",
        "dispatch_prepared",
        "await_worker",
        "validate_reply",
        "record_handoff",
        "activate_next",
    }
)
_RECOVERY_RECORD_FIELDS = frozenset(
    {
        "classification",
        "reason",
        "mission_id",
        "attempt_id",
        "next_transition",
        "classified_at",
    }
)
_DURABLE_EVIDENCE_FIELDS = frozenset({"mission_id", "attempt_id", "agent_id"})
_REPLY_ID = re.compile(r"mrp_[0-9a-f]{12}")
_HANDOFF_ID = re.compile(r"hof_[0-9a-f]{12}")
_PERMISSION_ID = re.compile(r"prm_[0-9a-f]{12}")


@dataclass(frozen=True)
class RecoveryFacts:
    mission_id: str
    mission_state: MissionState
    attempt_id: str | None
    attempt_state: AttemptState
    receipt_state: ReceiptState
    reply_state: ReplyState
    handoff_state: HandoffState
    permission_state: PermissionState
    configured_transport: ConfiguredTransport
    transport_state: TransportState
    snapshot_state: SnapshotState
    lineage_state: LineageState
    ownership_state: OwnershipState

    def __post_init__(self) -> None:
        if not is_canonical_mission_id(self.mission_id):
            raise RecoveryError("invalid recovery facts: mission identity")
        for field, allowed in _STATE_DOMAINS.items():
            value = getattr(self, field)
            if type(value) is not str or value not in allowed:
                raise RecoveryError("invalid recovery facts: unknown state")
        if self.attempt_id is not None and (
            type(self.attempt_id) is not str
            or _ATTEMPT_ID.fullmatch(self.attempt_id) is None
        ):
            raise RecoveryError("invalid recovery facts: attempt identity")
        if (self.attempt_state == "none") != (self.attempt_id is None):
            raise RecoveryError("invalid recovery facts: attempt lineage")
        if self.attempt_state == "none" and any(
            value != "none"
            for value in (
                self.receipt_state,
                self.reply_state,
                self.handoff_state,
            )
        ):
            raise RecoveryError("invalid recovery facts: attempt lineage")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "RecoveryFacts":
        if type(value) is not dict or set(value) != set(_FACT_FIELDS):
            raise RecoveryError("invalid recovery facts: exact fields required")
        return cls(**dict(value))  # type: ignore[arg-type]

    def summary(self) -> dict[str, object]:
        return {field: getattr(self, field) for field in _FACT_FIELDS}


@dataclass(frozen=True)
class RecoveryDecision:
    classification: Classification
    reason: str
    mission_id: str
    attempt_id: str | None
    next_transition: NextTransition | None

    def __post_init__(self) -> None:
        if self.classification not in {
            "resumable",
            "waiting_human",
            "ambiguous",
            "blocked",
            "terminal",
        }:
            raise RecoveryError("invalid recovery decision classification")
        if type(self.reason) is not str or not self.reason:
            raise RecoveryError("invalid recovery decision reason")
        if not is_canonical_mission_id(self.mission_id):
            raise RecoveryError("invalid recovery decision Mission identity")
        if self.attempt_id is not None and (
            type(self.attempt_id) is not str
            or _ATTEMPT_ID.fullmatch(self.attempt_id) is None
        ):
            raise RecoveryError("invalid recovery decision attempt identity")
        if (
            self.classification == "resumable"
            and self.next_transition not in _ALLOWED_RECOVERY_TRANSITIONS
        ):
            raise RecoveryError(
                "resumable recovery decision requires an allowed transition"
            )
        if self.classification != "resumable" and self.next_transition is not None:
            raise RecoveryError("non-resumable recovery decision has a transition")


def _decision(
    facts: RecoveryFacts,
    classification: Classification,
    reason: str,
    next_transition: NextTransition | None = None,
) -> RecoveryDecision:
    return RecoveryDecision(
        classification=classification,
        reason=reason,
        mission_id=facts.mission_id,
        attempt_id=facts.attempt_id,
        next_transition=next_transition,
    )


def reconcile_gate(facts: RecoveryFacts) -> RecoveryDecision:
    """Classify one complete immutable persisted-evidence projection."""
    if not isinstance(facts, RecoveryFacts):
        raise RecoveryError("recovery facts must be RecoveryFacts")
    if facts.snapshot_state == "drift":
        return _decision(facts, "blocked", "frozen Mission snapshot drift")
    if facts.snapshot_state == "missing":
        return _decision(facts, "blocked", "frozen Mission snapshot is missing")
    if facts.lineage_state == "missing":
        return _decision(facts, "blocked", "Mission recovery lineage is incomplete")
    if facts.lineage_state == "conflict":
        return _decision(facts, "blocked", "Mission recovery lineage conflicts")
    if facts.ownership_state == "human_owned":
        return _decision(facts, "blocked", "Worker is human-owned")
    if facts.ownership_state == "conflict":
        return _decision(facts, "blocked", "Worker ownership conflicts")
    if facts.handoff_state != "none" and facts.reply_state != "validated":
        return _decision(
            facts, "blocked", "Mission handoff lacks validated reply lineage"
        )
    if facts.reply_state != "none" and facts.receipt_state != "recorded":
        return _decision(facts, "blocked", "Worker reply lacks receipt lineage")

    # An admission claim means the daemon may have crossed the external-effect
    # boundary.  No permission or readiness fact can make that safe to replay.
    if facts.attempt_state == "admitting":
        return _decision(facts, "ambiguous", "Worker admission outcome is unknown")
    if facts.attempt_state == "ambiguous":
        return _decision(facts, "ambiguous", "Worker attempt outcome is ambiguous")
    if (
        facts.configured_transport == "acp"
        and facts.attempt_state in {"submitted", "running"}
    ):
        return _decision(
            facts,
            "ambiguous",
            "ACP Worker connection was lost across daemon restart",
        )
    if (
        facts.attempt_state in {"submitted", "running"}
        and facts.receipt_state == "none"
    ):
        return _decision(facts, "ambiguous", "Worker dispatch outcome lacks a receipt")

    if facts.mission_state in _TERMINAL_MISSIONS:
        if facts.attempt_state in {"prepared", "submitted", "running"}:
            return _decision(
                facts, "blocked", "terminal Mission retains an active Worker attempt"
            )
        if facts.attempt_state in _SUCCESSFUL_ATTEMPTS:
            if facts.receipt_state != "recorded":
                return _decision(
                    facts, "blocked", "terminal Worker result lacks a receipt"
                )
            if facts.reply_state == "none":
                return _decision(
                    facts, "blocked", "terminal Worker result has no reply evidence"
                )
            if facts.reply_state != "validated" or facts.handoff_state != "recorded":
                return _decision(
                    facts, "blocked", "terminal Mission result is not fully validated"
                )
        return _decision(facts, "terminal", f"Mission is {facts.mission_state}")
    if facts.mission_state == "pending_confirmation":
        return _decision(facts, "waiting_human", "Mission confirmation is pending")

    if facts.transport_state == "missing":
        return _decision(facts, "blocked", "Worker transport is missing")
    if facts.transport_state == "invalid":
        return _decision(facts, "blocked", "Worker transport is invalid")
    if facts.permission_state == "pending":
        return _decision(facts, "waiting_human", "Worker permission is pending")
    if facts.permission_state in {"denied", "expired"}:
        return _decision(
            facts, "blocked", f"Worker permission is {facts.permission_state}"
        )

    if facts.attempt_state == "none":
        return _decision(
            facts,
            "resumable",
            "Mission has no active Worker attempt",
            "prepare_dispatch",
        )
    if facts.attempt_state == "prepared":
        if facts.receipt_state != "none" or facts.reply_state != "none":
            return _decision(facts, "blocked", "prepared attempt has external evidence")
        return _decision(
            facts,
            "resumable",
            "prepared Worker attempt has not been dispatched",
            "dispatch_prepared",
        )
    if facts.attempt_state in {"submitted", "running"}:
        if facts.reply_state != "none":
            return _decision(
                facts,
                "blocked",
                "Worker reply precedes a terminal successful attempt",
            )
        return _decision(
            facts,
            "resumable",
            "submitted Worker receipt is waiting for a reply",
            "await_worker",
        )
    if facts.attempt_state in _FAILED_ATTEMPTS:
        return _decision(
            facts,
            "blocked",
            f"Worker attempt ended as {facts.attempt_state}",
        )
    if facts.attempt_state in _SUCCESSFUL_ATTEMPTS:
        if facts.receipt_state != "recorded":
            return _decision(facts, "blocked", "terminal Worker result lacks a receipt")
        if facts.reply_state == "none":
            return _decision(
                facts, "blocked", "terminal Worker result has no reply evidence"
            )
        if facts.reply_state == "invalid":
            return _decision(facts, "blocked", "Worker reply is invalid")
        if facts.reply_state == "received":
            if facts.handoff_state != "none":
                return _decision(facts, "blocked", "handoff precedes reply validation")
            return _decision(
                facts,
                "resumable",
                "terminal Worker reply awaits validation",
                "validate_reply",
            )
        if facts.handoff_state == "none":
            return _decision(
                facts,
                "resumable",
                "validated Worker reply awaits compact handoff",
                "record_handoff",
            )
        if facts.handoff_state == "pending":
            return _decision(facts, "blocked", "Mission handoff state is incomplete")
        return _decision(
            facts,
            "resumable",
            "validated compact handoff can activate the next step",
            "activate_next",
        )
    return _decision(facts, "blocked", "Mission recovery evidence is incomplete")


def validate_recovery_record(value: object) -> dict[str, object]:
    """Validate the exact compact recovery record persisted by StateStore."""
    if type(value) is not dict or set(value) != _RECOVERY_RECORD_FIELDS:
        raise ValueError("recovery_decisions record is invalid")
    decision = RecoveryDecision(
        classification=value["classification"],  # type: ignore[arg-type]
        reason=value["reason"],  # type: ignore[arg-type]
        mission_id=value["mission_id"],  # type: ignore[arg-type]
        attempt_id=value["attempt_id"],  # type: ignore[arg-type]
        next_transition=value["next_transition"],  # type: ignore[arg-type]
    )
    classified_at = value["classified_at"]
    if type(classified_at) is not str:
        raise ValueError("recovery_decisions record is invalid")
    try:
        timestamp = datetime.fromisoformat(classified_at)
    except ValueError:
        raise ValueError("recovery_decisions record is invalid") from None
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("recovery_decisions record is invalid")
    return {
        "classification": decision.classification,
        "reason": decision.reason,
        "mission_id": decision.mission_id,
        "attempt_id": decision.attempt_id,
        "next_transition": decision.next_transition,
        "classified_at": classified_at,
    }


def validate_mission_recovery_evidence_record(value: object) -> dict[str, object]:
    """Validate one controlled Mission/current-attempt recovery binding."""
    if type(value) is not dict or set(value) != _DURABLE_EVIDENCE_FIELDS:
        raise ValueError("durable recovery evidence is invalid")
    mission_id = value.get("mission_id")
    attempt_id = value.get("attempt_id")
    agent_id = value.get("agent_id")
    if not is_canonical_mission_id(mission_id) or (
        attempt_id is not None
        and (type(attempt_id) is not str or _ATTEMPT_ID.fullmatch(attempt_id) is None)
    ):
        raise ValueError("durable recovery evidence is invalid")
    if attempt_id is None:
        if agent_id is not None:
            raise ValueError("durable recovery attempt lineage is invalid")
    elif type(agent_id) is not str or not agent_id:
        raise ValueError("durable recovery attempt lineage is invalid")
    return {
        "mission_id": mission_id,
        "attempt_id": attempt_id,
        "agent_id": agent_id,
    }


def validate_mission_reply_evidence_record(value: object) -> dict[str, object]:
    from ..workflow import validate_canonical_handoff

    fields = {
        "mission_id", "attempt_id", "reply_id", "dispatch_key", "state",
        "canonical_handoff",
    }
    if (
        type(value) is not dict
        or set(value) != fields
        or not is_canonical_mission_id(value.get("mission_id"))
        or type(value.get("attempt_id")) is not str
        or _ATTEMPT_ID.fullmatch(value["attempt_id"]) is None
        or type(value.get("reply_id")) is not str
        or _REPLY_ID.fullmatch(value["reply_id"]) is None
        or type(value.get("dispatch_key")) is not str
        or re.fullmatch(r"dsp_[0-9a-f]{32}", value["dispatch_key"]) is None
        or value.get("state") not in {"received", "validated", "invalid"}
    ):
        raise ValueError("durable recovery reply evidence is invalid")
    try:
        compact = validate_canonical_handoff(
            value["canonical_handoff"], expected_handoff_token=value["dispatch_key"]
        ).compact()
    except (TypeError, ValueError):
        raise ValueError("durable recovery reply evidence is invalid") from None
    return {**value, "canonical_handoff": compact}


def validate_mission_handoff_evidence_record(value: object) -> dict[str, object]:
    from ..workflow import validate_canonical_handoff

    fields = {
        "mission_id", "attempt_id", "handoff_id", "reply_id", "state",
        "canonical_handoff",
    }
    if (
        type(value) is not dict
        or set(value) != fields
        or not is_canonical_mission_id(value.get("mission_id"))
        or type(value.get("attempt_id")) is not str
        or _ATTEMPT_ID.fullmatch(value["attempt_id"]) is None
        or type(value.get("handoff_id")) is not str
        or _HANDOFF_ID.fullmatch(value["handoff_id"]) is None
        or type(value.get("reply_id")) is not str
        or _REPLY_ID.fullmatch(value["reply_id"]) is None
        or value.get("state") not in {"pending", "recorded"}
    ):
        raise ValueError("durable recovery handoff evidence is invalid")
    try:
        compact = validate_canonical_handoff(value["canonical_handoff"]).compact()
    except (TypeError, ValueError):
        raise ValueError("durable recovery handoff evidence is invalid") from None
    return {**value, "canonical_handoff": compact}


def validate_mission_permission_binding(value: object) -> dict[str, object]:
    fields = {"mission_id", "attempt_id", "permission_id"}
    if (
        type(value) is not dict
        or set(value) != fields
        or not is_canonical_mission_id(value.get("mission_id"))
        or type(value.get("attempt_id")) is not str
        or _ATTEMPT_ID.fullmatch(value["attempt_id"]) is None
        or type(value.get("permission_id")) is not str
        or _PERMISSION_ID.fullmatch(value["permission_id"]) is None
    ):
        raise ValueError("durable recovery permission binding is invalid")
    return dict(value)


def recovery_facts_from_persisted_state(
    state: Mapping[str, object], mission_id: str
) -> RecoveryFacts:
    """Build pure recovery facts only from an already validated state snapshot."""
    if type(state) is not dict or not is_canonical_mission_id(mission_id):
        raise RecoveryError("persisted recovery state is invalid")
    missions = state.get("missions")
    attempts = state.get("mission_attempts")
    evidence_items = state.get("mission_recovery_evidence")
    if (
        type(missions) is not list
        or type(attempts) is not list
        or type(evidence_items) is not list
    ):
        raise RecoveryError("persisted recovery state is invalid")
    mission_matches = [
        item for item in missions if type(item) is dict and item.get("mission_id") == mission_id
    ]
    if len(mission_matches) != 1:
        raise RecoveryError("persisted Mission evidence is invalid")
    mission = mission_matches[0]
    if mission.get("status") not in MISSION_STATUSES:
        raise RecoveryError("persisted Mission evidence is invalid")

    mission_attempts = [
        item for item in attempts if type(item) is dict and item.get("mission_id") == mission_id
    ]
    active = [
        item
        for item in mission_attempts
        if item.get("state") in {"prepared", "admitting", "submitted", "running"}
    ]
    if len(active) > 1:
        raise RecoveryError("multiple active Mission attempts")
    current = max(
        mission_attempts,
        key=lambda item: (str(item.get("created_at")), str(item.get("attempt_id"))),
        default=None,
    )
    if active and current is not active[0]:
        raise RecoveryError("active Mission attempt is not current")
    current_attempt_id = current.get("attempt_id") if current is not None else None
    bindings = [
        validate_mission_recovery_evidence_record(item)
        for item in evidence_items
        if type(item) is dict and item.get("mission_id") == mission_id
    ]
    if len(bindings) != 1 or bindings[0]["attempt_id"] != current_attempt_id:
        raise RecoveryError("durable recovery evidence mismatch")
    binding = bindings[0]
    current_agent_id = current.get("agent_id") if current is not None else None
    if binding["agent_id"] != current_agent_id:
        raise RecoveryError("durable recovery attempt binding mismatch")

    try:
        from ..state import canonical_snapshot_hash, validate_execution_snapshot

        snapshot = validate_execution_snapshot(mission.get("execution_snapshot"))
    except (TypeError, ValueError):
        snapshot = None
    mission_snapshot_hash = mission.get("snapshot_hash")
    snapshot_state = (
        "missing"
        if snapshot is None or type(mission_snapshot_hash) is not str
        else "drift"
        if snapshot["execution_hash"] != mission_snapshot_hash
        else "valid"
    )

    target_agent_id: str | None = None
    configured_transport = "none"
    transport_state = "missing"
    lineage_state = "valid"
    if snapshot is not None:
        steps = snapshot["mission"]["steps"]
        if current is None:
            if steps:
                target_agent_id = steps[0]["agent_id"]
        else:
            matches = [
                item
                for item in steps
                if item["step_id"] == current.get("step_id")
            ]
            if len(matches) != 1 or matches[0]["agent_id"] != current_agent_id:
                lineage_state = "conflict"
            else:
                target_agent_id = current_agent_id  # type: ignore[assignment]
        workers = {
            item["agent_id"]: item for item in snapshot["workers"]
        }
        worker = workers.get(target_agent_id)
        if worker is not None:
            configured_transport = worker["configured_transport"]
            if current is not None and worker["configured_transport"] != current.get(
                "configured_transport"
            ):
                transport_state = "invalid"
            else:
                transport_state = "ready"

    reply_records = [
        validate_mission_reply_evidence_record(item)
        for item in state.get("mission_worker_replies", [])
        if type(item) is dict and item.get("attempt_id") == current_attempt_id
    ]
    if len(reply_records) > 1:
        raise RecoveryError("duplicate durable recovery reply evidence")
    reply = reply_records[0] if reply_records else None
    if reply is not None and (
        current is None
        or reply["mission_id"] != mission_id
        or reply["dispatch_key"] != current.get("dispatch_key")
    ):
        raise RecoveryError("durable recovery reply lineage is invalid")
    handoff_records = [
        validate_mission_handoff_evidence_record(item)
        for item in state.get("mission_handoffs", [])
        if type(item) is dict and item.get("attempt_id") == current_attempt_id
    ]
    if len(handoff_records) > 1:
        raise RecoveryError("duplicate durable recovery handoff evidence")
    handoff = handoff_records[0] if handoff_records else None
    if handoff is not None and (
        reply is None
        or handoff["mission_id"] != mission_id
        or handoff["reply_id"] != reply["reply_id"]
        or reply["state"] != "validated"
        or handoff["canonical_handoff"] != reply["canonical_handoff"]
    ):
        raise RecoveryError("durable recovery handoff reply lineage is invalid")

    permission_bindings = [
        validate_mission_permission_binding(item)
        for item in state.get("mission_permission_bindings", [])
        if type(item) is dict and item.get("attempt_id") == current_attempt_id
    ]
    if len(permission_bindings) > 1:
        raise RecoveryError("duplicate durable recovery permission binding")
    permission_state = "none"
    if permission_bindings:
        permission_binding = permission_bindings[0]
        if current is None or permission_binding["mission_id"] != mission_id:
            raise RecoveryError("durable recovery permission lineage is invalid")
        permissions = [
            item
            for item in state.get("permission_requests", [])
            if type(item) is dict
            and item.get("permission_id") == permission_binding["permission_id"]
        ]
        if len(permissions) != 1:
            raise RecoveryError("durable recovery permission record is missing")
        permission = permissions[0]
        sessions = [
            item
            for item in state.get("agent_sessions", [])
            if type(item) is dict and item.get("session_id") == permission.get("session_id")
        ]
        if len(sessions) != 1 or sessions[0].get("agent_id") != current_agent_id:
            raise RecoveryError("durable recovery permission agent scope mismatch")
        session = sessions[0]
        workers = [
            item for item in snapshot["workers"]
            if item["agent_id"] == current_agent_id
        ] if snapshot is not None else []
        caps = session.get("capabilities")
        workspace = session.get("workspace")
        if (
            current is None
            or current.get("configured_transport") != "acp"
            or len(workers) != 1
            or workers[0]["configured_transport"] != "acp"
            or session.get("provider") != workers[0]["provider"]
            or session.get("transport") != "acp-adapter"
            or type(workspace) is not str
            or canonical_snapshot_hash({"project_root": workspace})
            != snapshot["mission"]["project_scope_hash"]
            or type(session.get("native_session_id")) is not str
            or type(caps) is not dict
            or any(
                caps.get(name) is not True
                for name in (
                    "structured_sessions", "streaming_updates",
                    "structured_tools", "permission_requests",
                )
            )
            or caps.get("observable_terminal") is not False
        ):
            raise RecoveryError("durable recovery permission agent scope mismatch")
        turns = [
            item for item in state.get("protocol_turns", [])
            if type(item) is dict and item.get("turn_id") == permission.get("turn_id")
        ]
        if (
            len(turns) != 1
            or turns[0].get("session_id") != session.get("session_id")
            or turns[0].get("kind") != "prompt"
            or turns[0].get("message_id") != current.get("dispatch_key")
        ):
            raise RecoveryError("durable recovery permission turn scope mismatch")
        permission_state = permission.get("status")
        for transition in state.get("protocol_state_transitions", []):
            if (
                type(transition) is dict
                and transition.get("entity_type") == "permission"
                and transition.get("entity_id") == permission_binding["permission_id"]
            ):
                if transition.get("from_state") != permission_state:
                    raise RecoveryError(
                        "durable recovery permission history is invalid"
                    )
                permission_state = transition.get("to_state")

    base_records = {
        key: state.get(key, [])
        for key in (
            "conversation_sessions",
            "conversation_turns",
            "conversation_preview_bindings",
        )
    }
    try:
        conversation = validate_conversation_history(
            base_records, state.get("conversation_state_transitions", [])
        )
    except (TypeError, ValueError):
        raise RecoveryError("durable recovery ownership history is invalid") from None
    ownership_raw = conversation["ownership_states"].get(
        target_agent_id, "agentdeck_owned"
    )
    ownership_state = (
        ownership_raw
        if ownership_raw in {"agentdeck_owned", "human_owned"}
        else "conflict"
    )

    if current is not None:
        receipt_state = "recorded" if current.get("receipt_summary") is not None else "none"
        attempt_state = current.get("state")
    else:
        receipt_state = "none"
        attempt_state = "none"
    if current is not None and current.get("snapshot_hash") != mission_snapshot_hash:
        snapshot_state = "drift"
    return RecoveryFacts(
        mission_id=mission_id,
        mission_state=mission["status"],  # type: ignore[arg-type]
        attempt_id=current_attempt_id,  # type: ignore[arg-type]
        attempt_state=attempt_state,  # type: ignore[arg-type]
        receipt_state=receipt_state,  # type: ignore[arg-type]
        reply_state="none" if reply is None else reply["state"],  # type: ignore[arg-type]
        handoff_state="none" if handoff is None else handoff["state"],  # type: ignore[arg-type]
        permission_state=permission_state,  # type: ignore[arg-type]
        configured_transport=configured_transport,  # type: ignore[arg-type]
        transport_state=transport_state,  # type: ignore[arg-type]
        snapshot_state=snapshot_state,  # type: ignore[arg-type]
        lineage_state=lineage_state,  # type: ignore[arg-type]
        ownership_state=ownership_state,  # type: ignore[arg-type]
    )


class RecoveryStore(Protocol):
    def flush_daemon_event_outbox(self) -> object: ...
    def flush_conversation_event_outbox(self) -> object: ...
    def flush_protocol_event_outbox(self) -> object: ...
    def load_recovery_snapshot(self) -> tuple[dict[str, object], str]: ...
    def commit_recovery_decisions(
        self,
        decisions: Iterable[RecoveryDecision],
        *,
        expected_recovery_token: str,
    ) -> list[dict[str, object]]: ...


def reconcile_startup(
    store: RecoveryStore,
    *,
    enable_scheduler: Callable[[], object],
) -> list[dict[str, object]]:
    """Flush old outboxes, classify every active Mission, persist, then enable.

    This is a bounded startup gate, not the Task 11 daemon service loop.
    """
    if not callable(enable_scheduler):
        raise TypeError("enable_scheduler must be callable")
    try:
        store.flush_daemon_event_outbox()
        store.flush_conversation_event_outbox()
        store.flush_protocol_event_outbox()
    except (OSError, RuntimeError, TypeError, ValueError):
        raise RecoveryError("pending outbox flush failed") from None

    try:
        state, recovery_token = store.load_recovery_snapshot()
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        raise RecoveryError("durable recovery evidence is invalid") from None
    for field in (
        "daemon_event_outbox",
        "conversation_event_outbox",
        "protocol_event_outbox",
    ):
        pending = state.get(field, [])
        if type(pending) is not list or pending:
            raise RecoveryError("pending outbox flush failed")
    missions = state.get("missions", [])
    if type(missions) is not list:
        raise RecoveryError("persisted Mission evidence is invalid")
    nonterminal_ids: list[str] = []
    for mission in missions:
        if type(mission) is not dict:
            raise RecoveryError("persisted Mission evidence is invalid")
        mission_id = mission.get("mission_id")
        status = mission.get("status")
        if not is_canonical_mission_id(mission_id) or status not in MISSION_STATUSES:
            raise RecoveryError("persisted Mission evidence is invalid")
        if status not in _TERMINAL_MISSIONS:
            nonterminal_ids.append(mission_id)
    if len(nonterminal_ids) != len(set(nonterminal_ids)):
        raise RecoveryError("persisted Mission evidence is invalid")

    try:
        facts = [
            recovery_facts_from_persisted_state(state, mission_id)
            for mission_id in nonterminal_ids
        ]
        decisions = [reconcile_gate(item) for item in facts]
    except (RecoveryError, TypeError, ValueError):
        raise RecoveryError("durable recovery evidence is invalid") from None
    try:
        persisted = store.commit_recovery_decisions(
            decisions, expected_recovery_token=recovery_token
        )
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        raise RecoveryError("recovery authority drift") from None
    enable_scheduler()
    return persisted
