from __future__ import annotations

import json
import os
from json import JSONDecodeError
from urllib import request

from .base import LeaderPlanRequest, validate_provider_plan_schema


class OpenAICompatibleProvider:
    name = "openai-compatible"

    api_key_env = "AGENTDECK_LEADER_API_KEY"
    base_url_env = "AGENTDECK_LEADER_BASE_URL"
    model_env = "AGENTDECK_LEADER_MODEL"

    def __init__(self, model: str | None = None, base_url: str | None = None, timeout: int = 60) -> None:
        self.model = model or os.environ.get(self.model_env, "deepseek-chat")
        self.base_url = (base_url or os.environ.get(self.base_url_env, "https://api.deepseek.com/v1")).rstrip("/")
        self.timeout = timeout

    def doctor(self) -> tuple[bool, str]:
        if os.environ.get(self.api_key_env):
            return True, f"{self.api_key_env} is set"
        return False, f"{self.api_key_env} is not set; provider calls are disabled"

    def plan(self, plan_request: LeaderPlanRequest) -> dict[str, object]:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(f"{self.api_key_env} is not set")
        payload = {
            "model": plan_request.model or self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": self._system_prompt(plan_request),
                },
                {
                    "role": "user",
                    "content": plan_request.task,
                },
            ],
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with request.urlopen(req, timeout=self.timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        plan = self._extract_plan(data)
        self._validate_plan(plan, plan_request)
        return plan

    def _system_prompt(self, request: LeaderPlanRequest) -> str:
        workers = [
            {
                "agent_id": agent.agent_id,
                "role": agent.role,
                "role_prompt": agent.role_prompt,
                "provider": agent.provider,
                "workspace_mode": agent.workspace_mode,
            }
            for agent in request.config.agents
        ]
        return "\n".join(
            [
                "You are the AgentDeck Leader Agent.",
                "Return only a JSON object plan. Do not dispatch work.",
                "Every step must require human approval before dispatch.",
                "Required schema: goal, summary, steps, approval_required, dispatch_ready.",
                "Each step must include: step, agent_id, role, task, risk, requires_approval.",
                f"Available workers: {json.dumps(workers, ensure_ascii=False)}",
            ]
        )

    @staticmethod
    def _extract_plan(response: dict[str, object]) -> dict[str, object]:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("provider response missing choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise RuntimeError("provider response choice is invalid")
        message = first.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("provider response missing message")
        content = message.get("content")
        if not isinstance(content, str):
            raise RuntimeError("provider response missing message content")
        try:
            parsed = json.loads(content)
        except JSONDecodeError as exc:
            raise RuntimeError("provider plan content is not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("provider plan content is not a JSON object")
        return parsed

    @staticmethod
    def _validate_plan(plan: dict[str, object], request: LeaderPlanRequest) -> None:
        validate_provider_plan_schema(plan, config=request.config)
