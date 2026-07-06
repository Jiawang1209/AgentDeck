from __future__ import annotations

from dataclasses import asdict
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import time

from .config import (
    config_path,
    load_config,
    project_root,
    update_agent_role,
    update_leader_approval_mode,
    update_leader_provider,
    write_default_config,
)
from .contracts import (
    agent_runtime_contract_response,
    approval_contract_response,
    artifacts_contract_response,
    contract_index_response,
    control_registry_item_id,
    continue_contract_response,
    controls_contract_response,
    doctor_contract_response,
    events_contract_response,
    inbox_contract_response,
    leader_actions_contract_response,
    leader_action_contract_response,
    leader_chat_action_card,
    leader_chat_capability_card,
    leader_chat_control_registry_card,
    leader_chat_contract_response,
    leader_chat_intent_placeholder_blocker,
    leader_review_contract_response,
    leader_summary_contract_response,
    project_view_contract_response,
    runtime_agent_controls,
    run_start_contract_response,
    trace_contract_response,
    workbench_contract_response,
    validate_approval_contract,
    validate_approval_dispatch_ready_contract,
    validate_artifacts_contract,
    validate_continue_contract,
    validate_control_registry_card_contract,
    validate_inbox_contract,
    validate_leader_actions_contract,
    validate_leader_action_contract,
    validate_leader_chat_contract,
    validate_leader_review_contract,
    validate_leader_summary_contract,
    validate_project_view_contract,
    validate_run_start_contract,
    validate_trace_contract,
    validate_workbench_contract,
)
from .models import PROJECT_VIEW_SCHEMA_VERSION, AgentRuntimeBinding, AgentSpec, EventRecord, ProjectConfig
from .orchestration.leader import LeaderOrchestrator
from .providers import DeepSeekProvider, OpenAICompatibleProvider, leader_provider
from .runtime import TmuxBackend
from .state import StateStore, agentdeck_dir, leader_backend_identity, leader_provider_backend, leader_provider_transport


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
    recovery = payload.get("recovery") if isinstance(payload.get("recovery"), dict) else {}
    recommended_action = recovery.get("recommended_action") if isinstance(recovery, dict) else None
    prefer_trace_card = (
        mode == "continue"
        and isinstance(recommended_action, dict)
        and recommended_action.get("source") == "reply"
        and payload.get("trace_card") is not None
    )
    card_names = (
        "workbench_card",
        "continue_card",
        "run_start_card",
        "run_progress_card",
        "leader_summary_card",
        "capture_card",
        "terminal_card",
        "dispatch_preview_card",
        "dispatch_batch_preview_card",
        "agent_ready_card",
        "runtime_action_card",
        "trace_card",
        "inbox_card",
        "approval_card",
        "runtime_card",
        "operator_card",
        "queue_card",
        "role_card",
        "ledger_card",
        "audit_card",
        "artifacts_card",
        "control_mode_card",
        "provider_health",
        "capability_card",
    )
    if prefer_trace_card:
        card_names = tuple(card for card in card_names if card != "trace_card")
        card_names = ("trace_card",) + card_names
    for card_name in card_names:
        if payload.get(card_name) is not None:
            embedded_card = card_name
            break
    secondary_embedded_cards: list[str] = []
    if embedded_card == "continue_card" and payload.get("runtime_card") is not None:
        secondary_embedded_cards.append("runtime_card")
    if embedded_card == "agent_ready_card" and payload.get("startup_preview_card") is not None:
        secondary_embedded_cards.append("startup_preview_card")
    if embedded_card == "agent_ready_card" and payload.get("runtime_card") is not None:
        secondary_embedded_cards.append("runtime_card")
    if embedded_card == "agent_ready_card" and payload.get("terminal_session_card") is not None:
        secondary_embedded_cards.append("terminal_session_card")
    if embedded_card == "agent_ready_card" and payload.get("control_registry_card") is not None:
        secondary_embedded_cards.append("control_registry_card")
    if embedded_card == "runtime_action_card" and payload.get("runtime_card") is not None:
        secondary_embedded_cards.append("runtime_card")
    if embedded_card == "runtime_action_card" and payload.get("terminal_session_card") is not None:
        secondary_embedded_cards.append("terminal_session_card")
    if embedded_card == "runtime_action_card" and payload.get("control_registry_card") is not None:
        secondary_embedded_cards.append("control_registry_card")
    if embedded_card == "runtime_card" and payload.get("startup_preview_card") is not None:
        secondary_embedded_cards.append("startup_preview_card")
    if embedded_card == "runtime_card" and payload.get("terminal_session_card") is not None:
        secondary_embedded_cards.append("terminal_session_card")
    if embedded_card == "runtime_card" and payload.get("control_registry_card") is not None:
        secondary_embedded_cards.append("control_registry_card")
    if embedded_card == "provider_health" and payload.get("provider_setup_card") is not None:
        secondary_embedded_cards.append("provider_setup_card")
    if embedded_card == "provider_health" and payload.get("provider_switch_card") is not None:
        secondary_embedded_cards.append("provider_switch_card")
    if embedded_card == "provider_health" and payload.get("control_registry_card") is not None:
        secondary_embedded_cards.append("control_registry_card")
    if (
        "runtime_card" in secondary_embedded_cards
        and "terminal_session_card" not in secondary_embedded_cards
        and payload.get("terminal_session_card") is not None
    ):
        secondary_embedded_cards.append("terminal_session_card")
    route_source = "provider_plan" if mode in {"plan", "run_start"} else "state_review" if mode in {"review", "summary"} else "local_rule"
    action_kind = explanation.get("action_kind")
    read_only = mode not in {"plan", "run_start", "review", "apply_action"} and action_kind != "approval_create"
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
    next_blocker = leader_chat_intent_placeholder_blocker(next_command) or _leader_chat_intent_card_blocker(
        embedded_card, payload
    )
    next_enabled = next_command is not None and next_blocker is None
    controls.append(
        {
            "kind": "next",
            "label": _leader_chat_next_control_label(next_command),
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
        "secondary_embedded_cards": secondary_embedded_cards,
        "read_only": read_only,
        "next_command": next_command,
        "requires_explicit_user": explanation.get("requires_explicit_user"),
        "controls": controls,
    }


def _leader_chat_next_control_label(next_command: object) -> str:
    command = str(next_command or "")
    if command == "agentdeck approval dispatch-ready --confirm":
        return "Dispatch ready approvals"
    if command == "agentdeck agent spawn-ready --confirm":
        return "Spawn ready agents"
    if command == "agentdeck agent refresh":
        return "Refresh runtime"
    if command.startswith("tmux ") and " attach " in command:
        return "Open terminal"
    spawn_match = re.fullmatch(r"agentdeck agent spawn --agent ([^\s]+)", command)
    if spawn_match:
        return f"Spawn {spawn_match.group(1)}"
    send_match = re.fullmatch(r"agentdeck agent send --agent ([^\s]+) --text .+", command)
    if send_match:
        return f"Send input to {send_match.group(1)}"
    stop_match = re.fullmatch(r"agentdeck agent stop --agent ([^\s]+)", command)
    if stop_match:
        return f"Stop {stop_match.group(1)}"
    policy_match = re.fullmatch(r"agentdeck policy set-mode --mode (ask|approve|autonomous)", command)
    if policy_match:
        mode = policy_match.group(1)
        if mode == "ask":
            return "Switch to ask mode"
        if mode == "approve":
            return "Switch to approval mode"
        return "Request autonomous mode"
    if re.fullmatch(r"agentdeck agent assign-role --agent [^\s]+ --role .+ --role-prompt .+", command):
        return "Assign role"
    if re.fullmatch(r"agentdeck leader set-provider --provider [^\s]+ --model [^\s]+(?: --require-ready)?", command):
        return "Switch Leader provider"
    if _provider_for_setup_command(command) is not None:
        return "Run provider setup"
    approval_match = re.fullmatch(
        r"agentdeck approval (approve|reject|dispatch) --approval-id [^\s]+(?: --reason .+)?", command
    )
    if approval_match:
        action = approval_match.group(1)
        if action == "approve":
            return "Approve approval"
        if action == "reject":
            return "Reject approval"
        return "Dispatch approval"
    if re.fullmatch(r"agentdeck ack --agent [^\s]+ --inbox-id [^\s]+", command):
        return "Acknowledge inbox item"
    if re.fullmatch(r"agentdeck agent capture --agent [^\s]+ --lines \d+", command):
        return "Capture agent output"
    if re.fullmatch(r"agentdeck capture-reply --agent [^\s]+ --message-id [^\s]+(?: --lines \d+)?", command):
        return "Capture reply"
    if re.fullmatch(r"agentdeck inbox --agent [^\s]+", command):
        return "Open inbox"
    if re.fullmatch(r"agentdeck trace --id [^\s]+", command):
        return "Inspect trace"
    if re.fullmatch(r"agentdeck leader summary --plan-id [^\s]+", command):
        return "Summarize plan"
    return "Next command"


def _leader_chat_intent_inspect_command(embedded_card: object, payload: dict[str, object]) -> str | None:
    if embedded_card == "workbench_card":
        return "agentdeck workbench"
    if embedded_card == "continue_card":
        return "agentdeck continue"
    if embedded_card == "run_start_card":
        return "agentdeck approval list"
    if embedded_card == "run_progress_card":
        run_progress_card = payload.get("run_progress_card")
        plan_id = run_progress_card.get("plan_id") if isinstance(run_progress_card, dict) else None
        return f"agentdeck run --plan-id {plan_id}" if plan_id else None
    if embedded_card == "leader_summary_card":
        summary_card = payload.get("leader_summary_card")
        command = (
            summary_card.get("review_command")
            if isinstance(summary_card, dict)
            else None
        )
        return str(command) if command else None
    if embedded_card == "runtime_card":
        return "agentdeck agent list"
    if embedded_card == "agent_ready_card":
        return "agentdeck agent ready"
    if embedded_card == "capture_card":
        capture_card = payload.get("capture_card")
        command = capture_card.get("capture_command") if isinstance(capture_card, dict) else None
        return str(command) if command else None
    if embedded_card == "terminal_card":
        terminal_card = payload.get("terminal_card")
        command = terminal_card.get("attach_command") if isinstance(terminal_card, dict) else None
        return str(command) if command else None
    if embedded_card == "dispatch_preview_card":
        dispatch_preview_card = payload.get("dispatch_preview_card")
        command = (
            dispatch_preview_card.get("approval_command")
            if isinstance(dispatch_preview_card, dict)
            else None
        )
        return str(command) if command else None
    if embedded_card == "dispatch_batch_preview_card":
        dispatch_batch_preview_card = payload.get("dispatch_batch_preview_card")
        command = (
            dispatch_batch_preview_card.get("approval_command")
            if isinstance(dispatch_batch_preview_card, dict)
            else None
        )
        return str(command) if command else None
    if embedded_card == "ledger_card":
        return "agentdeck workbench"
    if embedded_card == "audit_card":
        audit_card = payload.get("audit_card")
        command = audit_card.get("events_command") if isinstance(audit_card, dict) else None
        return str(command) if command else "agentdeck events --limit 20"
    if embedded_card == "artifacts_card":
        return "agentdeck artifacts"
    if embedded_card == "trace_card":
        trace_card = payload.get("trace_card")
        query_id = trace_card.get("query_id") if isinstance(trace_card, dict) else None
        return _trace_command(query_id) if query_id else None
    if embedded_card == "role_card":
        return "agentdeck workbench"
    if embedded_card == "queue_card" or embedded_card == "operator_card":
        return "agentdeck workbench"
    if embedded_card == "control_mode_card":
        return "agentdeck workbench"
    if embedded_card == "provider_health":
        return "agentdeck doctor"
    if embedded_card == "approval_card":
        return "agentdeck approval list"
    if embedded_card == "runtime_action_card":
        runtime_action_card = payload.get("runtime_action_card")
        agent_id = runtime_action_card.get("agent_id") if isinstance(runtime_action_card, dict) else None
        return f"agentdeck agent terminal --agent {agent_id}" if agent_id else None
    if embedded_card == "inbox_card":
        inbox_card = payload.get("inbox_card")
        agent_id = inbox_card.get("agent_id") if isinstance(inbox_card, dict) else None
        return f"agentdeck inbox --agent {agent_id}" if agent_id else None
    if embedded_card == "capability_card":
        return "agentdeck workbench"
    return None


def _leader_chat_intent_card_blocker(embedded_card: object, payload: dict[str, object]) -> str | None:
    if embedded_card == "dispatch_preview_card":
        dispatch_preview_card = payload.get("dispatch_preview_card")
        blocker = dispatch_preview_card.get("blocker") if isinstance(dispatch_preview_card, dict) else None
        return str(blocker) if blocker else None
    if embedded_card == "dispatch_batch_preview_card":
        dispatch_batch_preview_card = payload.get("dispatch_batch_preview_card")
        if isinstance(dispatch_batch_preview_card, dict) and not dispatch_batch_preview_card.get("ready_count"):
            return "no ready approvals to dispatch"
    if embedded_card == "provider_health":
        provider_health = payload.get("provider_health")
        controls = provider_health.get("controls") if isinstance(provider_health, dict) else None
        if isinstance(controls, list):
            for control in controls:
                if isinstance(control, dict) and control.get("command") == payload.get("next_command"):
                    blocker = control.get("blocker")
                    return str(blocker) if blocker else None
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
    payload.setdefault("control_registry_card", None)
    payload.setdefault("control_mode_card", None)
    payload.setdefault("provider_health", None)
    payload.setdefault("lineage_card", None)
    payload.setdefault("audit_card", None)
    payload.setdefault("artifacts_card", None)
    payload.setdefault("trace_card", None)
    payload.setdefault("capture_card", None)
    payload.setdefault("terminal_card", None)
    payload.setdefault("terminal_session_card", None)
    payload.setdefault("dispatch_preview_card", None)
    payload.setdefault("dispatch_batch_preview_card", None)
    payload.setdefault("runtime_action_card", None)
    payload.setdefault("startup_preview_card", None)
    payload.setdefault("provider_setup_card", None)
    payload.setdefault("provider_switch_card", None)
    payload.setdefault("agent_ready_card", None)
    payload.setdefault("leader_summary_card", None)
    payload.setdefault("run_start_card", None)
    payload.setdefault("run_progress_card", None)
    leader_action = payload.get("leader_action")
    payload.setdefault(
        "leader_action_card",
        leader_chat_action_card(leader_action) if isinstance(leader_action, dict) else None,
    )
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
            "deepseek": _doctor_api_provider_check("deepseek", deepseek),
            "openai_compatible": _doctor_api_provider_check("openai-compatible", openai_compatible),
            "codex_cli": _doctor_cli_provider_check("codex-cli"),
            "claude_cli": _doctor_cli_provider_check("claude-cli"),
        }
    )
    return 0 if ok else 1


def _doctor_api_provider_check(provider: str, doctor_result: tuple[bool, str]) -> dict[str, object]:
    return {
        "ok": doctor_result[0],
        "detail": doctor_result[1],
        "provider_backend": leader_provider_backend(provider),
        "provider_transport": leader_provider_transport(provider),
        "command_path": None,
        "setup_commands": _provider_setup_commands(provider),
    }


def _doctor_cli_provider_check(provider: str) -> dict[str, object]:
    command = {
        "codex-cli": "codex",
        "claude-cli": "claude",
    }[provider]
    command_path = _command_path(command)
    return {
        "ok": command_path is not None,
        "detail": f"{command} is available" if command_path else f"{command} is not found on PATH",
        "provider_backend": leader_provider_backend(provider),
        "provider_transport": leader_provider_transport(provider),
        "command_path": command_path,
        "setup_commands": _provider_setup_commands(provider),
    }


def _doctor_configured_leader(config: ProjectConfig | None) -> dict[str, object] | None:
    if config is None:
        return None
    return _leader_provider_readiness(
        agent_id=config.leader.agent_id,
        provider=config.leader.provider,
        model=config.leader.model,
        approval_mode=config.leader.approval_mode,
    )


def _leader_provider_readiness(
    *,
    agent_id: str,
    provider: str,
    model: str,
    approval_mode: str,
) -> dict[str, object]:
    leader_backend = leader_backend_identity(provider, model)
    if provider == "fake":
        return {
            "agent_id": agent_id,
            "provider": provider,
            "model": model,
            "approval_mode": approval_mode,
            "provider_backend": leader_provider_backend(provider),
            "provider_transport": leader_provider_transport(provider),
            "leader_backend": leader_backend,
            "ready": True,
            "supported": True,
            "missing_env": [],
            "detail": "fake provider is local and ready",
            "command_path": None,
            "setup_commands": _provider_setup_commands(provider),
        }
    required_env = {
        "deepseek": "DEEPSEEK_API_KEY",
        "openai-compatible": "AGENTDECK_LEADER_API_KEY",
    }.get(provider)
    cli_command = {
        "codex-cli": "codex",
        "claude-cli": "claude",
    }.get(provider)
    if cli_command is not None:
        command_path = _command_path(cli_command)
        ready = command_path is not None
        return {
            "agent_id": agent_id,
            "provider": provider,
            "model": model,
            "approval_mode": approval_mode,
            "provider_backend": leader_provider_backend(provider),
            "provider_transport": leader_provider_transport(provider),
            "leader_backend": leader_backend,
            "ready": ready,
            "supported": True,
            "missing_env": [],
            "detail": f"{cli_command} is available" if ready else f"{cli_command} is not found on PATH",
            "command_path": command_path,
            "setup_commands": _provider_setup_commands(provider),
        }
    if required_env is None:
        return {
            "agent_id": agent_id,
            "provider": provider,
            "model": model,
            "approval_mode": approval_mode,
            "provider_backend": leader_provider_backend(provider),
            "provider_transport": leader_provider_transport(provider),
            "leader_backend": leader_backend,
            "ready": False,
            "supported": False,
            "missing_env": [],
            "detail": f"unsupported leader provider: {provider}",
            "command_path": None,
            "setup_commands": [],
        }
    ready = bool(os.environ.get(required_env))
    detail = f"{required_env} is set" if ready else f"{required_env} is not set; provider calls are disabled"
    return {
        "agent_id": agent_id,
        "provider": provider,
        "model": model,
        "approval_mode": approval_mode,
        "provider_backend": leader_provider_backend(provider),
        "provider_transport": leader_provider_transport(provider),
        "leader_backend": leader_backend,
        "ready": ready,
        "supported": True,
        "missing_env": [] if ready else [required_env],
        "detail": detail,
        "command_path": None,
        "setup_commands": _provider_setup_commands(provider),
    }


def _leader_chat_provider_switch_card(
    project_view: dict[str, object],
    *,
    target_provider: str,
    target_model: str,
    require_ready: bool,
    command: str,
) -> dict[str, object]:
    leader = project_view.get("leader") if isinstance(project_view.get("leader"), dict) else {}
    approval_mode = str(leader.get("approval_mode") or "confirm")
    target_readiness = _leader_provider_readiness(
        agent_id="leader",
        provider=target_provider,
        model=target_model,
        approval_mode=approval_mode,
    )
    target_leader_backend = (
        target_readiness.get("leader_backend")
        if isinstance(target_readiness.get("leader_backend"), dict)
        else leader_backend_identity(target_provider, target_model)
    )
    control_kind = "guarded_set_provider" if require_ready else "set_provider"
    control_label = "Switch Leader provider if ready" if require_ready else "Switch Leader provider"
    target_ready = target_readiness.get("ready") is True
    provider_control_enabled = not require_ready or target_ready
    provider_control_blocker = None if provider_control_enabled else "target provider is not ready"
    setup_controls = []
    if not provider_control_enabled and isinstance(target_readiness.get("setup_commands"), list):
        setup_controls = [
            {
                "kind": "setup",
                "label": "Run provider setup",
                "command": str(setup_command),
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            }
            for setup_command in target_readiness["setup_commands"]
        ]
    return {
        "mode": "provider_switch",
        "title": "Switch Leader provider",
        "current_provider": leader.get("provider"),
        "current_model": leader.get("model"),
        "target_provider": target_provider,
        "target_model": target_model,
        "target_leader_backend": target_leader_backend,
        "target_readiness": target_readiness,
        "require_ready": require_ready,
        "command": command,
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
                "kind": control_kind,
                "label": control_label,
                "command": command,
                "safety": "explicit_user",
                "enabled": provider_control_enabled,
                "blocker": provider_control_blocker,
            },
            *setup_controls,
        ],
    }


def _command_path(command: str) -> str | None:
    return shutil.which(command)


def _command_available(command: str) -> bool:
    return _command_path(command) is not None


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


def artifacts_command(_args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    project_view = _project_view_payload_or_error(config, store)
    if project_view is None:
        return 1
    payload = _artifacts_card_payload(project_view)
    validation = validate_artifacts_contract(payload)
    if not validation["ok"]:
        print("Artifacts contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1
    _print_json(payload)
    return 0


def _artifacts_card_payload(project_view: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "artifacts_command": "agentdeck artifacts",
        "project_view_contract": "agentdeck contract project-view",
        "trace_contract": "agentdeck contract trace",
        "trace_command_template": "agentdeck trace --id <id>",
        "artifacts": project_view.get("artifacts"),
    }


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
    next_command = recovery.get("next_command") if isinstance(recovery, dict) else None
    if _continue_should_promote_dispatch_ready(project_view, recovery, recommended_action):
        next_command = "agentdeck approval dispatch-ready --confirm"
        recommended_action = {
            "label": "Dispatch ready approvals",
            "command": next_command,
            "safety": "explicit_runtime",
            "requires_explicit_user": True,
            "source": "approval",
            "target_id": "dispatch_ready",
        }
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
        "next_command": next_command,
        "recommended_action": recommended_action,
        "pending": recovery.get("pending") if isinstance(recovery, dict) else None,
        "leader_action": leader_action,
        "action_detail_command": action_detail_command,
    }


def _continue_should_promote_dispatch_ready(
    project_view: dict[str, object], recovery: object, recommended_action: object
) -> bool:
    if not isinstance(recovery, dict) or not isinstance(recommended_action, dict):
        return False
    if recovery.get("status") != "dispatch_ready" or recommended_action.get("source") != "approval":
        return False
    pending = recovery.get("pending") if isinstance(recovery.get("pending"), dict) else {}
    if int(pending.get("approved_approvals", 0)) <= 1:
        return False
    approvals = project_view.get("approvals") if isinstance(project_view.get("approvals"), dict) else {}
    if int(approvals.get("approved", 0)) <= 1:
        return False
    return True


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
    return source if source in ("leader_action", "inbox", "approval", "provider_health", "runtime", "reply") else "none"


def _workbench_snapshot_payload(
    project_view: dict[str, object], store: StateStore, since_event_id: str | None = None
) -> dict[str, object]:
    continue_card = _continue_card_payload(project_view, store)
    inbox_card, approval_card = _leader_chat_recovery_cards(project_view, store)
    leader_inbox_card = _inbox_queue_payload("leader", store)
    recovery = project_view.get("recovery", {})
    active_queue_source = _active_queue_source(project_view)
    leader_action = continue_card.get("leader_action")
    leader_card = _workbench_leader_card(project_view)
    provider_health = _workbench_provider_health(project_view)
    runtime_card = _workbench_runtime_card(project_view)
    agent_ready_card = _agent_ready_card_payload(project_view)
    terminal_session_card = _workbench_terminal_session_card(load_config(store.root), runtime_card)
    role_card = _workbench_role_card(project_view)
    ledger_card = _workbench_ledger_card(project_view)
    lineage_card = _workbench_lineage_card(project_view, inbox_card, leader_inbox_card)
    queue_card = _workbench_queue_card(project_view, continue_card, active_queue_source)
    operator_card = _workbench_operator_card(project_view, continue_card, active_queue_source)
    audit_card = _workbench_audit_card(project_view)
    artifacts_card = _artifacts_card_payload(project_view)
    leader_summary_card = _workbench_leader_summary_card(store)
    contracts_card = _workbench_contracts_card()
    control_mode_card = _workbench_control_mode_card(project_view)
    run_progress_card = _workbench_run_progress_card(store)
    payload = {
        "ok": True,
        "mode": "workbench",
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "project_view": project_view,
        "leader_actions": project_view.get("leader_actions"),
        "leader_card": leader_card,
        "provider_health": provider_health,
        "runtime_card": runtime_card,
        "agent_ready_card": agent_ready_card,
        "terminal_session_card": terminal_session_card,
        "role_card": role_card,
        "ledger_card": ledger_card,
        "lineage_card": lineage_card,
        "queue_card": queue_card,
        "operator_card": operator_card,
        "audit_card": audit_card,
        "artifacts_card": artifacts_card,
        "leader_summary_card": leader_summary_card,
        "contracts_card": contracts_card,
        "control_mode_card": control_mode_card,
        "recovery": recovery,
        "next_command": continue_card.get("next_command"),
        "continue_card": continue_card,
        "active_queue_source": active_queue_source,
        "run_progress_card": run_progress_card,
        "inbox_card": inbox_card,
        "leader_inbox_card": leader_inbox_card,
        "approval_card": approval_card,
        "leader_action": leader_action if isinstance(leader_action, dict) else None,
        "control_registry": [],
        "change_summary": _workbench_change_summary(store, since_event_id),
    }
    payload["control_registry"] = _workbench_control_registry(payload)
    return payload


def _workbench_run_progress_card(store: StateStore) -> dict[str, object] | None:
    plans = store.list_plans()
    if not plans:
        return None
    latest_plan_id = str(plans[-1]["plan_id"])
    return _run_progress_payload(store, latest_plan_id)


def _workbench_leader_summary_card(store: StateStore) -> dict[str, object] | None:
    plans = store.list_plans()
    if not plans:
        return None
    latest_plan_id = str(plans[-1]["plan_id"])
    review = store.leader_review(latest_plan_id)
    if review.get("next_action") != "summarize":
        return None
    return _leader_summary_payload(store, latest_plan_id)


def _control_mode_from_approval_mode(approval_mode: object) -> str:
    return "approve" if approval_mode in {"auto_approve", "approve"} else "ask"


def _workbench_control_mode_card(project_view: dict[str, object]) -> dict[str, object]:
    leader = project_view.get("leader") if isinstance(project_view.get("leader"), dict) else {}
    approval_mode = str(leader.get("approval_mode", "confirm"))
    current_mode = _control_mode_from_approval_mode(approval_mode)
    available_modes = [
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
    ]
    return {
        "mode": "control_mode",
        "title": "Control mode",
        "current_mode": current_mode,
        "approval_mode": approval_mode,
        "default_safety": "inspect" if current_mode == "ask" else "safe_apply",
        "available_modes": available_modes,
        "active_controls": [
            _control(kind="inspect", label="Inspect policy", command="agentdeck workbench", safety="inspect"),
            *_control_mode_set_controls(current_mode, available_modes),
        ],
        "set_mode_command_template": "agentdeck policy set-mode --mode <mode>",
        "policy_source": ".agentdeck/config.toml:leader.approval_mode",
    }


def _control_mode_set_controls(current_mode: str, available_modes: list[dict[str, object]]) -> list[dict[str, object]]:
    controls: list[dict[str, object]] = []
    for option in available_modes:
        mode = str(option.get("mode"))
        enabled = bool(option.get("enabled")) and mode != current_mode
        blocker = "already current mode" if mode == current_mode else option.get("blocker")
        safety = "explicit_user" if mode == "approve" else option.get("safety")
        controls.append(
            _control(
                kind="set_mode",
                label=str(option.get("label", mode)),
                command=f"agentdeck policy set-mode --mode {mode}",
                safety=str(safety),
                enabled=enabled,
                blocker=str(blocker) if blocker else None,
            )
        )
    return controls


LEADER_PROVIDER_SWITCHES: tuple[tuple[str, str, str], ...] = (
    ("fake", "fake-plan", "Use fake"),
    ("deepseek", "deepseek-chat", "Use DeepSeek"),
    ("openai-compatible", "openai-compatible-default", "Use OpenAI-compatible"),
    ("codex-cli", "codex-default", "Use Codex CLI"),
    ("claude-cli", "claude-default", "Use Claude CLI"),
)


def _leader_provider_controls(current_provider: str) -> list[dict[str, object]]:
    controls: list[dict[str, object]] = []
    for provider, model, label in LEADER_PROVIDER_SWITCHES:
        enabled = provider != current_provider
        command = f"agentdeck leader set-provider --provider {provider} --model {model}"
        blocker = None if enabled else "already current provider"
        controls.append(
            _control(
                kind="set_provider",
                label=label,
                command=command,
                safety="explicit_user",
                enabled=enabled,
                blocker=blocker,
            )
        )
        controls.append(
            _control(
                kind="guarded_set_provider",
                label=f"{label} if ready",
                command=f"{command} --require-ready",
                safety="explicit_user",
                enabled=enabled,
                blocker=blocker,
            )
        )
        setup_label = label.replace("Use ", "Setup ", 1)
        for setup_command in _provider_setup_commands(provider):
            controls.append(
                _control(
                    kind="setup_provider",
                    label=setup_label,
                    command=setup_command,
                    safety="explicit_user",
                    enabled=True,
                    blocker=None,
                )
            )
    return controls


def _workbench_control_registry(payload: dict[str, object]) -> list[dict[str, object]]:
    registry: list[dict[str, object]] = []
    leader_card = payload.get("leader_card") if isinstance(payload.get("leader_card"), dict) else {}
    _append_workbench_control_registry_items(
        registry,
        scope="leader",
        card="leader_card",
        agent_id=leader_card.get("agent_id") or "leader",
        controls=leader_card.get("controls"),
    )
    provider_health = payload.get("provider_health") if isinstance(payload.get("provider_health"), dict) else {}
    _append_workbench_control_registry_items(
        registry,
        scope="provider",
        card="provider_health",
        agent_id=provider_health.get("agent_id") or "leader",
        controls=provider_health.get("controls"),
    )
    control_mode_card = payload.get("control_mode_card") if isinstance(payload.get("control_mode_card"), dict) else {}
    _append_workbench_control_registry_items(
        registry,
        scope="policy",
        card="control_mode_card",
        agent_id=None,
        controls=control_mode_card.get("active_controls"),
    )
    terminal_session_card = (
        payload.get("terminal_session_card") if isinstance(payload.get("terminal_session_card"), dict) else {}
    )
    _append_workbench_control_registry_items(
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
            _append_workbench_control_registry_items(
                registry,
                scope="terminal_session",
                card="terminal_session_card",
                agent_id=terminal.get("agent_id"),
                controls=terminal.get("controls"),
            )
    agent_ready_card = payload.get("agent_ready_card") if isinstance(payload.get("agent_ready_card"), dict) else {}
    _append_workbench_control_registry_items(
        registry,
        scope="agent_ready",
        card="agent_ready_card",
        agent_id=None,
        controls=agent_ready_card.get("controls"),
    )
    startup_preview_card = (
        payload.get("startup_preview_card") if isinstance(payload.get("startup_preview_card"), dict) else {}
    )
    _append_workbench_control_registry_items(
        registry,
        scope="startup_preview",
        card="startup_preview_card",
        agent_id=None,
        controls=startup_preview_card.get("controls"),
    )
    runtime_action_card = (
        payload.get("runtime_action_card") if isinstance(payload.get("runtime_action_card"), dict) else {}
    )
    _append_workbench_control_registry_items(
        registry,
        scope="runtime_action",
        card="runtime_action_card",
        agent_id=runtime_action_card.get("agent_id"),
        controls=runtime_action_card.get("controls"),
    )
    startup_preview_items = (
        startup_preview_card.get("items") if isinstance(startup_preview_card.get("items"), list) else []
    )
    for item in startup_preview_items:
        if isinstance(item, dict):
            _append_workbench_control_registry_items(
                registry,
                scope="startup_preview",
                card="startup_preview_card",
                agent_id=item.get("agent_id"),
                controls=item.get("controls"),
            )
    runtime_card = payload.get("runtime_card") if isinstance(payload.get("runtime_card"), dict) else {}
    runtime_agents = runtime_card.get("agents") if isinstance(runtime_card.get("agents"), list) else []
    for agent in runtime_agents:
        if isinstance(agent, dict):
            _append_workbench_control_registry_items(
                registry,
                scope="runtime",
                card="runtime_card",
                agent_id=agent.get("agent_id"),
                controls=agent.get("controls"),
            )
    role_card = payload.get("role_card") if isinstance(payload.get("role_card"), dict) else {}
    role_agents = role_card.get("agents") if isinstance(role_card.get("agents"), list) else []
    for agent in role_agents:
        if isinstance(agent, dict):
            _append_workbench_control_registry_items(
                registry,
                scope="role",
                card="role_card",
                agent_id=agent.get("agent_id"),
                controls=agent.get("controls"),
            )
    inbox_card = payload.get("inbox_card") if isinstance(payload.get("inbox_card"), dict) else {}
    _append_workbench_inbox_control_registry_items(
        registry,
        card="inbox_card",
        inbox_card=inbox_card,
    )
    leader_inbox_card = payload.get("leader_inbox_card") if isinstance(payload.get("leader_inbox_card"), dict) else {}
    _append_workbench_inbox_control_registry_items(
        registry,
        card="leader_inbox_card",
        inbox_card=leader_inbox_card,
    )
    operator_card = payload.get("operator_card") if isinstance(payload.get("operator_card"), dict) else {}
    _append_workbench_control_registry_items(
        registry,
        scope="operator",
        card="operator_card",
        agent_id=None,
        controls=operator_card.get("controls"),
    )
    return registry


def _append_workbench_inbox_control_registry_items(
    registry: list[dict[str, object]],
    *,
    card: str,
    inbox_card: dict[str, object],
) -> None:
    agent_id = inbox_card.get("agent_id")
    items = inbox_card.get("items") if isinstance(inbox_card.get("items"), list) else []
    for item in items:
        if isinstance(item, dict):
            _append_workbench_control_registry_items(
                registry,
                scope="inbox",
                card=card,
                agent_id=agent_id,
                controls=item.get("controls"),
            )


def _append_workbench_control_registry_items(
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
        "api_backed": provider in ("deepseek", "openai-compatible"),
        "leader_backend": leader_backend_identity(provider, str(leader.get("model") or "")),
        "chat_command": "agentdeck leader chat --message <text>",
        "continue_command": "agentdeck continue",
        "review_command_template": "agentdeck leader review --plan-id <plan_id>",
        "actions_command": "agentdeck leader actions",
        "status_command": "agentdeck status",
        "controls": [
            _control(
                kind="chat",
                label="Ask Leader",
                command="agentdeck leader chat --message <text>",
                safety="explicit_user",
                enabled=False,
                blocker="requires message text",
            ),
            _control(kind="continue", label="Continue", command="agentdeck continue", safety="inspect"),
            _control(
                kind="review",
                label="Review plan",
                command="agentdeck leader review --plan-id <plan_id>",
                safety="inspect",
                enabled=False,
                blocker="requires plan_id",
            ),
            _control(kind="actions", label="Leader actions", command="agentdeck leader actions", safety="inspect"),
            _control(kind="status", label="Project status", command="agentdeck status", safety="inspect"),
        ],
    }


def _workbench_provider_health(project_view: dict[str, object]) -> dict[str, object]:
    leader = project_view.get("leader") if isinstance(project_view.get("leader"), dict) else {}
    provider = str(leader.get("provider", ""))
    base = {
        "agent_id": leader.get("agent_id"),
        "provider": provider,
        "model": leader.get("model"),
        "approval_mode": leader.get("approval_mode"),
        "api_backed": provider in ("deepseek", "openai-compatible"),
        "provider_backend": leader_provider_backend(provider),
        "provider_transport": leader_provider_transport(provider),
        "leader_backend": leader.get("leader_backend")
        or leader_backend_identity(provider, str(leader.get("model") or "")),
        "command_path": None,
        "doctor_contract": "agentdeck contract doctor",
        "controls": _leader_provider_controls(provider),
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
    cli_command = {
        "codex-cli": "codex",
        "claude-cli": "claude",
    }.get(provider)
    if cli_command is not None:
        command_path = _command_path(cli_command)
        ready = command_path is not None
        return {
            **base,
            "supported": True,
            "ready": ready,
            "missing_env": [],
            "detail": f"{cli_command} is available" if ready else f"{cli_command} is not found on PATH",
            "command_path": command_path,
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
    if provider == "codex-cli":
        return ["codex login", "codex doctor"]
    if provider == "claude-cli":
        return ["claude auth", "claude doctor"]
    return []


def _provider_for_setup_command(command: str) -> str | None:
    for provider, _model, _label in LEADER_PROVIDER_SWITCHES:
        if command in _provider_setup_commands(provider):
            return provider
    return None


def _control_registry_id_for_command(
    workbench_card: dict[str, object],
    *,
    scope: str,
    kind: str,
    command: str,
) -> str | None:
    registry = workbench_card.get("control_registry") if isinstance(workbench_card.get("control_registry"), list) else []
    for item in registry:
        if not isinstance(item, dict):
            continue
        if item.get("scope") == scope and item.get("kind") == kind and item.get("command") == command:
            control_id = item.get("control_id")
            return str(control_id) if isinstance(control_id, str) and control_id else None
    return None


def _leader_chat_provider_setup_card(
    *,
    target_provider: str,
    target_model: str,
    setup_commands: list[str],
    recommended_command: str,
    recommended_control_id: str | None,
    followup_switch_command: str,
    require_ready: bool,
    control_registry_workbench: dict[str, object],
) -> dict[str, object]:
    controls: list[dict[str, object]] = []
    for setup_command in setup_commands:
        controls.append(
            {
                "kind": "setup_provider",
                "label": "Run provider setup",
                "command": setup_command,
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
                "control_id": _control_registry_id_for_command(
                    control_registry_workbench,
                    scope="provider",
                    kind="setup_provider",
                    command=setup_command,
                ),
            }
        )
    controls.append(
        {
            "kind": "guarded_set_provider" if require_ready else "set_provider",
            "label": "Switch Leader provider if ready" if require_ready else "Switch Leader provider",
            "command": followup_switch_command,
            "safety": "explicit_user",
            "enabled": True,
            "blocker": None,
        }
    )
    return {
        "mode": "provider_setup",
        "title": "Set up Leader provider",
        "target_provider": target_provider,
        "target_model": target_model,
        "setup_commands": setup_commands,
        "recommended_command": recommended_command,
        "recommended_control_id": recommended_control_id,
        "followup_switch_command": followup_switch_command,
        "require_ready": require_ready,
        "safety": "explicit_user",
        "requires_explicit_user": True,
        "mutates_config": False,
        "controls": controls,
    }


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
                    "controls": _role_agent_controls(agent_id),
                }
            )
    return {
        "count": len(role_agents),
        "agents": role_agents,
        "assign_command_template": (
            "agentdeck agent assign-role --agent <agent_id> --role <role> --role-prompt <role_prompt>"
        ),
    }


def _role_agent_controls(agent_id: str) -> list[dict[str, object]]:
    return [
        _control(
            kind="assign_role",
            label="Assign role",
            command=f"agentdeck agent assign-role --agent {agent_id} --role <role> --role-prompt <role_prompt>",
            safety="explicit_user",
            enabled=False,
            blocker="requires role and role_prompt",
        )
    ]


def _agent_assign_role_command(agent_id: str, role: str, role_prompt: str) -> str:
    return " ".join(
        [
            "agentdeck",
            "agent",
            "assign-role",
            "--agent",
            _quote_assign_role_arg(agent_id),
            "--role",
            _quote_assign_role_arg(role),
            "--role-prompt",
            _quote_assign_role_arg(role_prompt),
        ]
    )


def _quote_assign_role_arg(value: str) -> str:
    if re.fullmatch(r"[\w@%+=:,./-]+", value):
        return value
    return shlex.quote(value)


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
        "controls_contract": "agentdeck contract controls",
        "agent_runtime_contract": "agentdeck contract agent-runtime",
        "leader_chat_contract": "agentdeck contract leader-chat",
        "leader_review_contract": "agentdeck contract leader-review",
        "leader_summary_contract": "agentdeck contract leader-summary",
        "project_view_contract": "agentdeck contract project-view",
        "events_contract": "agentdeck contract events",
        "doctor_contract": "agentdeck contract doctor",
        "run_contract": "agentdeck contract run",
        "artifacts_contract": "agentdeck contract artifacts",
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
    action_kind = source if source in ("inbox", "approval", "leader_action", "provider_health", "runtime", "reply") else "none"
    can_apply = bool(leader_action.get("can_apply")) if action_kind == "leader_action" else False
    apply_command = leader_action.get("apply_command") if can_apply else None
    command = recommended_action.get("command")
    explicit_command = leader_action.get("explicit_command") or recommended_action.get("command")
    explicit_label = "Run explicit command"
    explicit_kind = "explicit"
    if action_kind == "reply":
        explicit_label = "Capture reply"
        explicit_kind = "capture_reply"
    if action_kind == "approval" and _workbench_approved_approval_count(project_view) > 1:
        action_kind = "approval_dispatch_ready"
        command = "agentdeck approval dispatch-ready --confirm"
        explicit_command = "agentdeck approval dispatch-ready --confirm"
        explicit_label = "Dispatch ready approvals"
        explicit_kind = "dispatch_ready"
    preview_command = _workbench_operator_preview_command(action_kind, target_id)
    action_blocker = leader_action.get("apply_blocker") or _workbench_operator_action_blocker(
        project_view, action_kind, target_id
    )
    return {
        "status": recovery.get("status"),
        "reason": recovery.get("reason"),
        "label": recommended_action.get("label"),
        "command": command,
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
            blocker=action_blocker,
            explicit_label=explicit_label,
            explicit_kind=explicit_kind,
        ),
        "active_queue_source": active_queue_source,
        "action_kind": action_kind,
        "can_apply": can_apply,
        "apply_command": apply_command,
        "explicit_command": explicit_command,
        "blocker": action_blocker,
    }


def _queue_mode_next_command(continue_card: dict[str, object], operator_card: dict[str, object]) -> object:
    command = operator_card.get("command")
    return command if command else continue_card.get("next_command")


def _workbench_operator_controls(
    *,
    preview_command: object,
    apply_command: object,
    explicit_command: object,
    safety: object,
    can_apply: bool,
    blocker: object,
    explicit_label: str = "Run explicit command",
    explicit_kind: str = "explicit",
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
            "kind": explicit_kind,
            "label": explicit_label,
            "command": explicit_command,
            "safety": safety,
            "enabled": explicit_command is not None and not blocker,
            "blocker": blocker or (None if explicit_command is not None else "no explicit command available"),
        }
    )
    return controls


def _workbench_operator_action_blocker(
    project_view: dict[str, object], action_kind: str, target_id: object
) -> str | None:
    if action_kind == "approval_dispatch_ready":
        return _workbench_dispatch_ready_blocker(project_view)
    return _workbench_operator_runtime_blocker(project_view, action_kind, target_id)


def _workbench_operator_runtime_blocker(
    project_view: dict[str, object], action_kind: str, target_id: object
) -> str | None:
    if action_kind != "approval" or not target_id:
        return None
    approval = _project_view_approval_item(project_view, target_id)
    agent_id = approval.get("agent_id") if isinstance(approval, dict) else None
    if not agent_id:
        return None
    agent = _project_view_agent_item(project_view, agent_id)
    runtime = agent.get("runtime") if isinstance(agent, dict) and isinstance(agent.get("runtime"), dict) else {}
    pane_id = runtime.get("pane_id") if isinstance(runtime, dict) else None
    status = str(runtime.get("status", "configured")) if isinstance(runtime, dict) else "configured"
    if not pane_id:
        return f"agent is not spawned: {agent_id}"
    if status != "running":
        return f"agent runtime is {status}: {agent_id}"
    return None


def _workbench_dispatch_ready_blocker(project_view: dict[str, object]) -> str | None:
    approved_items = _workbench_approved_approval_items(project_view)
    if not approved_items:
        return "no approved approvals"
    for approval in approved_items:
        agent_id = approval.get("agent_id")
        if _project_view_agent_is_running(project_view, agent_id):
            return None
    return "no approved approvals have running agents"


def _workbench_approved_approval_count(project_view: dict[str, object]) -> int:
    approvals = project_view.get("approvals") if isinstance(project_view.get("approvals"), dict) else {}
    return int(approvals.get("approved", 0))


def _workbench_approved_approval_items(project_view: dict[str, object]) -> list[dict[str, object]]:
    approvals = project_view.get("approvals") if isinstance(project_view.get("approvals"), dict) else {}
    items = approvals.get("items") if isinstance(approvals.get("items"), list) else []
    return [item for item in items if isinstance(item, dict) and item.get("status") == "approved"]


def _project_view_agent_is_running(project_view: dict[str, object], agent_id: object) -> bool:
    agent = _project_view_agent_item(project_view, agent_id)
    runtime = agent.get("runtime") if isinstance(agent, dict) and isinstance(agent.get("runtime"), dict) else {}
    return bool(runtime.get("pane_id")) and runtime.get("status") == "running"


def _project_view_approval_item(project_view: dict[str, object], approval_id: object) -> dict[str, object] | None:
    approvals = project_view.get("approvals") if isinstance(project_view.get("approvals"), dict) else {}
    items = approvals.get("items") if isinstance(approvals.get("items"), list) else []
    for item in items:
        if isinstance(item, dict) and item.get("approval_id") == approval_id:
            return item
    return None


def _project_view_agent_item(project_view: dict[str, object], agent_id: object) -> dict[str, object] | None:
    agents = project_view.get("agents") if isinstance(project_view.get("agents"), list) else []
    for item in agents:
        if isinstance(item, dict) and item.get("agent_id") == agent_id:
            return item
    return None


def _workbench_operator_preview_command(action_kind: str, target_id: object) -> str:
    if action_kind == "leader_action" and target_id:
        return f"agentdeck leader action --action-id {target_id}"
    if action_kind == "inbox" and target_id:
        return f"agentdeck trace --id {target_id}"
    if action_kind == "reply" and target_id:
        return f"agentdeck trace --id {target_id}"
    if action_kind in ("approval", "approval_dispatch_ready"):
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
    artifacts = project_view.get("artifacts") if isinstance(project_view.get("artifacts"), dict) else {}
    inbox = project_view.get("inbox") if isinstance(project_view.get("inbox"), dict) else {}
    trace_commands = _workbench_trace_commands(messages, jobs, replies, artifacts, inbox)
    return {
        "messages": messages,
        "jobs": jobs,
        "replies": replies,
        "artifacts": artifacts,
        "inbox": inbox,
        "trace_commands": trace_commands,
    }


def _workbench_lineage_card(
    project_view: dict[str, object],
    inbox_card: dict[str, object] | None,
    leader_inbox_card: dict[str, object] | None,
) -> dict[str, object]:
    messages = project_view.get("messages") if isinstance(project_view.get("messages"), dict) else {}
    jobs = project_view.get("jobs") if isinstance(project_view.get("jobs"), dict) else {}
    replies = project_view.get("replies") if isinstance(project_view.get("replies"), dict) else {}
    inbox = project_view.get("inbox") if isinstance(project_view.get("inbox"), dict) else {}
    job_items = _summary_items_by("message_id", jobs)
    reply_items = _summary_items_by("message_id", replies)
    inbox_items = _workbench_lineage_inbox_items(inbox, inbox_card, leader_inbox_card)
    inbox_items_by_message = _items_by("message_id", inbox_items)
    recent_paths = []
    for message in _summary_items(messages)[-5:]:
        if not isinstance(message, dict):
            continue
        message_id = message.get("message_id")
        job = job_items.get(message_id, {})
        reply = reply_items.get(message_id, {})
        inbox_item = _workbench_lineage_inbox_item_for_message(message_id, reply.get("reply_id"), inbox_items_by_message)
        trace_id = message_id or job.get("job_id") or reply.get("reply_id") or inbox_item.get("inbox_id")
        recent_paths.append(
            {
                "message_id": message_id,
                "job_id": job.get("job_id"),
                "reply_id": reply.get("reply_id"),
                "inbox_id": inbox_item.get("inbox_id"),
                "from_actor": message.get("from_actor"),
                "to_agent": message.get("to_agent"),
                "from_agent": reply.get("from_agent"),
                "to_actor": reply.get("to_actor"),
                "task": message.get("task") or inbox_item.get("task"),
                "status": _workbench_lineage_status(message, job, reply, inbox_item),
                "trace_command": _trace_command(trace_id) if trace_id else None,
            }
        )
    return {
        "mode": "lineage",
        "title": "Communication lineage",
        "message_count": int(messages.get("count", 0)),
        "job_count": int(jobs.get("count", 0)),
        "reply_count": int(replies.get("count", 0)),
        "inbox_count": int(inbox.get("total", 0)),
        "trace_command_template": "agentdeck trace --id <id>",
        "recent_paths": recent_paths,
    }


def _summary_items(summary: dict[str, object]) -> list[object]:
    items = summary.get("items")
    return items if isinstance(items, list) else []


def _summary_items_by(key: str, summary: dict[str, object]) -> dict[object, dict[str, object]]:
    return _items_by(key, _summary_items(summary))


def _items_by(key: str, items: list[object]) -> dict[object, dict[str, object]]:
    indexed: dict[object, dict[str, object]] = {}
    for item in items:
        if isinstance(item, dict) and item.get(key) is not None:
            indexed[item.get(key)] = item
    return indexed


def _workbench_lineage_inbox_items(
    inbox: dict[str, object],
    inbox_card: dict[str, object] | None,
    leader_inbox_card: dict[str, object] | None,
) -> list[object]:
    items: list[object] = []
    heads = inbox.get("heads")
    if isinstance(heads, dict):
        items.extend(head for head in heads.values() if isinstance(head, dict))
    for card in (inbox_card, leader_inbox_card):
        if isinstance(card, dict) and isinstance(card.get("items"), list):
            items.extend(card["items"])
    return items


def _workbench_lineage_inbox_item_for_message(
    message_id: object,
    reply_id: object,
    inbox_items_by_message: dict[object, dict[str, object]],
) -> dict[str, object]:
    item = inbox_items_by_message.get(message_id)
    if not reply_id or not item:
        return item or {}
    if item.get("reply_id") == reply_id:
        return item
    return item


def _workbench_lineage_status(
    message: dict[str, object],
    job: dict[str, object],
    reply: dict[str, object],
    inbox_item: dict[str, object],
) -> str:
    if reply and inbox_item:
        return "reply_pending_ack"
    if reply:
        return "replied"
    if inbox_item:
        return "inbox_pending"
    if job:
        return str(job.get("status") or message.get("status") or "dispatched")
    return str(message.get("status") or "unknown")


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
                    "terminal_command": f"agentdeck agent terminal --agent {agent_id}",
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


def _agent_ready_card_payload(project_view: dict[str, object]) -> dict[str, object]:
    runtime_card = _workbench_runtime_card(project_view)
    agents = runtime_card.get("agents") if isinstance(runtime_card.get("agents"), list) else []
    running_count = 0
    spawn_commands: list[str] = []
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        if agent.get("status") == "running" and agent.get("pane_id"):
            running_count += 1
        else:
            spawn_command = agent.get("spawn_command")
            if spawn_command:
                spawn_commands.append(str(spawn_command))
    total_count = len(agents)
    not_running_count = total_count - running_count
    dispatch_ready_command = "agentdeck approval dispatch-ready --confirm"
    spawn_ready_command = "agentdeck agent spawn-ready --confirm"
    next_command = (
        spawn_ready_command
        if len(spawn_commands) > 1
        else spawn_commands[0]
        if spawn_commands
        else dispatch_ready_command
    )
    controls = [
        _control(
            kind="inspect",
            label="Inspect readiness",
            command="agentdeck agent ready",
            safety="inspect",
        ),
    ]
    if len(spawn_commands) > 1:
        controls.append(
            _control(
                kind="spawn_ready",
                label="Spawn ready agents",
                command=spawn_ready_command,
                safety="explicit_runtime",
            )
        )
    elif spawn_commands:
        controls.append(
            _control(
                kind="spawn",
                label="Spawn agent",
                command=spawn_commands[0],
                safety="explicit_runtime",
            )
        )
    else:
        controls.append(
            _control(
                kind="dispatch_ready",
                label="Dispatch ready approvals",
                command=dispatch_ready_command,
                safety="explicit_runtime",
            )
        )
    controls.append(
        _control(
            kind="refresh_runtime",
            label="Refresh runtime",
            command=str(runtime_card.get("refresh_command") or "agentdeck agent refresh"),
            safety="explicit_runtime",
        )
    )
    return {
        "ok": True,
        "mode": "agent_runtime_ready",
        "runtime_backend": project_view.get("runtime_backend"),
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


def _runtime_action_card_payload(
    runtime_card: dict[str, object],
    *,
    action: str,
    agent_id: str,
    command: str,
    preview_text: str | None = None,
) -> dict[str, object] | None:
    agents = runtime_card.get("agents") if isinstance(runtime_card.get("agents"), list) else []
    target = next(
        (agent for agent in agents if isinstance(agent, dict) and agent.get("agent_id") == agent_id),
        None,
    )
    if target is None:
        return None
    title = (
        "Send input to {agent_id}".format(agent_id=agent_id)
        if action == "send"
        else f"Stop {agent_id}"
        if action == "stop"
        else "Runtime action"
    )
    controls = [
        _control(
            kind="inspect",
            label=f"Inspect {agent_id} runtime",
            command=str(target.get("terminal_command") or f"agentdeck agent terminal --agent {agent_id}"),
            safety="inspect",
        ),
        _control(
            kind=action,
            label=title,
            command=command,
            safety="explicit_runtime",
            enabled=target.get("status") == "running",
            blocker=None if target.get("status") == "running" else f"agent is not running: {agent_id}",
        ),
    ]
    return {
        "mode": "runtime_action",
        "title": title,
        "action": action,
        "agent_id": agent_id,
        "role": target.get("role"),
        "runtime_status": target.get("status"),
        "pane_id": target.get("pane_id"),
        "command": command,
        "preview_text": preview_text,
        "requires_explicit_user": True,
        "safety": "explicit_runtime",
        "blocker": None if target.get("status") == "running" else f"agent is not running: {agent_id}",
        "controls": controls,
    }


def _startup_preview_card_payload(
    agent_ready_card: dict[str, object],
    *,
    target_agent_id: str | None = None,
    next_command: str | None = None,
) -> dict[str, object]:
    runtime_card = agent_ready_card.get("runtime_card") if isinstance(agent_ready_card.get("runtime_card"), dict) else {}
    agents = runtime_card.get("agents") if isinstance(runtime_card.get("agents"), list) else []
    items: list[dict[str, object]] = []
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        agent_id = str(agent.get("agent_id"))
        if target_agent_id is not None and agent_id != target_agent_id:
            continue
        status = str(agent.get("status") or "unknown")
        pane_id = agent.get("pane_id")
        if status == "running" and pane_id:
            continue
        spawn_command = str(agent.get("spawn_command") or f"agentdeck agent spawn --agent {agent_id}")
        blocker = None if spawn_command else f"missing spawn command: {agent_id}"
        items.append(
            {
                "agent_id": agent_id,
                "role": agent.get("role"),
                "runtime_status": status,
                "pane_id": pane_id,
                "spawn_command": spawn_command,
                "terminal_command": str(agent.get("terminal_command") or f"agentdeck agent terminal --agent {agent_id}"),
                "blocker": blocker,
                "controls": [
                    _control(
                        kind="inspect",
                        label="Inspect runtime",
                        command="agentdeck agent ready",
                        safety="inspect",
                    ),
                    _control(
                        kind="spawn",
                        label=f"Spawn {agent_id}",
                        command=spawn_command,
                        safety="explicit_runtime",
                        enabled=blocker is None,
                        blocker=blocker,
                    ),
                ],
            }
        )
    ready_count = sum(1 for item in items if item.get("blocker") is None)
    blocked_count = len(items) - ready_count
    spawn_ready_command = str(agent_ready_card.get("spawn_ready_command") or "agentdeck agent spawn-ready --confirm")
    resolved_next_command = str(next_command or agent_ready_card.get("next_command") or spawn_ready_command)
    blocker = None if ready_count else "no agents need startup"
    primary_control = (
        _control(
            kind="spawn",
            label=f"Spawn {resolved_next_command.removeprefix('agentdeck agent spawn --agent ')}",
            command=resolved_next_command,
            safety="explicit_runtime",
            enabled=ready_count > 0,
            blocker=blocker,
        )
        if resolved_next_command.startswith("agentdeck agent spawn --agent ")
        else _control(
            kind="spawn_ready",
            label="Spawn ready agents",
            command=spawn_ready_command,
            safety="explicit_runtime",
            enabled=ready_count > 0,
            blocker=blocker,
        )
    )
    controls = [
        _control(
            kind="inspect",
            label="Inspect readiness",
            command="agentdeck agent ready",
            safety="inspect",
        ),
        primary_control,
    ]
    return {
        "mode": "startup_preview",
        "title": "Agent startup preview",
        "next_command": resolved_next_command,
        "spawn_ready_command": spawn_ready_command,
        "count": len(items),
        "ready_count": ready_count,
        "blocked_count": blocked_count,
        "requires_explicit_user": True,
        "safety": "explicit_runtime",
        "blocker": blocker,
        "items": items,
        "controls": controls,
    }


def _workbench_terminal_session_card(config: ProjectConfig, runtime_card: dict[str, object]) -> dict[str, object]:
    agents = runtime_card.get("agents") if isinstance(runtime_card.get("agents"), list) else []
    terminals: list[dict[str, object]] = []
    running_count = 0
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        agent_id = str(agent.get("agent_id"))
        status = str(agent.get("status", "unknown"))
        pane_id = agent.get("pane_id")
        enabled = status == "running" and bool(pane_id)
        if enabled:
            running_count += 1
        select_pane_command = _tmux_select_pane_command(config, str(pane_id)) if enabled else None
        blocker = None if enabled else "agent is not running"
        terminals.append(
            {
                "agent_id": agent_id,
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
        "session_name": config.runtime.session_name,
        "attach_command": _tmux_attach_command(config),
        "running_count": running_count,
        "agent_count": len(terminals),
        "open_terminals_command": "agentdeck controls",
        "refresh_command": runtime_card.get("refresh_command"),
        "controls": [
            {
                "kind": "attach_session",
                "label": "Attach session",
                "command": _tmux_attach_command(config),
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


def _tmux_attach_command(config: ProjectConfig) -> str:
    return (
        f"tmux -L {shlex.quote(config.runtime.socket_name)} "
        f"attach -t {shlex.quote(config.runtime.session_name)}"
    )


def _tmux_select_pane_command(config: ProjectConfig, pane_id: str) -> str:
    return f"tmux -L {shlex.quote(config.runtime.socket_name)} select-pane -t {shlex.quote(pane_id)}"


def _agent_terminal_card_payload(
    config: ProjectConfig,
    store: StateStore,
    agent_id: str,
) -> tuple[dict[str, object] | None, int]:
    agent = next((item for item in config.agents if item.agent_id == agent_id), None)
    if agent is None:
        print(f"unknown agent: {agent_id}", file=sys.stderr)
        return None, 1
    binding, exit_code = _running_binding_or_error(store, agent_id)
    if binding is None:
        return None, exit_code
    pane_id = str(binding["pane_id"])
    return (
        {
            "ok": True,
            "mode": "agent_terminal",
            "agent_id": agent.agent_id,
            "role": agent.role,
            "provider": agent.provider,
            "workspace_mode": agent.workspace_mode,
            "status": str(binding.get("status", "running")),
            "pane_id": pane_id,
            "session_name": binding.get("session_name") or config.runtime.session_name,
            "cwd": binding.get("cwd") or config.root,
            "attach_command": _tmux_attach_command(config),
            "select_pane_command": _tmux_select_pane_command(config, pane_id),
            "capture_command": f"agentdeck agent capture --agent {agent.agent_id} --lines 200",
            "send_command_template": f"agentdeck agent send --agent {agent.agent_id} --text <text>",
            "stop_command": f"agentdeck agent stop --agent {agent.agent_id}",
            "inbox_command": f"agentdeck inbox --agent {agent.agent_id}",
            "refresh_command": "agentdeck agent refresh",
            "controls": runtime_agent_controls(agent.agent_id, True),
        },
        0,
    )


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


def controls_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    project_view = _project_view_payload_or_error(config, store)
    if project_view is None:
        return 1
    workbench_card = _workbench_snapshot_payload(project_view, store, since_event_id=None)
    payload = leader_chat_control_registry_card(
        workbench_card,
        scope=args.scope,
        card=args.card,
        query=args.query,
        control_id=args.control_id,
        enabled_only=args.enabled_only,
    )
    validation = validate_control_registry_card_contract(payload)
    if not validation["ok"]:
        print("Control registry card contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1
    _print_json(payload)
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


def contract_run_command(args: argparse.Namespace) -> int:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "run-schema.md"
    payload = run_start_contract_response(contract_path, include_example=args.example)
    _print_json(payload)
    return 0


def contract_workbench_command(args: argparse.Namespace) -> int:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "workbench-schema.md"
    payload = workbench_contract_response(contract_path, include_example=args.example)
    _print_json(payload)
    return 0


def contract_controls_command(args: argparse.Namespace) -> int:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "controls-schema.md"
    payload = controls_contract_response(contract_path, include_example=args.example)
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


def contract_leader_summary_command(args: argparse.Namespace) -> int:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "leader-summary-schema.md"
    payload = leader_summary_contract_response(contract_path, include_example=args.example)
    _print_json(payload)
    return 0


def contract_trace_command(args: argparse.Namespace) -> int:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "trace-schema.md"
    payload = trace_contract_response(contract_path, include_example=args.example)
    _print_json(payload)
    return 0


def contract_artifacts_command(args: argparse.Namespace) -> int:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "artifacts-schema.md"
    payload = artifacts_contract_response(contract_path, include_example=args.example)
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


def _is_known_mailbox_agent(config: ProjectConfig, agent_id: str) -> bool:
    return agent_id == config.leader.agent_id or _agent_by_id(config, agent_id) is not None


def agent_list_command(_args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    _print_json(asdict(store.project_view(config)))
    return 0


def agent_ready_command(_args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    project_view = _project_view_payload_or_error(config, store)
    if project_view is None:
        return 1
    _print_json(_agent_ready_card_payload(project_view))
    return 0


def policy_set_mode_command(args: argparse.Namespace) -> int:
    _config, store, exit_code = _load_project_or_error()
    if store is None:
        return exit_code
    mode = str(args.mode)
    if mode == "autonomous":
        store.append_event(
            EventRecord.create(
                "policy_mode_rejected",
                {
                    "mode": mode,
                    "reason": "autonomous control mode is not implemented",
                    "policy_source": ".agentdeck/config.toml:leader.approval_mode",
                },
            )
        )
        print("autonomous control mode is not implemented", file=sys.stderr)
        return 1

    approval_mode = "confirm" if mode == "ask" else "approve"
    leader = update_leader_approval_mode(project_root(), approval_mode)
    store.append_event(
        EventRecord.create(
            "policy_mode_updated",
            {
                "mode": mode,
                "approval_mode": leader.approval_mode,
                "policy_source": ".agentdeck/config.toml:leader.approval_mode",
            },
        )
    )
    _print_json(
        {
            "ok": True,
            "mode": mode,
            "approval_mode": leader.approval_mode,
            "policy_source": ".agentdeck/config.toml:leader.approval_mode",
            "workbench_command": "agentdeck workbench",
        }
    )
    return 0


def leader_set_provider_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    provider_name = str(args.provider)
    try:
        leader_provider(provider_name)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    requested_model = args.model or config.leader.model
    readiness = _leader_provider_readiness(
        agent_id=config.leader.agent_id,
        provider=provider_name,
        model=requested_model,
        approval_mode=config.leader.approval_mode,
    )
    if args.require_ready and not readiness["ready"]:
        store.append_event(
            EventRecord.create(
                "leader_provider_update_rejected",
                {
                    "provider": provider_name,
                    "model": requested_model,
                    "reason": "provider_not_ready",
                    "detail": readiness["detail"],
                    "config_source": ".agentdeck/config.toml:leader",
                },
            )
        )
        print(f"leader provider is not ready: {readiness['detail']}", file=sys.stderr)
        return 1
    leader = update_leader_provider(project_root(), provider_name, args.model)
    readiness = _leader_provider_readiness(
        agent_id=leader.agent_id,
        provider=leader.provider,
        model=leader.model,
        approval_mode=leader.approval_mode,
    )
    store.append_event(
        EventRecord.create(
            "leader_provider_updated",
            {
                "provider": leader.provider,
                "model": leader.model,
                "config_source": ".agentdeck/config.toml:leader",
            },
        )
    )
    _print_json(
        {
            "ok": True,
            "agent_id": leader.agent_id,
            "provider": leader.provider,
            "model": leader.model,
            "approval_mode": leader.approval_mode,
            "ready": readiness["ready"],
            "supported": readiness["supported"],
            "missing_env": readiness["missing_env"],
            "detail": readiness["detail"],
            "command_path": readiness["command_path"],
            "setup_commands": readiness["setup_commands"],
            "config_path": str(config_path(project_root())),
            "doctor_command": "agentdeck doctor",
            "workbench_command": "agentdeck workbench",
        }
    )
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


def agent_spawn_ready_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    if not args.confirm:
        print("agent spawn-ready requires --confirm", file=sys.stderr)
        return 1

    backend = TmuxBackend()
    created_session = False
    results: list[dict[str, object]] = []
    spawned_count = 0
    skipped_count = 0
    for agent in config.agents:
        existing = store.agent_binding(agent.agent_id)
        previous_status = (
            str(existing.get("status", "configured"))
            if isinstance(existing, dict)
            else "configured"
        )
        previous_pane_id = existing.get("pane_id") if isinstance(existing, dict) else None
        spawn_command = f"agentdeck agent spawn --agent {agent.agent_id}"
        if isinstance(existing, dict) and existing.get("pane_id") and existing.get("status") == "running":
            skipped_count += 1
            results.append(
                {
                    "agent_id": agent.agent_id,
                    "status": "skipped",
                    "previous_status": previous_status,
                    "pane_id": previous_pane_id,
                    "spawn_command": spawn_command,
                    "blocker": "agent already running",
                }
            )
            continue
        if not created_session:
            backend.create_session(config.runtime)
            created_session = True
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
        spawned_count += 1
        results.append(
            {
                "agent_id": agent.agent_id,
                "status": "spawned",
                "previous_status": previous_status,
                "pane_id": pane_id,
                "spawn_command": spawn_command,
                "blocker": None,
            }
        )
    store.append_event(
        EventRecord.create(
            "agent_spawn_ready_completed",
            {
                "spawned_count": spawned_count,
                "skipped_count": skipped_count,
            },
        )
    )
    _print_json(
        {
            "ok": True,
            "mode": "agent_spawn_ready",
            "requires_explicit_user": True,
            "safety": "explicit_runtime",
            "spawned_count": spawned_count,
            "skipped_count": skipped_count,
            "results": results,
            "ready_command": "agentdeck agent ready",
        }
    )
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


def agent_terminal_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    payload, exit_code = _agent_terminal_card_payload(config, store, args.agent)
    if payload is None:
        return exit_code
    _print_json(payload)
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
            "full_output_path:",
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
    if not _is_known_mailbox_agent(config, args.agent):
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
                "artifact_count": len(reply.get("artifacts", [])) if isinstance(reply.get("artifacts"), list) else 0,
            },
        )
    )
    payload = _reply_success_payload(reply, store)
    if payload is None:
        return 1
    _print_json(payload)
    return 0


def _reply_success_payload(reply: dict[str, object], store: StateStore) -> dict[str, object] | None:
    payload = {
        "ok": True,
        "reply_id": reply["reply_id"],
        "message_id": reply["message_id"],
        "from_agent": reply["from_agent"],
        "trace_command": _trace_command(reply["reply_id"]),
    }
    to_actor = reply.get("to_actor")
    if to_actor and to_actor != "user":
        inbox_card = _inbox_queue_payload(str(to_actor), store)
        validation = validate_inbox_contract(inbox_card)
        if not validation["ok"]:
            print("Inbox contract validation failed", file=sys.stderr)
            for error in validation["errors"]:
                print(f"- {error}", file=sys.stderr)
            return None
        payload["inbox_card"] = inbox_card
    artifacts = reply.get("artifacts")
    if isinstance(artifacts, list) and artifacts:
        payload["artifacts"] = store.artifact_summaries(artifacts)
    return payload


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
                "artifact_count": len(reply.get("artifacts", [])) if isinstance(reply.get("artifacts"), list) else 0,
            },
        )
    )
    payload = _reply_success_payload(reply, store)
    if payload is None:
        return 1
    payload["pane_id"] = pane_id
    payload["captured_lines"] = len(text.splitlines())
    _print_json(payload)
    return 0


def ack_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    if not _is_known_mailbox_agent(config, args.agent):
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


def _trace_card_for_query(store: StateStore, query_id: object) -> dict[str, object] | None:
    if query_id is None:
        return None
    try:
        trace = store.trace(str(query_id))
    except KeyError:
        return None
    validation = validate_trace_contract(trace)
    if not validation["ok"]:
        return None
    return trace


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
        plan = orchestrator.plan(args.task, model_label)
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
            "provider_backend": record["provider_backend"],
            "provider_transport": record["provider_transport"],
            "leader_backend": record["leader_backend"],
            "model": record["model"],
            "dispatch_ready": record["dispatch_ready"],
            "plan": record["plan"],
        }
    )
    return 0


def run_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    if args.plan_id:
        try:
            payload = _run_progress_payload(store, args.plan_id)
        except KeyError:
            print(f"unknown plan: {args.plan_id}", file=sys.stderr)
            return 1
        validation = validate_run_start_contract(payload)
        if not validation["ok"]:
            print("Run progress contract validation failed", file=sys.stderr)
            for error in validation["errors"]:
                print(f"- {error}", file=sys.stderr)
            return 1
        _print_json(payload)
        return 0
    try:
        payload, _record, _approvals = _create_run_start_payload(
            config,
            store,
            task=args.task,
            provider_override=args.provider,
            model_override=args.model,
            source="run",
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"leader provider failed: {exc}", file=sys.stderr)
        return 1
    validation = validate_run_start_contract(payload)
    if not validation["ok"]:
        print("Run start contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1
    _print_json(payload)
    return 0


def _create_run_start_payload(
    config: ProjectConfig,
    store: StateStore,
    *,
    task: str,
    provider_override: str | None,
    model_override: str | None,
    source: str,
) -> tuple[dict[str, object], dict[str, object], list[dict[str, object]]]:
    provider_name = _leader_provider_name(config, provider_override)
    model_label = _leader_model_label(config, model_override)
    provider = leader_provider(provider_name)
    orchestrator = LeaderOrchestrator(config, provider)
    try:
        plan = orchestrator.plan(task, model_label)
    except RuntimeError as exc:
        _record_leader_provider_failure(store, source, provider.name, model_label, task, exc)
        raise
    record = store.record_plan(task, provider.name, model_label, plan)
    approvals = store.create_approvals_from_plan(record["plan_id"])
    store.append_event(
        EventRecord.create(
            "leader_plan_created",
            {
                "plan_id": record["plan_id"],
                "provider": record["provider"],
                "model": record["model"],
                "task_length": len(task),
                "source": source,
            },
        )
    )
    store.append_event(
        EventRecord.create(
            "approvals_created_from_plan",
            {
                "plan_id": record["plan_id"],
                "count": len(approvals),
                "source": source,
            },
        )
    )
    store.append_event(
        EventRecord.create(
            "run_started",
            {
                "plan_id": record["plan_id"],
                "approval_count": len(approvals),
                "task_length": len(task),
                "source": source,
            },
        )
    )
    approval_card = _approval_queue_payload(store, plan_id=record["plan_id"])
    return _run_start_payload(record, approvals, approval_card), record, approvals


def _run_progress_payload(store: StateStore, plan_id: str) -> dict[str, object]:
    status = store.plan_status(plan_id)
    review = _leader_review_payload(store.leader_review(plan_id))
    approval_card = _approval_queue_payload(store, plan_id=plan_id)
    next_command = review.get("next_command")
    controls = [
        _control(
            kind="plan_status",
            label="Plan status",
            command=f"agentdeck plan status --plan-id {plan_id}",
            safety="inspect",
        ),
        _control(
            kind="review",
            label="Review run",
            command=f"agentdeck leader review --plan-id {plan_id}",
            safety="inspect",
        ),
        _control(
            kind="approval_queue",
            label="Review approval queue",
            command="agentdeck approval list",
            safety="inspect",
        ),
    ]
    if next_command:
        review_next_action = review.get("next_action")
        controls.append(
            _control(
                kind="next",
                label="Next command",
                command=next_command,
                safety="inspect" if review_next_action in {"summarize", "wait_for_approval"} else "explicit_runtime",
            )
        )
    controls.extend(
        [
            _control(kind="continue", label="Continue", command="agentdeck continue", safety="inspect"),
            _control(kind="workbench", label="Open workbench", command="agentdeck workbench", safety="inspect"),
        ]
    )
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "ok": True,
        "mode": "run_progress",
        "plan_id": plan_id,
        "task": status.get("task"),
        "status": status.get("status"),
        "provider": status.get("provider"),
        "provider_backend": status.get("provider_backend"),
        "provider_transport": status.get("provider_transport"),
        "leader_backend": status.get("leader_backend")
        or leader_backend_identity(
            str(status.get("provider") or ""),
            str(status.get("model") or ""),
            False,
        ),
        "model": status.get("model"),
        "counts": status.get("counts"),
        "steps": status.get("steps"),
        "review": review,
        "approval_card": approval_card,
        "next_command": next_command,
        "plan_status_command": f"agentdeck plan status --plan-id {plan_id}",
        "review_command": f"agentdeck leader review --plan-id {plan_id}",
        "continue_command": "agentdeck continue",
        "workbench_command": "agentdeck workbench",
        "controls": controls,
        "safety": "approval_gated",
        "requires_explicit_user": True,
    }


def _run_start_payload(
    plan_record: dict[str, object],
    approvals: list[dict[str, object]],
    approval_card: dict[str, object],
) -> dict[str, object]:
    plan_id = str(plan_record["plan_id"])
    pending_approvals = [item for item in approvals if item.get("status") == "pending"]
    first_approval = pending_approvals[0] if pending_approvals else None
    first_approval_id = first_approval.get("approval_id") if isinstance(first_approval, dict) else None
    approve_next_command = (
        f"agentdeck approval approve --approval-id {first_approval_id}" if first_approval_id else None
    )
    review_command = f"agentdeck leader review --plan-id {plan_id}"
    controls = [
        _control(
            kind="preview",
            label="Review approval queue",
            command="agentdeck approval list",
            safety="inspect",
        ),
        _control(
            kind="approve",
            label="Approve next step",
            command=approve_next_command or "agentdeck approval approve --approval-id <approval_id>",
            safety="explicit_runtime",
            enabled=approve_next_command is not None,
            blocker=None if approve_next_command is not None else "no pending approval",
        ),
        _control(kind="review", label="Review run", command=review_command, safety="inspect"),
        _control(kind="continue", label="Continue", command="agentdeck continue", safety="inspect"),
        _control(kind="workbench", label="Open workbench", command="agentdeck workbench", safety="inspect"),
    ]
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "ok": True,
        "mode": "run_start",
        "task": plan_record["task"],
        "plan_id": plan_id,
        "provider": plan_record["provider"],
        "provider_backend": plan_record.get("provider_backend")
        or leader_provider_backend(str(plan_record.get("provider") or "")),
        "provider_transport": plan_record.get("provider_transport")
        or leader_provider_transport(str(plan_record.get("provider") or "")),
        "leader_backend": plan_record.get("leader_backend")
        or leader_backend_identity(
            str(plan_record.get("provider") or ""),
            str(plan_record.get("model") or ""),
            bool(plan_record.get("dispatch_ready", False)),
        ),
        "model": plan_record["model"],
        "approval_count": len(approvals),
        "pending_approval_count": len(pending_approvals),
        "plan": plan_record["plan"],
        "approval_card": approval_card,
        "next_command": "agentdeck approval list",
        "approve_next_command": approve_next_command,
        "review_command": review_command,
        "continue_command": "agentdeck continue",
        "workbench_command": "agentdeck workbench",
        "controls": controls,
        "safety": "approval_gated",
        "requires_explicit_user": True,
    }


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


def leader_summary_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    if _project_view_payload_or_error(config, store) is None:
        return 1
    try:
        payload = _leader_summary_payload(store, args.plan_id)
    except KeyError:
        print(f"unknown plan: {args.plan_id}", file=sys.stderr)
        return 1
    validation = validate_leader_summary_contract(payload)
    if not validation["ok"]:
        print("Leader summary contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1
    _print_json(payload)
    return 0


def _leader_summary_payload(store: StateStore, plan_id: str) -> dict[str, object]:
    plan = store.plan_by_id(plan_id)
    plan_status = store.plan_status(plan_id)
    state = store.load()
    replies_by_message = {reply.get("message_id"): reply for reply in state.get("replies", [])}
    artifacts_by_message: dict[object, list[dict[str, object]]] = {}
    for artifact in state.get("artifacts", []):
        artifacts_by_message.setdefault(artifact.get("message_id"), []).append(artifact)
    steps = []
    reply_count = 0
    artifact_count = 0
    for step in plan_status.get("steps", []):
        if not isinstance(step, dict):
            continue
        message_id = step.get("message_id")
        reply = replies_by_message.get(message_id)
        artifacts = artifacts_by_message.get(message_id, [])
        if reply is not None:
            reply_count += 1
        artifact_count += len(artifacts)
        trace_id = message_id or step.get("job_id") or step.get("approval_id")
        steps.append(
            {
                "step": step.get("step"),
                "agent_id": step.get("agent_id"),
                "role": step.get("role"),
                "task": step.get("task"),
                "approval_id": step.get("approval_id"),
                "message_id": message_id,
                "attempt_id": step.get("attempt_id"),
                "job_id": step.get("job_id"),
                "reply_id": reply.get("reply_id") if isinstance(reply, dict) else None,
                "reply_text": reply.get("text") if isinstance(reply, dict) else None,
                "artifact_count": len(artifacts),
                "artifacts": [_leader_summary_artifact(item) for item in artifacts],
                "trace_command": _trace_command(trace_id) if trace_id else None,
            }
        )
    controls = [
        _control(
            kind="plan_status",
            label="Plan status",
            command=f"agentdeck plan status --plan-id {plan_id}",
            safety="inspect",
        ),
        _control(
            kind="review",
            label="Review plan",
            command=f"agentdeck leader review --plan-id {plan_id}",
            safety="inspect",
        ),
    ]
    for step in steps:
        if step.get("trace_command"):
            controls.append(
                _control(
                    kind="trace",
                    label="Trace step",
                    command=step.get("trace_command"),
                    safety="inspect",
                )
            )
            break
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "plan_id": plan_id,
        "task": plan.get("task"),
        "status": "ready" if reply_count else "waiting",
        "provider": plan.get("provider"),
        "model": plan.get("model"),
        "leader_backend": plan_status.get("leader_backend")
        or leader_backend_identity(
            str(plan.get("provider") or ""),
            str(plan.get("model") or ""),
            bool(plan.get("dispatch_ready", False)),
        ),
        "counts": plan_status.get("counts"),
        "reply_count": reply_count,
        "artifact_count": artifact_count,
        "summary": f"{reply_count} dispatched step has replies; {artifact_count} artifact recorded.",
        "plan_status_command": f"agentdeck plan status --plan-id {plan_id}",
        "review_command": f"agentdeck leader review --plan-id {plan_id}",
        "steps": steps,
        "controls": controls,
    }


def _leader_summary_artifact(artifact: dict[str, object]) -> dict[str, object]:
    artifact_id = artifact.get("artifact_id")
    return {
        "artifact_id": artifact_id,
        "path": artifact.get("path"),
        "kind": artifact.get("kind"),
        "status": artifact.get("status"),
        "trace_command": _trace_command(artifact_id) if artifact_id else None,
    }


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
        return f"agentdeck leader summary --plan-id {review['plan_id']}"
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
        return f"agentdeck leader summary --plan-id {review['plan_id']}"
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
    } or any(
        token in normalized
        for token in [
            "command palette",
            "control registry",
            "命令面板",
            "命令列表",
            "控制项列表",
        ]
    )


def _chat_control_registry_filters(message: str) -> dict[str, object]:
    normalized = message.strip().lower()
    control_id = _chat_control_registry_control_id(message)
    query = _chat_control_registry_query(message)
    scope = None
    if query is None and control_id is None:
        scope_aliases: tuple[tuple[str, tuple[str, ...]], ...] = (
            ("leader", ("leader", "leader_card", "调度者")),
            ("provider", ("provider", "provider_health", "模型后端")),
            ("policy", ("policy", "control_mode", "控制模式", "授权模式")),
            ("terminal_session", ("terminal_session", "terminal session", "项目终端", "终端会话")),
            ("role", ("role", "roles", "角色", "分工")),
            ("runtime", ("runtime", "tmux", "pane", "terminal", "运行时", "终端", "面板")),
            ("inbox", ("inbox", "mailbox", "收件箱", "消息箱")),
            ("operator", ("operator", "主操作", "操作面", "下一步按钮")),
        )
        for candidate_scope, aliases in scope_aliases:
            if any(alias in normalized for alias in aliases):
                scope = candidate_scope
                break
    card = None
    card_match = re.search(r"\b[A-Za-z0-9_]+_card\b", message) if control_id is None else None
    if card_match:
        card = card_match.group(0)
    enabled_only = any(
        token in normalized
        for token in [
            "enabled only",
            "enabled-only",
            "only enabled",
            "可用",
            "启用",
            "只看可用",
            "只显示可用",
        ]
    )
    return {"scope": scope, "card": card, "query": query, "control_id": control_id, "enabled_only": enabled_only}


def _chat_control_registry_control_id(message: str) -> str | None:
    patterns = [
        r"(?:control_id|control id|控件id|控制id|id)\s+(?P<control_id>[A-Za-z0-9_.:-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if not match:
            continue
        control_id = match.group("control_id").strip(" \t\r\n\"'`：:，,。.!！?")
        return control_id or None
    return None


def _chat_control_registry_query(message: str) -> str | None:
    patterns = [
        r"(?:搜索|查找|筛选)\s+(?P<query>.+)",
        r"(?:search|query|find|filter)\s+(?P<query>.+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if not match:
            continue
        query = match.group("query").strip(" \t\r\n\"'`：:，,。.!！?")
        return query or None
    return None


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


def _chat_wants_require_ready(message: str) -> bool:
    normalized = message.strip().lower()
    return any(
        token in normalized
        for token in [
            "require-ready",
            "require ready",
            "ready first",
            "ready-only",
            "先预检",
            "预检",
            "要求可用",
            "必须可用",
            "确保可用",
            "不可用就拒绝",
            "可用再切",
            "ready 再切",
        ]
    )


def _chat_provider_setup_intent(message: str) -> tuple[str, str, str, bool] | None:
    normalized = message.strip().lower()
    mentions_setup = any(
        token in normalized
        for token in [
            "配置",
            "设置",
            "安装",
            "登录",
            "认证",
            "setup",
            "configure",
            "install",
            "login",
            "auth",
        ]
    )
    if not mentions_setup:
        return None
    aliases: tuple[tuple[str, str, tuple[str, ...]], ...] = (
        ("codex-cli", "codex", ("codex cli", "codex-cli", "codex")),
        ("claude-cli", "claude", ("claude code", "claude cli", "claude-cli", "claude")),
        ("deepseek", "deepseek", ("deepseek", "deep seek", "深度求索")),
        ("openai-compatible", "openai", ("openai-compatible", "openai compatible", "openai兼容", "openai 兼容")),
    )
    for provider, query, provider_aliases in aliases:
        if not any(alias in normalized for alias in provider_aliases):
            continue
        setup_commands = _provider_setup_commands(provider)
        if setup_commands:
            return provider, query, setup_commands[0], _chat_wants_require_ready(message)
    return None


def _chat_provider_switch_intent(message: str) -> tuple[str, str, bool] | None:
    if _chat_provider_setup_intent(message) is not None:
        return None
    normalized = message.strip().lower()
    mentions_switch = any(
        token in normalized
        for token in [
            "切换",
            "换成",
            "改成",
            "使用",
            "用 ",
            "use ",
            "switch",
            "set provider",
            "leader provider",
        ]
    )
    if not mentions_switch:
        return None
    require_ready = _chat_wants_require_ready(message)
    aliases: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("codex-cli", ("codex cli", "codex-cli", "codex")),
        ("claude-cli", ("claude code", "claude cli", "claude-cli", "claude")),
        ("deepseek", ("deepseek", "deep seek", "深度求索")),
        ("openai-compatible", ("openai-compatible", "openai compatible", "openai兼容", "openai 兼容")),
        ("fake", ("fake", "假 provider", "本地 fake")),
    )
    for provider, provider_aliases in aliases:
        if any(alias in normalized for alias in provider_aliases):
            for switch_provider, switch_model, _label in LEADER_PROVIDER_SWITCHES:
                if switch_provider == provider:
                    return switch_provider, switch_model, require_ready
    return None


def _chat_policy_mode(message: str) -> str | None:
    normalized = message.strip().lower()
    mentions_policy = any(
        token in normalized
        for token in [
            "policy",
            "control mode",
            "控制模式",
            "授权模式",
            "策略",
            "放权",
            "审批模式",
            "ask 模式",
            "ask模式",
            "approve 模式",
            "approve模式",
            "autonomous",
            "自主模式",
            "自动模式",
        ]
    )
    if not mentions_policy:
        return None
    if any(token in normalized for token in ["autonomous", "完全放权", "全自动", "自主模式", "自动模式"]):
        return "autonomous"
    if any(token in normalized for token in ["ask", "询问", "只问", "回到问", "观察模式"]):
        return "ask"
    if any(token in normalized for token in ["approve", "approval", "审批模式", "批准模式", "安全应用"]):
        return "approve"
    return None


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


def _chat_wants_audit(message: str) -> bool:
    normalized = message.strip().lower()
    return any(
        token in normalized
        for token in [
            "audit",
            "events",
            "event log",
            "recent events",
            "审计",
            "事件",
            "事件日志",
            "最近事件",
            "操作记录",
        ]
    )


def _chat_wants_artifacts(message: str) -> bool:
    normalized = message.strip().lower()
    return any(
        token in normalized
        for token in [
            "artifact",
            "artifacts",
            "outputs",
            "deliverables",
            "产物",
            "成果",
            "输出文件",
            "交付物",
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


def _chat_wants_runtime_ready(message: str) -> bool:
    normalized = message.strip().lower()
    mentions_all = any(token in normalized for token in ["all", "所有", "全部", "多 agent", "多agent", "多个 agent"])
    mentions_runtime = any(token in normalized for token in ["agent", "agents", "智能体", "runtime", "终端"])
    mentions_prepare = any(
        token in normalized
        for token in ["ready", "prepare", "start", "spawn", "launch", "启动", "开启", "准备", "就绪"]
    )
    return (mentions_all and mentions_runtime and mentions_prepare) or any(
        token in normalized
        for token in [
            "agent ready",
            "runtime ready",
            "准备多 agent",
            "准备多个 agent",
            "启动所有 agent",
            "启动全部 agent",
            "开启所有 agent",
            "开启全部 agent",
        ]
    )


def _chat_runtime_spawn_agent_id(message: str, project_view: dict[str, object]) -> str | None:
    normalized = message.strip().lower()
    if not any(token in normalized for token in ["spawn", "start", "launch", "启动", "开启"]):
        return None
    agents = project_view.get("agents") if isinstance(project_view.get("agents"), list) else []
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        agent_id = str(agent.get("agent_id", ""))
        if agent_id and re.search(rf"(?<![\w-]){re.escape(agent_id.lower())}(?![\w-])", normalized):
            return agent_id
    return None


def _chat_runtime_send_intent(message: str, project_view: dict[str, object]) -> tuple[str, str] | None:
    agents = project_view.get("agents") if isinstance(project_view.get("agents"), list) else []
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        agent_id = str(agent.get("agent_id", ""))
        if not agent_id:
            continue
        patterns = [
            rf"^\s*(?:发送给|发给|告诉)\s*{re.escape(agent_id)}\s*[:：,，]?\s*(?P<text>.+?)\s*$",
            rf"^\s*(?:send|tell)\s+{re.escape(agent_id)}\s+(?P<text>.+?)\s*$",
        ]
        for pattern in patterns:
            match = re.match(pattern, message, flags=re.IGNORECASE)
            if match:
                text = match.group("text").strip()
                return (agent_id, text) if text else None
    return None


def _chat_runtime_stop_agent_id(message: str, project_view: dict[str, object]) -> str | None:
    normalized = message.strip().lower()
    if not any(token in normalized for token in ["stop", "停止", "关闭"]):
        return None
    agents = project_view.get("agents") if isinstance(project_view.get("agents"), list) else []
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        agent_id = str(agent.get("agent_id", ""))
        if agent_id and re.search(rf"(?<![\w-]){re.escape(agent_id.lower())}(?![\w-])", normalized):
            return agent_id
    return None


def _chat_wants_runtime_refresh(message: str) -> bool:
    normalized = message.strip().lower()
    return any(token in normalized for token in ["refresh runtime", "runtime refresh", "刷新 runtime", "刷新运行时", "刷新终端"])


def _agent_send_command(agent_id: str, text: str) -> str:
    return " ".join(
        [
            "agentdeck",
            "agent",
            "send",
            "--agent",
            shlex.quote(agent_id),
            "--text",
            shlex.quote(text),
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


def _chat_wants_summary(message: str) -> bool:
    normalized = message.strip().lower()
    return any(
        token in normalized
        for token in [
            "summary",
            "summarize",
            "final summary",
            "总结",
            "汇总",
            "最终总结",
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


def _chat_role_assignment_intent(message: str, config: ProjectConfig) -> tuple[str, str, str] | None:
    normalized = message.strip()
    if not normalized:
        return None
    known_agent_ids = {agent.agent_id.lower(): agent.agent_id for agent in config.agents}
    patterns = [
        r"(?:把|将)\s*(?P<agent>[A-Za-z0-9_.-]+)\s*(?:设为|设置为|改为|指派为|指定为)\s*(?P<role>.+)",
        r"(?:让|请让)\s*(?P<agent>[A-Za-z0-9_.-]+)\s*(?:作为|担任|负责|扮演)\s*(?P<role>.+)",
        r"(?:set|assign)\s+(?P<agent>[A-Za-z0-9_.-]+)\s+(?:role\s+)?(?:to|as)\s+(?P<role>.+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if not match:
            continue
        requested_agent = match.group("agent").lower()
        agent_id = known_agent_ids.get(requested_agent)
        if agent_id is None:
            return None
        role = match.group("role").strip(" \t\r\n\"'`：:，,。.!！?")
        if not role:
            return None
        role_prompt = f"你负责{role}。"
        return agent_id, role, role_prompt
    return None


def _chat_task_assignment_intent(message: str, config: ProjectConfig) -> tuple[str, str] | None:
    normalized = message.strip()
    if not normalized:
        return None
    known_agent_ids = {agent.agent_id.lower(): agent.agent_id for agent in config.agents}
    patterns = [
        r"(?:让|请让|安排|指派)\s*(?P<agent>[A-Za-z0-9_.-]+)\s*(?P<task>.+)",
        r"(?:ask|assign)\s+(?P<agent>[A-Za-z0-9_.-]+)\s+(?:to\s+)?(?P<task>.+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if not match:
            continue
        requested_agent = match.group("agent").lower()
        agent_id = known_agent_ids.get(requested_agent)
        if agent_id is None:
            return None
        task = match.group("task").strip(" \t\r\n\"'`：:，,。.!！?")
        if not task:
            return None
        return agent_id, task
    return None


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


def _chat_trace_query_id(message: str) -> str | None:
    normalized = message.strip().lower()
    wants_trace = any(token in normalized for token in ["trace", "追踪", "溯源", "lineage", "链路"])
    if not wants_trace:
        return None
    match = re.search(r"\b(?:msg|att|job|rep|art|inb)_[A-Za-z0-9][A-Za-z0-9_-]*\b", message)
    return match.group(0) if match else None


def _chat_capture_agent_id(message: str, config: ProjectConfig) -> str | None:
    normalized = message.strip().lower()
    wants_capture = any(
        token in normalized
        for token in [
            "capture",
            "output",
            "pane output",
            "terminal output",
            "输出",
            "终端输出",
            "面板输出",
            "屏幕",
        ]
    )
    if not wants_capture:
        return None
    for agent in config.agents:
        if agent.agent_id.lower() in normalized:
            return agent.agent_id
    return None


def _chat_capture_reply_intent(message: str, config: ProjectConfig) -> tuple[str, str] | None:
    normalized = message.strip().lower()
    wants_capture_reply = _chat_wants_capture_reply(normalized)
    if not wants_capture_reply:
        return None
    message_match = re.search(r"\bmsg_[A-Za-z0-9][A-Za-z0-9_-]*\b", message)
    if not message_match:
        return None
    for agent in config.agents:
        if agent.agent_id.lower() in normalized:
            return agent.agent_id, message_match.group(0)
    return None


def _chat_current_capture_reply_intent(message: str, store: StateStore) -> tuple[str, dict[str, object], str, str] | None:
    normalized = message.strip().lower()
    if not _chat_wants_capture_reply(normalized):
        return None
    wants_current = any(token in normalized for token in ["current", "当前", "现在", "this", "latest", "最近"])
    if not wants_current:
        return None
    if re.search(r"\bmsg_[A-Za-z0-9][A-Za-z0-9_-]*\b", message):
        return None
    plans = store.list_plans()
    if not plans:
        return None
    plan_id = str(plans[-1]["plan_id"])
    review = store.leader_review(plan_id)
    if review.get("next_action") != "wait_for_reply":
        return None
    agent_id = review.get("agent_id")
    message_id = review.get("message_id")
    if not agent_id or not message_id:
        return None
    return plan_id, review, str(agent_id), str(message_id)


def _chat_wants_capture_reply(normalized_message: str) -> bool:
    return any(
        token in normalized_message
        for token in [
            "capture-reply",
            "capture reply",
            "capture the reply",
            "捕获回复",
            "捕获",
            "回收",
            "回收回复",
            "收取",
            "收取回复",
            "提取",
            "提取回复",
        ]
    ) and any(token in normalized_message for token in ["reply", "回复", "结果"])


def _chat_terminal_agent_id(message: str, config: ProjectConfig) -> str | None:
    normalized = message.strip().lower()
    wants_terminal = any(
        token in normalized
        for token in [
            "open",
            "attach",
            "focus",
            "select pane",
            "terminal",
            "pane",
            "打开",
            "进入",
            "切到",
            "聚焦",
            "终端",
            "面板",
        ]
    )
    wants_output = any(token in normalized for token in ["output", "输出", "屏幕", "capture"])
    wants_inbox = any(token in normalized for token in ["inbox", "mailbox", "收件箱", "消息"])
    if not wants_terminal or wants_output or wants_inbox:
        return None
    for agent in config.agents:
        if agent.agent_id.lower() in normalized:
            return agent.agent_id
    return None


def _chat_inbox_agent_id(message: str, config: ProjectConfig) -> str | None:
    normalized = message.strip().lower()
    mentions_inbox = any(token in normalized for token in ["inbox", "收件箱", "消息", "mailbox"])
    if not mentions_inbox:
        return None
    if config.leader.agent_id.lower() in normalized:
        return config.leader.agent_id
    for agent in config.agents:
        if agent.agent_id.lower() in normalized:
            return agent.agent_id
    return None


def _chat_recovery_inbox_agent_id(
    message: str,
    config: ProjectConfig,
    store: StateStore,
    project_view: dict[str, object],
) -> str | None:
    explicit_agent_id = _chat_inbox_agent_id(message, config)
    if explicit_agent_id:
        return explicit_agent_id
    normalized = message.strip().lower()
    mentions_inbox = any(token in normalized for token in ["inbox", "收件箱", "消息", "mailbox"])
    wants_current = any(token in normalized for token in ["current", "当前", "现在", "this"])
    if not mentions_inbox or not wants_current:
        return None
    recovery = project_view.get("recovery") if isinstance(project_view.get("recovery"), dict) else {}
    recommended_action = recovery.get("recommended_action") if isinstance(recovery.get("recommended_action"), dict) else {}
    if recommended_action.get("source") != "inbox":
        return None
    return _inbox_agent_id_for_item(store, recommended_action.get("target_id"))


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


def _chat_wants_approval_dispatch_all(message: str) -> bool:
    normalized = message.strip().lower()
    wants_dispatch = _chat_wants_approval_dispatch(normalized)
    wants_many = any(token in normalized for token in ["all", "batch", "所有", "全部", "批量"])
    return wants_dispatch and wants_many


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


def _approved_approval_items(approval_card: dict[str, object]) -> list[dict[str, object]]:
    approvals = approval_card.get("approvals")
    if not isinstance(approvals, list):
        return []
    return [approval for approval in approvals if isinstance(approval, dict) and approval.get("status") == "approved"]


def _approval_dispatch_preview_card(
    approval: dict[str, object],
    config: ProjectConfig,
    store: StateStore,
) -> dict[str, object]:
    agent_id = str(approval.get("agent_id", ""))
    approval_id = str(approval.get("approval_id", ""))
    agent = _agent_by_id(config, agent_id)
    binding = store.agent_binding(agent_id) or {}
    pane_id = binding.get("pane_id")
    runtime_status = str(binding.get("status", "configured"))
    blocker = None
    if agent is None:
        blocker = f"unknown agent: {agent_id}"
    elif not pane_id:
        blocker = f"agent is not spawned: {agent_id}"
    elif runtime_status != "running":
        blocker = f"agent runtime is {runtime_status}: {agent_id}"
    dispatch_command = f"agentdeck approval dispatch --approval-id {approval_id}"
    return {
        "approval_id": approval_id,
        "agent_id": agent_id,
        "agent_role": agent.role if agent is not None else None,
        "pane_id": pane_id,
        "runtime_status": runtime_status,
        "task": approval.get("task"),
        "dispatch_command": dispatch_command,
        "approval_command": "agentdeck approval list",
        "inbox_command": f"agentdeck inbox --agent {agent_id}",
        "requires_explicit_user": True,
        "safety": "explicit_runtime",
        "blocker": blocker,
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
                "command": dispatch_command,
                "safety": "explicit_runtime",
                "enabled": blocker is None,
                "blocker": blocker,
            },
        ],
    }


def _approval_dispatch_batch_preview_card(
    approvals: list[dict[str, object]],
    config: ProjectConfig,
    store: StateStore,
) -> dict[str, object]:
    items = [_approval_dispatch_preview_card(approval, config, store) for approval in approvals]
    blocked_count = sum(1 for item in items if item.get("blocker"))
    ready_count = len(items) - blocked_count
    dispatch_ready_command = "agentdeck approval dispatch-ready --confirm"
    return {
        "mode": "dispatch_batch_preview",
        "approval_command": "agentdeck approval list",
        "dispatch_ready_command": dispatch_ready_command,
        "count": len(items),
        "ready_count": ready_count,
        "blocked_count": blocked_count,
        "items": items,
        "requires_explicit_user": True,
        "safety": "explicit_runtime",
        "blocker": "some dispatch targets are blocked" if blocked_count else None,
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
                "command": dispatch_ready_command,
                "safety": "explicit_runtime",
                "enabled": ready_count > 0,
                "blocker": None if ready_count > 0 else "no ready approvals to dispatch",
            },
        ],
    }


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
    capture_card: dict[str, object] | None = None,
    terminal_card: dict[str, object] | None = None,
    trace_card: dict[str, object] | None = None,
    approval_card: dict[str, object] | None = None,
    approval_action_kind: str | None = None,
    dispatch_batch_preview_card: dict[str, object] | None = None,
    recommended_action: dict[str, object] | None = None,
    agent_ready_card: dict[str, object] | None = None,
) -> dict[str, object]:
    recovery = project_view.get("recovery")
    recovery_action = (
        recommended_action
        if isinstance(recommended_action, dict)
        else recovery.get("recommended_action")
        if isinstance(recovery, dict)
        else None
    )
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
    if mode == "run_start":
        run_start_card = result if isinstance(result, dict) else {}
        return {
            "mode": mode,
            "summary": "Leader started an approval-gated run from natural language and queued pending approvals.",
            "reason": "human asked to start a multi-agent run",
            "next_command": next_command,
            "recommended_action_id": run_start_card.get("plan_id"),
            "action_kind": "run_start",
            "action_status": "approval_gated",
            "safety": "approval_gated",
            "requires_explicit_user": True,
        }
    if mode == "run_progress":
        run_progress_card = result if isinstance(result, dict) else {}
        return {
            "mode": mode,
            "summary": "Leader is showing read-only run progress without mutating runtime state.",
            "reason": "human asked to inspect an existing run",
            "next_command": next_command,
            "recommended_action_id": run_progress_card.get("plan_id"),
            "action_kind": "run_progress",
            "action_status": run_progress_card.get("status"),
            "safety": "inspect",
            "requires_explicit_user": False,
        }
    if mode == "artifacts":
        artifacts = project_view.get("artifacts") if isinstance(project_view.get("artifacts"), dict) else {}
        count = artifacts.get("count") if isinstance(artifacts, dict) else 0
        return {
            "mode": mode,
            "summary": "Leader recommends inspecting the artifact index without reading file contents.",
            "reason": "human asked to inspect worker artifacts",
            "next_command": next_command,
            "recommended_action_id": None,
            "action_kind": "artifacts",
            "action_status": "has_artifacts" if count else "empty",
            "safety": "inspect",
            "requires_explicit_user": False,
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
    if mode == "summary":
        summary_match = re.fullmatch(r"agentdeck leader summary --plan-id ([^\s]+)", str(next_command or ""))
        return {
            "mode": mode,
            "summary": "Leader is showing a deterministic reply and artifact summary without mutating state.",
            "reason": reason or "human asked to summarize the current plan",
            "next_command": next_command,
            "recommended_action_id": summary_match.group(1) if summary_match else None,
            "action_kind": "leader_summary",
            "action_status": "ready",
            "safety": "inspect",
            "requires_explicit_user": False,
        }
    if mode == "setup":
        leader = project_view.get("leader") if isinstance(project_view.get("leader"), dict) else {}
        provider = leader.get("provider")
        provider_switch_match = re.fullmatch(
            r"agentdeck leader set-provider --provider ([^\s]+) --model [^\s]+(?: --require-ready)?",
            str(next_command or ""),
        )
        if provider_switch_match:
            return {
                "mode": mode,
                "summary": "Leader recommends an explicit provider switch command without mutating provider config.",
                "reason": "human asked to switch Leader provider",
                "next_command": next_command,
                "recommended_action_id": provider_switch_match.group(1),
                "action_kind": "provider_switch",
                "action_status": "suggested" if provider_switch_match.group(1) != provider else "already_current",
                "safety": "explicit_user",
                "requires_explicit_user": True,
            }
        setup_provider = _provider_for_setup_command(str(next_command or ""))
        if setup_provider is not None:
            return {
                "mode": mode,
                "summary": "Leader recommends explicit provider setup commands without mutating provider config.",
                "reason": "human asked to configure a Leader provider",
                "next_command": next_command,
                "recommended_action_id": setup_provider,
                "action_kind": "provider_setup",
                "action_status": "suggested",
                "safety": "explicit_user",
                "requires_explicit_user": True,
            }
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
    if mode == "policy":
        target_mode = str(next_command).rsplit(" ", 1)[-1] if next_command else None
        return {
            "mode": mode,
            "summary": "Leader recommends an explicit control mode command without mutating policy.",
            "reason": "human asked to change control mode",
            "next_command": next_command,
            "recommended_action_id": target_mode,
            "action_kind": "policy_mode",
            "action_status": "blocked" if target_mode == "autonomous" else "suggested",
            "safety": "explicit_user",
            "requires_explicit_user": True,
        }
    if mode == "runtime":
        if isinstance(agent_ready_card, dict):
            action_status = "ready" if agent_ready_card.get("all_running") is True else "partial"
            return {
                "mode": mode,
                "summary": (
                    "Leader recommends explicitly preparing all configured agent runtimes without mutating runtime state."
                ),
                "reason": "human asked to prepare all agent runtimes",
                "next_command": next_command,
                "recommended_action_id": "agent_runtime_ready",
                "action_kind": "runtime_ready",
                "action_status": action_status,
                "safety": "explicit_runtime",
                "requires_explicit_user": True,
            }
        if next_command == "agentdeck agent refresh":
            return {
                "mode": mode,
                "summary": "Leader recommends explicitly refreshing runtime bindings without mutating runtime state.",
                "reason": "human asked to refresh runtime bindings",
                "next_command": next_command,
                "recommended_action_id": None,
                "action_kind": "runtime_refresh",
                "action_status": "suggested",
                "safety": "explicit_runtime",
                "requires_explicit_user": True,
            }
        stop_match = re.fullmatch(r"agentdeck agent stop --agent ([^\s]+)", str(next_command or ""))
        if stop_match:
            agent_id = stop_match.group(1)
            agent = _project_view_agent_item(project_view, agent_id)
            runtime = agent.get("runtime") if isinstance(agent, dict) and isinstance(agent.get("runtime"), dict) else {}
            status = str(runtime.get("status", "configured")) if isinstance(runtime, dict) else "configured"
            return {
                "mode": mode,
                "summary": f"Leader recommends explicitly stopping {agent_id} without mutating runtime state.",
                "reason": "human asked to stop one agent runtime",
                "next_command": next_command,
                "recommended_action_id": agent_id,
                "action_kind": "runtime_stop",
                "action_status": status,
                "safety": "explicit_runtime",
                "requires_explicit_user": True,
            }
        send_match = re.fullmatch(r"agentdeck agent send --agent ([^\s]+) --text (.+)", str(next_command or ""))
        if send_match:
            agent_id = send_match.group(1)
            agent = _project_view_agent_item(project_view, agent_id)
            runtime = agent.get("runtime") if isinstance(agent, dict) and isinstance(agent.get("runtime"), dict) else {}
            status = str(runtime.get("status", "configured")) if isinstance(runtime, dict) else "configured"
            return {
                "mode": mode,
                "summary": f"Leader recommends explicitly sending input to {agent_id} without mutating runtime state.",
                "reason": "human asked to send input to one agent runtime",
                "next_command": next_command,
                "recommended_action_id": agent_id,
                "action_kind": "runtime_send",
                "action_status": status,
                "safety": "explicit_runtime",
                "requires_explicit_user": True,
            }
        spawn_match = re.fullmatch(r"agentdeck agent spawn --agent ([^\s]+)", str(next_command or ""))
        if spawn_match:
            agent_id = spawn_match.group(1)
            agent = _project_view_agent_item(project_view, agent_id)
            runtime = agent.get("runtime") if isinstance(agent, dict) and isinstance(agent.get("runtime"), dict) else {}
            status = str(runtime.get("status", "configured")) if isinstance(runtime, dict) else "configured"
            return {
                "mode": mode,
                "summary": f"Leader recommends explicitly spawning {agent_id} without mutating runtime state.",
                "reason": "human asked to spawn one agent runtime",
                "next_command": next_command,
                "recommended_action_id": agent_id,
                "action_kind": "runtime_spawn",
                "action_status": status,
                "safety": "explicit_runtime",
                "requires_explicit_user": True,
            }
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
    if mode == "capture":
        capture_reply_match = re.fullmatch(
            r"agentdeck capture-reply --agent ([^\s]+) --message-id ([^\s]+)(?: --lines \d+)?",
            str(next_command or ""),
        )
        if capture_reply_match:
            return {
                "mode": mode,
                "summary": "Leader recommends explicitly capturing a structured reply without reading the pane in chat.",
                "reason": "human asked to capture an agent reply for a message",
                "next_command": next_command,
                "recommended_action_id": capture_reply_match.group(2),
                "action_kind": "capture_reply",
                "action_status": "suggested",
                "safety": "explicit_runtime",
                "requires_explicit_user": True,
            }
        agent_id = capture_card.get("agent_id") if isinstance(capture_card, dict) else None
        return {
            "mode": mode,
            "summary": "Leader captured a visible agent pane as a read-only terminal snapshot.",
            "reason": "human asked to inspect one agent pane output",
            "next_command": next_command,
            "recommended_action_id": agent_id,
            "action_kind": "capture",
            "action_status": "captured" if agent_id else "missing",
            "safety": "inspect",
            "requires_explicit_user": False,
        }
    if mode == "terminal":
        agent_id = terminal_card.get("agent_id") if isinstance(terminal_card, dict) else None
        status = str(terminal_card.get("status", "unknown")) if isinstance(terminal_card, dict) else "unknown"
        return {
            "mode": mode,
            "summary": "Leader recommends opening a visible agent terminal pane without reading or mutating it.",
            "reason": "human asked to open one agent terminal pane",
            "next_command": next_command,
            "recommended_action_id": agent_id,
            "action_kind": "terminal",
            "action_status": status,
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
    if mode == "audit":
        recovery = project_view.get("recovery") if isinstance(project_view.get("recovery"), dict) else {}
        recent_events = recovery.get("recent_events") if isinstance(recovery.get("recent_events"), list) else []
        latest_event = recovery.get("latest_event") if isinstance(recovery.get("latest_event"), dict) else None
        return {
            "mode": mode,
            "summary": "Leader recommends inspecting the audit timeline without mutating state.",
            "reason": "human asked to inspect recent audit events",
            "next_command": next_command,
            "recommended_action_id": latest_event.get("event_id") if isinstance(latest_event, dict) else None,
            "action_kind": "audit",
            "action_status": "has_events" if recent_events else "empty",
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
        role_assign_match = re.fullmatch(
            r"agentdeck agent assign-role --agent ([^\s]+) --role .+ --role-prompt .+",
            str(next_command or ""),
        )
        if role_assign_match:
            return {
                "mode": mode,
                "summary": "Leader recommends an explicit role assignment command without mutating role config.",
                "reason": "human asked to assign an agent role",
                "next_command": next_command,
                "recommended_action_id": role_assign_match.group(1),
                "action_kind": "role_assign",
                "action_status": "suggested",
                "safety": "explicit_user",
                "requires_explicit_user": True,
            }
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
    if mode == "trace":
        query_id = trace_card.get("query_id") if isinstance(trace_card, dict) else None
        return {
            "mode": mode,
            "summary": "Leader recommends inspecting a specific communication trace without mutating messages or runtime state.",
            "reason": "human asked to inspect one communication lineage by id",
            "next_command": next_command,
            "recommended_action_id": query_id,
            "action_kind": "trace",
            "action_status": "found" if query_id else "missing",
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
        if approval_action_kind == "approval_create":
            approval_id = pending.get("approval_id") if isinstance(pending, dict) else None
            return {
                "mode": mode,
                "summary": "Leader created a pending approval from explicit task assignment without dispatching runtime work.",
                "reason": "human asked to assign a task to an agent",
                "next_command": next_command,
                "recommended_action_id": approval_id,
                "action_kind": "approval_create",
                "action_status": pending.get("status") if isinstance(pending, dict) else "missing",
                "safety": "explicit_runtime",
                "requires_explicit_user": True,
            }
        if approval_action_kind == "approval_dispatch_batch":
            count = (
                dispatch_batch_preview_card.get("count")
                if isinstance(dispatch_batch_preview_card, dict)
                else len(_approved_approval_items(approval_card)) if isinstance(approval_card, dict) else 0
            )
            blocked_count = (
                dispatch_batch_preview_card.get("blocked_count")
                if isinstance(dispatch_batch_preview_card, dict)
                else 0
            )
            return {
                "mode": mode,
                "summary": "Leader previews all approved approval dispatches without mutating runtime state.",
                "reason": "human asked to dispatch all approved approvals",
                "next_command": next_command,
                "recommended_action_id": f"{count} approvals",
                "action_kind": "approval_dispatch_batch",
                "action_status": "partially_blocked" if blocked_count else "ready",
                "safety": "explicit_runtime",
                "requires_explicit_user": True,
            }
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


def _chat_run_start_task(message: str) -> str | None:
    text = message.strip()
    prefixes = (
        "开始运行",
        "开始执行",
        "启动运行",
        "运行",
        "执行",
        "/run",
        "run ",
        "start run ",
    )
    lowered = text.lower()
    for prefix in prefixes:
        candidate = lowered if prefix in {"run ", "start run "} else text
        if not candidate.startswith(prefix):
            continue
        task = text[len(prefix) :].strip(" ：:，,")
        return task or None
    return None


def _chat_run_progress_plan_id(message: str) -> str | None:
    if not _chat_wants_run_progress(message):
        return None
    text = message.strip()
    match = re.search(r"\bpln_[A-Za-z0-9_-]+\b", text)
    return match.group(0) if match else None


def _chat_wants_run_progress(message: str) -> bool:
    text = message.strip()
    return bool(
        re.search(r"(运行进度|执行进度|任务进度|run progress)", text, re.IGNORECASE)
        or re.search(r"(查看|检查|inspect|show|status).*(progress|进度)", text, re.IGNORECASE)
        or re.search(r"(progress|进度).*(查看|检查|inspect|show|status)", text, re.IGNORECASE)
    )


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
        approval_card = _approval_queue_payload(store)
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
            "approval_card": approval_card,
            "runtime_card": None,
            "queue_card": None,
            "operator_card": None,
            "role_card": None,
            "ledger_card": None,
            "workbench_card": None,
            "result": result,
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    run_task = _chat_run_start_task(args.message)
    if run_task:
        try:
            run_start_card, record, _approvals = _create_run_start_payload(
                config,
                store,
                task=run_task,
                provider_override=args.provider,
                model_override=args.model,
                source="leader_chat",
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except RuntimeError as exc:
            print(f"leader provider failed: {exc}", file=sys.stderr)
            return 1
        validation = validate_run_start_contract(run_start_card)
        if not validation["ok"]:
            print("Run start contract validation failed", file=sys.stderr)
            for error in validation["errors"]:
                print(f"- {error}", file=sys.stderr)
            return 1
        turn = store.record_chat_turn(
            mode="run_start",
            message=args.message,
            plan_id=str(record["plan_id"]),
            next_command=run_start_card.get("next_command"),
            provider=str(record["provider"]),
            model=str(record["model"]),
            review=None,
            action_id=None,
            action_kind="run_start",
        )
        store.append_event(
            EventRecord.create(
                "leader_chat_turn",
                {
                    "turn_id": turn["turn_id"],
                    "mode": "run_start",
                    "plan_id": record["plan_id"],
                    "provider": record["provider"],
                    "model": record["model"],
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
            "mode": "run_start",
            "message": args.message,
            "project_view": refreshed_project_view,
            "leader_actions": refreshed_project_view.get("leader_actions"),
            "leader_explanation": _leader_chat_explanation(
                "run_start",
                next_command=run_start_card.get("next_command"),
                project_view=refreshed_project_view,
                result=run_start_card,
            ),
            "plan_id": record["plan_id"],
            "review": None,
            "recovery": refreshed_project_view.get("recovery"),
            "next_command": run_start_card.get("next_command"),
            "leader_action": None,
            "continue_card": None,
            "run_start_card": run_start_card,
            "inbox_card": None,
            "approval_card": run_start_card.get("approval_card"),
            "runtime_card": None,
            "queue_card": None,
            "operator_card": None,
            "role_card": None,
            "ledger_card": None,
            "workbench_card": None,
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    if _chat_wants_run_progress(args.message):
        run_progress_plan_id = _chat_run_progress_plan_id(args.message)
        if run_progress_plan_id is None:
            plans = store.list_plans()
            if not plans:
                print("no plans available for run progress", file=sys.stderr)
                return 1
            run_progress_plan_id = str(plans[-1]["plan_id"])
        try:
            run_progress_card = _run_progress_payload(store, run_progress_plan_id)
        except KeyError:
            print(f"unknown plan: {run_progress_plan_id}", file=sys.stderr)
            return 1
        validation = validate_run_start_contract(run_progress_card)
        if not validation["ok"]:
            print("Run progress contract validation failed", file=sys.stderr)
            for error in validation["errors"]:
                print(f"- {error}", file=sys.stderr)
            return 1
        turn = store.record_chat_turn(
            mode="run_progress",
            message=args.message,
            plan_id=run_progress_plan_id,
            next_command=run_progress_card.get("next_command"),
            review=run_progress_card.get("review") if isinstance(run_progress_card.get("review"), dict) else None,
            action_id=None,
            action_kind="run_progress",
        )
        store.append_event(
            EventRecord.create(
                "leader_chat_turn",
                {
                    "turn_id": turn["turn_id"],
                    "mode": "run_progress",
                    "plan_id": run_progress_plan_id,
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
            "mode": "run_progress",
            "message": args.message,
            "project_view": refreshed_project_view,
            "leader_actions": refreshed_project_view.get("leader_actions"),
            "leader_explanation": _leader_chat_explanation(
                "run_progress",
                next_command=run_progress_card.get("next_command"),
                project_view=refreshed_project_view,
                result=run_progress_card,
            ),
            "plan_id": run_progress_plan_id,
            "review": run_progress_card.get("review"),
            "recovery": refreshed_project_view.get("recovery"),
            "next_command": run_progress_card.get("next_command"),
            "leader_action": None,
            "continue_card": None,
            "run_progress_card": run_progress_card,
            "inbox_card": None,
            "approval_card": run_progress_card.get("approval_card"),
            "runtime_card": None,
            "queue_card": None,
            "operator_card": None,
            "role_card": None,
            "ledger_card": None,
            "workbench_card": None,
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
        control_registry_workbench = _workbench_snapshot_payload(refreshed_project_view, store, since_event_id=None)
        control_registry_filters = _chat_control_registry_filters(args.message)
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
            "control_registry_card": leader_chat_control_registry_card(
                control_registry_workbench,
                scope=control_registry_filters["scope"] if isinstance(control_registry_filters["scope"], str) else None,
                card=control_registry_filters["card"] if isinstance(control_registry_filters["card"], str) else None,
                query=control_registry_filters["query"] if isinstance(control_registry_filters["query"], str) else None,
                control_id=control_registry_filters["control_id"]
                if isinstance(control_registry_filters["control_id"], str)
                else None,
                enabled_only=control_registry_filters["enabled_only"] is True,
            ),
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    if _is_continue_chat_message(args.message):
        plans = store.list_plans()
        plan_id = str(plans[-1]["plan_id"]) if plans else None
        initial_continue_card = _continue_card_payload(project_view, store)
        next_command = initial_continue_card.get("next_command")
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
        continue_card = _continue_card_payload(refreshed_project_view, store)
        next_command = continue_card.get("next_command")
        inbox_card, approval_card = _leader_chat_recovery_cards(refreshed_project_view, store)
        continue_recommended_action = continue_card.get("recommended_action")
        recommended_action = recovery.get("recommended_action") if isinstance(recovery, dict) else None
        runtime_card = (
            _workbench_runtime_card(refreshed_project_view)
            if isinstance(recommended_action, dict) and recommended_action.get("source") == "runtime"
            else None
        )
        terminal_session_card = (
            _workbench_terminal_session_card(config, runtime_card)
            if isinstance(runtime_card, dict)
            else None
        )
        trace_card = (
            _trace_card_for_query(store, recommended_action.get("target_id"))
            if isinstance(recommended_action, dict) and recommended_action.get("source") == "reply"
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
                recommended_action=continue_recommended_action if isinstance(continue_recommended_action, dict) else None,
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
            "terminal_session_card": terminal_session_card,
            "queue_card": None,
            "operator_card": None,
            "role_card": None,
            "trace_card": trace_card,
            "ledger_card": None,
            "workbench_card": None,
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    if _chat_wants_summary(args.message):
        plans = store.list_plans()
        if not plans:
            print("no saved plans to summarize", file=sys.stderr)
            return 1
        latest_plan = plans[-1]
        plan_id = str(latest_plan["plan_id"])
        review = _leader_review_payload(store.leader_review(plan_id))
        if review.get("next_action") != "summarize":
            reason = review.get("reason") or "plan is not ready to summarize"
            print(f"plan is not ready to summarize: {reason}", file=sys.stderr)
            return 1
        next_command = f"agentdeck leader summary --plan-id {plan_id}"
        summary_card = _leader_summary_payload(store, plan_id)
        turn = store.record_chat_turn(
            mode="summary",
            message=args.message,
            plan_id=plan_id,
            next_command=next_command,
            review=review,
            action_id=None,
            action_kind="leader_summary",
        )
        store.append_event(
            EventRecord.create(
                "leader_chat_turn",
                {
                    "turn_id": turn["turn_id"],
                    "mode": "summary",
                    "plan_id": plan_id,
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
            "mode": "summary",
            "message": args.message,
            "project_view": refreshed_project_view,
            "leader_actions": refreshed_project_view.get("leader_actions"),
            "leader_explanation": _leader_chat_explanation(
                "summary",
                next_command=next_command,
                project_view=refreshed_project_view,
                review=review,
            ),
            "plan_id": plan_id,
            "review": review,
            "recovery": refreshed_project_view.get("recovery"),
            "next_command": next_command,
            "leader_action": None,
            "leader_summary_card": summary_card,
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
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    policy_mode = _chat_policy_mode(args.message)
    if policy_mode is not None:
        next_command = f"agentdeck policy set-mode --mode {policy_mode}"
        turn = store.record_chat_turn(
            mode="policy",
            message=args.message,
            plan_id=None,
            next_command=next_command,
            review=None,
            action_id=None,
            action_kind="policy_mode",
        )
        store.append_event(
            EventRecord.create(
                "leader_chat_turn",
                {
                    "turn_id": turn["turn_id"],
                    "mode": "policy",
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
            "mode": "policy",
            "message": args.message,
            "project_view": refreshed_project_view,
            "leader_actions": refreshed_project_view.get("leader_actions"),
            "leader_explanation": _leader_chat_explanation(
                "policy",
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
            "control_mode_card": _workbench_control_mode_card(refreshed_project_view),
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    provider_switch_intent = _chat_provider_switch_intent(args.message)
    if provider_switch_intent is not None:
        target_provider, target_model, require_ready = provider_switch_intent
        next_command = f"agentdeck leader set-provider --provider {target_provider} --model {target_model}"
        if require_ready:
            next_command = f"{next_command} --require-ready"
        turn = store.record_chat_turn(
            mode="setup",
            message=args.message,
            plan_id=None,
            next_command=next_command,
            review=None,
            action_id=None,
            action_kind="provider_switch",
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
        provider_health = _workbench_provider_health(refreshed_project_view)
        provider_switch_card = _leader_chat_provider_switch_card(
            refreshed_project_view,
            target_provider=target_provider,
            target_model=target_model,
            require_ready=require_ready,
            command=next_command,
        )
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
            "provider_health": provider_health,
            "provider_switch_card": provider_switch_card,
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    provider_setup_intent = _chat_provider_setup_intent(args.message)
    if provider_setup_intent is not None:
        target_provider, query, next_command, require_ready = provider_setup_intent
        turn = store.record_chat_turn(
            mode="setup",
            message=args.message,
            plan_id=None,
            next_command=next_command,
            review=None,
            action_id=None,
            action_kind="provider_setup",
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
        control_registry_workbench = _workbench_snapshot_payload(refreshed_project_view, store, since_event_id=None)
        selected_setup_control_id = _control_registry_id_for_command(
            control_registry_workbench,
            scope="provider",
            kind="setup_provider",
            command=next_command,
        )
        setup_commands = _provider_setup_commands(target_provider)
        target_model = next(
            (switch_model for switch_provider, switch_model, _label in LEADER_PROVIDER_SWITCHES if switch_provider == target_provider),
            "",
        )
        provider_switch_command = f"agentdeck leader set-provider --provider {target_provider} --model {target_model}"
        if require_ready:
            provider_switch_command = f"{provider_switch_command} --require-ready"
        provider_switch_card = _leader_chat_provider_switch_card(
            refreshed_project_view,
            target_provider=target_provider,
            target_model=target_model,
            require_ready=require_ready,
            command=provider_switch_command,
        )
        provider_setup_card = _leader_chat_provider_setup_card(
            target_provider=target_provider,
            target_model=target_model,
            setup_commands=setup_commands,
            recommended_command=next_command,
            recommended_control_id=selected_setup_control_id,
            followup_switch_command=provider_switch_command,
            require_ready=require_ready,
            control_registry_workbench=control_registry_workbench,
        )
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
            "provider_health": _workbench_provider_health(refreshed_project_view),
            "provider_setup_card": provider_setup_card,
            "control_registry_card": leader_chat_control_registry_card(
                control_registry_workbench,
                scope="provider",
                query=query,
                control_id=selected_setup_control_id,
            ),
            "provider_switch_card": provider_switch_card,
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

    terminal_agent_id = _chat_terminal_agent_id(args.message, config)
    if terminal_agent_id is not None:
        terminal_card, exit_code = _agent_terminal_card_payload(config, store, terminal_agent_id)
        if terminal_card is None:
            return exit_code
        next_command = terminal_card["attach_command"]
        turn = store.record_chat_turn(
            mode="terminal",
            message=args.message,
            plan_id=None,
            next_command=next_command,
            review=None,
            action_id=None,
            action_kind="terminal",
        )
        store.append_event(
            EventRecord.create(
                "leader_chat_turn",
                {
                    "turn_id": turn["turn_id"],
                    "mode": "terminal",
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
            "mode": "terminal",
            "message": args.message,
            "project_view": refreshed_project_view,
            "leader_actions": refreshed_project_view.get("leader_actions"),
            "leader_explanation": _leader_chat_explanation(
                "terminal",
                next_command=next_command,
                project_view=refreshed_project_view,
                terminal_card=terminal_card,
            ),
            "plan_id": None,
            "review": None,
            "recovery": refreshed_project_view.get("recovery"),
            "next_command": next_command,
            "leader_action": None,
            "continue_card": None,
            "capture_card": None,
            "terminal_card": terminal_card,
            "inbox_card": None,
            "approval_card": None,
            "runtime_card": None,
            "queue_card": None,
            "operator_card": None,
            "role_card": None,
            "ledger_card": None,
            "lineage_card": None,
            "workbench_card": None,
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    capture_reply_intent = _chat_capture_reply_intent(args.message, config)
    if capture_reply_intent is not None:
        capture_reply_agent_id, capture_reply_message_id = capture_reply_intent
        trace_card = _trace_card_for_query(store, capture_reply_message_id)
        if trace_card is None:
            print(f"unknown trace id: {capture_reply_message_id}", file=sys.stderr)
            return 1
        next_command = f"agentdeck capture-reply --agent {capture_reply_agent_id} --message-id {capture_reply_message_id}"
        turn = store.record_chat_turn(
            mode="capture",
            message=args.message,
            plan_id=None,
            next_command=next_command,
            review=None,
            action_id=None,
            action_kind="capture_reply",
        )
        store.append_event(
            EventRecord.create(
                "leader_chat_turn",
                {
                    "turn_id": turn["turn_id"],
                    "mode": "capture",
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
            "mode": "capture",
            "message": args.message,
            "project_view": refreshed_project_view,
            "leader_actions": refreshed_project_view.get("leader_actions"),
            "leader_explanation": _leader_chat_explanation(
                "capture",
                next_command=next_command,
                project_view=refreshed_project_view,
            ),
            "plan_id": None,
            "review": None,
            "recovery": refreshed_project_view.get("recovery"),
            "next_command": next_command,
            "leader_action": None,
            "continue_card": None,
            "capture_card": None,
            "terminal_card": None,
            "trace_card": trace_card,
            "inbox_card": None,
            "approval_card": None,
            "runtime_card": None,
            "queue_card": None,
            "operator_card": None,
            "role_card": None,
            "ledger_card": None,
            "lineage_card": None,
            "workbench_card": None,
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    current_capture_reply_intent = _chat_current_capture_reply_intent(args.message, store)
    if current_capture_reply_intent is not None:
        plan_id, review, capture_reply_agent_id, capture_reply_message_id = current_capture_reply_intent
        trace_card = _trace_card_for_query(store, capture_reply_message_id)
        if trace_card is None:
            print(f"unknown trace id: {capture_reply_message_id}", file=sys.stderr)
            return 1
        next_command = f"agentdeck capture-reply --agent {capture_reply_agent_id} --message-id {capture_reply_message_id}"
        turn = store.record_chat_turn(
            mode="capture",
            message=args.message,
            plan_id=plan_id,
            next_command=next_command,
            review=review,
            action_id=None,
            action_kind="capture_reply",
        )
        store.append_event(
            EventRecord.create(
                "leader_chat_turn",
                {
                    "turn_id": turn["turn_id"],
                    "mode": "capture",
                    "plan_id": plan_id,
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
            "mode": "capture",
            "message": args.message,
            "project_view": refreshed_project_view,
            "leader_actions": refreshed_project_view.get("leader_actions"),
            "leader_explanation": _leader_chat_explanation(
                "capture",
                next_command=next_command,
                project_view=refreshed_project_view,
            ),
            "plan_id": plan_id,
            "review": review,
            "recovery": refreshed_project_view.get("recovery"),
            "next_command": next_command,
            "leader_action": None,
            "continue_card": None,
            "capture_card": None,
            "terminal_card": None,
            "trace_card": trace_card,
            "inbox_card": None,
            "approval_card": None,
            "runtime_card": None,
            "queue_card": None,
            "operator_card": None,
            "role_card": None,
            "ledger_card": None,
            "lineage_card": None,
            "workbench_card": None,
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    capture_agent_id = _chat_capture_agent_id(args.message, config)
    if capture_agent_id is not None:
        binding, exit_code = _running_binding_or_error(store, capture_agent_id)
        if binding is None:
            return exit_code
        pane_id = str(binding["pane_id"])
        lines = 200
        next_command = f"agentdeck agent capture --agent {capture_agent_id} --lines {lines}"
        output = TmuxBackend().capture_output(config.runtime, pane_id, lines)
        capture_card = {
            "agent_id": capture_agent_id,
            "pane_id": pane_id,
            "lines": lines,
            "capture_command": next_command,
            "output": output,
        }
        turn = store.record_chat_turn(
            mode="capture",
            message=args.message,
            plan_id=None,
            next_command=next_command,
            review=None,
            action_id=None,
            action_kind="capture",
        )
        store.append_event(
            EventRecord.create(
                "leader_chat_turn",
                {
                    "turn_id": turn["turn_id"],
                    "mode": "capture",
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
            "mode": "capture",
            "message": args.message,
            "project_view": refreshed_project_view,
            "leader_actions": refreshed_project_view.get("leader_actions"),
            "leader_explanation": _leader_chat_explanation(
                "capture",
                next_command=next_command,
                project_view=refreshed_project_view,
                capture_card=capture_card,
            ),
            "plan_id": None,
            "review": None,
            "recovery": refreshed_project_view.get("recovery"),
            "next_command": next_command,
            "leader_action": None,
            "continue_card": None,
            "capture_card": capture_card,
            "inbox_card": None,
            "approval_card": None,
            "runtime_card": None,
            "queue_card": None,
            "operator_card": None,
            "role_card": None,
            "ledger_card": None,
            "lineage_card": None,
            "workbench_card": None,
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    trace_query_id = _chat_trace_query_id(args.message)
    if trace_query_id is not None:
        trace_card = _trace_card_for_query(store, trace_query_id)
        if trace_card is None:
            print(f"unknown trace id: {trace_query_id}", file=sys.stderr)
            return 1
        next_command = _trace_command(trace_query_id)
        turn = store.record_chat_turn(
            mode="trace",
            message=args.message,
            plan_id=None,
            next_command=next_command,
            review=None,
            action_id=None,
            action_kind="trace",
        )
        store.append_event(
            EventRecord.create(
                "leader_chat_turn",
                {
                    "turn_id": turn["turn_id"],
                    "mode": "trace",
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
            "mode": "trace",
            "message": args.message,
            "project_view": refreshed_project_view,
            "leader_actions": refreshed_project_view.get("leader_actions"),
            "leader_explanation": _leader_chat_explanation(
                "trace",
                next_command=next_command,
                project_view=refreshed_project_view,
                trace_card=trace_card,
            ),
            "plan_id": None,
            "review": None,
            "recovery": refreshed_project_view.get("recovery"),
            "next_command": next_command,
            "leader_action": None,
            "continue_card": None,
            "inbox_card": None,
            "trace_card": trace_card,
            "approval_card": None,
            "runtime_card": None,
            "queue_card": None,
            "operator_card": None,
            "role_card": None,
            "ledger_card": None,
            "lineage_card": None,
            "workbench_card": None,
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

    if _chat_wants_audit(args.message):
        next_command = "agentdeck events --limit 20"
        turn = store.record_chat_turn(
            mode="audit",
            message=args.message,
            plan_id=None,
            next_command=next_command,
            review=None,
            action_id=None,
            action_kind="audit",
        )
        store.append_event(
            EventRecord.create(
                "leader_chat_turn",
                {
                    "turn_id": turn["turn_id"],
                    "mode": "audit",
                    "plan_id": None,
                    "message_length": len(args.message),
                },
            )
        )
        refreshed_project_view = _project_view_payload_or_error(config, store)
        if refreshed_project_view is None:
            return 1
        audit_card = _workbench_audit_card(refreshed_project_view)
        payload = {
            "ok": True,
            "turn_id": turn["turn_id"],
            "mode": "audit",
            "message": args.message,
            "project_view": refreshed_project_view,
            "leader_actions": refreshed_project_view.get("leader_actions"),
            "leader_explanation": _leader_chat_explanation(
                "audit",
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
            "audit_card": audit_card,
            "workbench_card": None,
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    if _chat_wants_artifacts(args.message):
        next_command = "agentdeck artifacts"
        turn = store.record_chat_turn(
            mode="artifacts",
            message=args.message,
            plan_id=None,
            next_command=next_command,
            review=None,
            action_id=None,
            action_kind="artifacts",
        )
        store.append_event(
            EventRecord.create(
                "leader_chat_turn",
                {
                    "turn_id": turn["turn_id"],
                    "mode": "artifacts",
                    "plan_id": None,
                    "message_length": len(args.message),
                },
            )
        )
        refreshed_project_view = _project_view_payload_or_error(config, store)
        if refreshed_project_view is None:
            return 1
        artifacts_card = _artifacts_card_payload(refreshed_project_view)
        validation = validate_artifacts_contract(artifacts_card)
        if not validation["ok"]:
            print("Artifacts contract validation failed", file=sys.stderr)
            for error in validation["errors"]:
                print(f"- {error}", file=sys.stderr)
            return 1
        payload = {
            "ok": True,
            "turn_id": turn["turn_id"],
            "mode": "artifacts",
            "message": args.message,
            "project_view": refreshed_project_view,
            "leader_actions": refreshed_project_view.get("leader_actions"),
            "leader_explanation": _leader_chat_explanation(
                "artifacts",
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
            "artifacts_card": artifacts_card,
            "workbench_card": None,
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    runtime_send_intent = _chat_runtime_send_intent(args.message, project_view)
    if runtime_send_intent is not None:
        send_agent_id, send_text = runtime_send_intent
        binding, exit_code = _running_binding_or_error(store, send_agent_id)
        if binding is None:
            return exit_code
    else:
        send_agent_id = None
        send_text = None
    runtime_stop_agent_id = _chat_runtime_stop_agent_id(args.message, project_view)
    if runtime_stop_agent_id is not None:
        binding, exit_code = _running_binding_or_error(store, runtime_stop_agent_id)
        if binding is None:
            return exit_code
    runtime_spawn_agent_id = _chat_runtime_spawn_agent_id(args.message, project_view)
    runtime_refresh = _chat_wants_runtime_refresh(args.message)
    runtime_ready = _chat_wants_runtime_ready(args.message)
    if (
        runtime_send_intent is not None
        or runtime_stop_agent_id
        or runtime_spawn_agent_id
        or runtime_refresh
        or runtime_ready
        or _chat_wants_runtime(args.message)
    ):
        initial_agent_ready_card = _agent_ready_card_payload(project_view) if runtime_ready else None
        next_command = (
            _agent_send_command(str(send_agent_id), str(send_text))
            if runtime_send_intent is not None
            else f"agentdeck agent stop --agent {runtime_stop_agent_id}"
            if runtime_stop_agent_id
            else f"agentdeck agent spawn --agent {runtime_spawn_agent_id}"
            if runtime_spawn_agent_id
            else "agentdeck agent refresh"
            if runtime_refresh
            else initial_agent_ready_card.get("next_command")
            if isinstance(initial_agent_ready_card, dict)
            else "agentdeck agent list"
        )
        action_kind = (
            "runtime_send"
            if runtime_send_intent is not None
            else "runtime_stop"
            if runtime_stop_agent_id
            else "runtime_spawn"
            if runtime_spawn_agent_id
            else "runtime_refresh"
            if runtime_refresh
            else "runtime_ready"
            if runtime_ready
            else "runtime"
        )
        turn = store.record_chat_turn(
            mode="runtime",
            message=args.message,
            plan_id=None,
            next_command=next_command,
            review=None,
            action_id=None,
            action_kind=action_kind,
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
        terminal_session_card = _workbench_terminal_session_card(config, runtime_card)
        agent_ready_card = _agent_ready_card_payload(refreshed_project_view) if runtime_ready else None
        startup_preview_source_card = _agent_ready_card_payload(refreshed_project_view) if runtime_spawn_agent_id else agent_ready_card
        startup_preview_card = (
            _startup_preview_card_payload(
                startup_preview_source_card,
                target_agent_id=str(runtime_spawn_agent_id) if runtime_spawn_agent_id else None,
                next_command=str(next_command) if runtime_spawn_agent_id else None,
            )
            if isinstance(startup_preview_source_card, dict) and (runtime_ready or runtime_spawn_agent_id)
            else None
        )
        if isinstance(agent_ready_card, dict):
            next_command = agent_ready_card.get("next_command")
        runtime_action_card = (
            _runtime_action_card_payload(
                runtime_card,
                action="send",
                agent_id=str(send_agent_id),
                command=str(next_command),
                preview_text=str(send_text),
            )
            if runtime_send_intent is not None
            else _runtime_action_card_payload(
                runtime_card,
                action="stop",
                agent_id=str(runtime_stop_agent_id),
                command=str(next_command),
            )
            if runtime_stop_agent_id
            else None
        )
        control_registry_card = None
        if (
            isinstance(agent_ready_card, dict)
            or isinstance(startup_preview_card, dict)
            or isinstance(runtime_action_card, dict)
        ):
            registry_source = {
                "agent_ready_card": agent_ready_card,
                "startup_preview_card": startup_preview_card,
                "runtime_action_card": runtime_action_card,
                "runtime_card": runtime_card,
                "terminal_session_card": terminal_session_card,
            }
            registry_items = _workbench_control_registry(registry_source)
            next_control_id = next(
                (
                    item.get("control_id")
                    for item in registry_items
                    if isinstance(item, dict) and item.get("command") == next_command
                ),
                None,
            )
            registry_card_filter = (
                "agent_ready_card"
                if isinstance(agent_ready_card, dict)
                else "startup_preview_card"
                if isinstance(startup_preview_card, dict)
                else "runtime_action_card"
            )
            control_registry_card = leader_chat_control_registry_card(
                {"control_registry": registry_items},
                card=registry_card_filter,
                control_id=str(next_control_id) if next_control_id else None,
            )
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
                agent_ready_card=agent_ready_card,
            ),
            "plan_id": None,
            "review": None,
            "recovery": refreshed_project_view.get("recovery"),
            "next_command": next_command,
            "leader_action": None,
            "continue_card": None,
            "inbox_card": None,
            "approval_card": None,
            "startup_preview_card": startup_preview_card,
            "agent_ready_card": agent_ready_card,
            "runtime_action_card": runtime_action_card,
            "runtime_card": runtime_card,
            "terminal_session_card": terminal_session_card,
            "control_registry_card": control_registry_card,
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
        workbench_snapshot = _workbench_snapshot_payload(refreshed_project_view, store, since_event_id=None)
        ledger_card = workbench_snapshot["ledger_card"]
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
        workbench_snapshot = _workbench_snapshot_payload(refreshed_project_view, store, since_event_id=None)
        ledger_card = workbench_snapshot["ledger_card"]
        lineage_card = workbench_snapshot["lineage_card"]
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
            "lineage_card": lineage_card,
            "workbench_card": None,
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    role_assignment_intent = _chat_role_assignment_intent(args.message, config)
    if role_assignment_intent is not None:
        role_agent_id, role, role_prompt = role_assignment_intent
        next_command = _agent_assign_role_command(role_agent_id, role, role_prompt)
        turn = store.record_chat_turn(
            mode="role",
            message=args.message,
            plan_id=None,
            next_command=next_command,
            review=None,
            action_id=None,
            action_kind="role_assign",
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

    task_assignment_intent = _chat_task_assignment_intent(args.message, config)
    if task_assignment_intent is not None:
        task_agent_id, task = task_assignment_intent
        agent = _agent_by_id(config, task_agent_id)
        if agent is None:
            print(f"unknown agent: {task_agent_id}", file=sys.stderr)
            return 1
        approval = store.create_chat_assignment_approval(agent.agent_id, agent.role, task)
        approval_id = str(approval["approval_id"])
        next_command = f"agentdeck approval approve --approval-id {approval_id}"
        store.append_event(
            EventRecord.create(
                "approval_created_from_chat",
                {
                    "approval_id": approval_id,
                    "agent_id": agent.agent_id,
                    "task": task,
                },
            )
        )
        turn = store.record_chat_turn(
            mode="approval",
            message=args.message,
            plan_id=None,
            next_command=next_command,
            review=None,
            action_id=None,
            action_kind="approval_create",
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
        approval_card = _approval_queue_payload(store)
        validation = validate_approval_contract(approval_card)
        if not validation["ok"]:
            print("Approval queue contract validation failed", file=sys.stderr)
            for error in validation["errors"]:
                print(f"- {error}", file=sys.stderr)
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
                approval_action_kind="approval_create",
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
        next_command = _queue_mode_next_command(continue_card, operator_card)
        queue_card["next_command"] = next_command
        operator_card["next_command"] = next_command
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
        next_command = _queue_mode_next_command(continue_card, operator_card)
        queue_card["next_command"] = next_command
        operator_card["next_command"] = next_command
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
        approved_approvals = _approved_approval_items(approval_card)
        wants_approve = _chat_wants_approval_approve(args.message)
        wants_reject = _chat_wants_approval_reject(args.message)
        wants_dispatch = _chat_wants_approval_dispatch(args.message)
        wants_dispatch_all = _chat_wants_approval_dispatch_all(args.message)
        next_command = (
            "agentdeck approval dispatch-ready --confirm"
            if wants_dispatch_all and approved_approvals
            else
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
            "approval_dispatch_batch"
            if wants_dispatch_all and approved_approvals
            else
            "approval_dispatch"
            if wants_dispatch and isinstance(approved_approval, dict)
            else "approval_reject"
            if wants_reject and isinstance(pending_approval, dict)
            else "approval_approve"
            if wants_approve and isinstance(pending_approval, dict)
            else "approval"
        )
        dispatch_preview_card = (
            _approval_dispatch_preview_card(approved_approval, config, store)
            if approval_action_kind == "approval_dispatch" and isinstance(approved_approval, dict)
            else None
        )
        dispatch_batch_preview_card = (
            _approval_dispatch_batch_preview_card(approved_approvals, config, store)
            if approval_action_kind == "approval_dispatch_batch"
            else None
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
                dispatch_batch_preview_card=dispatch_batch_preview_card,
            ),
            "plan_id": None,
            "review": None,
            "recovery": refreshed_project_view.get("recovery"),
            "next_command": next_command,
            "leader_action": None,
            "continue_card": None,
            "inbox_card": None,
            "dispatch_preview_card": dispatch_preview_card,
            "dispatch_batch_preview_card": dispatch_batch_preview_card,
            "approval_card": approval_card,
            "runtime_card": None,
            "queue_card": None,
            "operator_card": None,
            "role_card": None,
            "ledger_card": None,
            "workbench_card": None,
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)

    inbox_agent_id = _chat_recovery_inbox_agent_id(args.message, config, store, project_view)
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
        trace_card = (
            _trace_card_for_query(store, head.get("inbox_id"))
            if inbox_action_kind == "inbox_trace" and isinstance(head, dict)
            else None
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
            "trace_card": trace_card,
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
        plan = orchestrator.plan(args.message, model_label)
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
        "provider_backend": plan.get("provider_backend") or leader_provider_backend(str(plan.get("provider") or "")),
        "provider_transport": plan.get("provider_transport")
        or leader_provider_transport(str(plan.get("provider") or "")),
        "leader_backend": plan.get("leader_backend")
        or leader_backend_identity(
            str(plan.get("provider") or ""),
            str(plan.get("model") or ""),
            bool(plan.get("dispatch_ready", False)),
        ),
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


def _approval_queue_payload(store: StateStore, plan_id: object = None) -> dict[str, object]:
    raw_approvals = store.list_approvals()
    if plan_id is not None:
        raw_approvals = [approval for approval in raw_approvals if approval.get("plan_id") == plan_id]
    approvals = [_approval_queue_item(approval) for approval in raw_approvals]
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


def _dispatch_approved_approval(
    approval: dict[str, object],
    *,
    approval_id: str,
    config: ProjectConfig,
    store: StateStore,
    backend: TmuxBackend,
) -> dict[str, object]:
    agent_id = str(approval.get("agent_id", ""))
    agent = _agent_by_id(config, agent_id)
    if agent is None:
        raise ValueError(f"unknown agent: {agent_id}")
    binding = store.agent_binding(agent_id)
    if not binding or not binding.get("pane_id"):
        raise RuntimeError(f"agent is not spawned: {agent_id}")
    pane_id = str(binding["pane_id"])
    task = str(approval.get("task", ""))
    prompt = build_dispatch_prompt(agent, task)
    records = store.create_dispatch_records("leader", agent.agent_id, task, prompt, pane_id)
    message = records["message"]
    attempt = records["attempt"]
    job = records["job"]
    backend.send_input(config.runtime, pane_id, prompt)
    store.mark_approval_dispatched(
        approval_id,
        str(message["message_id"]),
        str(attempt["attempt_id"]),
        str(job["job_id"]),
    )
    store.append_event(
        EventRecord.create(
            "approval_dispatched",
            {
                "approval_id": approval_id,
                "plan_id": approval.get("plan_id"),
                "message_id": message["message_id"],
                "agent_id": agent.agent_id,
                "pane_id": pane_id,
            },
        )
    )
    inbox_card = _inbox_queue_payload(agent.agent_id, store)
    validation = validate_inbox_contract(inbox_card)
    if not validation["ok"]:
        error = "; ".join(str(item) for item in validation["errors"])
        raise ValueError(f"Inbox contract validation failed: {error}")
    return {
        "ok": True,
        "approval_id": approval_id,
        "message_id": message["message_id"],
        "agent_id": agent.agent_id,
        "pane_id": pane_id,
        "trace_command": _trace_command(message["message_id"]),
        "inbox_card": inbox_card,
    }


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
    try:
        payload = _dispatch_approved_approval(
            approval,
            approval_id=args.approval_id,
            config=config,
            store=store,
            backend=TmuxBackend(),
        )
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _print_json(payload)
    return 0


def approval_dispatch_ready_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    if not args.confirm:
        print("approval dispatch-ready requires --confirm", file=sys.stderr)
        return 1
    approvals = [
        approval
        for approval in store.load().get("approvals", [])
        if isinstance(approval, dict) and approval.get("status") == "approved"
    ]
    backend = TmuxBackend()
    results: list[dict[str, object]] = []
    for approval in approvals:
        approval_id = str(approval.get("approval_id", ""))
        preview = _approval_dispatch_preview_card(approval, config, store)
        blocker = preview.get("blocker")
        if blocker:
            results.append(
                {
                    "approval_id": approval_id,
                    "status": "blocked",
                    "agent_id": preview.get("agent_id"),
                    "pane_id": preview.get("pane_id"),
                    "message_id": None,
                    "trace_command": None,
                    "blocker": blocker,
                    "dispatch_command": preview.get("dispatch_command"),
                }
            )
            continue
        dispatched = _dispatch_approved_approval(
            approval,
            approval_id=approval_id,
            config=config,
            store=store,
            backend=backend,
        )
        results.append(
            {
                "approval_id": approval_id,
                "status": "dispatched",
                "agent_id": dispatched["agent_id"],
                "pane_id": dispatched["pane_id"],
                "message_id": dispatched["message_id"],
                "trace_command": dispatched["trace_command"],
                "blocker": None,
                "dispatch_command": f"agentdeck approval dispatch --approval-id {approval_id}",
            }
        )
    dispatched_count = sum(1 for item in results if item.get("status") == "dispatched")
    blocked_count = sum(1 for item in results if item.get("status") == "blocked")
    store.append_event(
        EventRecord.create(
            "approval_dispatch_ready_completed",
            {
                "dispatched_count": dispatched_count,
                "blocked_count": blocked_count,
            },
        )
    )
    payload = {
        "ok": True,
        "mode": "dispatch_ready",
        "requires_explicit_user": True,
        "safety": "explicit_runtime",
        "dispatched_count": dispatched_count,
        "blocked_count": blocked_count,
        "skipped_count": blocked_count,
        "results": results,
    }
    validation = validate_approval_dispatch_ready_contract(payload)
    if not validation["ok"]:
        print("Approval dispatch-ready contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1
    _print_json(payload)
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

    run = subparsers.add_parser("run", help="Start an approval-gated multi-agent run")
    run_target = run.add_mutually_exclusive_group(required=True)
    run_target.add_argument("--task", help="Goal for the Leader Agent to plan and queue for approval")
    run_target.add_argument("--plan-id", help="Existing plan id to inspect as a run progress card")
    run.add_argument("--provider", help="Leader provider to use; defaults to .agentdeck/config.toml")
    run.add_argument("--model", help="Provider model label recorded with the plan; defaults to config")
    run.set_defaults(func=run_command)

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

    controls = subparsers.add_parser("controls", help="Show the GUI-ready command palette from the workbench")
    controls.add_argument("--scope", help="Filter command palette controls by scope")
    controls.add_argument("--card", help="Filter command palette controls by source card")
    controls.add_argument("--query", help="Search command palette controls by label, kind, command, scope, card, or agent")
    controls.add_argument("--control-id", help="Filter command palette controls by stable control_id")
    controls.add_argument("--enabled-only", action="store_true", help="Only include enabled command palette controls")
    controls.set_defaults(func=controls_command)

    policy = subparsers.add_parser("policy", help="Policy and control mode commands")
    policy_subparsers = policy.add_subparsers(dest="policy_command")
    policy_set_mode = policy_subparsers.add_parser("set-mode", help="Set explicit control mode")
    policy_set_mode.add_argument("--mode", required=True, choices=["ask", "approve", "autonomous"])
    policy_set_mode.set_defaults(func=policy_set_mode_command)

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
    contract_run = contract_subparsers.add_parser(
        "run",
        help="Show run start response contract discovery metadata",
    )
    contract_run.add_argument("--example", action="store_true", help="Include a GUI-ready run start example")
    contract_run.set_defaults(func=contract_run_command)
    contract_workbench = contract_subparsers.add_parser(
        "workbench",
        help="Show workbench snapshot contract discovery metadata",
    )
    contract_workbench.add_argument("--example", action="store_true", help="Include a GUI-ready workbench example")
    contract_workbench.set_defaults(func=contract_workbench_command)
    contract_controls = contract_subparsers.add_parser(
        "controls",
        help="Show command palette contract discovery metadata",
    )
    contract_controls.add_argument("--example", action="store_true", help="Include a GUI-ready controls example")
    contract_controls.set_defaults(func=contract_controls_command)
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
    contract_leader_summary = contract_subparsers.add_parser(
        "leader-summary",
        help="Show Leader summary response contract discovery metadata",
    )
    contract_leader_summary.add_argument(
        "--example",
        action="store_true",
        help="Include a GUI-ready Leader summary response example",
    )
    contract_leader_summary.set_defaults(func=contract_leader_summary_command)
    contract_trace = contract_subparsers.add_parser(
        "trace",
        help="Show communication trace contract discovery metadata",
    )
    contract_trace.add_argument("--example", action="store_true", help="Include a GUI-ready trace example")
    contract_trace.set_defaults(func=contract_trace_command)
    contract_artifacts = contract_subparsers.add_parser(
        "artifacts",
        help="Show artifact index contract discovery metadata",
    )
    contract_artifacts.add_argument("--example", action="store_true", help="Include a GUI-ready artifacts example")
    contract_artifacts.set_defaults(func=contract_artifacts_command)

    project = subparsers.add_parser("project", help="Project management commands")
    project_subparsers = project.add_subparsers(dest="project_command")
    project_init = project_subparsers.add_parser("init", help="Initialize .agentdeck project state")
    project_init.set_defaults(func=init_command)

    agent = subparsers.add_parser("agent", help="Agent runtime commands")
    agent_subparsers = agent.add_subparsers(dest="agent_command")

    agent_list = agent_subparsers.add_parser("list", help="List configured agents and runtime bindings")
    agent_list.set_defaults(func=agent_list_command)

    agent_ready = agent_subparsers.add_parser(
        "ready",
        help="Show a read-only startup card for configured agent panes",
    )
    agent_ready.set_defaults(func=agent_ready_command)

    agent_spawn = agent_subparsers.add_parser("spawn", help="Spawn a configured agent in tmux")
    agent_spawn.add_argument("--agent", required=True, help="Agent id from .agentdeck/config.toml")
    agent_spawn.set_defaults(func=agent_spawn_command)

    agent_spawn_ready = agent_subparsers.add_parser(
        "spawn-ready",
        help="Spawn all configured agents that are not already running",
    )
    agent_spawn_ready.add_argument("--confirm", action="store_true", help="Explicitly confirm batch spawn")
    agent_spawn_ready.set_defaults(func=agent_spawn_ready_command)

    agent_capture = agent_subparsers.add_parser("capture", help="Capture output from a spawned agent pane")
    agent_capture.add_argument("--agent", required=True, help="Agent id from .agentdeck/config.toml")
    agent_capture.add_argument("--lines", type=int, default=200, help="Number of recent lines to capture")
    agent_capture.set_defaults(func=agent_capture_command)

    agent_terminal = agent_subparsers.add_parser(
        "terminal",
        help="Show a read-only terminal card for a spawned agent pane",
    )
    agent_terminal.add_argument("--agent", required=True, help="Agent id from .agentdeck/config.toml")
    agent_terminal.set_defaults(func=agent_terminal_command)

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
    leader_set_provider = leader_subparsers.add_parser(
        "set-provider",
        help="Set the default Leader provider in .agentdeck/config.toml",
    )
    leader_set_provider.add_argument(
        "--provider",
        required=True,
        help="Leader provider: fake, deepseek, openai-compatible, codex-cli, or claude-cli",
    )
    leader_set_provider.add_argument("--model", help="Default model label to record for new Leader plans")
    leader_set_provider.add_argument(
        "--require-ready",
        action="store_true",
        help="Reject the provider switch unless the target Leader backend is locally ready",
    )
    leader_set_provider.set_defaults(func=leader_set_provider_command)
    leader_review = leader_subparsers.add_parser("review", help="Review plan progress and recommend next action")
    leader_review.add_argument("--plan-id", required=True, help="Plan id from agentdeck leader plan")
    leader_review.set_defaults(func=leader_review_command)
    leader_summary = leader_subparsers.add_parser("summary", help="Summarize replied steps for a saved Leader plan")
    leader_summary.add_argument("--plan-id", required=True, help="Plan id from agentdeck leader plan")
    leader_summary.set_defaults(func=leader_summary_command)
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
    approval_dispatch_ready = approval_subparsers.add_parser(
        "dispatch-ready",
        help="Dispatch all approved approvals whose agent runtime is ready",
    )
    approval_dispatch_ready.add_argument("--confirm", action="store_true", help="Explicitly confirm batch dispatch")
    approval_dispatch_ready.set_defaults(func=approval_dispatch_ready_command)

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

    trace = subparsers.add_parser("trace", help="Trace message, attempt, job, reply, artifact, or inbox lineage")
    trace.add_argument(
        "--id",
        required=True,
        help="message_id, attempt_id, job_id, reply_id, artifact_id, or inbox_id",
    )
    trace.set_defaults(func=trace_command)

    artifacts = subparsers.add_parser("artifacts", help="List recoverable worker artifacts")
    artifacts.set_defaults(func=artifacts_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    return args.func(args)
