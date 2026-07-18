"""Immutable, persistence-independent Mission domain values."""

from __future__ import annotations

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
_MAX_TEXT_BYTES = 4 * 1024
_MAX_METADATA_DEPTH = 16


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
) -> CanonicalValue:
    if depth > _MAX_METADATA_DEPTH:
        raise _InvalidCanonicalValue
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
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
                _freeze_canonical(item, depth=depth + 1, active=active)
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
                    item, depth=depth + 1, active=active
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


@dataclass(frozen=True, slots=True)
class TaskSpec:
    task_id: str
    objective: str
    acceptance_criteria: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    retry_limit: int = 0
    budget_units: int = 1

    def __post_init__(self) -> None:
        if not (
            _valid_text(self.task_id)
            and _valid_text(self.objective)
            and isinstance(self.dependencies, tuple)
            and all(_valid_text(item) for item in self.dependencies)
            and _validate_text_tuple(self.acceptance_criteria, allow_empty=False)
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
            "acceptance_criteria": list(self.acceptance_criteria),
            "dependencies": list(self.dependencies),
            "retry_limit": self.retry_limit,
            "budget_units": self.budget_units,
        }


def validate_task_dag(tasks: tuple[TaskSpec, ...]) -> tuple[TaskSpec, ...]:
    """Validate a closed DAG while preserving the declared Task order."""

    if not isinstance(tasks, tuple) or not tasks or not all(
        isinstance(item, TaskSpec) for item in tasks
    ):
        raise ValueError("task graph invalid")
    task_ids = tuple(item.task_id for item in tasks)
    if len(set(task_ids)) != len(task_ids):
        raise ValueError("task graph invalid")
    known = set(task_ids)
    if any(
        len(set(item.dependencies)) != len(item.dependencies)
        or item.task_id in item.dependencies
        or not set(item.dependencies).issubset(known)
        for item in tasks
    ):
        raise ValueError("task graph invalid")

    dependencies = {item.task_id: item.dependencies for item in tasks}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise ValueError("task graph invalid")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in dependencies[task_id]:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in task_ids:
        visit(task_id)
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
    tasks: tuple[TaskSpec, ...]
    acceptance_criteria: tuple[str, ...]
    constraints: tuple[str, ...] = ()
    metadata: Mapping[str, CanonicalValue] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not (
            _valid_text(self.mission_id)
            and _bounded_int(self.version, minimum=1, maximum=(2**63) - 1)
            and _valid_text(self.goal)
            and _validate_text_tuple(self.acceptance_criteria, allow_empty=False)
            and _validate_text_tuple(self.constraints, allow_empty=True)
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
            "tasks": [item.to_dict() for item in self.tasks],
            "acceptance_criteria": list(self.acceptance_criteria),
            "constraints": list(self.constraints),
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
