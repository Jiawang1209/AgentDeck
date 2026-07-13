from __future__ import annotations

from dataclasses import dataclass
import re
from collections.abc import Callable
from typing import Any, TypeVar

from ..models import AgentSpec


class OwnershipError(ValueError):
    pass


@dataclass(frozen=True)
class WorkerRuntimeFacts:
    session_state: str
    runtime_status: str
    pane_id: str | None
    active_turn: bool
    pending_permission: bool
    workflow_running: bool
    ownership: str
    acp_capabilities: dict[str, bool]
    tmux_session: str | None


@dataclass(frozen=True)
class OwnershipGate:
    allowed: bool
    blocker: str | None


@dataclass(frozen=True)
class OwnershipChange:
    from_state: str
    to_state: str
    reason: str


@dataclass(frozen=True)
class WorkerRoute:
    agent_id: str
    configured_transport: str
    effective_transport: str
    ready: bool
    blocker: str | None
    fallback: dict[str, object]
    live_mirror: dict[str, object]
    mirror_controls: tuple[dict[str, object], ...]
    ownership: str
    automation_allowed: bool
    prompt_blocker: str | None


def _mirror(facts: WorkerRuntimeFacts) -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
    session = facts.tmux_session
    pane = facts.pane_id
    safe_session = isinstance(session, str) and re.fullmatch(r"[A-Za-z0-9_.:-]+", session) is not None
    safe_pane = isinstance(pane, str) and re.fullmatch(r"%[0-9]+", pane) is not None
    available = bool(safe_session and facts.runtime_status == "running" and safe_pane)
    attach = f"tmux attach -t {session}" if safe_session else None
    select = f"tmux select-pane -t {pane}" if safe_pane else None
    blocker = None if available else "tmux pane is not ready"
    controls = (
        {
            "kind": "attach_session",
            "label": "Attach tmux mirror",
            "command": attach,
            "safety": "inspect",
            "enabled": available,
            "blocker": blocker,
        },
        {
            "kind": "select_pane",
            "label": "Select Worker pane",
            "command": select,
            "safety": "inspect",
            "enabled": available,
            "blocker": blocker,
        },
    )
    return (
        {
            "available": available,
            "read_only": True,
            "attach_command": attach,
            "select_pane_command": select,
            "blocker": blocker,
        },
        controls,
    )


class WorkerTransportRouter:
    def describe(self, agent: AgentSpec, facts: WorkerRuntimeFacts) -> WorkerRoute:
        if not isinstance(agent, AgentSpec) or not isinstance(facts, WorkerRuntimeFacts):
            raise TypeError("Worker route requires AgentSpec and WorkerRuntimeFacts")
        if agent.transport not in {"acp", "tmux"}:
            raise ValueError("unsupported Worker transport")
        mirror, controls = _mirror(facts)
        if agent.transport == "acp":
            capabilities_ready = all(
                facts.acp_capabilities.get(name) is True
                for name in ("structured_sessions", "streaming_updates")
            )
            if not agent.transport_command:
                blocker = "ACP Worker transport command is missing"
            elif facts.session_state != "ready":
                blocker = "ACP Worker session is not ready"
            elif not capabilities_ready:
                blocker = "ACP Worker capabilities are incomplete"
            else:
                blocker = None
            fallback = {
                "available": bool(mirror["available"]),
                "transport": "tmux",
                "requires_confirmation": True,
                "blocker": "explicit reroute required",
            }
        else:
            blocker = None if mirror["available"] else "tmux Worker pane is not ready"
            fallback = {
                "available": False,
                "transport": None,
                "requires_confirmation": True,
                "blocker": "no automatic fallback",
            }
        prompt_blocker = None
        if facts.ownership != "agentdeck_owned":
            prompt_blocker = f"Worker is {facts.ownership}"
        elif facts.active_turn:
            prompt_blocker = "Worker has an active turn"
        elif facts.pending_permission:
            prompt_blocker = "Worker has a pending permission"
        elif facts.workflow_running:
            prompt_blocker = "Worker is executing a workflow step"
        return WorkerRoute(
            agent_id=agent.agent_id,
            configured_transport=agent.transport,
            effective_transport=agent.transport,
            ready=blocker is None,
            blocker=blocker,
            fallback=fallback,
            live_mirror=mirror,
            mirror_controls=controls,
            ownership=facts.ownership,
            automation_allowed=blocker is None and prompt_blocker is None,
            prompt_blocker=prompt_blocker,
        )


def ownership_gate(facts: WorkerRuntimeFacts, *, target: str) -> OwnershipGate:
    if target not in {"human_owned", "agentdeck_owned"}:
        raise OwnershipError("invalid ownership target")
    if facts.ownership in {"takeover_pending", "return_pending"}:
        return OwnershipGate(False, "Worker ownership change is pending")
    expected = "agentdeck_owned" if target == "human_owned" else "human_owned"
    if facts.ownership != expected:
        return OwnershipGate(False, "Worker ownership does not match requested change")
    if facts.session_state != "ready":
        return OwnershipGate(False, "Worker session is not ready")
    if facts.active_turn:
        return OwnershipGate(False, "Worker has an active turn")
    if facts.pending_permission:
        return OwnershipGate(False, "Worker has a pending permission")
    if facts.workflow_running:
        return OwnershipGate(False, "Worker is executing a workflow step")
    return OwnershipGate(True, None)


def begin_ownership_change(
    facts: WorkerRuntimeFacts, *, target: str
) -> OwnershipChange:
    gate = ownership_gate(facts, target=target)
    if not gate.allowed:
        raise OwnershipError(gate.blocker or "ownership change blocked")
    if target == "human_owned":
        return OwnershipChange("agentdeck_owned", "takeover_pending", "takeover_confirmed")
    return OwnershipChange("human_owned", "return_pending", "return_confirmed")


def finish_ownership_change(pending_state: str, *, success: bool) -> OwnershipChange:
    if pending_state == "takeover_pending":
        return OwnershipChange(
            pending_state,
            "human_owned" if success else "agentdeck_owned",
            "takeover_completed" if success else "takeover_rolled_back",
        )
    if pending_state == "return_pending":
        return OwnershipChange(
            pending_state,
            "agentdeck_owned" if success else "human_owned",
            "return_completed" if success else "return_rolled_back",
        )
    raise OwnershipError("ownership change must enter pending state")


T = TypeVar("T")


def dispatch_worker_route(
    route: WorkerRoute,
    *,
    acp_dispatch: Callable[[], T],
    tmux_dispatch: Callable[[], T],
) -> T:
    if not isinstance(route, WorkerRoute):
        raise TypeError("route must be a WorkerRoute")
    if not route.ready:
        raise RuntimeError(route.blocker or "Worker transport is not ready")
    if not route.automation_allowed:
        raise RuntimeError(route.prompt_blocker or "Worker automation is blocked")
    if route.effective_transport == "acp":
        return acp_dispatch()
    if route.effective_transport == "tmux":
        return tmux_dispatch()
    raise RuntimeError("unsupported effective Worker transport")
