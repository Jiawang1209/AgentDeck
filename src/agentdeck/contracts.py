from __future__ import annotations

from pathlib import Path

from .models import PROJECT_VIEW_SCHEMA_VERSION


CONTRACT_INDEX_RESPONSE_FIELDS = (
    "schema_version",
    "contracts_command",
    "contract_docs_dir",
    "response_fields",
    "contract_item_fields",
    "count",
    "contracts",
)

CONTRACT_INDEX_ITEM_FIELDS = (
    "name",
    "command",
    "example_command",
    "contract_path",
    "contract_exists",
)

CONTRACT_INDEX_SPECS = (
    (
        "project-view",
        "agentdeck contract project-view",
        "agentdeck contract project-view --example",
        "project-view-schema.md",
    ),
    (
        "continue",
        "agentdeck contract continue",
        "agentdeck contract continue --example",
        "continue-card-schema.md",
    ),
    (
        "doctor",
        "agentdeck contract doctor",
        "agentdeck contract doctor --example",
        "doctor-schema.md",
    ),
    (
        "events",
        "agentdeck contract events",
        "agentdeck contract events --example",
        "events-schema.md",
    ),
    (
        "workbench",
        "agentdeck contract workbench",
        "agentdeck contract workbench --example",
        "workbench-schema.md",
    ),
    (
        "controls",
        "agentdeck contract controls",
        "agentdeck contract controls --example",
        "controls-schema.md",
    ),
    (
        "agent-runtime",
        "agentdeck contract agent-runtime",
        "agentdeck contract agent-runtime --example",
        "agent-runtime-schema.md",
    ),
    (
        "leader-chat",
        "agentdeck contract leader-chat",
        "agentdeck contract leader-chat --example",
        "leader-chat-schema.md",
    ),
    (
        "leader-actions",
        "agentdeck contract leader-actions",
        "agentdeck contract leader-actions --example",
        "leader-actions-schema.md",
    ),
    (
        "leader-review",
        "agentdeck contract leader-review",
        "agentdeck contract leader-review --example",
        "leader-review-schema.md",
    ),
    (
        "leader-action",
        "agentdeck contract leader-action",
        "agentdeck contract leader-action --example",
        "leader-action-schema.md",
    ),
    (
        "approvals",
        "agentdeck contract approvals",
        "agentdeck contract approvals --example",
        "approvals-schema.md",
    ),
    (
        "inbox",
        "agentdeck contract inbox",
        "agentdeck contract inbox --example",
        "inbox-schema.md",
    ),
    (
        "trace",
        "agentdeck contract trace",
        "agentdeck contract trace --example",
        "trace-schema.md",
    ),
)


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
    "runtime_stale",
)

PROJECT_VIEW_RECOMMENDED_ACTION_FIELDS = (
    "label",
    "command",
    "safety",
    "requires_explicit_user",
    "source",
    "target_id",
)

EVENTS_RESPONSE_FIELDS = (
    "count",
    "limit",
    "since_event_id",
    "latest_event_id",
    "cursor_found",
    "events",
)

EVENTS_CURSOR_FIELDS = (
    "since_event_id",
    "latest_event_id",
    "cursor_found",
)

EVENTS_EVENT_ITEM_FIELDS = (
    "event_id",
    "event_type",
    "created_at",
    "payload",
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
    "preview_command",
    "controls",
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
    "preview_command",
    "controls",
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
    "preview_command",
    "controls",
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


def contract_index_response(contract_docs_dir: Path) -> dict[str, object]:
    contracts = []
    for name, command, example_command, filename in CONTRACT_INDEX_SPECS:
        contract_path = contract_docs_dir / filename
        contracts.append(
            {
                "name": name,
                "command": command,
                "example_command": example_command,
                "contract_path": str(contract_path),
                "contract_exists": contract_path.exists(),
            }
        )
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "contracts_command": "agentdeck contract list",
        "contract_docs_dir": str(contract_docs_dir),
        "response_fields": list(CONTRACT_INDEX_RESPONSE_FIELDS),
        "contract_item_fields": list(CONTRACT_INDEX_ITEM_FIELDS),
        "count": len(contracts),
        "contracts": contracts,
    }


DOCTOR_RESPONSE_FIELDS = (
    "ok",
    "doctor_command",
    "root",
    "config_exists",
    "config_path",
    "tmux",
    "configured_leader",
    "deepseek",
    "openai_compatible",
)

DOCTOR_CONFIGURED_LEADER_FIELDS = (
    "agent_id",
    "provider",
    "model",
    "approval_mode",
    "ready",
    "supported",
    "missing_env",
    "detail",
    "setup_commands",
)

DOCTOR_PROVIDER_CHECK_FIELDS = (
    "ok",
    "detail",
)

LEADER_CHAT_RESPONSE_FIELDS = (
    "ok",
    "turn_id",
    "mode",
    "message",
    "project_view",
    "leader_actions",
    "leader_explanation",
    "intent_card",
    "plan_id",
    "review",
    "recovery",
    "next_command",
    "leader_action",
    "leader_action_card",
    "continue_card",
    "capture_card",
    "inbox_card",
    "trace_card",
    "approval_card",
    "runtime_card",
    "queue_card",
    "operator_card",
    "role_card",
    "ledger_card",
    "lineage_card",
    "workbench_card",
    "control_mode_card",
    "capability_card",
    "control_registry_card",
)

LEADER_CHAT_ACTION_CARD_FIELDS = (
    "mode",
    "title",
    "action_id",
    "kind",
    "status",
    "reason",
    "preview_command",
    "can_apply",
    "apply_command",
    "explicit_command",
    "apply_blocker",
    "controls",
)

LEADER_CHAT_CAPTURE_CARD_FIELDS = (
    "agent_id",
    "pane_id",
    "lines",
    "capture_command",
    "output",
)

CONTROL_REGISTRY_CARD_FIELDS = (
    "mode",
    "title",
    "source_command",
    "default_command",
    "item_count",
    "items",
)

LEADER_CHAT_CONTROL_REGISTRY_CARD_FIELDS = CONTROL_REGISTRY_CARD_FIELDS

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

WORKBENCH_SNAPSHOT_FIELDS = (
    "ok",
    "mode",
    "schema_version",
    "project_view",
    "leader_actions",
    "leader_card",
    "provider_health",
    "runtime_card",
    "role_card",
    "ledger_card",
    "lineage_card",
    "queue_card",
    "operator_card",
    "audit_card",
    "contracts_card",
    "control_mode_card",
    "recovery",
    "next_command",
    "continue_card",
    "active_queue_source",
    "inbox_card",
    "leader_inbox_card",
    "approval_card",
    "leader_action",
    "control_registry",
    "change_summary",
)

WORKBENCH_LEADER_CARD_FIELDS = (
    "agent_id",
    "provider",
    "model",
    "approval_mode",
    "api_backed",
    "chat_command",
    "continue_command",
    "review_command_template",
    "actions_command",
    "status_command",
    "controls",
)

WORKBENCH_LEADER_CONTROL_FIELDS = (
    "kind",
    "label",
    "command",
    "safety",
    "enabled",
    "blocker",
)

WORKBENCH_CONTROL_MODE_CARD_FIELDS = (
    "mode",
    "title",
    "current_mode",
    "approval_mode",
    "default_safety",
    "available_modes",
    "active_controls",
    "set_mode_command_template",
    "policy_source",
)

WORKBENCH_CONTROL_MODE_OPTION_FIELDS = (
    "mode",
    "label",
    "description",
    "enabled",
    "requires_explicit_user",
    "safety",
    "blocker",
)

WORKBENCH_CONTROL_MODE_CONTROL_FIELDS = (
    "kind",
    "label",
    "command",
    "safety",
    "enabled",
    "blocker",
)

WORKBENCH_PROVIDER_HEALTH_FIELDS = (
    "agent_id",
    "provider",
    "model",
    "approval_mode",
    "api_backed",
    "supported",
    "ready",
    "missing_env",
    "detail",
    "doctor_command",
    "doctor_contract",
    "setup_commands",
)

WORKBENCH_RUNTIME_CARD_FIELDS = (
    "backend",
    "count",
    "by_status",
    "refresh_command",
    "agents",
)

WORKBENCH_RUNTIME_AGENT_FIELDS = (
    "agent_id",
    "role",
    "provider",
    "workspace_mode",
    "status",
    "pane_id",
    "session_name",
    "cwd",
    "spawn_command",
    "stop_command",
    "capture_command",
    "send_command_template",
    "inbox_command",
    "controls",
)

WORKBENCH_RUNTIME_CONTROL_FIELDS = (
    "kind",
    "label",
    "command",
    "safety",
    "enabled",
    "blocker",
)

WORKBENCH_CONTROL_REGISTRY_ITEM_FIELDS = (
    "scope",
    "card",
    "kind",
    "label",
    "command",
    "safety",
    "enabled",
    "blocker",
    "agent_id",
)

AGENT_RUNTIME_AGENT_ITEM_FIELDS = (
    "agent_id",
    "role",
    "provider",
    "workspace_mode",
    "runtime",
)

AGENT_RUNTIME_CAPTURE_RESPONSE_FIELDS = (
    "agent_id",
    "pane_id",
    "output",
)

AGENT_RUNTIME_REFRESH_RESPONSE_FIELDS = (
    "ok",
    "agents",
    "stale_count",
    "running_count",
)

AGENT_RUNTIME_REFRESH_AGENT_FIELDS = (
    "agent_id",
    "previous_status",
    "status",
    "pane_id",
    "pane_exists",
    "changed",
)

WORKBENCH_ROLE_CARD_FIELDS = (
    "count",
    "agents",
    "assign_command_template",
)

WORKBENCH_ROLE_AGENT_FIELDS = (
    "agent_id",
    "role",
    "provider",
    "workspace_mode",
    "role_prompt",
    "assign_command",
)

WORKBENCH_LEDGER_CARD_FIELDS = (
    "messages",
    "jobs",
    "replies",
    "inbox",
    "trace_commands",
)

WORKBENCH_LINEAGE_CARD_FIELDS = (
    "mode",
    "title",
    "message_count",
    "job_count",
    "reply_count",
    "inbox_count",
    "trace_command_template",
    "recent_paths",
)

WORKBENCH_LINEAGE_PATH_FIELDS = (
    "message_id",
    "job_id",
    "reply_id",
    "inbox_id",
    "from_actor",
    "to_agent",
    "from_agent",
    "to_actor",
    "task",
    "status",
    "trace_command",
)

WORKBENCH_QUEUE_CARD_FIELDS = (
    "active_queue_source",
    "next_command",
    "leader_actions",
    "approvals",
    "inbox",
    "refresh_command",
)

WORKBENCH_OPERATOR_CARD_FIELDS = (
    "status",
    "reason",
    "label",
    "command",
    "next_command",
    "safety",
    "requires_explicit_user",
    "source",
    "target_id",
    "preview_command",
    "controls",
    "active_queue_source",
    "action_kind",
    "can_apply",
    "apply_command",
    "explicit_command",
    "blocker",
)

WORKBENCH_AUDIT_CARD_FIELDS = (
    "latest_event",
    "recent_events",
    "event_count",
    "events_command",
)

WORKBENCH_CONTRACTS_CARD_FIELDS = (
    "contracts_command",
    "contract_index_contract",
    "workbench_contract",
    "controls_contract",
    "agent_runtime_contract",
    "leader_chat_contract",
    "leader_review_contract",
    "project_view_contract",
    "events_contract",
    "doctor_contract",
)

WORKBENCH_CHANGE_SUMMARY_FIELDS = (
    "since_event_id",
    "latest_event_id",
    "has_new_events",
    "new_event_count",
    "new_events",
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
    "preview_command",
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

LEADER_REVIEW_RESPONSE_FIELDS = (
    "plan_id",
    "next_action",
    "reason",
    "approval_id",
    "agent_id",
    "message_id",
    "replies",
    "next_command",
    "controls",
)

LEADER_REVIEW_CONTROL_FIELDS = (
    "kind",
    "label",
    "command",
    "safety",
    "enabled",
    "blocker",
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

LEADER_CHAT_INTENT_CARD_FIELDS = (
    "mode",
    "matched_intent",
    "route_source",
    "embedded_card",
    "read_only",
    "next_command",
    "requires_explicit_user",
    "controls",
)

LEADER_CHAT_INTENT_CONTROL_FIELDS = (
    "kind",
    "label",
    "command",
    "safety",
    "enabled",
    "blocker",
)

LEADER_CHAT_CAPABILITY_CARD_FIELDS = (
    "mode",
    "title",
    "summary",
    "default_command",
    "capability_count",
    "capabilities",
)

LEADER_CHAT_CAPABILITY_ITEM_FIELDS = (
    "mode",
    "label",
    "description",
    "example_messages",
    "command",
    "safety",
    "requires_explicit_user",
    "card",
    "controls",
)

LEADER_CHAT_CAPABILITY_PLACEHOLDER_FIELDS = (
    "placeholder",
    "blocker",
)

LEADER_CHAT_CAPABILITY_PLACEHOLDERS = (
    {"placeholder": "<goal>", "blocker": "requires goal text"},
    {"placeholder": "<plan_id>", "blocker": "requires plan_id"},
    {"placeholder": "<action_id>", "blocker": "requires action_id"},
    {"placeholder": "<agent_id>", "blocker": "requires agent_id"},
    {"placeholder": "<mode>", "blocker": "requires control mode"},
)

LEADER_CHAT_INTENT_PLACEHOLDERS = (
    {"placeholder": "<reason>", "blocker": "requires reason"},
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
        "intent_card_fields": list(LEADER_CHAT_INTENT_CARD_FIELDS),
        "intent_control_fields": list(LEADER_CHAT_INTENT_CONTROL_FIELDS),
        "leader_action_card_fields": list(LEADER_CHAT_ACTION_CARD_FIELDS),
        "continue_card_fields": list(CONTINUE_CARD_FIELDS),
        "capture_card_fields": list(LEADER_CHAT_CAPTURE_CARD_FIELDS),
        "runtime_card_fields": list(WORKBENCH_RUNTIME_CARD_FIELDS),
        "queue_card_fields": list(WORKBENCH_QUEUE_CARD_FIELDS),
        "operator_card_fields": list(WORKBENCH_OPERATOR_CARD_FIELDS),
        "role_card_fields": list(WORKBENCH_ROLE_CARD_FIELDS),
        "role_agent_fields": list(WORKBENCH_ROLE_AGENT_FIELDS),
        "ledger_card_fields": list(WORKBENCH_LEDGER_CARD_FIELDS),
        "lineage_card_fields": list(WORKBENCH_LINEAGE_CARD_FIELDS),
        "lineage_path_fields": list(WORKBENCH_LINEAGE_PATH_FIELDS),
        "trace_card_fields": list(TRACE_TOP_LEVEL_FIELDS),
        "trace_message_fields": list(TRACE_MESSAGE_FIELDS),
        "trace_attempt_fields": list(TRACE_ATTEMPT_FIELDS),
        "trace_job_fields": list(TRACE_JOB_FIELDS),
        "trace_reply_fields": list(TRACE_REPLY_FIELDS),
        "trace_inbox_item_fields": list(TRACE_INBOX_ITEM_FIELDS),
        "workbench_card_fields": list(WORKBENCH_SNAPSHOT_FIELDS),
        "control_mode_card_fields": list(WORKBENCH_CONTROL_MODE_CARD_FIELDS),
        "control_mode_option_fields": list(WORKBENCH_CONTROL_MODE_OPTION_FIELDS),
        "control_mode_control_fields": list(WORKBENCH_CONTROL_MODE_CONTROL_FIELDS),
        "workbench_control_registry_item_fields": list(WORKBENCH_CONTROL_REGISTRY_ITEM_FIELDS),
        "control_registry_card_fields": list(LEADER_CHAT_CONTROL_REGISTRY_CARD_FIELDS),
        "capability_card_fields": list(LEADER_CHAT_CAPABILITY_CARD_FIELDS),
        "capability_item_fields": list(LEADER_CHAT_CAPABILITY_ITEM_FIELDS),
        "capability_control_fields": list(LEADER_CHAT_INTENT_CONTROL_FIELDS),
        "capability_placeholder_fields": list(LEADER_CHAT_CAPABILITY_PLACEHOLDER_FIELDS),
        "capability_placeholders": [dict(item) for item in LEADER_CHAT_CAPABILITY_PLACEHOLDERS],
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
        payload["example_intent_card_fields"] = list(example["intent_card"])
        payload["example_intent_control_fields"] = list(example["intent_card"]["controls"][0])
        payload["example_leader_action_card_fields"] = list(example["leader_action_card"])
        payload["example_continue_card_fields"] = list(example["continue_card"])
        payload["example_runtime_card_fields"] = list(example["runtime_card"])
        payload["example_queue_card_fields"] = list(example["queue_card"])
        payload["example_operator_card_fields"] = list(example["operator_card"])
        payload["example_role_card_fields"] = list(example["role_card"])
        payload["example_role_agent_fields"] = list(example["role_card"]["agents"][0])
        payload["example_ledger_card_fields"] = list(example["ledger_card"])
        payload["example_lineage_card_fields"] = list(example["lineage_card"])
        payload["example_lineage_path_fields"] = list(example["lineage_card"]["recent_paths"][0])
        payload["example_workbench_card_fields"] = list(example["workbench_card"])
        payload["example_control_mode_card_fields"] = list(example["control_mode_card"])
        payload["example_workbench_control_registry_item_fields"] = list(
            example["workbench_card"]["control_registry"][0]
        )
        payload["example_control_registry_card_fields"] = list(example["control_registry_card"])
        payload["example_capability_card_fields"] = list(example["capability_card"])
        payload["example_capability_item_fields"] = list(example["capability_card"]["capabilities"][0])
        payload["example_capability_control_fields"] = list(example["capability_card"]["capabilities"][0]["controls"][0])
        payload["example_capability_placeholder_fields"] = list(LEADER_CHAT_CAPABILITY_PLACEHOLDER_FIELDS)
        payload["example_leader_chat"] = example
    return payload


def leader_chat_capability_card() -> dict[str, object]:
    capability_specs = [
        {
            "mode": "workbench",
            "label": "Open workbench",
            "description": "Inspect the full local control plane snapshot.",
            "example_messages": ["打开工作台", "workbench"],
            "command": "agentdeck workbench",
            "safety": "inspect",
            "requires_explicit_user": False,
            "card": "workbench_card",
        },
        {
            "mode": "plan",
            "label": "Create Leader plan",
            "description": "Create a plan-only record from a new goal.",
            "example_messages": ["帮我实现一个功能", "设计多 Agent 任务"],
            "command": "agentdeck leader plan --task <goal>",
            "safety": "plan_only",
            "requires_explicit_user": False,
            "card": "leader_action",
        },
        {
            "mode": "review",
            "label": "Review current plan",
            "description": "Review latest plan state and recommend the next Leader action.",
            "example_messages": ["继续推进这个计划", "下一步做什么"],
            "command": "agentdeck leader review --plan-id <plan_id>",
            "safety": "safe_apply",
            "requires_explicit_user": False,
            "card": "leader_action",
        },
        {
            "mode": "apply_action",
            "label": "Apply safe Leader action",
            "description": "Apply a queued safe Leader action such as creating approvals.",
            "example_messages": ["apply action act_xxx", "/apply-action act_xxx"],
            "command": "agentdeck leader apply-action --action-id <action_id>",
            "safety": "safe_apply",
            "requires_explicit_user": False,
            "card": "leader_action",
        },
        {
            "mode": "continue",
            "label": "Continue from recovery",
            "description": "Inspect the recovery-driven next step.",
            "example_messages": ["继续", "/continue"],
            "command": "agentdeck continue",
            "safety": "inspect",
            "requires_explicit_user": False,
            "card": "continue_card",
        },
        {
            "mode": "runtime",
            "label": "Inspect runtime",
            "description": "Inspect visible tmux agent panes without sending input.",
            "example_messages": ["查看 runtime", "查看终端"],
            "command": "agentdeck agent list",
            "safety": "inspect",
            "requires_explicit_user": False,
            "card": "runtime_card",
        },
        {
            "mode": "role",
            "label": "Inspect roles",
            "description": "Inspect configured agent roles and assignment commands.",
            "example_messages": ["查看角色", "查看分工"],
            "command": "agentdeck workbench",
            "safety": "inspect",
            "requires_explicit_user": False,
            "card": "role_card",
        },
        {
            "mode": "ledger",
            "label": "Inspect ledger",
            "description": "Inspect message, job, reply, inbox, and trace summaries.",
            "example_messages": ["查看账本", "查看通信"],
            "command": "agentdeck workbench",
            "safety": "inspect",
            "requires_explicit_user": False,
            "card": "ledger_card",
        },
        {
            "mode": "queue",
            "label": "Inspect queues",
            "description": "Inspect active queue and operator controls without applying actions.",
            "example_messages": ["查看队列", "下一步按钮"],
            "command": "agentdeck workbench",
            "safety": "inspect",
            "requires_explicit_user": False,
            "card": "queue_card",
        },
        {
            "mode": "approval",
            "label": "Inspect approvals",
            "description": "Inspect approval queue and explicit approve or dispatch commands.",
            "example_messages": ["查看审批", "批准当前审批"],
            "command": "agentdeck approval list",
            "safety": "inspect",
            "requires_explicit_user": False,
            "card": "approval_card",
        },
        {
            "mode": "inbox",
            "label": "Inspect inbox",
            "description": "Inspect an agent mailbox head, trace command, or explicit ack command.",
            "example_messages": ["查看 planner inbox", "追踪 planner 当前 inbox"],
            "command": "agentdeck inbox --agent <agent_id>",
            "safety": "inspect",
            "requires_explicit_user": False,
            "card": "inbox_card",
        },
        {
            "mode": "policy",
            "label": "Set control mode",
            "description": "Suggest an explicit ask or approval-gated control mode command.",
            "example_messages": ["切换到审批模式", "回到 ask 模式"],
            "command": "agentdeck policy set-mode --mode <mode>",
            "safety": "explicit_user",
            "requires_explicit_user": True,
            "card": "control_mode_card",
        },
        {
            "mode": "setup",
            "label": "Inspect provider setup",
            "description": "Inspect Leader provider readiness and missing environment names.",
            "example_messages": ["doctor", "检查 Leader provider 配置"],
            "command": "agentdeck doctor",
            "safety": "inspect",
            "requires_explicit_user": False,
            "card": "provider_health",
        },
    ]
    capabilities = [_capability_item_with_controls(item) for item in capability_specs]
    return {
        "mode": "help",
        "title": "Leader chat capabilities",
        "summary": "Read-only capability map for natural-language and GUI command surfaces.",
        "default_command": "agentdeck workbench",
        "capability_count": len(capabilities),
        "capabilities": capabilities,
    }


def leader_chat_control_registry_card(workbench_card: dict[str, object]) -> dict[str, object]:
    items = workbench_card.get("control_registry") if isinstance(workbench_card.get("control_registry"), list) else []
    return {
        "mode": "control_registry",
        "title": "Command palette",
        "source_command": "agentdeck workbench",
        "default_command": "agentdeck controls",
        "item_count": len(items),
        "items": items,
    }


def leader_chat_action_card(action: dict[str, object]) -> dict[str, object]:
    return {
        "mode": "leader_action",
        "title": "Leader action",
        "action_id": action.get("action_id"),
        "kind": action.get("kind"),
        "status": action.get("status"),
        "reason": action.get("reason"),
        "preview_command": action.get("preview_command"),
        "can_apply": action.get("can_apply"),
        "apply_command": action.get("apply_command"),
        "explicit_command": action.get("explicit_command"),
        "apply_blocker": action.get("apply_blocker"),
        "controls": action.get("controls", []),
    }


def controls_example() -> dict[str, object]:
    return leader_chat_control_registry_card(workbench_example())


def _capability_item_with_controls(item: dict[str, object]) -> dict[str, object]:
    return {**item, "controls": [_capability_item_control(item)]}


def _capability_item_control(item: dict[str, object]) -> dict[str, object]:
    mode = str(item["mode"])
    command = str(item["command"])
    kind = {
        "plan": "plan",
        "review": "review",
        "apply_action": "apply",
        "policy": "set",
    }.get(mode, "inspect")
    blocker = _placeholder_blocker(command)
    return {
        "kind": kind,
        "label": item["label"],
        "command": command,
        "safety": item["safety"],
        "enabled": blocker is None,
        "blocker": blocker,
    }


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


def doctor_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "doctor_command": "agentdeck doctor",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "response_fields": list(DOCTOR_RESPONSE_FIELDS),
        "configured_leader_fields": list(DOCTOR_CONFIGURED_LEADER_FIELDS),
        "provider_check_fields": list(DOCTOR_PROVIDER_CHECK_FIELDS),
        "workbench_contract": "agentdeck contract workbench",
        "leader_chat_contract": "agentdeck contract leader-chat",
        "leader_review_contract": "agentdeck contract leader-review",
    }


def doctor_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = doctor_contract_payload(contract_path)
    if include_example:
        example = doctor_example()
        payload["example"] = True
        payload["example_response_fields"] = list(example)
        payload["example_configured_leader_fields"] = list(example["configured_leader"])
        payload["example_provider_check_fields"] = list(example["deepseek"])
        payload["example_doctor"] = example
    return payload


def events_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "events_command": "agentdeck events",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "response_fields": list(EVENTS_RESPONSE_FIELDS),
        "cursor_fields": list(EVENTS_CURSOR_FIELDS),
        "event_item_fields": list(EVENTS_EVENT_ITEM_FIELDS),
        "project_view_schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "project_view_contract": "agentdeck contract project-view",
        "workbench_contract": "agentdeck contract workbench",
    }


def events_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = events_contract_payload(contract_path)
    if include_example:
        example = events_example()
        payload["example"] = True
        payload["example_response_fields"] = list(example)
        payload["example_event_item_fields"] = list(example["events"][0])
        payload["example_events"] = example
    return payload


def workbench_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "workbench_command": "agentdeck workbench",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "snapshot_fields": list(WORKBENCH_SNAPSHOT_FIELDS),
        "leader_card_fields": list(WORKBENCH_LEADER_CARD_FIELDS),
        "leader_control_fields": list(WORKBENCH_LEADER_CONTROL_FIELDS),
        "control_mode_card_fields": list(WORKBENCH_CONTROL_MODE_CARD_FIELDS),
        "control_mode_option_fields": list(WORKBENCH_CONTROL_MODE_OPTION_FIELDS),
        "control_mode_control_fields": list(WORKBENCH_CONTROL_MODE_CONTROL_FIELDS),
        "provider_health_fields": list(WORKBENCH_PROVIDER_HEALTH_FIELDS),
        "runtime_card_fields": list(WORKBENCH_RUNTIME_CARD_FIELDS),
        "runtime_agent_fields": list(WORKBENCH_RUNTIME_AGENT_FIELDS),
        "runtime_control_fields": list(WORKBENCH_RUNTIME_CONTROL_FIELDS),
        "role_card_fields": list(WORKBENCH_ROLE_CARD_FIELDS),
        "role_agent_fields": list(WORKBENCH_ROLE_AGENT_FIELDS),
        "ledger_card_fields": list(WORKBENCH_LEDGER_CARD_FIELDS),
        "lineage_card_fields": list(WORKBENCH_LINEAGE_CARD_FIELDS),
        "lineage_path_fields": list(WORKBENCH_LINEAGE_PATH_FIELDS),
        "queue_card_fields": list(WORKBENCH_QUEUE_CARD_FIELDS),
        "operator_card_fields": list(WORKBENCH_OPERATOR_CARD_FIELDS),
        "audit_card_fields": list(WORKBENCH_AUDIT_CARD_FIELDS),
        "contracts_card_fields": list(WORKBENCH_CONTRACTS_CARD_FIELDS),
        "change_summary_fields": list(WORKBENCH_CHANGE_SUMMARY_FIELDS),
        "control_registry_item_fields": list(WORKBENCH_CONTROL_REGISTRY_ITEM_FIELDS),
        "project_view_schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "project_view_contract": "agentdeck contract project-view",
        "continue_contract": "agentdeck contract continue",
        "leader_actions_contract": "agentdeck contract leader-actions",
        "inbox_contract": "agentdeck contract inbox",
        "approvals_contract": "agentdeck contract approvals",
    }


def workbench_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = workbench_contract_payload(contract_path)
    if include_example:
        example = workbench_example()
        payload["example"] = True
        payload["example_snapshot_fields"] = list(example)
        payload["example_workbench"] = example
    return payload


def controls_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "controls_command": "agentdeck controls",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "control_registry_card_fields": list(CONTROL_REGISTRY_CARD_FIELDS),
        "control_registry_item_fields": list(WORKBENCH_CONTROL_REGISTRY_ITEM_FIELDS),
        "workbench_contract": "agentdeck contract workbench",
        "leader_chat_contract": "agentdeck contract leader-chat",
    }


def controls_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = controls_contract_payload(contract_path)
    if include_example:
        example = controls_example()
        payload["example"] = True
        payload["example_control_registry_card_fields"] = list(example)
        payload["example_control_registry_item_fields"] = list(example["items"][0])
        payload["example_control_registry_card"] = example
    return payload


def agent_runtime_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "list_command": "agentdeck agent list",
        "spawn_command_template": "agentdeck agent spawn --agent <id>",
        "capture_command_template": "agentdeck agent capture --agent <id> --lines 200",
        "send_command_template": "agentdeck agent send --agent <id> --text <text>",
        "stop_command_template": "agentdeck agent stop --agent <id>",
        "refresh_command": "agentdeck agent refresh",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "agent_item_fields": list(AGENT_RUNTIME_AGENT_ITEM_FIELDS),
        "capture_response_fields": list(AGENT_RUNTIME_CAPTURE_RESPONSE_FIELDS),
        "refresh_response_fields": list(AGENT_RUNTIME_REFRESH_RESPONSE_FIELDS),
        "refresh_agent_fields": list(AGENT_RUNTIME_REFRESH_AGENT_FIELDS),
        "runtime_control_fields": list(WORKBENCH_RUNTIME_CONTROL_FIELDS),
        "project_view_schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "project_view_contract": "agentdeck contract project-view",
        "workbench_contract": "agentdeck contract workbench",
    }


def agent_runtime_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = agent_runtime_contract_payload(contract_path)
    if include_example:
        example = agent_runtime_example()
        payload["example"] = True
        payload["example_agent_item_fields"] = list(example["agents"][0])
        payload["example_capture_response_fields"] = list(example["capture"])
        payload["example_refresh_response_fields"] = list(example["refresh"])
        payload["example_refresh_agent_fields"] = list(example["refresh"]["agents"][0])
        payload["example_control_fields"] = list(example["controls"][0])
        payload["example_agent_runtime"] = example
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


def leader_review_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "review_command": "agentdeck leader review --plan-id <id>",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "response_fields": list(LEADER_REVIEW_RESPONSE_FIELDS),
        "control_fields": list(LEADER_REVIEW_CONTROL_FIELDS),
        "project_view_schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "project_view_contract": "agentdeck contract project-view",
    }


def leader_review_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = leader_review_contract_payload(contract_path)
    if include_example:
        example = leader_review_example()
        payload["example"] = True
        payload["example_response_fields"] = list(example)
        payload["example_control_fields"] = list(example["controls"][0])
        payload["example_leader_review"] = example
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


def validate_leader_review_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in LEADER_REVIEW_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing leader_review field: {field}")
    controls = payload.get("controls")
    if isinstance(controls, list):
        for control in controls:
            if not isinstance(control, dict):
                errors.append("leader review controls must be objects")
                continue
            for field in LEADER_REVIEW_CONTROL_FIELDS:
                if field not in control:
                    errors.append(f"missing leader review control field: {field}")
            if "enabled" in control and not isinstance(control.get("enabled"), bool):
                errors.append("leader review control enabled must be a boolean")
            if control.get("kind") == "capture_reply":
                if "next_command" in payload and control.get("command") != payload.get("next_command"):
                    errors.append("capture_reply control command must match next_command")
                if control.get("safety") != "explicit_runtime":
                    errors.append("capture_reply control safety must be explicit_runtime")
    elif "controls" in payload:
        errors.append("controls must be a list")
    if payload.get("next_action") == "wait_for_reply" and isinstance(controls, list):
        has_capture_reply = any(
            isinstance(control, dict) and control.get("kind") == "capture_reply" for control in controls
        )
        if not has_capture_reply:
            errors.append("wait_for_reply requires capture_reply control")
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


def _validate_runtime_card_contract(errors: list[str], runtime_card: dict[str, object], *, prefix: str) -> None:
    for field in WORKBENCH_RUNTIME_CARD_FIELDS:
        if field not in runtime_card:
            errors.append(f"{prefix}: missing runtime_card field: {field}")
    agents = runtime_card.get("agents")
    if isinstance(agents, list):
        if agents:
            first_agent = agents[0]
            if isinstance(first_agent, dict):
                for field in WORKBENCH_RUNTIME_AGENT_FIELDS:
                    if field not in first_agent:
                        errors.append(f"{prefix}: missing runtime agent field: {field}")
                controls = first_agent.get("controls")
                if isinstance(controls, list):
                    if controls:
                        first_control = controls[0]
                        if isinstance(first_control, dict):
                            for field in WORKBENCH_RUNTIME_CONTROL_FIELDS:
                                if field not in first_control:
                                    errors.append(f"{prefix}: missing runtime control field: {field}")
                        else:
                            errors.append(f"{prefix}: runtime agent controls items must be objects")
                elif "controls" in first_agent:
                    errors.append(f"{prefix}: runtime agent controls must be a list")
            else:
                errors.append(f"{prefix}: runtime_card.agents items must be objects")
    elif "agents" in runtime_card:
        errors.append(f"{prefix}: runtime_card.agents must be a list")


def _validate_queue_card_contract(errors: list[str], queue_card: dict[str, object], *, prefix: str) -> None:
    for field in WORKBENCH_QUEUE_CARD_FIELDS:
        if field not in queue_card:
            errors.append(f"{prefix}: missing queue_card field: {field}")
    for section in ("leader_actions", "approvals", "inbox"):
        if section in queue_card and not isinstance(queue_card.get(section), dict):
            errors.append(f"{prefix}: queue_card.{section} must be an object")


def _validate_operator_card_contract(errors: list[str], operator_card: dict[str, object], *, prefix: str) -> None:
    for field in WORKBENCH_OPERATOR_CARD_FIELDS:
        if field not in operator_card:
            errors.append(f"{prefix}: missing operator_card field: {field}")
    if "requires_explicit_user" in operator_card and not isinstance(operator_card.get("requires_explicit_user"), bool):
        errors.append(f"{prefix}: operator_card.requires_explicit_user must be a boolean")
    if "can_apply" in operator_card and not isinstance(operator_card.get("can_apply"), bool):
        errors.append(f"{prefix}: operator_card.can_apply must be a boolean")


def _validate_role_card_contract(errors: list[str], role_card: dict[str, object], *, prefix: str) -> None:
    for field in WORKBENCH_ROLE_CARD_FIELDS:
        if field not in role_card:
            errors.append(f"{prefix}: missing role_card field: {field}")
    if "count" in role_card and not isinstance(role_card.get("count"), int):
        errors.append(f"{prefix}: role_card.count must be an integer")
    role_agents = role_card.get("agents")
    if isinstance(role_agents, list):
        if role_agents:
            first_agent = role_agents[0]
            if isinstance(first_agent, dict):
                for field in WORKBENCH_ROLE_AGENT_FIELDS:
                    if field not in first_agent:
                        errors.append(f"{prefix}: missing role agent field: {field}")
            else:
                errors.append(f"{prefix}: role_card.agents items must be objects")
    elif "agents" in role_card:
        errors.append(f"{prefix}: role_card.agents must be a list")


def _validate_ledger_card_contract(errors: list[str], ledger_card: dict[str, object], *, prefix: str) -> None:
    for field in WORKBENCH_LEDGER_CARD_FIELDS:
        if field not in ledger_card:
            errors.append(f"{prefix}: missing ledger_card field: {field}")
    for section in ("messages", "jobs", "replies", "inbox"):
        if section in ledger_card and not isinstance(ledger_card.get(section), dict):
            errors.append(f"{prefix}: ledger_card.{section} must be an object")
    trace_commands = ledger_card.get("trace_commands")
    if "trace_commands" in ledger_card and not isinstance(trace_commands, list):
        errors.append(f"{prefix}: ledger_card.trace_commands must be a list")


def _validate_lineage_card_contract(errors: list[str], lineage_card: dict[str, object], *, prefix: str) -> None:
    for field in WORKBENCH_LINEAGE_CARD_FIELDS:
        if field not in lineage_card:
            errors.append(f"{prefix}: missing lineage_card field: {field}")
    for count_field in ("message_count", "job_count", "reply_count", "inbox_count"):
        if count_field in lineage_card and not isinstance(lineage_card.get(count_field), int):
            errors.append(f"{prefix}: lineage_card.{count_field} must be an integer")
    recent_paths = lineage_card.get("recent_paths")
    if isinstance(recent_paths, list):
        for path in recent_paths:
            if not isinstance(path, dict):
                errors.append(f"{prefix}: lineage paths must be objects")
                continue
            for field in WORKBENCH_LINEAGE_PATH_FIELDS:
                if field not in path:
                    errors.append(f"{prefix}: missing lineage path field: {field}")
    elif "recent_paths" in lineage_card:
        errors.append(f"{prefix}: lineage_card.recent_paths must be a list")


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
    intent_card = payload.get("intent_card")
    if isinstance(intent_card, dict):
        for field in LEADER_CHAT_INTENT_CARD_FIELDS:
            if field not in intent_card:
                errors.append(f"missing intent_card field: {field}")
        if intent_card.get("next_command") != payload.get("next_command"):
            errors.append("intent_card: next_command must match response next_command")
        controls = intent_card.get("controls")
        if isinstance(controls, list):
            for control in controls:
                if isinstance(control, dict):
                    for field in LEADER_CHAT_INTENT_CONTROL_FIELDS:
                        if field not in control:
                            errors.append(f"intent_card.controls: missing control field: {field}")
                    if control.get("kind") == "inspect" and control.get("safety") != "inspect":
                        errors.append("intent_card.controls: inspect controls must use safety=inspect")
                    placeholder_enabled = _command_has_placeholder(control.get("command")) and control.get("enabled") is not False
                    if placeholder_enabled:
                        errors.append("intent_card.controls: placeholder commands must be disabled")
                    expected_blocker = leader_chat_intent_placeholder_blocker(control.get("command"))
                    if not placeholder_enabled and expected_blocker is not None and control.get("blocker") != expected_blocker:
                        errors.append("intent_card.controls: blocker must match placeholder")
                    if control.get("enabled") is False and not control.get("blocker"):
                        errors.append("intent_card.controls: disabled controls must include blocker")
                else:
                    errors.append("intent_card.controls items must be objects")
        elif "controls" in intent_card:
            errors.append("intent_card.controls must be a list")
    elif "intent_card" in payload:
        errors.append("intent_card must be an object")
    continue_card = payload.get("continue_card")
    if isinstance(continue_card, dict):
        continue_card_validation = validate_continue_contract(continue_card)
        for error in continue_card_validation["errors"]:
            errors.append(f"continue_card: {error}")
    elif "continue_card" in payload and continue_card is not None:
        errors.append("continue_card must be an object")
    capture_card = payload.get("capture_card")
    if isinstance(capture_card, dict):
        _validate_leader_chat_capture_card_contract(errors, capture_card)
    elif "capture_card" in payload and capture_card is not None:
        errors.append("capture_card must be an object")
    leader_action_card = payload.get("leader_action_card")
    leader_action = payload.get("leader_action")
    if isinstance(leader_action_card, dict):
        _validate_leader_chat_action_card_contract(errors, leader_action_card)
        if isinstance(leader_action, dict) and leader_action_card.get("action_id") != leader_action.get("action_id"):
            errors.append("leader_action_card.action_id must match leader_action.action_id")
    elif isinstance(leader_action, dict):
        errors.append("leader_action_card is required when leader_action is present")
    elif "leader_action_card" in payload and leader_action_card is not None:
        errors.append("leader_action_card must be an object")
    inbox_card = payload.get("inbox_card")
    if isinstance(inbox_card, dict):
        inbox_card_validation = validate_inbox_contract(inbox_card)
        for error in inbox_card_validation["errors"]:
            errors.append(f"inbox_card: {error}")
    elif "inbox_card" in payload and inbox_card is not None:
        errors.append("inbox_card must be an object")
    approval_card = payload.get("approval_card")
    if isinstance(approval_card, dict):
        approval_card_validation = validate_approval_contract(approval_card)
        for error in approval_card_validation["errors"]:
            errors.append(f"approval_card: {error}")
    elif "approval_card" in payload and approval_card is not None:
        errors.append("approval_card must be an object")
    runtime_card = payload.get("runtime_card")
    if isinstance(runtime_card, dict):
        _validate_runtime_card_contract(errors, runtime_card, prefix="runtime_card")
    elif "runtime_card" in payload and runtime_card is not None:
        errors.append("runtime_card must be an object")
    queue_card = payload.get("queue_card")
    if isinstance(queue_card, dict):
        _validate_queue_card_contract(errors, queue_card, prefix="queue_card")
        if payload.get("next_command") != queue_card.get("next_command"):
            errors.append("queue_card: next_command must match queue_card.next_command")
    elif "queue_card" in payload and queue_card is not None:
        errors.append("queue_card must be an object")
    operator_card = payload.get("operator_card")
    if isinstance(operator_card, dict):
        _validate_operator_card_contract(errors, operator_card, prefix="operator_card")
        if payload.get("next_command") != operator_card.get("next_command"):
            errors.append("operator_card: next_command must match operator_card.next_command")
    elif "operator_card" in payload and operator_card is not None:
        errors.append("operator_card must be an object")
    role_card = payload.get("role_card")
    if isinstance(role_card, dict):
        _validate_role_card_contract(errors, role_card, prefix="role_card")
    elif "role_card" in payload and role_card is not None:
        errors.append("role_card must be an object")
    ledger_card = payload.get("ledger_card")
    if isinstance(ledger_card, dict):
        _validate_ledger_card_contract(errors, ledger_card, prefix="ledger_card")
    elif "ledger_card" in payload and ledger_card is not None:
        errors.append("ledger_card must be an object")
    lineage_card = payload.get("lineage_card")
    if isinstance(lineage_card, dict):
        _validate_lineage_card_contract(errors, lineage_card, prefix="lineage_card")
    elif "lineage_card" in payload and lineage_card is not None:
        errors.append("lineage_card must be an object")
    trace_card = payload.get("trace_card")
    if isinstance(trace_card, dict):
        trace_card_validation = validate_trace_contract(trace_card)
        for error in trace_card_validation["errors"]:
            errors.append(f"trace_card: {error}")
    elif "trace_card" in payload and trace_card is not None:
        errors.append("trace_card must be an object")
    workbench_card = payload.get("workbench_card")
    if isinstance(workbench_card, dict):
        workbench_validation = validate_workbench_contract(workbench_card)
        for error in workbench_validation["errors"]:
            errors.append(f"workbench_card: {error}")
    elif "workbench_card" in payload and workbench_card is not None:
        errors.append("workbench_card must be an object")
    capability_card = payload.get("capability_card")
    if isinstance(capability_card, dict):
        _validate_capability_card_contract(errors, capability_card)
    elif "capability_card" in payload and capability_card is not None:
        errors.append("capability_card must be an object")
    control_registry_card = payload.get("control_registry_card")
    if isinstance(control_registry_card, dict):
        _validate_control_registry_card_contract(errors, control_registry_card)
    elif "control_registry_card" in payload and control_registry_card is not None:
        errors.append("control_registry_card must be an object")
    return {"ok": not errors, "errors": errors}


def validate_control_registry_card_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    _validate_control_registry_card_contract(errors, payload)
    return {"ok": not errors, "errors": errors}


def _validate_leader_chat_action_card_contract(errors: list[str], action_card: dict[str, object]) -> None:
    for field in LEADER_CHAT_ACTION_CARD_FIELDS:
        if field not in action_card:
            errors.append(f"missing leader_action_card field: {field}")
    if action_card.get("mode") != "leader_action":
        errors.append("leader_action_card.mode must be leader_action")
    controls = action_card.get("controls")
    if isinstance(controls, list):
        for control in controls:
            if isinstance(control, dict):
                for field in LEADER_CHAT_INTENT_CONTROL_FIELDS:
                    if field not in control:
                        errors.append(f"leader_action_card.controls: missing control field: {field}")
                if control.get("kind") == "inspect" and control.get("safety") != "inspect":
                    errors.append("leader_action_card.controls: inspect controls must use safety=inspect")
                if control.get("enabled") is False and not control.get("blocker"):
                    errors.append("leader_action_card.controls: disabled controls must include blocker")
            else:
                errors.append("leader_action_card.controls items must be objects")
    elif "controls" in action_card:
        errors.append("leader_action_card.controls must be a list")


def _validate_leader_chat_capture_card_contract(errors: list[str], capture_card: dict[str, object]) -> None:
    for field in LEADER_CHAT_CAPTURE_CARD_FIELDS:
        if field not in capture_card:
            errors.append(f"missing capture_card field: {field}")
    if "lines" in capture_card and not isinstance(capture_card.get("lines"), int):
        errors.append("capture_card.lines must be an integer")
    if "output" in capture_card and not isinstance(capture_card.get("output"), str):
        errors.append("capture_card.output must be a string")


def _validate_control_registry_card_contract(errors: list[str], control_registry_card: dict[str, object]) -> None:
    for field in LEADER_CHAT_CONTROL_REGISTRY_CARD_FIELDS:
        if field not in control_registry_card:
            errors.append(f"missing control_registry_card field: {field}")
    if control_registry_card.get("mode") != "control_registry":
        errors.append("control_registry_card.mode must be control_registry")
    items = control_registry_card.get("items")
    if isinstance(items, list):
        if control_registry_card.get("item_count") != len(items):
            errors.append("control_registry_card.item_count must match items length")
        for item in items:
            if not isinstance(item, dict):
                errors.append("control_registry_card.items must be objects")
                continue
            for field in WORKBENCH_CONTROL_REGISTRY_ITEM_FIELDS:
                if field not in item:
                    errors.append(f"control_registry_card.items: missing item field: {field}")
    elif "items" in control_registry_card:
        errors.append("control_registry_card.items must be a list")


def _validate_capability_card_contract(errors: list[str], capability_card: dict[str, object]) -> None:
    for field in LEADER_CHAT_CAPABILITY_CARD_FIELDS:
        if field not in capability_card:
            errors.append(f"missing capability_card field: {field}")
    if capability_card.get("mode") != "help":
        errors.append("capability_card.mode must be help")
    capabilities = capability_card.get("capabilities")
    if isinstance(capabilities, list):
        if capability_card.get("capability_count") != len(capabilities):
            errors.append("capability_card.capability_count must match capabilities length")
        for item in capabilities:
            if isinstance(item, dict):
                for field in LEADER_CHAT_CAPABILITY_ITEM_FIELDS:
                    if field not in item:
                        errors.append(f"capability_card.capabilities: missing capability field: {field}")
                if item.get("mode") == "plan" and item.get("safety") != "plan_only":
                    errors.append("capability_card.capabilities: plan must use safety=plan_only")
                if item.get("mode") in {"review", "apply_action"} and item.get("safety") != "safe_apply":
                    errors.append(f"capability_card.capabilities: {item.get('mode')} must use safety=safe_apply")
                _validate_capability_controls(errors, item)
            else:
                errors.append("capability_card.capabilities items must be objects")
    elif "capabilities" in capability_card:
        errors.append("capability_card.capabilities must be a list")


def _validate_capability_controls(errors: list[str], item: dict[str, object]) -> None:
    controls = item.get("controls")
    if isinstance(controls, list):
        for control in controls:
            if isinstance(control, dict):
                for field in LEADER_CHAT_INTENT_CONTROL_FIELDS:
                    if field not in control:
                        errors.append(f"capability_card.capabilities.controls: missing control field: {field}")
                if control.get("command") != item.get("command"):
                    errors.append("capability_card.capabilities.controls: command must match capability command")
                if control.get("safety") != item.get("safety"):
                    errors.append("capability_card.capabilities.controls: safety must match capability safety")
                placeholder_enabled = _command_has_placeholder(control.get("command")) and control.get("enabled") is not False
                if placeholder_enabled:
                    errors.append("capability_card.capabilities.controls: placeholder commands must be disabled")
                expected_blocker = _placeholder_blocker(control.get("command"))
                if _command_has_placeholder(control.get("command")) and expected_blocker is None:
                    errors.append("capability_card.capabilities.controls: unsupported placeholder")
                if not placeholder_enabled and expected_blocker is not None and control.get("blocker") != expected_blocker:
                    errors.append("capability_card.capabilities.controls: blocker must match placeholder")
                if control.get("enabled") is False and not control.get("blocker"):
                    errors.append("capability_card.capabilities.controls: disabled controls must include blocker")
            else:
                errors.append("capability_card.capabilities.controls items must be objects")
    elif "controls" in item:
        errors.append("capability_card.capabilities.controls must be a list")


def _command_has_placeholder(command: object) -> bool:
    return isinstance(command, str) and "<" in command and ">" in command


def _placeholder_blocker(command: object) -> str | None:
    if not isinstance(command, str):
        return None
    for item in LEADER_CHAT_CAPABILITY_PLACEHOLDERS:
        if item["placeholder"] in command:
            return str(item["blocker"])
    return None


def leader_chat_intent_placeholder_blocker(command: object) -> str | None:
    if not isinstance(command, str):
        return None
    for item in LEADER_CHAT_INTENT_PLACEHOLDERS:
        if item["placeholder"] in command:
            return str(item["blocker"])
    if _command_has_placeholder(command):
        return "requires template input"
    return None


def validate_workbench_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in WORKBENCH_SNAPSHOT_FIELDS:
        if field not in payload:
            errors.append(f"missing workbench field: {field}")
    if payload.get("mode") != "workbench":
        errors.append("mode must be workbench")
    project_view = payload.get("project_view")
    if isinstance(project_view, dict):
        project_view_validation = validate_project_view_contract(project_view)
        for error in project_view_validation["errors"]:
            errors.append(f"project_view: {error}")
        if payload.get("leader_actions") != project_view.get("leader_actions"):
            errors.append("leader_actions must match project_view.leader_actions")
        if payload.get("recovery") != project_view.get("recovery"):
            errors.append("recovery must match project_view.recovery")
    elif "project_view" in payload:
        errors.append("project_view must be an object")
    leader_card = payload.get("leader_card")
    if isinstance(leader_card, dict):
        for field in WORKBENCH_LEADER_CARD_FIELDS:
            if field not in leader_card:
                errors.append(f"missing leader_card field: {field}")
        if "api_backed" in leader_card and not isinstance(leader_card.get("api_backed"), bool):
            errors.append("leader_card.api_backed must be a boolean")
        controls = leader_card.get("controls")
        if isinstance(controls, list):
            for control in controls:
                if not isinstance(control, dict):
                    errors.append("leader controls must be objects")
                    continue
                for field in WORKBENCH_LEADER_CONTROL_FIELDS:
                    if field not in control:
                        errors.append(f"missing leader control field: {field}")
                if "enabled" in control and not isinstance(control.get("enabled"), bool):
                    errors.append("leader control enabled must be a boolean")
                if control.get("enabled") is False and not control.get("blocker"):
                    errors.append("disabled leader control requires blocker")
        elif "controls" in leader_card:
            errors.append("leader_card.controls must be a list")
    elif "leader_card" in payload:
        errors.append("leader_card must be an object")
    control_mode_card = payload.get("control_mode_card")
    if isinstance(control_mode_card, dict):
        for field in WORKBENCH_CONTROL_MODE_CARD_FIELDS:
            if field not in control_mode_card:
                errors.append(f"missing control_mode_card field: {field}")
        available_modes = control_mode_card.get("available_modes")
        if isinstance(available_modes, list):
            for option in available_modes:
                if not isinstance(option, dict):
                    errors.append("control mode options must be objects")
                    continue
                for field in WORKBENCH_CONTROL_MODE_OPTION_FIELDS:
                    if field not in option:
                        errors.append(f"missing control mode option field: {field}")
                if "enabled" in option and not isinstance(option.get("enabled"), bool):
                    errors.append("control mode option enabled must be a boolean")
                if "requires_explicit_user" in option and not isinstance(
                    option.get("requires_explicit_user"), bool
                ):
                    errors.append("control mode option requires_explicit_user must be a boolean")
                if option.get("enabled") is False and not option.get("blocker"):
                    errors.append("disabled control mode option requires blocker")
        elif "available_modes" in control_mode_card:
            errors.append("control_mode_card.available_modes must be a list")
        active_controls = control_mode_card.get("active_controls")
        if isinstance(active_controls, list):
            for control in active_controls:
                if not isinstance(control, dict):
                    errors.append("control mode controls must be objects")
                    continue
                for field in WORKBENCH_CONTROL_MODE_CONTROL_FIELDS:
                    if field not in control:
                        errors.append(f"missing control mode control field: {field}")
                if "enabled" in control and not isinstance(control.get("enabled"), bool):
                    errors.append("control mode control enabled must be a boolean")
                if control.get("enabled") is False and not control.get("blocker"):
                    errors.append("disabled control mode control requires blocker")
        elif "active_controls" in control_mode_card:
            errors.append("control_mode_card.active_controls must be a list")
    elif "control_mode_card" in payload:
        errors.append("control_mode_card must be an object")
    control_registry = payload.get("control_registry")
    if isinstance(control_registry, list):
        for item in control_registry:
            if not isinstance(item, dict):
                errors.append("control_registry items must be objects")
                continue
            for field in WORKBENCH_CONTROL_REGISTRY_ITEM_FIELDS:
                if field not in item:
                    errors.append(f"missing control_registry item field: {field}")
            if "enabled" in item and not isinstance(item.get("enabled"), bool):
                errors.append("control_registry item enabled must be a boolean")
            if item.get("enabled") is False and not item.get("blocker"):
                errors.append("disabled control_registry item requires blocker")
    elif "control_registry" in payload:
        errors.append("control_registry must be a list")
    provider_health = payload.get("provider_health")
    if isinstance(provider_health, dict):
        for field in WORKBENCH_PROVIDER_HEALTH_FIELDS:
            if field not in provider_health:
                errors.append(f"missing provider_health field: {field}")
        if "supported" in provider_health and not isinstance(provider_health.get("supported"), bool):
            errors.append("provider_health.supported must be a boolean")
        if "ready" in provider_health and not isinstance(provider_health.get("ready"), bool):
            errors.append("provider_health.ready must be a boolean")
        if "missing_env" in provider_health and not isinstance(provider_health.get("missing_env"), list):
            errors.append("provider_health.missing_env must be a list")
        if "setup_commands" in provider_health and not isinstance(provider_health.get("setup_commands"), list):
            errors.append("provider_health.setup_commands must be a list")
    elif "provider_health" in payload:
        errors.append("provider_health must be an object")
    runtime_card = payload.get("runtime_card")
    if isinstance(runtime_card, dict):
        for field in WORKBENCH_RUNTIME_CARD_FIELDS:
            if field not in runtime_card:
                errors.append(f"missing runtime_card field: {field}")
        agents = runtime_card.get("agents")
        if isinstance(agents, list):
            if agents:
                first_agent = agents[0]
                if isinstance(first_agent, dict):
                    for field in WORKBENCH_RUNTIME_AGENT_FIELDS:
                        if field not in first_agent:
                            errors.append(f"missing runtime agent field: {field}")
                    controls = first_agent.get("controls")
                    if isinstance(controls, list):
                        if controls:
                            first_control = controls[0]
                            if isinstance(first_control, dict):
                                for field in WORKBENCH_RUNTIME_CONTROL_FIELDS:
                                    if field not in first_control:
                                        errors.append(f"missing runtime control field: {field}")
                            else:
                                errors.append("runtime agent controls items must be objects")
                    elif "controls" in first_agent:
                        errors.append("runtime agent controls must be a list")
                else:
                    errors.append("runtime_card.agents items must be objects")
        elif "agents" in runtime_card:
            errors.append("runtime_card.agents must be a list")
    elif "runtime_card" in payload:
        errors.append("runtime_card must be an object")
    role_card = payload.get("role_card")
    if isinstance(role_card, dict):
        for field in WORKBENCH_ROLE_CARD_FIELDS:
            if field not in role_card:
                errors.append(f"missing role_card field: {field}")
        if "count" in role_card and not isinstance(role_card.get("count"), int):
            errors.append("role_card.count must be an integer")
        role_agents = role_card.get("agents")
        if isinstance(role_agents, list):
            if role_agents:
                first_agent = role_agents[0]
                if isinstance(first_agent, dict):
                    for field in WORKBENCH_ROLE_AGENT_FIELDS:
                        if field not in first_agent:
                            errors.append(f"missing role agent field: {field}")
                else:
                    errors.append("role_card.agents items must be objects")
        elif "agents" in role_card:
            errors.append("role_card.agents must be a list")
    elif "role_card" in payload:
        errors.append("role_card must be an object")
    ledger_card = payload.get("ledger_card")
    if isinstance(ledger_card, dict):
        for field in WORKBENCH_LEDGER_CARD_FIELDS:
            if field not in ledger_card:
                errors.append(f"missing ledger_card field: {field}")
        messages = ledger_card.get("messages")
        if isinstance(messages, dict):
            _validate_project_view_summary_items(
                errors, ledger_card, "messages", PROJECT_VIEW_MESSAGE_ITEM_FIELDS, "message"
            )
        elif "messages" in ledger_card:
            errors.append("ledger_card.messages must be an object")
        jobs = ledger_card.get("jobs")
        if isinstance(jobs, dict):
            _validate_project_view_summary_items(errors, ledger_card, "jobs", PROJECT_VIEW_JOB_ITEM_FIELDS, "job")
        elif "jobs" in ledger_card:
            errors.append("ledger_card.jobs must be an object")
        replies = ledger_card.get("replies")
        if isinstance(replies, dict):
            _validate_project_view_summary_items(errors, ledger_card, "replies", PROJECT_VIEW_REPLY_ITEM_FIELDS, "reply")
        elif "replies" in ledger_card:
            errors.append("ledger_card.replies must be an object")
        trace_commands = ledger_card.get("trace_commands")
        if not isinstance(trace_commands, list):
            errors.append("ledger_card.trace_commands must be a list")
    elif "ledger_card" in payload:
        errors.append("ledger_card must be an object")
    lineage_card = payload.get("lineage_card")
    if isinstance(lineage_card, dict):
        for field in WORKBENCH_LINEAGE_CARD_FIELDS:
            if field not in lineage_card:
                errors.append(f"missing lineage_card field: {field}")
        for count_field in ("message_count", "job_count", "reply_count", "inbox_count"):
            if count_field in lineage_card and not isinstance(lineage_card.get(count_field), int):
                errors.append(f"lineage_card.{count_field} must be an integer")
        recent_paths = lineage_card.get("recent_paths")
        if isinstance(recent_paths, list):
            for path in recent_paths:
                if not isinstance(path, dict):
                    errors.append("lineage paths must be objects")
                    continue
                for field in WORKBENCH_LINEAGE_PATH_FIELDS:
                    if field not in path:
                        errors.append(f"missing lineage path field: {field}")
        elif "recent_paths" in lineage_card:
            errors.append("lineage_card.recent_paths must be a list")
    elif "lineage_card" in payload:
        errors.append("lineage_card must be an object")
    queue_card = payload.get("queue_card")
    if isinstance(queue_card, dict):
        for field in WORKBENCH_QUEUE_CARD_FIELDS:
            if field not in queue_card:
                errors.append(f"missing queue_card field: {field}")
        if "active_queue_source" in queue_card and payload.get("active_queue_source") != queue_card.get(
            "active_queue_source"
        ):
            errors.append("active_queue_source must match queue_card.active_queue_source")
        if "next_command" in queue_card and payload.get("next_command") != queue_card.get("next_command"):
            errors.append("next_command must match queue_card.next_command")
        for section in ("leader_actions", "approvals", "inbox"):
            if section in queue_card and not isinstance(queue_card.get(section), dict):
                errors.append(f"queue_card.{section} must be an object")
    elif "queue_card" in payload:
        errors.append("queue_card must be an object")
    operator_card = payload.get("operator_card")
    if isinstance(operator_card, dict):
        for field in WORKBENCH_OPERATOR_CARD_FIELDS:
            if field not in operator_card:
                errors.append(f"missing operator_card field: {field}")
        if "next_command" in operator_card and payload.get("next_command") != operator_card.get("next_command"):
            errors.append("next_command must match operator_card.next_command")
        if "active_queue_source" in operator_card and payload.get("active_queue_source") != operator_card.get(
            "active_queue_source"
        ):
            errors.append("active_queue_source must match operator_card.active_queue_source")
        if "requires_explicit_user" in operator_card and not isinstance(
            operator_card.get("requires_explicit_user"), bool
        ):
            errors.append("operator_card.requires_explicit_user must be a boolean")
        if "can_apply" in operator_card and not isinstance(operator_card.get("can_apply"), bool):
            errors.append("operator_card.can_apply must be a boolean")
    elif "operator_card" in payload:
        errors.append("operator_card must be an object")
    audit_card = payload.get("audit_card")
    if isinstance(audit_card, dict):
        for field in WORKBENCH_AUDIT_CARD_FIELDS:
            if field not in audit_card:
                errors.append(f"missing audit_card field: {field}")
        if "recent_events" in audit_card and not isinstance(audit_card.get("recent_events"), list):
            errors.append("audit_card.recent_events must be a list")
        if "event_count" in audit_card and not isinstance(audit_card.get("event_count"), int):
            errors.append("audit_card.event_count must be an integer")
    elif "audit_card" in payload:
        errors.append("audit_card must be an object")
    contracts_card = payload.get("contracts_card")
    if isinstance(contracts_card, dict):
        for field in WORKBENCH_CONTRACTS_CARD_FIELDS:
            if field not in contracts_card:
                errors.append(f"missing contracts_card field: {field}")
    elif "contracts_card" in payload:
        errors.append("contracts_card must be an object")
    change_summary = payload.get("change_summary")
    if isinstance(change_summary, dict):
        for field in WORKBENCH_CHANGE_SUMMARY_FIELDS:
            if field not in change_summary:
                errors.append(f"missing change_summary field: {field}")
        if "has_new_events" in change_summary and not isinstance(change_summary.get("has_new_events"), bool):
            errors.append("change_summary.has_new_events must be a boolean")
        if "new_event_count" in change_summary and not isinstance(change_summary.get("new_event_count"), int):
            errors.append("change_summary.new_event_count must be an integer")
        if "new_events" in change_summary and not isinstance(change_summary.get("new_events"), list):
            errors.append("change_summary.new_events must be a list")
    elif "change_summary" in payload:
        errors.append("change_summary must be an object")
    continue_card = payload.get("continue_card")
    if isinstance(continue_card, dict):
        continue_card_validation = validate_continue_contract(continue_card)
        for error in continue_card_validation["errors"]:
            errors.append(f"continue_card: {error}")
        if payload.get("next_command") != continue_card.get("next_command"):
            errors.append("next_command must match continue_card.next_command")
    elif "continue_card" in payload:
        errors.append("continue_card must be an object")
    inbox_card = payload.get("inbox_card")
    if isinstance(inbox_card, dict):
        inbox_card_validation = validate_inbox_contract(inbox_card)
        for error in inbox_card_validation["errors"]:
            errors.append(f"inbox_card: {error}")
    elif "inbox_card" in payload and inbox_card is not None:
        errors.append("inbox_card must be an object")
    leader_inbox_card = payload.get("leader_inbox_card")
    if isinstance(leader_inbox_card, dict):
        leader_inbox_card_validation = validate_inbox_contract(leader_inbox_card)
        for error in leader_inbox_card_validation["errors"]:
            errors.append(f"leader_inbox_card: {error}")
        if leader_inbox_card.get("agent_id") != "leader":
            errors.append("leader_inbox_card.agent_id must be leader")
    elif "leader_inbox_card" in payload:
        errors.append("leader_inbox_card must be an object")
    approval_card = payload.get("approval_card")
    if isinstance(approval_card, dict):
        approval_card_validation = validate_approval_contract(approval_card)
        for error in approval_card_validation["errors"]:
            errors.append(f"approval_card: {error}")
    elif "approval_card" in payload and approval_card is not None:
        errors.append("approval_card must be an object")
    source = payload.get("active_queue_source")
    if source not in ("none", "leader_action", "inbox", "approval", "provider_health", "runtime"):
        errors.append("active_queue_source must be none, leader_action, inbox, approval, provider_health, or runtime")
    if source == "inbox" and not isinstance(inbox_card, dict):
        errors.append("inbox active queue requires inbox_card")
    if source == "approval" and not isinstance(approval_card, dict):
        errors.append("approval active queue requires approval_card")
    if source == "leader_action" and not isinstance(payload.get("leader_action"), dict):
        errors.append("leader_action active queue requires leader_action")
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
                    "preview_command": "agentdeck leader action --action-id act_example",
                    "controls": _leader_action_controls(
                        "act_example",
                        "agentdeck leader apply-action --action-id act_example",
                        "agentdeck approval create-from-plan --plan-id pln_example",
                        None,
                    ),
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
                "runtime_stale": 0,
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
    runtime_card = workbench_example()["runtime_card"]
    queue_card = workbench_example()["queue_card"]
    operator_card = workbench_example()["operator_card"]
    role_card = workbench_example()["role_card"]
    ledger_card = workbench_example()["ledger_card"]
    lineage_card = workbench_example()["lineage_card"]
    workbench_card = workbench_example()
    control_mode_card = workbench_card["control_mode_card"]
    capability_card = leader_chat_capability_card()
    control_registry_card = leader_chat_control_registry_card(workbench_card)
    leader_action_card = leader_chat_action_card(leader_action)
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
        "intent_card": {
            "mode": "continue",
            "matched_intent": "continue",
            "route_source": "local_rule",
            "embedded_card": "continue_card",
            "read_only": True,
            "next_command": next_command,
            "requires_explicit_user": False,
            "controls": [
                {
                    "kind": "next",
                    "label": "Next command",
                    "command": next_command,
                    "safety": "safe_apply",
                    "enabled": True,
                    "blocker": None,
                }
            ],
        },
        "plan_id": "pln_example",
        "review": None,
        "recovery": recovery,
        "next_command": next_command,
        "leader_action": leader_action,
        "leader_action_card": leader_action_card,
        "continue_card": continue_card,
        "capture_card": None,
        "inbox_card": None,
        "trace_card": None,
        "approval_card": None,
        "runtime_card": runtime_card,
        "queue_card": queue_card,
        "operator_card": operator_card,
        "role_card": role_card,
        "ledger_card": ledger_card,
        "lineage_card": lineage_card,
        "workbench_card": workbench_card,
        "control_mode_card": control_mode_card,
        "capability_card": capability_card,
        "control_registry_card": control_registry_card,
    }


def doctor_example() -> dict[str, object]:
    return {
        "ok": False,
        "doctor_command": "agentdeck doctor",
        "root": "/workspace/agentdeck-example",
        "config_exists": True,
        "config_path": "/workspace/agentdeck-example/.agentdeck/config.toml",
        "tmux": {
            "ok": True,
            "detail": "tmux is available",
        },
        "configured_leader": {
            "agent_id": "leader",
            "provider": "deepseek",
            "model": "deepseek-chat",
            "approval_mode": "confirm",
            "ready": False,
            "supported": True,
            "missing_env": ["DEEPSEEK_API_KEY"],
            "detail": "DEEPSEEK_API_KEY is not set; provider calls are disabled",
            "setup_commands": [
                'export DEEPSEEK_API_KEY="<your-deepseek-api-key>"',
                'export DEEPSEEK_BASE_URL="https://api.deepseek.com/v1"',
                'export DEEPSEEK_MODEL="deepseek-chat"',
            ],
        },
        "deepseek": {
            "ok": False,
            "detail": "DEEPSEEK_API_KEY is not set; provider calls are disabled",
        },
        "openai_compatible": {
            "ok": False,
            "detail": "AGENTDECK_LEADER_API_KEY is not set; provider calls are disabled",
        },
    }


def events_example() -> dict[str, object]:
    return {
        "count": 1,
        "limit": 20,
        "since_event_id": "evt_old",
        "latest_event_id": "evt_new",
        "cursor_found": True,
        "events": [
            {
                "event_id": "evt_new",
                "event_type": "leader_plan_created",
                "created_at": "2026-07-05T00:00:00+00:00",
                "payload": {"plan_id": "pln_example"},
            }
        ],
    }


def runtime_agent_controls(agent_id: str, running: bool) -> list[dict[str, object]]:
    if running:
        return [
            {
                "kind": "capture",
                "label": "Capture pane output",
                "command": f"agentdeck agent capture --agent {agent_id} --lines 200",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "send",
                "label": "Send input",
                "command": f"agentdeck agent send --agent {agent_id} --text <text>",
                "safety": "explicit_runtime",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "stop",
                "label": "Stop pane",
                "command": f"agentdeck agent stop --agent {agent_id}",
                "safety": "explicit_runtime",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "inbox",
                "label": "Open inbox",
                "command": f"agentdeck inbox --agent {agent_id}",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
        ]
    return [
        {
            "kind": "spawn",
            "label": "Spawn pane",
            "command": f"agentdeck agent spawn --agent {agent_id}",
            "safety": "explicit_runtime",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "capture",
            "label": "Capture pane output",
            "command": f"agentdeck agent capture --agent {agent_id} --lines 200",
            "safety": "inspect",
            "enabled": False,
            "blocker": "agent is not running",
        },
        {
            "kind": "send",
            "label": "Send input",
            "command": f"agentdeck agent send --agent {agent_id} --text <text>",
            "safety": "explicit_runtime",
            "enabled": False,
            "blocker": "agent is not running",
        },
        {
            "kind": "stop",
            "label": "Stop pane",
            "command": f"agentdeck agent stop --agent {agent_id}",
            "safety": "explicit_runtime",
            "enabled": False,
            "blocker": "agent is not running",
        },
        {
            "kind": "inbox",
            "label": "Open inbox",
            "command": f"agentdeck inbox --agent {agent_id}",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
    ]


def workbench_control_registry(payload: dict[str, object]) -> list[dict[str, object]]:
    registry: list[dict[str, object]] = []
    leader_card = payload.get("leader_card") if isinstance(payload.get("leader_card"), dict) else {}
    _append_control_registry_items(
        registry,
        scope="leader",
        card="leader_card",
        agent_id=str(leader_card.get("agent_id", "leader")),
        controls=leader_card.get("controls"),
    )
    control_mode_card = payload.get("control_mode_card") if isinstance(payload.get("control_mode_card"), dict) else {}
    _append_control_registry_items(
        registry,
        scope="policy",
        card="control_mode_card",
        agent_id=None,
        controls=control_mode_card.get("active_controls"),
    )
    runtime_card = payload.get("runtime_card") if isinstance(payload.get("runtime_card"), dict) else {}
    runtime_agents = runtime_card.get("agents") if isinstance(runtime_card.get("agents"), list) else []
    for agent in runtime_agents:
        if isinstance(agent, dict):
            _append_control_registry_items(
                registry,
                scope="runtime",
                card="runtime_card",
                agent_id=agent.get("agent_id"),
                controls=agent.get("controls"),
            )
    operator_card = payload.get("operator_card") if isinstance(payload.get("operator_card"), dict) else {}
    _append_control_registry_items(
        registry,
        scope="operator",
        card="operator_card",
        agent_id=None,
        controls=operator_card.get("controls"),
    )
    return registry


def _append_control_registry_items(
    registry: list[dict[str, object]],
    *,
    scope: str,
    card: str,
    agent_id: object,
    controls: object,
) -> None:
    if not isinstance(controls, list):
        return
    for control in controls:
        if not isinstance(control, dict):
            continue
        registry.append(
            {
                "scope": scope,
                "card": card,
                "kind": control.get("kind"),
                "label": control.get("label"),
                "command": control.get("command"),
                "safety": control.get("safety"),
                "enabled": control.get("enabled"),
                "blocker": control.get("blocker"),
                "agent_id": agent_id,
            }
        )


def workbench_example() -> dict[str, object]:
    project_view = project_view_example()
    leader_action = project_view["leader_actions"]["items"][0]
    recovery = project_view["recovery"]
    recommended_action = recovery["recommended_action"]
    payload = {
        "ok": True,
        "mode": "workbench",
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "project_view": project_view,
        "leader_actions": project_view["leader_actions"],
        "leader_card": {
            "agent_id": "leader",
            "provider": "fake",
            "model": "fake-plan",
            "approval_mode": "confirm",
            "api_backed": False,
            "chat_command": "agentdeck leader chat --message <text>",
            "continue_command": "agentdeck continue",
            "review_command_template": "agentdeck leader review --plan-id <plan_id>",
            "actions_command": "agentdeck leader actions",
            "status_command": "agentdeck status",
            "controls": [
                {
                    "kind": "chat",
                    "label": "Ask Leader",
                    "command": "agentdeck leader chat --message <text>",
                    "safety": "explicit_user",
                    "enabled": False,
                    "blocker": "requires message text",
                },
                {
                    "kind": "continue",
                    "label": "Continue",
                    "command": "agentdeck continue",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "review",
                    "label": "Review plan",
                    "command": "agentdeck leader review --plan-id <plan_id>",
                    "safety": "inspect",
                    "enabled": False,
                    "blocker": "requires plan_id",
                },
                {
                    "kind": "actions",
                    "label": "Leader actions",
                    "command": "agentdeck leader actions",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "status",
                    "label": "Project status",
                    "command": "agentdeck status",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
            ],
        },
        "provider_health": {
            "agent_id": "leader",
            "provider": "fake",
            "model": "fake-plan",
            "approval_mode": "confirm",
            "api_backed": False,
            "supported": True,
            "ready": True,
            "missing_env": [],
            "detail": "fake provider is local and ready",
            "doctor_command": "agentdeck doctor",
            "doctor_contract": "agentdeck contract doctor",
            "setup_commands": [],
        },
        "runtime_card": {
            "backend": "tmux",
            "count": 3,
            "by_status": {"configured": 2, "running": 1},
            "refresh_command": "agentdeck agent refresh",
            "agents": [
                {
                    "agent_id": "planner",
                    "role": "planner",
                    "provider": "codex",
                    "workspace_mode": "shared",
                    "status": "running",
                    "pane_id": "%42",
                    "session_name": "agentdeck",
                    "cwd": "/workspace/agentdeck-example",
                    "spawn_command": "agentdeck agent spawn --agent planner",
                    "stop_command": "agentdeck agent stop --agent planner",
                    "capture_command": "agentdeck agent capture --agent planner --lines 200",
                    "send_command_template": "agentdeck agent send --agent planner --text <text>",
                    "inbox_command": "agentdeck inbox --agent planner",
                    "controls": runtime_agent_controls("planner", True),
                },
                {
                    "agent_id": "coder",
                    "role": "coder",
                    "provider": "claude",
                    "workspace_mode": "worktree",
                    "status": "configured",
                    "pane_id": None,
                    "session_name": None,
                    "cwd": None,
                    "spawn_command": "agentdeck agent spawn --agent coder",
                    "stop_command": "agentdeck agent stop --agent coder",
                    "capture_command": "agentdeck agent capture --agent coder --lines 200",
                    "send_command_template": "agentdeck agent send --agent coder --text <text>",
                    "inbox_command": "agentdeck inbox --agent coder",
                    "controls": runtime_agent_controls("coder", False),
                },
                {
                    "agent_id": "reviewer",
                    "role": "reviewer",
                    "provider": "codex",
                    "workspace_mode": "shared",
                    "status": "configured",
                    "pane_id": None,
                    "session_name": None,
                    "cwd": None,
                    "spawn_command": "agentdeck agent spawn --agent reviewer",
                    "stop_command": "agentdeck agent stop --agent reviewer",
                    "capture_command": "agentdeck agent capture --agent reviewer --lines 200",
                    "send_command_template": "agentdeck agent send --agent reviewer --text <text>",
                    "inbox_command": "agentdeck inbox --agent reviewer",
                    "controls": runtime_agent_controls("reviewer", False),
                },
            ],
        },
        "role_card": {
            "count": 3,
            "assign_command_template": (
                "agentdeck agent assign-role --agent <agent_id> --role <role> --role-prompt <role_prompt>"
            ),
            "agents": [
                {
                    "agent_id": "planner",
                    "role": "planner",
                    "provider": "codex",
                    "workspace_mode": "shared",
                    "role_prompt": "Break down goals and prepare implementation steps.",
                    "assign_command": (
                        "agentdeck agent assign-role --agent planner --role planner "
                        "--role-prompt 'Break down goals and prepare implementation steps.'"
                    ),
                },
                {
                    "agent_id": "coder",
                    "role": "coder",
                    "provider": "claude",
                    "workspace_mode": "worktree",
                    "role_prompt": "Implement approved tasks and report verification evidence.",
                    "assign_command": (
                        "agentdeck agent assign-role --agent coder --role coder "
                        "--role-prompt 'Implement approved tasks and report verification evidence.'"
                    ),
                },
                {
                    "agent_id": "reviewer",
                    "role": "reviewer",
                    "provider": "codex",
                    "workspace_mode": "shared",
                    "role_prompt": "Review implementation risks, tests, and missing requirements.",
                    "assign_command": (
                        "agentdeck agent assign-role --agent reviewer --role reviewer "
                        "--role-prompt 'Review implementation risks, tests, and missing requirements.'"
                    ),
                },
            ],
        },
        "ledger_card": {
            "messages": project_view["messages"],
            "jobs": project_view["jobs"],
            "replies": project_view["replies"],
            "inbox": project_view["inbox"],
            "trace_commands": [
                "agentdeck trace --id msg_example",
                "agentdeck trace --id job_example",
                "agentdeck trace --id rep_example",
            ],
        },
        "lineage_card": {
            "mode": "lineage",
            "title": "Communication lineage",
            "message_count": project_view["messages"]["count"],
            "job_count": project_view["jobs"]["count"],
            "reply_count": project_view["replies"]["count"],
            "inbox_count": 1,
            "trace_command_template": "agentdeck trace --id <id>",
            "recent_paths": [
                {
                    "message_id": "msg_example",
                    "job_id": "job_example",
                    "reply_id": "rep_example",
                    "inbox_id": "inb_leader_example",
                    "from_actor": "leader",
                    "to_agent": "planner",
                    "from_agent": "planner",
                    "to_actor": "leader",
                    "task": "Build a GUI-ready recovery panel",
                    "status": "reply_pending_ack",
                    "trace_command": "agentdeck trace --id msg_example",
                }
            ],
        },
        "queue_card": {
            "active_queue_source": "leader_action",
            "next_command": recovery["next_command"],
            "leader_actions": {
                "count": project_view["leader_actions"]["count"],
                "pending": project_view["leader_actions"]["by_status"]["pending"],
                "recommended_action_id": project_view["leader_actions"]["recommended_action_id"],
                "command": "agentdeck leader actions",
            },
            "approvals": {
                "count": project_view["approvals"]["count"],
                "pending": project_view["approvals"]["pending"],
                "approved": project_view["approvals"]["approved"],
                "command": "agentdeck approval list",
            },
            "inbox": {
                "total": project_view["inbox"]["total"],
                "by_agent": project_view["inbox"]["by_agent"],
                "command_template": "agentdeck inbox --agent <agent_id>",
            },
            "refresh_command": "agentdeck workbench",
        },
        "operator_card": {
            "status": recovery["status"],
            "reason": recovery["reason"],
            "label": recommended_action["label"],
            "command": recommended_action["command"],
            "next_command": recovery["next_command"],
            "safety": recommended_action["safety"],
            "requires_explicit_user": recommended_action["requires_explicit_user"],
            "source": recommended_action["source"],
            "target_id": recommended_action["target_id"],
            "preview_command": "agentdeck leader action --action-id act_example",
            "controls": [
                {
                    "kind": "preview",
                    "label": "Preview",
                    "command": "agentdeck leader action --action-id act_example",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "apply",
                    "label": "Apply",
                    "command": leader_action["apply_command"],
                    "safety": "safe_apply",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "explicit",
                    "label": "Run explicit command",
                    "command": leader_action["explicit_command"],
                    "safety": "safe_apply",
                    "enabled": True,
                    "blocker": None,
                },
            ],
            "active_queue_source": "leader_action",
            "action_kind": "leader_action",
            "can_apply": leader_action["can_apply"],
            "apply_command": leader_action["apply_command"],
            "explicit_command": leader_action["explicit_command"],
            "blocker": leader_action["apply_blocker"],
        },
        "audit_card": {
            "latest_event": recovery["latest_event"],
            "recent_events": recovery["recent_events"],
            "event_count": len(recovery["recent_events"]),
            "events_command": "agentdeck events --limit 20",
        },
        "contracts_card": {
            "contracts_command": "agentdeck contract list",
            "contract_index_contract": "docs/contracts/contract-index-schema.md",
            "workbench_contract": "agentdeck contract workbench",
            "controls_contract": "agentdeck contract controls",
            "agent_runtime_contract": "agentdeck contract agent-runtime",
            "leader_chat_contract": "agentdeck contract leader-chat",
            "leader_review_contract": "agentdeck contract leader-review",
            "project_view_contract": "agentdeck contract project-view",
            "events_contract": "agentdeck contract events",
            "doctor_contract": "agentdeck contract doctor",
        },
        "control_mode_card": {
            "mode": "control_mode",
            "title": "Control mode",
            "current_mode": "ask",
            "approval_mode": "confirm",
            "default_safety": "inspect",
            "available_modes": [
                {
                    "mode": "ask",
                    "label": "Ask / inspect",
                    "description": "Plan, inspect, and suggest commands without mutating runtime state.",
                    "enabled": True,
                    "requires_explicit_user": False,
                    "safety": "inspect",
                    "blocker": None,
                },
                {
                    "mode": "approve",
                    "label": "Approval gated",
                    "description": "Allow safe apply after explicit human approval while runtime actions remain explicit.",
                    "enabled": True,
                    "requires_explicit_user": True,
                    "safety": "safe_apply",
                    "blocker": None,
                },
                {
                    "mode": "autonomous",
                    "label": "Autonomous bounded",
                    "description": "Reserved for future scoped delegation with budgets, allowlists, and audit gates.",
                    "enabled": False,
                    "requires_explicit_user": True,
                    "safety": "delegated",
                    "blocker": "autonomous execution policy is not implemented",
                },
            ],
            "active_controls": [
                {
                    "kind": "inspect",
                    "label": "Inspect policy",
                    "command": "agentdeck workbench",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "set_mode",
                    "label": "Ask / inspect",
                    "command": "agentdeck policy set-mode --mode ask",
                    "safety": "inspect",
                    "enabled": False,
                    "blocker": "already current mode",
                },
                {
                    "kind": "set_mode",
                    "label": "Approval gated",
                    "command": "agentdeck policy set-mode --mode approve",
                    "safety": "explicit_user",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "set_mode",
                    "label": "Autonomous bounded",
                    "command": "agentdeck policy set-mode --mode autonomous",
                    "safety": "delegated",
                    "enabled": False,
                    "blocker": "autonomous execution policy is not implemented",
                },
            ],
            "set_mode_command_template": "agentdeck policy set-mode --mode <mode>",
            "policy_source": ".agentdeck/config.toml:leader.approval_mode",
        },
        "recovery": recovery,
        "next_command": recovery["next_command"],
        "continue_card": continue_example(),
        "active_queue_source": "leader_action",
        "inbox_card": None,
        "leader_inbox_card": {
            "agent_id": "leader",
            "count": 1,
            "head_inbox_id": "inb_leader_example",
            "items": [
                {
                    "inbox_id": "inb_leader_example",
                    "event_type": "task_reply",
                    "message_id": "msg_example",
                    "attempt_id": "att_example",
                    "job_id": "job_example",
                    "reply_id": "rep_example",
                    "from_actor": None,
                    "from_agent": "planner",
                    "to_agent": "leader",
                    "task": "Summarize completed planner work.",
                    "status": "pending",
                    "created_at": "2026-07-05T00:00:05+00:00",
                    "preview_command": "agentdeck trace --id inb_leader_example",
                    "controls": [
                        {
                            "kind": "preview",
                            "label": "Trace inbox item",
                            "command": "agentdeck trace --id inb_leader_example",
                            "safety": "inspect",
                            "enabled": True,
                            "blocker": None,
                        },
                        {
                            "kind": "ack",
                            "label": "Acknowledge inbox head",
                            "command": "agentdeck ack --agent leader --inbox-id inb_leader_example",
                            "safety": "explicit_runtime",
                            "enabled": True,
                            "blocker": None,
                        },
                    ],
                    "trace_command": "agentdeck trace --id inb_leader_example",
                    "ack_command": "agentdeck ack --agent leader --inbox-id inb_leader_example",
                    "can_ack": True,
                    "ack_blocker": None,
                    "is_head": True,
                }
            ],
        },
        "approval_card": None,
        "leader_action": leader_action,
        "control_registry": [],
        "change_summary": {
            "since_event_id": None,
            "latest_event_id": recovery["latest_event"]["event_id"],
            "has_new_events": False,
            "new_event_count": 0,
            "new_events": [],
        },
    }
    payload["control_registry"] = workbench_control_registry(payload)
    return payload


def agent_runtime_example() -> dict[str, object]:
    agent_id = "planner"
    return {
        "agents": [
            {
                "agent_id": agent_id,
                "role": "planning",
                "provider": "codex",
                "workspace_mode": "shared",
                "runtime": {
                    "pane_id": "%42",
                    "session_name": "agentdeck",
                    "cwd": "/workspace/project",
                    "status": "running",
                },
            }
        ],
        "capture": {
            "agent_id": agent_id,
            "pane_id": "%42",
            "output": "status: completed\n",
        },
        "refresh": {
            "ok": True,
            "agents": [
                {
                    "agent_id": agent_id,
                    "previous_status": "running",
                    "status": "running",
                    "pane_id": "%42",
                    "pane_exists": True,
                    "changed": False,
                }
            ],
            "stale_count": 0,
            "running_count": 1,
        },
        "controls": runtime_agent_controls(agent_id, True),
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
        "preview_command": "agentdeck approval list",
        "controls": _approval_item_controls(approval_id, status == "approved"),
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
        "preview_command": f"agentdeck trace --id {inbox_id}",
        "controls": _inbox_item_controls("planner", inbox_id, is_head),
        "trace_command": f"agentdeck trace --id {inbox_id}",
        "ack_command": f"agentdeck ack --agent planner --inbox-id {inbox_id}",
        "is_head": is_head,
        "can_ack": is_head,
        "ack_blocker": None if is_head else "inbox item is not head",
    }
    return {field: item.get(field) for field in INBOX_ITEM_FIELDS}


def _approval_item_controls(approval_id: str, can_dispatch: bool) -> list[dict[str, object]]:
    dispatch_blocker = None if can_dispatch else "approval is not approved"
    return [
        {
            "kind": "preview",
            "label": "Preview approval queue",
            "command": "agentdeck approval list",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "approve",
            "label": "Approve",
            "command": f"agentdeck approval approve --approval-id {approval_id}",
            "safety": "explicit_runtime",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "reject",
            "label": "Reject",
            "command": f"agentdeck approval reject --approval-id {approval_id} --reason <reason>",
            "safety": "explicit_runtime",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "dispatch",
            "label": "Dispatch",
            "command": f"agentdeck approval dispatch --approval-id {approval_id}",
            "safety": "explicit_runtime",
            "enabled": can_dispatch,
            "blocker": dispatch_blocker,
        },
    ]


def _inbox_item_controls(agent_id: str, inbox_id: str, can_ack: bool) -> list[dict[str, object]]:
    ack_blocker = None if can_ack else "inbox item is not head"
    return [
        {
            "kind": "preview",
            "label": "Trace inbox item",
            "command": f"agentdeck trace --id {inbox_id}",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "ack",
            "label": "Acknowledge inbox head",
            "command": f"agentdeck ack --agent {agent_id} --inbox-id {inbox_id}",
            "safety": "explicit_runtime",
            "enabled": can_ack,
            "blocker": ack_blocker,
        },
    ]


def _leader_action_controls(action_id: str, apply_command: object, explicit_command: object, apply_blocker: object) -> list[dict[str, object]]:
    return [
        {
            "kind": "preview",
            "label": "Preview Leader action",
            "command": f"agentdeck leader action --action-id {action_id}",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "apply",
            "label": "Apply safe Leader action",
            "command": apply_command,
            "safety": "safe_apply",
            "enabled": apply_command is not None,
            "blocker": apply_blocker,
        },
        {
            "kind": "explicit",
            "label": "Run explicit command",
            "command": explicit_command,
            "safety": "explicit_runtime",
            "enabled": explicit_command is not None,
            "blocker": None,
        },
    ]


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


def leader_review_example() -> dict[str, object]:
    return {
        "plan_id": "pln_example",
        "next_action": "wait_for_reply",
        "reason": "dispatched step has no reply yet",
        "approval_id": None,
        "agent_id": "planner",
        "message_id": "msg_example",
        "replies": [],
        "next_command": "agentdeck capture-reply --agent planner --message-id msg_example",
        "controls": [
            {
                "kind": "preview",
                "label": "Preview message lineage",
                "command": "agentdeck trace --id msg_example",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "capture_reply",
                "label": "Capture reply",
                "command": "agentdeck capture-reply --agent planner --message-id msg_example",
                "safety": "explicit_runtime",
                "enabled": True,
                "blocker": None,
            },
        ],
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
