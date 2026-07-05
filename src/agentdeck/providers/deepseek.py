from __future__ import annotations

from .openai_compatible import OpenAICompatibleProvider


class DeepSeekProvider(OpenAICompatibleProvider):
    """DeepSeek adapter using the OpenAI-compatible chat completions contract."""

    name = "deepseek"
    api_key_env = "DEEPSEEK_API_KEY"
    base_url_env = "DEEPSEEK_BASE_URL"
    model_env = "DEEPSEEK_MODEL"

    def __init__(self, model: str | None = None, base_url: str | None = None, timeout: int = 60) -> None:
        super().__init__(
            model=model,
            base_url=base_url,
            timeout=timeout,
        )
