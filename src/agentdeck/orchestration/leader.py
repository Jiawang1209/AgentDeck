from __future__ import annotations

from dataclasses import asdict

from agentdeck.models import ProjectConfig
from agentdeck.providers import LeaderPlanRequest, LeaderProvider


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

    def plan(self, task: str) -> dict[str, object]:
        if self.provider is None:
            raise RuntimeError("leader provider is not configured")
        return self.provider.plan(LeaderPlanRequest(task=task, config=self.config))
