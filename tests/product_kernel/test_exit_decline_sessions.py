from __future__ import annotations

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.exit_service import ExitService

from .fakes import FrozenClock
from .test_sqlite_exit_authority import (
    NOW, REQUEST_ID, pending_exit_fields, seed_active_exit,
)


def _service(store: SQLiteStore, session_id: str) -> ExitService:
    return ExitService(
        store=store, clock=FrozenClock(NOW), session_id=session_id,
        request_id_factory=lambda: REQUEST_ID,
    )


def _seed_second_session(store: SQLiteStore):
    connection = store._require_writer()
    now = NOW.isoformat()
    connection.execute(
        """INSERT INTO product_sessions (
               session_id,project_id,state,permission_profile,pending_goal,
               created_at,updated_at,leader_backend,leader_model)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        ("ses_2", store._project_id, "running", "approve_for_me", None,
         now, now, "codex-cli", "native-default"),
    )
    connection.execute(
        "INSERT INTO missions VALUES (?,?,?,?,?,?)",
        ("msn_2", "ses_2", "running", 1, now, now),
    )
    connection.execute(
        "INSERT INTO mission_versions VALUES (?,?,?,?,?,?)",
        ("msn_2", 1, "prv_2", "b" * 64, "{}", now),
    )
    connection.execute(
        "INSERT INTO agent_instances VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("agt_2", "ses_2", "codex-cli", "acp", "1", "implementer",
         "ses_acp_2", "active", now, now),
    )
    connection.execute(
        "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("tsk_2", "msn_2", 1, 1, "implementation", "implementer",
         "codex-cli", "agt_2", "acp://route", "running", "{}", now, now),
    )
    connection.execute(
        "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("att_2", "tsk_2", "agt_2", 1, "running", None, None, 0,
         "ses_acp_2", 0, now, now),
    )
    request = _service(store, "ses_2").request_exit().request
    assert request is not None
    return request


def test_same_request_id_declines_are_isolated_by_product_session(tmp_path):
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        first = seed_active_exit(store)
        second = _seed_second_session(store)
        assert first.request_id == second.request_id == REQUEST_ID
        one = _service(store, "ses_1")
        two = _service(store, "ses_2")

        assert one.decline(first.request_id, first.attempt_hash).mode == "exit_declined"
        assert pending_exit_fields(store, "ses_1") == (None,) * 5
        assert pending_exit_fields(store, "ses_2")[0] == REQUEST_ID
        assert two.decline(second.request_id, second.attempt_hash).mode == "exit_declined"
        assert pending_exit_fields(store, "ses_2") == (None,) * 5
        for session_id in ("ses_1", "ses_2"):
            assert store.lookup_command(
                f"exit:decline:{session_id}:{REQUEST_ID}",
                "decline_product_exit",
            ) is not None
    finally:
        store.close()


def test_decline_replay_validates_original_session_and_attempt_lineage(tmp_path):
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        first = seed_active_exit(store)
        second = _seed_second_session(store)
        one = _service(store, "ses_1")
        assert one.decline(first.request_id, first.attempt_hash).mode == "exit_declined"
        foreign = store.lookup_command(
            f"exit:decline:ses_1:{REQUEST_ID}", "decline_product_exit"
        )
        store.execute_once(
            f"exit:decline:ses_2:{REQUEST_ID}", "decline_product_exit",
            lambda transaction: foreign,
        )
        before = pending_exit_fields(store, "ses_2")
        replay = _service(store, "ses_2").decline(
            second.request_id, second.attempt_hash
        )
        assert replay.diagnostic.code == "exit_authority_invalid"
        assert pending_exit_fields(store, "ses_2") == before
    finally:
        store.close()
