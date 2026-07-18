from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from agentdeck.domain.authorization import (
    MAX_AUTHORIZATION_ATTEMPTS,
    MAX_AUTHORIZATION_BUDGET_UNITS,
    AuthorizationEnvelope,
    ConfirmedMissionVersion,
    authorization_digest,
)
from agentdeck.domain.mission import MissionVersion, TaskSpec


def task(task_id: str, *, dependency: str | None = None) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        objective=f"complete {task_id}",
        dependencies=() if dependency is None else (dependency,),
        acceptance_criteria=(f"{task_id} verified",),
        retry_limit=1,
        budget_units=20,
    )


def mission_version(
    *,
    goal: str = "ship",
    tasks: tuple[TaskSpec, ...] = (task("build"),),
    metadata: object = None,
) -> MissionVersion:
    return MissionVersion(
        mission_id="mis_1",
        version=3,
        goal=goal,
        tasks=tasks,
        acceptance_criteria=("all evidence passes",),
        constraints=("project scope only",),
        metadata={"source": "human", "priority": 1} if metadata is None else metadata,
    )


def envelope(
    *,
    operations: tuple[str, ...] = ("write_project", "run_tests"),
    metadata: object = None,
) -> AuthorizationEnvelope:
    return AuthorizationEnvelope(
        operations=operations,
        max_attempts=2,
        budget_units=100,
        allowed_agents=("codex", "claude"),
        ordered_routes=("acp", "cli_pty"),
        metadata={"risk": "bounded", "owner": "human"} if metadata is None else metadata,
    )


def test_confirmation_binds_exact_version_and_envelope() -> None:
    version = mission_version()
    authority = envelope()
    frozen = version.bind_authorization(authority)

    assert isinstance(frozen, ConfirmedMissionVersion)
    assert frozen.mission_version is version
    assert frozen.authorization_envelope is authority
    assert frozen.authorization_digest == authorization_digest(version, authority)
    assert frozen.confirm(frozen.authorization_digest) is frozen

    with pytest.raises(ValueError, match="^authorization digest mismatch$"):
        frozen.confirm("sha256:" + ("0" * 64))


def test_confirmation_rejects_stale_version_or_envelope_digest() -> None:
    authority = envelope()
    frozen = mission_version().bind_authorization(authority)
    stale_version = replace(frozen.mission_version, version=2)
    changed_authority = replace(authority, max_attempts=3)

    with pytest.raises(ValueError, match="^authorization digest mismatch$"):
        ConfirmedMissionVersion(
            mission_version=stale_version,
            authorization_envelope=authority,
            authorization_digest=frozen.authorization_digest,
        )
    with pytest.raises(ValueError, match="^authorization digest mismatch$"):
        ConfirmedMissionVersion(
            mission_version=frozen.mission_version,
            authorization_envelope=changed_authority,
            authorization_digest=frozen.authorization_digest,
        )


def test_digest_preserves_declared_tuple_order() -> None:
    version = mission_version(
        tasks=(task("build"), task("review", dependency="build"))
    )
    reversed_operations = envelope(operations=("run_tests", "write_project"))
    forward_operations = envelope(operations=("write_project", "run_tests"))
    reversed_tasks = MissionVersion(
        mission_id="mis_1",
        version=3,
        goal="ship",
        tasks=(task("review"), task("build")),
        acceptance_criteria=("all evidence passes",),
        constraints=("project scope only",),
    )

    assert authorization_digest(version, reversed_operations) != authorization_digest(
        version, forward_operations
    )
    assert authorization_digest(version, forward_operations) != authorization_digest(
        reversed_tasks, forward_operations
    )


def test_digest_ignores_mapping_insertion_order() -> None:
    first_version = mission_version(
        metadata={"outer": {"b": 2, "a": 1}, "source": "human"}
    )
    second_version = mission_version(
        metadata={"source": "human", "outer": {"a": 1, "b": 2}}
    )
    first_envelope = envelope(metadata={"z": 2, "a": {"y": 2, "x": 1}})
    second_envelope = envelope(metadata={"a": {"x": 1, "y": 2}, "z": 2})

    assert authorization_digest(first_version, first_envelope) == authorization_digest(
        second_version, second_envelope
    )


def test_digest_is_stable_lowercase_sha256() -> None:
    digest = authorization_digest(mission_version(), envelope())
    assert digest.startswith("sha256:")
    assert len(digest) == 71
    assert digest == digest.lower()


def test_authorization_envelope_is_frozen_and_detached() -> None:
    metadata = {"nested": {"paths": ["src"]}}
    authority = envelope(metadata=metadata)
    metadata["nested"]["paths"].append("tests")  # type: ignore[index,union-attr]

    assert authority.to_dict()["metadata"] == {"nested": {"paths": ["src"]}}
    with pytest.raises(FrozenInstanceError):
        authority.max_attempts = 4  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_attempts", None),
        ("max_attempts", True),
        ("max_attempts", 0),
        ("max_attempts", MAX_AUTHORIZATION_ATTEMPTS + 1),
        ("budget_units", None),
        ("budget_units", False),
        ("budget_units", 0),
        ("budget_units", MAX_AUTHORIZATION_BUDGET_UNITS + 1),
    ],
)
def test_envelope_rejects_unbounded_or_illegal_attempts_and_budget(
    field: str, value: object
) -> None:
    values: dict[str, object] = {
        "operations": ("write_project",),
        "max_attempts": 2,
        "budget_units": 100,
    }
    values[field] = value

    with pytest.raises(ValueError, match="^authorization envelope invalid$"):
        AuthorizationEnvelope(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "operations",
    [(), ("",), ("write_project", "write_project")],
)
def test_envelope_rejects_empty_or_duplicate_operations(
    operations: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError, match="^authorization envelope invalid$"):
        envelope(operations=operations)

