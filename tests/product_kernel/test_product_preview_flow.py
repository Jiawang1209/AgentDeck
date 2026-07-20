from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.async_exit_coordinator import AsyncExitCoordinator
from agentdeck.application.exit_service import ExitService
from agentdeck.application.execution_runtime import ForegroundExecutionRuntime
from agentdeck.application.leader_service import LeaderService
from agentdeck.application.mission_service import MissionService
from agentdeck.application.project_lifecycle_service import ProjectLifecycleService
from agentdeck.application.recovery_service import RecoveryService
from agentdeck.application.session_service import SessionService
from agentdeck.product.bootstrap import build_product_shell
from agentdeck.product.shell import ProductShell, validate_mission_preview

from .fakes import FrozenClock
from .test_leader_contract import request, valid_proposal


NOW = datetime(2026, 7, 20, 8, 9, 10, tzinfo=timezone.utc)
AVAILABLE = {"codex-cli": ("native-default",)}


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


class EchoLeader:
    def __init__(self, root: Path) -> None:
        self.root = root

    def propose_mission(self, leader_request):
        payload = deepcopy(valid_proposal())
        payload["project_root"] = str(self.root)
        payload["objective"] = leader_request.user_goal
        return payload


class RecordingExecution:
    def __init__(self) -> None:
        self.calls = []
        self.loop_ids: set[int] = set()

    async def run_confirmed_mission(self, **facts):
        self.calls.append(facts)
        self.loop_ids.add(id(asyncio.get_running_loop()))


def _mission_service(
    root: Path, store: SQLiteStore, session: SessionService,
) -> MissionService:
    base = request()
    template = replace(
        base, project_context=replace(base.project_context, project_root=str(root))
    )
    return MissionService(
        store=store, clock=FrozenClock(NOW),
        session_id=session.current().session_id,
        leader_service=LeaderService(EchoLeader(root)), request_template=template,
        session_authority=session, preview_validator=validate_mission_preview,
    )


def _async_lines(*values: str):
    pending = iter(values)

    async def read(_prompt: str) -> str:
        await asyncio.sleep(0)
        try:
            return next(pending)
        except StopIteration:
            raise EOFError from None

    return read


def _shell(
    root: Path, store: SQLiteStore, session: SessionService, *,
    mission: MissionService | None, execution: RecordingExecution | None,
    lines: tuple[str, ...], output: list[str],
) -> ProductShell:
    clock = FrozenClock(NOW)
    runtime = ForegroundExecutionRuntime()
    lifecycle = ProjectLifecycleService(
        store=store, clock=clock, session_id=session.current().session_id
    )
    exit_service = ExitService(
        store=store, clock=clock, session_id=session.current().session_id,
        request_id_factory=iter(("xrt_" + "1" * 32,)).__next__,
    )
    return ProductShell(
        session_service=session,
        exit_coordinator=AsyncExitCoordinator(
            exit_service=exit_service, store=store, clock=clock,
            runtime=runtime, lifecycle=lifecycle,
            session_id=session.current().session_id,
        ),
        recovery_service=RecoveryService(
            store=store, clock=clock, session_id=session.current().session_id,
            recovery_run_id="restart_preview",
        ),
        lifecycle=lifecycle, mission_service=mission,
        execution_service=execution,
        resume_snapshot_loader=lambda: store.load_execution_resume(
            session.current().session_id
        ),
        available_leaders=AVAILABLE, read_line=_async_lines(*lines),
        write_line=output.append, close=store.close,
    )


@async_test
async def test_exact_fresh_confirmation_uses_same_singleton_child_guard(
    tmp_path: Path,
) -> None:
    output: list[str] = []
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    session = SessionService(
        store=store, clock=FrozenClock(NOW), session_id="ses_product",
        project_root=str(tmp_path), available_leaders=AVAILABLE,
    )
    session.configure(leader="codex-cli", model="native-default")
    mission = _mission_service(tmp_path, store, session)
    preview = mission.propose("Build an accessible page").preview
    assert preview is not None
    execution = RecordingExecution()
    shell = _shell(
        tmp_path, store, session, mission=mission, execution=execution,
        lines=(f"confirm {preview.preview_id} {preview.content_hash}", "/exit"),
        output=output,
    )

    await shell.run_async()

    transcript = "\n".join(output)
    assert len(execution.calls) == 1
    assert execution.calls[0]["resume_plan"] is None
    assert execution.loop_ids == {id(asyncio.get_running_loop())}
    assert preview.content_hash in transcript
    assert "Mission confirmed" in transcript
    assert "{" not in transcript


@async_test
async def test_fresh_confirmation_without_execution_adapter_is_closed(
    tmp_path: Path,
) -> None:
    output: list[str] = []
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    session = SessionService(
        store=store, clock=FrozenClock(NOW), session_id="ses_product",
        project_root=str(tmp_path), available_leaders=AVAILABLE,
    )
    session.configure(leader="codex-cli", model="native-default")
    mission = _mission_service(tmp_path, store, session)
    preview = mission.propose("Build an accessible page").preview
    assert preview is not None
    shell = _shell(
        tmp_path, store, session, mission=mission, execution=None,
        lines=(f"confirm {preview.preview_id} {preview.content_hash}", "/exit"),
        output=output,
    )

    await shell.run_async()

    reopened = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        assert reopened.count("attempts") == 0
    finally:
        reopened.close()
    assert "execution_adapter_unavailable" in "\n".join(output)


@async_test
async def test_open_goal_without_leader_is_retained_not_discarded(
    tmp_path: Path,
) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    session = SessionService(
        store=store, clock=FrozenClock(NOW), session_id="ses_product",
        project_root=str(tmp_path), available_leaders=AVAILABLE,
    )
    shell = _shell(
        tmp_path, store, session, mission=None, execution=None,
        lines=("Build a page", "/exit"), output=[],
    )

    await shell.run_async()

    reopened = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        assert reopened.load_aggregate(
            "product_sessions", "ses_product"
        )["pending_goal"] == "Build a page"
    finally:
        reopened.close()


@async_test
async def test_bootstrap_binds_only_injected_mission_factory(tmp_path: Path) -> None:
    calls: list[str] = []
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))

    def mission_factory(**facts):
        calls.append("mission")
        assert facts["store"] is store
        assert isinstance(facts["session_service"], SessionService)
        return None

    shell = build_product_shell(
        project_root=str(tmp_path), read_line=_async_lines("/exit"),
        write_line=lambda _: None, clock_factory=lambda: FrozenClock(NOW),
        discovery_factory=lambda: {}, config_factory=lambda **_: type(
            "Config", (), {"resolve": lambda self, key: type(
                "V", (), {"value": "approve-for-me"}
            )()}
        )(), store_factory=lambda *args, **kwargs: store,
        mission_service_factory=mission_factory,
    )

    assert calls == ["mission"]
    await shell.run_async()
