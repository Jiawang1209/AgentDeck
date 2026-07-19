from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from uuid import uuid4

from agentdeck.adapters.config import ConfigResolver
from agentdeck.adapters.discovery import ReadinessState, ToolDiscovery, discover_tools
from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.adapters.system_clock import SystemClock
from agentdeck.application.exit_records import restore_pending_exit
from agentdeck.application.exit_service import ExitResult, ExitService
from agentdeck.application.session_service import SessionService
from agentdeck.ports.clock import Clock
from agentdeck.product.renderer import render
from agentdeck.product.shell import ProductShell, validate_mission_preview


def _new_session_id() -> str:
    return f"ses_{uuid4().hex}"


def _new_exit_request_id() -> str:
    return f"xrt_{uuid4().hex}"


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


__all__ = ["build_product_shell", "run_product_dev"]
