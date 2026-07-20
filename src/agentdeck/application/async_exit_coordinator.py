"""Async, exact project-pause coordination around one ACP cancellation."""

from __future__ import annotations

from hashlib import sha256
from hmac import compare_digest

from agentdeck.application.execution_runtime import (
    ExecutionBindingError,
    ForegroundExecutionRuntime,
)
from agentdeck.application.exit_records import (
    ExitResult,
    closed_exit_result,
    exit_failure,
    exit_result_from_command,
)
from agentdeck.application.exit_service import ExitService
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
    authority: ActiveExitAuthority, expected: ActiveExitAuthority,
) -> bool:
    return compare_digest(authority.content_hash, expected.content_hash)


def _paused_attempt(authority: ActiveExitAuthority) -> dict[str, object]:
    attempt = authority.request.attempt
    return {
        "attempt_id": attempt.attempt_id,
        "task_id": attempt.task_id,
        "ordinal": attempt.ordinal,
        "state": "interrupted",
        "reason": "product_exit_confirmed",
        "retryable": False,
    }


def _paused_session(authority: ActiveExitAuthority) -> dict[str, object]:
    return {
        "session_id": authority.session_id,
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
        _dependency(runtime, ("resolve_exact", "release", "is_empty"))
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
            paused = self._lifecycle.pause_between_stages()
            return exit_result_from_lifecycle(
                paused, default=result, clock=self._clock
            )

    async def decline(self, request_id: str, attempt_hash: str) -> ExitResult:
        async with self._lifecycle.stop_lease():
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
        try:
            authority = self._store.load_active_exit_authority(
                self._session_id
            )
            binding = self._runtime.resolve_exact(pending.attempt)
        except (ExecutionBindingError, TypeError, ValueError, RuntimeError):
            return exit_failure(
                self._clock, "exit_binding_drift", request=pending
            )
        if (
            not authority.is_cancellable
            or authority.request != pending
            or binding.worker_handle != authority.worker_handle
        ):
            return exit_failure(
                self._clock, "exit_binding_drift", request=pending
            )
        try:
            await binding.worker.cancel_task(
                binding.worker_handle, reason="product_exit_confirmed"
            )
        except WorkerCancellationError as error:
            return self._persist_failure(authority, error)
        result = self._persist_success(authority)
        if result.mode == "project_paused":
            self._runtime.release(pending.attempt.attempt_id, binding.worker_handle)
        return result

    def _completed_replay(
        self, request_id: str, attempt_hash: str,
    ) -> ExitResult | None:
        if not _valid_request_id(request_id) or not _valid_hash(attempt_hash):
            return exit_failure(self._clock, "exit_request_identity_mismatch")
        result = self._store.lookup_command(
            f"exit:confirm:{request_id}", "confirm_product_exit"
        )
        if result is None:
            return None
        replay = exit_result_from_command(result, clock=self._clock)
        if result["request_id"] != request_id or not compare_digest(
            result["attempt_hash"], attempt_hash
        ):
            return exit_failure(self._clock, "exit_request_identity_mismatch")
        return replay

    def _persist_failure(
        self, authority: ActiveExitAuthority, error: WorkerCancellationError,
    ) -> ExitResult:
        request = authority.request
        command_id = f"exit:confirm:{request.request_id}"

        def persist(transaction: StoreTransaction) -> CommandResult:
            code, known = error.code, error.outcome_known
            try:
                live = transaction.load_active_exit_authority(authority.session_id)
                if not _authority_matches(live, authority):
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
        return exit_result_from_command(result, clock=self._clock)

    def _persist_success(self, authority: ActiveExitAuthority) -> ExitResult:
        request = authority.request
        command_id = f"exit:confirm:{request.request_id}"

        def persist(transaction: StoreTransaction) -> CommandResult:
            try:
                live = transaction.load_active_exit_authority(authority.session_id)
                if not _authority_matches(live, authority):
                    raise ValueError
            except (TypeError, ValueError, RuntimeError):
                return closed_exit_result(
                    request=request, mode="diagnostic",
                    diagnostic_code="exit_authority_changed_after_cancel",
                    outcome_known=False, should_exit=False,
                )
            occurred_at = self._clock.now().isoformat()
            transaction.save_attempt(_paused_attempt(authority))
            transaction.save_session(_paused_session(authority))
            for kind, aggregate_type, aggregate_id in (
                ("attempt_interrupted", "attempt", request.attempt.attempt_id),
                ("project_paused", "product_session", authority.session_id),
                ("exit_confirmed", "product_session", authority.session_id),
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
        return exit_result_from_command(result, clock=self._clock)

__all__ = ["AsyncExitCoordinator", "exit_result_from_lifecycle"]
