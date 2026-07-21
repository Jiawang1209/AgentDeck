"""Closed Store records for acknowledged human Observer delivery."""

from __future__ import annotations

from hashlib import sha256
import json

from agentdeck.kernel.events import DomainEvent
from agentdeck.ports.clock import Clock
from agentdeck.ports.observer import ObserverBinding, ObserverCursor
from agentdeck.ports.store import Store


_CURSOR_FIELDS = {
    "cursor_id", "project_id", "session_id", "agent_id", "task_id",
    "attempt_id", "transport", "sequence", "event_id", "fingerprint",
}


def cursor_id(binding: ObserverBinding) -> str:
    if type(binding) is not ObserverBinding:
        raise TypeError("cursor identity requires an ObserverBinding")
    canonical = json.dumps(
        binding.__dict__, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8", "strict")
    return f"cur_{sha256(canonical).hexdigest()[:32]}"


def cursor_snapshot(cursor: ObserverCursor) -> dict[str, object]:
    if type(cursor) is not ObserverCursor:
        raise TypeError("cursor snapshot requires an ObserverCursor")
    return {"cursor_id": cursor_id(cursor.binding), **cursor.__dict__}


def cursor_from_snapshot(value: object) -> ObserverCursor:
    if type(value) is not dict or set(value) != _CURSOR_FIELDS:
        raise ValueError("stored Observer cursor fields are invalid")
    cursor = ObserverCursor(
        value["project_id"], value["session_id"], value["agent_id"],
        value["task_id"], value["attempt_id"], value["transport"],
        value["sequence"], value["event_id"], value["fingerprint"],
    )
    if value["cursor_id"] != cursor_id(cursor.binding):
        raise ValueError("stored Observer cursor identity drifted")
    return cursor


def acknowledgement_command_id(cursor: ObserverCursor) -> str:
    digest = sha256(json.dumps(
        cursor.__dict__, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8", "strict")).hexdigest()
    return f"observer:ack:{digest}"


class ObserverCursorWriter:
    """The sole Application writer for an emitted-and-acknowledged cursor."""

    def __init__(self, *, store: Store, clock: Clock) -> None:
        if not callable(getattr(store, "execute_once", None)):
            raise TypeError("cursor writer requires a Store")
        if not callable(getattr(clock, "now", None)):
            raise TypeError("cursor writer requires a Clock")
        self._store, self._clock = store, clock

    def load(self, binding: ObserverBinding) -> ObserverCursor | None:
        snapshot = self._store.load_aggregate(
            "observer_cursors", cursor_id(binding),
        )
        return None if snapshot is None else cursor_from_snapshot(snapshot)

    def acknowledge(self, cursor: ObserverCursor) -> ObserverCursor:
        snapshot = cursor_snapshot(cursor)
        identity = snapshot["cursor_id"]
        result = {"accepted": True, "cursor": snapshot}
        command_id = acknowledgement_command_id(cursor)

        def commit(transaction):
            previous = transaction.load_aggregate(
                "observer_cursors", identity,
            )
            if previous is None:
                if cursor.sequence != 1:
                    raise ValueError("observer_cursor_gap")
            else:
                prior = cursor_from_snapshot(previous)
                if prior.binding != cursor.binding:
                    raise ValueError("observer_cursor_identity_drift")
                if cursor.sequence != prior.sequence + 1:
                    raise ValueError("observer_cursor_sequence_conflict")
            transaction.save_aggregate(
                "observer_cursors", identity, snapshot,
            )
            transaction.append_event(DomainEvent.create(
                kind="observer_cursor_acknowledged",
                aggregate_type="observer_cursor", aggregate_id=identity,
                occurred_at=self._clock.now().isoformat(), payload={
                    "project_id": cursor.project_id,
                    "session_id": cursor.session_id,
                    "agent_id": cursor.agent_id,
                    "task_id": cursor.task_id,
                    "attempt_id": cursor.attempt_id,
                    "transport": cursor.transport,
                    "sequence": cursor.sequence,
                    "event_id": cursor.event_id,
                    "fingerprint": cursor.fingerprint,
                },
            ))
            return result

        durable = self._store.execute_once(
            command_id, "observer_cursor_acknowledged", commit,
        )
        if durable != result:
            raise ValueError("observer acknowledgement replay drifted")
        return cursor


__all__ = [
    "ObserverCursorWriter", "acknowledgement_command_id", "cursor_from_snapshot",
    "cursor_id", "cursor_snapshot",
]
