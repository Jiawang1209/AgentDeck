from __future__ import annotations

import pytest

from agentdeck.workflow import (
    authorized_steps,
    build_compact_handoff,
    parse_correlated_reply,
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
