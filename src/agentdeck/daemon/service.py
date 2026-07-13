"""Authoritative bounded project-daemon service composition.

The service serializes every durable mutation and external-effect completion
through one queue.  Worker I/O may run concurrently, but it can only enqueue a
completion callback; it never receives the service's mutation authority.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import inspect
import math
from pathlib import Path
import re
import shutil
from typing import Any, Protocol

from .scheduler import SchedulerDecision, SchedulerFacts, schedule_gate
from .supervisor import SubmittedReceipt, TransportResult


class ServiceError(RuntimeError):
    """The daemon service could not preserve its authority boundary."""


class ServiceServer(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...


def scheduler_facts_from_store(store: object) -> SchedulerFacts | None:
    """Project one admitted Mission from one durable StateStore snapshot."""
    if not callable(getattr(store, "load", None)):
        raise ServiceError("scheduler store is invalid")
    state = store.load()
    missions = [
        item for item in state.get("missions", [])
        if isinstance(item, dict)
        and item.get("status") not in {"completed", "stopped", "interrupted"}
        and isinstance(item.get("daemon_admission"), dict)
        and item["daemon_admission"].get("state") == "admitted"
    ]
    if not missions:
        return None
    if len(missions) != 1:
        raise ServiceError("multiple admitted Missions are unsupported")
    mission = missions[0]
    snapshot = mission.get("execution_snapshot")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("mission"), dict):
        raise ServiceError("scheduler snapshot is invalid")
    steps = snapshot["mission"].get("steps")
    if not isinstance(steps, list):
        raise ServiceError("scheduler steps are invalid")
    current_step = mission.get("current_step", 0)
    if type(current_step) is not int or current_step < 0 or current_step > len(steps):
        raise ServiceError("scheduler step cursor is invalid")
    step = None if current_step == len(steps) else steps[current_step]
    if step is not None and not isinstance(step, dict):
        raise ServiceError("scheduler step is invalid")
    attempts = [
        item for item in state.get("mission_attempts", [])
        if isinstance(item, dict) and item.get("mission_id") == mission["mission_id"]
    ]
    active = [
        item for item in attempts
        if item.get("state") in {"prepared", "admitting", "submitted", "running"}
    ]
    if len(active) > 1:
        raise ServiceError("multiple active Mission attempts")
    bindings = [
        item for item in state.get("mission_recovery_evidence", [])
        if isinstance(item, dict) and item.get("mission_id") == mission["mission_id"]
    ]
    if len(bindings) != 1:
        raise ServiceError("scheduler recovery binding is invalid")
    bound_attempt_id = bindings[0].get("attempt_id")
    current_matches = [
        item for item in attempts if item.get("attempt_id") == bound_attempt_id
    ]
    current = None if bound_attempt_id is None else (
        current_matches[0] if len(current_matches) == 1 else None
    )
    if bound_attempt_id is not None and current is None:
        raise ServiceError("scheduler attempt binding is invalid")
    if current is not None and (
        step is None or current.get("step_id") != step.get("step_id")
    ):
        current = None
    replies = [
        item for item in state.get("mission_worker_replies", [])
        if isinstance(item, dict) and current is not None
        and item.get("attempt_id") == current.get("attempt_id")
    ]
    handoffs = [
        item for item in state.get("mission_handoffs", [])
        if isinstance(item, dict) and current is not None
        and item.get("attempt_id") == current.get("attempt_id")
    ]
    all_completed = current_step == len(steps)
    attempt_state = "none" if current is None else current.get("state")
    reply_state = "none" if not replies else replies[-1].get("state")
    handoff_state = "none" if not handoffs else handoffs[-1].get("state")
    worker_ready = True
    ownership_state = "owned"
    if step is not None:
        try:
            from ..config import load_config

            config = load_config(Path(store.root))
            agent = next(item for item in config.agents if item.agent_id == step.get("agent_id"))
            if agent.transport == "acp":
                command = agent.transport_command[0] if agent.transport_command else ""
                worker_ready = bool(
                    command
                    and (
                        Path(command).expanduser().is_file()
                        if "/" in command
                        else shutil.which(command) is not None
                    )
                )
            else:
                project_view = store.project_view(config)
                runtime = next(
                    (
                        item.get("runtime", {})
                        for item in project_view.get("agents", [])
                        if isinstance(item, dict)
                        and item.get("agent_id") == agent.agent_id
                    ),
                    {},
                )
                worker_ready = bool(
                    isinstance(runtime, dict)
                    and runtime.get("status") == "running"
                    and isinstance(runtime.get("pane_id"), str)
                )
                ownership = next(
                    (
                        item.get("state")
                        for item in project_view.get("conversation", {}).get("ownership", [])
                        if isinstance(item, dict) and item.get("agent_id") == agent.agent_id
                    ),
                    "agentdeck_owned",
                )
                ownership_state = "owned" if ownership == "agentdeck_owned" else "conflict"
        except (AttributeError, KeyError, OSError, StopIteration, TypeError, ValueError):
            worker_ready = False
    return SchedulerFacts(
        mission_id=mission["mission_id"],
        mission_state=mission["status"],
        step_id=None if step is None else step.get("step_id"),
        step_state="none" if step is None else ("pending" if current is None else "active"),
        attempt_id=None if current is None else current.get("attempt_id"),
        attempt_state=attempt_state,
        reply_state=reply_state,
        handoff_state=handoff_state,
        permission_state="none",
        worker_ready=worker_ready,
        next_step_eligible=(
            handoff_state == "recorded" and current_step < len(steps)
        ),
        all_steps_completed=all_completed,
        snapshot_state=(
            "valid" if mission.get("snapshot_hash") == snapshot.get("execution_hash")
            else "drift"
        ),
        lineage_state="valid",
        ownership_state=ownership_state,
        active_attempt_count=len(active),
        blocker=mission["daemon_admission"].get("blocker"),
    )


Callback = Callable[[], object | Awaitable[object]]
TransitionCallback = Callable[[SchedulerDecision], object | Awaitable[object]]


class WorkerTransport(Protocol):
    async def admit(self, attempt: dict[str, object]) -> SubmittedReceipt: ...
    async def complete(
        self, attempt: dict[str, object], receipt: SubmittedReceipt
    ) -> TransportResult: ...


class DaemonWorkerCoordinator:
    """Split Worker execution at the durable submitted-receipt fence."""

    def __init__(
        self,
        *,
        service: "ProjectDaemonService",
        store: object,
        transport_for: Callable[[dict[str, object]], WorkerTransport],
        refresh_recovery: Callable[[], object] = lambda: None,
    ) -> None:
        self.service = service
        self.store = store
        self.transport_for = transport_for
        self.refresh_recovery = refresh_recovery

    def launch(self, attempt: dict[str, object]) -> None:
        if attempt.get("state") != "prepared":
            raise ServiceError("Worker attempt must be prepared")
        # Construction is state-free and performs no external I/O.  Complete
        # all prompt/config authority checks before acquiring an admission
        # claim so a local configuration error cannot strand `admitting`.
        transport = self.transport_for(dict(attempt))
        claimed = self.store.claim_mission_attempt_admission(
            attempt_id=attempt["attempt_id"], dispatch_key=attempt["dispatch_key"]
        )
        self.refresh_recovery()

        async def admit() -> tuple[bool, object]:
            try:
                return True, await transport.admit(dict(claimed))
            except Exception as exc:
                return False, exc

        def admission_completed(outcome: tuple[bool, object]) -> None:
            ok, value = outcome
            if not ok or not isinstance(value, SubmittedReceipt):
                self.store.mark_mission_attempt_admission_ambiguous(
                    attempt_id=claimed["attempt_id"],
                    dispatch_key=claimed["dispatch_key"],
                    expected_claim_id=claimed["admission_claim_id"],
                    reason="admission_outcome_unknown",
                )
                return
            receipt = value
            if receipt.dispatch_key != claimed["dispatch_key"]:
                self.store.mark_mission_attempt_ambiguous(
                    attempt_id=claimed["attempt_id"],
                    dispatch_key=claimed["dispatch_key"],
                    expected_claim_id=claimed["admission_claim_id"],
                    observed_dispatch_key=receipt.dispatch_key,
                    receipt_summary=receipt.summary,
                    reason="receipt_persistence_unknown",
                )
                return
            try:
                submitted = self.store.record_mission_attempt_submitted(
                    attempt_id=claimed["attempt_id"],
                    dispatch_key=claimed["dispatch_key"],
                    expected_claim_id=claimed["admission_claim_id"],
                    receipt_summary=receipt.summary,
                )
            except Exception:
                self.store.mark_mission_attempt_ambiguous(
                    attempt_id=claimed["attempt_id"],
                    dispatch_key=claimed["dispatch_key"],
                    expected_claim_id=claimed["admission_claim_id"],
                    observed_dispatch_key=receipt.dispatch_key,
                    receipt_summary=receipt.summary,
                    reason="receipt_persistence_unknown",
                )
                return
            self.refresh_recovery()

            async def complete() -> tuple[bool, object]:
                try:
                    return True, await transport.complete(dict(submitted), receipt)
                except Exception as exc:
                    return False, exc

            self.service.start_worker_io(
                complete(), on_completion=lambda result: completion_completed(result)
            )

        def completion_completed(outcome: tuple[bool, object]) -> None:
            ok, value = outcome
            succeeded = bool(
                ok
                and isinstance(value, TransportResult)
                and value.validated
                and isinstance(value.reply, dict)
                and value.reply.get("handoff_token") == claimed["dispatch_key"]
                and value.reply.get("status") == "completed"
                and (
                    (claimed["configured_transport"] == "tmux" and value.stop_reason == "structured_reply")
                    or (claimed["configured_transport"] == "acp" and value.stop_reason in {"end_turn", "completed"})
                )
            )
            summary = (
                str(value.reply.get("summary") or "Worker completed")
                if succeeded and isinstance(value, TransportResult)
                else "Worker transport failed"
            )
            self.store.record_mission_attempt_result(
                attempt_id=claimed["attempt_id"],
                dispatch_key=claimed["dispatch_key"],
                succeeded=succeeded,
                summary=summary,
            )
            self.refresh_recovery()
            if succeeded:
                self.store.record_mission_reply_evidence(
                    attempt_id=claimed["attempt_id"],
                    dispatch_key=claimed["dispatch_key"],
                    state="received",
                )
                self.refresh_recovery()

        self.service.start_worker_io(admit(), on_completion=admission_completed)


class DaemonTransitionEffects:
    def __init__(self, store: object, *, launch_attempt: Callable[[dict[str, object]], object], refresh_recovery: Callable[[], object] = lambda: None) -> None:
        self.store = store
        self.launch_attempt = launch_attempt
        self.refresh_recovery = refresh_recovery

    def _applied(self, value: object) -> object:
        self.refresh_recovery()
        return value

    def apply(self, decision: SchedulerDecision) -> object:
        if decision.kind in {"idle", "await_worker", "wait_human", "wait_ambiguity", "blocked"}:
            return None
        if decision.kind == "prepare_dispatch":
            mission = self.store.mission_by_id(decision.mission_id)
            snapshot = mission["execution_snapshot"]
            step = next(item for item in snapshot["mission"]["steps"] if item["step_id"] == decision.step_id)
            worker = next(item for item in snapshot["workers"] if item["agent_id"] == step["agent_id"])
            return self._applied(self.store.prepare_mission_attempt(
                mission_id=decision.mission_id,
                step_id=decision.step_id,
                agent_id=step["agent_id"],
                configured_transport=worker["configured_transport"],
            ))
        if decision.kind == "dispatch_prepared":
            return self.launch_attempt(self.store.mission_attempt_by_id(decision.attempt_id))
        if decision.kind == "validate_reply":
            replies = [item for item in self.store.load().get("mission_worker_replies", []) if item.get("attempt_id") == decision.attempt_id]
            reply = replies[-1]
            attempt = self.store.mission_attempt_by_id(decision.attempt_id)
            return self._applied(self.store.record_mission_reply_evidence(
                attempt_id=decision.attempt_id,
                dispatch_key=attempt["dispatch_key"],
                state="validated",
                expected_reply_id=reply["reply_id"],
            ))
        if decision.kind == "record_handoff":
            state = self.store.load()
            reply = next(item for item in state.get("mission_worker_replies", []) if item.get("attempt_id") == decision.attempt_id)
            handoffs = [item for item in state.get("mission_handoffs", []) if item.get("attempt_id") == decision.attempt_id]
            if not handoffs:
                return self._applied(self.store.record_mission_handoff_evidence(
                    attempt_id=decision.attempt_id, reply_id=reply["reply_id"], state="pending"
                ))
            return self._applied(self.store.record_mission_handoff_evidence(
                attempt_id=decision.attempt_id, reply_id=reply["reply_id"], state="recorded",
                expected_handoff_id=handoffs[-1]["handoff_id"],
            ))
        if decision.kind == "activate_next":
            handoff = next(item for item in self.store.load().get("mission_handoffs", []) if item.get("attempt_id") == decision.attempt_id)
            return self._applied(self.store.advance_mission_after_handoff(
                decision.mission_id, attempt_id=decision.attempt_id, handoff_id=handoff["handoff_id"]
            ))
        if decision.kind == "complete_mission":
            return self._applied(self.store.complete_admitted_mission(decision.mission_id))
        raise ServiceError("unsupported scheduler transition")


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def validate_confirmed_mission_admission(
    store: object, params: object
) -> dict[str, object]:
    """Revalidate client-submitted authority against the daemon's durable copy."""
    if type(params) is not dict or set(params) != {
        "mission_id",
        "snapshot_hash",
        "execution_snapshot",
    }:
        raise ServiceError("frozen Mission admission is invalid")
    mission_id = params["mission_id"]
    snapshot_hash = params["snapshot_hash"]
    snapshot = params["execution_snapshot"]
    if (
        type(mission_id) is not str
        or re.fullmatch(r"mis_[0-9a-f]{12}", mission_id) is None
        or type(snapshot_hash) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", snapshot_hash) is None
        or not isinstance(snapshot, Mapping)
        or not callable(getattr(store, "mission_by_id", None))
        or not callable(getattr(store, "admit_mission_execution", None))
    ):
        raise ServiceError("frozen Mission admission is invalid")
    try:
        persisted = store.mission_by_id(mission_id)
    except Exception:
        raise ServiceError("frozen Mission admission is invalid") from None
    submitted_snapshot = _thaw_json(snapshot)
    if (
        type(persisted) is not dict
        or persisted.get("status") != "preparing"
        or persisted.get("snapshot_hash") != snapshot_hash
        or persisted.get("execution_snapshot") != submitted_snapshot
        or submitted_snapshot.get("execution_hash") != snapshot_hash
        or not isinstance(submitted_snapshot.get("mission"), dict)
        or submitted_snapshot["mission"].get("mission_id") != mission_id
    ):
        raise ServiceError("frozen Mission admission drift")
    try:
        durable = store.admit_mission_execution(
            mission_id, snapshot_hash=snapshot_hash
        )
    except (KeyError, TypeError, ValueError):
        raise ServiceError("frozen Mission admission drift") from None
    if not isinstance(durable, dict) or durable.get("state") != "admitted":
        raise ServiceError("frozen Mission admission drift")
    return {
        "accepted": True,
        "mission_id": mission_id,
        "snapshot_hash": snapshot_hash,
        "state": "admitted",
    }


async def _call(callback: Callable[..., object], *args: object) -> object:
    value = callback(*args)
    if inspect.isawaitable(value):
        return await value
    return value


class ProjectDaemonService:
    """Compose startup recovery, RPC serving, and one bounded transition tick."""

    def __init__(
        self,
        *,
        server: ServiceServer,
        reconcile_all: Callback,
        flush_safe_outboxes: Callback,
        load_scheduler_facts: Callable[
            [], SchedulerFacts | None | Awaitable[SchedulerFacts | None]
        ],
        apply_transition: TransitionCallback,
    ) -> None:
        if not all(
            callable(value)
            for value in (
                reconcile_all,
                flush_safe_outboxes,
                load_scheduler_facts,
                apply_transition,
            )
        ):
            raise TypeError("daemon service callbacks must be callable")
        if not callable(getattr(server, "start", None)) or not callable(
            getattr(server, "close", None)
        ):
            raise TypeError("daemon service server is invalid")
        self.server = server
        self._reconcile_all = reconcile_all
        self._flush_safe_outboxes = flush_safe_outboxes
        self._load_scheduler_facts = load_scheduler_facts
        self._apply_transition = apply_transition
        self._queue: asyncio.Queue[
            tuple[Callback, asyncio.Future[object] | None]
        ] = asyncio.Queue()
        self._worker_tasks: set[asyncio.Task[None]] = set()
        self._started = False
        self._closed = False
        self._shutdown = asyncio.Event()

    @property
    def started(self) -> bool:
        return self._started and not self._closed

    async def start(self) -> None:
        if self._closed:
            raise ServiceError("daemon service is closed")
        if self._started:
            return
        try:
            await _call(self._reconcile_all)
        except Exception:
            raise ServiceError("startup reconciliation failed") from None
        try:
            await self.server.start()
        except Exception:
            raise ServiceError("daemon server startup failed") from None
        self._started = True

    def submit_mutation(self, callback: Callback) -> asyncio.Future[object]:
        if not self.started or not callable(callback):
            raise ServiceError("daemon service is not accepting mutations")
        future = asyncio.get_running_loop().create_future()
        self._queue.put_nowait((callback, future))
        return future

    def start_worker_io(
        self,
        operation: Awaitable[Any],
        *,
        on_completion: Callable[[Any], object | Awaitable[object]],
    ) -> None:
        if not self.started or not inspect.isawaitable(operation) or not callable(
            on_completion
        ):
            if inspect.iscoroutine(operation):
                operation.close()
            raise ServiceError("Worker I/O submission is invalid")

        async def run() -> None:
            try:
                result = await operation
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                callback: Callback = lambda: (_ for _ in ()).throw(exc)
            else:
                callback = lambda: on_completion(result)
            self._queue.put_nowait((callback, None))

        task = asyncio.create_task(run())
        self._worker_tasks.add(task)
        task.add_done_callback(self._worker_tasks.discard)

    async def tick(self) -> SchedulerDecision | None:
        if not self.started:
            raise ServiceError("daemon service is not started")
        await _call(self._flush_safe_outboxes)
        try:
            callback, future = self._queue.get_nowait()
        except asyncio.QueueEmpty:
            facts = await _call(self._load_scheduler_facts)
            if facts is None:
                return None
            if not isinstance(facts, SchedulerFacts):
                raise ServiceError("scheduler facts are invalid")
            decision = schedule_gate(facts)
            await _call(self._apply_transition, decision)
            return decision
        try:
            result = await _call(callback)
        except Exception as exc:
            if future is not None and not future.done():
                future.set_exception(exc)
            return None
        if future is not None and not future.done():
            future.set_result(result)
        return None

    def request_shutdown(self) -> None:
        self._shutdown.set()

    async def run(self, *, poll_interval_seconds: float = 0.1) -> None:
        if (
            type(poll_interval_seconds) not in {int, float}
            or not math.isfinite(float(poll_interval_seconds))
            or poll_interval_seconds <= 0
        ):
            raise ServiceError("daemon service poll interval is invalid")
        await self.start()
        try:
            while not self._shutdown.is_set():
                await self.tick()
                try:
                    await asyncio.wait_for(
                        self._shutdown.wait(), timeout=float(poll_interval_seconds)
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for task in tuple(self._worker_tasks):
            task.cancel()
        if self._worker_tasks:
            await asyncio.gather(*tuple(self._worker_tasks), return_exceptions=True)
        while not self._queue.empty():
            _callback, future = self._queue.get_nowait()
            if future is not None and not future.done():
                future.set_exception(ServiceError("daemon service closed"))
        if self._started:
            await self.server.close()
        self._started = False
