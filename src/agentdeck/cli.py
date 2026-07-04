from __future__ import annotations

from dataclasses import asdict
import argparse
import json
import sys

from .config import config_path, load_config, project_root, write_default_config
from .models import EventRecord
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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    return args.func(args)
