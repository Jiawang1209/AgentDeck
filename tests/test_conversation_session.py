from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from agentdeck.config import load_config, write_default_config
from agentdeck.conversation.leader_gateway import LeaderGatewayError
from agentdeck.conversation.session import ConversationSession
from agentdeck.mission_orchestration import LeaderMissionCandidate
from agentdeck.state import StateStore


MESSAGE = "让 Codex 和 Claude 一人一句接龙百家姓，共2轮"


def _plan() -> dict[str, object]:
    return {
        "goal": "完成两轮接龙",
        "summary": "严格串行",
        "steps": [
            {
                "step": 1,
                "agent_id": "planner",
                "role": "planning",
                "task": "第一轮",
                "risk": "review",
                "requires_approval": True,
            },
            {
                "step": 2,
                "agent_id": "reviewer",
                "role": "review",
                "task": "第二轮",
                "risk": "review",
                "requires_approval": True,
            },
        ],
    }


def _project(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    write_default_config(tmp_path)
    path = tmp_path / ".agentdeck" / "config.toml"
    text = path.read_text(encoding="utf-8")
    text = text.replace('provider = "deepseek"', 'provider = "fake"', 1)
    text = text.replace('model = "deepseek-chat"', 'model = "fake-plan"', 1)
    path.write_text(text, encoding="utf-8")
    return load_config(tmp_path), StateStore(tmp_path)


class _Gateway:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def generate_mission(self, request, _cancel):
        self.calls += 1
        if self.fail:
            raise LeaderGatewayError("Leader request cancelled")
        return LeaderMissionCandidate(
            provider=request.config.leader.provider,
            model=request.config.leader.model,
            user_message=request.user_message,
            plan=_plan(),
            timeout_seconds=request.timeout_seconds,
        )


def test_uninitialized_session_and_setup_preview_are_zero_write(tmp_path: Path) -> None:
    session = ConversationSession(root=tmp_path)

    response = session.handle("请初始化并开始工作")

    assert response.kind == "project_setup_preview"
    assert response.payload["binding"]["state"] == "pending"
    assert not (tmp_path / ".agentdeck").exists()


def test_natural_setup_confirmation_initializes_same_session(tmp_path: Path) -> None:
    session = ConversationSession(root=tmp_path)
    session.handle("请初始化并开始工作")

    confirmed = session.handle("确认执行当前预览")
    status = session.handle("/status")

    assert confirmed.kind == "project_initialized"
    assert confirmed.payload["status"] == "initialized"
    assert (tmp_path / ".agentdeck" / "config.toml").exists()
    assert session.config is not None
    assert session.store is not None
    assert status.kind == "deterministic"
    assert any(
        event["event_type"] == "project_initialized_from_conversation"
        for event in session.store.all_events()
    )


def test_deterministic_status_does_not_call_missing_leader(tmp_path: Path) -> None:
    config, store = _project(tmp_path)
    gateway = _Gateway()
    session = ConversationSession(
        root=tmp_path,
        config=replace(config, leader=replace(config.leader, provider="missing")),
        store=store,
        leader_gateway=gateway,
    )

    response = session.handle("/status")

    assert response.kind == "deterministic"
    assert response.payload["schema_version"] == "project-view/v1"
    assert gateway.calls == 0
    state = store.load()
    assert len(state["conversation_turns"]) == 1
    assert store.project_view(session.config).conversation["latest_turn_state"] == "completed"
    assert "/status" not in repr(state["conversation_turns"])


def test_leader_turn_commits_mission_and_exact_pending_binding_without_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, store = _project(tmp_path)
    gateway = _Gateway()
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}"
    )
    session = ConversationSession(
        root=tmp_path, config=config, store=store, leader_gateway=gateway
    )

    response = session.handle(MESSAGE)

    assert response.kind == "mission_preview"
    state = store.load()
    assert gateway.calls == 1
    assert len(state["plans"]) == len(state["missions"]) == 1
    assert len(state["conversation_preview_bindings"]) == 1
    assert state["conversation_event_outbox"] == []
    assert store.project_view(config).conversation["pending_preview"]["preview_kind"] == "mission_confirm"
    assert MESSAGE not in repr(state["conversation_sessions"])
    assert MESSAGE not in repr(state["conversation_turns"])
    assert MESSAGE not in repr(state["conversation_preview_bindings"])


def test_session_context_is_bounded_and_never_persisted(tmp_path: Path) -> None:
    config, store = _project(tmp_path)
    session = ConversationSession(root=tmp_path, config=config, store=store)

    for index in range(40):
        session.handle(f"/help {index} " + "x" * 5000)

    assert len(session.context_items) <= 24
    assert session.context_bytes <= 128 * 1024
    assert "x" * 100 not in repr(store.load()["conversation_turns"])


def test_cancelled_leader_turn_terminalizes_without_partial_mission(tmp_path: Path) -> None:
    config, store = _project(tmp_path)
    session = ConversationSession(
        root=tmp_path, config=config, store=store, leader_gateway=_Gateway(fail=True)
    )

    response = session.handle(MESSAGE)

    assert response.kind == "cancelled"
    assert store.load()["plans"] == []
    assert store.load()["missions"] == []
    assert store.project_view(config).conversation["latest_turn_state"] == "cancelled"


def test_natural_confirmation_consumes_exact_mission_preview_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, store = _project(tmp_path)
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}"
    )
    calls: list[str] = []
    session = ConversationSession(
        root=tmp_path,
        config=config,
        store=store,
        leader_gateway=_Gateway(),
        preview_executor=lambda mission_id: calls.append(mission_id)
        or {"status": "started", "mission_id": mission_id},
    )
    preview = session.handle(MESSAGE)

    confirmed = session.handle("确认执行当前预览")

    assert confirmed.kind == "preview_executed"
    assert calls == [preview.payload["mission_id"]]
    assert store.project_view(config).conversation["pending_preview"] is None
    second = session.handle("确认执行当前预览")
    assert second.kind == "blocked"
    assert calls == [preview.payload["mission_id"]]


def test_pending_preview_blocks_new_leader_turn_but_not_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, store = _project(tmp_path)
    gateway = _Gateway()
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}"
    )
    session = ConversationSession(
        root=tmp_path, config=config, store=store, leader_gateway=gateway
    )
    session.handle(MESSAGE)

    blocked = session.handle("再设计另一个任务")
    status = session.handle("/status")

    assert blocked.kind == "blocked"
    assert blocked.payload["blocker"] == "pending preview requires a decision"
    assert status.kind == "deterministic"
    assert gateway.calls == 1
