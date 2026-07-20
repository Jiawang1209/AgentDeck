from __future__ import annotations

import asyncio
from functools import wraps

from agentdeck.application.project_lifecycle_service import ProjectLifecycleService
from agentdeck.application.recovery_service import RecoveryService

from .fakes import FrozenClock
from .test_sqlite_execution_resume import NOW, store


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


def lifecycle(store) -> ProjectLifecycleService:
    return ProjectLifecycleService(
        store=store, clock=FrozenClock(NOW), session_id="ses_1"
    )


@async_test
async def test_resume_after_recovery_uses_a_new_durable_generation(store):
    snapshot = store.load_execution_resume("ses_1")
    first = await lifecycle(store).resume(snapshot)
    assert first.should_start is True
    await RecoveryService(
        store=store, clock=FrozenClock(NOW), session_id="ses_1",
        recovery_run_id="restart_after_resume_crash",
    ).reconcile()
    assert store.load_aggregate("product_sessions", "ses_1")["state"] == "paused"

    second = await lifecycle(store).resume(snapshot)

    assert second.should_start is True
    assert store.load_aggregate("product_sessions", "ses_1")["state"] == "running"
    assert store._require_writer().execute(
        "SELECT count(*) FROM events WHERE kind='project_resumed'"
    ).fetchone() == (2,)
    assert store._require_writer().execute(
        "SELECT count(*) FROM commands WHERE command_kind='resume_project'"
    ).fetchone() == (2,)
