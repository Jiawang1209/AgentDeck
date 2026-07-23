"""Strict Leader Port and untrusted Mission proposal boundary."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import json
from typing import Protocol

from agentdeck.kernel.agents import AgentRole
from agentdeck.kernel.mission import MissionDraft, TaskDefinition
from agentdeck.kernel.permissions import Effect, PermissionProfile
from agentdeck.ports.leader_schema import (
    BUDGET_CEILINGS as _BUDGET_CEILINGS,
    GOAL_MAX_BYTES as _GOAL_MAX_BYTES,
    PROPOSAL_FIELDS as _PROPOSAL_FIELDS,
    TASK_FIELDS as _TASK_FIELDS,
    TEXT_MAX_BYTES as _TEXT_MAX_BYTES,
    leader_proposal_json_schema,
)


class LeaderFailureCode(StrEnum):
    TIMEOUT = "timeout"
    NONZERO = "nonzero"
    AUTHENTICATION = "authentication"
    TRANSPORT = "transport"
    SCHEMA = "schema"
    SEMANTIC = "semantic"
    CANCELLATION = "cancellation"
    OVERSIZE = "oversize"
    # Unexpected non-Leader exceptions (e.g. a composition/programming error)
    # are sanitized to this category rather than mislabeled as TRANSPORT.
    INTERNAL = "internal"


_FAILURE_CODES = {item.value: item for item in LeaderFailureCode}
_PROFILES = {item.value: item for item in PermissionProfile}
_ROLES = {item.value: item for item in AgentRole}
_EFFECTS = {item.value: item for item in Effect}


class LeaderFailure(RuntimeError):
    """A stable, content-free Leader diagnostic category."""

    def __init__(self, code: LeaderFailureCode | str) -> None:
        checked = code if type(code) is LeaderFailureCode else None
        if type(code) is str:
            checked = _FAILURE_CODES.get(code)
        if checked is None:
            raise ValueError("unknown Leader failure code") from None
        self.code = checked
        super().__init__(f"Leader proposal failed: {checked.value}")


class ProposalError(LeaderFailure):
    """Raised when untrusted Leader output violates schema or semantics."""

    def __init__(self, code: LeaderFailureCode, message: str) -> None:
        if type(code) is not LeaderFailureCode or code not in {
            LeaderFailureCode.SCHEMA, LeaderFailureCode.SEMANTIC
        }:
            raise ValueError("ProposalError requires schema or semantic code")
        self.code = code
        RuntimeError.__init__(self, message)


_SUMMARY_MAX_BYTES = 16384
_MAPPING_MAX_ITEMS = 64
_PROFILE_RANK = {
    PermissionProfile.ASK_FOR_APPROVAL: 0,
    PermissionProfile.APPROVE_FOR_ME: 1,
    PermissionProfile.FULL_ACCESS: 2,
}


def _bounded_text(value: object, field: str, maximum: int = _TEXT_MAX_BYTES) -> str:
    if type(value) is not str:
        raise TypeError(f"{field} must be a string")
    invalid_utf8 = False
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        invalid_utf8 = True
        encoded = b""
    if invalid_utf8:
        raise ValueError(f"{field} must be bounded UTF-8") from None
    if not value.strip():
        raise ValueError(f"{field} must not be empty")
    if len(encoded) > maximum:
        raise ValueError(f"{field} must be bounded UTF-8")
    return value


def _profile(value: object, field: str) -> PermissionProfile:
    if type(value) is PermissionProfile:
        return value
    if type(value) is str:
        profile = _PROFILES.get(value)
        if profile is not None:
            return profile
        raise ValueError(f"{field} must be an exact permission profile")
    raise TypeError(f"{field} must be a PermissionProfile or exact string")


@dataclass(frozen=True)
class ProjectContext:
    project_root: str
    summary: str

    def __post_init__(self) -> None:
        _bounded_text(self.project_root, "project_root")
        _bounded_text(self.summary, "project summary", _SUMMARY_MAX_BYTES)


@dataclass(frozen=True)
class AvailableAgent:
    instance_id: str
    role: AgentRole
    backend_id: str
    acp_route_id: str

    def __post_init__(self) -> None:
        _bounded_text(self.instance_id, "Agent instance_id")
        if type(self.role) is not AgentRole:
            raise TypeError("Agent role must be an AgentRole")
        _bounded_text(self.backend_id, "Agent backend_id")
        route = _bounded_text(self.acp_route_id, "Agent acp_route_id")
        if not route.startswith("acp://"):
            raise ValueError("Agent acp_route_id must name an ACP route")


@dataclass(frozen=True)
class ResolvedLeaderModel:
    backend_id: str
    adapter_id: str
    model_id: str
    version: str

    def __post_init__(self) -> None:
        for value, field in (
            (self.backend_id, "resolved backend_id"),
            (self.adapter_id, "resolved adapter_id"),
            (self.model_id, "resolved model_id"),
            (self.version, "resolved version"),
        ):
            _bounded_text(value, field)


@dataclass(frozen=True)
class SchemaRepair:
    attempt: int
    code: LeaderFailureCode

    def __post_init__(self) -> None:
        if self.attempt != 1 or type(self.attempt) is not int:
            raise ValueError("schema repair attempt must be exactly one")
        if self.code is not LeaderFailureCode.SCHEMA:
            raise ValueError("schema repair category must be schema")


@dataclass(frozen=True)
class LeaderRequest:
    user_goal: str
    project_context: ProjectContext
    available_agents: tuple[AvailableAgent, ...]
    permission_ceiling: PermissionProfile
    resolved_model: ResolvedLeaderModel
    schema_repair: SchemaRepair | None = None

    def __post_init__(self) -> None:
        _bounded_text(self.user_goal, "user goal", _GOAL_MAX_BYTES)
        if type(self.project_context) is not ProjectContext:
            raise TypeError("project_context must be a ProjectContext")
        if type(self.available_agents) not in {tuple, list}:
            raise TypeError("available_agents must be a tuple or list")
        if len(self.available_agents) > 64:
            raise ValueError("available_agents must contain at most 64 items")
        agents = tuple(self.available_agents)
        if not agents or any(type(agent) is not AvailableAgent for agent in agents):
            raise TypeError("available_agents must contain AvailableAgent values")
        instance_ids = tuple(agent.instance_id for agent in agents)
        route_ids = tuple(agent.acp_route_id for agent in agents)
        if len(set(instance_ids)) != len(instance_ids) or len(set(route_ids)) != len(route_ids):
            raise ValueError("available_agents contain a duplicate Agent or ACP route")
        object.__setattr__(self, "available_agents", agents)
        if type(self.permission_ceiling) is not PermissionProfile:
            raise TypeError("permission_ceiling must be a PermissionProfile")
        if type(self.resolved_model) is not ResolvedLeaderModel:
            raise TypeError("resolved_model must be a ResolvedLeaderModel")
        if self.schema_repair is not None and type(self.schema_repair) is not SchemaRepair:
            raise TypeError("schema_repair must be a SchemaRepair or None")

    def for_schema_repair(self) -> "LeaderRequest":
        if self.schema_repair is not None:
            raise ValueError("schema repair is already requested")
        return replace(
            self,
            schema_repair=SchemaRepair(1, LeaderFailureCode.SCHEMA),
        )


class LeaderPort(Protocol):
    def propose_mission(
        self, request: LeaderRequest
    ) -> "LeaderProposal | dict[str, object]": ...


@dataclass(frozen=True)
class LeaderProposal:
    mission: MissionDraft

    def __post_init__(self) -> None:
        if type(self.mission) is not MissionDraft:
            raise TypeError("mission must be a MissionDraft")

    @property
    def objective(self) -> str:
        return self.mission.objective

    def to_mapping(self) -> dict[str, object]:
        payload = json.loads(self.mission.preview(1).canonical_content)
        payload.pop("version")
        return payload

    @classmethod
    def from_mapping(
        cls,
        payload: object,
        *,
        ceiling: PermissionProfile | str | None = None,
        request: LeaderRequest | None = None,
    ) -> "LeaderProposal":
        copied = _copy_closed_proposal(payload)
        invalid_ceiling = False
        try:
            parsed_ceiling = None if ceiling is None else _profile(ceiling, "ceiling")
        except (TypeError, ValueError):
            invalid_ceiling = True
            parsed_ceiling = None
        if invalid_ceiling:
            raise ProposalError(LeaderFailureCode.SCHEMA, "invalid permission ceiling") from None
        if request is not None and type(request) is not LeaderRequest:
            raise TypeError("request must be a LeaderRequest")
        if request is not None:
            if parsed_ceiling is not None and parsed_ceiling is not request.permission_ceiling:
                raise ValueError("ceiling and request permission ceiling disagree")
            parsed_ceiling = request.permission_ceiling
        mission = _mission_from_copied(copied)
        _validate_semantics(mission, parsed_ceiling, request)
        return cls(mission)


def _copy_closed_proposal(payload: object) -> dict[str, object]:
    copied = _closed_string_dict(
        payload,
        _PROPOSAL_FIELDS,
        type_message="proposal must be an exact mapping",
        unknown_message="proposal contains an unknown field",
        missing_message="proposal is missing field",
    )
    tasks = copied["tasks"]
    if type(tasks) is not list:
        raise ProposalError(LeaderFailureCode.SCHEMA, "tasks must be a JSON list")
    if len(tasks) != 4:
        raise ProposalError(
            LeaderFailureCode.SCHEMA, "proposal tasks must contain exactly four items"
        )
    copied["tasks"] = [
        _closed_string_dict(
            task,
            _TASK_FIELDS,
            type_message="task must be an exact mapping",
            unknown_message="proposal contains an unknown task field",
            missing_message="proposal is missing task field",
        )
        for task in tasks
    ]
    return copied


def _closed_string_dict(
    value: object,
    fields: set[str],
    *,
    type_message: str,
    unknown_message: str,
    missing_message: str,
) -> dict[str, object]:
    if type(value) is not dict:
        raise ProposalError(LeaderFailureCode.SCHEMA, type_message)
    keys_failed = False
    try:
        if dict.__len__(value) > _MAPPING_MAX_ITEMS:
            raise ValueError
        keys = tuple(dict.__iter__(value))
    except Exception:
        keys_failed = True
        keys = ()
    if keys_failed:
        raise ProposalError(LeaderFailureCode.SCHEMA, "mapping keys are invalid") from None
    if any(type(key) is not str for key in keys):
        raise ProposalError(LeaderFailureCode.SCHEMA, "mapping keys must be strings")
    invalid_key = False
    try:
        for key in keys:
            _bounded_text(key, "mapping key", 256)
    except (TypeError, ValueError):
        invalid_key = True
    if invalid_key:
        raise ProposalError(LeaderFailureCode.SCHEMA, "mapping keys are invalid") from None
    unknown = set(keys) - fields
    missing = fields - set(keys)
    if unknown:
        raise ProposalError(LeaderFailureCode.SCHEMA, unknown_message)
    if missing:
        raise ProposalError(LeaderFailureCode.SCHEMA, missing_message)
    snapshot_failed = False
    try:
        snapshot = {key: dict.__getitem__(value, key) for key in keys}
        after = tuple(dict.__iter__(value))
    except Exception:
        snapshot_failed = True
        snapshot = {}
        after = ()
    if snapshot_failed:
        raise ProposalError(LeaderFailureCode.SCHEMA, "mapping snapshot failed") from None
    if any(type(key) is not str for key in after) or after != keys:
        raise ProposalError(LeaderFailureCode.SCHEMA, "mapping changed during snapshot")
    return snapshot


def _string_list(value: object, field: str) -> tuple[str, ...]:
    if type(value) is not list:
        raise TypeError(f"{field} must be a JSON list")
    if len(value) > 64:
        raise ValueError(f"{field} exceeds its item bound")
    return tuple(_bounded_text(item, f"{field} item") for item in value)


def _budget_mapping(value: object) -> dict[str, int]:
    snapshot = _closed_string_dict(
        value,
        set(_BUDGET_CEILINGS),
        type_message="budgets must be an exact JSON object",
        unknown_message="budgets contain an unknown field",
        missing_message="budgets are missing a field",
    )
    result: dict[str, int] = {}
    for name in _BUDGET_CEILINGS:
        amount = snapshot[name]
        if type(amount) is not int or not 1 <= amount <= 2**63 - 1:
            raise ProposalError(
                LeaderFailureCode.SCHEMA,
                "budget values must be positive bounded integers",
            )
        result[name] = amount
    return result


def _mission_from_copied(payload: dict[str, object]) -> MissionDraft:
    schema_failed = False
    try:
        tasks: list[TaskDefinition] = []
        for raw in payload["tasks"]:
            dependencies = _string_list(raw["dependencies"], "dependencies")
            outputs = _string_list(raw["expected_outputs"], "expected_outputs")
            criteria = _string_list(raw["acceptance_criteria"], "task evidence criteria")
            if not criteria:
                raise ProposalError(
                    LeaderFailureCode.SEMANTIC,
                    "task evidence criteria must not be empty",
                )
            effects = _string_list(raw["allowed_effects"], "allowed_effects")
            role = _ROLES.get(_bounded_text(raw["role"], "task role"))
            resolved_effects = tuple(_EFFECTS.get(effect) for effect in effects)
            if role is None or any(effect is None for effect in resolved_effects):
                raise ProposalError(
                    LeaderFailureCode.SCHEMA,
                    "proposal uses an undeclared role or effect",
                )
            task_failed = False
            try:
                task = TaskDefinition(
                    task_id=_bounded_text(raw["task_id"], "task_id"),
                    name=_bounded_text(raw["name"], "task name"),
                    role=role,
                    backend=_bounded_text(raw["backend"], "task backend"),
                    agent_instance_id=_bounded_text(raw["agent_instance_id"], "agent_instance_id"),
                    acp_route=_bounded_text(raw["acp_route"], "ACP route"),
                    dependencies=dependencies,
                    allowed_effects=frozenset(resolved_effects),
                    expected_outputs=outputs,
                    acceptance_criteria=criteria,
                )
            except Exception:
                task_failed = True
                task = None
            if task_failed:
                raise ProposalError(
                    LeaderFailureCode.SEMANTIC,
                    "proposal violates the fixed four-stage ACP route Mission semantics",
                )
            tasks.append(task)
        acceptance = _string_list(payload["acceptance_criteria"], "evidence criteria")
        if not acceptance:
            raise ProposalError(
                LeaderFailureCode.SEMANTIC, "proposal requires nonempty evidence criteria"
            )
        budgets = _budget_mapping(payload["budgets"])
        arguments = dict(
            draft_id=_bounded_text(payload["draft_id"], "draft_id"),
            objective=_bounded_text(payload["objective"], "objective", _GOAL_MAX_BYTES),
            project_root=_bounded_text(payload["project_root"], "project_root"),
            leader_backend=_bounded_text(payload["leader_backend"], "leader_backend"),
            leader_model=_bounded_text(payload["leader_model"], "leader_model"),
            permission_profile=_profile(payload["permission_profile"], "permission_profile"),
            tasks=tasks, acceptance_criteria=acceptance,
            non_goals=_string_list(payload["non_goals"], "non_goals"),
            risks=_string_list(payload["risks"], "risks"), budgets=budgets,
            scope=_bounded_text(payload["scope"], "scope"),
            leader_adapter=_bounded_text(payload["leader_adapter"], "leader_adapter"),
            leader_version=_bounded_text(payload["leader_version"], "leader_version"),
        )
    except ProposalError:
        raise
    except Exception:
        schema_failed = True
    if schema_failed:
        raise ProposalError(
            LeaderFailureCode.SCHEMA, "proposal has an invalid field value"
        )
    mission_failed = False
    try:
        mission = MissionDraft(**arguments)
    except Exception:
        mission_failed = True
        mission = None
    if mission_failed:
        raise ProposalError(
            LeaderFailureCode.SEMANTIC,
            "proposal violates the fixed four-stage ACP route Mission semantics",
        )
    return mission


def _validate_semantics(
    mission: MissionDraft,
    ceiling: PermissionProfile | None,
    request: LeaderRequest | None,
) -> None:
    if mission.scope != "project":
        raise ProposalError(LeaderFailureCode.SEMANTIC, "proposal must use project scope")
    if ceiling is not None and _PROFILE_RANK[mission.permission_profile] > _PROFILE_RANK[ceiling]:
        raise ProposalError(LeaderFailureCode.SEMANTIC, "proposal exceeds permission ceiling")
    for name, value in mission.budgets:
        if value > _BUDGET_CEILINGS[name]:
            raise ProposalError(LeaderFailureCode.SEMANTIC, "proposal exceeds a budget ceiling")
    if request is None:
        return
    if mission.project_root != request.project_context.project_root:
        raise ProposalError(LeaderFailureCode.SEMANTIC, "proposal changes the project root")
    resolved = request.resolved_model
    actual = (
        mission.leader_backend,
        mission.leader_adapter,
        mission.leader_model,
        mission.leader_version,
    )
    expected = (resolved.backend_id, resolved.adapter_id, resolved.model_id, resolved.version)
    if actual != expected:
        raise ProposalError(LeaderFailureCode.SEMANTIC, "proposal changes the resolved Leader")
    available = {agent.instance_id: agent for agent in request.available_agents}
    for task in mission.tasks:
        agent = available.get(task.agent_instance_id)
        if agent is None:
            raise ProposalError(LeaderFailureCode.SEMANTIC, "proposal must use a known Agent")
        if (
            task.role is not agent.role
            or task.backend != agent.backend_id
            or task.acp_route != agent.acp_route_id
        ):
            raise ProposalError(LeaderFailureCode.SEMANTIC, "proposal must use a known ACP route")
