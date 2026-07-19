from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.session_service import SessionService
from agentdeck.kernel.mission import MissionDraft

from .fakes import FrozenClock


NOW = datetime(2026, 7, 19, 8, 9, 10, tzinfo=timezone.utc)


def _store(root: Path) -> SQLiteStore:
    store = SQLiteStore.open(root, clock=FrozenClock(NOW))
    service = SessionService(
        store=store,
        clock=FrozenClock(NOW),
        session_id="ses_preview",
        project_root=str(root),
        available_leaders={"codex-cli": ("native-default",)},
    )
    service.configure(leader="codex-cli", model="native-default")
    return store


def _preview_snapshot(root: Path, *, version: int = 1) -> dict[str, object]:
    preview = MissionDraft.coding_default(
        draft_id="drf_preview",
        objective="Build an accessible page",
        project_root=str(root),
        leader_backend="codex-cli",
        leader_model="native-default",
        permission_profile="approve_for_me",
        leader_version="1.2.3",
    ).preview(version)
    return {
        "mission_id": f"msn_{preview.content_hash[:24]}",
        "session_id": "ses_preview",
        "state": "awaiting_confirmation",
        "current_version": version,
        "preview_id": preview.preview_id,
        "version": version,
        "content_hash": preview.content_hash,
        "canonical_content": preview.canonical_content,
        "confirmed_at": None,
    }


def test_preview_save_and_current_load_are_command_atomic(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snapshot = _preview_snapshot(tmp_path)
    try:
        store.execute_once(
            "mission:preview:one",
            "create_mission_preview",
            lambda transaction: _save(transaction, "mission_previews", snapshot),
        )

        assert store.load_aggregate("mission_previews", snapshot["preview_id"]) == snapshot
        assert store.load_aggregate("current_mission_preview", "ses_preview") == snapshot
        assert store.count("missions") == store.count("mission_versions") == 1
        assert store.count("tasks") == 0
    finally:
        store.close()


def test_confirmation_freezes_canonical_version_and_materializes_tasks(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    preview = _preview_snapshot(tmp_path)
    confirmed = {**preview, "state": "confirmed", "confirmed_at": NOW.isoformat()}
    try:
        store.execute_once(
            "mission:preview:one", "create_mission_preview",
            lambda transaction: _save(transaction, "mission_previews", preview),
        )
        store.execute_once(
            "mission:confirm:one", "confirm_mission",
            lambda transaction: _save(transaction, "confirmed_missions", confirmed),
        )

        assert store.load_aggregate("confirmed_missions", confirmed["mission_id"]) == confirmed
        rows = store.connection.execute(
            "SELECT name,role,state,mission_id,mission_version FROM tasks ORDER BY ordinal"
        ).fetchall()
        assert rows == [
            ("implementation", "implementer", "pending", confirmed["mission_id"], 1),
            ("review", "reviewer", "pending", confirmed["mission_id"], 1),
            ("revision", "reviser", "pending", confirmed["mission_id"], 1),
            ("acceptance", "acceptance_reviewer", "pending", confirmed["mission_id"], 1),
        ]
    finally:
        store.close()


def test_mission_adapter_rejects_identity_and_confirmation_drift(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    preview = _preview_snapshot(tmp_path)
    try:
        with pytest.raises(ValueError):
            store.execute_once(
                "mission:bad:id", "create_mission_preview",
                lambda transaction: _save(
                    transaction, "mission_previews", {**preview, "mission_id": "msn_forged"}
                ),
            )
        with pytest.raises(ValueError):
            store.execute_once(
                "mission:bad:confirm", "confirm_mission",
                lambda transaction: _save(
                    transaction, "confirmed_missions",
                    {**preview, "state": "confirmed", "confirmed_at": NOW.isoformat()},
                ),
            )

        assert store.count("missions") == store.count("mission_versions") == 0
        assert store.count("tasks") == 0
    finally:
        store.close()


def _save(transaction, aggregate_type: str, snapshot: dict[str, object]):
    transaction.save_aggregate(aggregate_type, str(snapshot["preview_id"] if aggregate_type == "mission_previews" else snapshot["mission_id"]), snapshot)
    return {"saved": True}
