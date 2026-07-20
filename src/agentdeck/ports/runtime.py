"""Read-only Observer workspace values and Runtime Port."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Final, Protocol


WORKER_OBSERVER_ROLES: Final = (
    "implementer", "reviewer", "reviser", "acceptance_reviewer",
)
_IDENTITY = re.compile(r"[a-z][a-z0-9_]{0,63}", re.ASCII)
_PANE_ID = re.compile(r"%[0-9]{1,10}", re.ASCII)
_ROLE_INDEX = {role: index for index, role in enumerate(WORKER_OBSERVER_ROLES)}
_OBSERVER_PREFIX = (
    "agentdeck", "observer", "--mode", "event-subscription", "--read-only",
    "--project-id",
)


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


def _plain_text(value: object, field: str, maximum: int = 256) -> str:
    if type(value) is not str:
        raise TypeError(f"{field} must be a plain string")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        raise ValueError(f"{field} must be valid UTF-8") from None
    if (
        not encoded or len(encoded) > maximum
        or any(byte < 33 or byte > 126 for byte in encoded)
    ):
        raise ValueError(f"{field} must be bounded safe text")
    return value


def _namespace(project_id: str) -> str:
    return f"agentdeck-{observer_identity(project_id, 'project_id', 'prj_')}"


def _command(value: object) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise TypeError("command must be an argv tuple")
    if not value or len(value) > 16:
        raise ValueError("command must be bounded nonempty argv")
    return tuple(_plain_text(argument, "command argument") for argument in value)


@dataclass(frozen=True)
class ObserverInstance:
    instance_id: str
    session_id: str
    role: str

    def __post_init__(self) -> None:
        observer_identity(self.instance_id, "instance_id", "agt_")
        observer_identity(self.session_id, "session_id", "ses_")
        if observer_role(self.role) not in WORKER_OBSERVER_ROLES:
            raise ValueError("role must be a worker observer role")


@dataclass(frozen=True)
class ObserverPane:
    name: str
    target: str
    role: str
    instance_id: str | None
    session_id: str | None
    pane_id: str | None
    command: tuple[str, ...]

    def __post_init__(self) -> None:
        name = _plain_text(self.name, "pane name", 128)
        target = _plain_text(self.target, "pane target", 128)
        role = observer_role(self.role)
        if role not in {"overview", *WORKER_OBSERVER_ROLES}:
            raise ValueError("pane role is invalid")
        if self.pane_id is not None and (
            type(self.pane_id) is not str or not _PANE_ID.fullmatch(self.pane_id)
        ):
            raise ValueError("pane_id must be an optional tmux pane identity")
        command = _command(self.command)
        window = "Overview" if role == "overview" else "Workers"
        index = 0 if role == "overview" else _ROLE_INDEX[role]
        suffix = f":{window}.{index}"
        if not target.endswith(suffix):
            raise ValueError("pane target does not match its role")
        namespace = target.removesuffix(suffix)
        if name != f"{namespace}-{role}":
            raise ValueError("pane name does not match its namespace and role")
        project_id = namespace.removeprefix("agentdeck-")
        if namespace != _namespace(project_id):
            raise ValueError("pane namespace is invalid")
        if role == "overview":
            if self.instance_id is not None or self.session_id is not None:
                raise ValueError("overview pane cannot bind a worker")
            expected = (*_OBSERVER_PREFIX, project_id, "--view", "overview")
        else:
            instance_id = observer_identity(self.instance_id, "instance_id", "agt_")
            session_id = observer_identity(self.session_id, "session_id", "ses_")
            expected = (
                *_OBSERVER_PREFIX, project_id, "--session-id", session_id,
                "--instance-id", instance_id,
            )
        if command != expected:
            raise ValueError("pane command does not match its observer binding")


@dataclass(frozen=True)
class ObserverWindow:
    name: str
    target: str
    panes: tuple[ObserverPane, ...]

    def __post_init__(self) -> None:
        name = _plain_text(self.name, "window name", 32)
        target = _plain_text(self.target, "window target", 128)
        if name not in {"Overview", "Workers"}:
            raise ValueError("window name is invalid")
        if type(self.panes) is not tuple or any(
            type(pane) is not ObserverPane for pane in self.panes
        ):
            raise TypeError("panes must be an ObserverPane tuple")
        if not target.endswith(f":{name}"):
            raise ValueError("window target does not match its name")
        namespace = target.removesuffix(f":{name}")
        expected_roles = ("overview",) if name == "Overview" else WORKER_OBSERVER_ROLES
        if tuple(pane.role for pane in self.panes) != expected_roles:
            raise ValueError("window panes do not match exact role order")
        if any(not pane.target.startswith(f"{target}.") for pane in self.panes):
            raise ValueError("window pane target is outside its namespace")
        if name == "Workers":
            instance_ids = tuple(pane.instance_id for pane in self.panes)
            session_ids = tuple(pane.session_id for pane in self.panes)
            if len(set(instance_ids)) != 4 or len(set(session_ids)) != 4:
                raise ValueError("worker pane bindings must be unique")
        if any(not pane.name.startswith(f"{namespace}-") for pane in self.panes):
            raise ValueError("window pane name is outside its namespace")


@dataclass(frozen=True)
class ObserverWorkspacePlan:
    project_id: str
    socket_name: str
    workspace_name: str
    windows: tuple[ObserverWindow, ...]

    def __post_init__(self) -> None:
        project_id = observer_identity(self.project_id, "project_id", "prj_")
        socket_name = _plain_text(self.socket_name, "socket_name", 128)
        workspace_name = _plain_text(self.workspace_name, "workspace_name", 128)
        namespace = _namespace(project_id)
        if socket_name != namespace or workspace_name != namespace:
            raise ValueError("workspace namespace does not match project_id")
        if type(self.windows) is not tuple or any(
            type(window) is not ObserverWindow for window in self.windows
        ):
            raise TypeError("windows must be an ObserverWindow tuple")
        if tuple(window.name for window in self.windows) != ("Overview", "Workers"):
            raise ValueError("workspace must contain exact ordered windows")
        if tuple(window.target for window in self.windows) != (
            f"{namespace}:Overview", f"{namespace}:Workers",
        ):
            raise ValueError("workspace windows do not match its namespace")


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

    def take_ownership(
        self, ownership: TakeoverOwnership, *, plan: ObserverWorkspacePlan,
    ) -> None: ...


__all__ = [
    "ObserverInstance", "ObserverPane", "ObserverRuntime", "ObserverWindow",
    "ObserverWorkspacePlan", "TakeoverOwnership", "WORKER_OBSERVER_ROLES",
    "observer_identity", "observer_role",
]
