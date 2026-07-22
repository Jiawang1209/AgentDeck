"""Golden-run slice (d) — fuse a real run with browser evidence into a report.

Composes the deterministic `GoldenRunner` (fake ACP boundaries) with the Task 34
`DeterministicBrowser` over the local reference homepage, then builds a report
that must pass `validate_golden_report`.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from functools import wraps
import json
from pathlib import Path

from agentdeck.adapters.browser import DeterministicBrowser
from agentdeck.application.golden_acceptance import validate_golden_report
from agentdeck.kernel.mission import MissionDraft
from agentdeck.kernel.permissions import PermissionProfile
from agentdeck.product.bootstrap import GoldenRunner, build_golden_report

from .fakes import FakeACPLeader, FrozenClock, ScriptedACPWorker

NOW = datetime(2026, 7, 22, 9, 0, tzinfo=timezone.utc)
GOLDEN_GOAL = (
    Path(__file__).parent / "fixtures" / "golden_goal.txt"
).read_text(encoding="utf-8").strip()
_FIXTURE = Path(__file__).parent / "fixtures" / "reference_homepage"
_LEADER_BACKEND = "fake-acp-leader"
_LEADER_MODEL = "test-model"


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


async def _run(project_root: Path):
    criteria = MissionDraft.coding_default(
        "drf_probe", "probe objective", str(project_root),
        _LEADER_BACKEND, _LEADER_MODEL, PermissionProfile.APPROVE_FOR_ME,
    ).acceptance_criteria
    runner = GoldenRunner(
        project_root=project_root,
        leader=FakeACPLeader(
            str(project_root), leader_backend=_LEADER_BACKEND,
            leader_model=_LEADER_MODEL,
        ),
        worker_factory=lambda task: ScriptedACPWorker(task.name, criteria),
        available_leaders={_LEADER_BACKEND: (_LEADER_MODEL,)},
        clock=FrozenClock(NOW),
    )
    return await runner.run(
        goal=GOLDEN_GOAL, leader_backend=_LEADER_BACKEND,
        leader_model=_LEADER_MODEL,
        permission_profile=PermissionProfile.APPROVE_FOR_ME,
    )


def _browser_report():
    manifest = json.loads((_FIXTURE / "target-manifest.json").read_text("utf-8"))
    return DeterministicBrowser().verify((_FIXTURE / "index.html").as_uri(), manifest)


@async_test
async def test_build_golden_report_from_run_and_browser(tmp_path: Path) -> None:
    run_result = await _run(tmp_path)
    report = build_golden_report(
        run_result=run_result,
        browser_report=_browser_report(),
        frozen_commit="da8d7a8c",
        authority_digest="sha256:" + "e" * 64,
        leader_backend=_LEADER_BACKEND,
        build_evidence={"ok": True},
        test_evidence={"passed": 1, "failed": 0},
        findings_resolution={"resolved": 0, "unresolved": 0},
        permission_lineage=[],
        tmux_fidelity={"missing": [], "duplicates": [], "mixed": []},
        diagnostics=[],
        exit_reentry={"exited": True, "reentered": True},
        final_result="Local homepage reproduced.",
        human_acceptance={"accepted": True, "reason": "matches target"},
    )
    validate_golden_report(report)  # must not raise
    assert report["worker_backends"] == list(run_result.worker_backends)
    assert report["agent_instance_ids"] == list(run_result.agent_instance_ids)
    assert set(report["interaction_checks"]) == {
        "navigation", "carousel", "responsive_menu",
    }
    assert report["desktop_screenshot_hash"].startswith("sha256:")
    assert report["mobile_screenshot_hash"].startswith("sha256:")
    assert report["sqlite_integrity"] == "ok"
