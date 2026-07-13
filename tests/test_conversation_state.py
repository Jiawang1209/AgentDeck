from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path

import pytest

from agentdeck.config import load_config, write_default_config
from agentdeck.contracts import validate_project_view_contract
from agentdeck.conversation.models import (
    ConversationMutation,
    build_conversation_record,
    build_conversation_transition,
    build_turn_record,
)
from agentdeck.models import EventRecord
from agentdeck.state import StateStore


CREATED_AT = "2026-07-13T00:00:00+00:00"


def _store(tmp_path: Path) -> StateStore:
    write_default_config(tmp_path)
    return StateStore(tmp_path)


def _conversation_mutation(*, event_id: str = "evt_conversation") -> ConversationMutation:
    session = build_conversation_record("c1", created_at=CREATED_AT)
    turn = build_turn_record("t1", conversation_id="c1", created_at=CREATED_AT)
    transitions = (
        build_conversation_transition(
            transition_id="x1",
            conversation_id="c1",
            entity_type="conversation",
            entity_id="c1",
            from_state=None,
            to_state="created",
            reason="started",
            created_at=CREATED_AT,
        ),
        build_conversation_transition(
            transition_id="x2",
            conversation_id="c1",
            entity_type="conversation",
            entity_id="c1",
            from_state="created",
            to_state="ready",
            reason="ready",
            created_at=CREATED_AT,
        ),
        build_conversation_transition(
            transition_id="x3",
            conversation_id="c1",
            entity_type="turn",
            entity_id="t1",
            from_state=None,
            to_state="created",
            reason="received",
            created_at=CREATED_AT,
        ),
    )
    event = EventRecord(
        event_id=event_id,
        event_type="conversation_started",
        created_at=CREATED_AT,
        payload={"conversation_id": "c1", "turn_id": "t1"},
    )
    return ConversationMutation(
        append_records={
            "conversation_sessions": (session,),
            "conversation_turns": (turn,),
            "conversation_state_transitions": transitions,
        },
        events=(event,),
    )


def test_new_state_contains_empty_conversation_collections(tmp_path: Path) -> None:
    state = _store(tmp_path).load()

    assert state["conversation_sessions"] == []
    assert state["conversation_turns"] == []
    assert state["conversation_preview_bindings"] == []
    assert state["conversation_state_transitions"] == []
    assert state["conversation_event_outbox"] == []


def test_conversation_mutation_commits_records_and_flushes_event(tmp_path: Path) -> None:
    store = _store(tmp_path)

    result = store.commit_conversation_mutation(_conversation_mutation())

    state = store.load()
    assert result == {"committed": True, "events_flushed": 1, "outbox_blocked": False}
    assert [item["conversation_id"] for item in state["conversation_sessions"]] == ["c1"]
    assert [item["turn_id"] for item in state["conversation_turns"]] == ["t1"]
    assert len(state["conversation_state_transitions"]) == 3
    assert state["conversation_event_outbox"] == []
    events = [line for line in store.events_path.read_text(encoding="utf-8").splitlines() if line]
    assert len(events) == 1
    assert "evt_conversation" in events[0]


def test_invalid_conversation_mutation_is_full_tree_zero_write(tmp_path: Path) -> None:
    store = _store(tmp_path)
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    invalid = replace(
        _conversation_mutation(),
        append_records={
            **_conversation_mutation().append_records,
            "conversation_state_transitions": (
                build_conversation_transition(
                    transition_id="bad",
                    conversation_id="c1",
                    entity_type="conversation",
                    entity_id="c1",
                    from_state=None,
                    to_state="closed",
                    reason="invalid",
                    created_at=CREATED_AT,
                ),
            ),
        },
    )

    with pytest.raises(ValueError, match="illegal initial conversation transition"):
        store.commit_conversation_mutation(invalid)

    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_event_flush_failure_preserves_outbox_and_does_not_repeat_domain_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    real_append = store.append_event
    monkeypatch.setattr(store, "append_event", lambda _event: (_ for _ in ()).throw(OSError("disk")))

    result = store.commit_conversation_mutation(_conversation_mutation())

    state = store.load()
    assert result == {"committed": True, "events_flushed": 0, "outbox_blocked": True}
    assert len(state["conversation_sessions"]) == 1
    assert [item["event_id"] for item in state["conversation_event_outbox"]] == [
        "evt_conversation"
    ]

    monkeypatch.setattr(store, "append_event", real_append)
    assert store.flush_conversation_event_outbox() == 1
    assert store.flush_conversation_event_outbox() == 0
    assert len(store.load()["conversation_sessions"]) == 1
    assert store.load()["conversation_event_outbox"] == []


def test_existing_event_id_is_removed_from_outbox_without_duplicate_append(tmp_path: Path) -> None:
    store = _store(tmp_path)
    event = _conversation_mutation().events[0]
    store.append_event(event)
    state = store.load()
    state["conversation_event_outbox"] = [asdict(event)]
    store.save(state)
    before = store.events_path.read_bytes()

    assert store.flush_conversation_event_outbox() == 0
    assert store.events_path.read_bytes() == before
    assert store.load()["conversation_event_outbox"] == []


def test_mutation_rejects_event_identity_already_present_in_ledger(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.append_event(_conversation_mutation().events[0])
    before = store.state_path.read_bytes() if store.state_path.exists() else None

    with pytest.raises(ValueError, match="duplicate conversation event identity"):
        store.commit_conversation_mutation(_conversation_mutation())

    after = store.state_path.read_bytes() if store.state_path.exists() else None
    assert after == before
    assert store.load()["conversation_sessions"] == []


def test_project_view_exposes_compact_conversation_summary(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.commit_conversation_mutation(_conversation_mutation())
    view = store.project_view(load_config(tmp_path))

    assert view.conversation == {
        "session_count": 1,
        "turn_count": 1,
        "preview_count": 0,
        "transition_count": 3,
        "latest_conversation_id": "c1",
        "latest_conversation_state": "ready",
        "latest_turn_id": "t1",
        "latest_turn_state": "created",
        "pending_preview": None,
        "ownership": [],
        "outbox_count": 0,
        "blockers": [],
    }
    serialized = str(asdict(view))
    assert "User request" not in serialized
    assert "raw_prompt" not in serialized


def test_project_view_contract_requires_compact_conversation_fields(tmp_path: Path) -> None:
    store = _store(tmp_path)
    payload = asdict(store.project_view(load_config(tmp_path)))
    payload["conversation"].pop("outbox_count")

    validation = validate_project_view_contract(payload)

    assert validation == {
        "ok": False,
        "errors": ["missing conversation field: outbox_count"],
    }
