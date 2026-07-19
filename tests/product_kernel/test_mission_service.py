from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.leader_service import LeaderService
from agentdeck.application.mission_service import MissionService, MissionServiceError
from agentdeck.application.session_service import SessionService
from agentdeck.kernel.mission import MissionDraft
from agentdeck.kernel.permissions import PermissionProfile
from agentdeck.ports.leader import LeaderFailure, LeaderFailureCode, LeaderRequest
from agentdeck.product.shell import validate_mission_preview

from .fakes import FrozenClock
from .test_leader_contract import request, valid_proposal


NOW = datetime(2026, 7, 19, 8, 9, 10, tzinfo=timezone.utc)


class FakeLeader:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.requests: list[LeaderRequest] = []

    def propose_mission(self, leader_request: LeaderRequest) -> object:
        self.requests.append(leader_request)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return deepcopy(result)


def _proposal(root: Path, objective: str) -> dict[str, object]:
    payload = valid_proposal()
    payload["project_root"] = str(root)
    payload["objective"] = objective
    return payload


def _request(root: Path) -> LeaderRequest:
    base = request()
    return replace(
        base,
        project_context=replace(base.project_context, project_root=str(root)),
    )


def _service(
    root: Path, leader: FakeLeader,
) -> tuple[MissionService, SQLiteStore]:
    store = SQLiteStore.open(root, clock=FrozenClock(NOW))
    session = SessionService(
        store=store, clock=FrozenClock(NOW), session_id="ses_mission",
        project_root=str(root), available_leaders={"codex-cli": ("native-default",)},
    )
    session.configure(leader="codex-cli", model="native-default")
    return MissionService(
        store=store,
        clock=FrozenClock(NOW),
        session_id="ses_mission",
        leader_service=LeaderService(leader),
        request_template=_request(root),
        session_authority=session,
        preview_validator=validate_mission_preview,
    ), store


def test_goal_becomes_durable_preview_and_exact_confirmation(tmp_path: Path) -> None:
    leader = FakeLeader([_proposal(tmp_path, "Build an accessible page")])
    service, store = _service(tmp_path, leader)
    try:
        shown = service.propose("Build an accessible page")

        assert shown.mode == "mission_preview"
        assert shown.preview is not None
        assert shown.preview.objective == "Build an accessible page"
        assert store.load_aggregate("product_sessions", "ses_mission")[
            "permission_profile"
        ] == "approve_for_me"
        preview = shown.preview.preview
        started = service.confirm(preview.preview_id, preview.content_hash)
        assert started.mode == "mission_confirmed"
        assert started.mission is not None
        assert started.mission.version == preview.version
        assert store.count("tasks") == 4
    finally:
        store.close()


def test_revision_sends_current_preview_and_invalidates_old_confirmation(
    tmp_path: Path,
) -> None:
    first = _proposal(tmp_path, "Build a page")
    second = _proposal(tmp_path, "Build a mobile-accessible page")
    second["acceptance_criteria"] = ["mobile viewport evidence is recorded"]
    leader = FakeLeader([first, second])
    service, store = _service(tmp_path, leader)
    try:
        old = service.propose("Build a page").preview
        assert old is not None
        new = service.revise("Use Claude as reviewer and add mobile acceptance").preview
        assert new is not None

        assert new.version == old.version + 1
        assert old.preview.canonical_content in leader.requests[1].user_goal
        assert "Use Claude as reviewer" in leader.requests[1].user_goal
        rejected = service.confirm(old.preview_id, old.content_hash)
        assert rejected.diagnostic is not None
        assert rejected.diagnostic.code == "mission_preview_drift"
        assert store.count("tasks") == 0
    finally:
        store.close()


def test_leader_failure_is_content_free_and_preserves_category(tmp_path: Path) -> None:
    marker = "provider-secret-marker"
    failure = LeaderFailure(LeaderFailureCode.TIMEOUT)
    failure.__context__ = RuntimeError(marker)
    service, store = _service(tmp_path, FakeLeader([failure]))
    try:
        result = service.propose("Build a page")

        assert result.diagnostic is not None
        assert result.diagnostic.code == "leader_timeout"
        assert marker not in str(result.diagnostic)
        assert store.count("missions") == 0
    finally:
        store.close()


def test_service_restores_the_current_exact_preview(tmp_path: Path) -> None:
    leader = FakeLeader([_proposal(tmp_path, "Build a page")])
    service, store = _service(tmp_path, leader)
    first = service.propose("Build a page").preview
    assert first is not None
    restored = MissionService(
        store=store,
        clock=FrozenClock(NOW),
        session_id="ses_mission",
        leader_service=LeaderService(FakeLeader([])),
        request_template=_request(tmp_path),
        session_authority=SessionService(
            store=store, clock=FrozenClock(NOW), session_id="ses_mission",
            project_root=str(tmp_path),
            available_leaders={"codex-cli": ("native-default",)},
        ),
        preview_validator=validate_mission_preview,
    )
    try:
        assert restored.current_preview() == first
        confirmed = restored.confirm(first.preview_id, first.content_hash)
        assert confirmed.mission is not None
        assert confirmed.mission.content_hash == first.content_hash
    finally:
        store.close()


def test_stale_service_cannot_confirm_after_another_service_creates_v2(
    tmp_path: Path,
) -> None:
    first = _proposal(tmp_path, "Build a page")
    second = _proposal(tmp_path, "Build a mobile page")
    service_v1, store = _service(tmp_path, FakeLeader([first]))
    old = service_v1.propose("Build a page").preview
    assert old is not None
    second_session = SessionService(
        store=store, clock=FrozenClock(NOW), session_id="ses_mission",
        project_root=str(tmp_path),
        available_leaders={"codex-cli": ("native-default",)},
    )
    service_v2 = MissionService(
        store=store, clock=FrozenClock(NOW), session_id="ses_mission",
        leader_service=LeaderService(FakeLeader([second])),
        request_template=_request(tmp_path), session_authority=second_session,
        preview_validator=validate_mission_preview,
    )
    new = service_v2.revise("Add mobile acceptance").preview
    assert new is not None and new.version == 2
    try:
        rejected = service_v1.confirm(old.preview_id, old.content_hash)

        assert rejected.diagnostic is not None
        assert rejected.diagnostic.code == "mission_preview_drift"
        assert store.count("tasks") == 0
        assert store.load_aggregate("product_sessions", "ses_mission")["state"] == "awaiting_confirmation"
    finally:
        store.close()


def test_confirmed_reentry_has_no_confirmable_current_preview(tmp_path: Path) -> None:
    service, store = _service(
        tmp_path, FakeLeader([_proposal(tmp_path, "Build a page")])
    )
    preview = service.propose("Build a page").preview
    assert preview is not None
    service.confirm(preview.preview_id, preview.content_hash)
    assert service.current_preview() is None
    restored_session = SessionService(
        store=store, clock=FrozenClock(NOW), session_id="ses_mission",
        project_root=str(tmp_path),
        available_leaders={"codex-cli": ("native-default",)},
    )
    restored = MissionService(
        store=store, clock=FrozenClock(NOW), session_id="ses_mission",
        leader_service=LeaderService(FakeLeader([])),
        request_template=_request(tmp_path), session_authority=restored_session,
        preview_validator=validate_mission_preview,
    )
    try:
        assert restored.current_preview() is None
        assert restored_session.current().state.value == "running"
    finally:
        store.close()


def test_request_identity_must_match_session_setup_authority(tmp_path: Path) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    session = SessionService(
        store=store, clock=FrozenClock(NOW), session_id="ses_mission",
        project_root=str(tmp_path), available_leaders={"codex-cli": ("native-default",)},
    )
    session.configure(leader="codex-cli", model="native-default")
    leader = FakeLeader([_proposal(tmp_path, "Build a page")])
    mismatches = (
        replace(
            _request(tmp_path),
            resolved_model=replace(
                _request(tmp_path).resolved_model, backend_id="claude-cli"
            ),
        ),
        replace(
            _request(tmp_path),
            resolved_model=replace(_request(tmp_path).resolved_model, model_id="fallback"),
        ),
        replace(
            _request(tmp_path), permission_ceiling=PermissionProfile.FULL_ACCESS
        ),
        replace(
            _request(tmp_path),
            project_context=replace(
                _request(tmp_path).project_context, project_root=str(tmp_path / "other")
            ),
        ),
    )
    try:
        for template in mismatches:
            with pytest.raises(MissionServiceError, match="setup authority"):
                MissionService(
                    store=store, clock=FrozenClock(NOW), session_id="ses_mission",
                    leader_service=LeaderService(leader), request_template=template,
                    session_authority=session,
                    preview_validator=validate_mission_preview,
                )
        assert leader.requests == []
        assert store.count("missions") == 0
    finally:
        store.close()


def test_missing_reentry_setup_authority_fails_closed(tmp_path: Path) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    unconfigured = SessionService(
        store=store, clock=FrozenClock(NOW), session_id="ses_missing",
        project_root=str(tmp_path), available_leaders={"codex-cli": ("native-default",)},
    )
    try:
        with pytest.raises(MissionServiceError, match="setup authority"):
            MissionService(
                store=store, clock=FrozenClock(NOW), session_id="ses_missing",
                leader_service=LeaderService(FakeLeader([])),
                request_template=_request(tmp_path), session_authority=unconfigured,
                preview_validator=validate_mission_preview,
            )
        assert store.count("missions") == 0
    finally:
        store.close()


@pytest.mark.parametrize(
    "unsafe_objective",
    (
        '{"task":"raw-preview-marker"}',
        "Authorization: Bearer raw-preview-marker",
        "raw-preview-marker-" + "x" * 2_100,
    ),
)
def test_unsafe_human_preview_is_diagnostic_and_never_persisted(
    tmp_path: Path, unsafe_objective: str,
) -> None:
    service, store = _service(
        tmp_path, FakeLeader([_proposal(tmp_path, unsafe_objective)])
    )
    try:
        result = service.propose("Build a page")

        assert result.diagnostic is not None
        assert result.diagnostic.code == "mission_preview_unsafe"
        assert "raw-preview-marker" not in str(result.diagnostic)
        assert store.count("missions") == store.count("mission_versions") == 0
        assert store.load_aggregate("product_sessions", "ses_mission")["state"] == "ready"
        reentered = MissionService(
            store=store, clock=FrozenClock(NOW), session_id="ses_mission",
            leader_service=LeaderService(FakeLeader([])),
            request_template=_request(tmp_path),
            session_authority=SessionService(
                store=store, clock=FrozenClock(NOW), session_id="ses_mission",
                project_root=str(tmp_path),
                available_leaders={"codex-cli": ("native-default",)},
            ),
            preview_validator=validate_mission_preview,
        )
        assert reentered.current_preview() is None
    finally:
        store.close()


def test_aggregate_oversize_human_preview_is_rejected_before_write(
    tmp_path: Path,
) -> None:
    payload = _proposal(tmp_path, "Build a page")
    payload["acceptance_criteria"] = [
        f"criterion-{index}-" + "x" * 1_900 for index in range(40)
    ]
    service, store = _service(tmp_path, FakeLeader([payload]))
    try:
        result = service.propose("Build a page")

        assert result.diagnostic is not None
        assert result.diagnostic.code == "mission_preview_unsafe"
        assert store.count("missions") == store.count("mission_versions") == 0
    finally:
        store.close()
