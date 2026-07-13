from __future__ import annotations

import pytest

from agentdeck.conversation.transports import (
    OwnershipError,
    WorkerRuntimeFacts,
    WorkerTransportRouter,
    begin_ownership_change,
    finish_ownership_change,
    ownership_gate,
    dispatch_worker_route,
)
from agentdeck.models import AgentSpec


def _agent(transport: str = "acp") -> AgentSpec:
    return AgentSpec(
        agent_id="reviewer",
        role="review",
        provider="claude",
        command="claude",
        transport=transport,
        transport_command=("claude-agent-acp",) if transport == "acp" else (),
    )


def _facts(**overrides: object) -> WorkerRuntimeFacts:
    values = {
        "session_state": "ready",
        "runtime_status": "running",
        "pane_id": "%2",
        "active_turn": False,
        "pending_permission": False,
        "workflow_running": False,
        "ownership": "agentdeck_owned",
        "acp_capabilities": {
            "structured_sessions": True,
            "streaming_updates": True,
            "permission_requests": True,
        },
        "tmux_session": "agentdeck",
    }
    values.update(overrides)
    return WorkerRuntimeFacts(**values)


def test_configured_acp_route_never_silently_becomes_tmux() -> None:
    route = WorkerTransportRouter().describe(_agent("acp"), _facts(session_state="disconnected"))

    assert route.configured_transport == "acp"
    assert route.effective_transport == "acp"
    assert route.ready is False
    assert route.blocker == "ACP Worker session is not ready"
    assert route.fallback == {
        "available": True,
        "transport": "tmux",
        "requires_confirmation": True,
        "blocker": "explicit reroute required",
    }


def test_configured_tmux_route_uses_only_visible_pane_facts() -> None:
    route = WorkerTransportRouter().describe(_agent("tmux"), _facts())

    assert route.effective_transport == "tmux"
    assert route.ready is True
    assert route.live_mirror["read_only"] is True
    assert route.live_mirror["attach_command"] == "tmux attach -t agentdeck"
    assert route.live_mirror["select_pane_command"] == "tmux select-pane -t %2"
    assert all(control["safety"] == "inspect" for control in route.mirror_controls)


@pytest.mark.parametrize(
    ("updates", "blocker"),
    [
        ({"session_state": "disconnected"}, "Worker session is not ready"),
        ({"active_turn": True}, "Worker has an active turn"),
        ({"pending_permission": True}, "Worker has a pending permission"),
        ({"workflow_running": True}, "Worker is executing a workflow step"),
        ({"ownership": "takeover_pending"}, "Worker ownership change is pending"),
    ],
)
def test_takeover_gate_fails_closed(updates: dict[str, object], blocker: str) -> None:
    gate = ownership_gate(_facts(**updates), target="human_owned")

    assert gate.allowed is False
    assert gate.blocker == blocker


def test_takeover_and_return_follow_single_writer_pending_sequence() -> None:
    takeover = begin_ownership_change(_facts(), target="human_owned")
    assert (takeover.from_state, takeover.to_state) == (
        "agentdeck_owned",
        "takeover_pending",
    )
    human = finish_ownership_change(takeover.to_state, success=True)
    assert (human.from_state, human.to_state) == ("takeover_pending", "human_owned")

    returned = begin_ownership_change(
        _facts(ownership="human_owned"), target="agentdeck_owned"
    )
    assert (returned.from_state, returned.to_state) == (
        "human_owned",
        "return_pending",
    )
    completed = finish_ownership_change(returned.to_state, success=True)
    assert completed.to_state == "agentdeck_owned"


def test_failed_pending_change_rolls_back_to_previous_writer() -> None:
    assert finish_ownership_change("takeover_pending", success=False).to_state == "agentdeck_owned"
    assert finish_ownership_change("return_pending", success=False).to_state == "human_owned"


@pytest.mark.parametrize("ownership", ["takeover_pending", "return_pending", "human_owned"])
def test_non_agentdeck_ownership_blocks_automation_prompt(ownership: str) -> None:
    route = WorkerTransportRouter().describe(_agent(), _facts(ownership=ownership))

    assert route.automation_allowed is False
    assert route.prompt_blocker == f"Worker is {ownership}"


def test_invalid_direct_ownership_jump_is_rejected() -> None:
    with pytest.raises(OwnershipError, match="ownership change must enter pending state"):
        finish_ownership_change("agentdeck_owned", success=True)


def test_dispatcher_calls_only_selected_acp_transport_without_fallback() -> None:
    route = WorkerTransportRouter().describe(_agent("acp"), _facts())
    calls: list[str] = []

    with pytest.raises(RuntimeError, match="acp failed"):
        dispatch_worker_route(
            route,
            acp_dispatch=lambda: calls.append("acp") or (_ for _ in ()).throw(RuntimeError("acp failed")),
            tmux_dispatch=lambda: calls.append("tmux"),
        )

    assert calls == ["acp"]


def test_dispatcher_calls_only_selected_tmux_transport() -> None:
    route = WorkerTransportRouter().describe(_agent("tmux"), _facts())
    calls: list[str] = []

    result = dispatch_worker_route(
        route,
        acp_dispatch=lambda: calls.append("acp"),
        tmux_dispatch=lambda: calls.append("tmux") or "sent",
    )

    assert result == "sent"
    assert calls == ["tmux"]
