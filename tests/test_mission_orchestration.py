from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import threading
from urllib.error import URLError

import pytest

from agentdeck.config import load_config, write_default_config
from agentdeck.contracts import validate_mission_preview_contract
from agentdeck.mission_orchestration import (
    create_mission_preview,
    interrupt_mission,
    mission_status_payload,
    resume_mission,
    run_mission,
)
from agentdeck.runtime.readiness import WorkerReadiness, WorkerReadinessBatch
from agentdeck.providers import LeaderPlanRequest
from agentdeck.state import StateStore


MESSAGE = "让 Codex 和 Claude 一人一句接龙百家姓，共8轮"


def eight_step_plan() -> dict[str, object]:
    steps = []
    for step in range(1, 9):
        agent_id, role = ("planner", "planning") if step % 2 else ("reviewer", "review")
        steps.append(
            {
                "step": step,
                "agent_id": agent_id,
                "role": role,
                "task": f"完成接龙第 {step} 轮",
                "risk": "requires human review before dispatch",
                "requires_approval": True,
            }
        )
    return {
        "goal": "完成八轮接龙",
        "summary": "Codex 与 Claude 严格串行交替执行。",
        "steps": steps,
        "approval_required": True,
        "dispatch_ready": False,
    }


class RecordingProvider:
    name = "fake"

    def __init__(self, plan: object | None = None) -> None:
        self.requests: list[LeaderPlanRequest] = []
        self.plan_result = eight_step_plan() if plan is None else plan

    def plan(self, request: LeaderPlanRequest) -> dict[str, object]:
        self.requests.append(request)
        return self.plan_result  # type: ignore[return-value]


class ExplodingProvider:
    name = "fake"

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def plan(self, request: LeaderPlanRequest) -> dict[str, object]:
        self.calls += 1
        raise self.error


def project(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    config_path = root / ".agentdeck" / "config.toml"
    text = config_path.read_text(encoding="utf-8")
    text = text.replace('provider = "deepseek"', 'provider = "fake"', 1)
    text = text.replace('model = "deepseek-chat"', 'model = "fake-plan"', 1)
    config_path.write_text(text, encoding="utf-8")
    return root, load_config(root), StateStore(root), config_path


class MissionBackend:
    def __init__(self, *, fail_spawn_for: str | None = None) -> None:
        self.fail_spawn_for = fail_spawn_for
        self.created = 0
        self.spawned: list[str] = []
        self.panes: dict[str, str] = {}

    def create_session(self, config) -> None:
        self.created += 1

    def spawn_agent(self, config, agent, cwd: str) -> str:
        self.spawned.append(agent.agent_id)
        if agent.agent_id == self.fail_spawn_for:
            raise RuntimeError("SECRET spawn detail")
        pane = f"%{len(self.panes) + 1}"
        self.panes[agent.agent_id] = pane
        return pane

    def pane_exists(self, config, pane_id: str) -> bool:
        return pane_id in self.panes.values()


class CorrelatedMissionBackend(MissionBackend):
    def __init__(self, *, interrupt_step2: bool = False) -> None:
        super().__init__()
        self.sent: list[tuple[str, str]] = []
        self.interrupt_step2 = interrupt_step2
        self.did_interrupt = False

    def send_input(self, config, pane_id: str, text: str) -> None:
        self.sent.append((pane_id, text))

    def capture_output(self, config, pane_id: str, lines: int = 200) -> str:
        prompts = [text for target, text in self.sent if target == pane_id]
        if lines < 400:
            agent_id = next(agent for agent, pane in self.panes.items() if pane == pane_id)
            if agent_id == "planner":
                return "OpenAI Codex\nmodel: fake\n› Ask Codex anything"
            return "Claude Code\n❯ Try a task\n100% context left"
        token = next(
            line.rsplit(":", 1)[1].strip()
            for line in prompts[-1].splitlines()
            if line.startswith("Complete only this task. Use this handoff token exactly:")
        )
        if self.interrupt_step2 and token.endswith("_step_2") and not self.did_interrupt:
            self.did_interrupt = True
            raise KeyboardInterrupt
        return (
            f"handoff_token: {token}\n"
            "status: completed\n"
            f"summary: completed {pane_id}\n"
            "verification: correlated fake\n"
            "risks: none\n"
            "next_steps: continue"
        )


class BaijiaxingMissionBackend(CorrelatedMissionBackend):
    summaries = (
        "赵钱孙李", "周吴郑王", "冯陈褚卫", "蒋沈韩杨",
        "朱秦尤许", "何吕施张", "孔曹严华", "金魏陶姜",
    )

    def capture_output(self, config, pane_id: str, lines: int = 200) -> str:
        if lines < 400:
            return super().capture_output(config, pane_id, lines)
        prompt = next(text for target, text in reversed(self.sent) if target == pane_id)
        token = next(
            line.rsplit(":", 1)[1].strip()
            for line in prompt.splitlines()
            if line.startswith("Complete only this task. Use this handoff token exactly:")
        )
        summary = self.summaries[len(self.sent) - 1]
        return (
            f"handoff_token: {token}\n"
            "status: completed\n"
            f"summary: {summary}\n"
            "verification: deterministic rehearsal\n"
            "risks: none\n"
            "next_steps: continue"
        )


def seeded_mission(tmp_path: Path, monkeypatch):
    root, config, store, _ = project(tmp_path)
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")
    preview = create_mission_preview(
        config=config,
        store=store,
        provider=RecordingProvider(),
        user_message=MESSAGE,
        timeout_seconds=180,
    )
    return root, config, store, preview


def ready_batch(*agent_ids: str) -> WorkerReadinessBatch:
    return WorkerReadinessBatch(
        True,
        tuple(WorkerReadiness(agent_id, "codex", "ready", None) for agent_id in agent_ids),
    )


def complete_fake_workflow(store: StateStore, run_id: str) -> dict[str, object]:
    turns = []
    for step in range(1, 9):
        agent_id = "planner" if step % 2 else "reviewer"
        handoff = {
            "step": step, "agent_id": agent_id, "status": "completed",
            "summary": f"turn {step}", "verification": "fake", "risks": "none",
            "next_steps": "continue", "artifact_paths": [],
            "trace_command": f"agentdeck trace --id rep_{step:012x}",
        }
        turns.append({"step": step, "agent_id": agent_id, "status": "completed", "handoff": handoff})
    return store.update_workflow_run(run_id, status="completed", current_step=8, turns=turns)


def test_run_mission_spawns_only_frozen_workers_and_completes(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = seeded_mission(tmp_path, monkeypatch)
    backend = MissionBackend()
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.wait_for_worker_readiness",
        lambda **kwargs: ready_batch("planner", "reviewer"),
    )
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.run_sequential_workflow",
        lambda **kwargs: complete_fake_workflow(store, kwargs["run_id"]),
    )

    result = run_mission(config=config, store=store, backend=backend, mission_id=preview["mission_id"])

    assert result["status"] == "completed"
    assert backend.spawned == ["planner", "reviewer"]
    assert "coder" not in backend.spawned
    assert len(store.load()["workflow_runs"]) == 1
    assert result["workflow_run_id"].startswith("wfr_")


def test_concurrent_confirm_creates_one_runtime_and_one_workflow(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = seeded_mission(tmp_path, monkeypatch)
    backend = MissionBackend()
    barrier = threading.Barrier(2)
    import agentdeck.mission_orchestration as orchestration
    original_preflight = orchestration._frozen_preflight
    def synchronized_preflight(*args, **kwargs):
        result = original_preflight(*args, **kwargs)
        barrier.wait(timeout=5)
        return result
    monkeypatch.setattr(orchestration, "_frozen_preflight", synchronized_preflight)
    monkeypatch.setattr(orchestration, "wait_for_worker_readiness", lambda **kwargs: ready_batch("planner", "reviewer"))
    monkeypatch.setattr(
        orchestration,
        "run_sequential_workflow",
        lambda **kwargs: complete_fake_workflow(store, kwargs["run_id"]),
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _index: run_mission(
                    config=config,
                    store=store,
                    backend=backend,
                    mission_id=preview["mission_id"],
                ),
                range(2),
            )
        )

    assert all(result["status"] in {"preparing", "running", "completed"} for result in results)
    assert store.mission_by_id(preview["mission_id"])["status"] == "completed"
    assert backend.spawned == ["planner", "reviewer"]
    assert len(store.load()["workflow_runs"]) == 1
    assert sum(event["event_type"] == "mission_confirmed" for event in store.all_events()) == 1


def test_plan_drift_stops_before_runtime_and_is_not_resumable(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = seeded_mission(tmp_path, monkeypatch)
    state = store.load()
    state["plans"][0]["plan"]["steps"][0]["task"] = "drifted"
    store.save(state)
    backend = MissionBackend()

    result = run_mission(config=config, store=store, backend=backend, mission_id=preview["mission_id"])

    assert result["status"] == "stopped"
    assert result["stop_reason"] == "plan_drift"
    assert result["can_resume"] is False
    assert backend.created == 0
    assert backend.spawned == []
    assert store.load().get("workflow_runs", []) == []


def test_frozen_spawn_rejects_external_running_pane(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = seeded_mission(tmp_path, monkeypatch)
    backend = MissionBackend()
    backend.panes["planner"] = "%9"
    from agentdeck.models import AgentRuntimeBinding
    store.bind_agent(AgentRuntimeBinding("planner", "%9", config.runtime.session_name, config.root, "running"))

    result = run_mission(config=config, store=store, backend=backend, mission_id=preview["mission_id"])

    assert result["stop_reason"] == "worker_runtime_drift"
    assert result["can_resume"] is False
    assert backend.spawned == []
    assert store.load().get("workflow_runs", []) == []


def test_frozen_reuse_rejects_lost_pane_without_spawning(tmp_path, monkeypatch) -> None:
    root, config, store, _ = project(tmp_path)
    backend = MissionBackend()
    backend.panes.update({"planner": "%1", "reviewer": "%2"})
    from agentdeck.models import AgentRuntimeBinding
    for agent_id, pane_id in backend.panes.items():
        store.bind_agent(AgentRuntimeBinding(agent_id, pane_id, config.runtime.session_name, config.root, "running"))
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")
    preview = create_mission_preview(config=config, store=store, provider=RecordingProvider(), user_message=MESSAGE, timeout_seconds=180)
    backend.panes.pop("reviewer")

    result = run_mission(config=config, store=store, backend=backend, mission_id=preview["mission_id"])

    assert result["stop_reason"] == "worker_runtime_drift"
    assert result["can_resume"] is False
    assert backend.spawned == []
    assert store.load().get("workflow_runs", []) == []
    assert root.exists()


def test_partial_spawn_failure_keeps_first_binding_and_dispatches_zero(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = seeded_mission(tmp_path, monkeypatch)
    backend = MissionBackend(fail_spawn_for="reviewer")

    result = run_mission(config=config, store=store, backend=backend, mission_id=preview["mission_id"])

    assert result["stop_reason"] == "worker_start_failed"
    assert store.agent_binding("planner")["pane_id"] == "%1"
    assert store.load().get("workflow_runs", []) == []
    assert "SECRET" not in repr(result)
    assert "SECRET" not in repr(store.all_events())

    backend.fail_spawn_for = None
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.wait_for_worker_readiness",
        lambda **kwargs: ready_batch("planner", "reviewer"),
    )
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.run_sequential_workflow",
        lambda **kwargs: complete_fake_workflow(store, kwargs["run_id"]),
    )
    resumed = resume_mission(
        config=config, store=store, backend=backend, mission_id=preview["mission_id"]
    )
    assert resumed["status"] == "completed"
    assert backend.spawned == ["planner", "reviewer", "reviewer"]


def test_setup_required_stops_before_workflow_dispatch(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = seeded_mission(tmp_path, monkeypatch)
    backend = MissionBackend()
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.wait_for_worker_readiness",
        lambda **kwargs: WorkerReadinessBatch(
            False,
            (
                WorkerReadiness("planner", "codex", "ready", None),
                WorkerReadiness("reviewer", "claude", "setup_required", "SECRET login screen"),
            ),
        ),
    )

    result = run_mission(config=config, store=store, backend=backend, mission_id=preview["mission_id"])

    assert result["stop_reason"] == "worker_setup_required"
    assert result["can_resume"] is True
    assert store.load().get("workflow_runs", []) == []
    assert "SECRET" not in repr(result)
    assert "SECRET" not in repr(store.all_events())
    assert [
        event["event_type"]
        for event in store.all_events()
        if event["event_type"].startswith("mission_")
    ][-6:] == [
        "mission_confirmed",
        "mission_worker_preparing",
        "mission_worker_preparing",
        "mission_worker_ready",
        "mission_worker_blocked",
        "mission_stopped",
    ]


def test_readiness_exception_is_bounded_and_redacted(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = seeded_mission(tmp_path, monkeypatch)
    backend = MissionBackend()
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.wait_for_worker_readiness",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("SECRET readiness")),
    )

    result = run_mission(config=config, store=store, backend=backend, mission_id=preview["mission_id"])

    assert result["stop_reason"] == "worker_readiness_failed"
    assert store.load().get("workflow_runs", []) == []
    assert "SECRET" not in repr(result)
    assert "SECRET" not in repr(store.all_events())


@pytest.mark.parametrize("phase", ["spawn", "readiness"])
def test_preparing_keyboard_interrupt_stops_and_can_resume(
    tmp_path, monkeypatch, phase
) -> None:
    _root, config, store, preview = seeded_mission(tmp_path, monkeypatch)
    backend = MissionBackend()
    if phase == "spawn":
        original = backend.spawn_agent
        calls = 0
        def interrupt_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise KeyboardInterrupt
            return original(*args, **kwargs)
        backend.spawn_agent = interrupt_once
    else:
        monkeypatch.setattr(
            "agentdeck.mission_orchestration.wait_for_worker_readiness",
            lambda **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
        )

    with pytest.raises(KeyboardInterrupt):
        run_mission(config=config, store=store, backend=backend, mission_id=preview["mission_id"])
    stopped = interrupt_mission(store, preview["mission_id"])

    assert stopped["status"] == "stopped"
    assert stopped["stop_reason"] == "interrupted"
    assert stopped["blockers"] == []


def test_corrupt_spawn_audit_stops_partial_resume_without_extra_spawn(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = seeded_mission(tmp_path, monkeypatch)
    backend = MissionBackend(fail_spawn_for="reviewer")
    first = run_mission(config=config, store=store, backend=backend, mission_id=preview["mission_id"])
    assert first["stop_reason"] == "worker_start_failed"
    store.events_path.write_text('{"broken":', encoding="utf-8")
    backend.fail_spawn_for = None
    before = list(backend.spawned)

    result = resume_mission(config=config, store=store, backend=backend, mission_id=preview["mission_id"])

    assert result["stop_reason"] == "mission_audit_invalid"
    assert result["can_resume"] is False
    assert backend.spawned == before
    assert store.load().get("workflow_runs", []) == []


def test_missing_workflow_reference_stops_as_state_drift(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = seeded_mission(tmp_path, monkeypatch)
    backend = CorrelatedMissionBackend(interrupt_step2=True)
    with pytest.raises(KeyboardInterrupt):
        run_mission(config=config, store=store, backend=backend, mission_id=preview["mission_id"], readiness_timeout_seconds=2)
    interrupted = interrupt_mission(store, preview["mission_id"])
    state = store.load()
    state["workflow_runs"] = []
    store.save(state)

    result = resume_mission(config=config, store=store, backend=backend, mission_id=preview["mission_id"], readiness_timeout_seconds=2)

    assert result["stop_reason"] == "workflow_state_drift"
    assert result["can_resume"] is False
    assert result["workflow_run_id"] == interrupted["workflow_run_id"]
    assert len(backend.sent) == 2


def test_workflow_exception_stops_both_records_without_secret(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = seeded_mission(tmp_path, monkeypatch)
    backend = MissionBackend()
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.wait_for_worker_readiness",
        lambda **kwargs: ready_batch("planner", "reviewer"),
    )
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.run_sequential_workflow",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("SECRET workflow")),
    )

    result = run_mission(config=config, store=store, backend=backend, mission_id=preview["mission_id"])

    assert result["stop_reason"] == "workflow_failed"
    workflow = store.workflow_run_by_id(result["workflow_run_id"])
    assert workflow["status"] == "stopped"
    assert workflow["stop_reason"] == "workflow_failed"
    assert "SECRET" not in repr(result)
    assert "SECRET" not in repr(store.all_events())


def test_confirmation_audit_failure_does_not_leave_preparing(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = seeded_mission(tmp_path, monkeypatch)
    backend = MissionBackend()
    monkeypatch.setattr(
        store,
        "append_event",
        lambda event: (_ for _ in ()).throw(OSError("SECRET audit")),
    )

    result = run_mission(config=config, store=store, backend=backend, mission_id=preview["mission_id"])

    assert result["status"] == "stopped"
    assert result["stop_reason"] == "mission_audit_failed"
    assert backend.spawned == []


def test_interrupt_preparing_mission_also_interrupts_existing_workflow(tmp_path, monkeypatch) -> None:
    _root, _config, store, preview = seeded_mission(tmp_path, monkeypatch)
    from agentdeck.models import utc_now
    from agentdeck.workflow import authorized_steps
    store.claim_mission_execution(
        preview["mission_id"], resuming=False, confirmed_at=utc_now()
    )
    workflow = store.create_workflow_run(
        plan_id=preview["plan_id"],
        plan_hash=preview["plan_hash"],
        timeout_seconds=180,
        authorized_steps=authorized_steps(store.plan_by_id(preview["plan_id"])),
    )
    store.update_mission(preview["mission_id"], workflow_run_id=workflow["run_id"])

    mission = interrupt_mission(store, preview["mission_id"])

    assert mission["status"] == "stopped"
    assert mission["stop_reason"] == "interrupted"
    interrupted = store.workflow_run_by_id(workflow["run_id"])
    assert interrupted["status"] == "interrupted"
    assert interrupted["stop_reason"] == "interrupted"


def test_workflow_started_audit_failure_fallback_stops_orphaned_run(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = seeded_mission(tmp_path, monkeypatch)
    backend = MissionBackend()
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.wait_for_worker_readiness",
        lambda **kwargs: ready_batch("planner", "reviewer"),
    )
    import agentdeck.mission_orchestration as orchestration
    original_audit = orchestration._audit
    failed = False
    def fail_workflow_started(store_arg, event_type, **payload):
        nonlocal failed
        if event_type == "workflow_started" and not failed:
            failed = True
            raise OSError("SECRET workflow audit")
        return original_audit(store_arg, event_type, **payload)
    monkeypatch.setattr(orchestration, "_audit", fail_workflow_started)

    with pytest.raises(OSError):
        run_mission(config=config, store=store, backend=backend, mission_id=preview["mission_id"])
    mission = interrupt_mission(store, preview["mission_id"])

    assert mission["status"] == "stopped"
    assert mission["stop_reason"] == "interrupted"
    assert mission["blockers"] == []
    workflow = store.workflow_run_by_id(mission["workflow_run_id"])
    assert workflow["status"] == "interrupted"
    assert workflow["status"] != "running"
    assert "SECRET" not in repr(store.all_events())


def test_resume_reuses_existing_workflow_and_duplicate_completion_is_idempotent(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = seeded_mission(tmp_path, monkeypatch)
    backend = MissionBackend()
    for agent_id, pane_id in (("planner", "%1"), ("reviewer", "%2")):
        backend.panes[agent_id] = pane_id
        from agentdeck.models import AgentRuntimeBinding
        store.bind_agent(AgentRuntimeBinding(agent_id, pane_id, config.runtime.session_name, config.root, "running"))
        from agentdeck.models import EventRecord
        store.append_event(EventRecord.create("agent_spawned", {
            "mission_id": preview["mission_id"], "agent_id": agent_id,
            "pane_id": pane_id, "session_name": config.runtime.session_name,
            "cwd": config.root,
        }))
    run = store.create_workflow_run(
        plan_id=preview["plan_id"],
        plan_hash=preview["plan_hash"],
        timeout_seconds=180,
        authorized_steps=__import__("agentdeck.workflow", fromlist=["authorized_steps"]).authorized_steps(store.plan_by_id(preview["plan_id"])),
    )
    store.update_workflow_run(run["run_id"], status="interrupted", stop_reason="interrupted")
    from agentdeck.models import utc_now
    store.update_mission(preview["mission_id"], status="preparing", confirmed_at=utc_now())
    store.update_mission(preview["mission_id"], status="running", workflow_run_id=run["run_id"])
    store.update_mission(preview["mission_id"], status="interrupted", stop_reason="interrupted")
    monkeypatch.setattr("agentdeck.mission_orchestration.wait_for_worker_readiness", lambda **kwargs: ready_batch("planner", "reviewer"))
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.run_sequential_workflow",
        lambda **kwargs: complete_fake_workflow(store, kwargs["run_id"]),
    )

    result = resume_mission(config=config, store=store, backend=backend, mission_id=preview["mission_id"])
    again = run_mission(config=config, store=store, backend=backend, mission_id=preview["mission_id"])

    assert result["workflow_run_id"] == run["run_id"]
    assert again["status"] == "completed"
    assert len(store.load()["workflow_runs"]) == 1
    assert backend.spawned == []


def test_mission_status_payload_is_contract_valid_and_read_only(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = seeded_mission(tmp_path, monkeypatch)
    before = store.state_path.read_bytes()

    payload = mission_status_payload(config, store, store.mission_by_id(preview["mission_id"]))

    from agentdeck.contracts import validate_mission_status_contract
    assert validate_mission_status_contract(payload) == {"ok": True, "errors": []}
    assert store.state_path.read_bytes() == before


def test_run_mission_executes_real_eight_turn_correlated_workflow(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = seeded_mission(tmp_path, monkeypatch)
    backend = CorrelatedMissionBackend()

    result = run_mission(
        config=config,
        store=store,
        backend=backend,
        mission_id=preview["mission_id"],
        readiness_timeout_seconds=2,
    )

    workflow = store.workflow_run_by_id(result["workflow_run_id"])
    assert result["status"] == "completed"
    assert len(workflow["turns"]) == 8
    assert len(backend.sent) == 8
    assert [turn["agent_id"] for turn in workflow["turns"]] == [
        "planner", "reviewer", "planner", "reviewer",
        "planner", "reviewer", "planner", "reviewer",
    ]
    assert len(store.load()["messages"]) == 8
    assert len(store.load()["replies"]) == 8
    mission_events = [event for event in store.all_events() if event["event_type"].startswith("mission_")]
    assert [event["event_type"] for event in mission_events] == [
        "mission_preview_created", "mission_confirmed",
        "mission_worker_preparing", "mission_worker_preparing",
        "mission_worker_ready", "mission_worker_ready",
        "mission_workflow_started", "mission_completed",
    ]
    allowed = {
        "mission_preview_created": {"mission_id", "plan_id", "selected_agent_ids", "step_count"},
        "mission_confirmed": {"mission_id", "plan_id"},
        "mission_worker_preparing": {"mission_id", "agent_id", "action"},
        "mission_worker_ready": {"mission_id", "agent_id", "status"},
        "mission_workflow_started": {"mission_id", "workflow_run_id", "plan_id"},
        "mission_completed": {"mission_id", "workflow_run_id"},
    }
    assert all(set(event["payload"]) == allowed[event["event_type"]] for event in mission_events)
    assert not any(token in repr(mission_events).lower() for token in ("prompt", "command", "output", "credential", "secret"))


def test_two_natural_language_messages_complete_baijiaxing_mission(
    tmp_path, monkeypatch, capsys
) -> None:
    from agentdeck import cli

    root, _config, _store, config_path = project(tmp_path)
    monkeypatch.chdir(root)
    config_before = config_path.read_bytes()
    provider = RecordingProvider()
    backend = BaijiaxingMissionBackend()
    monkeypatch.setattr(cli, "leader_provider", lambda _name: provider)
    monkeypatch.setattr(cli, "TmuxBackend", lambda: backend)
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}"
    )

    assert cli.main(["leader", "chat", "--message", MESSAGE]) == 0
    preview = json.loads(capsys.readouterr().out)
    mission_id = preview["mission_preview_card"]["mission_id"]
    assert cli.main(
        ["leader", "chat", "--message", f"批准执行 {mission_id}"]
    ) == 0
    completed = json.loads(capsys.readouterr().out)

    assert completed["mode"] == "mission_run"
    assert completed["mission_run_card"]["status"] == "completed"
    assert [turn["agent_id"] for turn in completed["mission_run_card"]["turns"]] == [
        "planner", "reviewer", "planner", "reviewer",
        "planner", "reviewer", "planner", "reviewer",
    ]
    assert [turn["handoff"]["summary"] for turn in completed["mission_run_card"]["turns"]] == list(
        BaijiaxingMissionBackend.summaries
    )
    assert not any(
        token in repr(completed["mission_run_card"]["turns"]).lower()
        for token in ("handoff_token", "prompt", "full_output", "credential", "secret")
    )

    store = StateStore(root)
    state = store.load()
    workflow = state["workflow_runs"][0]
    assert len(state["missions"]) == len(state["plans"]) == len(state["workflow_runs"]) == 1
    assert len(state["messages"]) == len(state["replies"]) == len(workflow["turns"]) == len(backend.sent) == 8
    assert len(state["chat_turns"]) == 2
    assert state["approvals"] == []
    assert state["skill_loads"] == []
    assert state["leader_actions"] == []
    assert len(state["jobs"]) == 8
    event_types = [event["event_type"] for event in store.all_events()]
    for event_type in (
        "mission_preview_created", "mission_confirmed", "mission_workflow_started",
        "mission_completed",
    ):
        assert event_types.count(event_type) == 1
    assert event_types.count("workflow_step_dispatched") == 8
    assert event_types.count("workflow_step_completed") == 8
    assert config_path.read_bytes() == config_before


def test_real_interrupted_step_two_resumes_without_duplicate_dispatch(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = seeded_mission(tmp_path, monkeypatch)
    backend = CorrelatedMissionBackend(interrupt_step2=True)

    with pytest.raises(KeyboardInterrupt):
        run_mission(config=config, store=store, backend=backend, mission_id=preview["mission_id"], readiness_timeout_seconds=2)
    interrupted = interrupt_mission(store, preview["mission_id"])
    before = store.workflow_run_by_id(interrupted["workflow_run_id"])
    assert [turn["status"] for turn in before["turns"]] == ["completed", "dispatched"]

    result = resume_mission(config=config, store=store, backend=backend, mission_id=preview["mission_id"], readiness_timeout_seconds=2)

    workflow = store.workflow_run_by_id(result["workflow_run_id"])
    assert result["status"] == "completed"
    assert len(store.load()["workflow_runs"]) == 1
    assert len(workflow["turns"]) == 8
    assert len(store.load()["messages"]) == 8
    assert len(store.load()["replies"]) == 8
    assert len(backend.sent) == 8
    first_two_tokens = [
        next(line for line in prompt.splitlines() if "handoff token exactly" in line)
        for _pane, prompt in backend.sent[:2]
    ]
    assert sum("_step_1" in token for token in first_two_tokens) == 1
    assert sum("_step_2" in token for token in first_two_tokens) == 1


def test_create_preview_selects_workers_freezes_serial_plan_and_never_touches_runtime(
    tmp_path, monkeypatch
) -> None:
    root, config, store, config_path = project(tmp_path)
    config_before = config_path.read_bytes()
    provider = RecordingProvider()
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")

    result = create_mission_preview(
        config=config,
        store=store,
        provider=provider,
        user_message=MESSAGE,
        timeout_seconds=180,
    )

    assert validate_mission_preview_contract(result) == {"ok": True, "errors": []}
    assert [item["provider"] for item in result["selected_agents"]] == ["codex", "claude"]
    assert [item["agent_id"] for item in result["selected_agents"]] == ["planner", "reviewer"]
    assert all(set(item) == {
        "agent_id", "provider", "role", "workspace_mode", "runtime_status",
        "effective_model", "model_source",
    } for item in result["selected_agents"])
    assert result["step_count"] == 8
    assert result["can_start"] is True
    assert result["confirmation_command"].endswith(f'批准执行 {result["mission_id"]}"')
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert [agent.agent_id for agent in request.config.agents] == ["planner", "reviewer"]
    assert "strictly serial" in request.task
    assert "exactly 8 steps" in request.task
    assert "planner, reviewer" in request.task
    assert "only after the previous step has completed" in request.task
    assert "one overall Mission confirmation" in request.task
    assert "must not request per-step approval" in request.task
    assert "Every step must require human approval" not in request.task
    assert config_path.read_bytes() == config_before
    state = store.load()
    assert state.get("workflow_runs", []) == []
    assert state["jobs"] == []
    assert state["messages"] == []
    assert state["approvals"] == []
    assert state.get("inbox", {}) == {}
    assert state["skill_loads"] == []
    assert len(state["plans"]) == 1
    assert len(state["missions"]) == 1
    assert [event["event_type"] for event in store.list_events(limit=10)] == [
        "mission_preview_created"
    ]
    assert root.exists()


def test_create_preview_preserves_compact_loaded_leader_skill_context(tmp_path, monkeypatch) -> None:
    _root, config, store, _config_path = project(tmp_path)
    state = store.load()
    state["skill_loads"] = [
        {
            "load_id": "sld_demo",
            "agent_id": "leader",
            "purpose": "plan serial work",
            "name": "sequential-handoff",
            "source": "project",
            "path": ".agentdeck/skills/sequential-handoff/SKILL.md",
            "content_hash": "sha256:" + "a" * 64,
            "content_snapshot": "SECRET FULL SKILL CONTENT",
            "description": "Plan fixed handoffs",
            "required_tools": [],
            "risk": "low",
            "loaded_at": "2026-07-11T00:00:00+00:00",
        }
    ]
    store.save(state)
    provider = RecordingProvider()
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")

    result = create_mission_preview(
        config=config,
        store=store,
        provider=provider,
        user_message=MESSAGE,
        timeout_seconds=180,
    )

    skill_context = provider.requests[0].skill_context
    assert skill_context is not None
    assert skill_context["count"] == 1
    assert "content_snapshot" not in repr(skill_context)
    plan_record = store.plan_by_id(result["plan_id"])
    assert plan_record["skill_context"] == skill_context
    assert "content_snapshot" not in repr(plan_record["skill_context"])
    assert len(store.load()["skill_loads"]) == 1


def test_create_preview_passes_selected_effective_models_without_rewriting_config(
    tmp_path, monkeypatch
) -> None:
    _root, config, store, config_path = project(tmp_path)
    config = replace(
        config,
        leader=replace(config.leader, provider="codex-cli", model="gpt-5.5"),
        agents=tuple(
            replace(item, command="claude --model opus-4.8")
            if item.agent_id == "reviewer"
            else item
            for item in config.agents
        ),
    )
    config_before = config_path.read_bytes()
    provider = RecordingProvider()
    provider.name = "codex-cli"
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")

    result = create_mission_preview(
        config=config,
        store=store,
        provider=provider,
        user_message=MESSAGE,
        timeout_seconds=180,
    )

    assert [
        (item["effective_model"], item["model_source"])
        for item in result["selected_agents"]
    ] == [
        ("gpt-5.5", "leader_inherited"),
        ("opus-4.8", "configured_command"),
    ]
    assert provider.requests[0].config.agents[0].command == "codex --model gpt-5.5"
    assert provider.requests[0].config.agents[1].command == "claude --model opus-4.8"
    assert config_path.read_bytes() == config_before


def test_create_preview_reuses_running_bindings_without_claiming_derived_models(
    tmp_path, monkeypatch
) -> None:
    _root, config, store, _config_path = project(tmp_path)
    config = replace(
        config,
        leader=replace(config.leader, provider="codex-cli", model="gpt-5.5"),
    )
    state = store.load()
    state["agents"] = {
        agent_id: {
            "agent_id": agent_id,
            "status": "running",
            "pane_id": pane_id,
        }
        for agent_id, pane_id in (("planner", "%1"), ("reviewer", "%2"))
    }
    store.save(state)
    provider = RecordingProvider()
    provider.name = "codex-cli"
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")

    result = create_mission_preview(
        config=config,
        store=store,
        provider=provider,
        user_message=MESSAGE,
        timeout_seconds=180,
    )

    assert [
        (item["runtime_status"], item["effective_model"], item["model_source"])
        for item in result["selected_agents"]
    ] == [
        ("running", None, "running_binding"),
        ("running", None, "running_binding"),
    ]
    assert [
        (item["action"], item["effective_model"], item["model_source"])
        for item in result["startup_actions"]
    ] == [
        ("reuse", None, "running_binding"),
        ("reuse", None, "running_binding"),
    ]
    assert [item.command for item in provider.requests[0].config.agents] == [
        "codex",
        "claude",
    ]


def test_running_binding_without_pane_uses_spawn_derivation(tmp_path, monkeypatch) -> None:
    _root, config, store, _config_path = project(tmp_path)
    config = replace(
        config,
        leader=replace(config.leader, provider="codex-cli", model="gpt-5.5"),
    )
    state = store.load()
    state["agents"] = {
        "planner": {"agent_id": "planner", "status": "running", "pane_id": None},
        "reviewer": {"agent_id": "reviewer", "status": "configured", "pane_id": None},
    }
    store.save(state)
    provider = RecordingProvider()
    provider.name = "codex-cli"
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")

    result = create_mission_preview(
        config=config, store=store, provider=provider, user_message=MESSAGE, timeout_seconds=180
    )

    planner = result["selected_agents"][0]
    startup = result["startup_actions"][0]
    assert (planner["effective_model"], planner["model_source"]) == (
        "gpt-5.5",
        "leader_inherited",
    )
    assert startup["action"] == "spawn"
    assert provider.requests[0].config.agents[0].command == "codex --model gpt-5.5"


@pytest.mark.parametrize(
    "binding",
    [
        {"agent_id": "planner", "status": "corrupt", "pane_id": None},
        {"agent_id": "planner", "status": "running", "pane_id": ""},
        {"agent_id": "planner", "status": "running", "pane_id": {"secret": "BINDING_MARKER"}},
    ],
)
def test_malformed_selected_binding_fails_before_provider_and_any_write(
    tmp_path, monkeypatch, binding
) -> None:
    _root, config, store, config_path = project(tmp_path)
    config_before = config_path.read_bytes()
    state = store.load()
    state["agents"] = {"planner": binding}
    store.save(state)
    state_before = store.state_path.read_bytes()
    events_before = store.events_path.read_bytes()
    provider = RecordingProvider()

    with pytest.raises(ValueError, match="^mission preview binding invalid$") as exc_info:
        create_mission_preview(
            config=config, store=store, provider=provider, user_message=MESSAGE, timeout_seconds=180
        )

    assert "BINDING_MARKER" not in str(exc_info.value)
    assert provider.requests == []
    assert store.state_path.read_bytes() == state_before
    assert store.events_path.read_bytes() == events_before
    assert store.list_plans() == []
    assert store.list_missions() == []
    assert config_path.read_bytes() == config_before


@pytest.mark.parametrize("case", ["duplicate", "fewer_than_two"])
def test_selection_blockers_fail_before_provider_and_any_write(
    tmp_path, monkeypatch, case
) -> None:
    _root, config, store, config_path = project(tmp_path)
    config_before = config_path.read_bytes()
    if case == "duplicate":
        config = replace(config, agents=(config.agents[0], config.agents[0], config.agents[2]))
    else:
        config = replace(config, agents=(config.agents[0],))
    provider = RecordingProvider()
    events_before = store.events_path.read_bytes()

    with pytest.raises(ValueError, match="^mission preview selection invalid$"):
        create_mission_preview(
            config=config, store=store, provider=provider, user_message=MESSAGE, timeout_seconds=180
        )

    assert provider.requests == []
    assert store.list_plans() == []
    assert store.list_missions() == []
    assert store.events_path.read_bytes() == events_before
    assert config_path.read_bytes() == config_before


@pytest.mark.parametrize("provider_name", ["", "codex-cli"])
def test_provider_identity_invalid_fails_before_provider_and_any_write(
    tmp_path, monkeypatch, provider_name
) -> None:
    _root, config, store, config_path = project(tmp_path)
    config_before = config_path.read_bytes()
    provider = RecordingProvider()
    provider.name = provider_name
    events_before = store.events_path.read_bytes()

    with pytest.raises(ValueError, match="^mission preview provider invalid$") as exc_info:
        create_mission_preview(
            config=config, store=store, provider=provider, user_message=MESSAGE, timeout_seconds=180
        )

    if provider_name:
        assert provider_name not in str(exc_info.value)
    assert provider.requests == []
    assert store.list_plans() == []
    assert store.list_missions() == []
    assert store.events_path.read_bytes() == events_before
    assert config_path.read_bytes() == config_before


@pytest.mark.parametrize(
    "configured_provider",
    ["fake", "codex-cli", "claude-cli", "deepseek"],
)
def test_provider_identity_is_canonicalized_for_payload_and_state(
    tmp_path, monkeypatch, configured_provider
) -> None:
    _root, config, store, _config_path = project(tmp_path)
    config = replace(
        config,
        leader=replace(config.leader, provider=configured_provider),
    )
    provider = RecordingProvider()
    provider.name = f" {configured_provider.upper()} "
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")

    result = create_mission_preview(
        config=config, store=store, provider=provider, user_message=MESSAGE, timeout_seconds=180
    )

    assert validate_mission_preview_contract(result) == {"ok": True, "errors": []}
    assert result["provider"] == configured_provider
    assert result["leader_backend"]["provider"] == configured_provider
    plan = store.plan_by_id(result["plan_id"])
    mission = store.mission_by_id(result["mission_id"])
    assert plan["provider"] == configured_provider
    assert plan["leader_backend"]["provider"] == configured_provider
    assert mission["provider"] == configured_provider
    assert mission["leader_backend"]["provider"] == configured_provider


def test_non_object_agents_state_fails_before_provider_and_any_business_write(
    tmp_path, monkeypatch
) -> None:
    _root, config, store, config_path = project(tmp_path)
    config_before = config_path.read_bytes()
    state = store.load()
    state["agents"] = ["STATE_MARKER"]
    store.save(state)
    state_before = store.state_path.read_bytes()
    events_before = store.events_path.read_bytes()
    provider = RecordingProvider()

    with pytest.raises(ValueError, match="^mission preview state invalid$") as exc_info:
        create_mission_preview(
            config=config, store=store, provider=provider, user_message=MESSAGE, timeout_seconds=180
        )

    assert "STATE_MARKER" not in str(exc_info.value)
    assert provider.requests == []
    assert store.state_path.read_bytes() == state_before
    assert store.events_path.read_bytes() == events_before
    assert config_path.read_bytes() == config_before


def test_project_view_state_failure_is_sanitized_before_provider_and_any_write(
    tmp_path, monkeypatch
) -> None:
    _root, config, store, config_path = project(tmp_path)
    config_before = config_path.read_bytes()
    events_before = store.events_path.read_bytes()
    provider = RecordingProvider()
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")
    monkeypatch.setattr(
        "agentdeck.mission_orchestration._explicit_leader_skill_context",
        lambda *_args: (_ for _ in ()).throw(ValueError("PROJECT_VIEW_MARKER")),
    )

    with pytest.raises(ValueError, match="^mission preview state invalid$") as exc_info:
        create_mission_preview(
            config=config, store=store, provider=provider, user_message=MESSAGE, timeout_seconds=180
        )

    assert "PROJECT_VIEW_MARKER" not in str(exc_info.value)
    assert provider.requests == []
    assert store.list_plans() == []
    assert store.list_missions() == []
    assert store.events_path.read_bytes() == events_before
    assert config_path.read_bytes() == config_before


def test_create_preview_reports_missing_command_without_echoing_command(tmp_path, monkeypatch) -> None:
    _root, config, store, _config_path = project(tmp_path)
    provider = RecordingProvider()
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.shutil.which",
        lambda command: "/bin/codex" if command == "codex" else None,
    )

    result = create_mission_preview(
        config=config,
        store=store,
        provider=provider,
        user_message=MESSAGE,
        timeout_seconds=180,
    )

    assert validate_mission_preview_contract(result) == {"ok": True, "errors": []}
    assert result["can_start"] is False
    assert result["blockers"] == ["worker command not found: reviewer"]
    assert "claude" not in repr(result["blockers"])
    confirm = next(item for item in result["controls"] if item["kind"] == "execute")
    assert confirm["enabled"] is False
    assert confirm["blocker"] == result["blockers"][0]
    assert len(store.load()["plans"]) == 1
    assert len(store.load()["missions"]) == 1


def test_create_preview_compacts_malformed_command_blocker_without_echoing_command(
    tmp_path, monkeypatch
) -> None:
    _root, config, store, _config_path = project(tmp_path)
    agents = tuple(
        replace(item, command='claude "') if item.agent_id == "reviewer" else item
        for item in config.agents
    )
    config = replace(config, agents=agents)
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")

    result = create_mission_preview(
        config=config,
        store=store,
        provider=RecordingProvider(),
        user_message=MESSAGE,
        timeout_seconds=180,
    )

    assert result["can_start"] is False
    assert result["blockers"] == ["invalid worker command: reviewer"]
    assert 'claude "' not in repr(result["blockers"])


def test_invalid_provider_plan_fails_closed_before_any_state_or_event_write(
    tmp_path, monkeypatch
) -> None:
    _root, config, store, _config_path = project(tmp_path)
    provider = RecordingProvider({"goal": "bad", "summary": "bad", "steps": []})
    state_before = store.state_path.read_bytes() if store.state_path.exists() else None
    events_before = store.events_path.read_bytes()
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")

    with pytest.raises(ValueError, match="^mission preview plan invalid$"):
        create_mission_preview(
            config=config,
            store=store,
            provider=provider,
            user_message=MESSAGE,
            timeout_seconds=180,
        )

    assert store.list_plans() == []
    assert store.list_missions() == []
    assert store.events_path.read_bytes() == events_before
    if state_before is not None:
        assert store.state_path.read_bytes() == state_before


def test_invalid_compact_summaries_fail_before_plan_record_write(
    tmp_path, monkeypatch
) -> None:
    _root, config, store, _config_path = project(tmp_path)
    provider = RecordingProvider()
    events_before = store.events_path.read_bytes()
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.selected_agent_summaries",
        lambda *_args: [{"agent_id": {"marker": "SUMMARY_MARKER"}}],
    )

    with pytest.raises(ValueError, match="^mission preview summaries invalid$") as exc_info:
        create_mission_preview(
            config=config, store=store, provider=provider, user_message=MESSAGE, timeout_seconds=180
        )

    assert "SUMMARY_MARKER" not in str(exc_info.value)
    assert len(provider.requests) == 1
    assert store.list_plans() == []
    assert store.list_missions() == []
    assert store.events_path.read_bytes() == events_before


@pytest.mark.parametrize(
    "error",
    [
        URLError("URL_MARKER"),
        subprocess.TimeoutExpired("TIMEOUT_MARKER", 1),
        RuntimeError("RUNTIME_MARKER"),
        ValueError("VALUE_MARKER"),
    ],
)
def test_provider_exceptions_are_sanitized_and_write_nothing(
    tmp_path, monkeypatch, error
) -> None:
    _root, config, store, _config_path = project(tmp_path)
    provider = ExplodingProvider(error)
    events_before = store.events_path.read_bytes()
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")

    with pytest.raises(ValueError, match="^mission preview provider failed$") as exc_info:
        create_mission_preview(
            config=config, store=store, provider=provider, user_message=MESSAGE, timeout_seconds=180
        )

    assert "MARKER" not in str(exc_info.value)
    assert provider.calls == 1
    assert store.list_plans() == []
    assert store.list_missions() == []
    assert store.events_path.read_bytes() == events_before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("parallel", False),
        ("dynamic_steps", []),
        ("dag", None),
        ("cycle", False),
    ],
)
def test_forbidden_plan_metadata_presence_fails_closed_before_any_write(
    tmp_path, monkeypatch, field, value
) -> None:
    _root, config, store, _config_path = project(tmp_path)
    plan = eight_step_plan()
    plan[field] = value
    provider = RecordingProvider(plan)
    state_before = store.state_path.read_bytes() if store.state_path.exists() else None
    events_before = store.events_path.read_bytes()
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")

    with pytest.raises(ValueError, match="^mission preview plan invalid$"):
        create_mission_preview(
            config=config,
            store=store,
            provider=provider,
            user_message=MESSAGE,
            timeout_seconds=180,
        )

    assert store.list_plans() == []
    assert store.list_missions() == []
    assert store.events_path.read_bytes() == events_before
    if state_before is not None:
        assert store.state_path.read_bytes() == state_before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("goal", "Complete the chain with per-step human approval"),
        ("summary", "Every step requires human approval before dispatch"),
        ("summary", "每一步人工批准后再继续"),
        ("goal", "逐步批准全部八轮"),
    ],
)
def test_per_step_approval_plan_language_fails_closed_before_any_write(
    tmp_path, monkeypatch, field, value
) -> None:
    _root, config, store, _config_path = project(tmp_path)
    plan = eight_step_plan()
    plan[field] = value
    provider = RecordingProvider(plan)
    state_before = store.state_path.read_bytes() if store.state_path.exists() else None
    events_before = store.events_path.read_bytes()
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")

    with pytest.raises(ValueError, match="^mission preview plan invalid$"):
        create_mission_preview(
            config=config,
            store=store,
            provider=provider,
            user_message=MESSAGE,
            timeout_seconds=180,
        )

    assert store.list_plans() == []
    assert store.list_missions() == []
    assert store.events_path.read_bytes() == events_before
    if state_before is not None:
        assert store.state_path.read_bytes() == state_before


def test_duplicate_request_creates_distinct_audited_previews(tmp_path, monkeypatch) -> None:
    _root, config, store, _config_path = project(tmp_path)
    provider = RecordingProvider()
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")

    first = create_mission_preview(
        config=config, store=store, provider=provider, user_message=MESSAGE, timeout_seconds=180
    )
    second = create_mission_preview(
        config=config, store=store, provider=provider, user_message=MESSAGE, timeout_seconds=180
    )

    assert first["mission_id"] != second["mission_id"]
    assert first["plan_id"] != second["plan_id"]
    assert len(store.list_missions()) == 2
    assert len(store.list_plans()) == 2
    assert [event["event_type"] for event in store.list_events(limit=10)] == [
        "mission_preview_created",
        "mission_preview_created",
    ]
