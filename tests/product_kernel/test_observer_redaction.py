from __future__ import annotations

from datetime import datetime, timezone
import importlib
from pathlib import Path
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
        "agentdeck.ports", "agentdeck.adapters", "agentdeck.store",
        "agentdeck.state", "sqlite3",
    ):
        assert forbidden_import not in source
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
