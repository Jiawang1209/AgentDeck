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


# The two frozen viewports the Golden browser evidence must cover.
_DESKTOP_VIEWPORT = (1440, 1200)
_MOBILE_VIEWPORT = (390, 844)


def assemble_golden_report(
    *,
    frozen_commit: str,
    authority_digest: str,
    leader_backend: str,
    worker_backends: object,
    agent_instance_ids: object,
    acp_session_ids: object,
    build_evidence: object,
    test_evidence: object,
    screenshot_hashes: Mapping[tuple[int, int], str],
    visual_diff: object,
    module_checks: object,
    interaction_checks: object,
    findings_resolution: object,
    sqlite_integrity: str,
    permission_lineage: object,
    tmux_fidelity: object,
    diagnostics: object,
    exit_reentry: object,
    final_result: str,
    human_acceptance: object,
) -> dict:
    """Assemble a validated Golden acceptance report from its evidence parts.

    ``screenshot_hashes`` maps each captured viewport to its content hash (the
    composition root derives it from a Task 34 ``BrowserEvidenceReport``); the
    two frozen viewports must both be present. The assembled report is validated
    with :func:`validate_golden_report` before return, so a partial or drifted
    report fails closed rather than reaching R7.
    """
    if not isinstance(screenshot_hashes, Mapping):
        raise GoldenGateError("screenshot_hashes must be a mapping of viewport hashes")
    desktop = screenshot_hashes.get(_DESKTOP_VIEWPORT)
    mobile = screenshot_hashes.get(_MOBILE_VIEWPORT)
    if not desktop:
        raise GoldenGateError(
            "desktop_screenshot_hash missing from browser evidence"
        )
    if not mobile:
        raise GoldenGateError(
            "mobile_screenshot_hash missing from browser evidence"
        )

    report = {
        "frozen_commit": frozen_commit,
        "authority_digest": authority_digest,
        "leader_backend": leader_backend,
        "worker_backends": worker_backends,
        "agent_instance_ids": agent_instance_ids,
        "acp_session_ids": acp_session_ids,
        "build_evidence": build_evidence,
        "test_evidence": test_evidence,
        "desktop_screenshot_hash": desktop,
        "mobile_screenshot_hash": mobile,
        "visual_diff": visual_diff,
        "module_checks": module_checks,
        "interaction_checks": interaction_checks,
        "lineage": list(_REQUIRED_LINEAGE),
        "findings_resolution": findings_resolution,
        "sqlite_integrity": sqlite_integrity,
        "permission_lineage": permission_lineage,
        "tmux_fidelity": tmux_fidelity,
        "diagnostics": diagnostics,
        "exit_reentry": exit_reentry,
        "final_result": final_result,
        "human_acceptance": human_acceptance,
    }
    validate_golden_report(report)
    return report


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


def finalize_golden_report(
    machine_report: object, *, accepted: bool, reason: str
) -> dict:
    """Apply an explicit human decision to a machine report and validate it.

    This is the "accept" step of the two-step Golden gate: the `golden run`
    command captures the machine evidence (everything except the human
    decision), and a human — after watching the Product journey — records
    accepted/rejected plus a reason here. `screenshot_hashes` arrive JSON-encoded
    as ``"<w>x<h>"`` string keys and are restored to `(w, h)` tuples. The result
    is validated by `assemble_golden_report`, so a rejected run (or any gap)
    fails closed and never reaches R7 PASS.
    """
    if not isinstance(machine_report, Mapping):
        raise GoldenGateError("machine_report must be a mapping")
    raw = dict(machine_report)
    hashes = raw.pop("screenshot_hashes", None)
    if not isinstance(hashes, Mapping):
        raise GoldenGateError("machine_report screenshot_hashes must be a mapping")
    screenshot_hashes: dict[tuple[int, int], str] = {}
    for key, value in hashes.items():
        parts = key.split("x") if isinstance(key, str) else ()
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            raise GoldenGateError(f"invalid screenshot viewport key: {key!r}")
        screenshot_hashes[(int(parts[0]), int(parts[1]))] = value
    return assemble_golden_report(
        **raw,
        screenshot_hashes=screenshot_hashes,
        human_acceptance={"accepted": bool(accepted), "reason": reason},
    )


__all__ = [
    "GOLDEN_REQUIRED_FIELDS",
    "GoldenGateError",
    "assemble_golden_report",
    "finalize_golden_report",
    "validate_golden_report",
]
