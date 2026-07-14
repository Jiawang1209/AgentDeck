from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path

import pytest

from agentdeck.config import load_config, write_default_config
from agentdeck.conversation.models import (
    ConversationMutation,
    build_conversation_record,
    build_conversation_transition,
)
from agentdeck.mission_orchestration import (
    LeaderMissionCandidate,
    MissionPreviewError,
    _mission_preview_mutation_is_durable,
    create_mission_preview,
    create_mission_preview_from_candidate,
    requested_mission_step_count,
)
from agentdeck.models import AgentSpec
from agentdeck.models import EventRecord
from agentdeck.providers import LeaderPlanRequest
from agentdeck.providers.plan_schema import build_leader_generation_provenance
from agentdeck.state import StateStore


MESSAGE = "让 Codex 和 Claude 一人一句接龙百家姓，共8轮"


def _plan() -> dict[str, object]:
    return {
        "goal": "完成八轮接龙",
        "summary": "Codex 与 Claude 严格串行交替执行。",
        "steps": [
            {
                "step": step,
                "agent_id": "planner" if step % 2 else "reviewer",
                "role": "planning" if step % 2 else "review",
                "task": f"完成接龙第 {step} 轮",
                "risk": "requires human review before dispatch",
                "requires_approval": True,
            }
            for step in range(1, 9)
        ],
    }


def _project(tmp_path: Path) -> tuple[Path, object, StateStore]:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    config_path = root / ".agentdeck" / "config.toml"
    text = config_path.read_text(encoding="utf-8")
    text = text.replace('provider = "deepseek"', 'provider = "fake"', 1)
    text = text.replace('model = "deepseek-chat"', 'model = "fake-plan"', 1)
    config_path.write_text(text, encoding="utf-8")
    return root, load_config(root), StateStore(root)


def _candidate(plan: dict[str, object] | None = None) -> LeaderMissionCandidate:
    return LeaderMissionCandidate(
        provider="fake",
        model="fake-plan",
        user_message=MESSAGE,
        plan=_plan() if plan is None else plan,
        timeout_seconds=180,
    )


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_candidate_path_does_not_call_any_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, config, store = _project(tmp_path)
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}"
    )
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.LeaderOrchestrator.plan",
        lambda *_args, **_kwargs: pytest.fail("candidate path must not call provider"),
    )

    payload = create_mission_preview_from_candidate(
        config=config,
        store=store,
        candidate=_candidate(),
    )

    assert payload["mode"] == "mission_preview"
    state = store.load()
    assert len(state["plans"]) == 1
    assert len(state["missions"]) == 1
    assert state["conversation_event_outbox"] == []
    assert [event["event_type"] for event in store.all_events()] == [
        "mission_preview_created"
    ]


def test_candidate_frozen_authority_survives_redacted_message_and_excludes_third_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, config, store = _project(tmp_path)
    config = replace(
        config,
        agents=(
            *config.agents,
            AgentSpec("auditor", "audit", "codex-cli", "codex"),
        ),
    )
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}"
    )
    candidate = LeaderMissionCandidate(
        provider="fake",
        model="fake-plan",
        user_message="[persisted provenance redacted]",
        plan=_plan(),
        timeout_seconds=180,
        selected_agent_ids=("planner", "reviewer"),
        step_count=8,
        leader_generation=build_leader_generation_provenance(
            request=LeaderPlanRequest(
                task="[persisted provenance redacted]",
                config=config,
                model="fake-plan",
                selected_agent_ids=("planner", "reviewer"),
                step_count=8,
                timeout_seconds=180,
            ),
            provider="fake",
            constraint_mode="local",
        ),
    )

    payload = create_mission_preview_from_candidate(
        config=config,
        store=store,
        candidate=candidate,
    )

    assert [item["agent_id"] for item in payload["selected_agents"]] == [
        "planner",
        "reviewer",
    ]
    assert len(payload["plan"]["steps"]) == 8


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("共2轮", 2),
        ("严格完成两步审阅，共2轮", 2),
        ("共十轮", 10),
        ("严格四步骤", 4),
        ("恰好四个串行步骤", 4),
        ("按3步完成", 3),
        ("按三步骤完成", 3),
        ("use exactly four steps", 4),
        ("use 4 steps", 4),
        ("use ten steps", 10),
    ],
)
def test_requested_mission_step_count_supports_bounded_explicit_phrases(
    message: str, expected: int
) -> None:
    assert requested_mission_step_count(message, default=8) == expected


@pytest.mark.parametrize(
    "message",
    [
        "version v4 steps are documented",
        "four agents review one step",
        "第四步骤的标题",
        "第贰步骤的标题",
        "step4 is a label",
        "there are four possible plans",
        "共同讨论轮值安排",
    ],
)
def test_requested_mission_step_count_avoids_ambiguous_false_positives(
    message: str,
) -> None:
    assert requested_mission_step_count(message, default=8) == 8


@pytest.mark.parametrize(
    "message",
    [
        "共0轮",
        "共1轮",
        "共65轮",
        "共九十九轮",
        "共两百轮",
        "共壹佰轮",
        "共64.0轮",
        "共+64轮",
        "共－2轮",
        "共" + "9" * 5000 + "轮",
        "按一百步骤完成",
        "按贰步骤完成",
        "严格2.0步骤完成",
        "先共2轮，再共65轮",
        "use 2 steps, then use 65 steps",
        "共2轮 then use 65 steps",
        "use 65 steps 然后共2轮",
        "共2轮 then use 4 steps",
        "use 4 steps 然后共2轮",
    ],
)
def test_requested_mission_step_count_rejects_explicit_unsafe_bounds(
    message: str,
) -> None:
    with pytest.raises(ValueError, match="mission step count invalid"):
        requested_mission_step_count(message, default=8)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("共2轮 then use 2 steps", 2),
        ("use 4 steps，然后共4轮，再按四步骤完成", 4),
    ],
)
def test_requested_mission_step_count_allows_repeated_equal_cross_language_counts(
    message: str, expected: int
) -> None:
    assert requested_mission_step_count(message, default=8) == expected


@pytest.mark.parametrize("channel", ["outbox", "journal"])
def test_exact_preview_proof_rejects_duplicate_event_within_one_channel(
    tmp_path: Path, channel: str
) -> None:
    _root, _config, store = _project(tmp_path)
    event = EventRecord.create("conversation_preview_presented", {"proof": "exact"})
    plan = {"plan_id": "pln_exact"}
    mutation = ConversationMutation(
        append_records={"plans": (plan,)},
        events=(event,),
    )
    state = store.load()
    state["plans"].append(plan)
    if channel == "outbox":
        state["conversation_event_outbox"] = [
            asdict(event),
            asdict(event),
        ]
        store.save(state)
    else:
        store.save(state)
        store.append_event(event)
        store.append_event(event)

    assert _mission_preview_mutation_is_durable(store, mutation) is False


def test_exact_preview_proof_accepts_one_identical_event_per_durable_channel(
    tmp_path: Path,
) -> None:
    _root, _config, store = _project(tmp_path)
    event = EventRecord.create("conversation_preview_presented", {"proof": "exact"})
    plan = {"plan_id": "pln_exact"}
    mutation = ConversationMutation(
        append_records={"plans": (plan,)},
        events=(event,),
    )
    state = store.load()
    state["plans"].append(plan)
    state["conversation_event_outbox"] = [asdict(event)]
    store.save(state)
    store.append_event(event)

    assert _mission_preview_mutation_is_durable(store, mutation) is True


def test_invalid_candidate_is_full_tree_zero_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config, store = _project(tmp_path)
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}"
    )
    before = _tree(root)
    invalid = _plan()
    invalid["steps"][0]["requires_approval"] = False  # type: ignore[index]

    with pytest.raises(MissionPreviewError, match="mission preview plan invalid"):
        create_mission_preview_from_candidate(
            config=config,
            store=store,
            candidate=_candidate(invalid),
        )

    assert _tree(root) == before
    assert store.load()["plans"] == []
    assert store.load()["missions"] == []


class _CountingProvider:
    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def plan(self, _request: LeaderPlanRequest) -> dict[str, object]:
        self.calls += 1
        return _plan()


def test_legacy_provider_path_calls_provider_once_and_uses_atomic_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, config, store = _project(tmp_path)
    provider = _CountingProvider()
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}"
    )
    commits = 0
    real_commit = store.commit_conversation_mutation

    def counting_commit(mutation):
        nonlocal commits
        commits += 1
        return real_commit(mutation)

    monkeypatch.setattr(store, "commit_conversation_mutation", counting_commit)

    payload = create_mission_preview(
        config=config,
        store=store,
        provider=provider,
        user_message=MESSAGE,
        timeout_seconds=180,
    )

    assert payload["mode"] == "mission_preview"
    assert provider.calls == 1
    assert commits == 1


def test_candidate_commits_conversation_lineage_with_plan_and_mission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, config, store = _project(tmp_path)
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}"
    )
    conversation = build_conversation_record(
        "c1", created_at="2026-07-13T00:00:00+00:00"
    )
    transition = build_conversation_transition(
        transition_id="x1",
        conversation_id="c1",
        entity_type="conversation",
        entity_id="c1",
        from_state=None,
        to_state="created",
        reason="foreground_started",
        created_at="2026-07-13T00:00:00+00:00",
    )

    payload = create_mission_preview_from_candidate(
        config=config,
        store=store,
        candidate=_candidate(),
        conversation_mutation=ConversationMutation(
            append_records={
                "conversation_sessions": (conversation,),
                "conversation_state_transitions": (transition,),
            }
        ),
    )

    state = store.load()
    assert payload["mission_id"] == state["missions"][0]["mission_id"]
    assert state["conversation_sessions"] == [conversation]
    assert state["conversation_state_transitions"] == [transition]
    assert len(state["plans"]) == len(state["missions"]) == 1
