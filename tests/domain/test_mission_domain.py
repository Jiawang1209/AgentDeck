from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agentdeck.domain.mission import (
    MAX_BUDGET_UNITS,
    MAX_CANONICAL_NODES,
    MAX_RETRY_LIMIT,
    MAX_TASK_COUNT,
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


def test_task_dag_accepts_deep_reverse_topological_chain_without_recursion() -> None:
    task_count = 1_100
    tasks = tuple(
        task(
            f"task_{index}",
            dependencies=()
            if index == task_count - 1
            else (f"task_{index + 1}",),
        )
        for index in range(task_count)
    )

    assert validate_task_dag(tasks) == tasks


def test_task_dag_rejects_unbounded_task_and_dependency_counts() -> None:
    too_many_tasks = tuple(
        task(f"task_{index}") for index in range(MAX_TASK_COUNT + 1)
    )
    with pytest.raises(ValueError, match="^task graph invalid$"):
        validate_task_dag(too_many_tasks)

    dependency_heavy = tuple(
        task(
            f"task_{index}",
            dependencies=tuple(
                f"task_{dependency}"
                for dependency in range(max(0, index - 5), index)
            ),
        )
        for index in range(MAX_TASK_COUNT)
    )
    with pytest.raises(ValueError, match="^task graph invalid$"):
        validate_task_dag(dependency_heavy)


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


def test_mission_metadata_accepts_signed64_and_exact_canonical_node_boundary() -> None:
    boundary_metadata = {
        "items": [0] * (MAX_CANONICAL_NODES - 4),
        "minimum": -(2**63),
        "maximum": (2**63) - 1,
    }

    version = mission(task("build"))
    bounded = MissionVersion(
        mission_id=version.mission_id,
        version=version.version,
        goal=version.goal,
        scope=version.scope,
        exclusions=version.exclusions,
        tasks=version.tasks,
        acceptance_criteria=version.acceptance_criteria,
        constraints=version.constraints,
        max_parallel_tasks=version.max_parallel_tasks,
        budget_units=version.budget_units,
        ordered_routes=version.ordered_routes,
        expires_at=version.expires_at,
        provenance_source=version.provenance_source,
        provenance_id=version.provenance_id,
        metadata=boundary_metadata,
    )

    assert bounded.to_dict()["metadata"] == boundary_metadata


@pytest.mark.parametrize("value", [2**63, -(2**63) - 1])
def test_mission_metadata_rejects_integer_outside_signed64(value: int) -> None:
    version = mission(task("build"))

    with pytest.raises(ValueError, match="^mission version invalid$"):
        MissionVersion(
            mission_id=version.mission_id,
            version=version.version,
            goal=version.goal,
            scope=version.scope,
            exclusions=version.exclusions,
            tasks=version.tasks,
            acceptance_criteria=version.acceptance_criteria,
            constraints=version.constraints,
            max_parallel_tasks=version.max_parallel_tasks,
            budget_units=version.budget_units,
            ordered_routes=version.ordered_routes,
            expires_at=version.expires_at,
            provenance_source=version.provenance_source,
            provenance_id=version.provenance_id,
            metadata={"value": value},
        )


def test_mission_metadata_rejects_more_than_canonical_node_limit() -> None:
    version = mission(task("build"))

    with pytest.raises(ValueError, match="^mission version invalid$"):
        MissionVersion(
            mission_id=version.mission_id,
            version=version.version,
            goal=version.goal,
            scope=version.scope,
            exclusions=version.exclusions,
            tasks=version.tasks,
            acceptance_criteria=version.acceptance_criteria,
            constraints=version.constraints,
            max_parallel_tasks=version.max_parallel_tasks,
            budget_units=version.budget_units,
            ordered_routes=version.ordered_routes,
            expires_at=version.expires_at,
            provenance_source=version.provenance_source,
            provenance_id=version.provenance_id,
            metadata={"items": [0] * (MAX_CANONICAL_NODES - 1)},
        )


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
