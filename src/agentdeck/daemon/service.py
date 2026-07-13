"""Authoritative bounded project-daemon service composition.

The service serializes every durable mutation and external-effect completion
through one queue.  Worker I/O may run concurrently, but it can only enqueue a
completion callback; it never receives the service's mutation authority.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
import hashlib
import inspect
import math
from pathlib import Path
import re
import shutil
from typing import Any, Protocol

from .scheduler import SchedulerDecision, SchedulerFacts, schedule_gate
from .supervisor import SubmittedReceipt, TransportResult
from ..workflow import build_canonical_handoff, validate_canonical_handoff


class ServiceError(RuntimeError):
    """The daemon service could not preserve its authority boundary."""


class ServiceServer(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...


def authorize_mission_acp_effect(
    store: object, *, attempt_id: str, permission_id: str
) -> dict[str, object]:
    """Invoke the StateStore's atomic three-gate ACP effect decision."""
    if not callable(getattr(store, "authorize_mission_acp_effect", None)):
        raise ServiceError("ACP effect authority store is invalid")
    try:
        result = store.authorize_mission_acp_effect(
            attempt_id=attempt_id, permission_id=permission_id
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ServiceError("ACP effect authority is invalid") from None
    if type(result) is not dict or result.get("allowed") not in {True, False}:
        raise ServiceError("ACP effect authority result is invalid")
    return result


def _canonical_transport_result(
    result: object, *, dispatch_key: str, transport: str
) -> dict[str, object] | None:
    if not isinstance(result, TransportResult) or not result.validated:
        return None
    if transport == "acp" and result.stop_reason != "end_turn":
        return None
    if transport == "tmux" and result.stop_reason != "structured_reply":
        return None
    try:
        return build_canonical_handoff(
            reply=result.reply,
            artifacts=[
                {"path": item.path, "content_hash": item.content_hash}
                for item in result.artifacts
            ],
            trace_ids=list(result.trace_ids),
            expected_handoff_token=dispatch_key,
            require_artifact_hashes=True,
        ).compact()
    except (AttributeError, TypeError, ValueError):
        return None


def resolve_previous_handoff(
    state: object, attempt: Mapping[str, object]
) -> dict[str, object] | None:
    """Resolve only the exact frozen predecessor's recorded compact handoff."""
    if type(state) is not dict:
        raise ServiceError("previous Worker handoff state is invalid")
    mission_id = attempt.get("mission_id")
    step_id = attempt.get("step_id")
    agent_id = attempt.get("agent_id")
    missions = [
        item for item in state.get("missions", [])
        if type(item) is dict and item.get("mission_id") == mission_id
    ]
    if len(missions) != 1:
        raise ServiceError("previous Worker handoff Mission is invalid")
    snapshot = missions[0].get("execution_snapshot")
    mission_snapshot = snapshot.get("mission") if type(snapshot) is dict else None
    steps = mission_snapshot.get("steps") if type(mission_snapshot) is dict else None
    if type(steps) is not list or any(type(item) is not dict for item in steps):
        raise ServiceError("previous Worker handoff step order is invalid")
    indexes = [
        index for index, item in enumerate(steps)
        if item.get("step_id") == step_id and item.get("agent_id") == agent_id
    ]
    if len(indexes) != 1:
        raise ServiceError("previous Worker handoff current step is invalid")
    index = indexes[0]
    if index == 0:
        return None
    previous_step = steps[index - 1]
    attempts = [
        item for item in state.get("mission_attempts", [])
        if type(item) is dict
        and item.get("mission_id") == mission_id
        and item.get("step_id") == previous_step.get("step_id")
        and item.get("agent_id") == previous_step.get("agent_id")
        and item.get("state") == "succeeded"
    ]
    if len(attempts) != 1:
        raise ServiceError("previous Worker handoff attempt is ambiguous")
    previous_attempt = attempts[0]
    previous_attempt_id = previous_attempt.get("attempt_id")
    dispatch_key = previous_attempt.get("dispatch_key")
    replies = [
        item for item in state.get("mission_worker_replies", [])
        if type(item) is dict
        and item.get("mission_id") == mission_id
        and item.get("attempt_id") == previous_attempt_id
        and item.get("dispatch_key") == dispatch_key
        and item.get("state") == "validated"
    ]
    if len(replies) != 1:
        raise ServiceError("previous Worker handoff validated reply is invalid")
    reply = replies[0]
    handoffs = [
        item for item in state.get("mission_handoffs", [])
        if type(item) is dict
        and item.get("mission_id") == mission_id
        and item.get("attempt_id") == previous_attempt_id
        and item.get("reply_id") == reply.get("reply_id")
        and item.get("state") == "recorded"
    ]
    if len(handoffs) != 1:
        raise ServiceError("previous Worker handoff recorded evidence is invalid")
    reply_content = reply.get("canonical_handoff")
    handoff_content = handoffs[0].get("canonical_handoff")
    if reply_content != handoff_content:
        raise ServiceError("previous Worker handoff content drift")
    try:
        canonical = validate_canonical_handoff(
            reply_content, expected_handoff_token=dispatch_key  # type: ignore[arg-type]
        )
    except (TypeError, ValueError):
        raise ServiceError("previous Worker handoff content is invalid") from None
    return canonical.compact()


def permission_state_for_attempt(
    state: Mapping[str, object], attempt: Mapping[str, object] | None
) -> str:
    """Derive one attempt's permission from bound protocol authority.

    Request base records remain pending; decisions are append-only protocol
    transitions.  Never infer permission from ACP recommendations or Worker
    output.
    """
    if attempt is None:
        return "none"
    def records(name: str) -> list[object]:
        value = state.get(name, [])
        if type(value) is not list:
            raise ServiceError("Mission permission state is invalid")
        return value

    attempt_id = attempt.get("attempt_id")
    mission_id = attempt.get("mission_id")
    agent_id = attempt.get("agent_id")
    bindings = [
        item
        for item in records("mission_permission_bindings")
        if type(item) is dict and item.get("attempt_id") == attempt_id
    ]
    if not bindings:
        return "none"
    if len(bindings) != 1 or bindings[0].get("mission_id") != mission_id:
        raise ServiceError("Mission permission binding is invalid")
    permission_id = bindings[0].get("permission_id")
    permissions = [
        item
        for item in records("permission_requests")
        if type(item) is dict and item.get("permission_id") == permission_id
    ]
    if len(permissions) != 1:
        raise ServiceError("Mission permission request is invalid")
    permission = permissions[0]
    sessions = [
        item
        for item in records("agent_sessions")
        if type(item) is dict and item.get("session_id") == permission.get("session_id")
    ]
    if len(sessions) != 1 or sessions[0].get("agent_id") != agent_id:
        raise ServiceError("Mission permission agent scope is invalid")
    current = permission.get("status")
    if current != "pending":
        raise ServiceError("Mission permission base state is invalid")
    for transition in records("protocol_state_transitions"):
        if (
            type(transition) is not dict
            or transition.get("entity_type") != "permission"
            or transition.get("entity_id") != permission_id
        ):
            continue
        if transition.get("from_state") != current:
            raise ServiceError("Mission permission history is invalid")
        if current != "pending":
            raise ServiceError("Mission permission history is invalid")
        next_state = transition.get("to_state")
        if next_state not in {"approved", "denied", "expired"}:
            raise ServiceError("Mission permission history is invalid")
        current = next_state
    return str(current)


def _mission_pause_facts(store: object, mission_id: str) -> dict[str, object]:
    if not callable(getattr(store, "load", None)):
        raise ServiceError("Mission pause store is invalid")
    state = store.load()
    missions = [
        item for item in state.get("missions", [])
        if type(item) is dict and item.get("mission_id") == mission_id
    ]
    if len(missions) != 1:
        raise ServiceError("Mission pause target is invalid")
    mission = missions[0]
    attempts = [
        {
            "attempt_id": item.get("attempt_id"),
            "state": item.get("state"),
            "dispatch_key": item.get("dispatch_key"),
        }
        for item in state.get("mission_attempts", [])
        if type(item) is dict
        and item.get("mission_id") == mission_id
        and item.get("state") not in {
            "succeeded", "completed", "failed", "cancelled", "interrupted", "ambiguous"
        }
    ]
    return {
        "mission_id": mission_id,
        "status": mission.get("status"),
        "current_step": mission.get("current_step"),
        "snapshot_hash": mission.get("snapshot_hash"),
        "active_attempts": attempts,
    }


def apply_mission_pause_request(
    store: object,
    params: object,
    *,
    generation: int,
    now: datetime,
) -> dict[str, object]:
    """Production RPC domain handler: preview first, exact confirm second."""
    if type(params) is not dict or set(params) not in (
        {"mission_id"},
        {"mission_id", "preview_id"},
    ):
        raise ServiceError("Mission pause request is invalid")
    mission_id = params.get("mission_id")
    if type(mission_id) is not str or re.fullmatch(r"mis_[0-9a-f]{12}", mission_id) is None:
        raise ServiceError("Mission pause request is invalid")
    facts = _mission_pause_facts(store, mission_id)
    if facts["status"] != "running":
        raise ServiceError("Mission pause target is not running")
    if facts["active_attempts"]:
        raise ServiceError(
            "Mission pause requires an idle boundary; active attempt must settle or use force-stop"
        )
    preview_id = params.get("preview_id")
    if preview_id is None:
        try:
            return store.record_governance_preview(
                "mission_pause",
                facts=facts,
                generation=generation,
                now=now,
            )
        except (AttributeError, TypeError, ValueError):
            raise ServiceError("Mission pause preview failed") from None
    if type(preview_id) is not str:
        raise ServiceError("Mission pause request is invalid")
    try:
        result = store.pause_mission_with_governance_preview(
            mission_id=mission_id,
            preview_id=preview_id,
            facts=facts,
            generation=generation,
            now=now,
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ServiceError("Mission pause confirmation failed") from None
    return result


def apply_permission_decision_request(
    store: object,
    params: object,
    *,
    generation: int,
    now: datetime,
) -> dict[str, object]:
    """Production RPC domain handler for one exact human permission decision."""
    if type(params) is not dict or set(params) not in (
        {"permission_id", "decision"},
        {"permission_id", "decision", "preview_id"},
    ):
        raise ServiceError("permission decision request is invalid")
    permission_id = params.get("permission_id")
    decision = params.get("decision")
    if (
        type(permission_id) is not str
        or re.fullmatch(r"prm_[a-z0-9]+", permission_id) is None
        or decision not in {"approved", "denied"}
        or not callable(getattr(store, "validated_protocol_state", None))
    ):
        raise ServiceError("permission decision request is invalid")
    try:
        state = store.validated_protocol_state()
        permissions = [
            item for item in state.get("permission_requests", [])
            if type(item) is dict and item.get("permission_id") == permission_id
        ]
        if len(permissions) != 1:
            raise ValueError
        permission = permissions[0]
        current = store._derived_protocol_state(
            state, "permission", permission_id, permission
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ServiceError("permission decision target is invalid") from None
    if current != "pending":
        raise ServiceError("permission decision target is terminal")
    facts = {
        "permission_id": permission_id,
        "session_id": permission.get("session_id"),
        "turn_id": permission.get("turn_id"),
        "tool_name": permission.get("tool_name"),
        "target": permission.get("target"),
        "risk": permission.get("risk"),
        "state": current,
        "decision": decision,
    }
    preview_id = params.get("preview_id")
    if preview_id is None:
        try:
            return store.record_governance_preview(
                "permission_decision",
                facts=facts,
                generation=generation,
                now=now,
            )
        except (AttributeError, TypeError, ValueError):
            raise ServiceError("permission decision preview failed") from None
    if type(preview_id) is not str:
        raise ServiceError("permission decision request is invalid")
    try:
        return store.decide_permission_with_governance_preview(
            permission_id=permission_id,
            decision=decision,
            preview_id=preview_id,
            facts=facts,
            generation=generation,
            now=now,
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ServiceError("permission decision confirmation failed") from None


def _force_stop_facts(store: object) -> dict[str, object]:
    if not callable(getattr(store, "load", None)):
        raise ServiceError("force-stop store is invalid")
    state = store.load()
    missions = state.get("missions", [])
    attempts = state.get("mission_attempts", [])
    if type(missions) is not list or type(attempts) is not list:
        raise ServiceError("force-stop state is invalid")
    return {
        "missions": [
            {
                "mission_id": item.get("mission_id"),
                "status": item.get("status"),
                "current_step": item.get("current_step"),
                "snapshot_hash": item.get("snapshot_hash"),
            }
            for item in missions
            if type(item) is dict
            and item.get("status") not in {"completed", "stopped", "interrupted"}
        ],
        "attempts": [
            {
                "attempt_id": item.get("attempt_id"),
                "mission_id": item.get("mission_id"),
                "state": item.get("state"),
                "dispatch_key": item.get("dispatch_key"),
                "admission_claim_id": item.get("admission_claim_id"),
                "receipt_summary": item.get("receipt_summary"),
            }
            for item in attempts
            if type(item) is dict
            and item.get("state")
            not in {"succeeded", "completed", "failed", "cancelled", "interrupted", "ambiguous"}
        ],
    }


def apply_force_stop_request(
    store: object,
    params: object,
    *,
    generation: int,
    now: datetime,
) -> dict[str, object]:
    """Production force-stop domain handler with exact preview confirmation."""
    if type(params) is not dict or set(params) not in (set(), {"preview_id"}):
        raise ServiceError("force-stop request is invalid")
    facts = _force_stop_facts(store)
    preview_id = params.get("preview_id")
    if preview_id is None:
        try:
            return store.record_governance_preview(
                "force_daemon_stop",
                facts=facts,
                generation=generation,
                now=now,
            )
        except (AttributeError, TypeError, ValueError):
            raise ServiceError("force-stop preview failed") from None
    if type(preview_id) is not str:
        raise ServiceError("force-stop request is invalid")
    try:
        return store.force_stop_with_governance_preview(
            preview_id=preview_id,
            facts=facts,
            generation=generation,
            now=now,
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ServiceError("force-stop confirmation failed") from None


def apply_mission_state_request(
    store: object,
    *,
    action: str,
    params: object,
    generation: int,
    now: datetime,
) -> dict[str, object]:
    """Production exact preview handler for Mission resume/cancel."""
    if action not in {"mission_resume", "mission_cancel"}:
        raise ServiceError("Mission governance action is invalid")
    if type(params) is not dict or set(params) not in (
        {"mission_id"},
        {"mission_id", "preview_id"},
    ):
        raise ServiceError("Mission governance request is invalid")
    mission_id = params.get("mission_id")
    if type(mission_id) is not str or re.fullmatch(r"mis_[0-9a-f]{12}", mission_id) is None:
        raise ServiceError("Mission governance request is invalid")
    facts = _mission_pause_facts(store, mission_id)
    expected = "stopped" if action == "mission_resume" else "running"
    if facts["status"] != expected or facts["active_attempts"]:
        raise ServiceError("Mission governance requires an idle boundary")
    preview_id = params.get("preview_id")
    if preview_id is None:
        try:
            return store.record_governance_preview(
                action,
                facts=facts,
                generation=generation,
                now=now,
            )
        except (AttributeError, TypeError, ValueError):
            raise ServiceError("Mission governance preview failed") from None
    if type(preview_id) is not str:
        raise ServiceError("Mission governance request is invalid")
    try:
        return store.transition_mission_with_governance_preview(
            action=action,
            mission_id=mission_id,
            preview_id=preview_id,
            facts=facts,
            generation=generation,
            now=now,
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ServiceError("Mission governance confirmation failed") from None


def _worker_ownership_state(state: Mapping[str, object], agent_id: str) -> str:
    from ..conversation.lifecycle import validate_conversation_history

    collections = {
        key: state.get(key, [])
        for key in (
            "conversation_sessions",
            "conversation_turns",
            "conversation_preview_bindings",
        )
    }
    projection = validate_conversation_history(
        collections, state.get("conversation_state_transitions", [])
    )
    return str(projection["ownership_states"].get(agent_id, "agentdeck_owned"))


def _bounded_worktree_snapshot(root: Path) -> dict[str, object]:
    """Hash the human-editable tree without reading daemon-owned state."""
    manifest: dict[str, str] = {}
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] in {".agentdeck", ".git"}:
            continue
        if path.is_symlink():
            resolved = path.resolve(strict=False)
            try:
                resolved.relative_to(root.resolve())
            except ValueError:
                raise ServiceError("Worker reconciliation found an escaping symlink") from None
            manifest[str(relative)] = f"symlink:{path.readlink()}"
            continue
        if path.is_dir():
            continue
        if not path.is_file() or len(manifest) >= 4096:
            raise ServiceError("Worker reconciliation worktree is unsupported")
        data = path.read_bytes()
        total_bytes += len(data)
        if total_bytes > 32 * 1024 * 1024:
            raise ServiceError("Worker reconciliation worktree exceeds bounded limits")
        manifest[str(relative)] = hashlib.sha256(data).hexdigest()
    digest = hashlib.sha256(
        repr(sorted(manifest.items())).encode("utf-8")
    ).hexdigest()
    return {"digest": digest, "manifest": manifest}


def _worker_reconciliation_snapshot(
    store: object, state: Mapping[str, object], agent_id: str
) -> dict[str, object]:
    from ..config import load_config
    from ..runtime.tmux import TmuxBackend

    root = Path(store.root).resolve()
    try:
        config = load_config(root)
        configured = [item for item in config.agents if item.agent_id == agent_id]
    except (OSError, TypeError, ValueError):
        raise ServiceError("Worker reconciliation runtime evidence is unverifiable") from None
    if len(configured) != 1:
        raise ServiceError("Worker reconciliation runtime evidence is missing")
    agent = configured[0]
    protocol = store.validated_protocol_state()
    current_states = store._validate_protocol_transition_history(protocol)
    sessions = sorted(
        (
            {
                "session_id": item.get("session_id"),
                "provider": item.get("provider"),
                "transport": item.get("transport"),
                "workspace": item.get("workspace"),
                "state": current_states.get(
                    ("session", item.get("session_id")), item.get("state")
                ),
            }
            for item in protocol.get("agent_sessions", [])
            if type(item) is dict and item.get("agent_id") == agent_id
        ),
        key=lambda item: str(item["session_id"]),
    )
    turns = sorted(
        (
            {
                "turn_id": item.get("turn_id"),
                "session_id": item.get("session_id"),
                "message_id": item.get("message_id"),
                "kind": item.get("kind"),
                "state": current_states.get(
                    ("turn", item.get("turn_id")), item.get("state")
                ),
            }
            for item in protocol.get("protocol_turns", [])
            if type(item) is dict
            and any(item.get("session_id") == session["session_id"] for session in sessions)
        ),
        key=lambda item: str(item["turn_id"]),
    )
    runtime_evidence: dict[str, object]
    if agent.transport == "acp":
        active_sessions = [item for item in sessions if item["state"] == "ready"]
        if len(active_sessions) != 1 or any(
            item["state"] not in {"ready", "disconnected", "stopped", "failed"}
            for item in sessions
        ):
            raise ServiceError("Worker reconciliation ACP session evidence is missing")
        raw_session = next(
            item for item in protocol.get("agent_sessions", [])
            if type(item) is dict
            and item.get("session_id") == active_sessions[0]["session_id"]
        )
        caps = raw_session.get("capabilities")
        if (
            active_sessions[0]["provider"] != agent.provider
            or active_sessions[0]["transport"] != "acp-adapter"
            or Path(str(active_sessions[0]["workspace"])).resolve(strict=False) != root
            or type(raw_session.get("native_session_id")) is not str
            or type(caps) is not dict
            or any(
                caps.get(name) is not True
                for name in (
                    "structured_sessions", "streaming_updates",
                    "structured_tools", "permission_requests",
                )
            )
            or caps.get("observable_terminal") is not False
            or any(
                item["state"]
                not in {"completed", "blocked", "failed", "cancelled", "ambiguous"}
                for item in turns
            )
        ):
            raise ServiceError("Worker reconciliation ACP runtime evidence mismatches")
        runtime_evidence = {
            "transport": "acp",
            "session_id": active_sessions[0]["session_id"],
            "native_session_id": raw_session["native_session_id"],
            "state": active_sessions[0]["state"],
        }
    elif agent.transport == "tmux":
        binding = state.get("agents", {}).get(agent_id) if type(state.get("agents")) is dict else None
        if (
            type(binding) is not dict
            or binding.get("agent_id") != agent_id
            or binding.get("status") != "running"
            or type(binding.get("pane_id")) is not str
            or not binding["pane_id"]
            or binding.get("session_name") != config.runtime.session_name
            or type(binding.get("cwd")) is not str
            or Path(binding["cwd"]).resolve(strict=False) != root
        ):
            raise ServiceError("Worker reconciliation tmux runtime evidence is missing")
        try:
            pane_exists = TmuxBackend().pane_exists(
                config.runtime, str(binding["pane_id"])
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            pane_exists = False
        if not pane_exists:
            raise ServiceError("Worker reconciliation tmux runtime evidence is unverifiable")
        runtime_evidence = {
            "transport": "tmux",
            "pane_id": binding["pane_id"],
            "session_name": binding["session_name"],
            "cwd": str(root),
        }
    else:
        raise ServiceError("Worker reconciliation configured transport is invalid")
    artifacts = sorted(
        (
            {
                "attempt_id": handoff.get("attempt_id"),
                "artifacts": handoff.get("canonical_handoff", {}).get("artifacts", []),
            }
            for handoff in state.get("mission_handoffs", [])
            if type(handoff) is dict
            and any(
                type(attempt) is dict
                and attempt.get("attempt_id") == handoff.get("attempt_id")
                and attempt.get("agent_id") == agent_id
                for attempt in state.get("mission_attempts", [])
            )
        ),
        key=lambda item: str(item["attempt_id"]),
    )
    worktree = _bounded_worktree_snapshot(root)
    return {
        "runtime_transport": agent.transport,
        "runtime_evidence_digest": hashlib.sha256(
            repr(sorted(runtime_evidence.items())).encode()
        ).hexdigest(),
        "session_digest": hashlib.sha256(repr((sessions, turns)).encode()).hexdigest(),
        "artifact_digest": hashlib.sha256(repr(artifacts).encode()).hexdigest(),
        "worktree_digest": worktree["digest"],
        "worktree_manifest": worktree["manifest"],
    }


def _worker_ownership_facts(
    store: object,
    agent_id: str,
    *,
    reported_changes: Mapping[str, object] | None = None,
) -> dict[str, object]:
    if not callable(getattr(store, "load", None)):
        raise ServiceError("Worker ownership store is invalid")
    state = store.load()
    try:
        collections = {
            key: state.get(key, [])
            for key in (
                "conversation_sessions",
                "conversation_turns",
                "conversation_preview_bindings",
            )
        }
        ownership = _worker_ownership_state(state, agent_id)
        sessions = collections["conversation_sessions"]
        if not sessions:
            raise ValueError
        conversation_id = sessions[-1]["conversation_id"]
        protocol = store.validated_protocol_state()
        current_states = store._validate_protocol_transition_history(protocol)
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ServiceError("Worker ownership facts are invalid") from None
    session_ids = {
        item.get("session_id")
        for item in protocol.get("agent_sessions", [])
        if type(item) is dict and item.get("agent_id") == agent_id
    }
    active_turn_ids = sorted(
        str(item["turn_id"])
        for item in protocol.get("protocol_turns", [])
        if type(item) is dict
        and item.get("session_id") in session_ids
        and current_states.get(("turn", item.get("turn_id")), item.get("state"))
        not in {"completed", "blocked", "failed", "cancelled", "ambiguous"}
    )
    pending_permission_ids = sorted(
        str(item["permission_id"])
        for item in protocol.get("permission_requests", [])
        if type(item) is dict
        and item.get("session_id") in session_ids
        and current_states.get(
            ("permission", item.get("permission_id")), item.get("status")
        )
        == "pending"
    )
    active_attempt_ids = sorted(
        str(item["attempt_id"])
        for item in state.get("mission_attempts", [])
        if type(item) is dict
        and item.get("agent_id") == agent_id
        and item.get("state") in {"prepared", "admitting", "submitted", "running"}
    )
    safe = not (active_turn_ids or pending_permission_ids or active_attempt_ids)
    reconciliation = _worker_reconciliation_snapshot(store, state, agent_id)
    baselines = [
        item for item in state.get("worker_takeover_baselines", [])
        if type(item) is dict and item.get("agent_id") == agent_id
        and item.get("state") == "active"
    ]
    if len(baselines) > 1:
        raise ServiceError("Worker reconciliation baseline is invalid")
    reconciled = False
    changed_paths: list[str] = []
    baseline_id = None
    if ownership == "agentdeck_owned":
        reconciled = safe
    elif baselines:
        baseline = baselines[0]
        baseline_id = baseline.get("baseline_id")
        old_manifest = baseline.get("worktree_manifest")
        current_manifest = reconciliation["worktree_manifest"]
        if type(old_manifest) is not dict or type(current_manifest) is not dict:
            raise ServiceError("Worker reconciliation baseline is invalid")
        changed_paths = sorted(
            key for key in set(old_manifest) | set(current_manifest)
            if old_manifest.get(key) != current_manifest.get(key)
        )
        report_paths = (
            reported_changes.get("paths") if reported_changes is not None else None
        )
        report_summary = (
            reported_changes.get("summary") if reported_changes is not None else None
        )
        report_valid = (
            type(report_paths) is list
            and len(report_paths) <= 256
            and all(type(item) is str and item for item in report_paths)
            and len(report_paths) == len(set(report_paths))
            and sorted(set(report_paths)) == changed_paths
            and type(report_summary) is str
            and len(report_summary.encode("utf-8")) <= 4096
            and (not changed_paths or bool(report_summary.strip()))
        )
        reconciled = bool(
            safe
            and report_valid
            and reconciliation["runtime_transport"] == baseline.get("runtime_transport")
            and reconciliation["runtime_evidence_digest"]
            == baseline.get("runtime_evidence_digest")
            and reconciliation["session_digest"] == baseline.get("session_digest")
            and reconciliation["artifact_digest"] == baseline.get("artifact_digest")
        )
    return {
        "agent_id": agent_id,
        "conversation_id": conversation_id,
        "ownership": ownership,
        "safe_boundary": safe,
        "reconciled": reconciled,
        "active_turn_ids": active_turn_ids,
        "pending_permission_ids": pending_permission_ids,
        "active_attempt_ids": active_attempt_ids,
        "baseline_id": baseline_id,
        "reconciliation": reconciliation,
        "changed_paths": changed_paths,
        "reported_changes": None if reported_changes is None else dict(reported_changes),
    }


def apply_worker_ownership_request(
    store: object,
    *,
    action: str,
    params: object,
    generation: int,
    now: datetime,
) -> dict[str, object]:
    from .governance import governance_transition_gate

    if action not in {"takeover", "return_control"}:
        raise ServiceError("Worker ownership action is invalid")
    allowed = {"agent_id", "preview_id", "reported_changes"}
    if type(params) is not dict or not set(params).issubset(allowed) or "agent_id" not in params:
        raise ServiceError("Worker ownership request is invalid")
    agent_id = params.get("agent_id")
    if type(agent_id) is not str or not agent_id:
        raise ServiceError("Worker ownership request is invalid")
    reported_changes = params.get("reported_changes")
    if action == "takeover" and reported_changes is not None:
        raise ServiceError("Worker ownership request is invalid")
    if reported_changes is not None and type(reported_changes) is not dict:
        raise ServiceError("Worker ownership request is invalid")
    def persist_ambiguity(blocker: str, evidence: dict[str, object]) -> None:
        try:
            store.record_worker_reconciliation_ambiguity(
                agent_id=agent_id,
                generation=generation,
                blocker=blocker,
                evidence=evidence,
                now=now,
            )
        except (AttributeError, KeyError, OSError, TypeError, ValueError):
            raise ServiceError(
                "Worker return reconciliation ambiguity could not be persisted"
            ) from None

    try:
        facts = _worker_ownership_facts(
            store,
            agent_id,
            reported_changes=reported_changes,  # type: ignore[arg-type]
        )
    except ServiceError as exc:
        if action == "return_control":
            persist_ambiguity(
                str(exc),
                {
                    "stage": "runtime_projection",
                    "reported_changes_hash": hashlib.sha256(
                        repr(reported_changes).encode()
                    ).hexdigest(),
                },
            )
            raise ServiceError("Worker return reconciliation is ambiguous") from None
        raise
    gate = governance_transition_gate(action, facts)
    if not gate.allowed:
        if action == "return_control" and facts.get("ownership") == "human_owned":
            persist_ambiguity(
                gate.blocker or "Worker reconciliation evidence mismatches",
                {
                    "stage": "reconciliation_gate",
                    "baseline_id": facts.get("baseline_id"),
                    "runtime_evidence_digest": facts.get("reconciliation", {}).get(
                        "runtime_evidence_digest"
                    ) if type(facts.get("reconciliation")) is dict else None,
                    "session_digest": facts.get("reconciliation", {}).get(
                        "session_digest"
                    ) if type(facts.get("reconciliation")) is dict else None,
                    "artifact_digest": facts.get("reconciliation", {}).get(
                        "artifact_digest"
                    ) if type(facts.get("reconciliation")) is dict else None,
                    "worktree_digest": facts.get("reconciliation", {}).get(
                        "worktree_digest"
                    ) if type(facts.get("reconciliation")) is dict else None,
                },
            )
        raise ServiceError(gate.blocker or "Worker ownership change is blocked")
    preview_id = params.get("preview_id")
    if preview_id is None:
        try:
            return store.record_governance_preview(
                action, facts=facts, generation=generation, now=now
            )
        except (AttributeError, TypeError, ValueError):
            raise ServiceError("Worker ownership preview failed") from None
    if type(preview_id) is not str:
        raise ServiceError("Worker ownership request is invalid")
    try:
        return store.change_worker_ownership_with_governance_preview(
            action=action,
            agent_id=agent_id,
            preview_id=preview_id,
            facts=facts,
            generation=generation,
            now=now,
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        if action == "return_control":
            persist_ambiguity(
                "Worker return confirmation evidence drifted",
                {
                    "stage": "confirmation",
                    "preview_id": preview_id,
                    "baseline_id": facts.get("baseline_id"),
                },
            )
        raise ServiceError("Worker ownership confirmation failed") from None


def apply_transport_reroute_request(
    store: object,
    params: object,
    *,
    generation: int,
    now: datetime,
) -> dict[str, object]:
    from .governance import governance_transition_gate

    base = {"mission_id", "step_id", "agent_id", "to_transport"}
    if type(params) is not dict or set(params) not in (base, base | {"preview_id"}):
        raise ServiceError("transport reroute request is invalid")
    mission_id = params.get("mission_id")
    step_id = params.get("step_id")
    agent_id = params.get("agent_id")
    to_transport = params.get("to_transport")
    if (
        type(mission_id) is not str
        or type(step_id) is not str
        or type(agent_id) is not str
        or not agent_id
        or to_transport not in {"acp", "tmux"}
    ):
        raise ServiceError("transport reroute request is invalid")
    state = store.load()
    missions = [
        item for item in state.get("missions", [])
        if type(item) is dict and item.get("mission_id") == mission_id
    ]
    if len(missions) != 1:
        raise ServiceError("transport reroute Mission is invalid")
    mission = missions[0]
    snapshot = mission.get("execution_snapshot")
    mission_snapshot = snapshot.get("mission") if type(snapshot) is dict else None
    steps = mission_snapshot.get("steps") if type(mission_snapshot) is dict else None
    workers = snapshot.get("workers") if type(snapshot) is dict else None
    if type(steps) is not list or type(workers) is not list:
        raise ServiceError("transport reroute snapshot is invalid")
    step_matches = [
        item for item in steps
        if type(item) is dict
        and item.get("step_id") == step_id
        and item.get("agent_id") == agent_id
    ]
    worker_matches = [
        item for item in workers
        if type(item) is dict and item.get("agent_id") == agent_id
    ]
    attempts = [
        item for item in state.get("mission_attempts", [])
        if type(item) is dict
        and item.get("mission_id") == mission_id
        and item.get("step_id") == step_id
    ]
    if len(step_matches) != 1 or len(worker_matches) != 1 or attempts:
        raise ServiceError("transport reroute cannot modify a frozen attempt")
    from_transport = worker_matches[0].get("configured_transport")
    try:
        ownership = _worker_ownership_state(state, agent_id)
    except (KeyError, TypeError, ValueError):
        raise ServiceError("transport reroute ownership is invalid") from None
    facts = {
        "mission_id": mission_id,
        "step_id": step_id,
        "agent_id": agent_id,
        "snapshot_hash": mission.get("snapshot_hash"),
        "mission_status": mission.get("status"),
        "current_step": mission.get("current_step"),
        "ownership": ownership,
        "attempt_state": "none",
        "from_transport": from_transport,
        "to_transport": to_transport,
    }
    gate = governance_transition_gate("reroute", facts)
    if not gate.allowed:
        raise ServiceError(gate.blocker or "transport reroute is blocked")
    preview_id = params.get("preview_id")
    if preview_id is None:
        try:
            return store.record_governance_preview(
                "reroute", facts=facts, generation=generation, now=now
            )
        except (AttributeError, TypeError, ValueError):
            raise ServiceError("transport reroute preview failed") from None
    if type(preview_id) is not str:
        raise ServiceError("transport reroute request is invalid")
    try:
        return store.record_transport_reroute_with_governance_preview(
            mission_id=mission_id,
            step_id=step_id,
            agent_id=agent_id,
            to_transport=to_transport,
            preview_id=preview_id,
            facts=facts,
            generation=generation,
            now=now,
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ServiceError("transport reroute confirmation failed") from None


def scheduler_facts_from_store(store: object) -> SchedulerFacts | None:
    """Project one admitted Mission from one durable StateStore snapshot."""
    if not callable(getattr(store, "load", None)):
        raise ServiceError("scheduler store is invalid")
    state = store.load()
    missions = [
        item for item in state.get("missions", [])
        if isinstance(item, dict)
        and item.get("status") not in {"completed", "stopped", "interrupted"}
        and isinstance(item.get("daemon_admission"), dict)
        and item["daemon_admission"].get("state") == "admitted"
    ]
    if not missions:
        return None
    if len(missions) != 1:
        raise ServiceError("multiple admitted Missions are unsupported")
    mission = missions[0]
    snapshot = mission.get("execution_snapshot")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("mission"), dict):
        raise ServiceError("scheduler snapshot is invalid")
    steps = snapshot["mission"].get("steps")
    if not isinstance(steps, list):
        raise ServiceError("scheduler steps are invalid")
    current_step = mission.get("current_step", 0)
    if type(current_step) is not int or current_step < 0 or current_step > len(steps):
        raise ServiceError("scheduler step cursor is invalid")
    step = None if current_step == len(steps) else steps[current_step]
    if step is not None and not isinstance(step, dict):
        raise ServiceError("scheduler step is invalid")
    attempts = [
        item for item in state.get("mission_attempts", [])
        if isinstance(item, dict) and item.get("mission_id") == mission["mission_id"]
    ]
    active = [
        item for item in attempts
        if item.get("state") in {"prepared", "admitting", "submitted", "running"}
    ]
    if len(active) > 1:
        raise ServiceError("multiple active Mission attempts")
    bindings = [
        item for item in state.get("mission_recovery_evidence", [])
        if isinstance(item, dict) and item.get("mission_id") == mission["mission_id"]
    ]
    if len(bindings) != 1:
        raise ServiceError("scheduler recovery binding is invalid")
    bound_attempt_id = bindings[0].get("attempt_id")
    current_matches = [
        item for item in attempts if item.get("attempt_id") == bound_attempt_id
    ]
    current = None if bound_attempt_id is None else (
        current_matches[0] if len(current_matches) == 1 else None
    )
    if bound_attempt_id is not None and current is None:
        raise ServiceError("scheduler attempt binding is invalid")
    if current is not None and (
        step is None or current.get("step_id") != step.get("step_id")
    ):
        current = None
    recovery_blocker = None
    recovery_matches = [
        item for item in state.get("recovery_decisions", [])
        if isinstance(item, dict)
        and item.get("mission_id") == mission["mission_id"]
        and item.get("attempt_id") == (
            None if current is None else current.get("attempt_id")
        )
    ]
    if len(recovery_matches) > 1:
        raise ServiceError("scheduler recovery decision is invalid")
    if (
        recovery_matches
        and recovery_matches[0].get("classification") == "ambiguous"
        and recovery_matches[0].get("reason")
        == "ACP Worker connection was lost across daemon restart"
    ):
        reason = recovery_matches[0].get("reason")
        if type(reason) is not str or not reason:
            raise ServiceError("scheduler recovery decision is invalid")
        recovery_blocker = reason
    reconciliation_blocker = None
    if step is not None:
        reconciliation_matches = [
            item for item in state.get("worker_reconciliation_decisions", [])
            if isinstance(item, dict)
            and item.get("agent_id") == step.get("agent_id")
            and item.get("status") == "ambiguous"
        ]
        if reconciliation_matches:
            latest = sorted(
                reconciliation_matches,
                key=lambda item: (str(item.get("created_at")), str(item.get("decision_id"))),
            )[-1]
            blocker = latest.get("blocker")
            if type(blocker) is not str or not blocker:
                raise ServiceError("scheduler reconciliation decision is invalid")
            reconciliation_blocker = blocker
    replies = [
        item for item in state.get("mission_worker_replies", [])
        if isinstance(item, dict) and current is not None
        and item.get("attempt_id") == current.get("attempt_id")
    ]
    handoffs = [
        item for item in state.get("mission_handoffs", [])
        if isinstance(item, dict) and current is not None
        and item.get("attempt_id") == current.get("attempt_id")
    ]
    all_completed = current_step == len(steps)
    attempt_state = "none" if current is None else current.get("state")
    reply_state = "none" if not replies else replies[-1].get("state")
    handoff_state = "none" if not handoffs else handoffs[-1].get("state")
    worker_ready = True
    ownership_state = "owned"
    if step is not None:
        try:
            ownership = _worker_ownership_state(state, str(step.get("agent_id")))
            ownership_state = (
                "owned" if ownership == "agentdeck_owned" else "conflict"
            )
        except (KeyError, TypeError, ValueError):
            ownership_state = "conflict"
            worker_ready = False
        try:
            from ..config import load_config
            from .governance import effective_transport_for_step

            config = load_config(Path(store.root))
            agent = next(item for item in config.agents if item.agent_id == step.get("agent_id"))
            worker = next(
                item for item in snapshot.get("workers", [])
                if isinstance(item, dict) and item.get("agent_id") == agent.agent_id
            )
            effective_transport = effective_transport_for_step(
                state,
                mission_id=mission["mission_id"],
                step_id=str(step.get("step_id")),
                agent_id=agent.agent_id,
                frozen_transport=worker["configured_transport"],
            )
            if effective_transport == "acp":
                command = agent.transport_command[0] if agent.transport_command else ""
                worker_ready = bool(
                    command
                    and (
                        Path(command).expanduser().is_file()
                        if "/" in command
                        else shutil.which(command) is not None
                    )
                )
            else:
                project_view = store.project_view(config)
                runtime = next(
                    (
                        item.get("runtime", {})
                        for item in project_view.get("agents", [])
                        if isinstance(item, dict)
                        and item.get("agent_id") == agent.agent_id
                    ),
                    {},
                )
                worker_ready = bool(
                    isinstance(runtime, dict)
                    and runtime.get("status") == "running"
                    and isinstance(runtime.get("pane_id"), str)
                )
        except (AttributeError, KeyError, OSError, StopIteration, TypeError, ValueError):
            worker_ready = False
    return SchedulerFacts(
        mission_id=mission["mission_id"],
        mission_state=mission["status"],
        step_id=None if step is None else step.get("step_id"),
        step_state="none" if step is None else ("pending" if current is None else "active"),
        attempt_id=None if current is None else current.get("attempt_id"),
        attempt_state=attempt_state,
        reply_state=reply_state,
        handoff_state=handoff_state,
        permission_state=permission_state_for_attempt(state, current),
        worker_ready=worker_ready,
        next_step_eligible=(
            handoff_state == "recorded" and current_step < len(steps)
        ),
        all_steps_completed=all_completed,
        snapshot_state=(
            "valid" if mission.get("snapshot_hash") == snapshot.get("execution_hash")
            else "drift"
        ),
        lineage_state="valid",
        ownership_state=ownership_state,
        active_attempt_count=len(active),
        blocker=(
            reconciliation_blocker
            or recovery_blocker
            or mission["daemon_admission"].get("blocker")
        ),
    )


Callback = Callable[[], object | Awaitable[object]]
TransitionCallback = Callable[[SchedulerDecision], object | Awaitable[object]]


class WorkerTransport(Protocol):
    async def admit(self, attempt: dict[str, object]) -> SubmittedReceipt: ...
    async def complete(
        self, attempt: dict[str, object], receipt: SubmittedReceipt
    ) -> TransportResult: ...


class DaemonWorkerCoordinator:
    """Split Worker execution at the durable submitted-receipt fence."""

    def __init__(
        self,
        *,
        service: "ProjectDaemonService",
        store: object,
        transport_for: Callable[[dict[str, object]], WorkerTransport],
        refresh_recovery: Callable[[], object] = lambda: None,
    ) -> None:
        self.service = service
        self.store = store
        self.transport_for = transport_for
        self.refresh_recovery = refresh_recovery

    def launch(self, attempt: dict[str, object]) -> None:
        if attempt.get("state") != "prepared":
            raise ServiceError("Worker attempt must be prepared")
        # Construction is state-free and performs no external I/O.  Complete
        # all prompt/config authority checks before acquiring an admission
        # claim so a local configuration error cannot strand `admitting`.
        transport = self.transport_for(dict(attempt))
        claimed = self.store.claim_mission_attempt_admission(
            attempt_id=attempt["attempt_id"], dispatch_key=attempt["dispatch_key"]
        )
        self.refresh_recovery()

        if claimed["configured_transport"] == "acp":
            async def execute_acp() -> tuple[bool, object]:
                try:
                    receipt = await transport.admit(dict(claimed))
                    result = await transport.complete(dict(claimed), receipt)
                    return True, (receipt, result)
                except Exception as exc:
                    return False, exc

            def acp_completed(outcome: tuple[bool, object]) -> None:
                ok, value = outcome
                if not ok or not isinstance(value, tuple) or len(value) != 2:
                    self.store.mark_mission_attempt_admission_ambiguous(
                        attempt_id=claimed["attempt_id"], dispatch_key=claimed["dispatch_key"],
                        expected_claim_id=claimed["admission_claim_id"], reason="admission_outcome_unknown",
                    )
                    return
                receipt, result = value
                if not isinstance(receipt, SubmittedReceipt) or receipt.dispatch_key != claimed["dispatch_key"]:
                    self.store.mark_mission_attempt_admission_ambiguous(
                        attempt_id=claimed["attempt_id"], dispatch_key=claimed["dispatch_key"],
                        expected_claim_id=claimed["admission_claim_id"], reason="admission_outcome_unknown",
                    )
                    return
                canonical = _canonical_transport_result(
                    result,
                    dispatch_key=str(claimed["dispatch_key"]),
                    transport="acp",
                )
                succeeded = canonical is not None and canonical["status"] == "completed"
                result_summary = (
                    str(result.reply.get("summary") or "Worker completed")
                    if isinstance(result, TransportResult)
                    else "Worker transport failed"
                )
                self.store.record_acp_mission_attempt_completion(
                    attempt_id=claimed["attempt_id"], dispatch_key=claimed["dispatch_key"],
                    expected_claim_id=claimed["admission_claim_id"],
                    receipt_summary=receipt.summary,
                    succeeded=succeeded,
                    summary=result_summary if canonical is not None else "Worker transport failed",
                    canonical_handoff=canonical,
                )
                self.refresh_recovery()

            self.service.start_worker_io(execute_acp(), on_completion=acp_completed)
            return

        async def admit() -> tuple[bool, object]:
            try:
                return True, await transport.admit(dict(claimed))
            except Exception as exc:
                return False, exc

        def admission_completed(outcome: tuple[bool, object]) -> None:
            ok, value = outcome
            if not ok or not isinstance(value, SubmittedReceipt):
                self.store.mark_mission_attempt_admission_ambiguous(
                    attempt_id=claimed["attempt_id"],
                    dispatch_key=claimed["dispatch_key"],
                    expected_claim_id=claimed["admission_claim_id"],
                    reason="admission_outcome_unknown",
                )
                return
            receipt = value
            if receipt.dispatch_key != claimed["dispatch_key"]:
                self.store.mark_mission_attempt_ambiguous(
                    attempt_id=claimed["attempt_id"],
                    dispatch_key=claimed["dispatch_key"],
                    expected_claim_id=claimed["admission_claim_id"],
                    observed_dispatch_key=receipt.dispatch_key,
                    receipt_summary=receipt.summary,
                    reason="receipt_persistence_unknown",
                )
                return
            try:
                submitted = self.store.record_mission_attempt_submitted(
                    attempt_id=claimed["attempt_id"],
                    dispatch_key=claimed["dispatch_key"],
                    expected_claim_id=claimed["admission_claim_id"],
                    receipt_summary=receipt.summary,
                )
            except Exception:
                self.store.mark_mission_attempt_ambiguous(
                    attempt_id=claimed["attempt_id"],
                    dispatch_key=claimed["dispatch_key"],
                    expected_claim_id=claimed["admission_claim_id"],
                    observed_dispatch_key=receipt.dispatch_key,
                    receipt_summary=receipt.summary,
                    reason="receipt_persistence_unknown",
                )
                return
            self.refresh_recovery()

            async def complete() -> tuple[bool, object]:
                try:
                    return True, await transport.complete(dict(submitted), receipt)
                except Exception as exc:
                    return False, exc

            self.service.start_worker_io(
                complete(), on_completion=lambda result: completion_completed(result)
            )

        def completion_completed(outcome: tuple[bool, object]) -> None:
            ok, value = outcome
            canonical = (
                _canonical_transport_result(
                    value,
                    dispatch_key=str(claimed["dispatch_key"]),
                    transport=str(claimed["configured_transport"]),
                )
                if ok
                else None
            )
            succeeded = canonical is not None and canonical["status"] == "completed"
            summary = (
                str(value.reply.get("summary") or "Worker completed")
                if canonical is not None and isinstance(value, TransportResult)
                else "Worker transport failed"
            )
            self.store.record_tmux_mission_attempt_completion(
                attempt_id=claimed["attempt_id"],
                dispatch_key=claimed["dispatch_key"],
                succeeded=succeeded,
                summary=summary,
                canonical_handoff=canonical,
            )
            self.refresh_recovery()

        self.service.start_worker_io(admit(), on_completion=admission_completed)


class DaemonTransitionEffects:
    def __init__(self, store: object, *, launch_attempt: Callable[[dict[str, object]], object], refresh_recovery: Callable[[], object] = lambda: None) -> None:
        self.store = store
        self.launch_attempt = launch_attempt
        self.refresh_recovery = refresh_recovery

    def _applied(self, value: object) -> object:
        self.refresh_recovery()
        return value

    def apply(self, decision: SchedulerDecision) -> object:
        if decision.kind in {"idle", "await_worker", "wait_human", "wait_ambiguity", "blocked"}:
            return None
        if decision.kind == "prepare_dispatch":
            from .governance import effective_transport_for_step

            mission = self.store.mission_by_id(decision.mission_id)
            snapshot = mission["execution_snapshot"]
            step = next(item for item in snapshot["mission"]["steps"] if item["step_id"] == decision.step_id)
            worker = next(item for item in snapshot["workers"] if item["agent_id"] == step["agent_id"])
            effective_transport = effective_transport_for_step(
                self.store.load(),
                mission_id=decision.mission_id,
                step_id=decision.step_id,
                agent_id=step["agent_id"],
                frozen_transport=worker["configured_transport"],
            )
            return self._applied(self.store.prepare_mission_attempt(
                mission_id=decision.mission_id,
                step_id=decision.step_id,
                agent_id=step["agent_id"],
                configured_transport=effective_transport,
            ))
        if decision.kind == "dispatch_prepared":
            return self.launch_attempt(self.store.mission_attempt_by_id(decision.attempt_id))
        if decision.kind == "validate_reply":
            replies = [item for item in self.store.load().get("mission_worker_replies", []) if item.get("attempt_id") == decision.attempt_id]
            reply = replies[-1]
            attempt = self.store.mission_attempt_by_id(decision.attempt_id)
            return self._applied(self.store.record_mission_reply_evidence(
                attempt_id=decision.attempt_id,
                dispatch_key=attempt["dispatch_key"],
                state="validated",
                expected_reply_id=reply["reply_id"],
            ))
        if decision.kind == "record_handoff":
            state = self.store.load()
            reply = next(item for item in state.get("mission_worker_replies", []) if item.get("attempt_id") == decision.attempt_id)
            handoffs = [item for item in state.get("mission_handoffs", []) if item.get("attempt_id") == decision.attempt_id]
            if not handoffs:
                return self._applied(self.store.record_mission_handoff_evidence(
                    attempt_id=decision.attempt_id, reply_id=reply["reply_id"], state="pending"
                ))
            return self._applied(self.store.record_mission_handoff_evidence(
                attempt_id=decision.attempt_id, reply_id=reply["reply_id"], state="recorded",
                expected_handoff_id=handoffs[-1]["handoff_id"],
            ))
        if decision.kind == "activate_next":
            handoff = next(item for item in self.store.load().get("mission_handoffs", []) if item.get("attempt_id") == decision.attempt_id)
            return self._applied(self.store.advance_mission_after_handoff(
                decision.mission_id, attempt_id=decision.attempt_id, handoff_id=handoff["handoff_id"]
            ))
        if decision.kind == "complete_mission":
            return self._applied(self.store.complete_admitted_mission(decision.mission_id))
        raise ServiceError("unsupported scheduler transition")


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def validate_confirmed_mission_admission(
    store: object, params: object
) -> dict[str, object]:
    """Revalidate client-submitted authority against the daemon's durable copy."""
    if type(params) is not dict or set(params) != {
        "mission_id",
        "snapshot_hash",
        "execution_snapshot",
    }:
        raise ServiceError("frozen Mission admission is invalid")
    mission_id = params["mission_id"]
    snapshot_hash = params["snapshot_hash"]
    snapshot = params["execution_snapshot"]
    if (
        type(mission_id) is not str
        or re.fullmatch(r"mis_[0-9a-f]{12}", mission_id) is None
        or type(snapshot_hash) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", snapshot_hash) is None
        or not isinstance(snapshot, Mapping)
        or not callable(getattr(store, "mission_by_id", None))
        or not callable(getattr(store, "admit_mission_execution", None))
    ):
        raise ServiceError("frozen Mission admission is invalid")
    try:
        persisted = store.mission_by_id(mission_id)
    except Exception:
        raise ServiceError("frozen Mission admission is invalid") from None
    submitted_snapshot = _thaw_json(snapshot)
    if (
        type(persisted) is not dict
        or persisted.get("status") != "preparing"
        or persisted.get("snapshot_hash") != snapshot_hash
        or persisted.get("execution_snapshot") != submitted_snapshot
        or submitted_snapshot.get("execution_hash") != snapshot_hash
        or not isinstance(submitted_snapshot.get("mission"), dict)
        or submitted_snapshot["mission"].get("mission_id") != mission_id
    ):
        raise ServiceError("frozen Mission admission drift")
    try:
        durable = store.admit_mission_execution(
            mission_id, snapshot_hash=snapshot_hash
        )
    except (KeyError, TypeError, ValueError):
        raise ServiceError("frozen Mission admission drift") from None
    if not isinstance(durable, dict) or durable.get("state") != "admitted":
        raise ServiceError("frozen Mission admission drift")
    return {
        "accepted": True,
        "mission_id": mission_id,
        "snapshot_hash": snapshot_hash,
        "state": "admitted",
    }


async def _call(callback: Callable[..., object], *args: object) -> object:
    value = callback(*args)
    if inspect.isawaitable(value):
        return await value
    return value


class ProjectDaemonService:
    """Compose startup recovery, RPC serving, and one bounded transition tick."""

    def __init__(
        self,
        *,
        server: ServiceServer,
        reconcile_all: Callback,
        flush_safe_outboxes: Callback,
        load_scheduler_facts: Callable[
            [], SchedulerFacts | None | Awaitable[SchedulerFacts | None]
        ],
        apply_transition: TransitionCallback,
    ) -> None:
        if not all(
            callable(value)
            for value in (
                reconcile_all,
                flush_safe_outboxes,
                load_scheduler_facts,
                apply_transition,
            )
        ):
            raise TypeError("daemon service callbacks must be callable")
        if not callable(getattr(server, "start", None)) or not callable(
            getattr(server, "close", None)
        ):
            raise TypeError("daemon service server is invalid")
        self.server = server
        self._reconcile_all = reconcile_all
        self._flush_safe_outboxes = flush_safe_outboxes
        self._load_scheduler_facts = load_scheduler_facts
        self._apply_transition = apply_transition
        self._queue: asyncio.Queue[
            tuple[Callback, asyncio.Future[object] | None]
        ] = asyncio.Queue()
        self._worker_tasks: set[asyncio.Task[None]] = set()
        self._permission_waiters: dict[
            str, tuple[dict[str, object], asyncio.Future[str]]
        ] = {}
        self._started = False
        self._closed = False
        self._shutdown = asyncio.Event()
        self._wakeup = asyncio.Event()
        self._scheduler_due = False

    @property
    def started(self) -> bool:
        return self._started and not self._closed

    async def start(self) -> None:
        if self._closed:
            raise ServiceError("daemon service is closed")
        if self._started:
            return
        try:
            await _call(self._reconcile_all)
        except Exception:
            raise ServiceError("startup reconciliation failed") from None
        try:
            await self.server.start()
        except Exception:
            raise ServiceError("daemon server startup failed") from None
        self._started = True

    def submit_mutation(self, callback: Callback) -> asyncio.Future[object]:
        if not self.started or not callable(callback):
            raise ServiceError("daemon service is not accepting mutations")
        future = asyncio.get_running_loop().create_future()
        self._queue.put_nowait((callback, future))
        self._wakeup.set()
        return future

    def submit_governed_mutation(
        self,
        *,
        revalidate: Callback,
        mutate: Callback,
    ) -> asyncio.Future[object]:
        """Queue one mutation whose authority is checked at execution time.

        Callers may perform an earlier request-validation pass for useful
        errors, but only this queue-owned check is authoritative.  This closes
        the lease/preview/ownership drift window between RPC admission and the
        durable write.
        """
        if not callable(revalidate) or not callable(mutate):
            raise ServiceError("governed mutation callbacks are invalid")

        async def apply() -> object:
            authorized = await _call(revalidate)
            if authorized is not True:
                raise ServiceError("governed mutation authority is stale")
            return await _call(mutate)

        return self.submit_mutation(apply)

    def start_worker_io(
        self,
        operation: Awaitable[Any],
        *,
        on_completion: Callable[[Any], object | Awaitable[object]],
    ) -> None:
        if not self.started or not inspect.isawaitable(operation) or not callable(
            on_completion
        ):
            if inspect.iscoroutine(operation):
                operation.close()
            raise ServiceError("Worker I/O submission is invalid")

        async def run() -> None:
            try:
                result = await operation
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                callback: Callback = lambda: (_ for _ in ()).throw(exc)
            else:
                callback = lambda: on_completion(result)
            self._queue.put_nowait((callback, None))
            self._wakeup.set()

        task = asyncio.create_task(run())
        self._worker_tasks.add(task)
        task.add_done_callback(self._worker_tasks.discard)

    @staticmethod
    def _permission_wait_authority(value: object) -> dict[str, object]:
        if type(value) is not dict or set(value) != {
            "permission_id", "attempt_id", "session_id", "generation"
        }:
            raise ServiceError("permission waiter authority is invalid")
        permission_id = value.get("permission_id")
        attempt_id = value.get("attempt_id")
        session_id = value.get("session_id")
        generation = value.get("generation")
        if (
            type(permission_id) is not str
            or re.fullmatch(r"prm_[a-z0-9]+", permission_id) is None
            or type(attempt_id) is not str
            or re.fullmatch(r"mat_[0-9a-f]{12}", attempt_id) is None
            or type(session_id) is not str
            or not session_id
            or type(generation) is not int
            or generation < 1
        ):
            raise ServiceError("permission waiter authority is invalid")
        return dict(value)

    async def wait_for_permission(
        self,
        authority: object,
        *,
        read_decision: Callable[[], str],
    ) -> str:
        """Wait without blocking the mutation queue for one exact decision."""
        exact = self._permission_wait_authority(authority)
        if not self.started or not callable(read_decision):
            raise ServiceError("permission waiter is unavailable")
        current = read_decision()
        if current in {"approved", "denied"}:
            return current
        if current != "pending":
            raise ServiceError("permission waiter state is invalid")
        permission_id = str(exact["permission_id"])
        if permission_id in self._permission_waiters:
            raise ServiceError("permission waiter is already pending")
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._permission_waiters[permission_id] = (exact, future)
        try:
            # Close the read/register race against a decision queued just before
            # this coroutine resumed after durable permission creation.
            current = read_decision()
            if current in {"approved", "denied"} and not future.done():
                future.set_result(current)
            elif current != "pending":
                raise ServiceError("permission waiter state is invalid")
            return await future
        finally:
            registered = self._permission_waiters.get(permission_id)
            if registered is not None and registered[1] is future:
                self._permission_waiters.pop(permission_id, None)

    def resolve_permission_waiter(self, authority: object, decision: str) -> None:
        exact = self._permission_wait_authority(authority)
        if decision not in {"approved", "denied"}:
            raise ServiceError("permission waiter decision is invalid")
        permission_id = str(exact["permission_id"])
        registered = self._permission_waiters.get(permission_id)
        if registered is None:
            raise ServiceError("permission waiter is not pending")
        expected, future = registered
        if expected != exact:
            raise ServiceError("permission waiter authority drift")
        if future.done():
            raise ServiceError("permission waiter is not pending")
        self._permission_waiters.pop(permission_id, None)
        future.set_result(decision)

    def notify_permission_decision(self, permission_id: str, decision: str) -> bool:
        """Wake a live exact waiter after its durable decision commits."""
        if (
            type(permission_id) is not str
            or re.fullmatch(r"prm_[a-z0-9]+", permission_id) is None
            or decision not in {"approved", "denied"}
        ):
            raise ServiceError("permission waiter notification is invalid")
        registered = self._permission_waiters.get(permission_id)
        if registered is None:
            return False
        authority, _future = registered
        self.resolve_permission_waiter(authority, decision)
        return True

    async def tick(self) -> SchedulerDecision | None:
        if not self.started:
            raise ServiceError("daemon service is not started")
        await _call(self._flush_safe_outboxes)
        self._wakeup.clear()

        async def schedule_once() -> SchedulerDecision | None:
            facts = await _call(self._load_scheduler_facts)
            if facts is None:
                return None
            if not isinstance(facts, SchedulerFacts):
                raise ServiceError("scheduler facts are invalid")
            decision = schedule_gate(facts)
            await _call(self._apply_transition, decision)
            return decision

        if self._scheduler_due:
            self._scheduler_due = False
            decision = await schedule_once()
            if decision is not None or self._queue.empty():
                if not self._queue.empty():
                    self._wakeup.set()
                return decision
        try:
            callback, future = self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return await schedule_once()
        if not self._queue.empty():
            self._wakeup.set()
        try:
            result = await _call(callback)
        except Exception as exc:
            if future is not None and not future.done():
                future.set_exception(exc)
            self._scheduler_due = True
            return None
        if future is not None and not future.done():
            future.set_result(result)
        self._scheduler_due = True
        return None

    def request_shutdown(self) -> None:
        self._shutdown.set()
        self._wakeup.set()

    async def wait_for_work(self, *, timeout_seconds: float) -> None:
        if (
            type(timeout_seconds) not in {int, float}
            or not math.isfinite(float(timeout_seconds))
            or timeout_seconds <= 0
        ):
            raise ServiceError("daemon service wait timeout is invalid")
        if self._shutdown.is_set() or self._wakeup.is_set():
            return
        try:
            await asyncio.wait_for(
                self._wakeup.wait(), timeout=float(timeout_seconds)
            )
        except asyncio.TimeoutError:
            pass

    async def run(self, *, poll_interval_seconds: float = 0.1) -> None:
        if (
            type(poll_interval_seconds) not in {int, float}
            or not math.isfinite(float(poll_interval_seconds))
            or poll_interval_seconds <= 0
        ):
            raise ServiceError("daemon service poll interval is invalid")
        await self.start()
        try:
            while not self._shutdown.is_set():
                await self.tick()
                await self.wait_for_work(timeout_seconds=float(poll_interval_seconds))
        finally:
            await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._wakeup.set()
        for _authority, future in tuple(self._permission_waiters.values()):
            if not future.done():
                future.set_exception(ServiceError("daemon service closed"))
        self._permission_waiters.clear()
        for task in tuple(self._worker_tasks):
            task.cancel()
        if self._worker_tasks:
            await asyncio.gather(*tuple(self._worker_tasks), return_exceptions=True)
        while not self._queue.empty():
            _callback, future = self._queue.get_nowait()
            if future is not None and not future.done():
                future.set_exception(ServiceError("daemon service closed"))
        if self._started:
            await self.server.close()
        self._started = False
