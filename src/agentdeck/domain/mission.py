"""Immutable, persistence-independent Mission domain values."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from agentdeck.domain.authorization import (
        AuthorizationEnvelope,
        ConfirmedMissionVersion,
    )


MAX_RETRY_LIMIT = 32
MAX_BUDGET_UNITS = 1_000_000
MAX_PARALLEL_TASKS = 256
MAX_TASK_COUNT = 2_048
MAX_TASK_DEPENDENCIES = 8_192
MAX_CANONICAL_NODES = 4_096
_MAX_TEXT_BYTES = 4 * 1024
_MAX_METADATA_DEPTH = 16
_MAX_RUNTIME_FACT_BYTES = 64 * 1024
_MIN_SIGNED_64 = -(2**63)
_MAX_SIGNED_64 = (2**63) - 1


type CanonicalValue = (
    None
    | bool
    | int
    | str
    | tuple["CanonicalValue", ...]
    | Mapping[str, "CanonicalValue"]
)


class _InvalidCanonicalValue(ValueError):
    pass


def _valid_text(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        return len(value.encode("utf-8")) <= _MAX_TEXT_BYTES
    except UnicodeEncodeError:
        return False


def _bounded_int(value: object, *, minimum: int, maximum: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= maximum
    )


def _freeze_canonical(
    value: object,
    *,
    depth: int = 0,
    active: set[int] | None = None,
    remaining_nodes: list[int] | None = None,
) -> CanonicalValue:
    remaining_nodes = (
        [MAX_CANONICAL_NODES] if remaining_nodes is None else remaining_nodes
    )
    remaining_nodes[0] -= 1
    if remaining_nodes[0] < 0:
        raise _InvalidCanonicalValue
    if depth > _MAX_METADATA_DEPTH:
        raise _InvalidCanonicalValue
    if value is None or isinstance(value, bool):
        return value
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and _MIN_SIGNED_64 <= value <= _MAX_SIGNED_64
    ):
        return value
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise _InvalidCanonicalValue from exc
        return value
    if isinstance(value, (list, tuple)):
        active = set() if active is None else active
        identity = id(value)
        if identity in active:
            raise _InvalidCanonicalValue
        active.add(identity)
        try:
            return tuple(
                _freeze_canonical(
                    item,
                    depth=depth + 1,
                    active=active,
                    remaining_nodes=remaining_nodes,
                )
                for item in value
            )
        finally:
            active.remove(identity)
    if isinstance(value, Mapping):
        active = set() if active is None else active
        identity = id(value)
        if identity in active:
            raise _InvalidCanonicalValue
        active.add(identity)
        try:
            frozen: dict[str, CanonicalValue] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise _InvalidCanonicalValue
                try:
                    key.encode("utf-8")
                except UnicodeEncodeError as exc:
                    raise _InvalidCanonicalValue from exc
                frozen[key] = _freeze_canonical(
                    item,
                    depth=depth + 1,
                    active=active,
                    remaining_nodes=remaining_nodes,
                )
            return MappingProxyType(frozen)
        finally:
            active.remove(identity)
    raise _InvalidCanonicalValue


def _thaw_canonical(value: CanonicalValue) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_canonical(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_canonical(item) for item in value]
    return value


def _validate_text_tuple(value: object, *, allow_empty: bool) -> bool:
    return (
        isinstance(value, tuple)
        and (allow_empty or bool(value))
        and all(_valid_text(item) for item in value)
        and len(set(value)) == len(value)
    )


class AttemptState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    AMBIGUOUS = "ambiguous"


class TaskRuntimeState(str, Enum):
    """Closed durable Task lifecycle independent of adapter prose."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    AWAITING_VERIFICATION = "awaiting_verification"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class VerificationState(str, Enum):
    PENDING = "pending"
    PASS = "pass"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"


_TERMINAL_TASK_STATES = frozenset(
    {
        TaskRuntimeState.COMPLETED,
        TaskRuntimeState.FAILED,
        TaskRuntimeState.CANCELLED,
    }
)
_RUNTIME_FACT_SCOPES = frozenset({"mission", "task", "session"})
_RUNTIME_FACT_KINDS = frozenset(
    {
        "terminal_failed",
        "terminal_cancelled",
        "proven_no_effect",
        "ambiguous_effect",
        "permission_conflict",
        "task_local_pause",
        "session_takeover",
        "running",
    }
)
_WORKER_EVENT_KINDS = frozenset(
    {
        "worker_message",
        "progress",
        "turn_completed",
        "evidence",
        "permission_requested",
        "permission_conflict",
        "ambiguous_effect",
        "failed",
        "cancelled",
        "session_takeover",
    }
)
_ATTEMPT_RUNTIME_STATES = frozenset(
    {
        "running",
        "paused",
        "completed",
        "failed",
        "cancelled",
        "awaiting_verification",
    }
)


def _task_state(value: object) -> TaskRuntimeState:
    try:
        return value if type(value) is TaskRuntimeState else TaskRuntimeState(value)
    except (TypeError, ValueError):
        raise ValueError("task runtime state invalid") from None


@dataclass(frozen=True, slots=True)
class RuntimeFact:
    scope: str
    kind: str
    reason: str

    def __post_init__(self) -> None:
        if (
            self.scope not in _RUNTIME_FACT_SCOPES
            or self.kind not in _RUNTIME_FACT_KINDS
            or not _valid_text(self.reason)
        ):
            raise ValueError("runtime fact invalid")


@dataclass(frozen=True, slots=True)
class AttemptDecision:
    attempt_number: int
    task_state: TaskRuntimeState
    attempt_state: str

    def __post_init__(self) -> None:
        if (
            not _bounded_int(self.attempt_number, minimum=1, maximum=MAX_RETRY_LIMIT + 1)
            or type(self.task_state) is not TaskRuntimeState
            or self.attempt_state not in _ATTEMPT_RUNTIME_STATES
        ):
            raise ValueError("attempt decision invalid")


@dataclass(frozen=True, slots=True)
class RecoveryContext:
    attempt_number: int
    task_retry_limit: int
    mission_attempt_count: int
    mission_max_attempts: int
    mission_retry_count: int
    mission_max_retries: int
    mission_recovery_count: int
    mission_max_recoveries: int
    task_budget_used: int
    task_budget_limit: int
    mission_budget_used: int
    mission_budget_limit: int

    def __post_init__(self) -> None:
        values = tuple(getattr(self, field) for field in self.__dataclass_fields__)
        if any(not isinstance(item, int) or isinstance(item, bool) for item in values):
            raise ValueError("recovery context invalid")
        if not (
            1 <= self.attempt_number <= MAX_RETRY_LIMIT + 1
            and 0 <= self.task_retry_limit <= MAX_RETRY_LIMIT
            and 0 <= self.mission_attempt_count <= _MAX_SIGNED_64
            and 1 <= self.mission_max_attempts <= _MAX_SIGNED_64
            and 0 <= self.mission_retry_count <= _MAX_SIGNED_64
            and 0 <= self.mission_max_retries <= _MAX_SIGNED_64
            and 0 <= self.mission_recovery_count <= _MAX_SIGNED_64
            and 0 <= self.mission_max_recoveries <= _MAX_SIGNED_64
            and 0 <= self.task_budget_used <= MAX_BUDGET_UNITS
            and 1 <= self.task_budget_limit <= MAX_BUDGET_UNITS
            and 0 <= self.mission_budget_used <= MAX_BUDGET_UNITS
            and 1 <= self.mission_budget_limit <= MAX_BUDGET_UNITS
        ):
            raise ValueError("recovery context invalid")

    @property
    def allows_retry(self) -> bool:
        return (
            self.attempt_number <= self.task_retry_limit
            and self.mission_attempt_count < self.mission_max_attempts
            and self.mission_retry_count < self.mission_max_retries
            and self.mission_recovery_count < self.mission_max_recoveries
            and self.task_budget_used < self.task_budget_limit
            and self.mission_budget_used < self.mission_budget_limit
        )


@dataclass(frozen=True, slots=True)
class WorkerEventDecision:
    task_state: TaskRuntimeState
    attempt_state: str
    mission_state: str
    facts: tuple[RuntimeFact, ...] = ()
    reasons: tuple[str, ...] = ()
    effective_scope: str = "task"
    dispatch_allowed: bool = False
    automation_allowed: bool = False
    recovery_allowed: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.task_state) is not TaskRuntimeState
            or self.attempt_state not in _ATTEMPT_RUNTIME_STATES
            or self.mission_state
            not in {"running", "paused", "completed", "failed", "cancelled"}
            or not isinstance(self.facts, tuple)
            or not all(type(item) is RuntimeFact for item in self.facts)
            or self.reasons != tuple(item.reason for item in self.facts)
            or self.effective_scope not in _RUNTIME_FACT_SCOPES
            or type(self.dispatch_allowed) is not bool
            or type(self.automation_allowed) is not bool
            or type(self.recovery_allowed) is not bool
        ):
            raise ValueError("worker event decision invalid")


@dataclass(frozen=True, slots=True)
class HandoffDecision:
    source_task_id: str
    destination_task_id: str
    accepted: bool

    def __post_init__(self) -> None:
        if not (
            _valid_text(self.source_task_id)
            and _valid_text(self.destination_task_id)
            and type(self.accepted) is bool
        ):
            raise ValueError("handoff decision invalid")


@dataclass(frozen=True, slots=True)
class EvidenceDecision:
    criterion: str
    fact: str
    reason: str

    def __post_init__(self) -> None:
        if (
            not _valid_text(self.criterion)
            or self.fact
            not in {"check_passed", "check_failed", "proven_no_effect"}
            or not _valid_text(self.reason)
        ):
            raise ValueError("evidence decision invalid")


def release_ready_tasks(
    tasks: tuple[TaskSpec, ...],
    task_states: Mapping[str, str | TaskRuntimeState],
    accepted_handoffs: tuple[tuple[str, str], ...],
) -> tuple[str, ...]:
    """Return pending Tasks whose complete dependency Handoffs are durable."""

    validate_task_dag(tasks)
    if not isinstance(task_states, Mapping) or set(task_states) != {
        item.task_id for item in tasks
    } or not isinstance(accepted_handoffs, tuple):
        raise ValueError("task release invalid")
    try:
        states = {key: _task_state(value) for key, value in task_states.items()}
        handoffs = set(accepted_handoffs)
    except (TypeError, ValueError):
        raise ValueError("task release invalid") from None
    if len(handoffs) != len(accepted_handoffs) or not all(
        isinstance(item, tuple)
        and len(item) == 2
        and all(_valid_text(value) for value in item)
        for item in accepted_handoffs
    ):
        raise ValueError("task release invalid")
    return tuple(
        task.task_id
        for task in tasks
        if states[task.task_id] is TaskRuntimeState.PENDING
        and all(
            states[dependency] is TaskRuntimeState.COMPLETED
            and (dependency, task.task_id) in handoffs
            for dependency in task.dependencies
        )
    )


def start_attempt(
    task: TaskSpec,
    task_state: str | TaskRuntimeState,
    prior_attempt_numbers: tuple[int, ...],
) -> AttemptDecision:
    """Allocate the next distinguishable Attempt inside the Task retry bound."""

    if type(task) is not TaskSpec or _task_state(task_state) is not TaskRuntimeState.READY:
        raise ValueError("task not ready")
    if (
        not isinstance(prior_attempt_numbers, tuple)
        or any(
            not _bounded_int(item, minimum=1, maximum=MAX_RETRY_LIMIT + 1)
            for item in prior_attempt_numbers
        )
        or prior_attempt_numbers != tuple(range(1, len(prior_attempt_numbers) + 1))
    ):
        raise ValueError("attempt history invalid")
    next_number = len(prior_attempt_numbers) + 1
    if next_number > task.retry_limit + 1:
        raise ValueError("task retry limit reached")
    return AttemptDecision(next_number, TaskRuntimeState.RUNNING, "running")


def record_worker_event(
    task_state: str | TaskRuntimeState,
    attempt_state: str,
    event_kind: str,
    *,
    facts: tuple[RuntimeFact, ...] = (),
    effect_status: str | None = None,
    recovery: RecoveryContext | None = None,
) -> WorkerEventDecision:
    """Classify Worker facts with terminal and widest-scope-safe precedence."""

    state = _task_state(task_state)
    if (
        attempt_state not in _ATTEMPT_RUNTIME_STATES
        or event_kind not in _WORKER_EVENT_KINDS
        or not isinstance(facts, tuple)
        or not all(type(item) is RuntimeFact for item in facts)
        or len(facts) > MAX_CANONICAL_NODES
        or sum(len(item.reason.encode("utf-8")) for item in facts)
        > _MAX_RUNTIME_FACT_BYTES
        or (
            event_kind == "failed"
            and (
                effect_status
                not in {"proven_no_effect", "ambiguous_effect", "known_effect"}
                or type(recovery) is not RecoveryContext
            )
        )
        or (
            event_kind != "failed"
            and (effect_status is not None or recovery is not None)
        )
    ):
        raise ValueError("worker event invalid")
    reasons = tuple(item.reason for item in facts)
    if state in _TERMINAL_TASK_STATES:
        terminal = state.value
        return WorkerEventDecision(
            state,
            terminal,
            terminal,
            facts,
            reasons,
            effective_scope="mission",
        )

    fact_kinds = {item.kind for item in facts}
    if "terminal_failed" in fact_kinds:
        return WorkerEventDecision(
            TaskRuntimeState.FAILED,
            "failed",
            "failed",
            facts,
            reasons,
            effective_scope="mission",
        )
    if event_kind == "cancelled" or "terminal_cancelled" in fact_kinds:
        return WorkerEventDecision(
            TaskRuntimeState.CANCELLED,
            "cancelled",
            "cancelled",
            facts,
            reasons,
            effective_scope="mission",
        )
    if event_kind in {"ambiguous_effect", "permission_conflict"} or fact_kinds.intersection(
        {"ambiguous_effect", "permission_conflict"}
    ):
        return WorkerEventDecision(
            TaskRuntimeState.PAUSED,
            "paused",
            "paused",
            facts,
            reasons,
            effective_scope="mission",
        )
    if "session_takeover" in fact_kinds:
        return WorkerEventDecision(
            TaskRuntimeState.PAUSED,
            "paused",
            "running",
            facts,
            reasons,
            effective_scope="session",
        )
    if event_kind == "failed":
        assert recovery is not None
        if effect_status == "proven_no_effect" and recovery.allows_retry:
            return WorkerEventDecision(
                TaskRuntimeState.READY,
                "failed",
                "running",
                facts,
                reasons,
                effective_scope="task",
                dispatch_allowed=True,
                automation_allowed=True,
                recovery_allowed=True,
            )
        return WorkerEventDecision(
            TaskRuntimeState.FAILED,
            "failed",
            "failed",
            facts,
            reasons,
            effective_scope="mission",
        )
    if event_kind == "permission_requested" or "task_local_pause" in fact_kinds:
        return WorkerEventDecision(
            TaskRuntimeState.PAUSED,
            "paused",
            "running",
            facts,
            reasons,
            effective_scope="task",
        )
    if state is TaskRuntimeState.PAUSED:
        return WorkerEventDecision(
            state, "paused", "paused", facts, reasons, effective_scope="task"
        )
    if state is TaskRuntimeState.AWAITING_VERIFICATION:
        return WorkerEventDecision(
            state,
            "awaiting_verification",
            "running",
            facts,
            reasons,
            effective_scope="task",
        )
    if event_kind == "turn_completed":
        return WorkerEventDecision(
            TaskRuntimeState.AWAITING_VERIFICATION,
            "awaiting_verification",
            "running",
            facts,
            reasons,
            effective_scope="task",
        )
    return WorkerEventDecision(
        TaskRuntimeState.RUNNING,
        "running",
        "running",
        facts,
        reasons,
        effective_scope="session",
        dispatch_allowed=True,
        automation_allowed=True,
    )


def record_handoff(
    source_task_id: str,
    source_state: str | TaskRuntimeState,
    destination: TaskSpec,
) -> HandoffDecision:
    if type(destination) is not TaskSpec or not _valid_text(source_task_id):
        raise ValueError("handoff invalid")
    if _task_state(source_state) is not TaskRuntimeState.COMPLETED:
        raise ValueError("handoff source incomplete")
    if source_task_id not in destination.dependencies:
        raise ValueError("handoff dependency invalid")
    return HandoffDecision(source_task_id, destination.task_id, True)


def record_evidence(criterion: str, fact: str, reason: str) -> EvidenceDecision:
    return EvidenceDecision(criterion, fact, reason)


@dataclass(frozen=True, slots=True)
class TaskSpec:
    task_id: str
    objective: str
    role: str
    scope: tuple[str, ...]
    acceptance_contribution: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    concurrency_keys: tuple[str, ...] = ()
    retry_limit: int = 0
    budget_units: int = 1

    def __post_init__(self) -> None:
        if not (
            _valid_text(self.task_id)
            and _valid_text(self.objective)
            and _valid_text(self.role)
            and _validate_text_tuple(self.scope, allow_empty=False)
            and _validate_text_tuple(
                self.acceptance_contribution, allow_empty=False
            )
            and isinstance(self.dependencies, tuple)
            and all(_valid_text(item) for item in self.dependencies)
            and _validate_text_tuple(self.acceptance_criteria, allow_empty=False)
            and _validate_text_tuple(self.concurrency_keys, allow_empty=True)
            and _bounded_int(
                self.retry_limit, minimum=0, maximum=MAX_RETRY_LIMIT
            )
            and _bounded_int(
                self.budget_units, minimum=1, maximum=MAX_BUDGET_UNITS
            )
        ):
            raise ValueError("task specification invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "objective": self.objective,
            "role": self.role,
            "scope": list(self.scope),
            "acceptance_contribution": list(self.acceptance_contribution),
            "acceptance_criteria": list(self.acceptance_criteria),
            "dependencies": list(self.dependencies),
            "concurrency_keys": list(self.concurrency_keys),
            "retry_limit": self.retry_limit,
            "budget_units": self.budget_units,
        }


def validate_task_dag(tasks: tuple[TaskSpec, ...]) -> tuple[TaskSpec, ...]:
    """Validate a closed DAG while preserving the declared Task order."""

    if (
        not isinstance(tasks, tuple)
        or not tasks
        or len(tasks) > MAX_TASK_COUNT
        or not all(
            isinstance(item, TaskSpec) for item in tasks
        )
    ):
        raise ValueError("task graph invalid")
    task_ids = tuple(item.task_id for item in tasks)
    if len(set(task_ids)) != len(task_ids):
        raise ValueError("task graph invalid")
    known = set(task_ids)
    if sum(len(item.dependencies) for item in tasks) > MAX_TASK_DEPENDENCIES or any(
        len(set(item.dependencies)) != len(item.dependencies)
        or item.task_id in item.dependencies
        or not set(item.dependencies).issubset(known)
        for item in tasks
    ):
        raise ValueError("task graph invalid")

    indegree = {item.task_id: len(item.dependencies) for item in tasks}
    dependents: dict[str, list[str]] = {task_id: [] for task_id in task_ids}
    for item in tasks:
        for dependency in item.dependencies:
            dependents[dependency].append(item.task_id)
    ready = deque(task_id for task_id in task_ids if indegree[task_id] == 0)
    visited_count = 0
    while ready:
        task_id = ready.popleft()
        visited_count += 1
        for dependent in dependents[task_id]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
    if visited_count != len(tasks):
        raise ValueError("task graph invalid")
    return tasks


def validate_mutating_mission_slot(
    mission_id: str, active_mutating_mission_ids: tuple[str, ...]
) -> str:
    """Express the V1 one-mutating-Mission invariant without persistence."""

    if not _valid_text(mission_id) or not _validate_text_tuple(
        active_mutating_mission_ids, allow_empty=True
    ):
        raise ValueError("mutating mission conflict")
    if set(active_mutating_mission_ids).difference({mission_id}):
        raise ValueError("mutating mission conflict")
    return mission_id


@dataclass(frozen=True, slots=True)
class MissionVersion:
    mission_id: str
    version: int
    goal: str
    scope: tuple[str, ...]
    exclusions: tuple[str, ...]
    tasks: tuple[TaskSpec, ...]
    acceptance_criteria: tuple[str, ...]
    constraints: tuple[str, ...]
    max_parallel_tasks: int
    budget_units: int
    ordered_routes: tuple[str, ...]
    expires_at: str | None
    provenance_source: str
    provenance_id: str
    metadata: Mapping[str, CanonicalValue] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not (
            _valid_text(self.mission_id)
            and _bounded_int(self.version, minimum=1, maximum=(2**63) - 1)
            and _valid_text(self.goal)
            and _validate_text_tuple(self.scope, allow_empty=False)
            and _validate_text_tuple(self.exclusions, allow_empty=True)
            and _validate_text_tuple(self.acceptance_criteria, allow_empty=False)
            and _validate_text_tuple(self.constraints, allow_empty=True)
            and _bounded_int(
                self.max_parallel_tasks,
                minimum=1,
                maximum=MAX_PARALLEL_TASKS,
            )
            and _bounded_int(
                self.budget_units,
                minimum=1,
                maximum=MAX_BUDGET_UNITS,
            )
            and _validate_text_tuple(self.ordered_routes, allow_empty=False)
            and (
                self.expires_at is None or _valid_text(self.expires_at)
            )
            and _valid_text(self.provenance_source)
            and _valid_text(self.provenance_id)
            and isinstance(self.metadata, Mapping)
        ):
            raise ValueError("mission version invalid")
        try:
            validate_task_dag(self.tasks)
            frozen = _freeze_canonical(self.metadata)
        except (_InvalidCanonicalValue, ValueError):
            raise ValueError("mission version invalid") from None
        if not isinstance(frozen, Mapping):
            raise ValueError("mission version invalid")
        object.__setattr__(self, "metadata", cast(Mapping[str, CanonicalValue], frozen))

    def to_dict(self) -> dict[str, object]:
        return {
            "mission_id": self.mission_id,
            "version": self.version,
            "goal": self.goal,
            "scope": list(self.scope),
            "exclusions": list(self.exclusions),
            "tasks": [item.to_dict() for item in self.tasks],
            "acceptance_criteria": list(self.acceptance_criteria),
            "constraints": list(self.constraints),
            "max_parallel_tasks": self.max_parallel_tasks,
            "budget_units": self.budget_units,
            "ordered_routes": list(self.ordered_routes),
            "expires_at": self.expires_at,
            "provenance_source": self.provenance_source,
            "provenance_id": self.provenance_id,
            "metadata": _thaw_canonical(cast(CanonicalValue, self.metadata)),
        }

    def bind_authorization(
        self, envelope: AuthorizationEnvelope
    ) -> ConfirmedMissionVersion:
        from agentdeck.domain.authorization import (
            AuthorizationEnvelope,
            ConfirmedMissionVersion,
            authorization_digest,
        )

        if not isinstance(envelope, AuthorizationEnvelope):
            raise ValueError("authorization envelope invalid")
        return ConfirmedMissionVersion(
            mission_version=self,
            authorization_envelope=envelope,
            authorization_digest=authorization_digest(self, envelope),
        )
