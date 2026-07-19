from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentdeck.adapters import discovery
from agentdeck.adapters.acp import ACPWorker
from agentdeck.adapters.acp_leader import ACPLeader
from agentdeck.adapters.codex_app_server_probe import (
    FROZEN_SERVER_VERSION,
    FROZEN_STABLE_SCHEMA_DIGEST,
)
from agentdeck.product import bootstrap

from .fakes import FrozenClock
from .fixtures.fake_acp_agent import FakeACPAgent


NOW = datetime(2026, 7, 20, 8, 0, 0, tzinfo=timezone.utc)


class TextImpostor(str):
    pass


def codex_facts(**changes: object) -> object:
    values: dict[str, object] = {
        "cli_path": "/tools/codex",
        "cli_version": "codex-cli 0.131.0",
        "app_server_available": True,
        "app_server_version": FROZEN_SERVER_VERSION,
        "bridge_path": "/tools/agentdeck-codex-acp",
        "schema_digest": FROZEN_STABLE_SCHEMA_DIGEST,
    }
    values.update(changes)
    return discovery.CodexAdapterFacts(**values)


def claude_facts(**changes: object) -> object:
    values: dict[str, object] = {
        "cli_path": "/tools/claude",
        "cli_version": "claude-cli 2.1.211",
        "authenticated": True,
        "adapter_path": "/tools/claude-agent-acp",
        "adapter_version": "claude-agent-acp 0.58.1",
    }
    values.update(changes)
    return discovery.ClaudeAdapterFacts(**values)


@pytest.mark.parametrize(
    ("changes", "code"),
    (
        ({"cli_path": None}, "codex_cli_missing"),
        ({"app_server_available": False}, "codex_app_server_missing"),
        ({"bridge_path": None}, "codex_acp_bridge_missing"),
        ({"bridge_path": "/tools/not-the-bridge"}, "codex_acp_bridge_missing"),
        ({"app_server_version": "0.130.0"}, "codex_app_server_version_drift"),
        ({"schema_digest": "0" * 64}, "codex_app_server_schema_drift"),
    ),
)
def test_codex_ready_requires_cli_app_server_bridge_and_frozen_schema(
    changes: dict[str, object], code: str,
) -> None:
    result = discovery.classify_codex(codex_facts(**changes))

    assert result.ready is False
    assert result.command is None
    assert result.diagnostic is not None
    assert result.diagnostic.code == code
    assert result.fallbacks == ()


def test_codex_ready_uses_only_the_task25_bridge_argv() -> None:
    result = discovery.classify_codex(codex_facts())

    assert result.ready is True
    assert result.command == ("agentdeck-codex-acp",)
    assert result.version == FROZEN_SERVER_VERSION
    assert result.diagnostic is None
    assert result.fallbacks == ()


@pytest.mark.parametrize(
    ("changes", "code"),
    (
        ({"cli_path": None}, "claude_cli_missing"),
        ({"authenticated": False}, "claude_authentication_missing"),
        ({"adapter_path": None}, "claude_acp_missing"),
        ({"adapter_path": "/tools/not-claude-acp"}, "claude_acp_missing"),
        ({"adapter_version": None}, "claude_acp_missing"),
    ),
)
def test_claude_ready_requires_cli_login_and_verified_claude_agent_acp(
    changes: dict[str, object], code: str,
) -> None:
    result = discovery.classify_claude(claude_facts(**changes))

    assert result.ready is False
    assert result.command is None
    assert result.diagnostic is not None
    assert result.diagnostic.code == code
    assert result.fallbacks == ()


def test_claude_ready_uses_only_verified_claude_agent_acp_argv() -> None:
    result = discovery.classify_claude(claude_facts())

    assert result.ready is True
    assert result.command == ("claude-agent-acp",)
    assert result.version == "claude-cli 2.1.211"
    assert result.diagnostic is None
    assert result.fallbacks == ()


@pytest.mark.parametrize(
    "facts",
    (
        codex_facts(cli_path="codex"),
        codex_facts(app_server_available=1),
        claude_facts(adapter_path="claude-agent-acp"),
        claude_facts(adapter_version=1),
        claude_facts(cli_version="x" * 4097),
    ),
)
def test_passive_readiness_rejects_nonexact_or_unbounded_facts(facts: object) -> None:
    result = (
        discovery.classify_codex(facts)
        if type(facts) is discovery.CodexAdapterFacts
        else discovery.classify_claude(facts)
    )

    assert result.ready is False
    assert result.command is None
    assert result.diagnostic is not None


@pytest.mark.parametrize(
    ("factory", "field", "basename"),
    (
        (codex_facts, "cli_path", "codex"),
        (codex_facts, "bridge_path", "agentdeck-codex-acp"),
        (claude_facts, "cli_path", "claude"),
        (claude_facts, "adapter_path", "claude-agent-acp"),
    ),
)
@pytest.mark.parametrize(
    "path_factory",
    (
        lambda name: "/" + "x" * 5000 + "/" + name,
        lambda name: "/tools/\x00/" + name,
        lambda name: "/tools/\n/" + name,
        lambda name: "/tools/\x7f/" + name,
    ),
)
def test_every_adapter_path_rejects_oversize_nul_or_control(
    factory: object, field: str, basename: str, path_factory: object,
) -> None:
    facts = factory(**{field: path_factory(basename)})
    result = (
        discovery.classify_codex(facts)
        if type(facts) is discovery.CodexAdapterFacts
        else discovery.classify_claude(facts)
    )

    assert result.ready is False
    assert result.command is None


@pytest.mark.parametrize(
    "changes",
    (
        {"cli_version": " claude-cli 2.1.211"},
        {"cli_version": "claude-cli 2.1.211\n"},
        {"cli_version": "claude-cli forged"},
        {"adapter_version": "claude-agent-acp 0.58.1\x00"},
        {"adapter_version": "claude-agent-acp forged"},
        {"adapter_version": "claude-agent-acp " + "1" * 5000},
    ),
)
def test_claude_versions_are_strict_bounded_canonical_facts(
    changes: dict[str, object],
) -> None:
    result = discovery.classify_claude(claude_facts(**changes))

    assert result.ready is False
    assert result.command is None


@pytest.mark.parametrize(
    "changes",
    (
        {"cli_version": TextImpostor("codex-cli 0.131.0")},
        {"app_server_version": TextImpostor(FROZEN_SERVER_VERSION)},
        {"schema_digest": TextImpostor(FROZEN_STABLE_SCHEMA_DIGEST)},
    ),
)
def test_codex_frozen_version_and_schema_facts_reject_string_impostors(
    changes: dict[str, object],
) -> None:
    result = discovery.classify_codex(codex_facts(**changes))

    assert result.ready is False
    assert result.command is None


class RecordingTransport:
    starts: list[tuple[object, ...]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.starts.append((*args, kwargs))


def test_readiness_and_composition_start_zero_subprocesses(tmp_path: Path) -> None:
    process_starts: list[tuple[tuple[str, ...], str]] = []

    def process_factory(command: tuple[str, ...], project_root: str) -> object:
        process_starts.append((command, project_root))
        return FakeACPAgent("success")

    ready = {
        "codex-cli": discovery.classify_codex(codex_facts()),
        "claude-cli": discovery.classify_claude(claude_facts()),
    }
    composition = bootstrap.build_acp_adapter_composition(
        readiness=ready,
        project_root=str(tmp_path),
        clock=FrozenClock(NOW),
        worker_agent_factory=process_factory,
        transport_factory=RecordingTransport,
    )

    assert process_starts == []
    assert RecordingTransport.starts == []
    assert "pty" not in repr(ready).lower()

    codex_leader = composition.leader("codex-cli", model="native-default")
    claude_leader = composition.leader("claude-cli", model="native-default")
    assert type(codex_leader) is ACPLeader
    assert type(claude_leader) is ACPLeader
    assert codex_leader.command == ("agentdeck-codex-acp",)
    assert claude_leader.command == ("claude-agent-acp",)
    assert process_starts == []
    assert RecordingTransport.starts == []


def test_each_worker_instance_gets_its_own_exact_acp_process(tmp_path: Path) -> None:
    process_starts: list[tuple[tuple[str, ...], str, object]] = []

    def process_factory(command: tuple[str, ...], project_root: str) -> object:
        agent = FakeACPAgent("success")
        process_starts.append((command, project_root, agent))
        return agent

    composition = bootstrap.build_acp_adapter_composition(
        readiness={
            "codex-cli": discovery.classify_codex(codex_facts()),
            "claude-cli": discovery.classify_claude(claude_facts()),
        },
        project_root=str(tmp_path),
        clock=FrozenClock(NOW),
        worker_agent_factory=process_factory,
        transport_factory=RecordingTransport,
    )

    codex_worker = composition.worker("codex-cli")
    claude_worker = composition.worker("claude-cli")

    assert type(codex_worker) is ACPWorker
    assert type(claude_worker) is ACPWorker
    assert codex_worker is not claude_worker
    assert process_starts == [
        (("agentdeck-codex-acp",), str(tmp_path), process_starts[0][2]),
        (("claude-agent-acp",), str(tmp_path), process_starts[1][2]),
    ]
    assert process_starts[0][2] is not process_starts[1][2]


def test_unready_adapter_cannot_be_composed(tmp_path: Path) -> None:
    composition = bootstrap.build_acp_adapter_composition(
        readiness={
            "claude-cli": discovery.classify_claude(
                claude_facts(authenticated=False)
            ),
        },
        project_root=str(tmp_path),
        clock=FrozenClock(NOW),
        worker_agent_factory=lambda *_: pytest.fail("must not start"),
        transport_factory=RecordingTransport,
    )

    with pytest.raises(ValueError, match="not ready"):
        composition.leader("claude-cli", model="native-default")
    with pytest.raises(ValueError, match="not ready"):
        composition.worker("claude-cli")


def test_composition_rejects_forged_ready_or_fallback_command(tmp_path: Path) -> None:
    starts: list[object] = []
    forged = discovery.AdapterReadiness(
        backend_id="claude-cli", ready=True, command=("pty", "claude"),
        version="2.1.211", diagnostic=None, fallbacks=("pty",),
    )
    composition = bootstrap.build_acp_adapter_composition(
        readiness={"claude-cli": forged}, project_root=str(tmp_path),
        clock=FrozenClock(NOW),
        worker_agent_factory=lambda *args: starts.append(args),
        transport_factory=RecordingTransport,
    )

    with pytest.raises(ValueError, match="not ready"):
        composition.leader("claude-cli", model="native-default")
    with pytest.raises(ValueError, match="not ready"):
        composition.worker("claude-cli")
    assert starts == []


@pytest.mark.parametrize(
    "changes",
    (
        {"ready": 1},
        {"version": "forged"},
        {"fallbacks": []},
        {"diagnostic": discovery.AdapterDiagnostic("impostor")},
    ),
)
def test_composition_rejects_malformed_exact_command_readiness_without_starting(
    tmp_path: Path, changes: dict[str, object],
) -> None:
    starts: list[object] = []
    values: dict[str, object] = {
        "backend_id": "claude-cli", "ready": True,
        "command": ("claude-agent-acp",), "version": "claude-cli 2.1.211",
        "diagnostic": None, "fallbacks": (),
    }
    values.update(changes)
    readiness = discovery.AdapterReadiness(**values)
    composition = bootstrap.build_acp_adapter_composition(
        readiness={"claude-cli": readiness}, project_root=str(tmp_path),
        clock=FrozenClock(NOW),
        worker_agent_factory=lambda *args: starts.append(args),
        transport_factory=RecordingTransport,
    )

    with pytest.raises(ValueError, match="not ready"):
        composition.leader("claude-cli", model="native-default")
    with pytest.raises(ValueError, match="not ready"):
        composition.worker("claude-cli")
    assert starts == []
