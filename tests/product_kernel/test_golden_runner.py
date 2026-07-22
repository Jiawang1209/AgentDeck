"""Golden-run orchestration slice (a) — a reusable src-level driver.

`GoldenRunner` productionizes the Task 33 fake-Golden harness: it composes the
same real Application graph and drives one goal through say -> configure ->
preview -> confirm -> a completed four-stage Mission, via injectable Leader and
Worker factories. Here it runs with the FAKE ACP boundaries (the only
substitution); swapping in the real ACP adapters is a later live slice.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from agentdeck.kernel.mission import MissionDraft
from agentdeck.kernel.permissions import PermissionProfile
from agentdeck.product.bootstrap import GoldenRunner

from .fakes import FakeACPLeader, FrozenClock, ScriptedACPWorker

NOW = datetime(2026, 7, 22, 9, 0, tzinfo=timezone.utc)
GOLDEN_GOAL = (
    Path(__file__).parent / "fixtures" / "golden_goal.txt"
).read_text(encoding="utf-8").strip()
_LEADER_BACKEND = "fake-acp-leader"
_LEADER_MODEL = "test-model"


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


def _acceptance_criteria(project_root: Path) -> tuple[str, ...]:
    return MissionDraft.coding_default(
        "drf_probe", "probe objective", str(project_root),
        _LEADER_BACKEND, _LEADER_MODEL, PermissionProfile.APPROVE_FOR_ME,
    ).acceptance_criteria


def _runner(project_root: Path) -> GoldenRunner:
    criteria = _acceptance_criteria(project_root)
    return GoldenRunner(
        project_root=project_root,
        leader=FakeACPLeader(
            str(project_root), leader_backend=_LEADER_BACKEND,
            leader_model=_LEADER_MODEL,
        ),
        worker_factory=lambda task: ScriptedACPWorker(task.name, criteria),
        available_leaders={_LEADER_BACKEND: (_LEADER_MODEL,)},
        clock=FrozenClock(NOW),
    )


@async_test
async def test_golden_runner_completes_the_four_stage_journey(tmp_path: Path) -> None:
    result = await _runner(tmp_path).run(
        goal=GOLDEN_GOAL,
        leader_backend=_LEADER_BACKEND,
        leader_model=_LEADER_MODEL,
        permission_profile=PermissionProfile.APPROVE_FOR_ME,
    )
    assert result.status == "completed"
    assert result.started_roles == (
        "implementer", "reviewer", "reviser", "acceptance_reviewer",
    )
    assert result.acceptance == "passed"
    assert result.handoff_count == 3
    assert result.sqlite_integrity == "ok"


@async_test
async def test_golden_runner_reports_four_distinct_agents_and_sessions(
    tmp_path: Path,
) -> None:
    result = await _runner(tmp_path).run(
        goal=GOLDEN_GOAL,
        leader_backend=_LEADER_BACKEND,
        leader_model=_LEADER_MODEL,
        permission_profile=PermissionProfile.APPROVE_FOR_ME,
    )
    assert len(result.agent_instance_ids) == 4
    assert len(set(result.agent_instance_ids)) == 4
    assert len(set(result.acp_session_ids)) == 4
    assert len(result.worker_backends) == 4
