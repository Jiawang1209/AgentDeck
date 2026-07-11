from __future__ import annotations

import json
import math
import threading
from copy import deepcopy
from dataclasses import asdict

import pytest

from agentdeck.models import AgentSpec, LeaderConfig, ProjectConfig, RuntimeConfig
from agentdeck.state import StateStore
from agentdeck.runtime.protocol import (
    AGENT_SESSION_STATES,
    PROTOCOL_ENTITY_TYPES,
    PROTOCOL_TRANSITION_EDGES,
    PERMISSION_STATES,
    TURN_KINDS,
    TURN_STATES,
    TRANSPORT_KINDS,
    UPDATE_KINDS,
    TransportCapabilities,
    build_agent_session,
    build_permission_request,
    build_protocol_transition,
    build_transport_update,
    build_turn,
)


CAPABILITIES = TransportCapabilities(True, True, True, True, True, False)


def test_state_store_default_constructor_still_creates_project_layout(tmp_path) -> None:
    store = StateStore(tmp_path)

    assert store.deck_dir == tmp_path / ".agentdeck"
    assert store.events_path.exists()
    assert (store.deck_dir / "state" / "approvals.jsonl").exists()


def test_state_store_open_existing_binds_paths_without_touching_layout(tmp_path) -> None:
    created = StateStore(tmp_path)
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in created.deck_dir.rglob("*") if path.is_file()
    }

    opened = StateStore.open_existing(tmp_path)

    assert opened.root == tmp_path
    assert opened.deck_dir == created.deck_dir
    assert opened.state_path == created.state_path
    assert opened.events_path == created.events_path
    assert {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in created.deck_dir.rglob("*") if path.is_file()
    } == before


def test_state_store_open_existing_missing_layout_does_not_create_it(tmp_path) -> None:
    opened = StateStore.open_existing(tmp_path)

    assert opened.deck_dir == tmp_path / ".agentdeck"
    assert not opened.deck_dir.exists()


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
        "planner", "codex", "tmux", "private-native-session", str(tmp_path), CAPABILITIES,
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
            "transport": "tmux", "state": "created", "capabilities": CAPABILITIES.summary(),
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


def test_populated_project_view_projects_to_valid_protocol_runtime_status_without_raw_values(
    tmp_path,
) -> None:
    from agentdeck.contracts import (
        PROTOCOL_RUNTIME_CONTRACT_VERSION,
        protocol_runtime_example,
        validate_protocol_runtime_contract,
    )

    store = StateStore(tmp_path)
    session = store.record_agent_session(
        "planner", "codex", "tmux", "private-native-session", str(tmp_path), CAPABILITIES,
    )
    turn = store.record_protocol_turn(session["session_id"], "msg_protocol")
    store.record_transport_update(
        session["session_id"], turn["turn_id"], 1, "tool_call", {"raw": "private-update"},
    )
    store.record_permission_request(
        session["session_id"], turn["turn_id"], "write_file", "private-target", "high",
    )
    view = asdict(store.project_view(_project_config(tmp_path)))
    payload = {
        "mode": "protocol_runtime_status",
        "contract_version": PROTOCOL_RUNTIME_CONTRACT_VERSION,
        "project": view["project"],
        "runtime_backend": view["runtime_backend"],
            **{name: view[name] for name in (
                "agent_sessions", "protocol_turns", "transport_updates", "permission_requests",
                "protocol_state_transitions",
            )},
        "controls": deepcopy(protocol_runtime_example()["controls"]),
    }

    assert validate_protocol_runtime_contract(payload) == {"ok": True, "errors": []}
    assert not _contains_string(payload, "private-native-session")
    assert not _contains_string(payload, "private-update")
    assert not _contains_string(payload, "private-target")


def test_project_view_protocol_summaries_are_sorted_counted_and_bounded(tmp_path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    session = build_agent_session("planner", "codex", "tmux", None, "/tmp", CAPABILITIES)
    session["session_id"] = "ags_1"
    turn = build_turn("ags_1", "msg_1")
    turn["turn_id"] = "trn_1"
    state["agent_sessions"] = [session]
    state["protocol_turns"] = [turn]
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
    ("agent_sessions", {"session_id": "ags_1", "agent_id": "", "provider": "codex", "transport": "tmux", "native_session_id": None, "workspace": "/tmp", "capabilities": CAPABILITIES.summary(), "state": "created", "created_at": "now", "updated_at": "now"}, "invalid agent session field: agent_id"),
    ("agent_sessions", {"session_id": "ags_1", "agent_id": "a", "provider": "codex", "transport": "tmux", "native_session_id": "", "workspace": "/tmp", "capabilities": CAPABILITIES.summary(), "state": "created", "created_at": "now", "updated_at": "now"}, "invalid agent session native_session_id"),
    ("agent_sessions", {"session_id": "ags_1", "agent_id": "a", "provider": "codex", "transport": "tmux", "native_session_id": None, "workspace": "/tmp", "capabilities": {**CAPABILITIES.summary(), "secret": True}, "state": "created", "created_at": "now", "updated_at": "now"}, "invalid agent session capabilities"),
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
    session = build_agent_session("planner", "codex", "tmux", None, "/tmp", CAPABILITIES)
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
    assert AGENT_SESSION_STATES == ("created", "connecting", "ready", "busy", "reconnecting", "disconnected", "stopped", "failed")
    assert PROTOCOL_ENTITY_TYPES == ("session", "turn", "permission")
    assert TURN_KINDS == ("prompt", "load_replay")
    assert TURN_STATES == ("created", "submitted", "streaming", "waiting_permission", "completed", "blocked", "failed", "ambiguous")
    assert UPDATE_KINDS == ("progress", "text", "tool_call", "tool_result", "permission_request", "artifact", "completion", "error")
    assert PERMISSION_STATES == ("pending", "approved", "denied", "expired")
    assert TRANSPORT_KINDS == ("acp", "acp-adapter", "tmux", "api")


def test_session_rejects_unsupported_transport_without_equality_hooks() -> None:
    class HostileTransport:
        equality_called = False

        def __eq__(self, other):
            type(self).equality_called = True
            raise AssertionError("equality hook must not run")

    with pytest.raises(ValueError, match=r"^transport must be one of TRANSPORT_KINDS$"):
        build_agent_session("planner", "codex", "telepathy", None, "/tmp", CAPABILITIES)
    with pytest.raises(ValueError, match=r"^transport must be one of TRANSPORT_KINDS$"):
        build_agent_session("planner", "codex", HostileTransport(), None, "/tmp", CAPABILITIES)
    assert HostileTransport.equality_called is False


@pytest.mark.parametrize("transport", ["", "   ", True, 7])
def test_session_rejects_invalid_transport_types_and_values(transport: object) -> None:
    with pytest.raises(ValueError, match=r"^transport must be one of TRANSPORT_KINDS$"):
        build_agent_session("planner", "codex", transport, None, "/tmp", CAPABILITIES)


def test_project_view_rejects_hidden_broken_protocol_lineage_without_writes(tmp_path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    session = build_agent_session("planner", "codex", "tmux", None, "/tmp", CAPABILITIES)
    turn = build_turn(session["session_id"], "msg_1")
    state["agent_sessions"] = [session]
    state["protocol_turns"] = [turn]
    state["transport_updates"] = [
        build_transport_update(session["session_id"], turn["turn_id"], index, "text", {})
        for index in range(21)
    ]
    state["transport_updates"][0]["turn_id"] = "trn_missing"
    store.save(state)
    before = store.state_path.read_bytes()

    with pytest.raises(ValueError, match="transport update turn reference missing"):
        store.project_view(_project_config(tmp_path))

    assert store.state_path.read_bytes() == before


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
    session = build_agent_session("planner", "codex", "tmux", None, "/tmp/project", CAPABILITIES)
    turn = build_turn(session["session_id"], "msg_123")
    update = build_transport_update(session["session_id"], turn["turn_id"], 0, "progress", {"percent": 10})
    permission = build_permission_request(session["session_id"], turn["turn_id"], "write_file", "README.md", "medium")

    assert session == {
        "session_id": session["session_id"], "agent_id": "planner", "provider": "codex", "transport": "tmux",
        "native_session_id": None, "workspace": "/tmp/project", "capabilities": CAPABILITIES.summary(),
        "state": "created", "created_at": session["created_at"], "updated_at": session["updated_at"],
        "observation_bindings": [],
    }
    assert session["session_id"].startswith("ags_")
    assert "pane_id" not in session
    assert turn["turn_id"].startswith("trn_") and turn["state"] == "created"
    assert turn["kind"] == "prompt"
    assert update["update_id"].startswith("upd_") and update["payload"] == {"percent": 10}
    assert permission["permission_id"].startswith("prm_")
    assert permission["status"] == "pending" and permission["decision"] is None
    assert "state" not in permission
    json.dumps([session, turn, update, permission])


def test_turn_kind_is_backward_compatible_and_explicit() -> None:
    assert build_turn("ags_1", "msg_1")["kind"] == "prompt"
    assert build_turn("ags_1", "msg_1", kind="load_replay")["kind"] == "load_replay"
    with pytest.raises(ValueError, match=r"^kind must be one of TURN_KINDS$"):
        build_turn("ags_1", "msg_1", kind="other")


def test_transition_builder_is_json_safe_bounded_and_isolated() -> None:
    details = {"attempt": 1, "nested": [True, None]}
    transition = build_protocol_transition(
        "session", "ags_1", "created", "ready", "session_new_completed", details,
    )
    details["nested"].append("changed")
    assert transition["details"] == {"attempt": 1, "nested": [True, None]}
    assert transition["transition_id"].startswith("pst_")
    json.dumps(transition)
    with pytest.raises(TypeError, match="details must be a dict"):
        build_protocol_transition("session", "ags_1", "created", "ready", None, [])
    with pytest.raises(ValueError, match="details must be at most"):
        build_protocol_transition("session", "ags_1", "created", "ready", None, {"x": "x" * 5000})
    with pytest.raises(ValueError, match="reason must be at most"):
        build_protocol_transition("session", "ags_1", "created", "ready", "x" * 129, {})


@pytest.mark.parametrize(("field", "value"), [
    ("entity_type", True),
    ("entity_id", True),
    ("from_state", True),
    ("to_state", True),
    ("reason", True),
    ("details", True),
])
def test_transition_builder_rejects_bool_impostors(field: str, value: object) -> None:
    values = {
        "entity_type": "session", "entity_id": "ags_1", "from_state": "created",
        "to_state": "ready", "reason": None, "details": {},
    }
    values[field] = value
    with pytest.raises((TypeError, ValueError)):
        build_protocol_transition(**values)


def test_transition_tables_use_declared_states_and_keep_terminals_terminal() -> None:
    vocabularies = {
        "session": set(AGENT_SESSION_STATES),
        "turn": set(TURN_STATES),
        "permission": set(PERMISSION_STATES),
    }
    terminals = {
        "session": {"stopped", "failed"},
        "turn": {"completed", "blocked", "failed", "ambiguous"},
        "permission": {"approved", "denied", "expired"},
    }
    for entity_type in PROTOCOL_ENTITY_TYPES:
        edges = PROTOCOL_TRANSITION_EDGES[entity_type]
        assert edges
        assert all(source in vocabularies[entity_type] for source, _ in edges)
        assert all(target in vocabularies[entity_type] for _, target in edges)
        assert not {source for source, _ in edges} & terminals[entity_type]


def test_transition_edge_tables_cover_required_lifecycle_and_terminal_states() -> None:
    allowed = [
        ("session", "created", "ready"),
        ("session", "ready", "disconnected"),
        ("session", "disconnected", "reconnecting"),
        ("session", "reconnecting", "ready"),
        ("turn", "created", "submitted"),
        ("turn", "created", "streaming"),
        ("turn", "created", "completed"),
        ("turn", "submitted", "streaming"),
        ("turn", "streaming", "waiting_permission"),
        ("turn", "waiting_permission", "streaming"),
        ("turn", "streaming", "completed"),
        ("permission", "pending", "approved"),
        ("permission", "pending", "denied"),
        ("permission", "pending", "expired"),
    ]
    for entity_type, from_state, to_state in allowed:
        build_protocol_transition(entity_type, f"{ {'session':'ags','turn':'trn','permission':'prm'}[entity_type] }_1", from_state, to_state, None, {})
    for entity_type, from_state, to_state in [
        ("turn", "completed", "streaming"),
        ("turn", "failed", "submitted"),
        ("permission", "denied", "approved"),
        ("session", "stopped", "ready"),
    ]:
        with pytest.raises(ValueError, match="invalid protocol state transition"):
            build_protocol_transition(entity_type, f"{ {'session':'ags','turn':'trn','permission':'prm'}[entity_type] }_1", from_state, to_state, None, {})


@pytest.mark.parametrize("to_state", ["approved", "denied", "expired"])
def test_permission_transition_outcomes_are_persisted_without_base_rewrite(
    tmp_path, to_state: str,
) -> None:
    store = StateStore(tmp_path)
    session = store.record_agent_session("a", "p", "acp", "native", "w", CAPABILITIES)
    turn = store.record_protocol_turn(session["session_id"], "msg")
    permission = store.record_permission_request(
        session["session_id"], turn["turn_id"], "shell", "cwd", "high"
    )
    transition = store.record_protocol_transition(
        "permission", permission["permission_id"], "pending", to_state, to_state, {}
    )
    assert transition["to_state"] == to_state
    assert store.load()["permission_requests"] == [permission]


def test_load_replay_can_become_ambiguous_before_first_update_and_stays_terminal(
    tmp_path,
) -> None:
    store = StateStore(tmp_path)
    session = store.record_agent_session("a", "p", "acp", "native", "w", CAPABILITIES)
    turn = store.record_protocol_turn(
        session["session_id"], "load-native", kind="load_replay"
    )

    transition = store.record_protocol_transition(
        "turn", turn["turn_id"], "created", "ambiguous", "eof_before_response", {}
    )

    assert transition["to_state"] == "ambiguous"
    assert store.load()["protocol_turns"] == [turn]
    before = _tree_snapshot(store)
    with pytest.raises(ValueError, match="invalid protocol state transition"):
        store.record_protocol_transition(
            "turn", turn["turn_id"], "ambiguous", "streaming", None, {}
        )
    assert _tree_snapshot(store) == before


@pytest.mark.parametrize("corruption", [
    "non_dict", "extra_field", "bad_id", "dangling", "bad_edge", "bad_chain",
    "bad_reason", "bad_details", "bad_created_at",
])
def test_corrupt_global_transition_history_blocks_valid_write_without_touching_tree(
    tmp_path, monkeypatch, corruption: str,
) -> None:
    store = StateStore(tmp_path)
    target = store.record_agent_session("target", "p", "acp", "n1", "w", CAPABILITIES)
    foreign = store.record_agent_session("foreign", "p", "acp", "n2", "w", CAPABILITIES)
    real_append_event = store.append_event
    monkeypatch.setattr(
        store, "append_event",
        lambda event: (_ for _ in ()).throw(OSError("pending")),
    )
    store.record_agent_session("pending", "p", "acp", "n3", "w", CAPABILITIES)
    monkeypatch.setattr(store, "append_event", real_append_event)
    assert len(store.load()["protocol_event_outbox"]) == 1
    persisted = build_protocol_transition(
        "session", foreign["session_id"], "created", "ready", None, {}
    )
    if corruption == "non_dict":
        corrupt = "not-a-transition"
    else:
        corrupt = dict(persisted)
        if corruption == "extra_field":
            corrupt["secret"] = "unexpected"
        elif corruption == "bad_id":
            corrupt["transition_id"] = True
        elif corruption == "dangling":
            corrupt["entity_id"] = "ags_missing"
        elif corruption == "bad_edge":
            corrupt["to_state"] = "stopped"
        elif corruption == "bad_chain":
            corrupt["from_state"] = "ready"
            corrupt["to_state"] = "busy"
        elif corruption == "bad_reason":
            corrupt["reason"] = True
        elif corruption == "bad_details":
            corrupt["details"] = []
        else:
            corrupt["created_at"] = True
    state = store.load()
    state["protocol_state_transitions"] = [corrupt]
    store.save(state)
    before = _tree_snapshot(store)

    with pytest.raises((KeyError, TypeError, ValueError)):
        store.record_protocol_transition(
            "session", target["session_id"], "created", "ready", None, {}
        )

    assert _tree_snapshot(store) == before


def test_concurrent_protocol_transition_writers_share_the_mutation_lock(tmp_path) -> None:
    store = StateStore(tmp_path)
    sessions = [
        store.record_agent_session(name, "p", "acp", name, "w", CAPABILITIES)
        for name in ("one", "two")
    ]
    barrier = threading.Barrier(3)
    records = []

    def record(session: dict[str, object]) -> None:
        barrier.wait()
        records.append(store.record_protocol_transition(
            "session", session["session_id"], "created", "ready", None, {}
        ))

    threads = [threading.Thread(target=record, args=(session,)) for session in sessions]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert len(records) == 2
    assert {item["entity_id"] for item in store.load()["protocol_state_transitions"]} == {
        session["session_id"] for session in sessions
    }


@pytest.mark.parametrize("field", ["agent_id", "provider", "workspace"])
@pytest.mark.parametrize("bad_value", ["", "   ", True, 7])
def test_session_rejects_invalid_required_strings(field: str, bad_value: object) -> None:
    values = {"agent_id": "planner", "provider": "codex", "transport": "tmux", "workspace": "/tmp/project"}
    values[field] = bad_value
    with pytest.raises(ValueError, match=rf"^{field} must be a non-empty string$"):
        build_agent_session(capabilities=CAPABILITIES, native_session_id=None, **values)


@pytest.mark.parametrize("native_session_id", ["", "  ", True, 3])
def test_session_rejects_bad_native_session_id(native_session_id: object) -> None:
    with pytest.raises(ValueError, match=r"^native_session_id must be None or a non-empty string$"):
        build_agent_session("planner", "codex", "tmux", native_session_id, "/tmp/project", CAPABILITIES)


def test_session_requires_exact_capabilities_type() -> None:
    with pytest.raises(TypeError, match=r"^capabilities must be a TransportCapabilities instance$"):
        build_agent_session("planner", "codex", "tmux", None, "/tmp/project", CAPABILITIES.summary())


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

    first_session = build_agent_session("a", "p", "tmux", None, "w", CAPABILITIES)
    second_session = build_agent_session("a", "p", "tmux", None, "w", CAPABILITIES)
    first_session["observation_bindings"].append({"kind": "terminal"})
    assert second_session["observation_bindings"] == []


def test_builders_are_pure_domain_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("external side effect")

    monkeypatch.setattr("builtins.open", forbidden)
    build_agent_session("a", "p", "tmux", None, "w", CAPABILITIES)
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
    assert state["protocol_state_transitions"] == []
    assert state["protocol_event_outbox"] == []


def test_protocol_transitions_are_append_only_and_derive_current_state(tmp_path) -> None:
    store = StateStore(tmp_path)
    session = store.record_agent_session("planner", "codex", "acp", "native-1", str(tmp_path), CAPABILITIES)
    turn = store.record_protocol_turn(session["session_id"], "msg_1", kind="load_replay")
    permission = store.record_permission_request(session["session_id"], turn["turn_id"], "shell", "cwd", "high")
    base_records = deepcopy({
        "sessions": store.load()["agent_sessions"],
        "turns": store.load()["protocol_turns"],
        "permissions": store.load()["permission_requests"],
    })

    transitions = [
        store.record_protocol_transition("session", session["session_id"], "created", "ready", "session_new_completed", {}),
        store.record_protocol_transition("session", session["session_id"], "ready", "disconnected", "clean_exit", {}),
        store.record_protocol_transition("session", session["session_id"], "disconnected", "reconnecting", "load_started", {}),
        store.record_protocol_transition("session", session["session_id"], "reconnecting", "ready", "load_completed", {}),
        store.record_protocol_transition("turn", turn["turn_id"], "created", "submitted", None, {}),
        store.record_protocol_transition("turn", turn["turn_id"], "submitted", "streaming", None, {}),
        store.record_protocol_transition("turn", turn["turn_id"], "streaming", "waiting_permission", None, {}),
        store.record_protocol_transition("permission", permission["permission_id"], "pending", "denied", "reject_once", {}),
        store.record_protocol_transition("turn", turn["turn_id"], "waiting_permission", "streaming", None, {}),
    ]

    state = store.load()
    assert state["protocol_state_transitions"] == transitions
    assert state["agent_sessions"] == base_records["sessions"]
    assert state["protocol_turns"] == base_records["turns"]
    assert state["permission_requests"] == base_records["permissions"]
    events = [json.loads(line) for line in store.events_path.read_text().splitlines()]
    assert events[-1]["event_type"] == "protocol_state_transition_recorded"
    assert events[-1]["payload"] == {
        key: transitions[-1][key]
        for key in ("transition_id", "entity_type", "entity_id", "from_state", "to_state", "reason")
    }


def test_project_view_derives_compact_current_protocol_states(tmp_path) -> None:
    store = StateStore(tmp_path)
    session = store.record_agent_session("planner", "codex", "acp", "native-secret", str(tmp_path), CAPABILITIES)
    turn = store.record_protocol_turn(session["session_id"], "msg_1")
    permission = store.record_permission_request(
        session["session_id"], turn["turn_id"], "shell", "sensitive-target", "high"
    )
    store.record_protocol_transition("session", session["session_id"], "created", "ready", None, {"credential": "secret"})
    store.record_protocol_transition("session", session["session_id"], "ready", "disconnected", "clean_exit", {})
    store.record_protocol_transition("turn", turn["turn_id"], "created", "submitted", None, {})
    store.record_protocol_transition("turn", turn["turn_id"], "submitted", "completed", "end_turn", {})
    store.record_protocol_transition("permission", permission["permission_id"], "pending", "denied", "reject_once", {"option": "secret"})

    before = _tree_snapshot(store)
    view = asdict(store.project_view(_project_config(tmp_path)))

    assert view["agent_sessions"]["items"][0]["state"] == "disconnected"
    assert view["agent_sessions"]["by_state"] == {"disconnected": 1}
    assert view["protocol_turns"]["items"][0]["state"] == "completed"
    assert view["protocol_turns"]["by_state"] == {"completed": 1}
    assert view["permission_requests"]["items"][0]["status"] == "denied"
    assert view["permission_requests"]["pending_count"] == 0
    assert view["permission_requests"]["by_status"] == {"denied": 1}
    assert view["protocol_state_transitions"]["count"] == 5
    assert all("details" not in item for item in view["protocol_state_transitions"]["items"])
    assert "secret" not in json.dumps(view["protocol_state_transitions"])
    assert _tree_snapshot(store) == before


def test_project_view_transition_window_is_latest_20_in_stable_order(tmp_path) -> None:
    store = StateStore(tmp_path)
    expected_ids = []
    for index in range(21):
        session = store.record_agent_session(
            f"agent-{index}", "codex", "acp", f"native-{index}", str(tmp_path), CAPABILITIES
        )
        transition = store.record_protocol_transition(
            "session", session["session_id"], "created", "ready", f"ready-{index}", {}
        )
        expected_ids.append(transition["transition_id"])

    summary = asdict(store.project_view(_project_config(tmp_path)))["protocol_state_transitions"]

    assert summary["count"] == 21
    assert summary["by_entity_type"] == {"session": 21}
    assert [item["transition_id"] for item in summary["items"]] == expected_ids[-20:]


def test_project_view_validates_corrupt_transition_outside_latest_window(tmp_path) -> None:
    store = StateStore(tmp_path)
    for index in range(21):
        session = store.record_agent_session(
            f"agent-{index}", "codex", "acp", f"native-{index}", str(tmp_path), CAPABILITIES
        )
        store.record_protocol_transition("session", session["session_id"], "created", "ready", None, {})
    state = store.load()
    state["protocol_state_transitions"][0]["from_state"] = "connecting"
    store.save(state)
    before = _tree_snapshot(store)

    with pytest.raises(ValueError, match="stale protocol transition from_state"):
        store.project_view(_project_config(tmp_path))

    assert _tree_snapshot(store) == before


def test_transition_history_validation_builds_entity_indexes_once(tmp_path, monkeypatch) -> None:
    store = StateStore(tmp_path)
    for index in range(100):
        session = store.record_agent_session(
            f"agent-{index}", "codex", "acp", f"native-{index}", str(tmp_path), CAPABILITIES
        )
        store.record_protocol_transition("session", session["session_id"], "created", "ready", None, {})

    lookup_calls = 0
    original = StateStore._protocol_transition_entity

    def counted_lookup(*args, **kwargs):
        nonlocal lookup_calls
        lookup_calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(StateStore, "_protocol_transition_entity", counted_lookup)
    StateStore._validate_protocol_transition_history(store.load())

    assert lookup_calls == 0


@pytest.mark.parametrize("rejection", ["unknown", "stale", "illegal", "duplicate"])
def test_protocol_transition_rejections_are_tree_zero_write_with_pending_outbox(
    tmp_path, monkeypatch, rejection: str,
) -> None:
    store = StateStore(tmp_path)
    real_append_event = store.append_event
    monkeypatch.setattr(store, "append_event", lambda event: (_ for _ in ()).throw(OSError("pending")))
    session = store.record_agent_session("a", "p", "acp", "native", "w", CAPABILITIES)
    monkeypatch.setattr(store, "append_event", real_append_event)
    assert len(store.load()["protocol_event_outbox"]) == 1
    existing = build_protocol_transition("session", session["session_id"], "created", "ready", None, {})
    if rejection == "duplicate":
        state = store.load()
        state["protocol_state_transitions"] = [existing]
        store.save(state)
        real_builder = build_protocol_transition
        def colliding_builder(*args, **kwargs):
            candidate = real_builder(*args, **kwargs)
            candidate["transition_id"] = existing["transition_id"]
            return candidate
        monkeypatch.setattr("agentdeck.state.build_protocol_transition", colliding_builder)
    before = _tree_snapshot(store)
    with pytest.raises((KeyError, ValueError)):
        if rejection == "unknown":
            store.record_protocol_transition("session", "ags_unknown", "created", "ready", None, {})
        elif rejection == "stale":
            store.record_protocol_transition("session", session["session_id"], "ready", "busy", None, {})
        elif rejection == "illegal":
            store.record_protocol_transition("session", session["session_id"], "created", "stopped", None, {})
        else:
            store.record_protocol_transition("session", session["session_id"], "ready", "disconnected", None, {})
    assert _tree_snapshot(store) == before


def test_state_store_records_complete_protocol_lineage_and_redacted_events(tmp_path) -> None:
    store = StateStore(tmp_path)
    session = store.record_agent_session("planner", "codex", "tmux", None, "/tmp/project", CAPABILITIES)
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
    assert events[0]["payload"] == {"session_id": session["session_id"], "agent_id": "planner", "transport": "tmux"}
    assert events[1]["payload"] == {"turn_id": turn["turn_id"], "session_id": session["session_id"], "message_id": "msg_123"}
    assert events[2]["payload"] == {"update_id": update["update_id"], "session_id": session["session_id"], "turn_id": turn["turn_id"], "sequence": 0, "kind": "tool_call"}
    assert events[3]["payload"] == {"permission_id": permission["permission_id"], "session_id": session["session_id"], "turn_id": turn["turn_id"], "tool_name": "write_file", "risk": "high"}
    assert "/secret" not in store.events_path.read_text()


def test_protocol_state_methods_support_old_state_and_lists_are_independent(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.save({"agents": {}})
    session = store.record_agent_session("planner", "codex", "tmux", None, "/tmp/project", CAPABILITIES)
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
    first = store.record_agent_session("a", "p", "tmux", None, "w", CAPABILITIES)
    second = store.record_agent_session("b", "p", "tmux", None, "w", CAPABILITIES)
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
    session = build_agent_session("a", "p", "tmux", None, "w", CAPABILITIES)
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
        store.record_agent_session("", "p", "tmux", None, "w", CAPABILITIES)
    assert _disk_snapshot(store) == before

    def fail_save(state):
        raise OSError("save failed")

    monkeypatch.setattr(store, "save", fail_save)
    before_save_failure = _disk_snapshot(store)
    with pytest.raises(OSError, match="save failed"):
        store.record_agent_session("a", "p", "tmux", None, "w", CAPABILITIES)
    assert _disk_snapshot(store) == before_save_failure


def test_event_failure_returns_record_with_durable_outbox_and_flushes_once(tmp_path, monkeypatch) -> None:
    store = StateStore(tmp_path)
    real_append_event = store.append_event

    def fail_event(event):
        raise OSError("event failed")

    monkeypatch.setattr(store, "append_event", fail_event)
    record = store.record_agent_session("a", "p", "tmux", None, "w", CAPABILITIES)
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
    record = store.record_agent_session("a", "p", "tmux", None, "w", CAPABILITIES)
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
    store.record_agent_session("a", "p", "tmux", None, "w", CAPABILITIES)
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
        records.append(store.record_agent_session(agent_id, "p", "tmux", None, "w", CAPABILITIES))

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
    session = store.record_agent_session("a", "p", "tmux", None, "w", CAPABILITIES)
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
            store.record_agent_session("b", "p", "tmux", None, "w", CAPABILITIES)
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
    existing = store.record_agent_session("a", "p", "tmux", None, "w", CAPABILITIES)
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
            store.record_agent_session("", "p", "tmux", None, "w", CAPABILITIES)
        elif rejection == "unknown":
            store.record_protocol_turn("ags_unknown", "msg")
        else:
            store.record_agent_session("b", "p", "tmux", None, "w", CAPABILITIES)
    assert _tree_snapshot(store) == before


def test_acp_permission_pending_is_one_atomic_mutation_with_exact_bounds(
    tmp_path, monkeypatch,
) -> None:
    store = StateStore(tmp_path)
    session = store.record_agent_session("a", "p", "acp-adapter", "native", "w", CAPABILITIES)
    turn = store.record_protocol_turn(session["session_id"], "msg")
    store.record_protocol_transition("turn", turn["turn_id"], "created", "submitted", None, {})
    payload = {"permission_id": "prm_" + "x" * 12, "tool_call_id": "call-1", "risk": "high"}
    encoded = len(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    monkeypatch.setattr("agentdeck.state.MAX_ACP_TURN_PAYLOAD_BYTES", encoded + 512)
    monkeypatch.setattr("agentdeck.state.MAX_ACP_UPDATES_PER_TURN", 2)

    result = store.record_acp_permission_pending(
        session["session_id"], turn["turn_id"], 0,
        tool_name="Edit", target="notes.txt", risk="high",
        tool_call_id="call-1",
    )

    state = store.load()
    assert result["permission"] in state["permission_requests"]
    assert result["update"] in state["transport_updates"]
    assert result["transition"] in state["protocol_state_transitions"]
    assert result["update"]["payload"]["permission_id"] == result["permission"]["permission_id"]


@pytest.mark.parametrize("overflow", ["bytes", "count"])
def test_acp_permission_bound_failure_is_full_tree_zero_write_with_pending_outbox(
    tmp_path, monkeypatch, overflow: str,
) -> None:
    store = StateStore(tmp_path)
    session = store.record_agent_session("a", "p", "acp-adapter", "native", "w", CAPABILITIES)
    turn = store.record_protocol_turn(session["session_id"], "msg")
    store.record_protocol_transition("turn", turn["turn_id"], "created", "submitted", None, {})
    real_append_event = store.append_event
    monkeypatch.setattr(store, "append_event", lambda _event: (_ for _ in ()).throw(OSError("pending")))
    store.record_transport_update(session["session_id"], turn["turn_id"], 0, "text", {"x": "y"})
    monkeypatch.setattr(store, "append_event", real_append_event)
    assert store.load()["protocol_event_outbox"]
    monkeypatch.setattr("agentdeck.state.MAX_ACP_TURN_PAYLOAD_BYTES", 1 if overflow == "bytes" else 10_000)
    monkeypatch.setattr("agentdeck.state.MAX_ACP_UPDATES_PER_TURN", 1 if overflow == "count" else 10)
    before = _tree_snapshot(store)

    with pytest.raises(ValueError, match="ACP turn"):
        store.record_acp_permission_pending(
            session["session_id"], turn["turn_id"], 1,
            tool_name="Edit", target="notes.txt", risk="high", tool_call_id="call-1",
        )

    assert _tree_snapshot(store) == before
