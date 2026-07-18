from __future__ import annotations

from pathlib import Path

import pytest

from agentdeck.projections.project_view import ProjectViewProjection, ProjectionError
from agentdeck.storage.ownership import ProjectWriterLease
from agentdeck.storage.sqlite_store import SQLiteMissionStore



def _canonical(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _seed_durable_state(store: SQLiteMissionStore) -> None:
    connection = store._connection  # noqa: SLF001 - deterministic reconnect fixture
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        "UPDATE projects SET revision = 3, authority_generation = 7 "
        "WHERE project_id = 'prj_1'"
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
                _canonical({"prompt": "PRIVATE-PROMPT-TRANSCRIPT-SECRET"}),
                f"int_{cursor}",
                f"2026-07-18T10:00:0{cursor}Z",
            ),
        )
    connection.commit()
    store._project_revision = 3  # noqa: SLF001


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


def test_cursor_pages_are_global_monotonic_and_snapshot_after_page_is_coherent(store) -> None:
    projection = ProjectViewProjection(store)

    first = projection.events_after(0, 2)
    assert first == {
        "project_revision": 3,
        "authority_generation": 7,
        "cursor": 2,
        "events": [
            {
                "cursor": 1,
                "event_id": "evt_1",
                "project_revision": 1,
                "trigger_kind": "internal_trigger",
                "kind": "fixture.event",
                "created_at": "2026-07-18T10:00:01Z",
            },
            {
                "cursor": 2,
                "event_id": "evt_2",
                "project_revision": 2,
                "trigger_kind": "internal_trigger",
                "kind": "fixture.event",
                "created_at": "2026-07-18T10:00:02Z",
            },
        ],
        "has_more": True,
        "limit": 2,
    }
    second = projection.events_after(first["cursor"], 2)
    assert [item["cursor"] for item in second["events"]] == [3]
    assert second["cursor"] == 3
    assert second["has_more"] is False

    store._connection.execute(  # noqa: SLF001 - deterministic concurrent commit
        "INSERT INTO events(event_cursor,event_id,project_id,project_revision,"
        "trigger_kind,kind,provenance_json,payload_json,internal_trigger_id,created_at) "
        "VALUES (4, 'evt_4', 'prj_1', 4, 'internal_trigger', 'fixture.event', ?, ?, "
        "'int_4', '2026-07-18T10:00:04Z')",
        (
            _canonical(
                {
                    "internal_trigger_id": "int_4",
                    "source_revision": 3,
                    "source_snapshot_id": "snapshot_3",
                }
            ),
            _canonical({}),
        ),
    )
    store._connection.execute(  # noqa: SLF001
        "UPDATE projects SET revision = 4 WHERE project_id = 'prj_1'"
    )
    store._project_revision = 4  # noqa: SLF001

    later = projection.events_after(second["cursor"], 2)
    snapshot = projection.snapshot("v2")
    assert [item["cursor"] for item in later["events"]] == [4]
    assert snapshot["project_revision"] == later["project_revision"] == 4
    assert snapshot["event_cursor"] == later["cursor"] == 4
    assert snapshot["authority"]["generation"] == later["authority_generation"] == 7


@pytest.mark.parametrize("cursor", [-1, True, None, 2**63])
def test_invalid_cursor_rejects_before_reader_and_writes_nothing(
    store, monkeypatch: pytest.MonkeyPatch, cursor: object
) -> None:
    calls = 0

    def forbidden(_self):
        nonlocal calls
        calls += 1
        raise AssertionError("reader must not open")

    monkeypatch.setattr(SQLiteMissionStore, "open_reader", forbidden)
    before = store._connection.total_changes  # noqa: SLF001
    with pytest.raises(ValueError, match="^event cursor invalid$"):
        ProjectViewProjection(store).events_after(cursor, 10)  # type: ignore[arg-type]
    assert calls == 0
    assert store._connection.total_changes == before  # noqa: SLF001


@pytest.mark.parametrize("limit", [0, -1, True, None, 101, 2**63])
def test_invalid_limit_rejects_before_reader_and_writes_nothing(
    store, monkeypatch: pytest.MonkeyPatch, limit: object
) -> None:
    calls = 0

    def forbidden(_self):
        nonlocal calls
        calls += 1
        raise AssertionError("reader must not open")

    monkeypatch.setattr(SQLiteMissionStore, "open_reader", forbidden)
    before = store._connection.total_changes  # noqa: SLF001
    with pytest.raises(ValueError, match="^event page limit invalid$"):
        ProjectViewProjection(store).events_after(0, limit)  # type: ignore[arg-type]
    assert calls == 0
    assert store._connection.total_changes == before  # noqa: SLF001


def test_reconnect_validates_hidden_event_json_and_sanitizes_failure(store) -> None:
    store._connection.execute(  # noqa: SLF001
        "UPDATE events SET payload_json = ? WHERE event_cursor = 2",
        ('{ "prompt": "PRIVATE" }',),
    )

    with pytest.raises(ProjectionError) as raised:
        ProjectViewProjection(store).events_after(0, 10)

    assert str(raised.value) == "ProjectView event reconnect unavailable"
    assert "PRIVATE" not in str(raised.value)


def test_reconnect_validates_lookahead_before_claiming_has_more(store) -> None:
    store._connection.execute(  # noqa: SLF001
        "UPDATE events SET provenance_json = ? WHERE event_cursor = 2",
        ('{ "internal_trigger_id": "PRIVATE" }',),
    )

    with pytest.raises(ProjectionError, match="^ProjectView event reconnect unavailable$"):
        ProjectViewProjection(store).events_after(0, 1)


def test_reconnect_accepts_bounded_canonical_non_object_event_payload(store) -> None:
    store._connection.execute(  # noqa: SLF001
        "UPDATE events SET payload_json = ? WHERE event_cursor = 1",
        (_canonical(["bounded-fact"]),),
    )

    page = ProjectViewProjection(store).events_after(0, 1)

    assert page["cursor"] == 1
    assert "bounded-fact" not in str(page)


def test_reader_is_closed_on_success_and_projection_failure(
    store, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = SQLiteMissionStore.open_reader
    closed: list[bool] = []

    class ReaderProxy:
        def __init__(self, reader):
            self.reader = reader

        def execute(self, *args, **kwargs):
            return self.reader.execute(*args, **kwargs)

        def commit(self):
            return self.reader.commit()

        def rollback(self):
            return self.reader.rollback()

        def close(self):
            self.reader.close()
            closed.append(True)

    def tracked(self):
        return ReaderProxy(original(self))

    monkeypatch.setattr(SQLiteMissionStore, "open_reader", tracked)
    assert ProjectViewProjection(store).events_after(0, 1)["cursor"] == 1
    assert closed == [True]

    store._connection.execute(  # noqa: SLF001
        "UPDATE events SET payload_json = ? WHERE event_cursor = 1",
        ('{ "prompt": "PRIVATE" }',),
    )
    with pytest.raises(ProjectionError):
        ProjectViewProjection(store).events_after(0, 1)
    assert closed == [True, True]
