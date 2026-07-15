from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path

import pytest

from agentdeck import cli
from agentdeck.config import load_config, write_default_config
from agentdeck.contracts import (
    validate_conversation_runtime_contract,
    validate_leader_backend_contract,
)
from agentdeck.conversation.leader_gateway import LeaderGatewayError
from agentdeck.conversation.bindings import execution_digest
from agentdeck.conversation.session import ConversationSession
from agentdeck.conversation.transports import (
    WorkerRuntimeFacts,
    WorkerTransportRouter,
    dispatch_worker_route,
)
from agentdeck.mission_orchestration import LeaderMissionCandidate
from agentdeck.providers import LeaderPlanRequest
from agentdeck.providers.plan_schema import build_leader_generation_provenance
from agentdeck.semantic_authority import (
    extract_semantic_authority,
    semantic_authority_hash,
)
from agentdeck.state import canonical_snapshot_hash, execution_policy_snapshot


MISSION_TEXT = "让 planner 和 reviewer 串行完成验收，共2轮"
SENSITIVE_MISSION_TEXT = f"{MISSION_TEXT}，secret=must-not-persist"
SEMANTIC_MISSION_TEXT = (
    "Have planner and reviewer complete 4 steps strictly sequentially. "
    "The phases must be exactly implementation, review, revision, acceptance: "
    "First, planner creates artifact.txt with content exactly draft-v1 newline; "
    "Second, reviewer performs a read-only review and requires accepted-v2; "
    "Third, planner updates artifact.txt to exactly accepted-v2 newline; "
    "Fourth, reviewer performs read-only verification of the exact bytes. "
    "There are 4 steps.\n"
)


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
    def __init__(self) -> None:
        self.calls = 0

    def generate_mission(self, request, cancel):
        self.calls += 1
        if cancel.cancelled:
            raise LeaderGatewayError("Leader request cancelled")
        return LeaderMissionCandidate(
            provider="fake",
            model="acceptance-model",
            user_message=request.user_message,
            plan=_plan(),
            timeout_seconds=request.timeout_seconds,
            selected_agent_ids=request.selected_agent_ids,
            step_count=request.step_count,
            leader_generation=build_leader_generation_provenance(
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
            ),
        )


def test_sensitive_semantic_intake_clarifies_without_leader_or_scheduling_state(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sensitive"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    config_path = root / ".agentdeck" / "config.toml"
    config_text = config_path.read_text(encoding="utf-8")
    config_text = config_text.replace('provider = "deepseek"', 'provider = "fake"', 1)
    config_text = config_text.replace('model = "deepseek-chat"', 'model = "acceptance-model"', 1)
    config_path.write_text(config_text, encoding="utf-8")
    leader = _FakeLeader()
    session = ConversationSession(root=root, leader_gateway=leader)

    response = session.handle(SENSITIVE_MISSION_TEXT)

    assert response.kind == "semantic_clarification"
    assert leader.calls == 0
    state = session.store.load()
    for key in (
        "plans", "missions", "approvals", "messages", "jobs", "inbox",
        "conversation_preview_bindings",
    ):
        assert state.get(key, []) == []
    persisted = json.dumps(state, ensure_ascii=False)
    events = json.dumps(session.store.all_events(), ensure_ascii=False)
    assert SENSITIVE_MISSION_TEXT not in persisted + events
    assert "must-not-persist" not in persisted + events


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


def _semantic_session(tmp_path: Path) -> tuple[ConversationSession, dict[str, object]]:
    root = tmp_path / "semantic-confirm"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    config_path = root / ".agentdeck" / "config.toml"
    config_text = config_path.read_text(encoding="utf-8")
    config_text = config_text.replace('provider = "deepseek"', 'provider = "fake"', 1)
    config_text = config_text.replace('model = "deepseek-chat"', 'model = "fake-plan"', 1)
    config_path.write_text(config_text, encoding="utf-8")
    session = ConversationSession(root=root)
    preview = session.handle(SEMANTIC_MISSION_TEXT)
    assert preview.kind == "mission_preview"
    return session, preview.payload


def _semantic_confirmation_facts(
    session: ConversationSession, preview: dict[str, object]
) -> dict[str, object]:
    assert session.config is not None
    authority = extract_semantic_authority(
        SEMANTIC_MISSION_TEXT,
        selected_agent_ids=("planner", "reviewer"),
        step_count=4,
        phases=("implementation", "review", "revision", "acceptance"),
    )
    return {
        "control_kind": "mission_confirm",
        "project_root": str(session.root),
        "leader_provider": session.config.leader.provider,
        "leader_model": session.config.leader.model,
        "action_id": preview["mission_id"],
        "action_hash": preview["plan_hash"],
        "semantic_authority_hash": semantic_authority_hash(authority),
        "compiled_task_hashes": [
            "sha256:" + hashlib.sha256(step["task"].encode("utf-8")).hexdigest()
            for step in preview["plan"]["steps"]
        ],
        "policy_snapshot_hash": canonical_snapshot_hash(
            execution_policy_snapshot(session.config)
        ),
        "preview_generation": 1,
    }


def test_semantic_preview_binds_exact_ten_execution_facts(tmp_path: Path) -> None:
    session, preview = _semantic_session(tmp_path)
    state = session.store.load()
    binding = state["conversation_preview_bindings"][0]

    assert binding["execution_digest"] == execution_digest(
        _semantic_confirmation_facts(session, preview)
    )


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("control_kind", "other_control"),
        ("project_root", "/tmp/other"),
        ("leader_provider", "other"),
        ("leader_model", "other-model"),
        ("action_id", "mis_cafebabefeed"),
        ("action_hash", "sha256:" + "6" * 64),
        ("semantic_authority_hash", "sha256:" + "7" * 64),
        ("compiled_task_hashes", ["sha256:" + "8" * 64]),
        ("policy_snapshot_hash", "sha256:" + "9" * 64),
        ("preview_generation", 2),
    ],
)
def test_semantic_confirmation_rejects_each_stale_bound_fact_without_effects(
    tmp_path: Path, field: str, changed: object
) -> None:
    session, preview = _semantic_session(tmp_path)
    executions: list[str] = []
    session.preview_executor = lambda mission_id: executions.append(mission_id) or {
        "mission_id": mission_id,
        "status": "started",
    }
    state = session.store.load()
    if field == "control_kind":
        # The control discriminator has no mutable runtime source. Rebind only
        # this digest and prove confirm recomputes the canonical discriminator.
        facts = _semantic_confirmation_facts(session, preview)
        facts[field] = changed
        state["conversation_preview_bindings"][0]["execution_digest"] = (
            execution_digest(facts)
        )
        session.store.save(state)
    elif field == "action_id":
        state["conversation_preview_bindings"][0]["action_id"] = changed
        session.store.save(state)
    elif field == "action_hash":
        state["missions"][0]["plan_hash"] = changed
        session.store.save(state)
    elif field == "project_root":
        session.root = Path(str(changed))
    elif field == "leader_provider":
        path = session.root / ".agentdeck" / "config.toml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                'provider = "fake"', f'provider = "{changed}"', 1
            ),
            encoding="utf-8",
        )
    elif field == "leader_model":
        path = session.root / ".agentdeck" / "config.toml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                'model = "fake-plan"', f'model = "{changed}"', 1
            ),
            encoding="utf-8",
        )
    elif field == "semantic_authority_hash":
        plan = state["plans"][0]["plan"]
        authority = plan.get("semantic_authority")
        if authority is None:
            authority = extract_semantic_authority(
                SEMANTIC_MISSION_TEXT,
                selected_agent_ids=("planner", "reviewer"),
                step_count=4,
                phases=("implementation", "review", "revision", "acceptance"),
            )
            plan["semantic_authority"] = authority
        authority["source_message_hash"] = changed
        session.store.save(state)
    elif field == "compiled_task_hashes":
        state["plans"][0]["plan"]["steps"][0]["task"] += "drift"
        session.store.save(state)
    elif field == "policy_snapshot_hash":
        path = session.root / ".agentdeck" / "config.toml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                'approval_mode = "confirm"', 'approval_mode = "approve"', 1
            ),
            encoding="utf-8",
        )
    elif field == "preview_generation":
        state["missions"][0]["preview_generation"] = changed
        session.store.save(state)
    else:  # pragma: no cover - the closed matrix above owns every branch
        raise AssertionError(field)

    response = session.handle("确认执行当前预览")

    assert response.kind == "blocked"
    assert executions == []
    after = session.store.load()
    pending = session.store._conversation_summary(after)["pending_preview"]
    assert pending["preview_id"] == state["conversation_preview_bindings"][0]["preview_id"]
    mission = after["missions"][0]
    assert mission["status"] == "pending_confirmation"
    assert mission["confirmed_at"] is None
    for collection in (
        "attempts", "mission_attempts", "permission_requests", "messages", "jobs", "inbox",
    ):
        assert after.get(collection, []) == []
    assert not any(
        event["event_type"] == "mission_semantic_authority_frozen"
        for event in session.store.all_events()
    )


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ('provider = "fake"', 'provider = "deepseek"'),
        ('model = "fake-plan"', 'model = "changed-model"'),
        ('approval_mode = "confirm"', 'approval_mode = "approve"'),
    ],
)
def test_semantic_confirmation_reloads_disk_config_and_blocks_drift(
    tmp_path: Path, before: str, after: str
) -> None:
    session, _preview = _semantic_session(tmp_path)
    executions: list[str] = []
    session.preview_executor = lambda mission_id: executions.append(mission_id) or {
        "mission_id": mission_id,
        "status": "started",
    }
    config_path = session.root / ".agentdeck" / "config.toml"
    text = config_path.read_text(encoding="utf-8")
    assert before in text
    config_path.write_text(text.replace(before, after, 1), encoding="utf-8")

    response = session.handle("确认执行当前预览")

    assert response.kind == "blocked"
    assert executions == []
    state = session.store.load()
    assert state["missions"][0]["status"] == "pending_confirmation"
    assert state["missions"][0]["confirmed_at"] is None
    assert session.store._conversation_summary(state)["pending_preview"] is not None
    for collection in (
        "attempts", "mission_attempts", "permission_requests", "messages", "jobs", "inbox",
    ):
        assert state.get(collection, []) == []
    assert not any(
        event["event_type"] == "mission_semantic_authority_frozen"
        for event in session.store.all_events()
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
