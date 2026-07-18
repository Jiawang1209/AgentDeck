"""Persistence boundary for canonical product-kernel facts."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol, TypeAlias

from agentdeck.kernel.events import DomainEvent, FactPayload


AggregateSnapshot: TypeAlias = FactPayload
CommandResult: TypeAlias = FactPayload


class StoreTransaction(Protocol):
    """One atomic unit over canonical facts; implementations own commit/rollback."""

    def lookup_command(self, command_id: str) -> CommandResult | None: ...

    def record_command(
        self, command_id: str, command_kind: str, result: CommandResult
    ) -> None: ...

    def load_aggregate(
        self, aggregate_type: str, aggregate_id: str
    ) -> AggregateSnapshot | None: ...

    def save_aggregate(
        self,
        aggregate_type: str,
        aggregate_id: str,
        snapshot: AggregateSnapshot,
    ) -> None: ...

    def append_event(self, event: DomainEvent) -> None: ...


class Store(Protocol):
    """Project reads and atomic transaction entry, never standalone mutation."""

    def transaction(self) -> AbstractContextManager[StoreTransaction]: ...

    def lookup_command(self, command_id: str) -> CommandResult | None: ...

    def load_aggregate(
        self, aggregate_type: str, aggregate_id: str
    ) -> AggregateSnapshot | None: ...

    def close(self) -> None: ...
