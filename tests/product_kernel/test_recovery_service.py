from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from functools import wraps
import inspect

import pytest

import agentdeck.application.recovery_service as recovery_module
from agentdeck.application.recovery_service import (
    RecoveryError,
    RecoveryOutcome,
    RecoveryService,
)
from agentdeck.kernel.events import DomainEvent
from agentdeck.kernel.execution import AttemptState
from agentdeck.kernel.session import ExitAttemptSnapshot

from .fakes import FrozenClock


NOW = datetime(2026, 7, 20, 4, 5, 6, tzinfo=timezone.utc)
PENDING_FIELDS = (
    "pending_exit_id", "pending_exit_attempt_id",
    "canonical_pending_exit_attempt_facts", "pending_exit_attempt_hash",
    "pending_exit_requested_at",
)


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


def _attempt(
    attempt_id: str = "att_1", *, effect_observed: bool = False,
    state: AttemptState = AttemptState.RUNNING,
) -> ExitAttemptSnapshot:
    return ExitAttemptSnapshot(
        attempt_id, f"tsk_{attempt_id[4:]}", f"agt_{attempt_id[4:]}", 1,
        state, f"ses_acp_{attempt_id[4:]}", effect_observed, "a" * 64,
    )


class FakeTransaction:
    def __init__(self, store: "FakeStore") -> None:
        self.store = store

    def load_aggregate(self, kind: str, identity: str):
        assert (kind, identity) == ("product_sessions", self.store.session_id)
        return deepcopy(self.store.session)

    def list_active_exit_attempts(self, session_id: str):
        assert session_id == self.store.session_id
        if self.store.drift_on_transaction and self.store.attempts:
            attempt = self.store.attempts[0]
            return (ExitAttemptSnapshot(
                attempt.attempt_id, attempt.task_id, attempt.agent_instance_id,
                attempt.ordinal + 1, attempt.state, attempt.acp_session_id,
                attempt.effect_observed, "b" * 64,
            ),)
        return tuple(self.store.attempts)

    def save_attempt(self, snapshot):
        identity = snapshot["attempt_id"]
        self.store.attempt_states[identity] = (
            snapshot["state"], snapshot["reason"]
        )
        self.store.attempts = tuple(
            item for item in self.store.attempts if item.attempt_id != identity
        )

    def save_session(self, snapshot):
        self.store.session.update(snapshot)

    def append_event(self, event: DomainEvent):
        if self.store.fail_event:
            raise RuntimeError("event write failed")
        self.store.events.append(event)


class FakeStore:
    def __init__(
        self, attempts: tuple[ExitAttemptSnapshot, ...] = (), *,
        state: str = "running", pending: dict[str, object] | None = None,
    ) -> None:
        self.session_id = "ses_1"
        self.attempts = attempts
        self.attempt_states = {
            item.attempt_id: (item.state.value, None) for item in attempts
        }
        self.session = {
            "session_id": self.session_id, "state": state,
            **dict.fromkeys(PENDING_FIELDS), **(pending or {}),
        }
        self.commands: dict[str, tuple[str, dict[str, object]]] = {}
        self.events: list[DomainEvent] = []
        self.fail_event = False
        self.drift_on_transaction = False

    def list_active_exit_attempts(self, session_id: str):
        assert session_id == self.session_id
        return tuple(self.attempts)

    def load_aggregate(self, kind: str, identity: str):
        return FakeTransaction(self).load_aggregate(kind, identity)

    def lookup_command(self, command_id: str, command_kind: str | None = None):
        value = self.commands.get(command_id)
        if value is not None and command_kind is not None:
            assert value[0] == command_kind
        return None if value is None else deepcopy(value[1])

    def execute_once(self, command_id: str, command_kind: str, callback):
        replay = self.lookup_command(command_id, command_kind)
        if replay is not None:
            return replay
        before = deepcopy((
            self.attempts, self.attempt_states, self.session, self.events,
            self.commands,
        ))
        try:
            result = callback(FakeTransaction(self))
            self.commands[command_id] = (command_kind, deepcopy(result))
            return deepcopy(result)
        except BaseException:
            (
                self.attempts, self.attempt_states, self.session, self.events,
                self.commands,
            ) = before
            raise


def _service(store: FakeStore, run_id: str = "restart_1") -> RecoveryService:
    return RecoveryService(
        store=store, clock=FrozenClock(NOW), session_id="ses_1",
        recovery_run_id=run_id,
    )


@async_test
@pytest.mark.parametrize(
    ("effect_observed", "outcome", "state", "reason"),
    [
        (False, RecoveryOutcome.INTERRUPTED, "interrupted", "process_binding_lost"),
        (True, RecoveryOutcome.OUTCOME_UNKNOWN, "outcome_unknown", "effects_may_exist"),
    ],
)
async def test_fresh_process_conservatively_pauses_abandoned_attempt(
    effect_observed: bool, outcome: RecoveryOutcome, state: str, reason: str,
) -> None:
    store = FakeStore((_attempt(effect_observed=effect_observed),))

    report = await _service(store).reconcile()

    assert report.outcomes == (("att_1", outcome),)
    assert store.attempt_states["att_1"] == (state, reason)
    assert store.session["state"] == "paused"
    assert all(store.session[field] is None for field in PENDING_FIELDS)
    assert {event.kind for event in store.events} == {
        "attempt_recovered", "project_paused",
    }


@async_test
async def test_running_session_without_attempt_is_paused_after_resume_crash() -> None:
    store = FakeStore()

    report = await _service(store).reconcile()

    assert report.outcomes == ()
    assert store.session["state"] == "paused"
    assert [event.kind for event in store.events] == ["project_paused"]


@async_test
async def test_paused_session_recovery_is_observational_and_replay_is_stable() -> None:
    store = FakeStore((_attempt(),))
    service = _service(store)

    first = await service.reconcile()
    second = await service.reconcile()

    assert first == second
    assert len(store.commands) == 1
    assert len(store.events) == 2
    assert store.session["state"] == "paused"


@async_test
@pytest.mark.parametrize(
    "attempts",
    [(_attempt(), _attempt()), (_attempt("att_1"), _attempt("att_2"))],
)
async def test_ambiguous_active_attempts_fail_closed_before_any_write(attempts) -> None:
    store = FakeStore(attempts)
    before = deepcopy((store.session, store.attempt_states, store.commands, store.events))

    with pytest.raises(RecoveryError, match="ambiguous"):
        await _service(store).reconcile()

    assert (store.session, store.attempt_states, store.commands, store.events) == before


@async_test
async def test_partial_pending_exit_fails_closed_before_any_write() -> None:
    store = FakeStore(pending={"pending_exit_id": "xrt_" + "1" * 32})
    before = deepcopy((store.session, store.commands, store.events))

    with pytest.raises(RecoveryError, match="authority"):
        await _service(store).reconcile()

    assert (store.session, store.commands, store.events) == before


@async_test
async def test_paused_session_clears_complete_stale_pending_exit_group() -> None:
    pending = dict(zip(PENDING_FIELDS, (
        "xrt_" + "1" * 32, "att_1", "{}", "a" * 64, NOW.isoformat(),
    ), strict=True))
    store = FakeStore(state="paused", pending=pending)

    report = await _service(store).reconcile()

    assert report == recovery_module.RecoveryReport()
    assert all(store.session[field] is None for field in PENDING_FIELDS)
    assert [event.kind for event in store.events] == ["project_paused"]


@async_test
@pytest.mark.parametrize("failure", ["drift", "event"])
async def test_recovery_fingerprint_or_event_failure_rolls_back(failure: str) -> None:
    store = FakeStore((_attempt(),))
    store.drift_on_transaction = failure == "drift"
    store.fail_event = failure == "event"
    before = deepcopy((store.session, store.attempt_states, store.commands, store.events))

    with pytest.raises((RecoveryError, RuntimeError)):
        await _service(store).reconcile()

    assert (store.session, store.attempt_states, store.commands, store.events) == before


def test_recovery_has_no_transport_backend_model_role_or_tmux_fallback() -> None:
    source = inspect.getsource(recovery_module).lower()
    assert all(word not in source for word in (
        "transport", "backend", "model", "role", "pane", "tmux",
        "list_running_attempts",
    ))


@pytest.mark.parametrize("run_id", [None, "", "bad id", "bad:id", "\ud800", "x" * 129])
def test_recovery_run_id_is_a_bounded_strict_identifier(run_id: object) -> None:
    with pytest.raises((TypeError, ValueError), match="recovery_run_id"):
        RecoveryService(
            store=FakeStore(), clock=FrozenClock(NOW), session_id="ses_1",
            recovery_run_id=run_id,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("dependency", ["store", "clock"])
def test_recovery_dependencies_fail_closed(dependency: str) -> None:
    values = {"store": FakeStore(), "clock": FrozenClock(NOW)}
    values[dependency] = None
    with pytest.raises(TypeError, match=dependency):
        RecoveryService(
            **values, session_id="ses_1", recovery_run_id="restart_1"
        )


@async_test
async def test_recovery_command_identity_is_bounded_before_store_access() -> None:
    store = FakeStore()
    service = RecoveryService(
        store=store, clock=FrozenClock(NOW), session_id="ses_" + "s" * 120,
        recovery_run_id="r" * 128,
    )

    with pytest.raises(RecoveryError, match="Store limit"):
        await service.reconcile()

    assert store.commands == {}
    assert store.events == []
