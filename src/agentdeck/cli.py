from __future__ import annotations

from dataclasses import asdict
import argparse
import json
import os
from pathlib import Path
import shlex
import sys
import time

from .config import config_path, load_config, project_root, update_agent_role, write_default_config
from .contracts import (
    agent_runtime_contract_response,
    approval_contract_response,
    contract_index_response,
    continue_contract_response,
    doctor_contract_response,
    events_contract_response,
    inbox_contract_response,
    leader_actions_contract_response,
    leader_action_contract_response,
    leader_chat_capability_card,
    leader_chat_contract_response,
    leader_chat_intent_placeholder_blocker,
    leader_review_contract_response,
    project_view_contract_response,
    runtime_agent_controls,
    trace_contract_response,
    workbench_contract_response,
    validate_approval_contract,
    validate_continue_contract,
    validate_inbox_contract,
    validate_leader_actions_contract,
    validate_leader_action_contract,
    validate_leader_chat_contract,
    validate_leader_review_contract,
    validate_project_view_contract,
    validate_trace_contract,
    validate_workbench_contract,
)
from .models import PROJECT_VIEW_SCHEMA_VERSION, AgentRuntimeBinding, AgentSpec, EventRecord, ProjectConfig
from .orchestration.leader import LeaderOrchestrator
from .providers import DeepSeekProvider, OpenAICompatibleProvider, leader_provider
from .runtime import TmuxBackend
from .state import StateStore, agentdeck_dir


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _print_json_line(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def _trace_command(trace_id: object) -> str:
    return f"agentdeck trace --id {trace_id}"


def _leader_chat_intent_card(payload: dict[str, object]) -> dict[str, object]:
    mode = str(payload.get("mode"))
    explanation = payload.get("leader_explanation") if isinstance(payload.get("leader_explanation"), dict) else {}
    embedded_card = None
    for card_name in (
        "workbench_card",
        "continue_card",
        "inbox_card",
        "approval_card",
        "runtime_card",
        "operator_card",
        "queue_card",
        "role_card",
        "ledger_card",
        "capability_card",
    ):
        if payload.get(card_name) is not None:
            embedded_card = card_name
            break
    route_source = "provider_plan" if mode == "plan" else "state_review" if mode == "review" else "local_rule"
    read_only = mode not in {"plan", "review", "apply_action"}
    next_command = payload.get("next_command")
    controls: list[dict[str, object]] = []
    inspect_command = _leader_chat_intent_inspect_command(embedded_card, payload)
    if inspect_command is not None:
        controls.append(
            {
                "kind": "inspect",
                "label": f"Inspect {embedded_card}",
                "command": inspect_command,
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            }
        )
    next_blocker = leader_chat_intent_placeholder_blocker(next_command)
    next_enabled = next_command is not None and next_blocker is None
    controls.append(
        {
            "kind": "next",
            "label": "Next command",
            "command": next_command,
            "safety": explanation.get("safety"),
            "enabled": next_enabled,
            "blocker": next_blocker if next_command is not None else "next command unavailable",
        }
    )
    return {
        "mode": mode,
        "matched_intent": mode,
        "route_source": route_source,
        "embedded_card": embedded_card,
        "read_only": read_only,
        "next_command": next_command,
        "requires_explicit_user": explanation.get("requires_explicit_user"),
        "controls": controls,
    }


def _leader_chat_intent_inspect_command(embedded_card: object, payload: dict[str, object]) -> str | None:
    if embedded_card == "workbench_card":
        return "agentdeck workbench"
    if embedded_card == "continue_card":
        return "agentdeck continue"
    if embedded_card == "runtime_card":
        return "agentdeck agent list"
    if embedded_card == "ledger_card":
        return "agentdeck workbench"
    if embedded_card == "role_card":
        return "agentdeck workbench"
    if embedded_card == "queue_card" or embedded_card == "operator_card":
        return "agentdeck workbench"
    if embedded_card == "approval_card":
        return "agentdeck approval list"
    if embedded_card == "inbox_card":
        inbox_card = payload.get("inbox_card")
        agent_id = inbox_card.get("agent_id") if isinstance(inbox_card, dict) else None
        return f"agentdeck inbox --agent {agent_id}" if agent_id else None
    if embedded_card == "capability_card":
        return "agentdeck workbench"
    return None


def _project_view_payload_or_error(config: ProjectConfig, store: StateStore) -> dict[str, object] | None:
    payload = asdict(store.project_view(config))
    validation = validate_project_view_contract(payload)
    if not validation["ok"]:
        print("ProjectView contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        return None
    return payload


def _print_leader_chat_payload_or_error(
    payload: dict[str, object],
    store: StateStore,
    *,
    task: str,
) -> int:
    payload.setdefault("capability_card", None)
    payload.setdefault("intent_card", _leader_chat_intent_card(payload))
    validation = validate_leader_chat_contract(payload)
    if not validation["ok"]:
        error = "; ".join(str(item) for item in validation["errors"])
        mode = str(payload.get("mode", "chat"))
        record = store.record_leader_error(mode, "agentdeck-contract", None, task, error)
        store.append_event(
            EventRecord.create(
                "leader_chat_contract_failed",
                {
                    "error_id": record["error_id"],
                    "mode": mode,
                    "message_length": len(task),
                    "error_count": len(validation["errors"]),
                },
            )
        )
        print("Leader chat contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1
    _print_json(payload)
    return 0


def doctor_command(_args: argparse.Namespace) -> int:
    root = project_root()
    tmux = TmuxBackend().doctor()
    config_exists = config_path(root).exists()
    config = load_config(root) if config_exists else None
    deepseek = DeepSeekProvider().doctor()
    openai_compatible = OpenAICompatibleProvider().doctor()
    configured_leader = _doctor_configured_leader(config)
    leader_ready = bool(configured_leader.get("ready")) if configured_leader else False
    ok = tmux.ok and config_exists and leader_ready
    _print_json(
        {
            "ok": ok,
            "doctor_command": "agentdeck doctor",
            "root": str(root),
            "config_exists": config_exists,
            "config_path": str(config_path(root)),
            "tmux": asdict(tmux),
            "configured_leader": configured_leader,
            "deepseek": {"ok": deepseek[0], "detail": deepseek[1]},
            "openai_compatible": {"ok": openai_compatible[0], "detail": openai_compatible[1]},
        }
    )
    return 0 if ok else 1


def _doctor_configured_leader(config: ProjectConfig | None) -> dict[str, object] | None:
    if config is None:
        return None
    provider = config.leader.provider
    required_env = {
        "deepseek": "DEEPSEEK_API_KEY",
        "openai-compatible": "AGENTDECK_LEADER_API_KEY",
    }.get(provider)
    if required_env is None:
        return {
            "agent_id": config.leader.agent_id,
            "provider": provider,
            "model": config.leader.model,
            "approval_mode": config.leader.approval_mode,
            "ready": False,
            "supported": False,
            "missing_env": [],
            "detail": f"unsupported leader provider: {provider}",
            "setup_commands": [],
        }
    ready = bool(os.environ.get(required_env))
    detail = f"{required_env} is set" if ready else f"{required_env} is not set; provider calls are disabled"
    return {
        "agent_id": config.leader.agent_id,
        "provider": provider,
        "model": config.leader.model,
        "approval_mode": config.leader.approval_mode,
        "ready": ready,
        "supported": True,
        "missing_env": [] if ready else [required_env],
        "detail": detail,
        "setup_commands": _provider_setup_commands(provider),
    }


def init_command(_args: argparse.Namespace) -> int:
    root = project_root()
    path = write_default_config(root)
    store = StateStore(root)
    state = store.load()
    store.save(state)
    store.append_event(EventRecord.create("project_initialized", {"config_path": str(path)}))
    _print_json(
        {
            "ok": True,
            "project_root": str(root),
            "agentdeck_dir": str(agentdeck_dir(root)),
            "config_path": str(path),
        }
    )
    return 0


def status_command(_args: argparse.Namespace) -> int:
    root = project_root()
    try:
        config = load_config(root)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        print("Run: python -m agentdeck project init", file=sys.stderr)
        return 1
    store = StateStore(root)
    payload = _project_view_payload_or_error(config, store)
    if payload is None:
        return 1
    _print_json(payload)
    return 0


def events_command(args: argparse.Namespace) -> int:
    _config, store, exit_code = _load_project_or_error()
    if store is None:
        return exit_code
    all_events = store.list_events(1000000)
    latest_event = all_events[-1] if all_events else None
    latest_event_id = latest_event.get("event_id") if isinstance(latest_event, dict) else None
    cursor_found = None
    if args.since:
        cursor_index = next(
            (index for index, event in enumerate(all_events) if event.get("event_id") == args.since),
            -1,
        )
        cursor_found = cursor_index >= 0
        events = all_events[cursor_index + 1 :] if cursor_found else all_events
        if args.limit > 0:
            events = events[-args.limit:]
        else:
            events = []
    else:
        events = store.list_events(args.limit)
    payload = {"count": len(events), "limit": args.limit, "events": events}
    if args.since:
        payload.update(
            {
                "since_event_id": args.since,
                "latest_event_id": latest_event_id,
                "cursor_found": cursor_found,
            }
        )
    _print_json(payload)
    return 0


def _continue_card_payload(project_view: dict[str, object], store: StateStore) -> dict[str, object]:
    recovery = project_view.get("recovery", {})
    recommended_action = recovery.get("recommended_action") if isinstance(recovery, dict) else None
    target_id = recommended_action.get("target_id") if isinstance(recommended_action, dict) else None
    source = recommended_action.get("source") if isinstance(recommended_action, dict) else None
    leader_action = None
    action_detail_command = None
    if source == "leader_action" and target_id:
        try:
            leader_action = store.leader_action_detail(str(target_id))
            action_detail_command = f"agentdeck leader action --action-id {target_id}"
        except KeyError:
            leader_action = None
    return {
        "ok": True,
        "mode": "continue",
        "project_view_schema_version": project_view.get("schema_version"),
        "project_view_command": "agentdeck status",
        "status": recovery.get("status") if isinstance(recovery, dict) else None,
        "reason": recovery.get("reason") if isinstance(recovery, dict) else None,
        "next_command": recovery.get("next_command") if isinstance(recovery, dict) else None,
        "recommended_action": recommended_action,
        "pending": recovery.get("pending") if isinstance(recovery, dict) else None,
        "leader_action": leader_action,
        "action_detail_command": action_detail_command,
    }


def _inbox_agent_id_for_item(store: StateStore, inbox_id: object) -> str | None:
    if not inbox_id:
        return None
    state = store.load()
    for agent_id, items in state.get("inbox", {}).items():
        if any(isinstance(item, dict) and item.get("inbox_id") == inbox_id for item in items):
            return str(agent_id)
    return None


def _leader_chat_recovery_cards(
    project_view: dict[str, object], store: StateStore
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    recovery = project_view.get("recovery", {})
    recommended_action = recovery.get("recommended_action") if isinstance(recovery, dict) else None
    source = recommended_action.get("source") if isinstance(recommended_action, dict) else None
    target_id = recommended_action.get("target_id") if isinstance(recommended_action, dict) else None
    if source == "inbox":
        agent_id = _inbox_agent_id_for_item(store, target_id)
        if agent_id:
            return _inbox_queue_payload(agent_id, store), None
    if source == "approval":
        return None, _approval_queue_payload(store)
    return None, None


def _active_queue_source(project_view: dict[str, object]) -> str:
    recovery = project_view.get("recovery", {})
    recommended_action = recovery.get("recommended_action") if isinstance(recovery, dict) else None
    source = recommended_action.get("source") if isinstance(recommended_action, dict) else None
    return source if source in ("leader_action", "inbox", "approval", "provider_health", "runtime") else "none"


def _workbench_snapshot_payload(
    project_view: dict[str, object], store: StateStore, since_event_id: str | None = None
) -> dict[str, object]:
    continue_card = _continue_card_payload(project_view, store)
    inbox_card, approval_card = _leader_chat_recovery_cards(project_view, store)
    recovery = project_view.get("recovery", {})
    active_queue_source = _active_queue_source(project_view)
    leader_action = continue_card.get("leader_action")
    return {
        "ok": True,
        "mode": "workbench",
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "project_view": project_view,
        "leader_actions": project_view.get("leader_actions"),
        "leader_card": _workbench_leader_card(project_view),
        "provider_health": _workbench_provider_health(project_view),
        "runtime_card": _workbench_runtime_card(project_view),
        "role_card": _workbench_role_card(project_view),
        "ledger_card": _workbench_ledger_card(project_view),
        "queue_card": _workbench_queue_card(project_view, continue_card, active_queue_source),
        "operator_card": _workbench_operator_card(project_view, continue_card, active_queue_source),
        "audit_card": _workbench_audit_card(project_view),
        "contracts_card": _workbench_contracts_card(),
        "recovery": recovery,
        "next_command": continue_card.get("next_command"),
        "continue_card": continue_card,
        "active_queue_source": active_queue_source,
        "inbox_card": inbox_card,
        "approval_card": approval_card,
        "leader_action": leader_action if isinstance(leader_action, dict) else None,
        "change_summary": _workbench_change_summary(store, since_event_id),
    }


def _workbench_change_summary(store: StateStore, since_event_id: str | None) -> dict[str, object]:
    events = store.list_events(1000000)
    latest_event = events[-1] if events else None
    latest_event_id = latest_event.get("event_id") if isinstance(latest_event, dict) else None
    if not since_event_id:
        new_events: list[dict[str, object]] = []
    else:
        cursor_index = next(
            (index for index, event in enumerate(events) if event.get("event_id") == since_event_id),
            -1,
        )
        new_events = [_event_summary(event) for event in events[cursor_index + 1 :]]
    return {
        "since_event_id": since_event_id,
        "latest_event_id": latest_event_id,
        "has_new_events": bool(new_events),
        "new_event_count": len(new_events),
        "new_events": new_events,
    }


def _event_summary(event: dict[str, object]) -> dict[str, object]:
    return {
        "event_id": event.get("event_id"),
        "event_type": event.get("event_type"),
        "created_at": event.get("created_at"),
    }


def _workbench_leader_card(project_view: dict[str, object]) -> dict[str, object]:
    leader = project_view.get("leader") if isinstance(project_view.get("leader"), dict) else {}
    provider = str(leader.get("provider", ""))
    return {
        "agent_id": leader.get("agent_id"),
        "provider": provider,
        "model": leader.get("model"),
        "approval_mode": leader.get("approval_mode"),
        "api_backed": provider not in ("", "fake"),
        "chat_command": "agentdeck leader chat --message <text>",
        "continue_command": "agentdeck continue",
        "actions_command": "agentdeck leader actions",
        "status_command": "agentdeck status",
    }


def _workbench_provider_health(project_view: dict[str, object]) -> dict[str, object]:
    leader = project_view.get("leader") if isinstance(project_view.get("leader"), dict) else {}
    provider = str(leader.get("provider", ""))
    base = {
        "agent_id": leader.get("agent_id"),
        "provider": provider,
        "model": leader.get("model"),
        "approval_mode": leader.get("approval_mode"),
        "api_backed": provider not in ("", "fake"),
        "doctor_contract": "agentdeck contract doctor",
    }
    provider_env = {
        "deepseek": "DEEPSEEK_API_KEY",
        "openai-compatible": "AGENTDECK_LEADER_API_KEY",
    }
    if provider == "fake":
        return {
            **base,
            "supported": True,
            "ready": True,
            "missing_env": [],
            "detail": "fake provider is local and ready",
            "doctor_command": "agentdeck doctor",
            "setup_commands": _provider_setup_commands(provider),
        }
    required_env = provider_env.get(provider)
    if required_env is None:
        return {
            **base,
            "supported": False,
            "ready": False,
            "missing_env": [],
            "detail": f"unsupported leader provider: {provider}",
            "doctor_command": "agentdeck doctor",
            "setup_commands": [],
        }
    missing_env = [] if os.environ.get(required_env) else [required_env]
    ready = not missing_env
    detail = f"{required_env} is set" if ready else f"{required_env} is not set; provider calls are disabled"
    return {
        **base,
        "supported": True,
        "ready": ready,
        "missing_env": missing_env,
        "detail": detail,
        "doctor_command": "agentdeck doctor",
        "setup_commands": _provider_setup_commands(provider),
    }


def _provider_setup_commands(provider: str) -> list[str]:
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
    return []


def _workbench_queue_card(
    project_view: dict[str, object], continue_card: dict[str, object], active_queue_source: str
) -> dict[str, object]:
    leader_actions = project_view.get("leader_actions") if isinstance(project_view.get("leader_actions"), dict) else {}
    approvals = project_view.get("approvals") if isinstance(project_view.get("approvals"), dict) else {}
    inbox = project_view.get("inbox") if isinstance(project_view.get("inbox"), dict) else {}
    leader_status = leader_actions.get("by_status") if isinstance(leader_actions.get("by_status"), dict) else {}
    return {
        "active_queue_source": active_queue_source,
        "next_command": continue_card.get("next_command"),
        "leader_actions": {
            "count": int(leader_actions.get("count", 0)),
            "pending": int(leader_status.get("pending", 0)),
            "recommended_action_id": leader_actions.get("recommended_action_id"),
            "command": "agentdeck leader actions",
        },
        "approvals": {
            "count": int(approvals.get("count", 0)),
            "pending": int(approvals.get("pending", 0)),
            "approved": int(approvals.get("approved", 0)),
            "command": "agentdeck approval list",
        },
        "inbox": {
            "total": int(inbox.get("total", 0)),
            "by_agent": inbox.get("by_agent", {}),
            "command_template": "agentdeck inbox --agent <agent_id>",
        },
        "refresh_command": "agentdeck workbench",
    }


def _workbench_role_card(project_view: dict[str, object]) -> dict[str, object]:
    agents = project_view.get("agents", [])
    role_agents = []
    if isinstance(agents, list):
        for agent in agents:
            if not isinstance(agent, dict):
                continue
            agent_id = str(agent.get("agent_id"))
            role = str(agent.get("role", ""))
            role_prompt = str(agent.get("role_prompt", ""))
            role_agents.append(
                {
                    "agent_id": agent_id,
                    "role": role,
                    "provider": agent.get("provider"),
                    "workspace_mode": agent.get("workspace_mode"),
                    "role_prompt": role_prompt,
                    "assign_command": _agent_assign_role_command(agent_id, role, role_prompt),
                }
            )
    return {
        "count": len(role_agents),
        "agents": role_agents,
        "assign_command_template": (
            "agentdeck agent assign-role --agent <agent_id> --role <role> --role-prompt <role_prompt>"
        ),
    }


def _agent_assign_role_command(agent_id: str, role: str, role_prompt: str) -> str:
    return " ".join(
        [
            "agentdeck",
            "agent",
            "assign-role",
            "--agent",
            shlex.quote(agent_id),
            "--role",
            shlex.quote(role),
            "--role-prompt",
            shlex.quote(role_prompt),
        ]
    )


def _workbench_audit_card(project_view: dict[str, object]) -> dict[str, object]:
    recovery = project_view.get("recovery") if isinstance(project_view.get("recovery"), dict) else {}
    recent_events = recovery.get("recent_events") if isinstance(recovery.get("recent_events"), list) else []
    return {
        "latest_event": recovery.get("latest_event"),
        "recent_events": recent_events,
        "event_count": len(recent_events),
        "events_command": "agentdeck events --limit 20",
    }


def _workbench_contracts_card() -> dict[str, object]:
    return {
        "contracts_command": "agentdeck contract list",
        "contract_index_contract": "docs/contracts/contract-index-schema.md",
        "workbench_contract": "agentdeck contract workbench",
        "agent_runtime_contract": "agentdeck contract agent-runtime",
        "leader_chat_contract": "agentdeck contract leader-chat",
        "project_view_contract": "agentdeck contract project-view",
        "events_contract": "agentdeck contract events",
        "doctor_contract": "agentdeck contract doctor",
    }


def _workbench_operator_card(
    project_view: dict[str, object], continue_card: dict[str, object], active_queue_source: str
) -> dict[str, object]:
    recovery = project_view.get("recovery") if isinstance(project_view.get("recovery"), dict) else {}
    recommended_action = (
        recovery.get("recommended_action") if isinstance(recovery.get("recommended_action"), dict) else {}
    )
    leader_action = continue_card.get("leader_action")
    leader_action = leader_action if isinstance(leader_action, dict) else {}
    source = str(recommended_action.get("source", "none"))
    target_id = recommended_action.get("target_id")
    action_kind = source if source in ("inbox", "approval", "leader_action", "provider_health", "runtime") else "none"
    can_apply = bool(leader_action.get("can_apply")) if action_kind == "leader_action" else False
    apply_command = leader_action.get("apply_command") if can_apply else None
    explicit_command = leader_action.get("explicit_command") or recommended_action.get("command")
    preview_command = _workbench_operator_preview_command(action_kind, target_id)
    return {
        "status": recovery.get("status"),
        "reason": recovery.get("reason"),
        "label": recommended_action.get("label"),
        "command": recommended_action.get("command"),
        "next_command": continue_card.get("next_command"),
        "safety": recommended_action.get("safety"),
        "requires_explicit_user": bool(recommended_action.get("requires_explicit_user")),
        "source": source,
        "target_id": target_id,
        "preview_command": preview_command,
        "controls": _workbench_operator_controls(
            preview_command=preview_command,
            apply_command=apply_command,
            explicit_command=explicit_command,
            safety=recommended_action.get("safety"),
            can_apply=can_apply,
            blocker=leader_action.get("apply_blocker"),
        ),
        "active_queue_source": active_queue_source,
        "action_kind": action_kind,
        "can_apply": can_apply,
        "apply_command": apply_command,
        "explicit_command": explicit_command,
        "blocker": leader_action.get("apply_blocker"),
    }


def _workbench_operator_controls(
    *,
    preview_command: object,
    apply_command: object,
    explicit_command: object,
    safety: object,
    can_apply: bool,
    blocker: object,
) -> list[dict[str, object]]:
    controls = [
        {
            "kind": "preview",
            "label": "Preview",
            "command": preview_command,
            "safety": "inspect",
            "enabled": preview_command is not None,
            "blocker": None,
        }
    ]
    if apply_command is not None or can_apply:
        controls.append(
            {
                "kind": "apply",
                "label": "Apply",
                "command": apply_command,
                "safety": safety,
                "enabled": can_apply and apply_command is not None,
                "blocker": blocker,
            }
        )
    controls.append(
        {
            "kind": "explicit",
            "label": "Run explicit command",
            "command": explicit_command,
            "safety": safety,
            "enabled": explicit_command is not None,
            "blocker": None,
        }
    )
    return controls


def _workbench_operator_preview_command(action_kind: str, target_id: object) -> str:
    if action_kind == "leader_action" and target_id:
        return f"agentdeck leader action --action-id {target_id}"
    if action_kind == "inbox" and target_id:
        return f"agentdeck trace --id {target_id}"
    if action_kind == "approval":
        return "agentdeck approval list"
    if action_kind == "provider_health":
        return "agentdeck doctor"
    if action_kind == "runtime":
        return "agentdeck agent refresh"
    return "agentdeck status"


def _workbench_ledger_card(project_view: dict[str, object]) -> dict[str, object]:
    messages = project_view.get("messages") if isinstance(project_view.get("messages"), dict) else {}
    jobs = project_view.get("jobs") if isinstance(project_view.get("jobs"), dict) else {}
    replies = project_view.get("replies") if isinstance(project_view.get("replies"), dict) else {}
    inbox = project_view.get("inbox") if isinstance(project_view.get("inbox"), dict) else {}
    trace_commands = _workbench_trace_commands(messages, jobs, replies, inbox)
    return {
        "messages": messages,
        "jobs": jobs,
        "replies": replies,
        "inbox": inbox,
        "trace_commands": trace_commands,
    }


def _workbench_trace_commands(*summaries: dict[str, object]) -> list[str]:
    commands: list[str] = []
    seen: set[str] = set()
    for summary in summaries:
        for command in _trace_commands_from_summary(summary):
            if command not in seen:
                commands.append(command)
                seen.add(command)
    return commands


def _trace_commands_from_summary(summary: dict[str, object]) -> list[str]:
    commands = []
    items = summary.get("items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("trace_command"):
                commands.append(str(item["trace_command"]))
    heads = summary.get("heads")
    if isinstance(heads, dict):
        for item in heads.values():
            if isinstance(item, dict) and item.get("inbox_id"):
                commands.append(_trace_command(item["inbox_id"]))
    return commands


def _workbench_runtime_card(project_view: dict[str, object]) -> dict[str, object]:
    agents = project_view.get("agents", [])
    runtime_agents = []
    by_status: dict[str, int] = {}
    if isinstance(agents, list):
        for agent in agents:
            if not isinstance(agent, dict):
                continue
            runtime = agent.get("runtime") if isinstance(agent.get("runtime"), dict) else {}
            agent_id = str(agent.get("agent_id"))
            status = str(runtime.get("status", "unknown"))
            by_status[status] = by_status.get(status, 0) + 1
            runtime_agents.append(
                {
                    "agent_id": agent_id,
                    "role": agent.get("role"),
                    "provider": agent.get("provider"),
                    "workspace_mode": agent.get("workspace_mode"),
                    "status": status,
                    "pane_id": runtime.get("pane_id"),
                    "session_name": runtime.get("session_name"),
                    "cwd": runtime.get("cwd"),
                    "spawn_command": f"agentdeck agent spawn --agent {agent_id}",
                    "stop_command": f"agentdeck agent stop --agent {agent_id}",
                    "capture_command": f"agentdeck agent capture --agent {agent_id} --lines 200",
                    "send_command_template": f"agentdeck agent send --agent {agent_id} --text <text>",
                    "inbox_command": f"agentdeck inbox --agent {agent_id}",
                    "controls": runtime_agent_controls(agent_id, status == "running"),
                }
            )
    return {
        "backend": project_view.get("runtime_backend"),
        "count": len(runtime_agents),
        "by_status": by_status,
        "refresh_command": "agentdeck agent refresh",
        "agents": runtime_agents,
    }


def continue_command(_args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    project_view = _project_view_payload_or_error(config, store)
    if project_view is None:
        return 1
    payload = _continue_card_payload(project_view, store)
    validation = validate_continue_contract(payload)
    if not validation["ok"]:
        print("Continue card contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1
    _print_json(payload)
    return 0


def workbench_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code

    if args.iterations is not None and args.iterations < 1:
        print("--iterations must be greater than 0", file=sys.stderr)
        return 1

    iteration = 0
    while True:
        project_view = _project_view_payload_or_error(config, store)
        if project_view is None:
            return 1
        payload = _workbench_snapshot_payload(project_view, store, since_event_id=args.since_event)
        validation = validate_workbench_contract(payload)
        if not validation["ok"]:
            print("Workbench contract validation failed", file=sys.stderr)
            for error in validation["errors"]:
                print(f"- {error}", file=sys.stderr)
            return 1
        if args.watch:
            _print_json_line(payload)
        else:
            _print_json(payload)
            return 0
        iteration += 1
        if args.iterations is not None and iteration >= args.iterations:
            return 0
        if args.interval > 0:
            try:
                time.sleep(args.interval)
            except KeyboardInterrupt:
                return 130
    return 0


def contract_project_view_command(args: argparse.Namespace) -> int:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "project-view-schema.md"
    payload = project_view_contract_response(contract_path, include_example=args.example)
    _print_json(payload)
    return 0


def contract_list_command(_args: argparse.Namespace) -> int:
    contract_docs_dir = Path(__file__).resolve().parents[2] / "docs" / "contracts"
    payload = contract_index_response(contract_docs_dir)
    _print_json(payload)
    return 0


def contract_leader_chat_command(args: argparse.Namespace) -> int:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "leader-chat-schema.md"
    payload = leader_chat_contract_response(contract_path, include_example=args.example)
    _print_json(payload)
    return 0


def contract_continue_command(args: argparse.Namespace) -> int:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "continue-card-schema.md"
    payload = continue_contract_response(contract_path, include_example=args.example)
    _print_json(payload)
    return 0


def contract_doctor_command(args: argparse.Namespace) -> int:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "doctor-schema.md"
    payload = doctor_contract_response(contract_path, include_example=args.example)
    _print_json(payload)
    return 0


def contract_events_command(args: argparse.Namespace) -> int:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "events-schema.md"
    payload = events_contract_response(contract_path, include_example=args.example)
    _print_json(payload)
    return 0


def contract_workbench_command(args: argparse.Namespace) -> int:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "workbench-schema.md"
    payload = workbench_contract_response(contract_path, include_example=args.example)
    _print_json(payload)
    return 0


def contract_agent_runtime_command(args: argparse.Namespace) -> int:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "agent-runtime-schema.md"
    payload = agent_runtime_contract_response(contract_path, include_example=args.example)
    _print_json(payload)
    return 0


def contract_approvals_command(args: argparse.Namespace) -> int:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "approvals-schema.md"
    payload = approval_contract_response(contract_path, include_example=args.example)
    _print_json(payload)
    return 0


def contract_inbox_command(args: argparse.Namespace) -> int:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "inbox-schema.md"
    payload = inbox_contract_response(contract_path, include_example=args.example)
    _print_json(payload)
    return 0


def contract_leader_action_command(args: argparse.Namespace) -> int:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "leader-action-schema.md"
    payload = leader_action_contract_response(contract_path, include_example=args.example)
    _print_json(payload)
    return 0


def contract_leader_actions_command(args: argparse.Namespace) -> int:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "leader-actions-schema.md"
    payload = leader_actions_contract_response(contract_path, include_example=args.example)
    _print_json(payload)
    return 0


def contract_leader_review_command(args: argparse.Namespace) -> int:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "leader-review-schema.md"
    payload = leader_review_contract_response(contract_path, include_example=args.example)
    _print_json(payload)
    return 0


def contract_trace_command(args: argparse.Namespace) -> int:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "trace-schema.md"
    payload = trace_contract_response(contract_path, include_example=args.example)
    _print_json(payload)
    return 0


def _load_project_or_error() -> tuple[ProjectConfig | None, StateStore | None, int]:
    root = project_root()
    try:
        config = load_config(root)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        print("Run: conda activate agentdeck && agentdeck project init", file=sys.stderr)
        return None, None, 1
    return config, StateStore(root), 0


def _agent_by_id(config: ProjectConfig, agent_id: str) -> AgentSpec | None:
    for agent in config.agents:
        if agent.agent_id == agent_id:
            return agent
    return None


def agent_list_command(_args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    _print_json(asdict(store.project_view(config)))
    return 0


def agent_spawn_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    agent = _agent_by_id(config, args.agent)
    if agent is None:
        print(f"unknown agent: {args.agent}", file=sys.stderr)
        return 1
    existing = store.agent_binding(agent.agent_id)
    if existing and existing.get("pane_id") and existing.get("status") == "running":
        print(f"agent already running: {agent.agent_id} pane={existing['pane_id']}", file=sys.stderr)
        return 1

    backend = TmuxBackend()
    backend.create_session(config.runtime)
    pane_id = backend.spawn_agent(config.runtime, agent, config.root)
    binding = AgentRuntimeBinding(
        agent_id=agent.agent_id,
        pane_id=pane_id,
        session_name=config.runtime.session_name,
        cwd=config.root,
        status="running",
    )
    store.bind_agent(binding)
    store.append_event(
        EventRecord.create(
            "agent_spawned",
            {
                "agent_id": agent.agent_id,
                "pane_id": pane_id,
                "session_name": config.runtime.session_name,
                "cwd": config.root,
            },
        )
    )
    _print_json(asdict(binding))
    return 0


def _running_binding_or_error(store: StateStore, agent_id: str) -> tuple[dict[str, object] | None, int]:
    binding = store.agent_binding(agent_id)
    if not binding or not binding.get("pane_id"):
        print(f"agent is not spawned: {agent_id}", file=sys.stderr)
        return None, 1
    return binding, 0


def agent_capture_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    binding, exit_code = _running_binding_or_error(store, args.agent)
    if binding is None:
        return exit_code
    pane_id = str(binding["pane_id"])
    output = TmuxBackend().capture_output(config.runtime, pane_id, args.lines)
    _print_json({"agent_id": args.agent, "pane_id": pane_id, "output": output})
    return 0


def agent_send_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    binding, exit_code = _running_binding_or_error(store, args.agent)
    if binding is None:
        return exit_code
    pane_id = str(binding["pane_id"])
    TmuxBackend().send_input(config.runtime, pane_id, args.text)
    store.append_event(
        EventRecord.create(
            "agent_input_sent",
            {
                "agent_id": args.agent,
                "pane_id": pane_id,
                "text_length": len(args.text),
            },
        )
    )
    _print_json({"ok": True, "agent_id": args.agent, "pane_id": pane_id})
    return 0


def agent_refresh_command(_args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    backend = TmuxBackend()
    agents = []
    stale_count = 0
    running_count = 0
    for agent in config.agents:
        binding = store.agent_binding(agent.agent_id) or {}
        previous_status = str(binding.get("status", "configured"))
        pane_id = binding.get("pane_id")
        pane_exists = None
        status = previous_status
        changed = False
        if pane_id and previous_status == "running":
            pane_exists = backend.pane_exists(config.runtime, str(pane_id))
            if pane_exists:
                running_count += 1
            else:
                stale_count += 1
                status = "stale"
                changed = True
                store.mark_agent_stale(agent.agent_id)
                store.append_event(
                    EventRecord.create(
                        "agent_runtime_stale",
                        {
                            "agent_id": agent.agent_id,
                            "pane_id": pane_id,
                            "previous_status": previous_status,
                        },
                    )
                )
        agents.append(
            {
                "agent_id": agent.agent_id,
                "previous_status": previous_status,
                "status": status,
                "pane_id": pane_id,
                "pane_exists": pane_exists,
                "changed": changed,
            }
        )
    _print_json({"ok": True, "agents": agents, "stale_count": stale_count, "running_count": running_count})
    return 0


def agent_stop_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    binding, exit_code = _running_binding_or_error(store, args.agent)
    if binding is None:
        return exit_code
    pane_id = str(binding["pane_id"])
    TmuxBackend().kill_pane(config.runtime, pane_id)
    store.mark_agent_stopped(args.agent)
    store.append_event(
        EventRecord.create(
            "agent_stopped",
            {
                "agent_id": args.agent,
                "pane_id": pane_id,
            },
        )
    )
    _print_json({"ok": True, "agent_id": args.agent, "pane_id": pane_id, "status": "stopped"})
    return 0


def agent_assign_role_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    if _agent_by_id(config, args.agent) is None:
        print(f"unknown agent: {args.agent}", file=sys.stderr)
        return 1
    updated = update_agent_role(project_root(), args.agent, args.role, args.role_prompt)
    store.append_event(
        EventRecord.create(
            "agent_role_assigned",
            {
                "agent_id": updated.agent_id,
                "role": updated.role,
                "role_prompt": updated.role_prompt,
            },
        )
    )
    _print_json(
        {
            "ok": True,
            "agent_id": updated.agent_id,
            "role": updated.role,
            "role_prompt": updated.role_prompt,
        }
    )
    return 0


def build_dispatch_prompt(agent: AgentSpec, task: str) -> str:
    return "\n".join(
        [
            "# AgentDeck dispatch",
            "",
            f"Agent: {agent.agent_id}",
            f"Provider: {agent.provider}",
            f"角色: {agent.role}",
            "",
            "角色说明:",
            agent.role_prompt or "请按该 agent 的配置角色完成任务。",
            "",
            "当前任务:",
            task,
            "",
            "请按以下格式返回:",
            "status: completed | blocked | failed",
            "summary:",
            "files_read:",
            "files_written:",
            "verification:",
            "risks:",
            "next_steps:",
        ]
    )


def dispatch_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    agent = _agent_by_id(config, args.agent)
    if agent is None:
        print(f"unknown agent: {args.agent}", file=sys.stderr)
        return 1
    binding, exit_code = _running_binding_or_error(store, args.agent)
    if binding is None:
        return exit_code
    pane_id = str(binding["pane_id"])
    prompt = build_dispatch_prompt(agent, args.task)
    records = store.create_dispatch_records(args.from_agent, agent.agent_id, args.task, prompt, pane_id)
    message = records["message"]
    TmuxBackend().send_input(config.runtime, pane_id, prompt)
    store.append_event(
        EventRecord.create(
            "task_dispatched",
            {
                "message_id": message["message_id"],
                "to_agent": agent.agent_id,
                "pane_id": pane_id,
                "task_length": len(args.task),
            },
        )
    )
    _print_json(
        {
            "ok": True,
            "message_id": message["message_id"],
            "agent_id": agent.agent_id,
            "pane_id": pane_id,
            "trace_command": _trace_command(message["message_id"]),
        }
    )
    return 0


def inbox_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    if _agent_by_id(config, args.agent) is None:
        print(f"unknown agent: {args.agent}", file=sys.stderr)
        return 1
    payload = _inbox_queue_payload(args.agent, store)
    validation = validate_inbox_contract(payload)
    if not validation["ok"]:
        print("Inbox contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1
    _print_json(payload)
    return 0


def _inbox_queue_payload(agent_id: str, store: StateStore) -> dict[str, object]:
    raw_items = store.inbox_items(agent_id)
    head = next((item for item in raw_items if item.get("status") == "pending"), None)
    head_inbox_id = head.get("inbox_id") if isinstance(head, dict) else None
    items = [_inbox_queue_item(agent_id, item, head_inbox_id) for item in raw_items]
    return {"agent_id": agent_id, "count": len(items), "head_inbox_id": head_inbox_id, "items": items}


def _inbox_queue_item(agent_id: str, item: dict[str, object], head_inbox_id: object) -> dict[str, object]:
    inbox_id = item.get("inbox_id")
    is_head = inbox_id == head_inbox_id
    can_ack = item.get("status") == "pending" and is_head
    return {
        "inbox_id": inbox_id,
        "event_type": item.get("event_type"),
        "message_id": item.get("message_id"),
        "attempt_id": item.get("attempt_id"),
        "job_id": item.get("job_id"),
        "reply_id": item.get("reply_id"),
        "from_actor": item.get("from_actor"),
        "from_agent": item.get("from_agent"),
        "to_agent": item.get("to_agent"),
        "task": item.get("task"),
        "status": item.get("status"),
        "created_at": item.get("created_at"),
        "preview_command": _trace_command(inbox_id),
        "controls": _inbox_item_controls(agent_id, str(inbox_id), can_ack),
        "trace_command": _trace_command(inbox_id),
        "ack_command": f"agentdeck ack --agent {agent_id} --inbox-id {inbox_id}",
        "is_head": is_head,
        "can_ack": can_ack,
        "ack_blocker": None if can_ack else "inbox item is not head",
    }


def reply_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    if _agent_by_id(config, args.agent) is None:
        print(f"unknown agent: {args.agent}", file=sys.stderr)
        return 1
    try:
        reply = store.record_reply(args.agent, args.message_id, args.text)
    except KeyError:
        print(f"unknown message: {args.message_id}", file=sys.stderr)
        return 1
    store.append_event(
        EventRecord.create(
            "task_replied",
            {
                "reply_id": reply["reply_id"],
                "message_id": reply["message_id"],
                "from_agent": args.agent,
            },
        )
    )
    _print_json(
        {
            "ok": True,
            "reply_id": reply["reply_id"],
            "message_id": reply["message_id"],
            "from_agent": args.agent,
            "trace_command": _trace_command(reply["reply_id"]),
        }
    )
    return 0


def _extract_structured_reply(output: str) -> str | None:
    lines = output.splitlines()
    start_index = None
    for index, line in enumerate(lines):
        if line.strip().startswith("status:"):
            start_index = index
    if start_index is None:
        return None
    return "\n".join(lines[start_index:]).strip()


def capture_reply_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    if _agent_by_id(config, args.agent) is None:
        print(f"unknown agent: {args.agent}", file=sys.stderr)
        return 1
    binding, exit_code = _running_binding_or_error(store, args.agent)
    if binding is None:
        return exit_code
    pane_id = str(binding["pane_id"])
    output = TmuxBackend().capture_output(config.runtime, pane_id, args.lines)
    text = _extract_structured_reply(output)
    if text is None:
        print(f"no structured reply found for agent: {args.agent}", file=sys.stderr)
        return 1
    try:
        reply = store.record_reply(args.agent, args.message_id, text)
    except KeyError:
        print(f"unknown message: {args.message_id}", file=sys.stderr)
        return 1
    store.append_event(
        EventRecord.create(
            "reply_captured",
            {
                "reply_id": reply["reply_id"],
                "message_id": reply["message_id"],
                "from_agent": args.agent,
                "pane_id": pane_id,
                "captured_lines": len(text.splitlines()),
            },
        )
    )
    _print_json(
        {
            "ok": True,
            "reply_id": reply["reply_id"],
            "message_id": reply["message_id"],
            "from_agent": args.agent,
            "pane_id": pane_id,
            "captured_lines": len(text.splitlines()),
            "trace_command": _trace_command(reply["reply_id"]),
        }
    )
    return 0


def ack_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    if _agent_by_id(config, args.agent) is None:
        print(f"unknown agent: {args.agent}", file=sys.stderr)
        return 1
    try:
        item = store.ack_inbox_item(args.agent, args.inbox_id)
    except KeyError:
        print(f"unknown inbox item: {args.inbox_id}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    store.append_event(
        EventRecord.create(
            "inbox_item_acked",
            {
                "agent_id": args.agent,
                "inbox_id": args.inbox_id,
                "event_type": item.get("event_type"),
            },
        )
    )
    _print_json({"ok": True, "agent_id": args.agent, "inbox_id": args.inbox_id, "status": "acked"})
    return 0


def trace_command(args: argparse.Namespace) -> int:
    _config, store, exit_code = _load_project_or_error()
    if store is None:
        return exit_code
    try:
        trace = store.trace(args.id)
    except KeyError:
        print(f"unknown trace id: {args.id}", file=sys.stderr)
        return 1
    validation = validate_trace_contract(trace)
    if not validation["ok"]:
        print("Trace contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1
    _print_json(trace)
    return 0


def _record_leader_provider_failure(
    store: StateStore,
    mode: str,
    provider: str,
    model: str | None,
    task: str,
    error: Exception,
) -> None:
    record = store.record_leader_error(mode, provider, model, task, str(error))
    store.append_event(
        EventRecord.create(
            "leader_provider_failed",
            {
                "error_id": record["error_id"],
                "mode": mode,
                "provider": provider,
                "model": model,
                "task_length": len(task),
                "error": str(error),
            },
        )
    )


def _leader_provider_name(config: ProjectConfig, requested_provider: str | None) -> str:
    if requested_provider:
        return requested_provider
    return config.leader.provider


def _leader_model_label(config: ProjectConfig, requested_model: str | None) -> str:
    if requested_model:
        return requested_model
    return config.leader.model


def leader_plan_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    provider_name = _leader_provider_name(config, args.provider)
    model_label = _leader_model_label(config, args.model)
    try:
        provider = leader_provider(provider_name)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    orchestrator = LeaderOrchestrator(config, provider)
    try:
        plan = orchestrator.plan(args.task)
    except RuntimeError as exc:
        _record_leader_provider_failure(store, "plan", provider.name, model_label, args.task, exc)
        print(f"leader provider failed: {exc}", file=sys.stderr)
        return 1
    record = store.record_plan(args.task, provider.name, model_label, plan)
    store.append_event(
        EventRecord.create(
            "leader_plan_created",
            {
                "plan_id": record["plan_id"],
                "provider": record["provider"],
                "model": record["model"],
                "task_length": len(args.task),
            },
        )
    )
    _print_json(
        {
            "ok": True,
            "plan_id": record["plan_id"],
            "status": record["status"],
            "provider": record["provider"],
            "model": record["model"],
            "dispatch_ready": record["dispatch_ready"],
            "plan": record["plan"],
        }
    )
    return 0


def leader_review_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    if _project_view_payload_or_error(config, store) is None:
        return 1
    try:
        review = store.leader_review(args.plan_id)
    except KeyError:
        print(f"unknown plan: {args.plan_id}", file=sys.stderr)
        return 1
    payload = _leader_review_payload(review)
    validation = validate_leader_review_contract(payload)
    if not validation["ok"]:
        print("Leader review contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1
    _print_json(payload)
    return 0


def _leader_review_payload(review: dict[str, object]) -> dict[str, object]:
    next_command = _leader_review_next_command(review)
    return {
        **review,
        "approval_id": review.get("approval_id"),
        "agent_id": review.get("agent_id"),
        "message_id": review.get("message_id"),
        "replies": review.get("replies", []),
        "next_command": next_command,
        "controls": _leader_review_controls(review, next_command),
    }


def _leader_review_next_command(review: dict[str, object]) -> str | None:
    next_action = review.get("next_action")
    if next_action == "dispatch_approved" and review.get("approval_id"):
        return f"agentdeck approval dispatch --approval-id {review['approval_id']}"
    if next_action == "wait_for_reply" and review.get("agent_id") and review.get("message_id"):
        return f"agentdeck capture-reply --agent {review['agent_id']} --message-id {review['message_id']}"
    if next_action == "summarize" and review.get("plan_id"):
        return f"agentdeck plan status --plan-id {review['plan_id']}"
    if next_action == "wait_for_approval":
        return "agentdeck approval list"
    return None


def _leader_review_controls(review: dict[str, object], next_command: str | None) -> list[dict[str, object]]:
    controls: list[dict[str, object]] = []
    message_id = review.get("message_id")
    if message_id:
        controls.append(
            _control(
                kind="preview",
                label="Preview message lineage",
                command=_trace_command(message_id),
                safety="inspect",
            )
        )
    if review.get("next_action") == "wait_for_reply":
        controls.append(
            _control(
                kind="capture_reply",
                label="Capture reply",
                command=next_command,
                safety="explicit_runtime",
                enabled=next_command is not None,
                blocker=None if next_command is not None else "capture reply command unavailable",
            )
        )
    elif next_command is not None:
        controls.append(
            _control(
                kind="next",
                label="Next command",
                command=next_command,
                safety="inspect" if review.get("next_action") in {"summarize", "wait_for_approval"} else "explicit_runtime",
            )
        )
    return controls


def leader_next_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    if _project_view_payload_or_error(config, store) is None:
        return 1
    try:
        action = store.suggest_leader_action(args.plan_id)
    except KeyError as exc:
        print(f"unknown plan: {exc.args[0]}", file=sys.stderr)
        return 1
    store.append_event(
        EventRecord.create(
            "leader_action_suggested",
            {
                "action_id": action["action_id"],
                "kind": action["kind"],
                "plan_id": action["plan_id"],
                "command": action["command"],
            },
        )
    )
    _print_json({"ok": True, **action})
    return 0


def _leader_action_summary(action: dict[str, object], recommended_action_id: object = None) -> dict[str, object]:
    detail_fields = StateStore._leader_action_detail_fields(action)
    return {
        "action_id": action.get("action_id"),
        "kind": action.get("kind"),
        "status": action.get("status"),
        "requires_confirmation": action.get("requires_confirmation"),
        "plan_id": action.get("plan_id"),
        "approval_id": action.get("approval_id"),
        "agent_id": action.get("agent_id"),
        "message_id": action.get("message_id"),
        "command": action.get("command"),
        "reason": action.get("reason"),
        "created_at": action.get("created_at"),
        "can_apply": detail_fields["can_apply"],
        "preview_command": detail_fields["preview_command"],
        "controls": detail_fields["controls"],
        "apply_command": detail_fields["apply_command"],
        "explicit_command": detail_fields["explicit_command"],
        "apply_blocker": detail_fields["apply_blocker"],
        "is_recommended": action.get("action_id") == recommended_action_id,
    }


def leader_actions_command(_args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    project_view = _project_view_payload_or_error(config, store)
    if project_view is None:
        return 1
    recovery = project_view.get("recovery", {})
    recommended_action = recovery.get("recommended_action") if isinstance(recovery, dict) else None
    recommended_action_id = recommended_action.get("target_id") if isinstance(recommended_action, dict) else None
    actions = [_leader_action_summary(action, recommended_action_id) for action in store.list_leader_actions()]
    payload = {
        "count": len(actions),
        "recommended_action_id": recommended_action_id,
        "actions": actions,
    }
    validation = validate_leader_actions_contract(payload)
    if not validation["ok"]:
        print("Leader actions contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1
    _print_json(payload)
    return 0


def leader_action_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    project_view = _project_view_payload_or_error(config, store)
    if project_view is None:
        return 1
    try:
        action = store.leader_action_detail(args.action_id)
    except KeyError:
        print(f"unknown leader action: {args.action_id}", file=sys.stderr)
        return 1
    recovery = project_view.get("recovery", {})
    recommended_action = recovery.get("recommended_action") if isinstance(recovery, dict) else None
    matches_recommended_action = (
        isinstance(recommended_action, dict) and recommended_action.get("target_id") == args.action_id
    )
    payload = {
        **action,
        "recovery": recovery,
        "recommended_action": recommended_action,
        "matches_recommended_action": matches_recommended_action,
    }
    validation = validate_leader_action_contract(payload)
    if not validation["ok"]:
        print("Leader action contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1
    _print_json(payload)
    return 0


def leader_apply_action_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    if _project_view_payload_or_error(config, store) is None:
        return 1
    try:
        applied = store.apply_leader_action(args.action_id)
    except KeyError:
        print(f"unknown leader action: {args.action_id}", file=sys.stderr)
        return 1
    except (PermissionError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    action = applied["action"]
    result = applied["result"]
    store.append_event(
        EventRecord.create(
            "leader_action_applied",
            {
                "action_id": action["action_id"],
                "kind": action["kind"],
                "plan_id": action["plan_id"],
                "result_count": result.get("count"),
            },
        )
    )
    _print_json({"ok": True, **_leader_action_summary(action), "result": result})
    return 0


def _next_command_for_review(review: dict[str, object]) -> str | None:
    action = review.get("next_action")
    if action == "dispatch_approved" and review.get("approval_id"):
        return f"agentdeck approval dispatch --approval-id {review['approval_id']}"
    if action == "wait_for_reply" and review.get("agent_id") and review.get("message_id"):
        return f"agentdeck capture-reply --agent {review['agent_id']} --message-id {review['message_id']}"
    if action == "wait_for_approval" and review.get("plan_id"):
        return f"agentdeck approval create-from-plan --plan-id {review['plan_id']}"
    if action == "summarize" and review.get("plan_id"):
        return f"agentdeck plan status --plan-id {review['plan_id']}"
    return None


def _chat_apply_action_id(message: str) -> str | None:
    stripped = message.strip()
    lowered = stripped.lower()
    for prefix in ["/apply-action ", "apply action "]:
        if lowered.startswith(prefix):
            return stripped[len(prefix) :].strip()
    for prefix in ["应用 action ", "应用action "]:
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].strip()
    return None


def _is_continue_chat_message(message: str) -> bool:
    normalized = message.strip().lower()
    return normalized in {"继续", "继续吧", "/continue", "continue"}


def _chat_wants_help(message: str) -> bool:
    normalized = message.strip().lower()
    return normalized in {
        "help",
        "/help",
        "?",
        "？",
        "帮助",
        "你能做什么",
        "能做什么",
        "有哪些能力",
        "查看能力",
        "命令面板",
        "commands",
        "capabilities",
    }


def _chat_wants_setup(message: str) -> bool:
    normalized = message.strip().lower()
    return any(
        token in normalized
        for token in [
            "doctor",
            "诊断",
            "检查",
            "setup",
            "provider",
            "api key",
            "apikey",
            "环境变量",
            "不能调度",
            "为什么不能",
        ]
    )


def _chat_wants_workbench(message: str) -> bool:
    normalized = message.strip().lower()
    return any(
        token in normalized
        for token in [
            "workbench",
            "dashboard",
            "overview",
            "control plane",
            "工作台",
            "总览",
            "仪表盘",
            "控制台",
            "全局状态",
        ]
    )


def _chat_wants_runtime(message: str) -> bool:
    normalized = message.strip().lower()
    return any(
        token in normalized
        for token in [
            "runtime",
            "tmux",
            "terminal",
            "pane",
            "agent list",
            "终端",
            "面板",
            "运行时",
            "查看 agent",
            "查看agents",
            "查看智能体",
        ]
    )


def _chat_wants_queue(message: str) -> bool:
    normalized = message.strip().lower()
    return any(
        token in normalized
        for token in [
            "queue",
            "operator",
            "actions",
            "action queue",
            "队列",
            "操作面",
            "控制面",
            "下一步按钮",
            "主操作",
        ]
    )


def _chat_wants_role(message: str) -> bool:
    normalized = message.strip().lower()
    return any(
        token in normalized
        for token in [
            "role",
            "roles",
            "role card",
            "assign-role",
            "角色",
            "角色卡",
            "分工",
            "职责",
            "人设",
        ]
    )


def _chat_wants_ledger(message: str) -> bool:
    normalized = message.strip().lower()
    return any(
        token in normalized
        for token in [
            "ledger",
            "message ledger",
            "trace commands",
            "账本",
            "消息账本",
            "通信账本",
            "通信",
            "链路",
            "追踪命令",
        ]
    )


def _chat_inbox_agent_id(message: str, config: ProjectConfig) -> str | None:
    normalized = message.strip().lower()
    mentions_inbox = any(token in normalized for token in ["inbox", "收件箱", "消息", "mailbox"])
    if not mentions_inbox:
        return None
    for agent in config.agents:
        if agent.agent_id.lower() in normalized:
            return agent.agent_id
    return None


def _chat_wants_inbox_trace(message: str) -> bool:
    normalized = message.strip().lower()
    return any(token in normalized for token in ["trace", "追踪", "溯源", "lineage"])


def _chat_wants_inbox_ack(message: str) -> bool:
    normalized = message.strip().lower()
    return any(token in normalized for token in ["ack", "acknowledge", "确认", "标记已读", "已处理"])


def _chat_wants_approval(message: str) -> bool:
    normalized = message.strip().lower()
    return any(token in normalized for token in ["approval", "approve", "审批", "批准"])


def _chat_wants_approval_approve(message: str) -> bool:
    normalized = message.strip().lower()
    return any(token in normalized for token in ["approve", "批准", "同意", "通过审批"])


def _chat_wants_approval_reject(message: str) -> bool:
    normalized = message.strip().lower()
    return any(token in normalized for token in ["reject", "拒绝", "驳回", "否决"])


def _chat_wants_approval_dispatch(message: str) -> bool:
    normalized = message.strip().lower()
    return any(token in normalized for token in ["dispatch", "派发", "发送", "执行审批"])


def _pending_approval_item(approval_card: dict[str, object]) -> dict[str, object] | None:
    approvals = approval_card.get("approvals")
    if not isinstance(approvals, list):
        return None
    for approval in approvals:
        if isinstance(approval, dict) and approval.get("status") == "pending":
            return approval
    return None


def _approved_approval_item(approval_card: dict[str, object]) -> dict[str, object] | None:
    approvals = approval_card.get("approvals")
    if not isinstance(approvals, list):
        return None
    for approval in approvals:
        if isinstance(approval, dict) and approval.get("status") == "approved":
            return approval
    return None


def _inbox_head_item(inbox_card: dict[str, object]) -> dict[str, object] | None:
    head_inbox_id = inbox_card.get("head_inbox_id")
    items = inbox_card.get("items")
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict) and item.get("inbox_id") == head_inbox_id:
            return item
    return None


def _leader_chat_explanation(
    mode: str,
    *,
    next_command: object,
    project_view: dict[str, object],
    leader_action: dict[str, object] | None = None,
    review: dict[str, object] | None = None,
    result: dict[str, object] | None = None,
    inbox_card: dict[str, object] | None = None,
    inbox_action_kind: str | None = None,
    approval_card: dict[str, object] | None = None,
    approval_action_kind: str | None = None,
) -> dict[str, object]:
    recovery = project_view.get("recovery")
    recovery_action = recovery.get("recommended_action") if isinstance(recovery, dict) else None
    action_kind = leader_action.get("kind") if isinstance(leader_action, dict) else None
    action_status = leader_action.get("status") if isinstance(leader_action, dict) else None
    reason = None
    if isinstance(review, dict):
        reason = review.get("reason")
    if reason is None and isinstance(leader_action, dict):
        reason = leader_action.get("reason")
    if mode == "plan":
        return {
            "mode": mode,
            "summary": f"Leader created a plan-only record and recommends {action_kind} as the safe next step.",
            "reason": "no existing plan was available for review",
            "next_command": next_command,
            "recommended_action_id": recovery_action.get("target_id") if isinstance(recovery_action, dict) else None,
            "action_kind": action_kind,
            "action_status": action_status,
            "safety": recovery_action.get("safety") if isinstance(recovery_action, dict) else "plan_only",
            "requires_explicit_user": recovery_action.get("requires_explicit_user")
            if isinstance(recovery_action, dict)
            else True,
        }
    if mode == "apply_action":
        result_count = result.get("count") if isinstance(result, dict) else None
        return {
            "mode": mode,
            "summary": f"Leader applied safe action {action_kind} and created {result_count} approval records.",
            "reason": reason,
            "next_command": next_command,
            "recommended_action_id": None,
            "action_kind": action_kind,
            "action_status": action_status,
            "safety": "safe_apply_completed",
            "requires_explicit_user": False,
            "result_count": result_count,
        }
    if mode == "continue":
        return {
            "mode": mode,
            "summary": f"Leader is continuing from ProjectView recovery status {recovery.get('status') if isinstance(recovery, dict) else None}.",
            "reason": recovery.get("reason") if isinstance(recovery, dict) else None,
            "next_command": next_command,
            "recommended_action_id": recovery_action.get("target_id") if isinstance(recovery_action, dict) else None,
            "action_kind": recovery_action.get("source") if isinstance(recovery_action, dict) else None,
            "action_status": recovery.get("status") if isinstance(recovery, dict) else None,
            "safety": recovery_action.get("safety") if isinstance(recovery_action, dict) else None,
            "requires_explicit_user": recovery_action.get("requires_explicit_user")
            if isinstance(recovery_action, dict)
            else None,
        }
    if mode == "setup":
        leader = project_view.get("leader") if isinstance(project_view.get("leader"), dict) else {}
        provider = leader.get("provider")
        return {
            "mode": mode,
            "summary": "Leader recommends inspecting provider setup before planning or dispatching work.",
            "reason": "human asked to inspect Leader provider setup",
            "next_command": next_command,
            "recommended_action_id": provider,
            "action_kind": "provider_health",
            "action_status": recovery.get("status") if isinstance(recovery, dict) else None,
            "safety": "inspect",
            "requires_explicit_user": False,
        }
    if mode == "help":
        return {
            "mode": mode,
            "summary": "Leader is showing the local capability map for chat and GUI command discovery.",
            "reason": "human asked what the Leader chat surface can do",
            "next_command": next_command,
            "recommended_action_id": None,
            "action_kind": "help",
            "action_status": "ready",
            "safety": "inspect",
            "requires_explicit_user": False,
        }
    if mode == "runtime":
        runtime_card = _workbench_runtime_card(project_view)
        by_status = runtime_card.get("by_status") if isinstance(runtime_card.get("by_status"), dict) else {}
        action_status = "empty"
        if by_status:
            action_status = "running" if by_status.get("running") else next(iter(by_status))
        return {
            "mode": mode,
            "summary": "Leader recommends inspecting the visible tmux runtime without mutating runtime state.",
            "reason": "human asked to inspect agent runtime bindings",
            "next_command": next_command,
            "recommended_action_id": None,
            "action_kind": "runtime",
            "action_status": action_status,
            "safety": "inspect",
            "requires_explicit_user": False,
        }
    if mode == "workbench":
        return {
            "mode": mode,
            "summary": "Leader recommends opening the unified workbench snapshot without mutating state.",
            "reason": "human asked to inspect the full local control plane",
            "next_command": next_command,
            "recommended_action_id": None,
            "action_kind": "workbench",
            "action_status": "ready",
            "safety": "inspect",
            "requires_explicit_user": False,
        }
    if mode == "queue":
        recovery = project_view.get("recovery") if isinstance(project_view.get("recovery"), dict) else {}
        recovery_action = recovery.get("recommended_action") if isinstance(recovery.get("recommended_action"), dict) else {}
        return {
            "mode": mode,
            "summary": "Leader recommends inspecting the active queue and operator controls without applying actions.",
            "reason": "human asked to inspect the queue control surface",
            "next_command": next_command,
            "recommended_action_id": recovery_action.get("target_id"),
            "action_kind": recovery_action.get("source"),
            "action_status": recovery.get("status"),
            "safety": recovery_action.get("safety"),
            "requires_explicit_user": bool(recovery_action.get("requires_explicit_user")),
        }
    if mode == "role":
        role_card = _workbench_role_card(project_view)
        return {
            "mode": mode,
            "summary": "Leader recommends inspecting configured agent roles without mutating role assignments.",
            "reason": "human asked to inspect role assignments",
            "next_command": next_command,
            "recommended_action_id": None,
            "action_kind": "role",
            "action_status": "configured" if role_card.get("count") else "empty",
            "safety": "inspect",
            "requires_explicit_user": False,
        }
    if mode == "ledger":
        ledger_card = _workbench_ledger_card(project_view)
        trace_commands = ledger_card.get("trace_commands") if isinstance(ledger_card.get("trace_commands"), list) else []
        recommended_action_id = None
        if trace_commands:
            first_command = str(trace_commands[0])
            recommended_action_id = first_command.rsplit(" ", 1)[-1]
        return {
            "mode": mode,
            "summary": "Leader recommends inspecting the communication ledger without mutating messages or runtime state.",
            "reason": "human asked to inspect message lineage",
            "next_command": next_command,
            "recommended_action_id": recommended_action_id,
            "action_kind": "ledger",
            "action_status": "has_traces" if trace_commands else "empty",
            "safety": "inspect",
            "requires_explicit_user": False,
        }
    if mode == "inbox":
        head = _inbox_head_item(inbox_card) if isinstance(inbox_card, dict) else None
        head_inbox_id = head.get("inbox_id") if isinstance(head, dict) else None
        safety = "explicit_runtime" if inbox_action_kind == "inbox_ack" else "inspect"
        return {
            "mode": mode,
            "summary": f"Leader recommends inspecting {inbox_card.get('agent_id') if isinstance(inbox_card, dict) else None} inbox without mutating runtime state.",
            "reason": "human asked to inspect an agent inbox",
            "next_command": next_command,
            "recommended_action_id": head_inbox_id,
            "action_kind": inbox_action_kind or "inbox",
            "action_status": head.get("status") if isinstance(head, dict) else "empty",
            "safety": safety,
            "requires_explicit_user": safety != "inspect",
        }
    if mode == "approval":
        pending = _pending_approval_item(approval_card) if isinstance(approval_card, dict) else None
        approved = _approved_approval_item(approval_card) if isinstance(approval_card, dict) else None
        selected = approved if approval_action_kind == "approval_dispatch" else pending
        approval_id = selected.get("approval_id") if isinstance(selected, dict) else None
        safety = (
            "explicit_runtime"
            if approval_action_kind in {"approval_approve", "approval_reject", "approval_dispatch"}
            else "inspect"
        )
        return {
            "mode": mode,
            "summary": "Leader recommends inspecting the approval queue without mutating runtime state.",
            "reason": "human asked to inspect or decide pending approvals",
            "next_command": next_command,
            "recommended_action_id": approval_id,
            "action_kind": approval_action_kind or "approval",
            "action_status": selected.get("status") if isinstance(selected, dict) else "empty",
            "safety": safety,
            "requires_explicit_user": safety != "inspect",
        }
    return {
        "mode": mode,
        "summary": f"Leader recommends {action_kind} because {reason}.",
        "reason": reason,
        "next_command": next_command,
        "recommended_action_id": recovery_action.get("target_id") if isinstance(recovery_action, dict) else None,
        "action_kind": action_kind,
        "action_status": action_status,
        "safety": recovery_action.get("safety") if isinstance(recovery_action, dict) else None,
        "requires_explicit_user": recovery_action.get("requires_explicit_user")
        if isinstance(recovery_action, dict)
        else None,
    }


def leader_chat_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    project_view = _project_view_payload_or_error(config, store)
    if project_view is None:
        return 1
    apply_action_id = _chat_apply_action_id(args.message)
    if apply_action_id:
        try:
            applied = store.apply_leader_action(apply_action_id)
        except KeyError:
            print(f"unknown leader action: {apply_action_id}", file=sys.stderr)
            return 1
        except (PermissionError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        action = applied["action"]
        result = applied["result"]
        action_detail = store.leader_action_detail(str(action["action_id"]))
        preview_project_view = _project_view_payload_or_error(config, store)
        if preview_project_view is None:
            return 1
        preview_recovery = preview_project_view.get("recovery", {})
        next_command = preview_recovery.get("next_command") if isinstance(preview_recovery, dict) else None
        turn = store.record_chat_turn(
            mode="apply_action",
            message=args.message,
            plan_id=str(action.get("plan_id")),
            next_command=next_command,
            review=None,
            action_id=str(action["action_id"]),
            action_kind=str(action["kind"]),
        )
        store.append_event(
            EventRecord.create(
                "leader_action_applied",
                {
                    "action_id": action["action_id"],
                    "kind": action["kind"],
                    "plan_id": action["plan_id"],
                    "result_count": result.get("count"),
                },
            )
        )
        store.append_event(
            EventRecord.create(
                "leader_chat_turn",
                {
                    "turn_id": turn["turn_id"],
                    "mode": "apply_action",
                    "plan_id": action.get("plan_id"),
                    "message_length": len(args.message),
                },
            )
        )
        refreshed_project_view = _project_view_payload_or_error(config, store)
        if refreshed_project_view is None:
            return 1
        payload = {
            "ok": True,
            "turn_id": turn["turn_id"],
            "mode": "apply_action",
            "message": args.message,
            "project_view": refreshed_project_view,
            "leader_actions": refreshed_project_view.get("leader_actions"),
            "leader_explanation": _leader_chat_explanation(
                "apply_action",
                next_command=next_command,
                project_view=refreshed_project_view,
                leader_action=action_detail,
                result=result,
            ),
            "plan_id": action.get("plan_id"),
            "review": None,
            "recovery": refreshed_project_view.get("recovery"),
            "next_command": next_command,
            "leader_action": action_detail,
            "continue_card": None,
            "inbox_card": None,
            "approval_card": None,
            "runtime_card": None,
            "queue_card": None,
            "operator_card": None,
            "role_card": None,
            "ledger_card": None,
            "workbench_card": None,
            "result": result,
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    if _chat_wants_help(args.message):
        next_command = "agentdeck workbench"
        turn = store.record_chat_turn(
            mode="help",
            message=args.message,
            plan_id=None,
            next_command=next_command,
            review=None,
            action_id=None,
            action_kind="help",
        )
        store.append_event(
            EventRecord.create(
                "leader_chat_turn",
                {
                    "turn_id": turn["turn_id"],
                    "mode": "help",
                    "plan_id": None,
                    "message_length": len(args.message),
                },
            )
        )
        refreshed_project_view = _project_view_payload_or_error(config, store)
        if refreshed_project_view is None:
            return 1
        payload = {
            "ok": True,
            "turn_id": turn["turn_id"],
            "mode": "help",
            "message": args.message,
            "project_view": refreshed_project_view,
            "leader_actions": refreshed_project_view.get("leader_actions"),
            "leader_explanation": _leader_chat_explanation(
                "help",
                next_command=next_command,
                project_view=refreshed_project_view,
            ),
            "plan_id": None,
            "review": None,
            "recovery": refreshed_project_view.get("recovery"),
            "next_command": next_command,
            "leader_action": None,
            "continue_card": None,
            "inbox_card": None,
            "approval_card": None,
            "runtime_card": None,
            "queue_card": None,
            "operator_card": None,
            "role_card": None,
            "ledger_card": None,
            "workbench_card": None,
            "capability_card": leader_chat_capability_card(),
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    if _is_continue_chat_message(args.message):
        plans = store.list_plans()
        plan_id = str(plans[-1]["plan_id"]) if plans else None
        initial_recovery = project_view.get("recovery", {})
        next_command = initial_recovery.get("next_command") if isinstance(initial_recovery, dict) else None
        turn = store.record_chat_turn(
            mode="continue",
            message=args.message,
            plan_id=plan_id,
            next_command=next_command,
            review=None,
            action_id=None,
            action_kind=None,
        )
        store.append_event(
            EventRecord.create(
                "leader_chat_turn",
                {
                    "turn_id": turn["turn_id"],
                    "mode": "continue",
                    "plan_id": plan_id,
                    "message_length": len(args.message),
                },
            )
        )
        refreshed_project_view = _project_view_payload_or_error(config, store)
        if refreshed_project_view is None:
            return 1
        recovery = refreshed_project_view.get("recovery", {})
        next_command = recovery.get("next_command") if isinstance(recovery, dict) else next_command
        continue_card = _continue_card_payload(refreshed_project_view, store)
        inbox_card, approval_card = _leader_chat_recovery_cards(refreshed_project_view, store)
        recommended_action = recovery.get("recommended_action") if isinstance(recovery, dict) else None
        runtime_card = (
            _workbench_runtime_card(refreshed_project_view)
            if isinstance(recommended_action, dict) and recommended_action.get("source") == "runtime"
            else None
        )
        leader_action = continue_card.get("leader_action")
        payload = {
            "ok": True,
            "turn_id": turn["turn_id"],
            "mode": "continue",
            "message": args.message,
            "project_view": refreshed_project_view,
            "leader_actions": refreshed_project_view.get("leader_actions"),
            "leader_explanation": _leader_chat_explanation(
                "continue",
                next_command=next_command,
                project_view=refreshed_project_view,
                leader_action=leader_action if isinstance(leader_action, dict) else None,
            ),
            "plan_id": plan_id,
            "review": None,
            "recovery": recovery,
            "next_command": next_command,
            "leader_action": leader_action,
            "continue_card": continue_card,
            "inbox_card": inbox_card,
            "approval_card": approval_card,
            "runtime_card": runtime_card,
            "queue_card": None,
            "operator_card": None,
            "role_card": None,
            "ledger_card": None,
            "workbench_card": None,
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    if _chat_wants_setup(args.message):
        next_command = "agentdeck doctor"
        turn = store.record_chat_turn(
            mode="setup",
            message=args.message,
            plan_id=None,
            next_command=next_command,
            review=None,
            action_id=None,
            action_kind="provider_health",
        )
        store.append_event(
            EventRecord.create(
                "leader_chat_turn",
                {
                    "turn_id": turn["turn_id"],
                    "mode": "setup",
                    "plan_id": None,
                    "message_length": len(args.message),
                },
            )
        )
        refreshed_project_view = _project_view_payload_or_error(config, store)
        if refreshed_project_view is None:
            return 1
        recovery = refreshed_project_view.get("recovery", {})
        payload = {
            "ok": True,
            "turn_id": turn["turn_id"],
            "mode": "setup",
            "message": args.message,
            "project_view": refreshed_project_view,
            "leader_actions": refreshed_project_view.get("leader_actions"),
            "leader_explanation": _leader_chat_explanation(
                "setup",
                next_command=next_command,
                project_view=refreshed_project_view,
            ),
            "plan_id": None,
            "review": None,
            "recovery": recovery,
            "next_command": next_command,
            "leader_action": None,
            "continue_card": None,
            "inbox_card": None,
            "approval_card": None,
            "runtime_card": None,
            "queue_card": None,
            "operator_card": None,
            "role_card": None,
            "ledger_card": None,
            "workbench_card": None,
            "provider_health": _workbench_provider_health(refreshed_project_view),
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    if _chat_wants_workbench(args.message):
        initial_workbench = _workbench_snapshot_payload(project_view, store, since_event_id=None)
        next_command = initial_workbench.get("next_command")
        turn = store.record_chat_turn(
            mode="workbench",
            message=args.message,
            plan_id=None,
            next_command=next_command,
            review=None,
            action_id=None,
            action_kind="workbench",
        )
        store.append_event(
            EventRecord.create(
                "leader_chat_turn",
                {
                    "turn_id": turn["turn_id"],
                    "mode": "workbench",
                    "plan_id": None,
                    "message_length": len(args.message),
                },
            )
        )
        refreshed_project_view = _project_view_payload_or_error(config, store)
        if refreshed_project_view is None:
            return 1
        workbench_card = _workbench_snapshot_payload(refreshed_project_view, store, since_event_id=None)
        next_command = workbench_card.get("next_command")
        payload = {
            "ok": True,
            "turn_id": turn["turn_id"],
            "mode": "workbench",
            "message": args.message,
            "project_view": refreshed_project_view,
            "leader_actions": refreshed_project_view.get("leader_actions"),
            "leader_explanation": _leader_chat_explanation(
                "workbench",
                next_command=next_command,
                project_view=refreshed_project_view,
            ),
            "plan_id": None,
            "review": None,
            "recovery": refreshed_project_view.get("recovery"),
            "next_command": next_command,
            "leader_action": None,
            "continue_card": None,
            "inbox_card": None,
            "approval_card": None,
            "runtime_card": None,
            "queue_card": None,
            "operator_card": None,
            "role_card": None,
            "ledger_card": None,
            "workbench_card": workbench_card,
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    if _chat_wants_runtime(args.message):
        next_command = "agentdeck agent list"
        turn = store.record_chat_turn(
            mode="runtime",
            message=args.message,
            plan_id=None,
            next_command=next_command,
            review=None,
            action_id=None,
            action_kind="runtime",
        )
        store.append_event(
            EventRecord.create(
                "leader_chat_turn",
                {
                    "turn_id": turn["turn_id"],
                    "mode": "runtime",
                    "plan_id": None,
                    "message_length": len(args.message),
                },
            )
        )
        refreshed_project_view = _project_view_payload_or_error(config, store)
        if refreshed_project_view is None:
            return 1
        runtime_card = _workbench_runtime_card(refreshed_project_view)
        payload = {
            "ok": True,
            "turn_id": turn["turn_id"],
            "mode": "runtime",
            "message": args.message,
            "project_view": refreshed_project_view,
            "leader_actions": refreshed_project_view.get("leader_actions"),
            "leader_explanation": _leader_chat_explanation(
                "runtime",
                next_command=next_command,
                project_view=refreshed_project_view,
            ),
            "plan_id": None,
            "review": None,
            "recovery": refreshed_project_view.get("recovery"),
            "next_command": next_command,
            "leader_action": None,
            "continue_card": None,
            "inbox_card": None,
            "approval_card": None,
            "runtime_card": runtime_card,
            "queue_card": None,
            "operator_card": None,
            "role_card": None,
            "ledger_card": None,
            "workbench_card": None,
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    if _chat_wants_ledger(args.message):
        refreshed_project_view = _project_view_payload_or_error(config, store)
        if refreshed_project_view is None:
            return 1
        ledger_card = _workbench_ledger_card(refreshed_project_view)
        trace_commands = ledger_card.get("trace_commands")
        next_command = trace_commands[0] if isinstance(trace_commands, list) and trace_commands else "agentdeck workbench"
        turn = store.record_chat_turn(
            mode="ledger",
            message=args.message,
            plan_id=None,
            next_command=next_command,
            review=None,
            action_id=None,
            action_kind="ledger",
        )
        store.append_event(
            EventRecord.create(
                "leader_chat_turn",
                {
                    "turn_id": turn["turn_id"],
                    "mode": "ledger",
                    "plan_id": None,
                    "message_length": len(args.message),
                },
            )
        )
        refreshed_project_view = _project_view_payload_or_error(config, store)
        if refreshed_project_view is None:
            return 1
        ledger_card = _workbench_ledger_card(refreshed_project_view)
        trace_commands = ledger_card.get("trace_commands")
        next_command = trace_commands[0] if isinstance(trace_commands, list) and trace_commands else "agentdeck workbench"
        payload = {
            "ok": True,
            "turn_id": turn["turn_id"],
            "mode": "ledger",
            "message": args.message,
            "project_view": refreshed_project_view,
            "leader_actions": refreshed_project_view.get("leader_actions"),
            "leader_explanation": _leader_chat_explanation(
                "ledger",
                next_command=next_command,
                project_view=refreshed_project_view,
            ),
            "plan_id": None,
            "review": None,
            "recovery": refreshed_project_view.get("recovery"),
            "next_command": next_command,
            "leader_action": None,
            "continue_card": None,
            "inbox_card": None,
            "approval_card": None,
            "runtime_card": None,
            "queue_card": None,
            "operator_card": None,
            "role_card": None,
            "ledger_card": ledger_card,
            "workbench_card": None,
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    if _chat_wants_role(args.message):
        next_command = "agentdeck workbench"
        turn = store.record_chat_turn(
            mode="role",
            message=args.message,
            plan_id=None,
            next_command=next_command,
            review=None,
            action_id=None,
            action_kind="role",
        )
        store.append_event(
            EventRecord.create(
                "leader_chat_turn",
                {
                    "turn_id": turn["turn_id"],
                    "mode": "role",
                    "plan_id": None,
                    "message_length": len(args.message),
                },
            )
        )
        refreshed_project_view = _project_view_payload_or_error(config, store)
        if refreshed_project_view is None:
            return 1
        role_card = _workbench_role_card(refreshed_project_view)
        payload = {
            "ok": True,
            "turn_id": turn["turn_id"],
            "mode": "role",
            "message": args.message,
            "project_view": refreshed_project_view,
            "leader_actions": refreshed_project_view.get("leader_actions"),
            "leader_explanation": _leader_chat_explanation(
                "role",
                next_command=next_command,
                project_view=refreshed_project_view,
            ),
            "plan_id": None,
            "review": None,
            "recovery": refreshed_project_view.get("recovery"),
            "next_command": next_command,
            "leader_action": None,
            "continue_card": None,
            "inbox_card": None,
            "approval_card": None,
            "runtime_card": None,
            "queue_card": None,
            "operator_card": None,
            "role_card": role_card,
            "ledger_card": None,
            "workbench_card": None,
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    if _chat_wants_queue(args.message):
        plans = store.list_plans()
        plan_id = str(plans[-1]["plan_id"]) if plans else None
        refreshed_project_view = _project_view_payload_or_error(config, store)
        if refreshed_project_view is None:
            return 1
        continue_card = _continue_card_payload(refreshed_project_view, store)
        active_queue_source = _active_queue_source(refreshed_project_view)
        queue_card = _workbench_queue_card(refreshed_project_view, continue_card, active_queue_source)
        operator_card = _workbench_operator_card(refreshed_project_view, continue_card, active_queue_source)
        next_command = continue_card.get("next_command")
        turn = store.record_chat_turn(
            mode="queue",
            message=args.message,
            plan_id=plan_id,
            next_command=next_command,
            review=None,
            action_id=None,
            action_kind="queue",
        )
        store.append_event(
            EventRecord.create(
                "leader_chat_turn",
                {
                    "turn_id": turn["turn_id"],
                    "mode": "queue",
                    "plan_id": plan_id,
                    "message_length": len(args.message),
                },
            )
        )
        refreshed_project_view = _project_view_payload_or_error(config, store)
        if refreshed_project_view is None:
            return 1
        continue_card = _continue_card_payload(refreshed_project_view, store)
        active_queue_source = _active_queue_source(refreshed_project_view)
        queue_card = _workbench_queue_card(refreshed_project_view, continue_card, active_queue_source)
        operator_card = _workbench_operator_card(refreshed_project_view, continue_card, active_queue_source)
        next_command = continue_card.get("next_command")
        payload = {
            "ok": True,
            "turn_id": turn["turn_id"],
            "mode": "queue",
            "message": args.message,
            "project_view": refreshed_project_view,
            "leader_actions": refreshed_project_view.get("leader_actions"),
            "leader_explanation": _leader_chat_explanation(
                "queue",
                next_command=next_command,
                project_view=refreshed_project_view,
            ),
            "plan_id": plan_id,
            "review": None,
            "recovery": refreshed_project_view.get("recovery"),
            "next_command": next_command,
            "leader_action": None,
            "continue_card": None,
            "inbox_card": None,
            "approval_card": None,
            "runtime_card": None,
            "queue_card": queue_card,
            "operator_card": operator_card,
            "role_card": None,
            "ledger_card": None,
            "workbench_card": None,
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    if _chat_wants_approval(args.message):
        approval_card = _approval_queue_payload(store)
        validation = validate_approval_contract(approval_card)
        if not validation["ok"]:
            print("Approval queue contract validation failed", file=sys.stderr)
            for error in validation["errors"]:
                print(f"- {error}", file=sys.stderr)
            return 1
        pending_approval = _pending_approval_item(approval_card)
        approved_approval = _approved_approval_item(approval_card)
        wants_approve = _chat_wants_approval_approve(args.message)
        wants_reject = _chat_wants_approval_reject(args.message)
        wants_dispatch = _chat_wants_approval_dispatch(args.message)
        next_command = (
            approved_approval.get("dispatch_command")
            if wants_dispatch
            and isinstance(approved_approval, dict)
            and approved_approval.get("dispatch_command")
            else pending_approval.get("reject_command")
            if wants_reject and isinstance(pending_approval, dict) and pending_approval.get("reject_command")
            else pending_approval.get("approve_command")
            if wants_approve and isinstance(pending_approval, dict) and pending_approval.get("approve_command")
            else "agentdeck approval list"
        )
        approval_action_kind = (
            "approval_dispatch"
            if wants_dispatch and isinstance(approved_approval, dict)
            else "approval_reject"
            if wants_reject and isinstance(pending_approval, dict)
            else "approval_approve"
            if wants_approve and isinstance(pending_approval, dict)
            else "approval"
        )
        turn = store.record_chat_turn(
            mode="approval",
            message=args.message,
            plan_id=None,
            next_command=next_command,
            review=None,
            action_id=None,
            action_kind=approval_action_kind,
        )
        store.append_event(
            EventRecord.create(
                "leader_chat_turn",
                {
                    "turn_id": turn["turn_id"],
                    "mode": "approval",
                    "plan_id": None,
                    "message_length": len(args.message),
                },
            )
        )
        refreshed_project_view = _project_view_payload_or_error(config, store)
        if refreshed_project_view is None:
            return 1
        payload = {
            "ok": True,
            "turn_id": turn["turn_id"],
            "mode": "approval",
            "message": args.message,
            "project_view": refreshed_project_view,
            "leader_actions": refreshed_project_view.get("leader_actions"),
            "leader_explanation": _leader_chat_explanation(
                "approval",
                next_command=next_command,
                project_view=refreshed_project_view,
                approval_card=approval_card,
                approval_action_kind=approval_action_kind,
            ),
            "plan_id": None,
            "review": None,
            "recovery": refreshed_project_view.get("recovery"),
            "next_command": next_command,
            "leader_action": None,
            "continue_card": None,
            "inbox_card": None,
            "approval_card": approval_card,
            "runtime_card": None,
            "queue_card": None,
            "operator_card": None,
            "role_card": None,
            "ledger_card": None,
            "workbench_card": None,
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    inbox_agent_id = _chat_inbox_agent_id(args.message, config)
    if inbox_agent_id:
        inbox_card = _inbox_queue_payload(inbox_agent_id, store)
        validation = validate_inbox_contract(inbox_card)
        if not validation["ok"]:
            print("Inbox contract validation failed", file=sys.stderr)
            for error in validation["errors"]:
                print(f"- {error}", file=sys.stderr)
            return 1
        head = _inbox_head_item(inbox_card)
        wants_trace = _chat_wants_inbox_trace(args.message)
        wants_ack = _chat_wants_inbox_ack(args.message)
        can_ack_head = isinstance(head, dict) and bool(head.get("can_ack"))
        next_command = (
            head.get("ack_command")
            if wants_ack and can_ack_head and head.get("ack_command")
            else head.get("trace_command")
            if wants_trace and isinstance(head, dict) and head.get("trace_command")
            else f"agentdeck inbox --agent {inbox_agent_id}"
        )
        inbox_action_kind = (
            "inbox_ack"
            if wants_ack and can_ack_head
            else "inbox_trace"
            if wants_trace and isinstance(head, dict)
            else "inbox"
        )
        turn = store.record_chat_turn(
            mode="inbox",
            message=args.message,
            plan_id=None,
            next_command=next_command,
            review=None,
            action_id=None,
            action_kind=inbox_action_kind,
        )
        store.append_event(
            EventRecord.create(
                "leader_chat_turn",
                {
                    "turn_id": turn["turn_id"],
                    "mode": "inbox",
                    "plan_id": None,
                    "message_length": len(args.message),
                },
            )
        )
        refreshed_project_view = _project_view_payload_or_error(config, store)
        if refreshed_project_view is None:
            return 1
        payload = {
            "ok": True,
            "turn_id": turn["turn_id"],
            "mode": "inbox",
            "message": args.message,
            "project_view": refreshed_project_view,
            "leader_actions": refreshed_project_view.get("leader_actions"),
            "leader_explanation": _leader_chat_explanation(
                "inbox",
                next_command=next_command,
                project_view=refreshed_project_view,
                inbox_card=inbox_card,
                inbox_action_kind=inbox_action_kind,
            ),
            "plan_id": None,
            "review": None,
            "recovery": refreshed_project_view.get("recovery"),
            "next_command": next_command,
            "leader_action": None,
            "continue_card": None,
            "inbox_card": inbox_card,
            "approval_card": None,
            "runtime_card": None,
            "queue_card": None,
            "operator_card": None,
            "role_card": None,
            "ledger_card": None,
            "workbench_card": None,
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    plans = store.list_plans()
    if plans:
        latest_plan = plans[-1]
        plan_id = str(latest_plan["plan_id"])
        review = store.leader_review(plan_id)
        action = store.suggest_leader_action(plan_id)
        action_detail = store.leader_action_detail(str(action["action_id"]))
        project_view = _project_view_payload_or_error(config, store)
        if project_view is None:
            return 1
        recovery = project_view.get("recovery", {})
        next_command = recovery.get("next_command") if isinstance(recovery, dict) else action.get("command")
        turn = store.record_chat_turn(
            mode="review",
            message=args.message,
            plan_id=plan_id,
            next_command=next_command,
            review=review,
            action_id=str(action["action_id"]),
            action_kind=str(action["kind"]),
        )
        payload = {
            "ok": True,
            "turn_id": turn["turn_id"],
            "mode": "review",
            "message": args.message,
            "project_view": project_view,
            "leader_actions": project_view.get("leader_actions"),
            "leader_explanation": _leader_chat_explanation(
                "review",
                next_command=next_command,
                project_view=project_view,
                leader_action=action_detail,
                review=review,
            ),
            "plan_id": plan_id,
            "review": review,
            "recovery": recovery,
            "next_command": next_command,
            "leader_action": action_detail,
            "continue_card": None,
            "inbox_card": None,
            "approval_card": None,
            "runtime_card": None,
            "queue_card": None,
            "operator_card": None,
            "role_card": None,
            "ledger_card": None,
            "workbench_card": None,
        }
        store.append_event(
            EventRecord.create(
                "leader_chat_turn",
                {
                    "turn_id": turn["turn_id"],
                    "mode": "review",
                    "plan_id": plan_id,
                    "message_length": len(args.message),
                },
            )
        )
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    try:
        provider_name = _leader_provider_name(config, args.provider)
        model_label = _leader_model_label(config, args.model)
        provider = leader_provider(provider_name)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    orchestrator = LeaderOrchestrator(config, provider)
    try:
        plan = orchestrator.plan(args.message)
    except RuntimeError as exc:
        _record_leader_provider_failure(store, "chat", provider.name, model_label, args.message, exc)
        print(f"leader provider failed: {exc}", file=sys.stderr)
        return 1
    record = store.record_plan(args.message, provider.name, model_label, plan)
    action = store.suggest_leader_action(str(record["plan_id"]))
    action_detail = store.leader_action_detail(str(action["action_id"]))
    project_view_with_action = _project_view_payload_or_error(config, store)
    if project_view_with_action is None:
        return 1
    recovery = project_view_with_action.get("recovery", {})
    next_command = (
        recovery.get("next_command")
        if isinstance(recovery, dict)
        else f"agentdeck approval create-from-plan --plan-id {record['plan_id']}"
    )
    turn = store.record_chat_turn(
        mode="plan",
        message=args.message,
        plan_id=str(record["plan_id"]),
        next_command=next_command,
        provider=record["provider"],
        model=record["model"],
        review=None,
        action_id=str(action["action_id"]),
        action_kind=str(action["kind"]),
    )
    refreshed_project_view = _project_view_payload_or_error(config, store)
    if refreshed_project_view is None:
        return 1
    payload = {
        "ok": True,
        "turn_id": turn["turn_id"],
        "mode": "plan",
        "message": args.message,
        "project_view": refreshed_project_view,
        "leader_actions": refreshed_project_view.get("leader_actions"),
        "leader_explanation": _leader_chat_explanation(
            "plan",
            next_command=next_command,
            project_view=refreshed_project_view,
            leader_action=action_detail,
        ),
        "plan_id": record["plan_id"],
        "recovery": refreshed_project_view.get("recovery"),
        "leader_action": action_detail,
        "continue_card": None,
        "status": record["status"],
        "provider": record["provider"],
        "model": record["model"],
        "dispatch_ready": record["dispatch_ready"],
        "plan": record["plan"],
        "review": None,
        "next_command": next_command,
        "inbox_card": None,
        "approval_card": None,
        "runtime_card": None,
        "queue_card": None,
        "operator_card": None,
        "role_card": None,
        "ledger_card": None,
        "workbench_card": None,
    }
    store.append_event(
        EventRecord.create(
            "leader_chat_turn",
            {
                "turn_id": turn["turn_id"],
                "mode": "plan",
                "plan_id": record["plan_id"],
                "provider": record["provider"],
                "model": record["model"],
                "message_length": len(args.message),
            },
        )
    )
    return _print_leader_chat_payload_or_error(payload, store, task=args.message)


def _chat_turn_summary(turn: dict[str, object]) -> dict[str, object]:
    return {
        "turn_id": turn.get("turn_id"),
        "mode": turn.get("mode"),
        "message": turn.get("message"),
        "plan_id": turn.get("plan_id"),
        "next_command": turn.get("next_command"),
        "action_id": turn.get("action_id"),
        "action_kind": turn.get("action_kind"),
        "created_at": turn.get("created_at"),
    }


def leader_chat_history_command(_args: argparse.Namespace) -> int:
    _config, store, exit_code = _load_project_or_error()
    if store is None:
        return exit_code
    turns = [_chat_turn_summary(turn) for turn in store.list_chat_turns()]
    _print_json({"count": len(turns), "turns": turns})
    return 0


def _plan_summary(plan: dict[str, object]) -> dict[str, object]:
    body = plan.get("plan", {})
    steps = body.get("steps", []) if isinstance(body, dict) else []
    return {
        "plan_id": plan.get("plan_id"),
        "task": plan.get("task"),
        "provider": plan.get("provider"),
        "model": plan.get("model"),
        "status": plan.get("status"),
        "dispatch_ready": plan.get("dispatch_ready"),
        "step_count": len(steps) if isinstance(steps, list) else 0,
        "created_at": plan.get("created_at"),
    }


def plan_list_command(_args: argparse.Namespace) -> int:
    _config, store, exit_code = _load_project_or_error()
    if store is None:
        return exit_code
    plans = [_plan_summary(plan) for plan in store.list_plans()]
    _print_json({"count": len(plans), "plans": plans})
    return 0


def plan_show_command(args: argparse.Namespace) -> int:
    _config, store, exit_code = _load_project_or_error()
    if store is None:
        return exit_code
    try:
        plan = store.plan_by_id(args.plan_id)
    except KeyError:
        print(f"unknown plan: {args.plan_id}", file=sys.stderr)
        return 1
    _print_json(plan)
    return 0


def plan_status_command(args: argparse.Namespace) -> int:
    _config, store, exit_code = _load_project_or_error()
    if store is None:
        return exit_code
    try:
        status = store.plan_status(args.plan_id)
    except KeyError:
        print(f"unknown plan: {args.plan_id}", file=sys.stderr)
        return 1
    _print_json(status)
    return 0


def approval_create_from_plan_command(args: argparse.Namespace) -> int:
    _config, store, exit_code = _load_project_or_error()
    if store is None:
        return exit_code
    try:
        approvals = store.create_approvals_from_plan(args.plan_id)
    except KeyError:
        print(f"unknown plan: {args.plan_id}", file=sys.stderr)
        return 1
    store.append_event(
        EventRecord.create(
            "approvals_created_from_plan",
            {
                "plan_id": args.plan_id,
                "count": len(approvals),
            },
        )
    )
    _print_json({"ok": True, "plan_id": args.plan_id, "count": len(approvals), "approvals": approvals})
    return 0


def approval_list_command(_args: argparse.Namespace) -> int:
    _config, store, exit_code = _load_project_or_error()
    if store is None:
        return exit_code
    payload = _approval_queue_payload(store)
    validation = validate_approval_contract(payload)
    if not validation["ok"]:
        print("Approval queue contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1
    _print_json(payload)
    return 0


def _approval_queue_payload(store: StateStore) -> dict[str, object]:
    approvals = [_approval_queue_item(approval) for approval in store.list_approvals()]
    return {"count": len(approvals), "approvals": approvals}


def _approval_queue_item(approval: dict[str, object]) -> dict[str, object]:
    approval_id = str(approval.get("approval_id"))
    can_dispatch = approval.get("status") == "approved"
    return {
        **approval,
        "reason": approval.get("reason"),
        "preview_command": "agentdeck approval list",
        "controls": _approval_item_controls(approval_id, can_dispatch),
        "approve_command": f"agentdeck approval approve --approval-id {approval_id}",
        "reject_command": f"agentdeck approval reject --approval-id {approval_id} --reason <reason>",
        "dispatch_command": f"agentdeck approval dispatch --approval-id {approval_id}",
        "can_dispatch": can_dispatch,
        "dispatch_blocker": None if can_dispatch else "approval is not approved",
    }


def _approval_item_controls(approval_id: str, can_dispatch: bool) -> list[dict[str, object]]:
    dispatch_blocker = None if can_dispatch else "approval is not approved"
    return [
        _control(
            kind="preview",
            label="Preview approval queue",
            command="agentdeck approval list",
            safety="inspect",
        ),
        _control(
            kind="approve",
            label="Approve",
            command=f"agentdeck approval approve --approval-id {approval_id}",
            safety="explicit_runtime",
        ),
        _control(
            kind="reject",
            label="Reject",
            command=f"agentdeck approval reject --approval-id {approval_id} --reason <reason>",
            safety="explicit_runtime",
        ),
        _control(
            kind="dispatch",
            label="Dispatch",
            command=f"agentdeck approval dispatch --approval-id {approval_id}",
            safety="explicit_runtime",
            enabled=can_dispatch,
            blocker=dispatch_blocker,
        ),
    ]


def _inbox_item_controls(agent_id: str, inbox_id: str, can_ack: bool) -> list[dict[str, object]]:
    ack_blocker = None if can_ack else "inbox item is not head"
    return [
        _control(
            kind="preview",
            label="Trace inbox item",
            command=f"agentdeck trace --id {inbox_id}",
            safety="inspect",
        ),
        _control(
            kind="ack",
            label="Acknowledge inbox head",
            command=f"agentdeck ack --agent {agent_id} --inbox-id {inbox_id}",
            safety="explicit_runtime",
            enabled=can_ack,
            blocker=ack_blocker,
        ),
    ]


def _control(
    *, kind: str, label: str, command: object, safety: str, enabled: bool = True, blocker: object = None
) -> dict[str, object]:
    return {
        "kind": kind,
        "label": label,
        "command": command,
        "safety": safety,
        "enabled": enabled,
        "blocker": blocker,
    }


def _approval_decision_command(approval_id: str, status: str, reason: str | None = None) -> int:
    _config, store, exit_code = _load_project_or_error()
    if store is None:
        return exit_code
    try:
        approval = store.decide_approval(approval_id, status, reason)
    except KeyError:
        print(f"unknown approval: {approval_id}", file=sys.stderr)
        return 1
    store.append_event(
        EventRecord.create(
            "approval_decided",
            {
                "approval_id": approval_id,
                "plan_id": approval.get("plan_id"),
                "status": status,
            },
        )
    )
    payload = {
        "ok": True,
        "approval_id": approval_id,
        "plan_id": approval.get("plan_id"),
        "status": approval.get("status"),
    }
    if approval.get("reason"):
        payload["reason"] = approval.get("reason")
    _print_json(payload)
    return 0


def approval_approve_command(args: argparse.Namespace) -> int:
    return _approval_decision_command(args.approval_id, "approved")


def approval_reject_command(args: argparse.Namespace) -> int:
    return _approval_decision_command(args.approval_id, "rejected", args.reason)


def approval_dispatch_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    try:
        approval = store.approval_by_id(args.approval_id)
    except KeyError:
        print(f"unknown approval: {args.approval_id}", file=sys.stderr)
        return 1
    if approval.get("status") != "approved":
        print(f"approval is not approved: {args.approval_id}", file=sys.stderr)
        return 1
    agent_id = str(approval.get("agent_id", ""))
    agent = _agent_by_id(config, agent_id)
    if agent is None:
        print(f"unknown agent: {agent_id}", file=sys.stderr)
        return 1
    binding, exit_code = _running_binding_or_error(store, agent_id)
    if binding is None:
        return exit_code
    pane_id = str(binding["pane_id"])
    task = str(approval.get("task", ""))
    prompt = build_dispatch_prompt(agent, task)
    records = store.create_dispatch_records("leader", agent.agent_id, task, prompt, pane_id)
    message = records["message"]
    attempt = records["attempt"]
    job = records["job"]
    TmuxBackend().send_input(config.runtime, pane_id, prompt)
    store.mark_approval_dispatched(
        args.approval_id,
        str(message["message_id"]),
        str(attempt["attempt_id"]),
        str(job["job_id"]),
    )
    store.append_event(
        EventRecord.create(
            "approval_dispatched",
            {
                "approval_id": args.approval_id,
                "plan_id": approval.get("plan_id"),
                "message_id": message["message_id"],
                "agent_id": agent.agent_id,
                "pane_id": pane_id,
            },
        )
    )
    _print_json(
        {
            "ok": True,
            "approval_id": args.approval_id,
            "message_id": message["message_id"],
            "agent_id": agent.agent_id,
            "pane_id": pane_id,
            "trace_command": _trace_command(message["message_id"]),
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentdeck")
    subparsers = parser.add_subparsers(dest="command")

    doctor = subparsers.add_parser("doctor", help="Check local AgentDeck prerequisites")
    doctor.set_defaults(func=doctor_command)

    status = subparsers.add_parser("status", help="Show project configuration and runtime state")
    status.set_defaults(func=status_command)

    events = subparsers.add_parser("events", help="Show recent audit events")
    events.add_argument("--limit", type=int, default=20, help="Number of recent events to show")
    events.add_argument("--since", default=None, help="Show audit events after this event id")
    events.set_defaults(func=events_command)

    continue_parser = subparsers.add_parser("continue", help="Show the current recovery-driven next step")
    continue_parser.set_defaults(func=continue_command)

    workbench = subparsers.add_parser("workbench", help="Show a GUI-ready read-only workbench snapshot")
    workbench.add_argument("--watch", action="store_true", help="Stream validated workbench snapshots as JSONL")
    workbench.add_argument("--since-event", default=None, help="Summarize audit events after this event id")
    workbench.add_argument(
        "--iterations",
        type=int,
        default=None,
        help="Number of snapshots to emit; with --watch omitted this still emits once",
    )
    workbench.add_argument("--interval", type=float, default=1.0, help="Seconds between --watch snapshots")
    workbench.set_defaults(func=workbench_command)

    contract = subparsers.add_parser("contract", help="Discover machine-readable AgentDeck contracts")
    contract_subparsers = contract.add_subparsers(dest="contract_command")
    contract_list = contract_subparsers.add_parser(
        "list",
        help="List all GUI-consumable contract discovery commands",
    )
    contract_list.set_defaults(func=contract_list_command)
    contract_project_view = contract_subparsers.add_parser(
        "project-view",
        help="Show ProjectView contract discovery metadata",
    )
    contract_project_view.add_argument("--example", action="store_true", help="Include a GUI-ready ProjectView example")
    contract_project_view.set_defaults(func=contract_project_view_command)
    contract_leader_chat = contract_subparsers.add_parser(
        "leader-chat",
        help="Show Leader chat response contract discovery metadata",
    )
    contract_leader_chat.add_argument("--example", action="store_true", help="Include a GUI-ready Leader chat example")
    contract_leader_chat.set_defaults(func=contract_leader_chat_command)
    contract_continue = contract_subparsers.add_parser(
        "continue",
        help="Show continue recovery card contract discovery metadata",
    )
    contract_continue.add_argument("--example", action="store_true", help="Include a GUI-ready continue card example")
    contract_continue.set_defaults(func=contract_continue_command)
    contract_doctor = contract_subparsers.add_parser(
        "doctor",
        help="Show doctor diagnostics contract discovery metadata",
    )
    contract_doctor.add_argument("--example", action="store_true", help="Include a GUI-ready doctor example")
    contract_doctor.set_defaults(func=contract_doctor_command)
    contract_events = contract_subparsers.add_parser(
        "events",
        help="Show event timeline contract discovery metadata",
    )
    contract_events.add_argument("--example", action="store_true", help="Include a GUI-ready events example")
    contract_events.set_defaults(func=contract_events_command)
    contract_workbench = contract_subparsers.add_parser(
        "workbench",
        help="Show workbench snapshot contract discovery metadata",
    )
    contract_workbench.add_argument("--example", action="store_true", help="Include a GUI-ready workbench example")
    contract_workbench.set_defaults(func=contract_workbench_command)
    contract_agent_runtime = contract_subparsers.add_parser(
        "agent-runtime",
        help="Show agent runtime command contract discovery metadata",
    )
    contract_agent_runtime.add_argument(
        "--example",
        action="store_true",
        help="Include a GUI-ready agent runtime example",
    )
    contract_agent_runtime.set_defaults(func=contract_agent_runtime_command)
    contract_approvals = contract_subparsers.add_parser(
        "approvals",
        help="Show approval queue contract discovery metadata",
    )
    contract_approvals.add_argument("--example", action="store_true", help="Include a GUI-ready approval queue example")
    contract_approvals.set_defaults(func=contract_approvals_command)
    contract_inbox = contract_subparsers.add_parser(
        "inbox",
        help="Show agent inbox contract discovery metadata",
    )
    contract_inbox.add_argument("--example", action="store_true", help="Include a GUI-ready inbox example")
    contract_inbox.set_defaults(func=contract_inbox_command)
    contract_leader_action = contract_subparsers.add_parser(
        "leader-action",
        help="Show Leader action detail contract discovery metadata",
    )
    contract_leader_action.add_argument(
        "--example",
        action="store_true",
        help="Include a GUI-ready Leader action detail example",
    )
    contract_leader_action.set_defaults(func=contract_leader_action_command)
    contract_leader_actions = contract_subparsers.add_parser(
        "leader-actions",
        help="Show Leader action queue contract discovery metadata",
    )
    contract_leader_actions.add_argument(
        "--example",
        action="store_true",
        help="Include a GUI-ready Leader action queue example",
    )
    contract_leader_actions.set_defaults(func=contract_leader_actions_command)
    contract_leader_review = contract_subparsers.add_parser(
        "leader-review",
        help="Show Leader review response contract discovery metadata",
    )
    contract_leader_review.add_argument(
        "--example",
        action="store_true",
        help="Include a GUI-ready Leader review response example",
    )
    contract_leader_review.set_defaults(func=contract_leader_review_command)
    contract_trace = contract_subparsers.add_parser(
        "trace",
        help="Show communication trace contract discovery metadata",
    )
    contract_trace.add_argument("--example", action="store_true", help="Include a GUI-ready trace example")
    contract_trace.set_defaults(func=contract_trace_command)

    project = subparsers.add_parser("project", help="Project management commands")
    project_subparsers = project.add_subparsers(dest="project_command")
    project_init = project_subparsers.add_parser("init", help="Initialize .agentdeck project state")
    project_init.set_defaults(func=init_command)

    agent = subparsers.add_parser("agent", help="Agent runtime commands")
    agent_subparsers = agent.add_subparsers(dest="agent_command")

    agent_list = agent_subparsers.add_parser("list", help="List configured agents and runtime bindings")
    agent_list.set_defaults(func=agent_list_command)

    agent_spawn = agent_subparsers.add_parser("spawn", help="Spawn a configured agent in tmux")
    agent_spawn.add_argument("--agent", required=True, help="Agent id from .agentdeck/config.toml")
    agent_spawn.set_defaults(func=agent_spawn_command)

    agent_capture = agent_subparsers.add_parser("capture", help="Capture output from a spawned agent pane")
    agent_capture.add_argument("--agent", required=True, help="Agent id from .agentdeck/config.toml")
    agent_capture.add_argument("--lines", type=int, default=200, help="Number of recent lines to capture")
    agent_capture.set_defaults(func=agent_capture_command)

    agent_send = agent_subparsers.add_parser("send", help="Send text to a spawned agent pane")
    agent_send.add_argument("--agent", required=True, help="Agent id from .agentdeck/config.toml")
    agent_send.add_argument("--text", required=True, help="Text to send followed by Enter")
    agent_send.set_defaults(func=agent_send_command)

    agent_refresh = agent_subparsers.add_parser("refresh", help="Refresh stored agent runtime bindings from tmux")
    agent_refresh.set_defaults(func=agent_refresh_command)

    agent_stop = agent_subparsers.add_parser("stop", help="Kill a spawned agent pane and mark it stopped")
    agent_stop.add_argument("--agent", required=True, help="Agent id from .agentdeck/config.toml")
    agent_stop.set_defaults(func=agent_stop_command)

    agent_assign_role = agent_subparsers.add_parser(
        "assign-role",
        help="Assign an agent role and role prompt in .agentdeck/config.toml",
    )
    agent_assign_role.add_argument("--agent", required=True, help="Agent id from .agentdeck/config.toml")
    agent_assign_role.add_argument("--role", required=True, help="Human-readable role name")
    agent_assign_role.add_argument("--role-prompt", required=True, help="Role instruction injected during dispatch")
    agent_assign_role.set_defaults(func=agent_assign_role_command)

    leader = subparsers.add_parser("leader", help="Leader planning commands")
    leader_subparsers = leader.add_subparsers(dest="leader_command")
    leader_plan = leader_subparsers.add_parser("plan", help="Create a plan without dispatching work")
    leader_plan.add_argument("--task", required=True, help="Goal for the Leader Agent to plan")
    leader_plan.add_argument("--provider", help="Leader provider to use; defaults to .agentdeck/config.toml")
    leader_plan.add_argument("--model", help="Provider model label recorded with the plan; defaults to config")
    leader_plan.set_defaults(func=leader_plan_command)
    leader_review = leader_subparsers.add_parser("review", help="Review plan progress and recommend next action")
    leader_review.add_argument("--plan-id", required=True, help="Plan id from agentdeck leader plan")
    leader_review.set_defaults(func=leader_review_command)
    leader_next = leader_subparsers.add_parser("next", help="Suggest and persist the next approval-gated action")
    leader_next.add_argument("--plan-id", help="Plan id to inspect; defaults to latest saved plan")
    leader_next.set_defaults(func=leader_next_command)
    leader_actions = leader_subparsers.add_parser("actions", help="List persisted Leader action suggestions")
    leader_actions.set_defaults(func=leader_actions_command)
    leader_action = leader_subparsers.add_parser("action", help="Show one persisted Leader action")
    leader_action.add_argument("--action-id", required=True, help="Leader action id from agentdeck leader next")
    leader_action.set_defaults(func=leader_action_command)
    leader_apply_action = leader_subparsers.add_parser(
        "apply-action",
        help="Apply a safe pending Leader action with explicit confirmation",
    )
    leader_apply_action.add_argument("--action-id", required=True, help="Leader action id from agentdeck leader next")
    leader_apply_action.set_defaults(func=leader_apply_action_command)
    leader_chat = leader_subparsers.add_parser("chat", help="Natural-language Leader entrypoint")
    leader_chat.add_argument("--message", required=True, help="Natural-language message for the Leader")
    leader_chat.add_argument("--provider", help="Leader provider to use when a new plan is needed; defaults to config")
    leader_chat.add_argument("--model", help="Provider model label recorded with new plans; defaults to config")
    leader_chat.set_defaults(func=leader_chat_command)
    leader_chat_history = leader_subparsers.add_parser("chat-history", help="List persisted Leader chat turns")
    leader_chat_history.set_defaults(func=leader_chat_history_command)

    plan = subparsers.add_parser("plan", help="Inspect Leader plans")
    plan_subparsers = plan.add_subparsers(dest="plan_command")
    plan_list = plan_subparsers.add_parser("list", help="List saved Leader plans")
    plan_list.set_defaults(func=plan_list_command)
    plan_show = plan_subparsers.add_parser("show", help="Show a saved Leader plan")
    plan_show.add_argument("--plan-id", required=True, help="Plan id from agentdeck leader plan")
    plan_show.set_defaults(func=plan_show_command)
    plan_status = plan_subparsers.add_parser("status", help="Show plan progress across approvals and dispatches")
    plan_status.add_argument("--plan-id", required=True, help="Plan id from agentdeck leader plan")
    plan_status.set_defaults(func=plan_status_command)

    approval = subparsers.add_parser("approval", help="Approval gate commands")
    approval_subparsers = approval.add_subparsers(dest="approval_command")
    approval_create = approval_subparsers.add_parser(
        "create-from-plan",
        help="Create pending approvals from a saved Leader plan",
    )
    approval_create.add_argument("--plan-id", required=True, help="Plan id from agentdeck leader plan")
    approval_create.set_defaults(func=approval_create_from_plan_command)
    approval_list = approval_subparsers.add_parser("list", help="List approval items")
    approval_list.set_defaults(func=approval_list_command)
    approval_approve = approval_subparsers.add_parser("approve", help="Approve an approval item")
    approval_approve.add_argument("--approval-id", required=True, help="Approval id")
    approval_approve.set_defaults(func=approval_approve_command)
    approval_reject = approval_subparsers.add_parser("reject", help="Reject an approval item")
    approval_reject.add_argument("--approval-id", required=True, help="Approval id")
    approval_reject.add_argument("--reason", default="", help="Reason for rejection")
    approval_reject.set_defaults(func=approval_reject_command)
    approval_dispatch = approval_subparsers.add_parser("dispatch", help="Dispatch an approved item")
    approval_dispatch.add_argument("--approval-id", required=True, help="Approved approval id")
    approval_dispatch.set_defaults(func=approval_dispatch_command)

    dispatch = subparsers.add_parser("dispatch", help="Send a role-aware task to a running agent")
    dispatch.add_argument("--from-agent", default="user", help="Actor or agent id that submitted this task")
    dispatch.add_argument("--agent", required=True, help="Agent id from .agentdeck/config.toml")
    dispatch.add_argument("--task", required=True, help="Task text to send with the agent role prompt")
    dispatch.set_defaults(func=dispatch_command)

    inbox = subparsers.add_parser("inbox", help="Show pending inbox items for an agent")
    inbox.add_argument("--agent", required=True, help="Agent id from .agentdeck/config.toml")
    inbox.set_defaults(func=inbox_command)

    reply = subparsers.add_parser("reply", help="Record an agent reply for a dispatched message")
    reply.add_argument("--agent", required=True, help="Agent id that produced the reply")
    reply.add_argument("--message-id", required=True, help="Message id being replied to")
    reply.add_argument("--text", required=True, help="Reply text")
    reply.set_defaults(func=reply_command)

    capture_reply = subparsers.add_parser("capture-reply", help="Capture structured reply from an agent pane")
    capture_reply.add_argument("--agent", required=True, help="Agent id that produced the reply")
    capture_reply.add_argument("--message-id", required=True, help="Message id being replied to")
    capture_reply.add_argument("--lines", type=int, default=200, help="Number of recent pane lines to inspect")
    capture_reply.set_defaults(func=capture_reply_command)

    ack = subparsers.add_parser("ack", help="Acknowledge an inbox item")
    ack.add_argument("--agent", required=True, help="Agent id that owns the inbox")
    ack.add_argument("--inbox-id", required=True, help="Inbox item id")
    ack.set_defaults(func=ack_command)

    trace = subparsers.add_parser("trace", help="Trace message, attempt, job, reply, or inbox lineage")
    trace.add_argument("--id", required=True, help="message_id, attempt_id, job_id, reply_id, or inbox_id")
    trace.set_defaults(func=trace_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    return args.func(args)
