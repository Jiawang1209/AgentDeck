"""Read-only end-to-end Mission trace and sanitized human support evidence."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from platform import python_version
import re
from typing import Final

from agentdeck.kernel.diagnostics import diagnostic
from agentdeck.ports.store import Store


class SupportServiceError(ValueError):
    """Raised when a Mission trace or support bundle cannot be safely built."""


_MAX_BUNDLE_BYTES: Final = 256_000
_TRUNCATION_NOTICE: Final = "... support bundle truncated to stay within the size bound ..."
_PROHIBITED_TERMS: Final = ("raw_protocol", "terminal_output", "API_KEY")
_SECRET_PATTERN: Final = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|bearer)\b\s*[:=]?\s*\S+"
)
_ABSOLUTE_PATH_PATTERN: Final = re.compile(
    r"(?<![\w/])/(?:home|Users|root)/[^\s\"']+"
)
_STORE_METHODS: Final = (
    "list_mission_tasks", "list_task_attempts", "list_attempt_handoffs",
    "list_mission_approvals", "list_attempt_evidence",
)


def _redact(text: str) -> str:
    """Strip raw frames, terminal output, secrets, and private absolute paths."""

    redacted = _SECRET_PATTERN.sub("[redacted]", text)
    redacted = _ABSOLUTE_PATH_PATTERN.sub("[redacted]", redacted)
    for term in _PROHIBITED_TERMS:
        redacted = redacted.replace(term, "[redacted]")
    return redacted


def _identity(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise SupportServiceError(f"{field} must be a nonempty string")
    return value


def _content_hash(canonical_facts: object) -> str:
    if type(canonical_facts) is not str:
        raise SupportServiceError("canonical facts must be a string")
    return sha256(canonical_facts.encode("utf-8", "strict")).hexdigest()


def _diagnostic_summary(reason: object, occurred_at: object, attempt_id: str) -> str:
    if reason is None:
        return "none"
    if type(reason) is not str:
        return "unknown"
    try:
        fact = diagnostic(
            reason,
            occurred_at=occurred_at if type(occurred_at) is str else "1970-01-01T00:00:00+00:00",
            attempt_id=attempt_id,
        )
    except (TypeError, ValueError):
        return reason
    return f"{fact.code}: {fact.summary}"


@dataclass(frozen=True)
class Permission:
    """One human-decided Approval surfaced as trace lineage evidence."""

    approval_id: str
    attempt_id: str | None
    effect: str
    state: str


@dataclass(frozen=True)
class MissionTrace:
    """The verified end-to-end lineage identity walk for one Mission."""

    path: tuple[str, ...]
    permissions: tuple[Permission, ...]


@dataclass(frozen=True)
class SupportBundle:
    """A bounded, sanitized, human-readable support evidence bundle."""

    text: str
    byte_count: int


class SupportService:
    """Own read-only Mission lineage verification and support evidence."""

    def __init__(self, *, store: Store) -> None:
        if any(not callable(getattr(store, name, None)) for name in _STORE_METHODS):
            raise TypeError("store does not satisfy the Support Service")
        self._store = store

    def trace(self, mission_id: str) -> MissionTrace:
        mission_id = _identity(mission_id, "mission_id")
        tasks = self._store.list_mission_tasks(mission_id)
        if not tasks:
            raise SupportServiceError(f"unknown mission: {mission_id}")
        tasks_by_id = {task["task_id"]: task for task in tasks}
        path: list[str] = [mission_id]
        visited: set[str] = set()
        current_task_id = tasks[0]["task_id"]
        while True:
            if current_task_id in visited:
                raise SupportServiceError("mission lineage contains a cycle")
            visited.add(current_task_id)
            path.append(current_task_id)
            attempts = self._store.list_task_attempts(current_task_id)
            next_task_id = self._follow_attempts(attempts, tasks_by_id, path)
            if next_task_id is None:
                break
            current_task_id = next_task_id
        return MissionTrace(path=tuple(path), permissions=self._permissions(mission_id))

    def _follow_attempts(
        self, attempts: tuple[dict[str, object], ...],
        tasks_by_id: dict[str, dict[str, object]], path: list[str],
    ) -> str | None:
        for attempt in attempts:
            attempt_id = attempt["attempt_id"]
            self._verify_attempt_evidence(attempt_id)
            handoffs = self._store.list_attempt_handoffs(attempt_id)
            verified: list[dict[str, object]] = []
            for handoff in handoffs:
                self._verify_handoff(handoff)
                if handoff["target_task_id"] not in tasks_by_id:
                    raise SupportServiceError(
                        f"handoff target is outside this mission: {handoff['handoff_id']}"
                    )
                verified.append(handoff)
            if verified:
                path.append(attempt_id)
                chosen = verified[0]
                path.append(chosen["handoff_id"])
                return chosen["target_task_id"]
        if attempts:
            path.append(attempts[0]["attempt_id"])
        return None

    def _permissions(self, mission_id: str) -> tuple[Permission, ...]:
        approvals = self._store.list_mission_approvals(mission_id)
        return tuple(
            Permission(
                approval_id=approval["approval_id"],
                attempt_id=approval.get("attempt_id"),
                effect=approval["effect"], state=approval["state"],
            )
            for approval in approvals
        )

    def _verify_handoff(self, handoff: dict[str, object]) -> None:
        if _content_hash(handoff["canonical_handoff_facts"]) != handoff["content_hash"]:
            raise SupportServiceError(
                f"handoff content hash mismatch: {handoff['handoff_id']}"
            )

    def _verify_attempt_evidence(self, attempt_id: str) -> None:
        for evidence in self._store.list_attempt_evidence(attempt_id):
            if _content_hash(evidence["canonical_evidence_facts"]) != evidence["content_hash"]:
                raise SupportServiceError(
                    f"evidence content hash mismatch: {evidence['evidence_id']}"
                )

    def support_bundle(self, mission_id: str) -> SupportBundle:
        mission_id = _identity(mission_id, "mission_id")
        tasks = self._store.list_mission_tasks(mission_id)
        if not tasks:
            raise SupportServiceError(f"unknown mission: {mission_id}")
        approvals = self._store.list_mission_approvals(mission_id)
        lines, add, done = _bundle_writer(mission_id, tasks)
        for task in tasks:
            if done[0] or not add(
                f"  - {task['task_id']} role={task['role']} state={task['state']}"
            ):
                break
            self._write_task_attempts(task, add)
        if not done[0]:
            self._write_approvals(approvals, add)
        if done[0]:
            lines.append("")
            lines.append(_TRUNCATION_NOTICE)
        text = _redact("\n".join(lines))
        encoded = text.encode("utf-8", "strict")
        if len(encoded) > _MAX_BUNDLE_BYTES:
            text = encoded[:_MAX_BUNDLE_BYTES].decode("utf-8", "ignore")
        return SupportBundle(text=text, byte_count=len(text.encode("utf-8", "strict")))

    def _write_task_attempts(self, task: dict[str, object], add) -> None:
        for attempt in self._store.list_task_attempts(task["task_id"]):
            summary = _diagnostic_summary(
                attempt.get("reason"), attempt.get("updated_at"), attempt["attempt_id"],
            )
            if not add(
                f"      attempt {attempt['attempt_id']} state={attempt['state']} "
                f"diagnostic={summary}"
            ):
                return
            for evidence in self._store.list_attempt_evidence(attempt["attempt_id"]):
                if not add(
                    f"      evidence {evidence['evidence_id']} kind={evidence['kind']} "
                    f"content_hash={evidence['content_hash']}"
                ):
                    return

    def _write_approvals(self, approvals, add) -> None:
        add("")
        add("approvals:")
        for approval in approvals:
            if not add(
                f"  - {approval['approval_id']} effect={approval['effect']} "
                f"state={approval['state']}"
            ):
                return


def _bundle_writer(mission_id: str, tasks: tuple[dict[str, object], ...]):
    lines: list[str] = [
        f"AgentDeck support bundle for mission {mission_id}",
        "",
        "environment:",
        f"  python: {python_version()}",
    ]
    backends = sorted({str(task.get("planned_backend")) for task in tasks})
    lines.append(f"  resolved backends: {', '.join(backends) if backends else 'none'}")
    lines.append("")
    lines.append("tasks:")
    budget = _MAX_BUNDLE_BYTES - len(_TRUNCATION_NOTICE.encode("utf-8", "strict")) - 64
    used = [sum(len(line.encode("utf-8", "strict")) + 1 for line in lines)]
    done = [False]

    def add(line: str) -> bool:
        cost = len(line.encode("utf-8", "strict")) + 1
        if used[0] + cost > budget:
            done[0] = True
            return False
        lines.append(line)
        used[0] += cost
        return True

    return lines, add, done


__all__ = [
    "MissionTrace", "Permission", "SupportBundle", "SupportService",
    "SupportServiceError",
]
