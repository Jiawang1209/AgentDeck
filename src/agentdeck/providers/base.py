from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentdeck.models import ProjectConfig


@dataclass(frozen=True)
class LeaderPlanRequest:
    task: str
    config: ProjectConfig
    model: str | None = None


class LeaderProvider(Protocol):
    name: str

    def plan(self, request: LeaderPlanRequest) -> dict[str, object]:
        """Return a structured plan without dispatching work."""
