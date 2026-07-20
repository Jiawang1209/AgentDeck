"""Typed, content-free CAS authority for one active Product exit."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from agentdeck.kernel.session import ExitRequest
from agentdeck.ports.worker import WorkerHandle


_ACTIVE_ATTEMPT_STATES = frozenset({
    "running", "awaiting_approval", "human_controlled",
})


def _identity(value: object, prefix: str, field: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field} must be a typed identity")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        raise ValueError(f"{field} must be a typed identity") from None
    if (
        not value.startswith(prefix) or not value.removeprefix(prefix)
        or len(encoded) > 255 or any(character.isspace() for character in value)
    ):
        raise ValueError(f"{field} must be a typed identity")
    return value


@dataclass(frozen=True)
class ActiveExitAuthority:
    session_id: str
    session_state: str
    request: ExitRequest
    task_id: str
    task_state: str
    task_agent_instance_id: str
    task_mission_id: str
    task_mission_version: int
    mission_state: str
    mission_session_id: str
    mission_current_version: int
    agent_instance_id: str
    agent_session_id: str
    agent_transport: str
    agent_acp_session_id: str
    agent_state: str
    worker_handle: WorkerHandle

    def __post_init__(self) -> None:
        _identity(self.session_id, "ses_", "session_id")
        _identity(self.task_id, "tsk_", "task_id")
        _identity(self.task_agent_instance_id, "agt_", "task agent")
        _identity(self.task_mission_id, "msn_", "task mission")
        _identity(self.mission_session_id, "ses_", "mission session")
        _identity(self.agent_instance_id, "agt_", "agent instance")
        _identity(self.agent_session_id, "ses_", "agent session")
        _identity(self.agent_acp_session_id, "ses_", "agent ACP session")
        if type(self.request) is not ExitRequest:
            raise TypeError("request must be an ExitRequest")
        if type(self.worker_handle) is not WorkerHandle:
            raise TypeError("worker_handle must be a WorkerHandle")
        if self.session_state != "running":
            raise ValueError("active exit session must be running")
        if self.request.attempt.state.value not in _ACTIVE_ATTEMPT_STATES:
            raise ValueError("active exit Attempt state is invalid")
        for value, field in (
            (self.task_state, "task state"),
            (self.mission_state, "mission state"),
            (self.agent_state, "agent state"),
            (self.agent_transport, "agent transport"),
        ):
            if type(value) is not str or not value or len(value.encode("utf-8")) > 64:
                raise ValueError(f"{field} is invalid")
        if type(self.task_mission_version) is not int or (
            type(self.mission_current_version) is not int
        ):
            raise TypeError("active exit Mission versions must be exact integers")
        expected = self.request.attempt
        if (
            self.session_id != self.mission_session_id
            or self.session_id != self.agent_session_id
            or self.task_id != expected.task_id
            or self.task_agent_instance_id != expected.agent_instance_id
            or self.agent_instance_id != expected.agent_instance_id
            or self.agent_acp_session_id != expected.acp_session_id
            or self.task_mission_version != self.mission_current_version
            or (
                self.worker_handle.attempt_id,
                self.worker_handle.task_id,
                self.worker_handle.agent_id,
                self.worker_handle.session_id,
                self.worker_handle.transport,
            ) != (
                expected.attempt_id,
                expected.task_id,
                expected.agent_instance_id,
                expected.acp_session_id,
                "acp",
            )
        ):
            raise ValueError("active exit authority lineage is invalid")
        self.canonical_bytes()

    @property
    def is_cancellable(self) -> bool:
        return (
            self.session_state == "running"
            and self.task_state == "running"
            and self.mission_state == "running"
            and self.agent_state == "active"
            and self.agent_transport == "acp"
        )

    def canonical_facts(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "session_state": self.session_state,
            "request_id": self.request.request_id,
            "request_attempt_hash": self.request.attempt_hash,
            "request_attempt": self.request.attempt.canonical_facts(),
            "request_requested_at": self.request.requested_at,
            "task_id": self.task_id,
            "task_state": self.task_state,
            "task_agent_instance_id": self.task_agent_instance_id,
            "task_mission_id": self.task_mission_id,
            "task_mission_version": self.task_mission_version,
            "mission_state": self.mission_state,
            "mission_session_id": self.mission_session_id,
            "mission_current_version": self.mission_current_version,
            "agent_instance_id": self.agent_instance_id,
            "agent_session_id": self.agent_session_id,
            "agent_transport": self.agent_transport,
            "agent_acp_session_id": self.agent_acp_session_id,
            "agent_state": self.agent_state,
            "worker_handle": {
                "session_id": self.worker_handle.session_id,
                "agent_id": self.worker_handle.agent_id,
                "task_id": self.worker_handle.task_id,
                "attempt_id": self.worker_handle.attempt_id,
                "transport": self.worker_handle.transport,
            },
        }

    def canonical_bytes(self) -> bytes:
        encoded = json.dumps(
            self.canonical_facts(), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8", "strict")
        if not encoded or len(encoded) > 16_384:
            raise ValueError("active exit authority is oversized")
        return encoded

    @property
    def content_hash(self) -> str:
        return sha256(self.canonical_bytes()).hexdigest()


__all__ = ["ActiveExitAuthority"]
