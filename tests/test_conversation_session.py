from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from agentdeck.config import load_config, write_default_config
from agentdeck.conversation.leader_gateway import LeaderGatewayError
from agentdeck.conversation.session import ConversationSession
from agentdeck.mission_orchestration import LeaderMissionCandidate
from agentdeck.providers import LeaderPlanRequest
from agentdeck.providers.plan_schema import build_leader_generation_provenance
from agentdeck.state import StateStore


MESSAGE = "让 Codex 和 Claude 一人一句接龙百家姓，共2轮"


def _generation(request) -> dict[str, object]:
    return build_leader_generation_provenance(
        request=LeaderPlanRequest(
            task=request.planning_task,
            config=request.config,
            model=request.config.leader.model,
            skill_context=request.skill_context,
            selected_agent_ids=request.selected_agent_ids,
            step_count=request.step_count,
            timeout_seconds=request.timeout_seconds,
        ),
        provider=request.config.leader.provider,
        constraint_mode="local",
    )


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
            selected_agent_ids=request.selected_agent_ids,
            step_count=request.step_count,
            leader_generation=_generation(request),
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


def test_open_natural_task_uses_one_frozen_selection_and_step_count(
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

    response = session.handle("请一起完成这个开放任务")

    assert response.kind == "mission_preview"
    assert [item["agent_id"] for item in response.payload["selected_agents"]] == [
        "planner",
        "reviewer",
    ]
    assert len(response.payload["plan"]["steps"]) == 2


def test_redacted_persisted_message_never_recomputes_mission_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, store = _project(tmp_path)
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}"
    )
    monkeypatch.setattr(
        "agentdeck.conversation.session._redact_persisted_user_message",
        lambda _message: "[persisted provenance redacted]",
    )

    response = ConversationSession(
        root=tmp_path,
        config=config,
        store=store,
        leader_gateway=_Gateway(),
    ).handle(MESSAGE)

    assert response.kind == "mission_preview"
    assert len(response.payload["plan"]["steps"]) == 2


def test_candidate_landing_failure_terminalizes_turn_instead_of_leaving_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, store = _project(tmp_path)
    monkeypatch.setattr(
        "agentdeck.conversation.session.create_mission_preview_from_candidate",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("unsafe detail")),
    )

    response = ConversationSession(
        root=tmp_path,
        config=config,
        store=store,
        leader_gateway=_Gateway(),
    ).handle(MESSAGE)

    assert response.kind == "failed"
    assert response.payload == {
        "blocker": "Leader planning failed at stage: schema",
        "stage": "schema",
        "diagnostic_code": None,
        "attempt_count": 0,
        "constraint_mode": "prompt_only",
    }
    assert store.project_view(config).conversation["latest_turn_state"] == "failed"
    assert store.all_events()[-1]["payload"]["stage"] == "schema"
    assert "unsafe detail" not in repr(store.load())


def test_postcommit_preview_audit_failure_returns_durable_preview_without_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, store = _project(tmp_path)
    real_append = store.append_event

    def fail_preview_audit(event):
        if event.event_type == "mission_preview_created":
            raise OSError("audit unavailable")
        real_append(event)

    monkeypatch.setattr(store, "append_event", fail_preview_audit)
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}"
    )
    response = ConversationSession(
        root=tmp_path,
        config=config,
        store=store,
        leader_gateway=_Gateway(),
    ).handle(MESSAGE)

    assert response.kind == "mission_preview"
    state = store.load()
    assert len(state["missions"]) == 1
    assert len(state["plans"]) == 1
    assert len(state["conversation_preview_bindings"]) == 1
    assert state["conversation_event_outbox"]
    assert store.project_view(config).conversation["latest_turn_state"] == "completed"

    retried = ConversationSession(
        root=tmp_path,
        config=config,
        store=store,
        leader_gateway=_Gateway(),
    ).handle(MESSAGE)

    assert retried.kind == "blocked"
    after = store.load()
    assert len(after["missions"]) == 1
    assert len(after["plans"]) == 1
    assert len(after["conversation_preview_bindings"]) == 1


def test_postsave_value_error_returns_exact_durable_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, store = _project(tmp_path)
    real_flush = store._flush_conversation_event_outbox_locked
    injected = False

    def fail_after_preview_save(state=None):
        nonlocal injected
        if (
            not injected
            and isinstance(state, dict)
            and len(state.get("missions", [])) == 1
        ):
            injected = True
            raise ValueError("post-save fault")
        return real_flush(state)

    monkeypatch.setattr(store, "_flush_conversation_event_outbox_locked", fail_after_preview_save)
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}"
    )

    response = ConversationSession(
        root=tmp_path,
        config=config,
        store=store,
        leader_gateway=_Gateway(),
    ).handle(MESSAGE)

    assert response.kind == "mission_preview"
    state = store.load()
    assert len(state["missions"]) == len(state["plans"]) == 1
    assert len(state["conversation_preview_bindings"]) == 1
    assert state["conversation_event_outbox"]
    assert store.project_view(config).conversation["latest_turn_state"] == "completed"


def test_partial_candidate_commit_is_rejected_and_turn_terminalizes_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, store = _project(tmp_path)
    real_commit = store.commit_conversation_mutation

    def partial_commit(mutation):
        if mutation.append_records.get("missions"):
            state = store.load()
            state["plans"].extend(mutation.append_records["plans"])
            store.save(state)
            raise ValueError("partial post-save fault")
        return real_commit(mutation)

    monkeypatch.setattr(store, "commit_conversation_mutation", partial_commit)
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}"
    )

    response = ConversationSession(
        root=tmp_path,
        config=config,
        store=store,
        leader_gateway=_Gateway(),
    ).handle(MESSAGE)

    assert response.kind == "failed"
    assert response.payload["stage"] == "schema"
    state = store.load()
    assert len(state["plans"]) == 1
    assert state["missions"] == []
    assert state["conversation_preview_bindings"] == []
    assert store.project_view(config).conversation["latest_turn_state"] == "failed"


@pytest.mark.parametrize(
    "message",
    [
        "共1轮完成任务",
        "共65轮完成任务",
        "共九十九轮完成任务",
        "共两百轮完成任务",
        "共壹佰轮完成任务",
        "共64.0轮完成任务",
        "共+64轮完成任务",
        "共2轮 then use 65 steps",
        "use 65 steps 然后共2轮",
        "共2轮 then use 4 steps",
        "use 4 steps 然后共2轮",
    ],
)
def test_explicit_invalid_step_count_durably_fails_before_leader_call(
    tmp_path: Path, message: str
) -> None:
    config, store = _project(tmp_path)
    gateway = _Gateway()

    response = ConversationSession(
        root=tmp_path,
        config=config,
        store=store,
        leader_gateway=gateway,
    ).handle(message)

    assert response.kind == "failed"
    assert response.payload["stage"] == "schema"
    assert gateway.calls == 0
    assert store.project_view(config).conversation["latest_turn_state"] == "failed"


def test_completed_turn_without_complete_mission_proof_fail_stops_without_reexecution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, store = _project(tmp_path)
    real_commit = store.commit_conversation_mutation
    injected = False

    def complete_turn_without_mission(mutation):
        nonlocal injected
        if not injected and mutation.append_records.get("missions"):
            injected = True
            partial_records = {
                key: records
                for key, records in mutation.append_records.items()
                if key != "missions"
            }
            real_commit(
                type(mutation)(
                    append_records=partial_records,
                    events=(),
                )
            )
            raise ValueError("post-domain-save proof failure")
        return real_commit(mutation)

    monkeypatch.setattr(store, "commit_conversation_mutation", complete_turn_without_mission)
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}"
    )
    gateway = _Gateway()

    response = ConversationSession(
        root=tmp_path,
        config=config,
        store=store,
        leader_gateway=gateway,
    ).handle(MESSAGE)

    assert response.kind == "blocked"
    assert response.payload == {
        "blocker": "Leader planning durable state is ambiguous; automatic recovery stopped",
        "stage": "durable_state",
        "fail_stop": True,
        "durable_turn_state": "completed",
    }
    state = store.load()
    assert gateway.calls == 1
    assert len(state["plans"]) == 1
    assert state["missions"] == []
    assert len(state["conversation_preview_bindings"]) == 1
    assert store.project_view(config).conversation["latest_turn_state"] == "completed"
    assert [
        event["event_type"] for event in store.all_events()
    ].count("conversation_turn_recovery_blocked") == 1


@pytest.mark.parametrize(
    "message",
    ["请严格四步骤完成开放任务", "请恰好四个串行步骤完成开放任务"],
)
def test_session_freezes_shared_parser_count_for_chinese_step_phrases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    config, store = _project(tmp_path)

    class FourStepGateway:
        def generate_mission(self, request, _cancel):
            plan = _plan()
            plan["steps"] = [
                {
                    "step": step,
                    "agent_id": "planner" if step % 2 else "reviewer",
                    "role": "planning" if step % 2 else "review",
                    "task": f"step {step}",
                    "risk": "review",
                    "requires_approval": True,
                }
                for step in range(1, 5)
            ]
            return LeaderMissionCandidate(
                provider=request.config.leader.provider,
                model=request.config.leader.model,
                user_message=request.user_message,
                plan=plan,
                timeout_seconds=request.timeout_seconds,
                selected_agent_ids=request.selected_agent_ids,
                step_count=request.step_count,
                leader_generation=_generation(request),
            )

    monkeypatch.setattr(
        "agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}"
    )
    response = ConversationSession(
        root=tmp_path,
        config=config,
        store=store,
        leader_gateway=FourStepGateway(),
    ).handle(message)

    assert response.kind == "mission_preview"
    assert len(response.payload["plan"]["steps"]) == 4


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
