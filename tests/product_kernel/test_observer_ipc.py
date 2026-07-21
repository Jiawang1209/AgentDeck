from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
import time

import pytest

from agentdeck.adapters.observer_ipc import UnixObserverClient, UnixObserverServer
from agentdeck.adapters.observer_protocol import (
    ObserverProtocolError, decode_frame, encode_frame,
)
from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.observer_broker import ObserverBroker, ObserverBrokerError
from agentdeck.ports.observer import ObserverSubscription, cursor_for_event
from agentdeck.ports.worker import WorkerEvent
from agentdeck.product.observer import ObserverStream
from product_kernel.fakes import FrozenClock


NOW = "2026-07-21T07:00:00+00:00"
SAFE_HANDSHAKE = {
    "version": "observer-ipc/v1", "type": "handshake",
    "project_id": "prj_1", "session_id": "ses_1", "agent_id": "agt_1",
    "mode": "read_only",
}


@pytest.mark.parametrize(
    "hostile",
    (
        {**SAFE_HANDSHAKE, "unknown": "secret-token-value"},
        {**SAFE_HANDSHAKE, "version": "observer-ipc/v9"},
        {**SAFE_HANDSHAKE, "raw_frame": "secret-token-value"},
        b"\xff\n",
        b"{" + b"x" * (256 * 1024),
    ),
)
def test_protocol_rejects_non_closed_frames_without_echo(hostile: object) -> None:
    wire = hostile if type(hostile) is bytes else (
        json.dumps(hostile, separators=(",", ":")).encode() + b"\n"
    )
    with pytest.raises(ObserverProtocolError) as raised:
        decode_frame(wire)
    assert str(raised.value) in ObserverProtocolError.ALLOWED
    assert "secret-token-value" not in str(raised.value)


def test_protocol_round_trip_is_one_canonical_json_line() -> None:
    wire = encode_frame(SAFE_HANDSHAKE)
    assert wire.endswith(b"\n") and wire.count(b"\n") == 1
    assert decode_frame(wire) == SAFE_HANDSHAKE


def test_protocol_rejects_foreign_subscription_without_echo() -> None:
    expected = ObserverSubscription("prj_2", "ses_1", "agt_1")
    with pytest.raises(ObserverProtocolError, match="identity_mismatch"):
        decode_frame(encode_frame(SAFE_HANDSHAKE), expected=expected)


def _wait(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("bounded Observer IPC wait expired")


def test_real_socket_ack_occurs_after_sink_emit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime

    store = SQLiteStore.open(tmp_path, clock=FrozenClock(datetime.fromisoformat(NOW)))
    timeline: list[str] = []
    holder: dict[str, ObserverBroker] = {}
    monkeypatch.chdir(tmp_path)

    def acknowledge(value) -> None:
        timeline.append("client_ack")
        holder["broker"].acknowledge(value)
        timeline.append("cursor_write")

    server = UnixObserverServer(
        project_root=tmp_path, project_id=store._project_id,
        acknowledge=acknowledge,
        cursor_reader=lambda binding: holder["broker"].current_cursor(binding.attempt_id),
    )
    broker = ObserverBroker(
        project_id=store._project_id, store=store,
        clock=FrozenClock(datetime.fromisoformat(NOW)), channel=server,
    )
    holder["broker"] = broker
    server.start()
    client = UnixObserverClient.connect(
        project_root=tmp_path,
        subscription=ObserverSubscription(store._project_id, "ses_1", "agt_1"),
    )
    try:
        _wait(lambda: server.subscriber_count == 1)
        event = WorkerEvent(
            "evt_1", "ses_1", "agt_1", "tsk_1", "att_1", "acp", 1,
            "progress", NOW, {"detail": "safe"},
        )
        assert broker.publish(event) is None
        binding, prior = client.receive_binding()
        received = client.receive_event()
        timeline.append("server_event")

        class Proxy:
            def load(self):
                return prior
            def acknowledge(self, cursor) -> None:
                client.acknowledge(cursor)

        class Sink:
            def emit(self, _record: str) -> None:
                timeline.append("sink_emit")

        ObserverStream(subscription=binding, cursor_store=Proxy(), sink=Sink()).render(
            (received,)
        )
        def acknowledged() -> bool:
            server.drain_acknowledgements()
            return broker.current_cursor("att_1") is not None
        _wait(acknowledged)
        assert timeline == ["server_event", "sink_emit", "client_ack", "cursor_write"]
        endpoint = server.endpoint
        assert endpoint.is_socket() and endpoint.stat().st_mode & 0o777 == 0o600
        assert endpoint.parent.stat().st_mode & 0o777 == 0o700
        assert endpoint.stat().st_uid == os.getuid()
    finally:
        client.close()
        server.close()
        store.close()


def test_future_socket_ack_never_advances_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime

    store = SQLiteStore.open(tmp_path, clock=FrozenClock(datetime.fromisoformat(NOW)))
    holder = {}
    monkeypatch.chdir(tmp_path)
    server = UnixObserverServer(
        project_root=tmp_path, project_id=store._project_id,
        acknowledge=lambda value: holder["broker"].acknowledge(value),
        cursor_reader=lambda binding: holder["broker"].current_cursor(binding.attempt_id),
    )
    broker = ObserverBroker(
        project_id=store._project_id, store=store,
        clock=FrozenClock(datetime.fromisoformat(NOW)), channel=server,
    )
    holder["broker"] = broker
    server.start()
    client = UnixObserverClient.connect(
        project_root=tmp_path,
        subscription=ObserverSubscription(store._project_id, "ses_1", "agt_1"),
    )
    try:
        _wait(lambda: server.subscriber_count == 1)
        value = WorkerEvent(
            "evt_1", "ses_1", "agt_1", "tsk_1", "att_1", "acp", 1,
            "progress", NOW, {"detail": "safe"},
        )
        broker.publish(value)
        client.receive_binding()
        client.receive_event()
        future = replace(cursor_for_event(store._project_id, value), sequence=2)
        client.acknowledge(future)
        _wait(lambda: server.pending_acknowledgement_count == 1)
        with pytest.raises(ObserverBrokerError, match="observer_ack_conflict"):
            server.drain_acknowledgements()
        assert broker.current_cursor("att_1") is None
    finally:
        client.close()
        server.close()
        store.close()


def test_reconnect_replays_exact_cursor_before_next_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime

    clock = FrozenClock(datetime.fromisoformat(NOW))
    store = SQLiteStore.open(tmp_path, clock=clock)
    holder = {}
    monkeypatch.chdir(tmp_path)
    server = UnixObserverServer(
        project_root=tmp_path, project_id=store._project_id,
        acknowledge=lambda value: holder["broker"].acknowledge(value),
        cursor_reader=lambda binding: holder["broker"].current_cursor(binding.attempt_id),
    )
    broker = ObserverBroker(
        project_id=store._project_id, store=store, clock=clock, channel=server,
    )
    holder["broker"] = broker
    subscription = ObserverSubscription(store._project_id, "ses_1", "agt_1")
    server.start()
    first = UnixObserverClient.connect(project_root=tmp_path, subscription=subscription)
    emitted: list[str] = []

    def consume(client, count: int) -> None:
        binding, prior = client.receive_binding()

        class Proxy:
            def load(self):
                return prior
            def acknowledge(self, cursor):
                client.acknowledge(cursor)

        stream = ObserverStream(
            subscription=binding, cursor_store=Proxy(),
            sink=type("Sink", (), {"emit": lambda self, record: emitted.append(record)})(),
        )
        for _ in range(count):
            stream.render((client.receive_event(),))

    try:
        _wait(lambda: server.subscriber_count == 1)
        broker.publish(WorkerEvent(
            "evt_1", "ses_1", "agt_1", "tsk_1", "att_1", "acp", 1,
            "progress", NOW, {"detail": "one"},
        ))
        consume(first, 1)
        _wait(lambda: server.pending_acknowledgement_count == 1)
        server.drain_acknowledgements()
        first.close()
        _wait(lambda: server.subscriber_count == 0)

        second = UnixObserverClient.connect(
            project_root=tmp_path, subscription=subscription,
        )
        try:
            _wait(lambda: server.subscriber_count == 1)
            broker.publish(WorkerEvent(
                "evt_2", "ses_1", "agt_1", "tsk_1", "att_1", "acp", 2,
                "completed", NOW, {"detail": "two"},
            ))
            consume(second, 2)
            _wait(lambda: server.pending_acknowledgement_count == 1)
            server.drain_acknowledgements()
            assert broker.current_cursor("att_1").sequence == 2
            assert len(emitted) == 2
        finally:
            second.close()
    finally:
        first.close()
        server.close()
        store.close()
