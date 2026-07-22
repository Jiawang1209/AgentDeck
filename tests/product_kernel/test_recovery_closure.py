"""Close outcome-unknown, observer degradation, and resume recovery."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

import pytest

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.recovery_service import (
    RecoveryError,
    RecoveryService,
)
from agentdeck.kernel.execution import Attempt

from .fakes import FrozenClock
from .test_product_shell import AsyncLines, _build
from .test_sqlite_execution_resume import _seed_base
from .test_sqlite_recovery_integrity import _recovery, _running_attempt


NOW = datetime(2026, 7, 20, 4, 5, 6, tzinfo=timezone.utc)


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


def attempt(state: str = "outcome_unknown", attempt_id: str = "att_resume_1") -> Attempt:
    """Build a real kernel Attempt in the requested state for resume tests."""

    base = Attempt.pending(attempt_id, "tsk_recovery", 1).start()
    if state == "running":
        return base
    if state == "awaiting_approval":
        return base.await_approval()
    if state == "human_controlled":
        return base.take_human_control()
    if state == "outcome_unknown":
        return base.unknown_outcome("worker_bridge_failed")
    if state == "interrupted":
        return base.interrupt("execution_start_failed")
    if state == "failed_retryable":
        return base.fail("worker_failed")
    if state == "failed_not_retryable":
        return base.fail("acceptance_failed")
    if state == "cancelled":
        return base.cancel("permission_denied")
    if state == "completed":
        return base.complete("attempt complete")
    raise ValueError(f"unsupported test attempt state: {state}")


@pytest.fixture
def store(tmp_path: Path):
    value = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    _seed_base(value)
    try:
        yield value
    finally:
        value.close()


@pytest.fixture
def recovery(store: SQLiteStore) -> RecoveryService:
    return _recovery(store)


@pytest.mark.parametrize("condition,action", [
    ("observer_down_worker_alive", "restart_observer"),
    ("transport_before_effect", "reconnect_once"),
    ("transport_after_effect", "human_reconcile"),
    ("login_lost", "reauthenticate_outside_agentdeck"),
    ("project_drift", "inspect_diff"),
])
def test_recovery_actions_are_condition_specific(condition, action, recovery):
    result = recovery.assess(condition)
    assert action in result.actions


def test_outcome_unknown_cannot_resume_or_retry_without_reconciliation(recovery):
    result = recovery.resume_attempt(attempt(state="outcome_unknown"))
    assert result.accepted is False
    assert result.diagnostic.outcome_known is False


def test_observer_degradation_never_affects_the_worker(recovery):
    result = recovery.assess("observer_down_worker_alive")
    assert result.diagnostic is not None
    assert result.diagnostic.severity.value == "warning"


def test_transport_before_effect_is_the_single_reconnect_budget(recovery):
    result = recovery.assess("transport_before_effect")
    assert result.actions == ("reconnect_once",)


def test_login_lost_never_auto_authenticates(recovery):
    result = recovery.assess("login_lost")
    assert result.actions == ("reauthenticate_outside_agentdeck",)
    assert result.diagnostic is not None


@pytest.mark.parametrize("condition", [
    "unknown_condition", "", "TRANSPORT_BEFORE_EFFECT", "observer_down",
])
def test_unknown_condition_is_refused_not_defaulted(condition, recovery):
    with pytest.raises((RecoveryError, ValueError)):
        recovery.assess(condition)


@pytest.mark.parametrize("state,accepted", [
    ("interrupted", True),
    ("failed_retryable", True),
    ("failed_not_retryable", False),
    ("cancelled", False),
    ("completed", False),
    ("outcome_unknown", False),
    ("running", False),
    ("awaiting_approval", False),
    ("human_controlled", False),
])
def test_resume_decision_is_fail_closed_by_state(state, accepted, recovery):
    result = recovery.resume_attempt(attempt(state=state, attempt_id=f"att_{state}"))
    assert result.accepted is accepted
    assert result.diagnostic is not None


def test_resume_attempt_never_mutates_attempt_or_session_state_on_rejection(
    store: SQLiteStore, recovery: RecoveryService,
) -> None:
    _running_attempt(store, effect_observed=True)
    before_attempt = store.connection.execute(
        "SELECT state FROM attempts WHERE attempt_id='att_recovery_1'"
    ).fetchone()
    before_session = store.load_aggregate("product_sessions", "ses_1")

    result = recovery.resume_attempt(attempt(state="outcome_unknown"))

    assert result.accepted is False
    assert store.connection.execute(
        "SELECT state FROM attempts WHERE attempt_id='att_recovery_1'"
    ).fetchone() == before_attempt
    assert store.load_aggregate("product_sessions", "ses_1") == before_session


def test_resume_attempt_is_idempotent_and_never_double_writes(
    store: SQLiteStore, recovery: RecoveryService,
) -> None:
    before = store.count("events")

    first = recovery.resume_attempt(attempt(state="interrupted"))
    second = recovery.resume_attempt(attempt(state="interrupted"))

    assert first == second
    assert store.count("events") == before + 1


def test_resume_attempt_idempotency_is_scoped_per_attempt(
    store: SQLiteStore, recovery: RecoveryService,
) -> None:
    before = store.count("events")

    recovery.resume_attempt(attempt(state="interrupted", attempt_id="att_a"))
    recovery.resume_attempt(attempt(state="interrupted", attempt_id="att_b"))

    assert store.count("events") == before + 2


@async_test
async def test_reconcile_behavior_is_unchanged_by_the_new_assessment_surface(
    store: SQLiteStore, recovery: RecoveryService,
) -> None:
    _running_attempt(store, effect_observed=True)

    report = await recovery.reconcile()

    assert report.outcome_unknown == ("att_recovery_1",)
    assert store.connection.execute(
        "SELECT state FROM attempts WHERE attempt_id='att_recovery_1'"
    ).fetchone() == ("outcome_unknown",)
    assert store.load_aggregate("product_sessions", "ses_1")["state"] == "paused"


@async_test
async def test_shell_presents_recovery_actions_without_running_them(
    tmp_path: Path,
) -> None:
    output: list[str] = []
    shell = _build(tmp_path, AsyncLines("/exit"), output)

    shell._show_recovery("login_lost")

    transcript = "\n".join(output)
    assert "reauthenticate_outside_agentdeck" in transcript
    assert "What happened:" in transcript


@async_test
async def test_shell_show_recovery_never_dispatches_or_reconnects(
    tmp_path: Path,
) -> None:
    output: list[str] = []
    shell = _build(tmp_path, AsyncLines("/exit"), output)

    shell._show_recovery("transport_before_effect")

    transcript = "\n".join(output)
    assert "reconnect_once" in transcript


@async_test
async def test_shell_rejects_an_unrecognized_recovery_condition_safely(
    tmp_path: Path,
) -> None:
    output: list[str] = []
    shell = _build(tmp_path, AsyncLines("/exit"), output)

    shell._show_recovery("not_a_real_condition")

    assert "not recognized" in "\n".join(output)


def test_recovery_has_no_transport_backend_model_role_or_tmux_fallback() -> None:
    import inspect

    import agentdeck.application.recovery_service as recovery_module

    source = inspect.getsource(recovery_module).lower()
    assert all(word not in source for word in (
        "transport", "backend", "model", "role", "pane", "tmux",
        "list_running_attempts",
    ))
