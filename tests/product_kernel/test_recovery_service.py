from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import inspect
from pathlib import Path

import pytest

import agentdeck.application.recovery_service as recovery_module
from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.recovery_service import (
    ReconnectStatus,
    RecoveryError,
    RecoveryOutcome,
    RecoveryService,
)
from agentdeck.application.session_service import SessionService
from agentdeck.kernel.events import DomainEvent
from agentdeck.kernel.execution import AttemptState
from agentdeck.ports.store import RunningAttempt

from .fakes import FrozenClock


NOW = datetime(2026, 7, 19, 4, 5, 6, tzinfo=timezone.utc)


class FakeTransaction:
    def __init__(self, store: "FakeStore") -> None:
        self.store = store

    def recover_attempt(
        self, attempt_id: str, state: AttemptState, reason: str | None,
        *, expected: RunningAttempt | None = None,
    ) -> None:
        if self.store.fail_on == "transition":
            raise RuntimeError("store transition failed")
        attempt, _, _ = self.store.attempts[attempt_id]
        self.store.attempts[attempt_id] = (attempt, state, reason)
        self.store.transitions.append((attempt_id, state, reason))

    def append_event(self, event: DomainEvent) -> None:
        if self.store.fail_on == "event":
            raise RuntimeError("store event failed")
        self.store.events.append(event)


class FakeStore:
    def __init__(self, attempts: list[RunningAttempt], *, fail_on: str | None = None) -> None:
        self.attempts = {
            attempt.attempt_id: (attempt, AttemptState.RUNNING, None)
            for attempt in attempts
        }
        self.commands: dict[str, tuple[str, dict[str, object]]] = {}
        self.transitions: list[tuple[str, AttemptState, str | None]] = []
        self.events: list[DomainEvent] = []
        self.fail_on = fail_on

    def list_running_attempts(self) -> tuple[RunningAttempt, ...]:
        return tuple(
            fact
            for fact, state, reason in reversed(tuple(self.attempts.values()))
            if state is AttemptState.RUNNING
        )

    def lookup_command(
        self, command_id: str, command_kind: str | None = None
    ) -> dict[str, object] | None:
        row = self.commands.get(command_id)
        if row is not None and command_kind is not None:
            assert row[0] == command_kind
        return None if row is None else deepcopy(row[1])

    def execute_once(self, command_id: str, command_kind: str, callback):
        existing = self.commands.get(command_id)
        if existing is not None:
            assert existing[0] == command_kind
            return deepcopy(existing[1])
        before = deepcopy((self.attempts, self.transitions, self.events))
        try:
            result = callback(FakeTransaction(self))
            self.commands[command_id] = (command_kind, deepcopy(result))
            return deepcopy(result)
        except BaseException:
            self.attempts, self.transitions, self.events = before
            raise


class FakeTransport:
    def __init__(
        self,
        statuses: dict[str, ReconnectStatus | Exception],
    ) -> None:
        self.statuses = statuses
        self.calls: list[str] = []

    def reconcile(self, acp_session_id: str) -> ReconnectStatus:
        self.calls.append(acp_session_id)
        result = self.statuses[acp_session_id]
        if isinstance(result, Exception):
            raise result
        return result


class TransactionAwareTransport(FakeTransport):
    def __init__(self, store: SQLiteStore, status: ReconnectStatus) -> None:
        super().__init__({"acp_1": status})
        self.store = store

    def reconcile(self, acp_session_id: str) -> ReconnectStatus:
        assert not self.store._writer.in_transaction
        return super().reconcile(acp_session_id)


def _attempt(
    attempt_id: str = "att_1",
    *,
    session: str | None = "acp_1",
    effect_observed: bool = False,
) -> RunningAttempt:
    return RunningAttempt(attempt_id, f"tsk_{attempt_id[4:]}", session, effect_observed)


def _service(
    store: FakeStore, transport: FakeTransport, run_id: str = "restart_1"
) -> RecoveryService:
    return RecoveryService(store, transport, FrozenClock(NOW), run_id)


def test_confirmed_reconnect_keeps_attempt_running_and_reports_resumed() -> None:
    store = FakeStore([_attempt()])
    transport = FakeTransport({"acp_1": ReconnectStatus.CONFIRMED})

    report = _service(store, transport).reconcile()

    assert report.resumed == ("att_1",)
    assert report.interrupted == report.outcome_unknown == ()
    assert store.transitions == [("att_1", AttemptState.RUNNING, None)]


def test_lost_session_without_observed_effect_becomes_interrupted() -> None:
    store = FakeStore([_attempt()])
    transport = FakeTransport({"acp_1": ReconnectStatus.LOST})

    report = _service(store, transport).reconcile()

    assert report.interrupted == ("att_1",)
    assert store.transitions == [
        ("att_1", AttemptState.INTERRUPTED, "acp_session_lost")
    ]


@pytest.mark.parametrize(
    ("status", "expected_reason"),
    [
        (ReconnectStatus.UNCERTAIN, "reconnect_uncertain"),
        (RuntimeError("transport broke"), "transport_reconcile_failed"),
    ],
)
def test_uncertain_or_exceptional_reconnect_becomes_outcome_unknown(
    status: ReconnectStatus | Exception, expected_reason: str
) -> None:
    store = FakeStore([_attempt()])
    transport = FakeTransport({"acp_1": status})

    report = _service(store, transport).reconcile()

    assert report.outcome_unknown == ("att_1",)
    assert store.transitions == [
        ("att_1", AttemptState.OUTCOME_UNKNOWN, expected_reason)
    ]


def test_lost_session_after_observed_effect_is_unknown_and_never_retryable() -> None:
    store = FakeStore([_attempt(effect_observed=True)])
    transport = FakeTransport({"acp_1": ReconnectStatus.LOST})

    report = _service(store, transport).reconcile()

    assert report.outcome_unknown == ("att_1",)
    assert report.retryable == ()
    assert store.transitions == [
        ("att_1", AttemptState.OUTCOME_UNKNOWN, "side_effect_observed")
    ]


@pytest.mark.parametrize(
    ("effect_observed", "outcome"),
    [
        (False, RecoveryOutcome.INTERRUPTED),
        (True, RecoveryOutcome.OUTCOME_UNKNOWN),
    ],
)
def test_missing_acp_session_counts_lost_unless_effect_is_uncertain(
    effect_observed: bool, outcome: RecoveryOutcome
) -> None:
    store = FakeStore([_attempt(session=None, effect_observed=effect_observed)])
    transport = FakeTransport({})

    report = _service(store, transport).reconcile()

    assert report.outcomes == (("att_1", outcome),)
    assert transport.calls == []


def test_mixed_recovery_is_deterministically_sorted() -> None:
    attempts = [
        _attempt("att_3", session="acp_3", effect_observed=True),
        _attempt("att_1", session="acp_1"),
        _attempt("att_2", session="acp_2"),
    ]
    store = FakeStore(attempts)
    transport = FakeTransport({
        "acp_1": ReconnectStatus.CONFIRMED,
        "acp_2": ReconnectStatus.LOST,
        "acp_3": ReconnectStatus.LOST,
    })
    first = _service(store, transport).reconcile()

    assert first.resumed == ("att_1",)
    assert first.interrupted == ("att_2",)
    assert first.outcome_unknown == ("att_3",)
    assert tuple(store.commands) == (
        "recover:restart_1:att_1", "recover:restart_1:att_2",
        "recover:restart_1:att_3",
    )
    assert len(store.events) == len(store.transitions) == 3
    assert transport.calls == ["acp_1", "acp_2", "acp_3"]


def test_same_recovery_run_replays_without_second_transport_call_or_event() -> None:
    store = FakeStore([_attempt()])
    transport = FakeTransport({"acp_1": ReconnectStatus.CONFIRMED})
    service = _service(store, transport, "restart_same")

    first = service.reconcile()
    transport.statuses["acp_1"] = ReconnectStatus.LOST
    second = service.reconcile()

    assert first == second
    assert transport.calls == ["acp_1"]
    assert len(store.events) == len(store.transitions) == 1
    assert tuple(store.commands) == ("recover:restart_same:att_1",)


def test_new_recovery_run_reconciles_still_running_attempt_again() -> None:
    store = FakeStore([_attempt()])
    transport = FakeTransport({"acp_1": ReconnectStatus.CONFIRMED})

    assert _service(store, transport, "restart_1").reconcile().resumed == ("att_1",)
    transport.statuses["acp_1"] = ReconnectStatus.LOST
    second = _service(store, transport, "restart_2").reconcile()

    assert second.interrupted == ("att_1",)
    assert transport.calls == ["acp_1", "acp_1"]
    assert len(store.events) == len(store.transitions) == 2
    assert tuple(store.commands) == (
        "recover:restart_1:att_1", "recover:restart_2:att_1"
    )
    assert len({event.event_id for event in store.events}) == 2
    expected = {
        f"evt_{sha256(f'recovery-event:{command_id}'.encode()).hexdigest()[:32]}"
        for command_id in store.commands
    }
    assert {event.event_id for event in store.events} == expected


def test_transition_and_recovery_event_are_one_atomic_store_command() -> None:
    store = FakeStore([_attempt()], fail_on="event")
    transport = FakeTransport({"acp_1": ReconnectStatus.LOST})

    with pytest.raises(RuntimeError, match="event failed"):
        _service(store, transport).reconcile()

    assert store.transitions == []
    assert store.events == []
    assert store.commands == {}
    assert store.list_running_attempts() == (_attempt(),)


def test_recovery_event_uses_injected_clock_and_has_no_terminal_dependency() -> None:
    store = FakeStore([_attempt()])
    transport = FakeTransport({"acp_1": ReconnectStatus.LOST})

    _service(store, transport).reconcile()

    assert len(store.events) == 1
    event = store.events[0]
    assert event.occurred_at == "2026-07-19T04:05:06+00:00"
    assert event.aggregate_id == "att_1"
    source = inspect.getsource(recovery_module).lower()
    assert "tmux" not in source
    assert "agentdeck.adapters" not in source


@pytest.mark.parametrize("run_id", [None, "", " ", "bad id", "\ud800", "x" * 129])
def test_recovery_run_id_must_be_bounded_strict_identifier(run_id: object) -> None:
    store = FakeStore([_attempt()])
    transport = FakeTransport({"acp_1": ReconnectStatus.CONFIRMED})

    with pytest.raises((TypeError, ValueError), match="recovery_run_id"):
        RecoveryService(store, transport, FrozenClock(NOW), run_id)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("store", "transport", "clock"),
    [
        (None, FakeTransport({}), FrozenClock(NOW)),
        (FakeStore([]), None, FrozenClock(NOW)),
        (FakeStore([]), FakeTransport({}), None),
    ],
)
def test_recovery_dependencies_fail_closed(store, transport, clock) -> None:
    with pytest.raises(TypeError, match="store|transport|clock"):
        RecoveryService(store, transport, clock, "restart_1")


def test_combined_recovery_command_identity_is_bounded() -> None:
    suffix = "x" * 240
    attempt = RunningAttempt(f"att_{suffix}", "tsk_1", "acp_1")
    store = FakeStore([attempt])
    transport = FakeTransport({"acp_1": ReconnectStatus.CONFIRMED})

    with pytest.raises(RecoveryError, match="command identity"):
        _service(store, transport).reconcile()
    assert transport.calls == []
    assert store.commands == {}


def _seed_sqlite_running_attempt(store: SQLiteStore) -> None:
    now = "2026-07-19T04:05:06+00:00"
    session = SessionService(
        store=store,
        clock=FrozenClock(NOW),
        session_id="ses_1",
        project_root=str(store._project_root),
        available_leaders={"codex-cli": ("native-default",)},
    )
    session.configure(leader="codex-cli", model="native-default")
    configured = session.current()
    with store.command("cmd_test_fixture", "test_fixture") as transaction:
        transaction.save_session({
            "session_id": "ses_1", "state": "running",
            "permission_profile": configured.permission,
            "pending_goal": configured.pending_goal,
        })
        project_id = transaction.load_aggregate("product_sessions", "ses_1")["project_id"]
        connection = store._writer
        connection.execute(
            "INSERT INTO agent_instances VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("agt_1", "ses_1", "codex", "acp", "1", "implementer", "acp_1",
             "active", now, now),
        )
        connection.execute(
            "INSERT INTO missions VALUES (?,?,?,?,?,?)",
            ("mis_1", "ses_1", "running", 1, now, now),
        )
        connection.execute(
            "INSERT INTO mission_versions VALUES (?,?,?,?,?,?)",
            ("mis_1", 1, "prv_1", "0" * 64, "{}", now),
        )
        connection.execute(
            "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("tsk_1", "mis_1", 1, 1, "implement", "implementer", "codex",
             "agt_1", "acp://codex", "running", "{}", now, now),
        )
        connection.execute(
            "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("att_1", "tsk_1", "agt_1", 1, "running", None, None, 0,
             "acp_1", 0, now, now),
        )
        assert type(project_id) is str


def test_sqlite_recovery_fixture_preserves_exact_session_reentry_authority(
    tmp_path: Path,
) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    _seed_sqlite_running_attempt(store)
    try:
        restored = SessionService(
            store=store, clock=FrozenClock(NOW), session_id="ses_1",
            project_root=str(tmp_path),
            available_leaders={"codex-cli": ("native-default",)},
        ).current()
        assert (restored.leader_backend, restored.model, restored.permission) == (
            "codex-cli", "native-default", "approve_for_me",
        )
        assert restored.pending_goal is None
        assert restored.state.value == "running"
    finally:
        store.close()


def test_sqlite_reopen_replays_same_run_then_new_run_reconciles(
    tmp_path: Path,
) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    _seed_sqlite_running_attempt(store)
    first_transport = TransactionAwareTransport(store, ReconnectStatus.CONFIRMED)
    assert RecoveryService(
        store, first_transport, FrozenClock(NOW), "restart_1"
    ).reconcile().resumed == ("att_1",)
    store.close()

    reopened = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    same_transport = FakeTransport({"acp_1": ReconnectStatus.LOST})
    assert RecoveryService(
        reopened, same_transport, FrozenClock(NOW), "restart_1"
    ).reconcile().resumed == ("att_1",)
    assert same_transport.calls == []

    later_transport = TransactionAwareTransport(reopened, ReconnectStatus.LOST)
    assert RecoveryService(
        reopened, later_transport, FrozenClock(NOW), "restart_2"
    ).reconcile().interrupted == ("att_1",)
    assert later_transport.calls == ["acp_1"]
    assert reopened.connection.execute(
        "SELECT state FROM attempts WHERE attempt_id='att_1'"
    ).fetchone() == ("interrupted",)
    commands = reopened.connection.execute(
        "SELECT command_id FROM commands WHERE command_id LIKE 'recover:%' ORDER BY command_id"
    ).fetchall()
    assert commands == [
        ("recover:restart_1:att_1",), ("recover:restart_2:att_1",)
    ]
    events = reopened.connection.execute(
        "SELECT event_id FROM events WHERE kind='attempt_recovered' ORDER BY event_id"
    ).fetchall()
    assert len(events) == len({row[0] for row in events}) == 2
    reopened.close()


@pytest.mark.parametrize(
    "assignment", ["reason='forged'", "retryable=1"]
)
def test_sqlite_recovery_rejects_malformed_running_row_before_transport(
    tmp_path: Path, assignment: str
) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    _seed_sqlite_running_attempt(store)
    store._writer.execute(f"UPDATE attempts SET {assignment} WHERE attempt_id='att_1'")
    transport = FakeTransport({"acp_1": ReconnectStatus.LOST})
    try:
        with pytest.raises((RuntimeError, ValueError)):
            RecoveryService(store, transport, FrozenClock(NOW), "restart_bad").reconcile()
        assert transport.calls == []
        assert store.connection.execute(
            "SELECT count(*) FROM commands WHERE command_id LIKE 'recover:%'"
        ).fetchone() == (0,)
        assert store.connection.execute(
            "SELECT count(*) FROM events WHERE kind='attempt_recovered'"
        ).fetchone() == (0,)
    finally:
        store.close()


class CorruptingTransport(TransactionAwareTransport):
    def __init__(
        self, store: SQLiteStore, status: ReconnectStatus, assignment: str
    ) -> None:
        super().__init__(store, status)
        self.assignment = assignment

    def reconcile(self, acp_session_id: str) -> ReconnectStatus:
        result = super().reconcile(acp_session_id)
        self.store._writer.execute(
            f"UPDATE attempts SET {self.assignment} WHERE attempt_id='att_1'"
        )
        return result


@pytest.mark.parametrize("assignment", ["reason='forged'", "effect_observed=1"])
def test_sqlite_recovery_revalidates_row_after_transport_before_writing(
    tmp_path: Path, assignment: str,
) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    _seed_sqlite_running_attempt(store)
    transport = CorruptingTransport(store, ReconnectStatus.LOST, assignment)
    try:
        with pytest.raises((RuntimeError, ValueError)):
            RecoveryService(store, transport, FrozenClock(NOW), "restart_drift").reconcile()
        assert transport.calls == ["acp_1"]
        assert store.connection.execute(
            "SELECT count(*) FROM commands WHERE command_id LIKE 'recover:%'"
        ).fetchone() == (0,)
        assert store.connection.execute(
            "SELECT count(*) FROM events WHERE kind='attempt_recovered'"
        ).fetchone() == (0,)
    finally:
        store.close()
