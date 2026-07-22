"""Deterministic Fake four-stage product journey: an end-to-end proof.

This is an integration proof, not a new behavior surface: it composes the
same real Application-layer graph `build_product_shell` composes (real
`SessionService`, `MissionService`, `ExecutionService`, `ApprovalService`,
`ProjectLifecycleService`, real `SQLiteStore`) with only two FAKE ACP
boundaries -- the Leader and the four per-stage Workers -- and drives one
natural-language goal through say -> configure -> preview -> confirm to a
completed Mission with a passed acceptance and three handoffs.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
import json
from pathlib import Path

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.approval_service import ApprovalService
from agentdeck.application.execution_resume import ExecutionResult
from agentdeck.application.execution_runtime import ForegroundExecutionRuntime
from agentdeck.application.leader_service import LeaderService
from agentdeck.application.mission_service import MissionService
from agentdeck.application.project_lifecycle_service import ProjectLifecycleService
from agentdeck.application.execution_service import ExecutionService
from agentdeck.application.support_service import SupportService
from agentdeck.application.session_service import SessionService
from agentdeck.kernel.execution import EvidenceKind
from agentdeck.kernel.mission import MissionDraft
from agentdeck.kernel.permissions import PermissionProfile, PermissionScope
from agentdeck.kernel.session import SessionState
from agentdeck.ports.leader import (
    AvailableAgent, LeaderRequest, ProjectContext, ResolvedLeaderModel,
)
from agentdeck.product.shell import validate_mission_preview

from .fakes import FakeACPLeader, FrozenClock, RecordingFidelityObserver, ScriptedACPWorker


NOW = datetime(2026, 7, 22, 9, 0, tzinfo=timezone.utc)
GOLDEN_GOAL = (
    Path(__file__).parent / "fixtures" / "golden_goal.txt"
).read_text(encoding="utf-8").strip()
_LEADER_BACKEND = "fake-acp-leader"
_LEADER_MODEL = "test-model"
AVAILABLE_LEADERS = {_LEADER_BACKEND: (_LEADER_MODEL,)}


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


def _seed_agent_instances(store: SQLiteStore, session_id: str, tasks, now: str) -> None:
    """Register the four distinct Agent Instances the confirmed Mission's
    Tasks name, satisfying `attempts.agent_instance_id`'s foreign key before
    any Attempt for that role starts. Real Agent Instance provisioning is a
    later product slice; this mirrors the exact seeding convention every
    other SQLite-backed execution test in this suite already uses."""
    connection = store._require_writer()
    for task in tasks:
        connection.execute(
            "INSERT INTO agent_instances VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                task.agent_instance_id, session_id, task.backend, "acp", "1",
                task.role.value, None, "active", now, now,
            ),
        )
    connection.commit()


@dataclass(frozen=True)
class PreviewHandle:
    preview_id: str
    content_hash: str
    acceptance_criteria: tuple[str, ...]


@dataclass(frozen=True)
class ConfirmResult:
    status: str
    started_roles: tuple[str, ...]
    acceptance: str
    handoff_count: int
    evidence_criteria: set[str]
    mission_id: str
    execution_result: ExecutionResult


class ProductJourneySession:
    """A thin conversational face over the real composed Application graph.

    `say`/`configure`/`current_preview` mirror the exact calls
    `ProductShell` itself makes on `SessionService`/`MissionService`;
    `confirm` mirrors `ProductShell._confirm_mission` plus the mission-child
    completion handling `ProductShell._await_child` now performs, but awaits
    the result directly instead of firing a background Task.

    `MissionService` requires an already-configured `SessionService` (it
    validates the Leader/model/permission setup authority at construction),
    so -- exactly like `ProductShell` itself -- it is only built once
    `configure()` has actually run.
    """

    def __init__(
        self, *, session: SessionService, execution: ExecutionService,
        lifecycle: ProjectLifecycleService, mission_factory,
    ) -> None:
        self._session = session
        self._execution = execution
        self._lifecycle = lifecycle
        self._mission_factory = mission_factory
        self._mission: MissionService | None = None

    def say(self, text: str) -> None:
        result = self._session.accept_text(text)
        if not result.accepted:
            raise AssertionError(f"goal was not accepted: {result}")

    def configure(self, *, leader: str, model: str, permission: str) -> None:
        result = self._session.configure(
            leader=leader, model=model, permission=permission.replace("-", "_"),
        )
        if not result.accepted:
            raise AssertionError(f"configure was rejected: {result.diagnostic}")
        self._mission = self._mission_factory()
        resumed = self._session.resume()
        if resumed.goal is not None:
            proposal = self._mission.propose(resumed.goal)
            if proposal.preview is None:
                raise AssertionError(f"mission proposal failed: {proposal.diagnostic}")

    def current_preview(self) -> PreviewHandle:
        if self._mission is None:
            raise AssertionError("no Mission Preview is available")
        preview = self._mission.current_preview()
        if preview is None:
            raise AssertionError("no Mission Preview is available")
        return PreviewHandle(
            preview_id=preview.preview_id, content_hash=preview.content_hash,
            acceptance_criteria=preview.draft.acceptance_criteria,
        )

    async def confirm(self, preview_id: str, content_hash: str) -> ConfirmResult:
        if self._mission is None:
            raise AssertionError("no Mission Preview is available to confirm")
        preview = self._mission.current_preview()
        if preview is None:
            raise AssertionError("no Mission Preview is available to confirm")
        draft = preview.draft
        confirmation = self._mission.confirm(preview_id, content_hash)
        if confirmation.mission is None:
            raise AssertionError(f"confirmation failed: {confirmation.diagnostic}")
        confirmed = confirmation.mission
        execution_result = await self._execution.run_confirmed_mission(
            session_id=self._session.current().session_id,
            confirmed=confirmed, draft=draft,
            permission_scope=PermissionScope.for_profile(draft.permission_profile),
        )
        if execution_result.diagnostic is None:
            self._lifecycle.complete_mission()
        role_by_task = {task.task_id: task.role.value for task in draft.tasks}
        started_roles = tuple(
            role_by_task[attempt.task_id] for attempt in execution_result.attempts
        )
        acceptance_evidence = next(
            (
                item for item in execution_result.evidence
                if item.kind is EvidenceKind.ACCEPTANCE_RESULT
            ),
            None,
        )
        evidence_criteria: set[str] = set()
        if acceptance_evidence is not None:
            payload = json.loads(acceptance_evidence.canonical_content)
            evidence_criteria = set(payload["evidence_by_criterion"])
        passed = execution_result.diagnostic is None
        return ConfirmResult(
            status="completed" if passed else "failed",
            started_roles=started_roles,
            acceptance="passed" if passed else "failed",
            handoff_count=len(execution_result.handoffs),
            evidence_criteria=evidence_criteria,
            mission_id=confirmed.mission_id,
            execution_result=execution_result,
        )


@dataclass
class ProductHarness:
    """Composes the same real graph `build_product_shell` composes, with a
    FAKE ACP Leader and FAKE per-stage ACP Workers as the only substitutions.
    """

    project_root: Path
    session_id: str = "ses_fake_product_journey"
    store: SQLiteStore | None = field(default=None, init=False)
    observer: RecordingFidelityObserver | None = field(default=None, init=False)

    def launch(self) -> ProductJourneySession:
        clock = FrozenClock(NOW)
        store = SQLiteStore.open(self.project_root, clock=clock)
        self.store = store
        observer = RecordingFidelityObserver(project_id=store._project_id)
        self.observer = observer
        session = SessionService(
            store=store, clock=clock, session_id=self.session_id,
            project_root=str(self.project_root), available_leaders=AVAILABLE_LEADERS,
        )
        probe = MissionDraft.coding_default(
            "drf_probe", "probe objective", str(self.project_root),
            _LEADER_BACKEND, _LEADER_MODEL, PermissionProfile.APPROVE_FOR_ME,
        )
        _seed_agent_instances(
            store, self.session_id, probe.tasks, clock.now().isoformat()
        )
        runtime = ForegroundExecutionRuntime()
        lifecycle = ProjectLifecycleService(
            store=store, clock=clock, session_id=self.session_id,
        )
        approval = ApprovalService(store=store, clock=clock, event_publisher=observer)
        execution = ExecutionService(
            store=store, clock=clock, approval_service=approval,
            worker_factory=lambda task: ScriptedACPWorker(
                task.name, probe.acceptance_criteria,
            ),
            runtime=runtime, lifecycle=lifecycle,
        )
        available_agents = tuple(
            AvailableAgent(
                instance_id=task.agent_instance_id, role=task.role,
                backend_id=task.backend, acp_route_id=task.acp_route,
            )
            for task in probe.tasks
        )
        request_template = LeaderRequest(
            user_goal="placeholder goal",
            project_context=ProjectContext(
                project_root=str(self.project_root),
                summary="fake product journey project",
            ),
            available_agents=available_agents,
            permission_ceiling=PermissionProfile.APPROVE_FOR_ME,
            resolved_model=ResolvedLeaderModel(
                backend_id=_LEADER_BACKEND, adapter_id="acp",
                model_id=_LEADER_MODEL, version="unreported",
            ),
        )
        leader = FakeACPLeader(
            str(self.project_root), leader_backend=_LEADER_BACKEND,
            leader_model=_LEADER_MODEL,
        )

        def mission_factory() -> MissionService:
            return MissionService(
                store=store, clock=clock, session_id=self.session_id,
                leader_service=LeaderService(leader),
                request_template=request_template,
                session_authority=session, preview_validator=validate_mission_preview,
            )

        return ProductJourneySession(
            session=session, execution=execution, lifecycle=lifecycle,
            mission_factory=mission_factory,
        )


def product_harness(tmp_path: Path) -> ProductHarness:
    return ProductHarness(project_root=tmp_path)


@async_test
async def test_fake_product_completes_exact_four_stage_journey(tmp_path: Path) -> None:
    harness = product_harness(tmp_path)
    session = harness.launch()
    session.say(GOLDEN_GOAL)
    session.configure(
        leader=_LEADER_BACKEND, model=_LEADER_MODEL, permission="approve-for-me",
    )
    preview = session.current_preview()

    result = await session.confirm(preview.preview_id, preview.content_hash)

    assert result.status == "completed"
    assert result.started_roles == (
        "implementer", "reviewer", "reviser", "acceptance_reviewer",
    )
    assert result.acceptance == "passed"
    assert result.handoff_count == 3
    assert result.evidence_criteria == set(preview.acceptance_criteria)
    assert harness.store.integrity_check() == "ok"
    report = harness.observer.fidelity_report()
    assert report.missing == ()
    assert report.duplicates == ()
    assert report.mixed == ()


@async_test
async def test_restored_completed_session_shows_four_separate_agent_bindings(
    tmp_path: Path,
) -> None:
    harness = product_harness(tmp_path)
    session = harness.launch()
    session.say(GOLDEN_GOAL)
    session.configure(
        leader=_LEADER_BACKEND, model=_LEADER_MODEL, permission="approve-for-me",
    )
    preview = session.current_preview()
    result = await session.confirm(preview.preview_id, preview.content_hash)
    assert result.status == "completed"

    # Exit: close the writer connection, exactly as a real process exit would.
    harness.store.close()

    # Recreate the composition root over the same project.
    reopened = SQLiteStore.open(harness.project_root, clock=FrozenClock(NOW))
    try:
        restored_session = SessionService(
            store=reopened, clock=FrozenClock(NOW), session_id=harness.session_id,
            project_root=str(harness.project_root), available_leaders=AVAILABLE_LEADERS,
        )
        assert restored_session.current().state is SessionState.COMPLETED

        trace = SupportService(store=reopened).trace(result.mission_id)
        assert trace.path[0] == result.mission_id

        bindings = tuple(
            (
                reopened.load_aggregate("attempts", attempt.attempt_id)["agent_instance_id"],
                reopened.load_aggregate("attempts", attempt.attempt_id)["acp_session_id"],
            )
            for attempt in result.execution_result.attempts
        )
        assert len(bindings) == 4
        agent_instance_ids = {agent_instance_id for agent_instance_id, _ in bindings}
        acp_session_ids = {acp_session_id for _, acp_session_id in bindings}
        assert len(agent_instance_ids) == 4
        assert len(acp_session_ids) == 4
    finally:
        reopened.close()
