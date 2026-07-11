from __future__ import annotations

import json
import math
import threading
from dataclasses import asdict

import pytest

from agentdeck.models import AgentSpec, LeaderConfig, ProjectConfig, RuntimeConfig
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


def _project_config(root) -> ProjectConfig:
    return ProjectConfig(
        name="protocol-project",
        root=str(root),
        leader=LeaderConfig(),
        agents=(AgentSpec("planner", "planner", "codex", "codex"),),
        runtime=RuntimeConfig(),
    )


def _contains_string(value: object, needle: str) -> bool:
    if isinstance(value, str):
        return needle in value
    if isinstance(value, dict):
        return any(_contains_string(key, needle) or _contains_string(item, needle) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_string(item, needle) for item in value)
    return False


def test_fresh_project_view_has_non_null_empty_protocol_summaries(tmp_path) -> None:
    payload = asdict(StateStore(tmp_path).project_view(_project_config(tmp_path)))

    assert payload["agent_sessions"] == {"count": 0, "by_state": {}, "items": []}
    assert payload["protocol_turns"] == {"count": 0, "by_state": {}, "items": []}
    assert payload["transport_updates"] == {"count": 0, "by_kind": {}, "items": []}
    assert payload["permission_requests"] == {
        "count": 0, "pending_count": 0, "by_status": {}, "items": [],
    }
    json.dumps(payload)


def test_project_view_exposes_compact_protocol_lineage_without_private_values(tmp_path) -> None:
    store = StateStore(tmp_path)
    session = store.record_agent_session(
        "planner", "codex", "native", "private-native-session", str(tmp_path), CAPABILITIES,
    )
    turn = store.record_protocol_turn(session["session_id"], "msg_protocol")
    update = store.record_transport_update(
        session["session_id"], turn["turn_id"], 7, "tool_call",
        {"nested": {"private": "private-update-payload"}},
    )
    permission = store.record_permission_request(
        session["session_id"], turn["turn_id"], "write_file", "private-target-path", "high",
    )

    payload = asdict(store.project_view(_project_config(tmp_path)))

    assert payload["agent_sessions"] == {
        "count": 1,
        "by_state": {"created": 1},
        "items": [{
            "session_id": session["session_id"], "agent_id": "planner", "provider": "codex",
            "transport": "native", "state": "created", "capabilities": CAPABILITIES.summary(),
            "native_session_present": True, "workspace": str(tmp_path),
            "created_at": session["created_at"], "updated_at": session["updated_at"],
        }],
    }
    assert payload["protocol_turns"]["items"] == [{
        key: turn[key] for key in ("turn_id", "session_id", "message_id", "state", "created_at", "updated_at")
    }]
    assert payload["transport_updates"] == {
        "count": 1, "by_kind": {"tool_call": 1},
        "items": [{key: update[key] for key in ("update_id", "session_id", "turn_id", "sequence", "kind", "created_at")}],
    }
    assert payload["permission_requests"] == {
        "count": 1, "pending_count": 1, "by_status": {"pending": 1},
        "items": [{key: permission[key] for key in (
            "permission_id", "session_id", "turn_id", "tool_name", "risk", "status", "decision", "created_at",
        )}],
    }
    assert not _contains_string(payload, "private-native-session")
    assert not _contains_string(payload, "private-update-payload")
    assert not _contains_string(payload, "private-target-path")


def test_project_view_protocol_summaries_are_sorted_counted_and_bounded(tmp_path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    for index in range(25):
        state["transport_updates"].append({
            "update_id": f"upd_{index:02d}", "session_id": "ags_1", "turn_id": "trn_1",
            "sequence": index, "kind": "text" if index % 2 else "progress",
            "payload": {"secret": index}, "created_at": f"2026-07-11T00:00:{index:02d}+00:00",
        })
    state["permission_requests"] = [
        {"permission_id": "prm_b", "session_id": "ags_1", "turn_id": "trn_1", "tool_name": "shell",
         "target": "secret", "risk": "high", "status": "denied", "decision": "deny",
         "created_at": "2026-07-11T00:00:00+00:00"},
        {"permission_id": "prm_a", "session_id": "ags_1", "turn_id": "trn_1", "tool_name": "read",
         "target": "secret", "risk": "low", "status": "pending", "decision": None,
         "created_at": "2026-07-11T00:00:00+00:00"},
    ]
    store.save(state)

    payload = asdict(store.project_view(_project_config(tmp_path)))

    updates = payload["transport_updates"]
    assert updates["count"] == 25
    assert updates["by_kind"] == {"progress": 13, "text": 12}
    assert len(updates["items"]) == 20
    assert [item["update_id"] for item in updates["items"]] == [f"upd_{index:02d}" for index in range(5, 25)]
    permissions = payload["permission_requests"]
    assert permissions["pending_count"] == 1
    assert permissions["by_status"] == {"denied": 1, "pending": 1}
    assert [item["permission_id"] for item in permissions["items"]] == ["prm_a", "prm_b"]


def test_project_view_rejects_corrupt_protocol_rows_instead_of_hiding_them(tmp_path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["protocol_turns"] = [{
        "turn_id": "trn_broken", "session_id": "ags_1", "message_id": None,
        "state": "created", "created_at": "2026-07-11T00:00:00+00:00",
        "updated_at": "2026-07-11T00:00:00+00:00",
    }]
    store.save(state)

    with pytest.raises(ValueError, match="invalid protocol summary field: message_id"):
        store.project_view(_project_config(tmp_path))


@pytest.mark.parametrize(("collection", "record", "error"), [
    ("agent_sessions", {"session_id": "ags_1", "agent_id": "", "provider": "codex", "transport": "native", "native_session_id": None, "workspace": "/tmp", "capabilities": CAPABILITIES.summary(), "state": "created", "created_at": "now", "updated_at": "now"}, "invalid agent session field: agent_id"),
    ("agent_sessions", {"session_id": "ags_1", "agent_id": "a", "provider": "codex", "transport": "native", "native_session_id": "", "workspace": "/tmp", "capabilities": CAPABILITIES.summary(), "state": "created", "created_at": "now", "updated_at": "now"}, "invalid agent session native_session_id"),
    ("agent_sessions", {"session_id": "ags_1", "agent_id": "a", "provider": "codex", "transport": "native", "native_session_id": None, "workspace": "/tmp", "capabilities": {**CAPABILITIES.summary(), "secret": True}, "state": "created", "created_at": "now", "updated_at": "now"}, "invalid agent session capabilities"),
    ("protocol_turns", {"turn_id": "trn_1", "session_id": "ags_1", "message_id": "msg_1", "state": "bogus", "created_at": "now", "updated_at": "now"}, "invalid protocol turn state"),
    ("transport_updates", {"update_id": "upd_1", "session_id": "ags_1", "turn_id": "trn_1", "sequence": True, "kind": "text", "created_at": "now"}, "invalid transport update sequence"),
    ("permission_requests", {"permission_id": "prm_1", "session_id": "ags_1", "turn_id": "trn_1", "tool_name": "shell", "risk": "high", "status": "bogus", "decision": None, "created_at": "now"}, "invalid permission request status"),
])
def test_project_view_protocol_source_validation_matrix(tmp_path, collection, record, error) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state[collection] = [record]
    store.save(state)

    with pytest.raises(ValueError, match=error):
        store.project_view(_project_config(tmp_path))


@pytest.mark.parametrize(("collection", "identity_field", "prefix"), [
    ("agent_sessions", "session_id", "ags"),
    ("protocol_turns", "turn_id", "trn"),
    ("transport_updates", "update_id", "upd"),
    ("permission_requests", "permission_id", "prm"),
])
def test_project_view_rejects_protocol_duplicate_hidden_by_latest_twenty(
    tmp_path, collection, identity_field, prefix,
) -> None:
    store = StateStore(tmp_path)
    session = build_agent_session("planner", "codex", "native", None, "/tmp", CAPABILITIES)
    turn = build_turn(session["session_id"], "msg_1")
    templates = {
        "agent_sessions": session,
        "protocol_turns": turn,
        "transport_updates": build_transport_update(session["session_id"], turn["turn_id"], 0, "text", {}),
        "permission_requests": build_permission_request(session["session_id"], turn["turn_id"], "read", "/tmp", "low"),
    }
    records = []
    for index in range(21):
        record = dict(templates[collection])
        record[identity_field] = f"{prefix}_{index:02d}"
        record["created_at"] = f"2026-07-11T00:00:{index:02d}+00:00"
        if collection == "transport_updates":
            record["sequence"] = index
        records.append(record)
    records[-1][identity_field] = records[0][identity_field]
    state = store.load()
    state[collection] = records
    store.save(state)

    with pytest.raises(ValueError, match=f"duplicate {identity_field}: {prefix}_00"):
        store.project_view(_project_config(tmp_path))


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


def _tree_snapshot(store: StateStore) -> dict[str, bytes]:
    return {
        str(path.relative_to(store.deck_dir)): path.read_bytes()
        for path in sorted(store.deck_dir.rglob("*"))
        if path.is_file()
    }


def test_fresh_state_has_protocol_lineage_collections(tmp_path) -> None:
    state = StateStore(tmp_path).load()
    assert state["agent_sessions"] == []
    assert state["protocol_turns"] == []
    assert state["transport_updates"] == []
    assert state["permission_requests"] == []
    assert state["protocol_event_outbox"] == []


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


def test_event_failure_returns_record_with_durable_outbox_and_flushes_once(tmp_path, monkeypatch) -> None:
    store = StateStore(tmp_path)
    real_append_event = store.append_event

    def fail_event(event):
        raise OSError("event failed")

    monkeypatch.setattr(store, "append_event", fail_event)
    record = store.record_agent_session("a", "p", "t", None, "w", CAPABILITIES)
    state = store.load()
    assert state["agent_sessions"] == [record]
    assert len(state["protocol_event_outbox"]) == 1
    assert store.events_path.read_text() == ""

    monkeypatch.setattr(store, "append_event", real_append_event)
    assert store.flush_protocol_event_outbox() == 1
    assert store.flush_protocol_event_outbox() == 0
    assert store.load()["protocol_event_outbox"] == []
    events = [json.loads(line) for line in store.events_path.read_text().splitlines()]
    assert len(events) == 1
    assert events[0]["payload"]["session_id"] == record["session_id"]


def test_outbox_replay_deduplicates_event_already_in_ledger(tmp_path) -> None:
    store = StateStore(tmp_path)
    record = store.record_agent_session("a", "p", "t", None, "w", CAPABILITIES)
    event = json.loads(store.events_path.read_text().splitlines()[0])
    state = store.load()
    state["protocol_event_outbox"] = [event]
    store.save(state)

    assert store.flush_protocol_event_outbox() == 0
    assert store.load()["protocol_event_outbox"] == []
    assert len(store.list_agent_sessions()) == 1
    assert store.list_agent_sessions()[0]["session_id"] == record["session_id"]
    assert len(store.events_path.read_text().splitlines()) == 1


def test_outbox_clear_save_failure_is_recoverable_without_duplicate_event(tmp_path, monkeypatch) -> None:
    store = StateStore(tmp_path)
    real_append_event = store.append_event
    monkeypatch.setattr(store, "append_event", lambda event: (_ for _ in ()).throw(OSError("event failed")))
    store.record_agent_session("a", "p", "t", None, "w", CAPABILITIES)
    monkeypatch.setattr(store, "append_event", real_append_event)
    real_save = store.save
    calls = 0

    def fail_clear_once(state):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("clear failed")
        return real_save(state)

    monkeypatch.setattr(store, "save", fail_clear_once)
    with pytest.raises(OSError, match="clear failed"):
        store.flush_protocol_event_outbox()
    assert len(store.events_path.read_text().splitlines()) == 1
    assert len(store.load()["protocol_event_outbox"]) == 1
    assert store.flush_protocol_event_outbox() == 0
    assert len(store.events_path.read_text().splitlines()) == 1
    assert store.load()["protocol_event_outbox"] == []


def test_concurrent_protocol_mutations_do_not_lose_records(tmp_path) -> None:
    store = StateStore(tmp_path)
    barrier = threading.Barrier(3)
    records = []

    def record(agent_id: str) -> None:
        barrier.wait()
        records.append(store.record_agent_session(agent_id, "p", "t", None, "w", CAPABILITIES))

    threads = [threading.Thread(target=record, args=(agent_id,)) for agent_id in ("a", "b")]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)
    assert all(not thread.is_alive() for thread in threads)
    assert {item["session_id"] for item in store.list_agent_sessions()} == {
        item["session_id"] for item in records
    }
    assert len(records) == 2
    assert len(store.events_path.read_text().splitlines()) == 2


@pytest.mark.parametrize(("builder_name", "record_kind"), [
    ("build_agent_session", "session"),
    ("build_turn", "turn"),
    ("build_transport_update", "update"),
    ("build_permission_request", "permission"),
])
def test_builder_candidate_id_collisions_are_zero_write(
    tmp_path, monkeypatch, builder_name: str, record_kind: str
) -> None:
    store = StateStore(tmp_path)
    session = store.record_agent_session("a", "p", "t", None, "w", CAPABILITIES)
    turn = store.record_protocol_turn(session["session_id"], "msg")
    update = store.record_transport_update(session["session_id"], turn["turn_id"], 0, "text", {})
    permission = store.record_permission_request(session["session_id"], turn["turn_id"], "shell", "cwd", "low")
    existing = {"session": session, "turn": turn, "update": update, "permission": permission}[record_kind]
    real_builder = {
        "session": build_agent_session,
        "turn": build_turn,
        "update": build_transport_update,
        "permission": build_permission_request,
    }[record_kind]

    def colliding_builder(*args, **kwargs):
        candidate = real_builder(*args, **kwargs)
        id_key = {"session": "session_id", "turn": "turn_id", "update": "update_id", "permission": "permission_id"}[record_kind]
        candidate[id_key] = existing[id_key]
        return candidate

    monkeypatch.setattr(f"agentdeck.state.{builder_name}", colliding_builder)
    before = _disk_snapshot(store)
    with pytest.raises(ValueError, match="duplicate .* identity"):
        if record_kind == "session":
            store.record_agent_session("b", "p", "t", None, "w", CAPABILITIES)
        elif record_kind == "turn":
            store.record_protocol_turn(session["session_id"], "msg2")
        elif record_kind == "update":
            store.record_transport_update(session["session_id"], turn["turn_id"], 1, "text", {})
        else:
            store.record_permission_request(session["session_id"], turn["turn_id"], "shell", "other", "low")
    assert _disk_snapshot(store) == before


@pytest.mark.parametrize("rejection", ["invalid", "unknown", "collision"])
def test_rejected_mutation_does_not_flush_pending_outbox(tmp_path, monkeypatch, rejection: str) -> None:
    store = StateStore(tmp_path)
    real_append_event = store.append_event
    monkeypatch.setattr(store, "append_event", lambda event: (_ for _ in ()).throw(OSError("pending")))
    existing = store.record_agent_session("a", "p", "t", None, "w", CAPABILITIES)
    monkeypatch.setattr(store, "append_event", real_append_event)
    assert len(store.load()["protocol_event_outbox"]) == 1

    if rejection == "collision":
        real_builder = build_agent_session

        def colliding_builder(*args, **kwargs):
            candidate = real_builder(*args, **kwargs)
            candidate["session_id"] = existing["session_id"]
            return candidate

        monkeypatch.setattr("agentdeck.state.build_agent_session", colliding_builder)

    before = _tree_snapshot(store)
    with pytest.raises((ValueError, KeyError)):
        if rejection == "invalid":
            store.record_agent_session("", "p", "t", None, "w", CAPABILITIES)
        elif rejection == "unknown":
            store.record_protocol_turn("ags_unknown", "msg")
        else:
            store.record_agent_session("b", "p", "t", None, "w", CAPABILITIES)
    assert _tree_snapshot(store) == before
