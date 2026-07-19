"""Synchronous Codex/Claude Leader projection over structured ACP updates."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
import json
from math import isfinite
import threading
from typing import Final, TypeVar

from agentdeck.adapters.acp_transport import ACPStdioTransport
from agentdeck.ports.leader import (
    LeaderFailure,
    LeaderFailureCode,
    LeaderProposal,
    LeaderRequest,
    ProposalError,
    leader_proposal_json_schema,
)
from agentdeck.ports.transport import (
    TransportFailure,
    TransportFailureCode,
    TransportPermissionDecision,
    TransportPort,
    TransportPromptPart,
    TransportUpdateKind,
)


_ADAPTER_ID: Final = "acp"
_PROPOSAL_URI: Final = "agentdeck://leader/mission-proposal"
_PROPOSAL_MIME: Final = "application/vnd.agentdeck.mission+json"
_REQUEST_URI: Final = "agentdeck://leader/mission-request"
_REQUEST_MIME: Final = "application/vnd.agentdeck.request+json"
_MAX_REQUEST_BYTES: Final = 1024 * 1024
_MAX_RESPONSE_BYTES: Final = 8 * 1024 * 1024
_MAX_IDENTITY_BYTES: Final = 4096
_BRIDGE_THREAD_GRACE_SECONDS: Final = 0.1
_T = TypeVar("_T")
TransportFactory = Callable[..., TransportPort]


def _identity(value: object, field: str) -> str:
    if type(value) is not str:
        raise ValueError(f"ACP Leader requires an exact {field}")
    failed = False
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        failed = True
        encoded = b""
    if (
        failed
        or not value.strip()
        or len(encoded) > _MAX_IDENTITY_BYTES
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"ACP Leader requires an exact {field}") from None
    return value


def _command(value: object) -> tuple[str, ...]:
    if type(value) not in {tuple, list} or not value:
        raise ValueError("ACP Leader requires a bounded argv command")
    copied = tuple(value)
    if any(type(item) is not str or not item or "\x00" in item for item in copied):
        raise ValueError("ACP Leader requires a bounded argv command")
    try:
        if sum(len(item.encode("utf-8", "strict")) for item in copied) > 64 * 1024:
            raise ValueError
    except (UnicodeEncodeError, ValueError):
        raise ValueError("ACP Leader requires a bounded argv command") from None
    return copied


def _response_bound(value: object) -> int:
    if type(value) is not int or not 1024 <= value <= _MAX_RESPONSE_BYTES:
        raise ValueError("ACP Leader requires a positive response bound")
    return value


def _timeout(value: object) -> float:
    if type(value) not in {int, float}:
        raise ValueError("ACP Leader requires a positive timeout")
    checked = float(value)
    if not isfinite(checked) or not 0 < checked <= 120:
        raise ValueError("ACP Leader requires a positive timeout")
    return checked


class ACPLeader:
    """Exact CLI identity using ACP resources, never terminal-text scraping."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        backend_id: str,
        model: str,
        version: str,
        max_bytes: int = 1024 * 1024,
        timeout_seconds: float = 30.0,
        transport_factory: TransportFactory = ACPStdioTransport,
    ) -> None:
        if not callable(transport_factory):
            raise TypeError("transport_factory must be callable")
        self.command = _command(command)
        self.backend_id = _identity(backend_id, "backend")
        self.model = _identity(model, "model")
        self.version = _identity(version, "version")
        self.max_bytes = _response_bound(max_bytes)
        self.timeout_seconds = _timeout(timeout_seconds)
        self._transport_factory = transport_factory

    def propose_mission(self, request: LeaderRequest) -> LeaderProposal:
        if type(request) is not LeaderRequest:
            raise TypeError("request must be a LeaderRequest")
        resolved = request.resolved_model
        expected = (self.backend_id, _ADAPTER_ID, self.model, self.version)
        actual = (
            resolved.backend_id,
            resolved.adapter_id,
            resolved.model_id,
            resolved.version,
        )
        if actual != expected:
            raise ValueError("request does not match the frozen resolved Leader identity")
        return _run_sync(lambda: self._propose(request), self.timeout_seconds)

    async def _propose(self, request: LeaderRequest) -> LeaderProposal:
        factory_failed = False
        try:
            transport = self._transport_factory(
                self.command,
                project_root=request.project_context.project_root,
                max_bytes=self.max_bytes,
                timeout_seconds=self.timeout_seconds,
            )
        except Exception:
            factory_failed = True
            transport = None
        if factory_failed or transport is None:
            raise TransportFailure(TransportFailureCode.INITIALIZATION_FAILED)
        request_part = self._request_part(request)
        artifacts: list[str] = []
        unexpected_side_effect = False
        async with transport:  # type: ignore[attr-defined]
            capabilities = await transport.initialize()
            if not capabilities.embedded_context:
                raise TransportFailure(TransportFailureCode.CAPABILITY_MISSING)
            session = await transport.new_session()
            prompt_task = asyncio.create_task(transport.prompt(session, (
                TransportPromptPart.text(
                    "Propose one AgentDeck Mission. Return the proposal only as the "
                    "declared structured ACP resource; do not execute tools or work."
                ),
                request_part,
            )))
            async for update in transport.stream_updates(session):
                if update.kind is TransportUpdateKind.ARTIFACT:
                    artifact = update.artifact
                    if (
                        artifact is not None
                        and artifact.uri == _PROPOSAL_URI
                        and artifact.mime_type == _PROPOSAL_MIME
                    ):
                        artifacts.append(artifact.text)
                elif update.kind is TransportUpdateKind.PERMISSION:
                    permission = update.permission
                    if permission is not None:
                        await transport.respond_permission(
                            session,
                            TransportPermissionDecision(
                                request_id=permission.request_id,
                                allowed=False,
                                reason="Leader planning cannot perform tools",
                            ),
                        )
                    unexpected_side_effect = True
                    await transport.cancel(session)
                    prompt_task.cancel()
                    break
                elif update.kind is TransportUpdateKind.TOOL:
                    unexpected_side_effect = True
                    await transport.cancel(session)
                    prompt_task.cancel()
                    break
            if unexpected_side_effect:
                raise TransportFailure(TransportFailureCode.UNEXPECTED_SIDE_EFFECT)
            response = await prompt_task
            if response.stop_reason == "cancelled":
                raise LeaderFailure(LeaderFailureCode.CANCELLATION)
            if response.stop_reason != "end_turn":
                raise LeaderFailure(LeaderFailureCode.NONZERO)
        if len(artifacts) != 1:
            raise LeaderFailure(LeaderFailureCode.SCHEMA)
        return _decode_proposal(artifacts[0], request)

    def _request_part(self, request: LeaderRequest) -> TransportPromptPart:
        payload = {
            "user_goal": request.user_goal,
            "project_context": {
                "project_root": request.project_context.project_root,
                "summary": request.project_context.summary,
            },
            "available_agents": [
                {
                    "instance_id": agent.instance_id,
                    "role": agent.role.value,
                    "backend_id": agent.backend_id,
                    "acp_route_id": agent.acp_route_id,
                }
                for agent in request.available_agents
            ],
            "permission_ceiling": request.permission_ceiling.value,
            "resolved_leader": {
                "backend_id": self.backend_id,
                "adapter_id": _ADAPTER_ID,
                "model_id": self.model,
                "version": self.version,
            },
            "schema_repair": request.schema_repair is not None,
            "proposal_schema": leader_proposal_json_schema(),
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded) > _MAX_REQUEST_BYTES:
            raise LeaderFailure(LeaderFailureCode.OVERSIZE)
        return TransportPromptPart.resource(
            uri=_REQUEST_URI,
            mime_type=_REQUEST_MIME,
            text=encoded.decode("utf-8"),
        )


def _decode_proposal(raw: str, request: LeaderRequest) -> LeaderProposal:
    failed = False
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, RecursionError):
        failed = True
        payload = None
    if failed:
        raise LeaderFailure(LeaderFailureCode.SCHEMA) from None
    try:
        return LeaderProposal.from_mapping(payload, request=request)
    except ProposalError as error:
        code = error.code
    except Exception:
        code = LeaderFailureCode.SCHEMA
    raise LeaderFailure(code) from None


def _drive_loop(factory: Callable[[], object], timeout: float) -> object:
    loop = asyncio.new_event_loop()
    task: asyncio.Task[object] | None = None
    try:
        task = loop.create_task(factory())  # type: ignore[arg-type]
        done, _pending = loop.run_until_complete(
            asyncio.wait((task,), timeout=timeout)
        )
        if not done:
            raise TransportFailure(TransportFailureCode.TIMEOUT)
        return task.result()
    finally:
        for pending in asyncio.all_tasks(loop):
            pending.cancel()
            pending._log_destroy_pending = False
        loop.close()


def _run_sync(factory: Callable[[], object], timeout: float) -> _T:
    running = True
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        running = False
    if not running:
        return _drive_loop(factory, timeout)  # type: ignore[return-value]
    result: list[object] = []
    failures: list[BaseException] = []

    def target() -> None:
        try:
            result.append(_drive_loop(factory, timeout))
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(
        target=target, name="agentdeck-acp-leader-bridge", daemon=True
    )
    thread.start()
    thread.join(timeout + _BRIDGE_THREAD_GRACE_SECONDS)
    if thread.is_alive():
        raise TransportFailure(TransportFailureCode.TIMEOUT)
    if failures:
        raise failures[0] from None
    if len(result) != 1:
        raise TransportFailure(TransportFailureCode.DISCONNECTED)
    return result[0]  # type: ignore[return-value]


__all__ = ["ACPLeader"]
