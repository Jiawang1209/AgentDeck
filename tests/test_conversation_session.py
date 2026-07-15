from __future__ import annotations

from dataclasses import replace
from copy import deepcopy
from pathlib import Path

import pytest

from agentdeck.config import load_config, write_default_config
from agentdeck.conversation.leader_gateway import LeaderGateway, LeaderGatewayError
from agentdeck.conversation.session import ConversationSession
from agentdeck.mission_orchestration import LeaderMissionCandidate
from agentdeck.providers import LeaderPlanRequest
from agentdeck.providers import LeaderPlanResult
from agentdeck.providers.fake import FakeLeaderProvider
from agentdeck.providers.plan_schema import build_leader_generation_provenance
from agentdeck.state import StateStore
from agentdeck.semantic_authority import extract_semantic_authority


MESSAGE = "让 Codex 和 Claude 一人一句接龙百家姓，共2轮"
SEMANTIC_MESSAGE = (
    "Have planner and reviewer complete 4 steps strictly sequentially. "
    "The phases must be exactly implementation, review, revision, acceptance: "
    "First, planner creates artifact.txt with content exactly draft-v1 newline; "
    "Second, reviewer performs a read-only review and requires accepted-v2; "
    "Third, planner updates artifact.txt to exactly accepted-v2 newline; "
    "Fourth, reviewer performs read-only verification of the exact bytes. "
    "There are 4 steps.\n"
)


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


def test_semantic_clarification_never_calls_leader_or_creates_scheduling_state(
    tmp_path: Path,
) -> None:
    config, store = _project(tmp_path)
    gateway = _Gateway()
    session = ConversationSession(root=tmp_path, leader_gateway=gateway)

    response = session.handle(
        "让 planner 和 reviewer 完成2步：第一轮 planner 创建 artifact.txt；"
        "第二轮 reviewer 验收 artifact.txt"
    )

    assert response.kind == "semantic_clarification"
    card = response.payload["semantic_clarification_card"]
    assert set(card) == {
        "schema_version", "authority_hash", "unresolved_count", "question", "controls"
    }
    assert card["unresolved_count"] > 0
    assert 0 < len(card["question"].encode("utf-8")) <= 512
    assert {item["kind"] for item in card["controls"]} == {"clarify", "inspect"}
    assert gateway.calls == 0
    state = store.load()
    for key in ("plans", "missions", "approvals", "messages", "jobs", "inbox"):
        assert state.get(key, []) == []
    assert state["conversation_preview_bindings"] == []


def test_exact_live_shaped_request_reaches_one_semantic_preview(tmp_path: Path) -> None:
    _config, store = _project(tmp_path)
    session = ConversationSession(root=tmp_path)

    response = session.handle(SEMANTIC_MESSAGE)

    assert response.kind == "mission_preview"
    state = store.load()
    assert len(state["plans"]) == len(state["missions"]) == 1
    assert len(state["conversation_preview_bindings"]) == 1
    assert any(
        event["event_type"] == "semantic_authority_extracted"
        for event in store.all_events()
    )


def test_session_and_gateway_deep_copy_exact_authority_through_provider_request(
    tmp_path: Path,
) -> None:
    _config, _store = _project(tmp_path)
    expected = extract_semantic_authority(
        SEMANTIC_MESSAGE,
        selected_agent_ids=("planner", "reviewer"),
        step_count=4,
        phases=("implementation", "review", "revision", "acceptance"),
    )
    observations: dict[str, object] = {}

    class RecordingProvider(FakeLeaderProvider):
        def plan_result(self, request):
            observations["provider_authority"] = deepcopy(request.semantic_authority)
            observations["provider_authority_id"] = id(request.semantic_authority)
            result = super().plan_result(request)
            request.semantic_authority["requirements"].clear()
            return result

    inner = LeaderGateway(provider_factory=lambda _name: RecordingProvider())

    class RecordingGateway:
        def generate_mission(self, request, cancel):
            observations["leader_request_authority"] = deepcopy(
                request.semantic_authority
            )
            observations["leader_request_authority_id"] = id(
                request.semantic_authority
            )
            candidate = inner.generate_mission(request, cancel)
            observations["candidate_authority"] = deepcopy(
                candidate.semantic_authority
            )
            observations["candidate_plan"] = deepcopy(candidate.plan)
            return candidate

    response = ConversationSession(
        root=tmp_path, leader_gateway=RecordingGateway()
    ).handle(SEMANTIC_MESSAGE)

    assert response.kind == "mission_preview"
    assert observations["leader_request_authority"] == expected
    assert observations["provider_authority"] == expected
    assert observations["candidate_authority"] == expected
    assert observations["leader_request_authority_id"] != observations["provider_authority_id"]
    assert expected["requirements"]
    state = _store.load()
    persisted_plan = state["plans"][0]["plan"]
    assert set(persisted_plan) == {"goal", "summary", "steps"}
    assert all(
        set(step) == {
            "step", "agent_id", "role", "task", "risk", "requires_approval",
        }
        for step in persisted_plan["steps"]
    )
    assert [step["task"] for step in persisted_plan["steps"]] == [
        step["task"] for step in observations["candidate_plan"]["steps"]
    ]
    rendered_records = str({"plans": state["plans"], "missions": state["missions"]})
    for semantic_key in ("semantic_authority", "semantic_steps", "semantic_step_hash"):
        assert semantic_key not in rendered_records


def test_double_semantic_failure_writes_only_turn_terminal_and_safe_audit(
    tmp_path: Path,
) -> None:
    _config, store = _project(tmp_path)

    class FailedGateway:
        calls = 0

        def generate_mission(self, request, _cancel):
            self.calls += 1
            assert request.semantic_authority is not None
            raise LeaderGatewayError(
                "schema", "semantic_candidate_missing_requirement",
                attempt_count=2, constraint_mode="local",
            )

    gateway = FailedGateway()
    response = ConversationSession(root=tmp_path, leader_gateway=gateway).handle(
        SEMANTIC_MESSAGE
    )

    assert response.kind == "failed"
    assert gateway.calls == 1
    state = store.load()
    for key in (
        "plans", "missions", "approvals", "messages", "jobs", "inbox",
        "conversation_preview_bindings",
    ):
        assert state.get(key, []) == []
    assert len(state["conversation_sessions"]) == 1
    assert len(state["conversation_turns"]) == 1
    turn_id = state["conversation_turns"][0]["turn_id"]
    turn_transitions = [
        item for item in state["conversation_state_transitions"]
        if item["entity_type"] == "turn" and item["entity_id"] == turn_id
    ]
    assert [item["to_state"] for item in turn_transitions] == [
        "created", "routing", "waiting_leader", "failed"
    ]
    assert all(
        item["to_state"] not in {"presenting_preview", "waiting_confirmation"}
        for item in state["conversation_state_transitions"]
    )
    semantic_events = [
        event for event in store.all_events()
        if event["event_type"] in {
            "semantic_authority_extracted", "leader_semantic_candidate_rejected"
        }
    ]
    assert [event["event_type"] for event in semantic_events] == [
        "semantic_authority_extracted", "leader_semantic_candidate_rejected"
    ]
    assert set(semantic_events[0]["payload"]) == {
        "schema_version", "authority_hash", "requirement_count", "unresolved_count"
    }
    assert semantic_events[1]["payload"] == {
        "code": "semantic_candidate_missing_requirement", "attempt_count": 2
    }
    rendered = str([event["payload"] for event in semantic_events])
    for forbidden in (SEMANTIC_MESSAGE, "artifact.txt", "draft-v1", "prompt", "stdout", "stderr", "/Users/", "secret"):
        assert forbidden not in rendered


def test_semantic_landing_rejection_emits_closed_safe_audit_and_zero_scheduling(
    tmp_path: Path,
) -> None:
    _config, store = _project(tmp_path)
    inner = LeaderGateway(provider_factory=lambda _name: FakeLeaderProvider())

    class TamperingGateway:
        def generate_mission(self, request, cancel):
            candidate = inner.generate_mission(request, cancel)
            plan = deepcopy(candidate.plan)
            plan["steps"][0]["task"] += " task-tamper-secret"
            return replace(candidate, plan=plan)

    response = ConversationSession(
        root=tmp_path, leader_gateway=TamperingGateway()
    ).handle(SEMANTIC_MESSAGE)

    assert response.kind == "failed"
    state = store.load()
    for key in (
        "plans", "missions", "approvals", "messages", "jobs", "inbox",
        "conversation_preview_bindings",
    ):
        assert state.get(key, []) == []
    rejected = [
        event for event in store.all_events()
        if event["event_type"] == "leader_semantic_candidate_rejected"
    ]
    assert [event["payload"] for event in rejected] == [
        {"code": "semantic_compilation_drift", "attempt_count": 1}
    ]
    rendered = str(rejected)
    for forbidden in (
        SEMANTIC_MESSAGE, "artifact.txt", "draft-v1", "task-tamper-secret"
    ):
        assert forbidden not in rendered


def test_session_rejects_gateway_rebased_source_hash_before_scheduling(
    tmp_path: Path,
) -> None:
    _config, store = _project(tmp_path)
    inner = LeaderGateway(provider_factory=lambda _name: FakeLeaderProvider())

    class RebasedGateway:
        def generate_mission(self, request, cancel):
            candidate = inner.generate_mission(request, cancel)
            frozen = deepcopy(candidate.semantic_authority)
            frozen["source_message_hash"] = f"sha256:{'f' * 64}"
            plan = deepcopy(candidate.plan)
            plan["semantic_authority"] = deepcopy(frozen)
            provenance = build_leader_generation_provenance(
                request=LeaderPlanRequest(
                    task=request.planning_task,
                    config=request.config,
                    model=request.config.leader.model,
                    skill_context=request.skill_context,
                    selected_agent_ids=request.selected_agent_ids,
                    step_count=request.step_count,
                    timeout_seconds=request.timeout_seconds,
                    semantic_authority=deepcopy(frozen),
                ),
                provider="fake",
                constraint_mode="local",
            )
            return replace(
                candidate,
                plan=plan,
                semantic_authority=frozen,
                leader_generation=provenance,
            )

    response = ConversationSession(
        root=tmp_path, leader_gateway=RebasedGateway()
    ).handle(SEMANTIC_MESSAGE)

    assert response.kind == "failed"
    state = store.load()
    for key in (
        "plans", "missions", "approvals", "messages", "jobs", "inbox",
        "conversation_preview_bindings",
    ):
        assert state.get(key, []) == []
    assert [
        event["payload"] for event in store.all_events()
        if event["event_type"] == "leader_semantic_candidate_rejected"
    ] == [{"code": "semantic_compilation_drift", "attempt_count": 1}]


def test_session_rejects_hostile_worker_tuple_without_equality_hook(
    tmp_path: Path,
) -> None:
    _config, store = _project(tmp_path)
    inner = LeaderGateway(provider_factory=lambda _name: FakeLeaderProvider())
    touched: list[str] = []

    class HostileTuple(tuple):
        def __eq__(self, _other):
            touched.append("eq")
            raise AssertionError("hostile equality executed")

    class HostileGateway:
        def generate_mission(self, request, cancel):
            candidate = inner.generate_mission(request, cancel)
            return replace(
                candidate,
                selected_agent_ids=HostileTuple(candidate.selected_agent_ids),
            )

    response = ConversationSession(
        root=tmp_path, leader_gateway=HostileGateway()
    ).handle(SEMANTIC_MESSAGE)

    assert response.kind == "failed"
    assert touched == []
    assert store.load()["plans"] == []
    assert store.load()["missions"] == []


def test_legal_project_local_proposal_reaches_semantic_preview(tmp_path: Path) -> None:
    _config, store = _project(tmp_path)

    class ProposalProvider(FakeLeaderProvider):
        @staticmethod
        def _semantic_candidate(authority, *, selected_agent_ids, roles, step_count):
            candidate = FakeLeaderProvider._semantic_candidate(
                authority,
                selected_agent_ids=selected_agent_ids,
                roles=roles,
                step_count=step_count,
            )
            candidate["steps"][1]["proposed_effects"] = [
                {
                    "target": "notes/report.txt",
                    "operation": "create",
                    "sensitivity": "ordinary",
                }
            ]
            return candidate

    response = ConversationSession(
        root=tmp_path,
        leader_gateway=LeaderGateway(
            provider_factory=lambda _name: ProposalProvider()
        ),
    ).handle(SEMANTIC_MESSAGE)

    assert response.kind == "mission_preview"
    assert len(store.load()["plans"]) == len(store.load()["missions"]) == 1


def test_regeneration_audit_uses_generation_provenance_not_diagnostic_shape(
    tmp_path: Path,
) -> None:
    _config, store = _project(tmp_path)

    class ActualRegeneratedProvider(FakeLeaderProvider):
        def plan_result(self, request):
            result = super().plan_result(request)
            return LeaderPlanResult(
                plan=result.plan,
                leader_generation=build_leader_generation_provenance(
                    request=request,
                    provider="fake",
                    constraint_mode="local",
                    attempt_count=2,
                ),
                semantic_diagnostics=(
                    {
                        "code": "semantic_candidate_missing_requirement",
                        "attempt_count": 1,
                        "regeneration_used": False,
                    },
                ),
            )

    response = ConversationSession(
        root=tmp_path,
        leader_gateway=LeaderGateway(
            provider_factory=lambda _name: ActualRegeneratedProvider()
        ),
    ).handle(SEMANTIC_MESSAGE)

    assert response.kind == "mission_preview"
    assert [
        event["payload"] for event in store.all_events()
        if event["event_type"] == "leader_semantic_candidate_rejected"
    ] == [{"code": "semantic_candidate_missing_requirement", "attempt_count": 1}]
    assert [
        event["payload"] for event in store.all_events()
        if event["event_type"] == "leader_semantic_candidate_regenerated"
    ] == [{"attempt_count": 2}]


@pytest.mark.parametrize(
    "diagnostics",
    [
        ({"code": "SECRET_DO_NOT_ECHO", "attempt_count": 1, "regeneration_used": False},),
        [{"code": "semantic_candidate_missing_requirement", "attempt_count": 1, "regeneration_used": False}],
        ({"code": "semantic_candidate_missing_requirement", "attempt_count": 2, "regeneration_used": False},),
        ({"code": "semantic_candidate_missing_requirement", "attempt_count": 1, "regeneration_used": True},),
        ({"code": "semantic_candidate_schema_invalid", "attempt_count": 1, "regeneration_used": False},),
    ],
)
def test_session_rejects_unvalidated_semantic_diagnostics_without_echo(
    tmp_path: Path, diagnostics: object,
) -> None:
    _config, store = _project(tmp_path)
    inner = LeaderGateway(provider_factory=lambda _name: FakeLeaderProvider())

    class InjectingGateway:
        def generate_mission(self, request, cancel):
            candidate = inner.generate_mission(request, cancel)
            return replace(candidate, semantic_diagnostics=diagnostics)

    response = ConversationSession(
        root=tmp_path, leader_gateway=InjectingGateway()
    ).handle(SEMANTIC_MESSAGE)

    assert response.kind == "failed"
    state = store.load()
    assert state["plans"] == state["missions"] == []
    assert state["conversation_preview_bindings"] == []
    rejected = [
        event for event in store.all_events()
        if event["event_type"] == "leader_semantic_candidate_rejected"
    ]
    assert [event["payload"] for event in rejected] == [
        {"code": "semantic_compilation_drift", "attempt_count": 1}
    ]
    assert "SECRET_DO_NOT_ECHO" not in str(rejected)


def test_session_rejects_diagnostic_container_subclasses_without_hooks(
    tmp_path: Path,
) -> None:
    _config, store = _project(tmp_path)
    inner = LeaderGateway(provider_factory=lambda _name: FakeLeaderProvider())
    touched: list[str] = []

    class HostileTuple(tuple):
        def __iter__(self):
            touched.append("iter")
            raise AssertionError("hostile tuple iterated")

    class HostileDict(dict):
        def get(self, *_args, **_kwargs):
            touched.append("get")
            raise AssertionError("hostile dict accessed")

    malicious_values = (
        HostileTuple(({"code": "x", "attempt_count": 1, "regeneration_used": False},)),
        (HostileDict({"code": "x", "attempt_count": 1, "regeneration_used": False}),),
    )
    for diagnostics in malicious_values:
        class InjectingGateway:
            def generate_mission(self, request, cancel):
                candidate = inner.generate_mission(request, cancel)
                return replace(candidate, semantic_diagnostics=diagnostics)

        response = ConversationSession(
            root=tmp_path, leader_gateway=InjectingGateway()
        ).handle(SEMANTIC_MESSAGE)
        assert response.kind == "failed"

    assert touched == []
    assert store.load()["plans"] == []
    assert store.load()["missions"] == []


def test_session_rejects_legacy_diagnostic_injection_with_safe_audit(
    tmp_path: Path,
) -> None:
    _config, store = _project(tmp_path)
    inner = LeaderGateway(provider_factory=lambda _name: FakeLeaderProvider())

    class LegacyInjectingGateway:
        def generate_mission(self, request, cancel):
            candidate = inner.generate_mission(request, cancel)
            return replace(
                candidate,
                semantic_diagnostics=(
                    {"code": "SECRET_LEGACY", "attempt_count": 1, "regeneration_used": False},
                ),
            )

    response = ConversationSession(
        root=tmp_path, leader_gateway=LegacyInjectingGateway()
    ).handle(MESSAGE)

    assert response.kind == "failed"
    assert store.load()["plans"] == []
    assert store.load()["missions"] == []
    rejected = [
        event for event in store.all_events()
        if event["event_type"] == "leader_semantic_candidate_rejected"
    ]
    assert [event["payload"] for event in rejected] == [
        {"code": "semantic_compilation_drift", "attempt_count": 1}
    ]
    assert "SECRET_LEGACY" not in str(rejected)


def test_session_rejects_attempt_two_without_exact_attempt_one_diagnostic(
    tmp_path: Path,
) -> None:
    _config, store = _project(tmp_path)
    inner = LeaderGateway(provider_factory=lambda _name: FakeLeaderProvider())

    class InconsistentGateway:
        def generate_mission(self, request, cancel):
            candidate = inner.generate_mission(request, cancel)
            provenance = build_leader_generation_provenance(
                request=LeaderPlanRequest(
                    task=request.planning_task,
                    config=request.config,
                    model=request.config.leader.model,
                    skill_context=request.skill_context,
                    selected_agent_ids=request.selected_agent_ids,
                    step_count=request.step_count,
                    timeout_seconds=request.timeout_seconds,
                    semantic_authority=request.semantic_authority,
                ),
                provider="fake",
                constraint_mode="local",
                attempt_count=2,
            )
            return replace(
                candidate, leader_generation=provenance, semantic_diagnostics=()
            )

    response = ConversationSession(
        root=tmp_path, leader_gateway=InconsistentGateway()
    ).handle(SEMANTIC_MESSAGE)

    assert response.kind == "failed"
    assert store.load()["plans"] == []
    assert store.load()["missions"] == []


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
