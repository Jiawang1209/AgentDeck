from __future__ import annotations

import os
import shutil
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
from agentdeck.storage.migrations import apply_schema_v1


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


def _make_valid_database(root: Path, *, project_id: str = "prj_1") -> Path:
    root.mkdir()
    with ProjectWriterLease.acquire(root) as lease:
        with SQLiteMissionStore.create(root, lease=lease, project_id=project_id):
            pass
    return root / ".agentdeck" / "state.db"


def _rewrite_schema_sql(
    database: Path,
    *,
    object_type: str,
    name: str,
    old: str,
    new: str,
) -> None:
    connection = sqlite3.connect(database)
    try:
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = ? AND name = ?",
            (object_type, name),
        ).fetchone()[0]
        assert old in sql
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute(
            "UPDATE sqlite_master SET sql = ? WHERE type = ? AND name = ?",
            (sql.replace(old, new, 1), object_type, name),
        )
        schema_version = connection.execute("PRAGMA schema_version").fetchone()[0]
        connection.execute(f"PRAGMA schema_version={schema_version + 1}")
        connection.execute("PRAGMA writable_schema=OFF")
        connection.commit()
    finally:
        connection.close()


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


def test_database_replacement_before_reader_open_fails_closed_and_recovers(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    other_root = tmp_path / "other"
    root.mkdir()
    other_database = _make_valid_database(other_root, project_id="prj_other")

    with ProjectWriterLease.acquire(root) as lease:
        with SQLiteMissionStore.create(root, lease=lease, project_id="prj_1") as store:
            database = root / ".agentdeck" / "state.db"
            original = root / ".agentdeck" / "state.db.original"
            database.rename(original)
            other_database.replace(database)
            try:
                with pytest.raises(
                    SQLiteStoreError,
                    match="^SQLite authority identity invalid$",
                ) as raised:
                    store.open_reader()
                assert raised.value.__cause__ is None
            finally:
                database.unlink()
                original.rename(database)

            with store.open_reader() as reader:
                assert reader.execute(
                    "SELECT project_id FROM projects"
                ).fetchone() == ("prj_1",)


def test_database_replacement_during_reader_open_fails_closed_and_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentdeck.storage import sqlite_store

    root = tmp_path / "project"
    other_root = tmp_path / "other"
    root.mkdir()
    other_database = _make_valid_database(other_root, project_id="prj_other")

    with ProjectWriterLease.acquire(root) as lease:
        with SQLiteMissionStore.create(root, lease=lease, project_id="prj_1") as store:
            database = root / ".agentdeck" / "state.db"
            original = root / ".agentdeck" / "state.db.original"
            real_connect = sqlite_store.sqlite3.connect
            replaced = False

            def replacing_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
                nonlocal replaced
                reader = real_connect(*args, **kwargs)
                if kwargs.get("factory") is not None and not replaced:
                    database.rename(original)
                    other_database.replace(database)
                    replaced = True
                return reader

            monkeypatch.setattr(sqlite_store.sqlite3, "connect", replacing_connect)
            try:
                with pytest.raises(
                    SQLiteStoreError,
                    match="^SQLite authority identity invalid$",
                ):
                    store.open_reader()
            finally:
                monkeypatch.setattr(sqlite_store.sqlite3, "connect", real_connect)
                database.unlink()
                original.rename(database)

            with store.open_reader() as reader:
                assert reader.execute(
                    "SELECT project_id FROM projects"
                ).fetchone() == ("prj_1",)


@pytest.mark.parametrize(
    "mutation",
    ["wrong_column", "missing_foreign_key", "missing_index", "missing_check"],
)
def test_schema_fingerprint_rejects_structural_drift_without_mutating_bytes(
    tmp_path: Path,
    mutation: str,
) -> None:
    root = tmp_path / mutation
    database = _make_valid_database(root)
    if mutation == "wrong_column":
        _rewrite_schema_sql(
            database,
            object_type="table",
            name="projects",
            old="configuration_identity TEXT",
            new="configuration_identity BLOB",
        )
    elif mutation == "missing_foreign_key":
        _rewrite_schema_sql(
            database,
            object_type="table",
            name="sessions",
            old=(
                "attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id) "
                "ON DELETE RESTRICT"
            ),
            new="attempt_id TEXT NOT NULL",
        )
    elif mutation == "missing_index":
        connection = sqlite3.connect(database)
        try:
            connection.execute("DROP INDEX events_project_cursor_idx")
            connection.commit()
        finally:
            connection.close()
    else:
        _rewrite_schema_sql(
            database,
            object_type="table",
            name="projects",
            old=(
                "authority_generation INTEGER NOT NULL DEFAULT 0 "
                "CHECK (authority_generation >= 0)"
            ),
            new="authority_generation INTEGER NOT NULL DEFAULT 0",
        )

    with ProjectWriterLease.acquire(root) as lease:
        before = _snapshot_state_files(root)
        with pytest.raises(SQLiteStoreError, match="^SQLite schema invalid$"):
            SQLiteMissionStore.open(root, lease=lease)
        assert _snapshot_state_files(root) == before


def _event_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    apply_schema_v1(
        connection,
        project_id="prj_1",
        authority_state="sqlite_active",
    )
    connection.execute(
        "INSERT INTO commands("
        "command_id, project_id, input_hash, expected_revision, status, "
        "actor_json, created_at"
        ") VALUES ('cmd_1', 'prj_1', 'sha256:x', 0, 'accepted', '{}', '')"
    )
    return connection


def _insert_event(
    connection: sqlite3.Connection,
    *,
    event_id: str,
    trigger_kind: str,
    command_id: str | None = None,
    adapter_event_id: str | None = None,
    internal_trigger_id: str | None = None,
) -> None:
    connection.execute(
        "INSERT INTO events("
        "event_id, project_id, project_revision, trigger_kind, kind, "
        "provenance_json, payload_json, command_id, adapter_event_id, "
        "internal_trigger_id, created_at"
        ") VALUES (?, 'prj_1', 0, ?, 'observed', '{}', '{}', ?, ?, ?, '')",
        (
            event_id,
            trigger_kind,
            command_id,
            adapter_event_id,
            internal_trigger_id,
        ),
    )


def test_event_schema_accepts_each_closed_trigger_provenance() -> None:
    connection = _event_connection()
    try:
        _insert_event(
            connection,
            event_id="evt_client",
            trigger_kind="client_command",
            command_id="cmd_1",
        )
        _insert_event(
            connection,
            event_id="evt_adapter",
            trigger_kind="adapter_event",
            adapter_event_id="ae_1",
        )
        _insert_event(
            connection,
            event_id="evt_internal",
            trigger_kind="internal_trigger",
            internal_trigger_id="it_1",
        )
        assert connection.execute("SELECT count(*) FROM events").fetchone() == (3,)
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("trigger_kind", "command_id", "adapter_event_id", "internal_trigger_id"),
    [
        ("client_command", None, None, None),
        ("client_command", "cmd_1", "ae_1", None),
        ("adapter_event", None, None, None),
        ("adapter_event", "cmd_1", "ae_1", None),
        ("internal_trigger", None, None, None),
        ("internal_trigger", None, "ae_1", "it_1"),
    ],
)
def test_event_schema_rejects_missing_or_mixed_trigger_provenance(
    trigger_kind: str,
    command_id: str | None,
    adapter_event_id: str | None,
    internal_trigger_id: str | None,
) -> None:
    connection = _event_connection()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_event(
                connection,
                event_id="evt_invalid",
                trigger_kind=trigger_kind,
                command_id=command_id,
                adapter_event_id=adapter_event_id,
                internal_trigger_id=internal_trigger_id,
            )
    finally:
        connection.close()


def test_preflight_reads_committed_uncheckpointed_wal_authority(
    tmp_path: Path,
) -> None:
    from agentdeck.storage.sqlite_store import _wal_aware_preflight

    root = tmp_path / "project"
    database = _make_valid_database(root)
    raw_writer = sqlite3.connect(database)
    raw_writer.execute("PRAGMA journal_mode=WAL")
    raw_writer.execute("PRAGMA wal_autocheckpoint=0")
    raw_writer.execute(
        "UPDATE projects SET revision = 7, "
        "authority_state = 'sqlite_installed_quarantined'"
    )
    raw_writer.commit()

    immutable = sqlite3.connect(
        f"file:{database}?mode=ro&immutable=1",
        uri=True,
    )
    try:
        assert immutable.execute(
            "SELECT revision, authority_state FROM projects"
        ).fetchone() == (0, "sqlite_active")
    finally:
        immutable.close()

    before_preflight = _snapshot_state_files(root)
    preflight, _ = _wal_aware_preflight(database)
    assert preflight.revision == 7
    assert preflight.authority_state == "sqlite_installed_quarantined"
    assert _snapshot_state_files(root) == before_preflight

    try:
        with ProjectWriterLease.acquire(root) as lease:
            with SQLiteMissionStore.open(root, lease=lease) as store:
                assert store.authority_state == "sqlite_installed_quarantined"
                with store.open_reader() as reader:
                    assert reader.execute(
                        "SELECT revision, authority_state FROM projects"
                    ).fetchone() == (7, "sqlite_installed_quarantined")
    finally:
        raw_writer.close()


def test_create_install_never_overwrites_competing_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentdeck.storage import sqlite_store

    root = tmp_path / "project"
    root.mkdir()
    database = root / ".agentdeck" / "state.db"
    competitor = b"competitor-owned-bytes"
    real_link = sqlite_store.os.link
    real_replace = sqlite_store.os.replace
    injected = False

    def inject_competitor() -> None:
        nonlocal injected
        if injected:
            return
        database.write_bytes(competitor)
        os.chmod(database, 0o600)
        injected = True

    def racing_link(source: object, destination: object, **kwargs: object) -> None:
        if Path(os.fspath(destination)) == database:
            inject_competitor()
        real_link(source, destination, **kwargs)

    def racing_replace(source: object, destination: object) -> None:
        if Path(os.fspath(destination)) == database:
            inject_competitor()
        real_replace(source, destination)

    monkeypatch.setattr(sqlite_store.os, "link", racing_link)
    monkeypatch.setattr(sqlite_store.os, "replace", racing_replace)
    with ProjectWriterLease.acquire(root) as lease:
        created: SQLiteMissionStore | None = None
        try:
            with pytest.raises(SQLiteStoreError, match="^SQLite state path invalid$"):
                created = SQLiteMissionStore.create(
                    root,
                    lease=lease,
                    project_id="prj_1",
                )
        finally:
            if created is not None:
                created.close()
        assert injected is True
        assert database.read_bytes() == competitor


def test_create_cleanup_never_deletes_replacement_after_successful_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentdeck.storage import sqlite_store

    root = tmp_path / "project"
    root.mkdir()
    database = root / ".agentdeck" / "state.db"
    displaced = root / ".agentdeck" / "state.db.installed"
    competitor = b"replacement-after-link"
    real_link = sqlite_store.os.link

    def replacing_link(source: object, destination: object, **kwargs: object) -> None:
        real_link(source, destination, **kwargs)
        database.rename(displaced)
        database.write_bytes(competitor)
        os.chmod(database, 0o600)

    monkeypatch.setattr(sqlite_store.os, "link", replacing_link)
    with ProjectWriterLease.acquire(root) as lease:
        with pytest.raises(
            SQLiteStoreError,
            match="^SQLite authority identity invalid$",
        ):
            SQLiteMissionStore.create(root, lease=lease, project_id="prj_1")
        assert database.read_bytes() == competitor


@pytest.mark.parametrize("race", ["disappear", "replace"])
def test_open_writer_connect_race_never_creates_or_deletes_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    race: str,
) -> None:
    from agentdeck.storage import sqlite_store

    root = tmp_path / "project"
    database = _make_valid_database(root)
    original_bytes = database.read_bytes()
    saved = root / ".agentdeck" / "state.db.saved"
    competitor_bytes = b""
    if race == "replace":
        other_root = tmp_path / "other"
        other_database = _make_valid_database(other_root, project_id="prj_other")
        competitor_bytes = other_database.read_bytes()

    real_connect = sqlite_store.sqlite3.connect
    injected = False

    def racing_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        nonlocal injected
        target = args[0] if args else kwargs.get("database")
        target_text = os.fspath(target) if target is not None else ""
        is_writer_open = (
            target == database
            or (
                isinstance(target_text, str)
                and "mode=rw" in target_text
                and str(database) in target_text
            )
        )
        if is_writer_open and not injected:
            database.rename(saved)
            if race == "replace":
                database.write_bytes(competitor_bytes)
                os.chmod(database, 0o600)
            injected = True
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite_store.sqlite3, "connect", racing_connect)
    try:
        with ProjectWriterLease.acquire(root) as lease:
            with pytest.raises(
                SQLiteStoreError,
                match="^SQLite authority identity invalid$",
            ):
                SQLiteMissionStore.open(root, lease=lease)
            assert injected is True
            assert saved.read_bytes() == original_bytes
            if race == "disappear":
                assert not database.exists()
            else:
                assert database.read_bytes() == competitor_bytes
    finally:
        monkeypatch.setattr(sqlite_store.sqlite3, "connect", real_connect)
        if database.exists():
            database.unlink()
        if saved.exists():
            shutil.move(saved, database)


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
@pytest.mark.parametrize("operation", ["property", "reader"])
def test_active_store_rejects_replaced_database_sidecar_identity(
    tmp_path: Path,
    suffix: str,
    operation: str,
) -> None:
    root = tmp_path / "project"
    other_root = tmp_path / "other"
    database = _make_valid_database(root)
    other_database = _make_valid_database(other_root, project_id="prj_other")
    other_writer = sqlite3.connect(other_database)
    other_writer.execute("PRAGMA journal_mode=WAL")
    other_writer.execute("PRAGMA wal_autocheckpoint=0")
    other_writer.execute("UPDATE projects SET revision = 99")
    other_writer.commit()
    assert other_writer.execute(
        "SELECT revision FROM projects"
    ).fetchone() == (99,)

    try:
        with ProjectWriterLease.acquire(root) as lease:
            with SQLiteMissionStore.open(root, lease=lease) as store:
                sidecar = Path(f"{database}{suffix}")
                other_sidecar = Path(f"{other_database}{suffix}")
                saved = root / ".agentdeck" / f"state.db{suffix}.original"
                assert sidecar.is_file()
                assert other_sidecar.is_file()
                sidecar.rename(saved)
                shutil.copyfile(other_sidecar, sidecar)
                os.chmod(sidecar, 0o600)
                reader: sqlite3.Connection | None = None
                try:
                    with pytest.raises(
                        SQLiteStoreError,
                        match="^SQLite authority identity invalid$",
                    ) as raised:
                        if operation == "property":
                            _ = store.project_id
                        else:
                            reader = store.open_reader()
                    assert raised.value.__cause__ is None
                finally:
                    if reader is not None:
                        reader.close()
                    sidecar.unlink()
                    saved.rename(sidecar)
    finally:
        other_writer.close()
