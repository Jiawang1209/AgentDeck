from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from functools import wraps
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

from agentdeck.adapters.observer_ipc import UnixObserverClient
from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.execution_authority import attempt_snapshot
from agentdeck.application.execution_runtime import ExecutionReservation
from agentdeck.kernel.execution import Attempt
from agentdeck.kernel.mission import MissionDraft
from agentdeck.kernel.permissions import Effect, PermissionProfile, PermissionScope
from agentdeck.ports.observer import ObserverSubscription, cursor_for_event
from agentdeck.ports.worker import WorkerEvent, WorkerHandle
from agentdeck.product.bootstrap import build_product_shell

from .fakes import FrozenClock


NOW = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


class BlockingReader:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, _prompt: str) -> str:
        self.started.set()
        await self.release.wait()
        return "/exit"


class BoundWorker:
    async def start_task(self, _request):
        raise AssertionError("composition test binds the exact handle directly")

    async def respond_permission(self, *_args, **_kwargs):
        return None

    async def cancel_task(self, *_args, **_kwargs):
        return None

    async def collect_result(self, *_args, **_kwargs):
        return None

    def stream_events(self, _handle):
        raise AssertionError("composition test publishes one decoded event directly")


def _git_repository(root: Path) -> None:
    subprocess.run(("git", "init", "-q", str(root)), check=True)
    subprocess.run(("git", "-C", str(root), "config", "user.email", "test@example.com"), check=True)
    subprocess.run(("git", "-C", str(root), "config", "user.name", "Test"), check=True)
    marker = root / "tracked.txt"
    marker.write_text("tracked\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(root), "add", "tracked.txt"), check=True)
    subprocess.run(("git", "-C", str(root), "commit", "-qm", "initial"), check=True)


def _build(root: Path, reader=None):
    return build_product_shell(
        project_root=str(root), read_line=reader or BlockingReader(),
        write_line=lambda _value: None, clock_factory=lambda: FrozenClock(NOW),
        discovery_factory=lambda: {}, config_factory=lambda **_: SimpleNamespace(
            resolve=lambda _key: SimpleNamespace(value="approve-for-me")
        ),
        adapter_readiness={},
        adapter_composition_factory=lambda **_: SimpleNamespace(
            worker=lambda _backend: BoundWorker()
        ),
    )


def test_shell_to_real_execution_service_has_all_takeover_proof_sources(
    tmp_path: Path,
) -> None:
    _git_repository(tmp_path)
    shell = _build(tmp_path)
    try:
        assert shell.execution_service.takeover_control.sources == (
            "typed_project_evidence", "permission_snapshot",
            "active_acp_session", "acknowledged_observer_cursor",
        )
    finally:
        shell._close_once()


@async_test
async def test_real_observer_ack_enables_takeover_and_missing_ack_fails_closed(
    tmp_path: Path, monkeypatch,
) -> None:
    _git_repository(tmp_path)
    monkeypatch.chdir(tmp_path)
    reader = BlockingReader()
    shell = _build(tmp_path, reader)
    running = asyncio.create_task(shell.run_async())
    await reader.started.wait()
    execution = shell.execution_service
    store = execution._store
    draft = MissionDraft.coding_default(
        "drf_1", "prove product takeover composition", str(tmp_path),
        "codex-cli", "native-default", PermissionProfile.APPROVE_FOR_ME,
    )
    preview = draft.preview(1)
    confirmed = preview.confirm(
        preview_id=preview.preview_id, content_hash=preview.content_hash,
    )
    task = draft.tasks[0]
    attempt = Attempt.pending("att_1", task.task_id, 1).start()
    worker = BoundWorker()
    reservation = ExecutionReservation(
        attempt.attempt_id, task.task_id, task.agent_instance_id, worker,
    )
    handle = WorkerHandle(
        "ses_acp", task.agent_instance_id, task.task_id, attempt.attempt_id,
    )
    execution._runtime.reserve(reservation)
    binding = execution._runtime.claim_handle(reservation, handle)
    execution._runtime.activate(reservation, binding)
    _seed_authority(
        store, shell._service.current().session_id, confirmed, task, attempt, handle,
    )
    permission = PermissionScope(
        PermissionProfile.APPROVE_FOR_ME, frozenset({Effect.READ}),
    )
    execution.takeover_control.arm(
        product_session_id=shell._service.current().session_id,
        confirmed=confirmed, task=task, attempt=attempt, permission=permission,
        acp_session_id=handle.session_id,
    )

    before = await execution.takeover(attempt.attempt_id)
    assert before.accepted is False
    assert before.diagnostic.code == "takeover_observer_cursor_unavailable"

    client = UnixObserverClient.connect(
        project_root=tmp_path,
        subscription=ObserverSubscription(
            store._project_id, handle.session_id, handle.agent_id,
        ),
    )
    try:
        observer = shell._observer.lifecycle
        await _eventually(lambda: observer.subscriber_count == 1)
        event = WorkerEvent(
            "evt_1", handle.session_id, handle.agent_id, handle.task_id,
            handle.attempt_id, handle.transport, 1, "progress",
            NOW.isoformat(), {"detail": "safe"},
        )
        execution._approval_service._publish_event(event)
        _binding, prior = await asyncio.to_thread(client.receive_binding)
        received = await asyncio.to_thread(client.receive_event)
        assert prior is None and received == event
        cursor = cursor_for_event(store._project_id, received)
        await asyncio.to_thread(client.acknowledge, cursor)
        await _eventually(
            lambda: observer.current_cursor(attempt.attempt_id) == cursor
        )
        assert (await execution.takeover(attempt.attempt_id)).accepted is True
    finally:
        client.close()
        running.cancel()
        with suppress(asyncio.CancelledError):
            await running


def _seed_authority(store, session_id, confirmed, task, attempt, handle) -> None:
    connection = store._require_writer()
    now = NOW.isoformat()
    connection.execute(
        "INSERT INTO agent_instances VALUES (?,?,?,?,?,?,?,?,?,?)",
        (task.agent_instance_id, session_id, task.backend, "acp", "1", task.role.value,
         handle.session_id, "active", now, now),
    )
    connection.execute(
        "INSERT INTO missions VALUES (?,?,?,?,?,?)",
        (confirmed.mission_id, session_id, "running", 1, now, now),
    )
    connection.execute(
        "INSERT INTO mission_versions VALUES (?,?,?,?,?,?)",
        (confirmed.mission_id, 1, f"prv_{confirmed.content_hash[:24]}",
         confirmed.content_hash, confirmed.canonical_content, now),
    )
    canonical = json.dumps(
        task.canonical_projection(), sort_keys=True, separators=(",", ":"),
    )
    connection.execute(
        "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (task.task_id, confirmed.mission_id, 1, 1, task.name, task.role.value,
         task.backend, task.agent_instance_id, task.acp_route, "running",
         canonical, now, now),
    )
    connection.commit()
    snapshot = attempt_snapshot(attempt, task, handle.session_id)
    store.execute_once(
        "seed:att_1", "seed_attempt",
        lambda transaction: transaction.save_aggregate(
            "attempts", attempt.attempt_id, snapshot,
        ) or {"accepted": True},
    )


async def _eventually(predicate) -> None:
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("bounded composition wait expired")
