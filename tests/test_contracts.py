from __future__ import annotations

from pathlib import Path

from agentdeck.contracts import (
    AGENT_RUNTIME_AGENT_ITEM_FIELDS,
    AGENT_RUNTIME_CAPTURE_RESPONSE_FIELDS,
    AGENT_RUNTIME_REFRESH_AGENT_FIELDS,
    AGENT_RUNTIME_REFRESH_RESPONSE_FIELDS,
    APPROVAL_DISPATCH_READY_RESPONSE_FIELDS,
    APPROVAL_DISPATCH_READY_RESULT_FIELDS,
    APPROVAL_ITEM_FIELDS,
    APPROVAL_QUEUE_FIELDS,
    CONTRACT_INDEX_ITEM_FIELDS,
    CONTRACT_INDEX_RESPONSE_FIELDS,
    CONTINUE_CARD_FIELDS,
    CONTROL_REGISTRY_CARD_FIELDS,
    EVENTS_CURSOR_FIELDS,
    EVENTS_EVENT_ITEM_FIELDS,
    EVENTS_RESPONSE_FIELDS,
    INBOX_ITEM_FIELDS,
    INBOX_QUEUE_FIELDS,
    LEADER_ACTION_DETAIL_FIELDS,
    LEADER_ACTIONS_LIST_FIELDS,
    LEADER_CHAT_CAPTURE_CARD_FIELDS,
    LEADER_CHAT_DISPATCH_PREVIEW_CARD_FIELDS,
    LEADER_REVIEW_CONTROL_FIELDS,
    LEADER_REVIEW_RESPONSE_FIELDS,
    LEADER_CHAT_EXPLANATION_FIELDS,
    LEADER_CHAT_INTENT_CARD_FIELDS,
    LEADER_CHAT_INTENT_CONTROL_FIELDS,
    LEADER_CHAT_RESPONSE_FIELDS,
    PROJECT_VIEW_LEADER_ACTIONS_FIELDS,
    PROJECT_VIEW_LEADER_ACTION_ITEM_FIELDS,
    PROJECT_VIEW_JOB_ITEM_FIELDS,
    PROJECT_VIEW_MESSAGE_ITEM_FIELDS,
    PROJECT_VIEW_RECOVERY_PENDING_FIELDS,
    PROJECT_VIEW_RECOMMENDED_ACTION_FIELDS,
    PROJECT_VIEW_REPLY_ITEM_FIELDS,
    PROJECT_VIEW_RECOVERY_FIELDS,
    PROJECT_VIEW_TOP_LEVEL_FIELDS,
    TRACE_ATTEMPT_FIELDS,
    TRACE_INBOX_ITEM_FIELDS,
    TRACE_JOB_FIELDS,
    TRACE_MESSAGE_FIELDS,
    TRACE_REPLY_FIELDS,
    TRACE_TOP_LEVEL_FIELDS,
    WORKBENCH_AUDIT_CARD_FIELDS,
    WORKBENCH_CHANGE_SUMMARY_FIELDS,
    WORKBENCH_CONTRACTS_CARD_FIELDS,
    WORKBENCH_CONTROL_MODE_CARD_FIELDS,
    WORKBENCH_CONTROL_MODE_CONTROL_FIELDS,
    WORKBENCH_CONTROL_MODE_OPTION_FIELDS,
    WORKBENCH_CONTROL_REGISTRY_ITEM_FIELDS,
    WORKBENCH_LEADER_CARD_FIELDS,
    WORKBENCH_LEADER_CONTROL_FIELDS,
    WORKBENCH_LEDGER_CARD_FIELDS,
    WORKBENCH_LINEAGE_CARD_FIELDS,
    WORKBENCH_LINEAGE_PATH_FIELDS,
    WORKBENCH_OPERATOR_CARD_FIELDS,
    WORKBENCH_PROVIDER_HEALTH_FIELDS,
    WORKBENCH_QUEUE_CARD_FIELDS,
    WORKBENCH_ROLE_AGENT_FIELDS,
    WORKBENCH_ROLE_CARD_FIELDS,
    WORKBENCH_RUNTIME_AGENT_FIELDS,
    WORKBENCH_RUNTIME_CARD_FIELDS,
    WORKBENCH_RUNTIME_CONTROL_FIELDS,
    WORKBENCH_SNAPSHOT_FIELDS,
    agent_runtime_contract_payload,
    agent_runtime_contract_response,
    agent_runtime_example,
    approval_dispatch_ready_example,
    approval_contract_payload,
    approval_contract_response,
    approval_example,
    contract_index_response,
    controls_contract_payload,
    controls_contract_response,
    controls_example,
    inbox_contract_payload,
    inbox_contract_response,
    inbox_example,
    leader_chat_contract_payload,
    leader_chat_contract_response,
    leader_chat_example,
    leader_action_contract_payload,
    leader_action_contract_response,
    leader_action_example,
    leader_actions_contract_payload,
    leader_actions_contract_response,
    leader_actions_example,
    leader_review_contract_payload,
    leader_review_contract_response,
    leader_review_example,
    continue_contract_payload,
    continue_contract_response,
    continue_example,
    doctor_contract_payload,
    doctor_contract_response,
    doctor_example,
    events_contract_payload,
    events_contract_response,
    events_example,
    project_view_contract_payload,
    project_view_contract_response,
    project_view_example,
    trace_contract_payload,
    trace_contract_response,
    trace_example,
    workbench_contract_payload,
    workbench_contract_response,
    workbench_example,
    validate_approval_contract,
    validate_approval_dispatch_ready_contract,
    validate_continue_contract,
    validate_control_registry_card_contract,
    validate_inbox_contract,
    validate_leader_action_contract,
    validate_leader_actions_contract,
    validate_leader_chat_contract,
    validate_leader_review_contract,
    validate_project_view_contract,
    validate_trace_contract,
    validate_workbench_contract,
)
from agentdeck.models import PROJECT_VIEW_SCHEMA_VERSION


def test_contract_index_response_is_reusable_without_cli(tmp_path: Path) -> None:
    docs = {
        "project-view-schema.md",
        "continue-card-schema.md",
        "doctor-schema.md",
        "events-schema.md",
        "workbench-schema.md",
        "controls-schema.md",
        "agent-runtime-schema.md",
        "leader-chat-schema.md",
        "leader-actions-schema.md",
        "leader-action-schema.md",
        "leader-review-schema.md",
        "approvals-schema.md",
        "inbox-schema.md",
        "trace-schema.md",
    }
    for filename in docs:
        (tmp_path / filename).write_text(f"# {filename}\n", encoding="utf-8")

    payload = contract_index_response(tmp_path)

    assert payload["schema_version"] == PROJECT_VIEW_SCHEMA_VERSION
    assert payload["contracts_command"] == "agentdeck contract list"
    assert payload["contract_docs_dir"] == str(tmp_path)
    assert payload["response_fields"] == list(CONTRACT_INDEX_RESPONSE_FIELDS)
    assert payload["contract_item_fields"] == list(CONTRACT_INDEX_ITEM_FIELDS)
    assert payload["count"] == 14
    assert len(payload["contracts"]) == payload["count"]
    assert [item["name"] for item in payload["contracts"]] == [
        "project-view",
        "continue",
        "doctor",
        "events",
        "workbench",
        "controls",
        "agent-runtime",
        "leader-chat",
        "leader-actions",
        "leader-review",
        "leader-action",
        "approvals",
        "inbox",
        "trace",
    ]
    for contract in payload["contracts"]:
        assert set(contract) == set(CONTRACT_INDEX_ITEM_FIELDS)
        assert contract["contract_exists"] is True
        assert contract["command"].startswith("agentdeck contract ")
        assert contract["example_command"].endswith(" --example")


def test_controls_contract_payload_is_reusable_without_cli(tmp_path: Path) -> None:
    contract_path = tmp_path / "controls-schema.md"
    contract_path.write_text("# Controls Contract\n", encoding="utf-8")

    payload = controls_contract_payload(contract_path)

    assert payload["schema_version"] == PROJECT_VIEW_SCHEMA_VERSION
    assert payload["controls_command"] == "agentdeck controls"
    assert payload["contract_path"] == str(contract_path)
    assert payload["contract_exists"] is True
    assert payload["control_registry_card_fields"] == list(CONTROL_REGISTRY_CARD_FIELDS)
    assert payload["control_registry_item_fields"] == list(WORKBENCH_CONTROL_REGISTRY_ITEM_FIELDS)
    assert payload["workbench_contract"] == "agentdeck contract workbench"
    assert payload["leader_chat_contract"] == "agentdeck contract leader-chat"


def test_controls_contract_response_includes_example_without_drift(tmp_path: Path) -> None:
    contract_path = tmp_path / "controls-schema.md"
    contract_path.write_text("# Controls Contract\n", encoding="utf-8")

    payload = controls_contract_response(contract_path, include_example=True)
    example = controls_example()

    assert payload["example"] is True
    assert payload["example_control_registry_card"] == example
    assert payload["example_control_registry_card_fields"] == list(example)
    assert payload["example_control_registry_item_fields"] == list(example["items"][0])
    assert set(payload["example_control_registry_card_fields"]) == set(CONTROL_REGISTRY_CARD_FIELDS)
    assert set(payload["example_control_registry_item_fields"]) == set(WORKBENCH_CONTROL_REGISTRY_ITEM_FIELDS)
    assert example["mode"] == "control_registry"
    assert example["source_command"] == "agentdeck workbench"
    assert example["default_command"] == "agentdeck controls"
    assert example["item_count"] == len(example["items"])


def test_validate_control_registry_card_contract_accepts_example() -> None:
    result = validate_control_registry_card_contract(controls_example())

    assert result == {"ok": True, "errors": []}


def test_agent_runtime_contract_payload_is_reusable_without_cli(tmp_path: Path) -> None:
    contract_path = tmp_path / "agent-runtime-schema.md"
    contract_path.write_text("# Agent Runtime Contract\n", encoding="utf-8")

    payload = agent_runtime_contract_payload(contract_path)

    assert payload["schema_version"] == PROJECT_VIEW_SCHEMA_VERSION
    assert payload["list_command"] == "agentdeck agent list"
    assert payload["spawn_command_template"] == "agentdeck agent spawn --agent <id>"
    assert payload["capture_command_template"] == "agentdeck agent capture --agent <id> --lines 200"
    assert payload["send_command_template"] == "agentdeck agent send --agent <id> --text <text>"
    assert payload["stop_command_template"] == "agentdeck agent stop --agent <id>"
    assert payload["refresh_command"] == "agentdeck agent refresh"
    assert payload["contract_path"] == str(contract_path)
    assert payload["contract_exists"] is True
    assert payload["agent_item_fields"] == list(AGENT_RUNTIME_AGENT_ITEM_FIELDS)
    assert payload["capture_response_fields"] == list(AGENT_RUNTIME_CAPTURE_RESPONSE_FIELDS)
    assert payload["refresh_response_fields"] == list(AGENT_RUNTIME_REFRESH_RESPONSE_FIELDS)
    assert payload["refresh_agent_fields"] == list(AGENT_RUNTIME_REFRESH_AGENT_FIELDS)
    assert payload["runtime_control_fields"] == list(WORKBENCH_RUNTIME_CONTROL_FIELDS)
    assert payload["workbench_contract"] == "agentdeck contract workbench"


def test_agent_runtime_contract_response_includes_example_without_drift(tmp_path: Path) -> None:
    contract_path = tmp_path / "agent-runtime-schema.md"
    contract_path.write_text("# Agent Runtime Contract\n", encoding="utf-8")

    payload = agent_runtime_contract_response(contract_path, include_example=True)
    example = agent_runtime_example()

    assert payload["schema_version"] == PROJECT_VIEW_SCHEMA_VERSION
    assert payload["example"] is True
    assert payload["example_agent_runtime"] == example
    assert payload["example_agent_item_fields"] == payload["agent_item_fields"]
    assert payload["example_capture_response_fields"] == payload["capture_response_fields"]
    assert payload["example_refresh_response_fields"] == payload["refresh_response_fields"]
    assert payload["example_refresh_agent_fields"] == payload["refresh_agent_fields"]
    assert payload["example_control_fields"] == payload["runtime_control_fields"]
    assert set(example["agents"][0]) == set(AGENT_RUNTIME_AGENT_ITEM_FIELDS)
    assert set(example["capture"]) == set(AGENT_RUNTIME_CAPTURE_RESPONSE_FIELDS)
    assert set(example["refresh"]) == set(AGENT_RUNTIME_REFRESH_RESPONSE_FIELDS)
    assert set(example["refresh"]["agents"][0]) == set(AGENT_RUNTIME_REFRESH_AGENT_FIELDS)
    assert set(example["controls"][0]) == set(WORKBENCH_RUNTIME_CONTROL_FIELDS)


def test_project_view_contract_payload_is_reusable_without_cli(tmp_path: Path) -> None:
    contract_path = tmp_path / "project-view-schema.md"
    contract_path.write_text("# ProjectView Contract\n", encoding="utf-8")

    payload = project_view_contract_payload(contract_path)

    assert payload["schema_version"] == PROJECT_VIEW_SCHEMA_VERSION
    assert payload["status_command"] == "agentdeck status"
    assert payload["contract_path"] == str(contract_path)
    assert payload["contract_exists"] is True
    assert payload["top_level_fields"] == list(PROJECT_VIEW_TOP_LEVEL_FIELDS)
    assert payload["recovery_fields"] == list(PROJECT_VIEW_RECOVERY_FIELDS)
    assert payload["recovery_pending_fields"] == list(PROJECT_VIEW_RECOVERY_PENDING_FIELDS)
    assert payload["recommended_action_fields"] == list(PROJECT_VIEW_RECOMMENDED_ACTION_FIELDS)
    assert payload["leader_actions_fields"] == list(PROJECT_VIEW_LEADER_ACTIONS_FIELDS)
    assert payload["leader_action_item_fields"] == list(PROJECT_VIEW_LEADER_ACTION_ITEM_FIELDS)
    assert payload["message_item_fields"] == list(PROJECT_VIEW_MESSAGE_ITEM_FIELDS)
    assert payload["job_item_fields"] == list(PROJECT_VIEW_JOB_ITEM_FIELDS)
    assert payload["reply_item_fields"] == list(PROJECT_VIEW_REPLY_ITEM_FIELDS)


def test_project_view_example_matches_contract_field_lists(tmp_path: Path) -> None:
    contract_path = tmp_path / "missing.md"
    payload = project_view_contract_payload(contract_path)
    example = project_view_example()

    assert payload["schema_version"] == example["schema_version"]
    assert set(payload["top_level_fields"]) == set(example)
    assert set(payload["recovery_fields"]) == set(example["recovery"])
    assert set(payload["recovery_pending_fields"]) == set(example["recovery"]["pending"])
    assert set(payload["recommended_action_fields"]) == set(example["recovery"]["recommended_action"])
    assert set(payload["leader_actions_fields"]) == set(example["leader_actions"])
    assert set(payload["leader_action_item_fields"]) == set(example["leader_actions"]["items"][0])
    assert set(payload["message_item_fields"]) == set(example["messages"]["items"][0])
    assert set(payload["job_item_fields"]) == set(example["jobs"]["items"][0])
    assert set(payload["reply_item_fields"]) == set(example["replies"]["items"][0])
    assert example["leader_actions"]["recommended_action_id"] == "act_example"
    assert example["leader_actions"]["items"][0]["is_recommended"] is True
    assert example["recovery"]["recommended_action"]["target_id"] == "act_example"


def test_project_view_contract_response_matches_cli_shape(tmp_path: Path) -> None:
    contract_path = tmp_path / "project-view-schema.md"
    contract_path.write_text("# ProjectView Contract\n", encoding="utf-8")

    payload = project_view_contract_response(contract_path)

    assert payload == project_view_contract_payload(contract_path)


def test_project_view_contract_response_includes_example_without_drift(tmp_path: Path) -> None:
    contract_path = tmp_path / "project-view-schema.md"
    contract_path.write_text("# ProjectView Contract\n", encoding="utf-8")

    payload = project_view_contract_response(contract_path, include_example=True)
    example = project_view_example()

    assert payload["example"] is True
    assert payload["example_project_view"] == example
    assert payload["example_top_level_fields"] == payload["top_level_fields"]
    assert set(payload["example_top_level_fields"]) == set(example)
    assert payload["example_recovery_fields"] == payload["recovery_fields"]
    assert set(payload["example_recovery_fields"]) == set(example["recovery"])
    assert payload["example_recovery_pending_fields"] == payload["recovery_pending_fields"]
    assert set(payload["example_recovery_pending_fields"]) == set(example["recovery"]["pending"])
    assert payload["example_recommended_action_fields"] == payload["recommended_action_fields"]
    assert set(payload["example_recommended_action_fields"]) == set(example["recovery"]["recommended_action"])
    assert payload["example_leader_actions_fields"] == payload["leader_actions_fields"]
    assert set(payload["example_leader_actions_fields"]) == set(example["leader_actions"])
    assert payload["example_leader_action_item_fields"] == payload["leader_action_item_fields"]
    assert set(payload["example_leader_action_item_fields"]) == set(example["leader_actions"]["items"][0])
    assert payload["example_message_item_fields"] == payload["message_item_fields"]
    assert set(payload["example_message_item_fields"]) == set(example["messages"]["items"][0])
    assert payload["example_job_item_fields"] == payload["job_item_fields"]
    assert set(payload["example_job_item_fields"]) == set(example["jobs"]["items"][0])
    assert payload["example_reply_item_fields"] == payload["reply_item_fields"]
    assert set(payload["example_reply_item_fields"]) == set(example["replies"]["items"][0])


def test_validate_project_view_contract_accepts_example() -> None:
    result = validate_project_view_contract(project_view_example())

    assert result == {"ok": True, "errors": []}


def test_validate_project_view_contract_reports_missing_top_level_field() -> None:
    payload = project_view_example()
    del payload["recovery"]

    result = validate_project_view_contract(payload)

    assert result == {"ok": False, "errors": ["missing top-level field: recovery"]}


def test_validate_project_view_contract_reports_schema_version_mismatch() -> None:
    payload = project_view_example()
    payload["schema_version"] = "project-view/v0"

    result = validate_project_view_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["schema_version mismatch: expected project-view/v1, got project-view/v0"],
    }


def test_validate_project_view_contract_reports_missing_leader_action_recommendation_fields() -> None:
    payload = project_view_example()
    del payload["leader_actions"]["recommended_action_id"]
    del payload["leader_actions"]["items"][0]["is_recommended"]

    result = validate_project_view_contract(payload)

    assert result == {
        "ok": False,
        "errors": [
            "missing leader_actions field: recommended_action_id",
            "missing leader_actions item field: is_recommended",
        ],
    }


def test_validate_project_view_contract_reports_missing_recovery_pending_field() -> None:
    payload = project_view_example()
    del payload["recovery"]["pending"]["runtime_stale"]

    result = validate_project_view_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["missing recovery pending field: runtime_stale"],
    }


def test_validate_project_view_contract_reports_missing_trace_commands() -> None:
    payload = project_view_example()
    del payload["messages"]["items"][0]["trace_command"]
    del payload["jobs"]["items"][0]["trace_command"]
    del payload["replies"]["items"][0]["trace_command"]

    result = validate_project_view_contract(payload)

    assert result == {
        "ok": False,
        "errors": [
            "missing message item field: trace_command",
            "missing job item field: trace_command",
            "missing reply item field: trace_command",
        ],
    }


def test_leader_chat_contract_payload_is_reusable_without_cli(tmp_path: Path) -> None:
    contract_path = tmp_path / "leader-chat-schema.md"
    contract_path.write_text("# Leader Chat Contract\n", encoding="utf-8")

    payload = leader_chat_contract_payload(contract_path)

    assert payload["schema_version"] == PROJECT_VIEW_SCHEMA_VERSION
    assert payload["chat_command"] == "agentdeck leader chat --message <text>"
    assert payload["contract_path"] == str(contract_path)
    assert payload["contract_exists"] is True
    assert payload["response_fields"] == list(LEADER_CHAT_RESPONSE_FIELDS)
    assert payload["explanation_fields"] == list(LEADER_CHAT_EXPLANATION_FIELDS)
    assert payload["intent_card_fields"] == list(LEADER_CHAT_INTENT_CARD_FIELDS)
    assert payload["intent_control_fields"] == list(LEADER_CHAT_INTENT_CONTROL_FIELDS)
    assert payload["leader_action_card_fields"] == [
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
    ]
    assert payload["continue_card_fields"] == list(CONTINUE_CARD_FIELDS)
    assert payload["capture_card_fields"] == list(LEADER_CHAT_CAPTURE_CARD_FIELDS)
    assert payload["dispatch_preview_card_fields"] == list(LEADER_CHAT_DISPATCH_PREVIEW_CARD_FIELDS)
    assert payload["runtime_card_fields"] == list(WORKBENCH_RUNTIME_CARD_FIELDS)
    assert payload["queue_card_fields"] == list(WORKBENCH_QUEUE_CARD_FIELDS)
    assert payload["operator_card_fields"] == list(WORKBENCH_OPERATOR_CARD_FIELDS)
    assert payload["role_card_fields"] == list(WORKBENCH_ROLE_CARD_FIELDS)
    assert payload["role_agent_fields"] == list(WORKBENCH_ROLE_AGENT_FIELDS)
    assert payload["ledger_card_fields"] == list(WORKBENCH_LEDGER_CARD_FIELDS)
    assert payload["lineage_card_fields"] == list(WORKBENCH_LINEAGE_CARD_FIELDS)
    assert payload["lineage_path_fields"] == list(WORKBENCH_LINEAGE_PATH_FIELDS)
    assert payload["trace_card_fields"] == list(TRACE_TOP_LEVEL_FIELDS)
    assert payload["trace_message_fields"] == list(TRACE_MESSAGE_FIELDS)
    assert payload["trace_attempt_fields"] == list(TRACE_ATTEMPT_FIELDS)
    assert payload["trace_job_fields"] == list(TRACE_JOB_FIELDS)
    assert payload["trace_reply_fields"] == list(TRACE_REPLY_FIELDS)
    assert payload["trace_inbox_item_fields"] == list(TRACE_INBOX_ITEM_FIELDS)
    assert payload["workbench_card_fields"] == list(WORKBENCH_SNAPSHOT_FIELDS)
    assert payload["control_mode_card_fields"] == list(WORKBENCH_CONTROL_MODE_CARD_FIELDS)
    assert payload["control_mode_option_fields"] == list(WORKBENCH_CONTROL_MODE_OPTION_FIELDS)
    assert payload["control_mode_control_fields"] == list(WORKBENCH_CONTROL_MODE_CONTROL_FIELDS)
    assert payload["workbench_control_registry_item_fields"] == list(WORKBENCH_CONTROL_REGISTRY_ITEM_FIELDS)
    assert payload["control_registry_card_fields"] == [
        "mode",
        "title",
        "source_command",
        "default_command",
        "item_count",
        "items",
    ]


def test_continue_contract_payload_is_reusable_without_cli(tmp_path: Path) -> None:
    contract_path = tmp_path / "continue-card-schema.md"
    contract_path.write_text("# Continue Card Contract\n", encoding="utf-8")

    payload = continue_contract_payload(contract_path)

    assert payload["schema_version"] == PROJECT_VIEW_SCHEMA_VERSION
    assert payload["continue_command"] == "agentdeck continue"
    assert payload["contract_path"] == str(contract_path)
    assert payload["contract_exists"] is True
    assert payload["continue_card_fields"] == list(CONTINUE_CARD_FIELDS)
    assert payload["project_view_schema_version"] == PROJECT_VIEW_SCHEMA_VERSION
    assert payload["project_view_contract"] == "agentdeck contract project-view"


def test_continue_contract_response_includes_example_without_drift(tmp_path: Path) -> None:
    contract_path = tmp_path / "continue-card-schema.md"
    contract_path.write_text("# Continue Card Contract\n", encoding="utf-8")

    payload = continue_contract_response(contract_path, include_example=True)
    example = continue_example()

    assert payload["example"] is True
    assert payload["example_continue_card"] == example
    assert payload["example_continue_card_fields"] == payload["continue_card_fields"]
    assert set(payload["example_continue_card_fields"]) == set(example)
    assert example["mode"] == "continue"
    assert example["project_view_command"] == "agentdeck status"
    assert example["action_detail_command"] == "agentdeck leader action --action-id act_example"


def test_doctor_contract_payload_is_reusable_without_cli(tmp_path: Path) -> None:
    contract_path = tmp_path / "doctor-schema.md"
    contract_path.write_text("# Doctor Contract\n", encoding="utf-8")

    payload = doctor_contract_payload(contract_path)

    assert payload["schema_version"] == PROJECT_VIEW_SCHEMA_VERSION
    assert payload["doctor_command"] == "agentdeck doctor"
    assert payload["contract_path"] == str(contract_path)
    assert payload["contract_exists"] is True
    assert payload["response_fields"] == [
        "ok",
        "doctor_command",
        "root",
        "config_exists",
        "config_path",
        "tmux",
        "configured_leader",
        "deepseek",
        "openai_compatible",
    ]
    assert payload["configured_leader_fields"] == [
        "agent_id",
        "provider",
        "model",
        "approval_mode",
        "ready",
        "supported",
        "missing_env",
        "detail",
        "setup_commands",
    ]
    assert payload["provider_check_fields"] == ["ok", "detail"]
    assert payload["workbench_contract"] == "agentdeck contract workbench"
    assert payload["leader_chat_contract"] == "agentdeck contract leader-chat"
    assert payload["leader_review_contract"] == "agentdeck contract leader-review"


def test_doctor_contract_response_includes_example_without_drift(tmp_path: Path) -> None:
    contract_path = tmp_path / "doctor-schema.md"
    contract_path.write_text("# Doctor Contract\n", encoding="utf-8")

    payload = doctor_contract_response(contract_path, include_example=True)
    example = doctor_example()

    assert payload["example"] is True
    assert payload["example_doctor"] == example
    assert payload["example_response_fields"] == payload["response_fields"]
    assert set(payload["example_response_fields"]) == set(example)
    assert payload["example_configured_leader_fields"] == payload["configured_leader_fields"]
    assert set(payload["example_configured_leader_fields"]) == set(example["configured_leader"])
    assert payload["example_provider_check_fields"] == payload["provider_check_fields"]
    assert set(payload["example_provider_check_fields"]) == set(example["deepseek"])
    assert example["configured_leader"]["setup_commands"][0] == (
        'export DEEPSEEK_API_KEY="<your-deepseek-api-key>"'
    )


def test_events_contract_payload_is_reusable_without_cli(tmp_path: Path) -> None:
    contract_path = tmp_path / "events-schema.md"
    contract_path.write_text("# Events Contract\n", encoding="utf-8")

    payload = events_contract_payload(contract_path)

    assert payload["schema_version"] == PROJECT_VIEW_SCHEMA_VERSION
    assert payload["events_command"] == "agentdeck events"
    assert payload["contract_path"] == str(contract_path)
    assert payload["contract_exists"] is True
    assert payload["response_fields"] == list(EVENTS_RESPONSE_FIELDS)
    assert payload["cursor_fields"] == list(EVENTS_CURSOR_FIELDS)
    assert payload["event_item_fields"] == list(EVENTS_EVENT_ITEM_FIELDS)
    assert payload["project_view_contract"] == "agentdeck contract project-view"
    assert payload["workbench_contract"] == "agentdeck contract workbench"


def test_events_contract_response_includes_example_without_drift(tmp_path: Path) -> None:
    contract_path = tmp_path / "events-schema.md"
    contract_path.write_text("# Events Contract\n", encoding="utf-8")

    payload = events_contract_response(contract_path, include_example=True)
    example = events_example()

    assert payload["example"] is True
    assert payload["example_events"] == example
    assert payload["example_response_fields"] == payload["response_fields"]
    assert set(payload["example_response_fields"]) == set(example)
    assert payload["example_event_item_fields"] == payload["event_item_fields"]
    assert set(payload["example_event_item_fields"]) == set(example["events"][0])


def test_validate_continue_contract_accepts_example() -> None:
    result = validate_continue_contract(continue_example())

    assert result == {"ok": True, "errors": []}


def test_validate_continue_contract_reports_missing_field() -> None:
    payload = continue_example()
    del payload["next_command"]

    result = validate_continue_contract(payload)

    assert result == {"ok": False, "errors": ["missing continue_card field: next_command"]}


def test_workbench_contract_response_includes_example_without_drift(tmp_path: Path) -> None:
    contract_path = tmp_path / "workbench-schema.md"
    contract_path.write_text("# Workbench Snapshot Contract\n", encoding="utf-8")

    payload = workbench_contract_response(contract_path, include_example=True)
    example = workbench_example()
    lineage_card_fields = [
        "mode",
        "title",
        "message_count",
        "job_count",
        "reply_count",
        "inbox_count",
        "trace_command_template",
        "recent_paths",
    ]
    lineage_path_fields = [
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
    ]

    assert payload["schema_version"] == PROJECT_VIEW_SCHEMA_VERSION
    assert payload["workbench_command"] == "agentdeck workbench"
    assert payload["snapshot_fields"] == list(WORKBENCH_SNAPSHOT_FIELDS)
    assert "leader_inbox_card" in payload["snapshot_fields"]
    assert payload["leader_card_fields"] == list(WORKBENCH_LEADER_CARD_FIELDS)
    assert payload["leader_control_fields"] == list(WORKBENCH_LEADER_CONTROL_FIELDS)
    assert payload["control_mode_card_fields"] == list(WORKBENCH_CONTROL_MODE_CARD_FIELDS)
    assert payload["control_mode_option_fields"] == list(WORKBENCH_CONTROL_MODE_OPTION_FIELDS)
    assert payload["control_mode_control_fields"] == list(WORKBENCH_CONTROL_MODE_CONTROL_FIELDS)
    assert payload["provider_health_fields"] == list(WORKBENCH_PROVIDER_HEALTH_FIELDS)
    assert payload["runtime_card_fields"] == list(WORKBENCH_RUNTIME_CARD_FIELDS)
    assert payload["runtime_agent_fields"] == list(WORKBENCH_RUNTIME_AGENT_FIELDS)
    assert payload["runtime_control_fields"] == list(WORKBENCH_RUNTIME_CONTROL_FIELDS)
    assert payload["role_card_fields"] == list(WORKBENCH_ROLE_CARD_FIELDS)
    assert payload["role_agent_fields"] == list(WORKBENCH_ROLE_AGENT_FIELDS)
    assert payload["ledger_card_fields"] == list(WORKBENCH_LEDGER_CARD_FIELDS)
    assert payload["lineage_card_fields"] == lineage_card_fields
    assert payload["lineage_path_fields"] == lineage_path_fields
    assert payload["queue_card_fields"] == list(WORKBENCH_QUEUE_CARD_FIELDS)
    assert payload["operator_card_fields"] == list(WORKBENCH_OPERATOR_CARD_FIELDS)
    assert payload["audit_card_fields"] == list(WORKBENCH_AUDIT_CARD_FIELDS)
    assert payload["contracts_card_fields"] == list(WORKBENCH_CONTRACTS_CARD_FIELDS)
    assert payload["change_summary_fields"] == list(WORKBENCH_CHANGE_SUMMARY_FIELDS)
    assert payload["control_registry_item_fields"] == list(WORKBENCH_CONTROL_REGISTRY_ITEM_FIELDS)
    assert payload["example"] is True
    assert payload["example_workbench"] == example
    assert payload["example_snapshot_fields"] == payload["snapshot_fields"]
    assert set(payload["example_snapshot_fields"]) == set(example)
    assert example["leader_inbox_card"]["agent_id"] == "leader"
    assert example["leader_inbox_card"]["items"][0]["event_type"] == "task_reply"
    assert example["mode"] == "workbench"
    assert example["leader_actions"] == example["project_view"]["leader_actions"]
    assert set(example["leader_card"]) == set(WORKBENCH_LEADER_CARD_FIELDS)
    assert example["leader_card"]["review_command_template"] == "agentdeck leader review --plan-id <plan_id>"
    assert [control["kind"] for control in example["leader_card"]["controls"]] == [
        "chat",
        "continue",
        "review",
        "actions",
        "status",
    ]
    assert set(example["leader_card"]["controls"][0]) == set(WORKBENCH_LEADER_CONTROL_FIELDS)
    assert example["leader_card"]["controls"][0]["enabled"] is False
    assert example["leader_card"]["controls"][0]["blocker"] == "requires message text"
    assert example["leader_card"]["controls"][2]["enabled"] is False
    assert example["leader_card"]["controls"][2]["blocker"] == "requires plan_id"
    assert set(example["control_mode_card"]) == set(WORKBENCH_CONTROL_MODE_CARD_FIELDS)
    assert set(example["control_mode_card"]["available_modes"][0]) == set(WORKBENCH_CONTROL_MODE_OPTION_FIELDS)
    assert set(example["control_mode_card"]["active_controls"][0]) == set(WORKBENCH_CONTROL_MODE_CONTROL_FIELDS)
    assert example["control_mode_card"]["current_mode"] == "ask"
    assert example["control_mode_card"]["available_modes"][2]["enabled"] is False
    assert set(example["control_registry"][0]) == set(WORKBENCH_CONTROL_REGISTRY_ITEM_FIELDS)
    assert example["control_registry"][0] == {
        "scope": "leader",
        "card": "leader_card",
        "kind": "chat",
        "label": "Ask Leader",
        "command": "agentdeck leader chat --message <text>",
        "safety": "explicit_user",
        "enabled": False,
        "blocker": "requires message text",
        "agent_id": "leader",
    }
    assert {
        (item["scope"], item["card"], item["kind"], item["agent_id"])
        for item in example["control_registry"]
    } >= {
        ("leader", "leader_card", "continue", "leader"),
        ("policy", "control_mode_card", "set_mode", None),
        ("runtime", "runtime_card", "capture", "planner"),
        ("operator", "operator_card", "apply", None),
    }
    policy_item = next(
        item for item in example["control_registry"] if item["scope"] == "policy" and item["kind"] == "set_mode"
    )
    assert policy_item["command"] == "agentdeck policy set-mode --mode ask"
    assert policy_item["safety"] == "inspect"
    assert policy_item["enabled"] is False
    assert policy_item["blocker"] == "already current mode"
    approve_item = next(
        item
        for item in example["control_registry"]
        if item["scope"] == "policy" and item["command"] == "agentdeck policy set-mode --mode approve"
    )
    assert approve_item["enabled"] is True
    assert approve_item["safety"] == "explicit_user"
    assert set(example["provider_health"]) == set(WORKBENCH_PROVIDER_HEALTH_FIELDS)
    assert set(example["runtime_card"]) == set(WORKBENCH_RUNTIME_CARD_FIELDS)
    assert set(example["runtime_card"]["agents"][0]) == set(WORKBENCH_RUNTIME_AGENT_FIELDS)
    assert example["runtime_card"]["agents"][0]["capture_command"] == (
        "agentdeck agent capture --agent planner --lines 200"
    )
    assert example["runtime_card"]["agents"][0]["send_command_template"] == (
        "agentdeck agent send --agent planner --text <text>"
    )
    assert example["runtime_card"]["agents"][0]["controls"][0] == {
        "kind": "capture",
        "label": "Capture pane output",
        "command": "agentdeck agent capture --agent planner --lines 200",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }
    assert example["runtime_card"]["agents"][0]["controls"][1] == {
        "kind": "send",
        "label": "Send input",
        "command": "agentdeck agent send --agent planner --text <text>",
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
    }
    assert set(example["runtime_card"]["agents"][0]["controls"][0]) == set(WORKBENCH_RUNTIME_CONTROL_FIELDS)
    assert set(example["role_card"]) == set(WORKBENCH_ROLE_CARD_FIELDS)
    assert set(example["role_card"]["agents"][0]) == set(WORKBENCH_ROLE_AGENT_FIELDS)
    assert set(example["ledger_card"]) == set(WORKBENCH_LEDGER_CARD_FIELDS)
    assert set(example["lineage_card"]) == set(lineage_card_fields)
    assert set(example["lineage_card"]["recent_paths"][0]) == set(lineage_path_fields)
    assert example["lineage_card"]["recent_paths"][0] == {
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
    assert set(example["queue_card"]) == set(WORKBENCH_QUEUE_CARD_FIELDS)
    assert set(example["operator_card"]) == set(WORKBENCH_OPERATOR_CARD_FIELDS)
    assert example["operator_card"]["controls"][0]["command"] == example["operator_card"]["preview_command"]
    assert example["operator_card"]["controls"][1]["command"] == example["operator_card"]["apply_command"]
    assert set(example["audit_card"]) == set(WORKBENCH_AUDIT_CARD_FIELDS)
    assert set(example["contracts_card"]) == set(WORKBENCH_CONTRACTS_CARD_FIELDS)
    assert example["contracts_card"]["contracts_command"] == "agentdeck contract list"
    assert example["contracts_card"]["contract_index_contract"] == "docs/contracts/contract-index-schema.md"
    assert example["contracts_card"]["controls_contract"] == "agentdeck contract controls"
    assert example["contracts_card"]["leader_chat_contract"] == "agentdeck contract leader-chat"
    assert example["contracts_card"]["leader_review_contract"] == "agentdeck contract leader-review"
    assert set(example["change_summary"]) == set(WORKBENCH_CHANGE_SUMMARY_FIELDS)
    assert example["ledger_card"]["trace_commands"] == [
        "agentdeck trace --id msg_example",
        "agentdeck trace --id job_example",
        "agentdeck trace --id rep_example",
    ]
    assert example["recovery"] == example["project_view"]["recovery"]
    assert example["audit_card"]["latest_event"] == example["recovery"]["latest_event"]
    assert example["audit_card"]["recent_events"] == example["recovery"]["recent_events"]
    assert example["next_command"] == example["continue_card"]["next_command"]


def test_validate_workbench_contract_accepts_example() -> None:
    result = validate_workbench_contract(workbench_example())

    assert result == {"ok": True, "errors": []}


def test_validate_workbench_contract_requires_leader_inbox_card_contract() -> None:
    payload = workbench_example()
    del payload["leader_inbox_card"]["items"][0]["ack_command"]

    result = validate_workbench_contract(payload)

    assert result == {"ok": False, "errors": ["leader_inbox_card: missing inbox item field: ack_command"]}


def test_validate_workbench_contract_reuses_continue_card_validator() -> None:
    payload = workbench_example()
    del payload["continue_card"]["pending"]["approvals"]

    result = validate_workbench_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["continue_card: missing pending field: approvals"],
    }


def test_validate_workbench_contract_requires_leader_fields() -> None:
    payload = workbench_example()
    del payload["leader_card"]["api_backed"]

    result = validate_workbench_contract(payload)

    assert result == {"ok": False, "errors": ["missing leader_card field: api_backed"]}


def test_validate_workbench_contract_requires_leader_control_fields() -> None:
    payload = workbench_example()
    del payload["leader_card"]["controls"][0]["safety"]

    result = validate_workbench_contract(payload)

    assert result == {"ok": False, "errors": ["missing leader control field: safety"]}


def test_validate_workbench_contract_requires_control_mode_fields() -> None:
    payload = workbench_example()
    del payload["control_mode_card"]["available_modes"][0]["requires_explicit_user"]

    result = validate_workbench_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["missing control mode option field: requires_explicit_user"],
    }


def test_validate_workbench_contract_requires_control_registry_item_fields() -> None:
    payload = workbench_example()
    del payload["control_registry"][0]["scope"]

    result = validate_workbench_contract(payload)

    assert result == {"ok": False, "errors": ["missing control_registry item field: scope"]}


def test_validate_workbench_contract_requires_lineage_card_fields() -> None:
    payload = workbench_example()
    del payload["lineage_card"]["recent_paths"][0]["trace_command"]

    result = validate_workbench_contract(payload)

    assert result == {"ok": False, "errors": ["missing lineage path field: trace_command"]}


def test_validate_workbench_contract_requires_provider_health_fields() -> None:
    payload = workbench_example()
    del payload["provider_health"]["ready"]

    result = validate_workbench_contract(payload)

    assert result == {"ok": False, "errors": ["missing provider_health field: ready"]}


def test_validate_workbench_contract_requires_provider_health_booleans() -> None:
    payload = workbench_example()
    payload["provider_health"]["ready"] = "yes"

    result = validate_workbench_contract(payload)

    assert result == {"ok": False, "errors": ["provider_health.ready must be a boolean"]}


def test_validate_workbench_contract_requires_runtime_agent_fields() -> None:
    payload = workbench_example()
    del payload["runtime_card"]["agents"][0]["pane_id"]

    result = validate_workbench_contract(payload)

    assert result == {"ok": False, "errors": ["missing runtime agent field: pane_id"]}


def test_validate_workbench_contract_requires_runtime_agent_controls() -> None:
    payload = workbench_example()
    del payload["runtime_card"]["agents"][0]["controls"]

    result = validate_workbench_contract(payload)

    assert result == {"ok": False, "errors": ["missing runtime agent field: controls"]}


def test_validate_workbench_contract_requires_runtime_control_fields() -> None:
    payload = workbench_example()
    del payload["runtime_card"]["agents"][0]["controls"][0]["enabled"]

    result = validate_workbench_contract(payload)

    assert result == {"ok": False, "errors": ["missing runtime control field: enabled"]}


def test_validate_workbench_contract_requires_role_agent_fields() -> None:
    payload = workbench_example()
    del payload["role_card"]["agents"][0]["assign_command"]

    result = validate_workbench_contract(payload)

    assert result == {"ok": False, "errors": ["missing role agent field: assign_command"]}


def test_validate_workbench_contract_requires_ledger_trace_commands() -> None:
    payload = workbench_example()
    payload["ledger_card"]["messages"] = {
        **payload["ledger_card"]["messages"],
        "items": [dict(payload["ledger_card"]["messages"]["items"][0])],
    }
    del payload["ledger_card"]["messages"]["items"][0]["trace_command"]

    result = validate_workbench_contract(payload)

    assert result == {"ok": False, "errors": ["missing message item field: trace_command"]}


def test_validate_workbench_contract_requires_queue_fields() -> None:
    payload = workbench_example()
    del payload["queue_card"]["refresh_command"]

    result = validate_workbench_contract(payload)

    assert result == {"ok": False, "errors": ["missing queue_card field: refresh_command"]}


def test_validate_workbench_contract_requires_operator_fields() -> None:
    payload = workbench_example()
    del payload["operator_card"]["controls"]

    result = validate_workbench_contract(payload)

    assert result == {"ok": False, "errors": ["missing operator_card field: controls"]}


def test_validate_workbench_contract_requires_dispatch_ready_operator_command() -> None:
    payload = workbench_example()
    payload["operator_card"]["action_kind"] = "approval_dispatch_ready"
    payload["operator_card"]["command"] = "agentdeck approval dispatch --approval-id apv_example"
    payload["operator_card"]["explicit_command"] = "agentdeck approval dispatch-ready --confirm"

    result = validate_workbench_contract(payload)

    assert result == {
        "ok": False,
        "errors": [
            "operator_card approval_dispatch_ready command must be agentdeck approval dispatch-ready --confirm"
        ],
    }


def test_validate_workbench_contract_requires_audit_fields() -> None:
    payload = workbench_example()
    del payload["audit_card"]["events_command"]

    result = validate_workbench_contract(payload)

    assert result == {"ok": False, "errors": ["missing audit_card field: events_command"]}


def test_validate_workbench_contract_requires_change_summary_fields() -> None:
    payload = workbench_example()
    del payload["change_summary"]["has_new_events"]

    result = validate_workbench_contract(payload)

    assert result == {"ok": False, "errors": ["missing change_summary field: has_new_events"]}


def test_validate_workbench_contract_requires_contracts_card_fields() -> None:
    payload = workbench_example()
    del payload["contracts_card"]["contracts_command"]

    result = validate_workbench_contract(payload)

    assert result == {"ok": False, "errors": ["missing contracts_card field: contracts_command"]}


def test_validate_workbench_contract_requires_matching_project_view_summaries() -> None:
    payload = workbench_example()
    payload["leader_actions"] = {"count": 0, "by_kind": {}, "by_status": {}, "recommended_action_id": None, "items": []}

    result = validate_workbench_contract(payload)

    assert result == {"ok": False, "errors": ["leader_actions must match project_view.leader_actions"]}


def test_approval_contract_payload_is_reusable_without_cli(tmp_path: Path) -> None:
    contract_path = tmp_path / "approvals-schema.md"
    contract_path.write_text("# Approvals Contract\n", encoding="utf-8")

    payload = approval_contract_payload(contract_path)

    assert payload["schema_version"] == PROJECT_VIEW_SCHEMA_VERSION
    assert payload["approvals_command"] == "agentdeck approval list"
    assert payload["contract_path"] == str(contract_path)
    assert payload["contract_exists"] is True
    assert payload["queue_fields"] == list(APPROVAL_QUEUE_FIELDS)
    assert payload["approval_item_fields"] == list(APPROVAL_ITEM_FIELDS)
    assert payload["dispatch_ready_command"] == "agentdeck approval dispatch-ready --confirm"
    assert payload["dispatch_ready_response_fields"] == list(APPROVAL_DISPATCH_READY_RESPONSE_FIELDS)
    assert payload["dispatch_ready_result_fields"] == list(APPROVAL_DISPATCH_READY_RESULT_FIELDS)
    assert payload["project_view_contract"] == "agentdeck contract project-view"


def test_approval_contract_response_includes_example_without_drift(tmp_path: Path) -> None:
    contract_path = tmp_path / "approvals-schema.md"
    contract_path.write_text("# Approvals Contract\n", encoding="utf-8")

    payload = approval_contract_response(contract_path, include_example=True)
    example = approval_example()

    assert payload["example"] is True
    assert payload["example_approval_queue"] == example
    assert payload["example_queue_fields"] == payload["queue_fields"]
    assert set(payload["example_queue_fields"]) == set(example)
    assert payload["example_approval_item_fields"] == payload["approval_item_fields"]
    assert set(payload["example_approval_item_fields"]) == set(example["approvals"][0])
    assert payload["example_dispatch_ready_fields"] == payload["dispatch_ready_response_fields"]
    assert payload["example_dispatch_ready_result_fields"] == payload["dispatch_ready_result_fields"]
    assert set(payload["example_dispatch_ready_fields"]) == set(payload["example_dispatch_ready"])
    assert set(payload["example_dispatch_ready_result_fields"]) == set(payload["example_dispatch_ready"]["results"][0])
    assert example["approvals"][0]["can_dispatch"] is False
    assert example["approvals"][0]["preview_command"] == "agentdeck approval list"
    assert example["approvals"][0]["controls"] == [
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
            "command": "agentdeck approval approve --approval-id apv_pending",
            "safety": "explicit_runtime",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "reject",
            "label": "Reject",
            "command": "agentdeck approval reject --approval-id apv_pending --reason <reason>",
            "safety": "explicit_runtime",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "dispatch",
            "label": "Dispatch",
            "command": "agentdeck approval dispatch --approval-id apv_pending",
            "safety": "explicit_runtime",
            "enabled": False,
            "blocker": "approval is not approved",
        },
    ]
    assert example["approvals"][1]["can_dispatch"] is True
    assert example["approvals"][1]["preview_command"] == "agentdeck approval list"
    assert example["approvals"][1]["controls"][3]["enabled"] is True
    assert example["approvals"][1]["controls"][3]["blocker"] is None


def test_validate_approval_contract_accepts_example() -> None:
    result = validate_approval_contract(approval_example())

    assert result == {"ok": True, "errors": []}


def test_validate_approval_contract_requires_gui_action_fields() -> None:
    payload = approval_example()
    del payload["approvals"][0]["controls"]

    result = validate_approval_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["missing approval item field: controls"],
    }


def test_validate_approval_dispatch_ready_contract_accepts_example() -> None:
    result = validate_approval_dispatch_ready_contract(approval_dispatch_ready_example())

    assert result == {"ok": True, "errors": []}


def test_validate_approval_dispatch_ready_contract_checks_counts() -> None:
    payload = approval_dispatch_ready_example()
    payload["dispatched_count"] = 99

    result = validate_approval_dispatch_ready_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["dispatch_ready.dispatched_count must match dispatched results"],
    }


def test_inbox_contract_payload_is_reusable_without_cli(tmp_path: Path) -> None:
    contract_path = tmp_path / "inbox-schema.md"
    contract_path.write_text("# Inbox Contract\n", encoding="utf-8")

    payload = inbox_contract_payload(contract_path)

    assert payload["schema_version"] == PROJECT_VIEW_SCHEMA_VERSION
    assert payload["inbox_command"] == "agentdeck inbox --agent <id>"
    assert payload["contract_path"] == str(contract_path)
    assert payload["contract_exists"] is True
    assert payload["queue_fields"] == list(INBOX_QUEUE_FIELDS)
    assert payload["inbox_item_fields"] == list(INBOX_ITEM_FIELDS)
    assert payload["trace_contract"] == "agentdeck contract trace"


def test_inbox_contract_response_includes_example_without_drift(tmp_path: Path) -> None:
    contract_path = tmp_path / "inbox-schema.md"
    contract_path.write_text("# Inbox Contract\n", encoding="utf-8")

    payload = inbox_contract_response(contract_path, include_example=True)
    example = inbox_example()

    assert payload["example"] is True
    assert payload["example_inbox"] == example
    assert payload["example_queue_fields"] == payload["queue_fields"]
    assert set(payload["example_queue_fields"]) == set(example)
    assert payload["example_inbox_item_fields"] == payload["inbox_item_fields"]
    assert set(payload["example_inbox_item_fields"]) == set(example["items"][0])
    assert example["head_inbox_id"] == "inb_task"
    assert example["items"][0]["can_ack"] is True
    assert example["items"][0]["preview_command"] == "agentdeck trace --id inb_task"
    assert example["items"][0]["controls"] == [
        {
            "kind": "preview",
            "label": "Trace inbox item",
            "command": "agentdeck trace --id inb_task",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "ack",
            "label": "Acknowledge inbox head",
            "command": "agentdeck ack --agent planner --inbox-id inb_task",
            "safety": "explicit_runtime",
            "enabled": True,
            "blocker": None,
        },
    ]
    assert example["items"][1]["can_ack"] is False
    assert example["items"][1]["preview_command"] == "agentdeck trace --id inb_reply"
    assert example["items"][1]["controls"][1]["enabled"] is False
    assert example["items"][1]["controls"][1]["blocker"] == "inbox item is not head"


def test_validate_inbox_contract_accepts_example() -> None:
    result = validate_inbox_contract(inbox_example())

    assert result == {"ok": True, "errors": []}


def test_validate_inbox_contract_requires_head_ack_fields() -> None:
    payload = inbox_example()
    del payload["items"][0]["controls"]

    result = validate_inbox_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["missing inbox item field: controls"],
    }


def test_leader_action_contract_payload_is_reusable_without_cli(tmp_path: Path) -> None:
    contract_path = tmp_path / "leader-action-schema.md"
    contract_path.write_text("# Leader Action Contract\n", encoding="utf-8")

    payload = leader_action_contract_payload(contract_path)

    assert payload["schema_version"] == PROJECT_VIEW_SCHEMA_VERSION
    assert payload["action_command"] == "agentdeck leader action --action-id <id>"
    assert payload["contract_path"] == str(contract_path)
    assert payload["contract_exists"] is True
    assert payload["action_fields"] == list(LEADER_ACTION_DETAIL_FIELDS)
    assert payload["project_view_contract"] == "agentdeck contract project-view"


def test_leader_action_contract_response_includes_example_without_drift(tmp_path: Path) -> None:
    contract_path = tmp_path / "leader-action-schema.md"
    contract_path.write_text("# Leader Action Contract\n", encoding="utf-8")

    payload = leader_action_contract_response(contract_path, include_example=True)
    example = leader_action_example()

    assert payload["example"] is True
    assert payload["example_leader_action"] == example
    assert payload["example_action_fields"] == payload["action_fields"]
    assert set(payload["example_action_fields"]) == set(example)
    assert example["action_id"] == "act_example"
    assert example["preview_command"] == "agentdeck leader action --action-id act_example"
    assert example["recovery"]["recommended_action"]["target_id"] == "act_example"
    assert example["matches_recommended_action"] is True


def test_validate_leader_action_contract_accepts_example() -> None:
    result = validate_leader_action_contract(leader_action_example())

    assert result == {"ok": True, "errors": []}


def test_validate_leader_action_contract_requires_embedded_recovery_contract() -> None:
    payload = leader_action_example()
    del payload["recovery"]["pending"]["leader_errors"]

    result = validate_leader_action_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["recovery: missing recovery pending field: leader_errors"],
    }


def test_leader_review_contract_payload_is_reusable_without_cli(tmp_path: Path) -> None:
    contract_path = tmp_path / "leader-review-schema.md"
    contract_path.write_text("# Leader Review Contract\n", encoding="utf-8")

    payload = leader_review_contract_payload(contract_path)

    assert payload["schema_version"] == PROJECT_VIEW_SCHEMA_VERSION
    assert payload["review_command"] == "agentdeck leader review --plan-id <id>"
    assert payload["contract_path"] == str(contract_path)
    assert payload["contract_exists"] is True
    assert payload["response_fields"] == list(LEADER_REVIEW_RESPONSE_FIELDS)
    assert payload["control_fields"] == list(LEADER_REVIEW_CONTROL_FIELDS)
    assert payload["project_view_contract"] == "agentdeck contract project-view"


def test_leader_review_contract_response_includes_example_without_drift(tmp_path: Path) -> None:
    contract_path = tmp_path / "leader-review-schema.md"
    contract_path.write_text("# Leader Review Contract\n", encoding="utf-8")

    payload = leader_review_contract_response(contract_path, include_example=True)
    example = leader_review_example()

    assert payload["example"] is True
    assert payload["example_leader_review"] == example
    assert payload["example_response_fields"] == payload["response_fields"]
    assert set(payload["example_response_fields"]) == set(example)
    assert payload["example_control_fields"] == payload["control_fields"]
    assert set(payload["example_control_fields"]) == set(example["controls"][0])
    assert example["next_action"] == "wait_for_reply"
    assert example["next_command"] == "agentdeck capture-reply --agent planner --message-id msg_example"
    assert example["controls"][0]["command"] == "agentdeck trace --id msg_example"
    assert example["controls"][1]["command"] == example["next_command"]
    assert example["controls"][1]["safety"] == "explicit_runtime"


def test_validate_leader_review_contract_accepts_example() -> None:
    result = validate_leader_review_contract(leader_review_example())

    assert result == {"ok": True, "errors": []}


def test_validate_leader_review_contract_requires_response_and_control_fields() -> None:
    payload = leader_review_example()
    del payload["next_command"]
    del payload["controls"][0]["safety"]

    result = validate_leader_review_contract(payload)

    assert result == {
        "ok": False,
        "errors": [
            "missing leader_review field: next_command",
            "missing leader review control field: safety",
        ],
    }


def test_validate_leader_review_contract_rejects_non_list_controls() -> None:
    payload = leader_review_example()
    payload["controls"] = None

    result = validate_leader_review_contract(payload)

    assert result == {"ok": False, "errors": ["controls must be a list"]}


def test_leader_actions_contract_payload_is_reusable_without_cli(tmp_path: Path) -> None:
    contract_path = tmp_path / "leader-actions-schema.md"
    contract_path.write_text("# Leader Actions Contract\n", encoding="utf-8")

    payload = leader_actions_contract_payload(contract_path)

    assert payload["schema_version"] == PROJECT_VIEW_SCHEMA_VERSION
    assert payload["actions_command"] == "agentdeck leader actions"
    assert payload["contract_path"] == str(contract_path)
    assert payload["contract_exists"] is True
    assert payload["list_fields"] == list(LEADER_ACTIONS_LIST_FIELDS)
    assert payload["action_item_fields"] == list(PROJECT_VIEW_LEADER_ACTION_ITEM_FIELDS)
    assert payload["project_view_contract"] == "agentdeck contract project-view"


def test_leader_actions_contract_response_includes_example_without_drift(tmp_path: Path) -> None:
    contract_path = tmp_path / "leader-actions-schema.md"
    contract_path.write_text("# Leader Actions Contract\n", encoding="utf-8")

    payload = leader_actions_contract_response(contract_path, include_example=True)
    example = leader_actions_example()

    assert payload["example"] is True
    assert payload["example_leader_actions"] == example
    assert payload["example_list_fields"] == payload["list_fields"]
    assert set(payload["example_list_fields"]) == set(example)
    assert payload["example_action_item_fields"] == payload["action_item_fields"]
    assert set(payload["example_action_item_fields"]) == set(example["actions"][0])
    assert example["recommended_action_id"] == "act_example"
    assert example["actions"][0]["preview_command"] == "agentdeck leader action --action-id act_example"
    assert example["actions"][0]["controls"] == [
        {
            "kind": "preview",
            "label": "Preview Leader action",
            "command": "agentdeck leader action --action-id act_example",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "apply",
            "label": "Apply safe Leader action",
            "command": "agentdeck leader apply-action --action-id act_example",
            "safety": "safe_apply",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "explicit",
            "label": "Run explicit command",
            "command": "agentdeck approval create-from-plan --plan-id pln_example",
            "safety": "explicit_runtime",
            "enabled": True,
            "blocker": None,
        },
    ]
    assert example["actions"][0]["can_apply"] is True
    assert example["actions"][0]["is_recommended"] is True


def test_validate_leader_actions_contract_accepts_example() -> None:
    result = validate_leader_actions_contract(leader_actions_example())

    assert result == {"ok": True, "errors": []}


def test_validate_leader_actions_contract_requires_applyability_fields() -> None:
    payload = leader_actions_example()
    del payload["actions"][0]["controls"]

    result = validate_leader_actions_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["missing leader action item field: controls"],
    }


def test_leader_chat_contract_response_includes_example_without_drift(tmp_path: Path) -> None:
    contract_path = tmp_path / "leader-chat-schema.md"
    contract_path.write_text("# Leader Chat Contract\n", encoding="utf-8")

    payload = leader_chat_contract_response(contract_path, include_example=True)
    example = leader_chat_example()

    assert payload["example"] is True
    assert payload["example_leader_chat"] == example
    assert payload["example_response_fields"] == payload["response_fields"]
    assert set(payload["example_response_fields"]) == set(example)
    assert payload["example_explanation_fields"] == payload["explanation_fields"]
    assert set(payload["example_explanation_fields"]) == set(example["leader_explanation"])
    assert payload["example_intent_card_fields"] == payload["intent_card_fields"]
    assert payload["example_intent_card_fields"] == list(example["intent_card"])
    assert payload["example_intent_control_fields"] == payload["intent_control_fields"]
    assert payload["example_intent_control_fields"] == list(example["intent_card"]["controls"][0])
    assert payload["example_leader_action_card_fields"] == payload["leader_action_card_fields"]
    assert payload["example_leader_action_card_fields"] == list(example["leader_action_card"])
    assert example["leader_action_card"]["action_id"] == example["leader_action"]["action_id"]
    assert example["leader_action_card"]["controls"] == example["leader_action"]["controls"]
    assert payload["example_continue_card_fields"] == payload["continue_card_fields"]
    assert set(payload["example_continue_card_fields"]) == set(example["continue_card"])
    assert payload["example_runtime_card_fields"] == payload["runtime_card_fields"]
    assert payload["example_runtime_card_fields"] == list(example["runtime_card"])
    assert payload["example_queue_card_fields"] == payload["queue_card_fields"]
    assert payload["example_queue_card_fields"] == list(example["queue_card"])
    assert payload["example_operator_card_fields"] == payload["operator_card_fields"]
    assert payload["example_operator_card_fields"] == list(example["operator_card"])
    assert set(payload["example_role_card_fields"]) == set(payload["role_card_fields"])
    assert set(payload["example_role_card_fields"]) == set(example["role_card"])
    assert set(payload["example_role_agent_fields"]) == set(payload["role_agent_fields"])
    assert set(payload["example_role_agent_fields"]) == set(example["role_card"]["agents"][0])
    assert payload["example_ledger_card_fields"] == payload["ledger_card_fields"]
    assert payload["example_ledger_card_fields"] == list(example["ledger_card"])
    assert payload["example_lineage_card_fields"] == payload["lineage_card_fields"]
    assert payload["example_lineage_card_fields"] == list(example["lineage_card"])
    assert payload["example_lineage_path_fields"] == payload["lineage_path_fields"]
    assert payload["example_lineage_path_fields"] == list(example["lineage_card"]["recent_paths"][0])
    assert example["trace_card"] is None
    assert payload["example_workbench_card_fields"] == payload["workbench_card_fields"]
    assert payload["example_workbench_card_fields"] == list(example["workbench_card"])
    assert payload["example_control_mode_card_fields"] == payload["control_mode_card_fields"]
    assert payload["example_control_mode_card_fields"] == list(example["control_mode_card"])
    assert payload["example_workbench_control_registry_item_fields"] == (
        payload["workbench_control_registry_item_fields"]
    )
    assert payload["example_workbench_control_registry_item_fields"] == list(
        example["workbench_card"]["control_registry"][0]
    )
    assert payload["example_control_registry_card_fields"] == payload["control_registry_card_fields"]
    assert payload["example_control_registry_card_fields"] == list(example["control_registry_card"])
    assert example["control_registry_card"]["items"][0] == example["workbench_card"]["control_registry"][0]
    assert example["control_registry_card"]["item_count"] == len(example["control_registry_card"]["items"])
    assert payload["example_capability_card_fields"] == payload["capability_card_fields"]
    assert payload["example_capability_card_fields"] == list(example["capability_card"])
    assert "controls" in payload["capability_item_fields"]
    assert payload["example_capability_item_fields"] == payload["capability_item_fields"]
    assert payload["example_capability_item_fields"] == list(example["capability_card"]["capabilities"][0])
    assert payload["capability_control_fields"] == payload["intent_control_fields"]
    assert payload["example_capability_control_fields"] == payload["capability_control_fields"]
    assert payload["example_capability_control_fields"] == list(example["capability_card"]["capabilities"][0]["controls"][0])
    assert payload["capability_placeholder_fields"] == ["placeholder", "blocker"]
    assert payload["example_capability_placeholder_fields"] == payload["capability_placeholder_fields"]
    assert payload["capability_placeholders"] == [
        {"placeholder": "<goal>", "blocker": "requires goal text"},
        {"placeholder": "<plan_id>", "blocker": "requires plan_id"},
        {"placeholder": "<action_id>", "blocker": "requires action_id"},
        {"placeholder": "<agent_id>", "blocker": "requires agent_id"},
        {"placeholder": "<mode>", "blocker": "requires control mode"},
    ]
    assert example["capability_card"]["capabilities"][0]["controls"][0] == {
        "kind": "inspect",
        "label": "Open workbench",
        "command": "agentdeck workbench",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }
    assert example["leader_explanation"]["recommended_action_id"] == "act_example"
    assert example["leader_explanation"]["safety"] == "safe_apply"
    assert example["mode"] == "continue"
    assert example["continue_card"]["next_command"] == example["next_command"]
    assert example["continue_card"]["leader_action"] == example["leader_action"]
    assert example["leader_actions"] == example["project_view"]["leader_actions"]


def test_validate_leader_chat_contract_accepts_example() -> None:
    result = validate_leader_chat_contract(leader_chat_example())

    assert result == {"ok": True, "errors": []}


def test_validate_leader_chat_contract_requires_control_registry_card_count() -> None:
    payload = leader_chat_example()
    payload["control_registry_card"]["item_count"] = 999

    result = validate_leader_chat_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["control_registry_card.item_count must match items length"],
    }


def test_validate_leader_chat_contract_requires_action_card_when_action_is_present() -> None:
    payload = leader_chat_example()
    payload["leader_action_card"] = None

    result = validate_leader_chat_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["leader_action_card is required when leader_action is present"],
    }


def test_validate_leader_chat_contract_checks_dispatch_batch_preview_counts() -> None:
    payload = leader_chat_example()
    payload["dispatch_batch_preview_card"] = {
        "mode": "dispatch_batch_preview",
        "approval_command": "agentdeck approval list",
        "dispatch_ready_command": "agentdeck approval dispatch-ready --confirm",
        "count": 2,
        "ready_count": 2,
        "blocked_count": 0,
        "items": [
            {
                "approval_id": "apv_one",
                "agent_id": "planner",
                "agent_role": "planning",
                "pane_id": "%42",
                "runtime_status": "running",
                "task": "Plan the work",
                "dispatch_command": "agentdeck approval dispatch --approval-id apv_one",
                "approval_command": "agentdeck approval list",
                "inbox_command": "agentdeck inbox --agent planner",
                "requires_explicit_user": True,
                "safety": "explicit_runtime",
                "blocker": None,
                "controls": [
                    {
                        "kind": "inspect",
                        "label": "Inspect approval",
                        "command": "agentdeck approval list",
                        "safety": "inspect",
                        "enabled": True,
                        "blocker": None,
                    },
                    {
                        "kind": "dispatch",
                        "label": "Dispatch approval",
                        "command": "agentdeck approval dispatch --approval-id apv_one",
                        "safety": "explicit_runtime",
                        "enabled": True,
                        "blocker": None,
                    },
                ],
            }
        ],
        "requires_explicit_user": True,
        "safety": "explicit_runtime",
        "blocker": None,
        "controls": [
            {
                "kind": "inspect",
                "label": "Inspect approvals",
                "command": "agentdeck approval list",
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
        ],
    }

    result = validate_leader_chat_contract(payload)

    assert result == {
        "ok": False,
        "errors": [
            "dispatch_batch_preview_card.count must match items length",
            "dispatch_batch_preview_card.ready_count must match unblocked items",
        ],
    }


def test_validate_leader_chat_contract_rejects_dispatch_preview_control_drift() -> None:
    payload = leader_chat_example()
    payload["dispatch_preview_card"] = {
        "approval_id": "apv_blocked",
        "agent_id": "planner",
        "agent_role": "planning",
        "pane_id": None,
        "runtime_status": "configured",
        "task": "Plan the work",
        "dispatch_command": "agentdeck approval dispatch --approval-id apv_blocked",
        "approval_command": "agentdeck approval list",
        "inbox_command": "agentdeck inbox --agent planner",
        "requires_explicit_user": True,
        "safety": "explicit_runtime",
        "blocker": "agent is not spawned: planner",
        "controls": [
            {
                "kind": "dispatch",
                "label": "Dispatch approval",
                "command": "agentdeck approval dispatch --approval-id apv_blocked",
                "safety": "explicit_runtime",
                "enabled": True,
                "blocker": None,
            }
        ],
    }

    result = validate_leader_chat_contract(payload)

    assert result == {
        "ok": False,
        "errors": [
            "dispatch_preview_card.controls: dispatch enabled must match blocker",
            "dispatch_preview_card.controls: dispatch blocker must match card blocker",
        ],
    }


def test_validate_leader_chat_contract_reuses_continue_card_validator() -> None:
    payload = leader_chat_example()
    del payload["continue_card"]["pending"]["leader_errors"]

    result = validate_leader_chat_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["continue_card: missing pending field: leader_errors"],
    }


def test_validate_leader_chat_contract_reports_missing_explanation_field() -> None:
    payload = leader_chat_example()
    del payload["leader_explanation"]["safety"]

    result = validate_leader_chat_contract(payload)

    assert result == {"ok": False, "errors": ["missing leader_explanation field: safety"]}


def test_validate_leader_chat_contract_reports_missing_intent_card_field() -> None:
    payload = leader_chat_example()
    del payload["intent_card"]["route_source"]

    result = validate_leader_chat_contract(payload)

    assert result == {"ok": False, "errors": ["missing intent_card field: route_source"]}


def test_validate_leader_chat_contract_requires_intent_next_command_match() -> None:
    payload = leader_chat_example()
    payload["intent_card"]["next_command"] = "agentdeck workbench"

    result = validate_leader_chat_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["intent_card: next_command must match response next_command"],
    }


def test_validate_leader_chat_contract_requires_intent_control_fields() -> None:
    payload = leader_chat_example()
    del payload["intent_card"]["controls"][0]["enabled"]

    result = validate_leader_chat_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["intent_card.controls: missing control field: enabled"],
    }


def test_validate_leader_chat_contract_requires_inspect_control_safety() -> None:
    payload = leader_chat_example()
    payload["intent_card"]["controls"].insert(
        0,
        {
            "kind": "inspect",
            "label": "Inspect",
            "command": "agentdeck workbench",
            "safety": "explicit_runtime",
            "enabled": True,
            "blocker": None,
        },
    )

    result = validate_leader_chat_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["intent_card.controls: inspect controls must use safety=inspect"],
    }


def test_validate_leader_chat_contract_requires_disabled_control_blocker() -> None:
    payload = leader_chat_example()
    payload["intent_card"]["controls"][0]["enabled"] = False
    payload["intent_card"]["controls"][0]["blocker"] = None

    result = validate_leader_chat_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["intent_card.controls: disabled controls must include blocker"],
    }


def test_validate_leader_chat_contract_requires_placeholder_intent_controls_disabled() -> None:
    payload = leader_chat_example()
    payload["next_command"] = "agentdeck approval reject --approval-id apv_example --reason <reason>"
    payload["intent_card"]["next_command"] = payload["next_command"]
    payload["queue_card"] = None
    payload["operator_card"] = None
    payload["intent_card"]["controls"][0] = {
        "kind": "next",
        "label": "Next command",
        "command": payload["next_command"],
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
    }

    result = validate_leader_chat_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["intent_card.controls: placeholder commands must be disabled"],
    }


def test_validate_leader_chat_contract_requires_placeholder_intent_blocker_match() -> None:
    payload = leader_chat_example()
    payload["next_command"] = "agentdeck approval reject --approval-id apv_example --reason <reason>"
    payload["intent_card"]["next_command"] = payload["next_command"]
    payload["queue_card"] = None
    payload["operator_card"] = None
    payload["intent_card"]["controls"][0] = {
        "kind": "next",
        "label": "Next command",
        "command": payload["next_command"],
        "safety": "explicit_runtime",
        "enabled": False,
        "blocker": "requires approval_id",
    }

    result = validate_leader_chat_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["intent_card.controls: blocker must match placeholder"],
    }


def test_validate_leader_chat_contract_requires_capability_count_match() -> None:
    payload = leader_chat_example()
    payload["capability_card"]["capability_count"] = 999

    result = validate_leader_chat_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["capability_card.capability_count must match capabilities length"],
    }


def test_validate_leader_chat_contract_requires_apply_capability_safety() -> None:
    payload = leader_chat_example()
    payload["capability_card"]["capabilities"].append(
        {
            "mode": "apply_action",
            "label": "Apply safe action",
            "description": "Apply a queued safe Leader action.",
            "example_messages": ["apply action act_xxx"],
            "command": "agentdeck leader apply-action --action-id <action_id>",
            "safety": "inspect",
            "requires_explicit_user": False,
            "card": "leader_action",
            "controls": [
                {
                    "kind": "apply",
                    "label": "Apply safe Leader action",
                    "command": "agentdeck leader apply-action --action-id <action_id>",
                    "safety": "inspect",
                    "enabled": False,
                    "blocker": "requires action_id",
                }
            ],
        }
    )
    payload["capability_card"]["capability_count"] += 1

    result = validate_leader_chat_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["capability_card.capabilities: apply_action must use safety=safe_apply"],
    }


def test_validate_leader_chat_contract_requires_capability_control_fields() -> None:
    payload = leader_chat_example()
    del payload["capability_card"]["capabilities"][0]["controls"][0]["enabled"]

    result = validate_leader_chat_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["capability_card.capabilities.controls: missing control field: enabled"],
    }


def test_validate_leader_chat_contract_requires_capability_control_safety_match() -> None:
    payload = leader_chat_example()
    payload["capability_card"]["capabilities"][0]["controls"][0]["safety"] = "safe_apply"

    result = validate_leader_chat_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["capability_card.capabilities.controls: safety must match capability safety"],
    }


def test_validate_leader_chat_contract_requires_capability_control_command_match() -> None:
    payload = leader_chat_example()
    payload["capability_card"]["capabilities"][0]["controls"][0]["command"] = "agentdeck status"

    result = validate_leader_chat_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["capability_card.capabilities.controls: command must match capability command"],
    }


def test_validate_leader_chat_contract_requires_placeholder_controls_disabled() -> None:
    payload = leader_chat_example()
    plan = next(item for item in payload["capability_card"]["capabilities"] if item["mode"] == "plan")
    plan["controls"][0]["enabled"] = True
    plan["controls"][0]["blocker"] = None

    result = validate_leader_chat_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["capability_card.capabilities.controls: placeholder commands must be disabled"],
    }


def test_validate_leader_chat_contract_requires_placeholder_blocker_match() -> None:
    payload = leader_chat_example()
    review = next(item for item in payload["capability_card"]["capabilities"] if item["mode"] == "review")
    review["controls"][0]["blocker"] = "requires goal text"

    result = validate_leader_chat_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["capability_card.capabilities.controls: blocker must match placeholder"],
    }


def test_validate_leader_chat_contract_rejects_unknown_placeholder_controls() -> None:
    payload = leader_chat_example()
    plan = next(item for item in payload["capability_card"]["capabilities"] if item["mode"] == "plan")
    plan["command"] = "agentdeck run --run-id <run_id>"
    plan["controls"][0]["command"] = "agentdeck run --run-id <run_id>"
    plan["controls"][0]["enabled"] = False
    plan["controls"][0]["blocker"] = "requires run_id"

    result = validate_leader_chat_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["capability_card.capabilities.controls: unsupported placeholder"],
    }


def test_validate_leader_chat_contract_requires_embedded_project_view_contract() -> None:
    payload = leader_chat_example()
    del payload["project_view"]["leader_actions"]["recommended_action_id"]

    result = validate_leader_chat_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["project_view: missing leader_actions field: recommended_action_id"],
    }


def test_trace_contract_payload_is_reusable_without_cli(tmp_path: Path) -> None:
    contract_path = tmp_path / "trace-schema.md"
    contract_path.write_text("# Trace Contract\n", encoding="utf-8")

    payload = trace_contract_payload(contract_path)

    assert payload["schema_version"] == PROJECT_VIEW_SCHEMA_VERSION
    assert payload["trace_command"] == "agentdeck trace --id <id>"
    assert payload["contract_path"] == str(contract_path)
    assert payload["contract_exists"] is True
    assert payload["top_level_fields"] == list(TRACE_TOP_LEVEL_FIELDS)
    assert payload["message_fields"] == list(TRACE_MESSAGE_FIELDS)
    assert payload["attempt_fields"] == list(TRACE_ATTEMPT_FIELDS)
    assert payload["job_fields"] == list(TRACE_JOB_FIELDS)
    assert payload["reply_fields"] == list(TRACE_REPLY_FIELDS)
    assert payload["inbox_item_fields"] == list(TRACE_INBOX_ITEM_FIELDS)


def test_trace_contract_response_includes_example_without_drift(tmp_path: Path) -> None:
    contract_path = tmp_path / "trace-schema.md"
    contract_path.write_text("# Trace Contract\n", encoding="utf-8")

    payload = trace_contract_response(contract_path, include_example=True)
    example = trace_example()

    assert payload["example"] is True
    assert payload["example_trace"] == example
    assert payload["example_top_level_fields"] == payload["top_level_fields"]
    assert set(payload["example_top_level_fields"]) == set(example)
    assert payload["example_message_fields"] == payload["message_fields"]
    assert set(payload["example_message_fields"]) == set(example["message"])
    assert payload["example_attempt_fields"] == payload["attempt_fields"]
    assert set(payload["example_attempt_fields"]) == set(example["attempts"][0])
    assert payload["example_job_fields"] == payload["job_fields"]
    assert set(payload["example_job_fields"]) == set(example["jobs"][0])
    assert payload["example_reply_fields"] == payload["reply_fields"]
    assert set(payload["example_reply_fields"]) == set(example["replies"][0])
    assert payload["example_inbox_item_fields"] == payload["inbox_item_fields"]
    assert set(payload["example_inbox_item_fields"]) == set(example["inbox_items"][0])


def test_validate_trace_contract_accepts_example() -> None:
    result = validate_trace_contract(trace_example())

    assert result == {"ok": True, "errors": []}


def test_validate_trace_contract_reports_missing_reply_field() -> None:
    payload = trace_example()
    del payload["replies"][0]["reply_id"]

    result = validate_trace_contract(payload)

    assert result == {"ok": False, "errors": ["missing reply field: reply_id"]}
