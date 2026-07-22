"""Task 35 — read-only real-preflight service contract."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentdeck.application.preflight_service import (
    ENVIRONMENT_FACT_FIELDS,
    EnvironmentReport,
    PREFLIGHT_FACT_FIELDS,
    PreflightService,
)
from agentdeck.kernel.permissions import PermissionProfile


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 22, tzinfo=timezone.utc)


class _ReadyProbe:
    """A probe reporting a fully ready environment (no blockers)."""

    def inspect(self) -> EnvironmentReport:
        facts = {key: f"{key}=ready" for key in ENVIRONMENT_FACT_FIELDS}
        return EnvironmentReport(facts=facts, blockers=())


class _DegradedProbe:
    def __init__(self, blockers: tuple[str, ...]) -> None:
        self._blockers = blockers

    def inspect(self) -> EnvironmentReport:
        facts = {key: f"{key}=ready" for key in ENVIRONMENT_FACT_FIELDS}
        return EnvironmentReport(facts=facts, blockers=self._blockers)


def frozen_inputs() -> dict:
    return {
        "commit": "a" * 40,
        "leader_model": "test-model",
        "authority_digest": "sha256:" + "0" * 64,
        "target_manifest_hash": "sha256:" + "1" * 64,
        "permission_profile": PermissionProfile.APPROVE_FOR_ME,
    }


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".agentdeck").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# fixture\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def preflight(project: Path) -> PreflightService:
    return PreflightService(project_root=project, probe=_ReadyProbe(), clock=_FixedClock())


def test_preflight_requires_frozen_build_model_and_authority(
    preflight: PreflightService,
) -> None:
    result = preflight.run(commit="", leader_model="", authority_digest="")
    assert result.ready is False
    assert result.blockers == (
        "frozen_commit_missing",
        "leader_model_missing",
        "authority_digest_missing",
    )


def test_ready_preflight_reports_full_fact_surface(
    preflight: PreflightService,
) -> None:
    result = preflight.run(**frozen_inputs())
    assert result.ready is True
    assert result.blockers == ()
    assert set(result.facts) == PREFLIGHT_FACT_FIELDS
    assert result.facts["frozen_commit"] == "a" * 40
    assert result.facts["permission_profile"] == "approve_for_me"


def test_environment_blockers_flow_through_but_keep_input_order(
    project: Path,
) -> None:
    service = PreflightService(
        project_root=project,
        probe=_DegradedProbe(("tmux_unavailable",)),
        clock=_FixedClock(),
    )
    result = service.run(commit="", leader_model="m", authority_digest="d")
    assert result.ready is False
    assert result.blockers == ("frozen_commit_missing", "tmux_unavailable")


def test_malformed_environment_facts_fail_closed(project: Path) -> None:
    class _BadProbe:
        def inspect(self) -> EnvironmentReport:
            return EnvironmentReport(facts={"python_version": "3.12"}, blockers=())

    service = PreflightService(
        project_root=project, probe=_BadProbe(), clock=_FixedClock()
    )
    result = service.run(**frozen_inputs())
    assert result.ready is False
    assert "environment_facts_invalid" in result.blockers


def test_ready_preflight_writes_only_redacted_evidence_under_preflight_dir(
    preflight: PreflightService, project: Path
) -> None:
    result = preflight.run(**frozen_inputs())
    evidence = Path(result.evidence_path)
    assert evidence.parent == project / ".agentdeck" / "preflight"
    assert evidence.name == "a" * 40 + ".json"
    assert evidence.read_text(encoding="utf-8").strip().startswith("{")
