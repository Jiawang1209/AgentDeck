from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

import pytest

from agentdeck.storage import (
    AUTHORITY_STATES,
    SCHEMA_TABLES,
    ProjectWriterLease,
    SQLiteMissionStore,
    SQLiteStoreError,
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)


def _snapshot_state_files(root: Path) -> dict[str, bytes]:
    state_dir = root / ".agentdeck"
    return {
        path.name: path.read_bytes()
        for path in sorted(state_dir.iterdir())
        if path.name.startswith("state.db") and path.is_file()
    }


def _make_database(root: Path, statements: tuple[str, ...]) -> Path:
    state_dir = root / ".agentdeck"
    state_dir.mkdir(mode=0o700)
    os.chmod(state_dir, 0o700)
    path = state_dir / "state.db"
    connection = sqlite3.connect(path)
    try:
        for statement in statements:
            connection.execute(statement)
        connection.commit()
    finally:
        connection.close()
    os.chmod(path, 0o600)
    return path


def test_create_installs_schema_v1_with_closed_authority_and_pragmas(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()

    with ProjectWriterLease.acquire(root) as lease:
        with SQLiteMissionStore.create(
            root,
            lease=lease,
            project_id="prj_1",
            authority_state="sqlite_active",
        ) as store:
            assert store.schema_version == 1
            assert store.project_id == "prj_1"
            assert store.authority_state == "sqlite_active"
            with store.open_reader() as reader:
                tables = {
                    row[0]
                    for row in reader.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                    )
                }
                assert tables == set(SCHEMA_TABLES)
                assert reader.execute("PRAGMA foreign_keys").fetchone()[0] == 1
                assert reader.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
                assert reader.execute("PRAGMA synchronous").fetchone()[0] == 2
                assert reader.execute("PRAGMA query_only").fetchone()[0] == 1
                assert reader.execute(
                    "SELECT version FROM schema_migrations"
                ).fetchall() == [(1,)]
                assert reader.execute(
                    "SELECT revision, authority_state FROM projects "
                    "WHERE project_id = 'prj_1'"
                ).fetchone() == (0, "sqlite_active")
                with pytest.raises(sqlite3.OperationalError):
                    reader.execute(
                        "UPDATE projects SET revision = 1 WHERE project_id = 'prj_1'"
                    )

        state_dir = root / ".agentdeck"
        assert _mode(state_dir) == 0o700
        assert _mode(state_dir / "state.db") == 0o600
        assert _mode(state_dir / "state.db.lock") == 0o600
        for suffix in ("-wal", "-shm"):
            sidecar = state_dir / f"state.db{suffix}"
            if sidecar.exists():
                assert _mode(sidecar) == 0o600


@pytest.mark.parametrize("authority_state", sorted(AUTHORITY_STATES))
def test_create_accepts_each_closed_authority_state(
    tmp_path: Path,
    authority_state: str,
) -> None:
    root = tmp_path / authority_state
    root.mkdir()
    with ProjectWriterLease.acquire(root) as lease:
        with SQLiteMissionStore.create(
            root,
            lease=lease,
            project_id="prj_1",
            authority_state=authority_state,
        ) as store:
            assert store.authority_state == authority_state


def test_create_rejects_invalid_authority_without_partial_database(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    with ProjectWriterLease.acquire(root) as lease:
        with pytest.raises(SQLiteStoreError, match="SQLite authority state invalid"):
            SQLiteMissionStore.create(
                root,
                lease=lease,
                project_id="prj_1",
                authority_state="maybe_active",
            )
        assert not (root / ".agentdeck" / "state.db").exists()
        assert not list((root / ".agentdeck").glob(".state.db.*"))


def test_create_sanitizes_non_string_authority_and_invalid_project_identity(
    tmp_path: Path,
) -> None:
    authority_root = tmp_path / "authority"
    authority_root.mkdir()
    with ProjectWriterLease.acquire(authority_root) as lease:
        with pytest.raises(SQLiteStoreError, match="SQLite authority state invalid"):
            SQLiteMissionStore.create(
                authority_root,
                lease=lease,
                project_id="prj_1",
                authority_state=[],  # type: ignore[arg-type]
            )

    identity_root = tmp_path / "identity"
    identity_root.mkdir()
    with ProjectWriterLease.acquire(identity_root) as lease:
        with pytest.raises(SQLiteStoreError, match="SQLite project identity invalid"):
            SQLiteMissionStore.create(
                identity_root,
                lease=lease,
                project_id="bad\ud800",
            )


def test_create_is_atomic_when_schema_installation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentdeck.storage import sqlite_store

    root = tmp_path / "project"
    root.mkdir()

    def fail_install(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected")

    monkeypatch.setattr(sqlite_store, "apply_schema_v1", fail_install)
    with ProjectWriterLease.acquire(root) as lease:
        with pytest.raises(RuntimeError, match="injected"):
            SQLiteMissionStore.create(root, lease=lease, project_id="prj_1")
        assert not (root / ".agentdeck" / "state.db").exists()
        assert not list((root / ".agentdeck").glob(".state.db.*"))


@pytest.mark.parametrize(
    ("statements", "message"),
    [
        (
            (
                "CREATE TABLE schema_migrations "
                "(version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)",
                "INSERT INTO schema_migrations VALUES (2, 'future', 'now')",
                "CREATE TABLE projects "
                "(project_id TEXT PRIMARY KEY, revision INTEGER, authority_state TEXT)",
                "INSERT INTO projects VALUES ('prj_1', 0, 'sqlite_active')",
            ),
            "unsupported SQLite schema",
        ),
        (
            (
                "CREATE TABLE projects "
                "(project_id TEXT PRIMARY KEY, revision INTEGER, authority_state TEXT)",
                "INSERT INTO projects VALUES ('prj_1', 0, 'sqlite_active')",
            ),
            "SQLite schema invalid",
        ),
        (
            (
                "CREATE TABLE schema_migrations "
                "(version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)",
                "INSERT INTO schema_migrations VALUES (1, 'v1', 'now')",
                "CREATE TABLE projects "
                "(project_id TEXT PRIMARY KEY, revision INTEGER, authority_state TEXT)",
                "INSERT INTO projects VALUES ('prj_1', 0, 'invented')",
            ),
            "SQLite authority state invalid",
        ),
    ],
)
def test_open_preflight_rejects_bad_database_without_changing_any_bytes(
    tmp_path: Path,
    statements: tuple[str, ...],
    message: str,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _make_database(root, statements)

    with ProjectWriterLease.acquire(root) as lease:
        before = _snapshot_state_files(root)
        with pytest.raises(SQLiteStoreError, match=message):
            SQLiteMissionStore.open(root, lease=lease)
        after = _snapshot_state_files(root)
        assert after == before


def test_open_rejects_non_regular_or_symlink_database(tmp_path: Path) -> None:
    outside = tmp_path / "outside.db"
    outside.write_bytes(b"not sqlite")
    os.chmod(outside, 0o600)

    symlink_root = tmp_path / "symlink-project"
    symlink_root.mkdir()
    state_dir = symlink_root / ".agentdeck"
    state_dir.mkdir(mode=0o700)
    (state_dir / "state.db").symlink_to(outside)
    with ProjectWriterLease.acquire(symlink_root) as lease:
        with pytest.raises(SQLiteStoreError, match="SQLite state path invalid"):
            SQLiteMissionStore.open(symlink_root, lease=lease)

    directory_root = tmp_path / "directory-project"
    directory_root.mkdir()
    state_dir = directory_root / ".agentdeck"
    state_dir.mkdir(mode=0o700)
    (state_dir / "state.db").mkdir()
    with ProjectWriterLease.acquire(directory_root) as lease:
        with pytest.raises(SQLiteStoreError, match="SQLite state path invalid"):
            SQLiteMissionStore.open(directory_root, lease=lease)


def test_malformed_database_error_is_fixed_and_redacted(tmp_path: Path) -> None:
    root = tmp_path / "sensitive-project-name"
    root.mkdir()
    state_dir = root / ".agentdeck"
    state_dir.mkdir(mode=0o700)
    database = state_dir / "state.db"
    database.write_bytes(b"not a sqlite database")
    os.chmod(database, 0o600)

    with ProjectWriterLease.acquire(root) as lease:
        with pytest.raises(SQLiteStoreError) as raised:
            SQLiteMissionStore.open(root, lease=lease)
    assert str(raised.value) == "SQLite schema invalid"
    assert raised.value.__cause__ is None
    assert str(root) not in str(raised.value)


def test_create_modes_are_deterministic_under_permissive_umask(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    old_umask = os.umask(0)
    try:
        with ProjectWriterLease.acquire(root) as lease:
            with SQLiteMissionStore.create(root, lease=lease, project_id="prj_1"):
                pass
            assert _mode(root / ".agentdeck") == 0o700
            assert _mode(root / ".agentdeck" / "state.db") == 0o600
            assert _mode(root / ".agentdeck" / "state.db.lock") == 0o600
    finally:
        os.umask(old_umask)


def test_valid_store_reopens_only_with_matching_active_lease(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    with ProjectWriterLease.acquire(root) as lease:
        with SQLiteMissionStore.create(root, lease=lease, project_id="prj_1"):
            pass
        with SQLiteMissionStore.open(root, lease=lease) as reopened:
            assert reopened.project_id == "prj_1"
            assert reopened.schema_version == 1
