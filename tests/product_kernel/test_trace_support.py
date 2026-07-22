"""End-to-end Mission trace and sanitized human support evidence."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from functools import wraps
from hashlib import sha256
import json
from pathlib import Path

import pytest

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.async_exit_coordinator import AsyncExitCoordinator
from agentdeck.application.exit_service import ExitService
from agentdeck.application.execution_runtime import ForegroundExecutionRuntime
from agentdeck.application.project_lifecycle_service import ProjectLifecycleService
from agentdeck.application.recovery_service import RecoveryService
from agentdeck.application.session_service import SessionService
from agentdeck.application.support_service import (
    MissionTrace, Permission, SupportBundle, SupportService, SupportServiceError,
)
from agentdeck.product.shell import ProductShell
from agentdeck.product.slash_commands import CommandKind, parse_command

from .fakes import FrozenClock


NOW = datetime(2026, 7, 22, 9, 0, 0, tzinfo=timezone.utc)
NOW_TEXT = NOW.isoformat()
AVAILABLE = {"codex-cli": ("native-default",)}


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _seed_confirmed_mission(
    store: SQLiteStore, *, mission_id: str = "mis_1", session_id: str = "ses_1",
    create_session: bool = True,
) -> None:
    """Seed one confirmed Mission with a full, verifiable end-to-end lineage."""

    connection = store._require_writer()
    connection.execute("BEGIN")
    try:
        if create_session:
            connection.execute(
                "INSERT INTO projects VALUES ('prj_1', ?, ?)",
                (str(store._project_root), NOW_TEXT),
            )
            connection.execute(
                """INSERT INTO product_sessions (
                       session_id,project_id,state,permission_profile,pending_goal,
                       created_at,updated_at,leader_backend,leader_model)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (session_id, "prj_1", "running", "approve_for_me", None,
                 NOW_TEXT, NOW_TEXT, "codex-cli", "native-default"),
            )
        connection.execute(
            "INSERT INTO agent_instances VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("agt_impl", session_id, "codex-cli", "acp", "1", "implementer",
             "acp_1", "active", NOW_TEXT, NOW_TEXT),
        )
        connection.execute(
            "INSERT INTO missions VALUES (?,?,?,?,?,?)",
            (mission_id, session_id, "confirmed", 1, NOW_TEXT, NOW_TEXT),
        )
        connection.execute(
            "INSERT INTO mission_versions VALUES (?,?,?,?,?,?)",
            (mission_id, 1, f"prv_{mission_id}", "a" * 64, "{}", NOW_TEXT),
        )
        for ordinal, (task_id, role) in enumerate(
            (("tsk_impl", "implementer"), ("tsk_review", "reviewer")), start=1,
        ):
            connection.execute(
                "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (task_id, mission_id, 1, ordinal, task_id, role, "codex-cli",
                 "agt_impl", "acp://route", "running", _canonical({"task_id": task_id}),
                 NOW_TEXT, NOW_TEXT),
            )
        connection.execute(
            "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("att_impl_1", "tsk_impl", "agt_impl", 1, "completed", None,
             "implementation complete", 0, "acp_1", 0, NOW_TEXT, NOW_TEXT),
        )
        connection.execute(
            "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("att_review_1", "tsk_review", "agt_impl", 1, "running", None,
             None, 0, None, 0, NOW_TEXT, NOW_TEXT),
        )
        handoff_facts = _canonical({"source": "att_impl_1", "target": "tsk_review"})
        connection.execute(
            "INSERT INTO handoffs VALUES (?,?,?,?,?,?,?)",
            ("hnd_impl", "att_impl_1", "tsk_review", "implementation complete",
             handoff_facts, sha256(handoff_facts.encode("utf-8")).hexdigest(), NOW_TEXT),
        )
        request_facts = _canonical({"scope": "implementation"})
        decision_facts = _canonical({"decision": "approved"})
        connection.execute(
            "INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("apv_impl", mission_id, 1, "att_impl_1", "write_project", "approved",
             "b" * 64, request_facts, decision_facts, NOW_TEXT, NOW_TEXT),
        )
        evidence_facts = _canonical({"artifact_reference": "workspace patch"})
        connection.execute(
            "INSERT INTO evidence VALUES (?,?,?,?,?,?,?)",
            ("ev_impl_1", "tsk_impl", "att_impl_1", "artifact_hash",
             evidence_facts, sha256(evidence_facts.encode("utf-8")).hexdigest(), NOW_TEXT),
        )
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise


@pytest.fixture
def store(tmp_path: Path):
    opened = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    _seed_confirmed_mission(opened)
    try:
        yield opened
    finally:
        opened.close()


@pytest.fixture
def service(store: SQLiteStore) -> SupportService:
    return SupportService(store=store)


# ---------------------------------------------------------------------------
# SupportService.trace


def test_trace_links_mission_task_attempt_permission_handoff_evidence(
    service: SupportService,
) -> None:
    trace = service.trace("mis_1")

    assert trace.path == (
        "mis_1", "tsk_impl", "att_impl_1", "hnd_impl", "tsk_review", "att_review_1",
    )
    assert trace.permissions[0].attempt_id == "att_impl_1"
    assert isinstance(trace, MissionTrace)
    assert isinstance(trace.permissions[0], Permission)


def test_trace_raises_for_unknown_mission(service: SupportService) -> None:
    with pytest.raises(SupportServiceError):
        service.trace("mis_unknown")
    assert issubclass(SupportServiceError, ValueError)


def test_trace_raises_when_handoff_content_hash_is_tampered(
    service: SupportService, store: SQLiteStore,
) -> None:
    store._require_writer().execute(
        "UPDATE handoffs SET content_hash=? WHERE handoff_id='hnd_impl'",
        ("f" * 64,),
    )

    with pytest.raises(SupportServiceError, match="handoff content hash mismatch"):
        service.trace("mis_1")


def test_trace_raises_when_evidence_content_hash_is_tampered(
    service: SupportService, store: SQLiteStore,
) -> None:
    store._require_writer().execute(
        "UPDATE evidence SET content_hash=? WHERE evidence_id='ev_impl_1'",
        ("f" * 64,),
    )

    with pytest.raises(SupportServiceError, match="evidence content hash mismatch"):
        service.trace("mis_1")


# ---------------------------------------------------------------------------
# SupportService.support_bundle


def test_support_bundle_is_bounded_and_contains_no_raw_frames(
    service: SupportService,
) -> None:
    bundle = service.support_bundle("mis_1")

    assert isinstance(bundle, SupportBundle)
    assert bundle.byte_count == len(bundle.text.encode("utf-8"))
    assert bundle.byte_count <= 256_000
    assert "raw_protocol" not in bundle.text
    assert "terminal_output" not in bundle.text
    assert "API_KEY" not in bundle.text
    assert "mis_1" in bundle.text
    assert "ev_impl_1" in bundle.text
    assert "workspace patch" not in bundle.text


def test_support_bundle_raises_for_unknown_mission(service: SupportService) -> None:
    with pytest.raises(SupportServiceError):
        service.support_bundle("mis_unknown")


def test_support_bundle_redacts_injected_secret_evidence_label(
    service: SupportService, store: SQLiteStore,
) -> None:
    facts = _canonical({"artifact_reference": "leaked"})
    store._require_writer().execute(
        "INSERT INTO evidence VALUES (?,?,?,?,?,?,?)",
        ("ev_API_KEY_leak", "tsk_impl", "att_impl_1", "artifact_hash", facts,
         sha256(facts.encode("utf-8")).hexdigest(), NOW_TEXT),
    )

    bundle = service.support_bundle("mis_1")

    assert "API_KEY" not in bundle.text
    assert "[redacted]" in bundle.text


class _ManyEventsStore:
    """A minimal fake Store returning many rows without touching a real DB."""

    def __init__(self, *, task_count: int, evidence_per_attempt: int) -> None:
        self._task_count = task_count
        self._evidence_per_attempt = evidence_per_attempt

    def list_mission_tasks(self, mission_id: str):
        if mission_id != "mis_many":
            return ()
        return tuple(
            {
                "task_id": f"tsk_{index}", "mission_id": mission_id,
                "mission_version": 1, "ordinal": index, "name": f"task{index}",
                "role": "implementer", "planned_backend": "codex-cli",
                "planned_agent_instance_id": "agt_1", "acp_route": "acp://route",
                "state": "completed", "canonical_task_facts": "{}",
                "created_at": NOW_TEXT, "updated_at": NOW_TEXT,
            }
            for index in range(self._task_count)
        )

    def list_task_attempts(self, task_id: str):
        return (
            {
                "attempt_id": f"att_{task_id}", "task_id": task_id,
                "agent_instance_id": "agt_1", "ordinal": 1, "state": "completed",
                "reason": None, "result_summary": "ok", "retryable": False,
                "acp_session_id": None, "effect_observed": False,
                "created_at": NOW_TEXT, "updated_at": NOW_TEXT,
            },
        )

    def list_attempt_handoffs(self, attempt_id: str):
        return ()

    def list_mission_approvals(self, mission_id: str):
        return ()

    def list_attempt_evidence(self, attempt_id: str):
        return tuple(
            {
                "evidence_id": f"ev_{attempt_id}_{index}", "task_id": "tsk",
                "attempt_id": attempt_id, "kind": "artifact_hash",
                "canonical_evidence_facts": "{}", "content_hash": "a" * 64,
                "created_at": NOW_TEXT,
            }
            for index in range(self._evidence_per_attempt)
        )


def test_support_bundle_stays_bounded_with_many_events() -> None:
    service = SupportService(
        store=_ManyEventsStore(task_count=80, evidence_per_attempt=40)
    )

    bundle = service.support_bundle("mis_many")

    assert bundle.byte_count <= 256_000
    assert bundle.byte_count == len(bundle.text.encode("utf-8"))
    assert "truncated" in bundle.text


# ---------------------------------------------------------------------------
# Slash command parsing


def test_support_command_takes_an_optional_mission_id_argument() -> None:
    assert parse_command("/support").kind is CommandKind.SUPPORT
    assert parse_command("/support").argument is None
    assert parse_command("/support mis_1").argument == "mis_1"


def test_trace_command_requires_a_mission_id_argument() -> None:
    assert parse_command("/trace") is None
    command = parse_command("/trace mis_1")
    assert command.kind is CommandKind.TRACE
    assert command.argument == "mis_1"


# ---------------------------------------------------------------------------
# Shell wiring


def _shell(
    root: Path, store: SQLiteStore, session: SessionService, *,
    support: SupportService | None, lines: tuple[str, ...], output: list[str],
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

    async def _read(_prompt: str) -> str:
        await asyncio.sleep(0)
        try:
            return next(_lines_iterator)
        except StopIteration:
            raise EOFError from None

    _lines_iterator = iter(lines)
    return ProductShell(
        session_service=session,
        exit_coordinator=AsyncExitCoordinator(
            exit_service=exit_service, store=store, clock=clock,
            runtime=runtime, lifecycle=lifecycle,
            session_id=session.current().session_id,
        ),
        recovery_service=RecoveryService(
            store=store, clock=clock, session_id=session.current().session_id,
            recovery_run_id="restart_support",
        ),
        lifecycle=lifecycle, support_service=support,
        resume_snapshot_loader=lambda: store.load_execution_resume(
            session.current().session_id
        ),
        available_leaders=AVAILABLE, read_line=_read,
        write_line=output.append, close=store.close,
    )


@async_test
async def test_shell_support_without_active_mission_emits_plain_language(
    tmp_path: Path,
) -> None:
    output: list[str] = []
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    session = SessionService(
        store=store, clock=FrozenClock(NOW), session_id="ses_1",
        project_root=str(tmp_path), available_leaders=AVAILABLE,
    )
    session.configure(leader="codex-cli", model="native-default")
    shell = _shell(
        tmp_path, store, session, support=SupportService(store=store),
        lines=("/support", "/exit"), output=output,
    )

    await shell.run_async()

    assert any("No Mission is active" in line for line in output)


@async_test
async def test_shell_support_missing_service_emits_plain_language(
    tmp_path: Path,
) -> None:
    output: list[str] = []
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    session = SessionService(
        store=store, clock=FrozenClock(NOW), session_id="ses_1",
        project_root=str(tmp_path), available_leaders=AVAILABLE,
    )
    session.configure(leader="codex-cli", model="native-default")
    shell = _shell(
        tmp_path, store, session, support=None,
        lines=("/support mis_1", "/exit"), output=output,
    )

    await shell.run_async()

    assert any("Support bundle is not available" in line for line in output)


@async_test
async def test_shell_trace_command_emits_the_verified_lineage_path(
    tmp_path: Path,
) -> None:
    output: list[str] = []
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    session = SessionService(
        store=store, clock=FrozenClock(NOW), session_id="ses_1",
        project_root=str(tmp_path), available_leaders=AVAILABLE,
    )
    session.configure(leader="codex-cli", model="native-default")
    _seed_confirmed_mission(store, session_id="ses_1", create_session=False)
    shell = _shell(
        tmp_path, store, session, support=SupportService(store=store),
        lines=("/trace mis_1", "/exit"), output=output,
    )

    await shell.run_async()

    assert any(
        "tsk_impl -> att_impl_1 -> hnd_impl -> tsk_review -> att_review_1" in line
        for line in output
    )
