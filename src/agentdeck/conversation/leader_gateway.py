from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
import shutil
from typing import Callable

from ..mission_orchestration import LeaderMissionCandidate
from ..models import LeaderConfig, ProjectConfig
from ..orchestration.leader import LeaderOrchestrator
from ..providers import LeaderProvider, leader_provider
from ..providers.cli_subprocess import CliLeaderProviderError
from ..runtime.acp import AcpTransport
from ..runtime.acp_client import AgentDeckAcpClient, PermissionDecision
from ..runtime.acp_mapping import ensure_turn_within_bounds


MAX_LEADER_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_LEADER_FRAGMENTS = 256


LEADER_FAILURE_STAGES = frozenset(
    {
        "cancelled",
        "backend_blocked",
        "backend_failure",
        "timeout",
        "nonzero",
        "json_parse",
        "schema",
        "oversize",
        "acp_incomplete",
        "acp_permission",
        "acp_empty",
        "acp_failure",
    }
)


class LeaderGatewayError(RuntimeError):
    """A fixed-stage Leader failure safe for durable diagnostics."""

    def __init__(self, stage: str) -> None:
        legacy = {
            "Leader request cancelled": "cancelled",
            "Leader backend failed": "backend_failure",
            "ACP Leader backend failed": "acp_failure",
        }
        normalized = legacy.get(stage, stage)
        if normalized not in LEADER_FAILURE_STAGES:
            normalized = "backend_failure"
        self.stage = normalized
        messages = {
            "cancelled": "Leader request cancelled",
            "backend_blocked": "Leader backend blocked",
            "backend_failure": "Leader backend failed",
            "acp_failure": "ACP Leader backend failed",
        }
        super().__init__(
            messages.get(normalized, f"Leader planning failed at stage: {normalized}")
        )


class CancellationToken:
    def __init__(self) -> None:
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True


@dataclass(frozen=True)
class LeaderRequest:
    config: ProjectConfig
    user_message: str
    planning_task: str
    timeout_seconds: int
    skill_context: dict[str, object] | None
    selected_agent_ids: tuple[str, ...] | None = None
    step_count: int | None = None


@dataclass(frozen=True)
class LeaderBackendStatus:
    backend_kind: str
    provider: str
    model: str
    transport: str
    readiness: str
    capabilities: tuple[str, ...]
    blockers: tuple[str, ...]
    fallback: dict[str, object]


class _LeaderAcpSink:
    def __init__(self) -> None:
        self.fragments: list[str] = []
        self.payload_bytes = 0
        self.permission_seen = False

    async def append_update(
        self, _session_id: str, kind: str, payload: dict[str, object]
    ) -> object:
        if kind != "text" or payload.get("role") != "agent":
            raise ValueError("ACP Leader emitted unsupported update")
        content = payload.get("content")
        text = content.get("text") if isinstance(content, dict) else None
        if not isinstance(text, str):
            raise ValueError("ACP Leader emitted invalid text update")
        prospective_bytes = self.payload_bytes + len(text.encode("utf-8"))
        prospective_count = len(self.fragments) + 1
        ensure_turn_within_bounds(prospective_bytes, prospective_count)
        self.fragments.append(text)
        self.payload_bytes = prospective_bytes
        return {"kind": kind}

    async def append_permission(self, *_args: object, **_kwargs: object) -> object:
        self.permission_seen = True
        return {"status": "denied"}

    async def append_permission_decision(self, *_args: object, **_kwargs: object) -> None:
        return None


def _derived_backend(leader: LeaderConfig) -> tuple[str, str]:
    if leader.backend_kind is not None and leader.transport is not None:
        return leader.backend_kind, leader.transport
    if leader.provider in {"codex-cli", "claude-cli"}:
        return "agent_cli", "cli_subprocess"
    if leader.provider == "fake":
        return "api", "local"
    return "api", "http"


class LeaderGateway:
    def __init__(
        self,
        *,
        provider_factory: Callable[[str], LeaderProvider] = leader_provider,
        which: Callable[[str], str | None] = shutil.which,
        request_timeout: float = 30.0,
    ) -> None:
        if type(request_timeout) not in {int, float} or request_timeout <= 0:
            raise ValueError("request_timeout must be positive")
        self._provider_factory = provider_factory
        self._which = which
        self._request_timeout = float(request_timeout)

    async def _generate_acp_plan(self, request: LeaderRequest) -> dict[str, object]:
        sink = _LeaderAcpSink()
        client = AgentDeckAcpClient(
            sink=sink,
            decide=lambda *_: PermissionDecision.cancelled("leader_planning_denied"),
        )
        transport = AcpTransport(
            request.config.leader.transport_command,
            request.config.root,
            client,
            request_timeout=self._request_timeout,
        )
        try:
            await transport.initialize()
            session = await transport.new_session()
            result = await transport.prompt(
                session.native_session_id, request.planning_task
            )
            if result.stop_reason != "end_turn" or result.outcome != "completed":
                raise LeaderGatewayError("acp_incomplete")
            if sink.permission_seen:
                raise LeaderGatewayError("acp_permission")
            if not sink.fragments:
                raise LeaderGatewayError("acp_empty")
            text = "".join(sink.fragments)
            try:
                plan = json.loads(text)
            except json.JSONDecodeError:
                raise LeaderGatewayError("json_parse") from None
            if not isinstance(plan, dict):
                raise LeaderGatewayError("schema")
            return plan
        finally:
            await transport.close()

    def describe(self, leader: LeaderConfig) -> LeaderBackendStatus:
        backend_kind, transport = _derived_backend(leader)
        blockers: list[str] = []
        capabilities: tuple[str, ...]
        if (backend_kind, transport) not in {
            ("api", "http"),
            ("api", "local"),
            ("agent_cli", "cli_subprocess"),
            ("agent_cli", "acp"),
        }:
            blockers.append("invalid Leader backend configuration")
            capabilities = ()
        elif transport == "acp":
            capabilities = ("initialize", "new_session", "prompt", "cancel")
            if not leader.transport_command:
                blockers.append("ACP Leader requires transport_command")
            elif self._which(leader.transport_command[0]) is None:
                blockers.append("ACP Leader executable is not available")
        elif transport == "cli_subprocess":
            capabilities = ("plan",)
            executable = {"codex-cli": "codex", "claude-cli": "claude"}.get(
                leader.provider
            )
            if executable is None or self._which(executable) is None:
                blockers.append("Leader CLI executable is not available")
        else:
            capabilities = ("plan",)
        return LeaderBackendStatus(
            backend_kind=backend_kind,
            provider=leader.provider,
            model=leader.model,
            transport=transport,
            readiness="blocked" if blockers else "ready",
            capabilities=capabilities,
            blockers=tuple(blockers),
            fallback={"automatic": False, "transport": None},
        )

    def generate_mission(
        self, request: LeaderRequest, cancel: CancellationToken
    ) -> LeaderMissionCandidate:
        if cancel.cancelled:
            raise LeaderGatewayError("cancelled")
        status = self.describe(request.config.leader)
        if status.readiness != "ready":
            raise LeaderGatewayError("backend_blocked")
        if status.transport == "acp":
            try:
                plan = asyncio.run(self._generate_acp_plan(request))
            except LeaderGatewayError:
                raise
            except Exception:
                raise LeaderGatewayError("acp_failure") from None
        else:
            try:
                provider = self._provider_factory(status.provider)
                result = LeaderOrchestrator(request.config, provider).plan_result(
                    request.planning_task,
                    request.config.leader.model,
                    skill_context=request.skill_context,
                    selected_agent_ids=request.selected_agent_ids,
                    step_count=request.step_count,
                    timeout_seconds=request.timeout_seconds,
                )
                plan = result.plan
            except CliLeaderProviderError as error:
                raise LeaderGatewayError(error.stage) from None
            except Exception:
                raise LeaderGatewayError("backend_failure") from None
        if cancel.cancelled:
            raise LeaderGatewayError("cancelled")
        try:
            encoded = json.dumps(plan, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError):
            raise LeaderGatewayError("schema") from None
        if len(encoded) > MAX_LEADER_OUTPUT_BYTES:
            raise LeaderGatewayError("oversize")
        return LeaderMissionCandidate(
            provider=status.provider,
            model=status.model,
            user_message=request.user_message,
            plan=plan,
            timeout_seconds=request.timeout_seconds,
            selected_agent_ids=request.selected_agent_ids,
            step_count=request.step_count,
        )
