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

PROJECT_VIEW_RECOVERY_PENDING_FIELDS = (
    "leader_actions",
    "approvals",
    "approved_approvals",
    "inbox_items",
    "leader_errors",
)

PROJECT_VIEW_RECOMMENDED_ACTION_FIELDS = (
    "label",
    "command",
    "safety",
    "requires_explicit_user",
    "source",
    "target_id",
)

APPROVAL_QUEUE_FIELDS = (
    "count",
    "approvals",
)

APPROVAL_ITEM_FIELDS = (
    "approval_id",
    "plan_id",
    "step",
    "agent_id",
    "role",
    "task",
    "risk",
    "status",
    "created_at",
    "reason",
    "approve_command",
    "reject_command",
    "dispatch_command",
    "can_dispatch",
    "dispatch_blocker",
)

INBOX_QUEUE_FIELDS = (
    "agent_id",
    "count",
    "head_inbox_id",
    "items",
)

INBOX_ITEM_FIELDS = (
    "inbox_id",
    "event_type",
    "message_id",
    "attempt_id",
    "job_id",
    "reply_id",
    "from_actor",
    "from_agent",
    "to_agent",
    "task",
    "status",
    "created_at",
    "trace_command",
    "ack_command",
    "is_head",
    "can_ack",
    "ack_blocker",
)

PROJECT_VIEW_LEADER_ACTIONS_FIELDS = (
    "count",
    "by_kind",
    "by_status",
    "recommended_action_id",
    "items",
)

PROJECT_VIEW_LEADER_ACTION_ITEM_FIELDS = (
    "action_id",
    "kind",
    "status",
    "requires_confirmation",
    "plan_id",
    "approval_id",
    "agent_id",
    "message_id",
    "command",
    "reason",
    "can_apply",
    "apply_command",
    "explicit_command",
    "apply_blocker",
    "is_recommended",
    "created_at",
)

PROJECT_VIEW_MESSAGE_ITEM_FIELDS = (
    "message_id",
    "from_actor",
    "to_agent",
    "task",
    "status",
    "created_at",
    "trace_command",
)

PROJECT_VIEW_JOB_ITEM_FIELDS = (
    "job_id",
    "message_id",
    "agent_id",
    "status",
    "created_at",
    "trace_command",
)

PROJECT_VIEW_REPLY_ITEM_FIELDS = (
    "reply_id",
    "message_id",
    "job_id",
    "from_agent",
    "to_actor",
    "created_at",
    "trace_command",
)

LEADER_CHAT_RESPONSE_FIELDS = (
    "ok",
    "turn_id",
    "mode",
    "message",
    "project_view",
    "leader_actions",
    "leader_explanation",
    "plan_id",
    "review",
    "recovery",
    "next_command",
    "leader_action",
    "continue_card",
    "inbox_card",
)

CONTINUE_CARD_FIELDS = (
    "ok",
    "mode",
    "project_view_schema_version",
    "project_view_command",
    "status",
    "reason",
    "next_command",
    "recommended_action",
    "pending",
    "leader_action",
    "action_detail_command",
)

LEADER_ACTION_DETAIL_FIELDS = (
    "action_id",
    "kind",
    "status",
    "requires_confirmation",
    "plan_id",
    "approval_id",
    "agent_id",
    "message_id",
    "command",
    "reason",
    "created_at",
    "can_apply",
    "apply_command",
    "explicit_command",
    "apply_blocker",
    "recovery",
    "recommended_action",
    "matches_recommended_action",
)

LEADER_ACTIONS_LIST_FIELDS = (
    "count",
    "recommended_action_id",
    "actions",
)

LEADER_CHAT_EXPLANATION_FIELDS = (
    "mode",
    "summary",
    "reason",
    "next_command",
    "recommended_action_id",
    "action_kind",
    "action_status",
    "safety",
    "requires_explicit_user",
)

TRACE_TOP_LEVEL_FIELDS = (
    "schema_version",
    "query_id",
    "message",
    "attempts",
    "jobs",
    "replies",
    "inbox_items",
)

TRACE_MESSAGE_FIELDS = (
    "message_id",
    "from_actor",
    "to_agent",
    "task",
    "prompt",
    "status",
    "created_at",
)

TRACE_ATTEMPT_FIELDS = (
    "attempt_id",
    "message_id",
    "agent_id",
    "status",
    "created_at",
)

TRACE_JOB_FIELDS = (
    "job_id",
    "message_id",
    "attempt_id",
    "agent_id",
    "pane_id",
    "status",
    "created_at",
)

TRACE_REPLY_FIELDS = (
    "reply_id",
    "message_id",
    "attempt_id",
    "job_id",
    "from_agent",
    "to_actor",
    "text",
    "created_at",
)

TRACE_INBOX_ITEM_FIELDS = (
    "inbox_id",
    "event_type",
    "message_id",
    "attempt_id",
    "job_id",
    "reply_id",
    "from_actor",
    "from_agent",
    "to_agent",
    "task",
    "status",
    "created_at",
)


def project_view_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "status_command": "agentdeck status",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "top_level_fields": list(PROJECT_VIEW_TOP_LEVEL_FIELDS),
        "recovery_fields": list(PROJECT_VIEW_RECOVERY_FIELDS),
        "recovery_pending_fields": list(PROJECT_VIEW_RECOVERY_PENDING_FIELDS),
        "recommended_action_fields": list(PROJECT_VIEW_RECOMMENDED_ACTION_FIELDS),
        "leader_actions_fields": list(PROJECT_VIEW_LEADER_ACTIONS_FIELDS),
        "leader_action_item_fields": list(PROJECT_VIEW_LEADER_ACTION_ITEM_FIELDS),
        "message_item_fields": list(PROJECT_VIEW_MESSAGE_ITEM_FIELDS),
        "job_item_fields": list(PROJECT_VIEW_JOB_ITEM_FIELDS),
        "reply_item_fields": list(PROJECT_VIEW_REPLY_ITEM_FIELDS),
    }


def project_view_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = project_view_contract_payload(contract_path)
    if include_example:
        example = project_view_example()
        payload["example"] = True
        payload["example_top_level_fields"] = list(example)
        payload["example_recovery_fields"] = list(example["recovery"])
        payload["example_recovery_pending_fields"] = list(example["recovery"]["pending"])
        payload["example_recommended_action_fields"] = list(example["recovery"]["recommended_action"])
        payload["example_leader_actions_fields"] = list(example["leader_actions"])
        payload["example_leader_action_item_fields"] = list(example["leader_actions"]["items"][0])
        payload["example_message_item_fields"] = list(example["messages"]["items"][0])
        payload["example_job_item_fields"] = list(example["jobs"]["items"][0])
        payload["example_reply_item_fields"] = list(example["replies"]["items"][0])
        payload["example_project_view"] = example
    return payload


def leader_chat_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "chat_command": "agentdeck leader chat --message <text>",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "response_fields": list(LEADER_CHAT_RESPONSE_FIELDS),
        "explanation_fields": list(LEADER_CHAT_EXPLANATION_FIELDS),
        "continue_card_fields": list(CONTINUE_CARD_FIELDS),
        "project_view_schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "project_view_contract": "agentdeck contract project-view",
    }


def leader_chat_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = leader_chat_contract_payload(contract_path)
    if include_example:
        example = leader_chat_example()
        payload["example"] = True
        payload["example_response_fields"] = list(example)
        payload["example_explanation_fields"] = list(example["leader_explanation"])
        payload["example_continue_card_fields"] = list(example["continue_card"])
        payload["example_leader_chat"] = example
    return payload


def continue_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "continue_command": "agentdeck continue",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "continue_card_fields": list(CONTINUE_CARD_FIELDS),
        "project_view_schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "project_view_contract": "agentdeck contract project-view",
    }


def continue_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = continue_contract_payload(contract_path)
    if include_example:
        example = continue_example()
        payload["example"] = True
        payload["example_continue_card_fields"] = list(example)
        payload["example_continue_card"] = example
    return payload


def approval_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "approvals_command": "agentdeck approval list",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "queue_fields": list(APPROVAL_QUEUE_FIELDS),
        "approval_item_fields": list(APPROVAL_ITEM_FIELDS),
        "project_view_schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "project_view_contract": "agentdeck contract project-view",
    }


def approval_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = approval_contract_payload(contract_path)
    if include_example:
        example = approval_example()
        payload["example"] = True
        payload["example_queue_fields"] = list(example)
        payload["example_approval_item_fields"] = list(example["approvals"][0])
        payload["example_approval_queue"] = example
    return payload


def inbox_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "inbox_command": "agentdeck inbox --agent <id>",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "queue_fields": list(INBOX_QUEUE_FIELDS),
        "inbox_item_fields": list(INBOX_ITEM_FIELDS),
        "project_view_schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "project_view_contract": "agentdeck contract project-view",
        "trace_contract": "agentdeck contract trace",
    }


def inbox_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = inbox_contract_payload(contract_path)
    if include_example:
        example = inbox_example()
        payload["example"] = True
        payload["example_queue_fields"] = list(example)
        payload["example_inbox_item_fields"] = list(example["items"][0])
        payload["example_inbox"] = example
    return payload


def leader_action_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "action_command": "agentdeck leader action --action-id <id>",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "action_fields": list(LEADER_ACTION_DETAIL_FIELDS),
        "project_view_schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "project_view_contract": "agentdeck contract project-view",
    }


def leader_action_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = leader_action_contract_payload(contract_path)
    if include_example:
        example = leader_action_example()
        payload["example"] = True
        payload["example_action_fields"] = list(example)
        payload["example_leader_action"] = example
    return payload


def leader_actions_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "actions_command": "agentdeck leader actions",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "list_fields": list(LEADER_ACTIONS_LIST_FIELDS),
        "action_item_fields": list(PROJECT_VIEW_LEADER_ACTION_ITEM_FIELDS),
        "project_view_schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "project_view_contract": "agentdeck contract project-view",
    }


def leader_actions_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = leader_actions_contract_payload(contract_path)
    if include_example:
        example = leader_actions_example()
        payload["example"] = True
        payload["example_list_fields"] = list(example)
        payload["example_action_item_fields"] = list(example["actions"][0])
        payload["example_leader_actions"] = example
    return payload


def trace_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "trace_command": "agentdeck trace --id <id>",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "top_level_fields": list(TRACE_TOP_LEVEL_FIELDS),
        "message_fields": list(TRACE_MESSAGE_FIELDS),
        "attempt_fields": list(TRACE_ATTEMPT_FIELDS),
        "job_fields": list(TRACE_JOB_FIELDS),
        "reply_fields": list(TRACE_REPLY_FIELDS),
        "inbox_item_fields": list(TRACE_INBOX_ITEM_FIELDS),
    }


def trace_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = trace_contract_payload(contract_path)
    if include_example:
        example = trace_example()
        payload["example"] = True
        payload["example_top_level_fields"] = list(example)
        payload["example_message_fields"] = list(example["message"])
        payload["example_attempt_fields"] = list(example["attempts"][0])
        payload["example_job_fields"] = list(example["jobs"][0])
        payload["example_reply_fields"] = list(example["replies"][0])
        payload["example_inbox_item_fields"] = list(example["inbox_items"][0])
        payload["example_trace"] = example
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
        pending = recovery.get("pending")
        if isinstance(pending, dict):
            for field in PROJECT_VIEW_RECOVERY_PENDING_FIELDS:
                if field not in pending:
                    errors.append(f"missing recovery pending field: {field}")
        elif "pending" in recovery:
            errors.append("recovery pending must be an object")
        recommended_action = recovery.get("recommended_action")
        if isinstance(recommended_action, dict):
            for field in PROJECT_VIEW_RECOMMENDED_ACTION_FIELDS:
                if field not in recommended_action:
                    errors.append(f"missing recommended_action field: {field}")
    elif "recovery" in payload:
        errors.append("recovery must be an object")
    leader_actions = payload.get("leader_actions")
    if isinstance(leader_actions, dict):
        for field in PROJECT_VIEW_LEADER_ACTIONS_FIELDS:
            if field not in leader_actions:
                errors.append(f"missing leader_actions field: {field}")
        items = leader_actions.get("items")
        if isinstance(items, list) and items:
            first_item = items[0]
            if isinstance(first_item, dict):
                for field in PROJECT_VIEW_LEADER_ACTION_ITEM_FIELDS:
                    if field not in first_item:
                        errors.append(f"missing leader_actions item field: {field}")
    elif "leader_actions" in payload:
        errors.append("leader_actions must be an object")
    _validate_project_view_summary_items(errors, payload, "messages", PROJECT_VIEW_MESSAGE_ITEM_FIELDS, "message")
    _validate_project_view_summary_items(errors, payload, "jobs", PROJECT_VIEW_JOB_ITEM_FIELDS, "job")
    _validate_project_view_summary_items(errors, payload, "replies", PROJECT_VIEW_REPLY_ITEM_FIELDS, "reply")
    return {"ok": not errors, "errors": errors}


def _validate_project_view_summary_items(
    errors: list[str],
    payload: dict[str, object],
    summary_name: str,
    fields: tuple[str, ...],
    label: str,
) -> None:
    summary = payload.get(summary_name)
    if not isinstance(summary, dict):
        if summary_name in payload:
            errors.append(f"{summary_name} must be an object")
        return
    items = summary.get("items")
    if not isinstance(items, list):
        if "items" in summary:
            errors.append(f"{summary_name}.items must be a list")
        return
    if not items:
        return
    first_item = items[0]
    if not isinstance(first_item, dict):
        errors.append(f"{summary_name}.items must contain objects")
        return
    for field in fields:
        if field not in first_item:
            errors.append(f"missing {label} item field: {field}")


def validate_continue_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in CONTINUE_CARD_FIELDS:
        if field not in payload:
            errors.append(f"missing continue_card field: {field}")
    if payload.get("mode") != "continue":
        errors.append(f"continue_card mode must be continue, got {payload.get('mode')}")
    if payload.get("project_view_schema_version") != PROJECT_VIEW_SCHEMA_VERSION:
        errors.append(
            "project_view_schema_version mismatch: "
            f"expected {PROJECT_VIEW_SCHEMA_VERSION}, got {payload.get('project_view_schema_version')}"
        )
    recommended_action = payload.get("recommended_action")
    if isinstance(recommended_action, dict):
        for field in PROJECT_VIEW_RECOMMENDED_ACTION_FIELDS:
            if field not in recommended_action:
                errors.append(f"missing recommended_action field: {field}")
    elif "recommended_action" in payload and recommended_action is not None:
        errors.append("recommended_action must be an object")
    pending = payload.get("pending")
    if isinstance(pending, dict):
        for field in PROJECT_VIEW_RECOVERY_PENDING_FIELDS:
            if field not in pending:
                errors.append(f"missing pending field: {field}")
    elif "pending" in payload and pending is not None:
        errors.append("pending must be an object")
    return {"ok": not errors, "errors": errors}


def validate_approval_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in APPROVAL_QUEUE_FIELDS:
        if field not in payload:
            errors.append(f"missing approval queue field: {field}")
    approvals = payload.get("approvals")
    if isinstance(approvals, list):
        if approvals:
            first_approval = approvals[0]
            if isinstance(first_approval, dict):
                for field in APPROVAL_ITEM_FIELDS:
                    if field not in first_approval:
                        errors.append(f"missing approval item field: {field}")
                if not isinstance(first_approval.get("can_dispatch"), bool):
                    errors.append("can_dispatch must be a boolean")
            else:
                errors.append("approval items must be objects")
    elif "approvals" in payload:
        errors.append("approvals must be a list")
    return {"ok": not errors, "errors": errors}


def validate_inbox_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in INBOX_QUEUE_FIELDS:
        if field not in payload:
            errors.append(f"missing inbox queue field: {field}")
    items = payload.get("items")
    if isinstance(items, list):
        if items:
            first_item = items[0]
            if isinstance(first_item, dict):
                for field in INBOX_ITEM_FIELDS:
                    if field not in first_item:
                        errors.append(f"missing inbox item field: {field}")
                if not isinstance(first_item.get("is_head"), bool):
                    errors.append("is_head must be a boolean")
                if not isinstance(first_item.get("can_ack"), bool):
                    errors.append("can_ack must be a boolean")
            else:
                errors.append("inbox items must be objects")
    elif "items" in payload:
        errors.append("items must be a list")
    return {"ok": not errors, "errors": errors}


def validate_leader_action_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in LEADER_ACTION_DETAIL_FIELDS:
        if field not in payload:
            errors.append(f"missing leader_action field: {field}")
    recovery = payload.get("recovery")
    if isinstance(recovery, dict):
        _validate_recovery_contract(errors, recovery, prefix="recovery")
    elif "recovery" in payload:
        errors.append("recovery must be an object")
    recommended_action = payload.get("recommended_action")
    if isinstance(recommended_action, dict):
        for field in PROJECT_VIEW_RECOMMENDED_ACTION_FIELDS:
            if field not in recommended_action:
                errors.append(f"missing recommended_action field: {field}")
    elif "recommended_action" in payload and recommended_action is not None:
        errors.append("recommended_action must be an object")
    if not isinstance(payload.get("matches_recommended_action"), bool):
        errors.append("matches_recommended_action must be a boolean")
    return {"ok": not errors, "errors": errors}


def validate_leader_actions_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in LEADER_ACTIONS_LIST_FIELDS:
        if field not in payload:
            errors.append(f"missing leader_actions field: {field}")
    actions = payload.get("actions")
    if isinstance(actions, list):
        if actions:
            first_action = actions[0]
            if isinstance(first_action, dict):
                for field in PROJECT_VIEW_LEADER_ACTION_ITEM_FIELDS:
                    if field not in first_action:
                        errors.append(f"missing leader action item field: {field}")
            else:
                errors.append("leader actions items must be objects")
    elif "actions" in payload:
        errors.append("actions must be a list")
    return {"ok": not errors, "errors": errors}


def _validate_recovery_contract(errors: list[str], recovery: dict[str, object], *, prefix: str) -> None:
    for field in PROJECT_VIEW_RECOVERY_FIELDS:
        if field not in recovery:
            errors.append(f"{prefix}: missing recovery field: {field}")
    pending = recovery.get("pending")
    if isinstance(pending, dict):
        for field in PROJECT_VIEW_RECOVERY_PENDING_FIELDS:
            if field not in pending:
                errors.append(f"{prefix}: missing recovery pending field: {field}")
    elif "pending" in recovery:
        errors.append(f"{prefix}: recovery pending must be an object")
    recommended_action = recovery.get("recommended_action")
    if isinstance(recommended_action, dict):
        for field in PROJECT_VIEW_RECOMMENDED_ACTION_FIELDS:
            if field not in recommended_action:
                errors.append(f"{prefix}: missing recommended_action field: {field}")
    elif "recommended_action" in recovery and recommended_action is not None:
        errors.append(f"{prefix}: recommended_action must be an object")


def validate_trace_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    schema_version = payload.get("schema_version")
    if schema_version != PROJECT_VIEW_SCHEMA_VERSION:
        errors.append(f"schema_version mismatch: expected {PROJECT_VIEW_SCHEMA_VERSION}, got {schema_version}")
    for field in TRACE_TOP_LEVEL_FIELDS:
        if field not in payload:
            errors.append(f"missing top-level field: {field}")
    message = payload.get("message")
    if isinstance(message, dict):
        for field in TRACE_MESSAGE_FIELDS:
            if field not in message:
                errors.append(f"missing message field: {field}")
    elif "message" in payload:
        errors.append("message must be an object")
    _validate_trace_items(errors, payload, "attempts", TRACE_ATTEMPT_FIELDS, "attempt")
    _validate_trace_items(errors, payload, "jobs", TRACE_JOB_FIELDS, "job")
    _validate_trace_items(errors, payload, "replies", TRACE_REPLY_FIELDS, "reply")
    _validate_trace_items(errors, payload, "inbox_items", TRACE_INBOX_ITEM_FIELDS, "inbox item")
    return {"ok": not errors, "errors": errors}


def _validate_trace_items(
    errors: list[str],
    payload: dict[str, object],
    collection_name: str,
    fields: tuple[str, ...],
    label: str,
) -> None:
    collection = payload.get(collection_name)
    if not isinstance(collection, list):
        if collection_name in payload:
            errors.append(f"{collection_name} must be a list")
        return
    if not collection:
        return
    first_item = collection[0]
    if not isinstance(first_item, dict):
        errors.append(f"{collection_name} items must be objects")
        return
    for field in fields:
        if field not in first_item:
            errors.append(f"missing {label} field: {field}")


def validate_leader_chat_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in LEADER_CHAT_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing response field: {field}")
    project_view = payload.get("project_view")
    if isinstance(project_view, dict):
        project_view_validation = validate_project_view_contract(project_view)
        for error in project_view_validation["errors"]:
            errors.append(f"project_view: {error}")
        if payload.get("leader_actions") != project_view.get("leader_actions"):
            errors.append("leader_actions must match project_view.leader_actions")
    elif "project_view" in payload:
        errors.append("project_view must be an object")
    explanation = payload.get("leader_explanation")
    if isinstance(explanation, dict):
        for field in LEADER_CHAT_EXPLANATION_FIELDS:
            if field not in explanation:
                errors.append(f"missing leader_explanation field: {field}")
    elif "leader_explanation" in payload:
        errors.append("leader_explanation must be an object")
    continue_card = payload.get("continue_card")
    if isinstance(continue_card, dict):
        continue_card_validation = validate_continue_contract(continue_card)
        for error in continue_card_validation["errors"]:
            errors.append(f"continue_card: {error}")
    elif "continue_card" in payload and continue_card is not None:
        errors.append("continue_card must be an object")
    inbox_card = payload.get("inbox_card")
    if isinstance(inbox_card, dict):
        inbox_card_validation = validate_inbox_contract(inbox_card)
        for error in inbox_card_validation["errors"]:
            errors.append(f"inbox_card: {error}")
    elif "inbox_card" in payload and inbox_card is not None:
        errors.append("inbox_card must be an object")
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
        "messages": {
            "count": 1,
            "by_status": {"replied": 1},
            "items": [
                {
                    "message_id": "msg_example",
                    "from_actor": "leader",
                    "to_agent": "planner",
                    "task": "Build a GUI-ready recovery panel",
                    "status": "replied",
                    "created_at": "2026-07-04T00:00:00+00:00",
                    "trace_command": "agentdeck trace --id msg_example",
                }
            ],
        },
        "jobs": {
            "count": 1,
            "by_status": {"completed": 1},
            "items": [
                {
                    "job_id": "job_example",
                    "message_id": "msg_example",
                    "agent_id": "planner",
                    "status": "completed",
                    "created_at": "2026-07-04T00:00:00+00:00",
                    "trace_command": "agentdeck trace --id job_example",
                }
            ],
        },
        "replies": {
            "count": 1,
            "items": [
                {
                    "reply_id": "rep_example",
                    "message_id": "msg_example",
                    "job_id": "job_example",
                    "from_agent": "planner",
                    "to_actor": "leader",
                    "created_at": "2026-07-04T00:00:01+00:00",
                    "trace_command": "agentdeck trace --id rep_example",
                }
            ],
        },
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
            "pending": {
                "leader_actions": 1,
                "approvals": 0,
                "approved_approvals": 0,
                "inbox_items": 0,
                "leader_errors": 0,
            },
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


def leader_chat_example() -> dict[str, object]:
    project_view = project_view_example()
    leader_action = project_view["leader_actions"]["items"][0]
    recovery = project_view["recovery"]
    next_command = recovery["next_command"]
    continue_card = continue_example()
    return {
        "ok": True,
        "turn_id": "cht_example",
        "mode": "continue",
        "message": "继续",
        "project_view": project_view,
        "leader_actions": project_view["leader_actions"],
        "leader_explanation": {
            "mode": "continue",
            "summary": "Leader is continuing from ProjectView recovery status action_required.",
            "reason": recovery["reason"],
            "next_command": next_command,
            "recommended_action_id": "act_example",
            "action_kind": "leader_action",
            "action_status": "action_required",
            "safety": "safe_apply",
            "requires_explicit_user": False,
        },
        "plan_id": "pln_example",
        "review": None,
        "recovery": recovery,
        "next_command": next_command,
        "leader_action": leader_action,
        "continue_card": continue_card,
        "inbox_card": None,
    }


def continue_example() -> dict[str, object]:
    project_view = project_view_example()
    leader_action = project_view["leader_actions"]["items"][0]
    recovery = project_view["recovery"]
    return {
        "ok": True,
        "mode": "continue",
        "project_view_schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "project_view_command": "agentdeck status",
        "status": recovery["status"],
        "reason": recovery["reason"],
        "next_command": recovery["next_command"],
        "recommended_action": recovery["recommended_action"],
        "pending": recovery["pending"],
        "leader_action": leader_action,
        "action_detail_command": "agentdeck leader action --action-id act_example",
    }


def approval_example() -> dict[str, object]:
    return {
        "count": 2,
        "approvals": [
            _approval_example_item(
                approval_id="apv_pending",
                status="pending",
                reason=None,
            ),
            _approval_example_item(
                approval_id="apv_approved",
                status="approved",
                reason=None,
            ),
        ],
    }


def _approval_example_item(approval_id: str, status: str, reason: object) -> dict[str, object]:
    item = {
        "approval_id": approval_id,
        "plan_id": "pln_example",
        "step": 1,
        "agent_id": "planner",
        "role": "planning",
        "task": "Prepare an implementation plan",
        "risk": "low",
        "status": status,
        "created_at": "2026-07-04T00:00:00+00:00",
        "reason": reason,
        "approve_command": f"agentdeck approval approve --approval-id {approval_id}",
        "reject_command": f"agentdeck approval reject --approval-id {approval_id} --reason <reason>",
        "dispatch_command": f"agentdeck approval dispatch --approval-id {approval_id}",
        "can_dispatch": status == "approved",
        "dispatch_blocker": None if status == "approved" else "approval is not approved",
    }
    return {field: item.get(field) for field in APPROVAL_ITEM_FIELDS}


def inbox_example() -> dict[str, object]:
    items = [
        _inbox_example_item(
            inbox_id="inb_task",
            event_type="task_request",
            is_head=True,
            reply_id=None,
            from_actor="leader",
            from_agent=None,
        ),
        _inbox_example_item(
            inbox_id="inb_reply",
            event_type="task_reply",
            is_head=False,
            reply_id="rep_example",
            from_actor=None,
            from_agent="coder",
        ),
    ]
    return {
        "agent_id": "planner",
        "count": len(items),
        "head_inbox_id": "inb_task",
        "items": items,
    }


def _inbox_example_item(
    *,
    inbox_id: str,
    event_type: str,
    is_head: bool,
    reply_id: object,
    from_actor: object,
    from_agent: object,
) -> dict[str, object]:
    item = {
        "inbox_id": inbox_id,
        "event_type": event_type,
        "message_id": "msg_example",
        "attempt_id": "att_example",
        "job_id": "job_example",
        "reply_id": reply_id,
        "from_actor": from_actor,
        "from_agent": from_agent,
        "to_agent": "planner",
        "task": "Review the implementation plan",
        "status": "pending",
        "created_at": "2026-07-04T00:00:00+00:00",
        "trace_command": f"agentdeck trace --id {inbox_id}",
        "ack_command": f"agentdeck ack --agent planner --inbox-id {inbox_id}",
        "is_head": is_head,
        "can_ack": is_head,
        "ack_blocker": None if is_head else "inbox item is not head",
    }
    return {field: item.get(field) for field in INBOX_ITEM_FIELDS}


def leader_action_example() -> dict[str, object]:
    project_view = project_view_example()
    leader_action = dict(project_view["leader_actions"]["items"][0])
    leader_action.pop("is_recommended", None)
    recovery = project_view["recovery"]
    action_detail = {
        **leader_action,
        "recovery": recovery,
        "recommended_action": recovery["recommended_action"],
        "matches_recommended_action": True,
    }
    return {field: action_detail.get(field) for field in LEADER_ACTION_DETAIL_FIELDS}


def leader_actions_example() -> dict[str, object]:
    project_view = project_view_example()
    action = project_view["leader_actions"]["items"][0]
    return {
        "count": 1,
        "recommended_action_id": "act_example",
        "actions": [{field: action.get(field) for field in PROJECT_VIEW_LEADER_ACTION_ITEM_FIELDS}],
    }


def trace_example() -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "query_id": "rep_example",
        "message": {
            "message_id": "msg_example",
            "from_actor": "coder",
            "to_agent": "planner",
            "task": "Review the implementation plan",
            "prompt": "# AgentDeck dispatch\n\nAgent: planner\n\n当前任务:\nReview the implementation plan",
            "status": "replied",
            "created_at": "2026-07-04T00:00:00+00:00",
        },
        "attempts": [
            {
                "attempt_id": "att_example",
                "message_id": "msg_example",
                "agent_id": "planner",
                "status": "completed",
                "created_at": "2026-07-04T00:00:00+00:00",
            }
        ],
        "jobs": [
            {
                "job_id": "job_example",
                "message_id": "msg_example",
                "attempt_id": "att_example",
                "agent_id": "planner",
                "pane_id": "%1",
                "status": "completed",
                "created_at": "2026-07-04T00:00:00+00:00",
            }
        ],
        "replies": [
            {
                "reply_id": "rep_example",
                "message_id": "msg_example",
                "attempt_id": "att_example",
                "job_id": "job_example",
                "from_agent": "planner",
                "to_actor": "coder",
                "text": "status: completed\nsummary: Plan is actionable.",
                "created_at": "2026-07-04T00:00:01+00:00",
            }
        ],
        "inbox_items": [
            {
                "inbox_id": "inb_request_example",
                "event_type": "task_request",
                "message_id": "msg_example",
                "attempt_id": "att_example",
                "job_id": "job_example",
                "reply_id": None,
                "from_actor": "coder",
                "from_agent": None,
                "to_agent": "planner",
                "task": "Review the implementation plan",
                "status": "pending",
                "created_at": "2026-07-04T00:00:00+00:00",
            },
            {
                "inbox_id": "inb_reply_example",
                "event_type": "task_reply",
                "message_id": "msg_example",
                "attempt_id": "att_example",
                "job_id": "job_example",
                "reply_id": "rep_example",
                "from_actor": None,
                "from_agent": "planner",
                "to_agent": "coder",
                "task": "Review the implementation plan",
                "status": "pending",
                "created_at": "2026-07-04T00:00:01+00:00",
            },
        ],
    }
