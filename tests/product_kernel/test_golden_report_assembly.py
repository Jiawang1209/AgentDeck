"""Golden report assembler — glue between browser evidence and the R7 contract.

Pure assembly: it maps captured evidence parts (including Task 34 browser
screenshot hashes) into a report that must pass `validate_golden_report`, and
fails closed on any gap. It runs no Mission and touches no adapters.
"""
from __future__ import annotations

import pytest

from agentdeck.application.golden_acceptance import (
    GoldenGateError,
    assemble_golden_report,
    validate_golden_report,
)

_DESKTOP = (1440, 1200)
_MOBILE = (390, 844)


def _parts(**overrides) -> dict:
    parts = {
        "frozen_commit": "da8d7a8c",
        "authority_digest": "sha256:" + "e" * 64,
        "leader_backend": "codex-cli",
        "worker_backends": ["codex-cli", "claude-cli", "codex-cli", "claude-cli"],
        "agent_instance_ids": ["impl", "review", "revise", "accept"],
        "acp_session_ids": ["s1", "s2", "s3", "s4"],
        "build_evidence": {"ok": True},
        "test_evidence": {"passed": 12, "failed": 0},
        "screenshot_hashes": {
            _DESKTOP: "sha256:" + "a" * 64,
            _MOBILE: "sha256:" + "b" * 64,
        },
        "visual_diff": {"pixel_ratio": 0.0, "layout_shift_px": 0},
        "module_checks": {"hero-carousel": "passed"},
        "interaction_checks": {
            "navigation": "passed",
            "carousel": "passed",
            "responsive_menu": "passed",
        },
        "findings_resolution": {"resolved": 2, "unresolved": 0},
        "sqlite_integrity": "ok",
        "permission_lineage": [{"operation": "write", "decision": "granted"}],
        "tmux_fidelity": {"missing": [], "duplicates": [], "mixed": []},
        "diagnostics": [],
        "exit_reentry": {"exited": True, "reentered": True},
        "final_result": "Local homepage reproduced and accepted.",
        "human_acceptance": {"accepted": True, "reason": "matches target"},
    }
    parts.update(overrides)
    return parts


def test_assembles_a_valid_report_with_both_viewport_hashes() -> None:
    report = assemble_golden_report(**_parts())
    validate_golden_report(report)  # must not raise
    assert report["desktop_screenshot_hash"] == "sha256:" + "a" * 64
    assert report["mobile_screenshot_hash"] == "sha256:" + "b" * 64
    assert report["lineage"] == ["implementation", "review", "revision", "acceptance"]
    assert report["interaction_checks"]["carousel"] == "passed"


def test_missing_mobile_screenshot_fails_closed() -> None:
    with pytest.raises(GoldenGateError, match="mobile_screenshot_hash"):
        assemble_golden_report(
            **_parts(screenshot_hashes={_DESKTOP: "sha256:" + "a" * 64})
        )


def test_missing_desktop_screenshot_fails_closed() -> None:
    with pytest.raises(GoldenGateError, match="desktop_screenshot_hash"):
        assemble_golden_report(
            **_parts(screenshot_hashes={_MOBILE: "sha256:" + "b" * 64})
        )


def test_assembled_report_still_enforces_the_full_contract() -> None:
    # An unaccepted human decision must fail even though every part was supplied.
    with pytest.raises(GoldenGateError, match="human_acceptance"):
        assemble_golden_report(
            **_parts(human_acceptance={"accepted": False, "reason": "drift"})
        )


def test_non_mapping_screenshot_hashes_rejected() -> None:
    with pytest.raises(GoldenGateError, match="screenshot_hashes"):
        assemble_golden_report(**_parts(screenshot_hashes=[("desktop", "x")]))
