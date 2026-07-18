from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentdeck.models import PROJECT_VIEW_SCHEMA_VERSION, PROJECT_VIEW_V2_SCHEMA_VERSION
from agentdeck.projections.project_view import ProjectViewProjection, ProjectionError
from agentdeck.storage.ownership import ProjectWriterLease
from agentdeck.storage.sqlite_store import SQLiteMissionStore


@pytest.fixture
def store(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    lease = ProjectWriterLease.acquire(root)
    mission_store = SQLiteMissionStore.create(root, lease=lease, project_id="prj_1")
    try:
        _seed_durable_state(mission_store)
        yield mission_store
    finally:
        mission_store.close()
        lease.close()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _seed_durable_state(store: SQLiteMissionStore) -> None:
    connection = store._connection  # noqa: SLF001 - deterministic projection fixture
    secret = "PRIVATE-PROMPT-TRANSCRIPT-SECRET"
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        "UPDATE projects SET revision = 3, authority_generation = 7 "
        "WHERE project_id = 'prj_1'"
    )
    connection.execute(
        "INSERT INTO missions VALUES (?, ?, ?, ?, ?, ?)",
        ("mis_1", "prj_1", 1, "running", 1, 3),
    )
    connection.execute(
        "INSERT INTO mission_versions VALUES (?, ?, ?, ?, ?, ?)",
        (
            "mis_1",
            1,
            _canonical({"goal": secret, "content_snapshot": secret}),
            "sha256:" + "a" * 64,
            _canonical({"provider_output": secret}),
            1,
        ),
    )
    connection.execute(
        "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "tsk_1",
            "mis_1",
            1,
            _canonical(
                {
                    "role": "worker",
                    "dependencies": [],
                    "objective": secret,
                    "prompt": secret,
                }
            ),
            "completed",
            1,
            3,
        ),
    )
    connection.execute(
        "INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "att_1",
            "tsk_1",
            1,
            "completed",
            0,
            _canonical({"budget_units": 5, "operation_id": "op_1"}),
            2,
            3,
        ),
    )
    connection.execute(
        "INSERT INTO handoffs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "hnd_1",
            "mis_1",
            "tsk_1",
            "tsk_1",
            "accepted",
            _canonical({"summary": secret, "transcript": secret}),
            3,
            3,
        ),
    )
    connection.execute(
        "INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "evd_1",
            "tsk_1",
            "att_1",
            "test_result",
            "sha256:" + "b" * 64,
            _canonical({"fact": "check_passed", "reason": secret}),
            3,
        ),
    )
    for cursor in range(1, 4):
        connection.execute(
            "INSERT INTO events(event_cursor,event_id,project_id,project_revision,"
            "trigger_kind,kind,provenance_json,payload_json,internal_trigger_id,created_at) "
            "VALUES (?, ?, 'prj_1', ?, 'internal_trigger', 'fixture.event', ?, ?, ?, ?)",
            (
                cursor,
                f"evt_{cursor}",
                cursor,
                _canonical(
                    {
                        "internal_trigger_id": f"int_{cursor}",
                        "source_revision": cursor - 1,
                        "source_snapshot_id": f"snapshot_{cursor - 1}",
                    }
                ),
                _canonical({"prompt": secret}),
                f"int_{cursor}",
                f"2026-07-18T10:00:0{cursor}Z",
            ),
        )
    connection.commit()
    store._project_revision = 3  # noqa: SLF001 - keep fixture writer revision coherent


def _walk(value: object):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)
    elif isinstance(value, str):
        yield value


def test_v1_and_v2_share_one_sqlite_authority_and_bounded_summaries(store) -> None:
    projection = ProjectViewProjection(store)

    v1 = projection.snapshot("v1")
    v2 = projection.snapshot("v2")

    assert PROJECT_VIEW_SCHEMA_VERSION == "project-view/v1"
    assert PROJECT_VIEW_V2_SCHEMA_VERSION == "project-view/v2"
    assert v1["schema_version"] == PROJECT_VIEW_SCHEMA_VERSION
    assert v2["schema_version"] == PROJECT_VIEW_V2_SCHEMA_VERSION
    assert v1["project_revision"] == v2["project_revision"] == 3
    assert v1["authority"] == v2["authority"] == {
        "state": "sqlite_active",
        "generation": 7,
    }
    assert v1["event_cursor"] == v2["event_cursor"] == 3
    assert v2["missions"] == {
        "count": 1,
        "items": [
            {
                "mission_id": "mis_1",
                "version": 1,
                "status": "running",
                "authorization_digest": "sha256:" + "a" * 64,
                "created_revision": 1,
                "updated_revision": 3,
            }
        ],
    }
    assert v2["tasks"]["items"][0] == {
        "task_id": "tsk_1",
        "mission_id": "mis_1",
        "mission_version": 1,
        "status": "completed",
        "created_revision": 1,
        "updated_revision": 3,
    }
    assert v2["attempts"]["items"][0] == {
        "attempt_id": "att_1",
        "task_id": "tsk_1",
        "attempt_number": 1,
        "status": "completed",
        "route_position": 0,
        "started_revision": 2,
        "terminal_revision": 3,
    }
    assert v2["handoffs"]["items"][0] == {
        "handoff_id": "hnd_1",
        "mission_id": "mis_1",
        "source_task_id": "tsk_1",
        "destination_task_id": "tsk_1",
        "status": "accepted",
        "created_revision": 3,
        "accepted_revision": 3,
    }
    assert v2["evidence"]["items"][0] == {
        "evidence_id": "evd_1",
        "task_id": "tsk_1",
        "attempt_id": "att_1",
        "kind": "test_result",
        "integrity_hash": "sha256:" + "b" * 64,
        "created_revision": 3,
    }
    flattened = set(_walk(v2))
    assert "PRIVATE-PROMPT-TRANSCRIPT-SECRET" not in flattened
    assert not {"prompt", "transcript", "secret", "content_snapshot"} & flattened


def test_snapshot_is_query_only_and_does_not_touch_legacy_json(store) -> None:
    root = Path(store._root)  # noqa: SLF001 - assert legacy state is not consulted
    legacy = root / ".agentdeck" / "state" / "state.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("not-json PRIVATE-PROMPT-TRANSCRIPT-SECRET", encoding="utf-8")
    before = store._connection.total_changes  # noqa: SLF001
    before_revision = store._connection.execute(  # noqa: SLF001
        "SELECT revision FROM projects"
    ).fetchone()

    result = ProjectViewProjection(store).snapshot("v1")

    assert result["project_revision"] == 3
    assert store._connection.total_changes == before  # noqa: SLF001
    assert store._connection.execute(  # noqa: SLF001
        "SELECT revision FROM projects"
    ).fetchone() == before_revision
    assert legacy.read_text(encoding="utf-8").startswith("not-json")


@pytest.mark.parametrize(
    ("statement", "params"),
    [
        (
            "UPDATE tasks SET specification_json = ? WHERE task_id = 'tsk_1'",
            ('{ "role": "worker" }',),
        ),
        (
            "UPDATE evidence SET summary_json = ? WHERE evidence_id = 'evd_1'",
            (_canonical({"reason": "x" * (65 * 1024)}),),
        ),
    ],
)
def test_corrupt_noncanonical_or_oversize_rows_fail_closed_without_raw_values(
    store, statement: str, params: tuple[object, ...]
) -> None:
    store._connection.execute(statement, params)  # noqa: SLF001

    with pytest.raises(ProjectionError) as raised:
        ProjectViewProjection(store).snapshot("v2")

    assert str(raised.value) == "ProjectView projection unavailable"
    assert "worker" not in str(raised.value)
    assert "x" not in str(raised.value)


@pytest.mark.parametrize("version", ["", "V1", "project-view/v3", None, 1])
def test_unknown_snapshot_version_is_rejected_before_opening_reader(
    store, monkeypatch: pytest.MonkeyPatch, version: object
) -> None:
    calls = 0

    def forbidden(_self):
        nonlocal calls
        calls += 1
        raise AssertionError("reader must not open")

    monkeypatch.setattr(SQLiteMissionStore, "open_reader", forbidden)
    with pytest.raises(ValueError, match="^ProjectView version invalid$"):
        ProjectViewProjection(store).snapshot(version)  # type: ignore[arg-type]
    assert calls == 0
