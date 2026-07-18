from dataclasses import FrozenInstanceError

import pytest

from agentdeck.kernel.agents import (
    AgentBackend,
    AgentIdentityError,
    AgentInstance,
    AgentRole,
    validate_distinct_agent_instances,
)


def test_same_backend_roles_require_distinct_instances_and_acp_sessions() -> None:
    backend = AgentBackend("codex-cli", "ACP", "0.131.0")
    implementer = AgentInstance("agt_1", backend, AgentRole.IMPLEMENTER, "acp_1")
    reviser = AgentInstance("agt_2", backend, AgentRole.REVISER, "acp_2")

    validated = validate_distinct_agent_instances([implementer, reviser])

    assert validated == (implementer, reviser)
    assert implementer.backend == reviser.backend == backend
    assert implementer.instance_id != reviser.instance_id
    assert implementer.session_id != reviser.session_id
    assert implementer != reviser


def test_agent_instance_boundary_rejects_duplicate_instance_id() -> None:
    backend = AgentBackend("codex-cli", "ACP", "0.131.0")
    implementer = AgentInstance("agt_1", backend, AgentRole.IMPLEMENTER, "acp_1")
    reviewer = AgentInstance("agt_1", backend, AgentRole.REVIEWER, "acp_2")

    with pytest.raises(AgentIdentityError, match="duplicate instance_id: agt_1"):
        validate_distinct_agent_instances([implementer, reviewer])


def test_agent_instance_boundary_rejects_duplicate_acp_session_id() -> None:
    backend = AgentBackend("codex-cli", "ACP", "0.131.0")
    implementer = AgentInstance("agt_1", backend, AgentRole.IMPLEMENTER, "acp_1")
    reviewer = AgentInstance("agt_2", backend, AgentRole.REVIEWER, "acp_1")

    with pytest.raises(AgentIdentityError, match="duplicate session_id: acp_1"):
        validate_distinct_agent_instances([implementer, reviewer])


def test_agent_instance_boundary_copies_input_to_an_immutable_tuple() -> None:
    backend = AgentBackend("codex-cli", "ACP", "0.131.0")
    implementer = AgentInstance("agt_1", backend, AgentRole.IMPLEMENTER, "acp_1")
    source = [implementer]

    validated = validate_distinct_agent_instances(source)
    source.clear()

    assert validated == (implementer,)
    assert isinstance(validated, tuple)


def test_agent_instance_boundary_allows_empty_input() -> None:
    assert validate_distinct_agent_instances([]) == ()


def test_agent_instance_boundary_rejects_non_instance_values() -> None:
    with pytest.raises(TypeError, match="instances must contain AgentInstance values"):
        validate_distinct_agent_instances(["agt_1"])  # type: ignore[list-item]


def test_backend_identity_cannot_substitute_for_an_agent_instance() -> None:
    backend = AgentBackend("codex-cli", "ACP", "0.131.0")

    with pytest.raises(TypeError):
        AgentInstance("agt_1", backend.backend_id, AgentRole.LEADER, "acp_1")  # type: ignore[arg-type]


def test_agent_identity_facts_are_immutable() -> None:
    backend = AgentBackend("codex-cli", "ACP", "0.131.0")
    instance = AgentInstance("agt_1", backend, AgentRole.LEADER, "acp_1")

    with pytest.raises(FrozenInstanceError):
        backend.backend_id = "other-cli"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        instance.role = AgentRole.REVIEWER  # type: ignore[misc]


def test_agent_roles_are_the_declared_product_roles() -> None:
    assert {role.value for role in AgentRole} == {
        "leader",
        "implementer",
        "reviewer",
        "reviser",
        "acceptance_reviewer",
    }


@pytest.mark.parametrize(
    ("backend_id", "transport", "version"),
    (
        ("", "ACP", "0.131.0"),
        ("codex-cli", " ", "0.131.0"),
        ("codex-cli", "ACP", "\t"),
    ),
)
def test_backend_rejects_empty_or_whitespace_identity_values(
    backend_id: str, transport: str, version: str
) -> None:
    with pytest.raises(ValueError):
        AgentBackend(backend_id, transport, version)


@pytest.mark.parametrize(
    ("backend_id", "transport", "version"),
    ((1, "ACP", "0.131.0"), ("codex-cli", 1, "0.131.0"), ("codex-cli", "ACP", 1)),
)
def test_backend_rejects_non_string_identity_values(
    backend_id: object, transport: object, version: object
) -> None:
    with pytest.raises(TypeError):
        AgentBackend(backend_id, transport, version)  # type: ignore[arg-type]


@pytest.mark.parametrize("instance_id", ("", " ", 1, None))
def test_instance_rejects_empty_or_non_string_instance_id(instance_id: object) -> None:
    backend = AgentBackend("codex-cli", "ACP", "0.131.0")

    with pytest.raises((TypeError, ValueError)):
        AgentInstance(instance_id, backend, AgentRole.LEADER, "acp_1")  # type: ignore[arg-type]


@pytest.mark.parametrize("session_id", ("", " ", 1, None))
def test_instance_rejects_empty_or_non_string_acp_session_id(session_id: object) -> None:
    backend = AgentBackend("codex-cli", "ACP", "0.131.0")

    with pytest.raises((TypeError, ValueError)):
        AgentInstance("agt_1", backend, AgentRole.LEADER, session_id)  # type: ignore[arg-type]


@pytest.mark.parametrize("backend", ("codex-cli", None))
def test_instance_requires_agent_backend(backend: object) -> None:
    with pytest.raises(TypeError):
        AgentInstance("agt_1", backend, AgentRole.LEADER, "acp_1")  # type: ignore[arg-type]


@pytest.mark.parametrize("role", ("leader", None))
def test_instance_requires_agent_role(role: object) -> None:
    backend = AgentBackend("codex-cli", "ACP", "0.131.0")

    with pytest.raises(TypeError):
        AgentInstance("agt_1", backend, role, "acp_1")  # type: ignore[arg-type]
