from __future__ import annotations

import os


class DeepSeekProvider:
    """OpenAI-compatible DeepSeek adapter boundary.

    The MVP only validates configuration. Network calls will be added after the
    Leader/Worker message contract and approval gate are in place.
    """

    api_key_env = "DEEPSEEK_API_KEY"
    base_url = "https://api.deepseek.com"

    def __init__(self, model: str = "deepseek-chat") -> None:
        self.model = model

    def doctor(self) -> tuple[bool, str]:
        if os.environ.get(self.api_key_env):
            return True, f"{self.api_key_env} is set"
        return False, f"{self.api_key_env} is not set; provider calls are disabled"
