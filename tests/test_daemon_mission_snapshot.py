from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentdeck import cli
from agentdeck import state as state_module
from agentdeck.config import load_config, write_default_config
from agentdeck.daemon.recovery import RecoveryError, reconcile_startup
from agentdeck.mission_orchestration import (
    MissionRunError,
    attempt_dispatch_key,
    build_execution_snapshot,
    canonical_hash,
    confirm_mission_for_daemon,
    create_mission_preview,
    prepare_attempt,
)
from agentdeck.providers import LeaderPlanRequest
from agentdeck.daemon.scheduler import SchedulerDecision
from agentdeck.daemon.service import (
    DaemonTmuxWorkerStarter,
    ServiceError,
    scheduler_facts_from_store,
)
from agentdeck.state import MissionStateError, StateStore, worker_runtime_identity_hash


MESSAGE = "让 Codex 和 Claude 严格串行完成两步审阅，共2轮"


class TwoStepProvider:
    name = "fake"

    def plan(self, request: LeaderPlanRequest) -> dict[str, object]:
        del request
        return {
            "goal": "完成实现和审阅 SECRET-GOAL",
            "summary": "两个 Worker 严格串行，password=summary-secret。",
            "steps": [
                {
                    "step": 1,
                    "agent_id": "planner",
                    "role": "planning",
                    "task": "实现任务，不要泄露 password=hunter2",
                    "risk": "requires human review before dispatch",
                    "requires_approval": True,
                },
                {
                    "step": 2,
                    "agent_id": "reviewer",
                    "role": "review",
                    "task": "审阅任务",
                    "risk": "requires human review before dispatch",
                    "requires_approval": True,
                },
            ],
            "approval_required": True,
            "dispatch_ready": False,
            "declared_tests": ["pytest tests/test_feature.py -q"],
            "acceptance_criteria": ["implementation and review both complete"],
        }


def _seed(
    tmp_path: Path,
    monkeypatch,
    *,
    project_memory: str | None = None,
    retry_limit: int = 0,
):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    config_path = root / ".agentdeck" / "config.toml"
    text = config_path.read_text(encoding="utf-8")
    text = text.replace('provider = "deepseek"', 'provider = "fake"', 1)
    text = text.replace('model = "deepseek-chat"', 'model = "fake-plan"', 1)
    text = text.replace(
        'agent_id = "planner"\nrole = "planning"',
        'agent_id = "planner"\nrole = "planning"\ntransport = "acp"\n'
        'transport_command = ["adapter", "--token", "SECRET"]',
        1,
    )
    config_path.write_text(text, encoding="utf-8")
    if project_memory is not None:
        memory_dir = root / ".agentdeck" / "memory"
        memory_dir.mkdir()
        (memory_dir / "project.md").write_text(project_memory, encoding="utf-8")
    config = load_config(root)
    store = StateStore(root)
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}"
    )
    preview = create_mission_preview(
        config=config,
        store=store,
        provider=TwoStepProvider(),
        user_message=MESSAGE,
        timeout_seconds=180,
        retry_limit=retry_limit,
    )
    return root, config, store, preview


def _state_bytes(store: StateStore) -> bytes:
    return store.state_path.read_bytes()


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _set_attempt_post_admission(
    state: dict[str, object], target_state: str, *, index: int = -1
) -> None:
    attempt = state["mission_attempts"][index]  # type: ignore[index]
    claim_id = "adm_" + attempt["attempt_id"].removeprefix("mat_")
    attempt.update(
        {
            "admission_claim_id": claim_id,
            "state": target_state,
            "receipt_summary": "admitted",
            "terminal_reason": (
                "retryable worker failure" if target_state == "failed" else target_state
            ),
        }
    )
    outbox = state["protocol_event_outbox"]  # type: ignore[index]
    base_payload = {
        "attempt_id": attempt["attempt_id"],
        "mission_id": attempt["mission_id"],
        "step_id": attempt["step_id"],
        "dispatch_key": attempt["dispatch_key"],
        "admission_claim_id": claim_id,
    }
    outbox.extend(
        [
            {
                "event_id": state_module.new_id("evt"),
                "event_type": "mission_attempt_admission_claimed",
                "created_at": attempt["updated_at"],
                "payload": dict(base_payload),
            },
            {
                "event_id": state_module.new_id("evt"),
                "event_type": "mission_attempt_submitted",
                "created_at": attempt["updated_at"],
                "payload": {**base_payload, "reason": None},
            },
        ]
    )


def test_worker_runtime_identity_is_hashed_into_frozen_authority(
    tmp_path, monkeypatch
) -> None:
    root, config, store, preview = _seed(tmp_path, monkeypatch)
    mission = store.mission_by_id(preview["mission_id"])
    snapshot = build_execution_snapshot(
        config, mission, store.plan_by_id(preview["plan_id"]),
        state_module.execution_policy_snapshot(config),
    )
    workers = {item["agent_id"]: item for item in snapshot["workers"]}
    assert workers["planner"]["runtime_identity_hash"] == worker_runtime_identity_hash(
        next(item for item in config.agents if item.agent_id == "planner"),
        config.runtime,
    )
    persisted = json.dumps(snapshot, sort_keys=True)
    assert '"--token"' not in persisted
    assert "SECRET" not in persisted
    assert "你是 AgentDeck 的规划 Agent" not in persisted


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('command = "codex"', 'command = "changed-worker-command"'),
        ('session_name = "agentdeck"', 'session_name = "changed-session"'),
    ],
)
def test_confirmation_rejects_worker_runtime_drift_after_preview(
    tmp_path, monkeypatch, old: str, new: str
) -> None:
    root, _config, store, preview = _seed(tmp_path, monkeypatch)
    path = root / ".agentdeck" / "config.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(old, new, 1),
        encoding="utf-8",
    )
    before = _tree_bytes(root)
    with pytest.raises(MissionStateError, match="execution authority drift"):
        store.freeze_mission_execution(
            preview["mission_id"],
            confirmed_at=store.mission_by_id(preview["mission_id"])["created_at"],
        )
    assert _tree_bytes(root) == before


@pytest.mark.parametrize(
    "effect_error",
    [RuntimeError("pane id parse failed"), subprocess.TimeoutExpired("tmux", 1)],
)
def test_claimed_tmux_start_is_ambiguous_and_never_spawned_twice_on_exception(
    tmp_path, monkeypatch, effect_error: Exception
) -> None:
    root, config, store, preview = _seed(tmp_path, monkeypatch)
    result = confirm_mission_for_daemon(
        config=config, store=store, mission_id=preview["mission_id"]
    )
    store.admit_mission_execution(
        preview["mission_id"], snapshot_hash=result["snapshot_hash"]
    )
    state = store.load()
    state["missions"][0].update({"current_step": 1, "status": "running"})
    store.save(state)
    calls: list[str] = []

    class Backend:
        def create_session(self, _config):
            calls.append("session")

        def spawn_agent(self, _config, _agent, _cwd):
            calls.append("spawn")
            raise effect_error

    starter = DaemonTmuxWorkerStarter(store, Backend())
    decision = SchedulerDecision(
        "start_worker", preview["mission_id"], "step_2", None, None
    )
    with pytest.raises(type(effect_error)):
        starter(decision)
    persisted = store.load()["mission_worker_starts"]
    assert len(persisted) == 1
    assert persisted[0]["state"] == "claimed"
    with pytest.raises(ValueError, match="already recorded"):
        starter(decision)
    assert calls == ["session", "spawn"]


def test_tmux_start_claim_rejects_negative_cursor_with_full_tree_zero_write(
    tmp_path, monkeypatch
) -> None:
    root, config, store, preview = _seed(tmp_path, monkeypatch)
    result = confirm_mission_for_daemon(
        config=config, store=store, mission_id=preview["mission_id"]
    )
    store.admit_mission_execution(
        preview["mission_id"], snapshot_hash=result["snapshot_hash"]
    )
    state = store.load()
    state["missions"][0]["current_step"] = -1
    store.save(state)
    before = _tree_bytes(root)

    with pytest.raises(ValueError, match="step authority is invalid"):
        store.claim_mission_worker_start(
            mission_id=preview["mission_id"],
            step_id="step_2",
            agent_id="reviewer",
        )

    assert _tree_bytes(root) == before


@pytest.mark.parametrize(
    ("agent_id", "step_id", "cursor", "old", "new"),
    [
        ("planner", "step_1", 0, 'command = "codex"', 'command = "changed"'),
        (
            "planner", "step_1", 0,
            'transport_command = ["adapter", "--token", "SECRET"]',
            'transport_command = ["adapter", "--changed"]',
        ),
        (
            "planner", "step_1", 0,
            'role_prompt = "你是 AgentDeck 的规划 Agent，负责需求澄清、任务拆解、架构方案和风险识别。"',
            'role_prompt = "changed prompt"',
        ),
        (
            "reviewer", "step_2", 1,
            'session_name = "agentdeck"',
            'session_name = "changed-session"',
        ),
    ],
)
def test_post_confirmation_worker_runtime_drift_blocks_scheduler_and_transport(
    tmp_path, monkeypatch, agent_id: str, step_id: str, cursor: int,
    old: str, new: str,
) -> None:
    root, config, store, preview = _seed(tmp_path, monkeypatch)
    result = confirm_mission_for_daemon(
        config=config, store=store, mission_id=preview["mission_id"]
    )
    store.admit_mission_execution(
        preview["mission_id"], snapshot_hash=result["snapshot_hash"]
    )
    state = store.load()
    state["missions"][0].update({"current_step": cursor, "status": "running"})
    store.save(state)
    config_path = root / ".agentdeck" / "config.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(old, new, 1),
        encoding="utf-8",
    )
    runtime_calls: list[str] = []
    backend = SimpleNamespace(
        pane_exists=lambda *_args: runtime_calls.append("probe") or True,
        capture_output=lambda *_args, **_kwargs: "ready",
    )

    facts = scheduler_facts_from_store(store, tmux_backend=backend)
    assert facts is not None
    assert facts.worker_ready is False
    assert facts.blocker == "Worker execution authority drift"
    assert runtime_calls == []

    monkeypatch.setattr(
        cli, "AcpWorkerTransport",
        lambda **_kwargs: runtime_calls.append("acp-transport"),
    )
    monkeypatch.setattr(
        cli, "TmuxWorkerTransport",
        lambda **_kwargs: runtime_calls.append("tmux-transport"),
    )
    attempt = {
        "mission_id": preview["mission_id"],
        "step_id": step_id,
        "agent_id": agent_id,
        "configured_transport": "acp" if agent_id == "planner" else "tmux",
        "dispatch_key": "dsp_" + "d" * 32,
    }
    with pytest.raises(ServiceError, match="execution authority drift"):
        cli._daemon_worker_transport_for(
            store=store,
            service=SimpleNamespace(),
            root=root,
            attempt=attempt,
        )
    assert runtime_calls == []


def test_post_confirmation_task_drift_constructs_no_worker_transport(
    tmp_path, monkeypatch
) -> None:
    root, config, store, preview = _seed(tmp_path, monkeypatch)
    result = confirm_mission_for_daemon(
        config=config, store=store, mission_id=preview["mission_id"]
    )
    store.admit_mission_execution(
        preview["mission_id"], snapshot_hash=result["snapshot_hash"]
    )
    state = store.load()
    state["plans"][0]["plan"]["steps"][0]["task"] = "changed after confirmation"
    store.save(state)
    calls: list[str] = []
    monkeypatch.setattr(
        cli, "AcpWorkerTransport", lambda **_kwargs: calls.append("acp")
    )
    monkeypatch.setattr(
        cli, "TmuxWorkerTransport", lambda **_kwargs: calls.append("tmux")
    )

    with pytest.raises(ServiceError, match="execution authority drift"):
        cli._daemon_worker_transport_for(
            store=store,
            service=SimpleNamespace(),
            root=root,
            attempt={
                "mission_id": preview["mission_id"],
                "step_id": "step_1",
                "agent_id": "planner",
                "configured_transport": "acp",
                "dispatch_key": "dsp_" + "d" * 32,
            },
        )
    assert calls == []


def test_project_view_scheduler_projects_active_and_terminal_mission_facts(
    tmp_path, monkeypatch
) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch)
    result = confirm_mission_for_daemon(
        config=config, store=store, mission_id=preview["mission_id"]
    )
    store.admit_mission_execution(
        preview["mission_id"], snapshot_hash=result["snapshot_hash"]
    )

    active = store.project_view(config).scheduler
    assert active == {
        "state": "running",
        "active_mission_id": preview["mission_id"],
        "active_step": "step_1",
        "next_transition": "prepare_dispatch",
        "blockers": [],
    }

    state = store.load()
    state["missions"][0].update({"status": "completed", "current_step": 2})
    store.save(state)
    terminal = store.project_view(config).scheduler
    assert terminal == {
        "state": "terminal",
        "active_mission_id": preview["mission_id"],
        "active_step": None,
        "next_transition": None,
        "blockers": [],
    }


def test_project_view_scheduler_is_read_only_and_never_probes_tmux(
    tmp_path, monkeypatch
) -> None:
    root, config, store, preview = _seed(tmp_path, monkeypatch)
    result = confirm_mission_for_daemon(
        config=config, store=store, mission_id=preview["mission_id"]
    )
    store.admit_mission_execution(
        preview["mission_id"], snapshot_hash=result["snapshot_hash"]
    )
    state = store.load()
    state["missions"][0].update({"status": "running", "current_step": 1})
    store.save(state)
    monkeypatch.setattr(
        "agentdeck.daemon.service.probe_tmux_worker_readiness",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ProjectView must not inspect tmux")
        ),
    )
    before = _tree_bytes(root)

    assert store.project_view(config).scheduler == {
        "state": "running",
        "active_mission_id": preview["mission_id"],
        "active_step": "step_2",
        "next_transition": "start_worker",
        "blockers": [],
    }
    assert _tree_bytes(root) == before


@pytest.mark.parametrize("drift", ["task", "order", "add", "remove"])
def test_freeze_recomputes_current_plan_hash_inside_lock_and_is_full_tree_zero_write(
    tmp_path, monkeypatch, drift
) -> None:
    root, _config, store, preview = _seed(tmp_path, monkeypatch)
    state = store.load()
    steps = state["plans"][0]["plan"]["steps"]
    if drift == "task":
        steps[0]["task"] = "changed after preview"
    elif drift == "order":
        steps[0], steps[1] = steps[1], steps[0]
    elif drift == "add":
        extra = deepcopy(steps[-1])
        extra["step"] = 3
        steps.append(extra)
    else:
        steps.pop()
    store.save(state)
    before = _tree_bytes(root)

    with pytest.raises(MissionStateError, match="plan hash drift"):
        store.freeze_mission_execution(
            preview["mission_id"], confirmed_at=state["missions"][0]["created_at"]
        )

    assert _tree_bytes(root) == before


def test_confirmed_mission_freezes_compact_execution_authority(tmp_path, monkeypatch) -> None:
    root, config, store, preview = _seed(
        tmp_path,
        monkeypatch,
        project_memory="private memory SECRET-MEMORY\n",
    )

    result = confirm_mission_for_daemon(
        config=config, store=store, mission_id=preview["mission_id"]
    )

    snapshot = result["execution_snapshot"]
    assert snapshot["mission_hash"] == canonical_hash(snapshot["mission"])
    assert snapshot["policy_hash"] == canonical_hash(snapshot["policy"])
    execution_body = {
        key: snapshot[key]
        for key in ("mission", "workers", "policy", "limits", "mission_hash", "policy_hash")
    }
    assert snapshot["execution_hash"] == canonical_hash(execution_body)
    assert result["snapshot_hash"] == snapshot["execution_hash"]
    assert (
        store.mission_by_id(preview["mission_id"])["execution_authority_hash"]
        == snapshot["execution_hash"]
    )
    assert result["execution_authority_hash"] == snapshot["execution_hash"]
    assert [item["configured_transport"] for item in snapshot["workers"]] == [
        "acp",
        "tmux",
    ]
    assert [item["agent_id"] for item in snapshot["workers"]] == [
        "planner",
        "reviewer",
    ]
    assert snapshot["mission"]["steps"][0]["step_id"] == "step_1"
    assert snapshot["mission"]["steps"][0]["task_hash"].startswith("sha256:")
    assert set(snapshot["mission"]) >= {"goal_hash", "summary_hash"}
    assert snapshot["mission"]["declared_tests_hash"] == canonical_hash(
        {"declared_tests": ["pytest tests/test_feature.py -q"]}
    )
    assert snapshot["mission"]["acceptance_criteria_hash"] == canonical_hash(
        {"acceptance_criteria": ["implementation and review both complete"]}
    )
    assert "goal" not in snapshot["mission"]
    assert "summary" not in snapshot["mission"]
    assert snapshot["mission"]["memory_provenance"] == [
        {
            "scope": "project",
            "content_hash": "sha256:"
            + hashlib.sha256("private memory SECRET-MEMORY\n".encode()).hexdigest(),
            "line_count": 1,
            "byte_count": len("private memory SECRET-MEMORY\n".encode()),
        }
    ]
    serialized = json.dumps(snapshot, ensure_ascii=False)
    assert "hunter2" not in serialized
    assert "SECRET" not in serialized
    assert "summary-secret" not in serialized
    assert "SECRET-MEMORY" not in serialized
    assert "--token" not in serialized
    assert "pane_id" not in serialized
    assert "native_session_id" not in serialized
    persisted = store.mission_by_id(preview["mission_id"])
    assert persisted["execution_snapshot"] == snapshot
    snapshot["mission"]["steps"][0]["role"] = "tampered"
    assert store.mission_by_id(preview["mission_id"])["execution_snapshot"] != snapshot


def test_confirmed_mission_recovers_before_first_attempt(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch)
    confirmed = confirm_mission_for_daemon(
        config=config, store=store, mission_id=preview["mission_id"]
    )
    assert store.load()["mission_recovery_evidence"] == [
        {
            "mission_id": preview["mission_id"],
            "attempt_id": None,
            "agent_id": None,
        }
    ]
    enabled: list[bool] = []
    result = reconcile_startup(
        store, enable_scheduler=lambda: enabled.append(True)
    )
    assert enabled == [True]
    assert result[0]["classification"] == "resumable"
    assert result[0]["next_transition"] == "prepare_dispatch"
    assert result[0]["attempt_id"] is None
    assert confirmed["snapshot_hash"] == store.mission_by_id(preview["mission_id"])[
        "snapshot_hash"
    ]


@pytest.mark.parametrize(
    "drift",
    ["goal", "summary", "skill", "declared_tests", "acceptance_criteria"],
)
def test_freeze_rejects_preview_authority_plan_fact_drift_with_full_tree_zero_write(
    tmp_path, monkeypatch, drift
) -> None:
    root, _config, store, preview = _seed(tmp_path, monkeypatch)
    state = store.load()
    plan_record = state["plans"][0]
    if drift == "skill":
        plan_record["skill_context"] = {
            "items": [
                {
                    "agent_id": "planner",
                    "name": "changed-skill",
                    "content_hash": "sha256:" + "1" * 64,
                    "source": "project",
                }
            ]
        }
    else:
        plan_record["plan"][drift] = f"changed {drift}"
    store.save(state)
    before = _tree_bytes(root)

    with pytest.raises(MissionStateError, match="execution authority drift"):
        store.freeze_mission_execution(
            preview["mission_id"], confirmed_at=state["missions"][0]["created_at"]
        )

    assert _tree_bytes(root) == before


@pytest.mark.parametrize("drift", ["worker", "policy"])
def test_freeze_rejects_preview_authority_config_drift_with_full_tree_zero_write(
    tmp_path, monkeypatch, drift
) -> None:
    root, config, store, preview = _seed(tmp_path, monkeypatch)
    changed = (
        replace(
            config,
            agents=tuple(
                replace(agent, role="changed")
                if agent.agent_id == "planner"
                else agent
                for agent in config.agents
            ),
        )
        if drift == "worker"
        else replace(config, leader=replace(config.leader, approval_mode="approve"))
    )
    monkeypatch.setattr("agentdeck.state.load_config", lambda root: changed)
    before = _tree_bytes(root)

    with pytest.raises(MissionStateError, match="execution authority drift"):
        store.freeze_mission_execution(
            preview["mission_id"],
            confirmed_at=store.mission_by_id(preview["mission_id"])["created_at"],
        )

    assert _tree_bytes(root) == before


def test_freeze_rejects_preview_authority_memory_drift_with_full_tree_zero_write(
    tmp_path, monkeypatch
) -> None:
    root, _config, store, preview = _seed(
        tmp_path, monkeypatch, project_memory="preview memory\n"
    )
    (root / ".agentdeck" / "memory" / "project.md").write_text(
        "changed memory\n", encoding="utf-8"
    )
    before = _tree_bytes(root)

    with pytest.raises(MissionStateError, match="execution authority drift"):
        store.freeze_mission_execution(
            preview["mission_id"],
            confirmed_at=store.mission_by_id(preview["mission_id"])["created_at"],
        )

    assert _tree_bytes(root) == before


def test_snapshot_builder_hashes_raw_goal_and_rejects_sensitive_policy_keys(
    tmp_path, monkeypatch
) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch)
    mission = store.mission_by_id(preview["mission_id"])
    plan = store.plan_by_id(mission["plan_id"])
    plan["plan"]["goal"] = "raw SECRET goal"
    plan["plan"]["summary"] = "raw password=summary"
    policy = {
        "approval_mode": "confirm",
        "autonomous_allowed_agents": [],
        "autonomous_max_approvals": 0,
        "policy_source": "project_config",
    }

    snapshot = build_execution_snapshot(config, mission, plan, policy)

    encoded = json.dumps(snapshot, ensure_ascii=False)
    assert "SECRET" not in encoded
    assert "password" not in encoded
    with pytest.raises(MissionRunError, match="execution snapshot invalid"):
        build_execution_snapshot(
            config,
            mission,
            plan,
            {**policy, "command": "publish --token SECRET"},
        )


def test_snapshot_hash_is_canonical_and_rejects_illegal_json_values() -> None:
    assert canonical_hash({"b": 2, "a": ["值", 1]}) == canonical_hash(
        {"a": ["值", 1], "b": 2}
    )
    with pytest.raises(MissionRunError, match="execution snapshot invalid"):
        canonical_hash({"bad": True})
    with pytest.raises(MissionRunError, match="execution snapshot invalid"):
        canonical_hash({"bad": "\ud800"})
    with pytest.raises(MissionRunError, match="execution snapshot invalid"):
        canonical_hash({"bad": float("nan")})


def test_state_rejects_forbidden_nested_runtime_facts_even_with_recomputed_hash(
    tmp_path, monkeypatch
) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch)
    mission = store.mission_by_id(preview["mission_id"])
    plan = store.plan_by_id(mission["plan_id"])
    policy = {
        "approval_mode": "confirm",
        "autonomous_allowed_agents": [],
        "autonomous_max_approvals": 0,
        "policy_source": "project_config",
    }
    snapshot = build_execution_snapshot(config, mission, plan, policy)
    snapshot["workers"][0]["native_session_id"] = "SECRET-session"
    snapshot["workers"][0]["pane_history"] = ["SECRET-pane"]
    body = {
        key: snapshot[key]
        for key in ("mission", "workers", "policy", "limits", "mission_hash", "policy_hash")
    }
    snapshot["execution_hash"] = canonical_hash(body)
    before = _state_bytes(store)

    with pytest.raises(TypeError, match="execution_snapshot"):
        store.freeze_mission_execution(
            preview["mission_id"],
            execution_snapshot=snapshot,
            confirmed_at=mission["created_at"],
        )

    assert _state_bytes(store) == before


def test_confirmation_rejects_timestamp_before_mission_creation_without_write(
    tmp_path, monkeypatch
) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch)
    mission = store.mission_by_id(preview["mission_id"])
    plan = store.plan_by_id(mission["plan_id"])
    snapshot = build_execution_snapshot(
        config,
        mission,
        plan,
        {
            "approval_mode": "confirm",
            "autonomous_allowed_agents": [],
            "autonomous_max_approvals": 0,
            "policy_source": "project_config",
        },
    )
    before = _state_bytes(store)

    with pytest.raises(ValueError, match="confirmation timestamp invalid"):
        store.freeze_mission_execution(
            preview["mission_id"],
            confirmed_at="2000-01-01T00:00:00+00:00",
        )

    assert _state_bytes(store) == before


def test_confirmation_reloads_config_inside_lock_and_rejects_worker_drift(
    tmp_path, monkeypatch
) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch)
    changed = replace(
        config,
        agents=tuple(
            replace(agent, role="changed")
            if agent.agent_id == "planner"
            else agent
            for agent in config.agents
        ),
    )
    monkeypatch.setattr("agentdeck.state.load_config", lambda root: changed)
    before = _state_bytes(store)

    with pytest.raises(MissionRunError, match="mission confirmation drift"):
        confirm_mission_for_daemon(
            config=config, store=store, mission_id=preview["mission_id"]
        )

    assert _state_bytes(store) == before


def test_caller_cached_config_does_not_define_attempt_authority(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch)
    confirmed = confirm_mission_for_daemon(
        config=config, store=store, mission_id=preview["mission_id"]
    )
    changed = replace(
        config,
        agents=tuple(
            replace(agent, role="changed") if agent.agent_id == "planner" else agent
            for agent in config.agents
        ),
    )
    attempt = prepare_attempt(
        config=changed,
        store=store,
        mission_id=preview["mission_id"],
        step_id="step_1",
        agent_id="planner",
        configured_transport="acp",
    )

    assert attempt["state"] == "prepared"
    assert confirmed["execution_snapshot"] == store.mission_by_id(preview["mission_id"])[
        "execution_snapshot"
    ]


@pytest.mark.parametrize("drift", ["transport", "policy"])
def test_prepare_reloads_authoritative_config_inside_lock_and_rejects_drift(
    tmp_path, monkeypatch, drift
) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    if drift == "transport":
        changed = replace(
            config,
            agents=tuple(
                replace(agent, transport="tmux", transport_command=())
                if agent.agent_id == "planner"
                else agent
                for agent in config.agents
            ),
        )
    else:
        changed = replace(config, leader=replace(config.leader, approval_mode="approve"))
    monkeypatch.setattr("agentdeck.state.load_config", lambda root: changed)
    before = _state_bytes(store)

    with pytest.raises(MissionRunError, match="frozen execution drift"):
        prepare_attempt(
            config=config,
            store=store,
            mission_id=preview["mission_id"],
            step_id="step_1",
            agent_id="planner",
            configured_transport="acp",
        )

    assert _state_bytes(store) == before


def test_plan_drift_rejects_with_zero_write(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    state = store.load()
    state["plans"][0]["plan"]["steps"][0]["task"] = "changed task"
    store.save(state)
    before = _state_bytes(store)
    with pytest.raises(MissionRunError, match="frozen execution drift"):
        prepare_attempt(
            config=config,
            store=store,
            mission_id=preview["mission_id"],
            step_id="step_1",
            agent_id="planner",
            configured_transport="acp",
        )
    assert _state_bytes(store) == before



def test_prepare_attempt_commits_exact_pre_dispatch_record_and_event(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch)
    confirmed = confirm_mission_for_daemon(
        config=config, store=store, mission_id=preview["mission_id"]
    )

    attempt = prepare_attempt(
        config=config,
        store=store,
        mission_id=preview["mission_id"],
        step_id="step_1",
        agent_id="planner",
        configured_transport="acp",
    )

    assert set(attempt) == {
        "attempt_id",
        "mission_id",
        "step_id",
        "agent_id",
        "configured_transport",
        "dispatch_key",
        "admission_claim_id",
        "snapshot_hash",
        "state",
        "created_at",
        "updated_at",
        "receipt_summary",
        "blocker",
        "terminal_reason",
    }
    assert attempt["attempt_id"].startswith("mat_")
    assert attempt["dispatch_key"].startswith("dsp_")
    assert attempt["snapshot_hash"] == confirmed["snapshot_hash"]
    assert attempt["state"] == "prepared"
    assert attempt["admission_claim_id"] is None
    assert attempt["receipt_summary"] is None
    persisted = [
        item
        for item in store.load()["mission_attempts"]
        if item.get("mission_id") == preview["mission_id"]
    ]
    assert persisted == [attempt]
    assert store.load()["mission_recovery_evidence"] == [
        {
            "mission_id": preview["mission_id"],
            "attempt_id": attempt["attempt_id"],
            "agent_id": "planner",
        }
    ]
    events = store.load()["protocol_event_outbox"]
    assert events[-1]["event_type"] == "mission_attempt_prepared"
    assert events[-1]["payload"] == {
        "attempt_id": attempt["attempt_id"],
        "mission_id": preview["mission_id"],
        "step_id": "step_1",
        "dispatch_key": attempt["dispatch_key"],
    }
    durable_execution_facts = json.dumps(
        {"snapshot": confirmed["execution_snapshot"], "attempt": attempt, "events": events},
        ensure_ascii=False,
    )
    assert "SECRET" not in durable_execution_facts
    assert "hunter2" not in durable_execution_facts
    assert "summary-secret" not in durable_execution_facts


def test_dispatch_key_is_deterministic_and_duplicate_active_attempt_is_zero_write(
    tmp_path, monkeypatch
) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    first = prepare_attempt(
        config=config,
        store=store,
        mission_id=preview["mission_id"],
        step_id="step_1",
        agent_id="planner",
        configured_transport="acp",
    )
    before = _state_bytes(store)
    with pytest.raises(MissionRunError, match="active attempt already exists"):
        prepare_attempt(
            config=config,
            store=store,
            mission_id=preview["mission_id"],
            step_id="step_1",
            agent_id="planner",
            configured_transport="acp",
        )
    assert _state_bytes(store) == before
    assert first["dispatch_key"] == attempt_dispatch_key(
        preview["mission_id"],
        "step_1",
        "planner",
        "acp",
        first["snapshot_hash"],
        attempt_ordinal=1,
    )


def test_pre_dispatch_stop_is_audited_and_recovery_valid(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    attempt = prepare_attempt(config=config, store=store, mission_id=preview["mission_id"], step_id="step_1", agent_id="planner", configured_transport="acp")
    stopped = store.interrupt_prepared_mission_attempt(
        attempt_id=attempt["attempt_id"], dispatch_key=attempt["dispatch_key"],
        target_state="interrupted", reason="human stop",
    )
    assert stopped["state"] == "interrupted"
    assert stopped["admission_claim_id"] is None and stopped["receipt_summary"] is None
    assert store.load()["protocol_event_outbox"][-1]["event_type"] == "mission_attempt_pre_dispatch_stopped"
    store.load_recovery_snapshot()


def test_forged_pre_dispatch_stop_without_audit_is_rejected(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    prepare_attempt(config=config, store=store, mission_id=preview["mission_id"], step_id="step_1", agent_id="planner", configured_transport="acp")
    state = store.load()
    state["mission_attempts"][0].update({"state": "cancelled", "terminal_reason": "forged"})
    store.save(state)
    enabled: list[bool] = []
    with pytest.raises(RecoveryError, match="durable recovery evidence"):
        reconcile_startup(store, enable_scheduler=lambda: enabled.append(True))
    assert enabled == []
    assert store.load().get("recovery_decisions", []) == []


@pytest.mark.parametrize("stage", ["admitting", "submitted"])
def test_pre_dispatch_stop_rejects_effect_boundary_states(tmp_path, monkeypatch, stage) -> None:
    root, config, store, preview = _seed(tmp_path, monkeypatch)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    attempt = prepare_attempt(config=config, store=store, mission_id=preview["mission_id"], step_id="step_1", agent_id="planner", configured_transport="acp")
    claimed = store.claim_mission_attempt_admission(attempt_id=attempt["attempt_id"], dispatch_key=attempt["dispatch_key"])
    if stage == "submitted":
        store.record_mission_attempt_submitted(attempt_id=attempt["attempt_id"], dispatch_key=attempt["dispatch_key"], expected_claim_id=claimed["admission_claim_id"], receipt_summary="admitted")
    before = _tree_bytes(root)
    with pytest.raises(ValueError, match="pre-dispatch stop"):
        store.interrupt_prepared_mission_attempt(attempt_id=attempt["attempt_id"], dispatch_key=attempt["dispatch_key"], target_state="cancelled", reason="too late")
    assert _tree_bytes(root) == before


def test_released_claim_can_be_reclaimed_and_submitted(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    attempt = prepare_attempt(config=config, store=store, mission_id=preview["mission_id"], step_id="step_1", agent_id="planner", configured_transport="acp")
    first = store.claim_mission_attempt_admission(attempt_id=attempt["attempt_id"], dispatch_key=attempt["dispatch_key"])
    store.release_mission_attempt_admission(attempt_id=attempt["attempt_id"], dispatch_key=attempt["dispatch_key"], expected_claim_id=first["admission_claim_id"])
    second = store.claim_mission_attempt_admission(attempt_id=attempt["attempt_id"], dispatch_key=attempt["dispatch_key"])
    store.record_mission_attempt_submitted(attempt_id=attempt["attempt_id"], dispatch_key=attempt["dispatch_key"], expected_claim_id=second["admission_claim_id"], receipt_summary="admitted")
    store.load_recovery_snapshot()


def test_submitted_history_cannot_be_rewritten_to_prepared_without_claim(
    tmp_path, monkeypatch
) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    attempt = prepare_attempt(config=config, store=store, mission_id=preview["mission_id"], step_id="step_1", agent_id="planner", configured_transport="acp")
    claimed = store.claim_mission_attempt_admission(attempt_id=attempt["attempt_id"], dispatch_key=attempt["dispatch_key"])
    store.record_mission_attempt_submitted(attempt_id=attempt["attempt_id"], dispatch_key=attempt["dispatch_key"], expected_claim_id=claimed["admission_claim_id"], receipt_summary="admitted")
    state = store.load()
    state["mission_attempts"][0].update({
        "state": "prepared", "admission_claim_id": None, "receipt_summary": None,
        "updated_at": state["mission_attempts"][0]["created_at"],
    })
    store.save(state)
    enabled: list[bool] = []
    with pytest.raises(RecoveryError, match="durable recovery evidence"):
        reconcile_startup(store, enable_scheduler=lambda: enabled.append(True))
    assert enabled == []
    assert store.load().get("recovery_decisions", []) == []


def test_pre_dispatch_stop_permanently_closes_attempt_lineage(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    attempt = prepare_attempt(config=config, store=store, mission_id=preview["mission_id"], step_id="step_1", agent_id="planner", configured_transport="acp")
    store.interrupt_prepared_mission_attempt(attempt_id=attempt["attempt_id"], dispatch_key=attempt["dispatch_key"], target_state="cancelled", reason="human stop")
    state = store.load()
    state["protocol_event_outbox"].append({
        "event_id": state_module.new_id("evt"),
        "event_type": "mission_attempt_admission_claimed",
        "created_at": state["mission_attempts"][0]["updated_at"],
        "payload": {
            "attempt_id": attempt["attempt_id"], "mission_id": preview["mission_id"],
            "step_id": "step_1", "dispatch_key": attempt["dispatch_key"],
            "admission_claim_id": "adm_" + "f" * 12,
        },
    })
    store.save(state)
    enabled: list[bool] = []
    with pytest.raises(RecoveryError, match="durable recovery evidence"):
        reconcile_startup(store, enable_scheduler=lambda: enabled.append(True))
    assert enabled == []
    assert store.load().get("recovery_decisions", []) == []


def test_released_claim_can_remain_prepared_or_stop_safely(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    attempt = prepare_attempt(config=config, store=store, mission_id=preview["mission_id"], step_id="step_1", agent_id="planner", configured_transport="acp")
    claimed = store.claim_mission_attempt_admission(attempt_id=attempt["attempt_id"], dispatch_key=attempt["dispatch_key"])
    store.release_mission_attempt_admission(attempt_id=attempt["attempt_id"], dispatch_key=attempt["dispatch_key"], expected_claim_id=claimed["admission_claim_id"])
    store.load_recovery_snapshot()
    store.interrupt_prepared_mission_attempt(attempt_id=attempt["attempt_id"], dispatch_key=attempt["dispatch_key"], target_state="interrupted", reason="stop after release")
    store.load_recovery_snapshot()


def test_failed_attempt_retries_once_when_frozen_limit_is_one(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch, retry_limit=1)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    first = prepare_attempt(
        config=config,
        store=store,
        mission_id=preview["mission_id"],
        step_id="step_1",
        agent_id="planner",
        configured_transport="acp",
    )
    state = store.load()
    _set_attempt_post_admission(state, "failed", index=0)
    store.save(state)

    second = prepare_attempt(
        config=config,
        store=store,
        mission_id=preview["mission_id"],
        step_id="step_1",
        agent_id="planner",
        configured_transport="acp",
    )

    assert second["dispatch_key"] == attempt_dispatch_key(
        preview["mission_id"],
        "step_1",
        "planner",
        "acp",
        second["snapshot_hash"],
        attempt_ordinal=2,
    )
    assert second["dispatch_key"] != first["dispatch_key"]


@pytest.mark.parametrize("corruption", ["missing", "cross", "nonlatest"])
def test_retry_rejects_corrupt_existing_recovery_binding_full_tree_zero_write(
    tmp_path, monkeypatch, corruption: str
) -> None:
    root, config, store, preview = _seed(
        tmp_path, monkeypatch, retry_limit=2 if corruption == "nonlatest" else 1
    )
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    first = prepare_attempt(
        config=config,
        store=store,
        mission_id=preview["mission_id"],
        step_id="step_1",
        agent_id="planner",
        configured_transport="acp",
    )
    state = store.load()
    _set_attempt_post_admission(state, "failed")
    store.save(state)
    latest = first
    if corruption == "nonlatest":
        latest = prepare_attempt(
            config=config,
            store=store,
            mission_id=preview["mission_id"],
            step_id="step_1",
            agent_id="planner",
            configured_transport="acp",
        )
        state = store.load()
        _set_attempt_post_admission(state, "failed")
        state["mission_recovery_evidence"][0]["attempt_id"] = first["attempt_id"]
    elif corruption == "missing":
        state["mission_recovery_evidence"][0]["attempt_id"] = "mat_ffffffffffff"
    else:
        state["mission_recovery_evidence"][0]["mission_id"] = "mis_ffffffffffff"
    assert latest["attempt_id"] != (
        first["attempt_id"] if corruption == "nonlatest" else ""
    )
    store.save(state)
    before = _tree_bytes(root)

    with pytest.raises(MissionRunError, match="durable recovery"):
        prepare_attempt(
            config=config,
            store=store,
            mission_id=preview["mission_id"],
            step_id="step_1",
            agent_id="planner",
            configured_transport="acp",
        )

    assert _tree_bytes(root) == before


def test_retry_rejects_unaudited_reply_in_full_recovery_authority_zero_write(
    tmp_path, monkeypatch
) -> None:
    root, config, store, preview = _seed(tmp_path, monkeypatch, retry_limit=1)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    attempt = prepare_attempt(
        config=config,
        store=store,
        mission_id=preview["mission_id"],
        step_id="step_1",
        agent_id="planner",
        configured_transport="acp",
    )
    state = store.load()
    _set_attempt_post_admission(state, "failed", index=0)
    state["mission_worker_replies"] = [
        {
            "mission_id": preview["mission_id"],
            "attempt_id": attempt["attempt_id"],
            "reply_id": "mrp_" + "a" * 12,
            "dispatch_key": attempt["dispatch_key"],
            "state": "received",
        }
    ]
    store.save(state)
    before = _tree_bytes(root)

    with pytest.raises(MissionRunError, match="durable recovery authority invalid"):
        prepare_attempt(
            config=config,
            store=store,
            mission_id=preview["mission_id"],
            step_id="step_1",
            agent_id="planner",
            configured_transport="acp",
        )

    assert _tree_bytes(root) == before


@pytest.mark.parametrize("drift", ["snapshot", "agent", "transport", "dispatch_key"])
def test_retry_replays_every_prior_attempt_authority_in_durable_order_zero_write(
    tmp_path, monkeypatch, drift
) -> None:
    root, config, store, preview = _seed(tmp_path, monkeypatch, retry_limit=1)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    prepare_attempt(
        config=config,
        store=store,
        mission_id=preview["mission_id"],
        step_id="step_1",
        agent_id="planner",
        configured_transport="acp",
    )
    state = store.load()
    prior = state["mission_attempts"][0]
    _set_attempt_post_admission(state, "failed", index=0)
    if drift == "snapshot":
        prior["snapshot_hash"] = "sha256:" + "f" * 64
    elif drift == "agent":
        prior["agent_id"] = "reviewer"
    elif drift == "transport":
        prior["configured_transport"] = "tmux"
    else:
        prior["dispatch_key"] = "dsp_" + "f" * 32
    store.save(state)
    before = _tree_bytes(root)

    with pytest.raises(MissionRunError, match="mission attempt lineage drift"):
        prepare_attempt(
            config=config,
            store=store,
            mission_id=preview["mission_id"],
            step_id="step_1",
            agent_id="planner",
            configured_transport="acp",
        )

    assert _tree_bytes(root) == before


def test_duplicate_existing_dispatch_keys_are_rejected_before_retry_without_write(
    tmp_path, monkeypatch
) -> None:
    root, config, store, preview = _seed(tmp_path, monkeypatch, retry_limit=2)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    for _ in range(2):
        prepare_attempt(
            config=config,
            store=store,
            mission_id=preview["mission_id"],
            step_id="step_1",
            agent_id="planner",
            configured_transport="acp",
        )
        state = store.load()
        _set_attempt_post_admission(state, "failed")
        store.save(state)
    state = store.load()
    state["mission_attempts"][1]["dispatch_key"] = state["mission_attempts"][0][
        "dispatch_key"
    ]
    store.save(state)
    before = _tree_bytes(root)

    with pytest.raises(MissionRunError, match="duplicate mission dispatch key"):
        prepare_attempt(
            config=config,
            store=store,
            mission_id=preview["mission_id"],
            step_id="step_1",
            agent_id="planner",
            configured_transport="acp",
        )

    assert _tree_bytes(root) == before


@pytest.mark.parametrize(
    ("retry_limit", "terminal_state", "message"),
    [
        (0, "failed", "mission retry budget exhausted"),
        (1, "completed", "terminal mission attempt"),
        (1, "succeeded", "terminal mission attempt"),
        (1, "cancelled", "terminal mission attempt"),
        (1, "interrupted", "terminal mission attempt"),
        (1, "ambiguous", "terminal mission attempt"),
    ],
)
def test_retry_budget_and_non_retryable_terminal_states_are_full_tree_zero_write(
    tmp_path, monkeypatch, retry_limit, terminal_state, message
) -> None:
    root, config, store, preview = _seed(
        tmp_path, monkeypatch, retry_limit=retry_limit
    )
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    prepare_attempt(
        config=config,
        store=store,
        mission_id=preview["mission_id"],
        step_id="step_1",
        agent_id="planner",
        configured_transport="acp",
    )
    state = store.load()
    _set_attempt_post_admission(state, terminal_state, index=0)
    store.save(state)
    before = _tree_bytes(root)

    with pytest.raises(MissionRunError, match=message):
        prepare_attempt(
            config=config,
            store=store,
            mission_id=preview["mission_id"],
            step_id="step_1",
            agent_id="planner",
            configured_transport="acp",
        )

    assert _tree_bytes(root) == before


def test_retry_limit_one_rejects_third_total_attempt_without_write(
    tmp_path, monkeypatch
) -> None:
    root, config, store, preview = _seed(tmp_path, monkeypatch, retry_limit=1)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    for expected_count in (1, 2):
        prepare_attempt(
            config=config,
            store=store,
            mission_id=preview["mission_id"],
            step_id="step_1",
            agent_id="planner",
            configured_transport="acp",
        )
        state = store.load()
        _set_attempt_post_admission(state, "failed")
        store.save(state)
        assert len(store.load()["mission_attempts"]) == expected_count
    before = _tree_bytes(root)

    with pytest.raises(MissionRunError, match="mission retry budget exhausted"):
        prepare_attempt(
            config=config,
            store=store,
            mission_id=preview["mission_id"],
            step_id="step_1",
            agent_id="planner",
            configured_transport="acp",
        )

    assert _tree_bytes(root) == before


def test_duplicate_dispatch_key_anywhere_in_mission_attempts_is_zero_write(
    tmp_path, monkeypatch
) -> None:
    root, config, store, preview = _seed(tmp_path, monkeypatch, retry_limit=1)
    confirmed = confirm_mission_for_daemon(
        config=config, store=store, mission_id=preview["mission_id"]
    )
    first = prepare_attempt(
        config=config,
        store=store,
        mission_id=preview["mission_id"],
        step_id="step_1",
        agent_id="planner",
        configured_transport="acp",
    )
    state = store.load()
    first_record = state["mission_attempts"][0]
    _set_attempt_post_admission(state, "failed", index=0)
    duplicate = deepcopy(first_record)
    duplicate["attempt_id"] = "mat_" + "f" * 12
    duplicate["mission_id"] = "mis_" + "e" * 12
    duplicate["dispatch_key"] = attempt_dispatch_key(
        preview["mission_id"],
        "step_1",
        "planner",
        "acp",
        confirmed["snapshot_hash"],
        attempt_ordinal=2,
    )
    state["mission_attempts"].append(duplicate)
    store.save(state)
    before = _tree_bytes(root)

    with pytest.raises(MissionRunError, match="duplicate mission dispatch key"):
        prepare_attempt(
            config=config,
            store=store,
            mission_id=preview["mission_id"],
            step_id="step_1",
            agent_id="planner",
            configured_transport="acp",
        )

    assert _tree_bytes(root) == before


def test_duplicate_mission_attempt_identity_is_strictly_rejected_without_write(
    tmp_path, monkeypatch
) -> None:
    root, config, store, preview = _seed(tmp_path, monkeypatch, retry_limit=1)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    prepare_attempt(
        config=config,
        store=store,
        mission_id=preview["mission_id"],
        step_id="step_1",
        agent_id="planner",
        configured_transport="acp",
    )
    state = store.load()
    _set_attempt_post_admission(state, "failed", index=0)
    duplicate = deepcopy(state["mission_attempts"][0])
    duplicate["mission_id"] = "mis_" + "d" * 12
    duplicate["dispatch_key"] = "dsp_" + "e" * 32
    state["mission_attempts"].append(duplicate)
    store.save(state)
    before = _tree_bytes(root)

    with pytest.raises(MissionRunError, match="duplicate mission attempt identity"):
        prepare_attempt(
            config=config,
            store=store,
            mission_id=preview["mission_id"],
            step_id="step_1",
            agent_id="planner",
            configured_transport="acp",
        )

    assert _tree_bytes(root) == before


def test_candidate_mission_attempt_id_collision_is_rejected_without_random_retry(
    tmp_path, monkeypatch
) -> None:
    root, config, store, preview = _seed(tmp_path, monkeypatch, retry_limit=1)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    first = prepare_attempt(
        config=config,
        store=store,
        mission_id=preview["mission_id"],
        step_id="step_1",
        agent_id="planner",
        configured_transport="acp",
    )
    state = store.load()
    _set_attempt_post_admission(state, "failed", index=0)
    store.save(state)
    real_new_id = state_module.new_id
    monkeypatch.setattr(
        state_module,
        "new_id",
        lambda prefix: first["attempt_id"] if prefix == "mat" else real_new_id(prefix),
    )
    before = _tree_bytes(root)

    with pytest.raises(MissionRunError, match="duplicate mission attempt identity"):
        prepare_attempt(
            config=config,
            store=store,
            mission_id=preview["mission_id"],
            step_id="step_1",
            agent_id="planner",
            configured_transport="acp",
        )

    assert _tree_bytes(root) == before


@pytest.mark.parametrize(
    "candidate_time",
    [True, "not-a-timestamp", "2026-07-13T12:00:00", "2000-01-01T00:00:00+00:00"],
)
def test_candidate_attempt_invalid_naive_or_backward_time_is_full_tree_zero_write(
    tmp_path, monkeypatch, candidate_time
) -> None:
    root, config, store, preview = _seed(tmp_path, monkeypatch)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    monkeypatch.setattr(state_module, "utc_now", lambda: candidate_time)
    before = _tree_bytes(root)

    with pytest.raises(MissionRunError, match="mission attempt state invalid"):
        prepare_attempt(
            config=config,
            store=store,
            mission_id=preview["mission_id"],
            step_id="step_1",
            agent_id="planner",
            configured_transport="acp",
        )

    assert _tree_bytes(root) == before


def test_candidate_attempt_at_confirmation_boundary_and_event_share_validated_time(
    tmp_path, monkeypatch
) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch)
    confirmed = confirm_mission_for_daemon(
        config=config, store=store, mission_id=preview["mission_id"]
    )
    boundary = confirmed["confirmed_at"]
    monkeypatch.setattr(state_module, "utc_now", lambda: boundary)

    attempt = prepare_attempt(
        config=config,
        store=store,
        mission_id=preview["mission_id"],
        step_id="step_1",
        agent_id="planner",
        configured_transport="acp",
    )

    assert attempt["created_at"] == boundary
    assert attempt["updated_at"] == boundary
    event = store.load()["protocol_event_outbox"][-1]
    assert event["created_at"] == boundary
    assert event["payload"]["attempt_id"] == attempt["attempt_id"]


@pytest.mark.parametrize("collision_source", ["outbox", "journal"])
def test_candidate_event_id_collision_with_durable_event_state_is_zero_write(
    tmp_path, monkeypatch, collision_source
) -> None:
    root, config, store, preview = _seed(tmp_path, monkeypatch)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    if collision_source == "outbox":
        collision_id = store.load()["protocol_event_outbox"][0]["event_id"]
    else:
        collision_id = json.loads(
            next(line for line in store.events_path.read_text().splitlines() if line)
        )["event_id"]
    real_new_id = state_module.new_id
    monkeypatch.setattr(
        state_module,
        "new_id",
        lambda prefix: collision_id if prefix == "evt" else real_new_id(prefix),
    )
    before = _tree_bytes(root)

    with pytest.raises(MissionRunError, match="duplicate protocol event identity"):
        prepare_attempt(
            config=config,
            store=store,
            mission_id=preview["mission_id"],
            step_id="step_1",
            agent_id="planner",
            configured_transport="acp",
        )

    assert _tree_bytes(root) == before


def test_existing_duplicate_protocol_outbox_identity_blocks_attempt_zero_write(
    tmp_path, monkeypatch
) -> None:
    root, config, store, preview = _seed(tmp_path, monkeypatch)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    state = store.load()
    state["protocol_event_outbox"].append(
        deepcopy(state["protocol_event_outbox"][0])
    )
    store.save(state)
    before = _tree_bytes(root)

    with pytest.raises(MissionRunError, match="duplicate protocol event identity"):
        prepare_attempt(
            config=config,
            store=store,
            mission_id=preview["mission_id"],
            step_id="step_1",
            agent_id="planner",
            configured_transport="acp",
        )

    assert _tree_bytes(root) == before


@pytest.mark.parametrize(
    "journal_text",
    ["{not-json}\n", '{"event_id":"evt_bad"}\n'],
)
def test_malformed_protocol_event_journal_blocks_attempt_zero_write(
    tmp_path, monkeypatch, journal_text
) -> None:
    root, config, store, preview = _seed(tmp_path, monkeypatch)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    store.events_path.write_text(journal_text, encoding="utf-8")
    before = _tree_bytes(root)

    with pytest.raises(MissionRunError, match="protocol event journal is malformed"):
        prepare_attempt(
            config=config,
            store=store,
            mission_id=preview["mission_id"],
            step_id="step_1",
            agent_id="planner",
            configured_transport="acp",
        )

    assert _tree_bytes(root) == before


def test_valid_candidate_event_remains_flushable_without_loss(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    attempt = prepare_attempt(
        config=config,
        store=store,
        mission_id=preview["mission_id"],
        step_id="step_1",
        agent_id="planner",
        configured_transport="acp",
    )
    event = store.load()["protocol_event_outbox"][-1]

    assert store.flush_protocol_event_outbox() >= 1
    assert store.load()["protocol_event_outbox"] == []
    durable = [json.loads(line) for line in store.events_path.read_text().splitlines()]
    matches = [item for item in durable if item["event_id"] == event["event_id"]]
    assert len(matches) == 1
    assert matches[0]["payload"]["attempt_id"] == attempt["attempt_id"]


def test_state_store_does_not_accept_caller_supplied_dispatch_key(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    before = _state_bytes(store)

    with pytest.raises(TypeError, match="dispatch_key"):
        store.prepare_mission_attempt(
            mission_id=preview["mission_id"],
            step_id="step_1",
            agent_id="planner",
            configured_transport="acp",
            dispatch_key="dsp_" + "0" * 32,
        )

    assert _state_bytes(store) == before


@pytest.mark.parametrize(
    ("step_id", "agent_id", "transport", "message"),
    [
        ("step_9", "planner", "acp", "unknown mission step"),
        ("step_1", "reviewer", "acp", "mission step agent drift"),
        ("step_1", "planner", "tmux", "mission step transport drift"),
        (True, "planner", "acp", "mission step identity invalid"),
    ],
)
def test_prepare_attempt_rejects_invalid_step_agent_and_transport_without_write(
    tmp_path, monkeypatch, step_id, agent_id, transport, message
) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    before = _state_bytes(store)
    with pytest.raises(MissionRunError, match=message):
        prepare_attempt(
            config=config,
            store=store,
            mission_id=preview["mission_id"],
            step_id=step_id,
            agent_id=agent_id,
            configured_transport=transport,
        )
    assert _state_bytes(store) == before


def test_prepare_attempt_rejects_terminal_or_unconfirmed_mission_without_write(
    tmp_path, monkeypatch
) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch)
    before = _state_bytes(store)
    with pytest.raises(MissionRunError, match="mission execution is not confirmed"):
        prepare_attempt(
            config=config,
            store=store,
            mission_id=preview["mission_id"],
            step_id="step_1",
            agent_id="planner",
            configured_transport="acp",
        )
    assert _state_bytes(store) == before

    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    state = store.load()
    mission = state["missions"][0]
    mission["status"] = "completed"
    mission["completed_at"] = mission["updated_at"]
    store.save(state)
    before = _state_bytes(store)
    with pytest.raises(MissionRunError, match="terminal mission step"):
        prepare_attempt(
            config=config,
            store=store,
            mission_id=preview["mission_id"],
            step_id="step_1",
            agent_id="planner",
            configured_transport="acp",
        )
    assert _state_bytes(store) == before


def test_corrupt_frozen_snapshot_fails_closed_without_repair(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    state = store.load()
    state["missions"][0]["execution_snapshot"]["workers"][0][
        "configured_transport"
    ] = "tmux"
    store.save(state)
    before = _state_bytes(store)
    with pytest.raises(MissionRunError, match="frozen execution snapshot invalid"):
        prepare_attempt(
            config=config,
            store=store,
            mission_id=preview["mission_id"],
            step_id="step_1",
            agent_id="planner",
            configured_transport="acp",
        )
    assert _state_bytes(store) == before


def test_invalid_confirmation_timestamp_fails_closed_without_attempt_write(
    tmp_path, monkeypatch
) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    state = store.load()
    state["missions"][0]["confirmed_at"] = True
    store.save(state)
    before = _state_bytes(store)

    with pytest.raises(MissionRunError, match="mission confirmation state invalid"):
        prepare_attempt(
            config=config,
            store=store,
            mission_id=preview["mission_id"],
            step_id="step_1",
            agent_id="planner",
            configured_transport="acp",
        )

    assert _state_bytes(store) == before


def test_legacy_state_without_mission_attempts_is_additively_upgraded(
    tmp_path, monkeypatch
) -> None:
    _root, config, store, preview = _seed(tmp_path, monkeypatch)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    state = store.load()
    legacy_attempts = deepcopy(state["attempts"])
    state.pop("mission_attempts", None)
    store.save(state)
    unrelated = deepcopy(store.load()["plans"])

    attempt = prepare_attempt(
        config=config,
        store=store,
        mission_id=preview["mission_id"],
        step_id="step_1",
        agent_id="planner",
        configured_transport="acp",
    )

    assert store.load()["mission_attempts"] == [attempt]
    assert store.load()["attempts"] == legacy_attempts
    assert store.load()["plans"] == unrelated


def test_generic_trace_treats_mat_identity_as_safe_unknown(
    tmp_path, monkeypatch, capsys
) -> None:
    root, config, store, preview = _seed(tmp_path, monkeypatch)
    confirm_mission_for_daemon(config=config, store=store, mission_id=preview["mission_id"])
    attempt = prepare_attempt(
        config=config,
        store=store,
        mission_id=preview["mission_id"],
        step_id="step_1",
        agent_id="planner",
        configured_transport="acp",
    )
    before = _tree_bytes(root)
    monkeypatch.chdir(root)

    assert cli.main(["trace", "--id", attempt["attempt_id"]]) == 1
    assert capsys.readouterr().err == f"unknown trace id: {attempt['attempt_id']}\n"
    assert _tree_bytes(root) == before
