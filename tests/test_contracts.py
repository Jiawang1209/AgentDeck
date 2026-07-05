from __future__ import annotations

from pathlib import Path

from agentdeck.contracts import (
    APPROVAL_ITEM_FIELDS,
    APPROVAL_QUEUE_FIELDS,
    CONTINUE_CARD_FIELDS,
    INBOX_ITEM_FIELDS,
    INBOX_QUEUE_FIELDS,
    LEADER_ACTION_DETAIL_FIELDS,
    LEADER_ACTIONS_LIST_FIELDS,
    LEADER_CHAT_EXPLANATION_FIELDS,
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
    WORKBENCH_LEDGER_CARD_FIELDS,
    WORKBENCH_OPERATOR_CARD_FIELDS,
    WORKBENCH_ROLE_AGENT_FIELDS,
    WORKBENCH_ROLE_CARD_FIELDS,
    WORKBENCH_RUNTIME_AGENT_FIELDS,
    WORKBENCH_RUNTIME_CARD_FIELDS,
    WORKBENCH_SNAPSHOT_FIELDS,
    approval_contract_payload,
    approval_contract_response,
    approval_example,
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
    continue_contract_payload,
    continue_contract_response,
    continue_example,
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
    validate_continue_contract,
    validate_inbox_contract,
    validate_leader_action_contract,
    validate_leader_actions_contract,
    validate_leader_chat_contract,
    validate_project_view_contract,
    validate_trace_contract,
    validate_workbench_contract,
)
from agentdeck.models import PROJECT_VIEW_SCHEMA_VERSION


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
    del payload["recovery"]["pending"]["leader_errors"]

    result = validate_project_view_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["missing recovery pending field: leader_errors"],
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
    assert payload["continue_card_fields"] == list(CONTINUE_CARD_FIELDS)


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

    assert payload["schema_version"] == PROJECT_VIEW_SCHEMA_VERSION
    assert payload["workbench_command"] == "agentdeck workbench"
    assert payload["snapshot_fields"] == list(WORKBENCH_SNAPSHOT_FIELDS)
    assert payload["runtime_card_fields"] == list(WORKBENCH_RUNTIME_CARD_FIELDS)
    assert payload["runtime_agent_fields"] == list(WORKBENCH_RUNTIME_AGENT_FIELDS)
    assert payload["role_card_fields"] == list(WORKBENCH_ROLE_CARD_FIELDS)
    assert payload["role_agent_fields"] == list(WORKBENCH_ROLE_AGENT_FIELDS)
    assert payload["ledger_card_fields"] == list(WORKBENCH_LEDGER_CARD_FIELDS)
    assert payload["operator_card_fields"] == list(WORKBENCH_OPERATOR_CARD_FIELDS)
    assert payload["audit_card_fields"] == list(WORKBENCH_AUDIT_CARD_FIELDS)
    assert payload["example"] is True
    assert payload["example_workbench"] == example
    assert payload["example_snapshot_fields"] == payload["snapshot_fields"]
    assert set(payload["example_snapshot_fields"]) == set(example)
    assert example["mode"] == "workbench"
    assert example["leader_actions"] == example["project_view"]["leader_actions"]
    assert set(example["runtime_card"]) == set(WORKBENCH_RUNTIME_CARD_FIELDS)
    assert set(example["runtime_card"]["agents"][0]) == set(WORKBENCH_RUNTIME_AGENT_FIELDS)
    assert set(example["role_card"]) == set(WORKBENCH_ROLE_CARD_FIELDS)
    assert set(example["role_card"]["agents"][0]) == set(WORKBENCH_ROLE_AGENT_FIELDS)
    assert set(example["ledger_card"]) == set(WORKBENCH_LEDGER_CARD_FIELDS)
    assert set(example["operator_card"]) == set(WORKBENCH_OPERATOR_CARD_FIELDS)
    assert set(example["audit_card"]) == set(WORKBENCH_AUDIT_CARD_FIELDS)
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


def test_validate_workbench_contract_reuses_continue_card_validator() -> None:
    payload = workbench_example()
    del payload["continue_card"]["pending"]["approvals"]

    result = validate_workbench_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["continue_card: missing pending field: approvals"],
    }


def test_validate_workbench_contract_requires_runtime_agent_fields() -> None:
    payload = workbench_example()
    del payload["runtime_card"]["agents"][0]["pane_id"]

    result = validate_workbench_contract(payload)

    assert result == {"ok": False, "errors": ["missing runtime agent field: pane_id"]}


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


def test_validate_workbench_contract_requires_operator_fields() -> None:
    payload = workbench_example()
    del payload["operator_card"]["preview_command"]

    result = validate_workbench_contract(payload)

    assert result == {"ok": False, "errors": ["missing operator_card field: preview_command"]}


def test_validate_workbench_contract_requires_audit_fields() -> None:
    payload = workbench_example()
    del payload["audit_card"]["events_command"]

    result = validate_workbench_contract(payload)

    assert result == {"ok": False, "errors": ["missing audit_card field: events_command"]}


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
    assert example["approvals"][0]["can_dispatch"] is False
    assert example["approvals"][1]["can_dispatch"] is True


def test_validate_approval_contract_accepts_example() -> None:
    result = validate_approval_contract(approval_example())

    assert result == {"ok": True, "errors": []}


def test_validate_approval_contract_requires_gui_action_fields() -> None:
    payload = approval_example()
    del payload["approvals"][0]["dispatch_blocker"]

    result = validate_approval_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["missing approval item field: dispatch_blocker"],
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
    assert example["items"][1]["can_ack"] is False


def test_validate_inbox_contract_accepts_example() -> None:
    result = validate_inbox_contract(inbox_example())

    assert result == {"ok": True, "errors": []}


def test_validate_inbox_contract_requires_head_ack_fields() -> None:
    payload = inbox_example()
    del payload["items"][0]["ack_blocker"]

    result = validate_inbox_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["missing inbox item field: ack_blocker"],
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
    assert example["actions"][0]["can_apply"] is True
    assert example["actions"][0]["is_recommended"] is True


def test_validate_leader_actions_contract_accepts_example() -> None:
    result = validate_leader_actions_contract(leader_actions_example())

    assert result == {"ok": True, "errors": []}


def test_validate_leader_actions_contract_requires_applyability_fields() -> None:
    payload = leader_actions_example()
    del payload["actions"][0]["apply_blocker"]

    result = validate_leader_actions_contract(payload)

    assert result == {
        "ok": False,
        "errors": ["missing leader action item field: apply_blocker"],
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
    assert payload["example_continue_card_fields"] == payload["continue_card_fields"]
    assert set(payload["example_continue_card_fields"]) == set(example["continue_card"])
    assert example["leader_explanation"]["recommended_action_id"] == "act_example"
    assert example["leader_explanation"]["safety"] == "safe_apply"
    assert example["mode"] == "continue"
    assert example["continue_card"]["next_command"] == example["next_command"]
    assert example["continue_card"]["leader_action"] == example["leader_action"]
    assert example["leader_actions"] == example["project_view"]["leader_actions"]


def test_validate_leader_chat_contract_accepts_example() -> None:
    result = validate_leader_chat_contract(leader_chat_example())

    assert result == {"ok": True, "errors": []}


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
