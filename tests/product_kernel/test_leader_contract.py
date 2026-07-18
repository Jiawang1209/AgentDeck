from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
import traceback

import pytest

import agentdeck.ports.leader as leader_module
from agentdeck.kernel.agents import AgentRole
from agentdeck.kernel.mission import MissionDraft
from agentdeck.kernel.permissions import PermissionProfile
from agentdeck.ports.leader import (
    AvailableAgent,
    LeaderProposal,
    LeaderRequest,
    ProjectContext,
    ProposalError,
    ResolvedLeaderModel,
    leader_proposal_json_schema,
)


PROJECT_ROOT = "/tmp/project"
HOSTILE_KEY_MARKER = "attacker-controlled-key-marker"


class HostileKey:
    def __init__(self, collides_with: str) -> None:
        self.collides_with = collides_with

    def __hash__(self) -> int:
        return hash(self.collides_with)

    def __eq__(self, other: object) -> bool:
        raise RuntimeError(HOSTILE_KEY_MARKER)


def proposal_with_hostile_key(location: str) -> dict[str, object]:
    payload = valid_proposal()
    target, replaced_key = {
        "proposal": (payload, "objective"),
        "task": (payload["tasks"][0], "name"),
        "budgets": (payload["budgets"], "max_attempts"),
    }[location]
    target.pop(replaced_key)
    target[HostileKey(replaced_key)] = "untrusted"  # type: ignore[index]
    return payload


def exception_chain_text(error: BaseException) -> str:
    pending = [error]
    seen: set[int] = set()
    rendered: list[str] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        rendered.append(f"{type(current).__name__}: {current}")
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return "\n".join(rendered)


def valid_proposal() -> dict[str, object]:
    draft = MissionDraft.coding_default(
        draft_id="drf_1",
        objective="Build an accessible page",
        project_root=PROJECT_ROOT,
        leader_backend="codex-cli",
        leader_model="native-default",
        permission_profile="approve_for_me",
        leader_version="1.2.3",
    )
    payload = json.loads(draft.preview(1).canonical_content)
    payload.pop("version")
    return payload


def request(*, ceiling: str = "approve_for_me") -> LeaderRequest:
    payload = valid_proposal()
    agents = tuple(
        AvailableAgent(
            instance_id=task["agent_instance_id"],
            role=AgentRole(task["role"]),
            backend_id=task["backend"],
            acp_route_id=task["acp_route"],
        )
        for task in payload["tasks"]
    )
    return LeaderRequest(
        user_goal="Build an accessible page",
        project_context=ProjectContext(
            project_root=PROJECT_ROOT,
            summary="Python project; no uncommitted generated output",
        ),
        available_agents=agents,
        permission_ceiling=PermissionProfile(ceiling),
        resolved_model=ResolvedLeaderModel(
            backend_id="codex-cli",
            adapter_id="acp",
            model_id="native-default",
            version="1.2.3",
        ),
    )


@pytest.mark.parametrize("field", ["objective", "tasks", "budgets"])
def test_proposal_rejects_missing_fields(field: str) -> None:
    payload = valid_proposal()
    payload.pop(field)

    with pytest.raises(ProposalError, match="missing field") as error:
        LeaderProposal.from_mapping(payload)

    assert error.value.code == "schema"


def test_proposal_rejects_unknown_top_level_and_task_fields() -> None:
    top_level = valid_proposal()
    top_level["dispatch_now"] = True
    task_level = valid_proposal()
    task_level["tasks"][0]["confirmed"] = True

    with pytest.raises(ProposalError, match="unknown field"):
        LeaderProposal.from_mapping(top_level)
    with pytest.raises(ProposalError, match="unknown task field"):
        LeaderProposal.from_mapping(task_level)


def test_valid_proposal_becomes_a_frozen_mission_draft() -> None:
    proposal = LeaderProposal.from_mapping(valid_proposal(), request=request())

    assert proposal.objective == "Build an accessible page"
    assert proposal.mission.permission_profile is PermissionProfile.APPROVE_FOR_ME
    assert [task.name for task in proposal.mission.tasks] == [
        "implementation",
        "review",
        "revision",
        "acceptance",
    ]
    with pytest.raises(FrozenInstanceError):
        proposal.mission.objective = "changed"  # type: ignore[misc]


def test_proposal_cannot_raise_the_permission_ceiling() -> None:
    payload = valid_proposal()
    payload["permission_profile"] = "full_access"

    with pytest.raises(ProposalError, match="permission ceiling") as error:
        LeaderProposal.from_mapping(payload, ceiling="approve_for_me")

    assert error.value.code == "semantic"


def test_lower_permission_profile_is_allowed() -> None:
    payload = valid_proposal()
    payload["permission_profile"] = "ask_for_approval"

    proposal = LeaderProposal.from_mapping(
        payload, ceiling=PermissionProfile.APPROVE_FOR_ME
    )

    assert proposal.mission.permission_profile is PermissionProfile.ASK_FOR_APPROVAL


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload.update(scope="workspace"), "project scope"),
        (lambda payload: payload.update(project_root="/tmp/other"), "project root"),
        (
            lambda payload: payload["tasks"][1].update(dependencies=[]),
            "four-stage",
        ),
        (
            lambda payload: payload["tasks"][0].update(role="reviewer"),
            "four-stage",
        ),
        (
            lambda payload: payload["tasks"][0].update(backend="claude-cli"),
            "four-stage",
        ),
        (
            lambda payload: payload["tasks"][1].update(
                task_id=payload["tasks"][0]["task_id"]
            ),
            "four-stage",
        ),
        (
            lambda payload: payload["tasks"][0].update(
                dependencies=[payload["tasks"][0]["task_id"]]
            ),
            "four-stage",
        ),
        (
            lambda payload: payload["tasks"][1].update(acp_route="acp://unknown/review"),
            "ACP route",
        ),
        (
            lambda payload: payload["tasks"][2].update(agent_instance_id="agt_unknown"),
            "known Agent",
        ),
        (
            lambda payload: payload["budgets"].update(max_attempts=3),
            "budget ceiling",
        ),
        (lambda payload: payload.update(acceptance_criteria=[]), "evidence criteria"),
    ],
)
def test_semantic_validation_fails_closed(mutate: object, message: str) -> None:
    payload = valid_proposal()
    mutate(payload)  # type: ignore[operator]

    with pytest.raises(ProposalError, match=message) as error:
        LeaderProposal.from_mapping(payload, request=request())

    assert error.value.code == "semantic"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("leader_backend", "claude-cli"),
        ("leader_adapter", "stdio"),
        ("leader_model", "fallback-model"),
        ("leader_version", "unknown"),
    ],
)
def test_proposal_must_preserve_the_resolved_leader_identity(
    field: str, value: str
) -> None:
    payload = valid_proposal()
    payload[field] = value

    with pytest.raises(ProposalError, match="resolved Leader"):
        LeaderProposal.from_mapping(payload, request=request())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["tasks"][0].update(allowed_effects=["root"]),
        lambda payload: payload["budgets"].update(max_attempts=True),
    ],
)
def test_invalid_types_and_declared_values_are_schema_errors(mutate: object) -> None:
    payload = valid_proposal()
    mutate(payload)  # type: ignore[operator]

    with pytest.raises(ProposalError) as error:
        LeaderProposal.from_mapping(payload)

    assert error.value.code == "schema"


def test_empty_task_evidence_criteria_is_a_semantic_error() -> None:
    payload = valid_proposal()
    payload["tasks"][0]["acceptance_criteria"] = []

    with pytest.raises(ProposalError, match="evidence criteria") as error:
        LeaderProposal.from_mapping(payload)

    assert error.value.code == "semantic"


def test_hostile_nested_value_cannot_escape_the_schema_category() -> None:
    class HostileValue:
        def __deepcopy__(self, memo: object) -> object:
            raise RuntimeError("attacker controlled marker")

    payload = valid_proposal()
    payload["objective"] = HostileValue()

    with pytest.raises(ProposalError) as error:
        LeaderProposal.from_mapping(payload)

    assert error.value.code == "schema"
    assert "attacker controlled marker" not in str(error.value)


@pytest.mark.parametrize("location", ["proposal", "task", "budgets"])
def test_hostile_mapping_key_fails_as_content_free_schema(location: str) -> None:
    payload = proposal_with_hostile_key(location)

    with pytest.raises(ProposalError) as error:
        LeaderProposal.from_mapping(payload)

    assert error.value.code == "schema"
    assert HOSTILE_KEY_MARKER not in str(error.value)


def test_untrusted_enum_marker_is_absent_from_the_complete_exception_chain() -> None:
    marker = "attacker-controlled-enum-marker"
    payload = valid_proposal()
    payload["permission_profile"] = marker

    with pytest.raises(ProposalError) as error:
        LeaderProposal.from_mapping(payload)

    rendered = "".join(traceback.format_exception(error.value))
    assert error.value.code == "schema"
    assert marker not in rendered


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload, marker: payload.update(permission_profile=marker),
        lambda payload, marker: payload["tasks"][0].update(role=marker),
        lambda payload, marker: payload["tasks"][0].update(allowed_effects=[marker]),
        lambda payload, marker: payload.update(objective=f"{marker}\ud800"),
    ],
)
def test_untrusted_conversion_marker_is_absent_from_recursive_chain(
    mutate: object,
) -> None:
    marker = "recursive-untrusted-conversion-marker"
    payload = valid_proposal()
    mutate(payload, marker)  # type: ignore[operator]

    with pytest.raises(ProposalError) as error:
        LeaderProposal.from_mapping(payload)

    assert marker not in exception_chain_text(error.value)


@pytest.mark.parametrize("validator_name", ["TaskDefinition", "MissionDraft"])
def test_validator_marker_is_absent_from_recursive_chain(
    validator_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = f"recursive-{validator_name}-marker"

    def rejected(*args: object, **kwargs: object) -> object:
        raise ValueError(marker)

    monkeypatch.setattr(leader_module, validator_name, rejected)

    with pytest.raises(ProposalError) as error:
        LeaderProposal.from_mapping(valid_proposal())

    assert marker not in exception_chain_text(error.value)


def test_oversized_task_list_is_rejected_before_any_item_is_touched() -> None:
    payload = valid_proposal()
    hostile_task = payload["tasks"][0].copy()
    hostile_task.pop("name")
    hostile_task[HostileKey("name")] = "untrusted"
    payload["tasks"] = [hostile_task] * 65

    with pytest.raises(ProposalError, match="exactly four") as error:
        LeaderProposal.from_mapping(payload)

    assert error.value.code == "schema"
    assert HOSTILE_KEY_MARKER not in "".join(traceback.format_exception(error.value))


def test_request_and_proposal_defensively_copy_mutable_inputs() -> None:
    payload = valid_proposal()
    source = deepcopy(payload)
    proposal = LeaderProposal.from_mapping(payload, request=request())
    mutable_agents = list(request().available_agents)
    leader_request = LeaderRequest(
        user_goal="Build page",
        project_context=request().project_context,
        available_agents=mutable_agents,
        permission_ceiling=PermissionProfile.APPROVE_FOR_ME,
        resolved_model=request().resolved_model,
    )

    payload["objective"] = "mutated"
    payload["tasks"][0]["name"] = "mutated"
    mutable_agents.clear()

    assert proposal.to_mapping() == source
    assert len(leader_request.available_agents) == 4


def test_request_rejects_unbounded_or_duplicate_agent_context() -> None:
    base = request()

    with pytest.raises(ValueError, match="bounded UTF-8"):
        LeaderRequest(
            user_goal="x" * 65537,
            project_context=base.project_context,
            available_agents=base.available_agents,
            permission_ceiling=base.permission_ceiling,
            resolved_model=base.resolved_model,
        )


def test_request_enforces_compact_available_agent_limit_before_copy() -> None:
    base = request()
    agents = tuple(
        AvailableAgent(
            instance_id=f"agt_extra_{index}",
            role=AgentRole.IMPLEMENTER,
            backend_id="codex-cli",
            acp_route_id=f"acp://codex-cli/extra-{index}",
        )
        for index in range(64)
    )

    bounded = LeaderRequest(
        user_goal=base.user_goal,
        project_context=base.project_context,
        available_agents=agents,
        permission_ceiling=base.permission_ceiling,
        resolved_model=base.resolved_model,
    )
    assert len(bounded.available_agents) == 64

    with pytest.raises(ValueError, match="at most 64"):
        LeaderRequest(
            user_goal=base.user_goal,
            project_context=base.project_context,
            available_agents=[object()] * 65,  # type: ignore[list-item]
            permission_ceiling=base.permission_ceiling,
            resolved_model=base.resolved_model,
        )
    with pytest.raises(ValueError, match="duplicate Agent"):
        LeaderRequest(
            user_goal="Build page",
            project_context=base.project_context,
            available_agents=(base.available_agents[0], base.available_agents[0]),
            permission_ceiling=base.permission_ceiling,
            resolved_model=base.resolved_model,
        )


def test_json_schema_is_closed_and_returned_as_a_defensive_copy() -> None:
    first = leader_proposal_json_schema()
    allowed_effects = first["properties"]["tasks"]["items"]["properties"][
        "allowed_effects"
    ]
    assert (allowed_effects["minItems"], allowed_effects["maxItems"]) == (1, 64)
    first["additionalProperties"] = True
    first["properties"]["tasks"]["items"]["additionalProperties"] = True
    allowed_effects["minItems"] = 0

    second = leader_proposal_json_schema()

    assert second["additionalProperties"] is False
    assert second["properties"]["tasks"]["items"]["additionalProperties"] is False
    fresh_effects = second["properties"]["tasks"]["items"]["properties"][
        "allowed_effects"
    ]
    assert (fresh_effects["minItems"], fresh_effects["maxItems"]) == (1, 64)
    assert set(second["required"]) == set(second["properties"])
