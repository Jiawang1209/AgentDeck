from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import pytest

from agentdeck.adapters.discovery import ReadinessState, ToolDiscovery
from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.exit_service import ExitService
from agentdeck.application.leader_service import LeaderService
from agentdeck.application.mission_service import MissionPreviewView, MissionService
from agentdeck.application.session_service import SessionService
from agentdeck.kernel.session import ExitRequest
from agentdeck.product.bootstrap import build_product_shell
from agentdeck.product.shell import ProductShell, validate_mission_preview

from .fakes import FrozenClock
from .test_leader_contract import request, valid_proposal


NOW = datetime(2026, 7, 19, 8, 9, 10, tzinfo=timezone.utc)
AVAILABLE = {"codex-cli": ("native-default",)}


class EchoLeader:
    def __init__(self, root: Path) -> None:
        self._root = root

    def propose_mission(self, leader_request):
        payload = deepcopy(valid_proposal())
        payload["project_root"] = str(self._root)
        payload["objective"] = leader_request.user_goal
        return payload


class CountingIds:
    def __init__(self, *values: str) -> None:
        self._values, self.calls = iter(values), 0

    def __iter__(self) -> CountingIds:
        return self

    def __next__(self) -> str:
        self.calls += 1
        return next(self._values)


class ReentryHarness:
    def __init__(
        self,
        root: Path,
        *,
        session_ids: Iterator[str],
        request_ids: Iterator[str],
    ) -> None:
        self.root = root
        self._session_ids = session_ids
        self._request_ids = request_ids
        self._pending: Iterator[object] = iter(())
        self.output: list[str] = []
        self.store: SQLiteStore | None = None
        self.session: SessionService | None = None
        self.exit: ExitService | None = None
        self.mission: MissionService | None = None
        self.shell = build_product_shell(
            project_root=str(root),
            read_line=self._read_line,
            write_line=self.output.append,
            clock_factory=lambda: FrozenClock(NOW),
            discovery_factory=_discovery,
            config_factory=_config,
            store_factory=self._store_factory,
            shell_factory=self._shell_factory,
            mission_service_factory=self._mission_factory,
            session_id_factory=self._session_ids.__next__,
            exit_request_id_factory=self._request_ids.__next__,
        )

    @property
    def session_id(self) -> str:
        assert self.session is not None
        return self.session.current().session_id

    def configure_and_preview(
        self, *, leader: str, model: str, permission: str, goal: str,
    ) -> MissionPreviewView:
        assert self.session is not None and self.store is not None
        retained = self.session.accept_text(goal)
        assert retained.accepted is True
        result = self.session.configure(
            leader=leader,
            model=model,
            permission=permission,
        )
        assert result.accepted is True
        self.mission = _mission_service(self.root, self.store, self.session)
        self.shell._mission = self.mission
        preview = self.mission.propose(goal).preview
        assert preview is not None
        return preview

    def configure(self) -> None:
        assert self.session is not None
        result = self.session.configure(
            leader="codex-cli",
            model="native-default",
            permission="approve_for_me",
        )
        assert result.accepted is True

    def seed_active_attempt(self) -> None:
        assert self.store is not None
        _seed_active_attempt(self.store, self.session_id)

    def seed_older_nonterminal_session(self) -> None:
        assert self.store is not None
        older = SessionService(
            store=self.store,
            clock=FrozenClock(NOW),
            session_id="ses_older",
            project_root=str(self.root),
            available_leaders=AVAILABLE,
        )
        assert older.configure(
            leader="codex-cli",
            model="native-default",
            permission="approve_for_me",
        ).accepted is True
        self.store._require_writer().execute(
            "UPDATE product_sessions SET created_at=?,updated_at=? "
            "WHERE session_id='ses_older'",
            ("2026-07-18T00:00:00+00:00", "2026-07-18T00:00:00+00:00"),
        )
        self.store._require_writer().commit()

    def prime_exit_request(self) -> tuple[str, str]:
        assert self.exit is not None
        request = self.exit.request_exit().request
        assert request is not None
        return request.request_id, request.attempt_hash

    def request_active_exit(self) -> ExitRequest:
        assert self.exit is not None
        request = self.exit.request_exit().request
        assert request is not None
        return request

    def run(self, values: list[object]) -> str:
        self._pending = iter(values)
        assert self.shell.run() == 0
        return "\n".join(self.output)

    def close_input(self) -> str:
        return self.run([EOFError()])

    @property
    def attempt_state(self) -> str:
        store = SQLiteStore.open(self.root, clock=FrozenClock(NOW))
        try:
            row = store.load_aggregate("attempts", "att_active")
            assert row is not None
            return row["state"]
        finally:
            store.close()

    def database_facts(self) -> tuple[tuple[object, ...], ...]:
        store, owned = self._projection_store()
        try:
            rows: list[tuple[object, ...]] = []
            for table in ("product_sessions", "attempts", "commands", "events"):
                rows.extend(store.connection.execute(
                    f"SELECT * FROM {table} ORDER BY 1"
                ).fetchall())
            return tuple(rows)
        finally:
            if owned:
                store.close()

    @property
    def configuration(self) -> tuple[str | None, ...]:
        if self.session is not None and self._store_is_open():
            self.session.resume()
            view = self.session.current()
            return (
                view.leader_backend,
                view.model,
                view.permission,
                view.pending_goal,
            )
        store = SQLiteStore.open(self.root, clock=FrozenClock(NOW))
        try:
            service = _open_session(store, self.root, iter(("ses_unused",)))
            view = service.current()
            return (
                view.leader_backend,
                view.model,
                view.permission,
                view.pending_goal,
            )
        finally:
            store.close()

    @property
    def preview_id(self) -> str | None:
        if self.mission is not None and self._store_is_open():
            preview = self.mission.current_preview()
            return None if preview is None else preview.preview_id
        store = SQLiteStore.open(self.root, clock=FrozenClock(NOW))
        try:
            service = _open_session(store, self.root, iter(("ses_unused",)))
            mission = _mission_service(self.root, store, service)
            preview = mission.current_preview()
            return None if preview is None else preview.preview_id
        finally:
            store.close()

    @property
    def pending_exit(self) -> ExitRequest | None:
        if self.exit is not None and self._store_is_open():
            return self.exit.request_exit().request
        store = SQLiteStore.open(self.root, clock=FrozenClock(NOW))
        try:
            service = _open_session(store, self.root, iter(("ses_unused",)))
            result = ExitService(
                store=store,
                clock=FrozenClock(NOW),
                session_id=service.current().session_id,
                request_id_factory=lambda: (_ for _ in ()).throw(
                    AssertionError("restored request must not allocate an identity")
                ),
            ).request_exit()
            return result.request
        finally:
            store.close()

    def _read_line(self, prompt: str) -> str:
        assert prompt == "agentdeck> "
        try:
            value = next(self._pending)
        except StopIteration:
            raise EOFError from None
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, str)
        return value

    def _store_factory(self, root: str, *, clock: FrozenClock) -> SQLiteStore:
        self.store = SQLiteStore.open(root, clock=clock)
        return self.store

    def _shell_factory(self, **values) -> ProductShell:
        self.exit = values["exit_service"]
        return ProductShell(**values)

    def _mission_factory(self, **values) -> MissionService | None:
        self.session = values["session_service"]
        if self.session.current().leader_backend is None:
            return None
        self.mission = _mission_service(self.root, values["store"], self.session)
        return self.mission

    def _projection_store(self) -> tuple[SQLiteStore, bool]:
        if self.store is not None and self._store_is_open():
            return self.store, False
        return SQLiteStore.open(self.root, clock=FrozenClock(NOW)), True

    def _store_is_open(self) -> bool:
        if self.store is None:
            return False
        try:
            self.store.connection.execute("SELECT 1")
            return True
        except RuntimeError:
            return False


def build_harness(
    root: Path,
    *,
    session_ids: Iterator[str],
    request_ids: Iterator[str],
) -> ReentryHarness:
    return ReentryHarness(
        root,
        session_ids=session_ids,
        request_ids=request_ids,
    )


@pytest.fixture
def shell_harness(tmp_path: Path) -> ReentryHarness:
    harness = build_harness(
        tmp_path,
        session_ids=iter(("ses_active",)),
        request_ids=iter(("xrt_" + "1" * 32, "xrt_" + "2" * 32)),
    )
    harness.configure()
    harness.seed_active_attempt()
    return harness


def test_active_exit_requires_copyable_exact_confirmation(
    shell_harness: ReentryHarness,
) -> None:
    request, digest = shell_harness.prime_exit_request()
    transcript = shell_harness.run([
        "/exit",
        f"/exit confirm {request} {digest}",
        f"/exit decline {request} {digest}",
        "/status",
        "/exit",
    ])

    assert f"/exit confirm {request} {digest}" in transcript
    assert "exit_confirmation_ready" not in transcript
    assert transcript.count(f"/exit confirm {request} {digest}") >= 2
    assert "Session is safe to exit" not in transcript
    assert shell_harness.attempt_state == "running"


def test_ctrl_c_enters_exit_surface_but_does_not_claim_safe_cancel(
    shell_harness: ReentryHarness,
) -> None:
    request, digest = shell_harness.prime_exit_request()
    transcript = shell_harness.run([
        KeyboardInterrupt(),
        f"/exit decline {request} {digest}",
        "/exit",
    ])

    assert "Exit needs confirmation" in transcript
    assert "safely cancelled" not in transcript


def test_eof_with_active_attempt_is_content_free_and_does_not_mutate(
    shell_harness: ReentryHarness,
) -> None:
    before = shell_harness.database_facts()
    transcript = shell_harness.run([EOFError()])

    assert "Diagnosis exit_input_closed_with_active_work" in transcript
    assert "Session saved" not in transcript
    assert shell_harness.database_facts() == before


def test_bootstrap_restores_latest_session_and_pending_exit(tmp_path: Path) -> None:
    first = build_harness(
        tmp_path,
        session_ids=iter(("ses_first",)),
        request_ids=iter(("xrt_" + "1" * 32,)),
    )
    preview_id = first.configure_and_preview(
        leader="codex-cli",
        model="native-default",
        permission="approve_for_me",
        goal="Build",
    ).preview_id
    first.seed_active_attempt()
    request = first.request_active_exit()
    first.seed_older_nonterminal_session()
    first.close_input()
    before = first.database_facts()
    request_ids = CountingIds("xrt_" + "2" * 32)
    second = build_harness(
        tmp_path, session_ids=iter(("ses_must_not_be_used",)), request_ids=request_ids,
    )
    assert second.session_id == "ses_first"
    assert second.configuration == (
        "codex-cli", "native-default", "approve_for_me", "Build",
    )
    assert second.preview_id == preview_id
    assert second.pending_exit == request
    assert request_ids.calls == 0
    assert second.database_facts() == before
    transcript = second.run([EOFError()])
    assert transcript.index("Diagnosis multiple_nonterminal_sessions") < (
        transcript.index("Exit needs confirmation")
    ) < transcript.index(f"Mission Preview {preview_id}")


def test_bootstrap_reports_pending_exit_drift_without_superseding_it(tmp_path: Path) -> None:
    first = build_harness(
        tmp_path, session_ids=iter(("ses_first",)),
        request_ids=iter(("xrt_" + "1" * 32,)),
    )
    first.configure()
    first.seed_active_attempt()
    request = first.request_active_exit()
    assert first.store is not None
    first.store._require_writer().execute(
        "UPDATE attempts SET effect_observed=1 WHERE attempt_id='att_active'"
    )
    first.store._require_writer().commit()
    first.close_input()
    before = first.database_facts()
    request_ids = CountingIds("xrt_" + "2" * 32)
    second = build_harness(
        tmp_path, session_ids=iter(("ses_must_not_be_used",)), request_ids=request_ids,
    )
    restored = second.shell._restored_exit
    assert restored is not None and restored.diagnostic is not None
    assert restored.diagnostic.code == "exit_request_drift"
    assert restored.request == request
    assert request_ids.calls == 0
    assert second.database_facts() == before


def _discovery() -> dict[str, ToolDiscovery]:
    return {
        "codex": ToolDiscovery(
            name="codex",
            command="codex",
            resolved_path="/tools/codex",
            version="codex 1.0",
            authenticated=True,
            acp_available=True,
            readiness=ReadinessState.READY,
            capabilities=("leader", "worker", "acp"),
        )
    }


def _config(**_layers) -> SimpleNamespace:
    return SimpleNamespace(
        resolve=lambda _key: SimpleNamespace(value="approve-for-me")
    )


def _open_session(
    store: SQLiteStore, root: Path, session_ids: Iterator[str],
) -> SessionService:
    return SessionService.open_latest(
        store=store,
        clock=FrozenClock(NOW),
        project_root=str(root),
        available_leaders=AVAILABLE,
        session_id_factory=session_ids.__next__,
    )


def _mission_service(
    root: Path, store: SQLiteStore, session: SessionService,
) -> MissionService:
    base = request()
    template = replace(
        base,
        project_context=replace(base.project_context, project_root=str(root)),
    )
    return MissionService(
        store=store,
        clock=FrozenClock(NOW),
        session_id=session.current().session_id,
        leader_service=LeaderService(EchoLeader(root)),
        request_template=template,
        session_authority=session,
        preview_validator=validate_mission_preview,
    )


def _seed_active_attempt(store: SQLiteStore, session_id: str) -> None:
    now = NOW.isoformat()
    connection = store._require_writer()
    connection.execute(
        "INSERT INTO missions (mission_id,session_id,state,current_version,"
        "created_at,updated_at) VALUES (?,?,?,?,?,?)",
        ("msn_active", session_id, "running", 1, now, now),
    )
    connection.execute(
        "INSERT INTO mission_versions (mission_id,version,preview_id,content_hash,"
        "canonical_mission_facts,confirmed_at) VALUES (?,?,?,?,?,?)",
        ("msn_active", 1, "prv_active", "a" * 64, "{}", now),
    )
    connection.execute(
        "INSERT INTO agent_instances (instance_id,session_id,backend_id,transport,"
        "backend_version,role,acp_session_id,state,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("agt_active", session_id, "codex-cli", "acp", "1", "implementer",
         "acp_active", "active", now, now),
    )
    connection.execute(
        "INSERT INTO tasks (task_id,mission_id,mission_version,ordinal,name,role,"
        "planned_backend,planned_agent_instance_id,acp_route,state,"
        "canonical_task_facts,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("tsk_active", "msn_active", 1, 1, "active", "implementer", "codex-cli",
         "agt_active", "acp://active", "running", "{}", now, now),
    )
    connection.execute(
        "INSERT INTO attempts (attempt_id,task_id,agent_instance_id,ordinal,state,"
        "reason,result_summary,retryable,acp_session_id,effect_observed,created_at,"
        "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("att_active", "tsk_active", "agt_active", 1, "running", None, None, 0,
         "acp_active", 0, now, now),
    )
    connection.commit()
