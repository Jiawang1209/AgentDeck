from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import json

import pytest

from agentdeck.kernel.agents import AgentRole
from agentdeck.kernel.mission import (
    ConfirmedMissionVersion,
    MissionDraft,
    MissionPreview,
    PreviewDriftError,
    TaskDefinition,
)
from agentdeck.kernel.permissions import Effect, PermissionProfile


def draft() -> MissionDraft:
    return MissionDraft.coding_default(
        draft_id="drf_1",
        objective="构建可访问页面",
        project_root="/tmp/project",
        leader_backend="codex-cli",
        leader_model="native-default",
        permission_profile="approve_for_me",
    )


def canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def preview_from_content(content: str, version: int) -> MissionPreview:
    content_hash = sha256(content.encode("utf-8")).hexdigest()
    return MissionPreview(f"prv_{content_hash[:24]}", version, content_hash, content)


def confirmed_from_content(content: str, version: int) -> ConfirmedMissionVersion:
    content_hash = sha256(content.encode("utf-8")).hexdigest()
    return ConfirmedMissionVersion(f"msn_{content_hash[:24]}", version, content_hash, content)


def test_coding_default_is_a_deterministic_four_stage_mission() -> None:
    mission = draft()

    assert [task.name for task in mission.tasks] == [
        "implementation",
        "review",
        "revision",
        "acceptance",
    ]
    assert [task.role for task in mission.tasks] == [
        AgentRole.IMPLEMENTER,
        AgentRole.REVIEWER,
        AgentRole.REVISER,
        AgentRole.ACCEPTANCE_REVIEWER,
    ]
    assert [task.dependencies for task in mission.tasks] == [
        (),
        (mission.tasks[0].task_id,),
        (mission.tasks[1].task_id,),
        (mission.tasks[2].task_id,),
    ]
    assert len({task.task_id for task in mission.tasks}) == 4
    assert len({task.agent_instance_id for task in mission.tasks}) == 4
    assert len({task.acp_route for task in mission.tasks}) == 4
    assert all(task.allowed_effects for task in mission.tasks)
    assert all(task.expected_outputs and task.acceptance_criteria for task in mission.tasks)
    assert mission.permission_profile is PermissionProfile.APPROVE_FOR_ME
    assert mission.max_attempts == 2
    assert mission.max_revision_cycles == 1
    assert mission.max_acp_reconnects == 1
    assert mission.max_leader_schema_repairs == 1
    assert mission.max_final_acceptance_attempts == 1
    assert mission.scope == "project"
    assert mission.leader_adapter == "acp"
    assert mission.leader_version == "unreported"
    assert [task.backend for task in mission.tasks] == [
        "codex-cli", "claude-cli", "codex-cli", "claude-cli",
    ]
    assert [task.acp_route for task in mission.tasks] == [
        "acp://codex-cli/implementation",
        "acp://claude-cli/review",
        "acp://codex-cli/revision",
        "acp://claude-cli/acceptance",
    ]
    assert mission.acceptance_criteria and mission.non_goals and mission.risks


@pytest.mark.parametrize("constructor", (preview_from_content, confirmed_from_content))
@pytest.mark.parametrize(
    "payload",
    (
        {},
        {"version": 1},
        {"unexpected": "field"},
    ),
)
def test_preview_and_confirmation_reject_arbitrary_canonical_json(
    constructor: object, payload: object
) -> None:
    with pytest.raises(ValueError):
        constructor(canonical_json(payload), 1)  # type: ignore[operator]


@pytest.mark.parametrize("constructor", (preview_from_content, confirmed_from_content))
def test_preview_and_confirmation_reject_non_json_canonical_content(
    constructor: object,
) -> None:
    with pytest.raises(ValueError):
        constructor("not-json", 1)  # type: ignore[operator]


@pytest.mark.parametrize("constructor", (preview_from_content, confirmed_from_content))
@pytest.mark.parametrize(
    "mutate",
    (
        lambda payload: payload.update(version=2),
        lambda payload: payload.pop("objective"),
        lambda payload: payload.update(extra="forbidden"),
        lambda payload: payload["tasks"][1].update(dependencies=[]),
        lambda payload: payload["tasks"][0].update(allowed_effects=[{}]),
        lambda payload: payload["tasks"][0].pop("backend"),
        lambda payload: payload["tasks"][0].update(unexpected="field"),
    ),
)
def test_preview_and_confirmation_require_an_exact_mission_projection(
    constructor: object, mutate: object
) -> None:
    payload = json.loads(draft().preview(1).canonical_content)
    mutate(payload)  # type: ignore[operator]
    with pytest.raises(ValueError):
        constructor(canonical_json(payload), 1)  # type: ignore[operator]


def test_mission_requires_the_exact_chain_and_acp_backend_routes() -> None:
    mission = draft()
    all_empty = tuple(replace(task, dependencies=()) for task in mission.tasks)
    non_acp = tuple(
        replace(task, acp_route="https://claude-cli/acceptance")
        if task.name == "acceptance" else task
        for task in mission.tasks
    )
    wrong_backend = tuple(
        replace(task, backend="codex-cli") if task.name == "acceptance" else task
        for task in mission.tasks
    )
    common = dict(
        draft_id=mission.draft_id,
        objective=mission.objective,
        project_root=mission.project_root,
        leader_backend=mission.leader_backend,
        leader_model=mission.leader_model,
        permission_profile=mission.permission_profile,
        acceptance_criteria=mission.acceptance_criteria,
        non_goals=mission.non_goals,
        risks=mission.risks,
        budgets=dict(mission.budgets),
    )
    with pytest.raises(ValueError):
        MissionDraft(tasks=all_empty, **common)
    with pytest.raises(ValueError):
        MissionDraft(tasks=non_acp, **common)
    with pytest.raises(ValueError):
        MissionDraft(tasks=wrong_backend, **common)


def test_preview_hash_is_canonical_for_unicode_and_mapping_order() -> None:
    first = draft().preview(version=1)
    rebuilt = MissionDraft(
        draft_id="drf_1",
        objective="构建可访问页面",
        project_root="/tmp/project",
        leader_backend="codex-cli",
        leader_model="native-default",
        permission_profile=PermissionProfile.APPROVE_FOR_ME,
        tasks=list(draft().tasks),
        acceptance_criteria=list(draft().acceptance_criteria),
        non_goals=list(draft().non_goals),
        risks=list(draft().risks),
        budgets={
            "max_final_acceptance_attempts": 1,
            "max_leader_schema_repairs": 1,
            "max_acp_reconnects": 1,
            "max_revision_cycles": 1,
            "max_attempts": 2,
        },
    ).preview(version=1)

    assert first.preview_id == rebuilt.preview_id
    assert first.content_hash == rebuilt.content_hash
    assert first.canonical_content == rebuilt.canonical_content
    assert len(first.content_hash) == 64
    assert first.preview_id.startswith("prv_")


def test_confirmation_consumes_only_the_current_exact_preview() -> None:
    old = draft().preview(version=1)
    current = draft().revise(objective="构建无障碍页面").preview(version=2)

    with pytest.raises(PreviewDriftError):
        current.confirm(preview_id=old.preview_id, content_hash=old.content_hash)

    confirmed = current.confirm(
        preview_id=current.preview_id,
        content_hash=current.content_hash,
    )
    assert isinstance(confirmed, ConfirmedMissionVersion)
    assert confirmed.version == 2
    assert confirmed.content_hash == current.content_hash
    assert confirmed.canonical_content == current.canonical_content
    assert confirmed.mission_id.startswith("msn_")
    with pytest.raises(FrozenInstanceError):
        confirmed.version = 3  # type: ignore[misc]


def test_semantic_revision_changes_same_version_preview_identity() -> None:
    original = draft().preview(version=1)
    revised = draft().revise(objective="build an accessible page").preview(version=1)

    assert revised.preview_id != original.preview_id
    assert revised.content_hash != original.content_hash


@pytest.mark.parametrize("version", (0, -1, True, "1"))
def test_preview_rejects_invalid_versions(version: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        draft().preview(version=version)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("preview_id", "content_hash"),
    (("", "0" * 64), ("prv_other", "0" * 64), ("prv_x", "bad"), (1, "0" * 64)),
)
def test_confirmation_rejects_invalid_or_drifted_identity(
    preview_id: object, content_hash: object
) -> None:
    with pytest.raises((PreviewDriftError, TypeError, ValueError)):
        draft().preview(version=1).confirm(
            preview_id=preview_id,  # type: ignore[arg-type]
            content_hash=content_hash,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("preview_id", "prv_" + "A" * 24),
        ("preview_id", "prv_" + "é" * 24),
        ("preview_id", "prv_" + "a" * 23),
        ("content_hash", "A" * 64),
        ("content_hash", "é" * 64),
        ("content_hash", "a" * 63),
    ),
)
def test_preview_constructor_rejects_noncanonical_derived_identity(
    field: str, invalid: str
) -> None:
    preview = draft().preview(1)
    values = dict(
        preview_id=preview.preview_id,
        version=preview.version,
        content_hash=preview.content_hash,
        canonical_content=preview.canonical_content,
    )
    values[field] = invalid
    with pytest.raises(ValueError) as error:
        MissionPreview(**values)
    assert type(error.value) is ValueError


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("mission_id", "msn_" + "A" * 24),
        ("mission_id", "msn_" + "é" * 24),
        ("mission_id", "msn_" + "a" * 23),
        ("content_hash", "A" * 64),
        ("content_hash", "é" * 64),
        ("content_hash", "a" * 63),
    ),
)
def test_confirmed_constructor_rejects_noncanonical_derived_identity(
    field: str, invalid: str
) -> None:
    preview = draft().preview(1)
    confirmed = preview.confirm(
        preview_id=preview.preview_id, content_hash=preview.content_hash
    )
    values = dict(
        mission_id=confirmed.mission_id,
        version=confirmed.version,
        content_hash=confirmed.content_hash,
        canonical_content=confirmed.canonical_content,
    )
    values[field] = invalid
    with pytest.raises(ValueError) as error:
        ConfirmedMissionVersion(**values)
    assert type(error.value) is ValueError


@pytest.mark.parametrize(
    ("preview_id", "content_hash"),
    (("prv_" + "é" * 24, "0" * 64), ("prv_" + "a" * 24, "é" * 64)),
)
def test_confirmation_maps_unicode_identity_to_preview_drift(
    preview_id: str, content_hash: str
) -> None:
    with pytest.raises(PreviewDriftError):
        draft().preview(1).confirm(
            preview_id=preview_id, content_hash=content_hash
        )


@pytest.mark.parametrize("version", (1, 2**63 - 1))
def test_preview_accepts_sqlite_safe_version_boundaries(version: int) -> None:
    assert draft().preview(version).version == version


@pytest.mark.parametrize("version", (2**63,))
def test_preview_rejects_sqlite_integer_overflow(version: int) -> None:
    with pytest.raises(ValueError):
        draft().preview(version)


@pytest.mark.parametrize("constructor", (preview_from_content, confirmed_from_content))
@pytest.mark.parametrize("version", (1, 2**63 - 1))
def test_wrappers_accept_sqlite_safe_version_boundaries(
    constructor: object, version: int
) -> None:
    payload = json.loads(draft().preview(1).canonical_content)
    payload["version"] = version
    assert constructor(canonical_json(payload), version).version == version  # type: ignore[operator]


@pytest.mark.parametrize("constructor", (preview_from_content, confirmed_from_content))
@pytest.mark.parametrize("version", (True, 2**63))
def test_wrappers_reject_non_sqlite_versions(
    constructor: object, version: object
) -> None:
    payload = json.loads(draft().preview(1).canonical_content)
    payload["version"] = version
    with pytest.raises((TypeError, ValueError)):
        constructor(canonical_json(payload), version)  # type: ignore[operator]


@pytest.mark.parametrize(
    ("value", "error"), ((2**63 - 1, None), (True, TypeError), (2**63, ValueError))
)
def test_budget_values_use_sqlite_safe_integer_range(
    value: object, error: type[Exception] | None
) -> None:
    budgets = dict(draft().budgets)
    budgets["max_attempts"] = value  # type: ignore[assignment]
    if error is None:
        assert draft().revise(budgets=budgets).max_attempts == value
    else:
        with pytest.raises(error):
            draft().revise(budgets=budgets)


@pytest.mark.parametrize(
    "constructor",
    (
        lambda: draft().revise(objective="\ud800"),
        lambda: MissionDraft.coding_default(
            "drf", "objective", "\udfff", "codex", "model", "approve_for_me"
        ),
        lambda: replace(draft().tasks[0], expected_outputs=("\ud800",)),
    ),
)
def test_domain_strings_reject_isolated_surrogates_at_construction(
    constructor: object,
) -> None:
    with pytest.raises(ValueError) as error:
        constructor()  # type: ignore[operator]
    assert type(error.value) is ValueError


@pytest.mark.parametrize("wrapper", (MissionPreview, ConfirmedMissionVersion))
def test_wrapper_canonical_content_rejects_isolated_surrogates_stably(
    wrapper: object,
) -> None:
    payload = json.loads(draft().preview(1).canonical_content)
    payload["objective"] = "\ud800"
    content = canonical_json(payload)
    identity_field = "preview_id" if wrapper is MissionPreview else "mission_id"
    values = {
        identity_field: ("prv_" if wrapper is MissionPreview else "msn_") + "0" * 24,
        "version": 1,
        "content_hash": "0" * 64,
        "canonical_content": content,
    }
    with pytest.raises(ValueError) as error:
        wrapper(**values)  # type: ignore[operator]
    assert type(error.value) is ValueError


@pytest.mark.parametrize(
    "constructor",
    (
        lambda: MissionDraft.coding_default(
            "", "objective", "/tmp/p", "codex", "native-default", "approve_for_me"
        ),
        lambda: MissionDraft.coding_default(
            "drf", "objective", "/tmp/p", "codex", "native-default", "other"
        ),
        lambda: MissionDraft.coding_default(
            "drf", "objective", "/tmp/p", "codex", "native-default", 1  # type: ignore[arg-type]
        ),
        lambda: MissionDraft(
            "drf", "objective", "/tmp/p", "codex", "model", PermissionProfile.APPROVE_FOR_ME,
            (TaskDefinition("task_1", "implementation", AgentRole.IMPLEMENTER, "codex-cli", "agt_1", "acp://codex-cli/implementation", (), {Effect.READ}, ("output",), ("criterion",)),
             TaskDefinition("task_1", "review", AgentRole.REVIEWER, "claude-cli", "agt_2", "acp://claude-cli/review", ("task_1",), {Effect.READ}, ("output",), ("criterion",))),
            ("criterion",), ("non-goal",), ("risk",),
            {"max_leader_schema_repairs": 1, "max_attempts": 2, "max_revision_cycles": 1, "max_acp_reconnects": 1, "max_final_acceptance_attempts": 1},
        ),
        lambda: draft().revise(budgets={"max_leader_schema_repairs": 1, "max_attempts": 0, "max_revision_cycles": 1, "max_acp_reconnects": 1, "max_final_acceptance_attempts": 1}),
    ),
)
def test_mission_rejects_invalid_profile_budget_and_task_graph(constructor: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        constructor()  # type: ignore[operator]


def test_task_and_mission_copy_mutable_inputs_into_immutable_values() -> None:
    effects = {Effect.READ}
    outputs = ["output"]
    criteria = ["criterion"]
    task = TaskDefinition(
        "task_1", "implementation", AgentRole.IMPLEMENTER, "codex-cli", "agt_1", "acp://codex-cli/implementation", (), effects, outputs, criteria
    )
    effects.clear()
    outputs.clear()
    criteria.clear()

    copied = draft().revise(
        objective="a revised objective", tasks=list(draft().tasks)
    )
    assert task.allowed_effects == frozenset({Effect.READ})
    assert task.expected_outputs == ("output",)
    assert task.acceptance_criteria == ("criterion",)
    assert copied.tasks == draft().tasks
    with pytest.raises(FrozenInstanceError):
        copied.project_root = "/other"  # type: ignore[misc]
