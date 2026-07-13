from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path

import pytest

from agentdeck import cli
from agentdeck.config import load_config
from agentdeck.contracts import (
    validate_conversation_runtime_contract,
    validate_leader_backend_contract,
)
from agentdeck.conversation.leader_gateway import LeaderGatewayError
from agentdeck.conversation.session import ConversationSession
from agentdeck.conversation.transports import (
    WorkerRuntimeFacts,
    WorkerTransportRouter,
    dispatch_worker_route,
)
from agentdeck.mission_orchestration import LeaderMissionCandidate


MISSION_TEXT = "让 planner 和 reviewer 串行完成验收，共2轮，secret=must-not-persist"


def _plan() -> dict[str, object]:
    return {
        "goal": "完成两步验收",
        "summary": "先规划再复核",
        "steps": [
            {
                "step": 1,
                "agent_id": "planner",
                "role": "planning",
                "task": "生成计划",
                "risk": "review",
                "requires_approval": True,
            },
            {
                "step": 2,
                "agent_id": "reviewer",
                "role": "review",
                "task": "复核计划",
                "risk": "review",
                "requires_approval": True,
            },
        ],
    }


class _FakeLeader:
    def generate_mission(self, request, cancel):
        if cancel.cancelled:
            raise LeaderGatewayError("Leader request cancelled")
        return LeaderMissionCandidate(
            provider="fake",
            model="acceptance-model",
            user_message=request.user_message,
            plan=_plan(),
            timeout_seconds=request.timeout_seconds,
        )


class _CancelledLeader:
    def generate_mission(self, _request, _cancel):
        raise LeaderGatewayError("Leader request cancelled")


def _facts() -> WorkerRuntimeFacts:
    return WorkerRuntimeFacts(
        session_state="ready",
        runtime_status="running",
        pane_id="%7",
        active_turn=False,
        pending_permission=False,
        workflow_running=False,
        ownership="agentdeck_owned",
        acp_capabilities={
            "structured_sessions": True,
            "streaming_updates": True,
            "permission_requests": True,
        },
        tmux_session="agentdeck",
    )


def test_phase3_m1_foreground_conversation_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "acceptance"
    root.mkdir()
    (root / ".git").mkdir()
    session = ConversationSession(root=root)

    setup = session.handle("请初始化这个项目")
    assert setup.kind == "project_setup_preview"
    assert not (root / ".agentdeck").exists()
    assert session.handle("确认执行当前预览").kind == "project_initialized"

    config_path = root / ".agentdeck" / "config.toml"
    config_text = config_path.read_text(encoding="utf-8")
    config_text = config_text.replace('provider = "deepseek"', 'provider = "fake"', 1)
    config_text = config_text.replace('model = "deepseek-chat"', 'model = "acceptance-model"', 1)
    config_path.write_text(config_text, encoding="utf-8")
    session.config = load_config(root)
    session.leader_gateway = _FakeLeader()
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}"
    )
    executions: list[str] = []
    session.preview_executor = lambda mission_id: executions.append(mission_id) or {
        "status": "started",
        "mission_id": mission_id,
        "governance": "approval_then_dispatch",
    }

    assert session.handle("/help").kind == "deterministic"
    preview = session.handle(MISSION_TEXT)
    assert preview.kind == "mission_preview"
    assert len(preview.payload["plan"]["steps"]) == 2
    confirmed = session.handle("确认执行当前预览")
    assert confirmed.kind == "preview_executed"
    assert executions == [preview.payload["mission_id"]]
    assert session.handle("/status").kind == "deterministic"
    assert session.handle("/approvals").payload == {
        "command": "approvals",
        "safety": "inspect",
        "executed": False,
    }
    assert session.handle("/trace msg_demo").payload["command"] == "trace"

    router = WorkerTransportRouter()
    planner = next(agent for agent in session.config.agents if agent.agent_id == "planner")
    reviewer = next(agent for agent in session.config.agents if agent.agent_id == "reviewer")
    acp_route = router.describe(replace(planner, transport="acp", transport_command=("fake-acp",)), _facts())
    tmux_route = router.describe(replace(reviewer, transport="tmux", transport_command=()), _facts())
    calls: list[str] = []
    dispatch_worker_route(
        acp_route,
        acp_dispatch=lambda: calls.append("acp"),
        tmux_dispatch=lambda: calls.append("unexpected-tmux"),
    )
    dispatch_worker_route(
        tmux_route,
        acp_dispatch=lambda: calls.append("unexpected-acp"),
        tmux_dispatch=lambda: calls.append("tmux"),
    )
    assert calls == ["acp", "tmux"]

    cancelled = ConversationSession(
        root=root,
        config=session.config,
        store=session.store,
        leader_gateway=_CancelledLeader(),
    ).handle("创建另一个任务")
    assert cancelled.kind == "cancelled"
    assert session.handle("/quit").kind == "exit"

    state = session.store.load()
    project_view = session.store.project_view(session.config)
    persisted = json.dumps(state, ensure_ascii=False)
    assert MISSION_TEXT not in persisted
    assert "must-not-persist" not in persisted
    assert len(state["plans"]) == len(state["missions"]) == 1
    assert state["conversation_event_outbox"] == []
    assert project_view.conversation["pending_preview"] is None
    assert project_view.conversation["latest_turn_state"] == "cancelled"
    compact_view = asdict(project_view)
    runtime_card = cli._conversation_runtime_card(compact_view)
    leader_card = cli._leader_backend_card(session.config)
    worker_card = cli._worker_transport_card(compact_view, session.config)
    assert validate_conversation_runtime_contract(runtime_card)["ok"] is True
    assert validate_leader_backend_contract(leader_card)["ok"] is True
    assert worker_card["count"] == len(session.config.agents)
