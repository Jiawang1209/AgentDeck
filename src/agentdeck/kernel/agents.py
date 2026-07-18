from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class AgentRole(StrEnum):
    LEADER = "leader"
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"
    REVISER = "reviser"
    ACCEPTANCE_REVIEWER = "acceptance_reviewer"


class AgentIdentityError(ValueError):
    """Raised when Agent Instance or ACP session identity is not distinct."""


def _require_nonempty_string(value: object, field: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field} must be a string")
    if not value.strip():
        raise ValueError(f"{field} must not be empty")
    return value


@dataclass(frozen=True)
class AgentBackend:
    backend_id: str
    transport: str
    version: str

    def __post_init__(self) -> None:
        _require_nonempty_string(self.backend_id, "backend_id")
        _require_nonempty_string(self.transport, "transport")
        _require_nonempty_string(self.version, "version")


@dataclass(frozen=True)
class AgentInstance:
    instance_id: str
    backend: AgentBackend
    role: AgentRole
    session_id: str

    def __post_init__(self) -> None:
        _require_nonempty_string(self.instance_id, "instance_id")
        if type(self.backend) is not AgentBackend:
            raise TypeError("backend must be an AgentBackend")
        if type(self.role) is not AgentRole:
            raise TypeError("role must be an AgentRole")
        _require_nonempty_string(self.session_id, "session_id")


def validate_distinct_agent_instances(
    instances: Iterable[AgentInstance],
) -> tuple[AgentInstance, ...]:
    copied = tuple(instances)
    if any(type(instance) is not AgentInstance for instance in copied):
        raise TypeError("instances must contain AgentInstance values")

    instance_ids: set[str] = set()
    session_ids: set[str] = set()
    for instance in copied:
        if instance.instance_id in instance_ids:
            raise AgentIdentityError(f"duplicate instance_id: {instance.instance_id}")
        if instance.session_id in session_ids:
            raise AgentIdentityError(f"duplicate session_id: {instance.session_id}")
        instance_ids.add(instance.instance_id)
        session_ids.add(instance.session_id)
    return copied
