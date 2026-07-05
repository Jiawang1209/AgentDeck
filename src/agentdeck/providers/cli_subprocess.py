from __future__ import annotations

import json
import shutil
import subprocess
from json import JSONDecodeError

from .base import LeaderPlanRequest


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
        result = subprocess.run(
            self.command,
            input=prompt,
            text=True,
            capture_output=True,
            cwd=request.config.root,
            timeout=self.timeout,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
            raise RuntimeError(f"{self.name} failed: {detail}")
        return self._parse_plan(result.stdout)

    def _prompt(self, request: LeaderPlanRequest) -> str:
        workers = [
            {
                "agent_id": agent.agent_id,
                "role": agent.role,
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
                f"Available worker agents: {json.dumps(workers, ensure_ascii=False)}",
                f"Goal: {request.task}",
            ]
        )

    def _parse_plan(self, stdout: str) -> dict[str, object]:
        text = stdout.strip()
        try:
            plan = json.loads(text)
        except JSONDecodeError as exc:
            raise RuntimeError("provider plan content is not valid JSON") from exc
        self._validate_plan(plan)
        return plan

    def _validate_plan(self, plan: object) -> None:
        if not isinstance(plan, dict):
            raise RuntimeError("provider plan content must be a JSON object")
        if not isinstance(plan.get("steps"), list) or not plan["steps"]:
            raise RuntimeError("provider plan must include non-empty steps")
        for step in plan["steps"]:
            if not isinstance(step, dict):
                raise RuntimeError("provider plan steps must be objects")
            if step.get("requires_approval") is not True:
                raise RuntimeError("provider plan steps must require approval")


class CodexCliProvider(CliLeaderProvider):
    name = "codex-cli"
    command_name = "codex"
    command = ["codex", "exec", "--sandbox", "read-only", "-"]


class ClaudeCliProvider(CliLeaderProvider):
    name = "claude-cli"
    command_name = "claude"
    command = ["claude", "--print", "--output-format", "text", "--permission-mode", "plan"]
