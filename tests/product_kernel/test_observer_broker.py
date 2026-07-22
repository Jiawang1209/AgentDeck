import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.approval_service import ApprovalContext, ApprovalService
from agentdeck.application.observer_broker import ObserverBroker, ObserverBrokerError
from agentdeck.ports.observer import ObserverAcknowledgement
from agentdeck.kernel.permissions import PermissionProfile, PermissionScope
from agentdeck.ports.worker import WorkerEvent, WorkerHandle, WorkerResult
from product_kernel.fakes import FrozenClock


NOW = datetime(2026, 7, 21, 6, 0, tzinfo=timezone.utc)


class Channel:
    def __init__(self, delivered: bool = True) -> None:
        self.items = []
        self._delivered = delivered

    def publish(self, publication) -> bool:
        self.items.append(publication)
        return self._delivered


def event(sequence: int = 1) -> WorkerEvent:
    return WorkerEvent(
        f"evt_{sequence}", "ses_1", "agt_1", "tsk_1", "att_1", "acp",
        sequence, "progress", NOW.isoformat(), {"detail": "safe"},
    )


def _broker(root: Path, delivered: bool = True):
    store = SQLiteStore.open(root, clock=FrozenClock(NOW))
    channel = Channel(delivered)
    broker = ObserverBroker(
        project_id=store._project_id, store=store, clock=FrozenClock(NOW),
        channel=channel,
    )
    return store, channel, broker


def test_cursor_is_written_only_after_exact_acknowledgement(tmp_path: Path) -> None:
    store, channel, broker = _broker(tmp_path)
    try:
        broker.publish(event())
        publication = channel.items[-1]
        assert broker.current_cursor("att_1") is None

        broker.acknowledge(ObserverAcknowledgement(publication.cursor))

        assert broker.current_cursor("att_1") == publication.cursor
    finally:
        store.close()


def test_future_ack_is_contained_without_write_or_advance(tmp_path: Path) -> None:
    store, channel, broker = _broker(tmp_path)
    try:
        broker.publish(event())
        cursor = replace(channel.items[-1].cursor, sequence=2, event_id="evt_2")

        broker.acknowledge(ObserverAcknowledgement(cursor))

        assert broker.current_cursor("att_1") is None
        assert store.count("commands") == 0
        assert broker.degradation_count == 1
    finally:
        store.close()


def test_malformed_ack_is_rejected_without_write(tmp_path: Path) -> None:
    store, channel, broker = _broker(tmp_path)
    try:
        broker.publish(event())

        with pytest.raises(ObserverBrokerError, match="observer_ack_conflict"):
            broker.acknowledge(object())

        assert broker.current_cursor("att_1") is None
        assert store.count("commands") == 0
    finally:
        store.close()


def test_ack_persists_even_when_delivery_was_degraded(tmp_path: Path) -> None:
    store, channel, broker = _broker(tmp_path, delivered=False)
    try:
        broker.publish(event())
        publication = channel.items[-1]
        assert broker.degradation_count == 1

        broker.acknowledge(ObserverAcknowledgement(publication.cursor))

        assert broker.current_cursor("att_1") == publication.cursor
    finally:
        store.close()


def test_duplicate_exact_ack_is_idempotent(tmp_path: Path) -> None:
    store, channel, broker = _broker(tmp_path)
    try:
        broker.publish(event())
        publication = channel.items[-1]

        broker.acknowledge(ObserverAcknowledgement(publication.cursor))
        broker.acknowledge(ObserverAcknowledgement(publication.cursor))

        assert broker.current_cursor("att_1") == publication.cursor
        assert store.count("commands") == 1
    finally:
        store.close()


def test_approval_publishes_each_event_before_interpreting_terminal(
    tmp_path: Path,
) -> None:
    class Publisher:
        def __init__(self) -> None:
            self.events = []
        def publish(self, value) -> None:
            self.events.append(value)

    class Worker:
        def stream_events(self, _handle):
            async def stream():
                yield event(1)
                yield replace(event(2), kind="completed")
            return stream()
        async def collect_result(self, _handle):
            return WorkerResult(
                "ses_1", "agt_1", "tsk_1", "att_1", "completed", {},
            )

    async def scenario() -> None:
        publisher = Publisher()
        store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
        service = ApprovalService(
            store=store, clock=FrozenClock(NOW), event_publisher=publisher,
        )
        try:
            handle = WorkerHandle("ses_1", "agt_1", "tsk_1", "att_1")
            result = await service.bridge_attempt(
                Worker(), handle, ApprovalContext(
                    "msn_1", 1,
                    PermissionScope.for_profile(PermissionProfile.APPROVE_FOR_ME),
                    "a" * 64,
                ),
            )
            assert result.worker_result.status == "completed"
            assert [item.event_id for item in publisher.events] == ["evt_1", "evt_2"]
        finally:
            store.close()

    asyncio.run(scenario())
