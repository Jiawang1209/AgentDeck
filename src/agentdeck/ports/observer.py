"""Closed values and narrow capabilities for human Observer delivery."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol

from agentdeck.ports.worker import WorkerEvent


_ID = re.compile(r"[a-z][a-z0-9_]{0,254}", re.ASCII)
_HASH = re.compile(r"[0-9a-f]{64}", re.ASCII)
_MAX_SEQUENCE = 2**63 - 1


def _identity(value: object, field: str, prefix: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field} must be a plain string")
    if (
        not value.startswith(prefix) or not value.removeprefix(prefix)
        or not _ID.fullmatch(value) or len(value.encode("ascii")) > 255
    ):
        raise ValueError(f"{field} must be a bounded {prefix} identity")
    return value


def _transport(value: object) -> str:
    if value != "acp":
        raise ValueError("observer transport must be acp")
    return value


@dataclass(frozen=True)
class ObserverSubscription:
    project_id: str
    session_id: str
    agent_id: str

    def __post_init__(self) -> None:
        _identity(self.project_id, "project_id", "prj_")
        _identity(self.session_id, "session_id", "ses_")
        _identity(self.agent_id, "agent_id", "agt_")


@dataclass(frozen=True)
class ObserverBinding:
    project_id: str
    session_id: str
    agent_id: str
    task_id: str
    attempt_id: str
    transport: str

    def __post_init__(self) -> None:
        _identity(self.project_id, "project_id", "prj_")
        _identity(self.session_id, "session_id", "ses_")
        _identity(self.agent_id, "agent_id", "agt_")
        _identity(self.task_id, "task_id", "tsk_")
        _identity(self.attempt_id, "attempt_id", "att_")
        _transport(self.transport)


@dataclass(frozen=True)
class ObserverCursor:
    project_id: str
    session_id: str
    agent_id: str
    task_id: str
    attempt_id: str
    transport: str
    sequence: int
    event_id: str
    fingerprint: str

    def __post_init__(self) -> None:
        ObserverBinding(
            self.project_id, self.session_id, self.agent_id, self.task_id,
            self.attempt_id, self.transport,
        )
        _identity(self.event_id, "event_id", "evt_")
        if type(self.sequence) is not int or not 1 <= self.sequence <= _MAX_SEQUENCE:
            raise ValueError("observer cursor sequence is invalid")
        if type(self.fingerprint) is not str or not _HASH.fullmatch(self.fingerprint):
            raise ValueError("observer cursor fingerprint is invalid")

    @property
    def binding(self) -> ObserverBinding:
        return ObserverBinding(
            self.project_id, self.session_id, self.agent_id, self.task_id,
            self.attempt_id, self.transport,
        )


@dataclass(frozen=True)
class ObserverAcknowledgement:
    cursor: ObserverCursor

    def __post_init__(self) -> None:
        if type(self.cursor) is not ObserverCursor:
            raise TypeError("acknowledgement requires an exact ObserverCursor")


@dataclass(frozen=True)
class ObserverPublication:
    binding: ObserverBinding
    event: WorkerEvent
    cursor: ObserverCursor

    def __post_init__(self) -> None:
        if (
            type(self.binding) is not ObserverBinding
            or type(self.event) is not WorkerEvent
            or type(self.cursor) is not ObserverCursor
            or self.cursor.binding != self.binding
            or (
                self.event.session_id, self.event.agent_id, self.event.task_id,
                self.event.attempt_id, self.event.transport,
                self.event.sequence, self.event.event_id,
            ) != (
                self.binding.session_id, self.binding.agent_id,
                self.binding.task_id, self.binding.attempt_id,
                self.binding.transport, self.cursor.sequence,
                self.cursor.event_id,
            )
        ):
            raise ValueError("Observer publication lineage is invalid")


class ObserverCursorReader(Protocol):
    def load(self, binding: ObserverBinding) -> ObserverCursor | None: ...


class ObserverCursorAcknowledger(Protocol):
    def acknowledge(self, acknowledgement: ObserverAcknowledgement) -> None: ...


class ObserverEventPublisher(Protocol):
    def publish(self, event: WorkerEvent) -> None: ...


class ObserverChannel(Protocol):
    def publish(self, publication: ObserverPublication) -> bool: ...


class ObserverCursorStore(Protocol):
    def load(self) -> ObserverCursor | None: ...
    def acknowledge(self, cursor: ObserverCursor) -> None: ...


class ObservationSink(Protocol):
    def emit(self, record: str) -> None: ...


__all__ = [
    "ObservationSink", "ObserverAcknowledgement", "ObserverBinding",
    "ObserverChannel", "ObserverCursor", "ObserverCursorAcknowledger",
    "ObserverCursorReader", "ObserverCursorStore", "ObserverEventPublisher",
    "ObserverPublication", "ObserverSubscription",
]
