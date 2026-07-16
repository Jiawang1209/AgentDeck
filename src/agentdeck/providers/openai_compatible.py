from __future__ import annotations

from dataclasses import replace
import json
import math
import os
import time
from json import JSONDecodeError
from urllib import request
from urllib.error import URLError

from agentdeck.models import AgentSpec
from agentdeck.semantic_authority import (
    SemanticAuthorityError,
    semantic_text_contains_sensitive_value,
    validate_semantic_authority,
)
from agentdeck.semantic_planning import (
    SEMANTIC_FAILURE_CODES,
    SEMANTIC_REGENERABLE_FAILURE_CODES,
    SemanticPlanningError,
    compile_semantic_plan,
    semantic_regeneration_guidance,
)

from .base import (
    LeaderPlanRequest,
    LeaderPlanResult,
    leader_skill_context_prompt_lines,
    validate_provider_plan_schema,
)
from .plan_schema import build_leader_generation_provenance
from .semantic_plan_schema import (
    SemanticPlanSchemaAuthorityError,
    resolve_semantic_leader_plan_context,
    semantic_authority_identity,
)


MAX_API_LEADER_OUTPUT_BYTES = 2 * 1024 * 1024
_API_READ_CHUNK_BYTES = 64 * 1024


class OpenAICompatibleProviderError(RuntimeError):
    """Sanitized bounded planning failure for API-backed Leaders."""

    _STAGES = frozenset({"timeout", "nonzero", "json_parse", "schema"})
    _PREFLIGHT_CODES = frozenset(
        {
            "semantic_compilation_failed",
            "semantic_authority_unresolved",
            "semantic_authority_sensitive_value",
        }
    )
    _FROZEN_FIELDS = frozenset(
        {"stage", "diagnostic_code", "attempt_count", "args", "_metadata_frozen"}
    )

    def __init__(
        self,
        stage: str,
        diagnostic_code: str | None = None,
        *,
        attempt_count: int = 0,
    ) -> None:
        if type(stage) is not str or stage not in self._STAGES:
            raise ValueError("invalid API Leader failure stage")
        if type(attempt_count) is not int or attempt_count < 0 or attempt_count > 2:
            raise ValueError("invalid API Leader failure attempt count")
        if stage in {"timeout", "nonzero"}:
            if diagnostic_code is not None:
                raise ValueError("invalid API Leader failure diagnostic")
        elif (
            type(diagnostic_code) is not str
            or diagnostic_code not in SEMANTIC_FAILURE_CODES
        ):
            raise ValueError("invalid API Leader failure diagnostic")
        if attempt_count == 0:
            if stage == "schema" and diagnostic_code in self._PREFLIGHT_CODES:
                pass
            elif stage != "timeout":
                raise ValueError("invalid API Leader failure attempt count")
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "diagnostic_code", diagnostic_code)
        object.__setattr__(self, "attempt_count", attempt_count)
        super().__init__(f"OpenAI-compatible Leader planning failed at stage: {stage}")
        object.__setattr__(self, "_metadata_frozen", True)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_metadata_frozen", False) and name in self._FROZEN_FIELDS:
            raise AttributeError("API Leader failure metadata is immutable")
        super().__setattr__(name, value)


class OpenAICompatibleProvider:
    name = "openai-compatible"
    constraint_mode = "json_object"

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
        if plan_request.semantic_authority is not None:
            return self.plan_result(plan_request).plan
        return self._legacy_plan(plan_request)

    def plan_result(self, plan_request: LeaderPlanRequest) -> LeaderPlanResult:
        if plan_request.semantic_authority is None:
            return LeaderPlanResult(
                plan=self._legacy_plan(plan_request),
                leader_generation=build_leader_generation_provenance(
                    request=plan_request,
                    provider=self.name,
                    constraint_mode=self.constraint_mode,
                ),
            )
        return self._semantic_plan_result(plan_request)

    def _legacy_plan(self, plan_request: LeaderPlanRequest) -> dict[str, object]:
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

    @staticmethod
    def _positive_timeout(value: object) -> float:
        if type(value) not in {int, float}:
            raise ValueError("API Leader planning timeout must be a positive number")
        try:
            timeout = float(value)
        except (OverflowError, ValueError):
            raise ValueError("API Leader planning timeout must be a positive number") from None
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("API Leader planning timeout must be a positive number")
        return timeout

    @staticmethod
    def _contains_sensitive_authority(value: object) -> bool:
        if type(value) is dict:
            if value.get("sensitivity") == "secret_ref":
                return True
            return any(
                OpenAICompatibleProvider._contains_sensitive_authority(item)
                for item in value.values()
            )
        if type(value) is list:
            return any(
                OpenAICompatibleProvider._contains_sensitive_authority(item)
                for item in value
            )
        return semantic_text_contains_sensitive_value(value)

    @staticmethod
    def _semantic_context(
        plan_request: LeaderPlanRequest,
    ) -> tuple[tuple[str, ...], dict[str, str], int]:
        return resolve_semantic_leader_plan_context(
            selected_agent_ids=plan_request.selected_agent_ids,
            step_count=plan_request.step_count,
            configured_context=tuple(
                agent
                for agent in plan_request.config.agents
                if type(agent) is AgentSpec
            ),
        )

    def _semantic_plan_result(
        self, plan_request: LeaderPlanRequest
    ) -> LeaderPlanResult:
        try:
            authority = validate_semantic_authority(plan_request.semantic_authority)
        except (SemanticAuthorityError, TypeError, ValueError):
            raise OpenAICompatibleProviderError(
                "schema", "semantic_compilation_failed", attempt_count=0
            ) from None
        if authority["unresolved"]:
            raise OpenAICompatibleProviderError(
                "schema", "semantic_authority_unresolved", attempt_count=0
            )
        if self._contains_sensitive_authority(authority):
            raise OpenAICompatibleProviderError(
                "schema", "semantic_authority_sensitive_value", attempt_count=0
            )
        try:
            selected, roles, count = self._semantic_context(plan_request)
        except (SemanticPlanSchemaAuthorityError, TypeError, ValueError):
            raise OpenAICompatibleProviderError(
                "schema", "semantic_compilation_failed", attempt_count=0
            ) from None
        effective_model = plan_request.model or self.model
        provider_name = self.name
        constraint_mode = self.constraint_mode
        base_url = self.base_url
        if (
            type(effective_model) is not str
            or not effective_model.strip()
            or type(provider_name) is not str
            or not provider_name.strip()
            or constraint_mode != "json_object"
            or type(base_url) is not str
            or not base_url.strip()
        ):
            raise OpenAICompatibleProviderError(
                "schema", "semantic_compilation_failed", attempt_count=0
            )
        frozen_request = replace(
            plan_request,
            model=effective_model,
            semantic_authority=authority,
        )
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(f"{self.api_key_env} is not set")
        provider_timeout = self._positive_timeout(self.timeout)
        request_timeout = self._positive_timeout(
            self.timeout
            if frozen_request.timeout_seconds is None
            else frozen_request.timeout_seconds
        )
        now = time.monotonic()
        budget = min(provider_timeout, request_timeout)
        deadline = now + budget
        if not math.isfinite(now) or not math.isfinite(deadline):
            raise ValueError("API Leader planning timeout must be a positive number")

        diagnostics: list[dict[str, object]] = []
        attempt_request = frozen_request
        for attempt in (1, 2):
            attempt_now = time.monotonic()
            remaining = deadline - attempt_now
            if (
                not math.isfinite(attempt_now)
                or not math.isfinite(remaining)
                or remaining <= 0
            ):
                raise OpenAICompatibleProviderError(
                    "timeout", attempt_count=attempt - 1
                )
            try:
                candidate = self._semantic_http_attempt(
                    attempt_request,
                    api_key=api_key,
                    timeout=remaining,
                    effective_model=effective_model,
                    base_url=base_url,
                    deadline=deadline,
                )
            except OpenAICompatibleProviderError as error:
                raise OpenAICompatibleProviderError(
                    error.stage,
                    error.diagnostic_code,
                    attempt_count=attempt,
                ) from None
            completed_at = time.monotonic()
            if not math.isfinite(completed_at) or completed_at >= deadline:
                raise OpenAICompatibleProviderError(
                    "timeout", attempt_count=attempt
                )
            try:
                plan = compile_semantic_plan(
                    authority,
                    candidate,
                    selected_agent_ids=selected,
                    roles=roles,
                    step_count=count,
                )
            except SemanticPlanningError as error:
                if error.code not in SEMANTIC_REGENERABLE_FAILURE_CODES or attempt == 2:
                    raise OpenAICompatibleProviderError(
                        "schema", error.code, attempt_count=attempt
                    ) from None
                diagnostics.append(
                    {
                        "code": error.code,
                        "attempt_count": attempt,
                        "regeneration_used": False,
                    }
                )
                attempt_request = replace(
                    frozen_request, regeneration_diagnostic=error.code
                )
                continue
            return LeaderPlanResult(
                plan=plan,
                leader_generation=build_leader_generation_provenance(
                    request=frozen_request,
                    provider=provider_name,
                    constraint_mode=constraint_mode,
                    attempt_count=attempt,
                ),
                semantic_diagnostics=tuple(diagnostics),
            )
        raise OpenAICompatibleProviderError("timeout", attempt_count=2)

    def _semantic_http_attempt(
        self,
        plan_request: LeaderPlanRequest,
        *,
        api_key: str,
        timeout: float,
        effective_model: str,
        base_url: str,
        deadline: float,
    ) -> dict[str, object]:
        payload = {
            "model": effective_model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": self._system_prompt(plan_request)},
                {
                    "role": "user",
                    "content": "Generate the complete semantic candidate from the frozen authority.",
                },
            ],
        }
        req = request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=timeout) as response:
                payload_bytes = self._read_semantic_response(
                    response, deadline=deadline
                )
                data = json.loads(payload_bytes.decode("utf-8"))
            return self._extract_plan(data)
        except OpenAICompatibleProviderError:
            raise
        except TimeoutError:
            raise OpenAICompatibleProviderError("timeout", attempt_count=1) from None
        except URLError as error:
            reason = error.reason if type(error) is URLError else None
            stage = "timeout" if isinstance(reason, TimeoutError) else "nonzero"
            raise OpenAICompatibleProviderError(stage, attempt_count=1) from None
        except OSError:
            raise OpenAICompatibleProviderError("nonzero", attempt_count=1) from None
        except (
            AttributeError,
            JSONDecodeError,
            UnicodeError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            raise OpenAICompatibleProviderError(
                "json_parse",
                "semantic_candidate_schema_invalid",
                attempt_count=1,
            ) from None

    @staticmethod
    def _read_semantic_response(response: object, *, deadline: float) -> bytes:
        """Read one bounded receive at a time under the total deadline.

        Real HTTP responses expose ``read1`` and the underlying socket; both are
        required so every receive gets the current remaining timeout. Minimal
        test doubles may expose only ``read`` and use the post-read deadline
        check, but that fallback is not treated as a production interruption
        guarantee.
        """
        try:
            read1 = getattr(response, "read1", None)
            if callable(read1):
                fp = getattr(response, "fp", None)
                raw = getattr(fp, "raw", None)
                sock = getattr(raw, "_sock", None)
                set_socket_timeout = getattr(sock, "settimeout", None)
                if not callable(set_socket_timeout):
                    raise OpenAICompatibleProviderError(
                        "json_parse",
                        "semantic_candidate_schema_invalid",
                        attempt_count=1,
                    )
                reader = read1
            else:
                reader = getattr(response, "read")
                set_socket_timeout = None
                if not callable(reader):
                    raise OpenAICompatibleProviderError(
                        "json_parse",
                        "semantic_candidate_schema_invalid",
                        attempt_count=1,
                    )
        except OpenAICompatibleProviderError:
            raise
        except Exception:
            raise OpenAICompatibleProviderError(
                "json_parse",
                "semantic_candidate_schema_invalid",
                attempt_count=1,
            ) from None

        chunks: list[bytes] = []
        total = 0
        while True:
            before = time.monotonic()
            if not math.isfinite(before) or before >= deadline:
                raise OpenAICompatibleProviderError(
                    "timeout", attempt_count=1
                )
            read_size = min(
                _API_READ_CHUNK_BYTES,
                MAX_API_LEADER_OUTPUT_BYTES + 1 - total,
            )
            if read_size <= 0:
                raise OpenAICompatibleProviderError(
                    "json_parse",
                    "semantic_candidate_schema_invalid",
                    attempt_count=1,
                )
            if set_socket_timeout is not None:
                remaining = deadline - before
                try:
                    set_socket_timeout(remaining)
                except OSError:
                    raise OpenAICompatibleProviderError(
                        "nonzero", attempt_count=1
                    ) from None
            try:
                chunk = reader(read_size)
            except TimeoutError:
                raise OpenAICompatibleProviderError(
                    "timeout", attempt_count=1
                ) from None
            after = time.monotonic()
            if not math.isfinite(after) or after >= deadline:
                raise OpenAICompatibleProviderError(
                    "timeout", attempt_count=1
                )
            if type(chunk) is not bytes:
                raise OpenAICompatibleProviderError(
                    "json_parse",
                    "semantic_candidate_schema_invalid",
                    attempt_count=1,
                )
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > MAX_API_LEADER_OUTPUT_BYTES:
                raise OpenAICompatibleProviderError(
                    "json_parse",
                    "semantic_candidate_schema_invalid",
                    attempt_count=1,
                )
            chunks.append(chunk)

    def _system_prompt(self, request: LeaderPlanRequest) -> str:
        if request.semantic_authority is not None:
            return self._semantic_system_prompt(request)
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
            "Return only a JSON object plan. Do not dispatch work.",
            "Every step must require human approval before dispatch.",
            "Required schema: goal, summary, steps, approval_required, dispatch_ready.",
            "Each step must include: step, agent_id, role, task, risk, requires_approval.",
            "Step numbers must be 1..n without duplicates or gaps.",
            "Use only listed worker agent_id values and copy each worker role exactly.",
            f"Available workers: {json.dumps(workers, ensure_ascii=False)}",
        ]
        lines.extend(leader_skill_context_prompt_lines(request.skill_context))
        return "\n".join(lines)

    @staticmethod
    def _semantic_system_prompt(request: LeaderPlanRequest) -> str:
        try:
            schema_version, authority_hash = semantic_authority_identity(
                request.semantic_authority
            )
            selected, roles, step_count = OpenAICompatibleProvider._semantic_context(
                request
            )
            authority = validate_semantic_authority(request.semantic_authority)
        except (
            SemanticAuthorityError,
            SemanticPlanSchemaAuthorityError,
            TypeError,
            ValueError,
        ):
            raise OpenAICompatibleProviderError(
                "schema", "semantic_compilation_failed", attempt_count=0
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
        workers = [
            {"agent_id": agent_id, "role": roles[agent_id]}
            for agent_id in selected
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
            "Every step risk must be low and requires_approval must be true.",
            f"Exact step count: {step_count}",
            f"Selected workers: {json.dumps(workers, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}",
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
