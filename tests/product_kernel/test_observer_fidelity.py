from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import importlib
import json
from types import SimpleNamespace
from typing import Any

import pytest

from agentdeck.ports.worker import WORKER_EVENT_KINDS, WorkerEvent


TIMESTAMP = datetime(2026, 7, 20, 8, 30, tzinfo=timezone.utc).isoformat()


def observer_api() -> Any:
    return importlib.import_module("agentdeck.product.observer")


def subscription(
    api: Any,
    *,
    session_id: object = "ses_1",
    agent_id: object = "agt_1",
    task_id: object = "tsk_1",
    attempt_id: object = "att_1",
    transport: object = "acp",
) -> object:
    return api.ObserverBinding(
        project_id="prj_1", session_id=session_id, agent_id=agent_id, task_id=task_id,
        attempt_id=attempt_id, transport=transport,
    )


class MemoryCursor:
    def __init__(self, initial: object | None = None, *, fail: str | None = None) -> None:
        self.initial = initial
        self.fail = fail
        self.acknowledged: list[object] = []

    def load(self) -> object | None:
        if self.fail == "load":
            raise RuntimeError("HOSTILE LOAD DETAILS token is sk-load")
        return self.initial

    def acknowledge(self, cursor: object) -> None:
        if self.fail == "acknowledge":
            raise RuntimeError("HOSTILE WRITER DETAILS token is sk-writer")
        self.acknowledged.append(cursor)
        self.initial = cursor


class RecordingSink:
    def __init__(self) -> None:
        self.records: list[str] = []

    def emit(self, record: str) -> None:
        self.records.append(record)


def event(
    sequence: int,
    *,
    kind: str = "progress",
    event_id: str | None = None,
    session_id: str = "ses_1",
    agent_id: str = "agt_1",
    task_id: str = "tsk_1",
    attempt_id: str = "att_1",
    payload: dict[str, object] | None = None,
) -> WorkerEvent:
    return WorkerEvent(
        event_id=event_id or f"evt_{sequence}",
        session_id=session_id,
        agent_id=agent_id,
        task_id=task_id,
        attempt_id=attempt_id,
        transport="acp",
        sequence=sequence,
        kind=kind,
        timestamp=TIMESTAMP,
        payload=payload or {"detail": f"safe event {sequence}"},
    )


def record_json(line: str) -> dict[str, object]:
    return json.loads(line[line.index("{"):])


def test_all_real_worker_event_kinds_render_full_identity_and_payload() -> None:
    api = observer_api()
    kinds = tuple(sorted(WORKER_EVENT_KINDS))
    events = tuple(
        event(index, kind=kind, payload={"detail": kind, "count": index})
        for index, kind in enumerate(kinds, start=1)
    )
    cursor = MemoryCursor()

    output = api.ObserverStream(
        subscription=subscription(api), cursor_store=cursor, sink=RecordingSink(),
    ).render(events)

    assert len(output) == len(kinds)
    for source, line in zip(events, output, strict=True):
        record = record_json(line)
        assert record == {
            "agent_id": source.agent_id,
            "attempt_id": source.attempt_id,
            "event_id": source.event_id,
            "kind": source.kind,
            "payload": {"count": source.sequence, "detail": source.kind},
            "sequence": source.sequence,
            "session_id": source.session_id,
            "task_id": source.task_id,
            "timestamp": source.timestamp,
            "transport": source.transport,
        }
        expected_label = (
            "[Agent agt_1]"
            if source.kind == "message"
            else f"[AgentDeck] observed {source.kind} from [Agent agt_1]"
        )
        assert line.startswith(expected_label)
    assert [item.sequence for item in cursor.acknowledged] == list(range(1, 11))


def test_sensitive_numeric_observability_metrics_remain_faithful() -> None:
    api = observer_api()
    metrics = {
        "token_count": 1234,
        "secret_bytes": 2048,
        "credential_total": 3,
        "authorization_latency_ms": 12.5,
        "private_key_duration_ms": 7,
        "password_count": True,
    }
    source = event(1, kind="progress", payload=metrics)

    output = api.render_event(source)

    assert record_json(output)["payload"] == metrics


def test_first_batch_then_reconnect_deduplicates_exact_cursor_event() -> None:
    api = observer_api()
    cursor = MemoryCursor()
    first_sink = RecordingSink()
    first = api.ObserverStream(
        subscription=subscription(api), cursor_store=cursor, sink=first_sink,
    ).render(
        (event(1), event(2))
    )
    second_sink = RecordingSink()
    second = api.ObserverStream(
        subscription=subscription(api), cursor_store=cursor, sink=second_sink,
    ).render(
        (event(2), event(3))
    )

    assert [record_json(line)["sequence"] for line in first + second] == [1, 2, 3]
    assert first_sink.records == list(first)
    assert second_sink.records == list(second)
    assert [item.sequence for item in cursor.acknowledged] == [1, 2, 3]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("session_id", "ses_2"),
        ("agent_id", "agt_2"),
        ("task_id", "tsk_2"),
        ("attempt_id", "att_2"),
    ),
)
def test_lineage_identity_drift_fails_without_output_or_cursor_advance(
    field: str, value: str,
) -> None:
    api = observer_api()
    cursor = MemoryCursor()
    sink = RecordingSink()
    stream = api.ObserverStream(
        subscription=subscription(api), cursor_store=cursor, sink=sink,
    )
    stream.render((event(1),))
    drifted = event(2, **{field: value})

    with pytest.raises(api.ObserverError) as raised:
        stream.render((drifted,))

    assert raised.value.code == "observer_identity_mismatch"
    assert str(raised.value) == "observer_identity_mismatch: identity mismatch"
    assert [item.sequence for item in cursor.acknowledged] == [1]
    assert [record_json(line)["sequence"] for line in sink.records] == [1]


def test_structurally_hostile_transport_drift_fails_closed() -> None:
    api = observer_api()
    cursor = MemoryCursor()
    stream = api.ObserverStream(
        subscription=subscription(api), cursor_store=cursor, sink=RecordingSink(),
    )
    stream.render((event(1),))
    source = event(2)
    hostile = SimpleNamespace(
        **{
            field: getattr(source, field)
            for field in (
                "event_id", "session_id", "agent_id", "task_id", "attempt_id",
                "sequence", "kind", "timestamp", "payload",
            )
        },
        transport="pty",
    )

    with pytest.raises(api.ObserverError) as raised:
        stream.render((hostile,))

    assert raised.value.code == "observer_identity_mismatch"
    assert [item.sequence for item in cursor.acknowledged] == [1]


@pytest.mark.parametrize(
    ("events", "code"),
    (
        ((event(1), event(3)), "observer_sequence_gap"),
        ((event(1), event(2), event(1)), "observer_sequence_rollback"),
        (
            (event(1), event(1, event_id="evt_conflict", payload={"detail": "other"})),
            "observer_sequence_conflict",
        ),
        ((event(1), event(2, event_id="evt_1")), "observer_cursor_conflict"),
    ),
)
def test_sequence_and_event_cursor_conflicts_fail_closed(
    events: tuple[WorkerEvent, ...], code: str,
) -> None:
    api = observer_api()
    cursor = MemoryCursor()
    sink = RecordingSink()

    with pytest.raises(api.ObserverError) as raised:
        api.ObserverStream(
            subscription=subscription(api), cursor_store=cursor, sink=sink,
        ).render(events)

    assert raised.value.code == code
    accepted = len(cursor.acknowledged)
    assert len(sink.records) == accepted
    assert all("other" not in line for line in sink.records)


def test_first_continuous_lineage_must_start_at_sequence_one() -> None:
    api = observer_api()
    cursor = MemoryCursor()

    with pytest.raises(api.ObserverError) as raised:
        api.ObserverStream(
            subscription=subscription(api), cursor_store=cursor, sink=RecordingSink(),
        ).render((event(2),))

    assert raised.value.code == "observer_sequence_gap"
    assert cursor.acknowledged == []


def test_foreign_and_stale_loaded_cursors_fail_closed() -> None:
    api = observer_api()
    source_store = MemoryCursor()
    expected = subscription(api)
    api.ObserverStream(
        subscription=expected, cursor_store=source_store, sink=RecordingSink(),
    ).render(
        (event(1),)
    )
    persisted = source_store.acknowledged[-1]

    foreign = replace(persisted, agent_id="agt_foreign")
    foreign_store = MemoryCursor(foreign)
    foreign_sink = RecordingSink()
    with pytest.raises(api.ObserverError) as foreign_error:
        api.ObserverStream(
            subscription=expected, cursor_store=foreign_store, sink=foreign_sink,
        )
    assert foreign_error.value.code == "observer_identity_mismatch"
    assert foreign_store.acknowledged == []
    assert foreign_sink.records == []

    stale = replace(
        persisted, sequence=5, event_id="evt_5", fingerprint="0" * 64,
    )
    with pytest.raises(api.ObserverError) as stale_error:
        api.ObserverStream(
            subscription=expected, cursor_store=MemoryCursor(stale),
            sink=RecordingSink(),
        ).render((event(4),))
    assert stale_error.value.code == "observer_sequence_rollback"


def test_loaded_cursor_requires_exact_replay_before_next_sequence() -> None:
    api = observer_api()
    expected = subscription(api)
    cursor = MemoryCursor()
    api.ObserverStream(
        subscription=expected, cursor_store=cursor, sink=RecordingSink(),
    ).render((event(1),))
    sink = RecordingSink()

    with pytest.raises(api.ObserverError) as raised:
        api.ObserverStream(
            subscription=expected, cursor_store=cursor, sink=sink,
        ).render((event(2),))

    assert raised.value.code == "observer_replay_required"
    assert [item.sequence for item in cursor.acknowledged] == [1]
    assert sink.records == []


@pytest.mark.parametrize(
    ("cursor_change", "replay", "code"),
    (
        ({"fingerprint": "0" * 64}, event(1), "observer_cursor_conflict"),
        ({}, event(1, event_id="evt_tampered"), "observer_cursor_conflict"),
        ({}, event(1, agent_id="agt_other"), "observer_identity_mismatch"),
    ),
)
def test_loaded_cursor_replay_rejects_tampered_fingerprint_event_or_identity(
    cursor_change: dict[str, object], replay: WorkerEvent, code: str,
) -> None:
    api = observer_api()
    expected = subscription(api)
    original = MemoryCursor()
    api.ObserverStream(
        subscription=expected, cursor_store=original, sink=RecordingSink(),
    ).render((event(1),))
    persisted = replace(original.acknowledged[-1], **cursor_change)
    reconnect_store = MemoryCursor(persisted)
    sink = RecordingSink()
    reconnect = api.ObserverStream(
        subscription=expected, cursor_store=reconnect_store, sink=sink,
    )

    with pytest.raises(api.ObserverError) as raised:
        reconnect.render((replay,))

    assert raised.value.code == code
    assert reconnect_store.acknowledged == []
    assert sink.records == []


def test_loaded_cursor_fingerprint_cannot_skip_replay_to_next_event() -> None:
    api = observer_api()
    expected = subscription(api)
    original = MemoryCursor()
    api.ObserverStream(
        subscription=expected, cursor_store=original, sink=RecordingSink(),
    ).render((event(1),))
    fabricated = replace(original.acknowledged[-1], fingerprint="0" * 64)
    reconnect_store = MemoryCursor(fabricated)

    with pytest.raises(api.ObserverError) as raised:
        api.ObserverStream(
            subscription=expected, cursor_store=reconnect_store,
            sink=RecordingSink(),
        ).render((event(2),))

    assert raised.value.code == "observer_replay_required"
    assert reconnect_store.acknowledged == []


def test_event_must_match_expected_subscription_even_without_cursor() -> None:
    api = observer_api()
    cursor = MemoryCursor()
    sink = RecordingSink()

    with pytest.raises(api.ObserverError) as raised:
        api.ObserverStream(
            subscription=subscription(api), cursor_store=cursor, sink=sink,
        ).render((event(1, task_id="tsk_foreign"),))

    assert raised.value.code == "observer_identity_mismatch"
    assert cursor.acknowledged == []
    assert sink.records == []


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("session_id", "HOSTILE SESSION secret-value"),
        ("agent_id", object()),
        ("task_id", "tsk_"),
        ("attempt_id", "att_hostile\nsecond-line"),
        ("transport", "pty-HOSTILE"),
    ),
)
def test_expected_subscription_rejects_hostile_values_content_free(
    field: str, value: object,
) -> None:
    api = observer_api()
    values = {field: value}

    with pytest.raises((TypeError, ValueError)) as raised:
        subscription(api, **values)

    assert "HOSTILE" not in str(raised.value)


def test_cursor_writer_failure_has_stable_error_and_does_not_advance_local_cursor() -> None:
    api = observer_api()
    cursor = MemoryCursor(fail="acknowledge")
    stream = api.ObserverStream(
        subscription=subscription(api), cursor_store=cursor, sink=RecordingSink(),
    )

    with pytest.raises(api.ObserverError) as raised:
        stream.render((event(1, payload={"detail": "token is sk-writer-event"}),))

    assert raised.value.code == "observer_cursor_write_failed"
    assert str(raised.value) == "observer_cursor_write_failed: cursor acknowledgement failed"
    assert "sk-writer" not in str(raised.value)
    assert cursor.acknowledged == []
    cursor.fail = None
    output = stream.render((event(1),))
    assert [record_json(line)["sequence"] for line in output] == [1]


def test_sink_failure_happens_before_acknowledgement_and_keeps_local_cursor() -> None:
    api = observer_api()
    cursor = MemoryCursor()

    class FailingOnceSink:
        def __init__(self) -> None:
            self.failed = False
            self.records: list[str] = []

        def emit(self, record: str) -> None:
            if not self.failed:
                self.failed = True
                raise RuntimeError(f"HOSTILE SINK token is sk-stream {record}")
            self.records.append(record)

    sink = FailingOnceSink()
    stream = api.ObserverStream(
        subscription=subscription(api), cursor_store=cursor, sink=sink,
    )
    with pytest.raises(api.ObserverError) as raised:
        stream.render((event(1),))

    assert raised.value.code == "observer_sink_failed"
    assert "HOSTILE" not in str(raised.value)
    assert cursor.acknowledged == []
    output = stream.render((event(1),))
    assert [record_json(line)["sequence"] for line in output] == [1]
    assert sink.records == list(output)


def test_cursor_load_failure_and_malformed_value_are_content_free() -> None:
    api = observer_api()
    with pytest.raises(api.ObserverError) as load_error:
        api.ObserverStream(
            subscription=subscription(api), cursor_store=MemoryCursor(fail="load"),
            sink=RecordingSink(),
        )
    assert load_error.value.code == "observer_cursor_load_failed"
    assert "HOSTILE" not in str(load_error.value)

    cursor = MemoryCursor()
    hostile = SimpleNamespace(
        event_id="evt_1", session_id="ses_1", agent_id="agt_1",
        task_id="tsk_1", attempt_id="att_1", transport="acp", sequence=1,
        kind="message", timestamp=TIMESTAMP,
        payload={"detail": object()},
    )
    with pytest.raises(api.ObserverError) as malformed:
        api.ObserverStream(
            subscription=subscription(api), cursor_store=cursor, sink=RecordingSink(),
        ).render((hostile,))
    assert malformed.value.code == "observer_malformed_event"
    assert "object" not in str(malformed.value)
    assert cursor.acknowledged == []


def test_observer_api_exposes_no_domain_or_task29_authority() -> None:
    api = observer_api()
    forbidden = (
        "dispatch", "approve", "complete", "result", "recover", "write_state",
        "capture_pane", "extract_reply", "takeover", "return_control",
    )

    for owner in (
        api.ObserverStream, api.ObserverCursor, api.ObserverSubscription,
        api.ObserverBinding,
    ):
        assert all(not hasattr(owner, name) for name in forbidden)
