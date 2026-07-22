"""Task 35 fix — RealPreflightProbe must consume the real ACP readiness signal.

Regression for the defect where the probe read bare ``discover_tools()`` (which
can never report ACP availability without passive probes) and therefore always
produced a false ``codex_not_ready`` / ``claude_not_ready``. The probe must
instead classify real adapter facts (``classify_codex`` / ``classify_claude``
returning ``AdapterReadiness``), injected here as fakes for determinism.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agentdeck.adapters.adapter_readiness import AdapterReadiness, blocked_readiness
from agentdeck.adapters.discovery import ReadinessState, ToolDiscovery
from agentdeck.product.bootstrap import RealPreflightProbe

_SCHEMA = "91fae2120975b74d2d02184de2d8fed5f90770ce5009f308bbcaeec02dedcc23"


def _ready(backend_id: str, *, schema: str | None) -> AdapterReadiness:
    return AdapterReadiness(
        backend_id=backend_id,
        ready=True,
        command=("/abs/adapter",),
        version="0.131.0",
        diagnostic=None,
        cli_path=f"/abs/{backend_id.split('-')[0]}",
        cli_version="frozen",
        adapter_path="/abs/adapter",
        adapter_version="0.131.0",
        schema_digest=schema,
    )


def _tmux_discovery() -> dict[str, ToolDiscovery]:
    return {
        "tmux": ToolDiscovery(
            name="tmux",
            command="tmux",
            resolved_path="/usr/bin/tmux",
            version="tmux 3.7",
            authenticated=False,
            acp_available=False,
            readiness=ReadinessState.DISCOVERED,
            capabilities=(),
        )
    }


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return tmp_path


def test_probe_reports_acp_available_when_readiness_sources_are_ready(
    project: Path,
) -> None:
    probe = RealPreflightProbe(
        str(project),
        discovery=_tmux_discovery,
        codex_readiness=lambda: _ready("codex-cli", schema=_SCHEMA),
        claude_readiness=lambda: _ready("claude-cli", schema=None),
    )
    report = probe.inspect()
    assert report.facts["codex_acp"] == "acp_available"
    assert report.facts["claude_acp"] == "acp_available"
    assert report.facts["codex_app_server_schema"] == _SCHEMA
    assert "codex_not_ready" not in report.blockers
    assert "claude_not_ready" not in report.blockers


def test_probe_reports_blockers_when_readiness_sources_are_blocked(
    project: Path,
) -> None:
    probe = RealPreflightProbe(
        str(project),
        discovery=_tmux_discovery,
        codex_readiness=lambda: blocked_readiness(
            "codex-cli", "codex_app_server_missing"
        ),
        claude_readiness=lambda: blocked_readiness(
            "claude-cli", "claude_authentication_missing"
        ),
    )
    report = probe.inspect()
    assert report.facts["codex_acp"] == "unavailable"
    assert report.facts["claude_acp"] == "unavailable"
    assert "codex_not_ready" in report.blockers
    assert "claude_not_ready" in report.blockers


def test_probe_flags_missing_tmux(project: Path) -> None:
    probe = RealPreflightProbe(
        str(project),
        discovery=dict,  # empty discovery -> no tmux
        codex_readiness=lambda: _ready("codex-cli", schema=_SCHEMA),
        claude_readiness=lambda: _ready("claude-cli", schema=None),
    )
    report = probe.inspect()
    assert report.facts["tmux"] == "missing"
    assert "tmux_unavailable" in report.blockers
