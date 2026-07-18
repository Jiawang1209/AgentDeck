from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agentdeck.domain.mission import (
    MAX_BUDGET_UNITS,
    MAX_RETRY_LIMIT,
    AttemptState,
    MissionVersion,
    TaskSpec,
    validate_mutating_mission_slot,
    validate_task_dag,
)


def task(
    task_id: str,
    *,
    dependencies: tuple[str, ...] = (),
    retry_limit: int = 1,
    budget_units: int = 10,
    acceptance_criteria: tuple[str, ...] = ("required evidence exists",),
) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        objective=f"complete {task_id}",
        role="worker",
        scope=("src",),
        acceptance_contribution=(f"{task_id} evidence",),
        dependencies=dependencies,
        acceptance_criteria=acceptance_criteria,
        concurrency_keys=("project-write",),
        retry_limit=retry_limit,
        budget_units=budget_units,
    )


def mission(*tasks: TaskSpec, mission_id: str = "mis_1") -> MissionVersion:
    return MissionVersion(
        mission_id=mission_id,
        version=1,
        goal="ship the durable kernel",
        scope=("durable-kernel",),
        exclusions=("external-publication",),
        tasks=tasks,
        acceptance_criteria=("all required task evidence passes",),
        constraints=("project scope only",),
        max_parallel_tasks=2,
        budget_units=100,
        ordered_routes=("acp", "cli_pty"),
        expires_at="2026-07-19T00:00:00Z",
        provenance_source="human",
        provenance_id="proposal_1",
        metadata={"source": "human", "priority": 1},
    )


def test_task_dag_accepts_declared_dependency_order() -> None:
    tasks = (
        task("build"),
        task("review", dependencies=("build",)),
        task("accept", dependencies=("build", "review")),
    )

    assert validate_task_dag(tasks) == tasks
    assert mission(*tasks).tasks == tasks


def test_task_and_mission_are_frozen_and_detached() -> None:
    metadata = {"nested": {"owners": ["human"]}}
    version = MissionVersion(
        mission_id="mis_1",
        version=1,
        goal="ship",
        scope=("durable-kernel",),
        exclusions=(),
        tasks=(task("build"),),
        acceptance_criteria=("verified",),
        constraints=(),
        max_parallel_tasks=1,
        budget_units=100,
        ordered_routes=("acp",),
        expires_at=None,
        provenance_source="human",
        provenance_id="proposal_1",
        metadata=metadata,
    )
    metadata["nested"]["owners"].append("leader")  # type: ignore[index,union-attr]

    assert version.to_dict()["metadata"] == {"nested": {"owners": ["human"]}}
    with pytest.raises(FrozenInstanceError):
        version.goal = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        version.tasks[0].objective = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "tasks",
    [
        (task("build"), task("build")),
        (task("review", dependencies=("missing",)),),
        (task("build", dependencies=("review",)), task("review", dependencies=("build",))),
        (task("build", dependencies=("build",)),),
        (task("accept", dependencies=("build", "build")), task("build")),
    ],
)
def test_task_dag_rejects_duplicate_missing_and_cyclic_dependencies(
    tasks: tuple[TaskSpec, ...],
) -> None:
    with pytest.raises(ValueError, match="^task graph invalid$"):
        validate_task_dag(tasks)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("retry_limit", None),
        ("retry_limit", True),
        ("retry_limit", -1),
        ("retry_limit", MAX_RETRY_LIMIT + 1),
        ("budget_units", None),
        ("budget_units", False),
        ("budget_units", 0),
        ("budget_units", MAX_BUDGET_UNITS + 1),
    ],
)
def test_task_rejects_unbounded_or_illegal_retry_and_budget(
    field: str, value: object
) -> None:
    values: dict[str, object] = {
        "task_id": "build",
        "objective": "build",
        "acceptance_criteria": ("verified",),
        "role": "worker",
        "scope": ("src",),
        "acceptance_contribution": ("task evidence",),
        "concurrency_keys": (),
        "retry_limit": 1,
        "budget_units": 10,
    }
    values[field] = value

    with pytest.raises(ValueError, match="^task specification invalid$"):
        TaskSpec(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("criteria", [(), ("",), ("   ",)])
def test_task_rejects_empty_acceptance_criteria(
    criteria: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError, match="^task specification invalid$"):
        task("build", acceptance_criteria=criteria)


def test_mission_rejects_empty_acceptance_criteria() -> None:
    with pytest.raises(ValueError, match="^mission version invalid$"):
        MissionVersion(
            mission_id="mis_1",
            version=1,
            goal="ship",
            scope=("durable-kernel",),
            exclusions=(),
            tasks=(task("build"),),
            acceptance_criteria=(),
            constraints=(),
            max_parallel_tasks=1,
            budget_units=100,
            ordered_routes=("acp",),
            expires_at=None,
            provenance_source="human",
            provenance_id="proposal_1",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("role", ""),
        ("scope", ()),
        ("acceptance_contribution", ()),
        ("concurrency_keys", ("",)),
    ],
)
def test_task_rejects_invalid_explicit_governance_fields(
    field: str, value: object
) -> None:
    values: dict[str, object] = {
        "task_id": "build",
        "objective": "build",
        "role": "worker",
        "scope": ("src",),
        "acceptance_contribution": ("task evidence",),
        "acceptance_criteria": ("verified",),
        "concurrency_keys": (),
        "retry_limit": 1,
        "budget_units": 10,
    }
    values[field] = value

    with pytest.raises(ValueError, match="^task specification invalid$"):
        TaskSpec(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scope", ()),
        ("exclusions", ("",)),
        ("max_parallel_tasks", 0),
        ("budget_units", 0),
        ("ordered_routes", ()),
        ("expires_at", "   "),
        ("provenance_source", ""),
        ("provenance_id", ""),
    ],
)
def test_mission_rejects_invalid_explicit_governance_fields(
    field: str, value: object
) -> None:
    values = {
        "mission_id": "mis_1",
        "version": 1,
        "goal": "ship",
        "scope": ("durable-kernel",),
        "exclusions": (),
        "tasks": (task("build"),),
        "acceptance_criteria": ("verified",),
        "constraints": (),
        "max_parallel_tasks": 1,
        "budget_units": 100,
        "ordered_routes": ("acp",),
        "expires_at": None,
        "provenance_source": "human",
        "provenance_id": "proposal_1",
    }
    values[field] = value

    with pytest.raises(ValueError, match="^mission version invalid$"):
        MissionVersion(**values)  # type: ignore[arg-type]


def test_explicit_governance_fields_are_serialized() -> None:
    serialized = mission(task("build")).to_dict()

    assert serialized["scope"] == ["durable-kernel"]
    assert serialized["exclusions"] == ["external-publication"]
    assert serialized["max_parallel_tasks"] == 2
    assert serialized["budget_units"] == 100
    assert serialized["ordered_routes"] == ["acp", "cli_pty"]
    assert serialized["expires_at"] == "2026-07-19T00:00:00Z"
    assert serialized["provenance_source"] == "human"
    assert serialized["provenance_id"] == "proposal_1"
    assert serialized["tasks"][0]["role"] == "worker"  # type: ignore[index]
    assert serialized["tasks"][0]["scope"] == ["src"]  # type: ignore[index]
    assert serialized["tasks"][0]["acceptance_contribution"] == [  # type: ignore[index]
        "build evidence"
    ]
    assert serialized["tasks"][0]["concurrency_keys"] == [  # type: ignore[index]
        "project-write"
    ]


def test_second_concurrently_mutating_mission_is_rejected_purely() -> None:
    assert validate_mutating_mission_slot("mis_1", ()) == "mis_1"
    assert validate_mutating_mission_slot("mis_1", ("mis_1",)) == "mis_1"

    with pytest.raises(ValueError, match="^mutating mission conflict$"):
        validate_mutating_mission_slot("mis_2", ("mis_1",))
    with pytest.raises(ValueError, match="^mutating mission conflict$"):
        validate_mutating_mission_slot("mis_1", ("mis_1", "mis_2"))


def test_attempt_state_is_a_closed_string_enum() -> None:
    assert tuple(state.value for state in AttemptState) == (
        "pending",
        "running",
        "paused",
        "recovering",
        "completed",
        "failed",
        "cancelled",
        "ambiguous",
    )
