from __future__ import annotations

from dataclasses import asdict
import argparse
import json
import sys

from .config import config_path, load_config, project_root, update_agent_role, write_default_config
from .models import AgentRuntimeBinding, AgentSpec, EventRecord, ProjectConfig
from .providers import DeepSeekProvider
from .runtime import TmuxBackend
from .state import StateStore, agentdeck_dir


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def doctor_command(_args: argparse.Namespace) -> int:
    root = project_root()
    tmux = TmuxBackend().doctor()
    config_exists = config_path(root).exists()
    provider = DeepSeekProvider().doctor()
    ok = tmux.ok and config_exists
    _print_json(
        {
            "ok": ok,
            "root": str(root),
            "config_exists": config_exists,
            "config_path": str(config_path(root)),
            "tmux": asdict(tmux),
            "deepseek": {"ok": provider[0], "detail": provider[1]},
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
    records = store.create_dispatch_records("user", agent.agent_id, args.task, prompt, pane_id)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentdeck")
    subparsers = parser.add_subparsers(dest="command")

    doctor = subparsers.add_parser("doctor", help="Check local AgentDeck prerequisites")
    doctor.set_defaults(func=doctor_command)

    status = subparsers.add_parser("status", help="Show project configuration and runtime state")
    status.set_defaults(func=status_command)

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

    dispatch = subparsers.add_parser("dispatch", help="Send a role-aware task to a running agent")
    dispatch.add_argument("--agent", required=True, help="Agent id from .agentdeck/config.toml")
    dispatch.add_argument("--task", required=True, help="Task text to send with the agent role prompt")
    dispatch.set_defaults(func=dispatch_command)

    inbox = subparsers.add_parser("inbox", help="Show pending inbox items for an agent")
    inbox.add_argument("--agent", required=True, help="Agent id from .agentdeck/config.toml")
    inbox.set_defaults(func=inbox_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    return args.func(args)
