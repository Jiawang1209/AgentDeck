"""Run the bare conversation CLI with a deterministic test Leader."""

from __future__ import annotations

from agentdeck import cli
from agentdeck.mission_orchestration import LeaderMissionCandidate
from agentdeck.providers import LeaderPlanRequest
from agentdeck.providers.plan_schema import build_leader_generation_provenance


def deterministic_mission(self, request, cancel):
    del self
    if cancel.cancelled:
        raise RuntimeError("test Leader request cancelled")
    return LeaderMissionCandidate(
        provider="fake",
        model="fake-plan",
        user_message=request.user_message,
        timeout_seconds=request.timeout_seconds,
        selected_agent_ids=request.selected_agent_ids,
        step_count=request.step_count,
        leader_generation=build_leader_generation_provenance(
            request=LeaderPlanRequest(
                task=request.planning_task,
                config=request.config,
                model=request.config.leader.model,
                skill_context=request.skill_context,
                selected_agent_ids=request.selected_agent_ids,
                step_count=request.step_count,
                timeout_seconds=request.timeout_seconds,
            ),
            provider=request.config.leader.provider,
            constraint_mode="local",
        ),
        plan={
            "goal": "implement then review",
            "summary": "two ordered workers",
            "steps": [
                {
                    "step": 1,
                    "agent_id": "planner",
                    "role": "planning",
                    "task": "implement",
                    "risk": "review",
                    "requires_approval": True,
                },
                {
                    "step": 2,
                    "agent_id": "reviewer",
                    "role": "review",
                    "task": "review",
                    "risk": "review",
                    "requires_approval": True,
                },
            ],
            "approval_required": True,
            "dispatch_ready": False,
            "declared_tests": ["deterministic acceptance"],
            "acceptance_criteria": ["ordered handoff"],
        },
    )


def main() -> int:
    cli.LeaderGateway.generate_mission = deterministic_mission
    return cli.main([])


if __name__ == "__main__":
    raise SystemExit(main())
