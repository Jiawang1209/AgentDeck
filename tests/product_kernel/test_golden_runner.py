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

import pytest

from agentdeck.kernel.mission import MissionDraft
from agentdeck.kernel.permissions import PermissionProfile
from agentdeck.product.bootstrap import GoldenRunner

from .fakes import FakeACPLeader, FrozenClock, ScriptedACPWorker


class _EmptyProposalLeader:
    """A Leader whose proposal fails validation (empty payload)."""

    def propose_mission(self, request) -> dict:
        return {}


class _VersionCheckingLeader:
    """Mimics the real ACPLeader frozen-identity check: the request's resolved
    model version must equal the Leader's own version, else identity mismatch."""

    def __init__(self, project_root: str, version: str) -> None:
        self.version = version
        self._inner = FakeACPLeader(
            project_root, leader_backend=_LEADER_BACKEND, leader_model=_LEADER_MODEL,
            leader_version=version,
        )

    def propose_mission(self, request):
        if request.resolved_model.version != self.version:
            raise ValueError(
                "request does not match the frozen resolved Leader identity"
            )
        return self._inner.propose_mission(request)

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


@async_test
async def test_golden_runner_surfaces_proposal_diagnostic(tmp_path: Path) -> None:
    # A failed Leader proposal must carry its diagnostic into the error, not a
    # bare "proposal failed" (the gap that hid the first live run's real reason).
    runner = GoldenRunner(
        project_root=tmp_path,
        leader=_EmptyProposalLeader(),
        worker_factory=lambda task: ScriptedACPWorker(task.name, ()),
        available_leaders={_LEADER_BACKEND: (_LEADER_MODEL,)},
        clock=FrozenClock(NOW),
    )
    with pytest.raises(RuntimeError) as excinfo:
        await runner.run(
            goal=GOLDEN_GOAL, leader_backend=_LEADER_BACKEND,
            leader_model=_LEADER_MODEL,
            permission_profile=PermissionProfile.APPROVE_FOR_ME,
        )
    message = str(excinfo.value)
    assert "proposal failed:" in message
    assert message.strip() != "golden mission proposal failed:"


@async_test
async def test_golden_runner_request_carries_the_leader_version(
    tmp_path: Path,
) -> None:
    # The real ACPLeader rejects a request whose resolved-model version does not
    # equal the Leader's frozen version; the runner must use the Leader's actual
    # version, not a placeholder, or every real Leader proposal fails identity.
    criteria = MissionDraft.coding_default(
        "drf_probe", "probe objective", str(tmp_path),
        _LEADER_BACKEND, _LEADER_MODEL, PermissionProfile.APPROVE_FOR_ME,
    ).acceptance_criteria
    runner = GoldenRunner(
        project_root=tmp_path,
        leader=_VersionCheckingLeader(str(tmp_path), version="codex-cli 0.131.0"),
        worker_factory=lambda task: ScriptedACPWorker(task.name, criteria),
        available_leaders={_LEADER_BACKEND: (_LEADER_MODEL,)},
        clock=FrozenClock(NOW),
    )
    result = await runner.run(
        goal=GOLDEN_GOAL, leader_backend=_LEADER_BACKEND,
        leader_model=_LEADER_MODEL,
        permission_profile=PermissionProfile.APPROVE_FOR_ME,
    )
    assert result.status == "completed"
