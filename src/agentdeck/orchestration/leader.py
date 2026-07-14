from __future__ import annotations

from dataclasses import asdict
from typing import Any, cast

from agentdeck.models import ProjectConfig
from agentdeck.providers import LeaderPlanRequest, LeaderPlanResult, LeaderProvider
from agentdeck.providers.plan_schema import (
    ProviderPlanValidationError,
    build_leader_generation_provenance,
    build_leader_plan_schema,
    leader_plan_authority,
    validate_provider_plan_schema,
)


def _exact_json_value(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        left_dict = cast(dict[object, object], left)
        right_dict = cast(dict[object, object], right)
        if len(left_dict) != len(right_dict):
            return False
        if any(type(key) is not str or key not in right_dict for key in left_dict):
            return False
        return all(
            _exact_json_value(value, right_dict[key])
            for key, value in left_dict.items()
        )
    if type(left) is list:
        left_list = cast(list[object], left)
        right_list = cast(list[object], right)
        return len(left_list) == len(right_list) and all(
            _exact_json_value(left_item, right_item)
            for left_item, right_item in zip(left_list, right_list)
        )
    if type(left) in {str, int, bool, type(None)}:
        return left == right
    return False


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
    ) -> dict[str, object]:
        return self.plan_result(
            task,
            model,
            skill_context=skill_context,
            selected_agent_ids=selected_agent_ids,
            step_count=step_count,
            timeout_seconds=timeout_seconds,
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
    ) -> LeaderPlanResult:
        if self.provider is None:
            raise RuntimeError("leader provider is not configured")
        request = LeaderPlanRequest(
            task=task,
            config=self.config,
            model=model,
            skill_context=skill_context,
            selected_agent_ids=selected_agent_ids,
            step_count=step_count,
            timeout_seconds=timeout_seconds,
        )
        resolved_agent_ids, resolved_step_count = leader_plan_authority(request)
        native_plan_result = getattr(self.provider, "plan_result", None)
        if callable(native_plan_result):
            result = native_plan_result(request)
            if not isinstance(result, LeaderPlanResult):
                raise TypeError("leader provider plan_result must return LeaderPlanResult")
            plan = validate_provider_plan_schema(
                result.plan,
                config=self.config,
                selected_agent_ids=resolved_agent_ids,
                step_count=resolved_step_count,
            )
            provenance = result.leader_generation
            if type(provenance) is not dict:
                raise _invalid_native_provenance()
            constraint_mode = provenance.get("constraint_mode")
            attempt_count = provenance.get("attempt_count")
            if type(constraint_mode) is not str or type(attempt_count) is not int:
                raise _invalid_native_provenance()
            schema = (
                build_leader_plan_schema(request)
                if constraint_mode == "native_json_schema"
                else None
            )
            try:
                expected_provenance = build_leader_generation_provenance(
                    request=request,
                    provider=self.provider.name,
                    constraint_mode=constraint_mode,
                    schema=schema,
                    attempt_count=attempt_count,
                )
            except (ProviderPlanValidationError, TypeError, ValueError):
                raise _invalid_native_provenance() from None
            if not _exact_json_value(provenance, expected_provenance):
                raise _invalid_native_provenance()
            return LeaderPlanResult(
                plan=plan,
                leader_generation=expected_provenance,
            )
        plan = validate_provider_plan_schema(
            self.provider.plan(request),
            config=self.config,
            selected_agent_ids=resolved_agent_ids,
            step_count=resolved_step_count,
        )
        constraint_mode = getattr(self.provider, "constraint_mode", "local")
        if type(constraint_mode) is str and constraint_mode == "native_json_schema":
            raise ProviderPlanValidationError(
                "native_schema_unavailable",
                "legacy leader provider cannot claim native JSON schema",
            )
        return LeaderPlanResult(
            plan=plan,
            leader_generation=build_leader_generation_provenance(
                request=request,
                provider=self.provider.name,
                constraint_mode=constraint_mode,
            ),
        )
