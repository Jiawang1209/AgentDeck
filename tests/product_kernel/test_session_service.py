from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.session_service import SessionService, SessionServiceError

from .fakes import FrozenClock


NOW = datetime(2026, 7, 19, 8, 9, 10, tzinfo=timezone.utc)
AVAILABLE_LEADERS = {
    "codex-cli": ("native-default",),
    "claude-cli": ("native-default",),
}


def _service(
    root: Path,
    *,
    store: SQLiteStore | None = None,
    session_id: str = "ses_1",
) -> tuple[SessionService, SQLiteStore]:
    authority = store or SQLiteStore.open(root, clock=FrozenClock(NOW))
    return (
        SessionService(
            store=authority,
            clock=FrozenClock(NOW),
            session_id=session_id,
            project_root=str(root),
            available_leaders=AVAILABLE_LEADERS,
        ),
        authority,
    )


def test_goal_survives_setup_and_resumes_after_store_reopen(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    result = service.accept_text("Build an accessible page")
    assert result.mode == "setup_required"
    store.close()

    reopened = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        resumed_service, _ = _service(tmp_path, store=reopened)
        configured = resumed_service.configure(
            leader="codex-cli",
            model="native-default",
            permission="approve_for_me",
        )
        resumed = resumed_service.resume()

        assert configured.accepted is True
        assert resumed.mode == "goal_ready"
        assert resumed.goal == "Build an accessible page"
        assert resumed_service.current().leader_backend == "codex-cli"
        assert resumed_service.current().model == "native-default"
        assert resumed_service.current().permission == "approve_for_me"
        loaded = reopened.load_aggregate("product_sessions", "ses_1")
        assert loaded is not None
        assert loaded["state"] == "ready"
        assert loaded["permission_profile"] == "approve_for_me"
        assert loaded["pending_goal"] == "Build an accessible page"
    finally:
        reopened.close()


def test_configured_leader_and_model_restore_after_store_reopen(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    service.configure(leader="codex-cli", model="native-default")
    store.close()

    reopened = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        restored, _ = _service(tmp_path, store=reopened)

        assert restored.current().leader_backend == "codex-cli"
        assert restored.current().model == "native-default"
        assert restored.current().permission == "approve_for_me"
        assert restored.current().state.value == "ready"
    finally:
        reopened.close()


def test_reentry_rejects_configure_result_that_conflicts_with_durable_goal(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    service.accept_text("Durable goal")
    service.configure(leader="codex-cli", model="native-default")
    row = store._writer.execute(
        "SELECT canonical_result_facts FROM commands WHERE command_id=?",
        ("session:configure:ses_1",),
    ).fetchone()
    forged = json.loads(row[0])
    forged["goal"] = "Forged goal"
    store._writer.execute(
        "UPDATE commands SET canonical_result_facts=? WHERE command_id=?",
        (json.dumps(forged, sort_keys=True, separators=(",", ":")),
         "session:configure:ses_1"),
    )
    store._writer.commit()
    store.close()

    reopened = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        with pytest.raises(SessionServiceError, match="conflicts"):
            _service(tmp_path, store=reopened)
    finally:
        reopened.close()


def test_existing_session_rejects_a_different_caller_project_root(
    tmp_path: Path,
) -> None:
    _, store = _service(tmp_path)
    different = tmp_path / "different"
    different.mkdir()
    try:
        with pytest.raises(SessionServiceError, match="project root does not match"):
            SessionService(
                store=store,
                clock=FrozenClock(NOW),
                session_id="ses_1",
                project_root=str(different),
                available_leaders=AVAILABLE_LEADERS,
            )
    finally:
        store.close()


def test_unavailable_provider_is_never_silently_selected(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    try:
        result = service.configure(
            leader="api:deepseek",
            model="deepseek-chat",
        )

        assert result.accepted is False
        assert result.diagnostic is not None
        assert result.diagnostic.code == "leader_credential_unavailable"
        assert service.current().leader_backend is None
        assert service.current().model is None
        assert store.load_aggregate("product_sessions", "ses_1")["state"] == "setup"
        assert store.count("commands") == 1
    finally:
        store.close()


def test_every_text_turn_is_persisted_with_session_mutation_atomically(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    try:
        service.accept_text("First goal", command_id="cmd_turn_1")
        service.accept_text("Refined goal", command_id="cmd_turn_2")

        rows = store.connection.execute(
            """SELECT c.command_id, e.kind, e.aggregate_id
               FROM commands c JOIN events e ON e.command_id=c.command_id
               WHERE e.kind='conversation_turn_recorded'
               ORDER BY c.command_id"""
        ).fetchall()
        assert rows == [
            ("cmd_turn_1", "conversation_turn_recorded", "ses_1"),
            ("cmd_turn_2", "conversation_turn_recorded", "ses_1"),
        ]
        assert service.resume().goal == "Refined goal"
    finally:
        store.close()


def test_replayed_command_returns_first_result_without_duplicate_turn(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    try:
        first = service.accept_text("Build it", command_id="cmd_retry")
        second = service.accept_text("Build it", command_id="cmd_retry")

        assert second == first
        assert store.connection.execute(
            "SELECT count(*) FROM events WHERE kind='conversation_turn_recorded'"
        ).fetchone() == (1,)
        assert store.connection.execute(
            "SELECT count(*) FROM commands WHERE command_id='cmd_retry'"
        ).fetchone() == (1,)
        assert store.connection.execute(
            "SELECT count(*) FROM conversation_turns WHERE session_id='ses_1'"
        ).fetchone() == (1,)
    finally:
        store.close()


def test_identical_default_text_calls_create_distinct_ordered_turns(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    try:
        first = service.accept_text("Same text")
        second = service.accept_text("Same text")

        assert first == second
        rows = store.connection.execute(
            """SELECT turn_id, ordinal, sanitized_content
               FROM conversation_turns WHERE session_id='ses_1'
               ORDER BY ordinal"""
        ).fetchall()
        assert len(rows) == 2
        assert rows[0][0] != rows[1][0]
        assert rows == [
            (rows[0][0], 1, "Same text"),
            (rows[1][0], 2, "Same text"),
        ]
        assert store.connection.execute(
            "SELECT count(*) FROM events WHERE kind='conversation_turn_recorded'"
        ).fetchone() == (2,)
    finally:
        store.close()


def test_reused_turn_command_cannot_cross_product_session_lineage(
    tmp_path: Path,
) -> None:
    first, store = _service(tmp_path, session_id="ses_1")
    second, _ = _service(tmp_path, store=store, session_id="ses_2")
    try:
        first.accept_text("First session goal", command_id="cmd_shared")

        with pytest.raises(SessionServiceError, match="lineage"):
            second.accept_text("Second session goal", command_id="cmd_shared")

        assert second.current().pending_goal is None
        assert store.load_aggregate("product_sessions", "ses_2")["pending_goal"] is None
    finally:
        store.close()


@pytest.mark.parametrize(
    ("leader", "model", "permission", "code"),
    [
        ("codex-cli", "unknown-model", "approve_for_me", "leader_model_unavailable"),
        ("missing-cli", "native-default", "approve_for_me", "leader_unavailable"),
        ("codex-cli", "native-default", "invalid", "permission_profile_invalid"),
    ],
)
def test_invalid_setup_selection_fails_closed_without_mutating_session(
    tmp_path: Path,
    leader: str,
    model: str,
    permission: str,
    code: str,
) -> None:
    service, store = _service(tmp_path)
    try:
        result = service.configure(
            leader=leader,
            model=model,
            permission=permission,
        )

        assert result.accepted is False
        assert result.diagnostic is not None
        assert result.diagnostic.code == code
        assert service.current().leader_backend is None
        assert store.load_aggregate("product_sessions", "ses_1")["state"] == "setup"
        assert store.count("commands") == 1
    finally:
        store.close()


@pytest.mark.parametrize("text", ["", "   ", "\ud800", "x" * 65_537])
def test_invalid_goal_is_rejected_before_any_turn_mutation(
    tmp_path: Path, text: str
) -> None:
    service, store = _service(tmp_path)
    try:
        with pytest.raises(ValueError, match="goal"):
            service.accept_text(text, command_id="cmd_bad")

        assert store.lookup_command("cmd_bad") is None
        assert store.count("events") == 1
        assert service.current().pending_goal is None
    finally:
        store.close()
