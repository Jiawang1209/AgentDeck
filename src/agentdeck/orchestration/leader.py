from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any, cast

from agentdeck.models import AgentSpec, ProjectConfig
from agentdeck.providers import LeaderPlanRequest, LeaderPlanResult, LeaderProvider
from agentdeck.providers.plan_schema import (
    ProviderPlanValidationError,
    build_leader_generation_provenance,
    leader_plan_authority,
    validate_leader_generation_provenance,
    validate_provider_plan_schema,
)
from agentdeck.providers.semantic_plan_schema import (
    SemanticPlanSchemaAuthorityError,
    resolve_semantic_leader_plan_context,
)
from agentdeck.semantic_authority import SemanticAuthorityError, validate_semantic_authority
from agentdeck.semantic_planning import (
    SemanticPlanningError,
    compile_semantic_plan,
    semantic_step_hash,
)


_COMPILED_SEMANTIC_PLAN_FIELDS = frozenset(
    {"goal", "summary", "steps", "semantic_authority", "semantic_steps"}
)
_COMPILED_SEMANTIC_STEP_FIELDS = frozenset(
    {
        "step",
        "agent_id",
        "role",
        "phase",
        "authority_refs",
        "proposed_effects",
        "verification",
        "risk",
        "requires_approval",
        "required_effects",
        "semantic_step_hash",
    }
)


def _exact_json_value(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        left_dict = cast(dict[object, object], left)
        right_dict = cast(dict[object, object], right)
        return (
            len(left_dict) == len(right_dict)
            and all(type(key) is str and key in right_dict for key in left_dict)
            and all(
                _exact_json_value(value, right_dict[key])
                for key, value in left_dict.items()
            )
        )
    if type(left) is list:
        left_list = cast(list[object], left)
        right_list = cast(list[object], right)
        return len(left_list) == len(right_list) and all(
            _exact_json_value(left_item, right_item)
            for left_item, right_item in zip(left_list, right_list)
        )
    return type(left) in {str, int, bool, type(None)} and left == right


def _validate_compiled_semantic_plan(
    request: LeaderPlanRequest, plan: object
) -> dict[str, object]:
    try:
        return rebuild_compiled_semantic_plan(request, plan)
    except Exception:
        raise SemanticPlanningError("semantic_compilation_drift") from None


def rebuild_compiled_semantic_plan(
    request: LeaderPlanRequest, plan: object
) -> dict[str, object]:
    if type(plan) is not dict or set(plan) != _COMPILED_SEMANTIC_PLAN_FIELDS:
        raise SemanticPlanningError("semantic_compilation_drift")
    semantic_steps = plan.get("semantic_steps")
    if (
        type(request.step_count) is not int
        or type(semantic_steps) is not list
        or len(semantic_steps) != request.step_count
    ):
        raise SemanticPlanningError("semantic_compilation_drift")
    candidate_steps: list[dict[str, object]] = []
    for raw_step in semantic_steps:
        semantic_step_hash(raw_step)
        if type(raw_step) is not dict or set(raw_step) != _COMPILED_SEMANTIC_STEP_FIELDS:
            raise SemanticPlanningError("semantic_compilation_drift")
        proposals = raw_step.get("proposed_effects")
        if type(proposals) is not list:
            raise SemanticPlanningError("semantic_compilation_drift")
        candidate_proposals: list[dict[str, object]] = []
        for proposal in proposals:
            if type(proposal) is not dict or set(proposal) != {
                "proposed_effect_id",
                "target",
                "operation",
                "sensitivity",
            }:
                raise SemanticPlanningError("semantic_compilation_drift")
            candidate_proposals.append(
                {
                    "target": proposal["target"],
                    "operation": proposal["operation"],
                    "sensitivity": proposal["sensitivity"],
                }
            )
        candidate_steps.append(
            {
                key: deepcopy(value)
                for key, value in raw_step.items()
                if key not in {"required_effects", "semantic_step_hash", "proposed_effects"}
            }
            | {"proposed_effects": candidate_proposals}
        )
    try:
        selected, roles, count = resolve_semantic_leader_plan_context(
            selected_agent_ids=request.selected_agent_ids,
            step_count=request.step_count,
            configured_context=tuple(
                agent for agent in request.config.agents if type(agent) is AgentSpec
            ),
        )
        expected = compile_semantic_plan(
            request.semantic_authority,
            {
                "goal": plan["goal"],
                "summary": plan["summary"],
                "steps": candidate_steps,
            },
            selected_agent_ids=selected,
            roles=roles,
            step_count=count,
        )
    except (SemanticPlanSchemaAuthorityError, TypeError, ValueError):
        raise SemanticPlanningError("semantic_compilation_drift") from None
    if not _exact_json_value(plan, expected):
        raise SemanticPlanningError("semantic_compilation_drift")
    return expected


def _invalid_native_provenance() -> ProviderPlanValidationError:
    return ProviderPlanValidationError(
        "invalid_output_envelope",
        "leader provider plan result provenance is invalid",
    )


class LeaderOrchestrator:
    """Plan-only skeleton for the Leader Agent.

    The first implementation returns a deterministic project plan so the CLI,
    state, runtime, and approval boundaries can stabilize before LLM calls are
    introduced.
    """

    def __init__(self, config: ProjectConfig, provider: LeaderProvider | None = None) -> None:
        self.config = config
        self.provider = provider

    def describe_team(self) -> dict[str, object]:
        return {
            "leader": asdict(self.config.leader),
            "workers": [asdict(agent) for agent in self.config.agents],
        }

    def plan(
        self,
        task: str,
        model: str | None = None,
        *,
        skill_context: dict[str, Any] | None = None,
        selected_agent_ids: tuple[str, ...] | None = None,
        step_count: int | None = None,
        timeout_seconds: int | None = None,
        semantic_authority: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return self.plan_result(
            task,
            model,
            skill_context=skill_context,
            selected_agent_ids=selected_agent_ids,
            step_count=step_count,
            timeout_seconds=timeout_seconds,
            semantic_authority=semantic_authority,
        ).plan

    def plan_result(
        self,
        task: str,
        model: str | None = None,
        *,
        skill_context: dict[str, Any] | None = None,
        selected_agent_ids: tuple[str, ...] | None = None,
        step_count: int | None = None,
        timeout_seconds: int | None = None,
        semantic_authority: dict[str, object] | None = None,
    ) -> LeaderPlanResult:
        if self.provider is None:
            raise RuntimeError("leader provider is not configured")
        provider_ref = self.provider
        provider_name = getattr(provider_ref, "name", None)
        constraint_mode_snapshot = getattr(provider_ref, "constraint_mode", "local")
        native_plan_result = getattr(provider_ref, "plan_result", None)
        provider_plan = getattr(provider_ref, "plan", None)
        provider_name_is_valid = (
            type(provider_name) is str and bool(provider_name.strip())
        )
        if semantic_authority is not None and not provider_name_is_valid:
            raise _invalid_native_provenance()
        effective_model = model
        if semantic_authority is not None and model is None:
            default_model = getattr(provider_ref, "model", None)
            if type(default_model) is str and default_model.strip():
                effective_model = default_model
        authority_snapshot: dict[str, object] | None = None
        if semantic_authority is not None:
            try:
                authority_snapshot = validate_semantic_authority(semantic_authority)
            except (SemanticAuthorityError, TypeError, ValueError):
                raise SemanticPlanningError("semantic_compilation_failed") from None
        request = LeaderPlanRequest(
            task=task,
            config=self.config,
            model=effective_model,
            skill_context=skill_context,
            selected_agent_ids=selected_agent_ids,
            step_count=step_count,
            timeout_seconds=timeout_seconds,
            semantic_authority=authority_snapshot,
        )
        if authority_snapshot is None:
            resolved_agent_ids, resolved_step_count = leader_plan_authority(request)
            provider_request = request
        else:
            try:
                resolved_agent_ids, _roles, resolved_step_count = (
                    resolve_semantic_leader_plan_context(
                        selected_agent_ids=request.selected_agent_ids,
                        step_count=request.step_count,
                        configured_context=tuple(
                            agent
                            for agent in request.config.agents
                            if type(agent) is AgentSpec
                        ),
                    )
                )
            except (SemanticPlanSchemaAuthorityError, TypeError, ValueError):
                raise SemanticPlanningError("semantic_compilation_failed") from None
            provider_request = LeaderPlanRequest(
                task=request.task,
                config=request.config,
                model=request.model,
                skill_context=request.skill_context,
                selected_agent_ids=request.selected_agent_ids,
                step_count=request.step_count,
                timeout_seconds=request.timeout_seconds,
                semantic_authority=deepcopy(authority_snapshot),
                regeneration_diagnostic=request.regeneration_diagnostic,
            )
        if callable(native_plan_result):
            result = native_plan_result(provider_request)
            if not isinstance(result, LeaderPlanResult):
                raise TypeError("leader provider plan_result must return LeaderPlanResult")
            if not provider_name_is_valid:
                raise _invalid_native_provenance()
            if authority_snapshot is None:
                if type(result.plan) is dict and (
                    "semantic_authority" in result.plan
                    or "semantic_steps" in result.plan
                ):
                    raise SemanticPlanningError("semantic_compilation_drift")
                plan = validate_provider_plan_schema(
                    result.plan,
                    config=self.config,
                    selected_agent_ids=resolved_agent_ids,
                    step_count=resolved_step_count,
                )
            else:
                plan = _validate_compiled_semantic_plan(request, result.plan)
            provenance = result.leader_generation
            try:
                expected_provenance = validate_leader_generation_provenance(
                    request=request,
                    provider=provider_name,
                    provenance=provenance,
                )
            except (ProviderPlanValidationError, TypeError, ValueError):
                raise _invalid_native_provenance() from None
            return LeaderPlanResult(
                plan=plan,
                leader_generation=expected_provenance,
                semantic_diagnostics=result.semantic_diagnostics,
            )
        if not callable(provider_plan):
            raise TypeError("leader provider plan must be callable")
        raw_plan = provider_plan(provider_request)
        if not provider_name_is_valid:
            raise _invalid_native_provenance()
        if authority_snapshot is None:
            if type(raw_plan) is dict and (
                "semantic_authority" in raw_plan or "semantic_steps" in raw_plan
            ):
                raise SemanticPlanningError("semantic_compilation_drift")
            plan = validate_provider_plan_schema(
                raw_plan,
                config=self.config,
                selected_agent_ids=resolved_agent_ids,
                step_count=resolved_step_count,
            )
        else:
            plan = _validate_compiled_semantic_plan(request, raw_plan)
        constraint_mode = constraint_mode_snapshot
        if type(constraint_mode) is str and constraint_mode == "native_json_schema":
            raise ProviderPlanValidationError(
                "native_schema_unavailable",
                "legacy leader provider cannot claim native JSON schema",
            )
        return LeaderPlanResult(
            plan=plan,
            leader_generation=build_leader_generation_provenance(
                request=request,
                provider=provider_name,
                constraint_mode=constraint_mode,
            ),
        )
