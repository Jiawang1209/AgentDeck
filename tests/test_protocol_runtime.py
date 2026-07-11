from __future__ import annotations

import json
import math

import pytest

from agentdeck.state import StateStore
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


def _disk_snapshot(store: StateStore) -> tuple[bool, bytes | None, bool, bytes | None]:
    return (
        store.state_path.exists(),
        store.state_path.read_bytes() if store.state_path.exists() else None,
        store.events_path.exists(),
        store.events_path.read_bytes() if store.events_path.exists() else None,
    )


def test_fresh_state_has_protocol_lineage_collections(tmp_path) -> None:
    state = StateStore(tmp_path).load()
    assert state["agent_sessions"] == []
    assert state["protocol_turns"] == []
    assert state["transport_updates"] == []
    assert state["permission_requests"] == []


def test_state_store_records_complete_protocol_lineage_and_redacted_events(tmp_path) -> None:
    store = StateStore(tmp_path)
    session = store.record_agent_session("planner", "codex", "native", None, "/tmp/project", CAPABILITIES)
    turn = store.record_protocol_turn(session["session_id"], "msg_123")
    update = store.record_transport_update(session["session_id"], turn["turn_id"], 0, "tool_call", {"path": "/secret"})
    permission = store.record_permission_request(session["session_id"], turn["turn_id"], "write_file", "/secret", "high")

    assert store.agent_session_by_id(session["session_id"]) == session
    assert store.protocol_turn_by_id(turn["turn_id"]) == turn
    assert store.list_agent_sessions() == [session]
    assert store.list_protocol_turns() == [turn]
    assert store.list_transport_updates() == [update]
    assert store.list_permission_requests() == [permission]
    json.dumps(store.load())
    events = [json.loads(line) for line in store.events_path.read_text().splitlines()]
    assert [event["event_type"] for event in events] == [
        "agent_session_recorded", "protocol_turn_recorded", "transport_update_recorded", "permission_request_recorded"
    ]
    assert events[0]["payload"] == {"session_id": session["session_id"], "agent_id": "planner", "transport": "native"}
    assert events[1]["payload"] == {"turn_id": turn["turn_id"], "session_id": session["session_id"], "message_id": "msg_123"}
    assert events[2]["payload"] == {"update_id": update["update_id"], "session_id": session["session_id"], "turn_id": turn["turn_id"], "sequence": 0, "kind": "tool_call"}
    assert events[3]["payload"] == {"permission_id": permission["permission_id"], "session_id": session["session_id"], "turn_id": turn["turn_id"], "tool_name": "write_file", "risk": "high"}
    assert "/secret" not in store.events_path.read_text()


def test_protocol_state_methods_support_old_state_and_lists_are_independent(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.save({"agents": {}})
    session = store.record_agent_session("planner", "codex", "native", None, "/tmp/project", CAPABILITIES)
    listed = store.list_agent_sessions()
    listed.clear()
    assert store.list_agent_sessions() == [session]


@pytest.mark.parametrize("operation", ["turn", "update", "permission"])
def test_unknown_protocol_references_are_zero_write(tmp_path, operation: str) -> None:
    store = StateStore(tmp_path)
    before = _disk_snapshot(store)
    with pytest.raises(KeyError):
        if operation == "turn":
            store.record_protocol_turn("ags_unknown", "msg_1")
        elif operation == "update":
            store.record_transport_update("ags_unknown", "trn_unknown", 0, "text", {})
        else:
            store.record_permission_request("ags_unknown", "trn_unknown", "shell", "cwd", "high")
    assert _disk_snapshot(store) == before


def test_mismatched_turn_references_and_duplicate_sequence_are_zero_write(tmp_path) -> None:
    store = StateStore(tmp_path)
    first = store.record_agent_session("a", "p", "t", None, "w", CAPABILITIES)
    second = store.record_agent_session("b", "p", "t", None, "w", CAPABILITIES)
    turn = store.record_protocol_turn(first["session_id"], "msg")
    store.record_transport_update(first["session_id"], turn["turn_id"], 0, "text", {})
    for call, message in [
        (lambda: store.record_transport_update(second["session_id"], turn["turn_id"], 1, "text", {}), "protocol turn session mismatch"),
        (lambda: store.record_permission_request(second["session_id"], turn["turn_id"], "shell", "cwd", "high"), "protocol turn session mismatch"),
        (lambda: store.record_transport_update(first["session_id"], turn["turn_id"], 0, "text", {}), "duplicate transport update sequence"),
    ]:
        before = _disk_snapshot(store)
        with pytest.raises(ValueError, match=message):
            call()
        assert _disk_snapshot(store) == before


@pytest.mark.parametrize("collection,error", [
    ("agent_sessions", "duplicate agent session identity"),
    ("protocol_turns", "duplicate protocol turn identity"),
])
def test_corrupt_duplicate_protocol_identity_is_zero_write(tmp_path, collection: str, error: str) -> None:
    store = StateStore(tmp_path)
    session = build_agent_session("a", "p", "t", None, "w", CAPABILITIES)
    turn = build_turn(session["session_id"], "msg")
    state = store.load()
    state["agent_sessions"] = [session]
    state["protocol_turns"] = [turn]
    state[collection].append(dict(state[collection][0]))
    store.save(state)
    before = _disk_snapshot(store)
    with pytest.raises(ValueError, match=error):
        store.record_permission_request(session["session_id"], turn["turn_id"], "shell", "cwd", "high")
    assert _disk_snapshot(store) == before


def test_builder_rejection_and_save_failure_do_not_append_events(tmp_path, monkeypatch) -> None:
    store = StateStore(tmp_path)
    before = _disk_snapshot(store)
    with pytest.raises(ValueError):
        store.record_agent_session("", "p", "t", None, "w", CAPABILITIES)
    assert _disk_snapshot(store) == before

    def fail_save(state):
        raise OSError("save failed")

    monkeypatch.setattr(store, "save", fail_save)
    before_save_failure = _disk_snapshot(store)
    with pytest.raises(OSError, match="save failed"):
        store.record_agent_session("a", "p", "t", None, "w", CAPABILITIES)
    assert _disk_snapshot(store) == before_save_failure


def test_event_failure_happens_after_state_save(tmp_path, monkeypatch) -> None:
    store = StateStore(tmp_path)

    def fail_event(event):
        raise OSError("event failed")

    monkeypatch.setattr(store, "append_event", fail_event)
    with pytest.raises(OSError, match="event failed"):
        store.record_agent_session("a", "p", "t", None, "w", CAPABILITIES)
    assert len(store.load()["agent_sessions"]) == 1
