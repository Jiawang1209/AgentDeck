"""Closed validator for the real Golden Product Mission acceptance report.

Pure ``application`` logic: it gates what a completed four-Worker Golden Mission
report must contain before R7 can be marked PASS. It runs no Mission, calls no
provider/ACP/tmux, and reads no state — it only validates a report mapping and
raises :class:`GoldenGateError` (message naming the offending field) on any gap.

Authority: design §18 (Real Golden Product Gate) and §23 (final completion).
"""
from __future__ import annotations

from collections.abc import Mapping

# The four fixed Golden stages, in order.
_REQUIRED_LINEAGE = ("implementation", "review", "revision", "acceptance")
_REQUIRED_WORKER_BACKENDS = frozenset({"codex-cli", "claude-cli"})
_WORKER_COUNT = 4

# Every top-level field a complete acceptance report must carry. Deleting any of
# these must fail the gate (design §18 PASS list + §23 completion definition).
GOLDEN_REQUIRED_FIELDS: tuple[str, ...] = (
    "frozen_commit",
    "authority_digest",
    "leader_backend",
    "worker_backends",
    "agent_instance_ids",
    "acp_session_ids",
    "build_evidence",
    "test_evidence",
    "desktop_screenshot_hash",
    "mobile_screenshot_hash",
    "visual_diff",
    "module_checks",
    "interaction_checks",
    "lineage",
    "findings_resolution",
    "sqlite_integrity",
    "permission_lineage",
    "tmux_fidelity",
    "diagnostics",
    "exit_reentry",
    "final_result",
    "human_acceptance",
)


class GoldenGateError(Exception):
    """Raised when a Golden acceptance report fails a required gate."""


def _distinct_count(value: object) -> int:
    if not isinstance(value, (list, tuple)):
        return -1
    try:
        return len(set(value))
    except TypeError:
        return -1


def validate_golden_report(report: object) -> None:
    """Validate a Golden acceptance report; raise ``GoldenGateError`` on any gap."""
    if not isinstance(report, Mapping):
        raise GoldenGateError("report must be a mapping")

    for field in GOLDEN_REQUIRED_FIELDS:
        if field not in report:
            raise GoldenGateError(f"missing required field: {field}")

    backends = report["worker_backends"]
    if (
        not isinstance(backends, (list, tuple))
        or len(backends) != _WORKER_COUNT
        or set(backends) != _REQUIRED_WORKER_BACKENDS
    ):
        raise GoldenGateError(
            "worker_backends must be four Instances across "
            "{codex-cli, claude-cli}"
        )

    if _distinct_count(report["agent_instance_ids"]) != _WORKER_COUNT:
        raise GoldenGateError(
            "agent_instance_ids must be four distinct Agent Instances"
        )

    if _distinct_count(report["acp_session_ids"]) != _WORKER_COUNT:
        raise GoldenGateError(
            "acp_session_ids must be four distinct real ACP sessions"
        )

    if tuple(report["lineage"]) != _REQUIRED_LINEAGE:
        raise GoldenGateError(
            "lineage must be the four stages "
            "implementation -> review -> revision -> acceptance"
        )

    if report["sqlite_integrity"] != "ok":
        raise GoldenGateError("sqlite_integrity must be ok")

    acceptance = report["human_acceptance"]
    if not isinstance(acceptance, Mapping) or acceptance.get("accepted") is not True:
        raise GoldenGateError(
            "human_acceptance must record an explicit accepted=true decision"
        )


__all__ = [
    "GOLDEN_REQUIRED_FIELDS",
    "GoldenGateError",
    "validate_golden_report",
]
