"""Golden-run slice (e) core — the "accept" step over a captured machine report.

`finalize_golden_report` applies the human accept/reject decision to a JSON-safe
machine report (screenshot hashes keyed by "<w>x<h>") and validates it, so only
an accepted, complete run reaches R7 PASS.
"""
from __future__ import annotations

import pytest

from agentdeck.application.golden_acceptance import (
    GoldenGateError,
    finalize_golden_report,
    validate_golden_report,
)


def _machine_report(**overrides) -> dict:
    report = {
        "frozen_commit": "da8d7a8c",
        "authority_digest": "sha256:" + "e" * 64,
        "leader_backend": "codex-cli",
        "worker_backends": ["codex-cli", "claude-cli", "codex-cli", "claude-cli"],
        "agent_instance_ids": ["impl", "review", "revise", "accept"],
        "acp_session_ids": ["s1", "s2", "s3", "s4"],
        "build_evidence": {"ok": True},
        "test_evidence": {"passed": 12, "failed": 0},
        "screenshot_hashes": {
            "1440x1200": "sha256:" + "a" * 64,
            "390x844": "sha256:" + "b" * 64,
        },
        "visual_diff": {"pixel_ratio": 0.0, "layout_shift_px": 0},
        "module_checks": {"hero-carousel": True},
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
        "final_result": "Local homepage reproduced.",
    }
    report.update(overrides)
    return report


def test_accept_finalizes_a_validated_pass_report() -> None:
    report = finalize_golden_report(
        _machine_report(), accepted=True, reason="matches target"
    )
    validate_golden_report(report)  # must not raise
    assert report["human_acceptance"] == {
        "accepted": True, "reason": "matches target",
    }
    assert report["desktop_screenshot_hash"] == "sha256:" + "a" * 64
    assert report["mobile_screenshot_hash"] == "sha256:" + "b" * 64


def test_reject_fails_closed() -> None:
    with pytest.raises(GoldenGateError, match="human_acceptance"):
        finalize_golden_report(
            _machine_report(), accepted=False, reason="layout drift"
        )


def test_invalid_viewport_key_fails_closed() -> None:
    with pytest.raises(GoldenGateError, match="viewport"):
        finalize_golden_report(
            _machine_report(screenshot_hashes={"desktop": "sha256:" + "a" * 64}),
            accepted=True,
            reason="ok",
        )


def test_non_mapping_screenshot_hashes_fails_closed() -> None:
    with pytest.raises(GoldenGateError, match="screenshot_hashes"):
        finalize_golden_report(
            _machine_report(screenshot_hashes=[("1440x1200", "x")]),
            accepted=True,
            reason="ok",
        )
