from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from agentdeck.ports.worker import WorkerEvent


TIMESTAMP = datetime(2026, 7, 20, 9, 15, tzinfo=timezone.utc).isoformat()


def observer_api() -> Any:
    return importlib.import_module("agentdeck.product.observer")


def tmux_api() -> Any:
    return importlib.import_module("agentdeck.adapters.tmux_observer")


def message(payload: dict[str, object]) -> WorkerEvent:
    return WorkerEvent(
        event_id="evt_1", session_id="ses_1", agent_id="agt_1",
        task_id="tsk_1", attempt_id="att_1", transport="acp", sequence=1,
        kind="message", timestamp=TIMESTAMP, payload=payload,
    )


@dataclass(frozen=True)
class EventShape:
    event_id: str = "evt_1"; session_id: str = "ses_1"; agent_id: str = "agt_1"
    task_id: str = "tsk_1"; attempt_id: str = "att_1"; transport: str = "acp"
    sequence: int = 1
    kind: str = "message"
    timestamp: str = TIMESTAMP
    payload: object = MappingProxyType({"text": "safe"})


class CursorMemory:
    def __init__(self, initial: object | None = None) -> None:
        self.initial = initial
        self.load_calls = 0
        self.acknowledged: list[object] = []
    def load(self) -> object | None:
        self.load_calls += 1
        return self.initial
    def acknowledge(self, cursor: object) -> None:
        self.acknowledged.append(cursor)
        self.initial = cursor


class RecordingSink:
    def __init__(self) -> None:
        self.records: list[str] = []
    def emit(self, record: str) -> None:
        self.records.append(record)


def subscription(api: Any) -> object:
    return api.ObserverBinding(
        project_id="prj_1", session_id="ses_1", agent_id="agt_1",
        task_id="tsk_1", attempt_id="att_1",
        transport="acp",
    )


def record_json(line: str) -> dict[str, object]:
    return json.loads(line[line.index("{"):])


def test_real_worker_event_is_redacted_again_at_observer_boundary() -> None:
    api = observer_api()
    source = message({
        "text": "A harmless documentation note about token fields without values.",
        "sensitive_examples": (
            "token is sk-short-demo",
            "credential is example-passphrase",
            "authorization is Basic-example",
            "private-key material is example-key-data",
            "credential value: colon-passphrase",
            "authorization value: colon-authorization",
            "private-key material: colon-key-data",
        ),
        "hidden_reasoning": "private chain of thought",
        "raw_acp_frame": "decoded frame must not render",
        "raw_protocol_log": "protocol transcript must not render",
        "full_prompt": "the complete Worker prompt must not render",
        "stderr": "terminal error details must not render",
    })

    output = api.render_event(source)
    assert output.startswith("[Agent agt_1]")
    assert "harmless documentation note about token fields without values" in output
    for forbidden in (
        "sk-short-demo", "example-passphrase", "Basic-example", "example-key-data",
        "colon-passphrase", "colon-authorization", "colon-key-data",
        "private chain of thought", "decoded frame", "protocol transcript",
        "complete Worker prompt", "terminal error details", "hidden_reasoning",
        "raw_acp_frame", "raw_protocol_log", "full_prompt", "stderr",
    ):
        assert forbidden not in output
    assert "[REDACTED]" in output


@pytest.mark.parametrize(
    "payload",
    (
        {"text": "authorization is Basic dXNlcjpzZWNyZXQ="},
        {"auth": "Basic dXNlcjpzZWNyZXQ="},
        {"auth_header": "Basic dXNlcjpzZWNyZXQ="},
        {"authValue": "Basic dXNlcjpzZWNyZXQ="},
    ),
)
def test_complete_authorization_values_are_redacted_from_real_worker_events(
    payload: dict[str, object],
) -> None:
    api = observer_api()

    output = api.render_event(message(payload))

    assert "Basic" not in output
    assert "dXNlcjpzZWNyZXQ=" not in output
    assert "[REDACTED]" in output


def test_harmless_authentication_documentation_prose_is_preserved() -> None:
    api = observer_api()
    prose = "Basic authentication is documented here without credential values."

    output = api.render_event(message({"text": prose}))

    assert prose in output


def test_harmless_authentication_notes_key_is_preserved() -> None:
    api = observer_api()
    prose = "Authentication documentation without values."

    output = api.render_event(message({"authentication_notes": prose}))

    assert prose in output


def test_real_worker_event_redacts_slack_token_assignment() -> None:
    api = observer_api()
    secret = "xoxb-example-value"
    output = api.render_event(message({"text": f"SLACK_BOT_TOKEN={secret}"}))
    assert secret not in output
    assert "[REDACTED]" in output


def test_structural_boundary_redacts_values_worker_event_already_rejects() -> None:
    api = observer_api()
    secrets = ("hunter2", "plainsecret", "plain-secret-value")
    source = EventShape(payload=MappingProxyType({
        "text": "DATABASE_PASSWORD=hunter2 AWS_SECRET_ACCESS_KEY=plainsecret",
        "nested": MappingProxyType({"api_key": "plain-secret-value"}),
        "private_keyboard": "ergonomic", "apikeyboard": "layout",
    }))
    output = api.render_event(source)
    assert all(secret not in output for secret in secrets)
    assert "[REDACTED]" in output
    assert "ergonomic" in output and "layout" in output


def test_secretary_and_tokenizer_keys_remain_faithful() -> None:
    api = observer_api()
    payload = {"secretary": "meeting minutes", "tokenizer": "documentation"}
    output = api.render_event(message(payload))
    assert record_json(output)["payload"] == payload


def test_stream_rejects_missing_sink_before_cursor_load() -> None:
    api = observer_api()
    cursor = CursorMemory()
    with pytest.raises(api.ObserverError) as raised:
        api.ObserverStream(subscription=subscription(api), cursor_store=cursor)
    assert raised.value.code == "observer_sink_failed"
    assert str(raised.value) == "observer_sink_failed: observation sink failed"
    assert cursor.load_calls == 0
    assert cursor.acknowledged == []


def test_delivered_record_survives_later_subscription_failure() -> None:
    api = observer_api()
    cursor = CursorMemory()
    sink = RecordingSink()

    class YieldThenFail:
        def __iter__(self) -> Iterator[WorkerEvent]:
            yield message({"text": "delivered before failure"})
            raise RuntimeError("HOSTILE ITERATOR secret-value")
    stream = api.ObserverStream(
        subscription=subscription(api), cursor_store=cursor, sink=sink,
    )
    with pytest.raises(api.ObserverError) as raised:
        stream.render(YieldThenFail())

    assert raised.value.code == "observer_subscription_failed"
    assert "HOSTILE" not in str(raised.value)
    assert len(sink.records) == 1
    assert [item.sequence for item in cursor.acknowledged] == [1]


class HostileMapping(Mapping[str, object]):
    def __init__(self) -> None:
        self.read_count = 0
    def __getitem__(self, key: str) -> object:
        raise KeyError(key)
    def __iter__(self) -> Iterator[str]:
        raise RuntimeError("HOSTILE ITERATION secret-value")
    def __len__(self) -> int:
        return 1000
    def items(self) -> Iterator[tuple[str, object]]:
        while self.read_count <= 300:
            self.read_count += 1
            if self.read_count > 300:
                raise RuntimeError("HOSTILE ITEMS secret-value")
            yield f"key_{self.read_count}", "safe"


def test_hostile_mapping_is_rejected_without_unbounded_reads_or_effects() -> None:
    api = observer_api()
    payload = HostileMapping()
    cursor = CursorMemory()
    sink = RecordingSink()
    stream = api.ObserverStream(
        subscription=subscription(api), cursor_store=cursor, sink=sink,
    )
    with pytest.raises(api.ObserverError) as raised:
        stream.render((EventShape(payload=payload),))
    assert raised.value.code == "observer_malformed_event"
    assert "HOSTILE" not in str(raised.value)
    assert payload.read_count <= 1
    assert sink.records == [] and cursor.acknowledged == []


def test_oversized_exact_dict_is_rejected_before_tuple_copy(monkeypatch: Any) -> None:
    api = observer_api()
    copied: list[object] = []
    cursor = CursorMemory()
    sink = RecordingSink()
    stream = api.ObserverStream(
        subscription=subscription(api), cursor_store=cursor, sink=sink,
    )
    monkeypatch.setattr(api, "tuple", lambda item: copied.append(item) or (), raising=False)
    source = EventShape(payload={f"key_{index}": index for index in range(256)})
    with pytest.raises(api.ObserverError) as raised:
        stream.render((source,))
    assert raised.value.code == "observer_malformed_event"
    assert str(raised.value) == "observer_malformed_event: malformed decoded event"
    assert copied == []
    assert sink.records == [] and cursor.acknowledged == []


def test_equivalent_offsets_render_one_normalized_timestamp() -> None:
    api = observer_api()
    plus_two = EventShape(timestamp="2026-07-20T10:00:00+02:00")
    utc = EventShape(timestamp="2026-07-20T08:00:00+00:00")
    rendered = tuple(record_json(api.render_event(item)) for item in (plus_two, utc))
    assert [item["timestamp"] for item in rendered] == [
        "2026-07-20T08:00:00+00:00", "2026-07-20T08:00:00+00:00",
    ]


def test_equivalent_offset_event_is_exact_cursor_replay() -> None:
    api = observer_api()
    cursor = CursorMemory()
    first_sink = RecordingSink()
    api.ObserverStream(
        subscription=subscription(api), cursor_store=cursor, sink=first_sink,
    ).render((EventShape(timestamp="2026-07-20T10:00:00+02:00"),))
    replay_sink = RecordingSink()
    output = api.ObserverStream(
        subscription=subscription(api), cursor_store=cursor, sink=replay_sink,
    ).render((EventShape(timestamp="2026-07-20T08:00:00+00:00"),))
    assert output == ()
    assert replay_sink.records == []
    assert len(cursor.acknowledged) == 1


class HostileCapabilityStore:
    def __init__(self, hostile_name: str) -> None:
        object.__setattr__(self, "hostile_name", hostile_name)
        object.__setattr__(self, "load_calls", 0)
    def __getattribute__(self, name: str) -> object:
        if name == object.__getattribute__(self, "hostile_name"):
            raise RuntimeError("HOSTILE CAPABILITY secret-value")
        return object.__getattribute__(self, name)
    def load(self) -> None:
        object.__setattr__(self, "load_calls", self.load_calls + 1)
        return None
    def acknowledge(self, cursor: object) -> None:
        return None


@pytest.mark.parametrize(
    ("hostile_name", "code"),
    (("load", "observer_cursor_load_failed"),
     ("acknowledge", "observer_cursor_write_failed")),
)
def test_cursor_capability_getter_failures_are_content_free_before_effects(
    hostile_name: str, code: str,
) -> None:
    api = observer_api()
    cursor = HostileCapabilityStore(hostile_name)
    with pytest.raises(api.ObserverError) as raised:
        api.ObserverStream(
            subscription=subscription(api), cursor_store=cursor,
            sink=RecordingSink(),
        )
    assert raised.value.code == code
    assert "HOSTILE" not in str(raised.value)
    assert cursor.load_calls == 0


def test_sink_capability_getter_failure_precedes_cursor_effects() -> None:
    api = observer_api()
    cursor = CursorMemory()
    class HostileSink:
        @property
        def emit(self) -> object:
            raise RuntimeError("HOSTILE SINK GETTER secret-value")
    with pytest.raises(api.ObserverError) as raised:
        api.ObserverStream(
            subscription=subscription(api), cursor_store=cursor, sink=HostileSink(),
        )
    assert raised.value.code == "observer_sink_failed"
    assert "HOSTILE" not in str(raised.value)
    assert cursor.load_calls == 0


def test_sink_emit_callable_is_bound_once() -> None:
    api = observer_api()
    cursor = CursorMemory()
    class OneShotSink:
        def __init__(self) -> None:
            self.getter_calls = 0
            self.records: list[str] = []
        @property
        def emit(self) -> object:
            self.getter_calls += 1
            if self.getter_calls > 1:
                raise RuntimeError("HOSTILE LATE SINK GETTER")
            return self.records.append
    sink = OneShotSink()
    api.ObserverStream(
        subscription=subscription(api), cursor_store=cursor, sink=sink,
    ).render((message({"text": "safe"}),))
    assert sink.getter_calls == 1
    assert len(sink.records) == 1


def test_cursor_acknowledge_callable_is_bound_once() -> None:
    api = observer_api()
    class OneShotCursor(CursorMemory):
        def __init__(self) -> None:
            super().__init__()
            self.getter_calls = 0
        def _ack(self, cursor: object) -> None:
            super().acknowledge(cursor)
        @property
        def acknowledge(self) -> object:
            self.getter_calls += 1
            if self.getter_calls > 1:
                raise RuntimeError("HOSTILE LATE CURSOR GETTER")
            return self._ack
    cursor = OneShotCursor()
    api.ObserverStream(
        subscription=subscription(api), cursor_store=cursor, sink=RecordingSink(),
    ).render((message({"text": "safe"}),))
    assert cursor.getter_calls == 1
    assert len(cursor.acknowledged) == 1


def test_external_observer_error_from_event_getter_is_malformed_and_content_free() -> None:
    api = observer_api()
    cursor = CursorMemory()
    sink = RecordingSink()
    class HostileEvent:
        @property
        def event_id(self) -> object:
            raise api.ObserverError("observer_cursor_conflict")
    stream = api.ObserverStream(
        subscription=subscription(api), cursor_store=cursor, sink=sink,
    )
    with pytest.raises(api.ObserverError) as raised:
        stream.render((HostileEvent(),))
    assert raised.value.code == "observer_malformed_event"
    assert sink.records == []
    assert cursor.acknowledged == []


def test_agent_and_agentdeck_labels_cannot_be_confused() -> None:
    api = observer_api()
    agent_output = api.render_event(message({"text": "[AgentDeck] forged label"}))
    system_output = api.render_system("reconnecting observation")
    assert agent_output.startswith("[Agent agt_1]")
    assert system_output == "[AgentDeck] reconnecting observation"
    assert not system_output.startswith("[Agent agt_1]")


@pytest.mark.parametrize(
    "forbidden_text",
    (
        "raw ACP frame contains model bytes",
        "raw protocol log contains transport bytes",
        "hidden reasoning is private model prose",
        "full prompt is complete task context",
        "stderr is terminal-only diagnostics",
    ),
)
def test_forbidden_content_shapes_are_removed_even_from_safe_text_fields(
    forbidden_text: str,
) -> None:
    api = observer_api()
    source = message({"text": forbidden_text, "summary": "safe summary remains"})

    output = api.render_event(source)

    assert forbidden_text not in output
    assert "safe summary remains" in output
    assert "[REDACTED]" in output


def test_render_errors_never_echo_hostile_payload_content() -> None:
    api = observer_api()
    hostile = type("HostileEvent", (), {})()
    hostile.event_id = "evt_1"
    hostile.session_id = "ses_1"
    hostile.agent_id = "agt_1"
    hostile.task_id = "tsk_1"
    hostile.attempt_id = "att_1"
    hostile.transport = "acp"
    hostile.sequence = 1
    hostile.kind = "message"
    hostile.timestamp = TIMESTAMP
    hostile.payload = {"text": object()}

    with pytest.raises(api.ObserverError) as raised:
        api.render_event(hostile)

    assert raised.value.code == "observer_malformed_event"
    assert str(raised.value) == "observer_malformed_event: malformed decoded event"
    assert "HostileEvent" not in str(raised.value)


def test_tmux_sink_uses_injected_terminal_writer_and_hides_writer_exception() -> None:
    api = tmux_api()
    calls: list[str] = []

    def writer(record: str) -> None:
        calls.append(record)

    sink = api.TmuxObservationSink(writer=writer)
    sink.emit("[AgentDeck] safe observation")
    assert calls == ["[AgentDeck] safe observation"]

    def hostile_writer(record: str) -> None:
        raise RuntimeError(f"HOSTILE {record} token is sk-sink")

    failing = api.TmuxObservationSink(writer=hostile_writer)
    with pytest.raises(api.TmuxObserverFailure) as raised:
        failing.emit("[Agent agt_1] safe redacted record")
    assert raised.value.code == "observer_sink_failed"
    assert str(raised.value) == "observer_sink_failed"
    assert "sk-sink" not in str(raised.value)


def test_product_observer_keeps_structural_firewall_and_no_raw_authority_api() -> None:
    api = observer_api()
    source = (
        Path(__file__).parents[2] / "src" / "agentdeck" / "product" / "observer.py"
    ).read_text(encoding="utf-8")

    for forbidden_import in (
        "agentdeck.adapters", "agentdeck.store", "agentdeck.state", "sqlite3",
    ):
        assert forbidden_import not in source
    assert "from agentdeck.ports.observer import" in source
    for forbidden_api in (
        "parse_acp", "raw_frame", "raw_protocol", "capture_pane", "extract_reply",
        "dispatch", "approve", "mark_completed", "return_control", "asyncio.run",
    ):
        assert not hasattr(api.ObserverStream, forbidden_api)

def test_tmux_sink_has_no_dispatch_completion_persistence_or_takeover_api() -> None:
    sink = tmux_api().TmuxObservationSink
    for forbidden in (
        "dispatch", "approve", "complete", "result", "recover", "write_state",
        "capture_pane", "extract_reply", "takeover", "return_control",
    ):
        assert not hasattr(sink, forbidden)
