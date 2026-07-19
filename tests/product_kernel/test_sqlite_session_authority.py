from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.adapters.sqlite_schema import StoreCommandStateError
from agentdeck.kernel.execution import AttemptState
from agentdeck.kernel.session import ExitAttemptSnapshot

from .fakes import FrozenClock


NOW = datetime(2026, 7, 19, 8, 9, 10, tzinfo=timezone.utc)
EARLIER = "2026-07-19T07:00:00+00:00"
LATEST = "2026-07-19T08:00:00+00:00"
PAIR = ("codex-cli", "native-default")
PENDING_ATTEMPT = ExitAttemptSnapshot(
    attempt_id="att_1",
    task_id="tsk_1",
    agent_instance_id="agt_1",
    ordinal=1,
    state=AttemptState.RUNNING,
    acp_session_id="acp_1",
    effect_observed=False,
    durable_fingerprint="a" * 64,
)
PENDING = (
    "xrt_" + "1" * 32,
    "att_1",
    PENDING_ATTEMPT.canonical_bytes().decode("utf-8"),
    PENDING_ATTEMPT.content_hash,
    "2026-07-19T08:01:00+00:00",
)


def _insert_session(
    store: SQLiteStore,
    *,
    session_id: str,
    state: str,
    project_id: str | None = None,
    created_at: str = LATEST,
    updated_at: str = LATEST,
) -> None:
    authority_project = store._project_id if project_id is None else project_id
    resolved_root = (
        str(store._project_root) if project_id is None else f"/other/{project_id}"
    )
    store._writer.execute(
        "INSERT OR IGNORE INTO projects VALUES (?, ?, ?)",
        (authority_project, resolved_root, EARLIER),
    )
    leader, model = (None, None) if state == "setup" else PAIR
    store._writer.execute(
        """INSERT INTO product_sessions (
               session_id,project_id,state,permission_profile,pending_goal,
               created_at,updated_at,leader_backend,leader_model,
               pending_exit_id,pending_exit_attempt_id,
               canonical_pending_exit_attempt_facts,pending_exit_attempt_hash,
               pending_exit_requested_at
           ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL)""",
        (
            session_id,
            authority_project,
            state,
            None if state == "setup" else "approve_for_me",
            created_at,
            updated_at,
            leader,
            model,
        ),
    )
    store._writer.commit()


def test_session_selection_value_requires_a_strict_typed_identity_and_exact_count() -> None:
    from agentdeck.ports.store import SessionSelection

    assert SessionSelection(None, 0).session_id is None
    assert SessionSelection("ses_live", 1).nonterminal_count == 1
    for session_id, count in ((None, 1), ("ses_live", 0), ("bad", 1), ("ses_", 1)):
        with pytest.raises((TypeError, ValueError)):
            SessionSelection(session_id, count)
    for count in (-1, True, 1.0):
        with pytest.raises((TypeError, ValueError)):
            SessionSelection(None, count)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        SessionSelection("ses_" + "x" * 252, 1)


def test_latest_nonterminal_selection_is_project_scoped_and_stably_ordered(
    tmp_path: Path,
) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        _insert_session(
            store, session_id="ses_old", state="ready",
            created_at=EARLIER, updated_at=EARLIER,
        )
        _insert_session(store, session_id="ses_a", state="running")
        _insert_session(store, session_id="ses_b", state="paused")
        _insert_session(store, session_id="ses_done", state="completed")
        _insert_session(
            store, session_id="ses_other", state="running",
            project_id="prj_other", created_at=LATEST,
            updated_at="2026-07-19T09:00:00+00:00",
        )
        before = tuple(store.count(table) for table in ("commands", "product_sessions", "events"))

        selection = store.select_latest_nonterminal_session()

        assert selection.session_id == "ses_b"
        assert selection.nonterminal_count == 3
        assert tuple(store.count(table) for table in ("commands", "product_sessions", "events")) == before
    finally:
        store.close()


def test_selection_ignores_other_project_when_current_project_has_no_session(
    tmp_path: Path,
) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        _insert_session(
            store, session_id="ses_other", state="running", project_id="prj_other"
        )
        selection = store.select_latest_nonterminal_session()
        assert selection.session_id is None
        assert selection.nonterminal_count == 0
    finally:
        store.close()


def test_corrupt_selected_session_identity_fails_closed(tmp_path: Path) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        _insert_session(store, session_id="ses_valid", state="setup")
        store._writer.execute(
            "UPDATE product_sessions SET session_id='corrupt' WHERE session_id='ses_valid'"
        )
        store._writer.commit()

        with pytest.raises(StoreCommandStateError, match="stored session"):
            store.select_latest_nonterminal_session()
    finally:
        store.close()


def test_session_write_preserves_pair_pending_group_and_loads_all_v2_fields(
    tmp_path: Path,
) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    _insert_session(store, session_id="ses_keep", state="ready")
    store._writer.execute(
        """UPDATE product_sessions SET pending_exit_id=?,pending_exit_attempt_id=?,
              canonical_pending_exit_attempt_facts=?,pending_exit_attempt_hash=?,
              pending_exit_requested_at=? WHERE session_id='ses_keep'""",
        PENDING,
    )
    store._writer.commit()
    try:
        store.execute_once("cmd_pause", "pause", lambda transaction: _save_pause(transaction))
        loaded = store.load_aggregate("product_sessions", "ses_keep")

        assert loaded is not None
        assert (loaded["leader_backend"], loaded["leader_model"]) == PAIR
        assert tuple(loaded[field] for field in (
            "pending_exit_id", "pending_exit_attempt_id",
            "canonical_pending_exit_attempt_facts", "pending_exit_attempt_hash",
            "pending_exit_requested_at",
        )) == PENDING
    finally:
        store.close()


def _save_pause(transaction: object) -> dict[str, object]:
    transaction.save_session({  # type: ignore[attr-defined]
        "session_id": "ses_keep",
        "state": "paused",
        "permission_profile": "approve_for_me",
        "pending_goal": None,
    })
    return {"saved": True}


@pytest.mark.parametrize(
    "changes",
    (
        {"leader_backend": "claude-cli", "leader_model": "native-default"},
        {"leader_backend": "codex-cli"},
        {"pending_exit_id": "exit_partial"},
    ),
)
def test_drifted_or_partial_v2_extension_write_rolls_back_without_mutation(
    tmp_path: Path, changes: dict[str, object],
) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    _insert_session(store, session_id="ses_keep", state="ready")
    before = store.load_aggregate("product_sessions", "ses_keep")
    counts = tuple(store.count(table) for table in ("commands", "product_sessions", "events"))

    def mutate(transaction: object) -> dict[str, object]:
        transaction.save_session({  # type: ignore[attr-defined]
            "session_id": "ses_keep", "state": "paused", **changes,
        })
        return {"saved": True}

    try:
        with pytest.raises((TypeError, ValueError)):
            store.execute_once("cmd_invalid", "invalid", mutate)
        assert store.load_aggregate("product_sessions", "ses_keep") == before
        assert tuple(store.count(table) for table in ("commands", "product_sessions", "events")) == counts
    finally:
        store.close()
