"""Task 36 Step 1-3 (deterministic) — Golden acceptance report contract.

This is the closed validator for the real four-Worker Golden Product Mission's
final report. It does NOT run a Mission; it only gates what a completed report
must contain before R7 can be marked PASS. The live run (Steps 4-6) is a
separate, explicitly human-authorized step.
"""
from __future__ import annotations

import pytest

from agentdeck.application.golden_acceptance import (
    GOLDEN_REQUIRED_FIELDS,
    GoldenGateError,
    validate_golden_report,
)


def complete_report() -> dict:
    return {
        "frozen_commit": "da8d7a8c30c27fc81ad1c9b26182b126f4990b01",
        "authority_digest": "sha256:" + "e" * 64,
        "leader_backend": "codex-cli",
        "worker_backends": ["codex-cli", "claude-cli", "codex-cli", "claude-cli"],
        "agent_instance_ids": ["impl", "review", "revise", "accept"],
        "acp_session_ids": ["ses1", "ses2", "ses3", "ses4"],
        "build_evidence": {"ok": True},
        "test_evidence": {"passed": 12, "failed": 0},
        "desktop_screenshot_hash": "sha256:" + "a" * 64,
        "mobile_screenshot_hash": "sha256:" + "b" * 64,
        "visual_diff": {"pixel_ratio": 0.0, "layout_shift_px": 0},
        "module_checks": {"hero-carousel": "passed"},
        "interaction_checks": {
            "navigation": "passed",
            "carousel": "passed",
            "responsive_menu": "passed",
        },
        "lineage": ["implementation", "review", "revision", "acceptance"],
        "findings_resolution": {"resolved": 2, "unresolved": 0},
        "sqlite_integrity": "ok",
        "permission_lineage": [{"operation": "write", "decision": "granted"}],
        "tmux_fidelity": {"missing": [], "duplicates": [], "mixed": []},
        "diagnostics": [],
        "exit_reentry": {"exited": True, "reentered": True},
        "final_result": "Local homepage reproduced and accepted.",
        "human_acceptance": {"accepted": True, "reason": "matches target"},
    }


def test_golden_report_requires_all_product_evidence() -> None:
    report = complete_report()
    validate_golden_report(report)
    for field in GOLDEN_REQUIRED_FIELDS:
        broken = complete_report()
        del broken[field]
        with pytest.raises(GoldenGateError, match=field):
            validate_golden_report(broken)


def test_four_workers_are_real_distinct_acp_sessions() -> None:
    report = complete_report()
    validate_golden_report(report)
    assert set(report["worker_backends"]) == {"codex-cli", "claude-cli"}
    assert len(set(report["agent_instance_ids"])) == 4
    assert len(set(report["acp_session_ids"])) == 4


def test_rejects_wrong_worker_backends() -> None:
    report = complete_report()
    report["worker_backends"] = ["codex-cli", "codex-cli", "codex-cli", "codex-cli"]
    with pytest.raises(GoldenGateError, match="worker_backends"):
        validate_golden_report(report)


def test_rejects_fewer_than_four_distinct_sessions() -> None:
    report = complete_report()
    report["acp_session_ids"] = ["ses1", "ses1", "ses2", "ses3"]
    with pytest.raises(GoldenGateError, match="acp_session_ids"):
        validate_golden_report(report)


def test_rejects_unaccepted_report() -> None:
    report = complete_report()
    report["human_acceptance"] = {"accepted": False, "reason": "layout drift"}
    with pytest.raises(GoldenGateError, match="human_acceptance"):
        validate_golden_report(report)


def test_rejects_broken_four_stage_lineage() -> None:
    report = complete_report()
    report["lineage"] = ["implementation", "acceptance"]
    with pytest.raises(GoldenGateError, match="lineage"):
        validate_golden_report(report)


def test_rejects_non_mapping_report() -> None:
    with pytest.raises(GoldenGateError, match="report"):
        validate_golden_report(["not", "a", "mapping"])
