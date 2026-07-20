"""Validated public records for the sequential approval bridge."""

from __future__ import annotations

from dataclasses import dataclass

from agentdeck.kernel.diagnostics import Diagnostic
from agentdeck.kernel.permissions import PermissionScope
from agentdeck.ports.approval import ApprovalRecord
from agentdeck.ports.worker import WorkerResult


@dataclass(frozen=True)
class ApprovalContext:
    mission_id: str
    mission_version: int
    permission_scope: PermissionScope
    scope_hash: str

    def __post_init__(self) -> None:
        if type(self.mission_id) is not str or not self.mission_id.startswith("msn_"):
            raise ValueError("mission_id must be a typed identity")
        if type(self.mission_version) is not int or self.mission_version < 1:
            raise ValueError("mission_version must be positive")
        if type(self.permission_scope) is not PermissionScope:
            raise TypeError("permission_scope must be a PermissionScope")
        if (
            type(self.scope_hash) is not str
            or len(self.scope_hash) != 64
            or any(character not in "0123456789abcdef" for character in self.scope_hash)
        ):
            raise ValueError("scope_hash must be 64 lowercase hex")


@dataclass(frozen=True)
class PermissionBridgeResult:
    approvals: tuple[ApprovalRecord, ...]
    worker_result: WorkerResult
    terminal_result_validated: bool
    handoff_committed: bool = False
    next_task_allowed: bool = False
    diagnostic: Diagnostic | None = None

    def __post_init__(self) -> None:
        if type(self.approvals) is not tuple or any(
            type(item) is not ApprovalRecord for item in self.approvals
        ):
            raise TypeError("approvals must contain ApprovalRecord values")
        if type(self.worker_result) is not WorkerResult:
            raise TypeError("worker_result must be a WorkerResult")
        if self.terminal_result_validated is not True:
            raise ValueError("bridge result requires a validated terminal result")
        if self.handoff_committed or self.next_task_allowed:
            raise ValueError("ApprovalService cannot authorize the next Task")
        if self.diagnostic is not None and type(self.diagnostic) is not Diagnostic:
            raise TypeError("diagnostic must be a Diagnostic or None")


__all__ = ["ApprovalContext", "PermissionBridgeResult"]
