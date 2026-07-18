"""Single-writer, command-atomic project-local SQLite authority."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from hashlib import sha256
import os
from pathlib import Path
import sqlite3

from agentdeck.adapters.sqlite_schema import (
    FileIdentity,
    _REQUIRED_TABLES,
    StoreCommandStateError,
    StoreSchemaError,
    StoreSerializationError,
    _configure_durability,
    _configure_local,
    _connect_database,
    _connect_inspection_database,
    _database_path,
    _migration_statements,
    _migrate_schema,
    _no_op_path_hook as _after_command_commit,
    _no_op_path_hook as _after_inspection_validation,
    _no_op_path_hook as _after_migrate,
    _require_database_identity,
    _resolved_project_root,
    _state_directory,
    _validate_before_durability,
    _validate_existing_schema,
)
from agentdeck.adapters.sqlite_validation import (
    StateIdentity,
    StoreWriterBusyError,
    _ATTEMPT_COLUMNS,
    _acquire_writer_lock,
    _attempt_record,
    _attempt_from_row,
    _attempt_fingerprint,
    _canonical,
    _decode_canonical,
    _event_record,
    _release_writer_lock,
    _require_state_identity,
    _session_record,
    _state_identity,
    _timestamp,
    _validate_command_row,
)
from agentdeck.adapters.system_clock import SystemClock
from agentdeck.kernel.events import DomainEvent
from agentdeck.kernel.execution import AttemptState
from agentdeck.ports.clock import Clock
from agentdeck.ports.store import (
    CommandResult,
    RunningAttempt,
    STORE_COMMAND_ID_MAX_BYTES,
    STORE_COMMAND_KIND_MAX_BYTES,
    StoreTransaction,
)


class StoreCommandConflictError(RuntimeError): pass


def _validate_text_reference(value: object, label: str, maximum: int) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} must be strict UTF-8") from error
    if len(encoded) > maximum:
        raise ValueError(f"{label} is too large")


def _validate_command_reference(command_id: object, command_kind: object | None) -> None:
    _validate_text_reference(command_id, "command_id", STORE_COMMAND_ID_MAX_BYTES)
    if command_kind is not None:
        _validate_text_reference(command_kind, "command_kind", STORE_COMMAND_KIND_MAX_BYTES)


def _migrate(connection: sqlite3.Connection, root: Path) -> None:
    _migrate_schema(connection, root, _migration_statements())


def _open_inspection_connection(
    path: Path, expected: FileIdentity, root: Path
) -> sqlite3.Connection:
    connection = _connect_inspection_database(path)
    try:
        _require_database_identity(path, expected)
        _configure_local(connection)
        connection.execute("PRAGMA query_only = ON")
        _validate_existing_schema(connection, root)
        _after_inspection_validation(path)
        _require_database_identity(path, expected)
        return connection
    except BaseException:
        connection.close()
        raise


class _SQLiteCommandTransaction:
    def __init__(self, store: "SQLiteStore", command_id: str, command_kind: str) -> None:
        self._store = store
        self.command_id = command_id
        self.command_kind = command_kind
        self._active = True
        self._result: CommandResult | None = None
        self.duplicate_result: CommandResult | None = None

    def _require_mutable(self) -> sqlite3.Connection:
        if not self._active:
            raise StoreCommandStateError("command transaction is inactive")
        if self.duplicate_result is not None:
            raise StoreCommandStateError("duplicate command transaction is read-only")
        return self._store._require_writer()

    def set_result(self, result: CommandResult) -> None:
        self._require_mutable()
        _, self._result = _canonical(result)

    def lookup_command(self, command_id: str, command_kind: str | None = None) -> CommandResult | None:
        return self._store._lookup_command(self._store._require_writer(), command_id, command_kind)

    def record_command(
        self, command_id: str, command_kind: str, result: CommandResult
    ) -> None:
        if (command_id, command_kind) != (self.command_id, self.command_kind):
            raise StoreCommandStateError("transaction cannot record another command")
        self.set_result(result)

    def load_aggregate(self, aggregate_type: str, aggregate_id: str) -> CommandResult | None:
        return self._store._load_aggregate(
            self._store._require_writer(), aggregate_type, aggregate_id
        )

    def save_aggregate(
        self, aggregate_type: str, aggregate_id: str, snapshot: CommandResult
    ) -> None:
        _validate_text_reference(aggregate_type, "aggregate_type", 128)
        _validate_text_reference(aggregate_id, "aggregate_id", 255)
        if aggregate_type == "product_sessions":
            if snapshot.get("session_id", aggregate_id) != aggregate_id:
                raise ValueError("aggregate identity does not match session_id")
            self.save_session({"session_id": aggregate_id, **snapshot})
            return
        if aggregate_type == "attempts":
            if snapshot.get("attempt_id", aggregate_id) != aggregate_id:
                raise ValueError("aggregate identity does not match attempt_id")
            self.save_attempt({"attempt_id": aggregate_id, **snapshot})
            return
        raise ValueError("unsupported aggregate type")

    def save_session(self, snapshot: Mapping[str, object]) -> None:
        connection = self._require_mutable()
        if not isinstance(snapshot, Mapping):
            raise StoreSerializationError("session snapshot must be an object")
        now = _timestamp(self._store._clock)
        session_id = snapshot.get("session_id")
        existing = connection.execute(
            "SELECT project_id,created_at,updated_at FROM product_sessions WHERE session_id=?",
            (session_id,),
        ).fetchone() if type(session_id) is str else None
        record = _session_record(snapshot, self._store._project_id, now, existing)
        connection.execute(
            "INSERT OR IGNORE INTO projects VALUES (?, ?, ?)",
            (record[1], str(self._store._project_root), now),
        )
        connection.execute(
            """INSERT INTO product_sessions VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                 state=excluded.state, permission_profile=excluded.permission_profile,
                 pending_goal=excluded.pending_goal, updated_at=excluded.updated_at""",
            record,
        )

    def save_attempt(self, snapshot: Mapping[str, object]) -> None:
        connection = self._require_mutable()
        if not isinstance(snapshot, Mapping):
            raise StoreSerializationError("attempt snapshot must be an object")
        now = _timestamp(self._store._clock)
        attempt_id = snapshot.get("attempt_id")
        existing = connection.execute(
            """SELECT attempt_id,task_id,agent_instance_id,ordinal,state,reason,
                      result_summary,retryable,acp_session_id,effect_observed,
                      created_at,updated_at FROM attempts WHERE attempt_id=?""",
            (attempt_id,),
        ).fetchone() if type(attempt_id) is str else None
        record = _attempt_record(snapshot, existing, now)
        connection.execute(
            """INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(attempt_id) DO UPDATE SET state=excluded.state,
                 reason=excluded.reason, result_summary=excluded.result_summary,
                 retryable=excluded.retryable,
                 effect_observed=excluded.effect_observed, updated_at=excluded.updated_at""",
            record,
        )

    def recover_attempt(
        self, attempt_id: str, state: AttemptState, reason: str | None,
        *, expected: RunningAttempt | None = None,
    ) -> None:
        connection = self._require_mutable()
        if state not in {AttemptState.RUNNING, AttemptState.INTERRUPTED, AttemptState.OUTCOME_UNKNOWN}:
            raise ValueError("unsupported recovery state")
        row = connection.execute(
            f"SELECT {','.join(_ATTEMPT_COLUMNS)} FROM attempts WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()
        if row is None:
            raise StoreCommandStateError("running attempt disappeared before recovery")
        attempt, values = _attempt_from_row(row)
        if attempt.state is not AttemptState.RUNNING:
            raise StoreCommandStateError("running attempt changed before recovery")
        current = RunningAttempt(
            attempt.attempt_id, attempt.task_id, values["acp_session_id"],
            bool(values["effect_observed"]), _attempt_fingerprint(values),
        )
        if expected is None or expected.durable_fingerprint is None:
            raise StoreCommandStateError("recovery requires a durable attempt fingerprint")
        if current != expected:
            raise StoreCommandStateError("running attempt drifted after reconciliation")
        if state is AttemptState.RUNNING:
            if reason is not None:
                raise ValueError("confirmed running recovery cannot have a reason")
        else:
            attempt.transition(state, reason=reason)
        changed = connection.execute(
            """UPDATE attempts SET state=?, reason=?, result_summary=NULL, retryable=0,
               updated_at=? WHERE attempt_id=? AND state='running'""",
            (state.value, reason, _timestamp(self._store._clock), attempt_id),
        ).rowcount
        if changed != 1:
            raise StoreCommandStateError("running attempt changed before recovery")

    def append_event(self, event: DomainEvent | Mapping[str, object]) -> None:
        connection = self._require_mutable()
        record = _event_record(event, self.command_id)
        connection.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
            (record[0], self.command_id, *record[1:]),
        )


class SQLiteStore:
    """Own one writer lock or an exact read-only project inspection handle."""

    def __init__(
        self, path: Path, identity: FileIdentity, project_root: Path, clock: Clock,
        *, writer: sqlite3.Connection | None, inspection: sqlite3.Connection | None,
        lock_descriptor: int | None, state_identity: StateIdentity,
    ) -> None:
        self.path = path
        self._database_identity = identity
        self._state_path = path.parent
        self._state_identity = state_identity
        self._project_root = project_root
        self._project_id = f"prj_{sha256(str(project_root).encode()).hexdigest()[:24]}"
        self._clock = clock
        self._writer = writer
        self._inspection = inspection
        self._lock_descriptor = lock_descriptor
        self._closed = False
        self._active_transaction: _SQLiteCommandTransaction | None = None

    @classmethod
    def open(
        cls, project_root: str | os.PathLike[str], *, clock: Clock | None = None
    ) -> "SQLiteStore":
        root = _resolved_project_root(project_root)
        state = _state_directory(root, create=True)
        lock_descriptor: int | None = None
        connection: sqlite3.Connection | None = None
        try:
            lock_descriptor, _, state_identity = _acquire_writer_lock(state)
            path, identity = _database_path(state, create=True)
            connection = _connect_database(path)
            _require_database_identity(path, identity)
            _configure_local(connection)
            _validate_before_durability(connection, root)
            _configure_durability(connection)
            _migrate(connection, root)
            _require_database_identity(path, identity)
            _after_migrate(path)
            _require_database_identity(path, identity)
            _require_state_identity(state, state_identity)
            return cls(
                path, identity, root, clock or SystemClock(), writer=connection,
                inspection=None, lock_descriptor=lock_descriptor,
                state_identity=state_identity,
            )
        except BaseException:
            try:
                if connection is not None:
                    connection.close()
            finally:
                _release_writer_lock(lock_descriptor)
            raise

    @classmethod
    def open_read_only(
        cls, project_root: str | os.PathLike[str], *, clock: Clock | None = None
    ) -> "SQLiteStore":
        root = _resolved_project_root(project_root)
        state = _state_directory(root, create=False)
        state_identity = _state_identity(state)
        path, identity = _database_path(state, create=False)
        inspection = _open_inspection_connection(path, identity, root)
        try:
            _require_state_identity(state, state_identity)
        except BaseException:
            inspection.close()
            raise
        return cls(
            path, identity, root, clock or SystemClock(), writer=None,
            inspection=inspection, lock_descriptor=None, state_identity=state_identity,
        )

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("SQLiteStore is closed")
        _require_state_identity(self._state_path, self._state_identity)
        _require_database_identity(self.path, self._database_identity)

    def _require_writer(self) -> sqlite3.Connection:
        self._require_open()
        if self._writer is None:
            raise StoreCommandStateError("read-only store cannot mutate")
        return self._writer

    @property
    def connection(self) -> sqlite3.Connection:
        self._require_open()
        if self._inspection is None:
            self._inspection = _open_inspection_connection(
                self.path, self._database_identity, self._project_root
            )
        return self._inspection

    def _read_connection(self) -> sqlite3.Connection:
        if self._active_transaction is not None:
            return self._require_writer()
        return self.connection

    def _lookup_command(self, connection: sqlite3.Connection, command_id: str,
                        command_kind: str | None) -> CommandResult | None:
        _validate_command_reference(command_id, command_kind)
        row = connection.execute(
            """SELECT command_kind, state, canonical_result_facts, created_at, completed_at
               FROM commands WHERE command_id=?""",
            (command_id,),
        ).fetchone()
        if row is None:
            return None
        if command_kind is not None and row[0] != command_kind:
            raise StoreCommandConflictError("command_id already has a different kind")
        return _validate_command_row(row)

    def lookup_command(self, command_id: str,
                       command_kind: str | None = None) -> CommandResult | None:
        return self._lookup_command(self._read_connection(), command_id, command_kind)

    def _load_aggregate(
        self, connection: sqlite3.Connection, aggregate_type: str, aggregate_id: str
    ) -> CommandResult | None:
        _validate_text_reference(aggregate_type, "aggregate_type", 128)
        _validate_text_reference(aggregate_id, "aggregate_id", 255)
        specs = {
            "product_sessions": ("session_id", (
                "session_id", "project_id", "state", "permission_profile", "pending_goal",
                "created_at", "updated_at",
            )),
            "attempts": ("attempt_id", (
                "attempt_id", "task_id", "agent_instance_id", "ordinal", "state", "reason",
                "result_summary", "retryable", "acp_session_id", "effect_observed",
                "created_at", "updated_at",
            )),
        }
        if aggregate_type not in specs:
            raise ValueError("unsupported aggregate type")
        identity, columns = specs[aggregate_type]
        row = connection.execute(
            f"SELECT {','.join(columns)} FROM {aggregate_type} WHERE {identity}=?",
            (aggregate_id,),
        ).fetchone()
        return None if row is None else dict(zip(columns, row, strict=True))

    def load_aggregate(self, aggregate_type: str, aggregate_id: str) -> CommandResult | None:
        return self._load_aggregate(self._read_connection(), aggregate_type, aggregate_id)

    def list_running_attempts(self):
        rows = self._read_connection().execute(
            f"""SELECT {','.join(_ATTEMPT_COLUMNS)}
               FROM attempts WHERE state='running' ORDER BY attempt_id"""
        ).fetchall()
        validated = (_attempt_from_row(row)[1] for row in rows)
        return tuple(RunningAttempt(
            row["attempt_id"], row["task_id"], row["acp_session_id"],
            bool(row["effect_observed"]), _attempt_fingerprint(row),
        ) for row in validated)

    def count(self, table: str) -> int:
        if table not in _REQUIRED_TABLES:
            raise ValueError("count requires an authority table name")
        return self._read_connection().execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    @contextmanager
    def command(
        self, command_id: str, command_kind: str
    ) -> Iterator[StoreTransaction]:
        connection = self._require_writer()
        if self._active_transaction is not None:
            raise StoreCommandStateError("another command transaction is active")
        _validate_command_reference(command_id, command_kind)
        _validate_existing_schema(connection, self._project_root)
        _require_database_identity(self.path, self._database_identity)
        connection.execute("BEGIN IMMEDIATE")
        transaction = _SQLiteCommandTransaction(self, command_id, command_kind)
        self._active_transaction = transaction
        committed = False
        try:
            row = connection.execute(
                """SELECT command_kind, state, canonical_result_facts, created_at, completed_at
                   FROM commands WHERE command_id=?""",
                (command_id,),
            ).fetchone()
            if row is not None:
                if row[0] != command_kind:
                    raise StoreCommandConflictError("command_id already has a different kind")
                transaction.duplicate_result = _validate_command_row(row)
                connection.execute("ROLLBACK")
                yield transaction
                return
            now = _timestamp(self._clock)
            connection.execute(
                "INSERT INTO commands VALUES (?, ?, 'started', NULL, ?, NULL)",
                (command_id, command_kind, now),
            )
            yield transaction
            result = transaction._result if transaction._result is not None else {}
            result_text, transaction._result = _canonical(result)
            connection.execute(
                """UPDATE commands SET state='completed', canonical_result_facts=?,
                   completed_at=? WHERE command_id=? AND state='started'""",
                (result_text, _timestamp(self._clock), command_id),
            )
            _require_state_identity(self._state_path, self._state_identity)
            _require_database_identity(self.path, self._database_identity)
            connection.execute("COMMIT")
            committed = True
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            transaction._active = False
            self._active_transaction = None
        if committed:
            _after_command_commit(self.path)
            _require_state_identity(self._state_path, self._state_identity)
            _require_database_identity(self.path, self._database_identity)

    def execute_once(self, command_id: str, command_kind: str, callback) -> CommandResult:
        with self.command(command_id, command_kind) as transaction:
            concrete = transaction
            if concrete.duplicate_result is not None:
                return _decode_canonical(_canonical(concrete.duplicate_result)[0])
            result = callback(concrete)
            concrete.set_result(result)
        return _decode_canonical(_canonical(concrete._result)[0])

    def close(self) -> None:
        if self._closed:
            return
        if self._active_transaction is not None:
            raise StoreCommandStateError("cannot close while a command transaction is active")
        self._closed = True
        inspection, self._inspection = self._inspection, None
        writer, self._writer = self._writer, None
        descriptor, self._lock_descriptor = self._lock_descriptor, None
        try:
            if inspection is not None:
                inspection.close()
            if writer is not None:
                writer.close()
        finally:
            _release_writer_lock(descriptor)
