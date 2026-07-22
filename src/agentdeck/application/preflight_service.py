"""Read-only real-preflight service (Task 35).

The preflight proves that a *frozen* candidate can attempt a real Golden Mission
without mutating project source, installing software, authenticating accounts,
selecting a fallback, or generating source. It records only redacted, stable
facts and hashes.

Architecture: this is an ``application`` service. It depends only on a
:class:`PreflightProbe` **port** (a Protocol) for environment readiness facts;
the real probe that inspects CLIs/ACP/tmux/SQLite is composed at the product
composition root, never imported here (the application layer must not import
adapters). The deterministic tests inject a fake probe.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Protocol

from agentdeck.kernel.permissions import PermissionProfile

# Fact fields supplied by the frozen human authorization inputs.
_INPUT_FACT_FIELDS = frozenset(
    {
        "frozen_commit",
        "permission_profile",
        "leader_model",
        "target_manifest_hash",
        "authority_digest",
    }
)

# Fact fields supplied by the read-only environment probe.
ENVIRONMENT_FACT_FIELDS = frozenset(
    {
        "python_version",
        "python_executable",
        "codex_cli",
        "claude_cli",
        "codex_acp",
        "claude_acp",
        "codex_app_server_schema",
        "tmux",
        "sqlite",
    }
)

# The complete, fixed fact surface of one preflight result.
PREFLIGHT_FACT_FIELDS = frozenset(_INPUT_FACT_FIELDS | ENVIRONMENT_FACT_FIELDS)


@dataclass(frozen=True)
class EnvironmentReport:
    """Read-only environment facts plus any environment readiness blockers."""

    facts: Mapping[str, object]
    blockers: tuple[str, ...] = ()


class PreflightProbe(Protocol):
    """Port: inspect the local environment read-only and report facts."""

    def inspect(self) -> EnvironmentReport: ...


@dataclass(frozen=True)
class PreflightResult:
    ready: bool
    blockers: tuple[str, ...]
    facts: Mapping[str, object]
    evidence_path: str | None = None


@dataclass(frozen=True)
class PreflightService:
    """Compose frozen authorization inputs with read-only environment facts."""

    project_root: Path
    probe: PreflightProbe
    clock: object  # ports.clock.Clock; kept structural to avoid a hard import

    def run(
        self,
        *,
        commit: str,
        leader_model: str,
        authority_digest: str,
        target_manifest_hash: str = "",
        permission_profile: PermissionProfile = PermissionProfile.APPROVE_FOR_ME,
    ) -> PreflightResult:
        if type(permission_profile) is not PermissionProfile:
            raise TypeError("permission_profile must be a PermissionProfile")

        blockers: list[str] = []
        if not commit:
            blockers.append("frozen_commit_missing")
        if not leader_model:
            blockers.append("leader_model_missing")
        if not authority_digest:
            blockers.append("authority_digest_missing")

        report = self.probe.inspect()
        environment = dict(report.facts)
        if set(environment) != ENVIRONMENT_FACT_FIELDS:
            blockers.append("environment_facts_invalid")
        blockers.extend(report.blockers)

        facts: dict[str, object] = {
            "frozen_commit": commit,
            "permission_profile": str(permission_profile),
            "leader_model": leader_model,
            "target_manifest_hash": target_manifest_hash,
            "authority_digest": authority_digest,
        }
        for key in ENVIRONMENT_FACT_FIELDS:
            facts[key] = environment.get(key)

        ready = not blockers
        evidence_path = self._write_evidence(commit, facts) if ready else None
        return PreflightResult(
            ready=ready,
            blockers=tuple(blockers),
            facts=facts,
            evidence_path=evidence_path,
        )

    def _write_evidence(self, commit: str, facts: Mapping[str, object]) -> str:
        directory = Path(self.project_root) / ".agentdeck" / "preflight"
        directory.mkdir(parents=True, exist_ok=True)
        captured_at = self.clock.now()
        if not isinstance(captured_at, datetime):
            raise TypeError("clock.now() must return a datetime")
        payload = {
            "captured_at": captured_at.isoformat(),
            "facts": {key: facts[key] for key in sorted(facts)},
        }
        path = directory / f"{commit}.json"
        path.write_text(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return str(path)


__all__ = [
    "PREFLIGHT_FACT_FIELDS",
    "ENVIRONMENT_FACT_FIELDS",
    "EnvironmentReport",
    "PreflightProbe",
    "PreflightResult",
    "PreflightService",
]
