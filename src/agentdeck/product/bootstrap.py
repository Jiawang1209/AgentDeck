from __future__ import annotations

from collections.abc import Callable, Mapping
from hashlib import sha256
from pathlib import Path

from agentdeck.adapters.config import ConfigResolver
from agentdeck.adapters.discovery import ReadinessState, ToolDiscovery, discover_tools
from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.adapters.system_clock import SystemClock
from agentdeck.application.session_service import SessionService
from agentdeck.product.renderer import render
from agentdeck.product.shell import ProductShell


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
        service = SessionService(
            store=store,
            clock=clock,
            session_id=_session_id(project_root),
            project_root=project_root,
            available_leaders=available_leaders,
        )
        mission_service = None
        if mission_service_factory is not None:
            mission_service = mission_service_factory(
                store=store, clock=clock, session_service=service,
                available_leaders=available_leaders, project_root=project_root,
            )
        return shell_factory(
            session_service=service,
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


def _session_id(project_root: str) -> str:
    resolved = Path(project_root).resolve(strict=True)
    digest = sha256(str(resolved).encode("utf-8", "strict")).hexdigest()[:24]
    return f"ses_{digest}"


def run_product_dev(*, diagnostic: bool = False) -> int:
    if diagnostic:
        print("AgentDeck Product Kernel development entry: ready")
        return 0
    return build_product_shell(project_root=str(Path.cwd())).run()


__all__ = ["build_product_shell", "run_product_dev"]
