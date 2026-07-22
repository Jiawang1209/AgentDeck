"""Task 35 — the preflight never mutates project source."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
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
    def inspect(self) -> EnvironmentReport:
        return EnvironmentReport(
            facts={key: f"{key}=ready" for key in ENVIRONMENT_FACT_FIELDS},
            blockers=(),
        )


def frozen_inputs() -> dict:
    return {
        "commit": "b" * 40,
        "leader_model": "test-model",
        "authority_digest": "sha256:" + "0" * 64,
        "target_manifest_hash": "sha256:" + "1" * 64,
        "permission_profile": PermissionProfile.APPROVE_FOR_ME,
    }


def tree_identity(root: Path, *, exclude: set[str]) -> frozenset[tuple[str, str]]:
    excluded = {root / part for part in exclude}
    identity: set[tuple[str, str]] = set()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(path == ex or ex in path.parents for ex in excluded):
            continue
        rel = path.relative_to(root).as_posix()
        digest = sha256(path.read_bytes()).hexdigest()
        identity.add((rel, digest))
    return frozenset(identity)


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


def test_preflight_is_read_only_for_project_source(
    preflight: PreflightService, project: Path
) -> None:
    before = tree_identity(project, exclude={".agentdeck/preflight"})
    result = preflight.run(**frozen_inputs())
    after = tree_identity(project, exclude={".agentdeck/preflight"})
    assert before == after
    assert set(result.facts) == PREFLIGHT_FACT_FIELDS


def test_preflight_evidence_is_the_only_new_path(
    preflight: PreflightService, project: Path
) -> None:
    before = {p.relative_to(project).as_posix() for p in project.rglob("*")}
    preflight.run(**frozen_inputs())
    after = {p.relative_to(project).as_posix() for p in project.rglob("*")}
    new_paths = after - before
    assert all(path.startswith(".agentdeck/preflight") for path in new_paths)
