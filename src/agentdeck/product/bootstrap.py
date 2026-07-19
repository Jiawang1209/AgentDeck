from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

from agentdeck.adapters.config import ConfigResolver
from agentdeck.adapters.acp import ACPWorker
from agentdeck.adapters.acp_leader import ACPLeader
from agentdeck.adapters.acp_transport import ACPStdioTransport
from agentdeck.adapters.discovery import (
    AdapterReadiness, ReadinessState, ToolDiscovery, canonical_adapter_version,
    discover_tools,
)
from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.adapters.system_clock import SystemClock
from agentdeck.application.exit_records import restore_pending_exit
from agentdeck.application.exit_service import ExitResult, ExitService
from agentdeck.application.session_service import SessionService
from agentdeck.ports.clock import Clock
from agentdeck.ports.transport import TransportPort
from agentdeck.product.renderer import render
from agentdeck.product.shell import ProductShell, validate_mission_preview


def _new_session_id() -> str:
    return f"ses_{uuid4().hex}"


def _new_exit_request_id() -> str:
    return f"xrt_{uuid4().hex}"


WorkerAgentFactory = Callable[[tuple[str, ...], str], object]
TransportFactory = Callable[..., TransportPort]


@dataclass(frozen=True)
class ACPAdapterComposition:
    """Lazy per-instance ACP composition; readiness itself performs no I/O."""

    readiness: Mapping[str, AdapterReadiness]
    project_root: str
    clock: Clock
    worker_agent_factory: WorkerAgentFactory
    transport_factory: TransportFactory

    def _require_ready(self, backend_id: str) -> AdapterReadiness:
        if type(backend_id) is not str:
            raise ValueError("ACP adapter backend is not ready")
        value = self.readiness.get(backend_id)
        expected = {
            "codex-cli": ("agentdeck-codex-acp",),
            "claude-cli": ("claude-agent-acp",),
        }.get(backend_id)
        if (
            type(value) is not AdapterReadiness
            or type(value.ready) is not bool or value.ready is not True
            or type(value.backend_id) is not str or value.backend_id != backend_id
            or type(value.command) is not tuple or value.command != expected
            or not canonical_adapter_version(backend_id, value.version)
            or value.diagnostic is not None
            or type(value.fallbacks) is not tuple or value.fallbacks != ()
        ):
            raise ValueError(f"ACP adapter {backend_id} is not ready")
        return value

    def leader(self, backend_id: str, *, model: str) -> ACPLeader:
        ready = self._require_ready(backend_id)
        return ACPLeader(
            ready.command, backend_id=backend_id, model=model,
            version=ready.version, transport_factory=self.transport_factory,
        )

    def worker(self, backend_id: str) -> ACPWorker:
        ready = self._require_ready(backend_id)
        agent = self.worker_agent_factory(ready.command, self.project_root)
        return ACPWorker(
            agent=agent, project_root=self.project_root, clock=self.clock,
            project_boundary_enforced=True,
        )


def build_acp_adapter_composition(
    *, readiness: Mapping[str, AdapterReadiness], project_root: str,
    clock: Clock, worker_agent_factory: WorkerAgentFactory,
    transport_factory: TransportFactory = ACPStdioTransport,
) -> ACPAdapterComposition:
    if type(project_root) is not str or not project_root.strip():
        raise ValueError("project_root must be a nonempty string")
    if not callable(getattr(clock, "now", None)):
        raise TypeError("clock must expose now")
    if not callable(worker_agent_factory) or not callable(transport_factory):
        raise TypeError("ACP composition factories must be callable")
    copied: dict[str, AdapterReadiness] = {}
    for key, value in readiness.items():
        if type(key) is not str or type(value) is not AdapterReadiness:
            raise TypeError("ACP readiness mapping is invalid")
        copied[key] = value
    return ACPAdapterComposition(
        MappingProxyType(copied), project_root, clock,
        worker_agent_factory, transport_factory,
    )


def build_product_shell(
    *,
    project_root: str,
    read_line: Callable[[str], str] = input,
    write_line: Callable[[str], object] = print,
    clock_factory: Callable[[], object] = SystemClock,
    discovery_factory: Callable[[], Mapping[str, ToolDiscovery]] = discover_tools,
    config_factory: Callable[..., object] = ConfigResolver,
    store_factory: Callable[..., object] = SQLiteStore.open,
    shell_factory: Callable[..., ProductShell] = ProductShell,
    mission_service_factory: Callable[..., object] | None = None,
    session_id_factory: Callable[[], str] = _new_session_id,
    exit_request_id_factory: Callable[[], str] = _new_exit_request_id,
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
        exit_service = ExitService(
            store=store,
            clock=clock,
            session_id=service.current().session_id,
            request_id_factory=exit_request_id_factory,
        )
        restored_exit = _restored_exit(store, service, clock)
        mission_service = None
        if mission_service_factory is not None:
            mission_service = mission_service_factory(
                store=store, clock=clock, session_service=service,
                available_leaders=available_leaders, project_root=project_root,
                preview_validator=validate_mission_preview,
            )
        return shell_factory(
            session_service=service,
            exit_service=exit_service,
            restored_exit=restored_exit,
            mission_service=mission_service,
            available_leaders=available_leaders,
            read_line=read_line,
            write_line=write_line,
            close=store.close,
            default_permission=default_permission,
            render_text=render,
        )
    except BaseException:
        store.close()
        raise


def _restored_exit(
    store: SQLiteStore,
    service: SessionService,
    clock: Clock,
) -> ExitResult | None:
    return restore_pending_exit(
        store=store, clock=clock, session_id=service.current().session_id,
    )


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
    return build_product_shell(project_root=str(Path.cwd())).run()


__all__ = [
    "ACPAdapterComposition", "build_acp_adapter_composition",
    "build_product_shell", "run_product_dev",
]
