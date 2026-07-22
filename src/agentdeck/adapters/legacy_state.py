"""Read-only parser for legacy AgentDeck JSON state as INERT external data.

Task 37 treats the old `.agentdeck/state.json` + `events.jsonl` strictly as
external input: this adapter parses them into frozen, inert records with bounded
reading and content hashes. It never imports `state.py`/`models.py`, never
writes, and never lets legacy JSON become a second write authority — migration
into the new SQLite kernel is an explicit, separately gated step.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path

_MAX_STATE_BYTES = 8 * 1024 * 1024
_MAX_EVENT_BYTES = 64 * 1024 * 1024
_MAX_EVENT_LINES = 1_000_000


class LegacyStateError(ValueError):
    """Raised when legacy state cannot be parsed as inert external data."""


@dataclass(frozen=True)
class LegacySource:
    path: str
    kind: str
    hash: str


@dataclass(frozen=True)
class LegacyState:
    project_id: str
    resolved_root: str
    created_at: str
    agent_count: int
    message_count: int
    job_count: int
    event_count: int
    sources: tuple[LegacySource, ...]
    content_hash: str


def _sha(data: bytes) -> str:
    return "sha256:" + sha256(data).hexdigest()


def _nonempty_str(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise LegacyStateError(f"legacy {field} must be a non-empty string")
    return value


def parse_legacy_state(project_dir: object) -> LegacyState | None:
    """Parse a project's legacy state, or return ``None`` if none exists."""
    root = Path(project_dir)
    state_path = root / ".agentdeck" / "state.json"
    if not state_path.is_file():
        return None
    state_bytes = state_path.read_bytes()
    if len(state_bytes) > _MAX_STATE_BYTES:
        raise LegacyStateError("legacy state.json exceeds size bound")
    try:
        data = json.loads(state_bytes)
    except json.JSONDecodeError as error:
        raise LegacyStateError("legacy state.json is not valid JSON") from error
    if not isinstance(data, dict):
        raise LegacyStateError("legacy state.json must be a JSON object")

    project = data.get("project")
    if not isinstance(project, dict):
        raise LegacyStateError("legacy state.json has no project object")
    project_id = _nonempty_str(project.get("project_id"), "project_id")
    resolved_root = _nonempty_str(project.get("root"), "project root")
    created_at = _nonempty_str(project.get("created_at"), "project created_at")

    agents = data.get("agents") or {}
    messages = data.get("messages") or []
    jobs = data.get("jobs") or []
    if (
        not isinstance(agents, dict)
        or not isinstance(messages, list)
        or not isinstance(jobs, list)
    ):
        raise LegacyStateError("legacy agents/messages/jobs have unexpected shapes")

    sources = [LegacySource(str(state_path), "state", _sha(state_bytes))]
    event_count = 0
    events_path = root / ".agentdeck" / "events.jsonl"
    if events_path.is_file():
        event_bytes = events_path.read_bytes()
        if len(event_bytes) > _MAX_EVENT_BYTES:
            raise LegacyStateError("legacy events.jsonl exceeds size bound")
        event_count = sum(1 for line in event_bytes.splitlines() if line.strip())
        if event_count > _MAX_EVENT_LINES:
            raise LegacyStateError("legacy events.jsonl exceeds line bound")
        sources.append(LegacySource(str(events_path), "events", _sha(event_bytes)))

    # Content-addressed over source hashes only (path-independent), so the same
    # legacy content hashes identically regardless of where it is checked out.
    content_hash = "sha256:" + sha256(
        "\n".join(f"{source.kind}:{source.hash}" for source in sources).encode(
            "utf-8"
        )
    ).hexdigest()

    return LegacyState(
        project_id=project_id,
        resolved_root=resolved_root,
        created_at=created_at,
        agent_count=len(agents),
        message_count=len(messages),
        job_count=len(jobs),
        event_count=event_count,
        sources=tuple(sources),
        content_hash=content_hash,
    )


__all__ = ["LegacySource", "LegacyState", "LegacyStateError", "parse_legacy_state"]
