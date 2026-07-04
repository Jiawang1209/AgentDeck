from __future__ import annotations

from dataclasses import asdict
import argparse
import json
from pathlib import Path
import sys

from .config import config_path, load_config, project_root, update_agent_role, write_default_config
from .models import AgentRuntimeBinding, AgentSpec, EventRecord, ProjectConfig
from .orchestration.leader import LeaderOrchestrator
from .providers import DeepSeekProvider, OpenAICompatibleProvider, leader_provider
from .runtime import TmuxBackend
from .state import StateStore, agentdeck_dir


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def doctor_command(_args: argparse.Namespace) -> int:
    root = project_root()
    tmux = TmuxBackend().doctor()
    config_exists = config_path(root).exists()
    deepseek = DeepSeekProvider().doctor()
    openai_compatible = OpenAICompatibleProvider().doctor()
    ok = tmux.ok and config_exists
    _print_json(
        {
            "ok": ok,
            "root": str(root),
            "config_exists": config_exists,
            "config_path": str(config_path(root)),
            "tmux": asdict(tmux),
            "deepseek": {"ok": deepseek[0], "detail": deepseek[1]},
            "openai_compatible": {"ok": openai_compatible[0], "detail": openai_compatible[1]},
        }
    )
    return 0 if ok else 1


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
    _print_json(asdict(store.project_view(config)))
    return 0


def events_command(args: argparse.Namespace) -> int:
    _config, store, exit_code = _load_project_or_error()
    if store is None:
        return exit_code
    events = store.list_events(args.limit)
    _print_json({"count": len(events), "limit": args.limit, "events": events})
    return 0


def contract_project_view_command(_args: argparse.Namespace) -> int:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "project-view-schema.md"
    _print_json(
        {
            "schema_version": "project-view/v1",
            "status_command": "agentdeck status",
            "contract_path": str(contract_path),
            "contract_exists": contract_path.exists(),
            "top_level_fields": [
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
            ],
            "recovery_fields": [
                "status",
                "reason",
                "next_command",
                "recommended_action",
                "pending",
                "leader_action",
                "latest_event",
                "recent_events",
            ],
            "recommended_action_fields": [
                "label",
                "command",
                "safety",
                "requires_explicit_user",
                "source",
                "target_id",
            ],
        }
    )
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
    items = store.inbox_items(args.agent)
    _print_json({"agent_id": args.agent, "count": len(items), "items": items})
    return 0


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


def leader_plan_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    try:
        provider = leader_provider(args.provider)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    orchestrator = LeaderOrchestrator(config, provider)
    try:
        plan = orchestrator.plan(args.task)
    except RuntimeError as exc:
        _record_leader_provider_failure(store, "plan", provider.name, args.model, args.task, exc)
        print(f"leader provider failed: {exc}", file=sys.stderr)
        return 1
    record = store.record_plan(args.task, provider.name, args.model, plan)
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
    _config, store, exit_code = _load_project_or_error()
    if store is None:
        return exit_code
    try:
        review = store.leader_review(args.plan_id)
    except KeyError:
        print(f"unknown plan: {args.plan_id}", file=sys.stderr)
        return 1
    _print_json(review)
    return 0


def leader_next_command(args: argparse.Namespace) -> int:
    _config, store, exit_code = _load_project_or_error()
    if store is None:
        return exit_code
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


def _leader_action_summary(action: dict[str, object]) -> dict[str, object]:
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
    }


def leader_actions_command(_args: argparse.Namespace) -> int:
    _config, store, exit_code = _load_project_or_error()
    if store is None:
        return exit_code
    actions = [_leader_action_summary(action) for action in store.list_leader_actions()]
    _print_json({"count": len(actions), "actions": actions})
    return 0


def leader_action_command(args: argparse.Namespace) -> int:
    _config, store, exit_code = _load_project_or_error()
    if store is None:
        return exit_code
    try:
        action = store.leader_action_detail(args.action_id)
    except KeyError:
        print(f"unknown leader action: {args.action_id}", file=sys.stderr)
        return 1
    _print_json(action)
    return 0


def leader_apply_action_command(args: argparse.Namespace) -> int:
    _config, store, exit_code = _load_project_or_error()
    if store is None:
        return exit_code
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


def leader_chat_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    project_view = asdict(store.project_view(config))
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
        turn = store.record_chat_turn(
            mode="apply_action",
            message=args.message,
            plan_id=str(action.get("plan_id")),
            next_command=None,
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
        _print_json(
            {
                "ok": True,
                "turn_id": turn["turn_id"],
                "mode": "apply_action",
                "message": args.message,
                "project_view": asdict(store.project_view(config)),
                "plan_id": action.get("plan_id"),
                "leader_action": action_detail,
                "result": result,
            }
        )
        return 0

    plans = store.list_plans()
    if plans:
        latest_plan = plans[-1]
        plan_id = str(latest_plan["plan_id"])
        review = store.leader_review(plan_id)
        action = store.suggest_leader_action(plan_id)
        action_detail = store.leader_action_detail(str(action["action_id"]))
        project_view = asdict(store.project_view(config))
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
            "plan_id": plan_id,
            "review": review,
            "recovery": recovery,
            "next_command": next_command,
            "leader_action": action_detail,
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
        _print_json(payload)
        return 0

    try:
        provider = leader_provider(args.provider)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    orchestrator = LeaderOrchestrator(config, provider)
    try:
        plan = orchestrator.plan(args.message)
    except RuntimeError as exc:
        _record_leader_provider_failure(store, "chat", provider.name, args.model, args.message, exc)
        print(f"leader provider failed: {exc}", file=sys.stderr)
        return 1
    record = store.record_plan(args.message, provider.name, args.model, plan)
    next_command = f"agentdeck approval create-from-plan --plan-id {record['plan_id']}"
    turn = store.record_chat_turn(
        mode="plan",
        message=args.message,
        plan_id=str(record["plan_id"]),
        next_command=next_command,
        provider=record["provider"],
        model=record["model"],
        review=None,
    )
    payload = {
        "ok": True,
        "turn_id": turn["turn_id"],
        "mode": "plan",
        "message": args.message,
        "project_view": project_view,
        "plan_id": record["plan_id"],
        "status": record["status"],
        "provider": record["provider"],
        "model": record["model"],
        "dispatch_ready": record["dispatch_ready"],
        "plan": record["plan"],
        "review": None,
        "next_command": next_command,
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
    _print_json(payload)
    return 0


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
    approvals = store.list_approvals()
    _print_json({"count": len(approvals), "approvals": approvals})
    return 0


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
    events.set_defaults(func=events_command)

    contract = subparsers.add_parser("contract", help="Discover machine-readable AgentDeck contracts")
    contract_subparsers = contract.add_subparsers(dest="contract_command")
    contract_project_view = contract_subparsers.add_parser(
        "project-view",
        help="Show ProjectView contract discovery metadata",
    )
    contract_project_view.set_defaults(func=contract_project_view_command)

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
    leader_plan.add_argument("--provider", default="fake", help="Leader provider to use; defaults to local fake")
    leader_plan.add_argument("--model", default="fake-plan", help="Provider model label recorded with the plan")
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
    leader_chat.add_argument("--provider", default="fake", help="Leader provider to use when a new plan is needed")
    leader_chat.add_argument("--model", default="fake-plan", help="Provider model label recorded with new plans")
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
