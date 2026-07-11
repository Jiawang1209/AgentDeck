from __future__ import annotations

import json
import math

import pytest

from agentdeck.runtime.protocol import (
    AGENT_SESSION_STATES,
    PERMISSION_STATES,
    TURN_STATES,
    UPDATE_KINDS,
    TransportCapabilities,
    build_agent_session,
    build_permission_request,
    build_transport_update,
    build_turn,
)


CAPABILITIES = TransportCapabilities(True, True, True, True, True, False)


def test_protocol_constants_are_stable() -> None:
    assert AGENT_SESSION_STATES == ("created", "connecting", "ready", "busy", "reconnecting", "stopped", "failed")
    assert TURN_STATES == ("created", "submitted", "streaming", "waiting_permission", "completed", "blocked", "failed", "ambiguous")
    assert UPDATE_KINDS == ("progress", "text", "tool_call", "tool_result", "permission_request", "artifact", "completion", "error")
    assert PERMISSION_STATES == ("pending", "approved", "denied", "expired")


def test_tmux_fallback_capabilities_are_terminal_only() -> None:
    capabilities = TransportCapabilities.tmux_fallback()
    assert capabilities.summary() == {
        "structured_sessions": False,
        "streaming_updates": False,
        "structured_tools": False,
        "permission_requests": False,
        "resume_session": False,
        "observable_terminal": True,
    }


def test_builders_create_json_serializable_domain_records() -> None:
    session = build_agent_session("planner", "codex", "native", None, "/tmp/project", CAPABILITIES)
    turn = build_turn(session["session_id"], "msg_123")
    update = build_transport_update(session["session_id"], turn["turn_id"], 0, "progress", {"percent": 10})
    permission = build_permission_request(session["session_id"], turn["turn_id"], "write_file", "README.md", "medium")

    assert session == {
        "session_id": session["session_id"], "agent_id": "planner", "provider": "codex", "transport": "native",
        "native_session_id": None, "workspace": "/tmp/project", "capabilities": CAPABILITIES.summary(),
        "state": "created", "created_at": session["created_at"], "updated_at": session["updated_at"],
        "observation_bindings": [],
    }
    assert session["session_id"].startswith("ags_")
    assert "pane_id" not in session
    assert turn["turn_id"].startswith("trn_") and turn["state"] == "created"
    assert update["update_id"].startswith("upd_") and update["payload"] == {"percent": 10}
    assert permission["permission_id"].startswith("prm_")
    assert permission["status"] == "pending" and permission["decision"] is None
    assert "state" not in permission
    json.dumps([session, turn, update, permission])


@pytest.mark.parametrize("field", ["agent_id", "provider", "transport", "workspace"])
@pytest.mark.parametrize("bad_value", ["", "   ", True, 7])
def test_session_rejects_invalid_required_strings(field: str, bad_value: object) -> None:
    values = {"agent_id": "planner", "provider": "codex", "transport": "native", "workspace": "/tmp/project"}
    values[field] = bad_value
    with pytest.raises(ValueError, match=rf"^{field} must be a non-empty string$"):
        build_agent_session(capabilities=CAPABILITIES, native_session_id=None, **values)


@pytest.mark.parametrize("native_session_id", ["", "  ", True, 3])
def test_session_rejects_bad_native_session_id(native_session_id: object) -> None:
    with pytest.raises(ValueError, match=r"^native_session_id must be None or a non-empty string$"):
        build_agent_session("planner", "codex", "native", native_session_id, "/tmp/project", CAPABILITIES)


def test_session_requires_exact_capabilities_type() -> None:
    with pytest.raises(TypeError, match=r"^capabilities must be a TransportCapabilities instance$"):
        build_agent_session("planner", "codex", "native", None, "/tmp/project", CAPABILITIES.summary())


@pytest.mark.parametrize(("builder", "message"), [
    (lambda: build_turn("bad", "msg_1"), "session_id must match ags_<lowercase alphanumeric token>"),
    (lambda: build_transport_update("ags_1", "bad", 0, "text", {}), "turn_id must match trn_<lowercase alphanumeric token>"),
    (lambda: build_permission_request("bad", "trn_1", "shell", "cwd", "high"), "session_id must match ags_<lowercase alphanumeric token>"),
])
def test_builders_reject_bad_record_id_prefixes(builder, message: str) -> None:
    with pytest.raises(ValueError, match=rf"^{message}$"):
        builder()


@pytest.mark.parametrize("sequence", [-1, True, 1.5, "1"])
def test_update_rejects_bad_sequence(sequence: object) -> None:
    with pytest.raises(ValueError, match=r"^sequence must be a non-negative integer$"):
        build_transport_update("ags_1", "trn_1", sequence, "text", {})


def test_update_rejects_unknown_kind_and_bad_payload() -> None:
    with pytest.raises(ValueError, match=r"^kind must be one of UPDATE_KINDS$"):
        build_transport_update("ags_1", "trn_1", 0, "unknown", {})
    with pytest.raises(TypeError, match=r"^payload must be a dict$"):
        build_transport_update("ags_1", "trn_1", 0, "text", [])


def test_update_rejects_non_string_kind_without_equality_hooks() -> None:
    class HostileKind:
        equality_called = False

        def __eq__(self, other):
            type(self).equality_called = True
            raise AssertionError("equality hook must not run")

    with pytest.raises(ValueError, match=r"^kind must be one of UPDATE_KINDS$"):
        build_transport_update("ags_1", "trn_1", 0, HostileKind(), {})
    assert HostileKind.equality_called is False


@pytest.mark.parametrize("bad_value", [{1, 2}, object(), (1, 2)])
def test_update_rejects_non_json_payload_values(bad_value: object) -> None:
    with pytest.raises(TypeError, match=r"^payload must contain only JSON-safe values$"):
        build_transport_update("ags_1", "trn_1", 0, "text", {"value": bad_value})


def test_update_rejects_non_string_payload_keys() -> None:
    with pytest.raises(TypeError, match=r"^payload keys must be strings$"):
        build_transport_update("ags_1", "trn_1", 0, "text", {1: "value"})


@pytest.mark.parametrize("bad_number", [math.nan, math.inf, -math.inf])
def test_update_rejects_non_finite_payload_numbers(bad_number: float) -> None:
    with pytest.raises(ValueError, match=r"^payload numbers must be finite$"):
        build_transport_update("ags_1", "trn_1", 0, "text", {"value": bad_number})


def test_update_does_not_invoke_payload_deepcopy_hooks() -> None:
    class HostileValue:
        deepcopy_called = False

        def __deepcopy__(self, memo):
            type(self).deepcopy_called = True
            raise AssertionError("deepcopy hook must not run")

    with pytest.raises(TypeError, match=r"^payload must contain only JSON-safe values$"):
        build_transport_update("ags_1", "trn_1", 0, "text", {"value": HostileValue()})
    assert HostileValue.deepcopy_called is False


@pytest.mark.parametrize("field,prefix,bad_value", [
    ("session_id", "ags_", "ags_"),
    ("session_id", "ags_", "ags_   "),
    ("session_id", "ags_", " ags_abc"),
    ("session_id", "ags_", "ags_abc "),
    ("session_id", "ags_", "ags_abc\n"),
    ("session_id", "ags_", "ags_ABC"),
    ("session_id", "ags_", "ags_a-b"),
    ("turn_id", "trn_", "trn_"),
    ("turn_id", "trn_", "trn_   "),
    ("turn_id", "trn_", " trn_abc"),
    ("turn_id", "trn_", "trn_abc "),
    ("turn_id", "trn_", "trn_abc\n"),
    ("turn_id", "trn_", "trn_ABC"),
    ("turn_id", "trn_", "trn_a-b"),
])
def test_builders_reject_malformed_protocol_ids(field: str, prefix: str, bad_value: str) -> None:
    if field == "session_id":
        builder = lambda: build_turn(bad_value, "msg_1")
    else:
        builder = lambda: build_transport_update("ags_abc", bad_value, 0, "text", {})
    with pytest.raises(ValueError, match=rf"^{field} must match {prefix}<lowercase alphanumeric token>$"):
        builder()


def test_builder_mutable_fields_are_isolated() -> None:
    payload = {"nested": {"items": [1]}}
    first = build_transport_update("ags_1", "trn_1", 0, "text", payload)
    second = build_transport_update("ags_1", "trn_1", 1, "text", payload)
    payload["nested"]["items"].append(2)
    first["payload"]["nested"]["items"].append(3)
    assert second["payload"] == {"nested": {"items": [1]}}

    first_session = build_agent_session("a", "p", "t", None, "w", CAPABILITIES)
    second_session = build_agent_session("a", "p", "t", None, "w", CAPABILITIES)
    first_session["observation_bindings"].append({"kind": "terminal"})
    assert second_session["observation_bindings"] == []


def test_builders_are_pure_domain_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("external side effect")

    monkeypatch.setattr("builtins.open", forbidden)
    build_agent_session("a", "p", "t", None, "w", CAPABILITIES)
    build_turn("ags_1", "msg_1")
    build_transport_update("ags_1", "trn_1", 0, "text", {})
    build_permission_request("ags_1", "trn_1", "shell", "cwd", "low")
