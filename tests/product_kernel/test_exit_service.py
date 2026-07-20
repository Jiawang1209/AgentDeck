from __future__ import annotations
from datetime import datetime, timezone
from hmac import compare_digest as real_compare_digest
import inspect
import json
from pathlib import Path
import pytest
from agentdeck.adapters.sqlite import SQLiteStore
import agentdeck.application.exit_service as exit_module
from agentdeck.application.exit_records import (
    ACTIVE_EXIT_RESULT_FIELDS,
    closed_exit_result,
    exit_result_from_command,
)
from agentdeck.application.exit_service import ExitResult, ExitService
from agentdeck.kernel.session import ExitRequest
from .fakes import FrozenClock
NOW = datetime(2026, 7, 19, 3, 0, tzinfo=timezone.utc)
EXIT_COLUMNS = (
    "pending_exit_id",
    "pending_exit_attempt_id",
    "canonical_pending_exit_attempt_facts",
    "pending_exit_attempt_hash",
    "pending_exit_requested_at",
)
def _seed_session(store: SQLiteStore) -> None:
    now = NOW.isoformat()
    connection = store._require_writer()
    connection.execute(
        "INSERT INTO projects (project_id,resolved_root,created_at) VALUES (?,?,?)",
        (store._project_id, str(store._project_root), now),
    )
    connection.execute(
        "INSERT INTO product_sessions (session_id,project_id,state,"
        "permission_profile,pending_goal,created_at,updated_at,leader_backend,"
        "leader_model) VALUES (?,?,?,?,?,?,?,?,?)",
        ("ses_1", store._project_id, "running", "approve_for_me", None,
         now, now, "codex-cli", "native-default"),
    )
    connection.execute(
        "INSERT INTO missions (mission_id,session_id,state,current_version,"
        "created_at,updated_at) VALUES (?,?,?,?,?,?)",
        ("msn_1", "ses_1", "running", 1, now, now),
    )
    connection.execute(
        "INSERT INTO mission_versions (mission_id,version,preview_id,content_hash,"
        "canonical_mission_facts,confirmed_at) VALUES (?,?,?,?,?,?)",
        ("msn_1", 1, "prv_1", "a" * 64, "{}", now),
    )
def _seed_attempt(
    store: SQLiteStore, *, attempt_id: str = "att_1", state: str = "running",
    task_ordinal: int = 1,
) -> None:
    connection = store._require_writer()
    now = NOW.isoformat()
    suffix = attempt_id.removeprefix("att_")
    agent_id = f"agt_{suffix}"
    task_id = f"tsk_{suffix}"
    connection.execute(
        "INSERT INTO agent_instances (instance_id,session_id,backend_id,transport,"
        "backend_version,role,acp_session_id,state,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (agent_id, "ses_1", "codex-cli", "acp", "1", "implementer",
         f"acp_{suffix}", "active", now, now),
    )
    connection.execute(
        "INSERT INTO tasks (task_id,mission_id,mission_version,ordinal,name,role,"
        "planned_backend,planned_agent_instance_id,acp_route,state,"
        "canonical_task_facts,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (task_id, "msn_1", 1, task_ordinal, task_id, "implementer", "codex-cli",
         agent_id, "acp://route", "running", "{}", now, now),
    )
    connection.execute(
        "INSERT INTO attempts (attempt_id,task_id,agent_instance_id,ordinal,state,"
        "reason,result_summary,retryable,acp_session_id,effect_observed,created_at,"
        "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (attempt_id, task_id, agent_id, 1, state, None, None, 0,
         f"acp_{suffix}", 0, now, now),
    )
@pytest.fixture
def store(tmp_path: Path) -> SQLiteStore:
    value = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    _seed_session(value)
    try:
        yield value
    finally:
        value.close()
class IdentityFactory:
    def __init__(self, *identities: str) -> None:
        self.identities, self.calls = iter(identities), 0

    def __call__(self) -> str:
        self.calls += 1
        return next(self.identities)


class AdvancingClock:
    def __init__(self, *values: datetime) -> None:
        self.values, self.calls = iter(values), 0

    def now(self) -> datetime:
        self.calls += 1
        return next(self.values)


def _service(store: SQLiteStore, *identities: str) -> ExitService:
    return ExitService(
        store=store,
        clock=FrozenClock(NOW),
        session_id="ses_1",
        request_id_factory=IdentityFactory(*(
            identities or ("xrt_" + "1" * 32, "xrt_" + "2" * 32)
        )),
    )


@pytest.fixture
def active_exit(store: SQLiteStore) -> tuple[ExitService, ExitRequest]:
    _seed_attempt(store)
    service = _service(store)
    request = service.request_exit().request
    assert request is not None
    return service, request


def pending_exit_fields(store: SQLiteStore, session_id: str = "ses_1") -> tuple[object, ...]:
    row = store.load_aggregate("product_sessions", session_id)
    assert row is not None
    return tuple(row[name] for name in EXIT_COLUMNS)


def database_facts(store: SQLiteStore) -> tuple[tuple[object, ...], ...]:
    connection = store._require_writer()
    return tuple(
        row for table in ("product_sessions", "attempts", "commands", "events")
        for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
    )

def advance_active_attempt(store: SQLiteStore, attempt_id: str) -> None:
    store._require_writer().execute(
        "UPDATE attempts SET effect_observed=1, updated_at=? WHERE attempt_id=?",
        ("2026-07-19T04:00:00+00:00", attempt_id),
    )


def forge_malformed_pending_group(store: SQLiteStore) -> None:
    store._require_writer().execute(
        "UPDATE product_sessions SET pending_exit_id=?, "
        "pending_exit_attempt_id='att_1', "
        "canonical_pending_exit_attempt_facts='{}', "
        "pending_exit_attempt_hash=?, pending_exit_requested_at=? "
        "WHERE session_id='ses_1'",
        ("xrt_" + "f" * 32, "f" * 64, NOW.isoformat()),
    )

def test_exit_service_exposes_only_the_four_exit_operations() -> None:
    public = {
        name for name, member in inspect.getmembers(ExitService, inspect.isfunction)
        if not name.startswith("_")
    }
    assert public == {"request_exit", "decline", "confirm", "input_closed"}
    assert ExitResult.__dataclass_params__.frozen is True


def test_no_active_attempt_exits_without_request_or_write(store: SQLiteStore) -> None:
    factory = IdentityFactory("xrt_" + "1" * 32)
    service = ExitService(
        store=store, clock=FrozenClock(NOW), session_id="ses_1",
        request_id_factory=factory,
    )
    before = database_facts(store)
    result = service.request_exit()
    assert result == ExitResult(mode="exit_ready", should_exit=True)
    assert database_facts(store) == before
    assert factory.calls == 0


@pytest.mark.parametrize(
    ("operation", "should_exit"), (("request_exit", False), ("input_closed", True)),
)
def test_missing_bound_session_never_claims_safe_exit(
    tmp_path: Path, operation: str, should_exit: bool,
) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    factory = IdentityFactory("xrt_" + "1" * 32)
    service = ExitService(store=store, clock=FrozenClock(NOW),
                          session_id="ses_missing", request_id_factory=factory)
    before = database_facts(store)
    try:
        result = getattr(service, operation)()
        assert result.diagnostic is not None and result.diagnostic.code == "exit_session_missing"
        assert result.should_exit is should_exit
        assert database_facts(store) == before
        assert factory.calls == 0
    finally:
        store.close()


def test_active_exit_persists_one_exact_request_and_replays_it(store: SQLiteStore) -> None:
    _seed_attempt(store)
    factory = IdentityFactory("xrt_" + "1" * 32, "xrt_" + "2" * 32)
    service = ExitService(
        store=store, clock=FrozenClock(NOW), session_id="ses_1",
        request_id_factory=factory,
    )
    first = service.request_exit()
    second = service.request_exit()
    assert first == second
    assert first.mode == "exit_confirmation_required"
    assert first.should_exit is False
    assert isinstance(first.request, ExitRequest)
    assert first.request.request_id == "xrt_" + "1" * 32
    assert pending_exit_fields(store) == (
        first.request.request_id,
        first.request.attempt.attempt_id,
        first.request.attempt.canonical_bytes().decode("utf-8"),
        first.request.attempt_hash,
        first.request.requested_at,
    )
    assert store.connection.execute(
        "SELECT count(*) FROM events WHERE kind='exit_requested'"
    ).fetchone() == (1,)
    assert store.connection.execute(
        "SELECT aggregate_type,aggregate_id FROM events WHERE kind='exit_requested'"
    ).fetchone() == ("product_session", "ses_1")
    assert factory.calls == 1


def test_exit_supersedes_only_well_formed_drifted_request(store: SQLiteStore) -> None:
    _seed_attempt(store)
    service = _service(store)
    old = service.request_exit().request
    assert old is not None
    advance_active_attempt(store, old.attempt.attempt_id)
    current = service.request_exit().request
    assert current is not None
    assert current.request_id != old.request_id
    assert current.attempt.effect_observed is True
    result = service.confirm(old.request_id, old.attempt_hash)
    assert result.diagnostic is not None
    assert result.diagnostic.code == "exit_request_drift"


def test_malformed_pending_group_is_never_silently_overwritten(store: SQLiteStore) -> None:
    _seed_attempt(store)
    forge_malformed_pending_group(store)
    before = database_facts(store)
    result = _service(store).request_exit()
    assert result.diagnostic is not None
    assert result.diagnostic.code == "exit_request_malformed"
    assert database_facts(store) == before


def test_multiple_active_attempts_are_ambiguous_and_zero_write(store: SQLiteStore) -> None:
    _seed_attempt(store)
    _seed_attempt(store, attempt_id="att_2", task_ordinal=2)
    before = database_facts(store)
    result = _service(store).request_exit()
    assert result.diagnostic is not None
    assert result.diagnostic.code == "exit_active_attempt_ambiguous"
    assert result.should_exit is False
    assert database_facts(store) == before


def test_invalid_factory_identity_is_rejected_before_any_command(store: SQLiteStore) -> None:
    _seed_attempt(store)
    before = database_facts(store)
    with pytest.raises(ValueError):
        _service(store, "bad_request_id").request_exit()
    assert database_facts(store) == before


@pytest.mark.parametrize("decision", ["decline", "confirm"])
def test_stale_exit_decision_rehashes_attempt_and_writes_nothing(
    store: SQLiteStore, active_exit: tuple[ExitService, ExitRequest], decision: str,
) -> None:
    service, request = active_exit
    advance_active_attempt(store, request.attempt.attempt_id)
    before = database_facts(store)
    result = getattr(service, decision)(request.request_id, request.attempt_hash)
    assert result.diagnostic is not None
    assert result.diagnostic.code == "exit_request_drift"
    assert database_facts(store) == before


@pytest.mark.parametrize("decision", ["decline", "confirm"])
@pytest.mark.parametrize("wrong_part", ["request_id", "attempt_hash"])
def test_wrong_exit_identity_writes_nothing_and_keeps_request(
    store: SQLiteStore, active_exit: tuple[ExitService, ExitRequest],
    decision: str, wrong_part: str,
) -> None:
    service, request = active_exit
    request_id = request.request_id
    attempt_hash = request.attempt_hash
    if wrong_part == "request_id":
        request_id = "xrt_" + "f" * 32
    else:
        attempt_hash = "f" * 64
    before = database_facts(store)
    result = getattr(service, decision)(request_id, attempt_hash)
    assert result.diagnostic is not None
    assert result.diagnostic.code == "exit_request_identity_mismatch"
    assert database_facts(store) == before
    assert pending_exit_fields(store) != (None,) * 5


@pytest.mark.parametrize("decision", ["decline", "confirm"])
def test_missing_pending_attempt_writes_nothing_and_keeps_request(
    store: SQLiteStore, active_exit: tuple[ExitService, ExitRequest], decision: str,
) -> None:
    service, request = active_exit
    store._require_writer().execute(
        "DELETE FROM attempts WHERE attempt_id=?", (request.attempt.attempt_id,)
    )
    before = database_facts(store)
    result = getattr(service, decision)(request.request_id, request.attempt_hash)
    assert result.diagnostic is not None
    assert result.diagnostic.code == "exit_attempt_missing"
    assert database_facts(store) == before


@pytest.mark.parametrize("decision", ["decline", "confirm"])
def test_malformed_pending_decision_is_zero_write(
    store: SQLiteStore, decision: str,
) -> None:
    _seed_attempt(store)
    forge_malformed_pending_group(store)
    before = database_facts(store)
    result = getattr(_service(store), decision)("xrt_" + "f" * 32, "f" * 64)
    assert result.diagnostic is not None
    assert result.diagnostic.code == "exit_request_malformed"
    assert database_facts(store) == before


def test_exact_decline_consumes_request_but_never_changes_attempt(
    store: SQLiteStore, active_exit: tuple[ExitService, ExitRequest],
) -> None:
    service, request = active_exit
    before_attempt = store.load_aggregate("attempts", request.attempt.attempt_id)
    result = service.decline(request.request_id, request.attempt_hash)
    assert result == ExitResult(mode="exit_declined", should_exit=False)
    assert pending_exit_fields(store) == (None,) * 5
    assert store.load_aggregate("attempts", request.attempt.attempt_id) == before_attempt
    assert store.connection.execute(
        "SELECT count(*) FROM events WHERE kind='exit_declined'"
    ).fetchone() == (1,)


def test_decline_then_request_same_attempt_creates_new_lineage(
    store: SQLiteStore, active_exit: tuple[ExitService, ExitRequest],
) -> None:
    service, first = active_exit
    service.decline(first.request_id, first.attempt_hash)
    second = service.request_exit().request
    assert second is not None
    assert second.request_id != first.request_id
    assert second.attempt_hash == first.attempt_hash


@pytest.mark.parametrize(
    ("mode", "code", "known", "should_exit"),
    [("project_paused", None, True, True),
     ("diagnostic", "cancel_timeout", False, False)],
)
def test_closed_active_exit_result_has_exact_seven_content_free_fields(
    active_exit, mode, code, known, should_exit,
) -> None:
    _, request = active_exit
    result = closed_exit_result(
        request=request, mode=mode, diagnostic_code=code,
        outcome_known=known, should_exit=should_exit,
    )
    assert set(result) == ACTIVE_EXIT_RESULT_FIELDS
    assert (result["mode"], result["diagnostic_code"]) == (mode, code)
    assert result["should_exit"] is should_exit
    assert type(result["outcome_known"]) is bool
    projected = exit_result_from_command(result, clock=FrozenClock(NOW))
    assert (projected.mode, projected.should_exit) == (mode, should_exit)


def test_between_stage_result_uses_null_attempt_fields_and_malformed_replay_fails():
    result = closed_exit_result(
        request=None, mode="project_paused", diagnostic_code=None,
        outcome_known=True, should_exit=True,
    )
    assert result["attempt_id"] is result["attempt_hash"] is None
    with pytest.raises(ValueError, match="stored exit result"):
        exit_result_from_command({**result, "raw_output": "secret"}, clock=FrozenClock(NOW))


def test_input_closed_is_read_only_and_requires_recovery_for_active_work(
    store: SQLiteStore,
) -> None:
    _seed_attempt(store)
    before = database_facts(store)
    result = _service(store).input_closed()
    assert result.diagnostic is not None
    assert result.diagnostic.code == "exit_input_closed_with_active_work"
    assert result.should_exit is True
    assert database_facts(store) == before


@pytest.mark.parametrize("decision", ["decline", "confirm"])
@pytest.mark.parametrize("matching", [False, True])
def test_valid_external_decision_hash_uses_constant_time_comparison_once(
    active_exit: tuple[ExitService, ExitRequest],
    monkeypatch: pytest.MonkeyPatch, decision: str, matching: bool,
) -> None:
    service, request = active_exit
    supplied = request.attempt_hash if matching else "f" * 64
    calls: list[tuple[str, str]] = []
    def compare(left: str, right: str) -> bool:
        calls.append((left, right))
        return real_compare_digest(left, right)
    monkeypatch.setattr(exit_module, "compare_digest", compare)
    result = getattr(service, decision)(request.request_id, supplied)
    if not matching:
        assert result.diagnostic is not None
        assert result.diagnostic.code == "exit_request_identity_mismatch"
    else:
        assert result.mode in {"exit_declined", "exit_confirmation_ready"}
    assert calls == [(supplied, request.attempt_hash)]

def test_committed_decline_replays_before_pending_preflight(
    store: SQLiteStore, active_exit: tuple[ExitService, ExitRequest],
) -> None:
    service, request = active_exit
    first = service.decline(request.request_id, request.attempt_hash)
    before = database_facts(store)
    replay = service.decline(request.request_id, request.attempt_hash)
    assert replay == first == ExitResult("exit_declined", False)
    assert database_facts(store) == before


@pytest.mark.parametrize("corruption", ["kind", "result"])
def test_conflicting_or_malformed_completed_decline_fails_closed(
    store: SQLiteStore, active_exit: tuple[ExitService, ExitRequest], corruption: str,
) -> None:
    service, request = active_exit
    service.decline(request.request_id, request.attempt_hash)
    command_id = f"exit:decline:ses_1:{request.request_id}"
    if corruption == "kind":
        store._require_writer().execute(
            "UPDATE commands SET command_kind='conflicting' WHERE command_id=?",
            (command_id,),
        )
    else:
        store._require_writer().execute(
            "UPDATE commands SET canonical_result_facts='{}' WHERE command_id=?",
            (command_id,),
        )
    before = database_facts(store)
    result = service.decline(request.request_id, request.attempt_hash)
    assert result.diagnostic is not None
    assert result.diagnostic.code == "exit_authority_invalid"
    assert database_facts(store) == before

def test_decline_replay_validates_external_hash_in_constant_time(
    active_exit: tuple[ExitService, ExitRequest], monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, request = active_exit
    service.decline(request.request_id, request.attempt_hash)
    supplied = "f" * 64
    calls: list[tuple[str, str]] = []
    def compare(left: str, right: str) -> bool:
        calls.append((left, right))
        return real_compare_digest(left, right)
    monkeypatch.setattr(exit_module, "compare_digest", compare)
    result = service.decline(request.request_id, supplied)
    assert result.diagnostic is not None
    assert result.diagnostic.code == "exit_request_identity_mismatch"
    assert calls == [(supplied, request.attempt_hash)]

def test_decline_audit_uses_fresh_time_and_replay_does_not_resample(
    store: SQLiteStore,
) -> None:
    _seed_attempt(store)
    requested = NOW
    decided = datetime(2026, 7, 19, 4, 0, tzinfo=timezone.utc)
    clock = AdvancingClock(requested, decided)
    service = ExitService(
        store=store, clock=clock, session_id="ses_1",
        request_id_factory=IdentityFactory("xrt_" + "1" * 32),
    )
    request = service.request_exit().request
    assert request is not None
    first = service.decline(request.request_id, request.attempt_hash)
    replay = service.decline(request.request_id, request.attempt_hash)
    rows = store.connection.execute(
        "SELECT kind,occurred_at FROM events "
        "WHERE kind IN ('exit_requested','exit_declined') ORDER BY occurred_at"
    ).fetchall()
    assert replay == first == ExitResult("exit_declined", False)
    assert rows == [
        ("exit_requested", requested.isoformat()),
        ("exit_declined", decided.isoformat()),
    ]
    assert clock.calls == 2
