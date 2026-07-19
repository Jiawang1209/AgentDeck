from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3

import pytest

from agentdeck.adapters.sqlite import (
    SQLiteStore,
    StoreCommandConflictError,
    StoreCommandStateError,
    StoreSchemaError,
    StoreSerializationError,
    StoreWriterBusyError,
)
from agentdeck.kernel.events import DomainEvent

from .fakes import FrozenClock


NOW = datetime(2026, 7, 19, 1, 2, 3, tzinfo=timezone.utc)


def _open(root: Path) -> SQLiteStore:
    return SQLiteStore.open(root, clock=FrozenClock(NOW))


def _event(event_id: str = "evt_1") -> DomainEvent:
    return DomainEvent(
        event_id=event_id,
        kind="session_saved",
        aggregate_type="product_session",
        aggregate_id="ses_1",
        payload=(("state", "running"),),
        occurred_at="2000-01-01T00:00:00+00:00",
    )


def test_state_command_and_event_rollback_together(tmp_path: Path) -> None:
    store = _open(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="stop"):
            with store.command("cmd_1", "confirm") as transaction:
                transaction.save_session({"session_id": "ses_1", "state": "setup"})
                transaction.append_event(_event())
                raise RuntimeError("stop")
        assert store.count("product_sessions") == 0
        assert store.count("events") == 0
        assert store.count("commands") == 0
        assert not store._writer.in_transaction
    finally:
        store.close()


def test_execute_once_commits_state_event_result_and_clock_together(tmp_path: Path) -> None:
    store = _open(tmp_path)
    try:
        def command(transaction):
            transaction.save_session({"session_id": "ses_1", "state": "setup"})
            transaction.append_event(_event())
            return {"mission_id": "mis_1", "ordered": {"b": 2, "a": 1}}

        result = store.execute_once("cmd_1", "confirm", command)

        assert result == {"mission_id": "mis_1", "ordered": {"a": 1, "b": 2}}
        assert store.count("product_sessions") == store.count("events") == 1
        row = store.connection.execute(
            "SELECT state, canonical_result_facts, created_at, completed_at FROM commands"
        ).fetchone()
        assert row == (
            "completed",
            '{"mission_id":"mis_1","ordered":{"a":1,"b":2}}',
            "2026-07-19T01:02:03+00:00",
            "2026-07-19T01:02:03+00:00",
        )
        assert store.connection.execute(
            "SELECT occurred_at FROM events"
        ).fetchone() == ("2000-01-01T00:00:00+00:00",)
        assert not store._writer.in_transaction
    finally:
        store.close()


def test_command_bound_aggregate_save_and_load_are_defensive(tmp_path: Path) -> None:
    store = _open(tmp_path)
    try:
        def command(transaction):
            snapshot = {"session_id": "ses_1", "state": "setup"}
            transaction.save_aggregate("product_sessions", "ses_1", snapshot)
            snapshot["state"] = "failed"
            return {"saved": transaction.load_aggregate("product_sessions", "ses_1")}

        result = store.execute_once("cmd_aggregate", "save_session", command)

        assert result["saved"]["state"] == "setup"
        loaded = store.load_aggregate("product_sessions", "ses_1")
        loaded["state"] = "failed"
        assert store.load_aggregate("product_sessions", "ses_1")["state"] == "setup"
    finally:
        store.close()


def test_duplicate_command_returns_defensive_first_result_without_callback(
    tmp_path: Path,
) -> None:
    store = _open(tmp_path)
    calls = 0
    try:
        first_source = {"nested": {"items": [1, 2]}}

        def first(transaction):
            nonlocal calls
            calls += 1
            return first_source

        first_result = store.execute_once("cmd_1", "confirm", first)
        first_source["nested"]["items"].append(3)
        first_result["nested"]["items"].append(4)

        def duplicate(transaction):
            nonlocal calls
            calls += 1
            return {"never": "used"}

        second_result = store.execute_once("cmd_1", "confirm", duplicate)
        second_result["nested"]["items"].append(5)
        third_result = store.execute_once("cmd_1", "confirm", duplicate)

        assert calls == 1
        assert third_result == {"nested": {"items": [1, 2]}}
        assert store.count("commands") == 1
    finally:
        store.close()


def test_duplicate_command_kind_conflict_and_malformed_rows_fail_closed(
    tmp_path: Path,
) -> None:
    store = _open(tmp_path)
    try:
        assert store.execute_once("cmd_1", "confirm", lambda transaction: {"ok": True}) == {
            "ok": True
        }
        with pytest.raises(StoreCommandConflictError, match="kind"):
            store.execute_once("cmd_1", "dispatch", lambda transaction: {"bad": True})

        store._writer.execute(
            "INSERT INTO commands VALUES (?, ?, ?, ?, ?, ?)",
            ("cmd_bad", "confirm", "started", None, "2026-07-19T01:02:03+00:00", None),
        )
        with pytest.raises(StoreCommandStateError, match="incomplete"):
            store.execute_once("cmd_bad", "confirm", lambda transaction: {"bad": True})
    finally:
        store.close()


def test_callback_exception_rolls_back_and_same_command_can_retry(tmp_path: Path) -> None:
    store = _open(tmp_path)
    calls = 0
    try:
        def failing(transaction):
            nonlocal calls
            calls += 1
            transaction.save_session({"session_id": "ses_1", "state": "setup"})
            raise RuntimeError("retry me")

        with pytest.raises(RuntimeError, match="retry me"):
            store.execute_once("cmd_1", "confirm", failing)
        assert store.count("commands") == store.count("product_sessions") == 0

        result = store.execute_once("cmd_1", "confirm", lambda transaction: {"ok": True})
        assert result == {"ok": True}
        assert calls == 1
        assert store.count("commands") == 1
    finally:
        store.close()


def test_nested_command_and_close_during_transaction_are_rejected(tmp_path: Path) -> None:
    store = _open(tmp_path)
    try:
        with store.command("cmd_1", "confirm") as transaction:
            with pytest.raises(StoreCommandStateError, match="active"):
                store.execute_once("cmd_2", "dispatch", lambda nested: {})
            with pytest.raises(StoreCommandStateError, match="active"):
                store.close()
            transaction.save_session({"session_id": "ses_1", "state": "setup"})

        with pytest.raises(StoreCommandStateError, match="inactive"):
            transaction.save_session({"session_id": "ses_2", "state": "setup"})
    finally:
        store.close()


def test_duplicate_context_is_read_only_and_exposes_first_result(tmp_path: Path) -> None:
    store = _open(tmp_path)
    try:
        store.execute_once("cmd_1", "confirm", lambda transaction: {"ok": True})
        with store.command("cmd_1", "confirm") as duplicate:
            assert duplicate.duplicate_result == {"ok": True}
            with pytest.raises(StoreCommandStateError, match="duplicate"):
                duplicate.append_event(_event("evt_never"))
        assert store.count("events") == 0
    finally:
        store.close()


@pytest.mark.parametrize(
    "bad_result",
    [
        {"too_large": 2**63},
        {"not_finite": float("nan")},
        {"surrogate": "\ud800"},
        {"raw": b"protocol frame"},
        {"raw_protocol": "unsanitized frame"},
        {1: "non-string key"},
        {"deep": [[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]},
        {"huge": "x" * 300_000},
    ],
)
def test_command_result_rejects_noncanonical_or_unbounded_values(
    tmp_path: Path, bad_result: object
) -> None:
    store = _open(tmp_path)
    try:
        with pytest.raises((StoreSerializationError, TypeError, ValueError)):
            store.execute_once("cmd_bad", "confirm", lambda transaction: bad_result)
        assert store.count("commands") == 0
        assert not store._writer.in_transaction
    finally:
        store.close()


def test_second_writer_same_project_fails_promptly_and_lock_releases(tmp_path: Path) -> None:
    first = _open(tmp_path)
    try:
        with pytest.raises(StoreWriterBusyError, match="writer"):
            _open(tmp_path)
        inspector = SQLiteStore.open_read_only(tmp_path)
        try:
            assert inspector.count("commands") == 0
        finally:
            inspector.close()
    finally:
        first.close()

    reopened = _open(tmp_path)
    try:
        assert reopened.connection.execute(
            "SELECT singleton, schema_version, project_root FROM schema_metadata"
        ).fetchall() == [(1, 2, str(tmp_path.resolve()))]
        assert reopened.connection.execute(
            "SELECT count(*) FROM schema_metadata"
        ).fetchone() == (1,)
    finally:
        reopened.close()


def test_different_projects_can_hold_writer_locks(tmp_path: Path) -> None:
    first_root, second_root = tmp_path / "first", tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = _open(first_root)
    second = _open(second_root)
    second.close()
    first.close()


@pytest.mark.parametrize("kind", ["symlink", "directory", "hardlink"])
def test_writer_lock_path_must_be_protected_regular_sole_link(
    tmp_path: Path, kind: str
) -> None:
    state = tmp_path / ".agentdeck"
    state.mkdir()
    lock = state / "writer.lock"
    outside = tmp_path / "outside"
    if kind == "symlink":
        outside.write_text("outside", encoding="utf-8")
        lock.symlink_to(outside)
    elif kind == "directory":
        lock.mkdir()
    else:
        outside.write_text("outside", encoding="utf-8")
        os.link(outside, lock)

    with pytest.raises((ValueError, StoreSchemaError), match="lock|regular|link"):
        _open(tmp_path)
    assert not (state / "agentdeck.db").exists()


def test_read_only_open_does_not_take_writer_lock_or_allow_mutation(tmp_path: Path) -> None:
    writer = _open(tmp_path)
    writer.execute_once("cmd_1", "inspectable", lambda transaction: {"ok": True})
    lock = tmp_path / ".agentdeck" / "writer.lock"
    before = lock.stat()
    reader = SQLiteStore.open_read_only(tmp_path)
    try:
        assert reader.lookup_command("cmd_1") == {"ok": True}
        assert reader.count("commands") == 1
        assert reader.connection.execute("PRAGMA query_only").fetchone() == (1,)
        with pytest.raises(StoreCommandStateError, match="read-only"):
            reader.execute_once("cmd_2", "mutate", lambda transaction: {})
        with pytest.raises(StoreCommandStateError, match="read-only"):
            with reader.command("cmd_2", "mutate"):
                pass
        assert (lock.stat().st_dev, lock.stat().st_ino) == (before.st_dev, before.st_ino)
    finally:
        reader.close()
        writer.close()


def test_write_boundary_rechecks_database_identity(tmp_path: Path) -> None:
    store = _open(tmp_path)
    replacement = store.path.with_name("replacement.db")
    replacement.write_bytes(store.path.read_bytes())
    os.replace(replacement, store.path)
    try:
        with pytest.raises(StoreSchemaError, match="identity"):
            store.execute_once("cmd_1", "confirm", lambda transaction: {"bad": True})
        assert not store._writer.in_transaction
    finally:
        store.close()


def test_count_uses_strict_authority_table_allowlist(tmp_path: Path) -> None:
    store = _open(tmp_path)
    try:
        assert store.count("commands") == 0
        with pytest.raises(ValueError, match="authority table"):
            store.count("commands; DROP TABLE commands")
        with pytest.raises(ValueError, match="authority table"):
            store.count("sqlite_schema")
        assert store.connection.execute(
            "SELECT name FROM sqlite_schema WHERE name='commands'"
        ).fetchone() == ("commands",)
    finally:
        store.close()


def test_post_commit_identity_change_is_reported_without_open_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agentdeck.adapters.sqlite as adapter

    store = _open(tmp_path)
    original = adapter._after_command_commit

    def replace(path: Path) -> None:
        spare = path.with_name("spare.db")
        spare.write_bytes(path.read_bytes())
        os.replace(spare, path)

    monkeypatch.setattr(adapter, "_after_command_commit", replace, raising=False)
    try:
        with pytest.raises(StoreSchemaError, match="identity"):
            store.execute_once("cmd_1", "confirm", lambda transaction: {"ok": True})
        assert not store._writer.in_transaction
    finally:
        monkeypatch.setattr(adapter, "_after_command_commit", original, raising=False)
        store.close()
