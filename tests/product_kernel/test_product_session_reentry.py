from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.session_service import SessionService, SessionServiceError
from agentdeck.kernel.diagnostics import Severity

from .fakes import FrozenClock
from .sqlite_v1_fixture import create_v1_database


NOW = datetime(2026, 7, 19, 8, 9, 10, tzinfo=timezone.utc)
AVAILABLE = {"codex-cli": ("native-default",)}
TABLES = ("commands", "product_sessions", "conversation_turns", "events")


def _open_latest(
    root: Path, store: SQLiteStore, factory,
) -> SessionService:
    return SessionService.open_latest(
        store=store,
        clock=FrozenClock(NOW),
        project_root=str(root),
        available_leaders=AVAILABLE,
        session_id_factory=factory,
    )


def _configured(root: Path, store: SQLiteStore, session_id: str) -> SessionService:
    service = SessionService(
        store=store,
        clock=FrozenClock(NOW),
        session_id=session_id,
        project_root=str(root),
        available_leaders=AVAILABLE,
    )
    service.configure(leader="codex-cli", model="native-default")
    return service


def test_terminal_only_history_creates_one_new_typed_session(tmp_path: Path) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    _configured(tmp_path, store, "ses_done")
    store._writer.execute(
        "UPDATE product_sessions SET state='completed' WHERE session_id='ses_done'"
    )
    store._writer.commit()
    calls = 0

    def factory() -> str:
        nonlocal calls
        calls += 1
        return "ses_new"

    try:
        service = _open_latest(tmp_path, store, factory)
        assert service.current().session_id == "ses_new"
        assert service.current().state.value == "setup"
        assert calls == 1
        assert store.load_aggregate("product_sessions", "ses_done")["state"] == "completed"
        assert store.count("product_sessions") == 2
    finally:
        store.close()


def test_terminal_only_factory_collision_fails_closed_without_restoring_history(
    tmp_path: Path,
) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    _configured(tmp_path, store, "ses_done")
    store._writer.execute(
        "UPDATE product_sessions SET state='completed' WHERE session_id='ses_done'"
    )
    store._writer.commit()
    before = tuple(store.count(table) for table in TABLES)
    calls = 0

    def colliding_factory() -> str:
        nonlocal calls
        calls += 1
        return "ses_done"

    try:
        with pytest.raises(SessionServiceError, match="factory identity"):
            _open_latest(tmp_path, store, colliding_factory)
        assert calls == 1
        assert tuple(store.count(table) for table in TABLES) == before
        assert store.load_aggregate("product_sessions", "ses_done")["state"] == "completed"
    finally:
        store.close()


def test_migrated_v1_reentry_restores_original_session_and_exact_pair(
    tmp_path: Path,
) -> None:
    create_v1_database(tmp_path)
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))

    def forbidden_factory() -> str:
        raise AssertionError("factory must not run")

    try:
        service = _open_latest(tmp_path, store, forbidden_factory)
        view = service.current()
        assert view.session_id == "ses_v1"
        assert view.leader_backend == "codex-cli"
        assert view.model == "native-default"
        assert view.permission == "approve_for_me"
        assert view.pending_goal == "Migrate safely"
    finally:
        store.close()


def test_configure_persists_exact_leader_model_pair_with_session_command(
    tmp_path: Path,
) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        service = _configured(tmp_path, store, "ses_pair")
        loaded = store.load_aggregate("product_sessions", "ses_pair")
        command = store.lookup_command(
            "session:configure:ses_pair", "configure_product_session"
        )
        assert loaded is not None
        assert (loaded["leader_backend"], loaded["leader_model"]) == (
            "codex-cli", "native-default",
        )
        assert command is not None
        assert (command["leader_backend"], command["model"]) == (
            loaded["leader_backend"], loaded["leader_model"],
        )
        assert service.current().leader_backend == loaded["leader_backend"]
    finally:
        store.close()


def test_reentry_rejects_row_model_drift_without_writes_or_fallback(
    tmp_path: Path,
) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    _configured(tmp_path, store, "ses_drift")
    marker = "RAW-DRIFT-MODEL"
    store._writer.execute(
        "UPDATE product_sessions SET leader_model=? WHERE session_id='ses_drift'",
        (marker,),
    )
    store._writer.commit()
    store.close()

    reopened = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    before_counts = tuple(reopened.count(table) for table in TABLES)
    before_row = reopened.load_aggregate("product_sessions", "ses_drift")
    try:
        with pytest.raises(SessionServiceError, match="conflicts|configuration") as error:
            _open_latest(tmp_path, reopened, lambda: "ses_fallback")
        assert marker not in str(error.value)
        assert tuple(reopened.count(table) for table in TABLES) == before_counts
        assert reopened.load_aggregate("product_sessions", "ses_drift") == before_row
        assert reopened.load_aggregate("product_sessions", "ses_fallback") is None
    finally:
        reopened.close()


def test_reentry_wraps_configure_command_identity_drift_without_writes(
    tmp_path: Path,
) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    _configured(tmp_path, store, "ses_command_drift")
    store._writer.execute(
        """UPDATE commands SET command_kind='drifted_kind'
           WHERE command_id='session:configure:ses_command_drift'"""
    )
    store._writer.commit()
    store.close()

    reopened = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    before = tuple(reopened.count(table) for table in TABLES)
    try:
        with pytest.raises(SessionServiceError, match="command authority"):
            _open_latest(tmp_path, reopened, lambda: "ses_fallback")
        assert tuple(reopened.count(table) for table in TABLES) == before
        assert reopened.load_aggregate("product_sessions", "ses_fallback") is None
    finally:
        reopened.close()


def test_reentry_wraps_malformed_configure_identity_content_free_without_writes(
    tmp_path: Path,
) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    _configured(tmp_path, store, "ses_malformed")
    row = store._writer.execute(
        """SELECT canonical_result_facts FROM commands
           WHERE command_id='session:configure:ses_malformed'"""
    ).fetchone()
    forged = json.loads(row[0])
    marker = "RAW-MALFORMED-CONFIG-" + "x" * 4_096
    forged["model"] = marker
    store._writer.execute(
        """UPDATE commands SET canonical_result_facts=?
           WHERE command_id='session:configure:ses_malformed'""",
        (json.dumps(forged, sort_keys=True, separators=(",", ":")),),
    )
    store._writer.commit()
    store.close()

    reopened = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    before = tuple(reopened.count(table) for table in TABLES)
    try:
        with pytest.raises(SessionServiceError, match="setup result") as error:
            _open_latest(tmp_path, reopened, lambda: "ses_fallback")
        assert marker not in str(error.value)
        assert tuple(reopened.count(table) for table in TABLES) == before
        assert reopened.load_aggregate("product_sessions", "ses_fallback") is None
    finally:
        reopened.close()


def test_multiple_nonterminal_reentry_is_stable_read_only_and_warns_with_count(
    tmp_path: Path,
) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    _configured(tmp_path, store, "ses_a")
    _configured(tmp_path, store, "ses_b")
    store._writer.execute(
        "UPDATE product_sessions SET updated_at=? WHERE session_id IN ('ses_a','ses_b')",
        (NOW.isoformat(),),
    )
    store._writer.commit()
    before_counts = tuple(store.count(table) for table in TABLES)
    before_rows = tuple(store.connection.execute(
        "SELECT * FROM product_sessions ORDER BY session_id"
    ))
    try:
        service = _open_latest(
            tmp_path, store,
            lambda: (_ for _ in ()).throw(AssertionError("factory must not run")),
        )
        view = service.current()
        assert view.session_id == "ses_b"
        assert view.reentry_diagnostic is not None
        assert view.reentry_diagnostic.code == "multiple_nonterminal_sessions"
        assert view.reentry_diagnostic.severity is Severity.WARNING
        assert "2" in view.reentry_diagnostic.cause
        assert "ses_a" not in str(view.reentry_diagnostic)
        assert "ses_b" not in str(view.reentry_diagnostic)
        assert tuple(store.count(table) for table in TABLES) == before_counts
        assert tuple(store.connection.execute(
            "SELECT * FROM product_sessions ORDER BY session_id"
        )) == before_rows
    finally:
        store.close()


@pytest.mark.parametrize("session_id", ["", "bad", "ses_", "ses_bad id", "ses_" + "x" * 252])
def test_new_session_factory_requires_a_strict_typed_identity_before_writes(
    tmp_path: Path, session_id: str,
) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    calls = 0

    def factory() -> str:
        nonlocal calls
        calls += 1
        return session_id

    try:
        with pytest.raises((SessionServiceError, ValueError)):
            _open_latest(tmp_path, store, factory)
        assert calls == 1
        assert store.count("commands") == 0
        assert store.count("product_sessions") == 0
        assert store.count("events") == 0
    finally:
        store.close()
