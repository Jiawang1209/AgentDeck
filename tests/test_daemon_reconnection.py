from __future__ import annotations

from pathlib import Path
import json

import pytest

from agentdeck.config import load_config, write_default_config
from agentdeck.conversation import session as session_module
from agentdeck.conversation.leader_gateway import LeaderGateway
from agentdeck.state import StateStore


MISSION_ID = "mis_131313131313"


def _seed_waiting_permission_mission(root: Path) -> tuple[object, StateStore]:
    (root / ".git").mkdir()
    write_default_config(root)
    config = load_config(root)
    store = StateStore(root)
    state = store.load()
    state["missions"] = [
        {
            "mission_id": MISSION_ID,
            "schema_version": "mission/v1",
            "user_message": "complete six compact steps",
            "status": "running",
            "stop_reason": None,
            "can_start": False,
            "blockers": [],
            "provider": "fake",
            "model": "fake-plan",
            "plan_id": "pln_131313131313",
            "plan_hash": "sha256:" + "1" * 64,
            "workflow_run_id": None,
            "current_step": 4,
            "step_count": 6,
            "timeout_seconds": 180,
            "selected_agents": [],
            "startup_actions": [],
            "created_at": "2026-07-14T00:00:00+00:00",
            "updated_at": "2026-07-14T00:04:00+00:00",
            "confirmed_at": "2026-07-14T00:00:30+00:00",
            "completed_at": None,
        }
    ]
    state["recovery_decisions"] = [
        {
            "classification": "waiting_human",
            "reason": "Worker permission is pending",
            "mission_id": MISSION_ID,
            "attempt_id": "mat_131313131313",
            "next_transition": None,
            "classified_at": "2026-07-14T00:04:00+00:00",
        }
    ]
    state["missions"][0]["execution_snapshot"] = {
        "mission": {
            "steps": [
                {
                    "step_id": f"step_{index}",
                    "position": index,
                    "agent_id": "planner" if index % 2 else "reviewer",
                    "role": "planning" if index % 2 else "review",
                    "task_hash": "sha256:" + str(index) * 64,
                }
                for index in range(1, 7)
            ]
        }
    }
    state["mission_attempts"] = []
    for index in range(1, 6):
        attempt_id = "mat_131313131313" if index == 5 else f"mat_{index:012x}"
        state["mission_attempts"].append(
            {
                "mission_id": MISSION_ID,
                "attempt_id": attempt_id,
                "step_id": f"step_{index}",
                "agent_id": "planner" if index % 2 else "reviewer",
                "configured_transport": "tmux",
                "dispatch_key": f"dsp_{index:032x}",
                "admission_claim_id": f"adm_{index:012x}",
                "snapshot_hash": "sha256:" + "a" * 64,
                "state": "succeeded" if index <= 4 else "running",
                "created_at": f"2026-07-14T00:0{index}:00+00:00",
                "updated_at": f"2026-07-14T00:0{index}:30+00:00",
                "receipt_summary": "submitted",
                "blocker": None,
                "terminal_reason": "completed" if index <= 4 else None,
            }
        )
    state["mission_worker_replies"] = [
        {
            "mission_id": MISSION_ID,
            "attempt_id": f"mat_{index:012x}",
            "reply_id": f"mrp_{index:012x}",
            "dispatch_key": f"dsp_{index:032x}",
            "state": "validated",
            "canonical_handoff": {
                "handoff_token": f"dsp_{index:032x}",
                "status": "completed",
                "summary": "SECRET raw result must not be rendered",
                "verification": "pytest passed",
                "risks": "none",
                "next_steps": "continue",
                "artifacts": [
                    {
                        "path": "reports/result.txt",
                        "content_hash": "sha256:" + "b" * 64,
                    }
                ],
                "trace_ids": ["native-opaque-secret"],
            },
        }
        for index in range(1, 5)
    ]
    state["conversation_full_transcript"] = [
        {"raw_prompt": "SECRET full transcript must not be reconstructed"}
    ]
    store.save(state)
    return config, store


def test_reconnection_summary_requires_no_llm(tmp_path: Path, monkeypatch) -> None:
    config, store = _seed_waiting_permission_mission(tmp_path)
    monkeypatch.setattr(
        LeaderGateway,
        "generate_mission",
        lambda *_args, **_kwargs: pytest.fail("LLM must not be called"),
    )

    response = session_module.reconnect_conversation(
        tmp_path, config=config, store=store
    )

    assert response.kind == "mission_recovery"
    assert response.payload["progress"] == {"completed": 4, "total": 6}
    assert response.payload["decision"]["kind"] == "permission"


def test_recovery_card_is_compact_same_source_and_zero_write(tmp_path: Path) -> None:
    config, store = _seed_waiting_permission_mission(tmp_path)
    before = {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    response = session_module.reconnect_conversation(
        tmp_path, config=config, store=store
    )
    project_view = store.project_view(config)
    encoded = json.dumps(response.payload, sort_keys=True)

    assert response.payload == project_view.mission_recovery
    assert [item["step_id"] for item in response.payload["completed_steps"]] == [
        "step_1", "step_2", "step_3", "step_4"
    ]
    assert response.payload["active_step"] == {
        "step_id": "step_5",
        "position": 5,
        "agent_id": "planner",
        "role": "planning",
    }
    assert len(response.payload["recent_results"]) == 3
    assert all(item["state"] == "validated" for item in response.payload["recent_results"])
    assert response.payload["wait_reason"] == "Worker permission is pending"
    assert response.payload["decision"]["controls"][0]["command"] == (
        "agentdeck daemon permission-preview --mission-id mis_131313131313 "
        "--attempt-id mat_131313131313"
    )
    assert response.payload["trace_commands"][-1] == (
        "agentdeck trace --id mat_131313131313"
    )
    assert response.payload["workspace_control"]["command"] == "agentdeck workbench"
    assert "SECRET" not in encoded
    assert "/Users/" not in encoded
    assert "native-opaque" not in encoded
    assert "raw_prompt" not in encoded
    assert {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == before


@pytest.mark.parametrize(
    ("classification", "status", "reason", "expected_kind"),
    [
        ("resumable", "running", "prepared attempt is safe", "resume"),
        ("ambiguous", "running", "external outcome unknown", "inspect"),
        ("blocked", "running", "transport invalid", "inspect"),
        ("terminal", "completed", "Mission is completed", "none"),
    ],
)
def test_recovery_card_covers_all_reconciliation_classes(
    tmp_path: Path,
    classification: str,
    status: str,
    reason: str,
    expected_kind: str,
) -> None:
    config, store = _seed_waiting_permission_mission(tmp_path)
    state = store.load()
    state["missions"][0]["status"] = status
    state["recovery_decisions"][0].update(
        {
            "classification": classification,
            "reason": reason,
            "next_transition": "dispatch_prepared" if classification == "resumable" else None,
        }
    )
    store.save(state)

    payload = session_module.reconnect_conversation(
        tmp_path, config=config, store=store
    ).payload

    assert payload["classification"] == classification
    assert payload["decision"]["kind"] == expected_kind


def test_recovery_card_selects_latest_valid_recovery_mission(tmp_path: Path) -> None:
    config, store = _seed_waiting_permission_mission(tmp_path)
    state = store.load()
    older = dict(state["missions"][0])
    older.update(
        {
            "mission_id": "mis_121212121212",
            "status": "completed",
            "current_step": 6,
            "updated_at": "2026-07-14T00:03:00+00:00",
            "completed_at": "2026-07-14T00:03:00+00:00",
        }
    )
    state["missions"] = [state["missions"][0], older]
    store.save(state)

    payload = session_module.reconnect_conversation(
        tmp_path, config=config, store=store
    ).payload

    assert payload["mission_id"] == MISSION_ID
    assert payload["classification"] == "waiting_human"


def test_recovery_card_ignores_invalid_recovery_record_without_leaking_reason(
    tmp_path: Path,
) -> None:
    config, store = _seed_waiting_permission_mission(tmp_path)
    state = store.load()
    state["recovery_decisions"].append(
        {
            "classification": "waiting_human",
            "reason": "SECRET forged recovery reason",
            "mission_id": MISSION_ID,
            "attempt_id": "not-an-attempt",
            "next_transition": None,
            "classified_at": "not-a-timestamp",
        }
    )
    store.save(state)

    payload = session_module.reconnect_conversation(
        tmp_path, config=config, store=store
    ).payload

    assert payload["wait_reason"] == "Worker permission is pending"
    assert "SECRET" not in json.dumps(payload, sort_keys=True)
