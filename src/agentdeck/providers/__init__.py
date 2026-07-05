from .base import LeaderPlanRequest, LeaderProvider
from .deepseek import DeepSeekProvider
from .fake import FakeLeaderProvider
from .openai_compatible import OpenAICompatibleProvider


def leader_provider(name: str) -> LeaderProvider:
    if name == "fake":
        return FakeLeaderProvider()
    if name == "deepseek":
        return DeepSeekProvider()
    if name == "openai-compatible":
        return OpenAICompatibleProvider()
    raise ValueError(f"unsupported leader provider: {name}")


__all__ = [
    "DeepSeekProvider",
    "FakeLeaderProvider",
    "LeaderPlanRequest",
    "LeaderProvider",
    "OpenAICompatibleProvider",
    "leader_provider",
]
