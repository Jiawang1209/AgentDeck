from __future__ import annotations

import pytest

from agentdeck.config import load_config, write_default_config
from agentdeck.workflow import (
    authorized_steps,
    build_compact_handoff,
    parse_correlated_reply,
    run_sequential_workflow,
    workflow_plan_hash,
)
from agentdeck.state import StateStore


PLAN = {
    "plan_id": "pln_demo",
    "plan": {
        "steps": [
            {
                "step": 1,
                "agent_id": "planner",
                "role": "planning",
                "task": "Prepare evidence",
                "requires_approval": True,
            },
            {
                "step": 2,
                "agent_id": "reviewer",
                "role": "review",
                "task": "Review evidence",
                "requires_approval": True,
            },
        ]
    },
}


def test_workflow_plan_hash_is_deterministic_and_task_sensitive() -> None:
    first = workflow_plan_hash(PLAN)
    second = workflow_plan_hash(PLAN)
    changed = {
        **PLAN,
        "plan": {
            "steps": [
                {**PLAN["plan"]["steps"][0], "task": "Changed"},
                PLAN["plan"]["steps"][1],
            ]
        },
    }

    assert first == second
    assert first.startswith("sha256:")
    assert workflow_plan_hash(changed) != first
    assert [item["task_hash"] for item in authorized_steps(PLAN)] != []


def test_parse_correlated_reply_ignores_stale_token_and_accepts_matching_block() -> None:
    output = """handoff_token: old
status: completed
summary: stale
verification: old
risks: none
next_steps: none

handoff_token: wfr_demo_step_1
status: completed
summary: fresh
verification: pytest
risks: none
next_steps: review
full_output_path: docs/result.md"""

    reply = parse_correlated_reply(output, "wfr_demo_step_1")

    assert reply is not None
    assert reply["status"] == "completed"
    assert reply["summary"] == "fresh"
    assert reply["full_output_path"] == "docs/result.md"


def test_parse_correlated_reply_rejects_matching_invalid_block() -> None:
    with pytest.raises(ValueError, match="missing workflow reply field: verification"):
        parse_correlated_reply(
            "handoff_token: wfr_demo_step_1\nstatus: completed\nsummary: incomplete",
            "wfr_demo_step_1",
        )


def test_build_compact_handoff_excludes_full_reply_text() -> None:
    handoff = build_compact_handoff(
        step=1,
        agent_id="planner",
        reply={
            "status": "completed",
            "summary": "done",
            "verification": "pytest",
            "risks": "none",
            "next_steps": "review",
            "full_output_path": "docs/result.md",
        },
        reply_id="rep_demo",
        artifact_paths=["docs/result.md"],
    )

    assert handoff == {
        "step": 1,
        "agent_id": "planner",
        "status": "completed",
        "summary": "done",
        "verification": "pytest",
        "risks": "none",
        "next_steps": "review",
        "artifact_paths": ["docs/result.md"],
        "trace_command": "agentdeck trace --id rep_demo",
    }
    assert "text" not in handoff


def test_state_store_records_and_updates_workflow_run(tmp_path) -> None:
    store = StateStore(tmp_path)
    record = store.create_workflow_run(
        plan_id="pln_demo",
        plan_hash="sha256:plan",
        timeout_seconds=30,
        authorized_steps=[
            {
                "step": 1,
                "agent_id": "planner",
                "role": "planning",
                "task": "Do",
                "task_hash": "sha256:task",
            }
        ],
    )

    assert record["run_id"].startswith("wfr_")
    assert record["status"] == "running"
    updated = store.update_workflow_run(
        record["run_id"], status="stopped", stop_reason="timed_out"
    )
    assert updated["status"] == "stopped"
    assert store.workflow_run_by_id(record["run_id"])["stop_reason"] == "timed_out"


class CorrelatedFakeBackend:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_input(self, _config, pane_id: str, text: str) -> None:
        self.sent.append((pane_id, text))

    def capture_output(self, _config, pane_id: str, lines: int = 200) -> str:
        prompt = next(text for sent_pane, text in reversed(self.sent) if sent_pane == pane_id)
        token = next(
            line.split(":", 1)[1].strip()
            for line in prompt.splitlines()
            if line.startswith("handoff_token:")
        )
        return (
            "unrelated pane history that must not be forwarded\n"
            f"handoff_token: {token}\n"
            "status: completed\n"
            f"summary: finished {pane_id}\n"
            "verification: fake backend\n"
            "risks: none\n"
            "next_steps: continue\n"
            f"full_output_path: docs/{pane_id.removeprefix('%')}.md"
        )

    def pane_exists(self, _config, _pane_id: str) -> bool:
        return True


class ToggleReplyBackend(CorrelatedFakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.respond = False

    def capture_output(self, config, pane_id: str, lines: int = 200) -> str:
        if not self.respond:
            return "still working"
        return super().capture_output(config, pane_id, lines)


def test_runner_completes_two_steps_with_compact_correlated_handoff(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    config = load_config(root)
    store = StateStore(root)
    state = store.load()
    for agent_id, pane_id in (("planner", "%1"), ("reviewer", "%2")):
        state["agents"][agent_id] = {
            "agent_id": agent_id,
            "pane_id": pane_id,
            "session_name": "agentdeck",
            "cwd": str(root),
            "status": "running",
        }
    store.save(state)
    plan = store.record_plan(
        "Prepare and review evidence",
        "fake",
        "fake-plan",
        PLAN["plan"],
    )
    steps = authorized_steps(plan)
    run = store.create_workflow_run(
        plan_id=str(plan["plan_id"]),
        plan_hash=workflow_plan_hash(plan),
        timeout_seconds=30,
        authorized_steps=steps,
    )
    backend = CorrelatedFakeBackend()

    result = run_sequential_workflow(
        config=config,
        store=store,
        backend=backend,
        run_id=str(run["run_id"]),
        poll_interval=0,
    )

    assert result["status"] == "completed"
    assert len(backend.sent) == 2
    assert backend.sent[0][0] == "%1"
    assert backend.sent[1][0] == "%2"
    assert '"summary": "finished %1"' in backend.sent[1][1]
    assert "unrelated pane history" not in backend.sent[1][1]
    persisted = store.workflow_run_by_id(str(run["run_id"]))
    assert [turn["status"] for turn in persisted["turns"]] == [
        "completed",
        "completed",
    ]
    state = store.load()
    assert len(state["messages"]) == 2
    assert len(state["replies"]) == 2
    assert [event["event_type"] for event in store.all_events()] == [
        "workflow_step_dispatched",
        "workflow_step_completed",
        "workflow_step_dispatched",
        "workflow_step_completed",
        "workflow_completed",
    ]


def test_runner_resume_waits_for_existing_token_without_duplicate_dispatch(
    tmp_path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    config = load_config(root)
    store = StateStore(root)
    state = store.load()
    for agent_id, pane_id in (("planner", "%1"), ("reviewer", "%2")):
        state["agents"][agent_id] = {
            "agent_id": agent_id,
            "pane_id": pane_id,
            "session_name": "agentdeck",
            "cwd": str(root),
            "status": "running",
        }
    store.save(state)
    plan = store.record_plan(
        "Prepare and review evidence", "fake", "fake-plan", PLAN["plan"]
    )
    run = store.create_workflow_run(
        plan_id=str(plan["plan_id"]),
        plan_hash=workflow_plan_hash(plan),
        timeout_seconds=30,
        authorized_steps=authorized_steps(plan),
    )
    backend = ToggleReplyBackend()
    clock = iter((0.0, 31.0))

    stopped = run_sequential_workflow(
        config=config,
        store=store,
        backend=backend,
        run_id=str(run["run_id"]),
        poll_interval=0,
        monotonic=lambda: next(clock),
        sleeper=lambda _seconds: None,
    )

    assert stopped["status"] == "stopped"
    assert stopped["stop_reason"] == "timed_out"
    assert len(backend.sent) == 1
    first_token = stopped["turns"][0]["handoff_token"]

    backend.respond = True
    resumed = run_sequential_workflow(
        config=config,
        store=store,
        backend=backend,
        run_id=str(run["run_id"]),
        poll_interval=0,
    )

    assert resumed["status"] == "completed"
    assert len(backend.sent) == 2
    assert sum(first_token in prompt for _pane, prompt in backend.sent) == 1
    assert [turn["status"] for turn in resumed["turns"]] == [
        "completed",
        "completed",
    ]
