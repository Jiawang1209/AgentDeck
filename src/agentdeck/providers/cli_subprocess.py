from __future__ import annotations

from dataclasses import replace
import json
import math
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from typing import BinaryIO
from json import JSONDecodeError

from agentdeck.models import AgentSpec
from agentdeck.semantic_authority import validate_semantic_authority
from agentdeck.semantic_planning import (
    SEMANTIC_REGENERABLE_FAILURE_CODES,
    SemanticPlanningError,
    compile_semantic_plan,
    semantic_regeneration_guidance,
)

from .base import (
    LeaderPlanRequest,
    LeaderPlanResult,
    build_refine_rework_prompt,
    leader_planner_brief_prompt_lines,
    leader_skill_context_prompt_lines,
    validate_provider_plan_schema,
)
from .planner_brief import build_planner_prompt, validate_planner_brief
from .plan_schema import (
    LEADER_CONSTRAINT_MODES,
    LEADER_PLAN_DIAGNOSTIC_CODES,
    ProviderPlanValidationError,
    build_leader_generation_provenance,
    build_leader_plan_schema,
    leader_plan_authority,
)
from .cli_failure import CLI_FAILURE_REASONS, classify_cli_failure
from .semantic_plan_schema import (
    SemanticPlanSchemaAuthorityError,
    resolve_semantic_leader_plan_context,
    semantic_authority_identity,
)


CLI_LEADER_FAILURE_STAGES = frozenset(
    {"timeout", "nonzero", "json_parse", "schema", "cancelled", "oversize"}
)
MAX_CLI_LEADER_OUTPUT_BYTES = 2 * 1024 * 1024
_NATIVE_PLAN_FIELDS = frozenset({"goal", "summary", "steps"})
_NATIVE_STEP_FIELDS = frozenset(
    {"step", "agent_id", "role", "task", "risk", "requires_approval"}
)
_NATIVE_SEMANTIC_STEP_FIELDS = frozenset(
    {
        "step",
        "agent_id",
        "role",
        "phase",
        "authority_refs",
        "proposed_effects",
        "verification",
        "risk",
        "requires_approval",
    }
)
_REGENERABLE_SCHEMA_CODES = frozenset(
    {
        "missing_required_field",
        "invalid_top_level_type",
        "invalid_string_field",
        "invalid_step_type",
        "invalid_step_count",
        "invalid_step_numbering",
        "unknown_agent",
        "role_mismatch",
        "approval_not_required",
    }
)


class _StrictJsonError(RuntimeError):
    pass


class _NativeOutputError(RuntimeError):
    def __init__(self, stage: str, diagnostic_code: str | None = None) -> None:
        self.stage = stage
        self.diagnostic_code = diagnostic_code
        super().__init__("native CLI output is invalid")


class _PrivateOutputSink:
    def __init__(
        self,
        path: str,
        output: BinaryIO,
        creation_identity: tuple[object, ...],
    ) -> None:
        self.path = path
        self.output = output
        self.creation_identity = creation_identity

    def fileno(self) -> int:
        return self.output.fileno()

    def flush(self) -> None:
        self.output.flush()

    def write(self, payload: bytes) -> int:
        return self.output.write(payload)

    def close(self) -> None:
        self.output.close()


def _strict_json_decode(payload: bytes) -> object:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise _StrictJsonError("duplicate JSON object key")
            result[key] = value
        return result

    def reject_constant(_value: str) -> object:
        raise _StrictJsonError("non-JSON numeric constant")

    def parse_finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise _StrictJsonError("non-finite JSON number")
        return parsed

    invalid = False
    decoded: object = None
    try:
        text = payload.decode("utf-8", errors="strict")
        decoded = json.loads(
            text,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
            parse_float=parse_finite_float,
        )
    except (
        JSONDecodeError,
        UnicodeDecodeError,
        _StrictJsonError,
        ValueError,
        RecursionError,
    ):
        invalid = True
    if invalid:
        raise _StrictJsonError("strict JSON decoding failed")
    return decoded


class CliLeaderProviderError(RuntimeError):
    """A credential-free, machine-readable CLI Leader failure."""

    _FROZEN_FIELDS = frozenset(
        {
            "stage",
            "diagnostic_code",
            "attempt_count",
            "constraint_mode",
            "retryable",
            "exit_code",
            "failure_reason",
        }
    )

    def __init__(
        self,
        stage: str,
        diagnostic_code: str | None = None,
        *,
        attempt_count: int = 0,
        constraint_mode: str | None = None,
        retryable: bool = False,
        exit_code: int | None = None,
        failure_reason: str | None = None,
    ) -> None:
        if type(stage) is not str or stage not in CLI_LEADER_FAILURE_STAGES:
            raise ValueError("invalid CLI Leader failure stage")
        if (
            diagnostic_code is not None
            and (
                type(diagnostic_code) is not str
                or diagnostic_code not in LEADER_PLAN_DIAGNOSTIC_CODES
            )
        ):
            raise ValueError("invalid CLI Leader diagnostic code")
        if (
            (
                stage == "json_parse"
                and diagnostic_code not in {None, "invalid_output_envelope"}
            )
            or (stage == "schema" and diagnostic_code == "invalid_output_envelope")
            or (stage not in {"json_parse", "schema"} and diagnostic_code is not None)
        ):
            raise ValueError("invalid CLI Leader stage and diagnostic combination")
        if type(attempt_count) is not int or attempt_count < 0 or attempt_count > 2:
            raise ValueError("invalid CLI Leader attempt count")
        if type(retryable) is not bool:
            raise ValueError("invalid CLI Leader retryable flag")
        if retryable and not (
            (stage == "json_parse" and diagnostic_code == "invalid_output_envelope")
            or (
                stage == "schema"
                and diagnostic_code
                in (_REGENERABLE_SCHEMA_CODES | SEMANTIC_REGENERABLE_FAILURE_CODES)
            )
        ):
            raise ValueError("invalid CLI Leader retryable combination")
        if (
            constraint_mode is not None
            and (
                type(constraint_mode) is not str
                or constraint_mode not in LEADER_CONSTRAINT_MODES
            )
        ):
            raise ValueError("invalid CLI Leader constraint mode")
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "diagnostic_code", diagnostic_code)
        object.__setattr__(self, "attempt_count", attempt_count)
        object.__setattr__(self, "constraint_mode", constraint_mode)
        if exit_code is not None and (
            type(exit_code) is not int or isinstance(exit_code, bool)
        ):
            raise ValueError("invalid CLI Leader exit code")
        if failure_reason is not None and failure_reason not in CLI_FAILURE_REASONS:
            raise ValueError("invalid CLI Leader failure reason")
        object.__setattr__(self, "retryable", retryable)
        object.__setattr__(self, "exit_code", exit_code)
        object.__setattr__(self, "failure_reason", failure_reason)
        # 诊断后缀只含**我们自己的**闭合枚举码与进程退出码(进程事实),
        # 绝不含任何 provider 输出片段。
        detail = ""
        if exit_code is not None or failure_reason is not None:
            parts = []
            if exit_code is not None:
                parts.append(f"exit={exit_code}")
            if failure_reason is not None:
                parts.append(f"reason={failure_reason}")
            detail = " (" + ", ".join(parts) + ")"
        super().__init__(f"CLI Leader planning failed at stage: {stage}{detail}")
        object.__setattr__(self, "_metadata_frozen", True)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_metadata_frozen", False) and name in self._FROZEN_FIELDS:
            raise AttributeError("CLI Leader failure metadata is immutable")
        super().__setattr__(name, value)

    def with_attempt_count(self, attempt_count: int) -> CliLeaderProviderError:
        return CliLeaderProviderError(
            self.stage,
            self.diagnostic_code,
            attempt_count=attempt_count,
            constraint_mode=self.constraint_mode,
            retryable=self.retryable,
            exit_code=self.exit_code,
            failure_reason=self.failure_reason,
        )

    def without_retry(self) -> CliLeaderProviderError:
        return CliLeaderProviderError(
            self.stage,
            self.diagnostic_code,
            attempt_count=self.attempt_count,
            constraint_mode=self.constraint_mode,
            retryable=False,
            exit_code=self.exit_code,
            failure_reason=self.failure_reason,
        )


def _create_private_workspace(prefix: str) -> str | None:
    path: str | None = None
    try:
        path = tempfile.mkdtemp(prefix=prefix)
    except OSError:
        return None
    directory_stat: os.stat_result | None = None
    try:
        directory_stat = os.stat(path, follow_symlinks=False)
    except OSError:
        pass
    if (
        directory_stat is None
        or not stat.S_ISDIR(directory_stat.st_mode)
        or directory_stat.st_uid != os.geteuid()
        or stat.S_IMODE(directory_stat.st_mode) != 0o700
    ):
        _cleanup_private_workspace(path)
        return None
    return path


def _cleanup_private_workspace(path: str) -> bool:
    try:
        shutil.rmtree(path)
    except OSError:
        return False
    return True


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

    def plan_brief(
        self,
        *,
        task: str,
        model: str | None = None,
        skill_context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        prompt = build_planner_prompt(task, skill_context)
        command = self._command_with_model(model) if model else list(self.command)
        try:
            result = subprocess.run(
                command,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise CliLeaderProviderError("timeout") from None
        except OSError:
            raise CliLeaderProviderError("nonzero") from None
        if result.returncode != 0:
            # 诊断:退出码是进程事实;reason 是闭合枚举码(解析→分类→丢弃
            # 原文),二者都不含 provider 输出片段。
            raise CliLeaderProviderError(
                "nonzero",
                exit_code=result.returncode,
                failure_reason=classify_cli_failure(result.stdout, result.stderr),
            )
        if len(result.stdout.encode("utf-8")) > MAX_CLI_LEADER_OUTPUT_BYTES:
            raise CliLeaderProviderError("oversize")
        try:
            parsed = self._brief_json(result.stdout)
        except (RuntimeError, JSONDecodeError):
            raise CliLeaderProviderError("json_parse") from None
        return validate_planner_brief(parsed)

    def refine_rework_task(
        self,
        *,
        task: str,
        feedback: str,
        model: str | None = None,
    ) -> str:
        """把审查意见精修成回炉任务正文——纯文本进、纯文本出。

        一次调用即止:不解析 JSON、不重试、不改 step 结构。任何失败都
        fail-closed 抛出,由调用方回落确定性模板并如实报告。"""
        prompt = build_refine_rework_prompt(task=task, feedback=feedback)
        command = self._command_with_model(model) if model else list(self.command)
        try:
            result = subprocess.run(
                command,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise CliLeaderProviderError("timeout") from None
        except OSError:
            raise CliLeaderProviderError("nonzero") from None
        if result.returncode != 0:
            # 诊断:退出码是进程事实;reason 是闭合枚举码(解析→分类→丢弃
            # 原文),二者都不含 provider 输出片段。
            raise CliLeaderProviderError(
                "nonzero",
                exit_code=result.returncode,
                failure_reason=classify_cli_failure(result.stdout, result.stderr),
            )
        if len(result.stdout.encode("utf-8")) > MAX_CLI_LEADER_OUTPUT_BYTES:
            raise CliLeaderProviderError("oversize")
        return self._refined_text(result.stdout)

    def _refined_text(self, stdout: str) -> str:
        return stdout

    def _brief_json(self, stdout: str) -> object:
        return self._load_json_plan(stdout.strip())

    def plan_result(self, request: LeaderPlanRequest) -> LeaderPlanResult:
        prompt = self._prompt(request)
        process_error: CliLeaderProviderError | None = None
        result: subprocess.CompletedProcess[str] | None = None
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
            process_error = CliLeaderProviderError("timeout")
        except OSError:
            process_error = CliLeaderProviderError("nonzero")
        if process_error is not None:
            raise process_error
        if result is None:
            raise CliLeaderProviderError("nonzero")
        if result.returncode != 0:
            # 诊断:退出码是进程事实;reason 是闭合枚举码(解析→分类→丢弃
            # 原文),二者都不含 provider 输出片段。
            raise CliLeaderProviderError(
                "nonzero",
                exit_code=result.returncode,
                failure_reason=classify_cli_failure(result.stdout, result.stderr),
            )
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

    @staticmethod
    def _positive_timeout(value: object) -> float:
        if type(value) not in {int, float}:
            raise ValueError("CLI Leader planning timeout must be a positive number")
        try:
            timeout = float(value)
        except (OverflowError, ValueError):
            raise ValueError(
                "CLI Leader planning timeout must be a positive number"
            ) from None
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("CLI Leader planning timeout must be a positive number")
        return timeout

    def _native_plan_result(self, request: LeaderPlanRequest) -> LeaderPlanResult:
        if request.semantic_authority is not None:
            try:
                authority_snapshot = validate_semantic_authority(
                    request.semantic_authority
                )
            except (TypeError, ValueError):
                raise CliLeaderProviderError(
                    "schema",
                    "authority_invalid",
                    attempt_count=0,
                    constraint_mode=self.constraint_mode,
                ) from None
            request = replace(request, semantic_authority=authority_snapshot)
        provider_timeout = self._positive_timeout(self.timeout)
        requested_timeout = self._positive_timeout(
            self.timeout
            if request.timeout_seconds is None
            else request.timeout_seconds
        )
        budget = min(requested_timeout, provider_timeout)
        now = time.monotonic()
        deadline = now + budget
        if not math.isfinite(now) or not math.isfinite(deadline):
            raise ValueError("CLI Leader planning timeout must be a positive number")
        schema_failed = False
        schema: dict[str, object] | None = None
        try:
            schema = build_leader_plan_schema(request)
        except ProviderPlanValidationError:
            schema_failed = True
        if schema_failed or schema is None:
            raise CliLeaderProviderError(
                "schema",
                "authority_invalid",
                attempt_count=0,
                constraint_mode=self.constraint_mode,
            ) from None
        original_prompt = self._prompt(request)
        prompt = original_prompt
        attempt_request = request
        attempted = 0
        semantic_diagnostics: list[dict[str, object]] = []
        for attempt in (1, 2):
            attempt_now = time.monotonic()
            remaining = deadline - attempt_now
            if (
                not math.isfinite(attempt_now)
                or not math.isfinite(remaining)
                or remaining <= 0
            ):
                raise CliLeaderProviderError(
                    "timeout",
                    attempt_count=attempted,
                    constraint_mode=self.constraint_mode,
                ) from None
            attempted = attempt
            try:
                plan = self._native_attempt(
                    request=attempt_request,
                    schema=schema,
                    prompt=prompt,
                    deadline=deadline,
                )
            except CliLeaderProviderError as error:
                safe_error = CliLeaderProviderError(
                    error.stage,
                    error.diagnostic_code,
                    constraint_mode=self.constraint_mode,
                    retryable=error.retryable,
                    exit_code=error.exit_code,
                    failure_reason=error.failure_reason,
                ).with_attempt_count(attempt)
            else:
                completed_at = time.monotonic()
                if not math.isfinite(completed_at) or completed_at >= deadline:
                    raise CliLeaderProviderError(
                        "timeout",
                        attempt_count=attempt,
                        constraint_mode=self.constraint_mode,
                    ) from None
                return LeaderPlanResult(
                    plan=plan,
                    leader_generation=build_leader_generation_provenance(
                        request=request,
                        provider=self.name,
                        constraint_mode=self.constraint_mode,
                        schema=schema,
                        attempt_count=attempt,
                    ),
                    semantic_diagnostics=tuple(semantic_diagnostics),
                )
            if attempt == 2 or safe_error.retryable is not True:
                raise safe_error from None
            diagnostic = safe_error.diagnostic_code or safe_error.stage
            if request.semantic_authority is not None:
                if diagnostic not in SEMANTIC_REGENERABLE_FAILURE_CODES:
                    raise safe_error.without_retry() from None
                semantic_diagnostics.append(
                    {
                        "code": diagnostic,
                        "attempt_count": attempt,
                        "regeneration_used": attempt > 1,
                    }
                )
                attempt_request = replace(
                    request, regeneration_diagnostic=diagnostic
                )
                prompt = self._prompt(attempt_request)
            else:
                prompt = (
                    f"{original_prompt}\n"
                    "Regenerate the complete plan.\n"
                    f"Previous attempt diagnostic: {diagnostic}.\n"
                    "Return a complete replacement; do not discuss the previous output."
                )
        raise CliLeaderProviderError(
            "timeout",
            attempt_count=attempted,
            constraint_mode=self.constraint_mode,
        ) from None

    @staticmethod
    def _remaining_subprocess_timeout(deadline: float) -> float:
        now = time.monotonic()
        remaining = deadline - now
        if not math.isfinite(now) or not math.isfinite(remaining) or remaining <= 0:
            raise CliLeaderProviderError("timeout")
        return remaining

    def _native_attempt(
        self,
        *,
        request: LeaderPlanRequest,
        schema: dict[str, object],
        prompt: str,
        deadline: float,
    ) -> dict[str, object]:
        raise NotImplementedError

    def _command_for_request(self, request: LeaderPlanRequest) -> list[str]:
        if not request.model:
            return list(self.command)
        return self._command_with_model(request.model)

    def _command_with_model(self, model: str) -> list[str]:
        return [*self.command[:1], "--model", model, *self.command[1:]]

    def _prompt(self, request: LeaderPlanRequest) -> str:
        if request.semantic_authority is not None:
            return self._semantic_prompt(request)
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
        _, step_count = leader_plan_authority(request)
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
            f"Plan between 1 and {step_count} steps; use only as many steps as the task actually needs.",
            "Use only listed worker agent_id values and copy each worker role exactly.",
            f"Available worker agents: {json.dumps(workers, ensure_ascii=False)}",
        ]
        lines.extend(leader_skill_context_prompt_lines(request.skill_context))
        lines.extend(leader_planner_brief_prompt_lines(request.planner_brief))
        lines.append(f"Goal: {request.task}")
        return "\n".join(lines)

    @staticmethod
    def _semantic_config_context(request: LeaderPlanRequest) -> tuple[AgentSpec, ...]:
        return tuple(
            item for item in request.config.agents if type(item) is AgentSpec
        )

    @staticmethod
    def _semantic_prompt(request: LeaderPlanRequest) -> str:
        try:
            schema_version, authority_hash = semantic_authority_identity(
                request.semantic_authority
            )
            agents, roles, step_count = resolve_semantic_leader_plan_context(
                selected_agent_ids=request.selected_agent_ids,
                step_count=request.step_count,
                configured_context=CliLeaderProvider._semantic_config_context(request),
            )
            authority = validate_semantic_authority(request.semantic_authority)
        except (SemanticPlanSchemaAuthorityError, TypeError, ValueError):
            raise CliLeaderProviderError(
                "schema", "authority_invalid", attempt_count=0
            ) from None
        requirements = [
            {
                "requirement_id": item["requirement_id"],
                "kind": item["kind"],
                "target": item["target"],
                "operation": item["operation"],
                "phase": item["phase"],
                "agent_id": item["agent_id"],
                "sensitivity": item["sensitivity"],
            }
            for item in authority["requirements"]
        ]
        compact_authority = {
            "schema_version": schema_version,
            "authority_hash": authority_hash,
            "requirement_count": len(requirements),
            "requirements": requirements,
        }
        worker_context = [
            {"agent_id": agent_id, "role": roles[agent_id]}
            for agent_id in agents
        ]
        lines = [
            "You are the AgentDeck Leader Agent.",
            "Return only one semantic JSON candidate; do not dispatch or execute work.",
            "Required top-level fields: goal, summary, steps.",
            "Never output an executable task field.",
            "Every step must include step, agent_id, role, phase, authority_refs, proposed_effects, verification, risk, requires_approval.",
            "Use every required authority reference exactly once and preserve atomic transitions.",
            "Targets already represented by required authority must appear only through authority_refs and must not appear in proposed_effects.",
            "Every proposed_effects target must be genuinely new and appear at most once across the complete candidate.",
            "Only project-local ordinary proposed effects are reviewable; they are not authority until confirmation.",
            "Every step risk must be low and requires_approval must be true.",
            f"Exact step count: {step_count}",
            f"Selected workers: {json.dumps(worker_context, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}",
            f"Compact semantic authority: {json.dumps(compact_authority, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}",
        ]
        if request.regeneration_diagnostic is not None:
            lines.extend(
                [
                    "Regenerate the complete semantic candidate.",
                    f"Previous attempt diagnostic: {request.regeneration_diagnostic}.",
                ]
            )
            lines.extend(
                semantic_regeneration_guidance(request.regeneration_diagnostic)
            )
            lines.append(
                "Return a complete replacement; do not discuss or reproduce the previous output."
            )
        return "\n".join(lines)

    def _parse_plan(self, stdout: str, request: LeaderPlanRequest) -> dict[str, object]:
        text = stdout.strip()
        parse_failed = False
        plan: object = None
        try:
            plan = self._load_json_plan(text)
        except (RuntimeError, JSONDecodeError):
            parse_failed = True
        if parse_failed:
            raise CliLeaderProviderError("json_parse")
        schema_failed = False
        try:
            self._validate_plan(plan, request)
        except (RuntimeError, TypeError, ValueError):
            schema_failed = True
        if schema_failed:
            raise CliLeaderProviderError("schema")
        if not isinstance(plan, dict):
            raise CliLeaderProviderError("schema")
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
    native_help_command = ("codex", "exec", "--help")
    native_required_flags = ("--output-schema", "--output-last-message")

    def _prompt(self, request: LeaderPlanRequest) -> str:
        return super()._prompt(request).replace(
            "Required schema: goal, summary, steps, approval_required, dispatch_ready.",
            "Required schema: goal, summary, steps.\n"
            "Output only those top-level fields; approval_required and dispatch_ready "
            "are normalized locally and must not be output.",
        )

    def plan_result(self, request: LeaderPlanRequest) -> LeaderPlanResult:
        return self._native_plan_result(request)

    def _native_attempt(
        self,
        *,
        request: LeaderPlanRequest,
        schema: dict[str, object],
        prompt: str,
        deadline: float,
    ) -> dict[str, object]:
        temp_dir = _create_private_workspace("agentdeck-leader-")
        if temp_dir is None:
            raise CliLeaderProviderError(
                "json_parse", "invalid_output_envelope"
            ) from None
        pending_error: CliLeaderProviderError | None = None
        plan: dict[str, object] | None = None
        try:
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
            process_error: CliLeaderProviderError | None = None
            completed: subprocess.CompletedProcess[str] | None = None
            try:
                completed = subprocess.run(
                    command,
                    input=prompt,
                    text=True,
                    # codex 的 stdout/stderr 在 **OS 边界**即丢弃——这是被
                    # 对抗性测试加固的不变量(诊断输出永不进入本进程),
                    # 比"读进来再丢"更强。因此 codex 失败只能报退出码。
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    cwd=request.config.root,
                    timeout=self._remaining_subprocess_timeout(deadline),
                    check=False,
                )
            except subprocess.TimeoutExpired:
                process_error = CliLeaderProviderError("timeout")
            except OSError:
                process_error = CliLeaderProviderError("nonzero")
            if process_error is not None:
                raise process_error
            if completed is None:
                raise CliLeaderProviderError("nonzero")
            returncode = completed.returncode
            del completed
            if returncode != 0:
                # codex 诊断在 OS 边界即丢弃(见上),没有可检查的输出——
                # 只记进程退出码,failure_reason 保持 None(绝不臆测)。
                raise CliLeaderProviderError("nonzero", exit_code=returncode)
            plan = self._read_native_plan(result_path, request)
        except CliLeaderProviderError as error:
            pending_error = error
        finally:
            cleanup_succeeded = _cleanup_private_workspace(temp_dir)
        if not cleanup_succeeded:
            if pending_error is None:
                pending_error = CliLeaderProviderError(
                    "json_parse", "invalid_output_envelope"
                )
            else:
                pending_error = pending_error.without_retry()
        if pending_error is not None:
            raise pending_error
        if plan is None:
            raise CliLeaderProviderError("schema")
        return plan

    @staticmethod
    def _write_schema(path: str, schema: dict[str, object]) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise CliLeaderProviderError("json_parse", "invalid_output_envelope")
        descriptor: int | None = None
        open_failed = False
        try:
            descriptor = os.open(path, flags | nofollow, 0o600)
        except OSError:
            open_failed = True
        if open_failed or descriptor is None:
            raise CliLeaderProviderError("json_parse", "invalid_output_envelope")
        pending_error: CliLeaderProviderError | None = None
        try:
            mode = stat.S_IMODE(os.fstat(descriptor).st_mode)
            if mode != 0o600:
                raise _NativeOutputError("json_parse", "invalid_output_envelope")
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
                    raise _NativeOutputError("json_parse", "invalid_output_envelope")
                view = view[written:]
        except _NativeOutputError as error:
            pending_error = CliLeaderProviderError(error.stage, error.diagnostic_code)
        except OSError:
            pending_error = CliLeaderProviderError(
                "json_parse", "invalid_output_envelope"
            )
        close_failed = False
        try:
            os.close(descriptor)
        except OSError:
            close_failed = True
        if close_failed and pending_error is None:
            pending_error = CliLeaderProviderError(
                "json_parse", "invalid_output_envelope"
            )
        if pending_error is not None:
            raise pending_error

    @staticmethod
    def _stable_file_metadata(file_stat: os.stat_result) -> tuple[object, ...]:
        return (
            file_stat.st_dev,
            file_stat.st_ino,
            stat.S_IFMT(file_stat.st_mode),
            file_stat.st_uid,
            file_stat.st_gid,
            file_stat.st_nlink,
            stat.S_IMODE(file_stat.st_mode),
            file_stat.st_size,
            file_stat.st_mtime_ns,
            file_stat.st_ctime_ns,
        )

    def _read_native_plan(
        self, path: str, request: LeaderPlanRequest
    ) -> dict[str, object]:
        payload = self._read_secure_payload(path, normalize_mode=True)
        decode_failed = False
        plan: object = None
        try:
            plan = _strict_json_decode(payload)
        except _StrictJsonError:
            decode_failed = True
        if decode_failed or not isinstance(plan, dict):
            raise CliLeaderProviderError(
                "json_parse",
                "invalid_output_envelope",
                retryable=True,
            )
        return self._validate_native_plan(plan, request)

    def _read_secure_payload(self, path: str, *, normalize_mode: bool) -> bytes:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise CliLeaderProviderError("json_parse", "invalid_output_envelope")
        descriptor: int | None = None
        open_failed = False
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | nofollow)
        except OSError:
            open_failed = True
        if open_failed or descriptor is None:
            raise CliLeaderProviderError("json_parse", "invalid_output_envelope")
        pending_error: CliLeaderProviderError | None = None
        payload = b""
        try:
            initial_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(initial_stat.st_mode)
                or initial_stat.st_uid != os.geteuid()
                or initial_stat.st_nlink != 1
            ):
                raise _NativeOutputError(
                    "json_parse", "invalid_output_envelope"
                )
            if initial_stat.st_size > MAX_CLI_LEADER_OUTPUT_BYTES:
                raise _NativeOutputError("oversize")
            named_stat = os.lstat(path)
            if (
                not stat.S_ISREG(named_stat.st_mode)
                or named_stat.st_dev != initial_stat.st_dev
                or named_stat.st_ino != initial_stat.st_ino
            ):
                raise _NativeOutputError(
                    "json_parse", "invalid_output_envelope"
                )
            if normalize_mode:
                os.fchmod(descriptor, 0o600)
            baseline_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(baseline_stat.st_mode)
                or baseline_stat.st_uid != os.geteuid()
                or baseline_stat.st_nlink != 1
                or stat.S_IMODE(baseline_stat.st_mode) != 0o600
                or baseline_stat.st_size > MAX_CLI_LEADER_OUTPUT_BYTES
            ):
                raise _NativeOutputError(
                    "json_parse", "invalid_output_envelope"
                )
            baseline_metadata = self._stable_file_metadata(baseline_stat)
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
                raise _NativeOutputError("oversize")
            final_stat = os.fstat(descriptor)
            final_named_stat = os.lstat(path)
            if (
                self._stable_file_metadata(final_stat) != baseline_metadata
                or final_stat.st_size != len(payload)
                or not stat.S_ISREG(final_named_stat.st_mode)
                or final_named_stat.st_dev != final_stat.st_dev
                or final_named_stat.st_ino != final_stat.st_ino
            ):
                raise _NativeOutputError(
                    "json_parse", "invalid_output_envelope"
                )
        except _NativeOutputError as error:
            pending_error = CliLeaderProviderError(error.stage, error.diagnostic_code)
        except OSError:
            pending_error = CliLeaderProviderError(
                "json_parse", "invalid_output_envelope"
            )
        close_failed = False
        try:
            os.close(descriptor)
        except OSError:
            close_failed = True
        if close_failed and pending_error is None:
            pending_error = CliLeaderProviderError(
                "json_parse", "invalid_output_envelope"
            )
        if pending_error is not None:
            raise pending_error
        return payload

    @staticmethod
    def _validate_native_plan(
        plan: dict[str, object], request: LeaderPlanRequest
    ) -> dict[str, object]:
        if request.semantic_authority is not None:
            return CodexCliProvider._validate_native_semantic_plan(plan, request)
        if set(plan) != _NATIVE_PLAN_FIELDS:
            raise CliLeaderProviderError(
                "json_parse",
                "invalid_output_envelope",
                retryable=True,
            )
        steps = plan.get("steps")
        if isinstance(steps, list) and any(
            isinstance(step, dict) and set(step) != _NATIVE_STEP_FIELDS
            for step in steps
        ):
            raise CliLeaderProviderError(
                "json_parse",
                "invalid_output_envelope",
                retryable=True,
            )
        selected_agent_ids, step_count = leader_plan_authority(request)
        validation_code: str | None = None
        validated_plan: dict[str, object] | None = None
        try:
            validated_plan = validate_provider_plan_schema(
                plan,
                config=request.config,
                selected_agent_ids=selected_agent_ids,
                step_count=step_count,
            )
        except ProviderPlanValidationError as error:
            validation_code = error.code
        if validation_code is not None:
            raise CliLeaderProviderError(
                "schema",
                validation_code,
                retryable=validation_code in _REGENERABLE_SCHEMA_CODES,
            )
        if validated_plan is None:
            raise CliLeaderProviderError("schema")
        return validated_plan

    @staticmethod
    def _validate_native_semantic_plan(
        plan: dict[str, object], request: LeaderPlanRequest
    ) -> dict[str, object]:
        if set(plan) != _NATIVE_PLAN_FIELDS:
            raise CliLeaderProviderError(
                "schema", "semantic_candidate_schema_invalid"
            )
        steps = plan.get("steps")
        if isinstance(steps, list) and any(
            not isinstance(step, dict) or set(step) != _NATIVE_SEMANTIC_STEP_FIELDS
            for step in steps
        ):
            raise CliLeaderProviderError(
                "schema", "semantic_candidate_schema_invalid"
            )
        try:
            selected_agent_ids, roles, step_count = (
                resolve_semantic_leader_plan_context(
                    selected_agent_ids=request.selected_agent_ids,
                    step_count=request.step_count,
                    configured_context=CliLeaderProvider._semantic_config_context(request),
                )
            )
        except (SemanticPlanSchemaAuthorityError, TypeError, ValueError):
            raise CliLeaderProviderError("schema", "authority_invalid") from None
        try:
            return compile_semantic_plan(
                request.semantic_authority,
                plan,
                selected_agent_ids=selected_agent_ids,
                roles=roles,
                step_count=step_count,
            )
        except SemanticPlanningError as error:
            raise CliLeaderProviderError(
                "schema",
                error.code,
                retryable=error.code in SEMANTIC_REGENERABLE_FAILURE_CODES,
            ) from None


class ClaudeCliProvider(CliLeaderProvider):
    name = "claude-cli"
    constraint_mode = "native_json_schema"
    command_name = "claude"
    command = [
        "claude",
        "--print",
        "--output-format",
        "json",
        "--permission-mode",
        "plan",
        "--no-session-persistence",
    ]
    native_help_command = ("claude", "--help")
    native_required_flags = (
        "--json-schema",
        "--output-format",
        "--no-session-persistence",
    )

    def _refined_text(self, stdout: str) -> str:
        """`claude --print --output-format json` 的信封:取 result 纯文本。"""
        try:
            envelope = json.loads(stdout.strip())
        except JSONDecodeError:
            return stdout
        if isinstance(envelope, dict) and isinstance(envelope.get("result"), str):
            return envelope["result"]
        return stdout

    def _brief_json(self, stdout: str) -> object:
        text = stdout.strip()
        try:
            envelope = json.loads(text)
        except JSONDecodeError:
            return super()._brief_json(stdout)
        if isinstance(envelope, dict) and isinstance(envelope.get("result"), str):
            return self._load_json_plan(envelope["result"].strip())
        return envelope

    def _prompt(self, request: LeaderPlanRequest) -> str:
        return super()._prompt(request).replace(
            "Required schema: goal, summary, steps, approval_required, dispatch_ready.",
            "Required schema: goal, summary, steps.\n"
            "Output only those top-level fields; approval_required and dispatch_ready "
            "are normalized locally and must not be output.",
        )

    def plan_result(self, request: LeaderPlanRequest) -> LeaderPlanResult:
        return self._native_plan_result(request)

    def _native_attempt(
        self,
        *,
        request: LeaderPlanRequest,
        schema: dict[str, object],
        prompt: str,
        deadline: float,
    ) -> dict[str, object]:
        schema_argument = json.dumps(
            schema,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        temp_dir = _create_private_workspace("agentdeck-leader-")
        if temp_dir is None:
            raise CliLeaderProviderError(
                "json_parse", "invalid_output_envelope"
            ) from None
        pending_error: CliLeaderProviderError | None = None
        plan: dict[str, object] | None = None
        try:
            output_path = os.path.join(temp_dir, "claude-output.json")
            output = self._create_private_output(output_path)
            process_error: CliLeaderProviderError | None = None
            completed: subprocess.CompletedProcess[str] | None = None
            payload = b""
            try:
                command = [
                    *self._command_for_request(request),
                    "--json-schema",
                    schema_argument,
                ]
                try:
                    completed = subprocess.run(
                        command,
                        input=prompt,
                        text=True,
                        stdout=output,
                        stderr=subprocess.DEVNULL,
                        cwd=request.config.root,
                        timeout=self._remaining_subprocess_timeout(deadline),
                        check=False,
                    )
                except subprocess.TimeoutExpired:
                    process_error = CliLeaderProviderError("timeout")
                except OSError:
                    process_error = CliLeaderProviderError("nonzero")
                if (
                    process_error is None
                    and completed is not None
                    and completed.returncode != 0
                ):
                    # stdout 已写进私有文件:做尽力而为的有界分类(失败即
                    # None,绝不臆测),退出码始终记录。
                    process_error = CliLeaderProviderError(
                        "nonzero",
                        exit_code=completed.returncode,
                        failure_reason=self._classify_private_failure(output),
                    )
                if process_error is None:
                    try:
                        payload = self._capture_private_output(output)
                    except CliLeaderProviderError as error:
                        process_error = error
            finally:
                close_failed = False
                try:
                    output.close()
                except OSError:
                    close_failed = True
                if close_failed and process_error is None:
                    process_error = CliLeaderProviderError(
                        "json_parse", "invalid_output_envelope"
                    )
            if process_error is not None:
                raise process_error
            if completed is None:
                raise CliLeaderProviderError("nonzero")
            plan = self._decode_envelope(payload, request)
        except CliLeaderProviderError as error:
            pending_error = error
        finally:
            cleanup_succeeded = _cleanup_private_workspace(temp_dir)
        if not cleanup_succeeded:
            if pending_error is None:
                pending_error = CliLeaderProviderError(
                    "json_parse", "invalid_output_envelope"
                )
            else:
                pending_error = pending_error.without_retry()
        if pending_error is not None:
            raise pending_error
        if plan is None:
            raise CliLeaderProviderError("schema")
        return plan

    @staticmethod
    def _private_sink_identity(file_stat: os.stat_result) -> tuple[object, ...]:
        return (
            file_stat.st_dev,
            file_stat.st_ino,
            file_stat.st_uid,
            file_stat.st_gid,
            file_stat.st_nlink,
            stat.S_IMODE(file_stat.st_mode),
        )

    @staticmethod
    def _create_private_output(path: str) -> _PrivateOutputSink:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise CliLeaderProviderError("json_parse", "invalid_output_envelope")
        descriptor: int | None = None
        try:
            descriptor = os.open(
                path,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | nofollow,
                0o600,
            )
            file_stat = os.fstat(descriptor)
            named_stat = os.lstat(path)
            if (
                not stat.S_ISREG(file_stat.st_mode)
                or file_stat.st_uid != os.geteuid()
                or file_stat.st_nlink != 1
                or stat.S_IMODE(file_stat.st_mode) != 0o600
                or named_stat.st_dev != file_stat.st_dev
                or named_stat.st_ino != file_stat.st_ino
            ):
                raise OSError
            output = os.fdopen(descriptor, "w+b", buffering=0)
            return _PrivateOutputSink(
                path,
                output,
                ClaudeCliProvider._private_sink_identity(file_stat),
            )
        except OSError:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            raise CliLeaderProviderError(
                "json_parse", "invalid_output_envelope"
            ) from None

    @staticmethod
    def _classify_private_failure(sink: _PrivateOutputSink) -> str | None:
        """失败路径的**尽力而为**诊断:只读私有输出的有界前缀做分类。

        与成功路径的 `_capture_private_output` 严格校验分开——诊断绝不
        影响控制流:任何异常一律返回 None。读到的文本只用于匹配,读后
        即弃,绝不存储、绝不进入错误消息或审计负载(只有闭合枚举码会)。
        """
        try:
            sink.flush()
            os.lseek(sink.fileno(), 0, os.SEEK_SET)
            head = os.read(sink.fileno(), 4096)
        except Exception:  # noqa: BLE001 - diagnosis must never break control flow
            return None
        if not head:
            return None
        # 语义约定:None = 没有可检查的输出;"unknown" = 检查过但认不出。
        return classify_cli_failure(head.decode("utf-8", "replace"), "")

    @staticmethod
    def _capture_private_output(sink: _PrivateOutputSink) -> bytes:
        payload = b""
        try:
            sink.flush()
            os.fsync(sink.fileno())
            baseline_stat = os.fstat(sink.fileno())
            named_stat = os.lstat(sink.path)
            if (
                not stat.S_ISREG(baseline_stat.st_mode)
                or ClaudeCliProvider._private_sink_identity(baseline_stat)
                != sink.creation_identity
                or named_stat.st_dev != baseline_stat.st_dev
                or named_stat.st_ino != baseline_stat.st_ino
            ):
                raise _NativeOutputError(
                    "json_parse", "invalid_output_envelope"
                )
            if baseline_stat.st_size > MAX_CLI_LEADER_OUTPUT_BYTES:
                raise _NativeOutputError("oversize")
            baseline_metadata = CodexCliProvider._stable_file_metadata(baseline_stat)
            os.lseek(sink.fileno(), 0, os.SEEK_SET)
            chunks: list[bytes] = []
            remaining = MAX_CLI_LEADER_OUTPUT_BYTES + 1
            while remaining:
                chunk = os.read(sink.fileno(), min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) > MAX_CLI_LEADER_OUTPUT_BYTES:
                raise _NativeOutputError("oversize")
            final_stat = os.fstat(sink.fileno())
            final_named_stat = os.lstat(sink.path)
            if (
                ClaudeCliProvider._private_sink_identity(final_stat)
                != sink.creation_identity
                or CodexCliProvider._stable_file_metadata(final_stat)
                != baseline_metadata
                or final_stat.st_size != len(payload)
                or final_named_stat.st_dev != final_stat.st_dev
                or final_named_stat.st_ino != final_stat.st_ino
            ):
                raise _NativeOutputError(
                    "json_parse", "invalid_output_envelope"
                )
        except _NativeOutputError as error:
            raise CliLeaderProviderError(error.stage, error.diagnostic_code) from None
        except OSError:
            raise CliLeaderProviderError(
                "json_parse", "invalid_output_envelope"
            ) from None
        return payload

    @staticmethod
    def _decode_envelope(
        payload: bytes, request: LeaderPlanRequest
    ) -> dict[str, object]:
        envelope: object = None
        try:
            envelope = _strict_json_decode(payload)
        except _StrictJsonError:
            pass
        if (
            not isinstance(envelope, dict)
            or type(envelope.get("type")) is not str
            or envelope.get("type") != "result"
            or type(envelope.get("subtype")) is not str
            or envelope.get("subtype") != "success"
            or envelope.get("is_error") is not False
            or not isinstance(envelope.get("structured_output"), dict)
        ):
            raise CliLeaderProviderError(
                "json_parse",
                "invalid_output_envelope",
                retryable=True,
            )
        plan = envelope["structured_output"]
        assert isinstance(plan, dict)
        return CodexCliProvider._validate_native_plan(plan, request)


_CLI_NATIVE_SCHEMA_UNSUPPORTED = "Leader CLI native JSON schema is unsupported"
_CLI_EXECUTABLE_UNAVAILABLE = "Leader CLI executable is not available"
_CLI_NATIVE_SCHEMA_UNAVAILABLE = (
    "Leader CLI native JSON schema capability is unavailable"
)


def cli_native_schema_ready(provider: str) -> tuple[bool, str | None]:
    probe = {
        "codex-cli": (
            CodexCliProvider.native_help_command,
            CodexCliProvider.native_required_flags,
        ),
        "claude-cli": (
            ClaudeCliProvider.native_help_command,
            ClaudeCliProvider.native_required_flags,
        ),
    }.get(provider)
    if probe is None:
        return False, _CLI_NATIVE_SCHEMA_UNSUPPORTED
    command, required_flags = probe
    temp_dir = _create_private_workspace("agentdeck-leader-probe-")
    if temp_dir is None:
        return False, _CLI_NATIVE_SCHEMA_UNAVAILABLE
    result: tuple[bool, str | None] | None = None
    try:
        try:
            output_path = os.path.join(temp_dir, "help-output.txt")
            output = ClaudeCliProvider._create_private_output(output_path)
        except CliLeaderProviderError:
            result = (False, _CLI_NATIVE_SCHEMA_UNAVAILABLE)
            output = None
        process_failed = False
        executable_missing = False
        completed: subprocess.CompletedProcess[bytes] | None = None
        payload = b""
        if output is not None:
            try:
                try:
                    completed = subprocess.run(
                        list(command),
                        stdout=output,
                        stderr=output,
                        timeout=5,
                        check=False,
                    )
                except FileNotFoundError:
                    executable_missing = True
                except (subprocess.TimeoutExpired, OSError):
                    process_failed = True
                if not executable_missing and not process_failed:
                    try:
                        if completed is not None and completed.returncode == 0:
                            payload = ClaudeCliProvider._capture_private_output(output)
                    except CliLeaderProviderError:
                        process_failed = True
            finally:
                try:
                    output.close()
                except OSError:
                    process_failed = True
            if executable_missing:
                result = (False, _CLI_EXECUTABLE_UNAVAILABLE)
            elif process_failed or completed is None or completed.returncode != 0:
                result = (False, _CLI_NATIVE_SCHEMA_UNAVAILABLE)
            else:
                try:
                    help_text = payload.decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    result = (False, _CLI_NATIVE_SCHEMA_UNAVAILABLE)
                else:
                    option_names = set(
                        re.findall(
                            r"(?<![A-Za-z0-9_-])--[A-Za-z0-9][A-Za-z0-9-]*"
                            r"(?![A-Za-z0-9_-])",
                            help_text,
                        )
                    )
                    if all(flag in option_names for flag in required_flags):
                        result = (True, None)
                    else:
                        result = (False, _CLI_NATIVE_SCHEMA_UNAVAILABLE)
    finally:
        cleanup_succeeded = _cleanup_private_workspace(temp_dir)
    if not cleanup_succeeded and (result is None or result[0]):
        return False, _CLI_NATIVE_SCHEMA_UNAVAILABLE
    if result is None:
        return False, _CLI_NATIVE_SCHEMA_UNAVAILABLE
    return result
