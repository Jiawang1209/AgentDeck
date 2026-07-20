"""Async, exact project-pause coordination around one ACP cancellation."""

from __future__ import annotations

import asyncio
from hashlib import sha256
from hmac import compare_digest

from agentdeck.application.execution_runtime import (
    ExecutionBindingError,
    ForegroundExecutionRuntime,
)
from agentdeck.application.exit_cancellation import (
    ExitCancellationKey,
    ExitCancellationOutcome,
)
from agentdeck.application.exit_records import (
    ExitResult,
    closed_exit_result,
    exit_failure,
    exit_result_from_command,
)
from agentdeck.application.exit_service import (
    ExitService,
    exit_request_command_id,
    exit_request_from_command_result,
)
from agentdeck.application.project_lifecycle_service import (
    ProjectLifecycleResult,
    ProjectLifecycleService,
)
from agentdeck.kernel.events import DomainEvent
from agentdeck.kernel.session import ExitRequest
from agentdeck.ports.clock import Clock
from agentdeck.ports.exit_authority import ActiveExitAuthority
from agentdeck.ports.store import (
    CommandResult, Store, StoreTransaction, _session_identity,
)
from agentdeck.ports.worker import WorkerCancellationError


def _dependency(value: object, methods: tuple[str, ...]) -> None:
    if any(not callable(getattr(value, method, None)) for method in methods):
        raise TypeError("async exit dependency is invalid")


def _valid_request_id(value: object) -> bool:
    return (
        type(value) is str and len(value) == 36 and value.startswith("xrt_")
        and all(character in "0123456789abcdef" for character in value[4:])
    )


def _valid_hash(value: object) -> bool:
    return type(value) is str and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _event(
    command_id: str, kind: str, aggregate_type: str, aggregate_id: str,
    request: ExitRequest, occurred_at: str,
) -> DomainEvent:
    digest = sha256(f"{command_id}:{kind}".encode("utf-8")).hexdigest()[:32]
    return DomainEvent(
        event_id=f"evt_{digest}", kind=kind,
        aggregate_type=aggregate_type, aggregate_id=aggregate_id,
        payload=(("attempt_hash", request.attempt_hash),
                 ("attempt_id", request.attempt.attempt_id),
                 ("request_id", request.request_id)),
        occurred_at=occurred_at,
    )


def _authority_matches(
    authority: ActiveExitAuthority, expected_hash: str,
) -> bool:
    return compare_digest(authority.content_hash, expected_hash)


def _confirm_command_id(session_id: str, request_id: str) -> str:
    return f"exit:confirm:{session_id}:{request_id}"


def _cancellation_key(authority: ActiveExitAuthority) -> ExitCancellationKey:
    request = authority.request
    return ExitCancellationKey(
        authority.session_id, request.request_id, request.attempt.attempt_id,
        request.attempt_hash, request.requested_at, authority.worker_handle,
        authority.content_hash,
    )


def _paused_attempt(request: ExitRequest) -> dict[str, object]:
    attempt = request.attempt
    return {
        "attempt_id": attempt.attempt_id,
        "task_id": attempt.task_id,
        "ordinal": attempt.ordinal,
        "state": "interrupted",
        "reason": "product_exit_confirmed",
        "retryable": False,
    }


def _paused_session(session_id: str) -> dict[str, object]:
    return {
        "session_id": session_id,
        "state": "paused",
        "pending_exit_id": None,
        "pending_exit_attempt_id": None,
        "canonical_pending_exit_attempt_facts": None,
        "pending_exit_attempt_hash": None,
        "pending_exit_requested_at": None,
    }


def exit_result_from_lifecycle(
    result: ProjectLifecycleResult, *, default: ExitResult, clock: Clock,
) -> ExitResult:
    if type(result) is not ProjectLifecycleResult:
        raise TypeError("project lifecycle result is invalid")
    if result.mode == "project_not_executing":
        return default
    if result.mode != "project_paused" or result.should_start:
        return exit_failure(clock, "project_dispatch_paused")
    facts = closed_exit_result(
        request=None, mode="project_paused", diagnostic_code=None,
        outcome_known=True, should_exit=True,
    )
    return exit_result_from_command(facts, clock=clock)


class AsyncExitCoordinator:
    def __init__(
        self, *, exit_service: ExitService, store: Store, clock: Clock,
        runtime: ForegroundExecutionRuntime,
        lifecycle: ProjectLifecycleService,
        session_id: str,
    ) -> None:
        _dependency(
            exit_service, ("request_exit", "decline", "confirm", "input_closed")
        )
        _dependency(
            store, ("lookup_command", "execute_once", "load_active_exit_authority")
        )
        _dependency(clock, ("now",))
        _dependency(runtime, (
            "has_live_owner", "claim_exit_cancellation",
            "close_exit_cancellation", "settle_exit_cancellation",
            "blocks_exit_decline", "matching_exit_cancellation",
        ))
        _dependency(lifecycle, ("stop_lease", "pause_between_stages"))
        self._exit_service = exit_service
        self._store = store
        self._clock = clock
        self._runtime = runtime
        self._lifecycle = lifecycle
        self._session_id = _session_identity(session_id)

    async def request_exit(self) -> ExitResult:
        async with self._lifecycle.stop_lease():
            result = self._exit_service.request_exit()
            if result.mode != "exit_ready":
                return result
            if self._runtime.has_live_owner():
                return exit_failure(self._clock, "project_dispatch_paused")
            paused = self._lifecycle.pause_between_stages()
            return exit_result_from_lifecycle(
                paused, default=result, clock=self._clock
            )

    async def decline(self, request_id: str, attempt_hash: str) -> ExitResult:
        async with self._lifecycle.stop_lease():
            if self._runtime.blocks_exit_decline(request_id, attempt_hash):
                return exit_failure(self._clock, "project_dispatch_paused")
            return self._exit_service.decline(request_id, attempt_hash)

    async def confirm(self, request_id: str, attempt_hash: str) -> ExitResult:
        async with self._lifecycle.stop_lease():
            return await self._confirm_locked(request_id, attempt_hash)

    async def input_closed(self) -> ExitResult:
        return await self.request_exit()

    async def _confirm_locked(
        self, request_id: str, attempt_hash: str,
    ) -> ExitResult:
        replay = self._completed_replay(request_id, attempt_hash)
        if replay is not None:
            return replay
        decision = self._exit_service.confirm(request_id, attempt_hash)
        if decision.mode != "exit_confirmation_ready":
            return decision
        pending = decision.request
        if pending is None:
            raise ValueError("exit confirmation authority is missing")
        lease = self._runtime.matching_exit_cancellation(
            pending.request_id, pending.attempt_hash
        )
        if lease is None:
            try:
                authority = self._store.load_active_exit_authority(self._session_id)
                if not authority.is_cancellable or authority.request != pending:
                    raise ExecutionBindingError("exit cancellation authority drifted")
                key = _cancellation_key(authority)
                lease = self._runtime.claim_exit_cancellation(
                    key, authority.worker_handle
                )
            except (ExecutionBindingError, TypeError, ValueError, RuntimeError):
                return exit_failure(
                    self._clock, "exit_binding_drift", request=pending
                )
        else:
            key = lease.key
        try:
            if lease.needs_worker_io:
                await lease.worker.cancel_task(
                    lease.worker_handle, reason="product_exit_confirmed"
                )
        except WorkerCancellationError as error:
            if lease.needs_worker_io:
                self._runtime.close_exit_cancellation(
                    lease, key, lease.worker_handle,
                    ExitCancellationOutcome.failure(
                        error.code, outcome_known=error.outcome_known
                    ),
                )
        except asyncio.CancelledError:
            if lease.needs_worker_io:
                self._runtime.close_exit_cancellation(
                    lease, key, lease.worker_handle,
                    ExitCancellationOutcome.failure(
                        "transport_disconnected", outcome_known=False
                    ),
                )
            raise
        except Exception:
            if lease.needs_worker_io:
                self._runtime.close_exit_cancellation(
                    lease, key, lease.worker_handle,
                    ExitCancellationOutcome.failure(
                        "transport_disconnected", outcome_known=False
                    ),
                )
        else:
            if lease.needs_worker_io:
                self._runtime.close_exit_cancellation(
                    lease, key, lease.worker_handle,
                    ExitCancellationOutcome.success(),
                )
        try:
            outcome = lease.outcome
            if outcome is None:
                raise ExecutionBindingError("exit cancellation outcome is absent")
            result = (
                self._persist_success(pending, key)
                if outcome.succeeded
                else self._persist_failure(pending, key, outcome)
            )
        except Exception:
            return exit_failure(
                self._clock, "exit_persistence_pending", request=pending,
                outcome_known=False, occurred_at=pending.requested_at,
            )
        return self._settle_result(result, pending, lease, key)

    def _completed_replay(
        self, request_id: str, attempt_hash: str,
    ) -> ExitResult | None:
        if not _valid_request_id(request_id) or not _valid_hash(attempt_hash):
            return exit_failure(self._clock, "exit_request_identity_mismatch")
        result = self._store.lookup_command(
            _confirm_command_id(self._session_id, request_id),
            "confirm_product_exit",
        )
        if result is None:
            return None
        original = self._store.lookup_command(
            exit_request_command_id(self._session_id, request_id),
            "request_product_exit",
        )
        if original is None:
            raise ValueError("stored exit request result is missing")
        try:
            request = exit_request_from_command_result(original, self._session_id)
        except (TypeError, ValueError):
            return exit_failure(self._clock, "exit_request_identity_mismatch")
        if request.request_id != request_id or not compare_digest(
            request.attempt_hash, attempt_hash
        ):
            return exit_failure(self._clock, "exit_request_identity_mismatch")
        replay = exit_result_from_command(
            result, clock=self._clock, request=request
        )
        return self._converge_replay(replay, request)

    def _persist_failure(
        self, request: ExitRequest, key: ExitCancellationKey,
        outcome: ExitCancellationOutcome,
    ) -> ExitResult:
        command_id = _confirm_command_id(key.session_id, request.request_id)

        def persist(transaction: StoreTransaction) -> CommandResult:
            code, known = outcome.code, outcome.outcome_known
            try:
                live = transaction.load_active_exit_authority(key.session_id)
                if not _authority_matches(live, key.authority_hash):
                    raise ValueError
            except (TypeError, ValueError, RuntimeError):
                code, known = "exit_authority_changed_after_cancel", False
            return closed_exit_result(
                request=request, mode="diagnostic", diagnostic_code=code,
                outcome_known=known, should_exit=False,
            )

        result = self._store.execute_once(
            command_id, "confirm_product_exit", persist
        )
        return exit_result_from_command(
            result, clock=self._clock, request=request
        )

    def _persist_success(
        self, request: ExitRequest, key: ExitCancellationKey,
    ) -> ExitResult:
        command_id = _confirm_command_id(key.session_id, request.request_id)

        def persist(transaction: StoreTransaction) -> CommandResult:
            try:
                live = transaction.load_active_exit_authority(key.session_id)
                if not _authority_matches(live, key.authority_hash):
                    raise ValueError
            except (TypeError, ValueError, RuntimeError):
                return closed_exit_result(
                    request=request, mode="diagnostic",
                    diagnostic_code="exit_authority_changed_after_cancel",
                    outcome_known=False, should_exit=False,
                )
            occurred_at = self._clock.now().isoformat()
            transaction.save_attempt(_paused_attempt(request))
            transaction.save_session(_paused_session(key.session_id))
            for kind, aggregate_type, aggregate_id in (
                ("attempt_interrupted", "attempt", request.attempt.attempt_id),
                ("project_paused", "product_session", key.session_id),
                ("exit_confirmed", "product_session", key.session_id),
            ):
                transaction.append_event(_event(
                    command_id, kind, aggregate_type, aggregate_id,
                    request, occurred_at,
                ))
            return closed_exit_result(
                request=request, mode="project_paused", diagnostic_code=None,
                outcome_known=True, should_exit=True,
            )

        result = self._store.execute_once(
            command_id, "confirm_product_exit", persist
        )
        return exit_result_from_command(
            result, clock=self._clock, request=request
        )

    def _settle_result(self, result, request, lease, key) -> ExitResult:
        quarantine = result.mode != "project_paused"
        try:
            self._runtime.settle_exit_cancellation(
                lease, key, lease.worker_handle, quarantine=quarantine
            )
        except ExecutionBindingError:
            return exit_failure(
                self._clock, "exit_runtime_convergence_failed",
                request=request, outcome_known=False,
                occurred_at=request.requested_at,
            )
        return result

    def _converge_replay(
        self, replay: ExitResult, request: ExitRequest,
    ) -> ExitResult:
        lease = self._runtime.matching_exit_cancellation(
            request.request_id, request.attempt_hash
        )
        if lease is None:
            if self._runtime.has_live_owner():
                return exit_failure(
                    self._clock, "exit_runtime_convergence_failed",
                    request=request, outcome_known=False,
                    occurred_at=request.requested_at,
                )
            return replay
        return self._settle_result(replay, request, lease, lease.key)

__all__ = ["AsyncExitCoordinator", "exit_result_from_lifecycle"]
