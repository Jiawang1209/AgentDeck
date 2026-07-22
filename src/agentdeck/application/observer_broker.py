"""Application Broker for decoded events and exact Observer acknowledgements."""

from __future__ import annotations

from agentdeck.application.observer_records import ObserverCursorWriter, cursor_id
from agentdeck.ports.clock import Clock
from agentdeck.ports.observer import (
    ObserverAcknowledgement, ObserverBinding, ObserverCursor,
    ObserverPublication, cursor_for_event,
)
from agentdeck.ports.store import Store
from agentdeck.ports.worker import WorkerEvent


class ObserverBrokerError(RuntimeError):
    ALLOWED = frozenset({
        "observer_ack_conflict", "observer_ack_unavailable",
        "observer_cursor_write_failed",
    })

    def __init__(self, code: str) -> None:
        if code not in self.ALLOWED:
            raise ValueError("unknown Observer Broker error")
        self.code = code
        super().__init__(code)


class ObserverBroker:
    """Publish observation-only facts; only exact acknowledgements become durable."""

    def __init__(self, *, project_id: str, store: Store, clock: Clock, channel) -> None:
        self._writer = ObserverCursorWriter(store=store, clock=clock)
        self._project_id = project_id
        if not callable(getattr(channel, "publish", None)):
            raise TypeError("Observer Broker requires a channel")
        self._channel = channel
        self._bindings: dict[str, ObserverBinding] = {}
        self.degradation_count = 0

    def publish(self, event: WorkerEvent) -> None:
        cursor = cursor_for_event(self._project_id, event)
        publication = ObserverPublication(cursor.binding, event, cursor)
        self._bindings[event.attempt_id] = publication.binding
        try:
            delivered = self._channel.publish(publication)
        except Exception:
            delivered = False
        if delivered is not True:
            self.degradation_count += 1

    def acknowledge(self, acknowledgement: ObserverAcknowledgement) -> None:
        if type(acknowledgement) is not ObserverAcknowledgement:
            raise ObserverBrokerError("observer_ack_conflict")
        cursor = acknowledgement.cursor
        try:
            self._writer.acknowledge(cursor)
        except ValueError:
            # A stale, future, gap, or drifted cursor is never authority. The
            # durable writer is the sole arbiter and has rejected it; contain it
            # as observation degradation instead of crashing the ack pump.
            self.degradation_count += 1
        except Exception:
            raise ObserverBrokerError("observer_cursor_write_failed") from None

    def current_cursor(self, attempt_id: str) -> ObserverCursor | None:
        binding = self._bindings.get(attempt_id)
        if binding is None:
            return None
        try:
            return self._writer.load(binding)
        except Exception:
            raise ObserverBrokerError("observer_ack_unavailable") from None

    @staticmethod
    def cursor_id(event: WorkerEvent, project_id: str) -> str:
        return cursor_id(cursor_for_event(project_id, event).binding)


__all__ = ["ObserverBroker", "ObserverBrokerError"]
