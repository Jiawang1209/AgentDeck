"""Golden-run slice (e) — CLI dispatch, --real refusal, and accept gate.

Deterministic coverage only: `golden run` refuses without --real (its live body
is the separately authorized live gate), and `golden accept` finalizes a
captured machine report into PASS or fails closed on rejection. No live Mission,
provider, ACP, or tmux is touched here.
"""
from __future__ import annotations

import json
from pathlib import Path

from agentdeck.entrypoint import main


def _machine_report() -> dict:
    return {
        "frozen_commit": "da8d7a8c",
        "authority_digest": "sha256:" + "e" * 64,
        "leader_backend": "codex-cli",
        "worker_backends": ["codex-cli", "claude-cli", "codex-cli", "claude-cli"],
        "agent_instance_ids": ["impl", "review", "revise", "accept"],
        "acp_session_ids": ["s1", "s2", "s3", "s4"],
        "build_evidence": {"mission_status": "completed"},
        "test_evidence": {"acceptance": "passed"},
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
        "findings_resolution": {"handoffs": 3},
        "sqlite_integrity": "ok",
        "permission_lineage": [],
        "tmux_fidelity": {"missing": [], "duplicates": [], "mixed": []},
        "diagnostics": [],
        "exit_reentry": {"exited": True, "reentered": True},
        "final_result": "Golden Mission msn_1 completed.",
    }


def test_golden_run_refuses_without_real() -> None:
    assert main(["_product", "golden", "run"]) == 2


def test_golden_requires_a_sub_action() -> None:
    assert main(["_product", "golden"]) == 2


def test_golden_accept_finalizes_a_valid_report_to_pass(tmp_path: Path) -> None:
    report = tmp_path / "msn_1.json"
    report.write_text(json.dumps(_machine_report()), encoding="utf-8")
    code = main([
        "_product", "golden", "accept",
        "--report", str(report), "--accept", "--reason", "matches target",
    ])
    assert code == 0
    accepted = tmp_path / "msn_1.accepted.json"
    assert accepted.is_file()
    final = json.loads(accepted.read_text(encoding="utf-8"))
    assert final["human_acceptance"] == {
        "accepted": True, "reason": "matches target",
    }


def test_golden_accept_rejection_fails_closed(tmp_path: Path) -> None:
    report = tmp_path / "msn_2.json"
    report.write_text(json.dumps(_machine_report()), encoding="utf-8")
    # No --accept -> a rejected decision must not produce a PASS report.
    code = main([
        "_product", "golden", "accept",
        "--report", str(report), "--reason", "layout drift",
    ])
    assert code == 1
    assert not (tmp_path / "msn_2.accepted.json").exists()


def test_golden_accept_missing_report_file(tmp_path: Path) -> None:
    assert main([
        "_product", "golden", "accept",
        "--report", str(tmp_path / "nope.json"), "--accept", "--reason", "x",
    ]) == 2
