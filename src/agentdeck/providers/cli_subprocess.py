from __future__ import annotations

import json
import math
import os
import re
import shutil
import stat
import subprocess
import tempfile
from typing import BinaryIO
from json import JSONDecodeError

from .base import (
    LeaderPlanRequest,
    LeaderPlanResult,
    leader_skill_context_prompt_lines,
    validate_provider_plan_schema,
)
from .plan_schema import (
    LEADER_PLAN_DIAGNOSTIC_CODES,
    ProviderPlanValidationError,
    build_leader_generation_provenance,
    build_leader_plan_schema,
    leader_plan_authority,
)


CLI_LEADER_FAILURE_STAGES = frozenset(
    {"timeout", "nonzero", "json_parse", "schema", "cancelled", "oversize"}
)
MAX_CLI_LEADER_OUTPUT_BYTES = 2 * 1024 * 1024
_NATIVE_PLAN_FIELDS = frozenset({"goal", "summary", "steps"})
_NATIVE_STEP_FIELDS = frozenset(
    {"step", "agent_id", "role", "task", "risk", "requires_approval"}
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

    def __init__(self, stage: str, diagnostic_code: str | None = None) -> None:
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
        self.stage = stage
        self.diagnostic_code = diagnostic_code
        super().__init__(f"CLI Leader planning failed at stage: {stage}")


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
        schema = build_leader_plan_schema(request)
        prompt = self._prompt(request)
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
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    cwd=request.config.root,
                    timeout=(
                        request.timeout_seconds
                        if request.timeout_seconds is not None
                        else self.timeout
                    ),
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
                raise CliLeaderProviderError("nonzero")
            plan = self._read_native_plan(result_path, request)
        except CliLeaderProviderError as error:
            pending_error = error
        finally:
            cleanup_succeeded = _cleanup_private_workspace(temp_dir)
        if not cleanup_succeeded and pending_error is None:
            pending_error = CliLeaderProviderError(
                "json_parse", "invalid_output_envelope"
            )
        if pending_error is not None:
            raise pending_error
        if plan is None:
            raise CliLeaderProviderError("schema")
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
            raise CliLeaderProviderError("json_parse", "invalid_output_envelope")
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
        if set(plan) != _NATIVE_PLAN_FIELDS:
            raise CliLeaderProviderError("json_parse", "invalid_output_envelope")
        steps = plan.get("steps")
        if isinstance(steps, list) and any(
            isinstance(step, dict) and set(step) != _NATIVE_STEP_FIELDS
            for step in steps
        ):
            raise CliLeaderProviderError("json_parse", "invalid_output_envelope")
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
            raise CliLeaderProviderError("schema", validation_code)
        if validated_plan is None:
            raise CliLeaderProviderError("schema")
        return validated_plan


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

    def _prompt(self, request: LeaderPlanRequest) -> str:
        return super()._prompt(request).replace(
            "Required schema: goal, summary, steps, approval_required, dispatch_ready.",
            "Required schema: goal, summary, steps.\n"
            "Output only those top-level fields; approval_required and dispatch_ready "
            "are normalized locally and must not be output.",
        )

    def plan_result(self, request: LeaderPlanRequest) -> LeaderPlanResult:
        schema = build_leader_plan_schema(request)
        schema_argument = json.dumps(
            schema,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        prompt = self._prompt(request)
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
                        timeout=(
                            request.timeout_seconds
                            if request.timeout_seconds is not None
                            else self.timeout
                        ),
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
                    process_error = CliLeaderProviderError("nonzero")
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
        if not cleanup_succeeded and pending_error is None:
            pending_error = CliLeaderProviderError(
                "json_parse", "invalid_output_envelope"
            )
        if pending_error is not None:
            raise pending_error
        if plan is None:
            raise CliLeaderProviderError("schema")
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
            raise CliLeaderProviderError("json_parse", "invalid_output_envelope")
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
