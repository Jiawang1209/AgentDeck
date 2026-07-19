"""Persistence Port for command-atomic canonical product facts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Final, Protocol, TypeAlias

from agentdeck.kernel.events import DomainEvent
from agentdeck.kernel.execution import AttemptState
from agentdeck.kernel.session import ExitAttemptSnapshot


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
CommandResult: TypeAlias = dict[str, JsonValue]
AggregateSnapshot: TypeAlias = CommandResult
STORE_COMMAND_ID_MAX_BYTES: Final = 255
STORE_COMMAND_KIND_MAX_BYTES: Final = 128
STORE_SESSION_ID_MAX_BYTES: Final = 255


def _session_identity(value: object) -> str:
    if type(value) is not str:
        raise TypeError("session_id must be a typed identity")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        raise ValueError("session_id must be a typed identity") from None
    suffix = value[4:] if value.startswith("ses_") else ""
    if (
        not suffix
        or len(encoded) > STORE_SESSION_ID_MAX_BYTES
        or not suffix.isascii()
        or not suffix[0].isalnum()
        or any(not (character.isalnum() or character in "_.-") for character in suffix)
    ):
        raise ValueError("session_id must be a typed identity")
    return value


@dataclass(frozen=True)
class SessionSelection:
    session_id: str | None
    nonterminal_count: int

    def __post_init__(self) -> None:
        if type(self.nonterminal_count) is not int:
            raise TypeError("nonterminal_count must be an exact integer")
        if self.nonterminal_count < 0:
            raise ValueError("nonterminal_count cannot be negative")
        if self.session_id is None:
            if self.nonterminal_count != 0:
                raise ValueError("a nonterminal selection requires a session_id")
            return
        _session_identity(self.session_id)
        if self.nonterminal_count == 0:
            raise ValueError("an empty selection cannot have a session_id")


@dataclass(frozen=True)
class RunningAttempt:
    attempt_id: str
    task_id: str
    acp_session_id: str | None
    effect_observed: bool = False
    durable_fingerprint: str | None = None

    def __post_init__(self) -> None:
        for value, prefix, field in (
            (self.attempt_id, "att_", "attempt_id"),
            (self.task_id, "tsk_", "task_id"),
        ):
            if type(value) is not str or not value.startswith(prefix) or not value[len(prefix):]:
                raise ValueError(f"{field} must be a typed identity")
        if self.acp_session_id is not None and (
            type(self.acp_session_id) is not str or not self.acp_session_id.strip()
        ):
            raise ValueError("acp_session_id must be a nonempty string or None")
        if type(self.effect_observed) is not bool:
            raise TypeError("effect_observed must be a bool")
        if self.durable_fingerprint is not None and (
            type(self.durable_fingerprint) is not str
            or len(self.durable_fingerprint) != 64
            or any(character not in "0123456789abcdef"
                   for character in self.durable_fingerprint)
        ):
            raise ValueError("durable_fingerprint must be 64 lowercase hex or None")


class StoreTransaction(Protocol):
    """One live command-bound unit; implementations own commit or rollback."""

    duplicate_result: CommandResult | None

    def set_result(self, result: CommandResult) -> None: ...
    def lookup_command(
        self, command_id: str, command_kind: str | None = None
    ) -> CommandResult | None: ...
    def record_command(
        self, command_id: str, command_kind: str, result: CommandResult
    ) -> None: ...
    def load_aggregate(
        self, aggregate_type: str, aggregate_id: str
    ) -> AggregateSnapshot | None: ...
    def save_aggregate(
        self, aggregate_type: str, aggregate_id: str, snapshot: AggregateSnapshot
    ) -> None: ...
    def save_session(self, snapshot: Mapping[str, object]) -> None: ...
    def save_attempt(self, snapshot: Mapping[str, object]) -> None: ...
    def recover_attempt(
        self, attempt_id: str, state: AttemptState, reason: str | None,
        *, expected: RunningAttempt | None = None,
    ) -> None: ...
    def list_active_exit_attempts(
        self, session_id: str,
    ) -> tuple[ExitAttemptSnapshot, ...]: ...
    def append_event(self, event: DomainEvent | Mapping[str, object]) -> None: ...


class Store(Protocol):
    """Read operations plus indivisible command entry; never standalone mutation."""

    def command(
        self, command_id: str, command_kind: str
    ) -> AbstractContextManager[StoreTransaction]: ...
    def execute_once(
        self,
        command_id: str,
        command_kind: str,
        callback: Callable[[StoreTransaction], CommandResult],
    ) -> CommandResult: ...
    def lookup_command(
        self, command_id: str, command_kind: str | None = None
    ) -> CommandResult | None: ...
    def load_aggregate(
        self, aggregate_type: str, aggregate_id: str
    ) -> AggregateSnapshot | None: ...
    def select_latest_nonterminal_session(self) -> SessionSelection: ...
    def list_running_attempts(self) -> tuple[RunningAttempt, ...]: ...
    def list_active_exit_attempts(
        self, session_id: str,
    ) -> tuple[ExitAttemptSnapshot, ...]: ...
    def count(self, table: str) -> int: ...
    def close(self) -> None: ...
