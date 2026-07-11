from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from pathlib import Path

from .mission import (
    MISSION_SCHEMA_VERSION,
    MISSION_SELECTED_AGENT_FIELDS as MISSION_STATE_SELECTED_AGENT_FIELDS,
    MISSION_SELECTED_AGENT_REQUIRED_FIELDS as MISSION_STATE_SELECTED_AGENT_REQUIRED_FIELDS,
    MISSION_STARTUP_ACTION_FIELDS as MISSION_STATE_STARTUP_ACTION_FIELDS,
    MISSION_STARTUP_ACTION_REQUIRED_FIELDS as MISSION_STATE_STARTUP_ACTION_REQUIRED_FIELDS,
    MISSION_STATUSES,
    MISSION_WORKER_NULLABLE_FIELDS,
    is_canonical_mission_id,
    mission_commands,
    workbench_mission_card,
    validate_mission_plan,
)
from .models import PROJECT_VIEW_SCHEMA_VERSION
from .state import leader_backend_identity


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
        "loop",
        "agentdeck contract loop",
        "agentdeck contract loop --example",
        "loop-schema.md",
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
        "run",
        "agentdeck contract run",
        "agentdeck contract run --example",
        "run-schema.md",
    ),
    (
        "run-loop",
        "agentdeck contract run-loop",
        "agentdeck contract run-loop --example",
        "run-loop-schema.md",
    ),
    (
        "run-loop-all",
        "agentdeck contract run-loop-all",
        "agentdeck contract run-loop-all --example",
        "run-loop-all-schema.md",
    ),
    (
        "workflow",
        "agentdeck contract workflow",
        "agentdeck contract workflow --example",
        "workflow-schema.md",
    ),
    (
        "mission",
        "agentdeck contract mission",
        "agentdeck contract mission --example",
        "mission-schema.md",
    ),
    (
        "demo",
        "agentdeck contract demo",
        "agentdeck contract demo --example",
        "demo-schema.md",
    ),
    (
        "plans",
        "agentdeck contract plans",
        "agentdeck contract plans --example",
        "plans-schema.md",
    ),
    (
        "release",
        "agentdeck contract release",
        "agentdeck contract release --example",
        "release-schema.md",
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
        "skills",
        "agentdeck contract skills",
        "agentdeck contract skills --example",
        "skills-schema.md",
    ),
    (
        "memory",
        "agentdeck contract memory",
        "agentdeck contract memory --example",
        "memory-schema.md",
    ),
    (
        "learning-review",
        "agentdeck contract learning-review",
        "agentdeck contract learning-review --example",
        "learning-review-schema.md",
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
        "leader-status",
        "agentdeck contract leader-status",
        "agentdeck contract leader-status --example",
        "leader-status-schema.md",
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
        "leader-summary",
        "agentdeck contract leader-summary",
        "agentdeck contract leader-summary --example",
        "leader-summary-schema.md",
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
    (
        "artifacts",
        "agentdeck contract artifacts",
        "agentdeck contract artifacts --example",
        "artifacts-schema.md",
    ),
)


DEMO_GOLDEN_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "demo_name",
    "summary",
    "current_status",
    "next_command",
    "recommended_task",
    "steps",
    "inspection_commands",
    "safety",
    "source_command",
)

DEMO_GOLDEN_STEP_FIELDS = (
    "step_id",
    "title",
    "status",
    "command",
    "enabled",
    "blocker",
    "safety",
    "description",
    "checks",
)

DEMO_GOLDEN_STEP_STATUSES = {"ready", "blocked", "waiting_for_input", "done", "inspect"}
DEMO_GOLDEN_STEP_SAFETIES = {"inspect", "explicit_user", "explicit_runtime"}


PROJECT_VIEW_TOP_LEVEL_FIELDS = (
    "schema_version",
    "project",
    "root",
    "runtime_backend",
    "leader",
    "agents",
    "state_path",
    "missions",
    "plans",
    "approvals",
    "messages",
    "jobs",
    "replies",
    "artifacts",
    "releases",
    "chat_turns",
    "leader_errors",
    "leader_actions",
    "skills",
    "memory",
    "agent_sessions",
    "protocol_turns",
    "transport_updates",
    "permission_requests",
    "inbox",
    "recovery",
)

PROJECT_VIEW_AGENT_SESSIONS_FIELDS = ("count", "by_state", "items")
PROJECT_VIEW_AGENT_SESSION_ITEM_FIELDS = (
    "session_id", "agent_id", "provider", "transport", "state", "capabilities",
    "native_session_present", "workspace", "created_at", "updated_at",
)
PROJECT_VIEW_PROTOCOL_TURNS_FIELDS = ("count", "by_state", "items")
PROJECT_VIEW_PROTOCOL_TURN_ITEM_FIELDS = (
    "turn_id", "session_id", "message_id", "state", "created_at", "updated_at",
)
PROJECT_VIEW_TRANSPORT_UPDATES_FIELDS = ("count", "by_kind", "items")
PROJECT_VIEW_TRANSPORT_UPDATE_ITEM_FIELDS = (
    "update_id", "session_id", "turn_id", "sequence", "kind", "created_at",
)
PROJECT_VIEW_PERMISSION_REQUESTS_FIELDS = ("count", "pending_count", "by_status", "items")
PROJECT_VIEW_PERMISSION_REQUEST_ITEM_FIELDS = (
    "permission_id", "session_id", "turn_id", "tool_name", "risk", "status", "decision", "created_at",
)

PROJECT_VIEW_LEADER_FIELDS = (
    "agent_id",
    "provider",
    "model",
    "approval_mode",
    "leader_backend",
    "coordination_roles",
)

PROJECT_VIEW_COORDINATION_ROLE_FIELDS = (
    "role_id",
    "label",
    "provider",
    "model",
    "lifecycle",
    "responsibility",
    "state_source",
    "runtime_kind",
    "pane_backed",
    "pane_id",
    "dispatch_ready",
    "approval_required",
    "next_command",
)

PROJECT_VIEW_PLAN_ITEM_FIELDS = (
    "plan_id",
    "task",
    "provider",
    "provider_backend",
    "provider_transport",
    "leader_backend",
    "model",
    "status",
    "dispatch_ready",
    "skill_context",
    "step_count",
    "created_at",
)

PROJECT_VIEW_MISSIONS_FIELDS = (
    "count",
    "by_status",
    "latest_id",
    "items",
)

PROJECT_VIEW_MISSION_ITEM_FIELDS = (
    "mission_id",
    "schema_version",
    "user_message",
    "status",
    "stop_reason",
    "can_start",
    "can_resume",
    "blockers",
    "provider",
    "model",
    "leader_backend",
    "plan_id",
    "plan_hash",
    "workflow_run_id",
    "current_step",
    "step_count",
    "timeout_seconds",
    "selected_agents",
    "startup_actions",
    "created_at",
    "updated_at",
    "confirmed_at",
    "completed_at",
    "status_command",
    "confirmation_command",
    "resume_command",
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
    "reply_waiting",
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

APPROVAL_DISPATCH_READY_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "requires_explicit_user",
    "safety",
    "dispatched_count",
    "blocked_count",
    "skipped_count",
    "results",
)

APPROVAL_DISPATCH_READY_RESULT_FIELDS = (
    "approval_id",
    "status",
    "agent_id",
    "pane_id",
    "message_id",
    "trace_command",
    "blocker",
    "dispatch_command",
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

PROJECT_VIEW_SKILLS_FIELDS = (
    "count",
    "by_agent",
    "by_source",
    "items",
)

PROJECT_VIEW_SKILL_ITEM_FIELDS = (
    "load_id",
    "agent_id",
    "purpose",
    "name",
    "source",
    "path",
    "content_hash",
    "description",
    "required_tools",
    "planning_guidance",
    "risk",
    "created_at",
    "show_command",
    "reload_command",
)

PROJECT_VIEW_MEMORY_FIELDS = (
    "count",
    "by_scope",
    "items",
)

PROJECT_VIEW_MEMORY_ITEM_FIELDS = (
    "scope",
    "path",
    "exists",
    "line_count",
    "byte_count",
    "content_hash",
    "preview",
)

LEADER_CHAT_SKILL_CONTEXT_CARD_FIELDS = (
    "mode",
    "title",
    "summary",
    "skills_command",
    "project_view_command",
    "count",
    "items",
    "controls",
)

LEADER_CHAT_MEMORY_CONTEXT_CARD_FIELDS = (
    "mode",
    "title",
    "summary",
    "project_view_command",
    "suggestions_command",
    "count",
    "items",
    "controls",
)

LEADER_CHAT_FRONTDESK_CARD_FIELDS = (
    "mode",
    "title",
    "summary",
    "user_message",
    "intake_summary",
    "classification",
    "next_command",
    "controls",
)

LEADER_CHAT_SKILL_SUGGESTIONS_CARD_FIELDS = (
    "mode",
    "title",
    "summary",
    "suggestions_command",
    "project_view_command",
    "count",
    "pending_count",
    "items",
    "controls",
)

LEADER_CHAT_MEMORY_SUGGESTIONS_CARD_FIELDS = (
    "mode",
    "title",
    "summary",
    "suggestions_command",
    "apply_preview_command_template",
    "project_view_command",
    "count",
    "pending_count",
    "items",
    "controls",
)

LEADER_CHAT_MEMORY_APPLY_PREVIEW_CARD_FIELDS = (
    "ok",
    "mode",
    "suggestion_id",
    "suggestion",
    "target",
    "target_exists",
    "would_create",
    "would_update_status",
    "proposed_append",
    "apply_command",
    "controls",
)

LEADER_CHAT_SKILL_IMPORT_PREVIEW_CARD_FIELDS = (
    "ok",
    "mode",
    "title",
    "summary",
    "skill",
    "source_path",
    "project_path",
    "would_overwrite",
    "import_command",
    "force_import_command",
    "controls",
)

LEADER_CHAT_SKILL_LOAD_PREVIEW_CARD_FIELDS = (
    "ok",
    "mode",
    "title",
    "summary",
    "agent_id",
    "purpose",
    "skill",
    "load_command",
    "controls",
)

LEADER_CHAT_SKILL_CREATE_PREVIEW_CARD_FIELDS = (
    "mode",
    "suggestion_id",
    "suggestion",
    "name",
    "target_path",
    "would_create",
    "would_overwrite",
    "source",
    "agent_id",
    "trace_id",
    "proposed_content",
    "proposed_content_hash",
    "draft_preview_command",
    "create_command",
    "controls",
)

SKILLS_LIST_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "skill_count",
    "import_command_template",
    "controls",
    "skills",
)

SKILLS_DETAIL_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "skill",
)

SKILLS_IMPORT_PREVIEW_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "skill",
    "source_path",
    "project_path",
    "would_overwrite",
    "source_allowlisted",
    "enforcement_active",
    "import_blocked",
    "import_command",
    "force_import_command",
    "controls",
)

SKILLS_IMPORT_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "skill",
    "source_path",
    "project_path",
    "overwritten",
    "show_command",
    "load_command",
)

SKILLS_LOAD_PREVIEW_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "agent_id",
    "purpose",
    "skill",
    "load_command",
    "unmet_dependencies",
    "has_dependency_cycle",
    "controls",
)

SKILLS_LOAD_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "load_id",
    "agent_id",
    "purpose",
    "created_at",
    "skill",
)

SKILLS_SUGGEST_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "suggestion",
    "next_command",
)

SKILLS_SUGGESTIONS_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "count",
    "pending_count",
    "items",
    "controls",
)

SKILLS_DRAFT_PREVIEW_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "suggestion_id",
    "suggestion",
    "name",
    "target_path",
    "would_create",
    "would_overwrite",
    "source",
    "agent_id",
    "trace_id",
    "proposed_content",
    "proposed_content_hash",
    "create_command",
    "controls",
)

SKILLS_CREATE_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "suggestion_id",
    "suggestion",
    "name",
    "path",
    "overwritten",
    "content_hash",
    "show_command",
    "load_command",
    "controls",
)

SKILLS_DEPS_RESPONSE_FIELDS = (
    "ok", "mode", "name", "depends_on", "resolved", "missing",
    "version_mismatch", "has_cycle", "cycle", "order", "controls",
)


def validate_skills_deps_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in SKILLS_DEPS_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing skills_deps field: {field}")
    if payload.get("mode") != "skills_deps":
        errors.append("skills_deps.mode must be skills_deps")
    if not isinstance(payload.get("has_cycle"), bool):
        errors.append("skills_deps.has_cycle must be a bool")
    for list_field in ("depends_on", "resolved", "missing", "version_mismatch", "cycle", "order", "controls"):
        if not isinstance(payload.get(list_field), list):
            errors.append(f"skills_deps.{list_field} must be a list")
    return {"ok": not errors, "errors": errors}


SKILL_LOAD_PLAN_RESPONSE_FIELDS = (
    "ok", "mode", "name", "agent", "order", "to_load", "already_loaded",
    "missing", "version_mismatch", "has_cycle", "cycle", "blockers", "can_load", "confirm_command", "controls",
)


def validate_skill_load_plan_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in SKILL_LOAD_PLAN_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing skill_load_plan field: {field}")
    if payload.get("mode") != "skill_load_plan":
        errors.append("skill_load_plan.mode must be skill_load_plan")
    if not isinstance(payload.get("can_load"), bool):
        errors.append("skill_load_plan.can_load must be a bool")
    if not isinstance(payload.get("has_cycle"), bool):
        errors.append("skill_load_plan.has_cycle must be a bool")
    for list_field in ("order", "to_load", "already_loaded", "missing", "version_mismatch", "cycle", "blockers", "controls"):
        if not isinstance(payload.get(list_field), list):
            errors.append(f"skill_load_plan.{list_field} must be a list")
    return {"ok": not errors, "errors": errors}


SKILL_LOCK_RESPONSE_FIELDS = ("ok", "mode", "name", "lock_path", "dependencies")


def validate_skill_lock_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in SKILL_LOCK_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing skill_lock field: {field}")
    if payload.get("mode") != "skill_locked":
        errors.append("skill_lock.mode must be skill_locked")
    if not isinstance(payload.get("dependencies"), list):
        errors.append("skill_lock.dependencies must be a list")
    return {"ok": not errors, "errors": errors}


SKILL_LOCK_VERIFY_RESPONSE_FIELDS = (
    "ok", "mode", "name", "locked", "in_sync", "changed", "added", "removed", "blockers",
)


def validate_skill_lock_verify_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in SKILL_LOCK_VERIFY_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing skill_lock_verify field: {field}")
    if payload.get("mode") != "skill_lock_verify":
        errors.append("skill_lock_verify.mode must be skill_lock_verify")
    for bool_field in ("locked", "in_sync"):
        if not isinstance(payload.get(bool_field), bool):
            errors.append(f"skill_lock_verify.{bool_field} must be a bool")
    for list_field in ("changed", "added", "removed", "blockers"):
        if not isinstance(payload.get(list_field), list):
            errors.append(f"skill_lock_verify.{list_field} must be a list")
    return {"ok": not errors, "errors": errors}


SKILLS_CATALOG_RESPONSE_FIELDS = (
    "ok", "mode", "source", "source_allowlisted", "skill_count", "imported_count", "controls", "items",
)

SKILLS_CATALOG_ITEM_FIELDS = (
    "name", "description", "source", "path", "content_hash", "required_tools", "risk",
    "show_command", "load_command", "controls",
    "import_status", "import_preview_command", "import_command",
)

SKILLS_SOURCES_RESPONSE_FIELDS = (
    "ok", "mode", "source_count", "sources", "controls",
)

SKILLS_SUGGESTION_ITEM_FIELDS = (
    "suggestion_id",
    "status",
    "name",
    "summary",
    "rationale",
    "source",
    "agent_id",
    "trace_id",
    "draft_path",
    "created_at",
    "draft_preview_command",
    "controls",
)

SKILLS_SKILL_ITEM_FIELDS = (
    "name",
    "description",
    "source",
    "path",
    "content_hash",
    "required_tools",
    "planning_guidance",
    "risk",
    "version",
    "show_command",
    "load_command",
    "controls",
)

SKILLS_DETAIL_SKILL_FIELDS = SKILLS_SKILL_ITEM_FIELDS + ("content",)
SKILLS_LOAD_SKILL_FIELDS = SKILLS_SKILL_ITEM_FIELDS + ("content_snapshot",)

SKILLS_CONTROL_FIELDS = (
    "kind",
    "label",
    "command",
    "safety",
    "enabled",
    "blocker",
)

MEMORY_SUGGEST_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "suggestion",
    "next_command",
)

MEMORY_SUGGESTIONS_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "count",
    "pending_count",
    "apply_preview_command_template",
    "items",
    "controls",
)

MEMORY_APPLY_PREVIEW_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "suggestion_id",
    "suggestion",
    "target",
    "target_exists",
    "would_create",
    "would_update_status",
    "proposed_append",
    "apply_command",
    "controls",
)

MEMORY_APPLY_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "suggestion_id",
    "suggestion",
    "target",
    "applied_path",
    "appended",
)

MEMORY_SUGGESTION_ITEM_FIELDS = (
    "suggestion_id",
    "status",
    "scope",
    "summary",
    "rationale",
    "source",
    "agent_id",
    "trace_id",
    "target",
    "created_at",
    "controls",
)

MEMORY_CONTROL_FIELDS = SKILLS_CONTROL_FIELDS

LEARNING_REVIEW_RESPONSE_FIELDS = (
    "schema_version",
    "ok",
    "mode",
    "plan_id",
    "task",
    "status",
    "reply_count",
    "artifact_count",
    "summary",
    "plan_status_command",
    "summary_command",
    "skill_suggestion",
    "memory_suggestion",
    "controls",
)

LEARNING_REVIEW_SKILL_SUGGESTION_FIELDS = (
    "kind",
    "name",
    "summary",
    "rationale",
    "source",
    "agent_id",
    "trace_id",
    "command",
)

LEARNING_REVIEW_MEMORY_SUGGESTION_FIELDS = (
    "kind",
    "scope",
    "summary",
    "rationale",
    "source",
    "agent_id",
    "trace_id",
    "command",
)

LEARNING_REVIEW_CONTROL_FIELDS = SKILLS_CONTROL_FIELDS

PROJECT_VIEW_MESSAGE_ITEM_FIELDS = (
    "message_id",
    "from_actor",
    "to_agent",
    "task",
    "status",
    "created_at",
    "trace_command",
    "prompt_skill_context",
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

PROJECT_VIEW_ARTIFACT_ITEM_FIELDS = (
    "artifact_id",
    "message_id",
    "job_id",
    "reply_id",
    "from_agent",
    "path",
    "kind",
    "status",
    "created_at",
    "trace_command",
)

PROJECT_VIEW_RELEASE_ITEM_FIELDS = (
    "release_id",
    "round",
    "status",
    "review_gate_status",
    "artifact_count",
    "review_reply_count",
    "code_reviewer_id",
    "round_reviewer_id",
    "code_review_reply_id",
    "round_review_reply_id",
    "created_at",
    "trace_command",
)

ARTIFACTS_RESPONSE_FIELDS = (
    "schema_version",
    "artifacts_command",
    "project_view_contract",
    "trace_contract",
    "trace_command_template",
    "artifacts",
    "controls",
)

ARTIFACTS_CONTROL_FIELDS = (
    "kind",
    "label",
    "command",
    "safety",
    "enabled",
    "blocker",
)

ARTIFACTS_SUMMARY_FIELDS = (
    "count",
    "by_status",
    "by_kind",
    "items",
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
    "codex_cli",
    "claude_cli",
)

DOCTOR_CONFIGURED_LEADER_FIELDS = (
    "agent_id",
    "provider",
    "model",
    "approval_mode",
    "provider_backend",
    "provider_transport",
    "leader_backend",
    "ready",
    "supported",
    "missing_env",
    "detail",
    "command_path",
    "setup_commands",
)

DOCTOR_PROVIDER_CHECK_FIELDS = (
    "ok",
    "detail",
    "provider_backend",
    "provider_transport",
    "command_path",
    "setup_commands",
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
    "learning_review_card",
    "leader_summary_card",
    "leader_status_card",
    "frontdesk_card",
    "skill_context_card",
    "memory_context_card",
    "skill_import_preview_card",
    "skill_load_preview_card",
    "skill_create_preview_card",
    "skill_suggestions_card",
    "memory_apply_preview_card",
    "memory_suggestions_card",
    "continue_card",
    "run_start_card",
    "run_progress_card",
    "plan_board_card",
    "skills_catalog_card",
    "run_loop_preview_card",
    "mission_preview_card",
    "mission_status_card",
    "mission_run_card",
    "capture_card",
    "terminal_card",
    "dispatch_preview_card",
    "dispatch_batch_preview_card",
    "runtime_action_card",
    "startup_preview_card",
    "provider_setup_card",
    "provider_switch_card",
    "agent_ready_card",
    "inbox_card",
    "trace_card",
    "approval_card",
    "runtime_card",
    "terminal_session_card",
    "queue_card",
    "operator_card",
    "role_card",
    "review_gate_card",
    "release_preview_card",
    "role_topology_card",
    "ledger_card",
    "lineage_card",
    "audit_card",
    "artifacts_card",
    "workbench_card",
    "control_mode_card",
    "provider_health",
    "capability_card",
    "control_registry_card",
)

LEADER_CHAT_PROVIDER_SWITCH_CARD_FIELDS = (
    "mode",
    "title",
    "current_provider",
    "current_model",
    "target_provider",
    "target_model",
    "target_leader_backend",
    "target_readiness",
    "require_ready",
    "command",
    "diagnostics_command",
    "safety",
    "requires_explicit_user",
    "mutates_config",
    "controls",
)

LEADER_CHAT_PROVIDER_SETUP_CARD_FIELDS = (
    "mode",
    "title",
    "target_provider",
    "target_model",
    "setup_commands",
    "recommended_command",
    "recommended_control_id",
    "followup_switch_command",
    "require_ready",
    "safety",
    "requires_explicit_user",
    "mutates_config",
    "controls",
)

LEADER_CHAT_RUNTIME_ACTION_CARD_FIELDS = (
    "mode",
    "title",
    "action",
    "agent_id",
    "role",
    "runtime_status",
    "pane_id",
    "command",
    "preview_text",
    "requires_explicit_user",
    "safety",
    "blocker",
    "controls",
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
    "controls",
)

LEADER_CHAT_RUN_LOOP_PREVIEW_CARD_FIELDS = (
    "mode",
    "plan_id",
    "command",
    "autonomous_enabled",
    "safety",
    "requires_explicit_user",
    "blocker",
    "enable_command",
    "controls",
)

LEADER_CHAT_TERMINAL_CARD_FIELDS = (
    "ok",
    "mode",
    "agent_id",
    "role",
    "provider",
    "workspace_mode",
    "status",
    "pane_id",
    "session_name",
    "cwd",
    "attach_command",
    "select_pane_command",
    "capture_command",
    "send_command_template",
    "stop_command",
    "inbox_command",
    "refresh_command",
    "controls",
)

LEADER_CHAT_DISPATCH_PREVIEW_CARD_FIELDS = (
    "approval_id",
    "agent_id",
    "agent_role",
    "pane_id",
    "runtime_status",
    "task",
    "dispatch_command",
    "approval_command",
    "inbox_command",
    "requires_explicit_user",
    "safety",
    "blocker",
    "controls",
)

LEADER_CHAT_DISPATCH_BATCH_PREVIEW_CARD_FIELDS = (
    "mode",
    "approval_command",
    "dispatch_ready_command",
    "count",
    "ready_count",
    "blocked_count",
    "items",
    "requires_explicit_user",
    "safety",
    "blocker",
    "controls",
)

LEADER_CHAT_STARTUP_PREVIEW_CARD_FIELDS = (
    "mode",
    "title",
    "next_command",
    "spawn_ready_command",
    "count",
    "ready_count",
    "blocked_count",
    "requires_explicit_user",
    "safety",
    "blocker",
    "items",
    "controls",
)

LEADER_CHAT_STARTUP_PREVIEW_ITEM_FIELDS = (
    "agent_id",
    "role",
    "runtime_status",
    "pane_id",
    "spawn_command",
    "terminal_command",
    "blocker",
    "controls",
)

CONTROL_REGISTRY_CARD_FIELDS = (
    "mode",
    "title",
    "source_command",
    "default_command",
    "filters",
    "selection",
    "item_count",
    "items",
    "group_count",
    "groups",
)

LEADER_CHAT_CONTROL_REGISTRY_CARD_FIELDS = CONTROL_REGISTRY_CARD_FIELDS

CONTROL_REGISTRY_GROUP_FIELDS = (
    "group_id",
    "scope",
    "card",
    "label",
    "item_count",
    "enabled_count",
    "disabled_count",
    "items",
)

CONTROL_REGISTRY_FILTER_FIELDS = (
    "scope",
    "card",
    "query",
    "control_id",
    "enabled_only",
    "active_filter_keys",
    "item_count_before_filter",
)

CONTROL_REGISTRY_SELECTION_FIELDS = (
    "requested_control_id",
    "matched",
    "matched_count",
    "selected_control",
    "blocker",
    "next_command",
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

RELEASE_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "requires_explicit_user",
    "safety",
    "release",
    "release_count",
    "next_command",
    "next_round_command",
    "trace_commands",
    "controls",
)

RELEASE_RECORD_FIELDS = (
    "release_id",
    "round",
    "status",
    "review_gate_status",
    "artifact_count",
    "review_reply_count",
    "code_reviewer_id",
    "round_reviewer_id",
    "code_review_reply_id",
    "round_review_reply_id",
    "created_at",
)

LOOP_ONCE_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "loop_id",
    "iteration",
    "max_iterations",
    "source_command",
    "project_view_command",
    "continue_command",
    "workbench_command",
    "status",
    "reason",
    "recovery",
    "continue_card",
    "recommended_action",
    "next_command",
    "stop_reason",
    "will_execute",
    "requires_explicit_user",
    "safety",
    "controls",
)

WORKBENCH_SNAPSHOT_FIELDS = (
    "ok",
    "mode",
    "schema_version",
    "project_view",
    "leader_actions",
    "leader_card",
    "mission_card",
    "provider_health",
    "runtime_card",
    "agent_ready_card",
    "terminal_session_card",
    "role_card",
    "worker_lifecycle_card",
    "review_gate_card",
    "release_preview_card",
    "role_topology_card",
    "ledger_card",
    "lineage_card",
    "queue_card",
    "operator_card",
    "audit_card",
    "artifacts_card",
    "skill_context_card",
    "skill_suggestions_card",
    "memory_context_card",
    "memory_suggestions_card",
    "leader_summary_card",
    "learning_review_card",
    "contracts_card",
    "control_mode_card",
    "recovery",
    "next_command",
    "continue_card",
    "active_queue_source",
    "run_progress_card",
    "plan_board_card",
    "skills_catalog_card",
    "inbox_card",
    "leader_inbox_card",
    "approval_card",
    "leader_action",
    "control_registry",
    "change_summary",
)

WORKBENCH_SKILLS_CATALOG_CARD_FIELDS = (
    "mode",
    "source_count",
    "total_skill_count",
    "imported_count",
    "sources",
)

WORKBENCH_SKILLS_CATALOG_SOURCE_FIELDS = (
    "path",
    "exists",
    "skill_count",
    "imported_count",
    "catalog_command",
)

WORKBENCH_LEADER_CARD_FIELDS = (
    "agent_id",
    "provider",
    "model",
    "approval_mode",
    "api_backed",
    "leader_backend",
    "coordination_roles",
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
    "autonomous_actions",
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
    "provider_backend",
    "provider_transport",
    "leader_backend",
    "supported",
    "ready",
    "missing_env",
    "detail",
    "command_path",
    "doctor_command",
    "doctor_contract",
    "setup_commands",
    "controls",
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
    "terminal_command",
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
    "control_id",
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

AGENT_RUNTIME_TERMINAL_RESPONSE_FIELDS = LEADER_CHAT_TERMINAL_CARD_FIELDS

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

AGENT_RUNTIME_SPAWN_READY_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "requires_explicit_user",
    "safety",
    "spawned_count",
    "skipped_count",
    "results",
    "ready_command",
)

AGENT_RUNTIME_SPAWN_READY_RESULT_FIELDS = (
    "agent_id",
    "status",
    "previous_status",
    "pane_id",
    "spawn_command",
    "blocker",
)

AGENT_RUNTIME_READY_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "runtime_backend",
    "total_count",
    "running_count",
    "not_running_count",
    "all_running",
    "next_command",
    "spawn_commands",
    "spawn_ready_command",
    "refresh_command",
    "dispatch_ready_command",
    "controls",
    "runtime_card",
)

WORKBENCH_TERMINAL_SESSION_CARD_FIELDS = (
    "mode",
    "runtime_backend",
    "session_name",
    "attach_command",
    "running_count",
    "agent_count",
    "open_terminals_command",
    "refresh_command",
    "controls",
    "terminals",
)

WORKBENCH_TERMINAL_SESSION_CONTROL_FIELDS = (
    "kind",
    "label",
    "command",
    "safety",
    "enabled",
    "blocker",
)

WORKBENCH_TERMINAL_SESSION_ITEM_FIELDS = (
    "agent_id",
    "role",
    "status",
    "pane_id",
    "terminal_command",
    "select_pane_command",
    "enabled",
    "blocker",
    "controls",
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
    "controls",
)

WORKBENCH_WORKER_LIFECYCLE_CARD_FIELDS = (
    "mode",
    "title",
    "source_command",
    "count",
    "by_stage",
    "items",
    "controls",
)

WORKBENCH_WORKER_LIFECYCLE_ITEM_FIELDS = (
    "agent_id",
    "role",
    "provider",
    "runtime_status",
    "pane_id",
    "lifecycle_stage",
    "active_message_id",
    "active_job_id",
    "latest_reply_id",
    "artifact_count",
    "pending_inbox_count",
    "trace_command",
    "inbox_command",
    "terminal_command",
    "capture_command",
    "controls",
)

WORKBENCH_REVIEW_GATE_CARD_FIELDS = (
    "mode",
    "title",
    "source_command",
    "status",
    "reason",
    "can_release",
    "artifact_count",
    "review_reply_count",
    "code_review",
    "round_review",
    "controls",
)

WORKBENCH_REVIEW_GATE_STAGE_FIELDS = (
    "stage",
    "agent_id",
    "role",
    "status",
    "latest_reply_id",
    "trace_command",
    "inbox_command",
    "blocker",
    "controls",
)

WORKBENCH_RELEASE_PREVIEW_CARD_FIELDS = (
    "mode",
    "title",
    "source_command",
    "status",
    "reason",
    "review_gate_status",
    "can_release",
    "already_released",
    "release_count",
    "latest_release_id",
    "next_command",
    "release_command",
    "next_round_command",
    "controls",
)

WORKBENCH_ROLE_TOPOLOGY_CARD_FIELDS = (
    "mode",
    "title",
    "source_command",
    "count",
    "logical_role_count",
    "worker_role_count",
    "by_status",
    "blocked_count",
    "roles",
    "controls",
)

WORKBENCH_ROLE_TOPOLOGY_ITEM_FIELDS = (
    "role_id",
    "label",
    "agent_id",
    "kind",
    "provider",
    "lifecycle",
    "runtime_kind",
    "pane_backed",
    "pane_id",
    "status",
    "blocker",
    "next_command",
    "controls",
)

WORKBENCH_LEDGER_CARD_FIELDS = (
    "messages",
    "jobs",
    "replies",
    "artifacts",
    "inbox",
    "trace_commands",
    "controls",
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
    "controls",
)

WORKBENCH_AUDIT_EVENT_FIELDS = (
    "event_id",
    "event_type",
    "created_at",
)

WORKBENCH_CONTRACTS_CARD_FIELDS = (
    "contracts_command",
    "contract_index_contract",
    "workbench_contract",
    "controls_contract",
    "skills_contract",
    "memory_contract",
    "learning_review_contract",
    "agent_runtime_contract",
    "leader_chat_contract",
    "leader_review_contract",
    "leader_summary_contract",
    "project_view_contract",
    "events_contract",
    "doctor_contract",
    "run_contract",
    "artifacts_contract",
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
    "leader_backend",
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

LEADER_SUMMARY_RESPONSE_FIELDS = (
    "schema_version",
    "plan_id",
    "task",
    "status",
    "provider",
    "model",
    "leader_backend",
    "counts",
    "reply_count",
    "artifact_count",
    "summary",
    "plan_status_command",
    "review_command",
    "steps",
    "controls",
)

LEADER_STATUS_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "schema_version",
    "source_command",
    "refresh_command",
    "project_view_command",
    "workbench_command",
    "leader",
    "provider_health",
    "coordination_roles",
    "latest_plan",
    "queues",
    "recovery",
    "next_command",
    "controls",
)

LEADER_STATUS_QUEUE_FIELDS = (
    "leader_actions_pending",
    "approvals_pending",
    "approvals_approved",
    "leader_inbox_pending",
    "leader_errors",
)

LEADER_SUMMARY_STEP_FIELDS = (
    "step",
    "agent_id",
    "role",
    "task",
    "approval_id",
    "message_id",
    "attempt_id",
    "job_id",
    "reply_id",
    "reply_text",
    "artifact_count",
    "artifacts",
    "trace_command",
)

LEADER_SUMMARY_ARTIFACT_FIELDS = (
    "artifact_id",
    "path",
    "kind",
    "status",
    "trace_command",
)

LEADER_SUMMARY_CONTROL_FIELDS = LEADER_REVIEW_CONTROL_FIELDS

RUN_LOOP_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "plan_id",
    "requires_explicit_user",
    "safety",
    "auto_approved",
    "dispatched",
    "blocked",
    "skipped",
    "stopped_reason",
    "next_command",
    "policy",
)

PLAN_BOARD_RESPONSE_FIELDS = (
    "ok", "mode", "board_command", "plan_count", "active_count", "plans",
)

PLAN_BOARD_ITEM_FIELDS = (
    "plan_id", "task", "provider_backend", "created_at", "status",
    "gate", "next_command", "active", "counts",
)

PLAN_BOARD_GATES = (
    "blocked", "needs_human_approval", "waiting_for_reply", "complete", "idle",
)

# The natural-language plan_board chat card reuses the plan-board response shape.
LEADER_CHAT_PLAN_BOARD_CARD_FIELDS = PLAN_BOARD_RESPONSE_FIELDS

# The natural-language skills_catalog chat card reuses the workbench skills-catalog card shape.
LEADER_CHAT_SKILLS_CATALOG_CARD_FIELDS = WORKBENCH_SKILLS_CATALOG_CARD_FIELDS

RUN_LOOP_STOP_REASONS = (
    "error",
    "blocked",
    "needs_human_approval",
    "waiting_for_reply",
    "complete",
    "idle",
)

RUN_LOOP_ALL_RESPONSE_FIELDS = (
    "ok", "mode", "requires_explicit_user", "safety",
    "plan_count", "active_count", "budget", "totals", "plans",
)

RUN_LOOP_ALL_PLAN_FIELDS = (
    "plan_id", "task", "auto_approved", "dispatched", "blocked",
    "skipped", "skipped_contention", "gate", "next_command",
)

WORKFLOW_PREVIEW_RESPONSE_FIELDS = (
    "schema_version",
    "ok",
    "mode",
    "safety",
    "plan_id",
    "plan_hash",
    "timeout_seconds",
    "step_count",
    "steps",
    "blockers",
    "can_run",
    "confirm_command",
    "controls",
)

WORKFLOW_STEP_FIELDS = (
    "step",
    "agent_id",
    "role",
    "task",
    "task_hash",
    "runtime_status",
    "pane_id",
    "ready",
    "blocker",
)

WORKFLOW_STATUS_RESPONSE_FIELDS = (
    "schema_version",
    "ok",
    "mode",
    "safety",
    "run_id",
    "plan_id",
    "plan_hash",
    "status",
    "current_step",
    "step_count",
    "timeout_seconds",
    "turns",
    "stop_reason",
    "created_at",
    "updated_at",
    "completed_at",
    "can_resume",
    "status_command",
    "resume_command",
    "controls",
)

WORKFLOW_RUN_RESPONSE_FIELDS = (
    *WORKFLOW_STATUS_RESPONSE_FIELDS,
    "requires_explicit_user",
    "confirmed",
)

WORKFLOW_TURN_FIELDS = (
    "step",
    "agent_id",
    "handoff_token",
    "status",
    "message_id",
    "job_id",
    "reply_id",
    "handoff",
    "artifact_paths",
    "trace_command",
    "started_at",
    "completed_at",
)

MISSION_PREVIEW_RESPONSE_FIELDS = (
    "schema_version", "ok", "mode", "mission_id", "status", "user_message",
    "provider", "model", "leader_backend", "plan_id", "plan_hash", "plan",
    "selected_agents", "startup_actions", "step_count", "timeout_seconds",
    "can_start", "blockers", "confirmation_command", "status_command",
    "workbench_command", "controls", "safety", "requires_explicit_user",
)
MISSION_STATUS_RESPONSE_FIELDS = (
    "schema_version", "ok", "mode", "mission_id", "status", "user_message",
    "plan_id", "plan_hash", "workflow_run_id", "current_step", "step_count",
    "timeout_seconds", "selected_agents", "blockers", "stop_reason",
    "created_at", "updated_at", "confirmed_at", "completed_at", "can_resume",
    "status_command", "resume_command", "attach_command", "workbench_command",
    "controls", "safety", "requires_explicit_user",
)
MISSION_RUN_RESPONSE_FIELDS = (*MISSION_STATUS_RESPONSE_FIELDS, "confirmed", "turns")
MISSION_RUN_TURN_FIELDS = ("step", "agent_id", "status", "handoff")
MISSION_RUN_HANDOFF_FIELDS = (
    "step", "agent_id", "status", "summary", "verification", "risks",
    "next_steps", "artifact_paths", "trace_command",
)
WORKBENCH_MISSION_CARD_FIELDS = (*MISSION_STATUS_RESPONSE_FIELDS, "confirmation_command")
MISSION_SELECTED_AGENT_FIELDS = (
    "agent_id", "provider", "role", "workspace_mode", "runtime_status",
    "effective_model", "model_source",
)
MISSION_STARTUP_ACTION_FIELDS = (
    "agent_id", "action", "runtime_status", "effective_model", "model_source",
)
MISSION_PLAN_FIELDS = ("goal", "summary", "steps")
MISSION_PLAN_STEP_FIELDS = ("step", "agent_id", "role", "task")
MISSION_CONTROL_FIELDS = (
    "kind", "label", "command", "safety", "enabled", "blocker",
)

WORKFLOW_STATUSES = ("running", "completed", "stopped", "interrupted")
WORKFLOW_TURN_STATUSES = (
    "pending",
    "dispatched",
    "completed",
    "blocked",
    "failed",
    "timed_out",
)
WORKFLOW_STOP_REASONS = (
    "agent_unavailable",
    "pane_lost",
    "timed_out",
    "invalid_reply",
    "worker_blocked",
    "worker_failed",
    "plan_drift",
    "contract_failed",
    "interrupted",
)

RUN_START_RESPONSE_FIELDS = (
    "schema_version",
    "ok",
    "mode",
    "task",
    "plan_id",
    "provider",
    "provider_backend",
    "provider_transport",
    "leader_backend",
    "model",
    "approval_count",
    "pending_approval_count",
    "plan",
    "approval_card",
    "next_command",
    "approve_next_command",
    "review_command",
    "continue_command",
    "workbench_command",
    "controls",
    "safety",
    "requires_explicit_user",
)

RUN_PROGRESS_RESPONSE_FIELDS = (
    "schema_version",
    "ok",
    "mode",
    "plan_id",
    "task",
    "status",
    "provider",
    "provider_backend",
    "provider_transport",
    "leader_backend",
    "model",
    "counts",
    "steps",
    "review",
    "approval_card",
    "next_command",
    "plan_status_command",
    "review_command",
    "continue_command",
    "workbench_command",
    "controls",
    "safety",
    "requires_explicit_user",
)

LEADER_BACKEND_FIELDS = (
    "agent_id",
    "provider",
    "model",
    "provider_backend",
    "provider_transport",
    "reasoning_backend",
    "runtime_kind",
    "pane_backed",
    "pane_id",
    "approval_required",
    "dispatch_ready",
)

RUN_START_CONTROL_FIELDS = LEADER_REVIEW_CONTROL_FIELDS

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
    "secondary_embedded_cards",
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
    {"placeholder": "<provider>", "blocker": "requires leader provider"},
    {"placeholder": "<model>", "blocker": "requires leader model"},
    {"placeholder": "<SKILL.md>", "blocker": "requires SKILL.md path"},
    {"placeholder": "<name>", "blocker": "requires skill name"},
    {"placeholder": "<purpose>", "blocker": "requires purpose"},
    {"placeholder": "<suggestion_id>", "blocker": "requires suggestion_id"},
)

LEADER_CHAT_INTENT_PLACEHOLDERS = (
    {"placeholder": "<reason>", "blocker": "requires reason"},
)

TRACE_TOP_LEVEL_FIELDS = (
    "schema_version",
    "query_id",
    "message",
    "plan",
    "attempts",
    "jobs",
    "replies",
    "artifacts",
    "inbox_items",
    "controls",
)

TRACE_MESSAGE_FIELDS = (
    "message_id",
    "from_actor",
    "to_agent",
    "task",
    "prompt",
    "prompt_skill_context",
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

TRACE_ARTIFACT_FIELDS = (
    "artifact_id",
    "message_id",
    "attempt_id",
    "job_id",
    "reply_id",
    "from_agent",
    "path",
    "kind",
    "status",
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
        "leader_fields": list(PROJECT_VIEW_LEADER_FIELDS),
        "coordination_role_fields": list(PROJECT_VIEW_COORDINATION_ROLE_FIELDS),
        "missions_fields": list(PROJECT_VIEW_MISSIONS_FIELDS),
        "mission_item_fields": list(PROJECT_VIEW_MISSION_ITEM_FIELDS),
        "plan_item_fields": list(PROJECT_VIEW_PLAN_ITEM_FIELDS),
        "recovery_fields": list(PROJECT_VIEW_RECOVERY_FIELDS),
        "recovery_pending_fields": list(PROJECT_VIEW_RECOVERY_PENDING_FIELDS),
        "recommended_action_fields": list(PROJECT_VIEW_RECOMMENDED_ACTION_FIELDS),
        "leader_actions_fields": list(PROJECT_VIEW_LEADER_ACTIONS_FIELDS),
        "leader_action_item_fields": list(PROJECT_VIEW_LEADER_ACTION_ITEM_FIELDS),
        "skill_summary_fields": list(PROJECT_VIEW_SKILLS_FIELDS),
        "skill_item_fields": list(PROJECT_VIEW_SKILL_ITEM_FIELDS),
        "memory_summary_fields": list(PROJECT_VIEW_MEMORY_FIELDS),
        "memory_item_fields": list(PROJECT_VIEW_MEMORY_ITEM_FIELDS),
        "message_item_fields": list(PROJECT_VIEW_MESSAGE_ITEM_FIELDS),
        "job_item_fields": list(PROJECT_VIEW_JOB_ITEM_FIELDS),
        "reply_item_fields": list(PROJECT_VIEW_REPLY_ITEM_FIELDS),
        "artifact_item_fields": list(PROJECT_VIEW_ARTIFACT_ITEM_FIELDS),
        "release_item_fields": list(PROJECT_VIEW_RELEASE_ITEM_FIELDS),
        "agent_sessions_fields": list(PROJECT_VIEW_AGENT_SESSIONS_FIELDS),
        "agent_session_item_fields": list(PROJECT_VIEW_AGENT_SESSION_ITEM_FIELDS),
        "protocol_turns_fields": list(PROJECT_VIEW_PROTOCOL_TURNS_FIELDS),
        "protocol_turn_item_fields": list(PROJECT_VIEW_PROTOCOL_TURN_ITEM_FIELDS),
        "transport_updates_fields": list(PROJECT_VIEW_TRANSPORT_UPDATES_FIELDS),
        "transport_update_item_fields": list(PROJECT_VIEW_TRANSPORT_UPDATE_ITEM_FIELDS),
        "permission_requests_fields": list(PROJECT_VIEW_PERMISSION_REQUESTS_FIELDS),
        "permission_request_item_fields": list(PROJECT_VIEW_PERMISSION_REQUEST_ITEM_FIELDS),
    }


def project_view_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = project_view_contract_payload(contract_path)
    if include_example:
        example = project_view_example()
        payload["example"] = True
        payload["example_top_level_fields"] = list(example)
        payload["example_leader_fields"] = list(example["leader"])
        payload["example_coordination_role_fields"] = list(example["leader"]["coordination_roles"][0])
        payload["example_missions_fields"] = list(example["missions"])
        payload["example_mission_item_fields"] = list(example["missions"]["items"][0])
        payload["example_plan_item_fields"] = list(example["plans"]["items"][0])
        payload["example_recovery_fields"] = list(example["recovery"])
        payload["example_recovery_pending_fields"] = list(example["recovery"]["pending"])
        payload["example_recommended_action_fields"] = list(example["recovery"]["recommended_action"])
        payload["example_leader_actions_fields"] = list(example["leader_actions"])
        payload["example_leader_action_item_fields"] = list(example["leader_actions"]["items"][0])
        payload["example_skill_summary_fields"] = list(example["skills"])
        payload["example_skill_item_fields"] = list(example["skills"]["items"][0])
        payload["example_memory_summary_fields"] = list(example["memory"])
        payload["example_memory_item_fields"] = list(example["memory"]["items"][0])
        payload["example_message_item_fields"] = list(example["messages"]["items"][0])
        payload["example_job_item_fields"] = list(example["jobs"]["items"][0])
        payload["example_reply_item_fields"] = list(example["replies"]["items"][0])
        payload["example_artifact_item_fields"] = list(example["artifacts"]["items"][0])
        payload["example_release_item_fields"] = list(example["releases"]["items"][0])
        for name in ("agent_sessions", "protocol_turns", "transport_updates", "permission_requests"):
            payload[f"example_{name}_fields"] = list(example[name])
            payload[f"example_{name[:-1]}_item_fields"] = list(example[name]["items"][0])
        payload["example_project_view"] = example
    return payload


def skills_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "skills_list_command": "agentdeck skills list",
        "catalog_command": "agentdeck skills catalog --source <dir>",
        "catalog_response_fields": list(SKILLS_CATALOG_RESPONSE_FIELDS),
        "catalog_item_fields": list(SKILLS_CATALOG_ITEM_FIELDS),
        "sources_command": "agentdeck skills sources",
        "sources_response_fields": list(SKILLS_SOURCES_RESPONSE_FIELDS),
        "deps_command": "agentdeck skills deps --name <name>",
        "deps_response_fields": list(SKILLS_DEPS_RESPONSE_FIELDS),
        "load_plan_command": "agentdeck skills load-plan --name <name> --agent <agent_id>",
        "skill_load_plan_response_fields": list(SKILL_LOAD_PLAN_RESPONSE_FIELDS),
        "lock_command": "agentdeck skills lock --name <name>",
        "skill_lock_response_fields": list(SKILL_LOCK_RESPONSE_FIELDS),
        "lock_verify_command": "agentdeck skills lock-verify --name <name>",
        "skill_lock_verify_response_fields": list(SKILL_LOCK_VERIFY_RESPONSE_FIELDS),
        "skills_show_command_template": "agentdeck skills show --name <name>",
        "skills_import_preview_command_template": "agentdeck skills import-preview --path <SKILL.md>",
        "skills_import_command_template": "agentdeck skills import --path <SKILL.md>",
        "skills_load_preview_command_template": "agentdeck skills load-preview --name <name> --agent <agent_id> --purpose <purpose>",
        "skills_load_command_template": "agentdeck skills load --name <name> --agent <agent_id> --purpose <purpose>",
        "skills_suggestions_command": "agentdeck skills suggestions",
        "skills_draft_preview_command_template": "agentdeck skills draft-preview --suggestion-id <id>",
        "skills_create_command_template": "agentdeck skills create --suggestion-id <id> --confirm",
        "skills_suggest_command_template": "agentdeck skills suggest --name <name> --summary <summary> --rationale <rationale> --source <source>",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "list_response_fields": list(SKILLS_LIST_RESPONSE_FIELDS),
        "detail_response_fields": list(SKILLS_DETAIL_RESPONSE_FIELDS),
        "import_preview_response_fields": list(SKILLS_IMPORT_PREVIEW_RESPONSE_FIELDS),
        "import_response_fields": list(SKILLS_IMPORT_RESPONSE_FIELDS),
        "load_preview_response_fields": list(SKILLS_LOAD_PREVIEW_RESPONSE_FIELDS),
        "load_response_fields": list(SKILLS_LOAD_RESPONSE_FIELDS),
        "suggest_response_fields": list(SKILLS_SUGGEST_RESPONSE_FIELDS),
        "suggestions_response_fields": list(SKILLS_SUGGESTIONS_RESPONSE_FIELDS),
        "draft_preview_response_fields": list(SKILLS_DRAFT_PREVIEW_RESPONSE_FIELDS),
        "create_response_fields": list(SKILLS_CREATE_RESPONSE_FIELDS),
        "skill_item_fields": list(SKILLS_SKILL_ITEM_FIELDS),
        "suggestion_item_fields": list(SKILLS_SUGGESTION_ITEM_FIELDS),
        "detail_skill_fields": list(SKILLS_DETAIL_SKILL_FIELDS),
        "load_skill_fields": list(SKILLS_LOAD_SKILL_FIELDS),
        "skill_control_fields": list(SKILLS_CONTROL_FIELDS),
    }


def skills_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = skills_contract_payload(contract_path)
    if include_example:
        example = skills_example()
        payload["example"] = True
        payload["example_list_response_fields"] = list(example["list"])
        payload["example_detail_response_fields"] = list(example["detail"])
        payload["example_import_preview_response_fields"] = list(example["import_preview"])
        payload["example_import_response_fields"] = list(example["import"])
        payload["example_load_preview_response_fields"] = list(example["load_preview"])
        payload["example_load_response_fields"] = list(example["load"])
        payload["example_suggest_response_fields"] = list(example["suggest"])
        payload["example_suggestions_response_fields"] = list(example["suggestions"])
        payload["example_draft_preview_response_fields"] = list(example["draft_preview"])
        payload["example_create_response_fields"] = list(example["create"])
        payload["example_skill_item_fields"] = list(example["list"]["skills"][0])
        payload["example_suggestion_item_fields"] = list(example["suggestions"]["items"][0])
        payload["example_detail_skill_fields"] = list(example["detail"]["skill"])
        payload["example_load_skill_fields"] = list(example["load"]["skill"])
        payload["example_skill_control_fields"] = list(example["list"]["skills"][0]["controls"][0])
        payload["example_skills"] = example
    return payload


def skills_example() -> dict[str, object]:
    show_command = "agentdeck skills show --name planning"
    load_command = "agentdeck skills load --name planning"
    skill_item = {
        "name": "planning",
        "description": "Break goals into approval-gated multi-agent plans.",
        "source": "builtin",
        "path": None,
        "content_hash": "sha256:example",
        "required_tools": ["leader-plan", "approval-list"],
        "planning_guidance": [],
        "risk": "inspect",
        "version": "0.0.0",
        "show_command": show_command,
        "load_command": load_command,
        "controls": [
            {
                "kind": "show",
                "label": "Show skill",
                "command": show_command,
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "load",
                "label": "Load skill",
                "command": load_command,
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
        ],
    }
    import_control = {
        "kind": "import",
        "label": "Import skill",
        "command": "agentdeck skills import --path <SKILL.md>",
        "safety": "explicit_user",
        "enabled": False,
        "blocker": "requires SKILL.md path",
    }
    suggest_control = {
        "kind": "suggest",
        "label": "Suggest skill",
        "command": "agentdeck skills suggest --name <name> --summary <summary> --rationale <rationale> --source human",
        "safety": "explicit_user",
        "enabled": False,
        "blocker": "requires suggestion fields",
    }
    suggestion_item = {
        "suggestion_id": "sgs_example",
        "status": "pending",
        "name": "incident-review",
        "summary": "Review incident response evidence.",
        "rationale": "planner repeatedly asked for the same incident review checklist",
        "source": "leader",
        "agent_id": "reviewer",
        "trace_id": "msg_example",
        "draft_path": ".agentdeck/skills/incident-review/SKILL.md",
        "created_at": "2026-07-04T00:00:00+00:00",
        "draft_preview_command": "agentdeck skills draft-preview --suggestion-id sgs_example",
        "controls": [
            {
                "kind": "inspect",
                "label": "List skill suggestions",
                "command": "agentdeck skills suggestions",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "draft_preview",
                "label": "Preview skill draft",
                "command": "agentdeck skills draft-preview --suggestion-id sgs_example",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            }
        ],
    }
    created_suggestion_item = {
        **deepcopy(suggestion_item),
        "status": "created",
        "created_skill_path": ".agentdeck/skills/incident-review/SKILL.md",
        "created_content_hash": "sha256:example-draft",
        "created_at": "2026-07-04T00:05:00+00:00",
    }
    draft_content = """---
name: incident-review
description: Review incident response evidence.
required_tools:
risk: inspect
---
# Incident Review

Review incident response evidence.

## Rationale

planner repeatedly asked for the same incident review checklist

## Provenance

- source: leader
- agent_id: reviewer
- trace_id: msg_example
"""
    return {
        "list": {
            "ok": True,
            "mode": "skills_list",
            "skill_count": 1,
            "import_command_template": "agentdeck skills import --path <SKILL.md>",
            "controls": [import_control],
            "skills": [deepcopy(skill_item)],
        },
        "detail": {
            "ok": True,
            "mode": "skill_detail",
            "skill": {
                **deepcopy(skill_item),
                "content": "# Planning\n\nBreak a user goal into role-aware steps.\n",
            },
        },
        "import_preview": {
            "ok": True,
            "mode": "skill_import_preview",
            "skill": {
                **deepcopy(skill_item),
                "source": "project",
                "path": "/workspace/project/.agentdeck/skills/planning/SKILL.md",
            },
            "source_path": "/external/skills/planning/SKILL.md",
            "project_path": "/workspace/project/.agentdeck/skills/planning/SKILL.md",
            "would_overwrite": False,
            "source_allowlisted": False,
            "enforcement_active": False,
            "import_blocked": False,
            "import_command": "agentdeck skills import --path /external/skills/planning/SKILL.md",
            "force_import_command": "agentdeck skills import --path /external/skills/planning/SKILL.md --force",
            "controls": [
                {
                    "kind": "import",
                    "label": "Import skill",
                    "command": "agentdeck skills import --path /external/skills/planning/SKILL.md",
                    "safety": "explicit_user",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "force_import",
                    "label": "Force import skill",
                    "command": "agentdeck skills import --path /external/skills/planning/SKILL.md --force",
                    "safety": "explicit_user",
                    "enabled": False,
                    "blocker": "skill does not exist",
                },
                {
                    "kind": "show_after_import",
                    "label": "Show skill after import",
                    "command": show_command,
                    "safety": "inspect",
                    "enabled": False,
                    "blocker": "skill is not imported yet",
                },
            ],
        },
        "load_preview": {
            "ok": True,
            "mode": "skill_load_preview",
            "agent_id": "planner",
            "purpose": "plan decomposition",
            "skill": deepcopy(skill_item),
            "load_command": "agentdeck skills load --name planning --agent planner --purpose 'plan decomposition'",
            "unmet_dependencies": [],
            "has_dependency_cycle": False,
            "controls": [
                {
                    "kind": "load",
                    "label": "Load skill",
                    "command": "agentdeck skills load --name planning --agent planner --purpose 'plan decomposition'",
                    "safety": "explicit_user",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "show",
                    "label": "Show skill",
                    "command": show_command,
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
            ],
        },
        "import": {
            "ok": True,
            "mode": "skill_imported",
            "skill": deepcopy(skill_item),
            "source_path": "/external/skills/planning/SKILL.md",
            "project_path": "/workspace/project/.agentdeck/skills/planning/SKILL.md",
            "overwritten": False,
            "show_command": show_command,
            "load_command": load_command,
        },
        "load": {
            "ok": True,
            "mode": "skill_loaded",
            "load_id": "skl_example",
            "agent_id": "leader",
            "purpose": "plan decomposition",
            "created_at": "2026-07-04T00:00:00+00:00",
            "skill": {
                **deepcopy(skill_item),
                "content_snapshot": "# Planning\n\nBreak a user goal into role-aware steps.\n",
            },
        },
        "suggest": {
            "ok": True,
            "mode": "skill_suggested",
            "suggestion": deepcopy(suggestion_item),
            "next_command": "agentdeck skills suggestions",
        },
        "suggestions": {
            "ok": True,
            "mode": "skill_suggestions",
            "count": 1,
            "pending_count": 1,
            "items": [deepcopy(suggestion_item)],
            "controls": [suggest_control],
        },
        "draft_preview": {
            "ok": True,
            "mode": "skill_draft_preview",
            "suggestion_id": "sgs_example",
            "suggestion": deepcopy(suggestion_item),
            "name": "incident-review",
            "target_path": ".agentdeck/skills/incident-review/SKILL.md",
            "would_create": True,
            "would_overwrite": False,
            "source": "leader",
            "agent_id": "reviewer",
            "trace_id": "msg_example",
            "proposed_content": draft_content,
            "proposed_content_hash": "sha256:example-draft",
            "create_command": "agentdeck skills create --suggestion-id sgs_example --confirm",
            "controls": [
                {
                    "kind": "create_skill",
                    "label": "Create skill",
                    "command": "agentdeck skills create --suggestion-id sgs_example --confirm",
                    "safety": "explicit_user",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "list_suggestions",
                    "label": "List skill suggestions",
                    "command": "agentdeck skills suggestions",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
            ],
        },
        "create": {
            "ok": True,
            "mode": "skill_created",
            "suggestion_id": "sgs_example",
            "suggestion": deepcopy(created_suggestion_item),
            "name": "incident-review",
            "path": ".agentdeck/skills/incident-review/SKILL.md",
            "overwritten": False,
            "content_hash": "sha256:example-draft",
            "show_command": "agentdeck skills show --name incident-review",
            "load_command": "agentdeck skills load --name incident-review",
            "controls": [
                {
                    "kind": "show",
                    "label": "Show skill",
                    "command": "agentdeck skills show --name incident-review",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "load",
                    "label": "Load skill",
                    "command": "agentdeck skills load --name incident-review",
                    "safety": "explicit_user",
                    "enabled": True,
                    "blocker": None,
                },
            ],
        },
    }


def memory_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "memory_suggest_command_template": "agentdeck memory suggest --summary <summary> --rationale <rationale> --source <source>",
        "memory_suggestions_command": "agentdeck memory suggestions",
        "memory_apply_preview_command_template": "agentdeck memory apply-preview --suggestion-id <id>",
        "memory_apply_command_template": "agentdeck memory apply --suggestion-id <id> --confirm",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "suggest_response_fields": list(MEMORY_SUGGEST_RESPONSE_FIELDS),
        "suggestions_response_fields": list(MEMORY_SUGGESTIONS_RESPONSE_FIELDS),
        "apply_preview_response_fields": list(MEMORY_APPLY_PREVIEW_RESPONSE_FIELDS),
        "apply_response_fields": list(MEMORY_APPLY_RESPONSE_FIELDS),
        "suggestion_item_fields": list(MEMORY_SUGGESTION_ITEM_FIELDS),
        "control_fields": list(MEMORY_CONTROL_FIELDS),
    }


def memory_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = memory_contract_payload(contract_path)
    if include_example:
        example = memory_example()
        payload["example"] = True
        payload["example_suggest_response_fields"] = list(example["suggest"])
        payload["example_suggestions_response_fields"] = list(example["suggestions"])
        payload["example_apply_preview_response_fields"] = list(example["apply_preview"])
        payload["example_apply_response_fields"] = list(example["apply"])
        payload["example_suggestion_item_fields"] = list(example["suggestions"]["items"][0])
        payload["example_control_fields"] = list(example["suggestions"]["items"][0]["controls"][0])
        payload["example_memory"] = example
    return payload


def memory_example() -> dict[str, object]:
    suggestion_id = "mem_example"
    target = ".agentdeck/memory/project.md"
    proposed_append = (
        "- Keep approval-gated worker dispatch.\n"
        "  - rationale: project safety preference\n"
        "  - source: reviewer\n"
        "  - agent_id: leader\n"
        "  - trace_id: msg_memory\n"
        f"  - suggestion_id: {suggestion_id}\n"
    )
    inspect_control = {
        "kind": "inspect",
        "label": "List memory suggestions",
        "command": "agentdeck memory suggestions",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }
    apply_preview_control = {
        "kind": "apply_preview",
        "label": "Preview memory apply",
        "command": f"agentdeck memory apply-preview --suggestion-id {suggestion_id}",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }
    apply_memory_control = {
        "kind": "apply_memory",
        "label": "Apply memory suggestion",
        "command": f"agentdeck memory apply --suggestion-id {suggestion_id} --confirm",
        "safety": "explicit_user",
        "enabled": True,
        "blocker": None,
    }
    suggestion_item = {
        "suggestion_id": suggestion_id,
        "status": "pending",
        "scope": "project",
        "summary": "Keep approval-gated worker dispatch.",
        "rationale": "project safety preference",
        "source": "reviewer",
        "agent_id": "leader",
        "trace_id": "msg_memory",
        "target": target,
        "created_at": "2026-07-07T00:00:00Z",
        "controls": [inspect_control, apply_preview_control, apply_memory_control],
    }
    applied_suggestion = {
        **deepcopy(suggestion_item),
        "status": "applied",
        "applied_at": "2026-07-07T00:01:00Z",
        "applied_path": target,
        "controls": [
            inspect_control,
            {
                **apply_preview_control,
                "enabled": False,
                "blocker": "memory suggestion is not pending",
            },
            {
                **apply_memory_control,
                "enabled": False,
                "blocker": "memory suggestion is not pending",
            },
        ],
    }
    return {
        "suggest": {
            "ok": True,
            "mode": "memory_suggested",
            "suggestion": deepcopy(suggestion_item),
            "next_command": "agentdeck memory suggestions",
        },
        "suggestions": {
            "ok": True,
            "mode": "memory_suggestions",
            "count": 1,
            "pending_count": 1,
            "apply_preview_command_template": "agentdeck memory apply-preview --suggestion-id <id>",
            "items": [deepcopy(suggestion_item)],
            "controls": [
                {
                    "kind": "suggest",
                    "label": "Suggest memory",
                    "command": "agentdeck memory suggest --summary <summary> --rationale <rationale> --source human",
                    "safety": "explicit_user",
                    "enabled": False,
                    "blocker": "requires suggestion fields",
                },
                {
                    "kind": "apply_preview",
                    "label": "Preview memory apply",
                    "command": "agentdeck memory apply-preview --suggestion-id <id>",
                    "safety": "inspect",
                    "enabled": False,
                    "blocker": "requires suggestion id",
                },
            ],
        },
        "apply_preview": {
            "ok": True,
            "mode": "memory_apply_preview",
            "suggestion_id": suggestion_id,
            "suggestion": deepcopy(suggestion_item),
            "target": target,
            "target_exists": False,
            "would_create": True,
            "would_update_status": "applied",
            "proposed_append": proposed_append,
            "apply_command": f"agentdeck memory apply --suggestion-id {suggestion_id} --confirm",
            "controls": [inspect_control, apply_memory_control],
        },
        "apply": {
            "ok": True,
            "mode": "memory_applied",
            "suggestion_id": suggestion_id,
            "suggestion": applied_suggestion,
            "target": target,
            "applied_path": target,
            "appended": proposed_append,
        },
    }


def learning_review_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "learn_review_command_template": "agentdeck learn review --plan-id <id>",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "response_fields": list(LEARNING_REVIEW_RESPONSE_FIELDS),
        "skill_suggestion_fields": list(LEARNING_REVIEW_SKILL_SUGGESTION_FIELDS),
        "memory_suggestion_fields": list(LEARNING_REVIEW_MEMORY_SUGGESTION_FIELDS),
        "control_fields": list(LEARNING_REVIEW_CONTROL_FIELDS),
        "project_view_schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "leader_summary_contract": "agentdeck contract leader-summary",
        "skills_contract": "agentdeck contract skills",
        "memory_contract": "agentdeck contract memory",
    }


def learning_review_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = learning_review_contract_payload(contract_path)
    if include_example:
        example = learning_review_example()
        payload["example"] = True
        payload["example_response_fields"] = list(example)
        payload["example_skill_suggestion_fields"] = list(example["skill_suggestion"])
        payload["example_memory_suggestion_fields"] = list(example["memory_suggestion"])
        payload["example_control_fields"] = list(example["controls"][0])
        payload["example_learning_review"] = example
    return payload


def learning_review_example() -> dict[str, object]:
    plan_id = "pln_example"
    skill_command = (
        "agentdeck skills suggest --name deployment-review "
        "--summary 'Review Build a GUI-ready recovery panel as a repeatable workflow.' "
        "--rationale 'Plan pln_example produced 1 replies and 1 artifacts; "
        "capture the reusable review procedure as an explicit skill suggestion.' "
        "--source learn-review --agent leader --from-trace pln_example"
    )
    memory_command = (
        "agentdeck memory suggest "
        "--summary 'Plan pln_example completed with 1 replies and 1 artifacts; "
        "review whether its lessons should become durable project memory.' "
        "--rationale 'Learning review is read-only, so durable memory must still go through "
        "memory suggestions, apply-preview, and explicit apply confirmation.' "
        "--source learn-review --agent leader --from-trace pln_example --scope project"
    )
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "ok": True,
        "mode": "learning_review",
        "plan_id": plan_id,
        "task": "Build a GUI-ready recovery panel",
        "status": "ready",
        "reply_count": 1,
        "artifact_count": 1,
        "summary": "Learning review can queue explicit skill and memory suggestions.",
        "plan_status_command": f"agentdeck plan status --plan-id {plan_id}",
        "summary_command": f"agentdeck leader summary --plan-id {plan_id}",
        "skill_suggestion": {
            "kind": "skill_suggestion",
            "name": "deployment-review",
            "summary": "Review Build a GUI-ready recovery panel as a repeatable workflow.",
            "rationale": (
                "Plan pln_example produced 1 replies and 1 artifacts; "
                "capture the reusable review procedure as an explicit skill suggestion."
            ),
            "source": "learn-review",
            "agent_id": "leader",
            "trace_id": plan_id,
            "command": skill_command,
        },
        "memory_suggestion": {
            "kind": "memory_suggestion",
            "scope": "project",
            "summary": (
                "Plan pln_example completed with 1 replies and 1 artifacts; "
                "review whether its lessons should become durable project memory."
            ),
            "rationale": (
                "Learning review is read-only, so durable memory must still go through "
                "memory suggestions, apply-preview, and explicit apply confirmation."
            ),
            "source": "learn-review",
            "agent_id": "leader",
            "trace_id": plan_id,
            "command": memory_command,
        },
        "controls": [
            {
                "kind": "summary",
                "label": "View Leader summary",
                "command": f"agentdeck leader summary --plan-id {plan_id}",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "suggest_skill",
                "label": "Queue skill suggestion",
                "command": skill_command,
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "suggest_memory",
                "label": "Queue memory suggestion",
                "command": memory_command,
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
        ],
    }


def validate_learning_review_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in LEARNING_REVIEW_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing learning_review field: {field}")
    if payload.get("schema_version") != PROJECT_VIEW_SCHEMA_VERSION:
        errors.append("schema_version must match ProjectView schema version")
    if payload.get("ok") is not True:
        errors.append("ok must be true")
    if payload.get("mode") != "learning_review":
        errors.append("mode must be learning_review")
    plan_id = payload.get("plan_id")
    if not isinstance(plan_id, str) or not plan_id:
        errors.append("plan_id must be a non-empty string")
    status = payload.get("status")
    if status not in {"ready", "waiting"}:
        errors.append("status must be ready or waiting")
    for count_field in ("reply_count", "artifact_count"):
        count = payload.get(count_field)
        if not isinstance(count, int) or count < 0:
            errors.append(f"{count_field} must be a non-negative integer")
    if isinstance(plan_id, str) and plan_id:
        if payload.get("plan_status_command") != f"agentdeck plan status --plan-id {plan_id}":
            errors.append("plan_status_command must match plan_id")
        if payload.get("summary_command") != f"agentdeck leader summary --plan-id {plan_id}":
            errors.append("summary_command must match plan_id")
    skill_suggestion = payload.get("skill_suggestion")
    if isinstance(skill_suggestion, dict):
        for field in LEARNING_REVIEW_SKILL_SUGGESTION_FIELDS:
            if field not in skill_suggestion:
                errors.append(f"skill_suggestion: missing field: {field}")
        if skill_suggestion.get("kind") != "skill_suggestion":
            errors.append("skill_suggestion.kind must be skill_suggestion")
        if skill_suggestion.get("source") != "learn-review":
            errors.append("skill_suggestion.source must be learn-review")
        if skill_suggestion.get("agent_id") != "leader":
            errors.append("skill_suggestion.agent_id must be leader")
        if isinstance(plan_id, str) and plan_id and skill_suggestion.get("trace_id") != plan_id:
            errors.append("skill_suggestion.trace_id must match plan_id")
        command = skill_suggestion.get("command")
        if not isinstance(command, str) or not command.startswith("agentdeck skills suggest "):
            errors.append("skill_suggestion.command must use agentdeck skills suggest")
        elif "--source learn-review" not in command:
            errors.append("skill_suggestion.command must include --source learn-review")
        elif "--agent leader" not in command:
            errors.append("skill_suggestion.command must include --agent leader")
        elif isinstance(plan_id, str) and plan_id and f"--from-trace {plan_id}" not in command:
            errors.append("skill_suggestion.command must include --from-trace plan_id")
    elif "skill_suggestion" in payload:
        errors.append("skill_suggestion must be an object")
    memory_suggestion = payload.get("memory_suggestion")
    if isinstance(memory_suggestion, dict):
        for field in LEARNING_REVIEW_MEMORY_SUGGESTION_FIELDS:
            if field not in memory_suggestion:
                errors.append(f"memory_suggestion: missing field: {field}")
        if memory_suggestion.get("kind") != "memory_suggestion":
            errors.append("memory_suggestion.kind must be memory_suggestion")
        if memory_suggestion.get("scope") != "project":
            errors.append("memory_suggestion.scope must be project")
        if memory_suggestion.get("source") != "learn-review":
            errors.append("memory_suggestion.source must be learn-review")
        if memory_suggestion.get("agent_id") != "leader":
            errors.append("memory_suggestion.agent_id must be leader")
        if isinstance(plan_id, str) and plan_id and memory_suggestion.get("trace_id") != plan_id:
            errors.append("memory_suggestion.trace_id must match plan_id")
        command = memory_suggestion.get("command")
        if not isinstance(command, str) or not command.startswith("agentdeck memory suggest "):
            errors.append("memory_suggestion.command must use agentdeck memory suggest")
        elif "--source learn-review" not in command:
            errors.append("memory_suggestion.command must include --source learn-review")
        elif "--agent leader" not in command:
            errors.append("memory_suggestion.command must include --agent leader")
        elif isinstance(plan_id, str) and plan_id and f"--from-trace {plan_id}" not in command:
            errors.append("memory_suggestion.command must include --from-trace plan_id")
        elif "--scope project" not in command:
            errors.append("memory_suggestion.command must include --scope project")
    elif "memory_suggestion" in payload:
        errors.append("memory_suggestion must be an object")
    controls = payload.get("controls")
    if isinstance(controls, list):
        seen_kinds: set[str] = set()
        for control in controls:
            if not isinstance(control, dict):
                errors.append("controls items must be objects")
                continue
            for field in LEARNING_REVIEW_CONTROL_FIELDS:
                if field not in control:
                    errors.append(f"controls: missing field: {field}")
            kind = control.get("kind")
            if isinstance(kind, str):
                seen_kinds.add(kind)
            if kind == "summary":
                if control.get("command") != payload.get("summary_command"):
                    errors.append("controls: summary command must match summary_command")
                if control.get("safety") != "inspect":
                    errors.append("controls: summary must use safety=inspect")
            if kind == "suggest_skill":
                expected_command = skill_suggestion.get("command") if isinstance(skill_suggestion, dict) else None
                if control.get("command") != expected_command:
                    errors.append("controls: suggest_skill command must match skill_suggestion.command")
                if control.get("safety") != "explicit_user":
                    errors.append("controls: suggest_skill must use safety=explicit_user")
            if kind == "suggest_memory":
                expected_command = memory_suggestion.get("command") if isinstance(memory_suggestion, dict) else None
                if control.get("command") != expected_command:
                    errors.append("controls: suggest_memory command must match memory_suggestion.command")
                if control.get("safety") != "explicit_user":
                    errors.append("controls: suggest_memory must use safety=explicit_user")
            if control.get("enabled") is False and not control.get("blocker"):
                errors.append("controls: disabled controls must include blocker")
        missing_kinds = {"summary", "suggest_skill", "suggest_memory"} - seen_kinds
        for kind in sorted(missing_kinds):
            errors.append(f"controls: missing {kind} control")
    elif "controls" in payload:
        errors.append("controls must be a list")
    return {"ok": not errors, "errors": errors}


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
        "learning_review_card_fields": list(LEARNING_REVIEW_RESPONSE_FIELDS),
        "leader_summary_card_fields": list(LEADER_SUMMARY_RESPONSE_FIELDS),
        "continue_card_fields": list(CONTINUE_CARD_FIELDS),
        "run_start_card_fields": list(RUN_START_RESPONSE_FIELDS),
        "run_progress_card_fields": list(RUN_PROGRESS_RESPONSE_FIELDS),
        "plan_board_card_fields": list(LEADER_CHAT_PLAN_BOARD_CARD_FIELDS),
        "skills_catalog_card_fields": list(LEADER_CHAT_SKILLS_CATALOG_CARD_FIELDS),
        "run_loop_preview_card_fields": list(LEADER_CHAT_RUN_LOOP_PREVIEW_CARD_FIELDS),
        "mission_preview_card_fields": list(MISSION_PREVIEW_RESPONSE_FIELDS),
        "mission_status_card_fields": list(MISSION_STATUS_RESPONSE_FIELDS),
        "mission_run_card_fields": list(MISSION_RUN_RESPONSE_FIELDS),
        "capture_card_fields": list(LEADER_CHAT_CAPTURE_CARD_FIELDS),
        "terminal_card_fields": list(LEADER_CHAT_TERMINAL_CARD_FIELDS),
        "dispatch_preview_card_fields": list(LEADER_CHAT_DISPATCH_PREVIEW_CARD_FIELDS),
        "dispatch_batch_preview_card_fields": list(LEADER_CHAT_DISPATCH_BATCH_PREVIEW_CARD_FIELDS),
        "dispatch_batch_preview_item_fields": list(LEADER_CHAT_DISPATCH_PREVIEW_CARD_FIELDS),
        "runtime_action_card_fields": list(LEADER_CHAT_RUNTIME_ACTION_CARD_FIELDS),
        "startup_preview_card_fields": list(LEADER_CHAT_STARTUP_PREVIEW_CARD_FIELDS),
        "startup_preview_item_fields": list(LEADER_CHAT_STARTUP_PREVIEW_ITEM_FIELDS),
        "provider_setup_card_fields": list(LEADER_CHAT_PROVIDER_SETUP_CARD_FIELDS),
        "provider_switch_card_fields": list(LEADER_CHAT_PROVIDER_SWITCH_CARD_FIELDS),
        "agent_ready_card_fields": list(AGENT_RUNTIME_READY_RESPONSE_FIELDS),
        "runtime_card_fields": list(WORKBENCH_RUNTIME_CARD_FIELDS),
        "terminal_session_card_fields": list(WORKBENCH_TERMINAL_SESSION_CARD_FIELDS),
        "terminal_session_control_fields": list(WORKBENCH_TERMINAL_SESSION_CONTROL_FIELDS),
        "terminal_session_item_fields": list(WORKBENCH_TERMINAL_SESSION_ITEM_FIELDS),
        "leader_status_card_fields": list(LEADER_STATUS_RESPONSE_FIELDS),
        "leader_status_queue_fields": list(LEADER_STATUS_QUEUE_FIELDS),
        "frontdesk_card_fields": list(LEADER_CHAT_FRONTDESK_CARD_FIELDS),
        "skill_context_card_fields": list(LEADER_CHAT_SKILL_CONTEXT_CARD_FIELDS),
        "memory_context_card_fields": list(LEADER_CHAT_MEMORY_CONTEXT_CARD_FIELDS),
        "skill_import_preview_card_fields": list(LEADER_CHAT_SKILL_IMPORT_PREVIEW_CARD_FIELDS),
        "skill_load_preview_card_fields": list(LEADER_CHAT_SKILL_LOAD_PREVIEW_CARD_FIELDS),
        "skill_create_preview_card_fields": list(LEADER_CHAT_SKILL_CREATE_PREVIEW_CARD_FIELDS),
        "skill_suggestions_card_fields": list(LEADER_CHAT_SKILL_SUGGESTIONS_CARD_FIELDS),
        "memory_apply_preview_card_fields": list(LEADER_CHAT_MEMORY_APPLY_PREVIEW_CARD_FIELDS),
        "memory_suggestions_card_fields": list(LEADER_CHAT_MEMORY_SUGGESTIONS_CARD_FIELDS),
        "skill_context_item_fields": list(PROJECT_VIEW_SKILL_ITEM_FIELDS),
        "provider_health_fields": list(WORKBENCH_PROVIDER_HEALTH_FIELDS),
        "queue_card_fields": list(WORKBENCH_QUEUE_CARD_FIELDS),
        "operator_card_fields": list(WORKBENCH_OPERATOR_CARD_FIELDS),
        "role_card_fields": list(WORKBENCH_ROLE_CARD_FIELDS),
        "role_agent_fields": list(WORKBENCH_ROLE_AGENT_FIELDS),
        "worker_lifecycle_card_fields": list(WORKBENCH_WORKER_LIFECYCLE_CARD_FIELDS),
        "worker_lifecycle_item_fields": list(WORKBENCH_WORKER_LIFECYCLE_ITEM_FIELDS),
        "review_gate_card_fields": list(WORKBENCH_REVIEW_GATE_CARD_FIELDS),
        "review_gate_stage_fields": list(WORKBENCH_REVIEW_GATE_STAGE_FIELDS),
        "release_preview_card_fields": list(WORKBENCH_RELEASE_PREVIEW_CARD_FIELDS),
        "role_topology_card_fields": list(WORKBENCH_ROLE_TOPOLOGY_CARD_FIELDS),
        "role_topology_item_fields": list(WORKBENCH_ROLE_TOPOLOGY_ITEM_FIELDS),
        "ledger_card_fields": list(WORKBENCH_LEDGER_CARD_FIELDS),
        "lineage_card_fields": list(WORKBENCH_LINEAGE_CARD_FIELDS),
        "lineage_path_fields": list(WORKBENCH_LINEAGE_PATH_FIELDS),
        "audit_card_fields": list(WORKBENCH_AUDIT_CARD_FIELDS),
        "audit_event_fields": list(WORKBENCH_AUDIT_EVENT_FIELDS),
        "artifacts_card_fields": list(ARTIFACTS_RESPONSE_FIELDS),
        "artifact_summary_fields": list(ARTIFACTS_SUMMARY_FIELDS),
        "artifact_item_fields": list(PROJECT_VIEW_ARTIFACT_ITEM_FIELDS),
        "trace_card_fields": list(TRACE_TOP_LEVEL_FIELDS),
        "trace_message_fields": list(TRACE_MESSAGE_FIELDS),
        "trace_attempt_fields": list(TRACE_ATTEMPT_FIELDS),
        "trace_job_fields": list(TRACE_JOB_FIELDS),
        "trace_reply_fields": list(TRACE_REPLY_FIELDS),
        "trace_artifact_fields": list(TRACE_ARTIFACT_FIELDS),
        "trace_inbox_item_fields": list(TRACE_INBOX_ITEM_FIELDS),
        "workbench_card_fields": list(WORKBENCH_SNAPSHOT_FIELDS),
        "mission_card_fields": list(WORKBENCH_MISSION_CARD_FIELDS),
        "control_mode_card_fields": list(WORKBENCH_CONTROL_MODE_CARD_FIELDS),
        "control_mode_option_fields": list(WORKBENCH_CONTROL_MODE_OPTION_FIELDS),
        "control_mode_control_fields": list(WORKBENCH_CONTROL_MODE_CONTROL_FIELDS),
        "workbench_control_registry_item_fields": list(WORKBENCH_CONTROL_REGISTRY_ITEM_FIELDS),
        "control_registry_card_fields": list(LEADER_CHAT_CONTROL_REGISTRY_CARD_FIELDS),
        "control_registry_group_fields": list(CONTROL_REGISTRY_GROUP_FIELDS),
        "control_registry_selection_fields": list(CONTROL_REGISTRY_SELECTION_FIELDS),
        "control_registry_filter_fields": list(CONTROL_REGISTRY_FILTER_FIELDS),
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
        payload["example_learning_review_card_fields"] = list(example["learning_review_card"])
        payload["example_leader_summary_card_fields"] = list(example["leader_summary_card"])
        payload["example_continue_card_fields"] = list(example["continue_card"])
        payload["example_run_start_card_fields"] = list(RUN_START_RESPONSE_FIELDS)
        payload["example_run_progress_card_fields"] = list(RUN_PROGRESS_RESPONSE_FIELDS)
        payload["example_terminal_card_fields"] = list(example["terminal_card"])
        payload["example_dispatch_preview_card_fields"] = list(LEADER_CHAT_DISPATCH_PREVIEW_CARD_FIELDS)
        payload["example_dispatch_batch_preview_card_fields"] = list(
            LEADER_CHAT_DISPATCH_BATCH_PREVIEW_CARD_FIELDS
        )
        payload["example_dispatch_batch_preview_item_fields"] = list(
            LEADER_CHAT_DISPATCH_PREVIEW_CARD_FIELDS
        )
        payload["example_runtime_action_card_fields"] = list(example["runtime_action_card"])
        payload["example_startup_preview_card_fields"] = list(example["startup_preview_card"])
        payload["example_startup_preview_item_fields"] = list(example["startup_preview_card"]["items"][0])
        payload["example_provider_setup_card_fields"] = list(example["provider_setup_card"])
        payload["example_provider_switch_card_fields"] = list(example["provider_switch_card"])
        payload["example_agent_ready_card_fields"] = list(example["agent_ready_card"])
        payload["example_runtime_card_fields"] = list(example["runtime_card"])
        terminal_session_card = example["workbench_card"]["terminal_session_card"]
        payload["example_terminal_session_card_fields"] = list(terminal_session_card)
        payload["example_terminal_session_control_fields"] = list(terminal_session_card["controls"][0])
        payload["example_terminal_session_item_fields"] = list(terminal_session_card["terminals"][0])
        payload["example_leader_status_card_fields"] = list(example["leader_status_card"])
        payload["example_leader_status_queue_fields"] = list(example["leader_status_card"]["queues"])
        payload["example_frontdesk_card_fields"] = list(example["frontdesk_card"])
        payload["example_skill_context_card_fields"] = list(example["skill_context_card"])
        payload["example_memory_context_card_fields"] = list(example["memory_context_card"])
        payload["example_skill_import_preview_card_fields"] = list(example["skill_import_preview_card"])
        payload["example_skill_load_preview_card_fields"] = list(example["skill_load_preview_card"])
        payload["example_skill_create_preview_card_fields"] = list(example["skill_create_preview_card"])
        payload["example_skill_suggestions_card_fields"] = list(example["skill_suggestions_card"])
        payload["example_memory_apply_preview_card_fields"] = list(example["memory_apply_preview_card"])
        payload["example_memory_suggestions_card_fields"] = list(example["memory_suggestions_card"])
        payload["example_provider_health_fields"] = list(example["provider_health"])
        payload["example_queue_card_fields"] = list(example["queue_card"])
        payload["example_operator_card_fields"] = list(example["operator_card"])
        payload["example_role_card_fields"] = list(example["role_card"])
        payload["example_role_agent_fields"] = list(example["role_card"]["agents"][0])
        payload["example_review_gate_card_fields"] = list(example["review_gate_card"])
        payload["example_review_gate_stage_fields"] = list(example["review_gate_card"]["code_review"])
        payload["example_release_preview_card_fields"] = list(example["release_preview_card"])
        payload["example_ledger_card_fields"] = list(example["ledger_card"])
        payload["example_lineage_card_fields"] = list(example["lineage_card"])
        payload["example_lineage_path_fields"] = list(example["lineage_card"]["recent_paths"][0])
        payload["example_audit_card_fields"] = list(example["audit_card"])
        payload["example_audit_event_fields"] = list(example["audit_card"]["recent_events"][0])
        payload["example_artifacts_card_fields"] = list(example["artifacts_card"])
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
            "mode": "frontdesk",
            "label": "Route with frontdesk",
            "description": "Intake a human request before deep planning or dispatch.",
            "example_messages": ["frontdesk 帮我梳理需求", "前台接待 帮我澄清任务"],
            "command": 'agentdeck leader chat --message "frontdesk <goal>"',
            "safety": "inspect",
            "requires_explicit_user": False,
            "card": "frontdesk_card",
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
            "mode": "run_start",
            "label": "Start approval-gated run",
            "description": "Create a plan and pending approvals from a natural-language run request.",
            "example_messages": ["开始运行 实现一个功能", "/run 实现多 Agent smoke"],
            "command": "agentdeck leader chat --message \"开始运行 <goal>\"",
            "safety": "approval_gated",
            "requires_explicit_user": True,
            "card": "run_start_card",
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
            "mode": "leader_status",
            "label": "Inspect Leader status",
            "description": "Inspect the logical Leader, provider readiness, queue counts, and recovery next command.",
            "example_messages": [
                "查看 Leader 状态",
                "刷新 Leader 状态",
                "Leader 概览",
                "leader status",
                "leader refresh",
                "leader overview",
            ],
            "command": "agentdeck leader status",
            "safety": "inspect",
            "requires_explicit_user": False,
            "card": "leader_status_card",
        },
        {
            "mode": "learning_review",
            "label": "Review learning",
            "description": "Inspect a completed plan for explicit skill and memory suggestion commands.",
            "example_messages": ["学习复盘 pln_xxx", "learning review pln_xxx"],
            "command": "agentdeck learn review --plan-id <plan_id>",
            "safety": "inspect",
            "requires_explicit_user": False,
            "card": "learning_review_card",
        },
        {
            "mode": "skill_import_preview",
            "label": "Preview external skill import",
            "description": "Preview an external SKILL.md before explicitly importing or overwriting it.",
            "example_messages": ["预览导入 skill /path/to/SKILL.md", "preview skill import /path/to/SKILL.md"],
            "command": "agentdeck leader chat --message \"预览导入 skill <SKILL.md>\"",
            "safety": "explicit_user",
            "requires_explicit_user": True,
            "card": "skill_import_preview_card",
        },
        {
            "mode": "skill_load_preview",
            "label": "Preview skill load",
            "description": "Preview loading a skill for an agent before recording replayable context.",
            "example_messages": [
                "预览加载 skill planning 给 planner 用于 decompose work",
                "preview load skill planning for planner purpose decompose work",
            ],
            "command": "agentdeck leader chat --message \"预览加载 skill <name> 给 <agent_id> 用于 <purpose>\"",
            "safety": "explicit_user",
            "requires_explicit_user": True,
            "card": "skill_load_preview_card",
        },
        {
            "mode": "skill_create_preview",
            "label": "Preview skill creation",
            "description": "Preview creating a project skill from a pending suggestion before writing SKILL.md.",
            "example_messages": ["创建 skill 建议 sgs_xxx", "create skill suggestion sgs_xxx"],
            "command": "agentdeck leader chat --message \"创建 skill 建议 <suggestion_id>\"",
            "safety": "explicit_user",
            "requires_explicit_user": True,
            "card": "skill_create_preview_card",
        },
        {
            "mode": "skill_suggestions",
            "label": "Inspect skill suggestions",
            "description": "Inspect pending skill suggestions without creating, importing, or loading skills.",
            "example_messages": ["查看 skill 建议", "skill suggestions"],
            "command": "agentdeck skills suggestions",
            "safety": "inspect",
            "requires_explicit_user": False,
            "card": "skill_suggestions_card",
        },
        {
            "mode": "skill_context",
            "label": "Inspect loaded skills",
            "description": "Inspect loaded skill provenance without installing, loading, or rewriting skills.",
            "example_messages": ["查看已加载技能", "skill context"],
            "command": 'agentdeck leader chat --message "查看已加载技能"',
            "safety": "inspect",
            "requires_explicit_user": False,
            "card": "skill_context_card",
        },
        {
            "mode": "memory_suggestions",
            "label": "Inspect memory suggestions",
            "description": "Inspect pending memory suggestions without writing long-term memory.",
            "example_messages": ["查看 memory 建议", "memory suggestions"],
            "command": "agentdeck memory suggestions",
            "safety": "inspect",
            "requires_explicit_user": False,
            "card": "memory_suggestions_card",
        },
        {
            "mode": "memory_context",
            "label": "Inspect memory context",
            "description": "Inspect applied long-term memory summaries without reading full content or injecting prompts.",
            "example_messages": ["查看长期记忆", "memory context"],
            "command": 'agentdeck leader chat --message "查看长期记忆"',
            "safety": "inspect",
            "requires_explicit_user": False,
            "card": "memory_context_card",
        },
        {
            "mode": "memory_apply_preview",
            "label": "Preview memory apply",
            "description": "Preview applying a pending memory suggestion before writing long-term memory.",
            "example_messages": ["预览 memory 建议 mem_xxx", "preview memory suggestion mem_xxx"],
            "command": "agentdeck leader chat --message \"预览 memory 建议 <suggestion_id>\"",
            "safety": "explicit_user",
            "requires_explicit_user": True,
            "card": "memory_apply_preview_card",
        },
        {
            "mode": "runtime",
            "label": "Inspect runtime",
            "description": "Inspect visible tmux agent panes or suggest explicit agent refresh/spawn/send/stop commands.",
            "example_messages": ["查看 runtime", "刷新 runtime", "启动 planner", "发送给 planner：继续", "停止 planner"],
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
            "mode": "review_gate",
            "label": "Inspect review gate",
            "description": "Inspect artifact, code review, and round review readiness before any release or next round.",
            "example_messages": ["查看验收门", "review gate"],
            "command": 'agentdeck leader chat --message "查看验收门"',
            "safety": "inspect",
            "requires_explicit_user": False,
            "card": "review_gate_card",
        },
        {
            "mode": "release_preview",
            "label": "Inspect release preview",
            "description": "Inspect release / next-round readiness derived from the review gate before any explicit release command exists.",
            "example_messages": ["查看发布预览", "release preview"],
            "command": 'agentdeck leader chat --message "查看发布预览"',
            "safety": "inspect",
            "requires_explicit_user": False,
            "card": "release_preview_card",
        },
        {
            "mode": "role_topology",
            "label": "Inspect role topology",
            "description": "Inspect the unified logical and worker role topology with per-role provider, status, and blocker.",
            "example_messages": ["查看角色拓扑", "role topology"],
            "command": 'agentdeck leader chat --message "查看角色拓扑"',
            "safety": "inspect",
            "requires_explicit_user": False,
            "card": "role_topology_card",
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
            "mode": "audit",
            "label": "Inspect audit events",
            "description": "Inspect recent audit events and the events timeline command.",
            "example_messages": ["查看审计", "最近事件"],
            "command": "agentdeck events --limit 20",
            "safety": "inspect",
            "requires_explicit_user": False,
            "card": "audit_card",
        },
        {
            "mode": "artifacts",
            "label": "Inspect artifacts",
            "description": "Inspect worker artifact indexes without reading file contents.",
            "example_messages": ["查看产物", "artifacts"],
            "command": "agentdeck artifacts",
            "safety": "inspect",
            "requires_explicit_user": False,
            "card": "artifacts_card",
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
            "mode": "provider_switch",
            "label": "Switch Leader provider",
            "description": "Suggest an explicit Leader provider switch command without mutating config.",
            "example_messages": ["切换 Leader 到 Codex CLI", "使用 Claude Code 做 Leader"],
            "command": "agentdeck leader set-provider --provider <provider> --model <model>",
            "safety": "explicit_user",
            "requires_explicit_user": True,
            "card": "provider_health",
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


def leader_chat_control_registry_card(
    workbench_card: dict[str, object],
    *,
    scope: str | None = None,
    card: str | None = None,
    query: str | None = None,
    control_id: str | None = None,
    enabled_only: bool = False,
) -> dict[str, object]:
    source_items = workbench_card.get("control_registry") if isinstance(workbench_card.get("control_registry"), list) else []
    items = _filter_control_registry_items(
        source_items,
        scope=scope,
        card=card,
        query=query,
        control_id=control_id,
        enabled_only=enabled_only,
    )
    groups = _control_registry_groups(items)
    selection = _control_registry_selection(items, control_id, source_items=source_items)
    return {
        "mode": "control_registry",
        "title": "Command palette",
        "source_command": "agentdeck workbench",
        "default_command": "agentdeck controls",
        "filters": {
            "scope": scope,
            "card": card,
            "query": query,
            "control_id": control_id,
            "enabled_only": enabled_only,
            "active_filter_keys": _control_registry_active_filter_keys(
                scope=scope,
                card=card,
                query=query,
                control_id=control_id,
                enabled_only=enabled_only,
            ),
            "item_count_before_filter": len(source_items),
        },
        "selection": selection,
        "item_count": len(items),
        "items": items,
        "group_count": len(groups),
        "groups": groups,
    }


def _control_registry_active_filter_keys(
    *,
    scope: str | None,
    card: str | None,
    query: str | None,
    control_id: str | None,
    enabled_only: bool,
) -> list[str]:
    keys: list[str] = []
    if scope is not None:
        keys.append("scope")
    if card is not None:
        keys.append("card")
    if query is not None:
        keys.append("query")
    if control_id is not None:
        keys.append("control_id")
    if enabled_only:
        keys.append("enabled_only")
    return keys


def _filter_control_registry_items(
    items: list[object],
    *,
    scope: str | None,
    card: str | None,
    query: str | None,
    control_id: str | None,
    enabled_only: bool,
) -> list[object]:
    filtered: list[object] = []
    normalized_query = query.lower() if query else None
    for item in items:
        if not isinstance(item, dict):
            continue
        if scope is not None and item.get("scope") != scope:
            continue
        if card is not None and item.get("card") != card:
            continue
        if normalized_query is not None and normalized_query not in _control_registry_search_text(item):
            continue
        if control_id is not None and item.get("control_id") != control_id:
            continue
        if enabled_only and item.get("enabled") is not True:
            continue
        filtered.append(item)
    return filtered


def _control_registry_search_text(item: dict[str, object]) -> str:
    searchable_fields = ("scope", "card", "kind", "label", "command", "agent_id", "control_id")
    return " ".join(str(item.get(field, "")) for field in searchable_fields).lower()


def _control_registry_selection(
    items: list[object],
    control_id: str | None,
    *,
    source_items: list[object] | None = None,
) -> dict[str, object]:
    matched_items = [
        item
        for item in items
        if isinstance(item, dict) and control_id is not None and item.get("control_id") == control_id
    ]
    source_matched_items = [
        item
        for item in (source_items or items)
        if isinstance(item, dict) and control_id is not None and item.get("control_id") == control_id
    ]
    matched = len(matched_items) == 1
    if control_id is None or matched:
        blocker = None
    elif matched_items:
        blocker = "control_id is not unique"
    elif source_matched_items:
        blocker = "control_id filtered out"
    else:
        blocker = "control_id not found"
    selected_control = matched_items[0] if matched else None
    next_command = (
        selected_control.get("command")
        if isinstance(selected_control, dict) and selected_control.get("enabled") is True
        else None
    )
    return {
        "requested_control_id": control_id,
        "matched": matched,
        "matched_count": len(matched_items),
        "selected_control": selected_control,
        "blocker": blocker,
        "next_command": next_command,
    }


def _control_registry_groups(items: list[object]) -> list[dict[str, object]]:
    groups: list[dict[str, object]] = []
    group_index: dict[tuple[str, str], dict[str, object]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        scope = str(item.get("scope") or "")
        card = str(item.get("card") or "")
        key = (scope, card)
        group = group_index.get(key)
        if group is None:
            group = {
                "group_id": f"{scope}:{card}",
                "scope": scope,
                "card": card,
                "label": _control_registry_group_label(scope, card),
                "item_count": 0,
                "enabled_count": 0,
                "disabled_count": 0,
                "items": [],
            }
            group_index[key] = group
            groups.append(group)
        group_items = group["items"] if isinstance(group["items"], list) else []
        group_items.append(item)
        group["item_count"] = int(group["item_count"]) + 1
        if item.get("enabled") is True:
            group["enabled_count"] = int(group["enabled_count"]) + 1
        else:
            group["disabled_count"] = int(group["disabled_count"]) + 1
    return groups


def _control_registry_group_label(scope: str, card: str) -> str:
    labels = {
        ("leader", "leader_card"): "Leader",
        ("provider", "provider_health"): "Provider",
        ("policy", "control_mode_card"): "Control mode",
        ("terminal_session", "terminal_session_card"): "Terminal session",
        ("runtime", "runtime_card"): "Runtime",
        ("role", "role_card"): "Roles",
        ("inbox", "inbox_card"): "Inbox",
        ("inbox", "leader_inbox_card"): "Leader inbox",
        ("operator", "operator_card"): "Operator",
    }
    return labels.get((scope, card), card.replace("_", " ").strip().title() or scope.replace("_", " ").title())


def control_registry_item_id(item: dict[str, object]) -> str:
    scope = _control_registry_id_part(item.get("scope"))
    card = _control_registry_id_part(item.get("card"))
    kind = _control_registry_id_part(item.get("kind"))
    agent_id = item.get("agent_id")
    agent = _control_registry_id_part(agent_id) if agent_id is not None else "global"
    fingerprint_fields = ("scope", "card", "kind", "agent_id", "label", "command")
    fingerprint = "\x1f".join(str(item.get(field, "")) for field in fingerprint_fields)
    digest = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:10]
    return f"{scope}:{card}:{kind}:{agent}:{digest}"


def _control_registry_id_part(value: object) -> str:
    text = str(value or "none").lower()
    sanitized = "".join(
        char if ("a" <= char <= "z" or "0" <= char <= "9" or char in "._-") else "_"
        for char in text
    )
    return sanitized.strip("_") or "none"


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
        "provider_switch": "set_provider",
        "skill_import_preview": "skill_import_preview",
        "skill_load_preview": "skill_load_preview",
        "skill_create_preview": "skill_create_preview",
        "memory_apply_preview": "memory_apply_preview",
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


def loop_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "loop_once_command": "agentdeck loop once",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "loop_once_response_fields": list(LOOP_ONCE_RESPONSE_FIELDS),
        "continue_card_fields": list(CONTINUE_CARD_FIELDS),
        "control_fields": list(WORKBENCH_CONTROL_MODE_CONTROL_FIELDS),
        "project_view_schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "project_view_contract": "agentdeck contract project-view",
        "continue_contract": "agentdeck contract continue",
        "workbench_contract": "agentdeck contract workbench",
    }


def loop_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = loop_contract_payload(contract_path)
    if include_example:
        example = loop_once_example()
        payload["example"] = True
        payload["example_loop_once_response_fields"] = list(example)
        payload["example_continue_card_fields"] = list(example["continue_card"])
        payload["example_control_fields"] = list(example["controls"][0])
        payload["example_loop_once"] = example
    return payload


def release_example() -> dict[str, object]:
    return {
        "ok": True,
        "mode": "release",
        "requires_explicit_user": True,
        "safety": "explicit_user",
        "release": {
            "release_id": "rel_example",
            "round": 1,
            "status": "released",
            "review_gate_status": "ready",
            "artifact_count": 1,
            "review_reply_count": 2,
            "code_reviewer_id": "reviewer",
            "round_reviewer_id": "coder",
            "code_review_reply_id": "rep_example",
            "round_review_reply_id": "rep_round_example",
            "created_at": "2026-07-04T00:00:03+00:00",
        },
        "release_count": 1,
        "next_command": "agentdeck workbench",
        "next_round_command": "agentdeck leader plan --task <goal>",
        "trace_commands": [
            "agentdeck trace --id rep_example",
            "agentdeck trace --id rep_round_example",
        ],
        "controls": [
            {
                "kind": "inspect",
                "label": "Inspect workbench",
                "command": "agentdeck workbench",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "trace_code_review",
                "label": "Trace code review",
                "command": "agentdeck trace --id rep_example",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "trace_round_review",
                "label": "Trace round review",
                "command": "agentdeck trace --id rep_round_example",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "next_round",
                "label": "Plan next round",
                "command": "agentdeck leader plan --task <goal>",
                "safety": "plan_only",
                "enabled": False,
                "blocker": "requires goal text",
            },
        ],
    }


def release_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "release_command": "agentdeck release --confirm",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "response_fields": list(RELEASE_RESPONSE_FIELDS),
        "release_record_fields": list(RELEASE_RECORD_FIELDS),
        "release_item_fields": list(PROJECT_VIEW_RELEASE_ITEM_FIELDS),
        "control_fields": list(WORKBENCH_CONTROL_MODE_CONTROL_FIELDS),
        "project_view_contract": "agentdeck contract project-view",
        "workbench_contract": "agentdeck contract workbench",
        "trace_contract": "agentdeck contract trace",
    }


def release_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = release_contract_payload(contract_path)
    if include_example:
        example = release_example()
        payload["example"] = True
        payload["example_response_fields"] = list(example)
        payload["example_release_record_fields"] = list(example["release"])
        payload["example_control_fields"] = list(example["controls"][0])
        payload["example_release"] = example
    return payload


def doctor_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "doctor_command": "agentdeck doctor",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "response_fields": list(DOCTOR_RESPONSE_FIELDS),
        "configured_leader_fields": list(DOCTOR_CONFIGURED_LEADER_FIELDS),
        "leader_backend_fields": list(LEADER_BACKEND_FIELDS),
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
        payload["example_leader_backend_fields"] = list(example["configured_leader"]["leader_backend"])
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


def demo_golden_example() -> dict[str, object]:
    return {
        "ok": True,
        "mode": "golden_demo",
        "demo_name": "golden",
        "summary": "Read-only guide for running the AgentDeck golden demo.",
        "current_status": "provider_setup_required",
        "next_command": "agentdeck doctor",
        "recommended_task": (
            "Add a tiny read-only dashboard or CLI affordance, update tests, "
            "and report files changed plus verification."
        ),
        "steps": [
            {
                "step_id": "doctor",
                "title": "Inspect environment",
                "status": "ready",
                "command": "agentdeck doctor",
                "enabled": True,
                "blocker": None,
                "safety": "inspect",
                "description": "Check tmux and configured Leader provider readiness.",
                "checks": ["tmux available", "configured Leader readiness is visible"],
            },
            {
                "step_id": "plan",
                "title": "Create the demo plan",
                "status": "waiting_for_input",
                "command": "agentdeck leader plan --task <task>",
                "enabled": False,
                "blocker": "requires task text",
                "safety": "explicit_user",
                "description": "Ask the Leader to create a plan without dispatching workers.",
                "checks": ["plan is recorded", "approval remains explicit"],
            },
        ],
        "inspection_commands": [
            "agentdeck status",
            "agentdeck workbench",
            "agentdeck dashboard",
            "agentdeck tui",
        ],
        "safety": "inspect",
        "source_command": "agentdeck demo golden",
    }


def validate_demo_golden_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in DEMO_GOLDEN_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"{field} is required")
    if payload.get("mode") != "golden_demo":
        errors.append("mode must be golden_demo")
    if payload.get("demo_name") != "golden":
        errors.append("demo_name must be golden")
    if payload.get("safety") != "inspect":
        errors.append("safety must be inspect")
    if not isinstance(payload.get("steps"), list):
        errors.append("steps must be a list")
    else:
        for index, step in enumerate(payload["steps"]):
            if not isinstance(step, dict):
                errors.append(f"steps[{index}] must be an object")
                continue
            for field in DEMO_GOLDEN_STEP_FIELDS:
                if field not in step:
                    errors.append(f"steps[{index}].{field} is required")
            if step.get("status") not in DEMO_GOLDEN_STEP_STATUSES:
                errors.append(f"steps[{index}].status is invalid")
            if step.get("safety") not in DEMO_GOLDEN_STEP_SAFETIES:
                errors.append(f"steps[{index}].safety is invalid")
            if not isinstance(step.get("enabled"), bool):
                errors.append(f"steps[{index}].enabled must be bool")
            if step.get("enabled") is False and step.get("blocker") in {None, ""}:
                errors.append(f"steps[{index}].blocker is required when disabled")
            if not isinstance(step.get("checks"), list):
                errors.append(f"steps[{index}].checks must be a list")
    if not isinstance(payload.get("inspection_commands"), list):
        errors.append("inspection_commands must be a list")
    return {"ok": not errors, "errors": errors}


def demo_contract_payload(contract_path: Path) -> dict[str, object]:
    example = demo_golden_example()
    return {
        "name": "demo",
        "golden_demo_command": "agentdeck demo golden",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "response_fields": list(DEMO_GOLDEN_RESPONSE_FIELDS),
        "step_fields": list(DEMO_GOLDEN_STEP_FIELDS),
        "example_response_fields": list(example.keys()),
        "example_step_fields": list(example["steps"][0].keys()),
    }


def demo_contract_response(contract_path: Path, *, include_example: bool = False) -> dict[str, object]:
    payload = demo_contract_payload(contract_path)
    if include_example:
        payload["example_golden_demo"] = demo_golden_example()
    return payload


def run_loop_example() -> dict[str, object]:
    return {
        "ok": True,
        "mode": "run_loop",
        "plan_id": "pln_example",
        "requires_explicit_user": True,
        "safety": "delegated",
        "auto_approved": 1,
        "dispatched": [
            {
                "approval_id": "apv_example",
                "agent_id": "planner",
                "message_id": "msg_example",
                "trace_command": "agentdeck trace --id msg_example",
            }
        ],
        "blocked": [],
        "skipped": [
            {"approval_id": "apv_other", "agent_id": "reviewer", "reason": "agent not in allowlist"}
        ],
        "stopped_reason": "waiting_for_reply",
        "next_command": "agentdeck capture-reply --agent planner --message-id msg_example",
        "policy": {"allowed_agents": ["planner"], "max_approvals": 3},
    }


def run_loop_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "run_loop_command": "agentdeck run-loop --plan-id <id> --confirm",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "run_loop_response_fields": list(RUN_LOOP_RESPONSE_FIELDS),
        "stop_reasons": list(RUN_LOOP_STOP_REASONS),
        "loop_contract": "agentdeck contract loop",
        "approvals_contract": "agentdeck contract approvals",
        "project_view_contract": "agentdeck contract project-view",
    }


def run_loop_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = run_loop_contract_payload(contract_path)
    if include_example:
        example = run_loop_example()
        payload["example"] = True
        payload["example_run_loop_response_fields"] = list(example)
        payload["example_run_loop"] = example
    return payload


def validate_run_loop_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in RUN_LOOP_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing run_loop field: {field}")
    if payload.get("mode") != "run_loop":
        errors.append(f"run_loop.mode must be run_loop, got {payload.get('mode')}")
    if payload.get("safety") != "delegated":
        errors.append("run_loop.safety must be delegated")
    if payload.get("requires_explicit_user") is not True:
        errors.append("run_loop.requires_explicit_user must be true")
    if payload.get("stopped_reason") not in RUN_LOOP_STOP_REASONS:
        errors.append(f"run_loop.stopped_reason must be one of {RUN_LOOP_STOP_REASONS}")
    if not isinstance(payload.get("next_command"), str) or not payload.get("next_command"):
        errors.append("run_loop.next_command must be a non-empty string")
    for list_field in ("dispatched", "blocked", "skipped"):
        if not isinstance(payload.get(list_field), list):
            errors.append(f"run_loop.{list_field} must be a list")
    return {"ok": not errors, "errors": errors}


def workflow_preview_example() -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "ok": True,
        "mode": "workflow_preview",
        "safety": "inspect",
        "plan_id": "pln_example",
        "plan_hash": "sha256:" + "a" * 64,
        "timeout_seconds": 300,
        "step_count": 2,
        "steps": [
            {
                "step": 1,
                "agent_id": "planner",
                "role": "planning",
                "task": "Prepare evidence",
                "task_hash": "sha256:" + "b" * 64,
                "runtime_status": "running",
                "pane_id": "%1",
                "ready": True,
                "blocker": None,
            },
            {
                "step": 2,
                "agent_id": "reviewer",
                "role": "review",
                "task": "Review evidence",
                "task_hash": "sha256:" + "c" * 64,
                "runtime_status": "running",
                "pane_id": "%2",
                "ready": True,
                "blocker": None,
            },
        ],
        "blockers": [],
        "can_run": True,
        "confirm_command": (
            "agentdeck workflow run --plan-id pln_example --timeout 300 --confirm"
        ),
        "controls": [
            {
                "kind": "execute",
                "label": "Run workflow",
                "command": (
                    "agentdeck workflow run --plan-id pln_example --timeout 300 --confirm"
                ),
                "safety": "delegated",
                "enabled": True,
                "blocker": None,
            }
        ],
    }


def workflow_status_example() -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "ok": True,
        "mode": "workflow_status",
        "safety": "inspect",
        "run_id": "wfr_example",
        "plan_id": "pln_example",
        "plan_hash": "sha256:" + "a" * 64,
        "status": "stopped",
        "current_step": 2,
        "step_count": 2,
        "timeout_seconds": 300,
        "turns": [
            {
                "step": 1,
                "agent_id": "planner",
                "handoff_token": "wfr_example_step_1",
                "status": "completed",
                "message_id": "msg_example",
                "job_id": "job_example",
                "reply_id": "rep_example",
                "handoff": {
                    "summary": "Evidence prepared",
                    "verification": "pytest",
                },
                "artifact_paths": ["docs/result.md"],
                "trace_command": "agentdeck trace --id rep_example",
                "started_at": "2026-07-10T00:00:00+00:00",
                "completed_at": "2026-07-10T00:00:01+00:00",
            }
        ],
        "stop_reason": "timed_out",
        "created_at": "2026-07-10T00:00:00+00:00",
        "updated_at": "2026-07-10T00:05:00+00:00",
        "completed_at": None,
        "can_resume": True,
        "status_command": "agentdeck workflow status --run-id wfr_example",
        "resume_command": "agentdeck workflow resume --run-id wfr_example --confirm",
        "controls": [
            {
                "kind": "execute",
                "label": "Resume workflow",
                "command": "agentdeck workflow resume --run-id wfr_example --confirm",
                "safety": "delegated",
                "enabled": True,
                "blocker": None,
            }
        ],
    }


def workflow_run_example() -> dict[str, object]:
    payload = deepcopy(workflow_status_example())
    payload.update(
        {
            "mode": "workflow_run",
            "safety": "delegated",
            "status": "completed",
            "current_step": 3,
            "stop_reason": None,
            "completed_at": "2026-07-10T00:00:02+00:00",
            "can_resume": False,
            "requires_explicit_user": True,
            "confirmed": True,
        }
    )
    return payload


def validate_workflow_preview_contract(
    payload: dict[str, object],
) -> dict[str, object]:
    errors: list[str] = []
    for field in WORKFLOW_PREVIEW_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing workflow_preview field: {field}")
    if payload.get("mode") != "workflow_preview":
        errors.append("workflow_preview.mode must be workflow_preview")
    if payload.get("safety") != "inspect":
        errors.append("workflow_preview.safety must be inspect")
    if not isinstance(payload.get("timeout_seconds"), int) or payload.get(
        "timeout_seconds", 0
    ) <= 0:
        errors.append("workflow_preview.timeout_seconds must be a positive integer")
    steps = payload.get("steps")
    if not isinstance(steps, list):
        errors.append("workflow_preview.steps must be a list")
        steps = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append(f"workflow_preview.steps[{index}] must be an object")
            continue
        missing = [field for field in WORKFLOW_STEP_FIELDS if field not in step]
        if missing:
            errors.append(
                f"workflow_preview.steps[{index}] missing fields: {', '.join(missing)}"
            )
    blockers = payload.get("blockers")
    if not isinstance(blockers, list):
        errors.append("workflow_preview.blockers must be a list")
        blockers = []
    if payload.get("step_count") != len(steps):
        errors.append("workflow_preview.step_count must equal len(steps)")
    if blockers and payload.get("can_run") is not False:
        errors.append("workflow_preview.can_run must be false when blockers exist")
    if not blockers and payload.get("can_run") is not True:
        errors.append("workflow_preview.can_run must be true when blockers are empty")
    if not isinstance(payload.get("confirm_command"), str) or "--confirm" not in str(
        payload.get("confirm_command")
    ):
        errors.append("workflow_preview.confirm_command must require --confirm")
    if not isinstance(payload.get("controls"), list):
        errors.append("workflow_preview.controls must be a list")
    return {"ok": not errors, "errors": errors}


def validate_workflow_status_contract(
    payload: dict[str, object],
) -> dict[str, object]:
    errors: list[str] = []
    for field in WORKFLOW_STATUS_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing workflow_status field: {field}")
    if payload.get("mode") != "workflow_status":
        errors.append("workflow_status.mode must be workflow_status")
    if payload.get("safety") != "inspect":
        errors.append("workflow_status.safety must be inspect")
    if payload.get("status") not in WORKFLOW_STATUSES:
        errors.append(f"workflow_status.status must be one of {WORKFLOW_STATUSES}")
    if not isinstance(payload.get("timeout_seconds"), int) or payload.get(
        "timeout_seconds", 0
    ) <= 0:
        errors.append("workflow_status.timeout_seconds must be a positive integer")
    turns = payload.get("turns")
    if not isinstance(turns, list):
        errors.append("workflow_status.turns must be a list")
        turns = []
    for index, turn in enumerate(turns):
        if not isinstance(turn, dict):
            errors.append(f"workflow_status.turns[{index}] must be an object")
            continue
        missing = [field for field in WORKFLOW_TURN_FIELDS if field not in turn]
        if missing:
            errors.append(
                f"workflow_status.turns[{index}] missing fields: {', '.join(missing)}"
            )
        if turn.get("status") not in WORKFLOW_TURN_STATUSES:
            errors.append(
                f"workflow_status.turns[{index}].status must be a workflow turn status"
            )
    can_resume = payload.get("can_resume")
    expected_can_resume = payload.get("status") in {"stopped", "interrupted"}
    if can_resume is not expected_can_resume:
        errors.append(
            "workflow_status.can_resume must be true only for stopped/interrupted runs"
        )
    if can_resume and "--confirm" not in str(payload.get("resume_command") or ""):
        errors.append("workflow_status.resume_command must require --confirm")
    if not isinstance(payload.get("controls"), list):
        errors.append("workflow_status.controls must be a list")
    return {"ok": not errors, "errors": errors}


def validate_workflow_run_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in WORKFLOW_RUN_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing workflow_run field: {field}")
    if payload.get("mode") not in {"workflow_run", "workflow_resume"}:
        errors.append("workflow_run.mode must be workflow_run or workflow_resume")
    if payload.get("safety") != "delegated":
        errors.append("workflow_run.safety must be delegated")
    if payload.get("requires_explicit_user") is not True:
        errors.append("workflow_run.requires_explicit_user must be true")
    if payload.get("confirmed") is not True:
        errors.append("workflow_run.confirmed must be true")
    status_projection = dict(payload)
    status_projection["mode"] = "workflow_status"
    status_projection["safety"] = "inspect"
    status_validation = validate_workflow_status_contract(status_projection)
    errors.extend(str(item) for item in status_validation["errors"])
    return {"ok": not errors, "errors": errors}


def workflow_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "name": "workflow",
        "preview_command": "agentdeck workflow preview --plan-id <id>",
        "run_command": "agentdeck workflow run --plan-id <id> --confirm",
        "status_command": "agentdeck workflow status --run-id <id>",
        "resume_command": "agentdeck workflow resume --run-id <id> --confirm",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "preview_response_fields": list(WORKFLOW_PREVIEW_RESPONSE_FIELDS),
        "step_fields": list(WORKFLOW_STEP_FIELDS),
        "status_response_fields": list(WORKFLOW_STATUS_RESPONSE_FIELDS),
        "run_response_fields": list(WORKFLOW_RUN_RESPONSE_FIELDS),
        "turn_fields": list(WORKFLOW_TURN_FIELDS),
        "statuses": list(WORKFLOW_STATUSES),
        "turn_statuses": list(WORKFLOW_TURN_STATUSES),
        "stop_reasons": list(WORKFLOW_STOP_REASONS),
        "run_loop_contract": "agentdeck contract run-loop",
    }


def workflow_contract_response(
    contract_path: Path, include_example: bool = False
) -> dict[str, object]:
    payload = workflow_contract_payload(contract_path)
    if include_example:
        preview = workflow_preview_example()
        status = workflow_status_example()
        run = workflow_run_example()
        payload.update(
            {
                "example": True,
                "example_preview": preview,
                "example_status": status,
                "example_run": run,
            }
        )
    return payload


def _mission_leader_backend_example() -> dict[str, object]:
    return {
        "agent_id": "leader",
        "provider": "fake",
        "model": "mission-planner",
        "provider_backend": "local",
        "provider_transport": "local",
        "reasoning_backend": "local-fake",
        "runtime_kind": "logical_leader",
        "pane_backed": False,
        "pane_id": None,
        "approval_required": True,
        "dispatch_ready": False,
    }


def _mission_selected_agents_example() -> list[dict[str, object]]:
    return [
        {
            "agent_id": "planner",
            "provider": "codex-cli",
            "role": "planning",
            "workspace_mode": "shared",
            "runtime_status": "configured",
            "effective_model": "gpt-5.5",
            "model_source": "configured_command",
        },
        {
            "agent_id": "reviewer",
            "provider": "claude-cli",
            "role": "review",
            "workspace_mode": "shared",
            "runtime_status": "running",
            "effective_model": None,
            "model_source": "provider_default",
        },
    ]


def _mission_plan_example() -> dict[str, object]:
    steps = []
    for step in range(1, 9):
        agent_id = "planner" if step % 2 else "reviewer"
        steps.append(
            {
                "step": step,
                "agent_id": agent_id,
                "role": "planning" if agent_id == "planner" else "review",
                "task": f"Complete fixed serial round {step}",
            }
        )
    return {
        "goal": "Complete an eight-round Codex and Claude handoff",
        "summary": "A fixed serial plan with no dynamic or parallel steps.",
        "steps": steps,
    }


def _mission_control(
    kind: str,
    label: str,
    command: str,
    safety: str,
    *,
    enabled: bool = True,
    blocker: str | None = None,
) -> dict[str, object]:
    return {
        "kind": kind,
        "label": label,
        "command": command,
        "safety": safety,
        "enabled": enabled,
        "blocker": blocker,
    }


def mission_example(kind: str) -> dict[str, object]:
    mission_id = "mis_deadbeefcafe"
    commands = mission_commands(mission_id)
    selected_agents = _mission_selected_agents_example()
    if kind == "preview":
        plan = _mission_plan_example()
        return {
            "schema_version": MISSION_SCHEMA_VERSION,
            "ok": True,
            "mode": "mission_preview",
            "mission_id": mission_id,
            "status": "pending_confirmation",
            "user_message": "让 Codex 和 Claude 一人一句接龙，共8轮",
            "provider": "fake",
            "model": "mission-planner",
            "leader_backend": _mission_leader_backend_example(),
            "plan_id": "pln_deadbeefcafe",
            "plan_hash": "sha256:" + "a" * 64,
            "plan": plan,
            "selected_agents": selected_agents,
            "startup_actions": [
                {
                    "agent_id": "planner",
                    "action": "spawn",
                    "runtime_status": "configured",
                    "effective_model": "gpt-5.5",
                    "model_source": "configured_command",
                },
                {
                    "agent_id": "reviewer",
                    "action": "reuse",
                    "runtime_status": "running",
                    "effective_model": None,
                    "model_source": "provider_default",
                },
            ],
            "step_count": len(plan["steps"]),
            "timeout_seconds": 300,
            "can_start": True,
            "blockers": [],
            "confirmation_command": commands["confirmation_command"],
            "status_command": commands["status_command"],
            "workbench_command": "agentdeck workbench",
            "controls": [
                _mission_control(
                    "execute", "Run mission", commands["confirmation_command"], "delegated"
                ),
                _mission_control(
                    "inspect", "Inspect mission", commands["status_command"], "inspect"
                ),
                _mission_control(
                    "inspect", "Open workbench", "agentdeck workbench", "inspect"
                ),
            ],
            "safety": "inspect",
            "requires_explicit_user": True,
        }
    if kind not in {"status", "run"}:
        raise ValueError("mission example kind must be preview, status, or run")
    payload: dict[str, object] = {
        "schema_version": MISSION_SCHEMA_VERSION,
        "ok": True,
        "mode": "mission_status",
        "mission_id": mission_id,
        "status": "stopped",
        "user_message": "让 Codex 和 Claude 一人一句接龙，共8轮",
        "plan_id": "pln_deadbeefcafe",
        "plan_hash": "sha256:" + "a" * 64,
        "workflow_run_id": "wfr_deadbeefcafe",
        "current_step": 4,
        "step_count": 8,
        "timeout_seconds": 300,
        "selected_agents": selected_agents,
        "blockers": [],
        "stop_reason": "timed_out",
        "created_at": "2026-07-11T00:00:00+00:00",
        "updated_at": "2026-07-11T00:05:00+00:00",
        "confirmed_at": "2026-07-11T00:00:01+00:00",
        "completed_at": None,
        "can_resume": True,
        "status_command": commands["status_command"],
        "resume_command": commands["resume_command"],
        "attach_command": "tmux attach -t agentdeck",
        "workbench_command": "agentdeck workbench",
        "controls": [
            _mission_control(
                "execute", "Resume mission", commands["resume_command"], "delegated"
            ),
            _mission_control(
                "inspect", "Inspect mission", commands["status_command"], "inspect"
            ),
            _mission_control(
                "inspect", "Attach terminal", "tmux attach -t agentdeck", "inspect"
            ),
            _mission_control(
                "inspect", "Open workbench", "agentdeck workbench", "inspect"
            ),
        ],
        "safety": "inspect",
        "requires_explicit_user": True,
    }
    if kind == "run":
        summaries = (
            "赵钱孙李", "周吴郑王", "冯陈褚卫", "蒋沈韩杨",
            "朱秦尤许", "何吕施张", "孔曹严华", "金魏陶姜",
        )
        payload.update(
            {
                "mode": "mission_run",
                "status": "completed",
                "current_step": 8,
                "stop_reason": None,
                "updated_at": "2026-07-11T00:08:00+00:00",
                "completed_at": "2026-07-11T00:08:00+00:00",
                "can_resume": False,
                "controls": [
                    _mission_control(
                        "execute",
                        "Resume mission",
                        commands["resume_command"],
                        "delegated",
                        enabled=False,
                        blocker="completed missions cannot resume",
                    ),
                    _mission_control(
                        "inspect", "Inspect mission", commands["status_command"], "inspect"
                    ),
                    _mission_control(
                        "inspect", "Attach terminal", "tmux attach -t agentdeck", "inspect"
                    ),
                    _mission_control(
                        "inspect", "Open workbench", "agentdeck workbench", "inspect"
                    ),
                ],
                "safety": "delegated",
                "confirmed": True,
                "turns": [
                    {
                        "step": step,
                        "agent_id": "planner" if step % 2 else "reviewer",
                        "status": "completed",
                        "handoff": {
                            "step": step,
                            "agent_id": "planner" if step % 2 else "reviewer",
                            "status": "completed",
                            "summary": summaries[step - 1],
                            "verification": "deterministic example",
                            "risks": "none",
                            "next_steps": "continue" if step < 8 else "done",
                            "artifact_paths": [],
                            "trace_command": f"agentdeck trace --id rep_{step:012x}",
                        },
                    }
                    for step in range(1, 9)
                ],
            }
        )
    return payload


def _mission_exact_fields(
    errors: list[str], prefix: str, value: dict[str, object], fields: tuple[str, ...]
) -> None:
    missing = [field for field in fields if field not in value]
    extra = [field for field in value if field not in fields]
    if missing:
        errors.append(f"{prefix} missing fields: {', '.join(missing)}")
    if extra:
        errors.append(f"{prefix} has unknown fields")


def _mission_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _mission_exact_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _mission_optional_nonempty_string(value: object) -> bool:
    return value is None or _mission_nonempty_string(value)


def _mission_valid_plan_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"pln_[0-9a-f]{12}", value) is not None


def _mission_valid_plan_hash(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None


def _validate_mission_nonempty_strings(
    errors: list[str], prefix: str, value: dict[str, object], fields: tuple[str, ...]
) -> None:
    for field in fields:
        if not _mission_nonempty_string(value.get(field)):
            errors.append(f"{prefix}.{field} must be a non-empty string")


def _mission_worker_schema(kind: str) -> tuple[tuple[str, ...], frozenset[str] | None]:
    if kind == "selected_agents":
        return MISSION_SELECTED_AGENT_FIELDS, None
    if kind == "startup_actions":
        return MISSION_STARTUP_ACTION_FIELDS, frozenset({"reuse", "spawn"})
    raise ValueError(f"unknown mission worker row kind: {kind}")


def _validate_mission_worker_rows(
    errors: list[str], prefix: str, value: object, *, kind: str
) -> list[dict[str, object]]:
    fields, allowed_actions = _mission_worker_schema(kind)
    if not isinstance(value, list):
        errors.append(f"{prefix} must be a list")
        return []
    rows: list[dict[str, object]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"{prefix}[{index}] must be an object")
            continue
        _mission_exact_fields(errors, f"{prefix}[{index}]", item, fields)
        for field in fields:
            field_value = item.get(field)
            if field == "effective_model":
                if not _mission_optional_nonempty_string(field_value):
                    errors.append(f"{prefix}[{index}].{field} must be a non-empty string or null")
            elif not _mission_nonempty_string(field_value):
                errors.append(f"{prefix}[{index}].{field} must be a non-empty string")
        action = item.get("action")
        if allowed_actions is not None and (
            not isinstance(action, str) or action not in allowed_actions
        ):
            errors.append(f"{prefix}[{index}].action must be reuse or spawn")
        rows.append(item)
    return rows


def _validate_mission_controls(
    errors: list[str], prefix: str, payload: dict[str, object], *, preview: bool
) -> None:
    controls = payload.get("controls")
    if not isinstance(controls, list):
        errors.append(f"{prefix}.controls must be a list")
        return
    expected_commands = {
        str(payload.get("status_command")): ("inspect", "inspect", True),
        str(payload.get("workbench_command")): ("inspect", "inspect", True),
    }
    if preview:
        expected_commands[str(payload.get("confirmation_command"))] = (
            "execute",
            "delegated",
            payload.get("can_start") is True,
        )
    else:
        expected_commands[str(payload.get("attach_command"))] = (
            "inspect",
            "inspect",
            True,
        )
        expected_commands[str(payload.get("resume_command"))] = (
            "execute",
            "delegated",
            payload.get("can_resume") is True,
        )
    seen: set[str] = set()
    for index, control in enumerate(controls):
        if not isinstance(control, dict):
            errors.append(f"{prefix}.controls[{index}] must be an object")
            continue
        _mission_exact_fields(
            errors, f"{prefix}.controls[{index}]", control, MISSION_CONTROL_FIELDS
        )
        _validate_mission_nonempty_strings(
            errors,
            f"{prefix}.controls[{index}]",
            control,
            ("kind", "label", "command", "safety"),
        )
        if not isinstance(control.get("enabled"), bool):
            errors.append(f"{prefix}.controls[{index}].enabled must be a boolean")
        if not _mission_optional_nonempty_string(control.get("blocker")):
            errors.append(
                f"{prefix}.controls[{index}].blocker must be a non-empty string or null"
            )
        command = control.get("command")
        if not isinstance(command, str) or command not in expected_commands:
            errors.append(f"{prefix}.controls[{index}].command must be a declared mission command")
            continue
        seen.add(command)
        expected_kind, expected_safety, expected_enabled = expected_commands[command]
        if control.get("kind") != expected_kind:
            errors.append(f"{prefix}.controls[{index}].kind does not match command")
        if control.get("safety") != expected_safety:
            errors.append(f"{prefix}.controls[{index}].safety does not match command")
        if control.get("enabled") is not expected_enabled:
            errors.append(f"{prefix}.controls[{index}].enabled conflicts with blockers/status")
        if expected_enabled and control.get("blocker") is not None:
            errors.append(f"{prefix}.controls[{index}].blocker must be null when enabled")
        if not expected_enabled and not _mission_nonempty_string(control.get("blocker")):
            errors.append(f"{prefix}.controls[{index}].blocker must explain disabled control")
    if seen != set(expected_commands):
        errors.append(f"{prefix}.controls must expose every declared mission command")


def _validate_mission_identity_and_commands(
    errors: list[str], prefix: str, payload: dict[str, object], *, preview: bool
) -> None:
    mission_id = payload.get("mission_id")
    if not is_canonical_mission_id(mission_id):
        errors.append(f"{prefix}.mission_id must be canonical")
        return
    commands = mission_commands(str(mission_id))
    expected = {"status_command": commands["status_command"]}
    if preview:
        expected["confirmation_command"] = commands["confirmation_command"]
    else:
        expected["resume_command"] = commands["resume_command"]
    for field, command in expected.items():
        if payload.get(field) != command:
            errors.append(f"{prefix}.{field} must match mission_id")


def _validate_mission_plan_identity(
    errors: list[str], prefix: str, payload: dict[str, object]
) -> None:
    if not _mission_valid_plan_id(payload.get("plan_id")):
        errors.append(f"{prefix}.plan_id must match pln_<12 lowercase hex>")
    if not _mission_valid_plan_hash(payload.get("plan_hash")):
        errors.append(f"{prefix}.plan_hash must be sha256:<64 lowercase hex>")


def _validate_mission_blockers(
    errors: list[str], prefix: str, value: object
) -> list[str]:
    if not isinstance(value, list) or any(
        not _mission_nonempty_string(item) for item in value
    ):
        errors.append(f"{prefix}.blockers must be a list of non-empty strings")
        return []
    return value


def _validate_mission_worker_provenance(
    errors: list[str],
    selected: list[dict[str, object]],
    startup: list[dict[str, object]],
) -> None:
    if len(selected) != len(startup):
        errors.append("mission_preview.startup_actions must match selected_agents")
        return
    shared_fields = ("agent_id", "runtime_status", "effective_model", "model_source")
    for index, (selected_row, startup_row) in enumerate(zip(selected, startup)):
        for field in shared_fields:
            if startup_row.get(field) != selected_row.get(field):
                errors.append(
                    f"mission_preview.startup_actions[{index}].{field} must match selected_agents"
                )


def validate_mission_preview_contract(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {"ok": False, "errors": ["mission_preview must be an object"]}
    errors: list[str] = []
    _mission_exact_fields(errors, "mission_preview", payload, MISSION_PREVIEW_RESPONSE_FIELDS)
    if payload.get("schema_version") != MISSION_SCHEMA_VERSION:
        errors.append("mission_preview.schema_version must be mission/v1")
    if payload.get("ok") is not True or payload.get("mode") != "mission_preview":
        errors.append("mission_preview mode must be a successful mission_preview")
    if payload.get("status") != "pending_confirmation":
        errors.append("mission_preview.status must be pending_confirmation")
    if payload.get("safety") != "inspect" or payload.get("requires_explicit_user") is not True:
        errors.append("mission_preview must be inspect-only and require an explicit user")
    _validate_mission_nonempty_strings(
        errors,
        "mission_preview",
        payload,
        (
            "user_message", "provider", "model", "plan_id", "plan_hash",
            "confirmation_command", "status_command", "workbench_command",
        ),
    )
    if not isinstance(payload.get("can_start"), bool):
        errors.append("mission_preview.can_start must be a boolean")
    _validate_mission_plan_identity(errors, "mission_preview", payload)
    _validate_mission_identity_and_commands(errors, "mission_preview", payload, preview=True)
    if not _mission_exact_int(payload.get("timeout_seconds")) or payload.get("timeout_seconds", 0) <= 0:
        errors.append("mission_preview.timeout_seconds must be a positive integer")
    if not _mission_exact_int(payload.get("step_count")) or payload.get("step_count", 0) < 2:
        errors.append("mission_preview.step_count must be an integer of at least two")
    selected = _validate_mission_worker_rows(
        errors,
        "mission_preview.selected_agents",
        payload.get("selected_agents"),
        kind="selected_agents",
    )
    startup = _validate_mission_worker_rows(
        errors,
        "mission_preview.startup_actions",
        payload.get("startup_actions"),
        kind="startup_actions",
    )
    selected_ids = [str(item.get("agent_id")) for item in selected]
    startup_ids = [str(item.get("agent_id")) for item in startup]
    if len(selected_ids) < 2 or len(set(selected_ids)) != len(selected_ids):
        errors.append("mission_preview.selected_agents must contain at least two unique agents")
    if startup_ids != selected_ids:
        errors.append("mission_preview.startup_actions must match selected_agents")
    _validate_mission_worker_provenance(errors, selected, startup)
    plan = payload.get("plan")
    if not isinstance(plan, dict):
        errors.append("mission_preview.plan must be an object")
        plan = {}
    else:
        _mission_exact_fields(errors, "mission_preview.plan", plan, MISSION_PLAN_FIELDS)
        _validate_mission_nonempty_strings(
            errors, "mission_preview.plan", plan, ("goal", "summary")
        )
        steps = plan.get("steps")
        if isinstance(steps, list):
            for index, step in enumerate(steps):
                if isinstance(step, dict):
                    _mission_exact_fields(
                        errors, f"mission_preview.plan.steps[{index}]", step, MISSION_PLAN_STEP_FIELDS
                    )
                    if not _mission_exact_int(step.get("step")):
                        errors.append(
                            f"mission_preview.plan.steps[{index}].step must be an integer"
                        )
                    _validate_mission_nonempty_strings(
                        errors,
                        f"mission_preview.plan.steps[{index}]",
                        step,
                        ("agent_id", "role", "task"),
                    )
                else:
                    errors.append(f"mission_preview.plan.steps[{index}] must be an object")
        try:
            timeout = payload.get("timeout_seconds")
            validate_mission_plan(plan, selected_ids, timeout if _mission_exact_int(timeout) else 0)
        except (TypeError, ValueError):
            errors.append("mission_preview.plan is invalid")
        selected_roles = {
            row["agent_id"]: row.get("role")
            for row in selected
            if _mission_nonempty_string(row.get("agent_id"))
        }
        if isinstance(steps, list):
            for index, step in enumerate(steps):
                step_agent_id = step.get("agent_id") if isinstance(step, dict) else None
                if (
                    isinstance(step, dict)
                    and _mission_nonempty_string(step_agent_id)
                    and step_agent_id in selected_roles
                    and step.get("role") != selected_roles.get(step_agent_id)
                ):
                    errors.append(
                        f"mission_preview.plan.steps[{index}].role must match selected agent role"
                    )
    steps = plan.get("steps") if isinstance(plan, dict) else None
    if not isinstance(steps, list) or payload.get("step_count") != len(steps):
        errors.append("mission_preview.step_count must equal len(plan.steps)")
    blockers = _validate_mission_blockers(errors, "mission_preview", payload.get("blockers"))
    can_start = payload.get("can_start")
    if can_start is not (not blockers):
        errors.append("mission_preview.can_start must equal not blockers")
    backend = payload.get("leader_backend")
    if isinstance(backend, dict):
        _validate_leader_backend(errors, "mission_preview", backend)
        if backend.get("provider") != payload.get("provider") or backend.get("model") != payload.get("model"):
            errors.append("mission_preview.leader_backend must match provider and model")
        elif backend != leader_backend_identity(
            str(payload.get("provider")), str(payload.get("model"))
        ):
            errors.append("mission_preview.leader_backend provenance fields must be coherent")
    else:
        errors.append("mission_preview.leader_backend must be an object")
    if payload.get("workbench_command") != "agentdeck workbench":
        errors.append("mission_preview.workbench_command must be agentdeck workbench")
    _validate_mission_controls(errors, "mission_preview", payload, preview=True)
    return {"ok": not errors, "errors": errors}


def _validate_mission_status_lifecycle(
    errors: list[str], payload: dict[str, object]
) -> None:
    status = payload.get("status")
    workflow_run_id = payload.get("workflow_run_id")
    stop_reason = payload.get("stop_reason")
    confirmed_at = payload.get("confirmed_at")
    completed_at = payload.get("completed_at")
    if not isinstance(status, str) or status not in MISSION_STATUSES:
        return
    if status == "pending_confirmation":
        if any(value is not None for value in (workflow_run_id, confirmed_at, completed_at)):
            errors.append("mission_status.pending_confirmation cannot be confirmed, run, or completed")
        if stop_reason is not None:
            errors.append("mission_status.pending_confirmation.stop_reason must be null")
    if status in ("preparing", "running", "completed", "stopped", "interrupted") and not _mission_nonempty_string(confirmed_at):
        errors.append("mission_status.confirmed_at must be non-empty after confirmation")
    if status in ("running", "completed", "interrupted") and not _mission_nonempty_string(workflow_run_id):
        errors.append("mission_status.workflow_run_id is required for an active workflow")
    if status in ("stopped", "interrupted") and not _mission_nonempty_string(stop_reason):
        errors.append("mission_status.stop_reason is required for a stopped mission")
    if status in ("preparing", "running") and stop_reason is not None:
        errors.append("mission_status.stop_reason must be null while active")
    if status != "completed" and completed_at is not None:
        errors.append("mission_status.completed_at must be null before completion")
    if status == "completed":
        if payload.get("current_step") != payload.get("step_count"):
            errors.append("mission_status.completed current_step must equal step_count")
        if not _mission_nonempty_string(completed_at):
            errors.append("mission_status.completed_at is required for completed status")
        if stop_reason is not None:
            errors.append("mission_status.completed.stop_reason must be null")


def validate_mission_status_contract(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {"ok": False, "errors": ["mission_status must be an object"]}
    errors: list[str] = []
    _mission_exact_fields(errors, "mission_status", payload, MISSION_STATUS_RESPONSE_FIELDS)
    if payload.get("schema_version") != MISSION_SCHEMA_VERSION:
        errors.append("mission_status.schema_version must be mission/v1")
    if payload.get("ok") is not True or payload.get("mode") != "mission_status":
        errors.append("mission_status mode must be a successful mission_status")
    status = payload.get("status")
    if status not in MISSION_STATUSES:
        errors.append(f"mission_status.status must be one of {MISSION_STATUSES}")
    if payload.get("safety") != "inspect" or payload.get("requires_explicit_user") is not True:
        errors.append("mission_status must be inspect-only and require an explicit user")
    _validate_mission_nonempty_strings(
        errors,
        "mission_status",
        payload,
        (
            "user_message", "plan_id", "plan_hash", "created_at", "updated_at",
            "status_command", "resume_command", "attach_command", "workbench_command",
        ),
    )
    _validate_mission_plan_identity(errors, "mission_status", payload)
    _validate_mission_identity_and_commands(errors, "mission_status", payload, preview=False)
    selected = _validate_mission_worker_rows(
        errors,
        "mission_status.selected_agents",
        payload.get("selected_agents"),
        kind="selected_agents",
    )
    selected_ids = [str(item.get("agent_id")) for item in selected]
    if len(selected_ids) < 2 or len(set(selected_ids)) != len(selected_ids):
        errors.append("mission_status.selected_agents must contain at least two unique agents")
    step_count = payload.get("step_count")
    current_step = payload.get("current_step")
    if not _mission_exact_int(step_count) or step_count < 2:
        errors.append("mission_status.step_count must be an integer of at least two")
    if not _mission_exact_int(current_step) or not _mission_exact_int(step_count) or not 0 <= current_step <= step_count:
        errors.append("mission_status.current_step must be between zero and step_count")
    if not _mission_exact_int(payload.get("timeout_seconds")) or payload.get("timeout_seconds", 0) <= 0:
        errors.append("mission_status.timeout_seconds must be a positive integer")
    blockers = _validate_mission_blockers(errors, "mission_status", payload.get("blockers"))
    can_resume = payload.get("can_resume")
    if not isinstance(can_resume, bool):
        errors.append("mission_status.can_resume must be a boolean")
    if can_resume is not (status in ("stopped", "interrupted") and not blockers):
        errors.append("mission_status.can_resume must match resumable status and empty blockers")
    for field in ("workflow_run_id", "stop_reason", "confirmed_at", "completed_at"):
        if not _mission_optional_nonempty_string(payload.get(field)):
            errors.append(f"mission_status.{field} must be a non-empty string or null")
    _validate_mission_status_lifecycle(errors, payload)
    if payload.get("workbench_command") != "agentdeck workbench":
        errors.append("mission_status.workbench_command must be agentdeck workbench")
    attach_command = payload.get("attach_command")
    if not isinstance(attach_command, str) or re.fullmatch(
        r"tmux attach -t [A-Za-z0-9_.:-]+", attach_command
    ) is None:
        errors.append("mission_status.attach_command must be a safe tmux attach command")
    _validate_mission_controls(errors, "mission_status", payload, preview=False)
    return {"ok": not errors, "errors": errors}


def validate_mission_run_contract(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {"ok": False, "errors": ["mission_run must be an object"]}
    errors: list[str] = []
    _mission_exact_fields(errors, "mission_run", payload, MISSION_RUN_RESPONSE_FIELDS)
    if payload.get("mode") not in ("mission_run", "mission_resume"):
        errors.append("mission_run.mode must be mission_run or mission_resume")
    if payload.get("safety") != "delegated":
        errors.append("mission_run.safety must be delegated")
    if payload.get("requires_explicit_user") is not True:
        errors.append("mission_run.requires_explicit_user must be true")
    if payload.get("confirmed") is not True:
        errors.append("mission_run.confirmed must be true")
    if payload.get("status") == "pending_confirmation":
        errors.append("mission_run.status cannot be pending_confirmation")
    turns = payload.get("turns")
    selected = payload.get("selected_agents")
    selected_ids = {
        item.get("agent_id") for item in selected if isinstance(item, dict)
    } if isinstance(selected, list) else set()
    step_count = payload.get("step_count")
    if not isinstance(turns, list):
        errors.append("mission_run.turns must be a list")
        turns = []
    elif isinstance(step_count, int) and not isinstance(step_count, bool) and len(turns) > step_count:
        errors.append("mission_run.turns cannot exceed step_count")
    for index, turn in enumerate(turns):
        prefix = f"mission_run.turns[{index}]"
        if not isinstance(turn, dict):
            errors.append(f"{prefix} must be an object")
            continue
        _mission_exact_fields(errors, prefix, turn, MISSION_RUN_TURN_FIELDS)
        if turn.get("step") != index + 1:
            errors.append(f"{prefix}.step must be contiguous from 1")
        if turn.get("agent_id") not in selected_ids:
            errors.append(f"{prefix}.agent_id must be a frozen selected agent")
        if turn.get("status") not in WORKFLOW_TURN_STATUSES:
            errors.append(f"{prefix}.status is invalid")
        handoff = turn.get("handoff")
        if handoff is None:
            if turn.get("status") in ("completed", "blocked", "failed"):
                errors.append(f"{prefix}.handoff is required for a terminal worker reply")
            continue
        if not isinstance(handoff, dict):
            errors.append(f"{prefix}.handoff must be an object or null")
            continue
        _mission_exact_fields(errors, f"{prefix}.handoff", handoff, MISSION_RUN_HANDOFF_FIELDS)
        if handoff.get("step") != turn.get("step") or handoff.get("agent_id") != turn.get("agent_id"):
            errors.append(f"{prefix}.handoff must match its turn")
        if handoff.get("status") != turn.get("status"):
            errors.append(f"{prefix}.handoff.status must match its turn")
        for field in ("summary", "verification", "risks", "next_steps"):
            if not isinstance(handoff.get(field), str) or not handoff.get(field):
                errors.append(f"{prefix}.handoff.{field} must be a non-empty string")
        if not isinstance(handoff.get("artifact_paths"), list) or not all(
            isinstance(item, str) and item for item in handoff.get("artifact_paths", [])
        ):
            errors.append(f"{prefix}.handoff.artifact_paths must be a string list")
        if not isinstance(handoff.get("trace_command"), str) or re.fullmatch(
            r"agentdeck trace --id rep_[0-9a-f]{12}", handoff.get("trace_command", "")
        ) is None:
            errors.append(f"{prefix}.handoff.trace_command must be a trace command")
    if payload.get("status") == "completed" and (
        not isinstance(step_count, int)
        or len(turns) != step_count
        or any(not isinstance(item, dict) or item.get("status") != "completed" for item in turns)
    ):
        errors.append("mission_run completed status requires every turn completed")
    status_projection = dict(payload)
    status_projection.pop("confirmed", None)
    status_projection.pop("turns", None)
    status_projection["mode"] = "mission_status"
    status_projection["safety"] = "inspect"
    status_validation = validate_mission_status_contract(status_projection)
    errors.extend(str(item) for item in status_validation["errors"])
    return {"ok": not errors, "errors": errors}


def mission_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": MISSION_SCHEMA_VERSION,
        "name": "mission",
        "preview_command": "agentdeck leader chat --message <mission>",
        "run_command": "agentdeck mission run --mission-id <id> --confirm",
        "status_command": "agentdeck mission status --mission-id <id>",
        "resume_command": "agentdeck mission resume --mission-id <id> --confirm",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "preview_response_fields": list(MISSION_PREVIEW_RESPONSE_FIELDS),
        "status_response_fields": list(MISSION_STATUS_RESPONSE_FIELDS),
        "run_response_fields": list(MISSION_RUN_RESPONSE_FIELDS),
        "run_turn_fields": list(MISSION_RUN_TURN_FIELDS),
        "run_handoff_fields": list(MISSION_RUN_HANDOFF_FIELDS),
        "selected_agent_fields": list(MISSION_SELECTED_AGENT_FIELDS),
        "startup_action_fields": list(MISSION_STARTUP_ACTION_FIELDS),
        "plan_fields": list(MISSION_PLAN_FIELDS),
        "plan_step_fields": list(MISSION_PLAN_STEP_FIELDS),
        "control_fields": list(MISSION_CONTROL_FIELDS),
        "leader_backend_fields": list(LEADER_BACKEND_FIELDS),
        "statuses": list(MISSION_STATUSES),
    }


def mission_contract_response(
    contract_path: Path, include_example: bool = False
) -> dict[str, object]:
    payload = mission_contract_payload(contract_path)
    if include_example:
        payload.update(
            {
                "example": True,
                "example_preview": mission_example("preview"),
                "example_status": mission_example("status"),
                "example_run": mission_example("run"),
            }
        )
    return payload


def run_loop_all_example() -> dict[str, object]:
    return {
        "ok": True,
        "mode": "run_loop_all",
        "requires_explicit_user": True,
        "safety": "delegated",
        "plan_count": 2,
        "active_count": 2,
        "budget": {"max_approvals": 5, "used": 1, "remaining": 4},
        "totals": {"auto_approved": 1, "dispatched": 1, "blocked": 0, "skipped_contention": 1},
        "plans": [
            {
                "plan_id": "pln_a", "task": "demoA", "auto_approved": 1,
                "dispatched": [{"approval_id": "apv_a", "agent_id": "planner",
                                "message_id": "msg_a", "trace_command": "agentdeck trace --id msg_a"}],
                "blocked": [], "skipped": [], "skipped_contention": [],
                "gate": "waiting_for_reply",
                "next_command": "agentdeck capture-reply --agent planner --message-id msg_a",
            },
            {
                "plan_id": "pln_b", "task": "demoB", "auto_approved": 0,
                "dispatched": [], "blocked": [],
                "skipped": [], "skipped_contention": [
                    {"approval_id": "apv_b", "agent_id": "planner", "blocker": "agent busy this wave"}],
                "gate": "needs_human_approval", "next_command": "agentdeck approval list",
            },
        ],
    }


def run_loop_all_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "run_loop_all_command": "agentdeck run-loop --all --confirm",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "run_loop_all_response_fields": list(RUN_LOOP_ALL_RESPONSE_FIELDS),
        "run_loop_all_plan_fields": list(RUN_LOOP_ALL_PLAN_FIELDS),
        "gates": list(RUN_LOOP_STOP_REASONS),
        "run_loop_contract": "agentdeck contract run-loop",
        "plans_contract": "agentdeck contract plans",
    }


def run_loop_all_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = run_loop_all_contract_payload(contract_path)
    if include_example:
        example = run_loop_all_example()
        payload["example"] = True
        payload["example_run_loop_all_response_fields"] = list(example)
        payload["example_run_loop_all_plan_fields"] = list(example["plans"][0])
        payload["example_run_loop_all"] = example
    return payload


def validate_run_loop_all_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in RUN_LOOP_ALL_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing run_loop_all field: {field}")
    if payload.get("mode") != "run_loop_all":
        errors.append(f"run_loop_all.mode must be run_loop_all, got {payload.get('mode')}")
    if payload.get("safety") != "delegated":
        errors.append("run_loop_all.safety must be delegated")
    if payload.get("requires_explicit_user") is not True:
        errors.append("run_loop_all.requires_explicit_user must be true")
    budget = payload.get("budget")
    if not isinstance(budget, dict) or any(k not in budget for k in ("max_approvals", "used", "remaining")):
        errors.append("run_loop_all.budget must have max_approvals/used/remaining")
    elif budget.get("used", 0) + budget.get("remaining", 0) != budget.get("max_approvals"):
        errors.append("run_loop_all.budget used+remaining must equal max_approvals")
    totals = payload.get("totals")
    if not isinstance(totals, dict) or any(
        k not in totals for k in ("auto_approved", "dispatched", "blocked", "skipped_contention")
    ):
        errors.append("run_loop_all.totals must have auto_approved/dispatched/blocked/skipped_contention")
    plans = payload.get("plans")
    if not isinstance(plans, list):
        errors.append("run_loop_all.plans must be a list")
        return {"ok": not errors, "errors": errors}
    if payload.get("active_count") != len(plans):
        errors.append("run_loop_all.active_count must equal len(plans)")
    for index, item in enumerate(plans):
        if not isinstance(item, dict):
            errors.append(f"run_loop_all.plans[{index}] must be an object")
            continue
        for field in RUN_LOOP_ALL_PLAN_FIELDS:
            if field not in item:
                errors.append(f"run_loop_all.plans[{index}] missing field: {field}")
        if item.get("gate") not in RUN_LOOP_STOP_REASONS:
            errors.append(f"run_loop_all.plans[{index}].gate invalid")
        for list_field in ("dispatched", "blocked", "skipped", "skipped_contention"):
            if not isinstance(item.get(list_field), list):
                errors.append(f"run_loop_all.plans[{index}].{list_field} must be a list")
    return {"ok": not errors, "errors": errors}


def plan_board_example() -> dict[str, object]:
    return {
        "ok": True,
        "mode": "plan_board",
        "board_command": "agentdeck plan board",
        "plan_count": 2,
        "active_count": 1,
        "plans": [
            {
                "plan_id": "pln_a", "task": "demoA", "provider_backend": "local",
                "created_at": "2026-07-04T00:00:00+00:00", "status": "planned",
                "gate": "needs_human_approval", "next_command": "agentdeck approval list",
                "active": True, "counts": {"steps": 1, "approvals": 1},
            },
            {
                "plan_id": "pln_b", "task": "demoB", "provider_backend": "local",
                "created_at": "2026-07-04T00:00:00+00:00", "status": "completed",
                "gate": "complete", "next_command": "agentdeck leader summary --plan-id pln_b",
                "active": False, "counts": {"steps": 1, "approvals": 1},
            },
        ],
    }


def plan_board_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "board_command": "agentdeck plan board",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "plan_board_response_fields": list(PLAN_BOARD_RESPONSE_FIELDS),
        "plan_board_item_fields": list(PLAN_BOARD_ITEM_FIELDS),
        "gates": list(PLAN_BOARD_GATES),
        "project_view_contract": "agentdeck contract project-view",
        "run_loop_contract": "agentdeck contract run-loop",
    }


def plan_board_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = plan_board_contract_payload(contract_path)
    if include_example:
        example = plan_board_example()
        payload["example"] = True
        payload["example_plan_board_response_fields"] = list(example)
        payload["example_plan_board_item_fields"] = list(example["plans"][0])
        payload["example_plan_board"] = example
    return payload


def validate_plan_board_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in PLAN_BOARD_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing plan_board field: {field}")
    if payload.get("mode") != "plan_board":
        errors.append(f"plan_board.mode must be plan_board, got {payload.get('mode')}")
    if payload.get("board_command") != "agentdeck plan board":
        errors.append("plan_board.board_command must be agentdeck plan board")
    plans = payload.get("plans")
    if not isinstance(plans, list):
        errors.append("plan_board.plans must be a list")
        return {"ok": not errors, "errors": errors}
    if payload.get("plan_count") != len(plans):
        errors.append("plan_board.plan_count must equal len(plans)")
    active = 0
    for index, item in enumerate(plans):
        if not isinstance(item, dict):
            errors.append(f"plan_board.plans[{index}] must be an object")
            continue
        for field in PLAN_BOARD_ITEM_FIELDS:
            if field not in item:
                errors.append(f"plan_board.plans[{index}] missing field: {field}")
        if item.get("gate") not in PLAN_BOARD_GATES:
            errors.append(f"plan_board.plans[{index}].gate must be one of {PLAN_BOARD_GATES}")
        if not isinstance(item.get("next_command"), str) or not item.get("next_command"):
            errors.append(f"plan_board.plans[{index}].next_command must be a non-empty string")
        if item.get("active") is True:
            active += 1
    if payload.get("active_count") != active:
        errors.append("plan_board.active_count must equal the number of active plans")
    return {"ok": not errors, "errors": errors}


def run_start_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "run_command": "agentdeck run --task <text>",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "response_fields": list(RUN_START_RESPONSE_FIELDS),
        "progress_response_fields": list(RUN_PROGRESS_RESPONSE_FIELDS),
        "leader_backend_fields": list(LEADER_BACKEND_FIELDS),
        "control_fields": list(RUN_START_CONTROL_FIELDS),
        "approval_contract": "agentdeck contract approvals",
        "leader_review_contract": "agentdeck contract leader-review",
        "project_view_contract": "agentdeck contract project-view",
    }


def run_start_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = run_start_contract_payload(contract_path)
    if include_example:
        example = run_start_example()
        payload["example"] = True
        payload["example_response_fields"] = list(example)
        payload["example_control_fields"] = list(example["controls"][0])
        payload["example_leader_backend_fields"] = list(example["leader_backend"])
        payload["example_run_start"] = example
        progress_example = run_progress_example()
        payload["example_progress_fields"] = list(progress_example)
        payload["example_run_progress"] = progress_example
    return payload


def workbench_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "workbench_command": "agentdeck workbench",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "snapshot_fields": list(WORKBENCH_SNAPSHOT_FIELDS),
        "leader_card_fields": list(WORKBENCH_LEADER_CARD_FIELDS),
        "mission_card_fields": list(WORKBENCH_MISSION_CARD_FIELDS),
        "coordination_role_fields": list(PROJECT_VIEW_COORDINATION_ROLE_FIELDS),
        "leader_control_fields": list(WORKBENCH_LEADER_CONTROL_FIELDS),
        "control_mode_card_fields": list(WORKBENCH_CONTROL_MODE_CARD_FIELDS),
        "control_mode_option_fields": list(WORKBENCH_CONTROL_MODE_OPTION_FIELDS),
        "control_mode_control_fields": list(WORKBENCH_CONTROL_MODE_CONTROL_FIELDS),
        "provider_health_fields": list(WORKBENCH_PROVIDER_HEALTH_FIELDS),
        "runtime_card_fields": list(WORKBENCH_RUNTIME_CARD_FIELDS),
        "agent_ready_card_fields": list(AGENT_RUNTIME_READY_RESPONSE_FIELDS),
        "terminal_session_card_fields": list(WORKBENCH_TERMINAL_SESSION_CARD_FIELDS),
        "terminal_session_control_fields": list(WORKBENCH_TERMINAL_SESSION_CONTROL_FIELDS),
        "terminal_session_item_fields": list(WORKBENCH_TERMINAL_SESSION_ITEM_FIELDS),
        "runtime_agent_fields": list(WORKBENCH_RUNTIME_AGENT_FIELDS),
        "runtime_control_fields": list(WORKBENCH_RUNTIME_CONTROL_FIELDS),
        "role_card_fields": list(WORKBENCH_ROLE_CARD_FIELDS),
        "role_agent_fields": list(WORKBENCH_ROLE_AGENT_FIELDS),
        "worker_lifecycle_card_fields": list(WORKBENCH_WORKER_LIFECYCLE_CARD_FIELDS),
        "worker_lifecycle_item_fields": list(WORKBENCH_WORKER_LIFECYCLE_ITEM_FIELDS),
        "review_gate_card_fields": list(WORKBENCH_REVIEW_GATE_CARD_FIELDS),
        "review_gate_stage_fields": list(WORKBENCH_REVIEW_GATE_STAGE_FIELDS),
        "release_preview_card_fields": list(WORKBENCH_RELEASE_PREVIEW_CARD_FIELDS),
        "role_topology_card_fields": list(WORKBENCH_ROLE_TOPOLOGY_CARD_FIELDS),
        "role_topology_item_fields": list(WORKBENCH_ROLE_TOPOLOGY_ITEM_FIELDS),
        "ledger_card_fields": list(WORKBENCH_LEDGER_CARD_FIELDS),
        "lineage_card_fields": list(WORKBENCH_LINEAGE_CARD_FIELDS),
        "lineage_path_fields": list(WORKBENCH_LINEAGE_PATH_FIELDS),
        "queue_card_fields": list(WORKBENCH_QUEUE_CARD_FIELDS),
        "operator_card_fields": list(WORKBENCH_OPERATOR_CARD_FIELDS),
        "run_progress_card_fields": list(RUN_PROGRESS_RESPONSE_FIELDS),
        "plan_board_card_fields": list(PLAN_BOARD_RESPONSE_FIELDS),
        "skills_catalog_card_fields": list(WORKBENCH_SKILLS_CATALOG_CARD_FIELDS),
        "skills_catalog_source_fields": list(WORKBENCH_SKILLS_CATALOG_SOURCE_FIELDS),
        "audit_card_fields": list(WORKBENCH_AUDIT_CARD_FIELDS),
        "audit_event_fields": list(WORKBENCH_AUDIT_EVENT_FIELDS),
        "artifacts_card_fields": list(ARTIFACTS_RESPONSE_FIELDS),
        "artifact_summary_fields": list(ARTIFACTS_SUMMARY_FIELDS),
        "artifact_item_fields": list(PROJECT_VIEW_ARTIFACT_ITEM_FIELDS),
        "skill_context_card_fields": list(LEADER_CHAT_SKILL_CONTEXT_CARD_FIELDS),
        "skill_suggestions_card_fields": list(LEADER_CHAT_SKILL_SUGGESTIONS_CARD_FIELDS),
        "memory_context_card_fields": list(LEADER_CHAT_MEMORY_CONTEXT_CARD_FIELDS),
        "memory_suggestions_card_fields": list(LEADER_CHAT_MEMORY_SUGGESTIONS_CARD_FIELDS),
        "skill_context_item_fields": list(PROJECT_VIEW_SKILL_ITEM_FIELDS),
        "leader_summary_card_fields": list(LEADER_SUMMARY_RESPONSE_FIELDS),
        "learning_review_card_fields": list(LEARNING_REVIEW_RESPONSE_FIELDS),
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
        payload["example_memory_context_card_fields"] = list(example["memory_context_card"])
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
        "control_registry_group_fields": list(CONTROL_REGISTRY_GROUP_FIELDS),
        "control_registry_selection_fields": list(CONTROL_REGISTRY_SELECTION_FIELDS),
        "control_registry_filter_fields": list(CONTROL_REGISTRY_FILTER_FIELDS),
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
        payload["example_control_registry_group_fields"] = list(example["groups"][0])
        payload["example_control_registry_filter_fields"] = list(example["filters"])
        payload["example_control_registry_card"] = example
    return payload


def agent_runtime_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "list_command": "agentdeck agent list",
        "ready_command": "agentdeck agent ready",
        "spawn_ready_command": "agentdeck agent spawn-ready --confirm",
        "spawn_command_template": "agentdeck agent spawn --agent <id>",
        "terminal_command_template": "agentdeck agent terminal --agent <id>",
        "capture_command_template": "agentdeck agent capture --agent <id> --lines 200",
        "send_command_template": "agentdeck agent send --agent <id> --text <text>",
        "stop_command_template": "agentdeck agent stop --agent <id>",
        "refresh_command": "agentdeck agent refresh",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "agent_item_fields": list(AGENT_RUNTIME_AGENT_ITEM_FIELDS),
        "capture_response_fields": list(AGENT_RUNTIME_CAPTURE_RESPONSE_FIELDS),
        "terminal_response_fields": list(AGENT_RUNTIME_TERMINAL_RESPONSE_FIELDS),
        "refresh_response_fields": list(AGENT_RUNTIME_REFRESH_RESPONSE_FIELDS),
        "refresh_agent_fields": list(AGENT_RUNTIME_REFRESH_AGENT_FIELDS),
        "ready_response_fields": list(AGENT_RUNTIME_READY_RESPONSE_FIELDS),
        "spawn_ready_response_fields": list(AGENT_RUNTIME_SPAWN_READY_RESPONSE_FIELDS),
        "spawn_ready_result_fields": list(AGENT_RUNTIME_SPAWN_READY_RESULT_FIELDS),
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
        payload["example_terminal_response_fields"] = list(example["terminal"])
        payload["example_refresh_response_fields"] = list(example["refresh"])
        payload["example_refresh_agent_fields"] = list(example["refresh"]["agents"][0])
        payload["example_ready_response_fields"] = list(example["ready"])
        payload["example_spawn_ready_response_fields"] = list(example["spawn_ready"])
        payload["example_spawn_ready_result_fields"] = list(example["spawn_ready"]["results"][0])
        payload["example_control_fields"] = list(example["controls"][0])
        payload["example_agent_runtime"] = example
    return payload


def approval_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "approvals_command": "agentdeck approval list",
        "dispatch_ready_command": "agentdeck approval dispatch-ready --confirm",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "queue_fields": list(APPROVAL_QUEUE_FIELDS),
        "approval_item_fields": list(APPROVAL_ITEM_FIELDS),
        "dispatch_ready_response_fields": list(APPROVAL_DISPATCH_READY_RESPONSE_FIELDS),
        "dispatch_ready_result_fields": list(APPROVAL_DISPATCH_READY_RESULT_FIELDS),
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
        dispatch_ready_example = approval_dispatch_ready_example()
        payload["example_dispatch_ready_fields"] = list(dispatch_ready_example)
        payload["example_dispatch_ready_result_fields"] = list(dispatch_ready_example["results"][0])
        payload["example_dispatch_ready"] = dispatch_ready_example
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
        "leader_backend_fields": list(LEADER_BACKEND_FIELDS),
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
        payload["example_leader_backend_fields"] = list(example["leader_backend"])
        payload["example_control_fields"] = list(example["controls"][0])
        payload["example_leader_review"] = example
    return payload


def leader_summary_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "summary_command": "agentdeck leader summary --plan-id <id>",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "response_fields": list(LEADER_SUMMARY_RESPONSE_FIELDS),
        "leader_backend_fields": list(LEADER_BACKEND_FIELDS),
        "step_fields": list(LEADER_SUMMARY_STEP_FIELDS),
        "artifact_fields": list(LEADER_SUMMARY_ARTIFACT_FIELDS),
        "control_fields": list(LEADER_SUMMARY_CONTROL_FIELDS),
        "project_view_schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "project_view_contract": "agentdeck contract project-view",
        "leader_review_contract": "agentdeck contract leader-review",
        "trace_contract": "agentdeck contract trace",
    }


def leader_summary_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = leader_summary_contract_payload(contract_path)
    if include_example:
        example = leader_summary_example()
        payload["example"] = True
        payload["example_response_fields"] = list(example)
        payload["example_leader_backend_fields"] = list(example["leader_backend"])
        payload["example_step_fields"] = list(example["steps"][0])
        payload["example_artifact_fields"] = list(example["steps"][0]["artifacts"][0])
        payload["example_control_fields"] = list(example["controls"][0])
        payload["example_leader_summary"] = example
    return payload


def leader_status_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "status_command": "agentdeck leader status",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "response_fields": list(LEADER_STATUS_RESPONSE_FIELDS),
        "leader_fields": list(PROJECT_VIEW_LEADER_FIELDS),
        "coordination_role_fields": list(PROJECT_VIEW_COORDINATION_ROLE_FIELDS),
        "provider_health_fields": list(WORKBENCH_PROVIDER_HEALTH_FIELDS),
        "latest_plan_fields": list(PROJECT_VIEW_PLAN_ITEM_FIELDS),
        "queue_fields": list(LEADER_STATUS_QUEUE_FIELDS),
        "recovery_fields": list(PROJECT_VIEW_RECOVERY_FIELDS),
        "control_fields": list(WORKBENCH_CONTROL_MODE_CONTROL_FIELDS),
        "project_view_schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "project_view_contract": "agentdeck contract project-view",
        "workbench_contract": "agentdeck contract workbench",
    }


def leader_status_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = leader_status_contract_payload(contract_path)
    if include_example:
        example = leader_status_example()
        payload["example"] = True
        payload["example_response_fields"] = list(example)
        payload["example_coordination_role_fields"] = list(example["coordination_roles"][0])
        payload["example_provider_health_fields"] = list(example["provider_health"])
        payload["example_queue_fields"] = list(example["queues"])
        payload["example_control_fields"] = list(example["controls"][0])
        payload["example_leader_status"] = example
    return payload


def trace_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "trace_command": "agentdeck trace --id <id>",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "top_level_fields": list(TRACE_TOP_LEVEL_FIELDS),
        "message_fields": list(TRACE_MESSAGE_FIELDS),
        "plan_fields": list(PROJECT_VIEW_PLAN_ITEM_FIELDS),
        "attempt_fields": list(TRACE_ATTEMPT_FIELDS),
        "job_fields": list(TRACE_JOB_FIELDS),
        "reply_fields": list(TRACE_REPLY_FIELDS),
        "artifact_fields": list(TRACE_ARTIFACT_FIELDS),
        "inbox_item_fields": list(TRACE_INBOX_ITEM_FIELDS),
        "control_fields": list(LEADER_CHAT_INTENT_CONTROL_FIELDS),
    }


def trace_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = trace_contract_payload(contract_path)
    if include_example:
        example = trace_example()
        payload["example"] = True
        payload["example_top_level_fields"] = list(example)
        payload["example_message_fields"] = list(example["message"])
        payload["example_plan_fields"] = list(example["plan"]) if isinstance(example.get("plan"), dict) else []
        payload["example_attempt_fields"] = list(example["attempts"][0])
        payload["example_job_fields"] = list(example["jobs"][0])
        payload["example_reply_fields"] = list(example["replies"][0])
        payload["example_artifact_fields"] = list(example["artifacts"][0])
        payload["example_inbox_item_fields"] = list(example["inbox_items"][0])
        payload["example_trace"] = example
    return payload


def artifacts_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "artifacts_command": "agentdeck artifacts",
        "project_view_contract": "agentdeck contract project-view",
        "trace_contract": "agentdeck contract trace",
        "trace_command_template": "agentdeck trace --id <id>",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "response_fields": list(ARTIFACTS_RESPONSE_FIELDS),
        "control_fields": list(ARTIFACTS_CONTROL_FIELDS),
        "artifact_summary_fields": list(ARTIFACTS_SUMMARY_FIELDS),
        "artifact_item_fields": list(PROJECT_VIEW_ARTIFACT_ITEM_FIELDS),
    }


def artifacts_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = artifacts_contract_payload(contract_path)
    if include_example:
        example = artifacts_example()
        payload["example"] = True
        payload["example_response_fields"] = list(example)
        payload["example_control_fields"] = list(example["controls"][0])
        payload["example_artifact_summary_fields"] = list(example["artifacts"])
        payload["example_artifact_item_fields"] = list(example["artifacts"]["items"][0])
        payload["example_artifacts"] = example
    return payload


def validate_project_view_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    schema_version = payload.get("schema_version")
    if schema_version != PROJECT_VIEW_SCHEMA_VERSION:
        errors.append(f"schema_version mismatch: expected {PROJECT_VIEW_SCHEMA_VERSION}, got {schema_version}")
    for field in PROJECT_VIEW_TOP_LEVEL_FIELDS:
        if field not in payload:
            errors.append(f"missing top-level field: {field}")
    leader = payload.get("leader")
    if isinstance(leader, dict):
        for field in PROJECT_VIEW_LEADER_FIELDS:
            if field not in leader:
                errors.append(f"missing leader field: {field}")
        leader_backend = leader.get("leader_backend")
        if isinstance(leader_backend, dict):
            _validate_leader_backend(errors, "project_view.leader", leader_backend)
        else:
            errors.append("project_view.leader.leader_backend must be an object")
        _validate_coordination_roles(errors, "project_view.leader", leader.get("coordination_roles"))
    elif "leader" in payload:
        errors.append("leader must be an object")
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
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    errors.append(
                        "leader_actions items must be objects"
                        if index == 0
                        else f"leader_actions.items[{index}] must be an object"
                    )
                    continue
                for field in PROJECT_VIEW_LEADER_ACTION_ITEM_FIELDS:
                    if field not in item:
                        errors.append(
                            f"missing leader_actions item field: {field}"
                            if index == 0
                            else f"missing leader_actions item field at index {index}: {field}"
                        )
    elif "leader_actions" in payload:
        errors.append("leader_actions must be an object")
    _validate_project_view_plan_items(errors, payload)
    _validate_project_view_mission_items(errors, payload)
    _validate_project_view_skill_items(errors, payload)
    _validate_project_view_memory_items(errors, payload)
    _validate_project_view_protocol_summaries(errors, payload)
    _validate_project_view_summary_items(errors, payload, "messages", PROJECT_VIEW_MESSAGE_ITEM_FIELDS, "message")
    _validate_project_view_summary_items(errors, payload, "jobs", PROJECT_VIEW_JOB_ITEM_FIELDS, "job")
    _validate_project_view_summary_items(errors, payload, "replies", PROJECT_VIEW_REPLY_ITEM_FIELDS, "reply")
    _validate_project_view_summary_items(errors, payload, "artifacts", PROJECT_VIEW_ARTIFACT_ITEM_FIELDS, "artifact")
    _validate_project_view_summary_items(errors, payload, "releases", PROJECT_VIEW_RELEASE_ITEM_FIELDS, "release")
    return {"ok": not errors, "errors": errors}


def _validate_project_view_protocol_summaries(
    errors: list[str], payload: dict[str, object]
) -> None:
    specs = {
        "agent_sessions": (PROJECT_VIEW_AGENT_SESSIONS_FIELDS, PROJECT_VIEW_AGENT_SESSION_ITEM_FIELDS, "by_state", "state", "session_id"),
        "protocol_turns": (PROJECT_VIEW_PROTOCOL_TURNS_FIELDS, PROJECT_VIEW_PROTOCOL_TURN_ITEM_FIELDS, "by_state", "state", "turn_id"),
        "transport_updates": (PROJECT_VIEW_TRANSPORT_UPDATES_FIELDS, PROJECT_VIEW_TRANSPORT_UPDATE_ITEM_FIELDS, "by_kind", "kind", "update_id"),
        "permission_requests": (PROJECT_VIEW_PERMISSION_REQUESTS_FIELDS, PROJECT_VIEW_PERMISSION_REQUEST_ITEM_FIELDS, "by_status", "status", "permission_id"),
    }
    enum_fields = {
        ("agent_sessions", "state"): {"created", "connecting", "ready", "busy", "reconnecting", "stopped", "failed"},
        ("protocol_turns", "state"): {"created", "submitted", "streaming", "waiting_permission", "completed", "blocked", "failed", "ambiguous"},
        ("transport_updates", "kind"): {"progress", "text", "tool_call", "tool_result", "permission_request", "artifact", "completion", "error"},
        ("permission_requests", "status"): {"pending", "approved", "denied", "expired"},
    }
    for name, (required_fields, item_fields, group_field, item_group_field, identity_field) in specs.items():
        summary = payload.get(name)
        if not isinstance(summary, dict):
            if name in payload:
                errors.append(f"{name} must be an object")
            continue
        for field in required_fields:
            if field not in summary:
                errors.append(f"missing {name} field: {field}")
        for field in sorted(set(summary) - set(required_fields)):
            errors.append(f"{name} has unexpected field: {field}")
        for field in ("count", "pending_count"):
            if field in summary and (type(summary[field]) is not int or summary[field] < 0):
                errors.append(f"{name}.{field} must be a non-negative integer")
        counts = summary.get(group_field)
        if not isinstance(counts, dict) or any(
            not isinstance(key, str) or not key or type(value) is not int or value < 0
            for key, value in counts.items()
        ):
            errors.append(f"{name}.{group_field} values must be non-negative integers")
        else:
            allowed_keys = enum_fields[(name, item_group_field)]
            for key in sorted(set(counts) - allowed_keys):
                errors.append(f"{name}.{group_field} has invalid key: {key}")
            count = summary.get("count")
            if type(count) is int and count >= 0 and count != sum(counts.values()):
                errors.append(f"{name}.count must equal sum({group_field})")
            if name == "permission_requests":
                pending_count = summary.get("pending_count")
                if type(pending_count) is int and pending_count >= 0 and pending_count != counts.get("pending", 0):
                    errors.append("permission_requests.pending_count must equal by_status pending count")
        if not isinstance(summary.get("items"), list):
            errors.append(f"{name}.items must be a list")
        elif len(summary["items"]) > 20:
            errors.append(f"{name}.items must contain at most 20 items")
        else:
            count = summary.get("count")
            if type(count) is int and count >= 0 and len(summary["items"]) != min(count, 20):
                errors.append(f"{name}.items length must equal min(count, 20)")
            identities: set[object] = set()
            order: list[tuple[object, object]] = []
            item_counts: dict[str, int] = {}
            for index, item in enumerate(summary["items"]):
                if not isinstance(item, dict):
                    errors.append(f"{name}.items[{index}] must be an object")
                    continue
                identity = item.get(identity_field)
                if isinstance(identity, str):
                    if identity in identities:
                        errors.append(f"{name}.items contains duplicate {identity_field}: {identity}")
                    identities.add(identity)
                order.append((item.get("created_at"), identity))
                group_value = item.get(item_group_field)
                if isinstance(group_value, str):
                    item_counts[group_value] = item_counts.get(group_value, 0) + 1
                for field in sorted(set(item) - set(item_fields)):
                    errors.append(f"{name}.items[{index}] has unexpected field: {field}")
                for field in item_fields:
                    if field not in item:
                        errors.append(f"{name}.items[{index}] missing field: {field}")
                        continue
                    value = item[field]
                    if field == "native_session_present":
                        valid = type(value) is bool
                    elif field == "sequence":
                        valid = type(value) is int and value >= 0
                    elif field == "decision":
                        valid = value is None or isinstance(value, str)
                    elif field == "capabilities":
                        valid = isinstance(value, dict) and set(value) == {
                            "structured_sessions", "streaming_updates", "structured_tools",
                            "permission_requests", "resume_session", "observable_terminal",
                        } and all(type(flag) is bool for flag in value.values())
                    else:
                        valid = isinstance(value, str) and bool(value.strip())
                    if not valid:
                        errors.append(f"{name}.items[{index}].{field} has invalid type")
                for field in ("state", "kind", "status"):
                    allowed = enum_fields.get((name, field))
                    if allowed is not None and item.get(field) not in allowed:
                        errors.append(f"{name}.items[{index}].{field} is invalid")
            if all(isinstance(created_at, str) and isinstance(identity, str) for created_at, identity in order):
                if order != sorted(order):
                    errors.append(f"{name}.items must be sorted by created_at and {identity_field}")
            if (
                type(summary.get("count")) is int
                and summary["count"] <= 20
                and isinstance(summary.get(group_field), dict)
                and item_counts != summary[group_field]
            ):
                errors.append(f"{name} items distribution must match {group_field}")


def _validate_project_view_mission_items(
    errors: list[str], payload: dict[str, object]
) -> None:
    missions = payload.get("missions")
    if not isinstance(missions, dict):
        if "missions" in payload:
            errors.append("missions must be an object")
        return
    for field in PROJECT_VIEW_MISSIONS_FIELDS:
        if field not in missions:
            errors.append(f"missing missions field: {field}")
    items = missions.get("items")
    if not isinstance(items, list):
        if "items" in missions:
            errors.append("missions.items must be a list")
        return
    count = missions.get("count")
    if not isinstance(count, int) or isinstance(count, bool):
        errors.append("missions.count must be an integer")
    elif count < 0:
        errors.append("missions.count must be a non-negative integer")
    elif count != len(items):
        errors.append("missions.count must equal len(missions.items)")
    by_status = missions.get("by_status")
    if not isinstance(by_status, dict) or not all(
        isinstance(key, str)
        and isinstance(value, int)
        and not isinstance(value, bool)
        for key, value in by_status.items()
    ):
        errors.append("missions.by_status must be an object of integer counts")
    expected_statuses: dict[str, int] = {}
    for item in items:
        if isinstance(item, dict):
            status = item.get("status")
            if isinstance(status, str):
                expected_statuses[status] = expected_statuses.get(status, 0) + 1
    if isinstance(by_status, dict) and by_status != expected_statuses:
        errors.append("missions.by_status must match mission item statuses")
    latest_id = missions.get("latest_id")
    expected_latest_id = items[-1].get("mission_id") if items and isinstance(items[-1], dict) else None
    if latest_id != expected_latest_id:
        errors.append("missions.latest_id must match the final mission item")
    mission_ids = [item.get("mission_id") for item in items if isinstance(item, dict)]
    if len(mission_ids) != len(set(mission_ids)):
        errors.append("missions.items mission_id must be unique")

    string_fields = {
        "mission_id",
        "schema_version",
        "user_message",
        "status",
        "provider",
        "model",
        "plan_id",
        "plan_hash",
        "created_at",
        "updated_at",
        "status_command",
        "confirmation_command",
        "resume_command",
    }
    nullable_string_fields = {
        "stop_reason",
        "workflow_run_id",
        "confirmed_at",
        "completed_at",
    }
    forbidden_fields = {
        "command",
        "launch_command",
        "prompt",
        "full_prompt",
        "credentials",
        "credential",
        "env",
        "environment",
        "api_key",
        "password",
        "access_token",
    }

    def forbidden_semantic_paths(value: object, prefix: str) -> list[str]:
        paths: list[str] = []
        if isinstance(value, dict):
            for raw_key, nested in value.items():
                key = str(raw_key)
                path = f"{prefix}.{key}"
                normalized = key.strip().lower().replace("-", "_")
                if normalized in forbidden_fields:
                    paths.append(path)
                paths.extend(forbidden_semantic_paths(nested, path))
        elif isinstance(value, list):
            for nested_index, nested in enumerate(value):
                paths.extend(
                    forbidden_semantic_paths(nested, f"{prefix}[{nested_index}]")
                )
        return paths

    selected_agent_types = {
        field: "nullable_string" if field in MISSION_WORKER_NULLABLE_FIELDS else "string"
        for field in MISSION_STATE_SELECTED_AGENT_FIELDS
    }
    startup_action_types = {
        field: "nullable_string" if field in MISSION_WORKER_NULLABLE_FIELDS else "string"
        for field in MISSION_STATE_STARTUP_ACTION_FIELDS
    }
    required_worker_fields = {
        "selected_agents": MISSION_STATE_SELECTED_AGENT_REQUIRED_FIELDS,
        "startup_actions": MISSION_STATE_STARTUP_ACTION_REQUIRED_FIELDS,
    }
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"missions.items[{index}] must be an object")
            continue
        for field in PROJECT_VIEW_MISSION_ITEM_FIELDS:
            if field not in item:
                errors.append(f"missing mission item field at index {index}: {field}")
        for field in sorted(forbidden_fields.intersection(item)):
            errors.append(f"missions.items[{index}] must not contain raw field: {field}")
        for path in forbidden_semantic_paths(item, f"missions.items[{index}]"):
            errors.append(f"{path} is forbidden")
        for field in string_fields:
            if field in item and not isinstance(item[field], str):
                errors.append(f"missions.items[{index}].{field} must be a string")
        for field in nullable_string_fields:
            if field in item and item[field] is not None and not isinstance(item[field], str):
                errors.append(f"missions.items[{index}].{field} must be a string or null")
        for field in ("can_start", "can_resume"):
            if field in item and not isinstance(item[field], bool):
                errors.append(f"missions.items[{index}].{field} must be a boolean")
        for field in ("current_step", "step_count", "timeout_seconds"):
            if field in item and (
                not isinstance(item[field], int) or isinstance(item[field], bool)
            ):
                errors.append(f"missions.items[{index}].{field} must be an integer")
        current_step = item.get("current_step")
        step_count = item.get("step_count")
        if (
            isinstance(current_step, int)
            and not isinstance(current_step, bool)
            and isinstance(step_count, int)
            and not isinstance(step_count, bool)
            and (current_step < 0 or current_step > step_count)
        ):
            errors.append(f"missions.items[{index}].current_step must be within 0..step_count")
        for field in ("blockers", "selected_agents", "startup_actions"):
            if field in item and not isinstance(item[field], list):
                errors.append(f"missions.items[{index}].{field} must be a list")
        blockers = item.get("blockers")
        if isinstance(blockers, list) and not all(
            isinstance(blocker, str) for blocker in blockers
        ):
            errors.append(f"missions.items[{index}].blockers must contain only strings")
        for field in ("selected_agents", "startup_actions"):
            entries = item.get(field)
            if not isinstance(entries, list):
                continue
            field_types = (
                selected_agent_types if field == "selected_agents" else startup_action_types
            )
            for entry_index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    errors.append(
                        f"missions.items[{index}].{field}[{entry_index}] must be an object"
                    )
                    continue
                for raw_field in sorted(forbidden_fields.intersection(entry)):
                    errors.append(
                        f"missions.items[{index}].{field}[{entry_index}] "
                        f"must not contain raw field: {raw_field}"
                    )
                for entry_field in entry:
                    if entry_field not in field_types and entry_field not in forbidden_fields:
                        errors.append(
                            f"missions.items[{index}].{field}[{entry_index}].{entry_field} "
                            "is not an allowed compact field"
                        )
                for entry_field, expected_type in field_types.items():
                    if entry_field not in entry:
                        continue
                    entry_value = entry[entry_field]
                    if expected_type == "string" and not isinstance(entry_value, str):
                        errors.append(
                            f"missions.items[{index}].{field}[{entry_index}].{entry_field} "
                            "must be a string"
                        )
                    if expected_type == "nullable_string" and (
                        entry_value is not None and not isinstance(entry_value, str)
                    ):
                        errors.append(
                            f"missions.items[{index}].{field}[{entry_index}].{entry_field} "
                            "must be a string or null"
                        )
                for required_field in required_worker_fields[field]:
                    if required_field not in entry:
                        errors.append(
                            f"missions.items[{index}].{field}[{entry_index}] "
                            f"missing required compact field: {required_field}"
                        )
                if field == "startup_actions" and entry.get("action") not in {
                    "reuse",
                    "spawn",
                }:
                    errors.append(
                        f"missions.items[{index}].startup_actions[{entry_index}].action "
                        "must be reuse or spawn"
                    )
        leader_backend = item.get("leader_backend")
        if isinstance(leader_backend, dict):
            _validate_leader_backend(
                errors, f"missions.items[{index}]", leader_backend
            )
            for backend_field in leader_backend:
                if (
                    backend_field not in LEADER_BACKEND_FIELDS
                    and backend_field not in forbidden_fields
                ):
                    errors.append(
                        f"missions.items[{index}].leader_backend.{backend_field} "
                        "is not an allowed compact field"
                    )
            for backend_field in (
                "agent_id",
                "provider",
                "model",
                "provider_backend",
                "provider_transport",
                "reasoning_backend",
                "runtime_kind",
            ):
                if backend_field in leader_backend and not isinstance(
                    leader_backend[backend_field], str
                ):
                    errors.append(
                        f"missions.items[{index}].leader_backend.{backend_field} "
                        "must be a string"
                    )
            for backend_field in (
                "pane_backed",
                "approval_required",
                "dispatch_ready",
            ):
                if backend_field in leader_backend and not isinstance(
                    leader_backend[backend_field], bool
                ):
                    errors.append(
                        f"missions.items[{index}].leader_backend.{backend_field} "
                        "must be a boolean"
                    )
            if (
                leader_backend.get("provider") != item.get("provider")
                or leader_backend.get("model") != item.get("model")
            ):
                errors.append(
                    f"missions.items[{index}].leader_backend provider/model "
                    "must match mission provider/model"
                )
        elif "leader_backend" in item:
            errors.append(f"missions.items[{index}].leader_backend must be an object")
        if item.get("schema_version") != MISSION_SCHEMA_VERSION:
            errors.append(
                f"missions.items[{index}].schema_version must be {MISSION_SCHEMA_VERSION}"
            )
        if item.get("status") not in MISSION_STATUSES:
            errors.append(f"missions.items[{index}].status must be a known mission status")
        mission_id = item.get("mission_id")
        if not is_canonical_mission_id(mission_id):
            errors.append(
                f"missions.items[{index}].mission_id must match canonical mission id grammar"
            )
        else:
            for command_field, expected_command in mission_commands(mission_id).items():
                if item.get(command_field) != expected_command:
                    errors.append(
                        f"missions.items[{index}].{command_field} "
                        "must match canonical mission command"
                    )
        selected_agents = item.get("selected_agents")
        startup_actions = item.get("startup_actions")
        if item.get("can_start") is True and (
            not isinstance(selected_agents, list)
            or not isinstance(startup_actions, list)
            or len(selected_agents) < 2
            or len(startup_actions) < 2
            or [entry.get("agent_id") for entry in selected_agents if isinstance(entry, dict)]
            != [entry.get("agent_id") for entry in startup_actions if isinstance(entry, dict)]
        ):
            errors.append(
                f"missions.items[{index}].can_start requires at least two valid "
                "selected agents and startup actions"
            )
        if item.get("can_start") is True and isinstance(item.get("blockers"), list) and item[
            "blockers"
        ]:
            errors.append(f"missions.items[{index}].can_start requires empty blockers")


def _validate_project_view_skill_items(errors: list[str], payload: dict[str, object]) -> None:
    skills = payload.get("skills")
    if not isinstance(skills, dict):
        if "skills" in payload:
            errors.append("skills must be an object")
        return
    for field in PROJECT_VIEW_SKILLS_FIELDS:
        if field not in skills:
            errors.append(f"missing skills field: {field}")
    items = skills.get("items")
    if not isinstance(items, list):
        if "items" in skills:
            errors.append("skills.items must be a list")
        return
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            if index == 0:
                errors.append("skills.items must contain objects")
            else:
                errors.append(f"skills.items[{index}] must be an object")
            continue
        for field in PROJECT_VIEW_SKILL_ITEM_FIELDS:
            if field not in item:
                if index == 0:
                    errors.append(f"missing skill item field: {field}")
                else:
                    errors.append(f"missing skill item field at index {index}: {field}")
        guidance = item.get("planning_guidance")
        if not isinstance(guidance, list) or not all(
            isinstance(entry, str) for entry in guidance
        ):
            errors.append(
                f"skills.items[{index}].planning_guidance must be a list of strings"
            )


def _validate_project_view_memory_items(errors: list[str], payload: dict[str, object]) -> None:
    memory = payload.get("memory")
    if not isinstance(memory, dict):
        if "memory" in payload:
            errors.append("memory must be an object")
        return
    for field in PROJECT_VIEW_MEMORY_FIELDS:
        if field not in memory:
            errors.append(f"missing memory field: {field}")
    items = memory.get("items")
    if not isinstance(items, list):
        if "items" in memory:
            errors.append("memory.items must be a list")
        return
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            if index == 0:
                errors.append("memory.items must contain objects")
            else:
                errors.append(f"memory.items[{index}] must be an object")
            continue
        for field in PROJECT_VIEW_MEMORY_ITEM_FIELDS:
            if field not in item:
                if index == 0:
                    errors.append(f"missing memory item field: {field}")
                else:
                    errors.append(f"missing memory item field at index {index}: {field}")


def _validate_project_view_plan_items(errors: list[str], payload: dict[str, object]) -> None:
    plans = payload.get("plans")
    if not isinstance(plans, dict):
        if "plans" in payload:
            errors.append("plans must be an object")
        return
    items = plans.get("items")
    if not isinstance(items, list):
        if "items" in plans:
            errors.append("plans.items must be a list")
        return
    if not items:
        return
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"plans.items[{index}] must be an object")
            continue
        for field in PROJECT_VIEW_PLAN_ITEM_FIELDS:
            if field not in item:
                errors.append(f"missing plan item field at index {index}: {field}")
        leader_backend = item.get("leader_backend")
        if isinstance(leader_backend, dict):
            _validate_leader_backend(errors, f"project_view.plans.items[{index}]", leader_backend)
        else:
            errors.append(f"project_view.plans.items[{index}].leader_backend must be an object")


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
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            if index == 0:
                errors.append(f"{summary_name}.items must contain objects")
            else:
                errors.append(f"{summary_name}.items[{index}] must be an object")
            continue
        for field in fields:
            if field not in item:
                if index == 0:
                    errors.append(f"missing {label} item field: {field}")
                else:
                    errors.append(f"missing {label} item field at index {index}: {field}")


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


def validate_loop_once_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in LOOP_ONCE_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing loop_once field: {field}")
    if payload.get("mode") != "loop_once":
        errors.append(f"loop_once.mode must be loop_once, got {payload.get('mode')}")
    if payload.get("loop_id") != "run_once":
        errors.append("loop_once.loop_id must be run_once")
    if payload.get("iteration") != 1:
        errors.append("loop_once.iteration must be 1")
    if payload.get("max_iterations") != 1:
        errors.append("loop_once.max_iterations must be 1")
    if payload.get("source_command") != "agentdeck loop once":
        errors.append("loop_once.source_command must be agentdeck loop once")
    if payload.get("project_view_command") != "agentdeck status":
        errors.append("loop_once.project_view_command must be agentdeck status")
    if payload.get("continue_command") != "agentdeck continue":
        errors.append("loop_once.continue_command must be agentdeck continue")
    if payload.get("workbench_command") != "agentdeck workbench":
        errors.append("loop_once.workbench_command must be agentdeck workbench")
    if payload.get("will_execute") is not False:
        errors.append("loop_once.will_execute must be false")
    next_command = payload.get("next_command")
    if next_command and payload.get("requires_explicit_user") is not True:
        errors.append("loop_once.requires_explicit_user must be true when next_command exists")
    continue_card = payload.get("continue_card")
    if isinstance(continue_card, dict):
        continue_validation = validate_continue_contract(continue_card)
        for error in continue_validation["errors"]:
            errors.append(f"continue_card: {error}")
        if payload.get("next_command") != continue_card.get("next_command"):
            errors.append("loop_once.next_command must match continue_card.next_command")
        if payload.get("recommended_action") != continue_card.get("recommended_action"):
            errors.append("loop_once.recommended_action must match continue_card.recommended_action")
    elif "continue_card" in payload:
        errors.append("loop_once.continue_card must be an object")
    controls = payload.get("controls")
    if isinstance(controls, list):
        if not controls:
            errors.append("loop_once.controls must not be empty")
        for control in controls:
            if not isinstance(control, dict):
                errors.append("loop_once.controls items must be objects")
                continue
            for field in WORKBENCH_CONTROL_MODE_CONTROL_FIELDS:
                if field not in control:
                    errors.append(f"loop_once.controls: missing control field: {field}")
            if control.get("kind") == "execute_next" and next_command and control.get("command") != next_command:
                errors.append("loop_once.controls: execute_next command must match next_command")
            if control.get("kind") == "execute_next" and control.get("enabled") is True and not next_command:
                errors.append("loop_once.controls: execute_next cannot be enabled without next_command")
    elif "controls" in payload:
        errors.append("loop_once.controls must be a list")
    return {"ok": not errors, "errors": errors}


def validate_release_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in RELEASE_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing release response field: {field}")
    if payload.get("mode") != "release":
        errors.append("release response mode must be release")
    if payload.get("safety") != "explicit_user":
        errors.append("release response safety must be explicit_user")
    if payload.get("requires_explicit_user") is not True:
        errors.append("release response must require explicit user")
    release = payload.get("release")
    if isinstance(release, dict):
        for field in RELEASE_RECORD_FIELDS:
            if field not in release:
                errors.append(f"missing release record field: {field}")
        if release.get("status") != "released":
            errors.append("release record status must be released")
        if not isinstance(release.get("round"), int) or release.get("round") < 1:
            errors.append("release record round must be a positive integer")
        if payload.get("release_count") != release.get("round"):
            errors.append("release_count must match release.round")
    elif "release" in payload:
        errors.append("release must be an object")
    if payload.get("next_command") != "agentdeck workbench":
        errors.append("release next_command must be agentdeck workbench")
    if payload.get("next_round_command") != "agentdeck leader plan --task <goal>":
        errors.append("release next_round_command must be the explicit plan template")
    trace_commands = payload.get("trace_commands")
    if isinstance(trace_commands, list):
        for command in trace_commands:
            if not isinstance(command, str) or not command.startswith("agentdeck trace --id "):
                errors.append("release trace_commands must be agentdeck trace commands")
    elif "trace_commands" in payload:
        errors.append("release trace_commands must be a list")
    controls = payload.get("controls")
    if isinstance(controls, list):
        seen_kinds: set[object] = set()
        for control in controls:
            if not isinstance(control, dict):
                errors.append("release controls items must be objects")
                continue
            for field in WORKBENCH_CONTROL_MODE_CONTROL_FIELDS:
                if field not in control:
                    errors.append(f"release control missing field: {field}")
            kind = control.get("kind")
            seen_kinds.add(kind)
            if kind == "inspect":
                if control.get("command") != "agentdeck workbench":
                    errors.append("release inspect control command must be agentdeck workbench")
                if control.get("safety") != "inspect":
                    errors.append("release inspect control must use safety=inspect")
            elif kind in {"trace_code_review", "trace_round_review"}:
                if control.get("safety") != "inspect":
                    errors.append("release trace controls must use safety=inspect")
                if control.get("command") not in (trace_commands if isinstance(trace_commands, list) else []):
                    errors.append("release trace control command must appear in trace_commands")
            elif kind == "next_round":
                if control.get("safety") != "plan_only":
                    errors.append("release next_round control must use safety=plan_only")
                if control.get("enabled") is not False:
                    errors.append("release next_round control must stay disabled")
                if control.get("command") != payload.get("next_round_command"):
                    errors.append("release next_round control command must match next_round_command")
            else:
                errors.append(f"unknown release control kind: {kind}")
            if control.get("enabled") is False and not control.get("blocker"):
                errors.append("release disabled controls must include blocker")
        for required_kind in ("inspect", "trace_code_review", "trace_round_review", "next_round"):
            if required_kind not in seen_kinds:
                errors.append(f"release response missing {required_kind} control")
    elif "controls" in payload:
        errors.append("release controls must be a list")
    return {"ok": not errors, "errors": errors}


def validate_approval_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in APPROVAL_QUEUE_FIELDS:
        if field not in payload:
            errors.append(f"missing approval queue field: {field}")
    approvals = payload.get("approvals")
    if isinstance(approvals, list):
        for index, approval in enumerate(approvals):
            if not isinstance(approval, dict):
                errors.append(
                    "approval items must be objects"
                    if index == 0
                    else f"approvals[{index}] must be an object"
                )
                continue
            for field in APPROVAL_ITEM_FIELDS:
                if field not in approval:
                    errors.append(
                        f"missing approval item field: {field}"
                        if index == 0
                        else f"missing approval item field at index {index}: {field}"
                    )
            if not isinstance(approval.get("can_dispatch"), bool):
                errors.append(
                    "can_dispatch must be a boolean"
                    if index == 0
                    else f"approvals[{index}].can_dispatch must be a boolean"
                )
    elif "approvals" in payload:
        errors.append("approvals must be a list")
    return {"ok": not errors, "errors": errors}


def validate_approval_dispatch_ready_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in APPROVAL_DISPATCH_READY_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing approval dispatch-ready field: {field}")
    if payload.get("mode") != "dispatch_ready":
        errors.append("dispatch_ready.mode must be dispatch_ready")
    if payload.get("requires_explicit_user") is not True:
        errors.append("dispatch_ready.requires_explicit_user must be true")
    if payload.get("safety") != "explicit_runtime":
        errors.append("dispatch_ready.safety must be explicit_runtime")

    results = payload.get("results")
    if isinstance(results, list):
        dispatched_count = 0
        blocked_count = 0
        for index, result in enumerate(results):
            if not isinstance(result, dict):
                errors.append("dispatch_ready.results items must be objects")
                continue
            for field in APPROVAL_DISPATCH_READY_RESULT_FIELDS:
                if field not in result:
                    errors.append(f"missing approval dispatch-ready result field: {field}")
            status = result.get("status")
            if status == "dispatched":
                dispatched_count += 1
                if not result.get("message_id"):
                    errors.append(f"dispatch_ready.results[{index}].message_id is required when dispatched")
                if not result.get("trace_command"):
                    errors.append(f"dispatch_ready.results[{index}].trace_command is required when dispatched")
                if result.get("blocker") is not None:
                    errors.append(f"dispatch_ready.results[{index}].blocker must be null when dispatched")
            elif status == "blocked":
                blocked_count += 1
                if not result.get("blocker"):
                    errors.append(f"dispatch_ready.results[{index}].blocker is required when blocked")
            else:
                errors.append(f"dispatch_ready.results[{index}].status must be dispatched or blocked")
            approval_id = result.get("approval_id")
            expected_dispatch = f"agentdeck approval dispatch --approval-id {approval_id}"
            if result.get("dispatch_command") != expected_dispatch:
                errors.append(f"dispatch_ready.results[{index}].dispatch_command must match approval_id")
        if payload.get("dispatched_count") != dispatched_count:
            errors.append("dispatch_ready.dispatched_count must match dispatched results")
        if payload.get("blocked_count") != blocked_count:
            errors.append("dispatch_ready.blocked_count must match blocked results")
        if payload.get("skipped_count") != blocked_count:
            errors.append("dispatch_ready.skipped_count must match blocked results")
    elif "results" in payload:
        errors.append("dispatch_ready.results must be a list")
    return {"ok": not errors, "errors": errors}


def validate_run_start_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    mode = payload.get("mode")
    required_fields = RUN_PROGRESS_RESPONSE_FIELDS if mode == "run_progress" else RUN_START_RESPONSE_FIELDS
    for field in required_fields:
        if field not in payload:
            errors.append(f"missing {mode or 'run'} field: {field}")
    if mode not in {"run_start", "run_progress"}:
        errors.append("run mode must be run_start or run_progress")
    if payload.get("requires_explicit_user") is not True:
        errors.append(f"{mode or 'run'}.requires_explicit_user must be true")
    if payload.get("safety") != "approval_gated":
        errors.append(f"{mode or 'run'}.safety must be approval_gated")
    plan_id = payload.get("plan_id")
    review_command = payload.get("review_command")
    if plan_id and review_command != f"agentdeck leader review --plan-id {plan_id}":
        errors.append(f"{mode or 'run'}.review_command must match plan_id")
    leader_backend = payload.get("leader_backend")
    if isinstance(leader_backend, dict):
        _validate_leader_backend(errors, str(mode or "run"), leader_backend)
    else:
        errors.append(f"{mode or 'run'}.leader_backend must be an object")
    if mode == "run_start" and payload.get("next_command") != "agentdeck approval list":
        errors.append("run_start.next_command must be agentdeck approval list")
    if mode == "run_progress":
        review = payload.get("review")
        if isinstance(review, dict):
            review_validation = validate_leader_review_contract(review)
            if not review_validation["ok"]:
                errors.extend(f"review: {error}" for error in review_validation["errors"])
            if payload.get("next_command") != review.get("next_command"):
                errors.append("run_progress.next_command must match review.next_command")
            if isinstance(leader_backend, dict) and review.get("leader_backend") != leader_backend:
                errors.append("run_progress.review.leader_backend must match leader_backend")
        else:
            errors.append("run_progress.review must be an object")
    approval_card = payload.get("approval_card")
    if isinstance(approval_card, dict):
        approval_validation = validate_approval_contract(approval_card)
        if not approval_validation["ok"]:
            errors.extend(f"approval_card: {error}" for error in approval_validation["errors"])
    else:
        errors.append(f"{mode or 'run'}.approval_card must be an object")
    controls = payload.get("controls")
    if isinstance(controls, list):
        if not controls:
            errors.append(f"{mode or 'run'}.controls must not be empty")
        for control in controls:
            if not isinstance(control, dict):
                errors.append(f"{mode or 'run'}.controls items must be objects")
                continue
            for field in RUN_START_CONTROL_FIELDS:
                if field not in control:
                    errors.append(f"missing {mode or 'run'} control field: {field}")
            if control.get("kind") == "approve" and control.get("safety") != "explicit_runtime":
                errors.append("run_start approve control safety must be explicit_runtime")
    elif "controls" in payload:
        errors.append(f"{mode or 'run'}.controls must be a list")
    return {"ok": not errors, "errors": errors}


def _validate_leader_backend(
    errors: list[str],
    prefix: str,
    leader_backend: dict[str, object],
) -> None:
    for field in LEADER_BACKEND_FIELDS:
        if field not in leader_backend:
            errors.append(f"{prefix}.leader_backend missing field: {field}")
    if "agent_id" in leader_backend and leader_backend.get("agent_id") != "leader":
        errors.append(f"{prefix}.leader_backend.agent_id must be leader")
    if (
        leader_backend.get("runtime_kind") != "logical_leader"
        or leader_backend.get("pane_backed") is not False
        or leader_backend.get("pane_id") is not None
    ):
        errors.append(f"{prefix}.leader_backend.runtime_kind must be logical_leader without a pane")
    if leader_backend.get("approval_required") is not True:
        errors.append(f"{prefix}.leader_backend.approval_required must be true")
    if leader_backend.get("dispatch_ready") is not False:
        errors.append(f"{prefix}.leader_backend.dispatch_ready must be false")


def _validate_coordination_roles(
    errors: list[str],
    prefix: str,
    roles: object,
) -> None:
    if not isinstance(roles, list):
        errors.append(f"{prefix}.coordination_roles must be a list")
        return
    seen: list[object] = []
    for index, role in enumerate(roles):
        if not isinstance(role, dict):
            errors.append(f"{prefix}.coordination_roles[{index}] must be an object")
            continue
        for field in PROJECT_VIEW_COORDINATION_ROLE_FIELDS:
            if field not in role:
                errors.append(f"{prefix}.coordination_roles[{index}] missing field: {field}")
        role_id = role.get("role_id")
        seen.append(role_id)
        if (
            role.get("runtime_kind") != "logical_role"
            or role.get("pane_backed") is not False
            or role.get("pane_id") is not None
        ):
            errors.append(f"{prefix}.coordination_roles[{index}] must be a logical role without a pane")
        if role.get("dispatch_ready") is not False:
            errors.append(f"{prefix}.coordination_roles[{index}].dispatch_ready must be false")
        if role_id == "frontdesk" and role.get("approval_required") is not False:
            errors.append(f"{prefix}.coordination_roles[{index}].approval_required must be false for frontdesk")
        if role_id in {"planner", "orchestrator"} and role.get("approval_required") is not True:
            errors.append(
                f"{prefix}.coordination_roles[{index}].approval_required must be true for planner/orchestrator"
            )
    if seen != ["frontdesk", "planner", "orchestrator"]:
        errors.append(f"{prefix}.coordination_roles must be ordered as frontdesk, planner, orchestrator")


def validate_inbox_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in INBOX_QUEUE_FIELDS:
        if field not in payload:
            errors.append(f"missing inbox queue field: {field}")
    items = payload.get("items")
    if isinstance(items, list):
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append("inbox items must be objects" if index == 0 else f"items[{index}] must be an object")
                continue
            for field in INBOX_ITEM_FIELDS:
                if field not in item:
                    errors.append(
                        f"missing inbox item field: {field}"
                        if index == 0
                        else f"missing inbox item field at index {index}: {field}"
                    )
            if not isinstance(item.get("is_head"), bool):
                errors.append("is_head must be a boolean" if index == 0 else f"items[{index}].is_head must be a boolean")
            if not isinstance(item.get("can_ack"), bool):
                errors.append("can_ack must be a boolean" if index == 0 else f"items[{index}].can_ack must be a boolean")
    elif "items" in payload:
        errors.append("items must be a list")
    return {"ok": not errors, "errors": errors}


def validate_leader_review_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in LEADER_REVIEW_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing leader_review field: {field}")
    leader_backend = payload.get("leader_backend")
    if isinstance(leader_backend, dict):
        _validate_leader_backend(errors, "leader_review", leader_backend)
    else:
        errors.append("leader_review.leader_backend must be an object")
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


def validate_leader_summary_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in LEADER_SUMMARY_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing leader_summary field: {field}")
    leader_backend = payload.get("leader_backend")
    if isinstance(leader_backend, dict):
        _validate_leader_backend(errors, "leader_summary", leader_backend)
    else:
        errors.append("leader_summary.leader_backend must be an object")
    plan_id = payload.get("plan_id")
    if plan_id:
        if payload.get("plan_status_command") != f"agentdeck plan status --plan-id {plan_id}":
            errors.append("plan_status_command must match plan_id")
        if payload.get("review_command") != f"agentdeck leader review --plan-id {plan_id}":
            errors.append("review_command must match plan_id")
    steps = payload.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                errors.append("leader summary steps must be objects")
                continue
            for field in LEADER_SUMMARY_STEP_FIELDS:
                if field not in step:
                    errors.append(f"missing leader summary step field: {field}")
            artifacts = step.get("artifacts")
            if isinstance(artifacts, list):
                for artifact in artifacts:
                    if not isinstance(artifact, dict):
                        errors.append("leader summary artifacts must be objects")
                        continue
                    for field in LEADER_SUMMARY_ARTIFACT_FIELDS:
                        if field not in artifact:
                            errors.append(f"missing leader summary artifact field: {field}")
            elif "artifacts" in step:
                errors.append("leader summary step artifacts must be a list")
    elif "steps" in payload:
        errors.append("steps must be a list")
    controls = payload.get("controls")
    if isinstance(controls, list):
        for control in controls:
            if not isinstance(control, dict):
                errors.append("leader summary controls must be objects")
                continue
            for field in LEADER_SUMMARY_CONTROL_FIELDS:
                if field not in control:
                    errors.append(f"missing leader summary control field: {field}")
            if "enabled" in control and not isinstance(control.get("enabled"), bool):
                errors.append("leader summary control enabled must be a boolean")
    elif "controls" in payload:
        errors.append("controls must be a list")
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
        for index, action in enumerate(actions):
            if not isinstance(action, dict):
                errors.append(
                    "leader actions items must be objects"
                    if index == 0
                    else f"leader actions items[{index}] must be an object"
                )
                continue
            for field in PROJECT_VIEW_LEADER_ACTION_ITEM_FIELDS:
                if field not in action:
                    errors.append(
                        f"missing leader action item field: {field}"
                        if index == 0
                        else f"missing leader action item field at index {index}: {field}"
                    )
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
    plan = payload.get("plan")
    if isinstance(plan, dict):
        for field in PROJECT_VIEW_PLAN_ITEM_FIELDS:
            if field not in plan:
                errors.append(f"missing trace plan field: {field}")
        leader_backend = plan.get("leader_backend")
        if isinstance(leader_backend, dict):
            _validate_leader_backend(errors, "trace.plan", leader_backend)
        else:
            errors.append("trace.plan.leader_backend must be an object")
    elif "plan" in payload and plan is not None:
        errors.append("plan must be an object or null")
    _validate_trace_items(errors, payload, "attempts", TRACE_ATTEMPT_FIELDS, "attempt")
    _validate_trace_items(errors, payload, "jobs", TRACE_JOB_FIELDS, "job")
    _validate_trace_items(errors, payload, "replies", TRACE_REPLY_FIELDS, "reply")
    _validate_trace_items(errors, payload, "artifacts", TRACE_ARTIFACT_FIELDS, "artifact")
    _validate_trace_items(errors, payload, "inbox_items", TRACE_INBOX_ITEM_FIELDS, "inbox item")
    controls = payload.get("controls")
    if isinstance(controls, list):
        for index, control in enumerate(controls):
            if not isinstance(control, dict):
                errors.append("trace controls must be objects")
                continue
            for field in LEADER_CHAT_INTENT_CONTROL_FIELDS:
                if field not in control:
                    errors.append(f"trace controls[{index}] missing control field: {field}")
            if control.get("kind") == "inspect":
                if control.get("safety") != "inspect":
                    errors.append("trace inspect control must use safety=inspect")
                if not str(control.get("command") or "").startswith("agentdeck trace --id "):
                    errors.append("trace inspect control command must use trace")
            if control.get("enabled") is False and not control.get("blocker"):
                errors.append("trace disabled controls must include blocker")
    elif "controls" in payload:
        errors.append("trace controls must be a list")
    return {"ok": not errors, "errors": errors}


def validate_artifacts_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    schema_version = payload.get("schema_version")
    if schema_version != PROJECT_VIEW_SCHEMA_VERSION:
        errors.append(f"schema_version mismatch: expected {PROJECT_VIEW_SCHEMA_VERSION}, got {schema_version}")
    for field in ARTIFACTS_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing artifacts response field: {field}")
    if payload.get("artifacts_command") != "agentdeck artifacts":
        errors.append("artifacts_command must be agentdeck artifacts")
    if payload.get("project_view_contract") != "agentdeck contract project-view":
        errors.append("project_view_contract must be agentdeck contract project-view")
    if payload.get("trace_contract") != "agentdeck contract trace":
        errors.append("trace_contract must be agentdeck contract trace")
    if payload.get("trace_command_template") != "agentdeck trace --id <id>":
        errors.append("trace_command_template must be agentdeck trace --id <id>")
    controls = payload.get("controls")
    if isinstance(controls, list):
        for index, control in enumerate(controls):
            if not isinstance(control, dict):
                errors.append("artifacts controls must be objects")
                continue
            for field in ARTIFACTS_CONTROL_FIELDS:
                if field not in control:
                    errors.append(f"artifacts controls[{index}] missing control field: {field}")
            if control.get("kind") == "inspect":
                if control.get("command") != "agentdeck artifacts":
                    errors.append("artifacts inspect control command must be agentdeck artifacts")
                if control.get("safety") != "inspect":
                    errors.append("artifacts inspect control must use safety=inspect")
            if control.get("enabled") is False and not control.get("blocker"):
                errors.append("artifacts disabled controls must include blocker")
    elif "controls" in payload:
        errors.append("artifacts controls must be a list")
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, dict):
        for field in ARTIFACTS_SUMMARY_FIELDS:
            if field not in artifacts:
                errors.append(f"missing artifacts summary field: {field}")
    elif "artifacts" in payload:
        errors.append("artifacts must be an object")
    _validate_project_view_summary_items(errors, payload, "artifacts", PROJECT_VIEW_ARTIFACT_ITEM_FIELDS, "artifact")
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
    for index, item in enumerate(collection):
        if not isinstance(item, dict):
            errors.append(
                f"{collection_name} items must be objects"
                if index == 0
                else f"{collection_name}[{index}] must be an object"
            )
            continue
        for field in fields:
            if field not in item:
                errors.append(
                    f"missing {label} field: {field}"
                    if index == 0
                    else f"missing {label} field at index {index}: {field}"
                )


def _prefixed_contract_error(prefix: str, message: str) -> str:
    return f"{prefix}: {message}" if prefix else message


def _runtime_agent_field_error(index: int, field: str) -> str:
    if index == 0:
        return f"missing runtime agent field: {field}"
    return f"runtime_card.agents[{index}] missing runtime agent field: {field}"


def _runtime_control_field_error(agent_index: int, control_index: int, field: str) -> str:
    if agent_index == 0 and control_index == 0:
        return f"missing runtime control field: {field}"
    return f"runtime_card.agents[{agent_index}].controls[{control_index}] missing runtime control field: {field}"


def _runtime_agent_controls_type_error(index: int) -> str:
    if index == 0:
        return "runtime agent controls must be a list"
    return f"runtime_card.agents[{index}].controls must be a list"


def _runtime_agent_controls_item_error(agent_index: int, control_index: int) -> str:
    if agent_index == 0 and control_index == 0:
        return "runtime agent controls items must be objects"
    return f"runtime_card.agents[{agent_index}].controls[{control_index}] must be an object"


def _runtime_agent_item_error(index: int) -> str:
    if index == 0:
        return "runtime_card.agents items must be objects"
    return f"runtime_card.agents[{index}] must be an object"


def _validate_runtime_agents_contract(errors: list[str], agents: list[object], *, prefix: str) -> None:
    for agent_index, agent in enumerate(agents):
        if not isinstance(agent, dict):
            errors.append(_prefixed_contract_error(prefix, _runtime_agent_item_error(agent_index)))
            continue
        for field in WORKBENCH_RUNTIME_AGENT_FIELDS:
            if field not in agent:
                errors.append(_prefixed_contract_error(prefix, _runtime_agent_field_error(agent_index, field)))
        controls = agent.get("controls")
        if isinstance(controls, list):
            for control_index, control in enumerate(controls):
                if isinstance(control, dict):
                    for field in WORKBENCH_RUNTIME_CONTROL_FIELDS:
                        if field not in control:
                            errors.append(
                                _prefixed_contract_error(
                                    prefix,
                                    _runtime_control_field_error(agent_index, control_index, field),
                                )
                            )
                else:
                    errors.append(
                        _prefixed_contract_error(
                            prefix,
                            _runtime_agent_controls_item_error(agent_index, control_index),
                        )
                    )
        elif "controls" in agent:
            errors.append(_prefixed_contract_error(prefix, _runtime_agent_controls_type_error(agent_index)))


def _validate_runtime_card_contract(errors: list[str], runtime_card: dict[str, object], *, prefix: str) -> None:
    for field in WORKBENCH_RUNTIME_CARD_FIELDS:
        if field not in runtime_card:
            errors.append(_prefixed_contract_error(prefix, f"missing runtime_card field: {field}"))
    agents = runtime_card.get("agents")
    if isinstance(agents, list):
        _validate_runtime_agents_contract(errors, agents, prefix=prefix)
    elif "agents" in runtime_card:
        errors.append(_prefixed_contract_error(prefix, "runtime_card.agents must be a list"))


def _validate_agent_ready_card_contract(errors: list[str], agent_ready_card: dict[str, object]) -> None:
    for field in AGENT_RUNTIME_READY_RESPONSE_FIELDS:
        if field not in agent_ready_card:
            errors.append(f"agent_ready_card: missing ready field: {field}")
    if agent_ready_card.get("mode") != "agent_runtime_ready":
        errors.append("agent_ready_card.mode must be agent_runtime_ready")
    runtime_card = agent_ready_card.get("runtime_card")
    if isinstance(runtime_card, dict):
        _validate_runtime_card_contract(errors, runtime_card, prefix="agent_ready_card.runtime_card")
    elif "runtime_card" in agent_ready_card:
        errors.append("agent_ready_card.runtime_card must be an object")
    spawn_commands = agent_ready_card.get("spawn_commands")
    if not isinstance(spawn_commands, list):
        errors.append("agent_ready_card.spawn_commands must be a list")
    controls = agent_ready_card.get("controls")
    if isinstance(controls, list):
        for control in controls:
            if not isinstance(control, dict):
                errors.append("agent_ready_card.controls items must be objects")
                continue
            for field in WORKBENCH_RUNTIME_CONTROL_FIELDS:
                if field not in control:
                    errors.append(f"agent_ready_card.controls: missing control field: {field}")
            if control.get("kind") == "inspect":
                if control.get("safety") != "inspect":
                    errors.append("agent_ready_card.controls: inspect must use safety=inspect")
                if control.get("command") != "agentdeck agent ready":
                    errors.append("agent_ready_card.controls: inspect command must be agentdeck agent ready")
            if control.get("kind") == "spawn_ready":
                if control.get("safety") != "explicit_runtime":
                    errors.append("agent_ready_card.controls: spawn_ready must use safety=explicit_runtime")
                if control.get("command") != "agentdeck agent spawn-ready --confirm":
                    errors.append("agent_ready_card.controls: spawn_ready command must be agentdeck agent spawn-ready --confirm")
            if control.get("kind") == "refresh_runtime":
                if control.get("safety") != "explicit_runtime":
                    errors.append("agent_ready_card.controls: refresh_runtime must use safety=explicit_runtime")
                if control.get("command") != "agentdeck agent refresh":
                    errors.append("agent_ready_card.controls: refresh_runtime command must be agentdeck agent refresh")
            if control.get("kind") == "dispatch_ready":
                if control.get("safety") != "explicit_runtime":
                    errors.append("agent_ready_card.controls: dispatch_ready must use safety=explicit_runtime")
                if control.get("command") != "agentdeck approval dispatch-ready --confirm":
                    errors.append("agent_ready_card.controls: dispatch_ready command must be agentdeck approval dispatch-ready --confirm")
            if control.get("enabled") is False and not control.get("blocker"):
                errors.append("agent_ready_card.controls: disabled controls must include blocker")
    elif "controls" in agent_ready_card:
        errors.append("agent_ready_card.controls must be a list")


def _validate_terminal_session_card_contract(
    errors: list[str], terminal_session_card: dict[str, object]
) -> None:
    for field in WORKBENCH_TERMINAL_SESSION_CARD_FIELDS:
        if field not in terminal_session_card:
            errors.append(f"missing terminal_session_card field: {field}")
    if terminal_session_card.get("mode") != "terminal_session":
        errors.append("terminal_session_card.mode must be terminal_session")
    for count_field in ("running_count", "agent_count"):
        if count_field in terminal_session_card and not isinstance(terminal_session_card.get(count_field), int):
            errors.append(f"terminal_session_card.{count_field} must be an integer")
    controls = terminal_session_card.get("controls")
    if isinstance(controls, list):
        for control in controls:
            if not isinstance(control, dict):
                errors.append("terminal_session controls must be objects")
                continue
            for field in WORKBENCH_TERMINAL_SESSION_CONTROL_FIELDS:
                if field not in control:
                    errors.append(f"missing terminal_session control field: {field}")
            if "enabled" in control and not isinstance(control.get("enabled"), bool):
                errors.append("terminal_session control enabled must be a boolean")
            if control.get("enabled") is False and not control.get("blocker"):
                errors.append("disabled terminal_session control requires blocker")
            if control.get("kind") == "attach_session":
                if "safety" in control and control.get("safety") != "inspect":
                    errors.append("terminal_session attach_session control must use safety=inspect")
                if not str(control.get("command") or "").startswith("tmux "):
                    errors.append("terminal_session attach_session control must use a tmux command")
            if control.get("kind") == "open_controls" and control.get("command") != "agentdeck controls":
                errors.append("terminal_session open_controls command must be agentdeck controls")
            if control.get("kind") == "refresh_runtime":
                if "safety" in control and control.get("safety") != "explicit_runtime":
                    errors.append("terminal_session refresh_runtime control must use safety=explicit_runtime")
                if control.get("command") != terminal_session_card.get("refresh_command"):
                    errors.append("terminal_session refresh_runtime command must match refresh_command")
    elif "controls" in terminal_session_card:
        errors.append("terminal_session_card.controls must be a list")
    terminals = terminal_session_card.get("terminals")
    if isinstance(terminals, list):
        for item in terminals:
            if not isinstance(item, dict):
                errors.append("terminal_session terminals must be objects")
                continue
            for field in WORKBENCH_TERMINAL_SESSION_ITEM_FIELDS:
                if field not in item:
                    errors.append(f"missing terminal_session item field: {field}")
            if "enabled" in item and not isinstance(item.get("enabled"), bool):
                errors.append("terminal_session item enabled must be a boolean")
            if item.get("enabled") is False and not item.get("blocker"):
                errors.append("disabled terminal_session item requires blocker")
            if item.get("enabled") is True and not item.get("select_pane_command"):
                errors.append("enabled terminal_session item requires select_pane_command")
            item_controls = item.get("controls")
            if isinstance(item_controls, list):
                for control in item_controls:
                    if not isinstance(control, dict):
                        errors.append("terminal_session item controls must be objects")
                        continue
                    for field in WORKBENCH_TERMINAL_SESSION_CONTROL_FIELDS:
                        if field not in control:
                            errors.append(f"missing terminal_session item control field: {field}")
                    if control.get("kind") == "select_pane":
                        if control.get("safety") != "inspect":
                            errors.append("terminal_session select_pane control must use safety=inspect")
                        if control.get("command") != item.get("select_pane_command"):
                            errors.append("terminal_session select_pane command must match select_pane_command")
                        if "enabled" in item and control.get("enabled") != item.get("enabled"):
                            errors.append("terminal_session select_pane enabled must match item enabled")
                    if control.get("enabled") is False and not control.get("blocker"):
                        errors.append("disabled terminal_session item control requires blocker")
            elif "controls" in item:
                errors.append("terminal_session item controls must be a list")
    elif "terminals" in terminal_session_card:
        errors.append("terminal_session_card.terminals must be a list")


def _validate_queue_card_contract(errors: list[str], queue_card: dict[str, object], *, prefix: str) -> None:
    for field in WORKBENCH_QUEUE_CARD_FIELDS:
        if field not in queue_card:
            errors.append(_prefixed_contract_error(prefix, f"missing queue_card field: {field}"))
    for section in ("leader_actions", "approvals", "inbox"):
        if section in queue_card and not isinstance(queue_card.get(section), dict):
            errors.append(_prefixed_contract_error(prefix, f"queue_card.{section} must be an object"))


def _validate_queue_card_project_view_alignment(
    errors: list[str], queue_card: dict[str, object], project_view: dict[str, object]
) -> None:
    leader_actions = queue_card.get("leader_actions")
    project_leader_actions = project_view.get("leader_actions")
    if isinstance(leader_actions, dict) and isinstance(project_leader_actions, dict):
        leader_status = project_leader_actions.get("by_status")
        expected_pending = leader_status.get("pending", 0) if isinstance(leader_status, dict) else 0
        for field, expected_value in (
            ("count", project_leader_actions.get("count")),
            ("pending", expected_pending),
            ("recommended_action_id", project_leader_actions.get("recommended_action_id")),
        ):
            if field in leader_actions and leader_actions.get(field) != expected_value:
                errors.append(f"queue_card.leader_actions.{field} must match project_view.leader_actions.{field}")

    approvals = queue_card.get("approvals")
    project_approvals = project_view.get("approvals")
    if isinstance(approvals, dict) and isinstance(project_approvals, dict):
        for field in ("count", "pending", "approved"):
            if field in approvals and approvals.get(field) != project_approvals.get(field):
                errors.append(f"queue_card.approvals.{field} must match project_view.approvals.{field}")

    inbox = queue_card.get("inbox")
    project_inbox = project_view.get("inbox")
    if isinstance(inbox, dict) and isinstance(project_inbox, dict):
        for field in ("total", "by_agent"):
            if field in inbox and inbox.get(field) != project_inbox.get(field):
                errors.append(f"queue_card.inbox.{field} must match project_view.inbox.{field}")


def _validate_operator_card_control_alignment(errors: list[str], operator_card: dict[str, object]) -> None:
    controls = operator_card.get("controls")
    if not isinstance(controls, list):
        return
    dispatch_ready_operator = operator_card.get("action_kind") == "approval_dispatch_ready"
    for control in controls:
        if not isinstance(control, dict):
            continue
        kind = control.get("kind")
        if kind == "preview" and control.get("command") != operator_card.get("preview_command"):
            errors.append("operator_card preview control command must match preview_command")
        if kind == "preview" and control.get("enabled") != (operator_card.get("preview_command") is not None):
            errors.append("operator_card preview control enabled must reflect preview_command")
        if kind == "preview" and control.get("blocker") is not None:
            errors.append("operator_card preview control blocker must be null")
        if kind == "apply" and control.get("command") != operator_card.get("apply_command"):
            errors.append("operator_card apply control command must match apply_command")
        if kind == "apply" and control.get("enabled") != (
            operator_card.get("can_apply") is True and operator_card.get("apply_command") is not None
        ):
            errors.append("operator_card apply control enabled must reflect can_apply and apply_command")
        if kind == "apply" and control.get("blocker") != operator_card.get("blocker"):
            errors.append("operator_card apply control blocker must match blocker")
        if not dispatch_ready_operator and kind in ("explicit", "capture_reply") and control.get(
            "command"
        ) != operator_card.get("explicit_command"):
            errors.append(f"operator_card {kind} control command must match explicit_command")
        if not dispatch_ready_operator and kind in ("explicit", "capture_reply") and control.get("enabled") != (
            operator_card.get("explicit_command") is not None and not operator_card.get("blocker")
        ):
            errors.append(f"operator_card {kind} control enabled must reflect explicit_command and blocker")
        if not dispatch_ready_operator and kind in ("explicit", "capture_reply"):
            expected_blocker = operator_card.get("blocker")
            if expected_blocker is None and operator_card.get("explicit_command") is None:
                expected_blocker = "no explicit command available"
            if control.get("blocker") != expected_blocker:
                errors.append(f"operator_card {kind} control blocker must match blocker")


def _validate_operator_card_contract(errors: list[str], operator_card: dict[str, object], *, prefix: str) -> None:
    for field in WORKBENCH_OPERATOR_CARD_FIELDS:
        if field not in operator_card:
            errors.append(f"{prefix}: missing operator_card field: {field}")
    if "requires_explicit_user" in operator_card and not isinstance(operator_card.get("requires_explicit_user"), bool):
        errors.append(f"{prefix}: operator_card.requires_explicit_user must be a boolean")
    if "can_apply" in operator_card and not isinstance(operator_card.get("can_apply"), bool):
        errors.append(f"{prefix}: operator_card.can_apply must be a boolean")


def _role_agent_field_error(index: int, field: str) -> str:
    if index == 0:
        return f"missing role agent field: {field}"
    return f"role_card.agents[{index}] missing role agent field: {field}"


def _role_control_field_error(agent_index: int, control_index: int, field: str) -> str:
    if agent_index == 0 and control_index == 0:
        return f"missing role control field: {field}"
    return f"role_card.agents[{agent_index}].controls[{control_index}] missing role control field: {field}"


def _role_agent_controls_type_error(index: int) -> str:
    if index == 0:
        return "role agent controls must be a list"
    return f"role_card.agents[{index}].controls must be a list"


def _role_agent_controls_item_error(agent_index: int, control_index: int) -> str:
    if agent_index == 0 and control_index == 0:
        return "role agent controls items must be objects"
    return f"role_card.agents[{agent_index}].controls[{control_index}] must be an object"


def _role_agent_item_error(index: int) -> str:
    if index == 0:
        return "role_card.agents items must be objects"
    return f"role_card.agents[{index}] must be an object"


def _validate_role_agents_contract(errors: list[str], role_agents: list[object], *, prefix: str) -> None:
    for agent_index, agent in enumerate(role_agents):
        if not isinstance(agent, dict):
            errors.append(_prefixed_contract_error(prefix, _role_agent_item_error(agent_index)))
            continue
        for field in WORKBENCH_ROLE_AGENT_FIELDS:
            if field not in agent:
                errors.append(_prefixed_contract_error(prefix, _role_agent_field_error(agent_index, field)))
        controls = agent.get("controls")
        if isinstance(controls, list):
            for control_index, control in enumerate(controls):
                if isinstance(control, dict):
                    for field in WORKBENCH_RUNTIME_CONTROL_FIELDS:
                        if field not in control:
                            errors.append(
                                _prefixed_contract_error(
                                    prefix,
                                    _role_control_field_error(agent_index, control_index, field),
                                )
                            )
                else:
                    errors.append(
                        _prefixed_contract_error(prefix, _role_agent_controls_item_error(agent_index, control_index))
                    )
        elif "controls" in agent:
            errors.append(_prefixed_contract_error(prefix, _role_agent_controls_type_error(agent_index)))


def _validate_role_card_contract(errors: list[str], role_card: dict[str, object], *, prefix: str) -> None:
    for field in WORKBENCH_ROLE_CARD_FIELDS:
        if field not in role_card:
            errors.append(_prefixed_contract_error(prefix, f"missing role_card field: {field}"))
    if "count" in role_card and not isinstance(role_card.get("count"), int):
        errors.append(_prefixed_contract_error(prefix, "role_card.count must be an integer"))
    role_agents = role_card.get("agents")
    if isinstance(role_agents, list):
        _validate_role_agents_contract(errors, role_agents, prefix=prefix)
    elif "agents" in role_card:
        errors.append(_prefixed_contract_error(prefix, "role_card.agents must be a list"))


def _validate_worker_lifecycle_card_contract(
    errors: list[str], worker_lifecycle_card: dict[str, object], *, prefix: str
) -> None:
    for field in WORKBENCH_WORKER_LIFECYCLE_CARD_FIELDS:
        if field not in worker_lifecycle_card:
            errors.append(_prefixed_contract_error(prefix, f"missing worker_lifecycle_card field: {field}"))
    if worker_lifecycle_card.get("mode") != "worker_lifecycle":
        errors.append(_prefixed_contract_error(prefix, "worker_lifecycle_card.mode must be worker_lifecycle"))
    if "count" in worker_lifecycle_card and not isinstance(worker_lifecycle_card.get("count"), int):
        errors.append(_prefixed_contract_error(prefix, "worker_lifecycle_card.count must be an integer"))
    if "by_stage" in worker_lifecycle_card and not isinstance(worker_lifecycle_card.get("by_stage"), dict):
        errors.append(_prefixed_contract_error(prefix, "worker_lifecycle_card.by_stage must be an object"))
    controls = worker_lifecycle_card.get("controls")
    if isinstance(controls, list):
        for control in controls:
            if not isinstance(control, dict):
                errors.append(_prefixed_contract_error(prefix, "worker_lifecycle_card.controls items must be objects"))
                continue
            for field in WORKBENCH_RUNTIME_CONTROL_FIELDS:
                if field not in control:
                    errors.append(
                        _prefixed_contract_error(
                            prefix, f"worker_lifecycle_card.controls: missing control field: {field}"
                        )
                    )
            if control.get("safety") != "inspect":
                errors.append(
                    _prefixed_contract_error(prefix, "worker_lifecycle_card.controls must use safety=inspect")
                )
            if control.get("enabled") is False and not control.get("blocker"):
                errors.append(
                    _prefixed_contract_error(
                        prefix, "worker_lifecycle_card.controls: disabled controls must include blocker"
                    )
                )
    elif "controls" in worker_lifecycle_card:
        errors.append(_prefixed_contract_error(prefix, "worker_lifecycle_card.controls must be a list"))
    items = worker_lifecycle_card.get("items")
    if isinstance(items, list):
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(_prefixed_contract_error(prefix, f"worker_lifecycle_card.items[{index}] must be an object"))
                continue
            for field in WORKBENCH_WORKER_LIFECYCLE_ITEM_FIELDS:
                if field not in item:
                    if index == 0:
                        errors.append(_prefixed_contract_error(prefix, f"missing worker_lifecycle item field: {field}"))
                    else:
                        errors.append(
                            _prefixed_contract_error(
                                prefix, f"worker_lifecycle_card.items[{index}] missing field: {field}"
                            )
                        )
            for count_field in ("artifact_count", "pending_inbox_count"):
                if count_field in item and not isinstance(item.get(count_field), int):
                    errors.append(
                        _prefixed_contract_error(
                            prefix, f"worker_lifecycle_card.items[{index}].{count_field} must be an integer"
                        )
                    )
            item_controls = item.get("controls")
            if isinstance(item_controls, list):
                for control in item_controls:
                    if not isinstance(control, dict):
                        errors.append(
                            _prefixed_contract_error(
                                prefix, f"worker_lifecycle_card.items[{index}].controls items must be objects"
                            )
                        )
                        continue
                    for field in WORKBENCH_RUNTIME_CONTROL_FIELDS:
                        if field not in control:
                            errors.append(
                                _prefixed_contract_error(
                                    prefix, f"worker_lifecycle item control missing field: {field}"
                                )
                            )
                    if control.get("safety") != "inspect":
                        errors.append(
                            _prefixed_contract_error(
                                prefix, "worker_lifecycle item controls must use safety=inspect"
                            )
                        )
                    if control.get("kind") == "trace":
                        if control.get("command") != item.get("trace_command"):
                            errors.append(
                                _prefixed_contract_error(
                                    prefix, "worker_lifecycle trace control command must match trace_command"
                                )
                            )
                    if control.get("kind") == "inbox":
                        if control.get("command") != item.get("inbox_command"):
                            errors.append(
                                _prefixed_contract_error(
                                    prefix, "worker_lifecycle inbox control command must match inbox_command"
                                )
                            )
                    if control.get("kind") == "terminal":
                        if control.get("command") != item.get("terminal_command"):
                            errors.append(
                                _prefixed_contract_error(
                                    prefix, "worker_lifecycle terminal control command must match terminal_command"
                                )
                            )
                    if control.get("kind") == "capture":
                        if control.get("command") != item.get("capture_command"):
                            errors.append(
                                _prefixed_contract_error(
                                    prefix, "worker_lifecycle capture control command must match capture_command"
                                )
                            )
                    if control.get("enabled") is False and not control.get("blocker"):
                        errors.append(
                            _prefixed_contract_error(
                                prefix,
                                "worker_lifecycle item controls: disabled controls must include blocker",
                            )
                        )
            elif "controls" in item:
                errors.append(
                    _prefixed_contract_error(prefix, f"worker_lifecycle_card.items[{index}].controls must be a list")
                )
    elif "items" in worker_lifecycle_card:
        errors.append(_prefixed_contract_error(prefix, "worker_lifecycle_card.items must be a list"))


def _validate_role_topology_card_contract(
    errors: list[str], role_topology_card: dict[str, object], *, prefix: str
) -> None:
    for field in WORKBENCH_ROLE_TOPOLOGY_CARD_FIELDS:
        if field not in role_topology_card:
            errors.append(_prefixed_contract_error(prefix, f"missing role_topology_card field: {field}"))
    if role_topology_card.get("mode") != "role_topology":
        errors.append(_prefixed_contract_error(prefix, "role_topology_card.mode must be role_topology"))
    if role_topology_card.get("source_command") != "agentdeck workbench":
        errors.append(
            _prefixed_contract_error(prefix, "role_topology_card.source_command must be agentdeck workbench")
        )
    for count_field in ("count", "logical_role_count", "worker_role_count", "blocked_count"):
        if count_field in role_topology_card and not isinstance(role_topology_card.get(count_field), int):
            errors.append(_prefixed_contract_error(prefix, f"role_topology_card.{count_field} must be an integer"))
    if "by_status" in role_topology_card and not isinstance(role_topology_card.get("by_status"), dict):
        errors.append(_prefixed_contract_error(prefix, "role_topology_card.by_status must be an object"))
    roles_for_counts = role_topology_card.get("roles")
    if isinstance(roles_for_counts, list) and isinstance(role_topology_card.get("blocked_count"), int):
        actual_blocked = sum(
            1 for role in roles_for_counts if isinstance(role, dict) and role.get("blocker")
        )
        if role_topology_card.get("blocked_count") != actual_blocked:
            errors.append(
                _prefixed_contract_error(prefix, "role_topology_card.blocked_count must match roles carrying a blocker")
            )
    controls = role_topology_card.get("controls")
    if isinstance(controls, list):
        for control in controls:
            if not isinstance(control, dict):
                errors.append(_prefixed_contract_error(prefix, "role_topology_card.controls items must be objects"))
                continue
            for field in WORKBENCH_RUNTIME_CONTROL_FIELDS:
                if field not in control:
                    errors.append(
                        _prefixed_contract_error(prefix, f"role_topology_card.controls missing field: {field}")
                    )
            if control.get("safety") != "inspect":
                errors.append(_prefixed_contract_error(prefix, "role_topology_card.controls must use safety=inspect"))
            if control.get("enabled") is False and not control.get("blocker"):
                errors.append(
                    _prefixed_contract_error(prefix, "role_topology_card.controls disabled controls need blocker")
                )
    elif "controls" in role_topology_card:
        errors.append(_prefixed_contract_error(prefix, "role_topology_card.controls must be a list"))
    roles = role_topology_card.get("roles")
    if isinstance(roles, list):
        for index, role in enumerate(roles):
            if not isinstance(role, dict):
                errors.append(_prefixed_contract_error(prefix, f"role_topology_card.roles[{index}] must be an object"))
                continue
            for field in WORKBENCH_ROLE_TOPOLOGY_ITEM_FIELDS:
                if field not in role:
                    if index == 0:
                        errors.append(_prefixed_contract_error(prefix, f"missing role_topology item field: {field}"))
                    else:
                        errors.append(
                            _prefixed_contract_error(
                                prefix, f"role_topology_card.roles[{index}] missing field: {field}"
                            )
                        )
            kind = role.get("kind")
            if kind not in {"logical_role", "worker"}:
                errors.append(
                    _prefixed_contract_error(prefix, f"role_topology_card.roles[{index}].kind must be logical_role or worker")
                )
            if kind == "logical_role":
                if role.get("runtime_kind") != "logical_role":
                    errors.append(_prefixed_contract_error(prefix, "role_topology logical roles must use runtime_kind=logical_role"))
                if role.get("pane_backed") is not False:
                    errors.append(_prefixed_contract_error(prefix, "role_topology logical roles must not be pane-backed"))
                if role.get("pane_id") is not None:
                    errors.append(_prefixed_contract_error(prefix, "role_topology logical roles must keep pane_id null"))
                if role.get("agent_id") is not None:
                    errors.append(_prefixed_contract_error(prefix, "role_topology logical roles must keep agent_id null"))
            elif kind == "worker":
                if role.get("runtime_kind") != "worker_pane":
                    errors.append(_prefixed_contract_error(prefix, "role_topology worker roles must use runtime_kind=worker_pane"))
                if not isinstance(role.get("pane_backed"), bool):
                    errors.append(_prefixed_contract_error(prefix, "role_topology worker roles must set boolean pane_backed"))
            role_controls = role.get("controls")
            if isinstance(role_controls, list):
                for control in role_controls:
                    if not isinstance(control, dict):
                        errors.append(
                            _prefixed_contract_error(prefix, f"role_topology_card.roles[{index}].controls items must be objects")
                        )
                        continue
                    for field in WORKBENCH_RUNTIME_CONTROL_FIELDS:
                        if field not in control:
                            errors.append(
                                _prefixed_contract_error(prefix, f"role_topology role control missing field: {field}")
                            )
                    if control.get("safety") != "inspect":
                        errors.append(
                            _prefixed_contract_error(prefix, "role_topology role controls must use safety=inspect")
                        )
                    if control.get("command") != role.get("next_command"):
                        errors.append(
                            _prefixed_contract_error(prefix, "role_topology role control command must match next_command")
                        )
            elif "controls" in role:
                errors.append(_prefixed_contract_error(prefix, f"role_topology_card.roles[{index}].controls must be a list"))
    elif "roles" in role_topology_card:
        errors.append(_prefixed_contract_error(prefix, "role_topology_card.roles must be a list"))


def _validate_review_gate_card_contract(
    errors: list[str], review_gate_card: dict[str, object], *, prefix: str
) -> None:
    for field in WORKBENCH_REVIEW_GATE_CARD_FIELDS:
        if field not in review_gate_card:
            errors.append(_prefixed_contract_error(prefix, f"missing review_gate_card field: {field}"))
    if review_gate_card.get("mode") != "review_gate":
        errors.append(_prefixed_contract_error(prefix, "review_gate_card.mode must be review_gate"))
    if "can_release" in review_gate_card and not isinstance(review_gate_card.get("can_release"), bool):
        errors.append(_prefixed_contract_error(prefix, "review_gate_card.can_release must be a boolean"))
    for count_field in ("artifact_count", "review_reply_count"):
        if count_field in review_gate_card and not isinstance(review_gate_card.get(count_field), int):
            errors.append(_prefixed_contract_error(prefix, f"review_gate_card.{count_field} must be an integer"))
    controls = review_gate_card.get("controls")
    if isinstance(controls, list):
        for control in controls:
            if not isinstance(control, dict):
                errors.append(_prefixed_contract_error(prefix, "review_gate_card.controls items must be objects"))
                continue
            for field in WORKBENCH_RUNTIME_CONTROL_FIELDS:
                if field not in control:
                    errors.append(
                        _prefixed_contract_error(prefix, f"review_gate_card.controls missing field: {field}")
                    )
            kind = control.get("kind")
            if kind == "inspect" and control.get("safety") != "inspect":
                errors.append(_prefixed_contract_error(prefix, "review_gate inspect controls must use safety=inspect"))
            if kind in {"assign_code_reviewer", "assign_round_reviewer"}:
                if control.get("safety") != "explicit_user":
                    errors.append(
                        _prefixed_contract_error(prefix, "review_gate assign controls must use safety=explicit_user")
                    )
                if control.get("enabled") is not False:
                    errors.append(_prefixed_contract_error(prefix, "review_gate assign controls must be disabled"))
                command = str(control.get("command") or "")
                if not command.startswith("agentdeck agent assign-role --agent <agent_id> --role "):
                    errors.append(
                        _prefixed_contract_error(prefix, "review_gate assign controls must use assign-role template")
                    )
            if kind not in {"inspect", "assign_code_reviewer", "assign_round_reviewer"}:
                errors.append(_prefixed_contract_error(prefix, f"unknown review_gate control kind: {kind}"))
            if control.get("enabled") is False and not control.get("blocker"):
                errors.append(
                    _prefixed_contract_error(prefix, "review_gate_card.controls disabled controls need blocker")
                )
    elif "controls" in review_gate_card:
        errors.append(_prefixed_contract_error(prefix, "review_gate_card.controls must be a list"))
    for stage_name in ("code_review", "round_review"):
        stage = review_gate_card.get(stage_name)
        if isinstance(stage, dict):
            _validate_review_gate_stage_contract(errors, stage, prefix=prefix)
        elif stage_name in review_gate_card:
            errors.append(_prefixed_contract_error(prefix, f"review_gate_card.{stage_name} must be an object"))


def _validate_review_gate_stage_contract(
    errors: list[str], stage: dict[str, object], *, prefix: str
) -> None:
    for field in WORKBENCH_REVIEW_GATE_STAGE_FIELDS:
        if field not in stage:
            errors.append(_prefixed_contract_error(prefix, f"missing review_gate stage field: {field}"))
    controls = stage.get("controls")
    if isinstance(controls, list):
        for control in controls:
            if not isinstance(control, dict):
                errors.append(_prefixed_contract_error(prefix, "review_gate stage controls items must be objects"))
                continue
            for field in WORKBENCH_RUNTIME_CONTROL_FIELDS:
                if field not in control:
                    errors.append(
                        _prefixed_contract_error(prefix, f"review_gate stage control missing field: {field}")
                    )
            if control.get("safety") != "inspect":
                errors.append(_prefixed_contract_error(prefix, "review_gate stage controls must use safety=inspect"))
            if control.get("kind") == "trace" and control.get("command") != stage.get("trace_command"):
                errors.append(_prefixed_contract_error(prefix, "review_gate trace command must match trace_command"))
            if control.get("kind") == "inbox" and control.get("command") != stage.get("inbox_command"):
                errors.append(_prefixed_contract_error(prefix, "review_gate inbox command must match inbox_command"))
            if control.get("enabled") is False and not control.get("blocker"):
                errors.append(_prefixed_contract_error(prefix, "review_gate stage disabled controls need blocker"))
    elif "controls" in stage:
        errors.append(_prefixed_contract_error(prefix, "review_gate stage controls must be a list"))


def _validate_release_preview_card_contract(
    errors: list[str], release_preview_card: dict[str, object], *, prefix: str
) -> None:
    for field in WORKBENCH_RELEASE_PREVIEW_CARD_FIELDS:
        if field not in release_preview_card:
            errors.append(_prefixed_contract_error(prefix, f"missing release_preview_card field: {field}"))
    if release_preview_card.get("mode") != "release_preview":
        errors.append(_prefixed_contract_error(prefix, "release_preview_card.mode must be release_preview"))
    if release_preview_card.get("source_command") != "agentdeck workbench":
        errors.append(_prefixed_contract_error(prefix, "release_preview_card.source_command must be agentdeck workbench"))
    if release_preview_card.get("status") not in {"blocked", "ready", "released"}:
        errors.append(_prefixed_contract_error(prefix, "release_preview_card.status must be blocked, ready, or released"))
    if "can_release" in release_preview_card and not isinstance(release_preview_card.get("can_release"), bool):
        errors.append(_prefixed_contract_error(prefix, "release_preview_card.can_release must be a boolean"))
    if "already_released" in release_preview_card and not isinstance(
        release_preview_card.get("already_released"), bool
    ):
        errors.append(_prefixed_contract_error(prefix, "release_preview_card.already_released must be a boolean"))
    if "release_count" in release_preview_card and not isinstance(release_preview_card.get("release_count"), int):
        errors.append(_prefixed_contract_error(prefix, "release_preview_card.release_count must be an integer"))
    if release_preview_card.get("status") == "blocked" and not release_preview_card.get("reason"):
        errors.append(_prefixed_contract_error(prefix, "blocked release_preview_card requires reason"))
    next_round_template = "agentdeck leader plan --task <goal>"
    if release_preview_card.get("status") == "ready":
        if release_preview_card.get("can_release") is not True:
            errors.append(_prefixed_contract_error(prefix, "ready release_preview_card requires can_release=true"))
        if release_preview_card.get("release_command") != "agentdeck release --confirm":
            errors.append(
                _prefixed_contract_error(prefix, "ready release_preview_card must expose the explicit release command")
            )
        if release_preview_card.get("next_command") != release_preview_card.get("release_command"):
            errors.append(
                _prefixed_contract_error(prefix, "release_preview_card.next_command must match release_command")
            )
        if release_preview_card.get("next_round_command") != next_round_template:
            errors.append(
                _prefixed_contract_error(prefix, "ready release_preview_card must expose the next-round plan template")
            )
    elif release_preview_card.get("status") == "released":
        if release_preview_card.get("can_release") is not False:
            errors.append(
                _prefixed_contract_error(prefix, "released release_preview_card must keep can_release false")
            )
        if release_preview_card.get("already_released") is not True:
            errors.append(
                _prefixed_contract_error(prefix, "released release_preview_card must set already_released true")
            )
        if release_preview_card.get("reason") != "round already released":
            errors.append(
                _prefixed_contract_error(prefix, "released release_preview_card must use reason round already released")
            )
        for command_field in ("next_command", "release_command"):
            if release_preview_card.get(command_field) is not None:
                errors.append(
                    _prefixed_contract_error(
                        prefix, f"released release_preview_card must keep {command_field} null"
                    )
                )
        if release_preview_card.get("next_round_command") != next_round_template:
            errors.append(
                _prefixed_contract_error(
                    prefix, "released release_preview_card must expose the next-round plan template"
                )
            )
    else:
        if release_preview_card.get("already_released") is not False:
            errors.append(
                _prefixed_contract_error(prefix, "blocked release_preview_card must keep already_released false")
            )
        for command_field in ("next_command", "release_command", "next_round_command"):
            if release_preview_card.get(command_field) is not None:
                errors.append(
                    _prefixed_contract_error(
                        prefix, f"blocked release_preview_card must keep {command_field} null"
                    )
                )
    controls = release_preview_card.get("controls")
    if isinstance(controls, list):
        seen_kinds: set[object] = set()
        for control in controls:
            if not isinstance(control, dict):
                errors.append(_prefixed_contract_error(prefix, "release_preview_card.controls items must be objects"))
                continue
            for field in WORKBENCH_RUNTIME_CONTROL_FIELDS:
                if field not in control:
                    errors.append(
                        _prefixed_contract_error(prefix, f"release_preview_card.controls missing field: {field}")
                    )
            kind = control.get("kind")
            seen_kinds.add(kind)
            if kind == "inspect_review_gate":
                if control.get("command") != "agentdeck workbench":
                    errors.append(
                        _prefixed_contract_error(prefix, "release preview inspect command must be agentdeck workbench")
                    )
                if control.get("safety") != "inspect":
                    errors.append(_prefixed_contract_error(prefix, "release preview inspect must use safety=inspect"))
            elif kind == "release_preview":
                if control.get("safety") != "explicit_user":
                    errors.append(
                        _prefixed_contract_error(prefix, "release preview explicit controls must use safety=explicit_user")
                    )
                if control.get("enabled") is True and release_preview_card.get("can_release") is not True:
                    errors.append(
                        _prefixed_contract_error(prefix, "release preview release control requires can_release=true")
                    )
                if control.get("command") != release_preview_card.get("release_command"):
                    errors.append(
                        _prefixed_contract_error(prefix, "release preview release command must match release_command")
                    )
            elif kind == "next_round_preview":
                if control.get("safety") != "explicit_user":
                    errors.append(
                        _prefixed_contract_error(prefix, "release preview explicit controls must use safety=explicit_user")
                    )
                if control.get("enabled") is not False:
                    errors.append(
                        _prefixed_contract_error(prefix, "release preview next-round control must stay disabled")
                    )
                if control.get("command") != release_preview_card.get("next_round_command"):
                    errors.append(
                        _prefixed_contract_error(prefix, "release preview next-round command must match next_round_command")
                    )
            else:
                errors.append(_prefixed_contract_error(prefix, f"unknown release_preview control kind: {kind}"))
            if control.get("enabled") is False and not control.get("blocker"):
                errors.append(
                    _prefixed_contract_error(prefix, "release_preview_card.controls disabled controls need blocker")
                )
        for required_kind in {"inspect_review_gate", "release_preview", "next_round_preview"}:
            if required_kind not in seen_kinds:
                errors.append(_prefixed_contract_error(prefix, f"release_preview_card missing {required_kind} control"))
    elif "controls" in release_preview_card:
        errors.append(_prefixed_contract_error(prefix, "release_preview_card.controls must be a list"))


def _validate_ledger_card_contract(errors: list[str], ledger_card: dict[str, object], *, prefix: str) -> None:
    for field in WORKBENCH_LEDGER_CARD_FIELDS:
        if field not in ledger_card:
            errors.append(_prefixed_contract_error(prefix, f"missing ledger_card field: {field}"))
    for section in ("messages", "jobs", "replies", "artifacts", "inbox"):
        if section in ledger_card and not isinstance(ledger_card.get(section), dict):
            errors.append(_prefixed_contract_error(prefix, f"ledger_card.{section} must be an object"))
    trace_commands = ledger_card.get("trace_commands")
    if isinstance(trace_commands, list):
        if not all(isinstance(command, str) for command in trace_commands):
            errors.append(_prefixed_contract_error(prefix, "ledger_card.trace_commands must contain strings"))
        trace_command_set = {command for command in trace_commands if isinstance(command, str)}
        for section in ("messages", "jobs", "replies", "artifacts"):
            section_card = ledger_card.get(section)
            items = section_card.get("items") if isinstance(section_card, dict) else None
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                trace_command = item.get("trace_command")
                if isinstance(trace_command, str) and trace_command not in trace_command_set:
                    errors.append(
                        _prefixed_contract_error(
                            prefix,
                            f"ledger_card.trace_commands missing trace command: {trace_command}",
                        )
                    )
    elif "trace_commands" in ledger_card:
        errors.append(_prefixed_contract_error(prefix, "ledger_card.trace_commands must be a list"))
    controls = ledger_card.get("controls")
    if isinstance(controls, list):
        for control in controls:
            if not isinstance(control, dict):
                errors.append(_prefixed_contract_error(prefix, "ledger_card.controls items must be objects"))
                continue
            for field in WORKBENCH_RUNTIME_CONTROL_FIELDS:
                if field not in control:
                    errors.append(
                        _prefixed_contract_error(prefix, f"ledger_card.controls: missing control field: {field}")
                    )
            if control.get("kind") == "inspect":
                if control.get("safety") != "inspect":
                    errors.append(
                        _prefixed_contract_error(prefix, "ledger_card.controls: inspect must use safety=inspect")
                    )
                if control.get("command") != "agentdeck workbench":
                    errors.append(
                        _prefixed_contract_error(
                            prefix,
                            "ledger_card.controls: inspect command must be agentdeck workbench",
                        )
                    )
            if control.get("enabled") is False and not control.get("blocker"):
                errors.append(
                    _prefixed_contract_error(prefix, "ledger_card.controls: disabled controls must include blocker")
                )
    elif "controls" in ledger_card:
        errors.append(_prefixed_contract_error(prefix, "ledger_card.controls must be a list"))


def _validate_lineage_card_contract(errors: list[str], lineage_card: dict[str, object], *, prefix: str) -> None:
    for field in WORKBENCH_LINEAGE_CARD_FIELDS:
        if field not in lineage_card:
            errors.append(_prefixed_contract_error(prefix, f"missing lineage_card field: {field}"))
    for count_field in ("message_count", "job_count", "reply_count", "inbox_count"):
        if count_field in lineage_card and not isinstance(lineage_card.get(count_field), int):
            errors.append(_prefixed_contract_error(prefix, f"lineage_card.{count_field} must be an integer"))
    recent_paths = lineage_card.get("recent_paths")
    if isinstance(recent_paths, list):
        path_counts = {"message": 0, "job": 0, "reply": 0, "inbox": 0}
        for path in recent_paths:
            if not isinstance(path, dict):
                errors.append(_prefixed_contract_error(prefix, "lineage paths must be objects"))
                continue
            for field in WORKBENCH_LINEAGE_PATH_FIELDS:
                if field not in path:
                    errors.append(_prefixed_contract_error(prefix, f"missing lineage path field: {field}"))
            if path.get("message_id"):
                path_counts["message"] += 1
            if path.get("job_id"):
                path_counts["job"] += 1
            if path.get("reply_id"):
                path_counts["reply"] += 1
            if path.get("inbox_id"):
                path_counts["inbox"] += 1
        for count_name, path_key in (
            ("message_count", "message"),
            ("job_count", "job"),
            ("reply_count", "reply"),
            ("inbox_count", "inbox"),
        ):
            if isinstance(lineage_card.get(count_name), int) and lineage_card.get(count_name) < path_counts[path_key]:
                errors.append(
                    _prefixed_contract_error(
                        prefix,
                        f"lineage_card.{count_name} must cover recent_paths with {path_key}_id",
                    )
                )
    elif "recent_paths" in lineage_card:
        errors.append(_prefixed_contract_error(prefix, "lineage_card.recent_paths must be a list"))


def _validate_audit_card_contract(errors: list[str], audit_card: dict[str, object], *, prefix: str) -> None:
    for field in WORKBENCH_AUDIT_CARD_FIELDS:
        if field not in audit_card:
            errors.append(f"{prefix}: missing audit_card field: {field}")
    recent_events = audit_card.get("recent_events")
    if isinstance(recent_events, list):
        for index, event in enumerate(recent_events):
            if not isinstance(event, dict):
                errors.append(f"{prefix}.recent_events[{index}] must be an object")
                continue
            for field in WORKBENCH_AUDIT_EVENT_FIELDS:
                if field not in event:
                    errors.append(f"{prefix}.recent_events[{index}] missing event field: {field}")
        if isinstance(audit_card.get("event_count"), int) and audit_card.get("event_count") != len(recent_events):
            errors.append(f"{prefix}: audit_card.event_count must match recent_events length")
    elif "recent_events" in audit_card:
        errors.append(f"{prefix}: audit_card.recent_events must be a list")
    if "event_count" in audit_card and not isinstance(audit_card.get("event_count"), int):
        errors.append(f"{prefix}: audit_card.event_count must be an integer")
    controls = audit_card.get("controls")
    if isinstance(controls, list):
        for control in controls:
            if not isinstance(control, dict):
                errors.append(f"{prefix}.controls items must be objects")
                continue
            for field in WORKBENCH_LEADER_CONTROL_FIELDS:
                if field not in control:
                    errors.append(f"{prefix}.controls: missing control field: {field}")
            if control.get("kind") == "inspect":
                if "events_command" in audit_card and control.get("command") != audit_card.get("events_command"):
                    errors.append(f"{prefix}.controls: inspect command must match audit_card.events_command")
                if control.get("safety") != "inspect":
                    errors.append(f"{prefix}.controls: inspect controls must use safety=inspect")
            if control.get("enabled") is False and not control.get("blocker"):
                errors.append(f"{prefix}.controls: disabled controls must include blocker")
    elif "controls" in audit_card:
        errors.append(f"{prefix}: audit_card.controls must be a list")


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
        if explanation.get("next_command") != payload.get("next_command"):
            errors.append("leader_explanation.next_command must match response next_command")
    elif "leader_explanation" in payload:
        errors.append("leader_explanation must be an object")
    explanation_action_kind = (
        explanation.get("action_kind")
        if isinstance(explanation, dict)
        else None
    )
    intent_card = payload.get("intent_card")
    if isinstance(intent_card, dict):
        for field in LEADER_CHAT_INTENT_CARD_FIELDS:
            if field not in intent_card:
                errors.append(f"missing intent_card field: {field}")
        if intent_card.get("next_command") != payload.get("next_command"):
            errors.append("intent_card: next_command must match response next_command")
        if (
            isinstance(explanation, dict)
            and "requires_explicit_user" in explanation
            and "requires_explicit_user" in intent_card
            and explanation.get("requires_explicit_user") != intent_card.get("requires_explicit_user")
        ):
            errors.append("leader_explanation.requires_explicit_user must match intent_card.requires_explicit_user")
        secondary_embedded_cards = intent_card.get("secondary_embedded_cards")
        if isinstance(secondary_embedded_cards, list):
            if not all(isinstance(card_name, str) for card_name in secondary_embedded_cards):
                errors.append("intent_card.secondary_embedded_cards must contain strings")
            if intent_card.get("embedded_card") in secondary_embedded_cards:
                errors.append("intent_card.secondary_embedded_cards must not repeat embedded_card")
            if (
                "startup_preview_card" in secondary_embedded_cards
                and payload.get("startup_preview_card") is None
            ):
                errors.append("intent_card.secondary_embedded_cards references missing startup_preview_card")
            if (
                "runtime_card" in secondary_embedded_cards
                and payload.get("runtime_card") is None
            ):
                errors.append("intent_card.secondary_embedded_cards references missing runtime_card")
            if (
                "runtime_action_card" in secondary_embedded_cards
                and payload.get("runtime_action_card") is None
            ):
                errors.append("intent_card.secondary_embedded_cards references missing runtime_action_card")
            if (
                "terminal_session_card" in secondary_embedded_cards
                and payload.get("terminal_session_card") is None
            ):
                errors.append("intent_card.secondary_embedded_cards references missing terminal_session_card")
            if (
                "provider_switch_card" in secondary_embedded_cards
                and payload.get("provider_switch_card") is None
            ):
                errors.append("intent_card.secondary_embedded_cards references missing provider_switch_card")
            if (
                "provider_setup_card" in secondary_embedded_cards
                and payload.get("provider_setup_card") is None
            ):
                errors.append("intent_card.secondary_embedded_cards references missing provider_setup_card")
            if (
                "control_registry_card" in secondary_embedded_cards
                and payload.get("control_registry_card") is None
            ):
                errors.append("intent_card.secondary_embedded_cards references missing control_registry_card")
            if (
                "mission_preview_card" in secondary_embedded_cards
                and payload.get("mission_preview_card") is None
            ):
                errors.append("intent_card.secondary_embedded_cards references missing mission_preview_card")
            if (
                "inbox_card" in secondary_embedded_cards
                and payload.get("inbox_card") is None
            ):
                errors.append("intent_card.secondary_embedded_cards references missing inbox_card")
            if (
                "approval_card" in secondary_embedded_cards
                and payload.get("approval_card") is None
            ):
                errors.append("intent_card.secondary_embedded_cards references missing approval_card")
            if (
                "dispatch_preview_card" in secondary_embedded_cards
                and payload.get("dispatch_preview_card") is None
            ):
                errors.append("intent_card.secondary_embedded_cards references missing dispatch_preview_card")
            if (
                "dispatch_batch_preview_card" in secondary_embedded_cards
                and payload.get("dispatch_batch_preview_card") is None
            ):
                errors.append("intent_card.secondary_embedded_cards references missing dispatch_batch_preview_card")
            if explanation_action_kind in {"approval_dispatch", "approval_dispatch_batch"}:
                for card_name in ["approval_card", "control_registry_card"]:
                    if card_name not in secondary_embedded_cards:
                        errors.append(
                            f"intent_card.secondary_embedded_cards must include {card_name} for {explanation_action_kind} responses"
                        )
            if (
                explanation_action_kind == "provider_setup"
                and isinstance(payload.get("provider_setup_card"), dict)
                and payload["provider_setup_card"].get("recommended_command") == payload.get("next_command")
            ):
                for card_name in ["provider_setup_card", "provider_switch_card", "control_registry_card"]:
                    if payload.get(card_name) is not None and card_name not in secondary_embedded_cards:
                        errors.append(
                            f"intent_card.secondary_embedded_cards must include {card_name} for provider_setup setup responses"
                        )
            if (
                explanation_action_kind == "provider_switch"
                and payload.get("provider_switch_card") is not None
                and "provider_switch_card" not in secondary_embedded_cards
            ):
                errors.append(
                    "intent_card.secondary_embedded_cards must include provider_switch_card for provider_switch setup responses"
                )
            if (
                explanation_action_kind == "runtime_ready"
                and payload.get("agent_ready_card") is not None
            ):
                for card_name in ["startup_preview_card", "runtime_card", "terminal_session_card", "control_registry_card"]:
                    if payload.get(card_name) is not None and card_name not in secondary_embedded_cards:
                        errors.append(
                            f"intent_card.secondary_embedded_cards must include {card_name} for runtime_ready responses"
                        )
            if (
                explanation_action_kind == "leader_status"
                and payload.get("leader_status_card") is not None
                and "control_registry_card" not in secondary_embedded_cards
            ):
                errors.append(
                    "intent_card.secondary_embedded_cards must include control_registry_card for leader_status responses"
                )
            if (
                explanation_action_kind == "leader_summary"
                and payload.get("leader_summary_card") is not None
                and "control_registry_card" not in secondary_embedded_cards
            ):
                errors.append(
                    "intent_card.secondary_embedded_cards must include control_registry_card for leader_summary responses"
                )
            if (
                explanation_action_kind == "run_progress"
                and payload.get("run_progress_card") is not None
                and "control_registry_card" not in secondary_embedded_cards
            ):
                errors.append(
                    "intent_card.secondary_embedded_cards must include control_registry_card for run_progress responses"
                )
            if (
                explanation_action_kind == "mission_preview"
                and payload.get("mission_preview_card") is not None
                and "control_registry_card" not in secondary_embedded_cards
            ):
                errors.append(
                    "intent_card.secondary_embedded_cards must include control_registry_card for mission_preview responses"
                )
            if (
                explanation_action_kind == "learning_review"
                and payload.get("learning_review_card") is not None
                and "control_registry_card" not in secondary_embedded_cards
            ):
                errors.append(
                    "intent_card.secondary_embedded_cards must include control_registry_card for learning_review responses"
                )
            if (
                explanation_action_kind == "artifacts"
                and payload.get("artifacts_card") is not None
                and "control_registry_card" not in secondary_embedded_cards
            ):
                errors.append(
                    "intent_card.secondary_embedded_cards must include control_registry_card for artifacts responses"
                )
            if (
                explanation_action_kind == "ledger"
                and payload.get("ledger_card") is not None
                and "control_registry_card" not in secondary_embedded_cards
            ):
                errors.append(
                    "intent_card.secondary_embedded_cards must include control_registry_card for ledger responses"
                )
            if (
                explanation_action_kind == "audit"
                and payload.get("audit_card") is not None
                and "control_registry_card" not in secondary_embedded_cards
            ):
                errors.append(
                    "intent_card.secondary_embedded_cards must include control_registry_card for audit responses"
                )
            if (
                explanation_action_kind == "trace"
                and payload.get("trace_card") is not None
                and "control_registry_card" not in secondary_embedded_cards
            ):
                errors.append(
                    "intent_card.secondary_embedded_cards must include control_registry_card for trace responses"
                )
            if (
                explanation_action_kind == "capture"
                and payload.get("capture_card") is not None
                and "control_registry_card" not in secondary_embedded_cards
            ):
                errors.append(
                    "intent_card.secondary_embedded_cards must include control_registry_card for capture responses"
                )
            if (
                explanation_action_kind == "terminal"
                and payload.get("terminal_card") is not None
                and "control_registry_card" not in secondary_embedded_cards
            ):
                errors.append(
                    "intent_card.secondary_embedded_cards must include control_registry_card for terminal responses"
                )
            if (
                explanation_action_kind in {"inbox", "inbox_ack", "inbox_trace"}
                and payload.get("inbox_card") is not None
                and "control_registry_card" not in secondary_embedded_cards
            ):
                errors.append(
                    f"intent_card.secondary_embedded_cards must include control_registry_card for {explanation_action_kind} responses"
                )
            if (
                explanation_action_kind in {"role", "role_assign"}
                and payload.get("role_card") is not None
                and "control_registry_card" not in secondary_embedded_cards
            ):
                errors.append(
                    f"intent_card.secondary_embedded_cards must include control_registry_card for {explanation_action_kind} responses"
                )
            if (
                explanation_action_kind == "review_gate"
                and payload.get("review_gate_card") is not None
                and "control_registry_card" not in secondary_embedded_cards
            ):
                errors.append(
                    "intent_card.secondary_embedded_cards must include control_registry_card for review_gate responses"
                )
            if (
                explanation_action_kind == "release_preview"
                and payload.get("release_preview_card") is not None
                and "control_registry_card" not in secondary_embedded_cards
            ):
                errors.append(
                    "intent_card.secondary_embedded_cards must include control_registry_card for release_preview responses"
                )
            if (
                explanation_action_kind == "role_topology"
                and payload.get("role_topology_card") is not None
                and "control_registry_card" not in secondary_embedded_cards
            ):
                errors.append(
                    "intent_card.secondary_embedded_cards must include control_registry_card for role_topology responses"
                )
            if (
                explanation_action_kind == "policy_mode"
                and payload.get("control_mode_card") is not None
                and "control_registry_card" not in secondary_embedded_cards
            ):
                errors.append(
                    "intent_card.secondary_embedded_cards must include control_registry_card for policy_mode responses"
                )
        elif "secondary_embedded_cards" in intent_card:
            errors.append("intent_card.secondary_embedded_cards must be a list")
        recovery = payload.get("recovery") if isinstance(payload.get("recovery"), dict) else {}
        recommended_action = recovery.get("recommended_action") if isinstance(recovery, dict) else None
        if (
            payload.get("mode") == "continue"
            and isinstance(recommended_action, dict)
            and recommended_action.get("source") == "reply"
            and payload.get("trace_card") is not None
            and intent_card.get("embedded_card") != "trace_card"
        ):
            errors.append("intent_card: reply_waiting continue must embed trace_card")
        trace_card = payload.get("trace_card") if isinstance(payload.get("trace_card"), dict) else {}
        trace_query_id = trace_card.get("query_id") if isinstance(trace_card, dict) else None
        expected_reply_waiting_trace_command = (
            f"agentdeck trace --id {trace_query_id}"
            if (
                payload.get("mode") == "continue"
                and isinstance(recommended_action, dict)
                and recommended_action.get("source") == "reply"
                and intent_card.get("embedded_card") == "trace_card"
                and trace_query_id
            )
            else None
        )
        controls = intent_card.get("controls")
        if isinstance(controls, list):
            has_next_control = False
            leader_status_refresh_control_ok = False
            leader_status_card = payload.get("leader_status_card") if isinstance(payload.get("leader_status_card"), dict) else {}
            expected_leader_status_refresh_command = (
                leader_status_card.get("refresh_command")
                if intent_card.get("embedded_card") == "leader_status_card"
                else None
            )
            for control in controls:
                if isinstance(control, dict):
                    for field in LEADER_CHAT_INTENT_CONTROL_FIELDS:
                        if field not in control:
                            errors.append(f"intent_card.controls: missing control field: {field}")
                    if control.get("kind") == "inspect" and control.get("safety") != "inspect":
                        errors.append("intent_card.controls: inspect controls must use safety=inspect")
                    if control.get("kind") == "next":
                        has_next_control = True
                        if control.get("command") != intent_card.get("next_command"):
                            errors.append("intent_card.controls: next control command must match intent next_command")
                        if intent_card.get("requires_explicit_user") is True and control.get("safety") == "inspect":
                            errors.append("intent_card.controls: explicit next control must not use safety=inspect")
                    if (
                        expected_reply_waiting_trace_command is not None
                        and control.get("kind") == "inspect"
                        and control.get("command") != expected_reply_waiting_trace_command
                    ):
                        errors.append("intent_card.controls: reply_waiting inspect must trace pending message")
                    if control.get("kind") == "refresh" and expected_leader_status_refresh_command is not None:
                        if control.get("command") == expected_leader_status_refresh_command:
                            leader_status_refresh_control_ok = True
                        if control.get("safety") != "inspect":
                            errors.append("intent_card.controls: leader_status refresh must use safety=inspect")
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
            if expected_leader_status_refresh_command is not None and not leader_status_refresh_control_ok:
                errors.append("intent_card.controls: leader_status refresh command must match leader_status_card.refresh_command")
            if intent_card.get("next_command") is not None and not has_next_control:
                errors.append("intent_card.controls: next_command requires a next control")
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
    run_start_card = payload.get("run_start_card")
    if isinstance(run_start_card, dict):
        run_start_validation = validate_run_start_contract(run_start_card)
        for error in run_start_validation["errors"]:
            errors.append(f"run_start_card: {error}")
        if payload.get("mode") == "run_start" and payload.get("next_command") != run_start_card.get("next_command"):
            errors.append("run_start_card: next_command must match response next_command")
    elif "run_start_card" in payload and run_start_card is not None:
        errors.append("run_start_card must be an object")
    run_progress_card = payload.get("run_progress_card")
    if isinstance(run_progress_card, dict):
        run_progress_validation = validate_run_start_contract(run_progress_card)
        for error in run_progress_validation["errors"]:
            errors.append(f"run_progress_card: {error}")
        if payload.get("mode") == "run_progress" and payload.get("next_command") != run_progress_card.get("next_command"):
            errors.append("run_progress_card: next_command must match response next_command")
    elif "run_progress_card" in payload and run_progress_card is not None:
        errors.append("run_progress_card must be an object")
    plan_board_card = payload.get("plan_board_card")
    if isinstance(plan_board_card, dict):
        plan_board_validation = validate_plan_board_contract(plan_board_card)
        for error in plan_board_validation["errors"]:
            errors.append(f"plan_board_card: {error}")
    elif "plan_board_card" in payload and plan_board_card is not None:
        errors.append("plan_board_card must be an object")
    if payload.get("mode") == "plan_board":
        if not isinstance(plan_board_card, dict):
            errors.append("plan_board mode requires plan_board_card")
        if payload.get("next_command") != "agentdeck plan board":
            errors.append("plan_board.next_command must be agentdeck plan board")
        intent_card = payload.get("intent_card")
        if isinstance(intent_card, dict) and intent_card.get("embedded_card") != "plan_board_card":
            errors.append("plan_board intent_card.embedded_card must be plan_board_card")
    skills_catalog_card = payload.get("skills_catalog_card")
    if isinstance(skills_catalog_card, dict):
        for field in LEADER_CHAT_SKILLS_CATALOG_CARD_FIELDS:
            if field not in skills_catalog_card:
                errors.append(f"skills_catalog_card: missing field: {field}")
        if skills_catalog_card.get("mode") != "skills_catalog":
            errors.append("skills_catalog_card: mode must be skills_catalog")
    elif "skills_catalog_card" in payload and skills_catalog_card is not None:
        errors.append("skills_catalog_card must be an object")
    if payload.get("mode") == "skills_catalog":
        if not isinstance(skills_catalog_card, dict):
            errors.append("skills_catalog mode requires skills_catalog_card")
        if payload.get("next_command") != "agentdeck skills sources":
            errors.append("skills_catalog.next_command must be agentdeck skills sources")
        intent_card = payload.get("intent_card")
        if isinstance(intent_card, dict) and intent_card.get("embedded_card") != "skills_catalog_card":
            errors.append("skills_catalog intent_card.embedded_card must be skills_catalog_card")
    run_loop_preview_card = payload.get("run_loop_preview_card")
    if isinstance(run_loop_preview_card, dict):
        for field in LEADER_CHAT_RUN_LOOP_PREVIEW_CARD_FIELDS:
            if field not in run_loop_preview_card:
                errors.append(f"missing run_loop_preview_card field: {field}")
    elif "run_loop_preview_card" in payload and run_loop_preview_card is not None:
        errors.append("run_loop_preview_card must be an object")
    if payload.get("mode") == "run_loop_preview":
        if not isinstance(run_loop_preview_card, dict):
            errors.append("run_loop_preview mode requires run_loop_preview_card")
        else:
            if payload.get("next_command") != run_loop_preview_card.get("command"):
                errors.append("run_loop_preview.next_command must match run_loop_preview_card.command")
            intent_card = payload.get("intent_card")
            if isinstance(intent_card, dict) and intent_card.get("embedded_card") != "run_loop_preview_card":
                errors.append("run_loop_preview intent_card.embedded_card must be run_loop_preview_card")
    mission_preview_card = payload.get("mission_preview_card")
    if isinstance(mission_preview_card, dict):
        mission_preview_validation = validate_mission_preview_contract(mission_preview_card)
        for error in mission_preview_validation["errors"]:
            errors.append(f"mission_preview_card: {error}")
    elif "mission_preview_card" in payload and mission_preview_card is not None:
        errors.append("mission_preview_card must be an object")
    if payload.get("mode") == "mission_preview":
        if not isinstance(mission_preview_card, dict):
            errors.append("mission_preview mode requires mission_preview_card")
        else:
            expected_next = (
                mission_preview_card.get("confirmation_command")
                if mission_preview_card.get("can_start") is True
                else mission_preview_card.get("status_command")
            )
            if payload.get("next_command") != expected_next:
                errors.append("mission_preview.next_command must match safe Mission control")
            intent_card = payload.get("intent_card")
            if isinstance(intent_card, dict) and intent_card.get("embedded_card") != "mission_preview_card":
                errors.append("mission_preview intent_card.embedded_card must be mission_preview_card")
    mission_status_card = payload.get("mission_status_card")
    if isinstance(mission_status_card, dict):
        validation = validate_mission_status_contract(mission_status_card)
        errors.extend(f"mission_status_card: {error}" for error in validation["errors"])
    elif "mission_status_card" in payload and mission_status_card is not None:
        errors.append("mission_status_card must be an object")
    mission_run_card = payload.get("mission_run_card")
    if isinstance(mission_run_card, dict):
        validation = validate_mission_run_contract(mission_run_card)
        errors.extend(f"mission_run_card: {error}" for error in validation["errors"])
    elif "mission_run_card" in payload and mission_run_card is not None:
        errors.append("mission_run_card must be an object")
    mode = payload.get("mode")
    if mode == "mission_status":
        if not isinstance(mission_status_card, dict):
            errors.append("mission_status mode requires mission_status_card")
        else:
            if payload.get("next_command") != mission_status_card.get("status_command"):
                errors.append("mission_status.next_command must match status_command")
            if isinstance(intent_card, dict) and intent_card.get("embedded_card") != "mission_status_card":
                errors.append("mission_status intent_card.embedded_card must be mission_status_card")
    if mode in ("mission_run", "mission_resume"):
        if not isinstance(mission_run_card, dict):
            errors.append("mission run mode requires mission_run_card")
        else:
            expected = (
                mission_run_card.get("resume_command")
                if mission_run_card.get("can_resume") is True
                else mission_run_card.get("status_command")
            )
            if payload.get("next_command") != expected:
                errors.append("mission run next_command must match enabled mission control")
            if isinstance(intent_card, dict) and intent_card.get("embedded_card") != "mission_run_card":
                errors.append("mission run intent_card.embedded_card must be mission_run_card")
    capture_card = payload.get("capture_card")
    if isinstance(capture_card, dict):
        _validate_leader_chat_capture_card_contract(errors, capture_card)
    elif "capture_card" in payload and capture_card is not None:
        errors.append("capture_card must be an object")
    terminal_card = payload.get("terminal_card")
    if isinstance(terminal_card, dict):
        _validate_leader_chat_terminal_card_contract(errors, terminal_card)
    elif "terminal_card" in payload and terminal_card is not None:
        errors.append("terminal_card must be an object")
    dispatch_preview_card = payload.get("dispatch_preview_card")
    if isinstance(dispatch_preview_card, dict):
        _validate_leader_chat_dispatch_preview_card_contract(errors, dispatch_preview_card)
    elif "dispatch_preview_card" in payload and dispatch_preview_card is not None:
        errors.append("dispatch_preview_card must be an object")
    dispatch_batch_preview_card = payload.get("dispatch_batch_preview_card")
    if isinstance(dispatch_batch_preview_card, dict):
        _validate_leader_chat_dispatch_batch_preview_card_contract(errors, dispatch_batch_preview_card)
    elif "dispatch_batch_preview_card" in payload and dispatch_batch_preview_card is not None:
        errors.append("dispatch_batch_preview_card must be an object")
    startup_preview_card = payload.get("startup_preview_card")
    if isinstance(startup_preview_card, dict):
        _validate_leader_chat_startup_preview_card_contract(errors, startup_preview_card)
        if explanation_action_kind == "runtime_ready" and startup_preview_card.get("next_command") != payload.get("next_command"):
            errors.append("startup_preview_card.next_command must match response next_command")
    elif "startup_preview_card" in payload and startup_preview_card is not None:
        errors.append("startup_preview_card must be an object")
    provider_switch_card = payload.get("provider_switch_card")
    if isinstance(provider_switch_card, dict):
        _validate_leader_chat_provider_switch_card_contract(errors, provider_switch_card)
        if explanation_action_kind == "provider_switch" and provider_switch_card.get("command") != payload.get("next_command"):
            errors.append("provider_switch_card.command must match response next_command")
    elif explanation_action_kind == "provider_switch":
        errors.append("provider_switch_card is required for provider_switch setup responses")
    elif explanation_action_kind == "provider_setup":
        errors.append("provider_switch_card is required for provider_setup setup responses")
    elif "provider_switch_card" in payload and provider_switch_card is not None:
        errors.append("provider_switch_card must be an object")
    agent_ready_card = payload.get("agent_ready_card")
    if isinstance(agent_ready_card, dict):
        _validate_agent_ready_card_contract(errors, agent_ready_card)
    elif "agent_ready_card" in payload and agent_ready_card is not None:
        errors.append("agent_ready_card must be an object")
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
    learning_review_card = payload.get("learning_review_card")
    if isinstance(learning_review_card, dict):
        learning_review_validation = validate_learning_review_contract(learning_review_card)
        for error in learning_review_validation["errors"]:
            errors.append(f"learning_review_card: {error}")
        if payload.get("mode") == "learning_review":
            expected_next_command = f"agentdeck learn review --plan-id {learning_review_card.get('plan_id')}"
            if payload.get("next_command") != expected_next_command:
                errors.append("learning_review_card next_command must match agentdeck learn review")
    elif "learning_review_card" in payload and learning_review_card is not None:
        errors.append("learning_review_card must be an object")
    leader_summary_card = payload.get("leader_summary_card")
    if isinstance(leader_summary_card, dict):
        summary_validation = validate_leader_summary_contract(leader_summary_card)
        for error in summary_validation["errors"]:
            errors.append(f"leader_summary_card: {error}")
    elif "leader_summary_card" in payload and leader_summary_card is not None:
        errors.append("leader_summary_card must be an object")
    leader_status_card = payload.get("leader_status_card")
    if isinstance(leader_status_card, dict):
        _validate_leader_status_card_contract(errors, leader_status_card)
        if payload.get("mode") == "leader_status":
            if leader_status_card.get("next_command") != payload.get("next_command"):
                errors.append("leader_status_card.next_command must match response next_command")
            if leader_status_card.get("provider_health") != payload.get("provider_health"):
                errors.append("leader_status_card.provider_health must match response provider_health")
    elif "leader_status_card" in payload and leader_status_card is not None:
        errors.append("leader_status_card must be an object")
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
    runtime_action_card = payload.get("runtime_action_card")
    if isinstance(runtime_action_card, dict):
        _validate_leader_chat_runtime_action_card_contract(errors, runtime_action_card)
        if explanation_action_kind in {"runtime_send", "runtime_stop", "runtime_refresh", "runtime_spawn"} and runtime_action_card.get("command") != payload.get("next_command"):
            errors.append("runtime_action_card.command must match response next_command")
    elif explanation_action_kind in {"runtime_send", "runtime_stop", "runtime_refresh", "runtime_spawn"}:
        errors.append(f"runtime_action_card is required for {explanation_action_kind} responses")
    elif "runtime_action_card" in payload and runtime_action_card is not None:
        errors.append("runtime_action_card must be an object")
    runtime_card = payload.get("runtime_card")
    if isinstance(runtime_card, dict):
        _validate_runtime_card_contract(errors, runtime_card, prefix="runtime_card")
    elif "runtime_card" in payload and runtime_card is not None:
        errors.append("runtime_card must be an object")
    terminal_session_card = payload.get("terminal_session_card")
    if isinstance(terminal_session_card, dict):
        _validate_terminal_session_card_contract(errors, terminal_session_card)
    elif "terminal_session_card" in payload and terminal_session_card is not None:
        errors.append("terminal_session_card must be an object")
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
    review_gate_card = payload.get("review_gate_card")
    if isinstance(review_gate_card, dict):
        _validate_review_gate_card_contract(errors, review_gate_card, prefix="review_gate_card")
    elif "review_gate_card" in payload and review_gate_card is not None:
        errors.append("review_gate_card must be an object")
    release_preview_card = payload.get("release_preview_card")
    if isinstance(release_preview_card, dict):
        _validate_release_preview_card_contract(errors, release_preview_card, prefix="release_preview_card")
        if isinstance(review_gate_card, dict):
            if release_preview_card.get("status") == "released":
                if review_gate_card.get("can_release") is not True:
                    errors.append("released release_preview_card requires a ready review gate")
            elif release_preview_card.get("can_release") != review_gate_card.get("can_release"):
                errors.append("release_preview_card.can_release must match review_gate_card.can_release")
            if (
                release_preview_card.get("status") == "blocked"
                and release_preview_card.get("reason") != review_gate_card.get("reason")
            ):
                errors.append("blocked release_preview_card.reason must match review_gate_card.reason")
    elif "release_preview_card" in payload and release_preview_card is not None:
        errors.append("release_preview_card must be an object")
    role_topology_card = payload.get("role_topology_card")
    if isinstance(role_topology_card, dict):
        _validate_role_topology_card_contract(errors, role_topology_card, prefix="role_topology_card")
    elif "role_topology_card" in payload and role_topology_card is not None:
        errors.append("role_topology_card must be an object")
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
    audit_card = payload.get("audit_card")
    if isinstance(audit_card, dict):
        _validate_audit_card_contract(errors, audit_card, prefix="audit_card")
    elif "audit_card" in payload and audit_card is not None:
        errors.append("audit_card must be an object")
    artifacts_card = payload.get("artifacts_card")
    if isinstance(artifacts_card, dict):
        artifacts_validation = validate_artifacts_contract(artifacts_card)
        for error in artifacts_validation["errors"]:
            errors.append(f"artifacts_card: {error}")
    elif "artifacts_card" in payload and artifacts_card is not None:
        errors.append("artifacts_card must be an object")
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
    provider_setup_card = payload.get("provider_setup_card")
    if isinstance(provider_setup_card, dict):
        _validate_leader_chat_provider_setup_card_contract(errors, provider_setup_card)
        if (
            explanation_action_kind == "provider_setup"
            and provider_setup_card.get("recommended_command") != payload.get("next_command")
        ):
            errors.append("provider_setup_card.recommended_command must match next_command")
        provider_switch_card = payload.get("provider_switch_card")
        has_provider_switch_errors = any("provider_switch_card" in error for error in errors)
        if (
            isinstance(provider_switch_card, dict)
            and not has_provider_switch_errors
        ):
            if provider_setup_card.get("target_provider") != provider_switch_card.get("target_provider"):
                errors.append("provider_setup_card.target_provider must match provider_switch_card.target_provider")
            if provider_setup_card.get("target_model") != provider_switch_card.get("target_model"):
                errors.append("provider_setup_card.target_model must match provider_switch_card.target_model")
            if provider_setup_card.get("require_ready") != provider_switch_card.get("require_ready"):
                errors.append("provider_setup_card.require_ready must match provider_switch_card.require_ready")
            if provider_setup_card.get("followup_switch_command") != provider_switch_card.get("command"):
                errors.append("provider_setup_card.followup_switch_command must match provider_switch_card.command")
    elif explanation_action_kind == "provider_setup":
        errors.append("provider_setup_card is required for provider_setup setup responses")
    elif "provider_setup_card" in payload and provider_setup_card is not None:
        errors.append("provider_setup_card must be an object")
    control_registry_card = payload.get("control_registry_card")
    if isinstance(control_registry_card, dict):
        _validate_control_registry_card_contract(errors, control_registry_card)
        filters = control_registry_card.get("filters")
        if explanation_action_kind == "approval_dispatch" and isinstance(filters, dict):
            if filters.get("card") != "dispatch_preview_card":
                errors.append(
                    "control_registry_card.filters.card must be dispatch_preview_card for approval_dispatch responses"
                )
        if explanation_action_kind == "approval_dispatch_batch" and isinstance(filters, dict):
            if filters.get("card") != "dispatch_batch_preview_card":
                errors.append(
                    "control_registry_card.filters.card must be dispatch_batch_preview_card for approval_dispatch_batch responses"
                )
        selection = control_registry_card.get("selection")
        if payload.get("mode") == "queue" and isinstance(selection, dict):
            if selection.get("next_command") != payload.get("next_command"):
                errors.append("control_registry_card.selection.next_command must match queue next_command")
        if (
            explanation_action_kind in {"runtime_send", "runtime_stop", "runtime_refresh", "runtime_spawn"}
            and isinstance(runtime_action_card, dict)
            and isinstance(selection, dict)
        ):
            if selection.get("next_command") != runtime_action_card.get("command"):
                errors.append("control_registry_card.selection.next_command must match runtime_action_card.command")
        if (
            explanation_action_kind == "provider_setup"
            and isinstance(provider_setup_card, dict)
            and isinstance(selection, dict)
            and provider_setup_card.get("recommended_command") == payload.get("next_command")
            and provider_setup_card.get("recommended_control_id") != selection.get("requested_control_id")
        ):
            errors.append(
                "provider_setup_card.recommended_control_id must match control_registry_card.selection.requested_control_id"
            )
        if (
            explanation_action_kind == "leader_status"
            and isinstance(leader_status_card, dict)
            and isinstance(selection, dict)
            and selection.get("next_command") != leader_status_card.get("refresh_command")
        ):
            errors.append("control_registry_card.selection.next_command must match leader_status_card.refresh_command")
        if (
            explanation_action_kind == "leader_summary"
            and isinstance(leader_summary_card, dict)
            and isinstance(selection, dict)
            and selection.get("next_command") != payload.get("next_command")
        ):
            errors.append("control_registry_card.selection.next_command must match leader_summary next_command")
        if (
            explanation_action_kind == "run_progress"
            and isinstance(run_progress_card, dict)
            and isinstance(selection, dict)
        ):
            expected_plan_status_command = f"agentdeck plan status --plan-id {run_progress_card.get('plan_id')}"
            selected_control = selection.get("selected_control")
            selected_kind = selected_control.get("kind") if isinstance(selected_control, dict) else None
            if selected_kind != "plan_status":
                errors.append("control_registry_card.selection.selected_control.kind must be plan_status for run_progress responses")
            if selection.get("next_command") != expected_plan_status_command:
                errors.append("control_registry_card.selection.next_command must match run_progress plan_status command")
        if (
            explanation_action_kind == "learning_review"
            and isinstance(learning_review_card, dict)
            and isinstance(selection, dict)
        ):
            selected_control = selection.get("selected_control")
            selected_kind = selected_control.get("kind") if isinstance(selected_control, dict) else None
            selected_command = selected_control.get("command") if isinstance(selected_control, dict) else None
            skill_suggestion = learning_review_card.get("skill_suggestion")
            expected_command = skill_suggestion.get("command") if isinstance(skill_suggestion, dict) else None
            if selected_kind != "suggest_skill":
                errors.append("control_registry_card.selection.selected_control.kind must be suggest_skill for learning_review responses")
            if selected_command != expected_command:
                errors.append("control_registry_card.selection.selected_control.command must match learning_review skill suggestion")
            if selection.get("next_command") != expected_command:
                errors.append("control_registry_card.selection.next_command must match learning_review skill suggestion command")
        if (
            explanation_action_kind == "artifacts"
            and isinstance(artifacts_card, dict)
            and isinstance(selection, dict)
            and selection.get("next_command") != artifacts_card.get("artifacts_command")
        ):
            errors.append("control_registry_card.selection.next_command must match artifacts_card.artifacts_command")
        if (
            explanation_action_kind == "review_gate"
            and isinstance(review_gate_card, dict)
            and isinstance(selection, dict)
            and selection.get("next_command") != "agentdeck workbench"
        ):
            errors.append("control_registry_card.selection.next_command must match review_gate inspect command")
        if (
            explanation_action_kind == "release_preview"
            and isinstance(release_preview_card, dict)
            and isinstance(selection, dict)
            and selection.get("next_command") != "agentdeck workbench"
        ):
            errors.append("control_registry_card.selection.next_command must match release_preview inspect command")
        if (
            explanation_action_kind == "role_topology"
            and isinstance(role_topology_card, dict)
            and isinstance(selection, dict)
            and selection.get("next_command") != "agentdeck workbench"
        ):
            errors.append("control_registry_card.selection.next_command must match role_topology inspect command")
        if (
            explanation_action_kind == "ledger"
            and isinstance(ledger_card, dict)
            and isinstance(selection, dict)
            and selection.get("next_command") != "agentdeck workbench"
        ):
            errors.append("control_registry_card.selection.next_command must match ledger_card inspect command")
        if (
            explanation_action_kind == "audit"
            and isinstance(audit_card, dict)
            and isinstance(selection, dict)
            and selection.get("next_command") != audit_card.get("events_command")
        ):
            errors.append("control_registry_card.selection.next_command must match audit_card.events_command")
        if (
            explanation_action_kind == "trace"
            and isinstance(trace_card, dict)
            and isinstance(selection, dict)
            and selection.get("next_command") != payload.get("next_command")
        ):
            errors.append("control_registry_card.selection.next_command must match trace next_command")
        if (
            explanation_action_kind == "capture"
            and isinstance(capture_card, dict)
            and isinstance(selection, dict)
            and selection.get("next_command") != payload.get("next_command")
        ):
            errors.append("control_registry_card.selection.next_command must match capture next_command")
        if (
            explanation_action_kind == "terminal"
            and isinstance(terminal_card, dict)
            and isinstance(selection, dict)
            and selection.get("next_command") != payload.get("next_command")
        ):
            errors.append("control_registry_card.selection.next_command must match terminal next_command")
        if (
            explanation_action_kind in {"inbox_ack", "inbox_trace"}
            and isinstance(inbox_card, dict)
            and isinstance(selection, dict)
            and selection.get("next_command") != payload.get("next_command")
        ):
            errors.append("control_registry_card.selection.next_command must match inbox next_command")
        if (
            explanation_action_kind == "role_assign"
            and isinstance(role_card, dict)
            and isinstance(selection, dict)
        ):
            leader_explanation = payload.get("leader_explanation")
            recommended_action_id = (
                leader_explanation.get("recommended_action_id")
                if isinstance(leader_explanation, dict)
                else None
            )
            selected_control = selection.get("selected_control")
            selected_kind = selected_control.get("kind") if isinstance(selected_control, dict) else None
            selected_agent_id = selected_control.get("agent_id") if isinstance(selected_control, dict) else None
            if selected_kind != "assign_role":
                errors.append("control_registry_card.selection.selected_control.kind must be assign_role for role_assign responses")
            if selected_agent_id != recommended_action_id:
                errors.append("control_registry_card.selection.selected_control.agent_id must match role_assign target")
        if (
            explanation_action_kind == "policy_mode"
            and isinstance(selection, dict)
        ):
            selected_control = selection.get("selected_control")
            selected_kind = selected_control.get("kind") if isinstance(selected_control, dict) else None
            selected_command = selected_control.get("command") if isinstance(selected_control, dict) else None
            selected_enabled = selected_control.get("enabled") if isinstance(selected_control, dict) else None
            if selected_kind != "set_mode":
                errors.append("control_registry_card.selection.selected_control.kind must be set_mode for policy_mode responses")
            if selected_command != payload.get("next_command"):
                errors.append("control_registry_card.selection.selected_control.command must match policy next_command")
            if selected_enabled is True and selection.get("next_command") != payload.get("next_command"):
                errors.append("control_registry_card.selection.next_command must match policy next_command for enabled policy controls")
            if selected_enabled is False and selection.get("next_command") is not None:
                errors.append("control_registry_card.selection.next_command must be null for disabled policy controls")
    elif explanation_action_kind == "provider_setup":
        errors.append("control_registry_card is required for provider_setup setup responses")
    elif explanation_action_kind == "leader_status":
        errors.append("control_registry_card is required for leader_status responses")
    elif explanation_action_kind == "leader_summary":
        errors.append("control_registry_card is required for leader_summary responses")
    elif explanation_action_kind == "run_progress":
        errors.append("control_registry_card is required for run_progress responses")
    elif explanation_action_kind == "learning_review":
        errors.append("control_registry_card is required for learning_review responses")
    elif explanation_action_kind == "artifacts":
        errors.append("control_registry_card is required for artifacts responses")
    elif explanation_action_kind == "ledger":
        errors.append("control_registry_card is required for ledger responses")
    elif explanation_action_kind == "audit":
        errors.append("control_registry_card is required for audit responses")
    elif explanation_action_kind == "trace":
        errors.append("control_registry_card is required for trace responses")
    elif explanation_action_kind == "capture":
        errors.append("control_registry_card is required for capture responses")
    elif explanation_action_kind == "terminal":
        errors.append("control_registry_card is required for terminal responses")
    elif explanation_action_kind in {"inbox", "inbox_ack", "inbox_trace"}:
        errors.append(f"control_registry_card is required for {explanation_action_kind} responses")
    elif explanation_action_kind in {"role", "role_assign"}:
        errors.append(f"control_registry_card is required for {explanation_action_kind} responses")
    elif explanation_action_kind == "review_gate":
        errors.append("control_registry_card is required for review_gate responses")
    elif explanation_action_kind == "release_preview":
        errors.append("control_registry_card is required for release_preview responses")
    elif explanation_action_kind == "role_topology":
        errors.append("control_registry_card is required for role_topology responses")
    elif explanation_action_kind == "policy_mode":
        errors.append("control_registry_card is required for policy_mode responses")
    elif explanation_action_kind in {"approval_dispatch", "approval_dispatch_batch"}:
        errors.append(f"control_registry_card is required for {explanation_action_kind} responses")
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


def _validate_leader_status_card_contract(errors: list[str], status_card: dict[str, object]) -> None:
    for field in LEADER_STATUS_RESPONSE_FIELDS:
        if field not in status_card:
            errors.append(f"missing leader_status_card field: {field}")
    if status_card.get("mode") != "leader_status":
        errors.append("leader_status_card.mode must be leader_status")
    if status_card.get("source_command") != "agentdeck leader status":
        errors.append("leader_status_card.source_command must be agentdeck leader status")
    if status_card.get("refresh_command") != "agentdeck leader status":
        errors.append("leader_status_card.refresh_command must be agentdeck leader status")
    provider_health = status_card.get("provider_health")
    if isinstance(provider_health, dict):
        for field in WORKBENCH_PROVIDER_HEALTH_FIELDS:
            if field not in provider_health:
                errors.append(f"leader_status_card: missing provider_health field: {field}")
    elif "provider_health" in status_card:
        errors.append("leader_status_card.provider_health must be an object")
    _validate_coordination_roles(errors, "leader_status_card", status_card.get("coordination_roles"))
    queues = status_card.get("queues")
    if isinstance(queues, dict):
        for field in LEADER_STATUS_QUEUE_FIELDS:
            if field not in queues:
                errors.append(f"leader_status_card: missing queue field: {field}")
            elif not isinstance(queues.get(field), int):
                errors.append(f"leader_status_card.queues.{field} must be an integer")
    elif "queues" in status_card:
        errors.append("leader_status_card.queues must be an object")
    controls = status_card.get("controls")
    if isinstance(controls, list):
        for control in controls:
            if isinstance(control, dict):
                for field in WORKBENCH_CONTROL_MODE_CONTROL_FIELDS:
                    if field not in control:
                        errors.append(f"leader_status_card.controls: missing control field: {field}")
                if control.get("kind") == "inspect" and control.get("safety") != "inspect":
                    errors.append("leader_status_card.controls: inspect controls must use safety=inspect")
                if control.get("kind") == "refresh":
                    if control.get("command") != status_card.get("refresh_command"):
                        errors.append("leader_status_card.controls: refresh command must match refresh_command")
                    if control.get("safety") != "inspect":
                        errors.append("leader_status_card.controls: refresh controls must use safety=inspect")
                if control.get("enabled") is False and not control.get("blocker"):
                    errors.append("leader_status_card.controls: disabled controls must include blocker")
            else:
                errors.append("leader_status_card.controls items must be objects")
    elif "controls" in status_card:
        errors.append("leader_status_card.controls must be a list")


def _validate_leader_chat_capture_card_contract(errors: list[str], capture_card: dict[str, object]) -> None:
    for field in LEADER_CHAT_CAPTURE_CARD_FIELDS:
        if field not in capture_card:
            errors.append(f"missing capture_card field: {field}")
    if "lines" in capture_card and not isinstance(capture_card.get("lines"), int):
        errors.append("capture_card.lines must be an integer")
    if "output" in capture_card and not isinstance(capture_card.get("output"), str):
        errors.append("capture_card.output must be a string")
    controls = capture_card.get("controls")
    if isinstance(controls, list):
        for control in controls:
            if not isinstance(control, dict):
                errors.append("capture_card.controls items must be objects")
                continue
            for field in LEADER_CHAT_INTENT_CONTROL_FIELDS:
                if field not in control:
                    errors.append(f"capture_card.controls: missing control field: {field}")
            if control.get("kind") == "inspect":
                if control.get("safety") != "inspect":
                    errors.append("capture_card.controls: inspect controls must use safety=inspect")
                if control.get("command") != capture_card.get("capture_command"):
                    errors.append("capture_card.controls: inspect command must match capture_command")
            if control.get("enabled") is False and not control.get("blocker"):
                errors.append("capture_card.controls: disabled controls must include blocker")
    elif "controls" in capture_card:
        errors.append("capture_card.controls must be a list")


def _validate_leader_chat_terminal_card_contract(errors: list[str], terminal_card: dict[str, object]) -> None:
    for field in LEADER_CHAT_TERMINAL_CARD_FIELDS:
        if field not in terminal_card:
            errors.append(f"missing terminal_card field: {field}")
    controls = terminal_card.get("controls")
    if isinstance(controls, list):
        for control in controls:
            if isinstance(control, dict):
                for field in WORKBENCH_RUNTIME_CONTROL_FIELDS:
                    if field not in control:
                        errors.append(f"terminal_card.controls: missing control field: {field}")
                if control.get("kind") == "terminal":
                    if control.get("safety") != "inspect":
                        errors.append("terminal_card.controls: terminal controls must use safety=inspect")
                    if control.get("command") != terminal_card.get("attach_command"):
                        errors.append("terminal_card.controls: terminal command must match attach_command")
                if control.get("kind") == "select_pane":
                    if control.get("safety") != "inspect":
                        errors.append("terminal_card.controls: select_pane controls must use safety=inspect")
                    if control.get("command") != terminal_card.get("select_pane_command"):
                        errors.append("terminal_card.controls: select_pane command must match select_pane_command")
                if control.get("kind") == "capture":
                    if control.get("safety") != "inspect":
                        errors.append("terminal_card.controls: capture controls must use safety=inspect")
                    if control.get("command") != terminal_card.get("capture_command"):
                        errors.append("terminal_card.controls: capture command must match capture_command")
                if control.get("kind") == "send":
                    if control.get("safety") != "explicit_runtime":
                        errors.append("terminal_card.controls: send controls must use safety=explicit_runtime")
                    if control.get("command") != terminal_card.get("send_command_template"):
                        errors.append("terminal_card.controls: send command must match send_command_template")
                if control.get("kind") == "stop":
                    if control.get("safety") != "explicit_runtime":
                        errors.append("terminal_card.controls: stop controls must use safety=explicit_runtime")
                    if control.get("command") != terminal_card.get("stop_command"):
                        errors.append("terminal_card.controls: stop command must match stop_command")
                if control.get("kind") == "inbox":
                    if control.get("safety") != "inspect":
                        errors.append("terminal_card.controls: inbox controls must use safety=inspect")
                    if control.get("command") != terminal_card.get("inbox_command"):
                        errors.append("terminal_card.controls: inbox command must match inbox_command")
                if control.get("enabled") is False and not control.get("blocker"):
                    errors.append("terminal_card.controls: disabled controls must include blocker")
            else:
                errors.append("terminal_card.controls items must be objects")
    elif "controls" in terminal_card:
        errors.append("terminal_card.controls must be a list")


def _validate_leader_chat_dispatch_preview_card_contract(
    errors: list[str], dispatch_preview_card: dict[str, object]
) -> None:
    for field in LEADER_CHAT_DISPATCH_PREVIEW_CARD_FIELDS:
        if field not in dispatch_preview_card:
            errors.append(f"missing dispatch_preview_card field: {field}")
    if dispatch_preview_card.get("requires_explicit_user") is not True:
        errors.append("dispatch_preview_card.requires_explicit_user must be true")
    if dispatch_preview_card.get("safety") != "explicit_runtime":
        errors.append("dispatch_preview_card.safety must be explicit_runtime")
    controls = dispatch_preview_card.get("controls")
    if isinstance(controls, list):
        for control in controls:
            if isinstance(control, dict):
                for field in LEADER_CHAT_INTENT_CONTROL_FIELDS:
                    if field not in control:
                        errors.append(f"dispatch_preview_card.controls: missing control field: {field}")
                if control.get("kind") == "inspect" and control.get("safety") != "inspect":
                    errors.append("dispatch_preview_card.controls: inspect controls must use safety=inspect")
                if control.get("kind") == "dispatch":
                    if control.get("command") != dispatch_preview_card.get("dispatch_command"):
                        errors.append("dispatch_preview_card.controls: dispatch command must match dispatch_command")
                    if control.get("safety") != "explicit_runtime":
                        errors.append("dispatch_preview_card.controls: dispatch controls must use safety=explicit_runtime")
                    expected_enabled = dispatch_preview_card.get("blocker") is None
                    if control.get("enabled") is not expected_enabled:
                        errors.append("dispatch_preview_card.controls: dispatch enabled must match blocker")
                    if dispatch_preview_card.get("blocker") and control.get("blocker") != dispatch_preview_card.get("blocker"):
                        errors.append("dispatch_preview_card.controls: dispatch blocker must match card blocker")
                if control.get("enabled") is False and not control.get("blocker"):
                    errors.append("dispatch_preview_card.controls: disabled controls must include blocker")
            else:
                errors.append("dispatch_preview_card.controls items must be objects")
    elif "controls" in dispatch_preview_card:
        errors.append("dispatch_preview_card.controls must be a list")


def _validate_leader_chat_dispatch_batch_preview_card_contract(
    errors: list[str], dispatch_batch_preview_card: dict[str, object]
) -> None:
    for field in LEADER_CHAT_DISPATCH_BATCH_PREVIEW_CARD_FIELDS:
        if field not in dispatch_batch_preview_card:
            errors.append(f"missing dispatch_batch_preview_card field: {field}")
    if dispatch_batch_preview_card.get("mode") != "dispatch_batch_preview":
        errors.append("dispatch_batch_preview_card.mode must be dispatch_batch_preview")
    if dispatch_batch_preview_card.get("requires_explicit_user") is not True:
        errors.append("dispatch_batch_preview_card.requires_explicit_user must be true")
    if dispatch_batch_preview_card.get("safety") != "explicit_runtime":
        errors.append("dispatch_batch_preview_card.safety must be explicit_runtime")
    controls = dispatch_batch_preview_card.get("controls")
    if isinstance(controls, list):
        for control in controls:
            if isinstance(control, dict):
                for field in LEADER_CHAT_INTENT_CONTROL_FIELDS:
                    if field not in control:
                        errors.append(f"dispatch_batch_preview_card.controls: missing control field: {field}")
                if control.get("kind") == "inspect" and control.get("safety") != "inspect":
                    errors.append("dispatch_batch_preview_card.controls: inspect controls must use safety=inspect")
                if control.get("kind") == "dispatch_ready":
                    if control.get("command") != dispatch_batch_preview_card.get("dispatch_ready_command"):
                        errors.append("dispatch_batch_preview_card.controls: dispatch_ready command must match dispatch_ready_command")
                    if control.get("safety") != "explicit_runtime":
                        errors.append("dispatch_batch_preview_card.controls: dispatch_ready controls must use safety=explicit_runtime")
                    expected_enabled = bool(dispatch_batch_preview_card.get("ready_count"))
                    if control.get("enabled") is not expected_enabled:
                        errors.append("dispatch_batch_preview_card.controls: dispatch_ready enabled must match ready_count")
                if control.get("enabled") is False and not control.get("blocker"):
                    errors.append("dispatch_batch_preview_card.controls: disabled controls must include blocker")
            else:
                errors.append("dispatch_batch_preview_card.controls items must be objects")
    elif "controls" in dispatch_batch_preview_card:
        errors.append("dispatch_batch_preview_card.controls must be a list")
    items = dispatch_batch_preview_card.get("items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                _validate_leader_chat_dispatch_preview_card_contract(errors, item)
            else:
                errors.append("dispatch_batch_preview_card.items items must be objects")
        if isinstance(dispatch_batch_preview_card.get("count"), int) and dispatch_batch_preview_card["count"] != len(items):
            errors.append("dispatch_batch_preview_card.count must match items length")
        ready_count = sum(1 for item in items if isinstance(item, dict) and not item.get("blocker"))
        blocked_count = sum(1 for item in items if isinstance(item, dict) and item.get("blocker"))
        if dispatch_batch_preview_card.get("ready_count") != ready_count:
            errors.append("dispatch_batch_preview_card.ready_count must match unblocked items")
        if dispatch_batch_preview_card.get("blocked_count") != blocked_count:
            errors.append("dispatch_batch_preview_card.blocked_count must match blocked items")
    elif "items" in dispatch_batch_preview_card:
        errors.append("dispatch_batch_preview_card.items must be a list")


def _validate_leader_chat_startup_preview_card_contract(
    errors: list[str], startup_preview_card: dict[str, object]
) -> None:
    for field in LEADER_CHAT_STARTUP_PREVIEW_CARD_FIELDS:
        if field not in startup_preview_card:
            errors.append(f"missing startup_preview_card field: {field}")
    if startup_preview_card.get("mode") != "startup_preview":
        errors.append("startup_preview_card.mode must be startup_preview")
    if startup_preview_card.get("requires_explicit_user") is not True:
        errors.append("startup_preview_card.requires_explicit_user must be true")
    if startup_preview_card.get("safety") != "explicit_runtime":
        errors.append("startup_preview_card.safety must be explicit_runtime")
    items = startup_preview_card.get("items")
    ready_count = 0
    blocked_count = 0
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                errors.append("startup_preview_card.items items must be objects")
                continue
            for field in LEADER_CHAT_STARTUP_PREVIEW_ITEM_FIELDS:
                if field not in item:
                    errors.append(f"startup_preview_card.items: missing item field: {field}")
            if not str(item.get("spawn_command") or "").startswith("agentdeck agent spawn --agent "):
                errors.append("startup_preview_card.items: spawn_command must use agent spawn")
            if not str(item.get("terminal_command") or "").startswith("agentdeck agent terminal --agent "):
                errors.append("startup_preview_card.items: terminal_command must use agent terminal")
            if item.get("blocker"):
                blocked_count += 1
            else:
                ready_count += 1
            controls = item.get("controls")
            if isinstance(controls, list):
                for control in controls:
                    if not isinstance(control, dict):
                        errors.append("startup_preview_card.items.controls items must be objects")
                        continue
                    for field in LEADER_CHAT_INTENT_CONTROL_FIELDS:
                        if field not in control:
                            errors.append(f"startup_preview_card.items.controls: missing control field: {field}")
                    if control.get("kind") == "inspect" and control.get("safety") != "inspect":
                        errors.append("startup_preview_card.items.controls: inspect controls must use safety=inspect")
                    if control.get("kind") == "spawn":
                        if control.get("command") != item.get("spawn_command"):
                            errors.append("startup_preview_card.items.controls: spawn command must match item spawn_command")
                        if control.get("safety") != "explicit_runtime":
                            errors.append("startup_preview_card.items.controls: spawn controls must use safety=explicit_runtime")
                        expected_enabled = item.get("blocker") is None
                        if control.get("enabled") is not expected_enabled:
                            errors.append("startup_preview_card.items.controls: spawn enabled must match blocker")
                        if item.get("blocker") and control.get("blocker") != item.get("blocker"):
                            errors.append("startup_preview_card.items.controls: spawn blocker must match item blocker")
                    if control.get("enabled") is False and not control.get("blocker"):
                        errors.append("startup_preview_card.items.controls: disabled controls must include blocker")
            elif "controls" in item:
                errors.append("startup_preview_card.items.controls must be a list")
    elif "items" in startup_preview_card:
        errors.append("startup_preview_card.items must be a list")
    if isinstance(startup_preview_card.get("count"), int) and isinstance(items, list):
        if startup_preview_card.get("count") != len(items):
            errors.append("startup_preview_card.count must match items length")
    if startup_preview_card.get("ready_count") != ready_count:
        errors.append("startup_preview_card.ready_count must match unblocked items")
    if startup_preview_card.get("blocked_count") != blocked_count:
        errors.append("startup_preview_card.blocked_count must match blocked items")
    controls = startup_preview_card.get("controls")
    if isinstance(controls, list):
        for control in controls:
            if not isinstance(control, dict):
                errors.append("startup_preview_card.controls items must be objects")
                continue
            for field in LEADER_CHAT_INTENT_CONTROL_FIELDS:
                if field not in control:
                    errors.append(f"startup_preview_card.controls: missing control field: {field}")
            if control.get("kind") == "inspect" and control.get("safety") != "inspect":
                errors.append("startup_preview_card.controls: inspect controls must use safety=inspect")
            if control.get("kind") == "spawn_ready":
                if control.get("command") != startup_preview_card.get("spawn_ready_command"):
                    errors.append("startup_preview_card.controls: spawn_ready command must match spawn_ready_command")
                if control.get("safety") != "explicit_runtime":
                    errors.append("startup_preview_card.controls: spawn_ready controls must use safety=explicit_runtime")
                expected_enabled = bool(startup_preview_card.get("ready_count"))
                if control.get("enabled") is not expected_enabled:
                    errors.append("startup_preview_card.controls: spawn_ready enabled must match ready_count")
                if startup_preview_card.get("blocker") and control.get("blocker") != startup_preview_card.get("blocker"):
                    errors.append("startup_preview_card.controls: spawn_ready blocker must match card blocker")
            if control.get("kind") == "spawn":
                if control.get("command") != startup_preview_card.get("next_command"):
                    errors.append("startup_preview_card.controls: spawn command must match next_command")
                if not str(control.get("command") or "").startswith("agentdeck agent spawn --agent "):
                    errors.append("startup_preview_card.controls: spawn command must use agent spawn")
                if control.get("safety") != "explicit_runtime":
                    errors.append("startup_preview_card.controls: spawn controls must use safety=explicit_runtime")
                expected_enabled = bool(startup_preview_card.get("ready_count"))
                if control.get("enabled") is not expected_enabled:
                    errors.append("startup_preview_card.controls: spawn enabled must match ready_count")
                if startup_preview_card.get("blocker") and control.get("blocker") != startup_preview_card.get("blocker"):
                    errors.append("startup_preview_card.controls: spawn blocker must match card blocker")
            if control.get("enabled") is False and not control.get("blocker"):
                errors.append("startup_preview_card.controls: disabled controls must include blocker")
    elif "controls" in startup_preview_card:
        errors.append("startup_preview_card.controls must be a list")


def _validate_leader_chat_runtime_action_card_contract(
    errors: list[str], runtime_action_card: dict[str, object]
) -> None:
    for field in LEADER_CHAT_RUNTIME_ACTION_CARD_FIELDS:
        if field not in runtime_action_card:
            errors.append(f"missing runtime_action_card field: {field}")
    if runtime_action_card.get("mode") != "runtime_action":
        errors.append("runtime_action_card.mode must be runtime_action")
    if runtime_action_card.get("requires_explicit_user") is not True:
        errors.append("runtime_action_card.requires_explicit_user must be true")
    if runtime_action_card.get("safety") != "explicit_runtime":
        errors.append("runtime_action_card.safety must be explicit_runtime")
    if runtime_action_card.get("action") == "send":
        command = str(runtime_action_card.get("command") or "")
        agent_id = runtime_action_card.get("agent_id")
        if not command.startswith(f"agentdeck agent send --agent {agent_id} --text "):
            errors.append("runtime_action_card.command must use agent send for target agent")
    if runtime_action_card.get("action") == "spawn":
        expected_command = f"agentdeck agent spawn --agent {runtime_action_card.get('agent_id')}"
        if runtime_action_card.get("command") != expected_command:
            errors.append("runtime_action_card.command must use agent spawn for target agent")
    if runtime_action_card.get("action") == "stop":
        expected_command = f"agentdeck agent stop --agent {runtime_action_card.get('agent_id')}"
        if runtime_action_card.get("command") != expected_command:
            errors.append("runtime_action_card.command must use agent stop for target agent")
    if runtime_action_card.get("action") == "refresh_runtime":
        if runtime_action_card.get("command") != "agentdeck agent refresh":
            errors.append("runtime_action_card.command must use agent refresh")
    controls = runtime_action_card.get("controls")
    if isinstance(controls, list):
        for control in controls:
            if not isinstance(control, dict):
                errors.append("runtime_action_card.controls items must be objects")
                continue
            for field in LEADER_CHAT_INTENT_CONTROL_FIELDS:
                if field not in control:
                    errors.append(f"runtime_action_card.controls: missing control field: {field}")
            if control.get("kind") == "inspect" and control.get("safety") != "inspect":
                errors.append("runtime_action_card.controls: inspect controls must use safety=inspect")
            if control.get("kind") == "send":
                if control.get("command") != runtime_action_card.get("command"):
                    errors.append("runtime_action_card.controls: send command must match card command")
                if control.get("safety") != "explicit_runtime":
                    errors.append("runtime_action_card.controls: send controls must use safety=explicit_runtime")
                expected_enabled = runtime_action_card.get("blocker") is None
                if control.get("enabled") is not expected_enabled:
                    errors.append("runtime_action_card.controls: send enabled must match blocker")
                if runtime_action_card.get("blocker") and control.get("blocker") != runtime_action_card.get("blocker"):
                    errors.append("runtime_action_card.controls: send blocker must match card blocker")
            if control.get("kind") == "spawn":
                if control.get("command") != runtime_action_card.get("command"):
                    errors.append("runtime_action_card.controls: spawn command must match card command")
                if control.get("safety") != "explicit_runtime":
                    errors.append("runtime_action_card.controls: spawn controls must use safety=explicit_runtime")
                expected_enabled = runtime_action_card.get("blocker") is None
                if control.get("enabled") is not expected_enabled:
                    errors.append("runtime_action_card.controls: spawn enabled must match blocker")
                if runtime_action_card.get("blocker") and control.get("blocker") != runtime_action_card.get("blocker"):
                    errors.append("runtime_action_card.controls: spawn blocker must match card blocker")
            if control.get("kind") == "stop":
                if control.get("command") != runtime_action_card.get("command"):
                    errors.append("runtime_action_card.controls: stop command must match card command")
                if control.get("safety") != "explicit_runtime":
                    errors.append("runtime_action_card.controls: stop controls must use safety=explicit_runtime")
                expected_enabled = runtime_action_card.get("blocker") is None
                if control.get("enabled") is not expected_enabled:
                    errors.append("runtime_action_card.controls: stop enabled must match blocker")
                if runtime_action_card.get("blocker") and control.get("blocker") != runtime_action_card.get("blocker"):
                    errors.append("runtime_action_card.controls: stop blocker must match card blocker")
            if control.get("kind") == "refresh_runtime":
                if control.get("command") != runtime_action_card.get("command"):
                    errors.append("runtime_action_card.controls: refresh_runtime command must match card command")
                if control.get("safety") != "explicit_runtime":
                    errors.append("runtime_action_card.controls: refresh_runtime controls must use safety=explicit_runtime")
                expected_enabled = runtime_action_card.get("blocker") is None
                if control.get("enabled") is not expected_enabled:
                    errors.append("runtime_action_card.controls: refresh_runtime enabled must match blocker")
                if runtime_action_card.get("blocker") and control.get("blocker") != runtime_action_card.get("blocker"):
                    errors.append("runtime_action_card.controls: refresh_runtime blocker must match card blocker")
            if control.get("enabled") is False and not control.get("blocker"):
                errors.append("runtime_action_card.controls: disabled controls must include blocker")
    elif "controls" in runtime_action_card:
        errors.append("runtime_action_card.controls must be a list")


def _validate_leader_chat_provider_setup_card_contract(
    errors: list[str],
    provider_setup_card: dict[str, object],
) -> None:
    for field in LEADER_CHAT_PROVIDER_SETUP_CARD_FIELDS:
        if field not in provider_setup_card:
            errors.append(f"missing provider_setup_card field: {field}")
    if provider_setup_card.get("mode") != "provider_setup":
        errors.append("provider_setup_card.mode must be provider_setup")
    setup_commands = provider_setup_card.get("setup_commands")
    if not isinstance(setup_commands, list) or not all(isinstance(command, str) for command in setup_commands):
        errors.append("provider_setup_card.setup_commands must be a list of strings")
        setup_command_values: list[str] = []
    else:
        setup_command_values = setup_commands
    if provider_setup_card.get("recommended_command") not in setup_command_values:
        errors.append("provider_setup_card.recommended_command must come from setup_commands")
    if provider_setup_card.get("safety") != "explicit_user":
        errors.append("provider_setup_card.safety must be explicit_user")
    if provider_setup_card.get("requires_explicit_user") is not True:
        errors.append("provider_setup_card.requires_explicit_user must be true")
    if provider_setup_card.get("mutates_config") is not False:
        errors.append("provider_setup_card.mutates_config must be false")
    if not isinstance(provider_setup_card.get("require_ready"), bool):
        errors.append("provider_setup_card.require_ready must be a boolean")
    controls = provider_setup_card.get("controls")
    setup_control_ids: list[str] = []
    setup_control_commands_by_id: dict[str, str] = {}
    if isinstance(controls, list):
        for control in controls:
            if not isinstance(control, dict):
                errors.append("provider_setup_card.controls items must be objects")
                continue
            for field in LEADER_CHAT_INTENT_CONTROL_FIELDS:
                if field not in control:
                    errors.append(f"provider_setup_card.controls: missing control field: {field}")
            if control.get("kind") == "setup_provider":
                if control.get("safety") != "explicit_user":
                    errors.append("provider_setup_card.controls: setup_provider controls must use safety=explicit_user")
                if control.get("command") not in setup_command_values:
                    errors.append("provider_setup_card.controls: setup_provider command must come from setup_commands")
                control_id = control.get("control_id")
                if isinstance(control_id, str):
                    setup_control_ids.append(control_id)
                    command = control.get("command")
                    if isinstance(command, str):
                        setup_control_commands_by_id[control_id] = command
                else:
                    errors.append("provider_setup_card.controls: setup_provider controls must include control_id")
            if control.get("kind") in {"set_provider", "guarded_set_provider"}:
                if control.get("command") != provider_setup_card.get("followup_switch_command"):
                    errors.append("provider_setup_card.controls: switch control command must match followup_switch_command")
                if provider_setup_card.get("require_ready") is True and control.get("kind") != "guarded_set_provider":
                    errors.append("provider_setup_card.controls: switch control kind must be guarded_set_provider when require_ready is true")
                if provider_setup_card.get("require_ready") is False and control.get("kind") != "set_provider":
                    errors.append("provider_setup_card.controls: switch control kind must be set_provider when require_ready is false")
            if control.get("enabled") is False and not control.get("blocker"):
                errors.append("provider_setup_card.controls: disabled controls must include blocker")
    elif "controls" in provider_setup_card:
        errors.append("provider_setup_card.controls must be a list")
    recommended_control_id = provider_setup_card.get("recommended_control_id")
    if recommended_control_id is not None and not isinstance(recommended_control_id, str):
        errors.append("provider_setup_card.recommended_control_id must be a string or null")
    if isinstance(recommended_control_id, str) and recommended_control_id not in setup_control_ids:
        errors.append("provider_setup_card.recommended_control_id must match a setup_provider control")
    if (
        isinstance(recommended_control_id, str)
        and recommended_control_id in setup_control_commands_by_id
        and setup_control_commands_by_id[recommended_control_id] != provider_setup_card.get("recommended_command")
    ):
        errors.append("provider_setup_card.recommended_control_id must point at recommended_command control")


def _validate_leader_chat_provider_switch_card_contract(
    errors: list[str],
    provider_switch_card: dict[str, object],
) -> None:
    for field in LEADER_CHAT_PROVIDER_SWITCH_CARD_FIELDS:
        if field not in provider_switch_card:
            errors.append(f"missing provider_switch_card field: {field}")
    target_leader_backend = provider_switch_card.get("target_leader_backend")
    if isinstance(target_leader_backend, dict):
        _validate_leader_backend(errors, "provider_switch_card.target", target_leader_backend)
    elif "target_leader_backend" in provider_switch_card:
        errors.append("provider_switch_card.target_leader_backend must be an object")
    target_readiness = provider_switch_card.get("target_readiness")
    if isinstance(target_readiness, dict):
        for field in DOCTOR_CONFIGURED_LEADER_FIELDS:
            if field not in target_readiness:
                errors.append(f"missing provider_switch_card target_readiness field: {field}")
        if target_readiness.get("provider") != provider_switch_card.get("target_provider"):
            errors.append("provider_switch_card.target_readiness.provider must match target_provider")
        if target_readiness.get("model") != provider_switch_card.get("target_model"):
            errors.append("provider_switch_card.target_readiness.model must match target_model")
        leader_backend = target_readiness.get("leader_backend")
        if isinstance(leader_backend, dict):
            _validate_leader_backend(errors, "provider_switch_card.target_readiness", leader_backend)
            if isinstance(target_leader_backend, dict) and leader_backend != target_leader_backend:
                errors.append("provider_switch_card.target_readiness.leader_backend must match target_leader_backend")
        else:
            errors.append("provider_switch_card.target_readiness.leader_backend must be an object")
        if "ready" in target_readiness and not isinstance(target_readiness.get("ready"), bool):
            errors.append("provider_switch_card.target_readiness.ready must be a boolean")
        if "supported" in target_readiness and not isinstance(target_readiness.get("supported"), bool):
            errors.append("provider_switch_card.target_readiness.supported must be a boolean")
        if "missing_env" in target_readiness and not isinstance(target_readiness.get("missing_env"), list):
            errors.append("provider_switch_card.target_readiness.missing_env must be a list")
        if "setup_commands" in target_readiness and not isinstance(target_readiness.get("setup_commands"), list):
            errors.append("provider_switch_card.target_readiness.setup_commands must be a list")
    elif "target_readiness" in provider_switch_card:
        errors.append("provider_switch_card.target_readiness must be an object")
    if "require_ready" in provider_switch_card and not isinstance(provider_switch_card.get("require_ready"), bool):
        errors.append("provider_switch_card.require_ready must be a boolean")
    if provider_switch_card.get("safety") != "explicit_user":
        errors.append("provider_switch_card.safety must be explicit_user")
    if provider_switch_card.get("requires_explicit_user") is not True:
        errors.append("provider_switch_card.requires_explicit_user must be true")
    if provider_switch_card.get("mutates_config") is not False:
        errors.append("provider_switch_card.mutates_config must be false")
    command = str(provider_switch_card.get("command") or "")
    has_command = "command" in provider_switch_card
    if has_command and not command.startswith("agentdeck leader set-provider --provider "):
        errors.append("provider_switch_card.command must use leader set-provider")
    if has_command and provider_switch_card.get("require_ready") is True and "--require-ready" not in command:
        errors.append("provider_switch_card.command must include --require-ready when require_ready is true")
    controls = provider_switch_card.get("controls")
    if isinstance(controls, list):
        setup_commands = (
            target_readiness.get("setup_commands")
            if isinstance(target_readiness, dict) and isinstance(target_readiness.get("setup_commands"), list)
            else []
        )
        setup_control_commands = [
            control.get("command")
            for control in controls
            if isinstance(control, dict) and control.get("kind") == "setup"
        ]
        for control in controls:
            if not isinstance(control, dict):
                errors.append("provider_switch_card.controls items must be objects")
                continue
            for field in LEADER_CHAT_INTENT_CONTROL_FIELDS:
                if field not in control:
                    errors.append(f"provider_switch_card.controls: missing control field: {field}")
            if control.get("kind") == "inspect" and control.get("safety") != "inspect":
                errors.append("provider_switch_card.controls: inspect controls must use safety=inspect")
            if control.get("kind") == "inspect" and control.get("command") != provider_switch_card.get("diagnostics_command"):
                errors.append("provider_switch_card.controls: inspect control command must match diagnostics_command")
            if control.get("kind") in {"set_provider", "guarded_set_provider"}:
                if control.get("safety") != "explicit_user":
                    errors.append("provider_switch_card.controls: provider controls must use safety=explicit_user")
                if has_command and control.get("command") != provider_switch_card.get("command"):
                    errors.append("provider_switch_card.controls: provider control command must match card command")
                if provider_switch_card.get("require_ready") is True and control.get("kind") != "guarded_set_provider":
                    errors.append(
                        "provider_switch_card.controls: provider control kind must be guarded_set_provider when require_ready is true"
                    )
                if provider_switch_card.get("require_ready") is False and control.get("kind") != "set_provider":
                    errors.append(
                        "provider_switch_card.controls: provider control kind must be set_provider when require_ready is false"
                    )
                target_not_ready = (
                    provider_switch_card.get("require_ready") is True
                    and isinstance(target_readiness, dict)
                    and target_readiness.get("ready") is False
                )
                if target_not_ready and control.get("kind") == "guarded_set_provider":
                    if control.get("enabled") is not False:
                        errors.append(
                            "provider_switch_card.controls: guarded provider control must be disabled when target is not ready"
                        )
                    if control.get("blocker") != "target provider is not ready":
                        errors.append(
                            "provider_switch_card.controls: disabled guarded provider control must use target provider is not ready blocker"
                        )
                    if (
                        control.get("enabled") is False
                        and control.get("blocker") == "target provider is not ready"
                        and setup_commands
                        and setup_control_commands != setup_commands
                    ):
                        errors.append(
                            "provider_switch_card.controls: blocked guarded provider switch must include setup controls for target_readiness.setup_commands"
                        )
            if control.get("kind") == "setup":
                if control.get("safety") != "explicit_user":
                    errors.append("provider_switch_card.controls: setup controls must use safety=explicit_user")
                if control.get("command") not in setup_commands:
                    errors.append(
                        "provider_switch_card.controls: setup control command must come from target_readiness.setup_commands"
                    )
            if control.get("enabled") is False and not control.get("blocker"):
                errors.append("provider_switch_card.controls: disabled controls must include blocker")
    elif "controls" in provider_switch_card:
        errors.append("provider_switch_card.controls must be a list")


def _validate_control_registry_card_contract(errors: list[str], control_registry_card: dict[str, object]) -> None:
    for field in LEADER_CHAT_CONTROL_REGISTRY_CARD_FIELDS:
        if field not in control_registry_card:
            errors.append(f"missing control_registry_card field: {field}")
    if control_registry_card.get("mode") != "control_registry":
        errors.append("control_registry_card.mode must be control_registry")
    filters = control_registry_card.get("filters")
    if isinstance(filters, dict):
        for field in CONTROL_REGISTRY_FILTER_FIELDS:
            if field not in filters:
                errors.append(f"control_registry_card.filters: missing filter field: {field}")
        if "scope" in filters and filters.get("scope") is not None and not isinstance(filters.get("scope"), str):
            errors.append("control_registry_card.filters.scope must be a string or null")
        if "card" in filters and filters.get("card") is not None and not isinstance(filters.get("card"), str):
            errors.append("control_registry_card.filters.card must be a string or null")
        if "query" in filters and filters.get("query") is not None and not isinstance(filters.get("query"), str):
            errors.append("control_registry_card.filters.query must be a string or null")
        if "control_id" in filters and filters.get("control_id") is not None and not isinstance(
            filters.get("control_id"), str
        ):
            errors.append("control_registry_card.filters.control_id must be a string or null")
        if "enabled_only" in filters and not isinstance(filters.get("enabled_only"), bool):
            errors.append("control_registry_card.filters.enabled_only must be a boolean")
        if "active_filter_keys" in filters and not isinstance(filters.get("active_filter_keys"), list):
            errors.append("control_registry_card.filters.active_filter_keys must be a list")
        if isinstance(filters.get("active_filter_keys"), list):
            active_filter_keys = filters["active_filter_keys"]
            allowed_filter_keys = {"scope", "card", "query", "control_id", "enabled_only"}
            if not all(isinstance(key, str) for key in active_filter_keys):
                errors.append("control_registry_card.filters.active_filter_keys must contain strings")
            elif any(key not in allowed_filter_keys for key in active_filter_keys):
                errors.append("control_registry_card.filters.active_filter_keys contains unknown filter key")
            else:
                expected_filter_keys = _control_registry_active_filter_keys(
                    scope=filters.get("scope") if isinstance(filters.get("scope"), str) else None,
                    card=filters.get("card") if isinstance(filters.get("card"), str) else None,
                    query=filters.get("query") if isinstance(filters.get("query"), str) else None,
                    control_id=filters.get("control_id") if isinstance(filters.get("control_id"), str) else None,
                    enabled_only=filters.get("enabled_only") is True,
                )
                if active_filter_keys != expected_filter_keys:
                    errors.append("control_registry_card.filters.active_filter_keys must match active filters")
        if "item_count_before_filter" in filters and not isinstance(filters.get("item_count_before_filter"), int):
            errors.append("control_registry_card.filters.item_count_before_filter must be an integer")
        if isinstance(filters.get("item_count_before_filter"), int) and isinstance(
            control_registry_card.get("item_count"), int
        ) and filters["item_count_before_filter"] < control_registry_card["item_count"]:
            errors.append("control_registry_card.filters.item_count_before_filter must be >= item_count")
        if (
            isinstance(filters.get("active_filter_keys"), list)
            and filters.get("active_filter_keys") == []
            and isinstance(filters.get("item_count_before_filter"), int)
            and isinstance(control_registry_card.get("item_count"), int)
            and filters["item_count_before_filter"] != control_registry_card["item_count"]
        ):
            errors.append("control_registry_card.filters.item_count_before_filter must match item_count when unfiltered")
    elif "filters" in control_registry_card:
        errors.append("control_registry_card.filters must be an object")
    selection = control_registry_card.get("selection")
    selection_fields_present = False
    if isinstance(selection, dict):
        selection_fields_present = True
        for field in CONTROL_REGISTRY_SELECTION_FIELDS:
            if field not in selection:
                errors.append(f"control_registry_card.selection: missing selection field: {field}")
                selection_fields_present = False
        if "requested_control_id" in selection and selection.get("requested_control_id") is not None and not isinstance(
            selection.get("requested_control_id"), str
        ):
            errors.append("control_registry_card.selection.requested_control_id must be a string or null")
        if "matched" in selection and not isinstance(selection.get("matched"), bool):
            errors.append("control_registry_card.selection.matched must be a boolean")
        if "matched_count" in selection and not isinstance(selection.get("matched_count"), int):
            errors.append("control_registry_card.selection.matched_count must be an integer")
        selected_control = selection.get("selected_control")
        if "selected_control" in selection and selected_control is not None and not isinstance(selected_control, dict):
            errors.append("control_registry_card.selection.selected_control must be an object or null")
        if "blocker" in selection and selection.get("blocker") is not None and not isinstance(selection.get("blocker"), str):
            errors.append("control_registry_card.selection.blocker must be a string or null")
        if "next_command" in selection and selection.get("next_command") is not None and not isinstance(
            selection.get("next_command"), str
        ):
            errors.append("control_registry_card.selection.next_command must be a string or null")
    elif "selection" in control_registry_card:
        errors.append("control_registry_card.selection must be an object")
    items = control_registry_card.get("items")
    if isinstance(items, list):
        if control_registry_card.get("item_count") != len(items):
            errors.append("control_registry_card.item_count must match items length")
        control_ids: set[str] = set()
        duplicate_control_id = False
        for item in items:
            if not isinstance(item, dict):
                errors.append("control_registry_card.items must be objects")
                continue
            for field in WORKBENCH_CONTROL_REGISTRY_ITEM_FIELDS:
                if field not in item:
                    errors.append(f"control_registry_card.items: missing item field: {field}")
            control_id = item.get("control_id")
            if "control_id" in item:
                if not isinstance(control_id, str) or not control_id:
                    errors.append("control_registry_card.items: control_id must be a non-empty string")
                elif control_id in control_ids:
                    duplicate_control_id = True
                else:
                    control_ids.add(control_id)
            if item.get("scope") == "provider" and item.get("kind") in {"set_provider", "guarded_set_provider"}:
                if item.get("safety") != "explicit_user":
                    errors.append("control_registry_card.items: provider set_provider must use safety=explicit_user")
                if not str(item.get("command") or "").startswith("agentdeck leader set-provider --provider "):
                    errors.append("control_registry_card.items: provider set_provider command must use leader set-provider")
                if item.get("kind") == "guarded_set_provider" and not str(item.get("command") or "").endswith(
                    " --require-ready"
                ):
                    errors.append(
                        "control_registry_card.items: provider guarded_set_provider command must use --require-ready"
                    )
                if item.get("enabled") is False and not item.get("blocker"):
                    errors.append("control_registry_card.items: disabled provider set_provider controls must include blocker")
            if item.get("scope") == "provider" and item.get("kind") == "setup_provider":
                if item.get("safety") != "explicit_user":
                    errors.append("control_registry_card.items: provider setup_provider must use safety=explicit_user")
                if item.get("command") not in leader_provider_setup_command_allowlist():
                    errors.append(
                        "control_registry_card.items: provider setup_provider command must come from provider setup commands"
                    )
            if item.get("scope") == "policy" and item.get("kind") == "set_mode":
                if not str(item.get("command") or "").startswith("agentdeck policy set-mode --mode "):
                    errors.append("control_registry_card.items: policy set_mode command must use policy set-mode")
                if item.get("enabled") is True and item.get("safety") != "explicit_user":
                    errors.append("control_registry_card.items: enabled policy set_mode must use safety=explicit_user")
                if item.get("enabled") is False and not item.get("blocker"):
                    errors.append("control_registry_card.items: disabled policy set_mode controls must include blocker")
            if item.get("scope") == "role" and item.get("kind") == "assign_role":
                if not str(item.get("command") or "").startswith("agentdeck agent assign-role --agent "):
                    errors.append("control_registry_card.items: role assign_role command must use agent assign-role")
                if item.get("enabled") is False and not item.get("blocker"):
                    errors.append("control_registry_card.items: disabled role assign_role controls must include blocker")
            if item.get("scope") == "artifacts" and item.get("kind") == "inspect":
                if item.get("safety") != "inspect":
                    errors.append("control_registry_card.items: artifacts inspect must use safety=inspect")
                if item.get("command") != "agentdeck artifacts":
                    errors.append("control_registry_card.items: artifacts inspect command must be agentdeck artifacts")
            if item.get("scope") == "ledger" and item.get("kind") == "inspect":
                if item.get("safety") != "inspect":
                    errors.append("control_registry_card.items: ledger inspect must use safety=inspect")
                if item.get("command") != "agentdeck workbench":
                    errors.append("control_registry_card.items: ledger inspect command must be agentdeck workbench")
            if item.get("scope") == "audit" and item.get("kind") == "inspect":
                if item.get("safety") != "inspect":
                    errors.append("control_registry_card.items: audit inspect must use safety=inspect")
                if item.get("command") != "agentdeck events --limit 20":
                    errors.append(
                        "control_registry_card.items: audit inspect command must be agentdeck events --limit 20"
                    )
            if item.get("scope") == "trace" and item.get("kind") == "inspect":
                if item.get("safety") != "inspect":
                    errors.append("control_registry_card.items: trace inspect must use safety=inspect")
                if not str(item.get("command") or "").startswith("agentdeck trace --id "):
                    errors.append("control_registry_card.items: trace inspect command must use trace")
            if item.get("scope") == "capture" and item.get("kind") == "inspect":
                if item.get("safety") != "inspect":
                    errors.append("control_registry_card.items: capture inspect must use safety=inspect")
                if not str(item.get("command") or "").startswith("agentdeck agent capture --agent "):
                    errors.append("control_registry_card.items: capture inspect command must use agent capture")
            if item.get("scope") == "terminal" and item.get("kind") == "terminal":
                if item.get("safety") != "inspect":
                    errors.append("control_registry_card.items: terminal open must use safety=inspect")
                if not str(item.get("command") or "").startswith("tmux "):
                    errors.append("control_registry_card.items: terminal open command must use tmux")
            if item.get("scope") == "inbox" and item.get("kind") == "preview":
                if not str(item.get("command") or "").startswith("agentdeck trace --id "):
                    errors.append("control_registry_card.items: inbox preview command must use trace")
            if item.get("scope") == "inbox" and item.get("kind") == "ack":
                if not str(item.get("command") or "").startswith("agentdeck ack --agent "):
                    errors.append("control_registry_card.items: inbox ack command must use ack")
            if item.get("scope") == "terminal_session":
                if item.get("enabled") is False and not item.get("blocker"):
                    errors.append("control_registry_card.items: disabled terminal_session controls must include blocker")
                if item.get("kind") == "attach_session":
                    if item.get("safety") != "inspect":
                        errors.append("control_registry_card.items: terminal_session attach_session must use safety=inspect")
                    if not str(item.get("command") or "").startswith("tmux "):
                        errors.append("control_registry_card.items: terminal_session attach_session command must use tmux")
                if item.get("kind") == "open_controls":
                    if item.get("safety") != "inspect":
                        errors.append("control_registry_card.items: terminal_session open_controls must use safety=inspect")
                    if item.get("command") != "agentdeck controls":
                        errors.append(
                            "control_registry_card.items: terminal_session open_controls command must be agentdeck controls"
                        )
                if item.get("kind") == "refresh_runtime":
                    if item.get("safety") != "explicit_runtime":
                        errors.append(
                            "control_registry_card.items: terminal_session refresh_runtime must use safety=explicit_runtime"
                        )
                    if item.get("command") != "agentdeck agent refresh":
                        errors.append(
                            "control_registry_card.items: terminal_session refresh_runtime command must be agentdeck agent refresh"
                        )
                if item.get("kind") == "select_pane":
                    if item.get("safety") != "inspect":
                        errors.append("control_registry_card.items: terminal_session select_pane must use safety=inspect")
                    if item.get("enabled") is True and " select-pane -t " not in str(item.get("command") or ""):
                        errors.append(
                            "control_registry_card.items: terminal_session select_pane command must use tmux select-pane"
                        )
                    if item.get("enabled") is False and item.get("command") is not None:
                        errors.append(
                            "control_registry_card.items: disabled terminal_session select_pane command must be null"
                        )
            if item.get("scope") == "agent_ready":
                if item.get("enabled") is False and not item.get("blocker"):
                    errors.append("control_registry_card.items: disabled agent_ready controls must include blocker")
                if item.get("kind") == "inspect":
                    if item.get("safety") != "inspect":
                        errors.append("control_registry_card.items: agent_ready inspect must use safety=inspect")
                    if item.get("command") != "agentdeck agent ready":
                        errors.append("control_registry_card.items: agent_ready inspect command must be agentdeck agent ready")
                if item.get("kind") == "spawn_ready":
                    if item.get("safety") != "explicit_runtime":
                        errors.append("control_registry_card.items: agent_ready spawn_ready must use safety=explicit_runtime")
                    if item.get("command") != "agentdeck agent spawn-ready --confirm":
                        errors.append(
                            "control_registry_card.items: agent_ready spawn_ready command must be agentdeck agent spawn-ready --confirm"
                        )
                if item.get("kind") == "refresh_runtime":
                    if item.get("safety") != "explicit_runtime":
                        errors.append(
                            "control_registry_card.items: agent_ready refresh_runtime must use safety=explicit_runtime"
                        )
                    if item.get("command") != "agentdeck agent refresh":
                        errors.append(
                            "control_registry_card.items: agent_ready refresh_runtime command must be agentdeck agent refresh"
                        )
                if item.get("kind") == "dispatch_ready":
                    if item.get("safety") != "explicit_runtime":
                        errors.append(
                            "control_registry_card.items: agent_ready dispatch_ready must use safety=explicit_runtime"
                        )
                    if item.get("command") != "agentdeck approval dispatch-ready --confirm":
                        errors.append(
                            "control_registry_card.items: agent_ready dispatch_ready command must be agentdeck approval dispatch-ready --confirm"
                        )
            if item.get("scope") == "startup_preview":
                if item.get("enabled") is False and not item.get("blocker"):
                    errors.append("control_registry_card.items: disabled startup_preview controls must include blocker")
                if item.get("kind") == "inspect":
                    if item.get("safety") != "inspect":
                        errors.append("control_registry_card.items: startup_preview inspect must use safety=inspect")
                    if item.get("command") != "agentdeck agent ready":
                        errors.append(
                            "control_registry_card.items: startup_preview inspect command must be agentdeck agent ready"
                        )
                if item.get("kind") == "spawn":
                    if item.get("safety") != "explicit_runtime":
                        errors.append(
                            "control_registry_card.items: startup_preview spawn must use safety=explicit_runtime"
                        )
                    if not str(item.get("command") or "").startswith("agentdeck agent spawn --agent "):
                        errors.append(
                            "control_registry_card.items: startup_preview spawn command must use agent spawn"
                        )
                if item.get("kind") == "spawn_ready":
                    if item.get("safety") != "explicit_runtime":
                        errors.append(
                            "control_registry_card.items: startup_preview spawn_ready must use safety=explicit_runtime"
                        )
                    if item.get("command") != "agentdeck agent spawn-ready --confirm":
                        errors.append(
                            "control_registry_card.items: startup_preview spawn_ready command must be agentdeck agent spawn-ready --confirm"
                        )
            if item.get("scope") == "dispatch_preview":
                if item.get("enabled") is False and not item.get("blocker"):
                    errors.append("control_registry_card.items: disabled dispatch_preview controls must include blocker")
                if item.get("kind") == "inspect":
                    if item.get("safety") != "inspect":
                        errors.append("control_registry_card.items: dispatch_preview inspect must use safety=inspect")
                    if item.get("command") != "agentdeck approval list":
                        errors.append(
                            "control_registry_card.items: dispatch_preview inspect command must be agentdeck approval list"
                        )
                if item.get("kind") == "dispatch":
                    if item.get("safety") != "explicit_runtime":
                        errors.append(
                            "control_registry_card.items: dispatch_preview dispatch must use safety=explicit_runtime"
                        )
                    if not str(item.get("command") or "").startswith("agentdeck approval dispatch --approval-id "):
                        errors.append(
                            "control_registry_card.items: dispatch_preview dispatch command must use approval dispatch"
                        )
            if item.get("scope") == "dispatch_batch_preview":
                if item.get("enabled") is False and not item.get("blocker"):
                    errors.append(
                        "control_registry_card.items: disabled dispatch_batch_preview controls must include blocker"
                    )
                if item.get("kind") == "inspect":
                    if item.get("safety") != "inspect":
                        errors.append(
                            "control_registry_card.items: dispatch_batch_preview inspect must use safety=inspect"
                        )
                    if item.get("command") != "agentdeck approval list":
                        errors.append(
                            "control_registry_card.items: dispatch_batch_preview inspect command must be agentdeck approval list"
                        )
                if item.get("kind") == "dispatch_ready":
                    if item.get("safety") != "explicit_runtime":
                        errors.append(
                            "control_registry_card.items: dispatch_batch_preview dispatch_ready must use safety=explicit_runtime"
                        )
                    if item.get("command") != "agentdeck approval dispatch-ready --confirm":
                        errors.append(
                            "control_registry_card.items: dispatch_batch_preview dispatch_ready command must be agentdeck approval dispatch-ready --confirm"
                        )
        if duplicate_control_id:
            errors.append("control_registry_card.items: control_id values must be unique")
        if isinstance(selection, dict) and selection_fields_present:
            requested_control_id = selection.get("requested_control_id")
            matched_items = [
                item
                for item in items
                if isinstance(item, dict) and requested_control_id is not None and item.get("control_id") == requested_control_id
            ]
            if selection.get("matched_count") != len(matched_items):
                errors.append("control_registry_card.selection.matched_count must match matching items")
            if selection.get("matched") != (len(matched_items) == 1):
                errors.append("control_registry_card.selection.matched must reflect matching items")
            expected_selected_control = matched_items[0] if len(matched_items) == 1 else None
            if selection.get("selected_control") != expected_selected_control:
                errors.append("control_registry_card.selection.selected_control must match selected item")
            if isinstance(filters, dict) and selection.get("requested_control_id") != filters.get("control_id"):
                errors.append("control_registry_card.selection.requested_control_id must match filters.control_id")
            blocker = selection.get("blocker")
            if requested_control_id is None and blocker is not None:
                errors.append("control_registry_card.selection: idle selection must not include blocker")
            elif len(matched_items) == 1 and blocker is not None:
                errors.append("control_registry_card.selection: matched control_id must not include blocker")
            elif requested_control_id is not None and len(matched_items) != 1 and not blocker:
                errors.append("control_registry_card.selection: unmatched control_id requires blocker")
            selected_enabled = isinstance(expected_selected_control, dict) and expected_selected_control.get("enabled") is True
            if selected_enabled:
                expected_next_command = expected_selected_control.get("command")
                if selection.get("next_command") != expected_next_command:
                    errors.append("control_registry_card.selection.next_command must match selected enabled command")
            elif selection.get("next_command") is not None:
                errors.append(
                    "control_registry_card.selection.next_command must be null when selected control is disabled or unmatched"
                )
    elif "items" in control_registry_card:
        errors.append("control_registry_card.items must be a list")
    groups = control_registry_card.get("groups")
    if isinstance(groups, list):
        if control_registry_card.get("group_count") != len(groups):
            errors.append("control_registry_card.group_count must match groups length")
        group_counts_valid = True
        for group in groups:
            if not isinstance(group, dict):
                errors.append("control_registry_card.groups must be objects")
                group_counts_valid = False
                continue
            for field in CONTROL_REGISTRY_GROUP_FIELDS:
                if field not in group:
                    errors.append(f"control_registry_card.groups: missing group field: {field}")
            group_items = group.get("items")
            if isinstance(group_items, list):
                if group.get("item_count") != len(group_items):
                    errors.append("control_registry_card.groups: group item_count must match items length")
                    group_counts_valid = False
                enabled_count = sum(1 for item in group_items if isinstance(item, dict) and item.get("enabled") is True)
                disabled_count = sum(1 for item in group_items if not (isinstance(item, dict) and item.get("enabled") is True))
                if group.get("enabled_count") != enabled_count:
                    errors.append("control_registry_card.groups: enabled_count must match enabled items")
                    group_counts_valid = False
                if group.get("disabled_count") != disabled_count:
                    errors.append("control_registry_card.groups: disabled_count must match disabled items")
                    group_counts_valid = False
            elif "items" in group:
                errors.append("control_registry_card.groups.items must be a list")
                group_counts_valid = False
        if isinstance(items, list) and group_counts_valid and groups != _control_registry_groups(items):
            errors.append("control_registry_card.groups must match items grouped by scope/card")
    elif "groups" in control_registry_card:
        errors.append("control_registry_card.groups must be a list")


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
        leader_backend = leader_card.get("leader_backend")
        if isinstance(leader_backend, dict):
            _validate_leader_backend(errors, "leader_card", leader_backend)
        else:
            errors.append("leader_card.leader_backend must be an object")
        _validate_coordination_roles(errors, "leader_card", leader_card.get("coordination_roles"))
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
    mission_card = payload.get("mission_card")
    if isinstance(mission_card, dict):
        status_projection = dict(mission_card)
        confirmation_command = status_projection.pop("confirmation_command", None)
        controls = status_projection.get("controls")
        confirm_controls = [
            item for item in controls
            if isinstance(item, dict) and item.get("command") == confirmation_command
        ] if isinstance(controls, list) else []
        status_projection["controls"] = [
            item for item in controls
            if not (isinstance(item, dict) and item.get("command") == confirmation_command)
        ] if isinstance(controls, list) else controls
        mission_validation = validate_mission_status_contract(status_projection)
        errors.extend(f"mission_card: {error}" for error in mission_validation["errors"])
        mission_id = mission_card.get("mission_id")
        expected_confirmation = (
            mission_commands(str(mission_id))["confirmation_command"]
            if is_canonical_mission_id(mission_id)
            else None
        )
        if confirmation_command != expected_confirmation:
            errors.append("mission_card.confirmation_command must match mission_id")
        if len(confirm_controls) != 1:
            errors.append("mission_card must expose exactly one confirmation control")
        else:
            control = confirm_controls[0]
            expected_enabled = mission_card.get("status") == "pending_confirmation" and not mission_card.get("blockers")
            if control.get("kind") != "execute" or control.get("safety") != "delegated":
                errors.append("mission_card confirmation control must be delegated execute")
            if control.get("enabled") is not expected_enabled:
                errors.append("mission_card confirmation control enabled conflicts with status")
            if expected_enabled and control.get("blocker") is not None:
                errors.append("mission_card enabled confirmation control blocker must be null")
            if not expected_enabled and not _mission_nonempty_string(control.get("blocker")):
                errors.append("mission_card disabled confirmation control needs blocker")
    elif "mission_card" in payload and mission_card is not None:
        errors.append("mission_card must be an object or null")
    missions_summary = project_view.get("missions") if isinstance(project_view, dict) else None
    mission_items = missions_summary.get("items") if isinstance(missions_summary, dict) else None
    latest_mission_id = missions_summary.get("latest_id") if isinstance(missions_summary, dict) else None
    latest_mission = next(
        (
            item for item in mission_items
            if isinstance(item, dict) and item.get("mission_id") == latest_mission_id
        ),
        None,
    ) if isinstance(mission_items, list) and isinstance(latest_mission_id, str) else None
    if isinstance(mission_items, list) and mission_items and latest_mission is None:
        errors.append("project_view.missions.latest_id must identify an item")
    if isinstance(mission_items, list) and not mission_items and mission_card is not None:
        errors.append("mission_card must be null when project_view has no Missions")
    if latest_mission is not None and not isinstance(mission_card, dict):
        errors.append("mission_card is required for project_view latest Mission")
    if latest_mission is not None and isinstance(mission_card, dict):
        shared_fields = (
            "mission_id", "schema_version", "user_message", "status", "stop_reason",
            "blockers", "plan_id", "plan_hash",
            "workflow_run_id", "current_step", "step_count", "timeout_seconds",
            "selected_agents", "created_at", "updated_at", "confirmed_at", "completed_at",
            "can_resume", "status_command", "resume_command", "confirmation_command",
        )
        for field in shared_fields:
            if field in latest_mission and field in mission_card and mission_card.get(field) != latest_mission.get(field):
                errors.append(f"mission_card.{field} must match project_view latest Mission")
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
        autonomous_actions = control_mode_card.get("autonomous_actions")
        if isinstance(autonomous_actions, list):
            for control in autonomous_actions:
                if not isinstance(control, dict):
                    errors.append("autonomous action controls must be objects")
                    continue
                for field in WORKBENCH_CONTROL_MODE_CONTROL_FIELDS:
                    if field not in control:
                        errors.append(f"missing autonomous action control field: {field}")
                if "enabled" in control and not isinstance(control.get("enabled"), bool):
                    errors.append("autonomous action control enabled must be a boolean")
                if control.get("enabled") is False and not control.get("blocker"):
                    errors.append("disabled autonomous action control requires blocker")
        elif "autonomous_actions" in control_mode_card:
            errors.append("control_mode_card.autonomous_actions must be a list")
    elif "control_mode_card" in payload:
        errors.append("control_mode_card must be an object")
    control_registry_shape_valid = False
    control_registry = payload.get("control_registry")
    if isinstance(control_registry, list):
        control_registry_shape_valid = True
        control_ids: set[str] = set()
        duplicate_control_id = False
        for item in control_registry:
            if not isinstance(item, dict):
                errors.append("control_registry items must be objects")
                control_registry_shape_valid = False
                continue
            for field in WORKBENCH_CONTROL_REGISTRY_ITEM_FIELDS:
                if field not in item:
                    errors.append(f"missing control_registry item field: {field}")
                    control_registry_shape_valid = False
            control_id = item.get("control_id")
            if "control_id" in item:
                if not isinstance(control_id, str) or not control_id:
                    errors.append("control_registry item control_id must be a non-empty string")
                    control_registry_shape_valid = False
                elif control_id in control_ids:
                    duplicate_control_id = True
                else:
                    control_ids.add(control_id)
            if "enabled" in item and not isinstance(item.get("enabled"), bool):
                errors.append("control_registry item enabled must be a boolean")
                control_registry_shape_valid = False
            if item.get("enabled") is False and not item.get("blocker"):
                errors.append("disabled control_registry item requires blocker")
                control_registry_shape_valid = False
        if duplicate_control_id:
            errors.append("control_registry control_id values must be unique")
            control_registry_shape_valid = False
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
        if "provider_backend" in provider_health and not isinstance(provider_health.get("provider_backend"), str):
            errors.append("provider_health.provider_backend must be a string")
        if "provider_transport" in provider_health and not isinstance(provider_health.get("provider_transport"), str):
            errors.append("provider_health.provider_transport must be a string")
        leader_backend = provider_health.get("leader_backend")
        if isinstance(leader_backend, dict):
            _validate_leader_backend(errors, "provider_health", leader_backend)
        else:
            errors.append("provider_health.leader_backend must be an object")
        if "missing_env" in provider_health and not isinstance(provider_health.get("missing_env"), list):
            errors.append("provider_health.missing_env must be a list")
        if "setup_commands" in provider_health and not isinstance(provider_health.get("setup_commands"), list):
            errors.append("provider_health.setup_commands must be a list")
        controls = provider_health.get("controls")
        if isinstance(controls, list):
            for control in controls:
                if isinstance(control, dict):
                    if control.get("kind") in {"set_provider", "guarded_set_provider"}:
                        if control.get("safety") != "explicit_user":
                            errors.append("provider_health.controls: set_provider controls must use safety=explicit_user")
                        if not str(control.get("command") or "").startswith(
                            "agentdeck leader set-provider --provider "
                        ):
                            errors.append("provider_health.controls: set_provider command must use leader set-provider")
                        if control.get("kind") == "guarded_set_provider" and not str(
                            control.get("command") or ""
                        ).endswith(" --require-ready"):
                            errors.append(
                                "provider_health.controls: guarded_set_provider command must use --require-ready"
                            )
                    if control.get("kind") == "setup_provider":
                        if control.get("safety") != "explicit_user":
                            errors.append("provider_health.controls: setup_provider controls must use safety=explicit_user")
                        if control.get("command") not in leader_provider_setup_command_allowlist():
                            errors.append(
                                "provider_health.controls: setup_provider command must come from provider setup commands"
                            )
                    if control.get("enabled") is False and not control.get("blocker"):
                        errors.append("provider_health.controls: disabled controls must include blocker")
                else:
                    errors.append("provider_health.controls items must be objects")
        elif "controls" in provider_health:
            errors.append("provider_health.controls must be a list")
    elif "provider_health" in payload:
        errors.append("provider_health must be an object")
    runtime_card = payload.get("runtime_card")
    runtime_card_is_valid = False
    if isinstance(runtime_card, dict):
        runtime_error_count = len(errors)
        _validate_runtime_card_contract(errors, runtime_card, prefix="")
        runtime_card_is_valid = len(errors) == runtime_error_count
    elif "runtime_card" in payload:
        errors.append("runtime_card must be an object")
    agent_ready_card = payload.get("agent_ready_card")
    if isinstance(agent_ready_card, dict):
        _validate_agent_ready_card_contract(errors, agent_ready_card)
        if runtime_card_is_valid and agent_ready_card.get("runtime_card") != runtime_card:
            errors.append("agent_ready_card.runtime_card must match runtime_card")
    elif "agent_ready_card" in payload:
        errors.append("agent_ready_card must be an object")
    terminal_session_card = payload.get("terminal_session_card")
    if isinstance(terminal_session_card, dict):
        _validate_terminal_session_card_contract(errors, terminal_session_card)
    elif "terminal_session_card" in payload:
        errors.append("terminal_session_card must be an object")
    role_card = payload.get("role_card")
    if isinstance(role_card, dict):
        _validate_role_card_contract(errors, role_card, prefix="")
    elif "role_card" in payload:
        errors.append("role_card must be an object")
    worker_lifecycle_card = payload.get("worker_lifecycle_card")
    if isinstance(worker_lifecycle_card, dict):
        _validate_worker_lifecycle_card_contract(errors, worker_lifecycle_card, prefix="")
    elif "worker_lifecycle_card" in payload:
        errors.append("worker_lifecycle_card must be an object")
    review_gate_card = payload.get("review_gate_card")
    if isinstance(review_gate_card, dict):
        _validate_review_gate_card_contract(errors, review_gate_card, prefix="")
    elif "review_gate_card" in payload:
        errors.append("review_gate_card must be an object")
    release_preview_card = payload.get("release_preview_card")
    if isinstance(release_preview_card, dict):
        _validate_release_preview_card_contract(errors, release_preview_card, prefix="")
        if isinstance(review_gate_card, dict):
            if release_preview_card.get("status") == "released":
                if review_gate_card.get("can_release") is not True:
                    errors.append("released release_preview_card requires a ready review gate")
            elif release_preview_card.get("can_release") != review_gate_card.get("can_release"):
                errors.append("release_preview_card.can_release must match review_gate_card.can_release")
            if (
                release_preview_card.get("status") == "blocked"
                and release_preview_card.get("reason") != review_gate_card.get("reason")
            ):
                errors.append("blocked release_preview_card.reason must match review_gate_card.reason")
    elif "release_preview_card" in payload:
        errors.append("release_preview_card must be an object")
    role_topology_card = payload.get("role_topology_card")
    if isinstance(role_topology_card, dict):
        _validate_role_topology_card_contract(errors, role_topology_card, prefix="")
    elif "role_topology_card" in payload:
        errors.append("role_topology_card must be an object")
    ledger_card = payload.get("ledger_card")
    if isinstance(ledger_card, dict):
        _validate_ledger_card_contract(errors, ledger_card, prefix="")
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
        artifacts = ledger_card.get("artifacts")
        if isinstance(artifacts, dict):
            _validate_project_view_summary_items(
                errors, ledger_card, "artifacts", PROJECT_VIEW_ARTIFACT_ITEM_FIELDS, "artifact"
            )
        elif "artifacts" in ledger_card:
            errors.append("ledger_card.artifacts must be an object")
    elif "ledger_card" in payload:
        errors.append("ledger_card must be an object")
    lineage_card = payload.get("lineage_card")
    if isinstance(lineage_card, dict):
        _validate_lineage_card_contract(errors, lineage_card, prefix="")
    elif "lineage_card" in payload:
        errors.append("lineage_card must be an object")
    queue_card = payload.get("queue_card")
    if isinstance(queue_card, dict):
        _validate_queue_card_contract(errors, queue_card, prefix="")
        if "active_queue_source" in queue_card and payload.get("active_queue_source") != queue_card.get(
            "active_queue_source"
        ):
            errors.append("active_queue_source must match queue_card.active_queue_source")
        if "next_command" in queue_card and payload.get("next_command") != queue_card.get("next_command"):
            errors.append("next_command must match queue_card.next_command")
        if isinstance(project_view, dict):
            _validate_queue_card_project_view_alignment(errors, queue_card, project_view)
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
        _validate_operator_card_control_alignment(errors, operator_card)
        if operator_card.get("action_kind") == "approval_dispatch_ready":
            dispatch_ready_command_valid = operator_card.get("command") == "agentdeck approval dispatch-ready --confirm"
            dispatch_ready_explicit_valid = (
                operator_card.get("explicit_command") == "agentdeck approval dispatch-ready --confirm"
            )
            if not dispatch_ready_command_valid:
                errors.append(
                    "operator_card approval_dispatch_ready command must be agentdeck approval dispatch-ready --confirm"
                )
            if not dispatch_ready_explicit_valid:
                errors.append(
                    "operator_card approval_dispatch_ready explicit_command must be agentdeck approval dispatch-ready --confirm"
                )
            controls = operator_card.get("controls")
            if dispatch_ready_command_valid and dispatch_ready_explicit_valid and isinstance(controls, list):
                dispatch_controls = [
                    control
                    for control in controls
                    if isinstance(control, dict)
                    and control.get("command") == "agentdeck approval dispatch-ready --confirm"
                ]
                if not dispatch_controls:
                    errors.append("operator_card approval_dispatch_ready control is required")
                for control in dispatch_controls:
                    if control.get("kind") != "dispatch_ready":
                        errors.append("operator_card approval_dispatch_ready control kind must be dispatch_ready")
                    if control.get("enabled") != (operator_card.get("blocker") is None):
                        errors.append("operator_card approval_dispatch_ready control enabled must reflect blocker")
                    if control.get("blocker") != operator_card.get("blocker"):
                        errors.append("operator_card approval_dispatch_ready control blocker must match blocker")
    elif "operator_card" in payload:
        errors.append("operator_card must be an object")
    run_progress_card = payload.get("run_progress_card")
    if isinstance(run_progress_card, dict):
        run_progress_validation = validate_run_start_contract(run_progress_card)
        for error in run_progress_validation["errors"]:
            errors.append(f"run_progress_card: {error}")
    elif "run_progress_card" in payload and run_progress_card is not None:
        errors.append("run_progress_card must be an object")
    plan_board_card = payload.get("plan_board_card")
    if isinstance(plan_board_card, dict):
        plan_board_validation = validate_plan_board_contract(plan_board_card)
        for error in plan_board_validation["errors"]:
            errors.append(f"plan_board_card: {error}")
    else:
        errors.append("plan_board_card must be an object")
    skills_catalog_card = payload.get("skills_catalog_card")
    if isinstance(skills_catalog_card, dict):
        for field in WORKBENCH_SKILLS_CATALOG_CARD_FIELDS:
            if field not in skills_catalog_card:
                errors.append(f"skills_catalog_card missing field: {field}")
        if skills_catalog_card.get("mode") != "skills_catalog":
            errors.append("skills_catalog_card.mode must be skills_catalog")
        sources = skills_catalog_card.get("sources")
        if not isinstance(sources, list):
            errors.append("skills_catalog_card.sources must be a list")
        else:
            if skills_catalog_card.get("source_count") != len(sources):
                errors.append("skills_catalog_card.source_count must equal len(sources)")
            for index, source in enumerate(sources):
                if not isinstance(source, dict):
                    errors.append(f"skills_catalog_card.sources[{index}] must be an object")
                    continue
                for field in WORKBENCH_SKILLS_CATALOG_SOURCE_FIELDS:
                    if field not in source:
                        errors.append(f"skills_catalog_card.sources[{index}] missing field: {field}")
    else:
        errors.append("skills_catalog_card must be an object")
    audit_card = payload.get("audit_card")
    if isinstance(audit_card, dict):
        _validate_audit_card_contract(errors, audit_card, prefix="audit_card")
    elif "audit_card" in payload:
        errors.append("audit_card must be an object")
    artifacts_card = payload.get("artifacts_card")
    if isinstance(artifacts_card, dict):
        artifacts_validation = validate_artifacts_contract(artifacts_card)
        for error in artifacts_validation["errors"]:
            errors.append(f"artifacts_card: {error}")
    elif "artifacts_card" in payload:
        errors.append("artifacts_card must be an object")
    leader_summary_card = payload.get("leader_summary_card")
    if isinstance(leader_summary_card, dict):
        summary_validation = validate_leader_summary_contract(leader_summary_card)
        for error in summary_validation["errors"]:
            errors.append(f"leader_summary_card: {error}")
    elif "leader_summary_card" in payload and leader_summary_card is not None:
        errors.append("leader_summary_card must be an object")
    learning_review_card = payload.get("learning_review_card")
    if isinstance(learning_review_card, dict):
        learning_validation = validate_learning_review_contract(learning_review_card)
        for error in learning_validation["errors"]:
            errors.append(f"learning_review_card: {error}")
    elif "learning_review_card" in payload and learning_review_card is not None:
        errors.append("learning_review_card must be an object")
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
    if source not in ("none", "leader_action", "inbox", "approval", "provider_health", "runtime", "reply"):
        errors.append(
            "active_queue_source must be none, leader_action, inbox, approval, provider_health, runtime, or reply"
        )
    if source == "inbox" and not isinstance(inbox_card, dict):
        errors.append("inbox active queue requires inbox_card")
    if source == "approval" and not isinstance(approval_card, dict):
        errors.append("approval active queue requires approval_card")
    if source == "leader_action" and not isinstance(payload.get("leader_action"), dict):
        errors.append("leader_action active queue requires leader_action")
    if control_registry_shape_valid and not errors and control_registry != workbench_control_registry(payload):
        errors.append("control_registry must match workbench card controls")
    return {"ok": not errors, "errors": errors}


def project_view_example() -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "project": "agentdeck-example",
        "root": "/workspace/agentdeck-example",
        "runtime_backend": "tmux",
        "leader": {
            "agent_id": "leader",
            "provider": "fake",
            "model": "fake-plan",
            "approval_mode": "confirm",
            "leader_backend": {
                "agent_id": "leader",
                "provider": "fake",
                "model": "fake-plan",
                "provider_backend": "local",
                "provider_transport": "local",
                "reasoning_backend": "local-fake",
                "runtime_kind": "logical_leader",
                "pane_backed": False,
                "pane_id": None,
                "approval_required": True,
                "dispatch_ready": False,
            },
            "coordination_roles": [
                {
                    "role_id": "frontdesk",
                    "label": "Frontdesk intake",
                    "provider": "local-rule",
                    "model": "deterministic",
                    "lifecycle": "persistent",
                    "responsibility": "Intake human requests and route them without provider planning.",
                    "state_source": "chat_turns",
                    "runtime_kind": "logical_role",
                    "pane_backed": False,
                    "pane_id": None,
                    "dispatch_ready": False,
                    "approval_required": False,
                    "next_command": 'agentdeck leader chat --message "frontdesk <goal>"',
                },
                {
                    "role_id": "planner",
                    "label": "Planner",
                    "provider": "fake",
                    "model": "fake-plan",
                    "lifecycle": "persistent",
                    "responsibility": "Create macro plans and acceptance criteria without dispatching workers.",
                    "state_source": "plans",
                    "runtime_kind": "logical_role",
                    "pane_backed": False,
                    "pane_id": None,
                    "dispatch_ready": False,
                    "approval_required": True,
                    "next_command": "agentdeck leader plan --task <goal>",
                },
                {
                    "role_id": "orchestrator",
                    "label": "Orchestrator",
                    "provider": "fake",
                    "model": "fake-plan",
                    "lifecycle": "persistent",
                    "responsibility": "Review plans, choose approval-gated actions, and coordinate worker handoff.",
                    "state_source": "leader_actions",
                    "runtime_kind": "logical_role",
                    "pane_backed": False,
                    "pane_id": None,
                    "dispatch_ready": False,
                    "approval_required": True,
                    "next_command": "agentdeck leader review --plan-id <plan_id>",
                },
            ],
        },
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
        "missions": {
            "count": 1,
            "by_status": {"pending_confirmation": 1},
            "latest_id": "mis_0123456789ab",
            "items": [
                {
                    "mission_id": "mis_0123456789ab",
                    "schema_version": MISSION_SCHEMA_VERSION,
                    "user_message": "让 Codex 和 Claude 接龙",
                    "status": "pending_confirmation",
                    "stop_reason": None,
                    "can_start": True,
                    "can_resume": False,
                    "blockers": [],
                    "provider": "fake",
                    "model": "fake-plan",
                    "leader_backend": {
                        "agent_id": "leader",
                        "provider": "fake",
                        "model": "fake-plan",
                        "provider_backend": "local",
                        "provider_transport": "local",
                        "reasoning_backend": "local-fake",
                        "runtime_kind": "logical_leader",
                        "pane_backed": False,
                        "pane_id": None,
                        "approval_required": True,
                        "dispatch_ready": False,
                    },
                    "plan_id": "pln_example",
                    "plan_hash": "sha256:plan-example",
                    "workflow_run_id": None,
                    "current_step": 0,
                    "step_count": 2,
                    "timeout_seconds": 180,
                    "selected_agents": [
                        {
                            "agent_id": "planner",
                            "provider": "codex-cli",
                            "role": "planning",
                            "workspace_mode": "shared",
                            "runtime_status": "running",
                            "effective_model": "gpt-5.5",
                            "model_source": "configured",
                        },
                        {
                            "agent_id": "reviewer",
                            "provider": "claude-cli",
                            "role": "review",
                            "workspace_mode": "shared",
                            "runtime_status": "configured",
                            "effective_model": "opus-4.8",
                            "model_source": "configured",
                        },
                    ],
                    "startup_actions": [
                        {
                            "agent_id": "planner",
                            "action": "reuse",
                            "runtime_status": "running",
                            "effective_model": "gpt-5.5",
                            "model_source": "configured",
                        },
                        {
                            "agent_id": "reviewer",
                            "action": "spawn",
                            "runtime_status": "configured",
                            "effective_model": "opus-4.8",
                            "model_source": "configured",
                        },
                    ],
                    "created_at": "2026-07-11T00:00:00+00:00",
                    "updated_at": "2026-07-11T00:00:00+00:00",
                    "confirmed_at": None,
                    "completed_at": None,
                    "status_command": (
                        "agentdeck mission status --mission-id mis_0123456789ab"
                    ),
                    "confirmation_command": (
                        'agentdeck leader chat --message "批准执行 mis_0123456789ab"'
                    ),
                    "resume_command": (
                        "agentdeck mission resume --mission-id mis_0123456789ab --confirm"
                    ),
                }
            ],
        },
        "plans": {
            "count": 1,
            "items": [
                {
                    "plan_id": "pln_example",
                    "task": "Build a GUI-ready recovery panel",
                    "provider": "fake",
                    "provider_backend": "local",
                    "provider_transport": "local",
                    "leader_backend": {
                        "agent_id": "leader",
                        "provider": "fake",
                        "model": "fake-plan",
                        "provider_backend": "local",
                        "provider_transport": "local",
                        "reasoning_backend": "local-fake",
                        "runtime_kind": "logical_leader",
                        "pane_backed": False,
                        "pane_id": None,
                        "approval_required": True,
                        "dispatch_ready": False,
                    },
                    "model": "fake-plan",
                    "status": "planned",
                    "dispatch_ready": False,
                    "skill_context": {
                        "count": 1,
                        "by_agent": {"leader": 1},
                        "by_source": {"builtin": 1},
                        "items": [
                            {
                                "load_id": "skl_example",
                                "agent_id": "leader",
                                "purpose": "decompose task",
                                "name": "planning",
                                "source": "builtin",
                                "path": "builtin://planning/SKILL.md",
                                "content_hash": "sha256:example",
                                "description": "Break broad goals into reviewable steps.",
                                "required_tools": [],
                                "risk": "inspect",
                                "created_at": "2026-07-04T00:00:00+00:00",
                                "show_command": "agentdeck skills show --name planning",
                                "reload_command": (
                                    "agentdeck skills load --name planning --agent leader "
                                    "--purpose 'decompose task'"
                                ),
                            }
                        ],
                    },
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
                    "prompt_skill_context": {
                        "count": 1,
                        "by_agent": {"planner": 1},
                        "by_source": {"builtin": 1},
                        "items": [
                            {
                                "load_id": "skl_worker_example",
                                "agent_id": "planner",
                                "purpose": "worker dispatch context",
                                "name": "verification",
                                "source": "builtin",
                                "path": "builtin://verification/SKILL.md",
                                "content_hash": "sha256:worker-example",
                                "description": "Prove claims with fresh command output.",
                                "required_tools": [],
                                "risk": "inspect",
                                "created_at": "2026-07-04T00:00:00+00:00",
                                "show_command": "agentdeck skills show --name verification",
                                "reload_command": (
                                    "agentdeck skills load --name verification --agent planner "
                                    "--purpose 'worker dispatch context'"
                                ),
                            }
                        ],
                    },
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
        "artifacts": {
            "count": 1,
            "by_status": {"created": 1},
            "by_kind": {"markdown": 1},
            "items": [
                {
                    "artifact_id": "art_example",
                    "message_id": "msg_example",
                    "job_id": "job_example",
                    "reply_id": "rep_example",
                    "from_agent": "planner",
                    "path": "docs/example-plan.md",
                    "kind": "markdown",
                    "status": "created",
                    "created_at": "2026-07-04T00:00:02+00:00",
                    "trace_command": "agentdeck trace --id msg_example",
                }
            ],
        },
        "releases": {
            "count": 1,
            "items": [
                {
                    "release_id": "rel_example",
                    "round": 1,
                    "status": "released",
                    "review_gate_status": "ready",
                    "artifact_count": 1,
                    "review_reply_count": 2,
                    "code_reviewer_id": "reviewer",
                    "round_reviewer_id": "coder",
                    "code_review_reply_id": "rep_example",
                    "round_review_reply_id": "rep_round_example",
                    "created_at": "2026-07-04T00:00:03+00:00",
                    "trace_command": "agentdeck trace --id rep_round_example",
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
        "skills": {
            "count": 1,
            "by_agent": {"leader": 1},
            "by_source": {"builtin": 1},
            "items": [
                {
                    "load_id": "skl_example",
                    "agent_id": "leader",
                    "purpose": "decompose task",
                    "name": "planning",
                    "source": "builtin",
                    "path": "builtin://planning/SKILL.md",
                    "content_hash": "sha256:example",
                    "description": "Break broad goals into reviewable steps.",
                    "required_tools": [],
                    "planning_guidance": [],
                    "risk": "inspect",
                    "created_at": "2026-07-04T00:00:00+00:00",
                    "show_command": "agentdeck skills show --name planning",
                    "reload_command": "agentdeck skills load --name planning --agent leader --purpose 'decompose task'",
                }
            ],
        },
        "memory": {
            "count": 1,
            "by_scope": {"project": 1},
            "items": [
                {
                    "scope": "project",
                    "path": ".agentdeck/memory/project.md",
                    "exists": True,
                    "line_count": 6,
                    "byte_count": 172,
                    "content_hash": "sha256:memoryexample",
                    "preview": "- Keep approval-gated worker dispatch.",
                }
            ],
        },
        "agent_sessions": {"count": 1, "by_state": {"ready": 1}, "items": [{
            "session_id": "ags_example", "agent_id": "planner", "provider": "codex-cli",
            "transport": "native", "state": "ready", "capabilities": {
                "structured_sessions": True, "streaming_updates": True,
                "structured_tools": True, "permission_requests": True,
                "resume_session": True, "observable_terminal": False,
            }, "native_session_present": True, "workspace": "/workspace/agentdeck-example",
            "created_at": "2026-07-04T00:00:00+00:00", "updated_at": "2026-07-04T00:00:01+00:00",
        }]},
        "protocol_turns": {"count": 1, "by_state": {"waiting_permission": 1}, "items": [{
            "turn_id": "trn_example", "session_id": "ags_example", "message_id": "msg_example",
            "state": "waiting_permission", "created_at": "2026-07-04T00:00:02+00:00",
            "updated_at": "2026-07-04T00:00:03+00:00",
        }]},
        "transport_updates": {"count": 1, "by_kind": {"permission_request": 1}, "items": [{
            "update_id": "upd_example", "session_id": "ags_example", "turn_id": "trn_example",
            "sequence": 0, "kind": "permission_request", "created_at": "2026-07-04T00:00:04+00:00",
        }]},
        "permission_requests": {
            "count": 1, "pending_count": 1, "by_status": {"pending": 1}, "items": [{
                "permission_id": "prm_example", "session_id": "ags_example", "turn_id": "trn_example",
                "tool_name": "write_file", "risk": "high", "status": "pending", "decision": None,
                "created_at": "2026-07-04T00:00:05+00:00",
            }],
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
                "reply_waiting": 0,
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
    terminal_card = agent_runtime_example()["terminal"]
    runtime_card = workbench_example()["runtime_card"]
    agent_ready_card = _agent_ready_card_from_runtime_card(runtime_card)
    provider_health = workbench_example()["provider_health"]
    queue_card = workbench_example()["queue_card"]
    operator_card = workbench_example()["operator_card"]
    role_card = workbench_example()["role_card"]
    review_gate_card = workbench_example()["review_gate_card"]
    release_preview_card = workbench_example()["release_preview_card"]
    role_topology_card = workbench_example()["role_topology_card"]
    ledger_card = workbench_example()["ledger_card"]
    lineage_card = workbench_example()["lineage_card"]
    artifacts_card = artifacts_example()
    workbench_card = workbench_example()
    audit_card = workbench_card["audit_card"]
    control_mode_card = workbench_card["control_mode_card"]
    capability_card = leader_chat_capability_card()
    frontdesk_card = {
        "mode": "frontdesk",
        "title": "Frontdesk intake",
        "summary": "Frontdesk routed the request without calling a planning provider.",
        "user_message": "frontdesk Build a multi-agent smoke test",
        "intake_summary": "Build a multi-agent smoke test",
        "classification": "planning_candidate",
        "next_command": "agentdeck leader plan --task 'Build a multi-agent smoke test'",
        "controls": [
            {
                "kind": "inspect",
                "label": "Open Leader help",
                "command": 'agentdeck leader chat --message "帮助"',
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "plan",
                "label": "Create Leader plan",
                "command": "agentdeck leader plan --task 'Build a multi-agent smoke test'",
                "safety": "plan_only",
                "enabled": True,
                "blocker": None,
            },
        ],
    }
    skill_context_card = {
        "mode": "skill_context",
        "title": "Loaded skill context",
        "summary": "1 loaded skill is available as replayable context.",
        "skills_command": "agentdeck skills list",
        "project_view_command": "agentdeck status",
        "count": 1,
        "items": project_view["skills"]["items"],
        "controls": [
            {
                "kind": "inspect",
                "label": "List skills",
                "command": "agentdeck skills list",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "inspect",
                "label": "Open project status",
                "command": "agentdeck status",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
        ],
    }
    memory_context_card = {
        "mode": "memory_context",
        "title": "Long-term memory context",
        "summary": "1 memory file is available for human review.",
        "project_view_command": "agentdeck status",
        "suggestions_command": "agentdeck memory suggestions",
        "count": 1,
        "items": project_view["memory"]["items"],
        "controls": [
            {
                "kind": "inspect",
                "label": "Open project status",
                "command": "agentdeck status",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "inspect",
                "label": "List memory suggestions",
                "command": "agentdeck memory suggestions",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
        ],
    }
    skill_import_preview_card = {
        "ok": True,
        "mode": "skill_import_preview",
        "title": "External skill import preview",
        "summary": "planning can be imported without overwriting an existing project skill.",
        "skill": {
            "name": "planning",
            "description": "Break goals into approval-gated multi-agent plans.",
            "source": "project",
            "path": "/workspace/project/.agentdeck/skills/planning/SKILL.md",
            "content_hash": "sha256:example",
            "required_tools": ["leader-plan", "approval-list"],
            "risk": "inspect",
            "show_command": "agentdeck skills show --name planning",
            "load_command": "agentdeck skills load --name planning",
            "controls": [
                {
                    "kind": "show",
                    "label": "Show skill",
                    "command": "agentdeck skills show --name planning",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "load",
                    "label": "Load skill",
                    "command": "agentdeck skills load --name planning",
                    "safety": "explicit_user",
                    "enabled": True,
                    "blocker": None,
                },
            ],
        },
        "source_path": "/external/skills/planning/SKILL.md",
        "project_path": "/workspace/project/.agentdeck/skills/planning/SKILL.md",
        "would_overwrite": False,
        "import_command": "agentdeck skills import --path /external/skills/planning/SKILL.md",
        "force_import_command": "agentdeck skills import --path /external/skills/planning/SKILL.md --force",
        "controls": [
            {
                "kind": "import",
                "label": "Import skill",
                "command": "agentdeck skills import --path /external/skills/planning/SKILL.md",
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "force_import",
                "label": "Force import skill",
                "command": "agentdeck skills import --path /external/skills/planning/SKILL.md --force",
                "safety": "explicit_user",
                "enabled": False,
                "blocker": "skill does not exist",
            },
            {
                "kind": "show_after_import",
                "label": "Show skill after import",
                "command": "agentdeck skills show --name planning",
                "safety": "inspect",
                "enabled": False,
                "blocker": "skill is not imported yet",
            },
        ],
    }
    skill_load_preview_card = {
        "ok": True,
        "mode": "skill_load_preview",
        "title": "Skill load preview",
        "summary": "planning can be loaded for planner as replayable context.",
        "agent_id": "planner",
        "purpose": "plan decomposition",
        "skill": {
            "name": "planning",
            "description": "Break goals into approval-gated multi-agent plans.",
            "source": "builtin",
            "path": None,
            "content_hash": "sha256:example",
            "required_tools": ["leader-plan", "approval-list"],
            "risk": "inspect",
            "show_command": "agentdeck skills show --name planning",
            "load_command": "agentdeck skills load --name planning",
            "controls": [
                {
                    "kind": "show",
                    "label": "Show skill",
                    "command": "agentdeck skills show --name planning",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "load",
                    "label": "Load skill",
                    "command": "agentdeck skills load --name planning",
                    "safety": "explicit_user",
                    "enabled": True,
                    "blocker": None,
                },
            ],
        },
        "load_command": "agentdeck skills load --name planning --agent planner --purpose 'plan decomposition'",
        "controls": [
            {
                "kind": "load",
                "label": "Load skill",
                "command": "agentdeck skills load --name planning --agent planner --purpose 'plan decomposition'",
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "show",
                "label": "Show skill",
                "command": "agentdeck skills show --name planning",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
        ],
    }
    skill_create_preview_card = {
        "mode": "skill_create_preview",
        "suggestion_id": "sgs_example",
        "suggestion": {
            "suggestion_id": "sgs_example",
            "status": "pending",
            "name": "incident-review",
            "summary": "Review incident response evidence.",
            "rationale": "planner repeatedly asked for the same incident review checklist",
            "source": "leader",
            "agent_id": "reviewer",
            "trace_id": "msg_example",
            "draft_path": ".agentdeck/skills/incident-review/SKILL.md",
            "created_at": "2026-07-07T00:00:00Z",
        },
        "name": "incident-review",
        "target_path": ".agentdeck/skills/incident-review/SKILL.md",
        "would_create": True,
        "would_overwrite": False,
        "source": "leader",
        "agent_id": "reviewer",
        "trace_id": "msg_example",
        "proposed_content": "---\nname: incident-review\n---\n\n# incident-review\n",
        "proposed_content_hash": "sha256:example",
        "draft_preview_command": "agentdeck skills draft-preview --suggestion-id sgs_example",
        "create_command": "agentdeck skills create --suggestion-id sgs_example --confirm",
        "controls": [
            {
                "kind": "create_skill",
                "label": "Create skill",
                "command": "agentdeck skills create --suggestion-id sgs_example --confirm",
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "draft_preview",
                "label": "Preview skill draft",
                "command": "agentdeck skills draft-preview --suggestion-id sgs_example",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "list_suggestions",
                "label": "List skill suggestions",
                "command": "agentdeck skills suggestions",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
        ],
    }
    skill_suggestions_card = {
        "mode": "skill_suggestions",
        "title": "Skill suggestions",
        "summary": "1 pending skill suggestion is waiting for human review.",
        "suggestions_command": "agentdeck skills suggestions",
        "project_view_command": "agentdeck status",
        "count": 1,
        "pending_count": 1,
        "items": [
            {
                "suggestion_id": "sgs_example",
                "status": "pending",
                "name": "incident-review",
                "summary": "Review incident response evidence.",
                "rationale": "planner repeatedly asked for the same incident review checklist",
                "source": "leader",
                "agent_id": "reviewer",
                "trace_id": "msg_example",
                "draft_path": ".agentdeck/skills/incident-review/SKILL.md",
                "created_at": "2026-07-07T00:00:00Z",
                "controls": [
                    {
                        "kind": "inspect",
                        "label": "List skill suggestions",
                        "command": "agentdeck skills suggestions",
                        "safety": "inspect",
                        "enabled": True,
                        "blocker": None,
                    }
                ],
            }
        ],
        "controls": [
            {
                "kind": "inspect",
                "label": "List skill suggestions",
                "command": "agentdeck skills suggestions",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "inspect",
                "label": "Open project status",
                "command": "agentdeck status",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
        ],
    }
    memory_suggestions_card = {
        "mode": "memory_suggestions",
        "title": "Memory suggestions",
        "summary": "1 pending memory suggestion is waiting for human review.",
        "suggestions_command": "agentdeck memory suggestions",
        "apply_preview_command_template": "agentdeck memory apply-preview --suggestion-id <id>",
        "project_view_command": "agentdeck status",
        "count": 1,
        "pending_count": 1,
        "items": [
            {
                "suggestion_id": "mem_example",
                "status": "pending",
                "scope": "project",
                "summary": "Keep approval-gated worker dispatch.",
                "rationale": "project safety preference",
                "source": "reviewer",
                "agent_id": "leader",
                "trace_id": "msg_memory",
                "target": ".agentdeck/memory/project.md",
                "created_at": "2026-07-07T00:00:00Z",
                "controls": [
                    {
                        "kind": "inspect",
                        "label": "List memory suggestions",
                        "command": "agentdeck memory suggestions",
                        "safety": "inspect",
                        "enabled": True,
                        "blocker": None,
                    },
                    {
                        "kind": "apply_preview",
                        "label": "Preview memory apply",
                        "command": "agentdeck memory apply-preview --suggestion-id mem_example",
                        "safety": "inspect",
                        "enabled": True,
                        "blocker": None,
                    },
                    {
                        "kind": "apply_memory",
                        "label": "Apply memory suggestion",
                        "command": "agentdeck memory apply --suggestion-id mem_example --confirm",
                        "safety": "explicit_user",
                        "enabled": True,
                        "blocker": None,
                    }
                ],
            }
        ],
        "controls": [
            {
                "kind": "inspect",
                "label": "List memory suggestions",
                "command": "agentdeck memory suggestions",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "apply_preview",
                "label": "Preview memory apply",
                "command": "agentdeck memory apply-preview --suggestion-id <id>",
                "safety": "inspect",
                "enabled": False,
                "blocker": "requires suggestion id",
            },
            {
                "kind": "inspect",
                "label": "Open project status",
                "command": "agentdeck status",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
        ],
    }
    memory_apply_preview_card = {
        "ok": True,
        "mode": "memory_apply_preview",
        "suggestion_id": "mem_example",
        "suggestion": memory_suggestions_card["items"][0],
        "target": ".agentdeck/memory/project.md",
        "target_exists": False,
        "would_create": True,
        "would_update_status": "applied",
        "proposed_append": (
            "- Keep approval-gated worker dispatch.\n"
            "  - rationale: project safety preference\n"
            "  - source: reviewer\n"
            "  - agent_id: leader\n"
            "  - trace_id: msg_memory\n"
            "  - suggestion_id: mem_example\n"
        ),
        "apply_command": "agentdeck memory apply --suggestion-id mem_example --confirm",
        "controls": [
            {
                "kind": "inspect",
                "label": "List memory suggestions",
                "command": "agentdeck memory suggestions",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "apply_memory",
                "label": "Apply memory suggestion",
                "command": "agentdeck memory apply --suggestion-id mem_example --confirm",
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
        ],
    }
    control_registry_card = leader_chat_control_registry_card(workbench_card)
    startup_preview_card = _startup_preview_card_from_agent_ready(agent_ready_card)
    runtime_action_card = {
        "mode": "runtime_action",
        "title": "Send input to planner",
        "action": "send",
        "agent_id": "planner",
        "role": "planner",
        "runtime_status": "running",
        "pane_id": "%1",
        "command": "agentdeck agent send --agent planner --text '继续'",
        "preview_text": "继续",
        "requires_explicit_user": True,
        "safety": "explicit_runtime",
        "blocker": None,
        "controls": [
            {
                "kind": "inspect",
                "label": "Inspect planner runtime",
                "command": "agentdeck agent terminal --agent planner",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "send",
                "label": "Send input to planner",
                "command": "agentdeck agent send --agent planner --text '继续'",
                "safety": "explicit_runtime",
                "enabled": True,
                "blocker": None,
            },
        ],
    }
    leader_action_card = leader_chat_action_card(leader_action)
    leader_summary_card = leader_summary_example()
    learning_review_card = learning_review_example()
    leader_status_card = leader_status_example()
    run_start_card = run_start_example()
    run_progress_card = run_progress_example()
    provider_switch_card = {
        "mode": "provider_switch",
        "title": "Switch Leader provider",
        "current_provider": "fake",
        "current_model": "fake-plan",
        "target_provider": "codex-cli",
        "target_model": "codex-default",
        "target_leader_backend": {
            "agent_id": "leader",
            "provider": "codex-cli",
            "model": "codex-default",
            "provider_backend": "cli",
            "provider_transport": "subprocess",
            "reasoning_backend": "cli-subprocess",
            "runtime_kind": "logical_leader",
            "pane_backed": False,
            "pane_id": None,
            "approval_required": True,
            "dispatch_ready": False,
        },
        "target_readiness": {
            "agent_id": "leader",
            "provider": "codex-cli",
            "model": "codex-default",
            "approval_mode": "confirm",
            "provider_backend": "cli",
            "provider_transport": "subprocess",
            "leader_backend": {
                "agent_id": "leader",
                "provider": "codex-cli",
                "model": "codex-default",
                "provider_backend": "cli",
                "provider_transport": "subprocess",
                "reasoning_backend": "cli-subprocess",
                "runtime_kind": "logical_leader",
                "pane_backed": False,
                "pane_id": None,
                "approval_required": True,
                "dispatch_ready": False,
            },
            "ready": False,
            "supported": True,
            "missing_env": [],
            "detail": "codex is not found on PATH",
            "command_path": None,
            "setup_commands": ["codex login", "codex doctor"],
        },
        "require_ready": False,
        "command": "agentdeck leader set-provider --provider codex-cli --model codex-default",
        "diagnostics_command": "agentdeck doctor",
        "safety": "explicit_user",
        "requires_explicit_user": True,
        "mutates_config": False,
        "controls": [
            {
                "kind": "inspect",
                "label": "Inspect provider setup",
                "command": "agentdeck doctor",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "set_provider",
                "label": "Switch Leader provider",
                "command": "agentdeck leader set-provider --provider codex-cli --model codex-default",
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
        ],
    }
    provider_setup_card = {
        "mode": "provider_setup",
        "title": "Set up Leader provider",
        "target_provider": "codex-cli",
        "target_model": "codex-default",
        "setup_commands": ["codex login", "codex doctor"],
        "recommended_command": "codex login",
        "recommended_control_id": "provider:provider_health:setup_provider:leader:example",
        "followup_switch_command": "agentdeck leader set-provider --provider codex-cli --model codex-default",
        "require_ready": False,
        "safety": "explicit_user",
        "requires_explicit_user": True,
        "mutates_config": False,
        "controls": [
            {
                "kind": "setup_provider",
                "label": "Run provider setup",
                "command": "codex login",
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
                "control_id": "provider:provider_health:setup_provider:leader:example",
            },
            {
                "kind": "setup_provider",
                "label": "Run provider setup",
                "command": "codex doctor",
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
                "control_id": "provider:provider_health:setup_provider:leader:example-doctor",
            },
            {
                "kind": "set_provider",
                "label": "Switch Leader provider",
                "command": "agentdeck leader set-provider --provider codex-cli --model codex-default",
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
        ],
    }
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
            "secondary_embedded_cards": [],
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
        "learning_review_card": learning_review_card,
        "leader_summary_card": leader_summary_card,
        "leader_status_card": leader_status_card,
        "frontdesk_card": frontdesk_card,
        "skill_context_card": skill_context_card,
        "memory_context_card": memory_context_card,
        "skill_import_preview_card": skill_import_preview_card,
        "skill_load_preview_card": skill_load_preview_card,
        "skill_create_preview_card": skill_create_preview_card,
        "skill_suggestions_card": skill_suggestions_card,
        "memory_apply_preview_card": memory_apply_preview_card,
        "memory_suggestions_card": memory_suggestions_card,
        "continue_card": continue_card,
        "run_start_card": run_start_card,
        "run_progress_card": run_progress_card,
        "plan_board_card": None,
        "skills_catalog_card": None,
        "run_loop_preview_card": None,
        "mission_preview_card": None,
        "mission_status_card": None,
        "mission_run_card": None,
        "capture_card": None,
        "terminal_card": terminal_card,
        "dispatch_preview_card": None,
        "dispatch_batch_preview_card": None,
        "runtime_action_card": runtime_action_card,
        "startup_preview_card": startup_preview_card,
        "provider_setup_card": provider_setup_card,
        "provider_switch_card": provider_switch_card,
        "agent_ready_card": agent_ready_card,
        "inbox_card": None,
        "trace_card": None,
        "approval_card": None,
        "runtime_card": runtime_card,
        "terminal_session_card": workbench_card["terminal_session_card"],
        "queue_card": queue_card,
        "operator_card": operator_card,
        "role_card": role_card,
        "review_gate_card": review_gate_card,
        "release_preview_card": release_preview_card,
        "role_topology_card": role_topology_card,
        "ledger_card": ledger_card,
        "lineage_card": lineage_card,
        "audit_card": audit_card,
        "artifacts_card": artifacts_card,
        "workbench_card": workbench_card,
        "control_mode_card": control_mode_card,
        "provider_health": provider_health,
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
            "provider_backend": "api",
            "provider_transport": "http",
            "leader_backend": {
                "agent_id": "leader",
                "provider": "deepseek",
                "model": "deepseek-chat",
                "provider_backend": "api",
                "provider_transport": "http",
                "reasoning_backend": "api-llm",
                "runtime_kind": "logical_leader",
                "pane_backed": False,
                "pane_id": None,
                "approval_required": True,
                "dispatch_ready": False,
            },
            "ready": False,
            "supported": True,
            "missing_env": ["DEEPSEEK_API_KEY"],
            "detail": "DEEPSEEK_API_KEY is not set; provider calls are disabled",
            "command_path": None,
            "setup_commands": [
                'export DEEPSEEK_API_KEY="<your-deepseek-api-key>"',
                'export DEEPSEEK_BASE_URL="https://api.deepseek.com/v1"',
                'export DEEPSEEK_MODEL="deepseek-chat"',
            ],
        },
        "deepseek": {
            "ok": False,
            "detail": "DEEPSEEK_API_KEY is not set; provider calls are disabled",
            "provider_backend": "api",
            "provider_transport": "http",
            "command_path": None,
            "setup_commands": [
                'export DEEPSEEK_API_KEY="<your-deepseek-api-key>"',
                'export DEEPSEEK_BASE_URL="https://api.deepseek.com/v1"',
                'export DEEPSEEK_MODEL="deepseek-chat"',
            ],
        },
        "openai_compatible": {
            "ok": False,
            "detail": "AGENTDECK_LEADER_API_KEY is not set; provider calls are disabled",
            "provider_backend": "api",
            "provider_transport": "http",
            "command_path": None,
            "setup_commands": [
                'export AGENTDECK_LEADER_API_KEY="<your-provider-api-key>"',
                'export AGENTDECK_LEADER_BASE_URL="https://api.example.com/v1"',
                'export AGENTDECK_LEADER_MODEL="<model-name>"',
            ],
        },
        "codex_cli": {
            "ok": True,
            "detail": "codex is available",
            "provider_backend": "cli",
            "provider_transport": "subprocess",
            "command_path": "/usr/local/bin/codex",
            "setup_commands": ["codex login", "codex doctor"],
        },
        "claude_cli": {
            "ok": False,
            "detail": "claude is not found on PATH",
            "provider_backend": "cli",
            "provider_transport": "subprocess",
            "command_path": None,
            "setup_commands": ["claude auth", "claude doctor"],
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
                "kind": "terminal",
                "label": "Open terminal",
                "command": f"agentdeck agent terminal --agent {agent_id}",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
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
            "kind": "terminal",
            "label": "Open terminal",
            "command": f"agentdeck agent terminal --agent {agent_id}",
            "safety": "inspect",
            "enabled": False,
            "blocker": "agent is not running",
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


def terminal_card_controls(
    *,
    attach_command: str,
    select_pane_command: str,
    capture_command: str,
    send_command_template: str,
    stop_command: str,
    inbox_command: str,
) -> list[dict[str, object]]:
    return [
        {
            "kind": "terminal",
            "label": "Open terminal",
            "command": attach_command,
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "select_pane",
            "label": "Select pane",
            "command": select_pane_command,
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "capture",
            "label": "Capture pane output",
            "command": capture_command,
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "send",
            "label": "Send input",
            "command": send_command_template,
            "safety": "explicit_runtime",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "stop",
            "label": "Stop pane",
            "command": stop_command,
            "safety": "explicit_runtime",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "inbox",
            "label": "Open inbox",
            "command": inbox_command,
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
    ]


def role_agent_controls(agent_id: str) -> list[dict[str, object]]:
    return [
        {
            "kind": "assign_role",
            "label": "Assign role",
            "command": f"agentdeck agent assign-role --agent {agent_id} --role <role> --role-prompt <role_prompt>",
            "safety": "explicit_user",
            "enabled": False,
            "blocker": "requires role and role_prompt",
        }
    ]


def ledger_card_controls() -> list[dict[str, object]]:
    return [
        {
            "kind": "inspect",
            "label": "Inspect communication ledger",
            "command": "agentdeck workbench",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        }
    ]


def workbench_control_registry(payload: dict[str, object]) -> list[dict[str, object]]:
    registry: list[dict[str, object]] = []
    mission_card = payload.get("mission_card") if isinstance(payload.get("mission_card"), dict) else {}
    _append_control_registry_items(
        registry,
        scope="mission",
        card="mission_card",
        agent_id="leader",
        controls=mission_card.get("controls"),
    )
    leader_card = payload.get("leader_card") if isinstance(payload.get("leader_card"), dict) else {}
    _append_control_registry_items(
        registry,
        scope="leader",
        card="leader_card",
        agent_id=str(leader_card.get("agent_id", "leader")),
        controls=leader_card.get("controls"),
    )
    provider_health = payload.get("provider_health") if isinstance(payload.get("provider_health"), dict) else {}
    _append_control_registry_items(
        registry,
        scope="provider",
        card="provider_health",
        agent_id=provider_health.get("agent_id") or "leader",
        controls=provider_health.get("controls"),
    )
    control_mode_card = payload.get("control_mode_card") if isinstance(payload.get("control_mode_card"), dict) else {}
    _append_control_registry_items(
        registry,
        scope="policy",
        card="control_mode_card",
        agent_id=None,
        controls=control_mode_card.get("active_controls"),
    )
    _append_control_registry_items(
        registry,
        scope="autonomous",
        card="control_mode_card",
        agent_id=None,
        controls=control_mode_card.get("autonomous_actions"),
    )
    agent_ready_card = payload.get("agent_ready_card") if isinstance(payload.get("agent_ready_card"), dict) else {}
    _append_control_registry_items(
        registry,
        scope="agent_ready",
        card="agent_ready_card",
        agent_id=None,
        controls=agent_ready_card.get("controls"),
    )
    run_progress_card = (
        payload.get("run_progress_card")
        if isinstance(payload.get("run_progress_card"), dict)
        else {}
    )
    _append_control_registry_items(
        registry,
        scope="run_progress",
        card="run_progress_card",
        agent_id="leader",
        controls=run_progress_card.get("controls"),
    )
    terminal_session_card = (
        payload.get("terminal_session_card") if isinstance(payload.get("terminal_session_card"), dict) else {}
    )
    _append_control_registry_items(
        registry,
        scope="terminal_session",
        card="terminal_session_card",
        agent_id=None,
        controls=terminal_session_card.get("controls"),
    )
    terminal_session_items = (
        terminal_session_card.get("terminals") if isinstance(terminal_session_card.get("terminals"), list) else []
    )
    for terminal in terminal_session_items:
        if isinstance(terminal, dict):
            _append_control_registry_items(
                registry,
                scope="terminal_session",
                card="terminal_session_card",
                agent_id=terminal.get("agent_id"),
                controls=terminal.get("controls"),
            )
    terminal_card = payload.get("terminal_card") if isinstance(payload.get("terminal_card"), dict) else {}
    _append_control_registry_items(
        registry,
        scope="terminal",
        card="terminal_card",
        agent_id=terminal_card.get("agent_id"),
        controls=terminal_card.get("controls"),
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
    capture_card = payload.get("capture_card") if isinstance(payload.get("capture_card"), dict) else {}
    _append_control_registry_items(
        registry,
        scope="capture",
        card="capture_card",
        agent_id=capture_card.get("agent_id"),
        controls=capture_card.get("controls"),
    )
    role_card = payload.get("role_card") if isinstance(payload.get("role_card"), dict) else {}
    role_agents = role_card.get("agents") if isinstance(role_card.get("agents"), list) else []
    for agent in role_agents:
        if isinstance(agent, dict):
            _append_control_registry_items(
                registry,
                scope="role",
                card="role_card",
                agent_id=agent.get("agent_id"),
                controls=agent.get("controls"),
            )
    worker_lifecycle_card = (
        payload.get("worker_lifecycle_card") if isinstance(payload.get("worker_lifecycle_card"), dict) else {}
    )
    _append_control_registry_items(
        registry,
        scope="worker_lifecycle",
        card="worker_lifecycle_card",
        agent_id=None,
        controls=worker_lifecycle_card.get("controls"),
    )
    worker_lifecycle_items = (
        worker_lifecycle_card.get("items") if isinstance(worker_lifecycle_card.get("items"), list) else []
    )
    for item in worker_lifecycle_items:
        if isinstance(item, dict):
            _append_control_registry_items(
                registry,
                scope="worker_lifecycle",
                card="worker_lifecycle_card",
                agent_id=item.get("agent_id"),
                controls=item.get("controls"),
            )
    review_gate_card = payload.get("review_gate_card") if isinstance(payload.get("review_gate_card"), dict) else {}
    _append_control_registry_items(
        registry,
        scope="review_gate",
        card="review_gate_card",
        agent_id=None,
        controls=review_gate_card.get("controls"),
    )
    for stage_name in ("code_review", "round_review"):
        stage = review_gate_card.get(stage_name) if isinstance(review_gate_card.get(stage_name), dict) else {}
        _append_control_registry_items(
            registry,
            scope="review_gate",
            card="review_gate_card",
            agent_id=stage.get("agent_id"),
            controls=stage.get("controls"),
        )
    release_preview_card = (
        payload.get("release_preview_card") if isinstance(payload.get("release_preview_card"), dict) else {}
    )
    _append_control_registry_items(
        registry,
        scope="release_preview",
        card="release_preview_card",
        agent_id=None,
        controls=release_preview_card.get("controls"),
    )
    role_topology_card = (
        payload.get("role_topology_card") if isinstance(payload.get("role_topology_card"), dict) else {}
    )
    _append_control_registry_items(
        registry,
        scope="role_topology",
        card="role_topology_card",
        agent_id=None,
        controls=role_topology_card.get("controls"),
    )
    role_topology_roles = (
        role_topology_card.get("roles") if isinstance(role_topology_card.get("roles"), list) else []
    )
    for role in role_topology_roles:
        if isinstance(role, dict):
            _append_control_registry_items(
                registry,
                scope="role_topology",
                card="role_topology_card",
                agent_id=role.get("agent_id"),
                controls=role.get("controls"),
            )
    ledger_card = payload.get("ledger_card") if isinstance(payload.get("ledger_card"), dict) else {}
    _append_control_registry_items(
        registry,
        scope="ledger",
        card="ledger_card",
        agent_id=None,
        controls=ledger_card.get("controls"),
    )
    inbox_card = payload.get("inbox_card") if isinstance(payload.get("inbox_card"), dict) else {}
    _append_inbox_control_registry_items(
        registry,
        card="inbox_card",
        inbox_card=inbox_card,
    )
    leader_inbox_card = payload.get("leader_inbox_card") if isinstance(payload.get("leader_inbox_card"), dict) else {}
    _append_inbox_control_registry_items(
        registry,
        card="leader_inbox_card",
        inbox_card=leader_inbox_card,
    )
    operator_card = payload.get("operator_card") if isinstance(payload.get("operator_card"), dict) else {}
    _append_control_registry_items(
        registry,
        scope="operator",
        card="operator_card",
        agent_id=None,
        controls=operator_card.get("controls"),
    )
    artifacts_card = payload.get("artifacts_card") if isinstance(payload.get("artifacts_card"), dict) else {}
    _append_control_registry_items(
        registry,
        scope="artifacts",
        card="artifacts_card",
        agent_id=None,
        controls=artifacts_card.get("controls"),
    )
    skill_context_card = payload.get("skill_context_card") if isinstance(payload.get("skill_context_card"), dict) else {}
    _append_control_registry_items(
        registry,
        scope="skills",
        card="skill_context_card",
        agent_id=None,
        controls=skill_context_card.get("controls"),
    )
    skill_suggestions_card = (
        payload.get("skill_suggestions_card") if isinstance(payload.get("skill_suggestions_card"), dict) else {}
    )
    _append_control_registry_items(
        registry,
        scope="skills",
        card="skill_suggestions_card",
        agent_id=None,
        controls=skill_suggestions_card.get("controls"),
    )
    memory_context_card = (
        payload.get("memory_context_card") if isinstance(payload.get("memory_context_card"), dict) else {}
    )
    _append_control_registry_items(
        registry,
        scope="memory",
        card="memory_context_card",
        agent_id=None,
        controls=memory_context_card.get("controls"),
    )
    memory_suggestions_card = (
        payload.get("memory_suggestions_card") if isinstance(payload.get("memory_suggestions_card"), dict) else {}
    )
    _append_control_registry_items(
        registry,
        scope="memory",
        card="memory_suggestions_card",
        agent_id=None,
        controls=memory_suggestions_card.get("controls"),
    )
    leader_summary_card = (
        payload.get("leader_summary_card")
        if isinstance(payload.get("leader_summary_card"), dict)
        else {}
    )
    _append_control_registry_items(
        registry,
        scope="leader_summary",
        card="leader_summary_card",
        agent_id="leader",
        controls=leader_summary_card.get("controls"),
    )
    learning_review_card = (
        payload.get("learning_review_card")
        if isinstance(payload.get("learning_review_card"), dict)
        else {}
    )
    _append_control_registry_items(
        registry,
        scope="learning_review",
        card="learning_review_card",
        agent_id="leader",
        controls=learning_review_card.get("controls"),
    )
    audit_card = payload.get("audit_card") if isinstance(payload.get("audit_card"), dict) else {}
    _append_control_registry_items(
        registry,
        scope="audit",
        card="audit_card",
        agent_id=None,
        controls=audit_card.get("controls"),
    )
    trace_card = payload.get("trace_card") if isinstance(payload.get("trace_card"), dict) else {}
    _append_control_registry_items(
        registry,
        scope="trace",
        card="trace_card",
        agent_id=None,
        controls=trace_card.get("controls"),
    )
    return registry


def _append_inbox_control_registry_items(
    registry: list[dict[str, object]],
    *,
    card: str,
    inbox_card: dict[str, object],
) -> None:
    agent_id = inbox_card.get("agent_id")
    items = inbox_card.get("items") if isinstance(inbox_card.get("items"), list) else []
    for item in items:
        if isinstance(item, dict):
            _append_control_registry_items(
                registry,
                scope="inbox",
                card=card,
                agent_id=agent_id,
                controls=item.get("controls"),
            )


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
        item = {
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
        item["control_id"] = control_registry_item_id(item)
        registry.append(item)


LEADER_PROVIDER_SWITCHES: tuple[tuple[str, str, str], ...] = (
    ("fake", "fake-plan", "Use fake"),
    ("deepseek", "deepseek-chat", "Use DeepSeek"),
    ("openai-compatible", "openai-compatible-default", "Use OpenAI-compatible"),
    ("codex-cli", "codex-default", "Use Codex CLI"),
    ("claude-cli", "claude-default", "Use Claude CLI"),
)


def leader_provider_controls(current_provider: str) -> list[dict[str, object]]:
    controls: list[dict[str, object]] = []
    for provider, model, label in LEADER_PROVIDER_SWITCHES:
        enabled = provider != current_provider
        command = f"agentdeck leader set-provider --provider {provider} --model {model}"
        blocker = None if enabled else "already current provider"
        controls.append(
            {
                "kind": "set_provider",
                "label": label,
                "command": command,
                "safety": "explicit_user",
                "enabled": enabled,
                "blocker": blocker,
            }
        )
        controls.append(
            {
                "kind": "guarded_set_provider",
                "label": f"{label} if ready",
                "command": f"{command} --require-ready",
                "safety": "explicit_user",
                "enabled": enabled,
                "blocker": blocker,
            }
        )
        setup_label = label.replace("Use ", "Setup ", 1)
        for setup_command in leader_provider_setup_commands(provider):
            controls.append(
                {
                    "kind": "setup_provider",
                    "label": setup_label,
                    "command": setup_command,
                    "safety": "explicit_user",
                    "enabled": True,
                    "blocker": None,
                }
            )
    return controls


def leader_provider_setup_commands(provider: str) -> list[str]:
    if provider == "deepseek":
        return [
            'export DEEPSEEK_API_KEY="<your-deepseek-api-key>"',
            'export DEEPSEEK_BASE_URL="https://api.deepseek.com/v1"',
            'export DEEPSEEK_MODEL="deepseek-chat"',
        ]
    if provider == "openai-compatible":
        return [
            'export AGENTDECK_LEADER_API_KEY="<your-provider-api-key>"',
            'export AGENTDECK_LEADER_BASE_URL="https://api.example.com/v1"',
            'export AGENTDECK_LEADER_MODEL="<model-name>"',
        ]
    if provider == "codex-cli":
        return ["codex login", "codex doctor"]
    if provider == "claude-cli":
        return ["claude auth", "claude doctor"]
    return []


def leader_provider_setup_command_allowlist() -> set[str]:
    commands: set[str] = set()
    for provider, _model, _label in LEADER_PROVIDER_SWITCHES:
        commands.update(leader_provider_setup_commands(provider))
    return commands


def _agent_ready_card_from_runtime_card(runtime_card: dict[str, object]) -> dict[str, object]:
    agents = runtime_card.get("agents") if isinstance(runtime_card.get("agents"), list) else []
    running_count = 0
    spawn_commands: list[str] = []
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        if agent.get("status") == "running" and agent.get("pane_id"):
            running_count += 1
            continue
        spawn_command = agent.get("spawn_command")
        if spawn_command:
            spawn_commands.append(str(spawn_command))
    total_count = len(agents)
    not_running_count = total_count - running_count
    spawn_ready_command = "agentdeck agent spawn-ready --confirm"
    dispatch_ready_command = "agentdeck approval dispatch-ready --confirm"
    next_command = (
        spawn_ready_command
        if len(spawn_commands) > 1
        else spawn_commands[0]
        if spawn_commands
        else dispatch_ready_command
    )
    controls = _agent_ready_controls(
        next_command=next_command,
        spawn_commands=spawn_commands,
        spawn_ready_command=spawn_ready_command,
        refresh_command=str(runtime_card.get("refresh_command") or "agentdeck agent refresh"),
        dispatch_ready_command=dispatch_ready_command,
    )
    return {
        "ok": True,
        "mode": "agent_runtime_ready",
        "runtime_backend": runtime_card.get("backend"),
        "total_count": total_count,
        "running_count": running_count,
        "not_running_count": not_running_count,
        "all_running": not_running_count == 0,
        "next_command": next_command,
        "spawn_commands": spawn_commands,
        "spawn_ready_command": spawn_ready_command,
        "refresh_command": runtime_card.get("refresh_command"),
        "dispatch_ready_command": dispatch_ready_command,
        "controls": controls,
        "runtime_card": runtime_card,
    }


def _agent_ready_controls(
    *,
    next_command: str,
    spawn_commands: list[str],
    spawn_ready_command: str,
    refresh_command: str,
    dispatch_ready_command: str,
) -> list[dict[str, object]]:
    controls = [
        {
            "kind": "inspect",
            "label": "Inspect readiness",
            "command": "agentdeck agent ready",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        }
    ]
    if next_command == spawn_ready_command:
        controls.append(
            {
                "kind": "spawn_ready",
                "label": "Spawn ready agents",
                "command": spawn_ready_command,
                "safety": "explicit_runtime",
                "enabled": True,
                "blocker": None,
            }
        )
    elif next_command == dispatch_ready_command:
        controls.append(
            {
                "kind": "dispatch_ready",
                "label": "Dispatch ready approvals",
                "command": dispatch_ready_command,
                "safety": "explicit_runtime",
                "enabled": True,
                "blocker": None,
            }
        )
    elif spawn_commands:
        controls.append(
            {
                "kind": "spawn",
                "label": "Spawn agent",
                "command": spawn_commands[0],
                "safety": "explicit_runtime",
                "enabled": True,
                "blocker": None,
            }
        )
    controls.append(
        {
            "kind": "refresh_runtime",
            "label": "Refresh runtime",
            "command": refresh_command,
            "safety": "explicit_runtime",
            "enabled": True,
            "blocker": None,
        }
    )
    return controls


def _startup_preview_card_from_agent_ready(agent_ready_card: dict[str, object]) -> dict[str, object]:
    runtime_card = agent_ready_card.get("runtime_card") if isinstance(agent_ready_card.get("runtime_card"), dict) else {}
    agents = runtime_card.get("agents") if isinstance(runtime_card.get("agents"), list) else []
    items: list[dict[str, object]] = []
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        status = str(agent.get("status") or "unknown")
        pane_id = agent.get("pane_id")
        if status == "running" and pane_id:
            continue
        agent_id = str(agent.get("agent_id"))
        spawn_command = str(agent.get("spawn_command") or f"agentdeck agent spawn --agent {agent_id}")
        terminal_command = str(agent.get("terminal_command") or f"agentdeck agent terminal --agent {agent_id}")
        blocker = None if spawn_command else f"missing spawn command: {agent_id}"
        items.append(
            {
                "agent_id": agent_id,
                "role": agent.get("role"),
                "runtime_status": status,
                "pane_id": pane_id,
                "spawn_command": spawn_command,
                "terminal_command": terminal_command,
                "blocker": blocker,
                "controls": [
                    {
                        "kind": "inspect",
                        "label": "Inspect runtime",
                        "command": "agentdeck agent ready",
                        "safety": "inspect",
                        "enabled": True,
                        "blocker": None,
                    },
                    {
                        "kind": "spawn",
                        "label": f"Spawn {agent_id}",
                        "command": spawn_command,
                        "safety": "explicit_runtime",
                        "enabled": blocker is None,
                        "blocker": blocker,
                    },
                ],
            }
        )
    ready_count = sum(1 for item in items if item.get("blocker") is None)
    blocked_count = len(items) - ready_count
    spawn_ready_command = str(agent_ready_card.get("spawn_ready_command") or "agentdeck agent spawn-ready --confirm")
    blocker = None if ready_count else "no agents need startup"
    return {
        "mode": "startup_preview",
        "title": "Agent startup preview",
        "next_command": str(agent_ready_card.get("next_command") or spawn_ready_command),
        "spawn_ready_command": spawn_ready_command,
        "count": len(items),
        "ready_count": ready_count,
        "blocked_count": blocked_count,
        "requires_explicit_user": True,
        "safety": "explicit_runtime",
        "blocker": blocker,
        "items": items,
        "controls": [
            {
                "kind": "inspect",
                "label": "Inspect readiness",
                "command": "agentdeck agent ready",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "spawn_ready",
                "label": "Spawn ready agents",
                "command": spawn_ready_command,
                "safety": "explicit_runtime",
                "enabled": ready_count > 0,
                "blocker": blocker,
            },
        ],
    }


def _terminal_session_card_from_runtime_card(runtime_card: dict[str, object]) -> dict[str, object]:
    agents = runtime_card.get("agents") if isinstance(runtime_card.get("agents"), list) else []
    terminals: list[dict[str, object]] = []
    running_count = 0
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        pane_id = agent.get("pane_id")
        status = str(agent.get("status", "unknown"))
        enabled = status == "running" and bool(pane_id)
        if enabled:
            running_count += 1
        select_pane_command = f"tmux -L agentdeck-multi-agent-explore select-pane -t {pane_id}" if enabled else None
        blocker = None if enabled else "agent is not running"
        terminals.append(
            {
                "agent_id": agent.get("agent_id"),
                "role": agent.get("role"),
                "status": status,
                "pane_id": pane_id,
                "terminal_command": agent.get("terminal_command"),
                "select_pane_command": select_pane_command,
                "enabled": enabled,
                "blocker": blocker,
                "controls": [
                    {
                        "kind": "select_pane",
                        "label": "Select pane",
                        "command": select_pane_command,
                        "safety": "inspect",
                        "enabled": enabled,
                        "blocker": blocker,
                    }
                ],
            }
        )
    return {
        "mode": "terminal_session",
        "runtime_backend": runtime_card.get("backend"),
        "session_name": "agentdeck",
        "attach_command": "tmux -L agentdeck-multi-agent-explore attach -t agentdeck",
        "running_count": running_count,
        "agent_count": len(terminals),
        "open_terminals_command": "agentdeck controls",
        "refresh_command": runtime_card.get("refresh_command"),
        "controls": [
            {
                "kind": "attach_session",
                "label": "Attach session",
                "command": "tmux -L agentdeck-multi-agent-explore attach -t agentdeck",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "open_controls",
                "label": "Open terminal controls",
                "command": "agentdeck controls",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "refresh_runtime",
                "label": "Refresh runtime",
                "command": runtime_card.get("refresh_command"),
                "safety": "explicit_runtime",
                "enabled": True,
                "blocker": None,
            },
        ],
        "terminals": terminals,
    }


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
            "leader_backend": {
                "agent_id": "leader",
                "provider": "fake",
                "model": "fake-plan",
                "provider_backend": "local",
                "provider_transport": "local",
                "reasoning_backend": "local-fake",
                "runtime_kind": "logical_leader",
                "pane_backed": False,
                "pane_id": None,
                "approval_required": True,
                "dispatch_ready": False,
            },
            "coordination_roles": project_view["leader"]["coordination_roles"],
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
                    "kind": "refresh",
                    "label": "Refresh Leader status",
                    "command": "agentdeck leader status",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "leader_status",
                    "label": "Leader status",
                    "command": "agentdeck leader status",
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
        "mission_card": None,
        "provider_health": {
            "agent_id": "leader",
            "provider": "fake",
            "model": "fake-plan",
            "approval_mode": "confirm",
            "api_backed": False,
            "provider_backend": "local",
            "provider_transport": "local",
            "leader_backend": {
                "agent_id": "leader",
                "provider": "fake",
                "model": "fake-plan",
                "provider_backend": "local",
                "provider_transport": "local",
                "reasoning_backend": "local-fake",
                "runtime_kind": "logical_leader",
                "pane_backed": False,
                "pane_id": None,
                "approval_required": True,
                "dispatch_ready": False,
            },
            "supported": True,
            "ready": True,
            "missing_env": [],
            "detail": "fake provider is local and ready",
            "command_path": None,
            "doctor_command": "agentdeck doctor",
            "doctor_contract": "agentdeck contract doctor",
            "setup_commands": [],
            "controls": leader_provider_controls("fake"),
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
                    "terminal_command": "agentdeck agent terminal --agent planner",
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
                    "terminal_command": "agentdeck agent terminal --agent coder",
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
                    "terminal_command": "agentdeck agent terminal --agent reviewer",
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
                    "controls": role_agent_controls("planner"),
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
                    "controls": role_agent_controls("coder"),
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
                    "controls": role_agent_controls("reviewer"),
                },
            ],
        },
        "worker_lifecycle_card": {
            "mode": "worker_lifecycle",
            "title": "Worker lifecycle",
            "source_command": "agentdeck workbench",
            "count": 3,
            "by_stage": {"inbox_pending": 1, "idle": 2},
            "items": [
                {
                    "agent_id": "planner",
                    "role": "planner",
                    "provider": "codex",
                    "runtime_status": "running",
                    "pane_id": "%42",
                    "lifecycle_stage": "inbox_pending",
                    "active_message_id": "msg_example",
                    "active_job_id": "job_example",
                    "latest_reply_id": "rep_example",
                    "artifact_count": 1,
                    "pending_inbox_count": 1,
                    "trace_command": "agentdeck trace --id msg_example",
                    "inbox_command": "agentdeck inbox --agent planner",
                    "terminal_command": "agentdeck agent terminal --agent planner",
                    "capture_command": "agentdeck agent capture --agent planner --lines 200",
                    "controls": [
                        {
                            "kind": "trace",
                            "label": "Trace active task",
                            "command": "agentdeck trace --id msg_example",
                            "safety": "inspect",
                            "enabled": True,
                            "blocker": None,
                        },
                        {
                            "kind": "inbox",
                            "label": "Inspect inbox",
                            "command": "agentdeck inbox --agent planner",
                            "safety": "inspect",
                            "enabled": True,
                            "blocker": None,
                        },
                        {
                            "kind": "terminal",
                            "label": "Open terminal",
                            "command": "agentdeck agent terminal --agent planner",
                            "safety": "inspect",
                            "enabled": True,
                            "blocker": None,
                        },
                        {
                            "kind": "capture",
                            "label": "Capture pane output",
                            "command": "agentdeck agent capture --agent planner --lines 200",
                            "safety": "inspect",
                            "enabled": True,
                            "blocker": None,
                        },
                    ],
                },
                {
                    "agent_id": "coder",
                    "role": "coder",
                    "provider": "claude",
                    "runtime_status": "configured",
                    "pane_id": None,
                    "lifecycle_stage": "idle",
                    "active_message_id": None,
                    "active_job_id": None,
                    "latest_reply_id": None,
                    "artifact_count": 0,
                    "pending_inbox_count": 0,
                    "trace_command": None,
                    "inbox_command": "agentdeck inbox --agent coder",
                    "terminal_command": "agentdeck agent terminal --agent coder",
                    "capture_command": "agentdeck agent capture --agent coder --lines 200",
                    "controls": [
                        {
                            "kind": "trace",
                            "label": "Trace active task",
                            "command": None,
                            "safety": "inspect",
                            "enabled": False,
                            "blocker": "no active task",
                        },
                        {
                            "kind": "inbox",
                            "label": "Inspect inbox",
                            "command": "agentdeck inbox --agent coder",
                            "safety": "inspect",
                            "enabled": True,
                            "blocker": None,
                        },
                        {
                            "kind": "terminal",
                            "label": "Open terminal",
                            "command": "agentdeck agent terminal --agent coder",
                            "safety": "inspect",
                            "enabled": True,
                            "blocker": None,
                        },
                        {
                            "kind": "capture",
                            "label": "Capture pane output",
                            "command": "agentdeck agent capture --agent coder --lines 200",
                            "safety": "inspect",
                            "enabled": False,
                            "blocker": "agent is not running",
                        },
                    ],
                },
                {
                    "agent_id": "reviewer",
                    "role": "reviewer",
                    "provider": "codex",
                    "runtime_status": "configured",
                    "pane_id": None,
                    "lifecycle_stage": "idle",
                    "active_message_id": None,
                    "active_job_id": None,
                    "latest_reply_id": None,
                    "artifact_count": 0,
                    "pending_inbox_count": 0,
                    "trace_command": None,
                    "inbox_command": "agentdeck inbox --agent reviewer",
                    "terminal_command": "agentdeck agent terminal --agent reviewer",
                    "capture_command": "agentdeck agent capture --agent reviewer --lines 200",
                    "controls": [
                        {
                            "kind": "trace",
                            "label": "Trace active task",
                            "command": None,
                            "safety": "inspect",
                            "enabled": False,
                            "blocker": "no active task",
                        },
                        {
                            "kind": "inbox",
                            "label": "Inspect inbox",
                            "command": "agentdeck inbox --agent reviewer",
                            "safety": "inspect",
                            "enabled": True,
                            "blocker": None,
                        },
                        {
                            "kind": "terminal",
                            "label": "Open terminal",
                            "command": "agentdeck agent terminal --agent reviewer",
                            "safety": "inspect",
                            "enabled": True,
                            "blocker": None,
                        },
                        {
                            "kind": "capture",
                            "label": "Capture pane output",
                            "command": "agentdeck agent capture --agent reviewer --lines 200",
                            "safety": "inspect",
                            "enabled": False,
                            "blocker": "agent is not running",
                        },
                    ],
                },
            ],
            "controls": [
                {
                    "kind": "inspect",
                    "label": "Refresh worker lifecycle",
                    "command": "agentdeck workbench",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                }
            ],
        },
        "review_gate_card": {
            "mode": "review_gate",
            "title": "Review gate",
            "source_command": "agentdeck workbench",
            "status": "blocked",
            "reason": "round_reviewer is not configured",
            "can_release": False,
            "artifact_count": 1,
            "review_reply_count": 1,
            "code_review": {
                "stage": "code_review",
                "agent_id": "reviewer",
                "role": "reviewer",
                "status": "ready",
                "latest_reply_id": "rep_review",
                "trace_command": "agentdeck trace --id rep_review",
                "inbox_command": "agentdeck inbox --agent reviewer",
                "blocker": None,
                "controls": [
                    {
                        "kind": "trace",
                        "label": "Trace review",
                        "command": "agentdeck trace --id rep_review",
                        "safety": "inspect",
                        "enabled": True,
                        "blocker": None,
                    },
                    {
                        "kind": "inbox",
                        "label": "Inspect reviewer inbox",
                        "command": "agentdeck inbox --agent reviewer",
                        "safety": "inspect",
                        "enabled": True,
                        "blocker": None,
                    },
                ],
            },
            "round_review": {
                "stage": "round_review",
                "agent_id": None,
                "role": None,
                "status": "missing_reviewer",
                "latest_reply_id": None,
                "trace_command": None,
                "inbox_command": None,
                "blocker": "round_reviewer is not configured",
                "controls": [
                    {
                        "kind": "trace",
                        "label": "Trace review",
                        "command": None,
                        "safety": "inspect",
                        "enabled": False,
                        "blocker": "round_reviewer is not configured",
                    },
                    {
                        "kind": "inbox",
                        "label": "Inspect reviewer inbox",
                        "command": None,
                        "safety": "inspect",
                        "enabled": False,
                        "blocker": "round_reviewer is not configured",
                    },
                ],
            },
            "controls": [
                {
                    "kind": "inspect",
                    "label": "Inspect review gate",
                    "command": "agentdeck workbench",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "assign_code_reviewer",
                    "label": "Assign code reviewer",
                    "command": (
                        "agentdeck agent assign-role --agent <agent_id> --role code_reviewer "
                        "--role-prompt <role_prompt>"
                    ),
                    "safety": "explicit_user",
                    "enabled": False,
                    "blocker": "requires agent_id and role_prompt",
                },
                {
                    "kind": "assign_round_reviewer",
                    "label": "Assign round reviewer",
                    "command": (
                        "agentdeck agent assign-role --agent <agent_id> --role round_reviewer "
                        "--role-prompt <role_prompt>"
                    ),
                    "safety": "explicit_user",
                    "enabled": False,
                    "blocker": "requires agent_id and role_prompt",
                },
            ],
        },
        "release_preview_card": {
            "mode": "release_preview",
            "title": "Release / next-round preview",
            "source_command": "agentdeck workbench",
            "status": "blocked",
            "reason": "round_reviewer is not configured",
            "review_gate_status": "blocked",
            "can_release": False,
            "already_released": False,
            "release_count": 1,
            "latest_release_id": "rel_example",
            "next_command": None,
            "release_command": None,
            "next_round_command": None,
            "controls": [
                {
                    "kind": "inspect_review_gate",
                    "label": "Inspect review gate",
                    "command": "agentdeck workbench",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "release_preview",
                    "label": "Preview release",
                    "command": None,
                    "safety": "explicit_user",
                    "enabled": False,
                    "blocker": "round_reviewer is not configured",
                },
                {
                    "kind": "next_round_preview",
                    "label": "Preview next round",
                    "command": None,
                    "safety": "explicit_user",
                    "enabled": False,
                    "blocker": "round_reviewer is not configured",
                },
            ],
        },
        "role_topology_card": {
            "mode": "role_topology",
            "title": "Role topology",
            "source_command": "agentdeck workbench",
            "count": 6,
            "logical_role_count": 3,
            "worker_role_count": 3,
            "by_status": {
                "ready": 1,
                "planning": 1,
                "coordinating": 1,
                "inbox_pending": 1,
                "idle": 1,
                "reviewed": 1,
            },
            "blocked_count": 0,
            "roles": [
                {
                    "role_id": "frontdesk",
                    "label": "Frontdesk intake",
                    "agent_id": None,
                    "kind": "logical_role",
                    "provider": "local-rule",
                    "lifecycle": "persistent",
                    "runtime_kind": "logical_role",
                    "pane_backed": False,
                    "pane_id": None,
                    "status": "ready",
                    "blocker": None,
                    "next_command": "agentdeck leader chat-history",
                    "controls": [
                        {
                            "kind": "inspect",
                            "label": "Inspect intake history",
                            "command": "agentdeck leader chat-history",
                            "safety": "inspect",
                            "enabled": True,
                            "blocker": None,
                        }
                    ],
                },
                {
                    "role_id": "planner",
                    "label": "Planner",
                    "agent_id": None,
                    "kind": "logical_role",
                    "provider": "fake",
                    "lifecycle": "persistent",
                    "runtime_kind": "logical_role",
                    "pane_backed": False,
                    "pane_id": None,
                    "status": "planning",
                    "blocker": None,
                    "next_command": "agentdeck plan list",
                    "controls": [
                        {
                            "kind": "inspect",
                            "label": "Inspect plans",
                            "command": "agentdeck plan list",
                            "safety": "inspect",
                            "enabled": True,
                            "blocker": None,
                        }
                    ],
                },
                {
                    "role_id": "orchestrator",
                    "label": "Orchestrator",
                    "agent_id": None,
                    "kind": "logical_role",
                    "provider": "fake",
                    "lifecycle": "persistent",
                    "runtime_kind": "logical_role",
                    "pane_backed": False,
                    "pane_id": None,
                    "status": "coordinating",
                    "blocker": None,
                    "next_command": "agentdeck leader actions",
                    "controls": [
                        {
                            "kind": "inspect",
                            "label": "Inspect Leader actions",
                            "command": "agentdeck leader actions",
                            "safety": "inspect",
                            "enabled": True,
                            "blocker": None,
                        }
                    ],
                },
                {
                    "role_id": "planner",
                    "label": "planner",
                    "agent_id": "planner",
                    "kind": "worker",
                    "provider": "codex",
                    "lifecycle": "running",
                    "runtime_kind": "worker_pane",
                    "pane_backed": True,
                    "pane_id": "%42",
                    "status": "inbox_pending",
                    "blocker": None,
                    "next_command": "agentdeck inbox --agent planner",
                    "controls": [
                        {
                            "kind": "inspect",
                            "label": "Inspect mailbox",
                            "command": "agentdeck inbox --agent planner",
                            "safety": "inspect",
                            "enabled": True,
                            "blocker": None,
                        }
                    ],
                },
                {
                    "role_id": "coder",
                    "label": "coder",
                    "agent_id": "coder",
                    "kind": "worker",
                    "provider": "claude",
                    "lifecycle": "configured",
                    "runtime_kind": "worker_pane",
                    "pane_backed": False,
                    "pane_id": None,
                    "status": "idle",
                    "blocker": None,
                    "next_command": "agentdeck inbox --agent coder",
                    "controls": [
                        {
                            "kind": "inspect",
                            "label": "Inspect mailbox",
                            "command": "agentdeck inbox --agent coder",
                            "safety": "inspect",
                            "enabled": True,
                            "blocker": None,
                        }
                    ],
                },
                {
                    "role_id": "reviewer",
                    "label": "reviewer",
                    "agent_id": "reviewer",
                    "kind": "worker",
                    "provider": "codex",
                    "lifecycle": "configured",
                    "runtime_kind": "worker_pane",
                    "pane_backed": False,
                    "pane_id": None,
                    "status": "reviewed",
                    "blocker": None,
                    "next_command": "agentdeck inbox --agent reviewer",
                    "controls": [
                        {
                            "kind": "inspect",
                            "label": "Inspect mailbox",
                            "command": "agentdeck inbox --agent reviewer",
                            "safety": "inspect",
                            "enabled": True,
                            "blocker": None,
                        }
                    ],
                },
            ],
            "controls": [
                {
                    "kind": "inspect",
                    "label": "Inspect role topology",
                    "command": "agentdeck workbench",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                }
            ],
        },
        "ledger_card": {
            "messages": project_view["messages"],
            "jobs": project_view["jobs"],
            "replies": project_view["replies"],
            "artifacts": project_view["artifacts"],
            "inbox": project_view["inbox"],
            "trace_commands": [
                "agentdeck trace --id msg_example",
                "agentdeck trace --id job_example",
                "agentdeck trace --id rep_example",
            ],
            "controls": ledger_card_controls(),
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
            "controls": [
                {
                    "kind": "inspect",
                    "label": "Inspect audit events",
                    "command": "agentdeck events --limit 20",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                }
            ],
        },
        "artifacts_card": artifacts_example(),
        "skill_context_card": {
            "mode": "skill_context",
            "title": "Loaded skill context",
            "summary": "1 loaded skill is available as replayable context.",
            "skills_command": "agentdeck skills list",
            "project_view_command": "agentdeck status",
            "count": 1,
            "items": project_view["skills"]["items"],
            "controls": [
                {
                    "kind": "inspect",
                    "label": "List skills",
                    "command": "agentdeck skills list",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "inspect",
                    "label": "Open project status",
                    "command": "agentdeck status",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
            ],
        },
        "skill_suggestions_card": {
            "mode": "skill_suggestions",
            "title": "Skill suggestions",
            "summary": "1 pending skill suggestion is waiting for human review.",
            "suggestions_command": "agentdeck skills suggestions",
            "project_view_command": "agentdeck status",
            "count": 1,
            "pending_count": 1,
            "items": [
                {
                    "suggestion_id": "sgs_example",
                    "status": "pending",
                    "name": "incident-review",
                    "summary": "Review incident response evidence.",
                    "rationale": "planner repeatedly asked for the same incident review checklist",
                    "source": "leader",
                    "agent_id": "reviewer",
                    "trace_id": "msg_example",
                    "draft_path": ".agentdeck/skills/incident-review/SKILL.md",
                    "created_at": "2026-07-07T00:00:00Z",
                    "controls": [
                        {
                            "kind": "inspect",
                            "label": "List skill suggestions",
                            "command": "agentdeck skills suggestions",
                            "safety": "inspect",
                            "enabled": True,
                            "blocker": None,
                        }
                    ],
                }
            ],
            "controls": [
                {
                    "kind": "inspect",
                    "label": "List skill suggestions",
                    "command": "agentdeck skills suggestions",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "inspect",
                    "label": "Open project status",
                    "command": "agentdeck status",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
            ],
        },
        "memory_context_card": {
            "mode": "memory_context",
            "title": "Long-term memory context",
            "summary": "1 memory file is available for human review.",
            "project_view_command": "agentdeck status",
            "suggestions_command": "agentdeck memory suggestions",
            "count": 1,
            "items": project_view["memory"]["items"],
            "controls": [
                {
                    "kind": "inspect",
                    "label": "Open project status",
                    "command": "agentdeck status",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "inspect",
                    "label": "List memory suggestions",
                    "command": "agentdeck memory suggestions",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
            ],
        },
        "memory_suggestions_card": {
            "mode": "memory_suggestions",
            "title": "Memory suggestions",
            "summary": "1 pending memory suggestion is waiting for human review.",
            "suggestions_command": "agentdeck memory suggestions",
            "apply_preview_command_template": "agentdeck memory apply-preview --suggestion-id <id>",
            "project_view_command": "agentdeck status",
            "count": 1,
            "pending_count": 1,
            "items": [
                {
                    "suggestion_id": "mem_example",
                    "status": "pending",
                    "scope": "project",
                    "summary": "Keep approval-gated worker dispatch.",
                    "rationale": "project safety preference",
                    "source": "reviewer",
                    "agent_id": "leader",
                    "trace_id": "msg_memory",
                    "target": ".agentdeck/memory/project.md",
                    "created_at": "2026-07-07T00:00:00Z",
                    "controls": [
                        {
                            "kind": "inspect",
                            "label": "List memory suggestions",
                            "command": "agentdeck memory suggestions",
                            "safety": "inspect",
                            "enabled": True,
                            "blocker": None,
                        },
                        {
                            "kind": "apply_preview",
                            "label": "Preview memory apply",
                            "command": "agentdeck memory apply-preview --suggestion-id mem_example",
                            "safety": "inspect",
                            "enabled": True,
                            "blocker": None,
                        },
                        {
                            "kind": "apply_memory",
                            "label": "Apply memory suggestion",
                            "command": "agentdeck memory apply --suggestion-id mem_example --confirm",
                            "safety": "explicit_user",
                            "enabled": True,
                            "blocker": None,
                        }
                    ],
                }
            ],
            "controls": [
                {
                    "kind": "inspect",
                    "label": "List memory suggestions",
                    "command": "agentdeck memory suggestions",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "apply_preview",
                    "label": "Preview memory apply",
                    "command": "agentdeck memory apply-preview --suggestion-id <id>",
                    "safety": "inspect",
                    "enabled": False,
                    "blocker": "requires suggestion id",
                },
                {
                    "kind": "inspect",
                    "label": "Open project status",
                    "command": "agentdeck status",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
            ],
        },
        "leader_summary_card": leader_summary_example(),
        "learning_review_card": learning_review_example(),
        "contracts_card": {
            "contracts_command": "agentdeck contract list",
            "contract_index_contract": "docs/contracts/contract-index-schema.md",
            "workbench_contract": "agentdeck contract workbench",
            "controls_contract": "agentdeck contract controls",
            "skills_contract": "agentdeck contract skills",
            "memory_contract": "agentdeck contract memory",
            "learning_review_contract": "agentdeck contract learning-review",
            "agent_runtime_contract": "agentdeck contract agent-runtime",
            "leader_chat_contract": "agentdeck contract leader-chat",
            "leader_review_contract": "agentdeck contract leader-review",
            "leader_summary_contract": "agentdeck contract leader-summary",
            "project_view_contract": "agentdeck contract project-view",
            "events_contract": "agentdeck contract events",
            "doctor_contract": "agentdeck contract doctor",
            "run_contract": "agentdeck contract run",
            "artifacts_contract": "agentdeck contract artifacts",
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
                    "description": "Scoped delegation: auto-approve allowlisted pending approvals within a count budget, fully audited.",
                    "enabled": True,
                    "requires_explicit_user": True,
                    "safety": "delegated",
                    "blocker": None,
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
                    "command": "agentdeck policy set-mode --mode autonomous --confirm --allow-agent <id> --max-approvals <N>",
                    "safety": "delegated",
                    "enabled": False,
                    "blocker": "requires --allow-agent and --max-approvals",
                },
            ],
            "autonomous_actions": [
                {
                    "kind": "approval_auto",
                    "label": "Auto-approve (autonomous)",
                    "command": "agentdeck approval auto --confirm",
                    "safety": "delegated",
                    "enabled": False,
                    "blocker": "autonomous mode is not enabled",
                },
                {
                    "kind": "run_loop",
                    "label": "Run-loop (autonomous)",
                    "command": "agentdeck run-loop --plan-id <id> --confirm",
                    "safety": "delegated",
                    "enabled": False,
                    "blocker": "requires --plan-id",
                },
            ],
            "set_mode_command_template": "agentdeck policy set-mode --mode <mode>",
            "policy_source": ".agentdeck/config.toml:leader.approval_mode",
        },
        "recovery": recovery,
        "next_command": recovery["next_command"],
        "continue_card": continue_example(),
        "active_queue_source": "leader_action",
        "run_progress_card": run_progress_example(),
        "plan_board_card": plan_board_example(),
        "skills_catalog_card": {
            "mode": "skills_catalog",
            "source_count": 0,
            "total_skill_count": 0,
            "imported_count": 0,
            "sources": [],
        },
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
    status_example = mission_example("status")
    mission_summary = project_view["missions"]
    latest_item = mission_summary["items"][0]
    for field in (
        "mission_id", "schema_version", "user_message", "status", "stop_reason",
        "blockers", "plan_id", "plan_hash", "workflow_run_id", "current_step",
        "step_count", "timeout_seconds", "selected_agents", "created_at", "updated_at",
        "confirmed_at", "completed_at", "can_resume", "status_command", "resume_command",
    ):
        latest_item[field] = deepcopy(status_example[field])
    latest_item["confirmation_command"] = mission_commands(str(latest_item["mission_id"]))["confirmation_command"]
    latest_item["can_start"] = False
    mission_summary["latest_id"] = latest_item["mission_id"]
    mission_summary["by_status"] = {str(latest_item["status"]): 1}
    mission_card = workbench_mission_card(latest_item, "agentdeck")
    payload["mission_card"] = mission_card
    payload["agent_ready_card"] = _agent_ready_card_from_runtime_card(deepcopy(payload["runtime_card"]))
    payload["terminal_session_card"] = _terminal_session_card_from_runtime_card(payload["runtime_card"])
    payload = {field: payload[field] for field in WORKBENCH_SNAPSHOT_FIELDS}
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
        "terminal": {
            "ok": True,
            "mode": "agent_terminal",
            "agent_id": agent_id,
            "role": "planning",
            "provider": "codex",
            "workspace_mode": "shared",
            "status": "running",
            "pane_id": "%42",
            "session_name": "agentdeck",
            "cwd": "/workspace/project",
            "attach_command": "tmux -L agentdeck-multi-agent-explore attach -t agentdeck",
            "select_pane_command": "tmux -L agentdeck-multi-agent-explore select-pane -t %42",
            "capture_command": "agentdeck agent capture --agent planner --lines 200",
            "send_command_template": "agentdeck agent send --agent planner --text <text>",
            "stop_command": "agentdeck agent stop --agent planner",
            "inbox_command": "agentdeck inbox --agent planner",
            "refresh_command": "agentdeck agent refresh",
            "controls": terminal_card_controls(
                attach_command="tmux -L agentdeck-multi-agent-explore attach -t agentdeck",
                select_pane_command="tmux -L agentdeck-multi-agent-explore select-pane -t %42",
                capture_command="agentdeck agent capture --agent planner --lines 200",
                send_command_template="agentdeck agent send --agent planner --text <text>",
                stop_command="agentdeck agent stop --agent planner",
                inbox_command="agentdeck inbox --agent planner",
            ),
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
        "ready": {
            "ok": True,
            "mode": "agent_runtime_ready",
            "runtime_backend": "tmux",
            "total_count": 1,
            "running_count": 1,
            "not_running_count": 0,
            "all_running": True,
            "next_command": "agentdeck approval dispatch-ready --confirm",
            "spawn_commands": [],
            "spawn_ready_command": "agentdeck agent spawn-ready --confirm",
            "refresh_command": "agentdeck agent refresh",
            "dispatch_ready_command": "agentdeck approval dispatch-ready --confirm",
            "controls": [
                {
                    "kind": "inspect",
                    "label": "Inspect readiness",
                    "command": "agentdeck agent ready",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "dispatch_ready",
                    "label": "Dispatch ready approvals",
                    "command": "agentdeck approval dispatch-ready --confirm",
                    "safety": "explicit_runtime",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "refresh_runtime",
                    "label": "Refresh runtime",
                    "command": "agentdeck agent refresh",
                    "safety": "explicit_runtime",
                    "enabled": True,
                    "blocker": None,
                },
            ],
            "runtime_card": {
                "backend": "tmux",
                "count": 1,
                "by_status": {"running": 1},
                "refresh_command": "agentdeck agent refresh",
                "agents": [
                    {
                        "agent_id": agent_id,
                        "role": "planning",
                        "provider": "codex",
                        "workspace_mode": "shared",
                        "status": "running",
                        "pane_id": "%42",
                        "session_name": "agentdeck",
                        "cwd": "/workspace/project",
                        "spawn_command": "agentdeck agent spawn --agent planner",
                        "stop_command": "agentdeck agent stop --agent planner",
                        "terminal_command": "agentdeck agent terminal --agent planner",
                        "capture_command": "agentdeck agent capture --agent planner --lines 200",
                        "send_command_template": "agentdeck agent send --agent planner --text <text>",
                        "inbox_command": "agentdeck inbox --agent planner",
                        "controls": runtime_agent_controls(agent_id, True),
                    }
                ],
            },
        },
        "spawn_ready": {
            "ok": True,
            "mode": "agent_spawn_ready",
            "requires_explicit_user": True,
            "safety": "explicit_runtime",
            "spawned_count": 1,
            "skipped_count": 1,
            "results": [
                {
                    "agent_id": "planner",
                    "status": "spawned",
                    "previous_status": "configured",
                    "pane_id": "%42",
                    "spawn_command": "agentdeck agent spawn --agent planner",
                    "blocker": None,
                },
                {
                    "agent_id": "reviewer",
                    "status": "skipped",
                    "previous_status": "running",
                    "pane_id": "%43",
                    "spawn_command": "agentdeck agent spawn --agent reviewer",
                    "blocker": "agent already running",
                },
            ],
            "ready_command": "agentdeck agent ready",
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


def loop_once_example() -> dict[str, object]:
    continue_card = continue_example()
    recovery = project_view_example()["recovery"]
    next_command = continue_card["next_command"]
    return {
        "ok": True,
        "mode": "loop_once",
        "loop_id": "run_once",
        "iteration": 1,
        "max_iterations": 1,
        "source_command": "agentdeck loop once",
        "project_view_command": "agentdeck status",
        "continue_command": "agentdeck continue",
        "workbench_command": "agentdeck workbench",
        "status": continue_card["status"],
        "reason": continue_card["reason"],
        "recovery": recovery,
        "continue_card": continue_card,
        "recommended_action": continue_card["recommended_action"],
        "next_command": next_command,
        "stop_reason": "requires_human_command",
        "will_execute": False,
        "requires_explicit_user": True,
        "safety": continue_card["recommended_action"]["safety"],
        "controls": [
            {
                "kind": "inspect",
                "label": "Inspect project status",
                "command": "agentdeck status",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "inspect",
                "label": "Inspect continue card",
                "command": "agentdeck continue",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "execute_next",
                "label": "Run explicit next command",
                "command": next_command,
                "safety": continue_card["recommended_action"]["safety"],
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "inspect",
                "label": "Open workbench",
                "command": "agentdeck workbench",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
        ],
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


def approval_dispatch_ready_example() -> dict[str, object]:
    return {
        "ok": True,
        "mode": "dispatch_ready",
        "requires_explicit_user": True,
        "safety": "explicit_runtime",
        "dispatched_count": 1,
        "blocked_count": 1,
        "skipped_count": 1,
        "results": [
            {
                "approval_id": "apv_ready",
                "status": "dispatched",
                "agent_id": "planner",
                "pane_id": "%42",
                "message_id": "msg_ready",
                "trace_command": "agentdeck trace --id msg_ready",
                "blocker": None,
                "dispatch_command": "agentdeck approval dispatch --approval-id apv_ready",
            },
            {
                "approval_id": "apv_blocked",
                "status": "blocked",
                "agent_id": "coder",
                "pane_id": None,
                "message_id": None,
                "trace_command": None,
                "blocker": "agent is not spawned: coder",
                "dispatch_command": "agentdeck approval dispatch --approval-id apv_blocked",
            },
        ],
    }


def run_start_example() -> dict[str, object]:
    plan_id = "pln_example"
    approval_card = approval_example()
    first_approval_id = approval_card["approvals"][0]["approval_id"]
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "ok": True,
        "mode": "run_start",
        "task": "Build an approval-gated multi-agent smoke test",
        "plan_id": plan_id,
        "provider": "fake",
        "provider_backend": "local",
        "provider_transport": "local",
        "leader_backend": {
            "agent_id": "leader",
            "provider": "fake",
            "model": "fake-plan",
            "provider_backend": "local",
            "provider_transport": "local",
            "reasoning_backend": "local-fake",
            "runtime_kind": "logical_leader",
            "pane_backed": False,
            "pane_id": None,
            "approval_required": True,
            "dispatch_ready": False,
        },
        "model": "fake-plan",
        "approval_count": approval_card["count"],
        "pending_approval_count": 1,
        "plan": {
            "goal": "Build an approval-gated multi-agent smoke test",
            "summary": "Plan generated by the Leader provider.",
            "steps": [
                {
                    "step": 1,
                    "agent_id": "planner",
                    "role": "planning",
                    "task": "Plan the approved smoke test.",
                    "risk": "requires human review before dispatch",
                    "requires_approval": True,
                }
            ],
            "approval_required": True,
            "dispatch_ready": False,
        },
        "approval_card": approval_card,
        "next_command": "agentdeck approval list",
        "approve_next_command": f"agentdeck approval approve --approval-id {first_approval_id}",
        "review_command": f"agentdeck leader review --plan-id {plan_id}",
        "continue_command": "agentdeck continue",
        "workbench_command": "agentdeck workbench",
        "controls": [
            {
                "kind": "preview",
                "label": "Review approval queue",
                "command": "agentdeck approval list",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "approve",
                "label": "Approve next step",
                "command": f"agentdeck approval approve --approval-id {first_approval_id}",
                "safety": "explicit_runtime",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "review",
                "label": "Review run",
                "command": f"agentdeck leader review --plan-id {plan_id}",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
        ],
        "safety": "approval_gated",
        "requires_explicit_user": True,
    }


def run_progress_example() -> dict[str, object]:
    plan_id = "pln_example"
    approval_card = approval_example()
    approval_id = approval_card["approvals"][1]["approval_id"]
    next_command = f"agentdeck approval dispatch --approval-id {approval_id}"
    leader_backend = {
        "agent_id": "leader",
        "provider": "fake",
        "model": "fake-plan",
        "provider_backend": "local",
        "provider_transport": "local",
        "reasoning_backend": "local-fake",
        "runtime_kind": "logical_leader",
        "pane_backed": False,
        "pane_id": None,
        "approval_required": True,
        "dispatch_ready": False,
    }
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "ok": True,
        "mode": "run_progress",
        "plan_id": plan_id,
        "task": "Build an approval-gated multi-agent smoke test",
        "status": "planned",
        "provider": "fake",
        "provider_backend": "local",
        "provider_transport": "local",
        "leader_backend": dict(leader_backend),
        "model": "fake-plan",
        "counts": {
            "steps": 2,
            "approvals": 2,
            "pending": 1,
            "approved": 1,
            "rejected": 0,
            "dispatched": 0,
        },
        "steps": [
            {
                "step": 1,
                "agent_id": "planner",
                "role": "planning",
                "task": "Plan the approved smoke test.",
                "approval_id": "apv_pending",
                "approval_status": "pending",
                "message_id": None,
                "attempt_id": None,
                "job_id": None,
            }
        ],
        "review": {
            "plan_id": plan_id,
            "next_action": "dispatch_approved",
            "reason": "approved step is waiting for dispatch",
            "approval_id": approval_id,
            "agent_id": "coder",
            "message_id": None,
            "replies": [],
            "leader_backend": dict(leader_backend),
            "next_command": next_command,
            "controls": [
                {
                    "kind": "next",
                    "label": "Next command",
                    "command": next_command,
                    "safety": "explicit_runtime",
                    "enabled": True,
                    "blocker": None,
                }
            ],
        },
        "approval_card": approval_card,
        "next_command": next_command,
        "plan_status_command": f"agentdeck plan status --plan-id {plan_id}",
        "review_command": f"agentdeck leader review --plan-id {plan_id}",
        "continue_command": "agentdeck continue",
        "workbench_command": "agentdeck workbench",
        "controls": [
            {
                "kind": "plan_status",
                "label": "Plan status",
                "command": f"agentdeck plan status --plan-id {plan_id}",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "review",
                "label": "Review run",
                "command": f"agentdeck leader review --plan-id {plan_id}",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "approval_queue",
                "label": "Review approval queue",
                "command": "agentdeck approval list",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "next",
                "label": "Next command",
                "command": next_command,
                "safety": "explicit_runtime",
                "enabled": True,
                "blocker": None,
            },
        ],
        "safety": "approval_gated",
        "requires_explicit_user": True,
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
        "leader_backend": {
            "agent_id": "leader",
            "provider": "fake",
            "model": "fake-plan",
            "provider_backend": "local",
            "provider_transport": "local",
            "reasoning_backend": "local-fake",
            "runtime_kind": "logical_leader",
            "pane_backed": False,
            "pane_id": None,
            "approval_required": True,
            "dispatch_ready": False,
        },
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


def leader_summary_example() -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "plan_id": "pln_example",
        "task": "Build a GUI-ready recovery panel",
        "status": "ready",
        "provider": "fake",
        "model": "fake-plan",
        "leader_backend": {
            "agent_id": "leader",
            "provider": "fake",
            "model": "fake-plan",
            "provider_backend": "local",
            "provider_transport": "local",
            "reasoning_backend": "local-fake",
            "runtime_kind": "logical_leader",
            "pane_backed": False,
            "pane_id": None,
            "approval_required": True,
            "dispatch_ready": False,
        },
        "counts": {
            "steps": 1,
            "approvals": 1,
            "pending": 0,
            "approved": 0,
            "rejected": 0,
            "dispatched": 1,
        },
        "reply_count": 1,
        "artifact_count": 1,
        "summary": "1 dispatched step has replies; 1 artifact recorded.",
        "plan_status_command": "agentdeck plan status --plan-id pln_example",
        "review_command": "agentdeck leader review --plan-id pln_example",
        "steps": [
            {
                "step": 1,
                "agent_id": "planner",
                "role": "Planner",
                "task": "Draft the recovery panel plan.",
                "approval_id": "apv_example",
                "message_id": "msg_example",
                "attempt_id": "att_example",
                "job_id": "job_example",
                "reply_id": "rep_example",
                "reply_text": "status: completed\nsummary: planner delivered.",
                "artifact_count": 1,
                "artifacts": [
                    {
                        "artifact_id": "art_example",
                        "path": "docs/recovery-panel.md",
                        "kind": "markdown",
                        "status": "ready",
                        "trace_command": "agentdeck trace --id art_example",
                    }
                ],
                "trace_command": "agentdeck trace --id msg_example",
            }
        ],
        "controls": [
            {
                "kind": "plan_status",
                "label": "Plan status",
                "command": "agentdeck plan status --plan-id pln_example",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "review",
                "label": "Review plan",
                "command": "agentdeck leader review --plan-id pln_example",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "trace",
                "label": "Trace step",
                "command": "agentdeck trace --id msg_example",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
        ],
    }


def leader_status_example() -> dict[str, object]:
    project_view = project_view_example()
    recovery = project_view["recovery"]
    return {
        "ok": True,
        "mode": "leader_status",
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "source_command": "agentdeck leader status",
        "refresh_command": "agentdeck leader status",
        "project_view_command": "agentdeck status",
        "workbench_command": "agentdeck workbench",
        "leader": project_view["leader"],
        "provider_health": workbench_example()["provider_health"],
        "coordination_roles": project_view["leader"]["coordination_roles"],
        "latest_plan": project_view["plans"]["items"][0],
        "queues": {
            "leader_actions_pending": 1,
            "approvals_pending": 0,
            "approvals_approved": 0,
            "leader_inbox_pending": 0,
            "leader_errors": 0,
        },
        "recovery": recovery,
        "next_command": recovery["next_command"],
        "controls": [
            {
                "kind": "refresh",
                "label": "Refresh Leader status",
                "command": "agentdeck leader status",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "inspect",
                "label": "Open project status",
                "command": "agentdeck status",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "inspect",
                "label": "Open workbench",
                "command": "agentdeck workbench",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "inspect",
                "label": "Inspect provider setup",
                "command": "agentdeck doctor",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "next",
                "label": "Continue",
                "command": recovery["next_command"],
                "safety": recovery["recommended_action"]["safety"],
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
            "prompt_skill_context": {
                "count": 1,
                "by_agent": {"planner": 1},
                "by_source": {"builtin": 1},
                "items": [
                    {
                        "load_id": "skl_worker_example",
                        "agent_id": "planner",
                        "purpose": "review implementation plan",
                        "name": "code-review",
                        "source": "builtin",
                        "path": "builtin://code-review/SKILL.md",
                        "content_hash": "sha256:worker-example",
                        "description": "Review changes for bugs, regressions, risks, and missing tests.",
                        "required_tools": ["git", "pytest"],
                        "risk": "inspect",
                        "created_at": "2026-07-04T00:00:00+00:00",
                        "show_command": "agentdeck skills show --name code-review",
                        "reload_command": (
                            "agentdeck skills load --name code-review --agent planner "
                            "--purpose 'review implementation plan'"
                        ),
                    }
                ],
            },
            "status": "replied",
            "created_at": "2026-07-04T00:00:00+00:00",
        },
        "plan": {
            "plan_id": "pln_example",
            "task": "Build a GUI-ready recovery panel",
            "provider": "fake",
            "provider_backend": "local",
            "provider_transport": "local",
            "leader_backend": {
                "agent_id": "leader",
                "provider": "fake",
                "model": "fake-plan",
                "provider_backend": "local",
                "provider_transport": "local",
                "reasoning_backend": "local-fake",
                "runtime_kind": "logical_leader",
                "pane_backed": False,
                "pane_id": None,
                "approval_required": True,
                "dispatch_ready": False,
            },
            "model": "fake-plan",
            "status": "planned",
            "dispatch_ready": False,
            "skill_context": {
                "count": 1,
                "by_agent": {"leader": 1},
                "by_source": {"builtin": 1},
                "items": [
                    {
                        "load_id": "skl_example",
                        "agent_id": "leader",
                        "purpose": "decompose task",
                        "name": "planning",
                        "source": "builtin",
                        "path": "builtin://planning/SKILL.md",
                        "content_hash": "sha256:example",
                        "description": "Break broad goals into reviewable steps.",
                        "required_tools": [],
                        "risk": "inspect",
                        "created_at": "2026-07-04T00:00:00+00:00",
                        "show_command": "agentdeck skills show --name planning",
                        "reload_command": (
                            "agentdeck skills load --name planning --agent leader --purpose 'decompose task'"
                        ),
                    }
                ],
            },
            "step_count": 1,
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
        "artifacts": [
            {
                "artifact_id": "art_example",
                "message_id": "msg_example",
                "attempt_id": "att_example",
                "job_id": "job_example",
                "reply_id": "rep_example",
                "from_agent": "planner",
                "path": "docs/example-plan.md",
                "kind": "markdown",
                "status": "created",
                "created_at": "2026-07-04T00:00:02+00:00",
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
        "controls": [
            {
                "kind": "inspect",
                "label": "Inspect trace",
                "command": "agentdeck trace --id rep_example",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            }
        ],
    }


def artifacts_example() -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "artifacts_command": "agentdeck artifacts",
        "project_view_contract": "agentdeck contract project-view",
        "trace_contract": "agentdeck contract trace",
        "trace_command_template": "agentdeck trace --id <id>",
        "artifacts": project_view_example()["artifacts"],
        "controls": [
            {
                "kind": "inspect",
                "label": "Inspect artifacts",
                "command": "agentdeck artifacts",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            }
        ],
    }
