from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
import sys
from types import MappingProxyType
from uuid import uuid4

from agentdeck.adapters.config import ConfigResolver
from agentdeck.adapters.acp import ACPWorker
from agentdeck.adapters.acp_leader import ACPLeader
from agentdeck.adapters.acp_transport import ACPStdioTransport
from agentdeck.adapters.acp_worker_connection import create_worker_connection
from agentdeck.adapters.adapter_readiness import (
    AdapterReadiness, canonical_project_root, execution_command,
    verified_readiness,
)
from agentdeck.adapters.discovery import (
    ReadinessState, ToolDiscovery, discover_tools,
)
from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.adapters.system_clock import SystemClock
from agentdeck.application.approval_service import ApprovalService
from agentdeck.application.async_exit_coordinator import AsyncExitCoordinator
from agentdeck.application.execution_runtime import ForegroundExecutionRuntime
from agentdeck.application.execution_service import ExecutionService
from agentdeck.application.exit_service import ExitService
from agentdeck.application.project_lifecycle_service import ProjectLifecycleService
from agentdeck.application.recovery_service import RecoveryService
from agentdeck.application.session_service import SessionService
from agentdeck.ports.clock import Clock
from agentdeck.ports.transport import TransportPort
from agentdeck.product.renderer import render
from agentdeck.product.shell import (
    AsyncTerminalReader, ProductShell, validate_mission_preview,
)


def _new_session_id() -> str:
    return f"ses_{uuid4().hex}"


def _new_exit_request_id() -> str:
    return f"xrt_{uuid4().hex}"


def _new_recovery_run_id() -> str:
    return f"restart_{uuid4().hex}"


WorkerAgentFactory = Callable[
    [tuple[str, ...], str, tuple[tuple[str, str], ...]], object
]
TransportFactory = Callable[..., TransportPort]


@dataclass(frozen=True)
class ACPAdapterComposition:
    """Lazy per-instance ACP composition; readiness itself performs no I/O."""

    readiness: Mapping[str, AdapterReadiness]
    project_root: str
    clock: Clock
    worker_agent_factory: WorkerAgentFactory
    transport_factory: TransportFactory
    _worker_owners: list[object] = field(
        default_factory=list, repr=False, compare=False,
    )

    def _require_ready(self, backend_id: str) -> AdapterReadiness:
        if type(backend_id) is not str:
            raise ValueError("ACP adapter backend is not ready")
        value = self.readiness.get(backend_id)
        if not verified_readiness(value, backend_id):
            raise ValueError(f"ACP adapter {backend_id} is not ready")
        return value

    def leader(self, backend_id: str, *, model: str) -> ACPLeader:
        ready = self._require_ready(backend_id)
        command = execution_command(ready, model=model)
        transport_factory = partial(
            self.transport_factory, environment=dict(ready.environment),
        )
        return ACPLeader(
            command, backend_id=backend_id, model=model,
            version=ready.version, transport_factory=transport_factory,
        )

    def worker(self, backend_id: str, *, model: str = "native-default") -> ACPWorker:
        ready = self._require_ready(backend_id)
        command = execution_command(ready, model=model)
        agent = self.worker_agent_factory(
            command, self.project_root, ready.environment,
        )
        if any(owner is agent for owner in self._worker_owners):
            raise ValueError("ACP Worker factory must return a fresh connection owner")
        self._worker_owners.append(agent)
        return ACPWorker(
            agent=agent, project_root=self.project_root, clock=self.clock,
            project_boundary_enforced=True,
        )


def build_acp_adapter_composition(
    *, readiness: Mapping[str, AdapterReadiness], project_root: str,
    clock: Clock, worker_agent_factory: WorkerAgentFactory | None = None,
    transport_factory: TransportFactory = ACPStdioTransport,
) -> ACPAdapterComposition:
    project_root = canonical_project_root(project_root)
    if not callable(getattr(clock, "now", None)):
        raise TypeError("clock must expose now")
    selected_worker_factory = (
        create_worker_connection
        if worker_agent_factory is None else worker_agent_factory
    )
    if not callable(selected_worker_factory) or not callable(transport_factory):
        raise TypeError("ACP composition factories must be callable")
    copied: dict[str, AdapterReadiness] = {}
    for key, value in readiness.items():
        if type(key) is not str or type(value) is not AdapterReadiness:
            raise TypeError("ACP readiness mapping is invalid")
        copied[key] = value
    return ACPAdapterComposition(
        MappingProxyType(copied), project_root, clock,
        selected_worker_factory, transport_factory,
    )


def build_product_shell(
    *,
    project_root: str,
    read_line: Callable | None = None,
    write_line: Callable[[str], object] = print,
    clock_factory: Callable[[], object] = SystemClock,
    discovery_factory: Callable[[], Mapping[str, ToolDiscovery]] = discover_tools,
    config_factory: Callable[..., object] = ConfigResolver,
    store_factory: Callable[..., object] = SQLiteStore.open,
    shell_factory: Callable[..., ProductShell] = ProductShell,
    mission_service_factory: Callable[..., object] | None = None,
    session_id_factory: Callable[[], str] = _new_session_id,
    exit_request_id_factory: Callable[[], str] = _new_exit_request_id,
    recovery_run_id_factory: Callable[[], str] = _new_recovery_run_id,
    runtime_factory: Callable[[], object] = ForegroundExecutionRuntime,
    lifecycle_factory: Callable[..., object] = ProjectLifecycleService,
    recovery_factory: Callable[..., object] = RecoveryService,
    approval_service_factory: Callable[..., object] = ApprovalService,
    execution_service_factory: Callable[..., object] = ExecutionService,
    exit_coordinator_factory: Callable[..., object] = AsyncExitCoordinator,
    adapter_composition_factory: Callable[..., object] = build_acp_adapter_composition,
    adapter_readiness: Mapping[str, AdapterReadiness] | None = None,
) -> ProductShell:
    """Compose the foreground Product Shell through injectable factories."""

    clock = clock_factory()
    discovered = discovery_factory()
    config = config_factory(
        discovered={"permission": "approve-for-me"},
        global_values={},
        project_values={},
        session_values={},
    )
    default_permission = config.resolve("permission").value
    available_leaders = _available_leaders(discovered)
    store = store_factory(project_root, clock=clock)
    try:
        service = SessionService.open_latest(
            store=store,
            clock=clock,
            project_root=project_root,
            available_leaders=available_leaders,
            session_id_factory=session_id_factory,
        )
        session_id = service.current().session_id
        runtime = runtime_factory()
        if not callable(getattr(runtime, "is_empty", None)) or not runtime.is_empty():
            raise RuntimeError("fresh Product composition requires an empty runtime")
        lifecycle = lifecycle_factory(
            store=store, clock=clock, session_id=session_id,
        )
        recovery = recovery_factory(
            store=store, clock=clock, session_id=session_id,
            recovery_run_id=recovery_run_id_factory(),
        )
        exit_service = ExitService(
            store=store,
            clock=clock,
            session_id=session_id,
            request_id_factory=exit_request_id_factory,
        )
        mission_service = None
        if mission_service_factory is not None:
            mission_service = mission_service_factory(
                store=store, clock=clock, session_service=service,
                available_leaders=available_leaders, project_root=project_root,
                preview_validator=validate_mission_preview,
            )
        execution = None
        if adapter_readiness is not None:
            adapters = adapter_composition_factory(
                readiness=adapter_readiness, project_root=project_root,
                clock=clock,
            )
            approval = approval_service_factory(store=store, clock=clock)
            execution = execution_service_factory(
                store=store, clock=clock, approval_service=approval,
                worker_factory=lambda task: adapters.worker(task.backend),
                runtime=runtime, lifecycle=lifecycle,
            )
        exit_coordinator = exit_coordinator_factory(
            exit_service=exit_service, store=store, clock=clock,
            runtime=runtime, lifecycle=lifecycle, session_id=session_id,
        )
        reader = (
            AsyncTerminalReader(sys.stdin, sys.stdout)
            if read_line is None else read_line
        )
        return shell_factory(
            session_service=service,
            exit_coordinator=exit_coordinator,
            recovery_service=recovery,
            lifecycle=lifecycle,
            mission_service=mission_service,
            execution_service=execution,
            resume_snapshot_loader=lambda: store.load_execution_resume(session_id),
            available_leaders=available_leaders,
            read_line=reader,
            write_line=write_line,
            close=store.close,
            default_permission=default_permission,
            render_text=render,
        )
    except BaseException:
        store.close()
        raise


def _available_leaders(
    discovered: Mapping[str, ToolDiscovery],
) -> dict[str, tuple[str, ...]]:
    available: dict[str, tuple[str, ...]] = {}
    for name, fact in discovered.items():
        if (
            type(name) is str
            and type(fact) is ToolDiscovery
            and fact.readiness is ReadinessState.READY
            and "leader" in fact.capabilities
            and name in {"codex", "claude"}
        ):
            available[f"{name}-cli"] = ("native-default",)
    return dict(sorted(available.items()))


def run_product_dev(*, diagnostic: bool = False) -> int:
    if diagnostic:
        print("AgentDeck Product Kernel development entry: ready")
        return 0
    shell = build_product_shell(project_root=str(Path.cwd()))
    return asyncio.run(shell.run_async())


__all__ = [
    "ACPAdapterComposition", "build_acp_adapter_composition",
    "build_product_shell", "run_product_dev",
]
