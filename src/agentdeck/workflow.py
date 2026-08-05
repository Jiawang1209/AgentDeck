from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .models import EventRecord, ProjectConfig, utc_now
from .mission_authority import (
    canonical_workflow_authorized_steps,
    canonical_workflow_plan_hash,
)
from .runtime.base import RuntimeBackend
from .state import StateStore


REPLY_FIELDS = (
    "handoff_token",
    "status",
    "summary",
    "verification",
    "risks",
    "next_steps",
)
REPLY_STATUSES = {"completed", "blocked", "failed"}
CANONICAL_HANDOFF_FIELDS = {
    "handoff_token",
    "status",
    "summary",
    "verification",
    "risks",
    "next_steps",
    "artifacts",
    "trace_ids",
}
MAX_CANONICAL_HANDOFF_BYTES = 32 * 1024
MAX_CANONICAL_HANDOFF_ITEMS = 64


@dataclass(frozen=True)
class CanonicalArtifact:
    path: str
    content_hash: str | None

    def __post_init__(self) -> None:
        if (
            type(self.path) is not str
            or not self.path
            or (
                self.content_hash is not None
                and (
                    type(self.content_hash) is not str
                    or re.fullmatch(r"sha256:[0-9a-f]{64}", self.content_hash) is None
                )
            )
        ):
            raise ValueError("invalid Worker artifact evidence")


@dataclass(frozen=True)
class CanonicalHandoff:
    handoff_token: str
    status: str
    summary: str
    verification: str
    risks: str
    next_steps: str
    artifacts: tuple[CanonicalArtifact, ...]
    trace_ids: tuple[str, ...]

    def compact(self) -> dict[str, Any]:
        return {
            "handoff_token": self.handoff_token,
            "status": self.status,
            "summary": self.summary,
            "verification": self.verification,
            "risks": self.risks,
            "next_steps": self.next_steps,
            "artifacts": [
                {"path": item.path, "content_hash": item.content_hash}
                for item in self.artifacts
            ],
            "trace_ids": list(self.trace_ids),
        }


def _validated_compact_reply(reply: dict[str, Any]) -> dict[str, str]:
    if type(reply) is not dict:
        raise ValueError("worker reply must be an object")
    status = reply.get("status")
    if status not in REPLY_STATUSES:
        raise ValueError(f"invalid workflow reply status: {status}")
    compact = {"status": status}
    for field in ("summary", "verification", "risks", "next_steps"):
        value = reply.get(field)
        if type(value) is not str or not value.strip():
            raise ValueError(f"worker reply {field} must be a non-empty string")
        compact[field] = value
    return compact


def validate_correlated_workflow_reply(
    reply: dict[str, Any], expected_handoff_token: str
) -> dict[str, str]:
    """Validate the canonical workflow fields and exact dispatch lineage token."""
    if type(expected_handoff_token) is not str or not expected_handoff_token:
        raise ValueError("workflow handoff token is invalid")
    if type(reply) is not dict or reply.get("handoff_token") != expected_handoff_token:
        raise ValueError("workflow handoff token does not match")
    return {
        "handoff_token": expected_handoff_token,
        **_validated_compact_reply(reply),
    }


def build_canonical_handoff(
    *,
    reply: dict[str, Any],
    artifacts: list[dict[str, Any]],
    trace_ids: list[str],
    expected_handoff_token: str,
    require_artifact_hashes: bool = True,
) -> CanonicalHandoff:
    """Build the sole validated compact handoff record used by every transport."""
    correlated = validate_correlated_workflow_reply(reply, expected_handoff_token)
    if type(require_artifact_hashes) is not bool:
        raise ValueError("artifact hash policy is invalid")
    if type(artifacts) is not list:
        raise ValueError("worker artifacts must be a list")
    compact_artifacts: list[CanonicalArtifact] = []
    for artifact in artifacts:
        if type(artifact) is not dict or set(artifact) != {"path", "content_hash"}:
            raise ValueError("worker artifact evidence is invalid")
        path = artifact.get("path")
        content_hash = artifact.get("content_hash")
        if (
            type(path) is not str
            or not path
            or (
                content_hash is None
                and require_artifact_hashes
            )
            or (
                content_hash is not None
                and (
                    type(content_hash) is not str
                    or re.fullmatch(r"sha256:[0-9a-f]{64}", content_hash) is None
                )
            )
        ):
            raise ValueError("worker artifact evidence is invalid")
        compact_artifacts.append(CanonicalArtifact(path, content_hash))
    if (
        type(trace_ids) is not list
        or any(type(item) is not str or not item for item in trace_ids)
        or len(trace_ids) != len(set(trace_ids))
    ):
        raise ValueError("worker trace ids are invalid")
    return CanonicalHandoff(
        handoff_token=expected_handoff_token,
        status=correlated["status"],
        summary=correlated["summary"],
        verification=correlated["verification"],
        risks=correlated["risks"],
        next_steps=correlated["next_steps"],
        artifacts=tuple(compact_artifacts),
        trace_ids=tuple(trace_ids),
    )


def validate_canonical_handoff(
    value: object, *, expected_handoff_token: str | None = None
) -> CanonicalHandoff:
    """Rebuild and bound one persisted compact handoff through the sole schema."""
    if type(value) is not dict or set(value) != CANONICAL_HANDOFF_FIELDS:
        raise ValueError("canonical Worker handoff is invalid")
    token = value.get("handoff_token")
    if type(token) is not str or not token:
        raise ValueError("canonical Worker handoff is invalid")
    if expected_handoff_token is not None and token != expected_handoff_token:
        raise ValueError("canonical Worker handoff lineage is invalid")
    artifacts = value.get("artifacts")
    trace_ids = value.get("trace_ids")
    if (
        type(artifacts) is not list
        or type(trace_ids) is not list
        or len(artifacts) > MAX_CANONICAL_HANDOFF_ITEMS
        or len(trace_ids) > MAX_CANONICAL_HANDOFF_ITEMS
    ):
        raise ValueError("canonical Worker handoff is invalid")
    rebuilt = build_canonical_handoff(
        reply={key: value[key] for key in REPLY_FIELDS},
        artifacts=artifacts,
        trace_ids=trace_ids,
        expected_handoff_token=token,
        require_artifact_hashes=True,
    )
    compact = rebuilt.compact()
    if compact != value:
        raise ValueError("canonical Worker handoff is not canonical")
    encoded = json.dumps(compact, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > MAX_CANONICAL_HANDOFF_BYTES:
        raise ValueError("canonical Worker handoff exceeds size limit")
    return rebuilt


def validate_compact_worker_outcome(
    *,
    reply: dict[str, Any],
    artifacts: list[dict[str, Any]],
    trace_ids: list[str],
    expected_handoff_token: str,
) -> dict[str, Any]:
    """Compatibility projection of the canonical compact handoff record."""
    return build_canonical_handoff(
        reply=reply,
        artifacts=artifacts,
        trace_ids=trace_ids,
        expected_handoff_token=expected_handoff_token,
        require_artifact_hashes=True,
    ).compact()


def authorized_steps(plan_record: dict[str, Any]) -> list[dict[str, Any]]:
    return canonical_workflow_authorized_steps(plan_record)


def workflow_plan_hash(plan_record: dict[str, Any]) -> str:
    return canonical_workflow_plan_hash(plan_record)


def _reply_blocks(output: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in output.splitlines():
        line = raw_line.strip()
        for tui_prefix in ("• ", "› ", "⏺ "):
            if line.startswith(tui_prefix):
                line = line.removeprefix(tui_prefix).lstrip()
                break
        if line.startswith("handoff_token:"):
            if current is not None:
                blocks.append(current)
            current = {"handoff_token": line.split(":", 1)[1].strip()}
            continue
        if current is None or ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key in {*REPLY_FIELDS, "full_output_path"}:
            current[key] = value.strip()
    if current is not None:
        blocks.append(current)
    return blocks


def parse_correlated_reply(output: str, token: str) -> dict[str, str] | None:
    matching = [
        item for item in _reply_blocks(output) if item.get("handoff_token") == token
    ]
    if not matching:
        return None
    reply = matching[-1]
    for field in REPLY_FIELDS:
        if not reply.get(field):
            return None
    validate_correlated_workflow_reply(reply, token)
    return reply


def build_compact_handoff(
    *,
    step: int,
    agent_id: str,
    reply: dict[str, str],
    reply_id: str,
    artifact_paths: list[str],
) -> dict[str, Any]:
    token = reply.get("handoff_token")
    canonical = build_canonical_handoff(
        reply=reply,
        expected_handoff_token=token if type(token) is str else "",
        artifacts=[
            {"path": path, "content_hash": None}
            for path in artifact_paths
        ],
        trace_ids=[reply_id],
        require_artifact_hashes=False,
    )
    return render_legacy_handoff(
        canonical,
        step=step,
        agent_id=agent_id,
    )


def render_legacy_handoff(
    canonical: CanonicalHandoff,
    *,
    step: int,
    agent_id: str,
) -> dict[str, Any]:
    """Render the historic workflow shape from one validated canonical record."""
    if not isinstance(canonical, CanonicalHandoff):
        raise TypeError("canonical handoff is required")
    if type(step) is not int or step < 1 or type(agent_id) is not str or not agent_id:
        raise ValueError("legacy workflow handoff identity is invalid")
    compact = canonical.compact()
    return {
        "step": step,
        "agent_id": agent_id,
        **{
            field: compact[field]
            for field in ("status", "summary", "verification", "risks", "next_steps")
        },
        "artifact_paths": [item.path for item in canonical.artifacts],
        "trace_command": f"agentdeck trace --id {canonical.trace_ids[0]}",
    }


def build_workflow_prompt(
    *,
    role: str,
    role_prompt: str,
    task: str,
    handoff_token: str,
    previous_handoff: dict[str, Any] | None,
) -> str:
    handoff = (
        json.dumps(previous_handoff, ensure_ascii=False, sort_keys=True, indent=2)
        if previous_handoff is not None
        else "none"
    )
    return (
        "You are executing one explicitly authorized AgentDeck sequential workflow step.\n"
        f"Role: {role}\n"
        f"Role instructions: {role_prompt}\n"
        f"Task: {task}\n"
        "Previous compact handoff:\n"
        f"{handoff}\n\n"
        "Complete only this task. "
        f"Use this handoff token exactly: {handoff_token}\n"
        "Return exactly one structured block:\n"
        "handoff_token: <provided token>\n"
        "status: completed | blocked | failed\n"
        "summary: <text>\n"
        "verification: <text>\n"
        "risks: <text>\n"
        "next_steps: <text>\n"
        "full_output_path: <optional path>"
    )


def _structured_reply_text(reply: dict[str, str]) -> str:
    fields = [*REPLY_FIELDS]
    if reply.get("full_output_path"):
        fields.append("full_output_path")
    return "\n".join(f"{field}: {reply[field]}" for field in fields)


def _stop_workflow(
    store: StateStore,
    *,
    run_id: str,
    turns: list[dict[str, Any]],
    turn: dict[str, Any],
    turn_status: str,
    reason: str,
) -> dict[str, Any]:
    turn["status"] = turn_status
    turn["completed_at"] = utc_now()
    record = store.update_workflow_run(
        run_id,
        status="stopped",
        turns=turns,
        stop_reason=reason,
    )
    store.append_event(
        EventRecord.create(
            "workflow_stopped",
            {
                "run_id": run_id,
                "plan_id": record.get("plan_id"),
                "step": turn.get("step"),
                "agent_id": turn.get("agent_id"),
                "reason": reason,
            },
        )
    )
    return record


def run_sequential_workflow(
    *,
    config: ProjectConfig,
    store: StateStore,
    backend: RuntimeBackend,
    run_id: str,
    poll_interval: float = 0.25,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    record = store.workflow_run_by_id(run_id)
    steps = list(record.get("authorized_steps") or [])
    turns = list(record.get("turns") or [])
    agents = {agent.agent_id: agent for agent in config.agents}
    timeout_seconds = int(record.get("timeout_seconds") or 0)
    previous_handoff = next(
        (
            turn.get("handoff")
            for turn in reversed(turns)
            if turn.get("status") == "completed" and isinstance(turn.get("handoff"), dict)
        ),
        None,
    )

    for step in steps:
        step_number = int(step.get("step") or 0)
        existing = next((turn for turn in turns if turn.get("step") == step_number), None)
        if existing is not None and existing.get("status") == "completed":
            if isinstance(existing.get("handoff"), dict):
                previous_handoff = existing["handoff"]
            continue

        agent_id = str(step.get("agent_id") or "")
        agent = agents.get(agent_id)
        binding = store.agent_binding(agent_id)
        if agent is None or not binding or binding.get("status") != "running" or not binding.get("pane_id"):
            turn = existing or {
                "step": step_number,
                "agent_id": agent_id,
                "handoff_token": f"{run_id}_step_{step_number}",
                "status": "pending",
                "message_id": None,
                "job_id": None,
                "reply_id": None,
                "handoff": None,
                "artifact_paths": [],
                "trace_command": None,
                "started_at": utc_now(),
                "completed_at": None,
            }
            if existing is None:
                turns.append(turn)
            return _stop_workflow(
                store,
                run_id=run_id,
                turns=turns,
                turn=turn,
                turn_status="failed",
                reason="agent_unavailable",
            )
        pane_id = str(binding["pane_id"])
        if not backend.pane_exists(config.runtime, pane_id):
            turn = existing or {
                "step": step_number,
                "agent_id": agent_id,
                "handoff_token": f"{run_id}_step_{step_number}",
                "status": "pending",
                "message_id": None,
                "job_id": None,
                "reply_id": None,
                "handoff": None,
                "artifact_paths": [],
                "trace_command": None,
                "started_at": utc_now(),
                "completed_at": None,
            }
            if existing is None:
                turns.append(turn)
            return _stop_workflow(
                store,
                run_id=run_id,
                turns=turns,
                turn=turn,
                turn_status="failed",
                reason="pane_lost",
            )

        # "不重复派发"这条守卫必须认**任务有没有送出去过**,而不是"有没有这条
        # turn 记录"。`pane_lost` / `agent_unavailable` 建的 turn 从未走到
        # send_input,`message_id` 因此是 None——把它当成"已派发"会让每次
        # resume 都去等一个从没问出口的问题,烧满超时后原地不动,而
        # `can_resume: true` 仍宣称这个 run 还能救。2026-08-05 live 连撞两次。
        undelivered = existing is not None and existing.get("message_id") is None
        if existing is None or undelivered:
            token = f"{run_id}_step_{step_number}"
            prompt = build_workflow_prompt(
                role=str(step.get("role") or agent.role),
                role_prompt=agent.role_prompt,
                task=str(step.get("task") or ""),
                handoff_token=token,
                previous_handoff=previous_handoff,
            )
            dispatch = store.create_dispatch_records(
                "leader", agent_id, str(step.get("task") or ""), prompt, pane_id
            )
            turn = {
                "step": step_number,
                "agent_id": agent_id,
                "handoff_token": token,
                "status": "dispatched",
                "message_id": dispatch["message"]["message_id"],
                "job_id": dispatch["job"]["job_id"],
                "reply_id": None,
                "handoff": None,
                "artifact_paths": [],
                "trace_command": f"agentdeck trace --id {dispatch['message']['message_id']}",
                "started_at": utc_now(),
                "completed_at": None,
            }
            if undelivered:
                turns[turns.index(existing)] = turn
            else:
                turns.append(turn)
            store.update_workflow_run(
                run_id,
                status="running",
                current_step=step_number,
                turns=turns,
                stop_reason=None,
            )
            try:
                backend.send_input(config.runtime, pane_id, prompt)
            except Exception:
                return _stop_workflow(
                    store,
                    run_id=run_id,
                    turns=turns,
                    turn=turn,
                    turn_status="failed",
                    reason="pane_lost",
                )
            store.append_event(
                EventRecord.create(
                    "workflow_step_dispatched",
                    {
                        "run_id": run_id,
                        "step": step_number,
                        "agent_id": agent_id,
                        "message_id": turn["message_id"],
                        "pane_id": pane_id,
                    },
                )
            )
        else:
            turn = existing
            token = str(turn.get("handoff_token") or "")

        started = monotonic()
        while True:
            if not backend.pane_exists(config.runtime, pane_id):
                return _stop_workflow(
                    store,
                    run_id=run_id,
                    turns=turns,
                    turn=turn,
                    turn_status="failed",
                    reason="pane_lost",
                )
            output = backend.capture_output(config.runtime, pane_id, lines=400)
            try:
                reply = parse_correlated_reply(output, token)
            except ValueError:
                return _stop_workflow(
                    store,
                    run_id=run_id,
                    turns=turns,
                    turn=turn,
                    turn_status="failed",
                    reason="invalid_reply",
                )
            if reply is not None:
                recorded = store.record_reply(
                    agent_id,
                    str(turn["message_id"]),
                    _structured_reply_text(reply),
                )
                artifact_paths = [
                    str(item.get("path"))
                    for item in recorded.get("artifacts", [])
                    if item.get("path")
                ]
                handoff = build_compact_handoff(
                    step=step_number,
                    agent_id=agent_id,
                    reply=reply,
                    reply_id=str(recorded["reply_id"]),
                    artifact_paths=artifact_paths,
                )
                turn.update(
                    {
                        "status": reply["status"],
                        "reply_id": recorded["reply_id"],
                        "handoff": handoff,
                        "artifact_paths": artifact_paths,
                        "trace_command": f"agentdeck trace --id {recorded['reply_id']}",
                        "completed_at": utc_now(),
                    }
                )
                store.update_workflow_run(
                    run_id,
                    turns=turns,
                    current_step=step_number + 1,
                )
                if reply["status"] == "completed":
                    store.append_event(
                        EventRecord.create(
                            "workflow_step_completed",
                            {
                                "run_id": run_id,
                                "step": step_number,
                                "agent_id": agent_id,
                                "message_id": turn["message_id"],
                                "reply_id": recorded["reply_id"],
                            },
                        )
                    )
                    previous_handoff = handoff
                    break
                reason = (
                    "worker_blocked" if reply["status"] == "blocked" else "worker_failed"
                )
                return _stop_workflow(
                    store,
                    run_id=run_id,
                    turns=turns,
                    turn=turn,
                    turn_status=reply["status"],
                    reason=reason,
                )
            if monotonic() - started >= timeout_seconds:
                return _stop_workflow(
                    store,
                    run_id=run_id,
                    turns=turns,
                    turn=turn,
                    turn_status="timed_out",
                    reason="timed_out",
                )
            sleeper(poll_interval)

    result = store.update_workflow_run(
        run_id,
        status="completed",
        current_step=len(steps) + 1,
        turns=turns,
        stop_reason=None,
    )
    store.append_event(
        EventRecord.create(
            "workflow_completed",
            {
                "run_id": run_id,
                "plan_id": result.get("plan_id"),
                "step_count": len(steps),
            },
        )
    )
    return result
