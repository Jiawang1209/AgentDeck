from __future__ import annotations

from pathlib import Path

from .models import PROJECT_VIEW_SCHEMA_VERSION


PROJECT_VIEW_TOP_LEVEL_FIELDS = (
    "schema_version",
    "project",
    "root",
    "runtime_backend",
    "leader",
    "agents",
    "state_path",
    "plans",
    "approvals",
    "messages",
    "jobs",
    "replies",
    "chat_turns",
    "leader_errors",
    "leader_actions",
    "inbox",
    "recovery",
)

PROJECT_VIEW_RECOVERY_FIELDS = (
    "status",
    "reason",
    "next_command",
    "recommended_action",
    "pending",
    "leader_action",
    "latest_event",
    "recent_events",
)

PROJECT_VIEW_RECOMMENDED_ACTION_FIELDS = (
    "label",
    "command",
    "safety",
    "requires_explicit_user",
    "source",
    "target_id",
)


def project_view_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "status_command": "agentdeck status",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "top_level_fields": list(PROJECT_VIEW_TOP_LEVEL_FIELDS),
        "recovery_fields": list(PROJECT_VIEW_RECOVERY_FIELDS),
        "recommended_action_fields": list(PROJECT_VIEW_RECOMMENDED_ACTION_FIELDS),
    }


def project_view_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = project_view_contract_payload(contract_path)
    if include_example:
        example = project_view_example()
        payload["example"] = True
        payload["example_top_level_fields"] = list(example)
        payload["example_recovery_fields"] = list(example["recovery"])
        payload["example_recommended_action_fields"] = list(example["recovery"]["recommended_action"])
        payload["example_project_view"] = example
    return payload


def validate_project_view_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    schema_version = payload.get("schema_version")
    if schema_version != PROJECT_VIEW_SCHEMA_VERSION:
        errors.append(f"schema_version mismatch: expected {PROJECT_VIEW_SCHEMA_VERSION}, got {schema_version}")
    for field in PROJECT_VIEW_TOP_LEVEL_FIELDS:
        if field not in payload:
            errors.append(f"missing top-level field: {field}")
    recovery = payload.get("recovery")
    if isinstance(recovery, dict):
        for field in PROJECT_VIEW_RECOVERY_FIELDS:
            if field not in recovery:
                errors.append(f"missing recovery field: {field}")
        recommended_action = recovery.get("recommended_action")
        if isinstance(recommended_action, dict):
            for field in PROJECT_VIEW_RECOMMENDED_ACTION_FIELDS:
                if field not in recommended_action:
                    errors.append(f"missing recommended_action field: {field}")
    elif "recovery" in payload:
        errors.append("recovery must be an object")
    return {"ok": not errors, "errors": errors}


def project_view_example() -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "project": "agentdeck-example",
        "root": "/workspace/agentdeck-example",
        "runtime_backend": "tmux",
        "leader": {"agent_id": "leader", "provider": "fake", "model": "fake-plan", "approval_mode": "confirm"},
        "agents": [
            {
                "agent_id": "planner",
                "role": "planner",
                "provider": "codex",
                "command": "codex",
                "workspace_mode": "shared",
                "role_prompt": "Break down goals and prepare implementation steps.",
                "runtime": {
                    "agent_id": "planner",
                    "pane_id": "%1",
                    "session_name": "agentdeck",
                    "cwd": "/workspace/agentdeck-example",
                    "status": "running",
                },
            }
        ],
        "state_path": "/workspace/agentdeck-example/.agentdeck/state/state.json",
        "plans": {
            "count": 1,
            "items": [
                {
                    "plan_id": "pln_example",
                    "task": "Build a GUI-ready recovery panel",
                    "status": "planned",
                    "provider": "fake",
                    "model": "fake-plan",
                    "dispatch_ready": False,
                    "step_count": 1,
                    "created_at": "2026-07-04T00:00:00+00:00",
                }
            ],
        },
        "approvals": {"count": 0, "pending": 0, "approved": 0, "rejected": 0, "dispatched": 0, "items": []},
        "messages": {"count": 0, "by_status": {}, "items": []},
        "jobs": {"count": 0, "by_status": {}, "items": []},
        "replies": {"count": 0, "items": []},
        "chat_turns": {
            "count": 1,
            "by_mode": {"review": 1},
            "items": [
                {
                    "turn_id": "cht_example",
                    "mode": "review",
                    "message": "继续",
                    "plan_id": "pln_example",
                    "next_command": "agentdeck leader apply-action --action-id act_example",
                    "action_id": "act_example",
                    "action_kind": "create_approvals",
                    "created_at": "2026-07-04T00:00:00+00:00",
                }
            ],
        },
        "leader_errors": {"count": 0, "by_mode": {}, "items": []},
        "leader_actions": {
            "count": 1,
            "by_kind": {"create_approvals": 1},
            "by_status": {"pending": 1},
            "recommended_action_id": "act_example",
            "items": [
                {
                    "action_id": "act_example",
                    "kind": "create_approvals",
                    "status": "pending",
                    "requires_confirmation": True,
                    "plan_id": "pln_example",
                    "approval_id": None,
                    "agent_id": None,
                    "message_id": None,
                    "command": "agentdeck approval create-from-plan --plan-id pln_example",
                    "reason": "plan has no approval records",
                    "can_apply": True,
                    "apply_command": "agentdeck leader apply-action --action-id act_example",
                    "explicit_command": "agentdeck approval create-from-plan --plan-id pln_example",
                    "apply_blocker": None,
                    "is_recommended": True,
                    "created_at": "2026-07-04T00:00:00+00:00",
                }
            ],
        },
        "inbox": {"total": 0, "by_agent": {}, "by_status": {}, "heads": {}},
        "recovery": {
            "status": "action_required",
            "reason": "pending leader action: create_approvals",
            "next_command": "agentdeck leader apply-action --action-id act_example",
            "recommended_action": {
                "label": "Apply safe Leader action",
                "command": "agentdeck leader apply-action --action-id act_example",
                "safety": "safe_apply",
                "requires_explicit_user": False,
                "source": "leader_action",
                "target_id": "act_example",
            },
            "pending": {"leader_actions": 1, "approvals": 0, "approved_approvals": 0, "inbox_items": 0},
            "leader_action": {
                "action_id": "act_example",
                "kind": "create_approvals",
                "command": "agentdeck approval create-from-plan --plan-id pln_example",
                "can_apply": True,
                "apply_command": "agentdeck leader apply-action --action-id act_example",
                "apply_blocker": None,
            },
            "latest_event": {
                "event_id": "evt_example",
                "event_type": "leader_chat_turn",
                "created_at": "2026-07-04T00:00:00+00:00",
            },
            "recent_events": [
                {
                    "event_id": "evt_example",
                    "event_type": "leader_chat_turn",
                    "created_at": "2026-07-04T00:00:00+00:00",
                }
            ],
        },
    }
