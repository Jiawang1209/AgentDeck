from .base import LeaderPlanRequest, LeaderProvider
from .deepseek import DeepSeekProvider
from .fake import FakeLeaderProvider


def leader_provider(name: str) -> LeaderProvider:
    if name == "fake":
        return FakeLeaderProvider()
    raise ValueError(f"unsupported leader provider: {name}")


__all__ = ["DeepSeekProvider", "FakeLeaderProvider", "LeaderPlanRequest", "LeaderProvider", "leader_provider"]
