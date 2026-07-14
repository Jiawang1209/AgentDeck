from __future__ import annotations

import json
import re
import shutil
import subprocess
from json import JSONDecodeError

from .base import LeaderPlanRequest, leader_skill_context_prompt_lines, validate_provider_plan_schema


CLI_LEADER_FAILURE_STAGES = frozenset(
    {"timeout", "nonzero", "json_parse", "schema", "cancelled", "oversize"}
)
MAX_CLI_LEADER_OUTPUT_BYTES = 2 * 1024 * 1024


class CliLeaderProviderError(RuntimeError):
    """A credential-free, machine-readable CLI Leader failure."""

    def __init__(self, stage: str) -> None:
        if stage not in CLI_LEADER_FAILURE_STAGES:
            raise ValueError("invalid CLI Leader failure stage")
        self.stage = stage
        super().__init__(f"CLI Leader planning failed at stage: {stage}")


class CliLeaderProvider:
    name = "cli"
    command_name = ""
    command: list[str] = []
    timeout = 120

    def doctor(self) -> tuple[bool, str]:
        if shutil.which(self.command_name):
            return True, f"{self.command_name} is available"
        return False, f"{self.command_name} is not found on PATH"

    def plan(self, request: LeaderPlanRequest) -> dict[str, object]:
        prompt = self._prompt(request)
        try:
            result = subprocess.run(
                self._command_for_request(request),
                input=prompt,
                text=True,
                capture_output=True,
                cwd=request.config.root,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise CliLeaderProviderError("timeout") from None
        if result.returncode != 0:
            raise CliLeaderProviderError("nonzero")
        if len(result.stdout.encode("utf-8")) > MAX_CLI_LEADER_OUTPUT_BYTES:
            raise CliLeaderProviderError("oversize")
        return self._parse_plan(result.stdout, request)

    def _command_for_request(self, request: LeaderPlanRequest) -> list[str]:
        if not request.model:
            return list(self.command)
        return self._command_with_model(request.model)

    def _command_with_model(self, model: str) -> list[str]:
        return [*self.command[:1], "--model", model, *self.command[1:]]

    def _prompt(self, request: LeaderPlanRequest) -> str:
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
        lines = [
            "You are the AgentDeck Leader Agent.",
            "You are the logical Leader Agent with agent_id=leader.",
            "You are backed by this local CLI subprocess only for reasoning.",
            "Do not reuse worker tmux panes or claim a dedicated Leader pane.",
            "Return only a JSON object plan. Do not dispatch work.",
            "Every step must require human approval before dispatch.",
            "Required schema: goal, summary, steps, approval_required, dispatch_ready.",
            "Each step must include: step, agent_id, role, task, risk, requires_approval.",
            "Step numbers must be 1..n without duplicates or gaps.",
            "Use only listed worker agent_id values and copy each worker role exactly.",
            f"Available worker agents: {json.dumps(workers, ensure_ascii=False)}",
        ]
        lines.extend(leader_skill_context_prompt_lines(request.skill_context))
        lines.append(f"Goal: {request.task}")
        return "\n".join(lines)

    def _parse_plan(self, stdout: str, request: LeaderPlanRequest) -> dict[str, object]:
        text = stdout.strip()
        try:
            plan = self._load_json_plan(text)
        except (RuntimeError, JSONDecodeError):
            raise CliLeaderProviderError("json_parse") from None
        try:
            self._validate_plan(plan, request)
        except (RuntimeError, TypeError, ValueError):
            raise CliLeaderProviderError("schema") from None
        return plan

    def _load_json_plan(self, text: str) -> object:
        try:
            return json.loads(text)
        except JSONDecodeError:
            pass
        fenced_plans = []
        for match in re.finditer(r"```(?:json)?\s*(?P<body>.*?)\s*```", text, re.IGNORECASE | re.DOTALL):
            try:
                fenced_plans.append(json.loads(match.group("body").strip()))
            except JSONDecodeError:
                continue
        if len(fenced_plans) == 1:
            return fenced_plans[0]
        if len(fenced_plans) > 1:
            raise RuntimeError("provider plan content contains multiple JSON plans")
        raise RuntimeError("provider plan content is not valid JSON")

    def _validate_plan(self, plan: object, request: LeaderPlanRequest) -> None:
        validate_provider_plan_schema(plan, config=request.config)


class CodexCliProvider(CliLeaderProvider):
    name = "codex-cli"
    command_name = "codex"
    command = ["codex", "exec", "--sandbox", "read-only", "-"]


class ClaudeCliProvider(CliLeaderProvider):
    name = "claude-cli"
    command_name = "claude"
    command = ["claude", "--print", "--output-format", "text", "--permission-mode", "plan"]
