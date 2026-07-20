"""Bounded, content-free identity for one foreground exit cancellation."""

from __future__ import annotations

from dataclasses import dataclass

from agentdeck.kernel.events import normalize_occurred_at
from agentdeck.kernel.session import ExitAttemptSnapshot
from agentdeck.ports.worker import Worker, WorkerHandle


_FAILURE_CODES = frozenset({
    "cancel_rejected", "cancel_timeout", "transport_disconnected",
})


class ExecutionBindingError(RuntimeError):
    """Foreground runtime ownership is absent, reused, or drifted."""


def _identity(value: object, prefix: str, field: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field} must be a typed identity")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        raise ValueError(f"{field} must be a typed identity") from None
    if (
        not value.startswith(prefix) or not value.removeprefix(prefix)
        or len(encoded) > 255
        or any(character.isspace() or not character.isprintable() for character in value)
    ):
        raise ValueError(f"{field} must be a typed identity")
    return value


def _hash(value: object, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"{field} must be 64 lowercase hex")
    return value


@dataclass(frozen=True)
class ExitCancellationKey:
    session_id: str
    request_id: str
    attempt_id: str
    attempt_hash: str
    requested_at: str
    worker_handle: WorkerHandle
    authority_hash: str

    def __post_init__(self) -> None:
        _identity(self.session_id, "ses_", "session_id")
        _identity(self.request_id, "xrt_", "request_id")
        if len(self.request_id) != 36 or any(
            character not in "0123456789abcdef" for character in self.request_id[4:]
        ):
            raise ValueError("request_id must have 32 lowercase hex digits")
        _identity(self.attempt_id, "att_", "attempt_id")
        _hash(self.attempt_hash, "attempt_hash")
        _hash(self.authority_hash, "authority_hash")
        if type(self.worker_handle) is not WorkerHandle:
            raise TypeError("worker_handle must be a WorkerHandle")
        if self.worker_handle.attempt_id != self.attempt_id:
            raise ValueError("cancellation key handle lineage drifted")
        normalized = normalize_occurred_at(self.requested_at)
        object.__setattr__(self, "requested_at", normalized)


@dataclass(frozen=True)
class ExitCancellationOutcome:
    code: str | None
    outcome_known: bool

    def __post_init__(self) -> None:
        if type(self.outcome_known) is not bool:
            raise TypeError("outcome_known must be an exact bool")
        if self.code is None:
            if not self.outcome_known:
                raise ValueError("successful cancellation outcome must be known")
        elif type(self.code) is not str or self.code not in _FAILURE_CODES:
            raise ValueError("cancellation outcome code is not allowlisted")

    @classmethod
    def success(cls) -> "ExitCancellationOutcome":
        return cls(None, True)

    @classmethod
    def failure(
        cls, code: str, *, outcome_known: bool,
    ) -> "ExitCancellationOutcome":
        return cls(code, outcome_known)

    @property
    def succeeded(self) -> bool:
        return self.code is None


class ExitCancellationLease:
    """Opaque object-identity capability owned by one runtime fence."""

    __slots__ = ("_key", "_worker", "_worker_handle", "_outcome", "_quarantined")

    def __init__(
        self, key: ExitCancellationKey, worker: Worker, worker_handle: WorkerHandle,
        *, _token: object,
    ) -> None:
        if _token is not _LEASE_TOKEN:
            raise TypeError("exit cancellation lease is runtime-owned")
        self._key = key
        self._worker = worker
        self._worker_handle = worker_handle
        self._outcome: ExitCancellationOutcome | None = None
        self._quarantined = False

    def __copy__(self):
        raise TypeError("exit cancellation lease cannot be copied")

    def __deepcopy__(self, memo):
        raise TypeError("exit cancellation lease cannot be copied")

    @property
    def key(self) -> ExitCancellationKey:
        return self._key

    @property
    def worker(self) -> Worker:
        return self._worker

    @property
    def worker_handle(self) -> WorkerHandle:
        return self._worker_handle

    @property
    def outcome(self) -> ExitCancellationOutcome | None:
        return self._outcome

    @property
    def needs_worker_io(self) -> bool:
        return self._outcome is None

    @property
    def quarantined(self) -> bool:
        return self._quarantined


_LEASE_TOKEN = object()


def _new_exit_cancellation_lease(
    key: ExitCancellationKey, worker: Worker, worker_handle: WorkerHandle,
) -> ExitCancellationLease:
    return ExitCancellationLease(key, worker, worker_handle, _token=_LEASE_TOKEN)


def _close_exit_cancellation_lease(
    lease: ExitCancellationLease, outcome: ExitCancellationOutcome,
) -> None:
    if lease._outcome is not None:
        raise ValueError("exit cancellation lease is already closed")
    lease._outcome = outcome


def _quarantine_exit_cancellation_lease(lease: ExitCancellationLease) -> None:
    if lease._outcome is None:
        raise ValueError("open exit cancellation lease cannot settle")
    lease._quarantined = True


def _binding_matches_snapshot(binding: object, snapshot: object) -> bool:
    if type(snapshot) is not ExitAttemptSnapshot:
        return False
    handle = getattr(binding, "worker_handle", None)
    return type(handle) is WorkerHandle and (
        getattr(binding, "attempt_id", None),
        getattr(binding, "task_id", None),
        getattr(binding, "agent_instance_id", None),
        getattr(binding, "acp_session_id", None),
        handle.attempt_id, handle.task_id, handle.agent_id,
        handle.session_id, handle.transport,
    ) == (
        snapshot.attempt_id, snapshot.task_id, snapshot.agent_instance_id,
        snapshot.acp_session_id, snapshot.attempt_id, snapshot.task_id,
        snapshot.agent_instance_id, snapshot.acp_session_id, "acp",
    )


class ExitCancellationRuntimeMixin:
    """Exact fence operations mixed into the foreground runtime."""

    def has_live_owner(self) -> bool:
        return (
            self._binding is not None or self._reservation is not None
            or self._quarantined or self._exit_fence is not None
        )

    def claim_exit_cancellation(self, key, expected_handle):
        self._require_loop()
        if type(key) is not ExitCancellationKey or type(expected_handle) is not WorkerHandle:
            raise ExecutionBindingError("exit cancellation authority is invalid")
        if self._exit_fence is not None:
            if self._exit_fence.key == key and self._exit_fence.worker_handle == expected_handle:
                return self._exit_fence
            raise ExecutionBindingError("exit cancellation fence drifted")
        binding = self._binding
        if (
            binding is None or self._reservation is not None or self._quarantined
            or key.worker_handle != expected_handle
            or (binding.attempt_id, binding.worker_handle)
            != (key.attempt_id, expected_handle)
        ):
            raise ExecutionBindingError("exit cancellation binding drifted")
        self._exit_fence = _new_exit_cancellation_lease(
            key, binding.worker, binding.worker_handle
        )
        return self._exit_fence

    def close_exit_cancellation(self, lease, key, worker_handle, outcome) -> None:
        self._require_exit_fence(lease, key, worker_handle)
        if type(outcome) is not ExitCancellationOutcome:
            raise ExecutionBindingError("exit cancellation outcome is invalid")
        try:
            _close_exit_cancellation_lease(lease, outcome)
        except ValueError as error:
            raise ExecutionBindingError(str(error)) from None

    def settle_exit_cancellation(
        self, lease, key, worker_handle, *, quarantine: bool,
    ) -> None:
        self._require_exit_fence(lease, key, worker_handle)
        if type(quarantine) is not bool or lease.outcome is None:
            raise ExecutionBindingError("exit cancellation settlement is invalid")
        if quarantine and lease.quarantined:
            return
        pair = (key.attempt_id, worker_handle)
        already_released = self._binding is None and self._released == pair
        if self._binding is not None:
            if pair != (self._binding.attempt_id, self._binding.worker_handle):
                raise ExecutionBindingError("exit cancellation settlement drifted")
            self._binding = None
            self._released = pair
        elif self._released != pair:
            raise ExecutionBindingError("exit cancellation settlement drifted")
        if quarantine:
            if already_released:
                self._exit_fence = None
            else:
                _quarantine_exit_cancellation_lease(lease)
        else:
            self._exit_fence = None

    def blocks_exit_decline(self, request_id: str, attempt_hash: str) -> bool:
        lease = self._exit_fence
        return lease is not None and (
            lease.key.request_id == request_id and lease.key.attempt_hash == attempt_hash
        )

    def matching_exit_cancellation(self, request_id, attempt_hash):
        return self._exit_fence if self.blocks_exit_decline(
            request_id, attempt_hash
        ) else None

    def _require_exit_fence(self, lease, key, worker_handle) -> None:
        self._require_loop()
        if (
            self._exit_fence is not lease or lease.key != key
            or lease.worker_handle != worker_handle
        ):
            raise ExecutionBindingError("exact exit cancellation lease is unavailable")


__all__ = [
    "ExecutionBindingError", "ExitCancellationKey", "ExitCancellationLease",
    "ExitCancellationOutcome", "ExitCancellationRuntimeMixin",
]
