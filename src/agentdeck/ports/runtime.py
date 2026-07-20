"""Read-only Observer workspace values and Runtime Port."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Final, Protocol


WORKER_OBSERVER_ROLES: Final = (
    "implementer", "reviewer", "reviser", "acceptance_reviewer",
)
_IDENTITY = re.compile(r"[a-z][a-z0-9_]{0,63}", re.ASCII)


def observer_identity(value: object, field: str, prefix: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field} must be a plain string")
    if (
        not value.startswith(prefix)
        or not _IDENTITY.fullmatch(value)
        or len(value.encode("ascii")) > 64
        or not value.removeprefix(prefix)
    ):
        raise ValueError(f"{field} must be a project-safe {prefix} identity")
    return value


def observer_role(value: object) -> str:
    if type(value) is not str:
        raise TypeError("role must be a plain string")
    if not _IDENTITY.fullmatch(value):
        raise ValueError("role must be a plain project-safe name")
    return value


@dataclass(frozen=True)
class ObserverInstance:
    instance_id: str
    session_id: str
    role: str

    def __post_init__(self) -> None:
        observer_identity(self.instance_id, "instance_id", "agt_")
        observer_identity(self.session_id, "session_id", "ses_")
        observer_role(self.role)


@dataclass(frozen=True)
class ObserverPane:
    name: str
    target: str
    role: str
    instance_id: str | None
    session_id: str | None
    pane_id: str | None
    command: tuple[str, ...]


@dataclass(frozen=True)
class ObserverWindow:
    name: str
    target: str
    panes: tuple[ObserverPane, ...]


@dataclass(frozen=True)
class ObserverWorkspacePlan:
    project_id: str
    socket_name: str
    workspace_name: str
    windows: tuple[ObserverWindow, ...]


@dataclass(frozen=True)
class TakeoverOwnership:
    project_id: str
    instance_id: str
    session_id: str
    role: str
    owner_id: str

    def __post_init__(self) -> None:
        observer_identity(self.project_id, "project_id", "prj_")
        observer_identity(self.instance_id, "instance_id", "agt_")
        observer_identity(self.session_id, "session_id", "ses_")
        if observer_role(self.role) not in WORKER_OBSERVER_ROLES:
            raise ValueError("role must be a worker observer role")
        if self.owner_id != "human":
            raise ValueError("takeover owner must be human")


class ObserverRuntime(Protocol):
    def create_workspace(
        self, *, project_id: str, instances: tuple[ObserverInstance, ...],
    ) -> ObserverWorkspacePlan: ...

    def select_workspace(self, *, project_id: str) -> None: ...

    def close_workspace(self, *, project_id: str) -> None: ...

    def take_ownership(self, ownership: TakeoverOwnership) -> None: ...


__all__ = [
    "ObserverInstance", "ObserverPane", "ObserverRuntime", "ObserverWindow",
    "ObserverWorkspacePlan", "TakeoverOwnership", "WORKER_OBSERVER_ROLES",
    "observer_identity", "observer_role",
]
