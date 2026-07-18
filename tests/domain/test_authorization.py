from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from agentdeck.domain.authorization import (
    MAX_AUTHORIZATION_ATTEMPTS,
    MAX_AUTHORIZATION_BUDGET_UNITS,
    AuthorizationEnvelope,
    ConfirmedMissionVersion,
    ExternalEffectPolicy,
    authorization_digest,
)
from agentdeck.domain.mission import MissionVersion, TaskSpec


def task(task_id: str, *, dependency: str | None = None) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        objective=f"complete {task_id}",
        role="worker",
        scope=("src",),
        acceptance_contribution=(f"{task_id} evidence",),
        dependencies=() if dependency is None else (dependency,),
        acceptance_criteria=(f"{task_id} verified",),
        concurrency_keys=("project-write",),
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
        scope=("durable-kernel",),
        exclusions=("external-publication",),
        tasks=tasks,
        acceptance_criteria=("all evidence passes",),
        constraints=("project scope only",),
        max_parallel_tasks=2,
        budget_units=200,
        ordered_routes=("acp", "cli_pty"),
        expires_at="2026-07-19T00:00:00Z",
        provenance_source="human",
        provenance_id="proposal_1",
        metadata={"source": "human", "priority": 1} if metadata is None else metadata,
    )


def envelope(
    *,
    operations: tuple[str, ...] = ("write_project", "run_tests"),
    metadata: object = None,
) -> AuthorizationEnvelope:
    return AuthorizationEnvelope(
        semantic_scope=("durable-kernel",),
        path_scope=("src", "tests"),
        exclusions=(".env",),
        operations=operations,
        allowed_agents=("codex", "claude"),
        allowed_roles=("worker", "reviewer"),
        external_effect_policy=ExternalEffectPolicy.DENY,
        max_attempts=2,
        max_retries=1,
        max_recoveries=1,
        budget_units=100,
        acceptance_criteria=("all evidence passes",),
        ordered_routes=("acp", "cli_pty"),
        expires_at="2026-07-19T00:00:00Z",
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
        scope=("durable-kernel",),
        exclusions=("external-publication",),
        constraints=("project scope only",),
        max_parallel_tasks=2,
        budget_units=200,
        ordered_routes=("acp", "cli_pty"),
        expires_at="2026-07-19T00:00:00Z",
        provenance_source="human",
        provenance_id="proposal_1",
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
        "semantic_scope": ("durable-kernel",),
        "path_scope": (),
        "exclusions": (),
        "operations": ("write_project",),
        "allowed_agents": (),
        "allowed_roles": (),
        "external_effect_policy": ExternalEffectPolicy.DENY,
        "max_attempts": 2,
        "max_retries": 1,
        "max_recoveries": 1,
        "budget_units": 100,
        "acceptance_criteria": ("verified",),
        "ordered_routes": ("acp",),
        "expires_at": None,
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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("semantic_scope", ()),
        ("path_scope", ("",)),
        ("exclusions", ("",)),
        ("allowed_roles", ("",)),
        ("external_effect_policy", "deny"),
        ("max_retries", -1),
        ("max_retries", 33),
        ("max_recoveries", -1),
        ("max_recoveries", 33),
        ("acceptance_criteria", ()),
        ("ordered_routes", ()),
        ("expires_at", "   "),
    ],
)
def test_envelope_rejects_invalid_explicit_governance_fields(
    field: str, value: object
) -> None:
    values: dict[str, object] = {
        "semantic_scope": ("durable-kernel",),
        "path_scope": ("src",),
        "exclusions": (),
        "operations": ("write_project",),
        "allowed_agents": ("codex",),
        "allowed_roles": ("worker",),
        "external_effect_policy": ExternalEffectPolicy.DENY,
        "max_attempts": 2,
        "max_retries": 1,
        "max_recoveries": 1,
        "budget_units": 100,
        "acceptance_criteria": ("verified",),
        "ordered_routes": ("acp",),
        "expires_at": None,
    }
    values[field] = value

    with pytest.raises(ValueError, match="^authorization envelope invalid$"):
        AuthorizationEnvelope(**values)  # type: ignore[arg-type]


def test_envelope_explicit_governance_fields_are_serialized() -> None:
    serialized = envelope().to_dict()

    assert serialized == {
        "semantic_scope": ["durable-kernel"],
        "path_scope": ["src", "tests"],
        "exclusions": [".env"],
        "operations": ["write_project", "run_tests"],
        "allowed_agents": ["codex", "claude"],
        "allowed_roles": ["worker", "reviewer"],
        "external_effect_policy": "deny",
        "max_attempts": 2,
        "max_retries": 1,
        "max_recoveries": 1,
        "budget_units": 100,
        "acceptance_criteria": ["all evidence passes"],
        "ordered_routes": ["acp", "cli_pty"],
        "expires_at": "2026-07-19T00:00:00Z",
        "metadata": {"risk": "bounded", "owner": "human"},
    }


def test_every_explicit_governance_field_changes_authorization_digest() -> None:
    version = mission_version()
    authority = envelope()
    baseline = authorization_digest(version, authority)
    envelope_changes = (
        {"semantic_scope": ("other-scope",)},
        {"path_scope": ("src",)},
        {"exclusions": ("secrets",)},
        {"operations": ("run_tests", "write_project")},
        {"allowed_agents": ("codex",)},
        {"allowed_roles": ("worker",)},
        {"external_effect_policy": ExternalEffectPolicy.REQUIRE_CONFIRMATION},
        {"max_attempts": 3},
        {"max_retries": 2},
        {"max_recoveries": 2},
        {"budget_units": 101},
        {"acceptance_criteria": ("different evidence",)},
        {"ordered_routes": ("cli_pty", "acp")},
        {"expires_at": "2026-07-20T00:00:00Z"},
    )
    mission_changes = (
        {"scope": ("other-scope",)},
        {"exclusions": ("secrets",)},
        {"max_parallel_tasks": 1},
        {"budget_units": 201},
        {"ordered_routes": ("cli_pty", "acp")},
        {"expires_at": "2026-07-20T00:00:00Z"},
        {"provenance_source": "leader"},
        {"provenance_id": "proposal_2"},
        {
            "tasks": (
                replace(version.tasks[0], role="reviewer"),
            )
        },
        {
            "tasks": (
                replace(version.tasks[0], scope=("tests",)),
            )
        },
        {
            "tasks": (
                replace(
                    version.tasks[0],
                    acceptance_contribution=("different evidence",),
                ),
            )
        },
        {
            "tasks": (
                replace(version.tasks[0], concurrency_keys=("tests-write",)),
            )
        },
    )

    for changes in envelope_changes:
        assert authorization_digest(version, replace(authority, **changes)) != baseline
    for changes in mission_changes:
        assert authorization_digest(replace(version, **changes), authority) != baseline
