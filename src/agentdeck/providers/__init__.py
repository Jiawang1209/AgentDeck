from .base import LeaderPlanRequest, LeaderPlanResult, LeaderProvider
from .cli_subprocess import ClaudeCliProvider, CodexCliProvider
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
    if name == "codex-cli":
        return CodexCliProvider()
    if name == "claude-cli":
        return ClaudeCliProvider()
    raise ValueError(f"unsupported leader provider: {name}")


__all__ = [
    "ClaudeCliProvider",
    "CodexCliProvider",
    "DeepSeekProvider",
    "FakeLeaderProvider",
    "LeaderPlanRequest",
    "LeaderPlanResult",
    "LeaderProvider",
    "OpenAICompatibleProvider",
    "leader_provider",
]
