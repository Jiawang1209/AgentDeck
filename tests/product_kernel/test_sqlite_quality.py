from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from types import MappingProxyType

import pytest

from agentdeck.adapters.sqlite import (
    SQLiteStore,
    StoreCommandStateError,
    StoreSerializationError,
    StoreWriterBusyError,
)
from agentdeck.kernel.events import DomainEvent

from .fakes import FrozenClock


NOW = datetime(2026, 7, 19, 8, 9, 10, tzinfo=timezone.utc)
NOW_TEXT = "2026-07-19T08:09:10+00:00"


def _open(root: Path) -> SQLiteStore:
    return SQLiteStore.open(root, clock=FrozenClock(NOW))


def _seed_task(store: SQLiteStore) -> None:
    with store.command("cmd_seed_lineage", "test_fixture") as transaction:
        connection = store._writer
        connection.execute(
            "INSERT INTO projects VALUES (?, ?, ?)",
            (store._project_id, str(store._project_root), NOW_TEXT),
        )
        connection.execute(
            """INSERT INTO product_sessions (
                   session_id,project_id,state,permission_profile,pending_goal,
                   created_at,updated_at,leader_backend,leader_model
               ) VALUES ('ses_1',?,'running','approve_for_me',NULL,?,?,?,?)""",
            (store._project_id, NOW_TEXT, NOW_TEXT, "codex-cli", "native-default"),
        )
        connection.execute(
            "INSERT INTO agent_instances VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("agt_1", "ses_1", "codex", "acp", "1", "implementer", "acp_1",
             "active", NOW_TEXT, NOW_TEXT),
        )
        connection.execute(
            "INSERT INTO missions VALUES (?,?,?,?,?,?)",
            ("mis_1", "ses_1", "running", 1, NOW_TEXT, NOW_TEXT),
        )
        connection.execute(
            "INSERT INTO mission_versions VALUES (?,?,?,?,?,?)",
            ("mis_1", 1, "prv_1", "0" * 64, "{}", NOW_TEXT),
        )
        connection.execute(
            "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("tsk_1", "mis_1", 1, 1, "implement", "implementer", "codex",
             "agt_1", "acp://codex", "running", "{}", NOW_TEXT, NOW_TEXT),
        )


def _attempt(**changes: object) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "attempt_id": "att_1",
        "task_id": "tsk_1",
        "agent_instance_id": "agt_1",
        "ordinal": 1,
        "state": "running",
        "retryable": False,
        "acp_session_id": "acp_1",
        "effect_observed": False,
    }
    snapshot.update(changes)
    return snapshot


def _save_attempt(store: SQLiteStore, command_id: str, snapshot) -> None:
    store.execute_once(
        command_id, "save_attempt", lambda transaction: (
            transaction.save_attempt(snapshot) or {"saved": True}
        ),
    )


def test_lock_file_inode_replacement_cannot_create_second_writer(tmp_path: Path) -> None:
    first = _open(tmp_path)
    lock = tmp_path / ".agentdeck" / "writer.lock"
    second = None
    try:
        lock.unlink()
        lock.write_text("replacement", encoding="utf-8")
        os.chmod(lock, 0o600)
        with pytest.raises(StoreWriterBusyError, match="writer"):
            second = _open(tmp_path)
        assert first.execute_once(
            "cmd_first", "still_owner", lambda transaction: {"ok": True}
        ) == {"ok": True}
    finally:
        if second is not None:
            second.close()
        first.close()


def test_state_directory_replacement_fails_existing_writer_closed(tmp_path: Path) -> None:
    store = _open(tmp_path)
    state = tmp_path / ".agentdeck"
    moved = tmp_path / "moved-agentdeck"
    state.rename(moved)
    state.mkdir()
    try:
        with pytest.raises((StoreCommandStateError, RuntimeError), match="state|identity"):
            store.execute_once("cmd_never", "mutate", lambda transaction: {"bad": True})
    finally:
        store.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"retryable": "false"},
        {"effect_observed": "false"},
        {"attempt_id": "bad"},
        {"attempt_id": "att_" + "x" * 300},
        {"task_id": "bad"},
        {"task_id": "tsk_" + "x" * 300},
        {"ordinal": 0},
        {"state": "unknown"},
        {"state": "completed"},
        {"state": "running", "result_summary": "forged"},
        {"state": "interrupted", "reason": "lost", "retryable": True},
    ],
)
def test_save_attempt_rejects_noncanonical_kernel_facts(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    store = _open(tmp_path)
    _seed_task(store)
    try:
        with pytest.raises((TypeError, ValueError)):
            _save_attempt(store, "cmd_bad_attempt", _attempt(**changes))
        assert store.count("attempts") == 0
        assert store.lookup_command("cmd_bad_attempt") is None
    finally:
        store.close()


def test_completed_attempt_requires_bounded_nonempty_summary(tmp_path: Path) -> None:
    store = _open(tmp_path)
    _seed_task(store)
    try:
        _save_attempt(store, "cmd_start", _attempt())
        with pytest.raises((TypeError, ValueError)):
            _save_attempt(store, "cmd_empty", _attempt(
                state="completed", result_summary=" "
            ))
        with pytest.raises((TypeError, ValueError)):
            _save_attempt(store, "cmd_huge", _attempt(
                state="completed", result_summary="x" * 70_000
            ))
        _save_attempt(store, "cmd_complete", _attempt(
            state="completed", result_summary="verified"
        ))
        assert store.load_aggregate("attempts", "att_1")["state"] == "completed"
    finally:
        store.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"task_id": "tsk_other"},
        {"agent_instance_id": "agt_other"},
        {"ordinal": 2},
        {"acp_session_id": "acp_other"},
        {"created_at": "2027-01-01T00:00:00+00:00"},
    ],
)
def test_existing_attempt_immutable_lineage_cannot_drift(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    store = _open(tmp_path)
    _seed_task(store)
    _save_attempt(store, "cmd_start", _attempt())
    try:
        with pytest.raises((TypeError, ValueError), match="immutable|lineage"):
            _save_attempt(store, "cmd_drift", _attempt(**changes))
        persisted = store.load_aggregate("attempts", "att_1")
        assert persisted["task_id"] == "tsk_1"
        assert persisted["ordinal"] == 1
        assert store.lookup_command("cmd_drift") is None
    finally:
        store.close()


def test_effect_observed_is_exact_bool_and_monotonic(tmp_path: Path) -> None:
    store = _open(tmp_path)
    _seed_task(store)
    _save_attempt(store, "cmd_seen", _attempt(effect_observed=True))
    try:
        with pytest.raises((TypeError, ValueError), match="effect_observed"):
            _save_attempt(store, "cmd_unsee", _attempt(effect_observed=False))
        assert store.load_aggregate("attempts", "att_1")["effect_observed"] == 1
    finally:
        store.close()


@pytest.mark.parametrize(
    "payload",
    [
        {"token": "x"},
        {"nested": {"apiKey": "x"}},
        {"nested": [{"accessToken": "x"}]},
        {"refreshToken": "x"},
        {"authorization": "Bearer x"},
        {"bearer": "x"},
        {"userPassword": "x"},
        {"clientSecret": "x"},
        {"sessionCookie": "x"},
        {"credential": "x"},
        {"openai_api_key": "x"},
        {"DEEPSEEK_API_KEY": "x"},
        {"auth_token": "x"},
        {"sessionToken": "x"},
        {"authorization_header": "x"},
        {"raw_protocol_frame": "x"},
        {"terminal_output_chunk": "x"},
    ],
)
def test_recursive_credential_aliases_rollback_without_command(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    store = _open(tmp_path)
    try:
        with pytest.raises(StoreSerializationError, match="secret|credential|prohibited"):
            store.execute_once("cmd_secret", "persist", lambda transaction: payload)
        assert store.count("commands") == 0
    finally:
        store.close()


def test_audit_provenance_keys_are_not_false_positive_secrets(tmp_path: Path) -> None:
    store = _open(tmp_path)
    try:
        result = store.execute_once("cmd_audit", "persist", lambda transaction: {
            "authorization_digest": "a" * 64,
            "content_hash": "b" * 64,
            "credential_source_name": "keychain",
        })
        assert result["credential_source_name"] == "keychain"
    finally:
        store.close()


@pytest.mark.parametrize("payload", [
    {"access_token_hash": "RAW_ACCESS_TOKEN"},
    {"authorization_digest": "Bearer RAW_SECRET"},
])
def test_provenance_shaped_secret_bypasses_are_rejected(
    tmp_path: Path, payload: dict[str, str]
) -> None:
    store = _open(tmp_path)
    try:
        with pytest.raises(StoreSerializationError):
            store.execute_once("cmd_bypass", "persist", lambda transaction: payload)
        assert store.count("commands") == 0
    finally:
        store.close()


@pytest.mark.parametrize("key,value", [
    ("authorization_digest", "A" * 64),
    ("content_hash", "not-a-digest"),
    ("credential_source_name", "Bearer RAW_SECRET"),
    ("token_count", "RAW_SECRET"),
])
def test_malformed_provenance_values_are_rejected(
    tmp_path: Path, key: str, value: str
) -> None:
    store = _open(tmp_path)
    try:
        with pytest.raises(StoreSerializationError):
            store.execute_once("cmd_bad_provenance", "persist", lambda transaction: {
                key: value,
            })
        assert store.count("commands") == 0
    finally:
        store.close()


def test_nonsensitive_token_count_and_exact_provenance_formats_are_accepted(
    tmp_path: Path,
) -> None:
    store = _open(tmp_path)
    try:
        result = store.execute_once("cmd_provenance", "persist", lambda transaction: {
            "token_count": 42,
            "authorization_digest": "a" * 64,
            "content_hash": "b" * 64,
            "credential_source_name": "DEEPSEEK_API_KEY",
            "monkey": "keyboard",
        })
        assert result["token_count"] == 42
    finally:
        store.close()


def test_store_reads_its_uncommitted_command_then_rollback_hides_it(tmp_path: Path) -> None:
    store = _open(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="rollback"):
            with store.command("cmd_read_own_write", "save") as transaction:
                transaction.save_session({"session_id": "ses_live", "state": "setup"})
                assert store.count("product_sessions") == 1
                assert store.load_aggregate(
                    "product_sessions", "ses_live"
                )["state"] == "setup"
                raise RuntimeError("rollback")
        assert store.count("product_sessions") == 0
        assert store.load_aggregate("product_sessions", "ses_live") is None
    finally:
        store.close()


def test_one_megabyte_event_identity_rolls_back_everything(tmp_path: Path) -> None:
    store = _open(tmp_path)
    event = DomainEvent(
        event_id="e" * 1_000_000,
        kind="bounded",
        aggregate_type="attempt",
        aggregate_id="att_1",
        payload=(),
        occurred_at="2000-01-01T00:00:00+00:00",
    )
    try:
        with pytest.raises(StoreSerializationError, match="event_id|large|bounded"):
            store.execute_once("cmd_event", "append", lambda transaction: (
                transaction.append_event(event) or {"saved": True}
            ))
        assert store.count("events") == store.count("commands") == 0
    finally:
        store.close()


@pytest.mark.parametrize(
    "field,value",
    [("event_id", "evt_\ud800"), ("kind", " "), ("kind", "k" * 129)],
)
def test_event_identity_fields_are_strict_nonempty_and_bounded(
    tmp_path: Path, field: str, value: str
) -> None:
    store = _open(tmp_path)
    event = {
        "event_id": "evt_1", "kind": "saved", "aggregate_type": "attempt",
        "aggregate_id": "att_1", "payload": {}, "occurred_at": NOW_TEXT,
    }
    event[field] = value
    try:
        with pytest.raises(StoreSerializationError):
            store.execute_once("cmd_bad_event", "append", lambda transaction: (
                transaction.append_event(event) or {"saved": True}
            ))
        assert store.count("events") == store.count("commands") == 0
    finally:
        store.close()


def test_mapping_event_and_domain_timestamp_are_preserved(tmp_path: Path) -> None:
    store = _open(tmp_path)
    domain = DomainEvent(
        event_id="evt_domain", kind="domain", aggregate_type="attempt",
        aggregate_id="att_1", payload=(),
        occurred_at="2000-01-01T01:02:03+00:00",
    )
    mapped = MappingProxyType({
        "event_id": "evt_mapping", "kind": "mapping",
        "aggregate_type": "attempt", "aggregate_id": "att_1",
        "payload": MappingProxyType({"safe": True}),
        "occurred_at": "2001-02-03T04:05:06+00:00",
    })
    try:
        store.execute_once("cmd_events", "append", lambda transaction: (
            transaction.append_event(domain), transaction.append_event(mapped), {"ok": True}
        )[-1])
        assert store.connection.execute(
            "SELECT event_id, occurred_at FROM events ORDER BY event_id"
        ).fetchall() == [
            ("evt_domain", "2000-01-01T01:02:03+00:00"),
            ("evt_mapping", "2001-02-03T04:05:06+00:00"),
        ]
    finally:
        store.close()


def test_mapping_proxy_session_snapshot_is_copied(tmp_path: Path) -> None:
    store = _open(tmp_path)
    snapshot = MappingProxyType({"session_id": "ses_1", "state": "setup"})
    try:
        store.execute_once("cmd_mapping", "save", lambda transaction: (
            transaction.save_session(snapshot) or {"saved": True}
        ))
        assert store.load_aggregate("product_sessions", "ses_1")["state"] == "setup"
    finally:
        store.close()


@pytest.mark.parametrize("damage", ["missing_completed_at", "bad_completed_at"])
def test_completed_command_requires_coherent_valid_timestamps(
    tmp_path: Path, damage: str
) -> None:
    store = _open(tmp_path)
    store.execute_once("cmd_1", "confirm", lambda transaction: {"ok": True})
    store._writer.execute(
        "UPDATE commands SET completed_at=? WHERE command_id='cmd_1'",
        (None if damage == "missing_completed_at" else "not-a-time",),
    )
    called = False
    try:
        with pytest.raises(StoreCommandStateError, match="command|timestamp|completed"):
            store.lookup_command("cmd_1", "confirm")
        def callback(transaction):
            nonlocal called
            called = True
            return {"bad": True}
        with pytest.raises(StoreCommandStateError):
            store.execute_once("cmd_1", "confirm", callback)
        assert not called
    finally:
        store.close()


def test_started_command_rejects_result_or_completed_timestamp(tmp_path: Path) -> None:
    store = _open(tmp_path)
    store._writer.execute(
        "INSERT INTO commands VALUES (?,?,?,?,?,?)",
        ("cmd_bad", "confirm", "started", "{}", NOW_TEXT, NOW_TEXT),
    )
    try:
        with pytest.raises(StoreCommandStateError, match="incomplete|coherent"):
            store.lookup_command("cmd_bad")
    finally:
        store.close()


@pytest.mark.parametrize(
    "column,value", [("created_at", "not-a-time"), ("state", "failed")]
)
def test_command_with_malformed_creation_or_state_fails_before_callback(
    tmp_path: Path, column: str, value: str
) -> None:
    store = _open(tmp_path)
    store.execute_once("cmd_damaged", "confirm", lambda transaction: {"ok": True})
    store._writer.execute(
        f"UPDATE commands SET {column}=? WHERE command_id='cmd_damaged'", (value,)
    )
    called = False
    try:
        def callback(transaction):
            nonlocal called
            called = True
            return {"bad": True}
        with pytest.raises(StoreCommandStateError):
            store.execute_once("cmd_damaged", "confirm", callback)
        assert not called
    finally:
        store.close()


def test_command_kind_has_smaller_bound_than_command_identity(tmp_path: Path) -> None:
    store = _open(tmp_path)
    try:
        result = store.execute_once("c" * 200, "normal", lambda transaction: {"ok": True})
        assert result == {"ok": True}
        with pytest.raises(ValueError, match="command_kind|large"):
            store.execute_once("cmd_kind", "k" * 200, lambda transaction: {"bad": True})
    finally:
        store.close()


def test_integrity_check_is_read_only_ok_then_detects_foreign_key_violation(
    tmp_path: Path,
) -> None:
    store = _open(tmp_path)
    try:
        _seed_task(store)
        assert store.integrity_check() == "ok"

        connection = store._writer
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "INSERT INTO evidence VALUES (?,?,?,?,?,?,?)",
            (
                "ev_orphan", "tsk_missing", "att_missing", "artifact_hash",
                '{"artifact_reference":"x","content_hash":"' + "a" * 64 + '"}',
                "b" * 64, NOW_TEXT,
            ),
        )
        connection.execute("PRAGMA foreign_keys=ON")

        assert store.integrity_check() == "foreign_key_violation"
    finally:
        store.close()
