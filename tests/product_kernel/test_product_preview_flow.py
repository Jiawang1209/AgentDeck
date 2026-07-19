from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.leader_service import LeaderService
from agentdeck.application.mission_service import MissionService
from agentdeck.application.session_service import SessionService
from agentdeck.product.bootstrap import build_product_shell
from agentdeck.product.shell import ProductShell, validate_mission_preview

from .fakes import FrozenClock
from .test_leader_contract import request, valid_proposal


NOW = datetime(2026, 7, 19, 8, 9, 10, tzinfo=timezone.utc)
AVAILABLE = {"codex-cli": ("native-default",)}


class EchoLeader:
    def __init__(self, root: Path) -> None:
        self.root = root

    def propose_mission(self, leader_request):
        payload = deepcopy(valid_proposal())
        payload["project_root"] = str(self.root)
        payload["objective"] = leader_request.user_goal
        return payload


def _mission_service(root: Path, store: SQLiteStore) -> MissionService:
    base = request()
    template = replace(
        base, project_context=replace(base.project_context, project_root=str(root))
    )
    return MissionService(
        store=store, clock=FrozenClock(NOW), session_id="ses_product",
        leader_service=LeaderService(EchoLeader(root)), request_template=template,
        session_authority=SessionService(
            store=store, clock=FrozenClock(NOW), session_id="ses_product",
            project_root=str(root), available_leaders=AVAILABLE,
        ),
        preview_validator=validate_mission_preview,
    )


def test_configured_shell_renders_human_preview_and_exact_confirmation(
    tmp_path: Path,
) -> None:
    output: list[str] = []
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    session = SessionService(
        store=store, clock=FrozenClock(NOW), session_id="ses_product",
        project_root=str(tmp_path), available_leaders=AVAILABLE,
    )
    session.configure(leader="codex-cli", model="native-default")
    mission = _mission_service(tmp_path, store)
    preview = mission.propose("Build an accessible page").preview
    assert preview is not None
    lines = iter((
        f"confirm {preview.preview_id} {preview.content_hash}", "/status", "/exit",
    ))
    shell = ProductShell(
        session_service=session, mission_service=mission,
        available_leaders=AVAILABLE, read_line=lambda _: next(lines),
        write_line=output.append, close=store.close,
    )

    assert shell.run() == 0
    transcript = "\n".join(output)
    assert preview.content_hash in transcript
    assert "Mission confirmed" in transcript
    assert "AgentDeck is running." in transcript
    assert "{" not in transcript


def test_open_goal_without_leader_is_retained_not_discarded(tmp_path: Path) -> None:
    output: list[str] = []
    pending = iter(("Build a page", "/exit"))
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    service = SessionService(
        store=store, clock=FrozenClock(NOW), session_id="ses_product",
        project_root=str(tmp_path), available_leaders=AVAILABLE,
    )
    shell = ProductShell(
        session_service=service, mission_service=None,
        available_leaders=AVAILABLE, read_line=lambda _: next(pending),
        write_line=output.append, close=store.close,
    )

    shell.run()
    reopened = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        assert reopened.load_aggregate("product_sessions", "ses_product")["pending_goal"] == "Build a page"
    finally:
        reopened.close()


def test_bootstrap_binds_only_an_injected_mission_factory(tmp_path: Path) -> None:
    calls: list[str] = []
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))

    def mission_factory(**facts):
        calls.append("mission")
        assert facts["store"] is store
        assert isinstance(facts["session_service"], SessionService)
        return None

    shell = build_product_shell(
        project_root=str(tmp_path), read_line=lambda _: (_ for _ in ()).throw(EOFError),
        write_line=lambda _: None, clock_factory=lambda: FrozenClock(NOW),
        discovery_factory=lambda: {}, config_factory=lambda **_: type(
            "Config", (), {"resolve": lambda self, key: type("V", (), {"value": "approve-for-me"})()}
        )(), store_factory=lambda *args, **kwargs: store,
        mission_service_factory=mission_factory,
    )

    assert calls == ["mission"]
    shell.run()
