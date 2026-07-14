from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from json import JSONDecodeError

from .base import (
    LeaderPlanRequest,
    LeaderPlanResult,
    leader_skill_context_prompt_lines,
    validate_provider_plan_schema,
)
from .plan_schema import (
    ProviderPlanValidationError,
    build_leader_generation_provenance,
    build_leader_plan_schema,
    leader_plan_authority,
)


CLI_LEADER_FAILURE_STAGES = frozenset(
    {"timeout", "nonzero", "json_parse", "schema", "cancelled", "oversize"}
)
MAX_CLI_LEADER_OUTPUT_BYTES = 2 * 1024 * 1024


class CliLeaderProviderError(RuntimeError):
    """A credential-free, machine-readable CLI Leader failure."""

    def __init__(self, stage: str, diagnostic_code: str | None = None) -> None:
        if stage not in CLI_LEADER_FAILURE_STAGES:
            raise ValueError("invalid CLI Leader failure stage")
        self.stage = stage
        self.diagnostic_code = diagnostic_code
        super().__init__(f"CLI Leader planning failed at stage: {stage}")


class CliLeaderProvider:
    name = "cli"
    constraint_mode = "prompt_only"
    command_name = ""
    command: list[str] = []
    timeout = 120

    def doctor(self) -> tuple[bool, str]:
        if shutil.which(self.command_name):
            return True, f"{self.command_name} is available"
        return False, f"{self.command_name} is not found on PATH"

    def plan(self, request: LeaderPlanRequest) -> dict[str, object]:
        return self.plan_result(request).plan

    def plan_result(self, request: LeaderPlanRequest) -> LeaderPlanResult:
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
        return LeaderPlanResult(
            plan=self._parse_plan(result.stdout, request),
            leader_generation=build_leader_generation_provenance(
                request=request,
                provider=self.name,
                constraint_mode=self.constraint_mode,
            ),
        )

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
    constraint_mode = "native_json_schema"
    command_name = "codex"
    command = ["codex", "exec", "--sandbox", "read-only", "-"]

    def plan_result(self, request: LeaderPlanRequest) -> LeaderPlanResult:
        schema = build_leader_plan_schema(request)
        prompt = self._prompt(request)
        with tempfile.TemporaryDirectory(prefix="agentdeck-leader-") as temp_dir:
            schema_path = os.path.join(temp_dir, "schema.json")
            result_path = os.path.join(temp_dir, "result.json")
            self._write_schema(schema_path, schema)
            base_command = self._command_for_request(request)
            insert_at = len(base_command) - 1
            command = [
                *base_command[:insert_at],
                "--output-schema",
                schema_path,
                "--output-last-message",
                result_path,
                *base_command[insert_at:],
            ]
            try:
                completed = subprocess.run(
                    command,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    cwd=request.config.root,
                    timeout=(
                        request.timeout_seconds
                        if request.timeout_seconds is not None
                        else self.timeout
                    ),
                    check=False,
                )
            except subprocess.TimeoutExpired:
                raise CliLeaderProviderError("timeout") from None
            returncode = completed.returncode
            del completed
            if returncode != 0:
                raise CliLeaderProviderError("nonzero")
            plan = self._read_native_plan(result_path, request)
        return LeaderPlanResult(
            plan=plan,
            leader_generation=build_leader_generation_provenance(
                request=request,
                provider=self.name,
                constraint_mode=self.constraint_mode,
                schema=schema,
                attempt_count=1,
            ),
        )

    @staticmethod
    def _write_schema(path: str, schema: dict[str, object]) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise CliLeaderProviderError("json_parse", "invalid_output_envelope")
        try:
            descriptor = os.open(path, flags | nofollow, 0o600)
        except OSError:
            raise CliLeaderProviderError("json_parse", "invalid_output_envelope") from None
        try:
            mode = stat.S_IMODE(os.fstat(descriptor).st_mode)
            if mode != 0o600:
                raise CliLeaderProviderError("json_parse", "invalid_output_envelope")
            payload = json.dumps(
                schema,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise CliLeaderProviderError("json_parse", "invalid_output_envelope")
                view = view[written:]
        except CliLeaderProviderError:
            raise
        except OSError:
            raise CliLeaderProviderError("json_parse", "invalid_output_envelope") from None
        finally:
            os.close(descriptor)

    def _read_native_plan(
        self, path: str, request: LeaderPlanRequest
    ) -> dict[str, object]:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise CliLeaderProviderError("json_parse", "invalid_output_envelope")
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | nofollow)
        except OSError:
            raise CliLeaderProviderError("json_parse", "invalid_output_envelope") from None
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise CliLeaderProviderError("json_parse", "invalid_output_envelope")
            if file_stat.st_size > MAX_CLI_LEADER_OUTPUT_BYTES:
                raise CliLeaderProviderError("oversize")
            chunks: list[bytes] = []
            remaining = MAX_CLI_LEADER_OUTPUT_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) > MAX_CLI_LEADER_OUTPUT_BYTES:
                raise CliLeaderProviderError("oversize")
        except CliLeaderProviderError:
            raise
        except OSError:
            raise CliLeaderProviderError("json_parse", "invalid_output_envelope") from None
        finally:
            os.close(descriptor)
        try:
            plan = json.loads(payload.decode("utf-8", errors="strict"))
        except (JSONDecodeError, UnicodeDecodeError):
            raise CliLeaderProviderError("json_parse", "invalid_output_envelope") from None
        if not isinstance(plan, dict):
            raise CliLeaderProviderError("json_parse", "invalid_output_envelope")
        selected_agent_ids, step_count = leader_plan_authority(request)
        try:
            return validate_provider_plan_schema(
                plan,
                config=request.config,
                selected_agent_ids=selected_agent_ids,
                step_count=step_count,
            )
        except ProviderPlanValidationError as error:
            raise CliLeaderProviderError("schema", error.code) from None


class ClaudeCliProvider(CliLeaderProvider):
    name = "claude-cli"
    command_name = "claude"
    command = ["claude", "--print", "--output-format", "text", "--permission-mode", "plan"]
