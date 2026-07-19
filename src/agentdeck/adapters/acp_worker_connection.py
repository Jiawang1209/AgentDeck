"""One lazy official-SDK ACP process owner for one Product Kernel Worker."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any, Final

from acp import PROTOCOL_VERSION, spawn_agent_process
from acp.schema import InitializeResponse, NewSessionResponse

from agentdeck.adapters.adapter_readiness import (
    canonical_project_root, exact_absolute_path, merged_environment,
)
from agentdeck.ports.transport import (
    transport_argv, transport_byte_bound, transport_project_root,
    transport_timeout,
)


_DEFAULT_MAX_BYTES: Final = 1024 * 1024
_DEFAULT_TIMEOUT_SECONDS: Final = 30.0


class _WorkerClient:
    def __init__(self, owner: "ACPWorkerConnection") -> None:
        self._owner = owner

    async def session_update(
        self, session_id: str, update: object, **kwargs: Any,
    ) -> None:
        await self._owner._callback("session_update", session_id, update, **kwargs)

    async def request_permission(
        self, session_id: str, tool_call: object, options: object, **kwargs: Any,
    ) -> object:
        return await self._owner._callback(
            "request_permission", session_id, tool_call, options, **kwargs
        )


class ACPWorkerConnection:
    """Own a single lazy subprocess, SDK connection, and Worker callbacks."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        project_root: str,
        environment: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        spawn_factory: object | None = None,
    ) -> None:
        checked = transport_argv(command)
        if not exact_absolute_path(checked[0]):
            raise ValueError("ACP Worker command must use an absolute executable")
        self.command = checked
        self.project_root = canonical_project_root(
            transport_project_root(project_root)
        )
        self.environment = merged_environment(environment)
        self.max_bytes = transport_byte_bound(max_bytes)
        self.timeout_seconds = transport_timeout(timeout_seconds)
        selected = spawn_agent_process if spawn_factory is None else spawn_factory
        if not callable(selected):
            raise TypeError("spawn_factory must be callable")
        self._spawn_factory = selected
        self._worker: object | None = None
        self._manager: AbstractAsyncContextManager[object] | None = None
        self._connection: object | None = None
        self._close_lock = asyncio.Lock()
        self._started = False
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def on_connect(self, worker: object) -> None:
        required = ("session_update", "request_permission")
        if self._worker is not None or not all(
            callable(getattr(worker, name, None)) for name in required
        ):
            raise ValueError("ACP Worker connection owner is already bound")
        self._worker = worker

    async def initialize(
        self, protocol_version: int, **kwargs: Any,
    ) -> InitializeResponse:
        connection = await self._connect()
        try:
            response = await connection.initialize(protocol_version, **kwargs)
            if (
                type(response) is not InitializeResponse
                or response.protocol_version != PROTOCOL_VERSION
            ):
                raise ValueError("ACP initialization response is invalid")
            return response
        except BaseException:
            await self.aclose()
            raise

    async def new_session(self, **kwargs: Any) -> NewSessionResponse:
        connection = self._require_connection()
        try:
            response = await connection.new_session(**kwargs)
            if type(response) is not NewSessionResponse or not response.session_id:
                raise ValueError("ACP session response is invalid")
            return response
        except BaseException:
            await self.aclose()
            raise

    async def prompt(self, *args: object, **kwargs: Any) -> object:
        connection = self._require_connection()
        try:
            return await connection.prompt(*args, **kwargs)
        finally:
            await self.aclose()

    async def cancel(self, *args: object, **kwargs: Any) -> None:
        connection = self._require_connection()
        try:
            await connection.cancel(*args, **kwargs)
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        async with self._close_lock:
            manager = self._manager
            self._manager = None
            self._connection = None
            if manager is None:
                if self._started:
                    self._closed = True
                return
            self._closed = True
            await manager.__aexit__(None, None, None)

    async def _connect(self) -> object:
        if self._started or self._closed:
            raise ValueError("ACP Worker connection lifecycle is invalid")
        if self._worker is None:
            raise ValueError("ACP Worker connection is not bound")
        self._started = True
        client = _WorkerClient(self)
        manager = self._spawn_factory(
            client, self.command[0], *self.command[1:],
            env=dict(self.environment), cwd=self.project_root,
            transport_kwargs={
                "limit": self.max_bytes,
                "stderr": asyncio.subprocess.DEVNULL,
                "shutdown_timeout": min(self.timeout_seconds, 2.0),
            },
            receive_timeout=self.timeout_seconds,
        )
        self._manager = manager
        entered_manager = False
        try:
            entered = await manager.__aenter__()
            entered_manager = True
            if type(entered) is not tuple or len(entered) != 2:
                raise TypeError("ACP SDK spawn result is invalid")
            self._connection = entered[0]
            return entered[0]
        except BaseException:
            if entered_manager:
                await self.aclose()
            else:
                self._manager = None
                self._connection = None
                self._closed = True
            raise

    async def _callback(self, method: str, *args: object, **kwargs: Any) -> object:
        worker = self._worker
        if worker is None or self._closed:
            raise ValueError("ACP Worker callback lifecycle is invalid")
        return await getattr(worker, method)(*args, **kwargs)

    def _require_connection(self) -> object:
        if self._connection is None or self._closed:
            raise ValueError("ACP Worker connection is not active")
        return self._connection


def create_worker_connection(
    command: tuple[str, ...], project_root: str,
    environment: tuple[tuple[str, str], ...],
) -> ACPWorkerConnection:
    return ACPWorkerConnection(
        command, project_root=project_root, environment=environment,
    )


__all__ = ["ACPWorkerConnection", "create_worker_connection"]
