from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Any, TypeVar

from .bindings import consume_preview_binding, execution_digest
from .models import build_preview_binding_record
from ..models import new_id


@dataclass(frozen=True)
class RoutingContext:
    initialized: bool
    leader_ready: bool
    pending_preview: Mapping[str, object] | None


@dataclass(frozen=True)
class RoutingDecision:
    kind: str
    command: str | None = None
    argument: str | None = None
    preview_id: str | None = None
    may_call_leader: bool = False
    requires_confirmation: bool = False
    blocker: str | None = None


_SLASH_COMMANDS = {
    "/help": "help",
    "/leader": "leader",
    "/model": "model",
    "/team": "team",
    "/role": "role",
    "/status": "status",
    "/approvals": "approvals",
    "/trace": "trace",
    "/takeover": "takeover",
    "/return-control": "return_control",
    "/quit": "quit",
}
_EXIT_WORDS = {"quit", "exit", "退出", "/quit"}
_CONFIRM = re.compile(r"^(?:确认|批准|同意)(?:执行|当前|此)?(?:预览|方案|任务|执行)?$|^确认执行当前预览$")


class ConversationRouter:
    def classify(self, text: str, context: RoutingContext) -> RoutingDecision:
        if not isinstance(text, str):
            raise TypeError("conversation input must be a string")
        if not isinstance(context, RoutingContext):
            raise TypeError("context must be RoutingContext")
        normalized = text.strip()
        if not normalized:
            return RoutingDecision("blocked", blocker="empty input")
        if normalized.lower() in _EXIT_WORDS:
            return RoutingDecision("exit", command="quit")

        if normalized.startswith("/"):
            name, _, argument = normalized.partition(" ")
            command = _SLASH_COMMANDS.get(name.lower())
            if command is None:
                return RoutingDecision("blocked", blocker="unknown slash command")
            if command == "quit":
                return RoutingDecision("exit", command="quit")
            if command in {"trace", "takeover"} and not argument.strip():
                return RoutingDecision("blocked", command=command, blocker="missing argument")
            return RoutingDecision(
                "deterministic",
                command=command,
                argument=argument.strip() or None,
            )

        if _CONFIRM.fullmatch(normalized):
            pending = context.pending_preview
            if not isinstance(pending, Mapping) or pending.get("state") != "pending":
                return RoutingDecision("blocked", blocker="no pending preview")
            preview_id = pending.get("preview_id")
            if not isinstance(preview_id, str) or not preview_id:
                return RoutingDecision("blocked", blocker="pending preview invalid")
            return RoutingDecision(
                "confirm_preview",
                preview_id=preview_id,
                requires_confirmation=True,
            )

        if re.search(r"(?:帮助|怎么用|使用说明)", normalized):
            return RoutingDecision("deterministic", command="help")
        if re.search(r"(?:状态|进度|当前情况)", normalized):
            return RoutingDecision("deterministic", command="status")
        if re.search(r"(?:审批|批准项)", normalized):
            return RoutingDecision("deterministic", command="approvals")
        if re.search(r"(?:追踪|trace)\s+\S+", normalized, re.IGNORECASE):
            return RoutingDecision("deterministic", command="trace")

        if (
            isinstance(context.pending_preview, Mapping)
            and context.pending_preview.get("state") == "pending"
        ):
            return RoutingDecision(
                "blocked", blocker="pending preview requires a decision"
            )

        if not context.initialized:
            return RoutingDecision(
                "project_setup_preview", requires_confirmation=True
            )
        if not context.leader_ready:
            return RoutingDecision(
                "leader_setup_preview", requires_confirmation=True
            )
        return RoutingDecision("leader_request", may_call_leader=True)


T = TypeVar("T")


def execute_bound_preview(
    binding: Mapping[str, object],
    current_facts: Mapping[str, object],
    *,
    now: datetime,
    execute: Callable[[dict[str, Any]], T],
) -> T:
    consumed = consume_preview_binding(binding, current_facts, now=now)
    return execute(consumed)


def _project_setup_facts(root: Path) -> dict[str, object]:
    canonical = root.resolve()
    return {
        "control_kind": "project_init",
        "project_root": str(canonical),
        "config_marker_absent": not (canonical / ".agentdeck" / "config.toml").exists(),
    }


def build_project_setup_preview(
    root: Path, *, now: datetime, ttl_seconds: int = 300
) -> dict[str, object]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if type(ttl_seconds) is not int or ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    canonical = root.resolve()
    facts = _project_setup_facts(canonical)
    if facts["config_marker_absent"] is not True:
        raise ValueError("project is already initialized")
    created_at = now.astimezone(timezone.utc)
    binding = build_preview_binding_record(
        new_id("cpv"),
        conversation_id=new_id("cvs"),
        turn_id=None,
        preview_kind="project_init",
        execution_digest=execution_digest(facts),
        expires_at=(created_at + timedelta(seconds=ttl_seconds)).isoformat(),
        created_at=created_at.isoformat(),
    )
    binding["state"] = "pending"
    return {
        "mode": "project_setup_preview",
        "project_root": str(canonical),
        "will_create": ".agentdeck/config.toml",
        "confirm_command": "agentdeck project init",
        "binding": binding,
        "requires_confirmation": True,
        "safety": "explicit_user",
    }


def confirm_project_setup(
    binding: Mapping[str, object],
    root: Path,
    *,
    now: datetime,
    initialize: Callable[[Path], dict[str, object]],
    append_audit: Callable[[dict[str, object]], None],
) -> dict[str, object]:
    facts = _project_setup_facts(root)

    def apply(_consumed: dict[str, Any]) -> dict[str, object]:
        initialized = initialize(root.resolve())
        try:
            append_audit(initialized)
        except OSError:
            return {
                **initialized,
                "status": "initialized_with_audit_blocker",
                "blocker": "project initialized but conversation audit is pending",
            }
        return {**initialized, "status": "initialized", "blocker": None}

    return execute_bound_preview(binding, facts, now=now, execute=apply)
