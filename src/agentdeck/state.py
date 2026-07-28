from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import copy
from contextlib import contextmanager
import fcntl
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import time
import threading
from typing import Any, Callable, Iterable, Mapping
import weakref

from .config import CONFIG_DIR, ensure_project_layout, load_config, project_root
from .storage.shadow import (
    append_events_if_enabled as _shadow_append_events_if_enabled,
    mirror_if_enabled as _shadow_mirror_if_enabled,
)
from .conversation.lifecycle import validate_conversation_history
from .conversation.models import ConversationMutation
from .daemon.lifecycle import validate_daemon_record
from .daemon.lease import (
    controller_lease_is_active,
    LeaseTransition,
    LeaseError,
    validate_daemon_event_record,
    validate_daemon_event_outbox,
    validate_lease_transition,
)
from .mission import (
    DAEMON_MISSION_RESUME_BLOCKER,
    MISSION_SCHEMA_VERSION,
    MISSION_STATUSES,
    MISSION_INVALID_BLOCKERS_BLOCKER,
    compact_mission_blockers,
    compact_mission_worker_entries,
    daemon_mission_authority_state,
    is_canonical_mission_id,
    mission_commands,
    mission_status_transition_allowed,
)
from .mission_authority import (
    SEMANTIC_MISSION_COMPACT_FIELDS,
    canonical_workflow_plan_hash,
    semantic_mission_provenance_shape,
    validated_compiled_semantic_plan,
)
from .models import (
    MIGRATION_SCHEMA_VERSION,
    PROJECT_VIEW_SCHEMA_VERSION,
    PROJECT_VIEW_SEMANTIC_AUTHORITY_FIELDS,
    AgentRuntimeBinding,
    AgentSpec,
    EventRecord,
    ProjectConfig,
    ProjectView,
    RuntimeConfig,
    new_id,
    utc_now,
)
from .providers.semantic_plan_schema import SEMANTIC_LEADER_PLAN_SCHEMA_VERSION
from .runtime.protocol import (
    AGENT_SESSION_STATES,
    PERMISSION_STATES,
    TURN_STATES,
    TRANSPORT_KINDS,
    UPDATE_KINDS,
    TransportCapabilities,
    build_agent_session,
    build_permission_request,
    build_protocol_transition,
    build_transport_update,
    build_turn,
    validate_protocol_transition_record,
    validate_transition_edge,
)
from .runtime.acp_mapping import (
    MAX_ACP_TURN_PAYLOAD_BYTES,
    MAX_ACP_UPDATES_PER_TURN,
    MAX_ACP_TERMINAL_UPDATE_BYTES,
)
from .semantic_authority import (
    SEMANTIC_AUTHORITY_SCHEMA_VERSION,
    compact_semantic_authority,
    semantic_authority_hash,
)


_EXECUTION_SNAPSHOT_FIELDS = frozenset(
    {
        "mission",
        "workers",
        "policy",
        "limits",
        "mission_hash",
        "policy_hash",
        "execution_hash",
    }
)

_SEMANTIC_FROZEN_EVENT_PAYLOAD_FIELDS = frozenset(
    {
        "mission_id",
        "plan_id",
        "semantic_authority_hash",
        "compiled_task_hashes",
        "compiled_step_count",
        "policy_snapshot_hash",
        "preview_generation",
    }
)


def _validate_semantic_frozen_event_payload(value: object) -> dict[str, object]:
    if type(value) is not dict or set(value) != _SEMANTIC_FROZEN_EVENT_PAYLOAD_FIELDS:
        raise ValueError("semantic frozen event authority is invalid")
    task_hashes = value.get("compiled_task_hashes")
    if (
        not is_canonical_mission_id(value.get("mission_id"))
        or type(value.get("plan_id")) is not str
        or re.fullmatch(r"pln_[0-9a-f]{12}", value["plan_id"]) is None
        or type(task_hashes) is not list
        or not task_hashes
        or len(task_hashes) > 64
        or any(
            type(item) is not str
            or re.fullmatch(r"sha256:[0-9a-f]{64}", item) is None
            for item in task_hashes
        )
        or type(value.get("compiled_step_count")) is not int
        or value["compiled_step_count"] != len(task_hashes)
        or type(value.get("preview_generation")) is not int
        or value["preview_generation"] != 1
        or any(
            type(value.get(field)) is not str
            or re.fullmatch(r"sha256:[0-9a-f]{64}", value[field]) is None
            for field in ("semantic_authority_hash", "policy_snapshot_hash")
        )
    ):
        raise ValueError("semantic frozen event authority is invalid")
    return copy.deepcopy(value)


def _validate_execution_frozen_event_payload(value: object) -> dict[str, object]:
    if (
        type(value) is not dict
        or set(value) != {"mission_id", "snapshot_hash"}
        or not is_canonical_mission_id(value.get("mission_id"))
        or type(value.get("snapshot_hash")) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", value["snapshot_hash"]) is None
    ):
        raise ValueError("execution frozen event authority is invalid")
    return dict(value)

_DAEMON_ADMISSION_FIELDS = frozenset(
    {"state", "snapshot_hash", "blocker", "recovery_command", "updated_at"}
)
_INVALID_DAEMON_ADMISSION_BLOCKER = "invalid daemon admission state"

_PLAN_LEADER_GENERATION_BASE_FIELDS = (
    "provider",
    "model",
    "constraint_mode",
    "schema_version",
    "schema_hash",
    "attempt_count",
    "regeneration_used",
    "selected_agent_ids",
    "step_count",
)
_PLAN_LEADER_GENERATION_SEMANTIC_FIELDS = (
    "semantic_authority_schema_version",
    "semantic_authority_hash",
)
_PLAN_LEADER_GENERATION_MODES = frozenset(
    {"local", "json_object", "prompt_only", "native_json_schema"}
)
_PLAN_LEADER_SCHEMA_VERSION = "leader-plan/v1"
_MISSING_PLAN_LEADER_GENERATION = object()
_PLAN_LEADER_FORBIDDEN_KEY_PARTS = frozenset(
    {
        "prompt",
        "argv",
        "path",
        "credential",
        "credentials",
        "secret",
        "token",
        "password",
        "authorization",
    }
)

_MIGRATION_ADDITIVE_COLLECTIONS = (
    "mission_attempts",
    "mission_recovery_evidence",
    "mission_worker_replies",
    "mission_handoffs",
    "mission_permission_bindings",
    "recovery_decisions",
)


def migration_preview(
    root: Path,
    *,
    now: datetime | None = None,
    ttl_seconds: int = 600,
    expires_at: datetime | None = None,
) -> dict[str, object]:
    """Inspect one legacy project migration without changing any project file."""
    if type(ttl_seconds) is not int or ttl_seconds <= 0:
        raise ValueError("migration preview ttl must be positive")
    canonical_root = root.resolve()
    with _secure_project_state_directory(canonical_root) as (_deck_fd, state_fd):
        source_bytes = _read_state_bytes_at(state_fd)
    state = json.loads(source_bytes.decode("utf-8"))
    if not isinstance(state, dict):
        raise ValueError("migration source state is invalid")
    issued_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expiry = (
        expires_at.astimezone(timezone.utc)
        if expires_at is not None
        else issued_at + timedelta(seconds=ttl_seconds)
    )
    return _migration_preview_payload(source_bytes, state, expiry=expiry)


def _migration_preview_payload(
    source_bytes: bytes,
    state: dict[str, object],
    *,
    expiry: datetime,
) -> dict[str, object]:
    source_hash = "sha256:" + hashlib.sha256(source_bytes).hexdigest()
    legacy_missions: list[dict[str, object]] = []
    for mission in state.get("missions", []):
        if not isinstance(mission, dict):
            continue
        if daemon_mission_authority_state(mission) != "admitted":
            legacy_missions.append(
                {
                    "mission_id": mission.get("mission_id"),
                    "mode": "inspect_only",
                    "reason": "complete frozen execution authority is unavailable",
                    "inspect_command": (
                        f"agentdeck mission status --mission-id {mission.get('mission_id')}"
                    ),
                }
            )
    preview_id = f"mig_{source_hash.removeprefix('sha256:')[:12]}"
    for item in legacy_missions:
        mission_id = item.get("mission_id")
        item["reconfirm_command"] = (
            "agentdeck leader chat --message "
            f'"Reconfirm legacy Mission {mission_id} as a new Mission preview"'
        )
    markers = (
        "schema_generation", "legacy_mission_migrations",
        "migration_previews_consumed",
    )
    marker_count = sum(key in state for key in markers)
    completely_migrated = (
        marker_count == len(markers)
        and state.get("schema_generation") == "project-daemon-m2b/v1"
        and isinstance(state.get("legacy_mission_migrations"), list)
        and isinstance(state.get("migration_previews_consumed"), list)
        and all(isinstance(state.get(key), list) for key in _MIGRATION_ADDITIVE_COLLECTIONS)
    )
    if marker_count == 0:
        status = "ready"
        blockers: list[str] = []
        target_changes: list[dict[str, object]] = [
            {"path": key, "operation": "add", "value": []}
            for key in _MIGRATION_ADDITIVE_COLLECTIONS
            if key not in state
        ]
        target_changes.extend([
            {
                "path": "schema_generation",
                "operation": "add",
                "value": "project-daemon-m2b/v1",
            },
            {
                "path": "legacy_mission_migrations",
                "operation": "add",
                "value": copy.deepcopy(legacy_missions),
            },
            {
                "path": "migration_previews_consumed",
                "operation": "add",
                "value": [
                    {
                        "preview_id": preview_id,
                        "source_hash": source_hash,
                        "expires_at": expiry.isoformat(),
                    }
                ],
            },
        ])
    elif completely_migrated:
        status = "noop"
        blockers = ["project is already migrated"]
        target_changes = []
    else:
        status = "blocked"
        blockers = ["partial or inconsistent migration state"]
        target_changes = []
    facts = {
        "preview_id": preview_id,
        "source_hash": source_hash,
        "target_changes": target_changes,
        "legacy_missions": legacy_missions,
        "expires_at": expiry.isoformat(),
    }
    digest = canonical_snapshot_hash(facts)
    backup_path = f".agentdeck/backups/{preview_id}/state.json"
    confirm_command = (
        "agentdeck project migrate "
        f"--preview-id {preview_id} --source-hash {source_hash} "
        f"--digest {digest} --expires-at {expiry.isoformat()} --confirm"
    ) if status == "ready" else None
    payload = {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "mode": "migration_preview",
        "status": status,
        "blockers": blockers,
        "can_migrate": status == "ready",
        "preview_id": preview_id,
        "source_hash": source_hash,
        "target_changes": target_changes,
        "legacy_missions": legacy_missions,
        "backup_path": backup_path,
        "expires_at": expiry.isoformat(),
        "digest": digest,
        "consume_once": status == "ready",
        "confirm_command": confirm_command,
        "controls": [
            {
                "kind": "inspect",
                "label": "Inspect migration preview",
                "command": "agentdeck project migration-preview",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "migrate",
                "label": "Confirm additive migration" if status == "ready" else "Migration unavailable",
                "command": confirm_command or "agentdeck project migration-preview",
                "safety": "explicit_user",
                "enabled": status == "ready",
                "blocker": blockers[0] if blockers else None,
            },
        ],
    }
    _require_valid_migration_contract(payload)
    return payload


def _require_valid_migration_contract(payload: dict[str, object]) -> None:
    """Apply the public migration contract as a pre-print/pre-mutation gate."""
    from .contracts import validate_migration_contract

    validation = validate_migration_contract(payload)
    if validation != {"ok": True, "errors": []}:
        raise ValueError("migration contract validation failed")


_MIGRATION_DIRECTORY_FLAGS = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
)


@contextmanager
def _secure_project_state_directory(root: Path, *, create_state: bool = False):
    """Anchor project, .agentdeck, and state without following directory links."""
    descriptors: list[int] = []
    try:
        try:
            root_fd = os.open(root, _MIGRATION_DIRECTORY_FLAGS)
            descriptors.append(root_fd)
            deck_fd = os.open(".agentdeck", _MIGRATION_DIRECTORY_FLAGS, dir_fd=root_fd)
            descriptors.append(deck_fd)
            if create_state:
                try:
                    os.mkdir("state", 0o700, dir_fd=deck_fd)
                    os.fsync(deck_fd)
                except FileExistsError:
                    pass
            state_fd = os.open("state", _MIGRATION_DIRECTORY_FLAGS, dir_fd=deck_fd)
            descriptors.append(state_fd)
        except OSError as exc:
            raise ValueError("migration state directory is unsafe") from exc
        yield deck_fd, state_fd
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _read_state_bytes_at(state_fd: int, *, missing_ok: bool = False) -> bytes | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open("state.json", flags, dir_fd=state_fd)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise ValueError("migration state file is unsafe") from None
    except OSError as exc:
        raise ValueError("migration state file is unsafe") from exc
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(descriptor)


def _verify_regular_file_at(
    directory_fd: int,
    name: str,
    descriptor: int,
    *,
    error: str,
) -> None:
    """Prove an opened file is still the named regular file in its anchor."""
    opened = os.fstat(descriptor)
    try:
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError as exc:
        raise ValueError(error) from exc
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(current.st_mode)
        or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise ValueError(error)


_EVENT_JOURNAL_MAX_BYTES = 64 * 1024 * 1024


def _read_event_journal_snapshot_at(
    state_fd: int,
) -> tuple[bytes | None, tuple[int, int] | None]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open("events.jsonl", flags, dir_fd=state_fd)
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        raise ValueError("event journal is unsafe") from exc
    try:
        _verify_regular_file_at(
            state_fd,
            "events.jsonl",
            descriptor,
            error="event journal is unsafe",
        )
        opened = os.fstat(descriptor)
        if opened.st_size > _EVENT_JOURNAL_MAX_BYTES:
            raise ValueError("event journal exceeds size limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(
                descriptor,
                min(64 * 1024, _EVENT_JOURNAL_MAX_BYTES + 1 - total),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > _EVENT_JOURNAL_MAX_BYTES:
                raise ValueError("event journal exceeds size limit")
        _verify_regular_file_at(
            state_fd,
            "events.jsonl",
            descriptor,
            error="event journal changed during read",
        )
        return b"".join(chunks), (opened.st_dev, opened.st_ino)
    finally:
        os.close(descriptor)


def _read_event_journal_at(state_fd: int) -> bytes | None:
    return _read_event_journal_snapshot_at(state_fd)[0]


def _verify_event_journal_identity_at(
    state_fd: int, expected: tuple[int, int] | None
) -> None:
    try:
        current = os.stat("events.jsonl", dir_fd=state_fd, follow_symlinks=False)
    except FileNotFoundError:
        if expected is None:
            return
        raise ValueError("event journal changed during append") from None
    except OSError as exc:
        raise ValueError("event journal changed during append") from exc
    if (
        expected is None
        or not stat.S_ISREG(current.st_mode)
        or (current.st_dev, current.st_ino) != expected
    ):
        raise ValueError("event journal changed during append")


def _append_event_journal_at(state_fd: int, payload: bytes) -> None:
    source, expected_identity = _read_event_journal_snapshot_at(state_fd)
    content = (source or b"") + payload
    if len(content) > _EVENT_JOURNAL_MAX_BYTES:
        raise ValueError("event journal exceeds size limit")
    temporary = f".events.jsonl.{os.getpid()}.{new_id('tmp')}.tmp"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, flags, 0o600, dir_fd=state_fd)
    except OSError as exc:
        raise ValueError("event journal is unsafe") from exc
    try:
        _verify_regular_file_at(
            state_fd,
            temporary,
            descriptor,
            error="event journal is unsafe",
        )
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("event journal append made no progress")
            view = view[written:]
        os.fsync(descriptor)
        _verify_regular_file_at(
            state_fd,
            temporary,
            descriptor,
            error="event journal changed during append",
        )
        _verify_event_journal_identity_at(state_fd, expected_identity)
        _guard_mutation_effect_before_replace(state_fd, "events.jsonl")
        os.replace(
            temporary,
            "events.jsonl",
            src_dir_fd=state_fd,
            dst_dir_fd=state_fd,
        )
        _mark_mutation_effect_installed("events.jsonl")
        _verify_regular_file_at(
            state_fd,
            "events.jsonl",
            descriptor,
            error="event journal changed during append",
        )
        os.fsync(state_fd)
    finally:
        try:
            os.close(descriptor)
        finally:
            try:
                os.unlink(temporary, dir_fd=state_fd)
            except FileNotFoundError:
                pass


def _verify_project_state_anchor(root: Path, deck_fd: int, state_fd: int) -> None:
    """Fail if either anchored directory was replaced after it was opened."""
    with _secure_project_state_directory(root) as (current_deck_fd, current_state_fd):
        anchored_deck = os.fstat(deck_fd)
        current_deck = os.fstat(current_deck_fd)
        anchored_state = os.fstat(state_fd)
        current_state = os.fstat(current_state_fd)
        if (anchored_deck.st_dev, anchored_deck.st_ino) != (
            current_deck.st_dev, current_deck.st_ino
        ) or (anchored_state.st_dev, anchored_state.st_ino) != (
            current_state.st_dev, current_state.st_ino
        ):
            raise ValueError("migration state directory changed during confirmation")


def _verify_project_root_anchor(root: Path, root_fd: int) -> None:
    """Fail if the canonical project root no longer names the guarded inode."""
    try:
        current_fd = os.open(root, _MIGRATION_DIRECTORY_FLAGS)
    except OSError as exc:
        raise ValueError("project root changed during state mutation") from exc
    try:
        guarded = os.fstat(root_fd)
        current = os.fstat(current_fd)
        if (guarded.st_dev, guarded.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError("project root changed during state mutation")
    finally:
        os.close(current_fd)


def _verify_mutation_anchor(
    root: Path,
    root_fd: int,
    anchored: tuple[int, int],
) -> None:
    _verify_project_root_anchor(root, root_fd)
    _verify_project_state_anchor(root, anchored[0], anchored[1])


_STATE_SOURCE_TOKEN_KEY = "__agentdeck_transient_state_source__"


class _StateSourceToken(dict[str, object]):
    __slots__ = ("__weakref__",)
    __hash__ = object.__hash__

    def __deepcopy__(self, memo: dict[int, object]) -> _StateSourceToken:
        clone = _StateSourceToken()
        memo[id(self)] = clone
        with _LOADED_STATE_SOURCES_LOCK:
            facts = _STATE_SOURCE_FACTS.get(self)
            if facts is not None:
                _STATE_SOURCE_FACTS[clone] = facts
        return clone


def _persistent_state(state: Mapping[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in state.items()
        if key != _STATE_SOURCE_TOKEN_KEY
    }


def _encoded_state(state: Mapping[str, object]) -> bytes:
    return (
        json.dumps(
            _persistent_state(state),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _open_retained_regular_at(
    directory_fd: int,
    name: str,
    *,
    error: str = "state mutation target is unsafe",
) -> int | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(error) from exc
    try:
        _verify_regular_file_at(
            directory_fd,
            name,
            descriptor,
            error=error,
        )
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_retained_bytes(
    descriptor: int,
    *,
    max_bytes: int | None = None,
    limit_error: str = "state mutation target exceeds size limit",
) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, 64 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if max_bytes is not None and total > max_bytes:
            raise ValueError(limit_error)


def _named_regular_snapshot_at(
    directory_fd: int,
    name: str,
    *,
    max_bytes: int | None = None,
    limit_error: str = "state mutation target exceeds size limit",
) -> tuple[bytes, tuple[int, int]] | None:
    descriptor = _open_retained_regular_at(directory_fd, name)
    if descriptor is None:
        return None
    try:
        opened = os.fstat(descriptor)
        return _read_retained_bytes(
            descriptor,
            max_bytes=max_bytes,
            limit_error=limit_error,
        ), (opened.st_dev, opened.st_ino)
    finally:
        os.close(descriptor)


def _restore_displaced_file_if_exact_unlocked(
    directory_fd: int,
    name: str,
    *,
    installed: bytes,
    displaced_fd: int | None,
    max_bytes: int | None = None,
    limit_error: str = "state mutation target exceeds size limit",
) -> bool:
    """Restore displaced bytes only while the named file is still our exact install."""
    current = _named_regular_snapshot_at(
        directory_fd,
        name,
        max_bytes=max_bytes,
        limit_error=limit_error,
    )
    if current is None or current[0] != installed:
        return False
    expected_identity = current[1]
    if displaced_fd is None:
        latest = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (latest.st_dev, latest.st_ino) != expected_identity:
            return False
        os.unlink(name, dir_fd=directory_fd)
        os.fsync(directory_fd)
        return True
    restore = _read_retained_bytes(
        displaced_fd,
        max_bytes=max_bytes,
        limit_error=limit_error,
    )
    temporary = f".rollback-{name}.{os.getpid()}.{new_id('tmp')}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, flags, 0o600, dir_fd=directory_fd)
        view = memoryview(restore)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("state mutation rollback made no progress")
            view = view[written:]
        os.fsync(descriptor)
        latest = _named_regular_snapshot_at(
            directory_fd,
            name,
            max_bytes=max_bytes,
            limit_error=limit_error,
        )
        if latest is None or latest[0] != installed or latest[1] != expected_identity:
            return False
        os.replace(
            temporary,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
        return True
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def _restore_displaced_file_if_exact(
    directory_fd: int,
    name: str,
    *,
    installed: bytes,
    displaced_fd: int | None,
    held_lock_identity: tuple[int, int],
    max_bytes: int | None = None,
    limit_error: str = "state mutation target exceeds size limit",
) -> bool:
    """Serialize rollback with a replacement legacy writer, then restore by CAS."""
    replacement_fd: int | None = None
    replacement_locked = False
    try:
        try:
            named_lock = os.stat(
                "protocol-mutation.lock",
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise ValueError(
                "protocol mutation lock changed during state mutation"
            ) from exc
        named_identity = (named_lock.st_dev, named_lock.st_ino)
        if named_identity != held_lock_identity:
            replacement_fd = _open_retained_regular_at(
                directory_fd,
                "protocol-mutation.lock",
                error="protocol mutation lock changed during state mutation",
            )
            if replacement_fd is None:
                raise ValueError("protocol mutation lock changed during state mutation")
            fcntl.flock(replacement_fd, fcntl.LOCK_EX)
            replacement_locked = True
            _verify_regular_file_at(
                directory_fd,
                "protocol-mutation.lock",
                replacement_fd,
                error="protocol mutation lock changed during state mutation",
            )
        return _restore_displaced_file_if_exact_unlocked(
            directory_fd,
            name,
            installed=installed,
            displaced_fd=displaced_fd,
            max_bytes=max_bytes,
            limit_error=limit_error,
        )
    finally:
        if replacement_fd is not None:
            try:
                if replacement_locked:
                    fcntl.flock(replacement_fd, fcntl.LOCK_UN)
            finally:
                os.close(replacement_fd)


def _atomic_save_state_at(state_fd: int, state: dict[str, object]) -> None:
    temporary = f".state.json.{os.getpid()}.{new_id('tmp')}.tmp"
    flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, flags, 0o600, dir_fd=state_fd)
        encoded = _encoded_state(state)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(encoded)
            handle.flush()
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        _guard_mutation_effect_before_replace(state_fd, "state.json")
        os.replace(
            temporary, "state.json", src_dir_fd=state_fd, dst_dir_fd=state_fd
        )
        _mark_mutation_effect_installed("state.json")
        os.fsync(state_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=state_fd)
        except FileNotFoundError:
            pass


_STATE_MUTATION_LOCAL = threading.local()
_LOADED_STATE_SOURCES_LOCK = threading.Lock()
_STATE_SOURCE_FACTS: weakref.WeakKeyDictionary[
    _StateSourceToken, tuple[str, str | None]
] = weakref.WeakKeyDictionary()


def _state_lock_entries() -> dict[str, dict[str, object]]:
    entries = getattr(_STATE_MUTATION_LOCAL, "entries", None)
    if entries is None:
        entries = {}
        _STATE_MUTATION_LOCAL.entries = entries
    return entries


def _active_state_lock_entry() -> dict[str, object] | None:
    entries = _state_lock_entries()
    if len(entries) != 1:
        return None
    return next(iter(entries.values()))


def _guard_mutation_effect_before_replace(state_fd: int, target: str) -> None:
    """Lock the currently named legacy authority and repeat target CAS at commit."""
    entry = _active_state_lock_entry()
    if entry is None:
        return
    guard = entry.get("effect_guard")
    if not isinstance(guard, dict) or guard.get("target") != target:
        return
    held_lock_fd = entry.get("lock_fd")
    if not isinstance(held_lock_fd, int):
        raise RuntimeError("state mutation lock descriptor is invalid")
    held = os.fstat(held_lock_fd)
    held_identity = (held.st_dev, held.st_ino)
    try:
        named = os.stat(
            "protocol-mutation.lock",
            dir_fd=state_fd,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise ValueError("protocol mutation lock changed during state mutation") from exc
    named_identity = (named.st_dev, named.st_ino)
    authority_changed = named_identity != held_identity
    authority_fd = held_lock_fd
    if authority_changed:
        replacement_fd = _open_retained_regular_at(
            state_fd,
            "protocol-mutation.lock",
            error="protocol mutation lock changed during state mutation",
        )
        if replacement_fd is None:
            raise ValueError("protocol mutation lock changed during state mutation")
        try:
            fcntl.flock(replacement_fd, fcntl.LOCK_EX)
            _verify_regular_file_at(
                state_fd,
                "protocol-mutation.lock",
                replacement_fd,
                error="protocol mutation lock changed during state mutation",
            )
        except BaseException:
            os.close(replacement_fd)
            raise
        guard["replacement_lock_fd"] = replacement_fd
        authority_fd = replacement_fd
    _verify_regular_file_at(
        state_fd,
        "protocol-mutation.lock",
        authority_fd,
        error="protocol mutation lock changed during state mutation",
    )
    maximum = guard.get("max_bytes")
    max_bytes = maximum if isinstance(maximum, int) else None
    limit_error = guard.get("limit_error")
    if not isinstance(limit_error, str):
        limit_error = "state mutation target exceeds size limit"
    current = _named_regular_snapshot_at(
        state_fd,
        target,
        max_bytes=max_bytes,
        limit_error=limit_error,
    )
    expected_source = guard.get("source")
    expected_identity = guard.get("identity")
    expected = (
        None
        if expected_identity is None
        else (expected_source, expected_identity)
    )
    if current != expected:
        raise ValueError("protocol mutation lock changed during state mutation")
    _verify_regular_file_at(
        state_fd,
        "protocol-mutation.lock",
        authority_fd,
        error="protocol mutation lock changed during state mutation",
    )
    if authority_changed:
        raise ValueError("protocol mutation lock changed during state mutation")


def _release_effect_guard(entry: dict[str, object]) -> None:
    guard = entry.pop("effect_guard", None)
    if not isinstance(guard, dict):
        return
    replacement_fd = guard.get("replacement_lock_fd")
    if isinstance(replacement_fd, int):
        try:
            fcntl.flock(replacement_fd, fcntl.LOCK_UN)
        finally:
            os.close(replacement_fd)


def _mark_mutation_effect_installed(target: str) -> None:
    entry = _active_state_lock_entry()
    if entry is None:
        return
    guard = entry.get("effect_guard")
    if isinstance(guard, dict) and guard.get("target") == target:
        guard["effect_installed"] = True


def _open_migration_directory(parent_fd: int, name: str) -> int:
    try:
        descriptor = os.open(name, _MIGRATION_DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise ValueError("migration backup symlink or non-directory is forbidden") from exc
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError("migration backup path must be a directory")
    return descriptor


def _write_migration_backup(
    root: Path, preview_id: str, source: bytes, *, deck_fd: int | None = None
) -> Path:
    if re.fullmatch(r"mig_[0-9a-f]{12}", preview_id) is None:
        raise ValueError("migration backup preview id is invalid")
    root_fd: int | None = None
    agentdeck_fd: int | None = None
    backups_fd: int | None = None
    preview_fd: int | None = None
    backups_created = False
    preview_created = False
    temporary = f".state.json.{os.getpid()}.tmp"
    installed = False
    try:
        if deck_fd is None:
            root_fd = os.open(root, _MIGRATION_DIRECTORY_FLAGS)
            agentdeck_fd = _open_migration_directory(root_fd, ".agentdeck")
        else:
            agentdeck_fd = os.dup(deck_fd)
        try:
            os.mkdir("backups", 0o700, dir_fd=agentdeck_fd)
            backups_created = True
            os.fsync(agentdeck_fd)
        except FileExistsError:
            pass
        backups_fd = _open_migration_directory(agentdeck_fd, "backups")
        try:
            os.mkdir(preview_id, 0o700, dir_fd=backups_fd)
            preview_created = True
            os.fsync(backups_fd)
        except FileExistsError:
            try:
                metadata = os.stat(preview_id, dir_fd=backups_fd, follow_symlinks=False)
            except OSError as exc:
                raise ValueError("migration backup directory is unavailable") from exc
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise ValueError("migration backup symlink or non-directory is forbidden")
            raise ValueError("migration backup already exists")
        preview_fd = _open_migration_directory(backups_fd, preview_id)
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=preview_fd,
        )
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(source)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(
            temporary,
            "state.json",
            src_dir_fd=preview_fd,
            dst_dir_fd=preview_fd,
        )
        installed = True
        final_fd = os.open(
            "state.json", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=preview_fd,
        )
        try:
            os.fsync(final_fd)
        finally:
            os.close(final_fd)
        os.fsync(preview_fd)
        return root / ".agentdeck" / "backups" / preview_id / "state.json"
    except BaseException:
        if preview_fd is not None:
            for name in (temporary, "state.json" if installed else None):
                if name is None:
                    continue
                try:
                    os.unlink(name, dir_fd=preview_fd)
                except FileNotFoundError:
                    pass
            try:
                os.fsync(preview_fd)
            except OSError:
                pass
        if preview_created and backups_fd is not None:
            try:
                os.rmdir(preview_id, dir_fd=backups_fd)
                os.fsync(backups_fd)
            except OSError:
                pass
        if backups_created and agentdeck_fd is not None:
            try:
                os.rmdir("backups", dir_fd=agentdeck_fd)
                os.fsync(agentdeck_fd)
            except OSError:
                pass
        raise
    finally:
        for descriptor in (preview_fd, backups_fd, agentdeck_fd, root_fd):
            if descriptor is not None:
                os.close(descriptor)


def _remove_migration_backup(
    root: Path, preview_id: str, *, deck_fd: int | None = None
) -> None:
    root_fd: int | None = None
    agentdeck_fd: int | None = None
    backups_fd: int | None = None
    preview_fd: int | None = None
    try:
        if deck_fd is None:
            root_fd = os.open(root, _MIGRATION_DIRECTORY_FLAGS)
            agentdeck_fd = _open_migration_directory(root_fd, ".agentdeck")
        else:
            agentdeck_fd = os.dup(deck_fd)
        backups_fd = _open_migration_directory(agentdeck_fd, "backups")
        preview_fd = _open_migration_directory(backups_fd, preview_id)
        os.unlink("state.json", dir_fd=preview_fd)
        os.fsync(preview_fd)
        os.close(preview_fd)
        preview_fd = None
        os.rmdir(preview_id, dir_fd=backups_fd)
        os.fsync(backups_fd)
        try:
            os.rmdir("backups", dir_fd=agentdeck_fd)
            os.fsync(agentdeck_fd)
        except OSError:
            pass
    finally:
        for descriptor in (preview_fd, backups_fd, agentdeck_fd, root_fd):
            if descriptor is not None:
                os.close(descriptor)


def _atomic_restore_migration_source_at(state_fd: int, source: bytes) -> None:
    temporary = f".state.json.{os.getpid()}.migration-rollback.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=state_fd,
        )
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(source)
            handle.flush()
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(
            temporary, "state.json", src_dir_fd=state_fd, dst_dir_fd=state_fd
        )
        os.fsync(state_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=state_fd)
        except FileNotFoundError:
            pass


def confirm_migration(
    root: Path,
    *,
    preview_id: str,
    source_hash: str,
    digest: str,
    expires_at: str,
    confirm: bool,
    now: datetime | None = None,
) -> dict[str, object]:
    """Consume one exact migration preview and atomically install additive state."""
    if confirm is not True:
        raise ValueError("migration requires exact confirmation")
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    started_monotonic = time.monotonic()
    try:
        expiry = datetime.fromisoformat(expires_at)
    except (TypeError, ValueError):
        raise ValueError("migration preview expiry is invalid") from None
    if expiry.tzinfo is None or expiry.utcoffset() is None:
        raise ValueError("migration preview expiry is invalid")
    expiry = expiry.astimezone(timezone.utc)
    if current_time >= expiry:
        raise ValueError("migration preview expired")
    canonical_root = root.resolve()
    _preflight_migration_confirmation(
        canonical_root,
        preview_id=preview_id,
        source_hash=source_hash,
        digest=digest,
        expiry=expiry,
    )
    store = StateStore.open_existing(canonical_root)
    with store._protocol_mutation_lock() as anchored:
        deck_fd, state_fd = anchored
        _verify_project_state_anchor(canonical_root, deck_fd, state_fd)
        fresh_time = (
            datetime.now(timezone.utc)
            if now is None
            else current_time + timedelta(seconds=time.monotonic() - started_monotonic)
        )
        if fresh_time >= expiry:
            raise ValueError("migration preview expired")
        return _confirm_migration_locked(
            store,
            root=root.resolve(),
            deck_fd=deck_fd,
            state_fd=state_fd,
            preview_id=preview_id,
            source_hash=source_hash,
            digest=digest,
            expiry=expiry,
        )


def _preflight_migration_confirmation(
    root: Path,
    *,
    preview_id: str,
    source_hash: str,
    digest: str,
    expiry: datetime,
) -> None:
    """Reject already-stale or replayed confirmation before lock-file creation."""
    with _secure_project_state_directory(root) as (_deck_fd, state_fd):
        source = _read_state_bytes_at(state_fd)
    state = json.loads(source.decode("utf-8"))
    if not isinstance(state, dict):
        raise ValueError("migration source state is invalid")
    consumed = state.get("migration_previews_consumed", [])
    if isinstance(consumed, list) and any(
        isinstance(item, dict) and item.get("preview_id") == preview_id
        for item in consumed
    ):
        raise ValueError("migration preview already consumed")
    if "sha256:" + hashlib.sha256(source).hexdigest() != source_hash:
        raise ValueError("migration source facts drifted")
    expected = migration_preview(
        root,
        now=expiry - timedelta(seconds=600),
        expires_at=expiry,
    )
    if expected["preview_id"] != preview_id:
        raise ValueError("unknown migration preview")
    if expected["digest"] != digest:
        raise ValueError("migration preview digest mismatch")


def _confirm_migration_locked(
    store: "StateStore",
    *,
    root: Path,
    deck_fd: int,
    state_fd: int,
    preview_id: str,
    source_hash: str,
    digest: str,
    expiry: datetime,
) -> dict[str, object]:
    """Apply one exact migration while the protocol mutation lock is held."""
    current_source = _read_state_bytes_at(state_fd)
    current_state = json.loads(current_source.decode("utf-8"))
    if not isinstance(current_state, dict):
        raise ValueError("migration source state is invalid")
    consumed = current_state.get("migration_previews_consumed", [])
    if isinstance(consumed, list) and any(
        isinstance(item, dict) and item.get("preview_id") == preview_id
        for item in consumed
    ):
        raise ValueError("migration preview already consumed")
    current_hash = "sha256:" + hashlib.sha256(current_source).hexdigest()
    if current_hash != source_hash:
        raise ValueError("migration source facts drifted")
    expected = _migration_preview_payload(current_source, current_state, expiry=expiry)
    if expected.get("status") != "ready" or expected.get("can_migrate") is not True:
        raise ValueError("migration preview is not actionable")
    if expected["preview_id"] != preview_id:
        raise ValueError("unknown migration preview")
    if expected["digest"] != digest:
        raise ValueError("migration preview digest mismatch")
    proposed = copy.deepcopy(current_state)
    for change in expected["target_changes"]:
        if not isinstance(change, dict):
            raise ValueError("migration target change is invalid")
        path = change.get("path")
        if (
            type(path) is not str
            or change.get("operation") != "add"
            or path in proposed
        ):
            raise ValueError("migration is not additive")
        proposed[path] = copy.deepcopy(change.get("value"))
    backup_bytes = (
        json.dumps(
            {
                "backup_version": "project-daemon-m2b/v1",
                "source_hash": source_hash,
                "affected_state": {
                    str(item["path"]): {"present": False}
                    for item in expected["target_changes"]
                    if isinstance(item, dict) and isinstance(item.get("path"), str)
                },
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    response = {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "mode": "migration_confirmed",
        "preview_id": preview_id,
        "source_hash": source_hash,
        "digest": digest,
        "backup_path": expected["backup_path"],
        "legacy_missions": expected["legacy_missions"],
        "target_changes": expected["target_changes"],
        "consumed": True,
    }
    _require_valid_migration_contract(response)
    _verify_project_state_anchor(root, deck_fd, state_fd)
    _write_migration_backup(root, preview_id, backup_bytes, deck_fd=deck_fd)
    try:
        _verify_project_state_anchor(root, deck_fd, state_fd)
        if _read_state_bytes_at(state_fd) != current_source:
            raise ValueError("migration source facts drifted")
        store._atomic_save(proposed)
    except BaseException:
        installed = _read_state_bytes_at(state_fd)
        if installed == current_source:
            try:
                _remove_migration_backup(root, preview_id, deck_fd=deck_fd)
            except (OSError, ValueError):
                pass
        raise
    return response


def compact_daemon_admission(
    value: object,
    *,
    mission_id: str,
    confirmation_command: str,
    safe_updated_at: object,
) -> tuple[dict[str, object], bool]:
    """Project exact daemon-admission facts without forwarding unknown state."""
    fallback_updated_at = safe_updated_at if type(safe_updated_at) is str else None
    fallback = {
        "state": "not_confirmed",
        "snapshot_hash": None,
        "blocker": "Invalid daemon admission state",
        "recovery_command": confirmation_command,
        "updated_at": fallback_updated_at,
    }
    if type(value) is not dict or set(value) != _DAEMON_ADMISSION_FIELDS:
        return fallback, True
    state = value.get("state")
    snapshot_hash = value.get("snapshot_hash")
    blocker = value.get("blocker")
    recovery_command = value.get("recovery_command")
    updated_at = value.get("updated_at")
    timestamp_valid = updated_at is None or (
        type(updated_at) is str and bool(updated_at)
    )
    hash_valid = (
        type(snapshot_hash) is str
        and re.fullmatch(r"sha256:[0-9a-f]{64}", snapshot_hash) is not None
    )
    expected_recovery = (
        f"agentdeck mission run --mission-id {mission_id} --confirm"
    )
    valid = False
    if type(state) is str and state == "not_confirmed":
        valid = (
            snapshot_hash is None
            and type(blocker) is str
            and bool(blocker)
            and type(recovery_command) is str
            and recovery_command == confirmation_command
            and timestamp_valid
        )
    elif type(state) is str and state == "confirmed_not_admitted":
        valid = (
            hash_valid
            and type(blocker) is str
            and bool(blocker)
            and type(recovery_command) is str
            and recovery_command == expected_recovery
            and type(updated_at) is str
            and bool(updated_at)
        )
    elif type(state) is str and state == "admitted":
        valid = (
            hash_valid
            and blocker is None
            and recovery_command is None
            and type(updated_at) is str
            and bool(updated_at)
        )
    if not valid:
        return fallback, True
    return {
        "state": state,
        "snapshot_hash": snapshot_hash,
        "blocker": blocker,
        "recovery_command": recovery_command,
        "updated_at": updated_at,
    }, False


_EXECUTION_SNAPSHOT_MAX_BYTES = 256 * 1024
_EXECUTION_SNAPSHOT_MAX_DEPTH = 32
_MISSION_ATTEMPT_FIELDS = frozenset(
    {
        "attempt_id",
        "mission_id",
        "step_id",
        "agent_id",
        "configured_transport",
        "dispatch_key",
        "admission_claim_id",
        "snapshot_hash",
        "state",
        "created_at",
        "updated_at",
        "receipt_summary",
        "blocker",
        "terminal_reason",
    }
)
_SEMANTIC_MISSION_ATTEMPT_FIELDS = _MISSION_ATTEMPT_FIELDS | {
    "semantic_step_hash"
}
_PROTOCOL_TURN_FIELDS = frozenset(
    {
        "turn_id",
        "session_id",
        "message_id",
        "kind",
        "state",
        "created_at",
        "updated_at",
    }
)
_AGENT_SESSION_FIELDS = frozenset(
    {
        "session_id",
        "agent_id",
        "provider",
        "transport",
        "native_session_id",
        "workspace",
        "capabilities",
        "state",
        "created_at",
        "updated_at",
        "observation_bindings",
    }
)
_PERMISSION_RECORD_FIELDS = frozenset(
    {
        "permission_id",
        "session_id",
        "turn_id",
        "tool_name",
        "target",
        "risk",
        "status",
        "decision",
        "created_at",
        "updated_at",
    }
)
_TRANSPORT_CAPABILITY_FIELDS = frozenset(
    {
        "structured_sessions",
        "streaming_updates",
        "structured_tools",
        "permission_requests",
        "resume_session",
        "observable_terminal",
    }
)
_MISSION_ATTEMPT_STATES = frozenset(
    {
        "prepared",
        "admitting",
        "submitted",
        "running",
        "completed",
        "succeeded",
        "failed",
        "cancelled",
        "interrupted",
        "ambiguous",
    }
)
_MISSION_ATTEMPT_ACTIVE_STATES = frozenset(
    {"prepared", "admitting", "submitted", "running"}
)
_MISSION_ATTEMPT_RETRYABLE_STATES = frozenset({"failed"})


class MissionStateError(ValueError):
    pass


def _strict_event_journal_ids(
    source: bytes | None,
    *,
    malformed_error: str,
    duplicate_error: str,
) -> set[str]:
    if source is None:
        return set()
    event_ids: list[str] = []

    def reject_duplicate_keys(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(_value: str) -> object:
        raise ValueError("non-finite JSON number")

    try:
        lines = source.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        raise ValueError(malformed_error) from None
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(
                line,
                object_pairs_hook=reject_duplicate_keys,
                parse_constant=reject_constant,
            )
            event_ids.append(validate_daemon_event_record(item))
        except (json.JSONDecodeError, LeaseError, ValueError):
            raise ValueError(malformed_error) from None
    if len(event_ids) != len(set(event_ids)):
        raise ValueError(duplicate_error)
    return set(event_ids)


def _validated_protocol_event_outbox_ids(value: object) -> set[str]:
    if type(value) is not list:
        raise ValueError("protocol event outbox is invalid")
    event_ids: list[str] = []
    for item in value:
        try:
            event_ids.append(validate_daemon_event_record(item))
        except LeaseError:
            raise ValueError("protocol event outbox is invalid") from None
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("duplicate protocol event identity")
    return set(event_ids)


def _validate_snapshot_json(value: object, *, depth: int = 0) -> None:
    if depth > _EXECUTION_SNAPSHOT_MAX_DEPTH:
        raise ValueError("execution snapshot nesting is invalid")
    if value is None:
        return
    if type(value) is bool or type(value) is float:
        raise ValueError("execution snapshot value is invalid")
    if type(value) is int:
        if not -(2**63) <= value < 2**63:
            raise ValueError("execution snapshot integer is invalid")
        return
    if type(value) is str:
        if "\x00" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("execution snapshot string is invalid")
        return
    if type(value) is list:
        for item in value:
            _validate_snapshot_json(item, depth=depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or not key:
                raise ValueError("execution snapshot key is invalid")
            _validate_snapshot_json(key, depth=depth + 1)
            _validate_snapshot_json(item, depth=depth + 1)
        return
    raise ValueError("execution snapshot value is invalid")


def _canonical_snapshot_bytes(value: object) -> bytes:
    _validate_snapshot_json(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (UnicodeEncodeError, ValueError, TypeError):
        raise ValueError("execution snapshot encoding is invalid") from None
    if len(encoded) > _EXECUTION_SNAPSHOT_MAX_BYTES:
        raise ValueError("execution snapshot is too large")
    return encoded


def canonical_snapshot_hash(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_snapshot_bytes(value)).hexdigest()}"


def worker_runtime_identity_hash(
    agent: AgentSpec, runtime_config: RuntimeConfig
) -> str:
    """Freeze executable/instruction identity without persisting raw invocation data."""
    if not isinstance(agent, AgentSpec) or not isinstance(runtime_config, RuntimeConfig):
        raise TypeError("Worker runtime identity requires AgentSpec")
    return canonical_snapshot_hash(
        {
            "command": agent.command,
            "transport_command": list(agent.transport_command),
            "role_prompt": agent.role_prompt,
            "runtime": {
                "backend": runtime_config.backend,
                "session_name": runtime_config.session_name,
                "socket_name": runtime_config.socket_name,
            },
        }
    )


def _is_force_stop_terminal_ambiguity(
    mission: Mapping[str, object],
    attempts: Iterable[Mapping[str, object]],
    facts: object,
    classification: object,
) -> bool:
    """Recognize only the force-stop ambiguity preserved by interrupted Missions."""
    from .daemon.recovery import RecoveryFacts

    mission_id = mission.get("mission_id")
    if not isinstance(facts, RecoveryFacts):
        return False
    return (
        mission.get("status") == "interrupted"
        and mission.get("stop_reason") == "force_daemon_stop"
        and classification == "ambiguous"
        and facts.mission_id == mission_id
        and facts.attempt_id is not None
        and facts.attempt_state == "ambiguous"
        and any(
            item.get("mission_id") == mission_id
            and item.get("attempt_id") == facts.attempt_id
            and item.get("state") == "ambiguous"
            and item.get("terminal_reason")
            == "force_daemon_stop_outcome_unknown"
            for item in attempts
        )
    )


def _recovery_authority_hash(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("recovery authority encoding is invalid") from None
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _validate_mission_attempt_record(value: object) -> dict[str, Any]:
    if type(value) is not dict or frozenset(value) not in {
        _MISSION_ATTEMPT_FIELDS,
        _SEMANTIC_MISSION_ATTEMPT_FIELDS,
    }:
        raise ValueError("mission attempt state invalid")
    if (
        type(value.get("attempt_id")) is not str
        or re.fullmatch(r"mat_[0-9a-f]{12}", value["attempt_id"]) is None
        or not is_canonical_mission_id(value.get("mission_id"))
        or type(value.get("step_id")) is not str
        or re.fullmatch(r"step_[1-9][0-9]*", value["step_id"]) is None
        or type(value.get("agent_id")) is not str
        or not value["agent_id"]
        or value.get("configured_transport") not in {"acp", "tmux"}
        or type(value.get("dispatch_key")) is not str
        or re.fullmatch(r"dsp_[0-9a-f]{32}", value["dispatch_key"]) is None
        or (
            value.get("admission_claim_id") is not None
            and (
                type(value.get("admission_claim_id")) is not str
                or re.fullmatch(r"adm_[0-9a-f]{12}", value["admission_claim_id"])
                is None
            )
        )
        or type(value.get("snapshot_hash")) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", value["snapshot_hash"]) is None
        or value.get("state") not in _MISSION_ATTEMPT_STATES
        or (
            "semantic_step_hash" in value
            and (
                type(value.get("semantic_step_hash")) is not str
                or re.fullmatch(
                    r"sha256:[0-9a-f]{64}", value["semantic_step_hash"]
                ) is None
            )
        )
    ):
        raise ValueError("mission attempt state invalid")
    timestamps: list[datetime] = []
    for field in ("created_at", "updated_at"):
        raw = value.get(field)
        if type(raw) is not str:
            raise ValueError("mission attempt state invalid")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            raise ValueError("mission attempt state invalid") from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("mission attempt state invalid")
        timestamps.append(parsed)
    if timestamps[1] < timestamps[0]:
        raise ValueError("mission attempt state invalid")
    if value["state"] == "prepared" and timestamps[1] != timestamps[0]:
        raise ValueError("mission attempt state invalid")
    if value["state"] == "prepared" and value["admission_claim_id"] is not None:
        raise ValueError("mission attempt state invalid")
    pre_dispatch_stopped = (
        value["state"] in {"cancelled", "interrupted"}
        and value["admission_claim_id"] is None
        and value["receipt_summary"] is None
    )
    if (
        value["state"] != "prepared"
        and not pre_dispatch_stopped
        and value["admission_claim_id"] is None
    ):
        raise ValueError("mission attempt state invalid")
    for field in ("receipt_summary", "blocker", "terminal_reason"):
        item = value.get(field)
        if item is not None and (type(item) is not str or not item):
            raise ValueError("mission attempt state invalid")
    if (
        value["state"]
        in {
            "submitted",
            "running",
            "completed",
            "succeeded",
            "failed",
            "cancelled",
            "interrupted",
            "ambiguous",
        }
        and value["receipt_summary"] is None
        and not pre_dispatch_stopped
    ):
        raise ValueError("mission attempt state invalid")
    return copy.deepcopy(value)


def _validate_protocol_turn_record(value: object) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _PROTOCOL_TURN_FIELDS:
        raise ValueError("protocol turn record is invalid")
    if (
        type(value.get("turn_id")) is not str
        or re.fullmatch(r"trn_[a-z0-9]+", value["turn_id"]) is None
        or type(value.get("session_id")) is not str
        or re.fullmatch(r"ags_[a-z0-9]+", value["session_id"]) is None
        or type(value.get("message_id")) is not str
        or not value["message_id"].strip()
        or value.get("kind") not in {"prompt", "load_replay"}
        or value.get("state") not in TURN_STATES
    ):
        raise ValueError("protocol turn record is invalid")
    try:
        created_at = datetime.fromisoformat(value["created_at"])
        updated_at = datetime.fromisoformat(value["updated_at"])
    except (TypeError, ValueError):
        raise ValueError("protocol turn record is invalid") from None
    if (
        created_at.tzinfo is None
        or created_at.utcoffset() is None
        or updated_at.tzinfo is None
        or updated_at.utcoffset() is None
        or updated_at < created_at
    ):
        raise ValueError("protocol turn record is invalid")
    return copy.deepcopy(value)


def _validated_aware_record_times(value: dict[str, Any], error: str) -> None:
    try:
        created_at = datetime.fromisoformat(value["created_at"])
        updated_at = datetime.fromisoformat(value["updated_at"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(error) from None
    if (
        created_at.tzinfo is None
        or created_at.utcoffset() is None
        or updated_at.tzinfo is None
        or updated_at.utcoffset() is None
        or updated_at < created_at
    ):
        raise ValueError(error)


def _validate_agent_session_record(value: object) -> dict[str, Any]:
    error = "agent session record is invalid"
    if type(value) is not dict or set(value) != _AGENT_SESSION_FIELDS:
        raise ValueError(error)
    capabilities = value.get("capabilities")
    observation_bindings = value.get("observation_bindings")
    native_session_id = value.get("native_session_id")
    if (
        type(value.get("session_id")) is not str
        or re.fullmatch(r"ags_[a-z0-9]+", value["session_id"]) is None
        or any(
            type(value.get(field)) is not str or not value[field].strip()
            for field in ("agent_id", "provider", "workspace")
        )
        or value.get("transport") not in TRANSPORT_KINDS
        or (
            native_session_id is not None
            and (type(native_session_id) is not str or not native_session_id.strip())
        )
        or type(capabilities) is not dict
        or set(capabilities) != _TRANSPORT_CAPABILITY_FIELDS
        or any(type(item) is not bool for item in capabilities.values())
        or value.get("state") not in AGENT_SESSION_STATES
        or type(observation_bindings) is not list
        or any(type(item) is not dict for item in observation_bindings)
    ):
        raise ValueError(error)
    _validated_aware_record_times(value, error)
    return copy.deepcopy(value)


def _validate_permission_record(value: object) -> dict[str, Any]:
    error = "permission record is invalid"
    if type(value) is not dict or set(value) != _PERMISSION_RECORD_FIELDS:
        raise ValueError(error)
    decision = value.get("decision")
    if (
        type(value.get("permission_id")) is not str
        or re.fullmatch(r"prm_[a-z0-9]+", value["permission_id"]) is None
        or type(value.get("session_id")) is not str
        or re.fullmatch(r"ags_[a-z0-9]+", value["session_id"]) is None
        or type(value.get("turn_id")) is not str
        or re.fullmatch(r"trn_[a-z0-9]+", value["turn_id"]) is None
        or any(
            type(value.get(field)) is not str or not value[field].strip()
            for field in ("tool_name", "target", "risk")
        )
        or value.get("status") != "pending"
        or decision is not None
    ):
        raise ValueError(error)
    _validated_aware_record_times(value, error)
    return copy.deepcopy(value)


def validate_mission_attempt_record(value: object) -> dict[str, Any]:
    """Validate the exact durable Task 7 Mission attempt record."""
    return _validate_mission_attempt_record(value)


def _require_unique_mission_attempt_lineage(
    attempts: list[dict[str, Any]],
) -> None:
    attempt_ids = [item["attempt_id"] for item in attempts]
    if len(attempt_ids) != len(set(attempt_ids)):
        raise ValueError("duplicate mission attempt identity")
    dispatch_keys = [item["dispatch_key"] for item in attempts]
    if len(dispatch_keys) != len(set(dispatch_keys)):
        raise ValueError("duplicate mission attempt dispatch key")
    claim_ids = [
        item["admission_claim_id"]
        for item in attempts
        if item["admission_claim_id"] is not None
    ]
    if len(claim_ids) != len(set(claim_ids)):
        raise ValueError("duplicate mission admission claim identity")
    active_by_mission: dict[str, int] = {}
    for item in attempts:
        if item["state"] in _MISSION_ATTEMPT_ACTIVE_STATES:
            mission_id = item["mission_id"]
            active_by_mission[mission_id] = active_by_mission.get(mission_id, 0) + 1
    if any(count > 1 for count in active_by_mission.values()):
        raise ValueError("multiple active Mission attempts")


_MISSION_CLAIM_EVENT_FIELDS = {
    "mission_attempt_admission_claimed": {
        "attempt_id",
        "mission_id",
        "step_id",
        "dispatch_key",
        "admission_claim_id",
    },
    "mission_attempt_admission_released": {
        "attempt_id",
        "mission_id",
        "step_id",
        "dispatch_key",
        "admission_claim_id",
    },
    "mission_attempt_submitted": {
        "attempt_id",
        "mission_id",
        "step_id",
        "dispatch_key",
        "admission_claim_id",
        "reason",
    },
    "mission_attempt_ambiguous": {
        "attempt_id",
        "mission_id",
        "step_id",
        "dispatch_key",
        "admission_claim_id",
        "reason",
        "expected_dispatch_key",
        "observed_dispatch_key",
    },
}
_PRE_DISPATCH_STOP_EVENT_FIELDS = {
    "attempt_id", "mission_id", "step_id", "dispatch_key", "state", "reason"
}
_ACP_COMPLETION_STAGES = {"prompt", "update", "parse", "finish", "cleanup"}


def _acp_completion_ambiguity_reason(stage: object) -> str:
    if type(stage) is not str or stage not in _ACP_COMPLETION_STAGES:
        raise ValueError("ACP completion stage is invalid")
    return f"acp_completion_{stage}_outcome_unknown"


def _is_acp_completion_ambiguity_reason(reason: object) -> bool:
    return type(reason) is str and reason in {
        _acp_completion_ambiguity_reason(stage)
        for stage in _ACP_COMPLETION_STAGES
    }


def _validate_mission_admission_claim_history(
    records: list[object], attempts: list[dict[str, Any]]
) -> dict[str, tuple[str, str]]:
    lineage: dict[str, tuple[str, str]] = {}
    stages: dict[str, str] = {}
    attempt_by_lineage = {
        (item["attempt_id"], item["dispatch_key"]): item for item in attempts
    }
    attempt_claims: dict[tuple[str, str], list[str]] = {}
    externally_closed: set[tuple[str, str]] = set()
    pre_dispatch_stops: dict[tuple[str, str], str] = {}
    for record in records:
        if type(record) is not dict:
            raise ValueError("mission admission claim history is invalid")
        try:
            validate_daemon_event_record(record)
        except LeaseError:
            raise ValueError("mission admission claim history is invalid") from None
        event_type = record.get("event_type")
        if event_type == "mission_attempt_pre_dispatch_stopped":
            payload = record.get("payload")
            if type(payload) is not dict or set(payload) != _PRE_DISPATCH_STOP_EVENT_FIELDS:
                raise ValueError("mission admission claim history is invalid")
            attempt_id = payload.get("attempt_id")
            dispatch_key = payload.get("dispatch_key")
            key = (attempt_id, dispatch_key)
            attempt = attempt_by_lineage.get(key)
            prior_claims = attempt_claims.get(key, [])
            if (
                attempt is None
                or payload.get("mission_id") != attempt["mission_id"]
                or payload.get("step_id") != attempt["step_id"]
                or payload.get("state") not in {"cancelled", "interrupted"}
                or type(payload.get("reason")) is not str
                or not payload["reason"]
                or key in pre_dispatch_stops
                or key in externally_closed
                or (prior_claims and stages.get(prior_claims[-1]) != "released")
            ):
                raise ValueError("mission admission claim history is invalid")
            pre_dispatch_stops[key] = payload["state"]
            continue
        required_fields = _MISSION_CLAIM_EVENT_FIELDS.get(event_type)
        if required_fields is None:
            continue
        payload = record.get("payload")
        if type(payload) is not dict or set(payload) != required_fields:
            raise ValueError("mission admission claim history is invalid")
        attempt_id = payload.get("attempt_id")
        mission_id = payload.get("mission_id")
        step_id = payload.get("step_id")
        dispatch_key = payload.get("dispatch_key")
        claim_id = payload.get("admission_claim_id")
        if (
            type(attempt_id) is not str
            or re.fullmatch(r"mat_[0-9a-f]{12}", attempt_id) is None
            or not is_canonical_mission_id(mission_id)
            or type(step_id) is not str
            or re.fullmatch(r"step_[1-9][0-9]*", step_id) is None
            or type(dispatch_key) is not str
            or re.fullmatch(r"dsp_[0-9a-f]{32}", dispatch_key) is None
            or type(claim_id) is not str
            or re.fullmatch(r"adm_[0-9a-f]{12}", claim_id) is None
        ):
            raise ValueError("mission admission claim history is invalid")
        claim_lineage = (attempt_id, dispatch_key)
        persisted_attempt = attempt_by_lineage.get(claim_lineage)
        if (
            persisted_attempt is None
            or persisted_attempt["mission_id"] != mission_id
            or persisted_attempt["step_id"] != step_id
        ):
            raise ValueError("mission admission claim history is invalid")
        if claim_lineage in pre_dispatch_stops:
            raise ValueError("mission admission claim history is invalid")
        if claim_id in lineage and lineage[claim_id] != claim_lineage:
            raise ValueError("mission admission claim history is invalid")
        prior_stage = stages.get(claim_id)
        if event_type == "mission_attempt_admission_claimed":
            prior_claims = attempt_claims.setdefault(claim_lineage, [])
            if (
                prior_stage is not None
                or claim_lineage in externally_closed
                or (prior_claims and stages.get(prior_claims[-1]) != "released")
            ):
                raise ValueError("mission admission claim history is invalid")
            prior_claims.append(claim_id)
            next_stage = "claimed"
        elif event_type == "mission_attempt_admission_released":
            if prior_stage != "claimed":
                raise ValueError("mission admission claim history is invalid")
            next_stage = "released"
        elif event_type == "mission_attempt_submitted":
            if prior_stage != "claimed" or payload.get("reason") is not None:
                raise ValueError("mission admission claim history is invalid")
            next_stage = "submitted"
            externally_closed.add(claim_lineage)
        else:
            reason = payload.get("reason")
            observed_dispatch_key = payload.get("observed_dispatch_key")
            allowed_prior_stages = (
                {"claimed"}
                if reason == "admission_outcome_unknown"
                else {"submitted"}
                if _is_acp_completion_ambiguity_reason(reason)
                else {"claimed", "submitted"}
            )
            if prior_stage not in allowed_prior_stages or (
                reason in {
                    "receipt_persistence_unknown",
                    "force_daemon_stop_outcome_unknown",
                }
                and (
                    type(observed_dispatch_key) is not str
                    or re.fullmatch(r"dsp_[0-9a-f]{32}", observed_dispatch_key)
                    is None
                )
            ) or (
                reason == "admission_outcome_unknown"
                and observed_dispatch_key is not None
            ) or (
                _is_acp_completion_ambiguity_reason(reason)
                and observed_dispatch_key != dispatch_key
            ) or (
                reason not in {
                    "receipt_persistence_unknown",
                    "admission_outcome_unknown",
                    "force_daemon_stop_outcome_unknown",
                }
                and not _is_acp_completion_ambiguity_reason(reason)
            ) or (
                payload.get("expected_dispatch_key") != dispatch_key
            ):
                raise ValueError("mission admission claim history is invalid")
            next_stage = "ambiguous"
            externally_closed.add(claim_lineage)
        lineage[claim_id] = claim_lineage
        stages[claim_id] = next_stage

    for attempt in attempts:
        claim_id = attempt["admission_claim_id"]
        attempt_key = (attempt["attempt_id"], attempt["dispatch_key"])
        if claim_id is None and attempt["state"] in {"cancelled", "interrupted"}:
            claims = attempt_claims.get(attempt_key, [])
            if (
                pre_dispatch_stops.get(attempt_key) != attempt["state"]
                or attempt_key in externally_closed
                or (claims and stages.get(claims[-1]) != "released")
            ):
                raise ValueError("mission admission claim history is invalid")
            continue
        if claim_id is None:
            claims = attempt_claims.get(attempt_key, [])
            if (
                attempt["state"] != "prepared"
                or attempt_key in externally_closed
                or attempt_key in pre_dispatch_stops
                or (claims and stages.get(claims[-1]) != "released")
            ):
                raise ValueError("mission admission claim history is invalid")
            continue
        if lineage.get(claim_id) != (attempt["attempt_id"], attempt["dispatch_key"]):
            raise ValueError("mission admission claim history is invalid")
        claims = attempt_claims.get(attempt_key, [])
        if not claims or claims[-1] != claim_id:
            raise ValueError("mission admission claim history is invalid")
        expected_stage = {
            "admitting": "claimed",
            "ambiguous": "ambiguous",
        }.get(
            attempt["state"],
            "submitted" if attempt["state"] != "prepared" else None,
        )
        if expected_stage is not None and stages.get(claim_id) != expected_stage:
            raise ValueError("mission admission claim history is invalid")
    return lineage


def validate_execution_snapshot(value: object) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _EXECUTION_SNAPSHOT_FIELDS:
        raise ValueError("execution snapshot fields are invalid")
    _canonical_snapshot_bytes(value)
    snapshot = copy.deepcopy(value)
    for name in ("mission", "policy", "limits"):
        if type(snapshot[name]) is not dict:
            raise ValueError(f"execution snapshot {name} is invalid")
    if type(snapshot["workers"]) is not list or len(snapshot["workers"]) < 2:
        raise ValueError("execution snapshot workers are invalid")
    mission = snapshot["mission"]
    legacy_mission_fields = {
        "mission_id",
        "schema_version",
        "plan_id",
        "plan_hash",
        "goal_hash",
        "summary_hash",
        "steps",
        "project_scope_hash",
        "action_classes",
        "skill_provenance",
        "memory_provenance",
        "declared_tests_hash",
        "acceptance_criteria_hash",
    }
    semantic_snapshot = "semantic_authority_schema_version" in mission
    mission_fields = legacy_mission_fields | (
        {"semantic_authority_schema_version", "semantic_authority_hash"}
        if semantic_snapshot
        else set()
    )
    if (
        set(mission) != mission_fields
        or not is_canonical_mission_id(mission.get("mission_id"))
        or mission.get("schema_version") != MISSION_SCHEMA_VERSION
        or type(mission.get("plan_id")) is not str
        or re.fullmatch(r"pln_[0-9a-f]{12}", mission["plan_id"]) is None
    ):
        raise ValueError("execution snapshot mission is invalid")
    hash_fields = (
        "plan_hash",
        "goal_hash",
        "summary_hash",
        "project_scope_hash",
    )
    if any(
        type(mission.get(field)) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", mission[field]) is None
        for field in hash_fields
    ):
        raise ValueError("execution snapshot mission is invalid")
    if semantic_snapshot and (
        mission.get("semantic_authority_schema_version")
        != SEMANTIC_AUTHORITY_SCHEMA_VERSION
        or type(mission.get("semantic_authority_hash")) is not str
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}", mission["semantic_authority_hash"]
        )
        is None
    ):
        raise ValueError("execution snapshot mission is invalid")
    for optional_hash in ("declared_tests_hash", "acceptance_criteria_hash"):
        value = mission.get(optional_hash)
        if value is not None and (
            type(value) is not str
            or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
        ):
            raise ValueError("execution snapshot mission is invalid")
    if mission.get("action_classes") != [
        "worker_task",
        "declared_local_verification",
    ]:
        raise ValueError("execution snapshot mission is invalid")
    steps = mission.get("steps")
    if type(steps) is not list or not steps:
        raise ValueError("execution snapshot mission is invalid")
    step_agents: list[str] = []
    step_fields = {"step_id", "position", "agent_id", "role", "task_hash"} | (
        {"semantic_step_hash"} if semantic_snapshot else set()
    )
    for position, step in enumerate(steps, start=1):
        if (
            type(step) is not dict
            or set(step) != step_fields
            or step.get("step_id") != f"step_{position}"
            or type(step.get("position")) is not int
            or step["position"] != position
            or type(step.get("agent_id")) is not str
            or not step["agent_id"]
            or type(step.get("role")) is not str
            or not step["role"]
            or type(step.get("task_hash")) is not str
            or re.fullmatch(r"sha256:[0-9a-f]{64}", step["task_hash"]) is None
            or (
                semantic_snapshot
                and (
                    type(step.get("semantic_step_hash")) is not str
                    or re.fullmatch(
                        r"sha256:[0-9a-f]{64}", step["semantic_step_hash"]
                    )
                    is None
                )
            )
        ):
            raise ValueError("execution snapshot mission is invalid")
        step_agents.append(step["agent_id"])
    workers = snapshot["workers"]
    worker_ids: list[str] = []
    for worker in workers:
        if (
            type(worker) is not dict
            or set(worker)
            != {
                "agent_id",
                "role",
                "provider",
                "workspace_mode",
                "configured_transport",
                "capability_provenance",
                "runtime_identity_hash",
            }
            or any(
                type(worker.get(field)) is not str or not worker[field]
                for field in ("agent_id", "role", "provider", "workspace_mode")
            )
            or worker.get("configured_transport") not in {"acp", "tmux"}
            or type(worker.get("runtime_identity_hash")) is not str
            or re.fullmatch(
                r"sha256:[0-9a-f]{64}", worker["runtime_identity_hash"]
            ) is None
        ):
            raise ValueError("execution snapshot workers are invalid")
        provenance = worker.get("capability_provenance")
        if (
            type(provenance) is not dict
            or set(provenance) != {"source", "transport", "adapter_configuration"}
            or provenance.get("source") != "project_config"
            or provenance.get("transport") != worker["configured_transport"]
            or provenance.get("adapter_configuration")
            not in {"present", "not_applicable"}
            or (
                worker["configured_transport"] == "acp"
                and provenance["adapter_configuration"] != "present"
            )
        ):
            raise ValueError("execution snapshot workers are invalid")
        worker_ids.append(worker["agent_id"])
    if len(worker_ids) != len(set(worker_ids)) or any(
        agent_id not in worker_ids for agent_id in step_agents
    ):
        raise ValueError("execution snapshot workers are invalid")
    policy = snapshot["policy"]
    if (
        set(policy)
        != {
            "approval_mode",
            "autonomous_allowed_agents",
            "autonomous_max_approvals",
            "policy_source",
        }
        or policy.get("approval_mode")
        not in {"confirm", "approve", "auto_approve", "autonomous"}
        or type(policy.get("autonomous_allowed_agents")) is not list
        or any(type(item) is not str or not item for item in policy["autonomous_allowed_agents"])
        or type(policy.get("autonomous_max_approvals")) is not int
        or policy["autonomous_max_approvals"] < 0
        or policy.get("policy_source") != "project_config"
    ):
        raise ValueError("execution snapshot policy is invalid")
    limits = snapshot["limits"]
    if set(limits) != {"step_count", "timeout_seconds", "retry_limit", "worker_budget"}:
        raise ValueError("execution snapshot limits are invalid")
    if any(
        type(limits.get(field)) is not int or limits[field] < minimum
        for field, minimum in (
            ("step_count", 1),
            ("timeout_seconds", 1),
            ("retry_limit", 0),
            ("worker_budget", 1),
        )
    ) or limits["step_count"] != len(steps) or limits["worker_budget"] != len(steps):
        raise ValueError("execution snapshot limits are invalid")
    skill_provenance = mission.get("skill_provenance")
    if type(skill_provenance) is not list:
        raise ValueError("execution snapshot mission is invalid")
    for item in skill_provenance:
        if (
            type(item) is not dict
            or set(item) != {"agent_id", "name_hash", "content_hash", "source_kind"}
            or type(item.get("agent_id")) is not str
            or not item["agent_id"]
            or item.get("source_kind") not in {"builtin", "project", "external"}
            or any(
                type(item.get(field)) is not str
                or re.fullmatch(r"sha256:[0-9a-f]{64}", item[field]) is None
                for field in ("name_hash", "content_hash")
            )
        ):
            raise ValueError("execution snapshot mission is invalid")
    memory_provenance = mission.get("memory_provenance")
    if type(memory_provenance) is not list:
        raise ValueError("execution snapshot mission is invalid")
    seen_scopes: set[str] = set()
    for item in memory_provenance:
        if (
            type(item) is not dict
            or set(item) != {"scope", "content_hash", "line_count", "byte_count"}
            or item.get("scope") not in {"project", "global"}
            or item["scope"] in seen_scopes
            or type(item.get("content_hash")) is not str
            or re.fullmatch(r"sha256:[0-9a-f]{64}", item["content_hash"]) is None
            or type(item.get("line_count")) is not int
            or item["line_count"] < 0
            or type(item.get("byte_count")) is not int
            or item["byte_count"] < 0
        ):
            raise ValueError("execution snapshot mission is invalid")
        seen_scopes.add(item["scope"])
    if snapshot["mission_hash"] != canonical_snapshot_hash(snapshot["mission"]):
        raise ValueError("execution snapshot mission hash is invalid")
    if snapshot["policy_hash"] != canonical_snapshot_hash(snapshot["policy"]):
        raise ValueError("execution snapshot policy hash is invalid")
    body = {
        key: snapshot[key]
        for key in (
            "mission",
            "workers",
            "policy",
            "limits",
            "mission_hash",
            "policy_hash",
        )
    }
    if snapshot["execution_hash"] != canonical_snapshot_hash(body):
        raise ValueError("execution snapshot execution hash is invalid")
    return snapshot


def execution_policy_snapshot(config: ProjectConfig) -> dict[str, object]:
    approval_mode = config.leader.approval_mode
    if approval_mode not in {"confirm", "approve", "auto_approve", "autonomous"}:
        raise ValueError("execution policy invalid")
    if any(type(item) is not str or not item for item in config.autonomous.allowed_agents):
        raise ValueError("execution policy invalid")
    if (
        type(config.autonomous.max_approvals) is not int
        or config.autonomous.max_approvals < 0
    ):
        raise ValueError("execution policy invalid")
    return {
        "approval_mode": approval_mode,
        "autonomous_allowed_agents": list(config.autonomous.allowed_agents),
        "autonomous_max_approvals": config.autonomous.max_approvals,
        "policy_source": "project_config",
    }


def _snapshot_skill_provenance(plan: Mapping[str, object]) -> list[dict[str, object]]:
    raw_context = plan.get("skill_context")
    if not isinstance(raw_context, Mapping):
        return []
    raw_items = raw_context.get("items")
    if not isinstance(raw_items, list):
        return []
    compact: list[dict[str, object]] = []
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            raise ValueError("execution snapshot invalid")
        name = raw.get("name")
        content_hash = raw.get("content_hash")
        agent_id = raw.get("agent_id")
        source = raw.get("source")
        if (
            type(name) is not str
            or not name
            or type(content_hash) is not str
            or re.fullmatch(r"sha256:[0-9a-f]{64}", content_hash) is None
            or type(agent_id) is not str
            or not agent_id
            or type(source) is not str
            or not source
        ):
            raise ValueError("execution snapshot invalid")
        source_kind = (
            "builtin"
            if source == "builtin"
            else "project"
            if source == "project"
            else "external"
        )
        compact.append(
            {
                "agent_id": agent_id,
                "name_hash": canonical_snapshot_hash({"name": name}),
                "content_hash": content_hash,
                "source_kind": source_kind,
            }
        )
    return compact


def _snapshot_optional_fact_hash(
    plan: Mapping[str, object], field: str
) -> str | None:
    value = plan.get(field)
    return None if value is None else canonical_snapshot_hash({field: value})


def collect_execution_memory_provenance(root: Path) -> list[dict[str, object]]:
    canonical_root = root.resolve(strict=False)
    records: list[dict[str, object]] = []
    for scope, relative in (
        ("project", Path(".agentdeck/memory/project.md")),
        ("global", Path(".agentdeck/memory/global.md")),
    ):
        path = canonical_root / relative
        if not path.exists():
            continue
        try:
            if not path.resolve(strict=True).is_relative_to(canonical_root):
                raise ValueError("execution snapshot invalid")
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_size > 256 * 1024:
                    raise ValueError("execution snapshot invalid")
                chunks: list[bytes] = []
                remaining = info.st_size
                while remaining:
                    chunk = os.read(descriptor, min(remaining, 64 * 1024))
                    if not chunk:
                        raise ValueError("execution snapshot invalid")
                    chunks.append(chunk)
                    remaining -= len(chunk)
                if os.read(descriptor, 1):
                    raise ValueError("execution snapshot invalid")
                data = b"".join(chunks)
            finally:
                os.close(descriptor)
            text = data.decode("utf-8")
        except ValueError:
            raise
        except (OSError, UnicodeError):
            raise ValueError("execution snapshot invalid") from None
        records.append(
            {
                "scope": scope,
                "content_hash": f"sha256:{hashlib.sha256(data).hexdigest()}",
                "line_count": len(text.splitlines()),
                "byte_count": len(data),
            }
        )
    return records


def build_execution_snapshot_authority(
    config: ProjectConfig,
    mission: Mapping[str, object],
    plan: Mapping[str, object],
    policy: Mapping[str, object],
    *,
    memory_provenance: list[dict[str, object]],
) -> dict[str, Any]:
    mission_id = mission.get("mission_id")
    if not is_canonical_mission_id(mission_id):
        raise ValueError("execution snapshot invalid")
    plan_id = mission.get("plan_id")
    if type(plan_id) is not str or plan.get("plan_id") != plan_id:
        raise ValueError("execution snapshot invalid")
    try:
        current_plan_hash = canonical_workflow_plan_hash(plan)
    except (TypeError, ValueError, OverflowError):
        raise MissionStateError("plan hash drift") from None
    if current_plan_hash != mission.get("plan_hash"):
        raise MissionStateError("plan hash drift")
    if mission.get("schema_version") != MISSION_SCHEMA_VERSION:
        raise ValueError("execution snapshot invalid")
    raw_plan = plan.get("plan")
    if type(raw_plan) is not dict:
        raise ValueError("execution snapshot invalid")
    semantic_active, plan_is_semantic, present_semantic = (
        semantic_mission_provenance_shape(raw_plan, mission)
    )
    semantic_plan: dict[str, Any] | None = None
    if semantic_active:
        if (
            not plan_is_semantic
            or present_semantic != SEMANTIC_MISSION_COMPACT_FIELDS
        ):
            raise ValueError("execution snapshot invalid")
        semantic_plan = validated_compiled_semantic_plan(raw_plan)
        expected_authority_hash = semantic_authority_hash(
            semantic_plan["semantic_authority"]
        )
        expected_task_hashes = [
            f"sha256:{hashlib.sha256(step['task'].encode('utf-8')).hexdigest()}"
            for step in semantic_plan["steps"]
        ]
        if (
            mission.get("semantic_authority_schema_version")
            != semantic_plan["semantic_authority"]["schema_version"]
            or mission.get("semantic_authority_hash") != expected_authority_hash
            or mission.get("compiled_task_hashes") != expected_task_hashes
            or mission.get("preview_generation") != 1
        ):
            raise ValueError("execution snapshot invalid")
        raw_plan = semantic_plan
    raw_steps = raw_plan.get("steps")
    selected = mission.get("selected_agents")
    if type(raw_steps) is not list or not raw_steps or type(selected) is not list:
        raise ValueError("execution snapshot invalid")
    selected_ids = [
        item.get("agent_id") if type(item) is dict else None for item in selected
    ]
    if len(selected_ids) < 2 or len(set(selected_ids)) != len(selected_ids):
        raise ValueError("execution snapshot invalid")
    agents_by_id = {agent.agent_id: agent for agent in config.agents}
    if len(agents_by_id) != len(config.agents):
        raise ValueError("execution snapshot invalid")
    workers: list[dict[str, object]] = []
    for selected_row in selected:
        if type(selected_row) is not dict:
            raise ValueError("execution snapshot invalid")
        agent = agents_by_id.get(selected_row.get("agent_id"))
        if agent is None or agent.transport not in {"acp", "tmux"}:
            raise ValueError("execution snapshot invalid")
        if agent.transport == "acp" and not agent.transport_command:
            raise ValueError("execution snapshot invalid")
        if any(type(part) is not str or not part for part in agent.transport_command):
            raise ValueError("execution snapshot invalid")
        if any(
            selected_row.get(field) != getattr(agent, field)
            for field in ("provider", "role", "workspace_mode")
        ):
            raise ValueError("execution snapshot invalid")
        workers.append(
            {
                "agent_id": agent.agent_id,
                "role": agent.role,
                "provider": agent.provider,
                "workspace_mode": agent.workspace_mode,
                "configured_transport": agent.transport,
                "runtime_identity_hash": worker_runtime_identity_hash(
                    agent, config.runtime
                ),
                "capability_provenance": {
                    "source": "project_config",
                    "transport": agent.transport,
                    "adapter_configuration": (
                        "present" if agent.transport_command else "not_applicable"
                    ),
                },
            }
        )
    compact_steps: list[dict[str, object]] = []
    semantic_steps = semantic_plan["semantic_steps"] if semantic_plan else None
    for position, raw_step in enumerate(raw_steps, start=1):
        if type(raw_step) is not dict:
            raise ValueError("execution snapshot invalid")
        step_number = raw_step.get("step")
        agent_id = raw_step.get("agent_id")
        role = raw_step.get("role")
        task = raw_step.get("task")
        if (
            type(step_number) is not int
            or step_number != position
            or type(agent_id) is not str
            or agent_id not in selected_ids
            or type(role) is not str
            or not role
            or type(task) is not str
            or not task
        ):
            raise ValueError("execution snapshot invalid")
        compact_step = {
            "step_id": f"step_{position}",
            "position": position,
            "agent_id": agent_id,
            "role": role,
            "task_hash": canonical_snapshot_hash({"task": task}),
        }
        if semantic_steps is not None:
            compact_step["semantic_step_hash"] = semantic_steps[position - 1][
                "semantic_step_hash"
            ]
        compact_steps.append(compact_step)
    if len(compact_steps) != mission.get("step_count"):
        raise ValueError("execution snapshot invalid")
    root = Path(config.root)
    plan_hash = mission.get("plan_hash")
    if (
        type(plan_hash) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", plan_hash) is None
    ):
        raise ValueError("execution snapshot invalid")
    policy_body = copy.deepcopy(dict(policy))
    if policy_body != execution_policy_snapshot(config):
        raise ValueError("execution snapshot invalid")
    mission_body: dict[str, object] = {
        "mission_id": mission_id,
        "schema_version": mission.get("schema_version"),
        "plan_id": plan_id,
        "plan_hash": plan_hash,
        "goal_hash": canonical_snapshot_hash({"goal": raw_plan.get("goal")}),
        "summary_hash": canonical_snapshot_hash({"summary": raw_plan.get("summary")}),
        "steps": compact_steps,
        "project_scope_hash": canonical_snapshot_hash({"project_root": str(root)}),
        "action_classes": ["worker_task", "declared_local_verification"],
        "skill_provenance": _snapshot_skill_provenance(plan),
        "memory_provenance": copy.deepcopy(memory_provenance),
        "declared_tests_hash": _snapshot_optional_fact_hash(
            raw_plan, "declared_tests"
        ),
        "acceptance_criteria_hash": _snapshot_optional_fact_hash(
            raw_plan, "acceptance_criteria"
        ),
    }
    if semantic_plan is not None:
        mission_body.update(
            {
                "semantic_authority_schema_version": semantic_plan[
                    "semantic_authority"
                ]["schema_version"],
                "semantic_authority_hash": semantic_authority_hash(
                    semantic_plan["semantic_authority"]
                ),
            }
        )
    limits: dict[str, object] = {
        "step_count": mission.get("step_count"),
        "timeout_seconds": mission.get("timeout_seconds"),
        "retry_limit": mission.get("retry_limit"),
        "worker_budget": mission.get("step_count"),
    }
    mission_hash = canonical_snapshot_hash(mission_body)
    policy_hash = canonical_snapshot_hash(policy_body)
    execution_body: dict[str, object] = {
        "mission": mission_body,
        "workers": workers,
        "policy": policy_body,
        "limits": limits,
        "mission_hash": mission_hash,
        "policy_hash": policy_hash,
    }
    return validate_execution_snapshot(
        {**execution_body, "execution_hash": canonical_snapshot_hash(execution_body)}
    )


def derive_attempt_dispatch_key(
    mission_id: str,
    step_id: str,
    agent_id: str,
    configured_transport: str,
    snapshot_hash: str,
    *,
    attempt_ordinal: int = 1,
) -> str:
    if (
        not is_canonical_mission_id(mission_id)
        or type(step_id) is not str
        or re.fullmatch(r"step_[1-9][0-9]*", step_id) is None
        or type(agent_id) is not str
        or not agent_id
        or configured_transport not in {"acp", "tmux"}
        or type(snapshot_hash) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", snapshot_hash) is None
        or type(attempt_ordinal) is not int
        or attempt_ordinal < 1
    ):
        raise ValueError("attempt identity invalid")
    digest = canonical_snapshot_hash(
        {
            "mission_id": mission_id,
            "step_id": step_id,
            "agent_id": agent_id,
            "configured_transport": configured_transport,
            "snapshot_hash": snapshot_hash,
            "attempt_ordinal": attempt_ordinal,
        }
    ).removeprefix("sha256:")
    return f"dsp_{digest[:32]}"


def leader_provider_backend(provider: str | None) -> str:
    if provider in {"deepseek", "openai-compatible"}:
        return "api"
    if provider in {"codex-cli", "claude-cli"}:
        return "cli"
    if provider == "fake":
        return "local"
    return "unknown"


def leader_provider_transport(provider: str | None) -> str:
    if provider in {"deepseek", "openai-compatible"}:
        return "http"
    if provider in {"codex-cli", "claude-cli"}:
        return "subprocess"
    if provider == "fake":
        return "local"
    return "unknown"


def leader_reasoning_backend(provider: str | None) -> str:
    if provider in {"deepseek", "openai-compatible"}:
        return "api-llm"
    if provider in {"codex-cli", "claude-cli"}:
        return "cli-subprocess"
    if provider == "fake":
        return "local-fake"
    return "unknown"


def leader_backend_identity(provider: str | None, model: str | None, dispatch_ready: bool = False) -> dict[str, Any]:
    return {
        "agent_id": "leader",
        "provider": provider,
        "model": model,
        "provider_backend": leader_provider_backend(provider),
        "provider_transport": leader_provider_transport(provider),
        "reasoning_backend": leader_reasoning_backend(provider),
        "runtime_kind": "logical_leader",
        "pane_backed": False,
        "pane_id": None,
        "approval_required": True,
        "dispatch_ready": dispatch_ready,
    }


def leader_coordination_roles(provider: str | None, model: str | None) -> list[dict[str, Any]]:
    return [
        {
            "role_id": "frontdesk",
            "label": "Frontdesk intake",
            "provider": "local-rule",
            "model": "deterministic",
            "lifecycle": "persistent",
            "responsibility": "Intake human requests and route them without provider planning.",
            "state_source": "chat_turns",
            "runtime_kind": "logical_role",
            "pane_backed": False,
            "pane_id": None,
            "dispatch_ready": False,
            "approval_required": False,
            "next_command": 'agentdeck leader chat --message "frontdesk <goal>"',
        },
        {
            "role_id": "planner",
            "label": "Planner",
            "provider": provider,
            "model": model,
            "lifecycle": "persistent",
            "responsibility": "Create macro plans and acceptance criteria without dispatching workers.",
            "state_source": "plans",
            "runtime_kind": "logical_role",
            "pane_backed": False,
            "pane_id": None,
            "dispatch_ready": False,
            "approval_required": True,
            "next_command": "agentdeck leader plan --task <goal>",
        },
        {
            "role_id": "orchestrator",
            "label": "Orchestrator",
            "provider": provider,
            "model": model,
            "lifecycle": "persistent",
            "responsibility": "Review plans, choose approval-gated actions, and coordinate worker handoff.",
            "state_source": "leader_actions",
            "runtime_kind": "logical_role",
            "pane_backed": False,
            "pane_id": None,
            "dispatch_ready": False,
            "approval_required": True,
            "next_command": "agentdeck leader review --plan-id <plan_id>",
        },
    ]


class StateStore:
    def __init__(self, root: Path | None = None, *, ensure_layout: bool = True) -> None:
        self.root = root or project_root()
        self.deck_dir = (
            ensure_project_layout(self.root)
            if ensure_layout
            else agentdeck_dir(self.root)
        )
        self.state_path = self.deck_dir / "state" / "state.json"
        self.events_path = self.deck_dir / "state" / "events.jsonl"

    def _remember_loaded_state(
        self,
        state: dict[str, Any],
        source_hash: str | None,
        *,
        replace_token: bool = False,
    ) -> dict[str, Any]:
        token = state.get(_STATE_SOURCE_TOKEN_KEY)
        if replace_token or not isinstance(token, _StateSourceToken):
            token = _StateSourceToken()
            state[_STATE_SOURCE_TOKEN_KEY] = token
        with _LOADED_STATE_SOURCES_LOCK:
            _STATE_SOURCE_FACTS[token] = (str(self.root.resolve()), source_hash)
        return state

    def _loaded_state_source(self, state: dict[str, Any]) -> tuple[bool, str | None]:
        token = state.get(_STATE_SOURCE_TOKEN_KEY)
        if not isinstance(token, _StateSourceToken):
            return False, None
        with _LOADED_STATE_SOURCES_LOCK:
            remembered = _STATE_SOURCE_FACTS.get(token)
        if remembered is None or remembered[0] != str(self.root.resolve()):
            return False, None
        return True, remembered[1]

    @classmethod
    def open_existing(cls, root: Path | None = None) -> StateStore:
        return cls(root, ensure_layout=False)

    def load(self) -> dict[str, Any]:
        key = str(self.root.resolve())
        entry = _state_lock_entries().get(key)
        if entry is not None:
            anchored = entry["anchored"]
            assert isinstance(anchored, tuple)
            source = _read_state_bytes_at(int(anchored[1]), missing_ok=True)
        else:
            try:
                with _secure_project_state_directory(self.root.resolve()) as (_deck_fd, state_fd):
                    source = _read_state_bytes_at(state_fd, missing_ok=True)
            except ValueError:
                if not (self.deck_dir / "state").exists():
                    source = None
                else:
                    raise
        if source is None:
            return self._remember_loaded_state({
                "agents": {},
                "messages": [],
                "attempts": [],
                "mission_attempts": [],
                "mission_worker_starts": [],
                "jobs": [],
                "replies": [],
                "artifacts": [],
                "missions": [],
                "plans": [],
                "approvals": [],
                "chat_turns": [],
                "leader_errors": [],
                "leader_actions": [],
                "skill_loads": [],
                "skill_suggestions": [],
                "memory_suggestions": [],
                "agent_sessions": [],
                "protocol_turns": [],
                "transport_updates": [],
                "permission_requests": [],
                "protocol_state_transitions": [],
                "protocol_event_outbox": [],
                "conversation_sessions": [],
                "conversation_turns": [],
                "conversation_preview_bindings": [],
                "conversation_state_transitions": [],
                "conversation_event_outbox": [],
                "daemon_runtime": None,
                "controller_lease": None,
                "daemon_event_outbox": [],
                "recovery_decisions": [],
                "mission_recovery_evidence": [],
                "mission_worker_replies": [],
                "mission_handoffs": [],
                "mission_permission_bindings": [],
                "governance_previews": [],
                "mission_transport_reroutes": [],
                "worker_takeover_baselines": [],
                "worker_reconciliation_decisions": [],
                "mission_acp_effect_consumptions": [],
            }, None)
        decoded = json.loads(source.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("state must be an object")
        decoded.pop(_STATE_SOURCE_TOKEN_KEY, None)
        return self._remember_loaded_state(
            decoded, "sha256:" + hashlib.sha256(source).hexdigest()
        )

    def save(self, state: dict[str, Any]) -> None:
        with self._protocol_mutation_lock() as (_deck_fd, state_fd):
            current = _read_state_bytes_at(state_fd, missing_ok=True)
            current_hash = (
                "sha256:" + hashlib.sha256(current).hexdigest()
                if current is not None
                else None
            )
            remembered, source_hash = self._loaded_state_source(state)
            if remembered:
                if source_hash != current_hash:
                    raise ValueError("state source facts drifted")
            elif current is not None:
                raise ValueError("state save requires a loaded snapshot")
            self._atomic_save(state)
            installed = _read_state_bytes_at(state_fd)
            assert installed is not None
            self._remember_loaded_state(
                state, "sha256:" + hashlib.sha256(installed).hexdigest()
            )
            # SQLite 影子镜像（quarantine 阶段）：仍在同一把 mutation lock 内
            # 打开连接（route spec 阶段 2 硬规则），JSON 保持唯一权威，镜像
            # 失败绝不进入本写路径（错误落 logs/shadow-errors.jsonl）。
            _shadow_mirror_if_enabled(self.root, state)

    def _event_journal_source(self) -> bytes | None:
        key = str(self.root.resolve())
        entry = _state_lock_entries().get(key)
        if entry is not None:
            _deck_fd, state_fd = self._verify_current_mutation_anchor()
            source = _read_event_journal_at(state_fd)
            self._verify_current_mutation_anchor()
            return source
        try:
            with _secure_project_state_directory(self.root.resolve()) as anchored:
                source = _read_event_journal_at(anchored[1])
                _verify_project_state_anchor(self.root.resolve(), *anchored)
                return source
        except ValueError:
            try:
                (self.deck_dir / "state").lstat()
            except FileNotFoundError:
                return None
            raise

    def _verify_current_mutation_anchor(self) -> tuple[int, int]:
        entry = _state_lock_entries().get(str(self.root.resolve()))
        if entry is None:
            raise RuntimeError("state mutation anchor is not held")
        anchored = entry["anchored"]
        root_fd = entry["root_fd"]
        lock_fd = entry["lock_fd"]
        assert isinstance(anchored, tuple)
        assert isinstance(root_fd, int)
        assert isinstance(lock_fd, int)
        _verify_mutation_anchor(self.root.resolve(), root_fd, anchored)
        _verify_regular_file_at(
            int(anchored[1]),
            "protocol-mutation.lock",
            lock_fd,
            error="protocol mutation lock changed during state mutation",
        )
        return int(anchored[0]), int(anchored[1])

    def _append_event_bytes_locked(self, encoded: bytes) -> None:
        _deck_fd, state_fd = self._verify_current_mutation_anchor()
        entry = _state_lock_entries()[str(self.root.resolve())]
        lock_fd = entry["lock_fd"]
        assert isinstance(lock_fd, int)
        held_lock = os.fstat(lock_fd)
        held_lock_identity = (held_lock.st_dev, held_lock.st_ino)
        displaced_fd = _open_retained_regular_at(
            state_fd,
            "events.jsonl",
            error="event journal is unsafe",
        )
        displaced_identity = (
            None
            if displaced_fd is None
            else (os.fstat(displaced_fd).st_dev, os.fstat(displaced_fd).st_ino)
        )
        source = (
            b""
            if displaced_fd is None
            else _read_retained_bytes(
                displaced_fd,
                max_bytes=_EVENT_JOURNAL_MAX_BYTES,
                limit_error="event journal exceeds size limit",
            )
        )
        installed = source + encoded
        if "effect_guard" in entry:
            raise RuntimeError("state mutation effect guard is already active")
        entry["effect_guard"] = {
            "target": "events.jsonl",
            "source": None if displaced_fd is None else source,
            "identity": displaced_identity,
            "effect_installed": False,
            "max_bytes": _EVENT_JOURNAL_MAX_BYTES,
            "limit_error": "event journal exceeds size limit",
        }
        try:
            try:
                self._verify_current_mutation_anchor()
                _append_event_journal_at(state_fd, encoded)
                self._verify_current_mutation_anchor()
            except BaseException:
                current = _named_regular_snapshot_at(
                    state_fd,
                    "events.jsonl",
                    max_bytes=_EVENT_JOURNAL_MAX_BYTES,
                    limit_error="event journal exceeds size limit",
                )
                if (
                    entry["effect_guard"].get("effect_installed") is True
                    and current is not None
                    and current[0] == installed
                    and current[1] != displaced_identity
                ):
                    _restore_displaced_file_if_exact(
                        state_fd,
                        "events.jsonl",
                        installed=installed,
                        displaced_fd=displaced_fd,
                        held_lock_identity=held_lock_identity,
                        max_bytes=_EVENT_JOURNAL_MAX_BYTES,
                        limit_error="event journal exceeds size limit",
                    )
                raise
        finally:
            try:
                _release_effect_guard(entry)
            finally:
                if displaced_fd is not None:
                    os.close(displaced_fd)
        # SQLite 阶段 5a：journal 权威写成功后，同一把 mutation lock 内向
        # shadow db events 表双写（route spec 阶段 2 硬规则）；失败只落
        # shadow-errors.jsonl，绝不进入本路径。
        _shadow_append_events_if_enabled(self.root, encoded)

    def append_event(self, event: EventRecord) -> None:
        encoded = (
            json.dumps(asdict(event), ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        with self._protocol_mutation_lock():
            self._append_event_bytes_locked(encoded)

    def record_governance_preview(
        self,
        action: str,
        *,
        facts: Mapping[str, object],
        generation: int,
        now: datetime,
        ttl_seconds: float = 300,
        controller_authority: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Durably record an exact-bound governance preview without acting."""
        from .daemon.governance import build_governance_preview

        candidate = {
            "preview_id": new_id("gov"),
            **build_governance_preview(
                action,
                facts=facts,
                generation=generation,
                now=now,
                ttl_seconds=ttl_seconds,
            ),
        }
        if controller_authority is not None:
            lease_id = controller_authority.get("lease_id")
            client_id = controller_authority.get("client_id")
            daemon_instance_id = controller_authority.get("daemon_instance_id")
            if (
                type(lease_id) is not str
                or type(client_id) is not str
                or type(daemon_instance_id) is not str
                or controller_authority.get("generation") != generation
            ):
                raise ValueError("governance controller authority is invalid")
            candidate["controller_lineage"] = {
                "client_id": client_id,
                "daemon_instance_id": daemon_instance_id,
                "generation": generation,
                "lease_id_hash": hashlib.sha256(lease_id.encode("utf-8")).hexdigest(),
            }
        with self._protocol_mutation_lock():
            state = self.load()
            if controller_authority is not None:
                current = state.get("controller_lease")
                runtime = state.get("daemon_runtime")
                if (
                    type(current) is not dict
                    or type(runtime) is not dict
                    or current.get("lease_id") != controller_authority.get("lease_id")
                    or current.get("client_id") != controller_authority.get("client_id")
                    or current.get("generation") != generation
                    or runtime.get("instance_id")
                    != controller_authority.get("daemon_instance_id")
                    or not controller_lease_is_active(current, now=now)
                ):
                    raise ValueError("governance controller authority drift")
            previews = state.setdefault("governance_previews", [])
            if type(previews) is not list or any(
                type(item) is not dict for item in previews
            ):
                raise ValueError("governance preview state is invalid")
            if any(item.get("preview_id") == candidate["preview_id"] for item in previews):
                raise ValueError("duplicate governance preview identity")
            previews.append(candidate)
            self._append_recovery_audit_locked(
                state,
                "governance_preview_recorded",
                {
                    "preview_id": candidate["preview_id"],
                    "action": action,
                    "generation": generation,
                    "execution_digest": candidate["execution_digest"],
                },
            )
            self._atomic_save(state)
            return copy.deepcopy(candidate)

    def consume_governance_preview(
        self,
        *,
        preview_id: str,
        action: str,
        facts: Mapping[str, object],
        generation: int,
        now: datetime,
    ) -> dict[str, object]:
        """Consume once only after state, expiry, action, and generation agree."""
        from .daemon.governance import consume_governance_preview

        with self._protocol_mutation_lock():
            state = self.load()
            previews = state.setdefault("governance_previews", [])
            if type(previews) is not list or any(
                type(item) is not dict for item in previews
            ):
                raise ValueError("governance preview state is invalid")
            matches = [item for item in previews if item.get("preview_id") == preview_id]
            if len(matches) != 1:
                raise KeyError(preview_id)
            current = matches[0]
            consumed = consume_governance_preview(
                current,
                action=action,
                facts=facts,
                generation=generation,
                now=now,
            )
            index = next(
                index for index, item in enumerate(previews)
                if item.get("preview_id") == preview_id
            )
            previews[index] = consumed
            self._append_recovery_audit_locked(
                state,
                "governance_preview_consumed",
                {
                    "preview_id": preview_id,
                    "action": action,
                    "generation": generation,
                    "execution_digest": consumed["execution_digest"],
                },
            )
            self._atomic_save(state)
            return copy.deepcopy(consumed)

    def pause_mission_with_governance_preview(
        self,
        *,
        mission_id: str,
        preview_id: str,
        facts: Mapping[str, object],
        generation: int,
        now: datetime,
    ) -> dict[str, object]:
        """Atomically consume exact pause authority and stop scheduling."""
        from .conversation.bindings import PreviewBindingError
        from .daemon.governance import consume_governance_preview

        with self._protocol_mutation_lock():
            state = self.load()
            missions = state.setdefault("missions", [])
            attempts = state.setdefault("mission_attempts", [])
            previews = state.setdefault("governance_previews", [])
            if any(type(value) is not list for value in (missions, attempts, previews)):
                raise ValueError("Mission pause state is invalid")
            matches = [
                item for item in missions
                if type(item) is dict and item.get("mission_id") == mission_id
            ]
            if len(matches) != 1 or matches[0].get("status") != "running":
                raise ValueError("Mission pause target is invalid")
            mission = matches[0]
            current_facts = {
                "mission_id": mission_id,
                "status": mission.get("status"),
                "current_step": mission.get("current_step"),
                "snapshot_hash": mission.get("snapshot_hash"),
                "active_attempts": [
                    {
                        "attempt_id": item.get("attempt_id"),
                        "state": item.get("state"),
                        "dispatch_key": item.get("dispatch_key"),
                    }
                    for item in attempts
                    if type(item) is dict
                    and item.get("mission_id") == mission_id
                    and item.get("state") not in {
                        "succeeded", "completed", "failed", "cancelled",
                        "interrupted", "ambiguous",
                    }
                ],
            }
            if current_facts["active_attempts"]:
                raise ValueError("Mission pause requires an idle boundary")
            if dict(facts) != current_facts:
                raise PreviewBindingError("preview state drift")
            preview_matches = [
                item for item in previews
                if type(item) is dict and item.get("preview_id") == preview_id
            ]
            if len(preview_matches) != 1:
                raise KeyError(preview_id)
            consumed = consume_governance_preview(
                preview_matches[0],
                action="mission_pause",
                facts=current_facts,
                generation=generation,
                now=now,
            )
            if not mission_status_transition_allowed("running", "stopped"):
                raise ValueError("Mission pause transition is invalid")
            updated_at = now.astimezone(timezone.utc).isoformat()
            mission.update(
                {
                    "status": "stopped",
                    "stop_reason": "human_pause",
                    "updated_at": updated_at,
                }
            )
            preview_index = next(
                index for index, item in enumerate(previews)
                if type(item) is dict and item.get("preview_id") == preview_id
            )
            previews[preview_index] = consumed
            self._append_recovery_audit_locked(
                state,
                "mission_paused",
                {
                    "mission_id": mission_id,
                    "preview_id": preview_id,
                    "generation": generation,
                    "execution_digest": consumed["execution_digest"],
                },
            )
            self._atomic_save(state)
            return {
                "accepted": True,
                "state": "stopped",
                "mission_id": mission_id,
                "preview_id": preview_id,
            }

    def decide_permission_with_governance_preview(
        self,
        *,
        permission_id: str,
        decision: str,
        preview_id: str,
        facts: Mapping[str, object],
        generation: int,
        now: datetime,
    ) -> dict[str, object]:
        """Atomically consume exact human authority and append the decision."""
        from .conversation.bindings import PreviewBindingError
        from .daemon.governance import consume_governance_preview

        if decision not in {"approved", "denied"}:
            raise ValueError("permission decision is invalid")
        with self._protocol_mutation_lock():
            state = self.load()
            self._validate_protocol_identities(state)
            current_states = self._validate_protocol_transition_history(state)
            permissions = state.setdefault("permission_requests", [])
            matches = [
                item for item in permissions
                if type(item) is dict and item.get("permission_id") == permission_id
            ]
            if len(matches) != 1:
                raise KeyError(permission_id)
            permission = _validate_permission_record(matches[0])
            current = current_states.get(("permission", permission_id), "pending")
            if current != "pending":
                raise ValueError("permission decision is terminal")
            bindings = [
                item
                for item in state.get("mission_permission_bindings", [])
                if type(item) is dict
                and item.get("permission_id") == permission_id
            ]
            if len(bindings) != 1:
                raise ValueError("permission decision binding is invalid")
            binding = bindings[0]
            mission_id = binding.get("mission_id")
            attempt_id = binding.get("attempt_id")
            if type(mission_id) is not str or type(attempt_id) is not str:
                raise ValueError("permission decision binding is invalid")
            current_facts = {
                "mission_id": mission_id,
                "attempt_id": attempt_id,
                "permission_id": permission_id,
                "session_id": permission["session_id"],
                "turn_id": permission["turn_id"],
                "tool_name": permission["tool_name"],
                "target": permission["target"],
                "risk": permission["risk"],
                "state": current,
                "decision": decision,
            }
            if dict(facts) != current_facts:
                raise PreviewBindingError("preview state drift")
            previews = state.setdefault("governance_previews", [])
            if type(previews) is not list:
                raise ValueError("governance preview state is invalid")
            preview_matches = [
                item for item in previews
                if type(item) is dict and item.get("preview_id") == preview_id
            ]
            if len(preview_matches) != 1:
                raise KeyError(preview_id)
            consumed = consume_governance_preview(
                preview_matches[0],
                action="permission_decision",
                facts=current_facts,
                generation=generation,
                now=now,
            )
            transition = build_protocol_transition(
                "permission",
                permission_id,
                "pending",
                decision,
                "human_exact_confirmation",
                {"preview_id": preview_id, "generation": generation},
            )
            state.setdefault("protocol_state_transitions", []).append(transition)
            self._validate_protocol_transition_history(state)
            preview_index = next(
                index for index, item in enumerate(previews)
                if type(item) is dict and item.get("preview_id") == preview_id
            )
            previews[preview_index] = consumed
            event = EventRecord.create(
                "permission_decision_recorded",
                {
                    "permission_id": permission_id,
                    "decision": decision,
                    "preview_id": preview_id,
                    "generation": generation,
                },
            )
            state.setdefault("protocol_event_outbox", []).append(asdict(event))
            self._append_recovery_audit_locked(
                state,
                "governance_preview_consumed",
                {
                    "preview_id": preview_id,
                    "action": "permission_decision",
                    "generation": generation,
                    "execution_digest": consumed["execution_digest"],
                },
            )
            self._atomic_save(state)
            return {
                "accepted": True,
                "permission_id": permission_id,
                "state": decision,
                "preview_id": preview_id,
            }

    def force_stop_with_governance_preview(
        self,
        *,
        preview_id: str,
        facts: Mapping[str, object],
        generation: int,
        now: datetime,
    ) -> dict[str, object]:
        """Atomically stop scheduling while preserving unknown effect outcomes."""
        from .conversation.bindings import PreviewBindingError
        from .daemon.governance import (
            classify_force_stop_attempts,
            consume_governance_preview,
        )

        with self._protocol_mutation_lock():
            state = self.load()
            missions = state.setdefault("missions", [])
            attempts_raw = state.setdefault("mission_attempts", [])
            previews = state.setdefault("governance_previews", [])
            if any(type(value) is not list for value in (missions, attempts_raw, previews)):
                raise ValueError("force-stop state is invalid")
            attempts = [_validate_mission_attempt_record(item) for item in attempts_raw]
            _require_unique_mission_attempt_lineage(attempts)
            current_facts = {
                "missions": [
                    {
                        "mission_id": item.get("mission_id"),
                        "status": item.get("status"),
                        "current_step": item.get("current_step"),
                        "snapshot_hash": item.get("snapshot_hash"),
                    }
                    for item in missions
                    if type(item) is dict
                    and item.get("status") not in {"completed", "stopped", "interrupted"}
                ],
                "attempts": [
                    {
                        "attempt_id": item["attempt_id"],
                        "mission_id": item["mission_id"],
                        "state": item["state"],
                        "dispatch_key": item["dispatch_key"],
                        "admission_claim_id": item["admission_claim_id"],
                        "receipt_summary": item["receipt_summary"],
                    }
                    for item in attempts
                    if item["state"] not in {
                        "succeeded", "completed", "failed", "cancelled",
                        "interrupted", "ambiguous",
                    }
                ],
            }
            if dict(facts) != current_facts:
                raise PreviewBindingError("preview state drift")
            preview_matches = [
                item for item in previews
                if type(item) is dict and item.get("preview_id") == preview_id
            ]
            if len(preview_matches) != 1:
                raise KeyError(preview_id)
            consumed = consume_governance_preview(
                preview_matches[0],
                action="force_daemon_stop",
                facts=current_facts,
                generation=generation,
                now=now,
            )
            classifications = classify_force_stop_attempts(attempts)
            timestamp = now.astimezone(timezone.utc).isoformat()
            next_attempts: list[dict[str, Any]] = []
            for attempt in attempts:
                target = classifications.get(attempt["attempt_id"])
                if target is None:
                    next_attempts.append(attempt)
                    continue
                reason = (
                    "force_daemon_stop_before_dispatch"
                    if target == "interrupted"
                    else "force_daemon_stop_outcome_unknown"
                )
                candidate = {
                    **attempt,
                    "state": target,
                    "updated_at": timestamp,
                    "receipt_summary": (
                        attempt["receipt_summary"]
                        if attempt["receipt_summary"] is not None
                        else (
                            None if target == "interrupted"
                            else "admission outcome unknown"
                        )
                    ),
                    "blocker": reason,
                    "terminal_reason": reason,
                }
                next_attempts.append(_validate_mission_attempt_record(candidate))
                if target == "ambiguous":
                    self._append_recovery_audit_locked(
                        state,
                        "mission_attempt_ambiguous",
                        {
                            "attempt_id": attempt["attempt_id"],
                            "mission_id": attempt["mission_id"],
                            "step_id": attempt["step_id"],
                            "dispatch_key": attempt["dispatch_key"],
                            "admission_claim_id": attempt["admission_claim_id"],
                            "reason": "force_daemon_stop_outcome_unknown",
                            "expected_dispatch_key": attempt["dispatch_key"],
                            "observed_dispatch_key": attempt["dispatch_key"],
                        },
                    )
            for mission in missions:
                if type(mission) is not dict or mission.get("status") in {
                    "completed", "stopped", "interrupted"
                }:
                    continue
                status = mission.get("status")
                if status != "preparing" and not mission_status_transition_allowed(
                    status, "interrupted"
                ):
                    raise ValueError("force-stop Mission transition is invalid")
                mission.update(
                    {
                        "status": "interrupted",
                        "stop_reason": "force_daemon_stop",
                        "updated_at": timestamp,
                    }
                )
            state["mission_attempts"] = next_attempts
            preview_index = next(
                index for index, item in enumerate(previews)
                if type(item) is dict and item.get("preview_id") == preview_id
            )
            previews[preview_index] = consumed
            self._append_recovery_audit_locked(
                state,
                "daemon_force_stopped",
                {
                    "preview_id": preview_id,
                    "generation": generation,
                    "interrupted_count": sum(
                        value == "interrupted" for value in classifications.values()
                    ),
                    "ambiguous_count": sum(
                        value == "ambiguous" for value in classifications.values()
                    ),
                },
            )
            self._atomic_save(state)
            return {
                "accepted": True,
                "state": "stopping",
                "preview_id": preview_id,
                "attempts": classifications,
            }

    def transition_mission_with_governance_preview(
        self,
        *,
        action: str,
        mission_id: str,
        preview_id: str,
        facts: Mapping[str, object],
        generation: int,
        now: datetime,
        current_authority: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Atomically resume or cancel an idle-boundary Mission."""
        from .conversation.bindings import PreviewBindingError
        from .daemon.governance import consume_governance_preview

        transitions = {
            "mission_resume": ("stopped", "running", None),
            "mission_cancel": ("running", "interrupted", "human_cancel"),
        }
        if action not in transitions:
            raise ValueError("Mission governance action is invalid")
        source, target, stop_reason = transitions[action]
        with self._protocol_mutation_lock():
            state = self.load()
            missions = state.setdefault("missions", [])
            attempts = state.setdefault("mission_attempts", [])
            previews = state.setdefault("governance_previews", [])
            if any(type(value) is not list for value in (missions, attempts, previews)):
                raise ValueError("Mission governance state is invalid")
            matches = [
                item for item in missions
                if type(item) is dict and item.get("mission_id") == mission_id
            ]
            if len(matches) != 1 or matches[0].get("status") != source:
                raise ValueError("Mission governance target is invalid")
            mission = matches[0]
            active_attempts = [
                item for item in attempts
                if type(item) is dict
                and item.get("mission_id") == mission_id
                and item.get("state") in _MISSION_ATTEMPT_ACTIVE_STATES
            ]
            if active_attempts:
                raise ValueError("Mission governance requires an idle boundary")
            current_facts = {
                "mission_id": mission_id,
                "status": mission.get("status"),
                "current_step": mission.get("current_step"),
                "snapshot_hash": mission.get("snapshot_hash"),
                "active_attempts": [],
            }
            if dict(facts) != current_facts:
                raise PreviewBindingError("preview state drift")
            preview_matches = [
                item for item in previews
                if type(item) is dict and item.get("preview_id") == preview_id
            ]
            if len(preview_matches) != 1:
                raise KeyError(preview_id)
            preview_record = preview_matches[0]
            if current_authority is not None:
                self._validate_mission_controller_lineage_locked(
                    state,
                    preview_record,
                    current_authority=current_authority,
                    now=now,
                )
            consumed = consume_governance_preview(
                preview_record,
                action=action,
                facts=current_facts,
                generation=(
                    int(preview_record["generation"])
                    if current_authority is not None
                    else generation
                ),
                now=now,
            )
            if not mission_status_transition_allowed(source, target):
                raise ValueError("Mission governance transition is invalid")
            mission.update(
                {
                    "status": target,
                    "stop_reason": stop_reason,
                    "updated_at": now.astimezone(timezone.utc).isoformat(),
                }
            )
            preview_index = next(
                index for index, item in enumerate(previews)
                if type(item) is dict and item.get("preview_id") == preview_id
            )
            previews[preview_index] = consumed
            self._append_recovery_audit_locked(
                state,
                {
                    "mission_resume": "mission_resumed",
                    "mission_cancel": "mission_cancelled",
                }[action],
                {
                    "mission_id": mission_id,
                    "preview_id": preview_id,
                    "generation": generation,
                    "execution_digest": consumed["execution_digest"],
                },
            )
            self._atomic_save(state)
            return {
                "accepted": True,
                "mission_id": mission_id,
                "state": target,
                "preview_id": preview_id,
            }

    def _validate_mission_controller_lineage_locked(
        self,
        state: dict[str, Any],
        preview: Mapping[str, object],
        *,
        current_authority: Mapping[str, object],
        now: datetime,
    ) -> None:
        """Validate resume/cancel controller succession under the mutation lock."""
        from .daemon.lease import controller_lease_is_active

        lineage = preview.get("controller_lineage")
        current = state.get("controller_lease")
        runtime = state.get("daemon_runtime")
        if (
            type(lineage) is not dict
            or type(current) is not dict
            or type(runtime) is not dict
            or current_authority.get("lease_id") != current.get("lease_id")
            or current_authority.get("client_id") != current.get("client_id")
            or current_authority.get("generation") != current.get("generation")
            or not controller_lease_is_active(current, now=now)
            or runtime.get("instance_id") != lineage.get("daemon_instance_id")
            or current_authority.get("daemon_instance_id")
            != lineage.get("daemon_instance_id")
            or current.get("client_id") != lineage.get("client_id")
            or current.get("generation") != lineage.get("generation", 0) + 1
        ):
            raise ValueError("Mission governance controller lineage drift")
        terminal = self._controller_lease_terminal_state_locked(
            state,
            lease_id_hash=lineage.get("lease_id_hash"),
            generation=lineage.get("generation"),
            client_id=lineage.get("client_id"),
        )
        if terminal != "released":
            raise ValueError("Mission governance predecessor was not released")

    def _controller_lease_terminal_state_locked(
        self,
        state: dict[str, Any],
        *,
        lease_id_hash: object,
        generation: object,
        client_id: object,
    ) -> str | None:
        """Read terminal evidence without recursively acquiring the mutation lock."""
        if (
            type(lease_id_hash) is not str
            or type(generation) is not int
            or type(client_id) is not str
        ):
            raise ValueError("controller lineage identity is invalid")
        outbox = state.setdefault("daemon_event_outbox", [])
        validate_daemon_event_outbox(outbox)
        records: list[dict[str, Any]] = []
        journal = self._event_journal_source()
        if journal is not None:
            for line in journal.decode("utf-8").splitlines():
                if line.strip():
                    record = json.loads(line)
                    validate_daemon_event_record(record)
                    records.append(record)
        durable_ids = {record["event_id"] for record in records}
        records.extend(
            record for record in outbox
            if type(record) is dict and record.get("event_id") not in durable_ids
        )
        matched: list[str] = []
        for record in records:
            payload = record.get("payload")
            lease_id = payload.get("lease_id") if type(payload) is dict else None
            if (
                record.get("event_type")
                in {"controller_lease_released", "controller_lease_expired"}
                and type(lease_id) is str
                and hashlib.sha256(lease_id.encode("utf-8")).hexdigest()
                == lease_id_hash
                and payload.get("generation") == generation
                and payload.get("client_id") == client_id
            ):
                matched.append(
                    str(record["event_type"]).removeprefix("controller_lease_")
                )
        terminal_states = set(matched)
        if terminal_states == {"released"}:
            return "released"
        if terminal_states == {"expired"}:
            return "expired"
        return None

    def change_worker_ownership_with_governance_preview(
        self,
        *,
        action: str,
        agent_id: str,
        preview_id: str,
        facts: Mapping[str, object],
        generation: int,
        now: datetime,
    ) -> dict[str, object]:
        """Atomically consume authority and append both ownership edges."""
        from .conversation.bindings import PreviewBindingError
        from .conversation.models import build_conversation_transition
        from .daemon.governance import consume_governance_preview

        edges = {
            "takeover": (
                "agentdeck_owned", "takeover_pending", "human_owned"
            ),
            "return_control": (
                "human_owned", "return_pending", "agentdeck_owned"
            ),
        }
        if action not in edges:
            raise ValueError("Worker ownership action is invalid")
        source, pending, target = edges[action]
        with self._protocol_mutation_lock():
            state = self.load()
            collections = self._conversation_collections(state)
            projection = validate_conversation_history(
                collections, state["conversation_state_transitions"]
            )
            ownership_initialized = agent_id in projection["ownership_states"]
            ownership = projection["ownership_states"].get(
                agent_id, "agentdeck_owned"
            )
            if ownership != source:
                raise ValueError("Worker ownership source is invalid")
            sessions = state["conversation_sessions"]
            if not sessions:
                raise ValueError("Worker ownership conversation is missing")
            conversation_id = sessions[-1].get("conversation_id")
            if type(conversation_id) is not str:
                raise ValueError("Worker ownership conversation is invalid")
            self._validate_protocol_identities(state)
            protocol_states = self._validate_protocol_transition_history(state)
            session_ids = {
                item.get("session_id")
                for item in state.get("agent_sessions", [])
                if type(item) is dict and item.get("agent_id") == agent_id
            }
            active_turn_ids = sorted(
                str(item["turn_id"])
                for item in state.get("protocol_turns", [])
                if type(item) is dict
                and item.get("session_id") in session_ids
                and protocol_states.get(
                    ("turn", item.get("turn_id")), item.get("state")
                ) not in {"completed", "blocked", "failed", "cancelled", "ambiguous"}
            )
            pending_permission_ids = sorted(
                str(item["permission_id"])
                for item in state.get("permission_requests", [])
                if type(item) is dict
                and item.get("session_id") in session_ids
                and protocol_states.get(
                    ("permission", item.get("permission_id")), item.get("status")
                ) == "pending"
            )
            active_attempt_ids = sorted(
                str(item["attempt_id"])
                for item in state.get("mission_attempts", [])
                if type(item) is dict
                and item.get("agent_id") == agent_id
                and item.get("state") in _MISSION_ATTEMPT_ACTIVE_STATES
            )
            safe = not (
                active_turn_ids or pending_permission_ids or active_attempt_ids
            )
            current_facts = {
                "agent_id": agent_id,
                "conversation_id": conversation_id,
                "ownership": ownership,
                "safe_boundary": safe,
                "reconciled": facts.get("reconciled"),
                "active_turn_ids": active_turn_ids,
                "pending_permission_ids": pending_permission_ids,
                "active_attempt_ids": active_attempt_ids,
                "baseline_id": facts.get("baseline_id"),
                "reconciliation": facts.get("reconciliation"),
                "changed_paths": facts.get("changed_paths"),
                "reported_changes": facts.get("reported_changes"),
            }
            reconciliation = current_facts["reconciliation"]
            if type(reconciliation) is not dict or set(reconciliation) != {
                "runtime_transport", "runtime_evidence_digest", "session_digest",
                "artifact_digest", "worktree_digest", "worktree_manifest",
            }:
                raise ValueError("Worker reconciliation facts are invalid")
            baselines = state.setdefault("worker_takeover_baselines", [])
            active_baselines = [
                item for item in baselines
                if type(item) is dict and item.get("agent_id") == agent_id
                and item.get("state") == "active"
            ]
            if action == "takeover":
                if active_baselines or current_facts["baseline_id"] is not None:
                    raise ValueError("Worker reconciliation baseline conflicts")
            else:
                if (
                    len(active_baselines) != 1
                    or current_facts["baseline_id"]
                    != active_baselines[0].get("baseline_id")
                    or current_facts["reconciled"] is not True
                ):
                    raise ValueError("Worker reconciliation baseline is invalid")
            if dict(facts) != current_facts:
                raise PreviewBindingError("preview state drift")
            previews = state.setdefault("governance_previews", [])
            preview_matches = [
                item for item in previews
                if type(item) is dict and item.get("preview_id") == preview_id
            ]
            if len(preview_matches) != 1:
                raise KeyError(preview_id)
            consumed = consume_governance_preview(
                preview_matches[0],
                action=action,
                facts=current_facts,
                generation=generation,
                now=now,
            )
            created_at = now.astimezone(timezone.utc).isoformat()
            transitions = []
            if not ownership_initialized:
                transitions.append(
                    build_conversation_transition(
                        transition_id=new_id("cst"),
                        conversation_id=conversation_id,
                        entity_type="ownership",
                        entity_id=agent_id,
                        from_state=None,
                        to_state="agentdeck_owned",
                        reason="ownership_initialized",
                        created_at=created_at,
                    )
                )
            transitions.extend([
                build_conversation_transition(
                    transition_id=new_id("cst"),
                    conversation_id=conversation_id,
                    entity_type="ownership",
                    entity_id=agent_id,
                    from_state=source,
                    to_state=pending,
                    reason=f"{action}_confirmed",
                    created_at=created_at,
                ),
                build_conversation_transition(
                    transition_id=new_id("cst"),
                    conversation_id=conversation_id,
                    entity_type="ownership",
                    entity_id=agent_id,
                    from_state=pending,
                    to_state=target,
                    reason=f"{action}_completed",
                    created_at=created_at,
                ),
            ])
            state["conversation_state_transitions"].extend(transitions)
            validate_conversation_history(
                self._conversation_collections(state),
                state["conversation_state_transitions"],
            )
            preview_index = next(
                index for index, item in enumerate(previews)
                if type(item) is dict and item.get("preview_id") == preview_id
            )
            previews[preview_index] = consumed
            if action == "takeover":
                baseline = {
                    "baseline_id": new_id("wob"),
                    "agent_id": agent_id,
                    "generation": generation,
                    "state": "active",
                    "created_at": created_at,
                    **copy.deepcopy(reconciliation),
                }
                baselines.append(baseline)
            else:
                active_baselines[0]["state"] = "reconciled"
                active_baselines[0]["reconciled_at"] = created_at
                active_baselines[0]["reported_changes"] = copy.deepcopy(
                    current_facts["reported_changes"]
                )
                for item in state.setdefault("worker_reconciliation_decisions", []):
                    if (
                        type(item) is dict
                        and item.get("agent_id") == agent_id
                        and item.get("baseline_id") == current_facts["baseline_id"]
                        and item.get("status") == "ambiguous"
                    ):
                        item["status"] = "resolved"
                        item["resolved_at"] = created_at
            event = EventRecord.create(
                "worker_ownership_changed",
                {
                    "agent_id": agent_id,
                    "from_state": source,
                    "to_state": target,
                    "preview_id": preview_id,
                    "generation": generation,
                },
            )
            state["conversation_event_outbox"].append(asdict(event))
            self._append_recovery_audit_locked(
                state,
                "worker_ownership_governed",
                {
                    "agent_id": agent_id,
                    "action": action,
                    "preview_id": preview_id,
                    "generation": generation,
                },
            )
            self._atomic_save(state)
            return {
                "accepted": True,
                "agent_id": agent_id,
                "ownership": target,
                "preview_id": preview_id,
                "baseline_id": (
                    baseline["baseline_id"] if action == "takeover"
                    else current_facts["baseline_id"]
                ),
            }

    def record_worker_reconciliation_ambiguity(
        self,
        *,
        agent_id: str,
        generation: int,
        blocker: str,
        evidence: Mapping[str, object],
        now: datetime,
    ) -> dict[str, object]:
        """Persist one exact fail-closed return-control classification."""
        if (
            type(agent_id) is not str
            or not agent_id
            or type(generation) is not int
            or generation < 1
            or type(blocker) is not str
            or not blocker.strip()
            or len(blocker.encode("utf-8")) > 512
            or type(evidence) is not dict
        ):
            raise ValueError("Worker reconciliation ambiguity is invalid")
        evidence_hash = canonical_snapshot_hash(dict(evidence))
        with self._protocol_mutation_lock():
            state = self.load()
            baselines = [
                item for item in state.setdefault("worker_takeover_baselines", [])
                if type(item) is dict
                and item.get("agent_id") == agent_id
                and item.get("state") == "active"
            ]
            if len(baselines) != 1 or baselines[0].get("generation") != generation:
                raise ValueError("Worker reconciliation baseline is invalid")
            baseline_id = baselines[0].get("baseline_id")
            decisions = state.setdefault("worker_reconciliation_decisions", [])
            if type(decisions) is not list or any(
                type(item) is not dict for item in decisions
            ):
                raise ValueError("Worker reconciliation decisions are invalid")
            exact = [
                item for item in decisions
                if item.get("agent_id") == agent_id
                and item.get("baseline_id") == baseline_id
                and item.get("generation") == generation
                and item.get("blocker") == blocker
                and item.get("evidence_hash") == evidence_hash
                and item.get("status") == "ambiguous"
            ]
            if exact:
                if len(exact) != 1:
                    raise ValueError("Worker reconciliation decisions are invalid")
                return copy.deepcopy(exact[0])
            created_at = now.astimezone(timezone.utc).isoformat()
            record = {
                "decision_id": new_id("wrd"),
                "agent_id": agent_id,
                "baseline_id": baseline_id,
                "generation": generation,
                "status": "ambiguous",
                "blocker": blocker,
                "evidence_hash": evidence_hash,
                "created_at": created_at,
            }
            decisions.append(record)
            event = EventRecord.create(
                "worker_reconciliation_ambiguous",
                {
                    "decision_id": record["decision_id"],
                    "agent_id": agent_id,
                    "baseline_id": baseline_id,
                    "generation": generation,
                    "blocker": blocker,
                    "evidence_hash": evidence_hash,
                },
            )
            state.setdefault("conversation_event_outbox", []).append(asdict(event))
            self._append_recovery_audit_locked(
                state,
                "worker_reconciliation_ambiguous",
                dict(event.payload),
            )
            self._atomic_save(state)
            return copy.deepcopy(record)

    def record_transport_reroute_with_governance_preview(
        self,
        *,
        mission_id: str,
        step_id: str,
        agent_id: str,
        to_transport: str,
        preview_id: str,
        facts: Mapping[str, object],
        generation: int,
        now: datetime,
    ) -> dict[str, object]:
        """Record one future-attempt transport override without rewriting snapshot."""
        from .conversation.bindings import PreviewBindingError
        from .daemon.governance import consume_governance_preview

        with self._protocol_mutation_lock():
            state = self.load()
            missions = [
                item for item in state.setdefault("missions", [])
                if type(item) is dict and item.get("mission_id") == mission_id
            ]
            if len(missions) != 1:
                raise ValueError("reroute Mission is invalid")
            mission = missions[0]
            snapshot = mission.get("execution_snapshot")
            mission_snapshot = snapshot.get("mission") if type(snapshot) is dict else None
            steps = mission_snapshot.get("steps") if type(mission_snapshot) is dict else None
            workers = snapshot.get("workers") if type(snapshot) is dict else None
            if type(steps) is not list or type(workers) is not list:
                raise ValueError("reroute snapshot is invalid")
            step_matches = [
                item for item in steps
                if type(item) is dict
                and item.get("step_id") == step_id
                and item.get("agent_id") == agent_id
            ]
            worker_matches = [
                item for item in workers
                if type(item) is dict and item.get("agent_id") == agent_id
            ]
            if len(step_matches) != 1 or len(worker_matches) != 1:
                raise ValueError("reroute frozen scope is invalid")
            from_transport = worker_matches[0].get("configured_transport")
            if (
                from_transport not in {"acp", "tmux"}
                or to_transport not in {"acp", "tmux"}
                or from_transport == to_transport
            ):
                raise ValueError("reroute transport is invalid")
            attempts = [
                item for item in state.setdefault("mission_attempts", [])
                if type(item) is dict
                and item.get("mission_id") == mission_id
                and item.get("step_id") == step_id
            ]
            if attempts:
                raise ValueError("reroute cannot modify a frozen attempt")
            projection = validate_conversation_history(
                {
                    key: state.setdefault(key, [])
                    for key in (
                        "conversation_sessions", "conversation_turns",
                        "conversation_preview_bindings",
                    )
                },
                state.setdefault("conversation_state_transitions", []),
            )
            ownership = projection["ownership_states"].get(
                agent_id, "agentdeck_owned"
            )
            if ownership != "agentdeck_owned":
                raise ValueError("reroute Worker is not AgentDeck-owned")
            current_facts = {
                "mission_id": mission_id,
                "step_id": step_id,
                "agent_id": agent_id,
                "snapshot_hash": mission.get("snapshot_hash"),
                "mission_status": mission.get("status"),
                "current_step": mission.get("current_step"),
                "ownership": ownership,
                "attempt_state": "none",
                "from_transport": from_transport,
                "to_transport": to_transport,
            }
            if dict(facts) != current_facts:
                raise PreviewBindingError("preview state drift")
            reroutes = state.setdefault("mission_transport_reroutes", [])
            if type(reroutes) is not list or any(
                type(item) is not dict for item in reroutes
            ):
                raise ValueError("reroute state is invalid")
            if any(
                item.get("mission_id") == mission_id
                and item.get("step_id") == step_id
                for item in reroutes
            ):
                raise ValueError("reroute already exists")
            previews = state.setdefault("governance_previews", [])
            preview_matches = [
                item for item in previews
                if type(item) is dict and item.get("preview_id") == preview_id
            ]
            if len(preview_matches) != 1:
                raise KeyError(preview_id)
            consumed = consume_governance_preview(
                preview_matches[0], action="reroute", facts=current_facts,
                generation=generation, now=now,
            )
            record = {
                **current_facts,
                "preview_id": preview_id,
                "generation": generation,
                "created_at": now.astimezone(timezone.utc).isoformat(),
            }
            reroutes.append(record)
            preview_index = next(
                index for index, item in enumerate(previews)
                if type(item) is dict and item.get("preview_id") == preview_id
            )
            previews[preview_index] = consumed
            self._append_recovery_audit_locked(
                state,
                "mission_transport_rerouted",
                {
                    "mission_id": mission_id,
                    "step_id": step_id,
                    "agent_id": agent_id,
                    "from_transport": from_transport,
                    "to_transport": to_transport,
                    "preview_id": preview_id,
                    "generation": generation,
                },
            )
            self._atomic_save(state)
            return {
                "accepted": True,
                "mission_id": mission_id,
                "step_id": step_id,
                "effective_transport": to_transport,
                "preview_id": preview_id,
            }

    def record_daemon_state(
        self,
        record: Mapping[str, object],
        *,
        expected_project_root_hash: str,
    ) -> dict[str, object]:
        validated = validate_daemon_record(record)
        if validated["project_root_hash"] != expected_project_root_hash:
            raise ValueError("project identity mismatch")
        with self._protocol_mutation_lock():
            state = self.load()
            state["daemon_runtime"] = dict(validated)
            self._atomic_save(state)
        return dict(validated)

    def commit_controller_lease(
        self, transition: LeaseTransition
    ) -> dict[str, object]:
        # Reject malformed current state and transitions before creating the lock path.
        initial_state = self.load()
        initial_outbox = initial_state.setdefault("daemon_event_outbox", [])
        initial_event_ids = validate_daemon_event_outbox(initial_outbox)
        validate_lease_transition(initial_state.get("controller_lease"), transition)
        candidate_event_id = transition.audit_event.event_id
        if candidate_event_id in initial_event_ids:
            raise ValueError("duplicate daemon event identity")
        # Lease commits stage daemon events in the durable outbox. The explicit
        # outbox-to-journal flush below holds this same mutation lock; this expiry
        # scan does not make arbitrary append_event calls atomic with lease commits.
        if transition.action == "expire" and (
            candidate_event_id in self._daemon_journal_event_ids()
        ):
            raise ValueError("duplicate daemon event identity")
        with self._protocol_mutation_lock():
            state = self.load()
            outbox = state.setdefault("daemon_event_outbox", [])
            event_ids = validate_daemon_event_outbox(outbox)
            persisted = state.get("controller_lease")
            validate_lease_transition(persisted, transition)
            if candidate_event_id in event_ids:
                raise ValueError("duplicate daemon event identity")
            if transition.action == "expire" and (
                candidate_event_id in self._daemon_journal_event_ids()
            ):
                raise ValueError("duplicate daemon event identity")
            assert transition.current is not None
            summary = transition.current.summary()
            state["controller_lease"] = summary
            outbox.append(transition.audit_event.summary())
            self._atomic_save(state)
        return dict(summary)

    def _daemon_journal_event_ids(self) -> set[str]:
        return _strict_event_journal_ids(
            self._event_journal_source(),
            malformed_error="daemon event journal is malformed",
            duplicate_error="duplicate daemon event identity",
        )

    def controller_lease_terminal_state(
        self, *, lease_id: str, generation: int
    ) -> str | None:
        """Return durable release/expiry evidence for one lease generation."""
        if type(lease_id) is not str or not lease_id or type(generation) is not int:
            raise ValueError("controller lease identity is invalid")
        with self._protocol_mutation_lock():
            state = self.load()
            outbox = state.setdefault("daemon_event_outbox", [])
            validate_daemon_event_outbox(outbox)
            journal_ids = self._daemon_journal_event_ids()
            records: list[dict[str, Any]] = []
            journal = self._event_journal_source()
            if journal is not None:
                for line in journal.decode("utf-8").splitlines():
                    if line.strip():
                        record = json.loads(line)
                        validate_daemon_event_record(record)
                        records.append(record)
            for record in outbox:
                event_id = validate_daemon_event_record(record)
                if event_id not in journal_ids:
                    records.append(record)
            matching = [
                record["event_type"]
                for record in records
                if record.get("event_type")
                in {"controller_lease_released", "controller_lease_expired"}
                and type(record.get("payload")) is dict
                and record["payload"].get("lease_id") == lease_id
                and record["payload"].get("generation") == generation
            ]
            terminal_states = set(matching)
            if terminal_states == {"controller_lease_released"}:
                return "released"
            if terminal_states == {"controller_lease_expired"}:
                return "expired"
            return None

    def _strict_protocol_journal_event_ids(self) -> set[str]:
        return _strict_event_journal_ids(
            self._event_journal_source(),
            malformed_error="protocol event journal is malformed",
            duplicate_error="duplicate protocol event identity",
        )

    def flush_daemon_event_outbox(self) -> dict[str, int]:
        with self._protocol_mutation_lock():
            state = self.load()
            outbox = state.setdefault("daemon_event_outbox", [])
            validate_daemon_event_outbox(outbox)
            durable_ids = self._daemon_journal_event_ids()
            pending = [
                item
                for item in outbox
                if validate_daemon_event_record(item) not in durable_ids
            ]
            if pending:
                encoded = "".join(
                    json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
                    for item in pending
                ).encode("utf-8")
                self._append_event_bytes_locked(encoded)
            state["daemon_event_outbox"] = []
            self._atomic_save(state)
            return {
                "flushed": len(pending),
                "already_durable": len(outbox) - len(pending),
            }

    def _atomic_save(self, state: dict[str, Any]) -> None:
        key = str(self.root.resolve())
        entry = _state_lock_entries().get(key)
        if entry is None:
            with self._protocol_mutation_lock():
                self._atomic_save(state)
            return
        _deck_fd, state_fd = self._verify_current_mutation_anchor()
        entry = _state_lock_entries()[str(self.root.resolve())]
        lock_fd = entry["lock_fd"]
        assert isinstance(lock_fd, int)
        held_lock = os.fstat(lock_fd)
        held_lock_identity = (held_lock.st_dev, held_lock.st_ino)
        displaced_fd = _open_retained_regular_at(
            state_fd,
            "state.json",
            error="migration state file is unsafe",
        )
        displaced_identity = (
            None
            if displaced_fd is None
            else (os.fstat(displaced_fd).st_dev, os.fstat(displaced_fd).st_ino)
        )
        displaced_source = (
            None if displaced_fd is None else _read_retained_bytes(displaced_fd)
        )
        expected_install = _encoded_state(state)
        if "effect_guard" in entry:
            raise RuntimeError("state mutation effect guard is already active")
        entry["effect_guard"] = {
            "target": "state.json",
            "source": displaced_source,
            "identity": displaced_identity,
            "effect_installed": False,
        }
        try:
            try:
                self._verify_current_mutation_anchor()
                current_source = _named_regular_snapshot_at(state_fd, "state.json")
                if displaced_fd is None:
                    if current_source is not None:
                        raise ValueError("state source facts drifted")
                elif current_source != (displaced_source, displaced_identity):
                    raise ValueError("state source facts drifted")
                _atomic_save_state_at(state_fd, state)
                self._verify_current_mutation_anchor()
                installed = _read_state_bytes_at(state_fd)
                assert installed is not None
                if installed != expected_install:
                    self._verify_current_mutation_anchor()
                    raise ValueError("state changed during atomic save")
                self._verify_current_mutation_anchor()
            except BaseException:
                current = _named_regular_snapshot_at(state_fd, "state.json")
                if (
                    entry["effect_guard"].get("effect_installed") is True
                    and current is not None
                    and current[0] == expected_install
                    and current[1] != displaced_identity
                ):
                    _restore_displaced_file_if_exact(
                        state_fd,
                        "state.json",
                        installed=expected_install,
                        displaced_fd=displaced_fd,
                        held_lock_identity=held_lock_identity,
                    )
                raise
        finally:
            try:
                _release_effect_guard(entry)
            finally:
                if displaced_fd is not None:
                    os.close(displaced_fd)
        self._remember_loaded_state(
            state,
            "sha256:" + hashlib.sha256(installed).hexdigest(),
            replace_token=True,
        )

    @staticmethod
    def _conversation_collections(state: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        for key in (
            "conversation_sessions",
            "conversation_turns",
            "conversation_preview_bindings",
        ):
            value = state.setdefault(key, [])
            if type(value) is not list:
                raise TypeError(f"{key} must be a list")
            result[key] = value
        transitions = state.setdefault("conversation_state_transitions", [])
        if type(transitions) is not list:
            raise TypeError("conversation_state_transitions must be a list")
        outbox = state.setdefault("conversation_event_outbox", [])
        if type(outbox) is not list:
            raise TypeError("conversation_event_outbox must be a list")
        return result

    def _apply_conversation_mutation(
        self, state: dict[str, Any], mutation: ConversationMutation
    ) -> dict[str, Any]:
        if not isinstance(mutation, ConversationMutation):
            raise TypeError("mutation must be a ConversationMutation")
        proposed = copy.deepcopy(state)
        self._conversation_collections(proposed)
        allowed = {
            "conversation_sessions",
            "conversation_turns",
            "conversation_preview_bindings",
            "conversation_state_transitions",
            "plans",
            "missions",
        }
        for collection, records in mutation.append_records.items():
            if collection not in allowed:
                raise ValueError(f"conversation mutation collection is not allowed: {collection}")
            if not isinstance(records, tuple) or any(
                not isinstance(record, Mapping) for record in records
            ):
                raise TypeError("conversation mutation records must be tuples of mappings")
            target = proposed.setdefault(collection, [])
            if type(target) is not list:
                raise TypeError(f"{collection} must be a list")
            target.extend(copy.deepcopy(list(records)))
        event_ids = [event.event_id for event in mutation.events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("duplicate conversation event identity")
        existing_outbox_ids = {
            item.get("event_id")
            for item in proposed["conversation_event_outbox"]
            if isinstance(item, dict)
        }
        if any(event_id in existing_outbox_ids for event_id in event_ids):
            raise ValueError("duplicate conversation event identity")
        proposed["conversation_event_outbox"].extend(
            asdict(event) for event in mutation.events
        )
        validate_conversation_history(
            self._conversation_collections(proposed),
            proposed["conversation_state_transitions"],
        )
        return proposed

    def _flush_conversation_event_outbox_locked(
        self, state: dict[str, Any] | None = None
    ) -> int:
        state = state if state is not None else self.load()
        self._conversation_collections(state)
        outbox = state["conversation_event_outbox"]
        if not outbox:
            return 0
        existing_ids = self._protocol_event_ids()
        appended = 0
        for item in outbox:
            if not isinstance(item, dict):
                raise TypeError("conversation event outbox items must be objects")
            event_id = item.get("event_id")
            if event_id in existing_ids:
                continue
            event = EventRecord(**item)
            self.append_event(event)
            existing_ids.add(event.event_id)
            appended += 1
        state["conversation_event_outbox"] = []
        self._atomic_save(state)
        return appended

    def flush_conversation_event_outbox(self) -> int:
        with self._protocol_mutation_lock():
            return self._flush_conversation_event_outbox_locked()

    def commit_conversation_mutation(
        self, mutation: ConversationMutation
    ) -> dict[str, object]:
        # Reject malformed/history-invalid batches before creating the lock file.
        self._apply_conversation_mutation(self.load(), mutation)
        event_ids = {event.event_id for event in mutation.events}
        if event_ids & self._protocol_event_ids():
            raise ValueError("duplicate conversation event identity")
        with self._protocol_mutation_lock():
            proposed = self._apply_conversation_mutation(self.load(), mutation)
            if event_ids & self._protocol_event_ids():
                raise ValueError("duplicate conversation event identity")
            self._atomic_save(proposed)
            try:
                flushed = self._flush_conversation_event_outbox_locked(proposed)
            except OSError:
                return {
                    "committed": True,
                    "events_flushed": 0,
                    "outbox_blocked": True,
                }
        return {
            "committed": True,
            "events_flushed": flushed,
            "outbox_blocked": False,
        }

    @staticmethod
    def _unique_protocol_record(
        records: list[dict[str, Any]], key: str, value: str, duplicate_error: str
    ) -> dict[str, Any]:
        matches = [item for item in records if isinstance(item, dict) and item.get(key) == value]
        if len(matches) > 1:
            raise ValueError(duplicate_error)
        if not matches:
            raise KeyError(value)
        return matches[0]

    @staticmethod
    def _validate_protocol_identities(state: dict[str, Any]) -> None:
        sessions = state.setdefault("agent_sessions", [])
        turns = state.setdefault("protocol_turns", [])
        updates = state.setdefault("transport_updates", [])
        permissions = state.setdefault("permission_requests", [])
        transitions = state.setdefault("protocol_state_transitions", [])
        state.setdefault("protocol_event_outbox", [])
        session_ids = [item.get("session_id") for item in sessions if isinstance(item, dict)]
        turn_ids = [item.get("turn_id") for item in turns if isinstance(item, dict)]
        update_ids = [item.get("update_id") for item in updates if isinstance(item, dict)]
        permission_ids = [item.get("permission_id") for item in permissions if isinstance(item, dict)]
        transition_ids = [item.get("transition_id") for item in transitions if isinstance(item, dict)]
        if len(session_ids) != len(set(session_ids)):
            raise ValueError("duplicate agent session identity")
        if len(turn_ids) != len(set(turn_ids)):
            raise ValueError("duplicate protocol turn identity")
        if len(update_ids) != len(set(update_ids)):
            raise ValueError("duplicate transport update identity")
        if len(permission_ids) != len(set(permission_ids)):
            raise ValueError("duplicate permission request identity")
        if len(transition_ids) != len(set(transition_ids)):
            raise ValueError("duplicate protocol transition identity")

    @staticmethod
    def _validate_protocol_lineage(state: dict[str, Any]) -> None:
        sessions = {
            item.get("session_id"): item
            for item in state.get("agent_sessions", [])
            if isinstance(item, dict) and isinstance(item.get("session_id"), str)
        }
        turns = {
            item.get("turn_id"): item
            for item in state.get("protocol_turns", [])
            if isinstance(item, dict) and isinstance(item.get("turn_id"), str)
        }
        for turn in state.get("protocol_turns", []):
            if isinstance(turn, dict) and turn.get("session_id") not in sessions:
                raise ValueError("protocol turn session reference missing")
        for collection, label in (
            ("transport_updates", "transport update"),
            ("permission_requests", "permission request"),
        ):
            for item in state.get(collection, []):
                if not isinstance(item, dict):
                    continue
                if item.get("session_id") not in sessions:
                    raise ValueError(f"{label} session reference missing")
                turn = turns.get(item.get("turn_id"))
                if turn is None:
                    raise ValueError(f"{label} turn reference missing")
                if item.get("session_id") != turn.get("session_id"):
                    raise ValueError(f"{label} session mismatch")

    @contextmanager
    def _protocol_mutation_lock(self):
        key = str(self.root.resolve())
        entries = _state_lock_entries()
        existing = entries.get(key)
        if existing is not None:
            existing["depth"] = int(existing["depth"]) + 1
            try:
                yield existing["anchored"]
            finally:
                existing["depth"] = int(existing["depth"]) - 1
            return
        if entries:
            raise RuntimeError("nested cross-project state mutations are forbidden")
        canonical_root = self.root.resolve()
        try:
            root_fd = os.open(canonical_root, _MIGRATION_DIRECTORY_FLAGS)
        except OSError as exc:
            raise ValueError("project root is unsafe for state mutation") from exc
        root_locked = False
        try:
            fcntl.flock(root_fd, fcntl.LOCK_EX)
            root_locked = True
            _verify_project_root_anchor(canonical_root, root_fd)
            with _secure_project_state_directory(
                canonical_root, create_state=True
            ) as anchored:
                _deck_fd, state_fd = anchored
                flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
                lock_fd: int | None = None
                legacy_locked = False
                try:
                    try:
                        try:
                            lock_fd = os.open(
                                "protocol-mutation.lock",
                                flags | os.O_CREAT | os.O_EXCL,
                                0o600,
                                dir_fd=state_fd,
                            )
                        except FileExistsError:
                            lock_fd = os.open(
                                "protocol-mutation.lock", flags, dir_fd=state_fd
                            )
                    except OSError as exc:
                        raise ValueError("protocol mutation lock is unsafe") from exc
                    _verify_regular_file_at(
                        state_fd,
                        "protocol-mutation.lock",
                        lock_fd,
                        error="protocol mutation lock is unsafe",
                    )
                    fcntl.flock(lock_fd, fcntl.LOCK_EX)
                    legacy_locked = True
                    _verify_regular_file_at(
                        state_fd,
                        "protocol-mutation.lock",
                        lock_fd,
                        error="protocol mutation lock changed after acquisition",
                    )
                    _verify_mutation_anchor(
                        canonical_root, root_fd, anchored
                    )
                    entries[key] = {
                        "depth": 1,
                        "anchored": anchored,
                        "lock_fd": lock_fd,
                        "root_fd": root_fd,
                    }
                    try:
                        yield anchored
                    finally:
                        entry = entries.pop(key)
                        if entry["depth"] != 1:
                            raise RuntimeError(
                                "state mutation lock depth is unbalanced"
                            )
                finally:
                    if lock_fd is not None:
                        try:
                            if legacy_locked:
                                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                        finally:
                            os.close(lock_fd)
        finally:
            try:
                if root_locked:
                    fcntl.flock(root_fd, fcntl.LOCK_UN)
            finally:
                os.close(root_fd)

    def _protocol_event_ids(self) -> set[str]:
        event_ids: set[str] = set()
        journal = self._event_journal_source()
        if journal is None:
            return event_ids
        try:
            lines = journal.decode("utf-8").splitlines()
        except UnicodeDecodeError:
            return event_ids
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and isinstance(event.get("event_id"), str):
                event_ids.add(event["event_id"])
        return event_ids

    def _flush_protocol_event_outbox_locked(self, state: dict[str, Any] | None = None) -> int:
        state = state if state is not None else self.load()
        outbox = state.setdefault("protocol_event_outbox", [])
        if not outbox:
            return 0
        existing_ids = self._protocol_event_ids()
        appended = 0
        for item in outbox:
            event_id = item.get("event_id") if isinstance(item, dict) else None
            if event_id in existing_ids:
                continue
            event = EventRecord(**item)
            self.append_event(event)
            existing_ids.add(event.event_id)
            appended += 1
        state["protocol_event_outbox"] = []
        self._atomic_save(state)
        return appended

    def flush_protocol_event_outbox(self) -> int:
        with self._protocol_mutation_lock():
            return self._flush_protocol_event_outbox_locked()

    def _save_protocol_record(
        self,
        state: dict[str, Any],
        collection: str,
        record: dict[str, Any],
        event: EventRecord,
    ) -> dict[str, Any]:
        state.setdefault(collection, []).append(record)
        state.setdefault("protocol_event_outbox", []).append(asdict(event))
        self.save(state)
        try:
            self.append_event(event)
        except OSError:
            return record
        state["protocol_event_outbox"] = [
            item for item in state["protocol_event_outbox"]
            if item.get("event_id") != event.event_id
        ]
        try:
            self.save(state)
        except OSError:
            pass
        return record

    def record_agent_session(
        self,
        agent_id: str,
        provider: str,
        transport: str,
        native_session_id: str | None,
        workspace: str,
        capabilities: TransportCapabilities,
    ) -> dict[str, Any]:
        with self._protocol_mutation_lock():
            state = self.load()
            self._validate_protocol_identities(state)
            record = build_agent_session(
                agent_id, provider, transport, native_session_id, workspace, capabilities
            )
            if any(item.get("session_id") == record["session_id"] for item in state["agent_sessions"]):
                raise ValueError("duplicate agent session identity")
            self._flush_protocol_event_outbox_locked(state)
            event = EventRecord.create("agent_session_recorded", {
                "session_id": record["session_id"],
                "agent_id": record["agent_id"],
                "transport": record["transport"],
            })
            return self._save_protocol_record(state, "agent_sessions", record, event)

    def record_protocol_turn(
        self, session_id: str, message_id: str, kind: str = "prompt"
    ) -> dict[str, Any]:
        with self._protocol_mutation_lock():
            state = self.load()
            self._validate_protocol_identities(state)
            self._unique_protocol_record(
                state.setdefault("agent_sessions", []), "session_id", session_id,
                "duplicate agent session identity",
            )
            record = build_turn(session_id, message_id, kind)
            if any(item.get("turn_id") == record["turn_id"] for item in state["protocol_turns"]):
                raise ValueError("duplicate protocol turn identity")
            self._flush_protocol_event_outbox_locked(state)
            event = EventRecord.create("protocol_turn_recorded", {
                "turn_id": record["turn_id"],
                "session_id": record["session_id"],
                "message_id": record["message_id"],
            })
            return self._save_protocol_record(state, "protocol_turns", record, event)

    @staticmethod
    def _protocol_transition_entity(
        state: dict[str, Any], entity_type: str, entity_id: str
    ) -> dict[str, Any]:
        entity_sources = {
            "session": ("agent_sessions", "session_id", "duplicate agent session identity", "state"),
            "turn": ("protocol_turns", "turn_id", "duplicate protocol turn identity", "state"),
            "permission": (
                "permission_requests", "permission_id",
                "duplicate permission request identity", "status",
            ),
        }
        collection, key, duplicate_error, _ = entity_sources[entity_type]
        return StateStore._unique_protocol_record(
            state.setdefault(collection, []), key, entity_id, duplicate_error
        )

    @staticmethod
    def _derived_protocol_state(
        state: dict[str, Any], entity_type: str, entity_id: str, base: dict[str, Any]
    ) -> str:
        state_field = "status" if entity_type == "permission" else "state"
        current = base.get(state_field)
        if type(current) is not str:
            raise ValueError("invalid protocol base state")
        for item in state.setdefault("protocol_state_transitions", []):
            if not isinstance(item, dict) or item.get("entity_type") != entity_type or item.get("entity_id") != entity_id:
                continue
            if item.get("from_state") != current:
                raise ValueError("stale protocol transition from_state")
            validate_transition_edge(entity_type, item.get("from_state"), item.get("to_state"))
            current = item["to_state"]
        return current

    @staticmethod
    def _validate_protocol_transition_history(
        state: dict[str, Any],
    ) -> dict[tuple[str, str], str]:
        transitions = state.setdefault("protocol_state_transitions", [])
        if type(transitions) is not list:
            raise TypeError("protocol_state_transitions must be a list")
        current_states: dict[tuple[str, str], str] = {}
        entity_specs = {
            "session": ("agent_sessions", "session_id", "state", AGENT_SESSION_STATES, "duplicate agent session identity"),
            "turn": ("protocol_turns", "turn_id", "state", TURN_STATES, "duplicate protocol turn identity"),
            "permission": ("permission_requests", "permission_id", "status", PERMISSION_STATES, "duplicate permission request identity"),
        }
        entity_maps: dict[str, dict[str, dict[str, Any]]] = {}
        for entity_type, (collection, identity_field, state_field, vocabulary, duplicate_error) in entity_specs.items():
            records = state.setdefault(collection, [])
            if type(records) is not list:
                raise TypeError(f"{collection} must be a list")
            indexed: dict[str, dict[str, Any]] = {}
            for record in records:
                if type(record) is not dict:
                    raise TypeError(f"{collection} items must be objects")
                identity = record.get(identity_field)
                if type(identity) is not str or not identity:
                    raise ValueError(f"invalid {identity_field}")
                if identity in indexed:
                    raise ValueError(duplicate_error)
                base_state = record.get(state_field)
                if type(base_state) is not str or base_state not in vocabulary:
                    raise ValueError("invalid protocol base state")
                indexed[identity] = record
            entity_maps[entity_type] = indexed
        for item in transitions:
            validate_protocol_transition_record(item)
            entity_type = item["entity_type"]
            entity_id = item["entity_id"]
            key = (entity_type, entity_id)
            if key not in current_states:
                entity = entity_maps[entity_type].get(entity_id)
                if entity is None:
                    raise KeyError(entity_id)
                state_field = entity_specs[entity_type][2]
                current_states[key] = entity[state_field]
            if item["from_state"] != current_states[key]:
                raise ValueError("stale protocol transition from_state")
            current_states[key] = item["to_state"]
        return current_states

    def record_protocol_transition(
        self,
        entity_type: str,
        entity_id: str,
        from_state: str,
        to_state: str,
        reason: str | None,
        details: dict[str, Any],
    ) -> dict[str, Any]:
        with self._protocol_mutation_lock():
            state = self.load()
            self._validate_protocol_identities(state)
            current_states = self._validate_protocol_transition_history(state)
            record = build_protocol_transition(
                entity_type, entity_id, from_state, to_state, reason, details
            )
            entity = self._protocol_transition_entity(state, entity_type, entity_id)
            state_field = "status" if entity_type == "permission" else "state"
            current_state = current_states.get((entity_type, entity_id), entity.get(state_field))
            if from_state != current_state:
                raise ValueError(
                    f"stale protocol transition from_state: expected {current_state}"
                )
            transitions = state.setdefault("protocol_state_transitions", [])
            if any(item.get("transition_id") == record["transition_id"] for item in transitions):
                raise ValueError("duplicate protocol transition identity")
            self._flush_protocol_event_outbox_locked(state)
            event = EventRecord.create("protocol_state_transition_recorded", {
                key: record[key]
                for key in (
                    "transition_id", "entity_type", "entity_id", "from_state",
                    "to_state", "reason",
                )
            })
            return self._save_protocol_record(
                state, "protocol_state_transitions", record, event
            )

    def _validated_protocol_turn(
        self, state: dict[str, Any], session_id: str, turn_id: str
    ) -> dict[str, Any]:
        self._validate_protocol_identities(state)
        self._unique_protocol_record(
            state.setdefault("agent_sessions", []), "session_id", session_id,
            "duplicate agent session identity",
        )
        turn = self._unique_protocol_record(
            state.setdefault("protocol_turns", []), "turn_id", turn_id,
            "duplicate protocol turn identity",
        )
        if turn.get("session_id") != session_id:
            raise ValueError("protocol turn session mismatch")
        return turn

    def record_transport_update(
        self, session_id: str, turn_id: str, sequence: int, kind: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        with self._protocol_mutation_lock():
            state = self.load()
            self._validated_protocol_turn(state, session_id, turn_id)
            updates = state.setdefault("transport_updates", [])
            if any(
                isinstance(item, dict)
                and item.get("turn_id") == turn_id
                and item.get("sequence") == sequence
                for item in updates
            ):
                raise ValueError("duplicate transport update sequence")
            record = build_transport_update(session_id, turn_id, sequence, kind, payload)
            if any(item.get("update_id") == record["update_id"] for item in updates):
                raise ValueError("duplicate transport update identity")
            self._flush_protocol_event_outbox_locked(state)
            event = EventRecord.create("transport_update_recorded", {
                "update_id": record["update_id"],
                "session_id": record["session_id"],
                "turn_id": record["turn_id"],
                "sequence": record["sequence"],
                "kind": record["kind"],
            })
            return self._save_protocol_record(state, "transport_updates", record, event)

    def record_permission_request(
        self, session_id: str, turn_id: str, tool_name: str, target: str, risk: str
    ) -> dict[str, Any]:
        with self._protocol_mutation_lock():
            state = self.load()
            self._validated_protocol_turn(state, session_id, turn_id)
            record = build_permission_request(session_id, turn_id, tool_name, target, risk)
            permissions = state.setdefault("permission_requests", [])
            if any(item.get("permission_id") == record["permission_id"] for item in permissions):
                raise ValueError("duplicate permission request identity")
            self._flush_protocol_event_outbox_locked(state)
            event = EventRecord.create("permission_request_recorded", {
                "permission_id": record["permission_id"],
                "session_id": record["session_id"],
                "turn_id": record["turn_id"],
                "tool_name": record["tool_name"],
                "risk": record["risk"],
            })
            return self._save_protocol_record(state, "permission_requests", record, event)

    def _stage_acp_permission_pending_locked(
        self,
        state: dict[str, Any],
        session_id: str,
        turn_id: str,
        sequence: int,
        *,
        tool_name: str,
        target: str,
        risk: str,
        tool_call_id: str,
        flush_existing: bool = True,
    ) -> tuple[dict[str, dict[str, Any]], list[EventRecord]]:
        self._validated_protocol_turn(state, session_id, turn_id)
        current_states = self._validate_protocol_transition_history(state)
        turn = self._unique_protocol_record(
            state.setdefault("protocol_turns", []), "turn_id", turn_id,
            "duplicate protocol turn identity",
        )
        current = current_states.get(("turn", turn_id), turn["state"])
        if current not in {"submitted", "streaming"}:
            raise ValueError("ACP permission requires a submitted or streaming turn")
        permission = build_permission_request(
            session_id, turn_id, tool_name, target, risk
        )
        payload = {
            "permission_id": permission["permission_id"],
            "tool_call_id": tool_call_id,
            "risk": risk,
        }
        updates = state.setdefault("transport_updates", [])
        turn_updates = [
            item for item in updates
            if isinstance(item, dict) and item.get("turn_id") == turn_id
        ]
        if any(item.get("sequence") == sequence for item in turn_updates):
            raise ValueError("duplicate transport update sequence")
        if sequence != len(turn_updates):
            raise ValueError("ACP transport update sequence must be contiguous")
        existing_bytes = sum(
            len(json.dumps(item["payload"], ensure_ascii=False, sort_keys=True).encode("utf-8"))
            for item in turn_updates
        )
        payload_bytes = len(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        )
        if existing_bytes + payload_bytes + MAX_ACP_TERMINAL_UPDATE_BYTES > MAX_ACP_TURN_PAYLOAD_BYTES:
            raise ValueError(
                f"ACP turn payload exceeds {MAX_ACP_TURN_PAYLOAD_BYTES} bytes"
            )
        if len(turn_updates) + 2 > MAX_ACP_UPDATES_PER_TURN:
            raise ValueError(
                f"ACP turn updates exceed {MAX_ACP_UPDATES_PER_TURN}"
            )
        update = build_transport_update(
            session_id, turn_id, sequence, "permission_request", payload
        )
        transition = build_protocol_transition(
            "turn", turn_id, current, "waiting_permission", "permission_requested", {}
        )
        permissions = state.setdefault("permission_requests", [])
        if any(item.get("permission_id") == permission["permission_id"] for item in permissions):
            raise ValueError("duplicate permission request identity")
        events = [
            EventRecord.create("permission_request_recorded", {
                "permission_id": permission["permission_id"], "session_id": session_id,
                "turn_id": turn_id, "tool_name": tool_name, "risk": risk,
            }),
            EventRecord.create("transport_update_recorded", {
                "update_id": update["update_id"], "session_id": session_id,
                "turn_id": turn_id, "sequence": sequence, "kind": "permission_request",
            }),
            EventRecord.create("protocol_state_transition_recorded", {
                key: transition[key] for key in (
                    "transition_id", "entity_type", "entity_id", "from_state",
                    "to_state", "reason",
                )
            }),
        ]
        if flush_existing:
            self._flush_protocol_event_outbox_locked(state)
        permissions.append(permission)
        updates.append(update)
        state.setdefault("protocol_state_transitions", []).append(transition)
        state.setdefault("protocol_event_outbox", []).extend(asdict(event) for event in events)
        return ({"permission": permission, "update": update, "transition": transition}, events)

    def record_acp_permission_pending(
        self,
        session_id: str,
        turn_id: str,
        sequence: int,
        *,
        tool_name: str,
        target: str,
        risk: str,
        tool_call_id: str,
    ) -> dict[str, dict[str, Any]]:
        """Atomically reserve one bounded ACP permission request and its ledger facts."""
        with self._protocol_mutation_lock():
            state = self.load()
            result, events = self._stage_acp_permission_pending_locked(
                state, session_id, turn_id, sequence, tool_name=tool_name,
                target=target, risk=risk, tool_call_id=tool_call_id,
            )
            self.save(state)
            for event in events:
                try:
                    self.append_event(event)
                except OSError:
                    return result
            event_ids = {event.event_id for event in events}
            state["protocol_event_outbox"] = [
                item for item in state["protocol_event_outbox"]
                if item.get("event_id") not in event_ids
            ]
            try:
                self.save(state)
            except OSError:
                pass
            return result

    def record_mission_acp_permission_pending(
        self,
        *,
        attempt_id: str,
        session_id: str,
        turn_id: str,
        sequence: int,
        tool_name: str,
        target: str,
        risk: str,
        tool_call_id: str,
    ) -> dict[str, dict[str, Any]]:
        """Atomically bind one daemon ACP permission to its exact attempt."""
        from .daemon.recovery import validate_mission_permission_binding

        with self._protocol_mutation_lock():
            state = self.load()
            self._validated_recovery_snapshot_locked(state)
            attempts = [
                _validate_mission_attempt_record(item)
                for item in state.setdefault("mission_attempts", [])
            ]
            matches = [item for item in attempts if item["attempt_id"] == attempt_id]
            if len(matches) != 1:
                raise ValueError("mission permission attempt reference is invalid")
            attempt = matches[0]
            if (
                attempt["configured_transport"] != "acp"
                or attempt["state"] not in {"admitting", "submitted", "running"}
                or type(attempt["admission_claim_id"]) is not str
                or not attempt["admission_claim_id"]
            ):
                raise ValueError("mission permission requires an active claimed ACP attempt")
            missions = [
                item for item in state.setdefault("missions", [])
                if type(item) is dict and item.get("mission_id") == attempt["mission_id"]
            ]
            if len(missions) != 1:
                raise ValueError("mission permission Mission is invalid")
            snapshot = validate_execution_snapshot(missions[0].get("execution_snapshot"))
            if missions[0].get("snapshot_hash") != snapshot["execution_hash"]:
                raise ValueError("mission permission snapshot drift")
            workers = [
                item for item in snapshot["workers"]
                if item["agent_id"] == attempt["agent_id"]
            ]
            steps = [
                item for item in snapshot["mission"]["steps"]
                if item["step_id"] == attempt["step_id"]
                and item["agent_id"] == attempt["agent_id"]
            ]
            if (
                len(workers) != 1
                or len(steps) != 1
                or workers[0]["configured_transport"] != "acp"
                or workers[0]["provider"] == ""
                or snapshot["mission"]["project_scope_hash"]
                != canonical_snapshot_hash({"project_root": str(Path(self.root).resolve())})
            ):
                raise ValueError("mission permission frozen ACP scope is invalid")
            sessions = [
                _validate_agent_session_record(item)
                for item in state.setdefault("agent_sessions", [])
                if item.get("session_id") == session_id
            ]
            if len(sessions) != 1:
                raise ValueError("mission permission agent scope mismatch")
            session = sessions[0]
            caps = session["capabilities"]
            protocol_states = self._validate_protocol_transition_history(state)
            if (
                session["agent_id"] != attempt["agent_id"]
                or session["provider"] != workers[0]["provider"]
                or session["transport"] != "acp-adapter"
                or Path(session["workspace"]).resolve(strict=False)
                != Path(self.root).resolve()
                or session["native_session_id"] is None
                or any(
                    caps[name] is not True
                    for name in (
                        "structured_sessions", "streaming_updates",
                        "structured_tools", "permission_requests",
                    )
                )
                or caps["observable_terminal"] is not False
                or protocol_states.get(("session", session_id), session["state"])
                != "busy"
            ):
                raise ValueError("mission permission agent scope mismatch")
            turns = [
                item for item in state.setdefault("protocol_turns", [])
                if type(item) is dict and item.get("turn_id") == turn_id
            ]
            if (
                len(turns) != 1
                or turns[0].get("session_id") != session_id
                or turns[0].get("kind") != "prompt"
                or turns[0].get("message_id") != attempt["dispatch_key"]
                or protocol_states.get(("turn", turn_id), turns[0].get("state"))
                not in {"submitted", "streaming", "waiting_permission"}
            ):
                raise ValueError("mission permission turn lineage is invalid")
            bounded = (tool_name, target, risk, tool_call_id)
            if (
                tool_name not in {"read", "search", "edit", "delete", "move", "execute"}
                or any(type(value) is not str or not value.strip() for value in bounded)
                or len(tool_name.encode("utf-8")) > 64
                or len(target.encode("utf-8")) > 1024
                or len(risk.encode("utf-8")) > 64
                or len(tool_call_id.encode("utf-8")) > 256
            ):
                raise ValueError("mission ACP permission fields are invalid")
            bindings = state.setdefault("mission_permission_bindings", [])
            validated = [validate_mission_permission_binding(item) for item in bindings]
            existing_updates = [
                item for item in state.setdefault("transport_updates", [])
                if type(item) is dict
                and item.get("session_id") == session_id
                and item.get("turn_id") == turn_id
                and item.get("sequence") == sequence
            ]
            if existing_updates:
                if len(existing_updates) != 1:
                    raise ValueError("mission ACP permission retry conflict")
                update = existing_updates[0]
                payload = update.get("payload")
                permission_id = (
                    payload.get("permission_id") if type(payload) is dict else None
                )
                permissions = [
                    item for item in state.setdefault("permission_requests", [])
                    if type(item) is dict and item.get("permission_id") == permission_id
                ]
                retry_bindings = [
                    item for item in validated
                    if item["attempt_id"] == attempt_id
                    and item["permission_id"] == permission_id
                ]
                transitions = [
                    item for item in state.setdefault("protocol_state_transitions", [])
                    if type(item) is dict
                    and item.get("entity_type") == "turn"
                    and item.get("entity_id") == turn_id
                    and item.get("to_state") == "waiting_permission"
                    and item.get("reason") == "permission_requested"
                ]
                exact = (
                    update.get("kind") == "permission_request"
                    and type(payload) is dict
                    and payload.get("tool_call_id") == tool_call_id
                    and payload.get("risk") == risk
                    and len(permissions) == 1
                    and permissions[0].get("session_id") == session_id
                    and permissions[0].get("turn_id") == turn_id
                    and permissions[0].get("tool_name") == tool_name
                    and permissions[0].get("target") == target
                    and permissions[0].get("risk") == risk
                    and len(retry_bindings) == 1
                    and len(transitions) == 1
                )
                if not exact:
                    raise ValueError("mission ACP permission retry conflict")
                return {
                    "permission": copy.deepcopy(permissions[0]),
                    "update": copy.deepcopy(update),
                    "transition": copy.deepcopy(transitions[0]),
                    "binding": copy.deepcopy(retry_bindings[0]),
                }
            result, _events = self._stage_acp_permission_pending_locked(
                state, session_id, turn_id, sequence, tool_name=tool_name,
                target=target, risk=risk, tool_call_id=tool_call_id,
                flush_existing=False,
            )
            permission = result["permission"]
            binding = validate_mission_permission_binding(
                {
                    "mission_id": attempt["mission_id"],
                    "attempt_id": attempt_id,
                    "permission_id": permission["permission_id"],
                }
            )
            bindings.append(binding)
            self._append_recovery_audit_locked(
                state, "mission_permission_bound", dict(binding)
            )
            self._atomic_save(state)
            return {**result, "binding": binding}

    def authorize_mission_acp_effect(
        self, *, attempt_id: str, permission_id: str
    ) -> dict[str, object]:
        """Rebuild and audit all three effect gates at the ACP decision edge."""
        from .conversation.lifecycle import validate_conversation_history
        from .daemon.governance import authorize_effect

        tool_actions = {
            "read": "worker_task",
            "search": "worker_task",
            "edit": "worker_task",
            "delete": "worker_task",
            "move": "worker_task",
            "execute": "declared_local_verification",
        }
        with self._protocol_mutation_lock():
            state = self.load()
            self._validated_recovery_snapshot_locked(state)
            attempts = [
                _validate_mission_attempt_record(item)
                for item in state.setdefault("mission_attempts", [])
            ]
            attempt_matches = [
                item for item in attempts if item["attempt_id"] == attempt_id
            ]
            if len(attempt_matches) != 1:
                raise ValueError("ACP effect attempt is invalid")
            attempt = attempt_matches[0]
            missions = [
                item for item in state.setdefault("missions", [])
                if type(item) is dict and item.get("mission_id") == attempt["mission_id"]
            ]
            if len(missions) != 1:
                raise ValueError("ACP effect Mission is invalid")
            mission = missions[0]
            snapshot = validate_execution_snapshot(mission.get("execution_snapshot"))
            if mission.get("snapshot_hash") != snapshot["execution_hash"]:
                raise ValueError("ACP effect snapshot drift")
            step_matches = [
                item for item in snapshot["mission"]["steps"]
                if item["step_id"] == attempt["step_id"]
                and item["agent_id"] == attempt["agent_id"]
            ]
            worker_matches = [
                item for item in snapshot["workers"]
                if item["agent_id"] == attempt["agent_id"]
            ]
            if len(step_matches) != 1 or len(worker_matches) != 1:
                raise ValueError("ACP effect frozen Worker scope is invalid")
            worker = worker_matches[0]
            permission_matches = [
                _validate_permission_record(item)
                for item in state.setdefault("permission_requests", [])
                if type(item) is dict and item.get("permission_id") == permission_id
            ]
            bindings = [
                item for item in state.setdefault("mission_permission_bindings", [])
                if type(item) is dict
                and item.get("attempt_id") == attempt_id
                and item.get("permission_id") == permission_id
            ]
            if len(permission_matches) != 1 or len(bindings) != 1:
                raise ValueError("ACP effect permission lineage is invalid")
            permission = permission_matches[0]
            protocol_states = self._validate_protocol_transition_history(state)
            permission_state = protocol_states.get(
                ("permission", permission_id), permission["status"]
            )
            sessions = [
                _validate_agent_session_record(item)
                for item in state.setdefault("agent_sessions", [])
                if type(item) is dict and item.get("session_id") == permission["session_id"]
            ]
            if len(sessions) != 1:
                raise ValueError("ACP effect session lineage is invalid")
            session = sessions[0]
            turns = [
                item for item in state.setdefault("protocol_turns", [])
                if type(item) is dict and item.get("turn_id") == permission["turn_id"]
            ]
            if (
                len(turns) != 1
                or turns[0].get("session_id") != session["session_id"]
                or turns[0].get("kind") != "prompt"
                or turns[0].get("message_id") != attempt["dispatch_key"]
            ):
                raise ValueError("ACP effect turn lineage is invalid")
            turn = turns[0]
            ownership = validate_conversation_history(
                {
                    key: state.setdefault(key, [])
                    for key in (
                        "conversation_sessions", "conversation_turns",
                        "conversation_preview_bindings",
                    )
                },
                state.setdefault("conversation_state_transitions", []),
            )["ownership_states"].get(attempt["agent_id"], "agentdeck_owned")
            raw_target = permission["target"]
            target_without_line = re.sub(r":\d+$", "", raw_target)
            root = Path(self.root).resolve()
            normalized_target: str | None = None
            try:
                target_path = Path(target_without_line).expanduser()
                resolved = (
                    target_path.resolve(strict=False)
                    if target_path.is_absolute()
                    else (root / target_path).resolve(strict=False)
                )
                normalized_target = resolved.relative_to(root).as_posix()
                if not normalized_target or normalized_target.startswith(".agentdeck/"):
                    normalized_target = None
            except (OSError, ValueError):
                normalized_target = None
            action_class = tool_actions.get(permission["tool_name"])
            effect = {
                "mission_id": attempt["mission_id"],
                "step_id": attempt["step_id"],
                "agent_id": attempt["agent_id"],
                "action_class": action_class or "unsupported",
                "transport": attempt["configured_transport"],
                "target": normalized_target or "outside_frozen_scope",
            }
            frozen = {
                "mission_id": attempt["mission_id"],
                "steps": [{
                    "step_id": attempt["step_id"],
                    "agent_id": attempt["agent_id"],
                    "transport": worker["configured_transport"],
                }],
                "allowed_action_classes": snapshot["mission"]["action_classes"],
                "allowed_targets": (
                    [normalized_target] if normalized_target is not None else []
                ),
            }
            policy = {
                "allowed_action_classes": snapshot["mission"]["action_classes"],
                "permission_state": permission_state,
            }
            runtime = {
                "owner": ownership,
                "ready": (
                    attempt["state"] in {"admitting", "submitted", "running"}
                    and session["agent_id"] == attempt["agent_id"]
                    and session["transport"] == "acp-adapter"
                    and protocol_states.get(("session", session["session_id"]), session["state"])
                    == "busy"
                    and protocol_states.get(("turn", turn["turn_id"]), turn["state"])
                    == "waiting_permission"
                ),
                "effective_transport": attempt["configured_transport"],
            }
            effect_hash = canonical_snapshot_hash(effect)
            consumptions = state.setdefault("mission_acp_effect_consumptions", [])
            if type(consumptions) is not list or any(
                type(item) is not dict for item in consumptions
            ):
                raise ValueError("ACP effect consumption ledger is invalid")
            consumed = [
                item for item in consumptions
                if item.get("permission_id") == permission_id
            ]
            if consumed:
                if (
                    len(consumed) != 1
                    or consumed[0].get("attempt_id") != attempt_id
                    or consumed[0].get("tool_call_id")
                    != next(
                        (
                            item.get("payload", {}).get("tool_call_id")
                            for item in state.setdefault("transport_updates", [])
                            if type(item) is dict
                            and item.get("kind") == "permission_request"
                            and item.get("payload", {}).get("permission_id")
                            == permission_id
                        ),
                        None,
                    )
                    or consumed[0].get("effect_hash") != effect_hash
                    or consumed[0].get("state") != "consumed"
                ):
                    raise ValueError("ACP effect consumption lineage is invalid")
                return {
                    "allowed": False,
                    "gate": "permission_consumed",
                    "blocker": "ACP allow-once permission was already consumed",
                    "effect": effect,
                }
            decision = authorize_effect(
                effect, snapshot=frozen, policy=policy, runtime=runtime
            )
            if decision.allowed:
                updates = [
                    item for item in state.setdefault("transport_updates", [])
                    if type(item) is dict
                    and item.get("kind") == "permission_request"
                    and item.get("payload", {}).get("permission_id") == permission_id
                ]
                if len(updates) != 1:
                    raise ValueError("ACP effect tool-call lineage is invalid")
                tool_call_id = updates[0].get("payload", {}).get("tool_call_id")
                if type(tool_call_id) is not str or not tool_call_id:
                    raise ValueError("ACP effect tool-call lineage is invalid")
                consumptions.append(
                    {
                        "consumption_id": new_id("aec"),
                        "attempt_id": attempt_id,
                        "permission_id": permission_id,
                        "tool_call_id": tool_call_id,
                        "effect_hash": effect_hash,
                        "state": "consumed",
                        "created_at": utc_now(),
                    }
                )
            event_payload = {
                "attempt_id": attempt_id,
                "permission_id": permission_id,
                "allowed": decision.allowed,
                "gate": decision.gate,
                "blocker": decision.blocker,
                "effect_hash": effect_hash,
            }
            self._append_recovery_audit_locked(
                state, "mission_acp_effect_authorized", event_payload
            )
            self._atomic_save(state)
            return {
                "allowed": decision.allowed,
                "gate": decision.gate,
                "blocker": decision.blocker,
                "effect": effect,
            }

    def agent_session_by_id(self, session_id: str) -> dict[str, Any]:
        state = self.load()
        self._validate_protocol_identities(state)
        return self._unique_protocol_record(
            state.setdefault("agent_sessions", []), "session_id", session_id,
            "duplicate agent session identity",
        )

    def protocol_turn_by_id(self, turn_id: str) -> dict[str, Any]:
        state = self.load()
        self._validate_protocol_identities(state)
        return self._unique_protocol_record(
            state.setdefault("protocol_turns", []), "turn_id", turn_id,
            "duplicate protocol turn identity",
        )

    def validated_protocol_state(self) -> dict[str, Any]:
        """Load and globally validate all protocol identities and transition lineage."""
        state = self.load()
        self._validate_protocol_identities(state)
        self._validate_protocol_lineage(state)
        for item in state.setdefault("agent_sessions", []):
            _validate_agent_session_record(item)
        for item in state.setdefault("protocol_turns", []):
            _validate_protocol_turn_record(item)
        for item in state.setdefault("permission_requests", []):
            _validate_permission_record(item)
        self._validate_protocol_transition_history(state)
        return state

    def list_agent_sessions(self) -> list[dict[str, Any]]:
        return list(self.load().setdefault("agent_sessions", []))

    def list_protocol_turns(self) -> list[dict[str, Any]]:
        return list(self.load().setdefault("protocol_turns", []))

    def list_transport_updates(self) -> list[dict[str, Any]]:
        return list(self.load().setdefault("transport_updates", []))

    def list_permission_requests(self) -> list[dict[str, Any]]:
        return list(self.load().setdefault("permission_requests", []))

    def create_mission(
        self,
        *,
        user_message: str,
        can_start: bool,
        blockers: list[str],
        provider: str,
        model: str,
        leader_backend: dict[str, Any],
        plan_id: str,
        plan_hash: str,
        selected_agents: list[dict[str, Any]],
        startup_actions: list[dict[str, Any]],
        step_count: int,
        timeout_seconds: int,
        retry_limit: int = 0,
    ) -> dict[str, Any]:
        state = self.load()
        if not all(
            isinstance(value, str) and value
            for value in (user_message, provider, model, plan_id, plan_hash)
        ):
            raise ValueError("mission identity fields must be non-empty strings")
        if not isinstance(can_start, bool):
            raise ValueError("can_start must be a boolean")
        compact_blockers, invalid_blockers = compact_mission_blockers(blockers)
        if invalid_blockers:
            raise ValueError("blockers must be a list of strings")
        if can_start and compact_blockers:
            raise ValueError("can_start requires empty blockers")
        if not isinstance(step_count, int) or isinstance(step_count, bool) or step_count < 1:
            raise ValueError("step_count must be a positive integer")
        if (
            not isinstance(timeout_seconds, int)
            or isinstance(timeout_seconds, bool)
            or timeout_seconds < 1
        ):
            raise ValueError("timeout_seconds must be a positive integer")
        if type(retry_limit) is not int or retry_limit < 0:
            raise ValueError("retry_limit must be a non-negative integer")
        expected_backend = leader_backend_identity(provider, model)
        if leader_backend != expected_backend:
            raise ValueError("leader_backend must match provider and model")
        compact_agents, invalid_agents = compact_mission_worker_entries(
            selected_agents, kind="selected_agents"
        )
        compact_actions, invalid_actions = compact_mission_worker_entries(
            startup_actions, kind="startup_actions"
        )
        if invalid_agents or invalid_actions:
            raise ValueError("mission worker summaries must use compact domain fields")
        selected_ids = [item["agent_id"] for item in compact_agents]
        action_ids = [item["agent_id"] for item in compact_actions]
        if can_start and (
            len(compact_agents) < 2
            or len(compact_actions) < 2
            or selected_ids != action_ids
        ):
            raise ValueError("startable mission requires matching worker and startup summaries")
        now = utc_now()
        record = {
            "mission_id": new_id("mis"),
            "schema_version": MISSION_SCHEMA_VERSION,
            "user_message": user_message,
            "status": MISSION_STATUSES[0],
            "stop_reason": None,
            "can_start": can_start,
            "blockers": compact_blockers,
            "provider": provider,
            "model": model,
            "leader_backend": expected_backend,
            "plan_id": plan_id,
            "plan_hash": plan_hash,
            "selected_agents": compact_agents,
            "startup_actions": compact_actions,
            "step_count": step_count,
            "timeout_seconds": timeout_seconds,
            "retry_limit": retry_limit,
            "workflow_run_id": None,
            "current_step": 0,
            "confirmed_at": None,
            "execution_snapshot": None,
            "snapshot_hash": None,
            "execution_authority_hash": None,
            "completed_at": None,
            "created_at": now,
            "updated_at": now,
        }
        state.setdefault("missions", []).append(record)
        self.save(state)
        return record

    def build_mission_record(
        self,
        *,
        user_message: str,
        can_start: bool,
        blockers: list[str],
        provider: str,
        model: str,
        leader_backend: dict[str, Any],
        plan_id: str,
        plan_hash: str,
        selected_agents: list[dict[str, Any]],
        startup_actions: list[dict[str, Any]],
        step_count: int,
        timeout_seconds: int,
        retry_limit: int = 0,
        semantic_plan: object | None = None,
    ) -> dict[str, Any]:
        if not all(
            isinstance(value, str) and value
            for value in (user_message, provider, model, plan_id, plan_hash)
        ):
            raise ValueError("mission identity fields must be non-empty strings")
        if not isinstance(can_start, bool):
            raise ValueError("can_start must be a boolean")
        compact_blockers, invalid_blockers = compact_mission_blockers(blockers)
        if invalid_blockers:
            raise ValueError("blockers must be a list of strings")
        if can_start and compact_blockers:
            raise ValueError("can_start requires empty blockers")
        if not isinstance(step_count, int) or isinstance(step_count, bool) or step_count < 1:
            raise ValueError("step_count must be a positive integer")
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds < 1:
            raise ValueError("timeout_seconds must be a positive integer")
        if type(retry_limit) is not int or retry_limit < 0:
            raise ValueError("retry_limit must be a non-negative integer")
        expected_backend = leader_backend_identity(provider, model)
        if leader_backend != expected_backend:
            raise ValueError("leader_backend must match provider and model")
        compact_agents, invalid_agents = compact_mission_worker_entries(
            selected_agents, kind="selected_agents"
        )
        compact_actions, invalid_actions = compact_mission_worker_entries(
            startup_actions, kind="startup_actions"
        )
        if invalid_agents or invalid_actions:
            raise ValueError("mission worker summaries must use compact domain fields")
        selected_ids = [item["agent_id"] for item in compact_agents]
        action_ids = [item["agent_id"] for item in compact_actions]
        if can_start and (
            len(compact_agents) < 2
            or len(compact_actions) < 2
            or selected_ids != action_ids
        ):
            raise ValueError("startable mission requires matching worker and startup summaries")
        semantic_fields: dict[str, object] = {}
        if semantic_plan is not None:
            validated_semantic_plan = validated_compiled_semantic_plan(semantic_plan)
            authority = validated_semantic_plan["semantic_authority"]
            semantic_fields = {
                "semantic_authority_schema_version": authority["schema_version"],
                "semantic_authority_hash": semantic_authority_hash(authority),
                "compiled_task_hashes": [
                    "sha256:"
                    + hashlib.sha256(step["task"].encode("utf-8")).hexdigest()
                    for step in validated_semantic_plan["steps"]
                ],
                "preview_generation": 1,
            }
        now = utc_now()
        return {
            "mission_id": new_id("mis"),
            "schema_version": MISSION_SCHEMA_VERSION,
            "user_message": user_message,
            "status": MISSION_STATUSES[0],
            "stop_reason": None,
            "can_start": can_start,
            "blockers": compact_blockers,
            "provider": provider,
            "model": model,
            "leader_backend": expected_backend,
            "plan_id": plan_id,
            "plan_hash": plan_hash,
            "selected_agents": compact_agents,
            "startup_actions": compact_actions,
            "step_count": step_count,
            "timeout_seconds": timeout_seconds,
            "retry_limit": retry_limit,
            "workflow_run_id": None,
            "current_step": 0,
            "confirmed_at": None,
            "execution_snapshot": None,
            "snapshot_hash": None,
            "execution_authority_hash": None,
            "completed_at": None,
            "created_at": now,
            "updated_at": now,
            **semantic_fields,
        }

    def mission_by_id(self, mission_id: str) -> dict[str, Any]:
        for item in self.load().get("missions", []):
            if isinstance(item, dict) and item.get("mission_id") == mission_id:
                return item
        raise KeyError(mission_id)

    def list_missions(self) -> list[dict[str, Any]]:
        return list(self.load().get("missions", []))

    @staticmethod
    def _unique_mission_record(
        state: dict[str, Any], mission_id: str
    ) -> dict[str, Any]:
        matches = [
            item
            for item in state.setdefault("missions", [])
            if isinstance(item, dict) and item.get("mission_id") == mission_id
        ]
        if not matches:
            raise KeyError(mission_id)
        if len(matches) != 1:
            raise ValueError("duplicate mission identity")
        return matches[0]

    @staticmethod
    def _unique_plan_record(state: dict[str, Any], plan_id: str) -> dict[str, Any]:
        matches = [
            item
            for item in state.setdefault("plans", [])
            if isinstance(item, dict) and item.get("plan_id") == plan_id
        ]
        if not matches:
            raise KeyError(plan_id)
        if len(matches) != 1:
            raise ValueError("duplicate plan identity")
        return matches[0]

    def freeze_mission_execution(
        self,
        mission_id: str,
        *,
        confirmed_at: str,
    ) -> dict[str, Any]:
        if not is_canonical_mission_id(mission_id):
            raise ValueError("mission identity invalid")
        if type(confirmed_at) is not str or not confirmed_at:
            raise ValueError("confirmation timestamp invalid")
        try:
            parsed = datetime.fromisoformat(confirmed_at)
        except ValueError:
            raise ValueError("confirmation timestamp invalid") from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("confirmation timestamp invalid")
        with self._protocol_mutation_lock():
            state = self.load()
            mission = self._unique_mission_record(state, mission_id)
            plan_id = str(mission.get("plan_id") or "")
            plan = self._unique_plan_record(state, plan_id)
            created_at = mission.get("created_at")
            if type(created_at) is not str:
                raise ValueError("confirmation timestamp invalid")
            try:
                created_time = datetime.fromisoformat(created_at)
            except ValueError:
                raise ValueError("confirmation timestamp invalid") from None
            if (
                created_time.tzinfo is None
                or created_time.utcoffset() is None
                or parsed < created_time
            ):
                raise ValueError("confirmation timestamp invalid")
            if (
                mission.get("status") != "pending_confirmation"
                or mission.get("can_start") is not True
                or mission.get("blockers") != []
                or mission.get("confirmed_at") is not None
                or mission.get("execution_snapshot") is not None
                or mission.get("snapshot_hash") is not None
            ):
                raise ValueError("mission is not confirmable")
            try:
                config = load_config(self.root)
                snapshot = build_execution_snapshot_authority(
                    config,
                    mission,
                    plan,
                    execution_policy_snapshot(config),
                    memory_provenance=collect_execution_memory_provenance(
                        Path(config.root)
                    ),
                )
            except MissionStateError:
                raise
            except (OSError, TypeError, ValueError):
                raise MissionStateError("execution authority drift") from None
            authority_hash = mission.get("execution_authority_hash")
            if (
                type(authority_hash) is not str
                or re.fullmatch(r"sha256:[0-9a-f]{64}", authority_hash) is None
                or snapshot["execution_hash"] != authority_hash
            ):
                raise MissionStateError("execution authority drift")
            event = EventRecord.create(
                "mission_execution_frozen",
                {
                    "mission_id": mission_id,
                    "snapshot_hash": snapshot["execution_hash"],
                },
            )
            semantic_frozen_event: EventRecord | None = None
            plan_body = plan.get("plan")
            semantic_active, plan_is_semantic, present_semantic = (
                semantic_mission_provenance_shape(plan_body, mission)
            )
            if semantic_active:
                if (
                    not plan_is_semantic
                    or present_semantic != SEMANTIC_MISSION_COMPACT_FIELDS
                ):
                    raise MissionStateError("execution authority drift")
                validated_semantic = validated_compiled_semantic_plan(
                    plan_body
                )
                expected_authority_hash = semantic_authority_hash(
                    validated_semantic["semantic_authority"]
                )
                expected_task_hashes = [
                    "sha256:"
                    + hashlib.sha256(step["task"].encode("utf-8")).hexdigest()
                    for step in validated_semantic["steps"]
                ]
                if (
                    mission.get("semantic_authority_schema_version")
                    != validated_semantic["semantic_authority"]["schema_version"]
                    or mission.get("semantic_authority_hash")
                    != expected_authority_hash
                    or mission.get("compiled_task_hashes")
                    != expected_task_hashes
                    or mission.get("preview_generation") != 1
                ):
                    raise MissionStateError("execution authority drift")
                semantic_frozen_event = EventRecord.create(
                    "mission_semantic_authority_frozen",
                    {
                        "mission_id": mission_id,
                        "plan_id": plan_id,
                        "semantic_authority_hash": expected_authority_hash,
                        "compiled_task_hashes": expected_task_hashes,
                        "compiled_step_count": len(expected_task_hashes),
                        "policy_snapshot_hash": snapshot["policy_hash"],
                        "preview_generation": 1,
                    },
                )
            mission.update(
                {
                    "status": "preparing",
                    "can_start": False,
                    "confirmed_at": confirmed_at,
                    "updated_at": confirmed_at,
                    "execution_snapshot": copy.deepcopy(snapshot),
                    "snapshot_hash": snapshot["execution_hash"],
                }
            )
            from .daemon.recovery import validate_mission_recovery_evidence_record

            recovery_evidence = state.setdefault("mission_recovery_evidence", [])
            if type(recovery_evidence) is not list:
                raise TypeError("mission_recovery_evidence must be a list")
            validated_evidence = [
                validate_mission_recovery_evidence_record(item)
                for item in recovery_evidence
            ]
            if any(item["mission_id"] == mission_id for item in validated_evidence):
                raise ValueError("duplicate durable recovery evidence")
            recovery_evidence.append(
                validate_mission_recovery_evidence_record(
                    {
                        "mission_id": mission_id,
                        "attempt_id": None,
                        "agent_id": None,
                    }
                )
            )
            outbox = state.setdefault("protocol_event_outbox", [])
            outbox.append(asdict(event))
            if semantic_frozen_event is not None:
                outbox.append(asdict(semantic_frozen_event))
            self._atomic_save(state)
            return copy.deepcopy(mission)

    @staticmethod
    def _mission_admission_record(
        mission_id: str,
        snapshot_hash: str,
        *,
        state: str,
        blocker: str | None,
    ) -> dict[str, Any]:
        if state not in {"confirmed_not_admitted", "admitted"}:
            raise ValueError("Mission admission state invalid")
        if state == "admitted" and blocker is not None:
            raise ValueError("Mission admission blocker invalid")
        if state == "confirmed_not_admitted" and (
            type(blocker) is not str or not blocker
        ):
            raise ValueError("Mission admission blocker invalid")
        return {
            "state": state,
            "snapshot_hash": snapshot_hash,
            "blocker": blocker,
            "recovery_command": (
                f"agentdeck mission run --mission-id {mission_id} --confirm"
                if state == "confirmed_not_admitted"
                else None
            ),
            "updated_at": utc_now(),
        }

    def record_mission_not_admitted(
        self, mission_id: str, *, snapshot_hash: str, blocker: str
    ) -> dict[str, Any]:
        with self._protocol_mutation_lock():
            state = self.load()
            mission = self._unique_mission_record(state, mission_id)
            if (
                mission.get("status") != "preparing"
                or mission.get("snapshot_hash") != snapshot_hash
                or validate_execution_snapshot(mission.get("execution_snapshot"))[
                    "execution_hash"
                ]
                != snapshot_hash
            ):
                raise ValueError("Mission admission drift")
            current = mission.get("daemon_admission")
            if isinstance(current, dict) and current.get("state") == "admitted":
                return copy.deepcopy(current)
            if (
                isinstance(current, dict)
                and current.get("state") == "confirmed_not_admitted"
                and current.get("snapshot_hash") == snapshot_hash
                and current.get("blocker") == blocker
            ):
                return copy.deepcopy(current)
            record = self._mission_admission_record(
                mission_id,
                snapshot_hash,
                state="confirmed_not_admitted",
                blocker=blocker,
            )
            mission["daemon_admission"] = record
            self._append_recovery_audit_locked(
                state,
                "mission_admission_deferred",
                {"mission_id": mission_id, "snapshot_hash": snapshot_hash},
            )
            self._atomic_save(state)
            return copy.deepcopy(record)

    def admit_mission_execution(
        self, mission_id: str, *, snapshot_hash: str
    ) -> dict[str, Any]:
        from .daemon.recovery import validate_mission_recovery_evidence_record

        with self._protocol_mutation_lock():
            state = self.load()
            mission = self._unique_mission_record(state, mission_id)
            try:
                snapshot = validate_execution_snapshot(
                    mission.get("execution_snapshot")
                )
            except ValueError:
                raise ValueError("Mission admission drift") from None
            bindings = [
                validate_mission_recovery_evidence_record(item)
                for item in state.setdefault("mission_recovery_evidence", [])
                if isinstance(item, dict) and item.get("mission_id") == mission_id
            ]
            if (
                mission.get("status") != "preparing"
                or mission.get("confirmed_at") is None
                or mission.get("snapshot_hash") != snapshot_hash
                or snapshot["execution_hash"] != snapshot_hash
                or len(bindings) != 1
                or bindings[0] != {
                    "mission_id": mission_id,
                    "attempt_id": None,
                    "agent_id": None,
                }
            ):
                raise ValueError("Mission admission drift")
            current = mission.get("daemon_admission")
            if (
                isinstance(current, dict)
                and current.get("state") == "admitted"
                and current.get("snapshot_hash") == snapshot_hash
            ):
                return copy.deepcopy(current)
            record = self._mission_admission_record(
                mission_id, snapshot_hash, state="admitted", blocker=None
            )
            mission["daemon_admission"] = record
            self._append_recovery_audit_locked(
                state,
                "mission_admitted",
                {"mission_id": mission_id, "snapshot_hash": snapshot_hash},
            )
            self._atomic_save(state)
            return copy.deepcopy(record)

    def claim_mission_worker_start(
        self, *, mission_id: str, step_id: str, agent_id: str
    ) -> dict[str, Any]:
        """Persist exact tmux startup intent before any runtime effect."""
        with self._protocol_mutation_lock():
            state = self.load()
            mission = self._unique_mission_record(state, mission_id)
            snapshot = validate_execution_snapshot(mission.get("execution_snapshot"))
            admission = mission.get("daemon_admission")
            if (
                mission.get("status") not in {"preparing", "running"}
                or type(admission) is not dict
                or admission.get("state") != "admitted"
                or admission.get("snapshot_hash") != snapshot["execution_hash"]
                or mission.get("snapshot_hash") != snapshot["execution_hash"]
            ):
                raise ValueError("Worker startup Mission authority is invalid")
            steps = snapshot["mission"]["steps"]
            cursor = mission.get("current_step")
            if type(cursor) is not int or cursor < 0 or cursor >= len(steps):
                raise ValueError("Worker startup step authority is invalid")
            step = steps[cursor]
            workers = [
                item for item in snapshot["workers"]
                if item["agent_id"] == agent_id
            ]
            actions = [
                item for item in mission.get("startup_actions", [])
                if type(item) is dict and item.get("agent_id") == agent_id
            ]
            if (
                step.get("step_id") != step_id
                or step.get("agent_id") != agent_id
                or len(workers) != 1
                or workers[0]["configured_transport"] != "tmux"
                or len(actions) != 1
                or actions[0].get("action") != "spawn"
            ):
                raise ValueError("Worker startup frozen scope is invalid")
            config = load_config(self.root)
            agents = [item for item in config.agents if item.agent_id == agent_id]
            if (
                len(agents) != 1
                or agents[0].transport != "tmux"
                or any(
                    workers[0].get(field) != getattr(agents[0], field)
                    for field in ("provider", "role", "workspace_mode")
                )
                or worker_runtime_identity_hash(agents[0], config.runtime)
                != workers[0]["runtime_identity_hash"]
            ):
                raise ValueError("Worker startup runtime identity drift")
            starts = state.setdefault("mission_worker_starts", [])
            if type(starts) is not list or any(type(item) is not dict for item in starts):
                raise ValueError("Worker startup ledger is invalid")
            if any(
                item.get("mission_id") == mission_id
                and item.get("step_id") == step_id
                for item in starts
            ):
                raise ValueError("Worker startup already recorded")
            now = utc_now()
            record = {
                "start_id": new_id("wst"),
                "mission_id": mission_id,
                "step_id": step_id,
                "agent_id": agent_id,
                "runtime_identity_hash": workers[0]["runtime_identity_hash"],
                "state": "claimed",
                "pane_id": None,
                "blocker": None,
                "created_at": now,
                "updated_at": now,
            }
            starts.append(record)
            state.setdefault("daemon_event_outbox", []).append(
                asdict(EventRecord.create("mission_worker_start_claimed", {
                    "start_id": record["start_id"], "mission_id": mission_id,
                    "step_id": step_id, "agent_id": agent_id,
                    "runtime_identity_hash": record["runtime_identity_hash"],
                }))
            )
            self._atomic_save(state)
            return copy.deepcopy(record)

    def finish_mission_worker_start(
        self, *, start_id: str, pane_id: str, session_name: str, cwd: str,
        blocker: str | None = None,
    ) -> dict[str, Any]:
        """Persist the sole terminal receipt for a claimed tmux startup."""
        with self._protocol_mutation_lock():
            state = self.load()
            starts = state.setdefault("mission_worker_starts", [])
            matches = [item for item in starts if type(item) is dict and item.get("start_id") == start_id]
            if len(matches) != 1 or matches[0].get("state") != "claimed":
                raise ValueError("Worker startup claim is invalid")
            record = matches[0]
            if blocker is None:
                if type(pane_id) is not str or not pane_id:
                    raise ValueError("Worker startup pane is invalid")
                config = load_config(self.root)
                agents = [
                    item for item in config.agents
                    if item.agent_id == record.get("agent_id")
                ]
                if (
                    len(agents) != 1
                    or worker_runtime_identity_hash(agents[0], config.runtime)
                    != record.get("runtime_identity_hash")
                    or session_name != config.runtime.session_name
                    or cwd != config.root
                ):
                    raise ValueError("Worker startup receipt authority drift")
                record.update({"state": "started", "pane_id": pane_id, "blocker": None})
                state.setdefault("agents", {})[record["agent_id"]] = asdict(
                    AgentRuntimeBinding(record["agent_id"], pane_id, session_name, cwd, "running")
                )
                event_type = "agent_spawned"
                payload = {
                    "mission_id": record["mission_id"], "agent_id": record["agent_id"],
                    "pane_id": pane_id, "session_name": session_name, "cwd": cwd,
                }
            else:
                if type(blocker) is not str or not blocker:
                    raise ValueError("Worker startup blocker is invalid")
                record.update({"state": "blocked", "pane_id": None, "blocker": blocker})
                event_type = "mission_worker_start_blocked"
                payload = {
                    "start_id": start_id, "mission_id": record["mission_id"],
                    "step_id": record["step_id"], "agent_id": record["agent_id"],
                    "reason": blocker,
                }
            record["updated_at"] = utc_now()
            state.setdefault("daemon_event_outbox", []).append(
                asdict(EventRecord.create(event_type, payload))
            )
            self._atomic_save(state)
            return copy.deepcopy(record)

    def prepare_mission_attempt(
        self,
        *,
        mission_id: str,
        step_id: str,
        agent_id: str,
        configured_transport: str,
    ) -> dict[str, Any]:
        if type(step_id) is not str or not re.fullmatch(r"step_[1-9][0-9]*", step_id):
            raise ValueError("mission step identity invalid")
        if type(agent_id) is not str or not agent_id:
            raise ValueError("mission step agent invalid")
        if configured_transport not in {"acp", "tmux"}:
            raise ValueError("mission step transport invalid")
        with self._protocol_mutation_lock():
            state = self.load()
            mission = self._unique_mission_record(state, mission_id)
            status = mission.get("status")
            if status in {"completed", "stopped", "interrupted"}:
                raise ValueError("terminal mission step")
            if (
                status not in {"preparing", "running"}
                or not mission.get("confirmed_at")
                or mission.get("execution_snapshot") is None
            ):
                raise ValueError("mission execution is not confirmed")
            confirmed_at = mission.get("confirmed_at")
            created_at = mission.get("created_at")
            if type(confirmed_at) is not str or type(created_at) is not str:
                raise ValueError("mission confirmation state invalid")
            try:
                confirmed_time = datetime.fromisoformat(confirmed_at)
                created_time = datetime.fromisoformat(created_at)
            except ValueError:
                raise ValueError("mission confirmation state invalid") from None
            if (
                confirmed_time.tzinfo is None
                or confirmed_time.utcoffset() is None
                or created_time.tzinfo is None
                or created_time.utcoffset() is None
                or confirmed_time < created_time
            ):
                raise ValueError("mission confirmation state invalid")
            try:
                persisted_snapshot = validate_execution_snapshot(
                    mission["execution_snapshot"]
                )
            except ValueError:
                raise ValueError("frozen execution snapshot invalid") from None
            if mission.get("snapshot_hash") != persisted_snapshot["execution_hash"]:
                raise ValueError("frozen execution drift")
            if (
                mission.get("execution_authority_hash")
                != persisted_snapshot["execution_hash"]
            ):
                raise ValueError("frozen execution drift")
            if (
                persisted_snapshot["mission"].get("plan_hash")
                != mission.get("plan_hash")
            ):
                raise MissionStateError("plan hash drift")
            plan = self._unique_plan_record(state, str(mission.get("plan_id") or ""))
            try:
                config = load_config(self.root)
                snapshot = build_execution_snapshot_authority(
                    config,
                    mission,
                    plan,
                    execution_policy_snapshot(config),
                    memory_provenance=collect_execution_memory_provenance(
                        Path(config.root)
                    ),
                )
            except MissionStateError:
                raise
            except (OSError, TypeError, ValueError):
                raise ValueError("frozen execution drift") from None
            if snapshot != persisted_snapshot:
                raise ValueError("frozen execution drift")
            steps = snapshot["mission"].get("steps")
            if type(steps) is not list:
                raise ValueError("frozen execution snapshot invalid")
            matches = [item for item in steps if type(item) is dict and item.get("step_id") == step_id]
            if len(matches) != 1:
                raise ValueError("unknown mission step")
            step = matches[0]
            position = step.get("position")
            current_step = mission.get("current_step")
            if type(position) is not int or type(current_step) is not int:
                raise ValueError("frozen execution snapshot invalid")
            if position <= current_step:
                raise ValueError("terminal mission step")
            if position != current_step + 1:
                raise ValueError("mission step lineage drift")
            if step.get("agent_id") != agent_id:
                raise ValueError("mission step agent drift")
            workers = snapshot["workers"]
            worker = next(
                (
                    item
                    for item in workers
                    if type(item) is dict and item.get("agent_id") == agent_id
                ),
                None,
            )
            if worker is None:
                raise ValueError("mission step agent drift")
            from .daemon.governance import (
                GovernanceError,
                effective_transport_for_step,
            )
            try:
                expected_transport = effective_transport_for_step(
                    state,
                    mission_id=mission_id,
                    step_id=step_id,
                    agent_id=agent_id,
                    frozen_transport=worker.get("configured_transport"),
                )
            except GovernanceError:
                raise ValueError("mission step transport drift") from None
            if expected_transport != configured_transport:
                raise ValueError("mission step transport drift")
            attempts = state.setdefault("mission_attempts", [])
            if type(attempts) is not list:
                raise ValueError("mission attempt state invalid")
            validated_attempts = [
                _validate_mission_attempt_record(item) for item in attempts
            ]
            attempt_ids = [item["attempt_id"] for item in validated_attempts]
            if len(attempt_ids) != len(set(attempt_ids)):
                raise ValueError("duplicate mission attempt identity")
            dispatch_keys = [item["dispatch_key"] for item in validated_attempts]
            if len(dispatch_keys) != len(set(dispatch_keys)):
                raise ValueError("duplicate mission dispatch key")
            matching = [
                item
                for item in validated_attempts
                if item["mission_id"] == mission_id and item["step_id"] == step_id
            ]
            for ordinal, prior in enumerate(matching, start=1):
                expected_key = derive_attempt_dispatch_key(
                    mission_id,
                    step_id,
                    agent_id,
                    configured_transport,
                    persisted_snapshot["execution_hash"],
                    attempt_ordinal=ordinal,
                )
                if (
                    prior["snapshot_hash"] != persisted_snapshot["execution_hash"]
                    or prior["agent_id"] != step["agent_id"]
                    or prior["configured_transport"]
                    != worker["configured_transport"]
                    or prior["dispatch_key"] != expected_key
                ):
                    raise ValueError("mission attempt lineage drift")
            from .daemon.recovery import validate_mission_recovery_evidence_record

            recovery_evidence = state.setdefault("mission_recovery_evidence", [])
            if type(recovery_evidence) is not list:
                raise ValueError("durable recovery authority invalid")
            try:
                validated_recovery_evidence = [
                    validate_mission_recovery_evidence_record(item)
                    for item in recovery_evidence
                ]
                mission_ids = {
                    item["mission_id"]
                    for item in state.setdefault("missions", [])
                    if type(item) is dict
                    and is_canonical_mission_id(item.get("mission_id"))
                }
                attempt_by_id = {
                    item["attempt_id"]: item for item in validated_attempts
                }
                evidence_mission_ids = [
                    item["mission_id"] for item in validated_recovery_evidence
                ]
                if len(evidence_mission_ids) != len(set(evidence_mission_ids)):
                    raise ValueError
                for binding in validated_recovery_evidence:
                    if binding["mission_id"] not in mission_ids:
                        raise ValueError
                    bound_attempt_id = binding["attempt_id"]
                    if bound_attempt_id is not None:
                        bound_attempt = attempt_by_id.get(bound_attempt_id)
                        if (
                            bound_attempt is None
                            or bound_attempt["mission_id"] != binding["mission_id"]
                            or bound_attempt["agent_id"] != binding["agent_id"]
                        ):
                            raise ValueError
                target_bindings = [
                    item
                    for item in validated_recovery_evidence
                    if item["mission_id"] == mission_id
                ]
                mission_attempts = [
                    item
                    for item in validated_attempts
                    if item["mission_id"] == mission_id
                ]
                latest_attempt = max(
                    mission_attempts,
                    key=lambda item: (item["created_at"], item["attempt_id"]),
                    default=None,
                )
                expected_attempt_id = (
                    None if latest_attempt is None else latest_attempt["attempt_id"]
                )
                expected_agent_id = (
                    None if latest_attempt is None else latest_attempt["agent_id"]
                )
                if len(target_bindings) != 1 or target_bindings[0] != {
                    "mission_id": mission_id,
                    "attempt_id": expected_attempt_id,
                    "agent_id": expected_agent_id,
                }:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError("durable recovery authority invalid") from None
            if any(item["state"] in _MISSION_ATTEMPT_ACTIVE_STATES for item in matching):
                raise ValueError("active attempt already exists")
            if any(
                item["state"] not in _MISSION_ATTEMPT_RETRYABLE_STATES
                for item in matching
            ):
                raise ValueError("terminal mission attempt")
            retry_limit = persisted_snapshot["limits"]["retry_limit"]
            if len(matching) >= 1 + retry_limit:
                raise ValueError("mission retry budget exhausted")
            attempt_ordinal = len(matching) + 1
            dispatch_key = derive_attempt_dispatch_key(
                mission_id,
                step_id,
                agent_id,
                configured_transport,
                persisted_snapshot["execution_hash"],
                attempt_ordinal=attempt_ordinal,
            )
            if any(item["dispatch_key"] == dispatch_key for item in validated_attempts):
                raise ValueError("duplicate mission dispatch key")
            try:
                self._validated_recovery_snapshot_locked(state)
            except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                message = str(exc)
                if message in {
                    "duplicate protocol event identity",
                    "protocol event journal is malformed",
                    "protocol event outbox is invalid",
                }:
                    raise ValueError(message) from None
                raise ValueError("durable recovery authority invalid") from None
            now = utc_now()
            attempt = {
                "attempt_id": new_id("mat"),
                "mission_id": mission_id,
                "step_id": step_id,
                "agent_id": agent_id,
                "configured_transport": configured_transport,
                "dispatch_key": dispatch_key,
                "admission_claim_id": None,
                "snapshot_hash": snapshot["execution_hash"],
                "state": "prepared",
                "created_at": now,
                "updated_at": now,
                "receipt_summary": None,
                "blocker": None,
                "terminal_reason": None,
            }
            if "semantic_step_hash" in step:
                attempt["semantic_step_hash"] = step["semantic_step_hash"]
            attempt = _validate_mission_attempt_record(attempt)
            if attempt["attempt_id"] in set(attempt_ids):
                raise ValueError("duplicate mission attempt identity")
            attempt_time = datetime.fromisoformat(attempt["created_at"])
            if attempt_time < confirmed_time:
                raise ValueError("mission attempt state invalid")
            event = EventRecord(
                event_id=new_id("evt"),
                event_type="mission_attempt_prepared",
                created_at=attempt["created_at"],
                payload={
                    "attempt_id": attempt["attempt_id"],
                    "mission_id": mission_id,
                    "step_id": step_id,
                    "dispatch_key": attempt["dispatch_key"],
                    **(
                        {"semantic_step_hash": attempt["semantic_step_hash"]}
                        if "semantic_step_hash" in attempt
                        else {}
                    ),
                },
            )
            event_summary = asdict(event)
            try:
                candidate_event_id = validate_daemon_event_record(event_summary)
            except LeaseError:
                raise ValueError("protocol event record is invalid") from None
            outbox = state.setdefault("protocol_event_outbox", [])
            outbox_ids = _validated_protocol_event_outbox_ids(outbox)
            journal_ids = self._strict_protocol_journal_event_ids()
            if candidate_event_id in outbox_ids or candidate_event_id in journal_ids:
                raise ValueError("duplicate protocol event identity")
            next_recovery_evidence = [
                item
                for item in validated_recovery_evidence
                if item["mission_id"] != mission_id
            ]
            next_recovery_evidence.append(
                validate_mission_recovery_evidence_record(
                    {
                        "mission_id": mission_id,
                        "attempt_id": attempt["attempt_id"],
                        "agent_id": agent_id,
                    }
                )
            )
            attempts.append(attempt)
            state["mission_recovery_evidence"] = next_recovery_evidence
            outbox.append(event_summary)
            self._atomic_save(state)
            return copy.deepcopy(attempt)

    def mission_attempt_by_id(self, attempt_id: str) -> dict[str, Any]:
        if type(attempt_id) is not str or re.fullmatch(r"mat_[0-9a-f]{12}", attempt_id) is None:
            raise ValueError("mission attempt identity invalid")
        attempts = self.load().get("mission_attempts", [])
        if type(attempts) is not list:
            raise ValueError("mission attempt state invalid")
        validated = [_validate_mission_attempt_record(item) for item in attempts]
        matches = [item for item in validated if item["attempt_id"] == attempt_id]
        if len(matches) != 1:
            if not matches:
                raise KeyError(attempt_id)
            raise ValueError("duplicate mission attempt identity")
        return matches[0]

    def record_semantic_worker_dispatch_event(
        self,
        event_type: str,
        *,
        mission_id: str,
        attempt_id: str,
        step_id: str,
        agent_id: str,
        task_hash: str | None = None,
        semantic_step_hash: str | None = None,
        code: str | None = None,
    ) -> dict[str, Any]:
        """Append one idempotent compact semantic dispatch audit fact."""
        if event_type not in {
            "worker_task_compiled",
            "semantic_authority_drift_detected",
        }:
            raise ValueError("semantic dispatch event is invalid")
        if (
            not is_canonical_mission_id(mission_id)
            or type(attempt_id) is not str
            or re.fullmatch(r"mat_[0-9a-f]{12}", attempt_id) is None
            or type(step_id) is not str
            or re.fullmatch(r"step_[1-9][0-9]*", step_id) is None
            or type(agent_id) is not str
            or not agent_id
        ):
            raise ValueError("semantic dispatch event is invalid")
        hash_pattern = r"sha256:[0-9a-f]{64}"
        if event_type == "worker_task_compiled":
            if (
                type(task_hash) is not str
                or re.fullmatch(hash_pattern, task_hash) is None
                or type(semantic_step_hash) is not str
                or re.fullmatch(hash_pattern, semantic_step_hash) is None
                or code is not None
            ):
                raise ValueError("semantic dispatch event is invalid")
            payload = {
                "mission_id": mission_id,
                "attempt_id": attempt_id,
                "step_id": step_id,
                "agent_id": agent_id,
                "task_hash": task_hash,
                "semantic_step_hash": semantic_step_hash,
            }
        else:
            if (
                code != "semantic_compilation_drift"
                or task_hash is not None
                or semantic_step_hash is not None
            ):
                raise ValueError("semantic dispatch event is invalid")
            payload = {
                "mission_id": mission_id,
                "attempt_id": attempt_id,
                "step_id": step_id,
                "agent_id": agent_id,
                "code": code,
            }
        with self._protocol_mutation_lock():
            state = self.load()
            attempts = state.get("mission_attempts", [])
            matches = [
                item for item in attempts
                if type(item) is dict and item.get("attempt_id") == attempt_id
            ] if type(attempts) is list else []
            if (
                len(matches) != 1
                or matches[0].get("mission_id") != mission_id
                or matches[0].get("step_id") != step_id
                or matches[0].get("agent_id") != agent_id
            ):
                raise ValueError("semantic dispatch event authority is invalid")
            events_by_id: dict[str, dict[str, Any]] = {}
            for item in [
                *self.all_events(),
                *state.setdefault("protocol_event_outbox", []),
            ]:
                if type(item) is not dict or type(item.get("event_id")) is not str:
                    continue
                existing = events_by_id.get(item["event_id"])
                if existing is not None and existing != item:
                    raise ValueError("semantic dispatch event conflicts")
                events_by_id[item["event_id"]] = item
            known = [
                item for item in events_by_id.values()
                if item.get("event_type") == event_type
                and type(item.get("payload")) is dict
                and item["payload"].get("attempt_id") == attempt_id
            ]
            if known:
                if all(item.get("payload") == payload for item in known):
                    return copy.deepcopy(known[0])
                raise ValueError("semantic dispatch event conflicts")
            event = asdict(EventRecord.create(event_type, payload))
            validate_daemon_event_record(event)
            state["protocol_event_outbox"].append(event)
            self._atomic_save(state)
            return copy.deepcopy(event)

    def _validated_recovery_snapshot_locked(
        self, state: dict[str, Any]
    ) -> str:
        from .daemon.recovery import (
            reconcile_gate,
            recovery_facts_from_persisted_state,
            validate_mission_handoff_evidence_record,
            validate_mission_permission_binding,
            validate_mission_recovery_evidence_record,
            validate_mission_reply_evidence_record,
            validate_recovery_record,
        )

        missions = state.setdefault("missions", [])
        attempts_raw = state.setdefault("mission_attempts", [])
        evidence_raw = state.setdefault("mission_recovery_evidence", [])
        replies_raw = state.setdefault("mission_worker_replies", [])
        handoffs_raw = state.setdefault("mission_handoffs", [])
        permission_bindings_raw = state.setdefault("mission_permission_bindings", [])
        decisions_raw = state.setdefault("recovery_decisions", [])
        outbox = state.setdefault("protocol_event_outbox", [])
        daemon_outbox = state.setdefault("daemon_event_outbox", [])
        conversation_outbox = state.setdefault("conversation_event_outbox", [])
        if type(missions) is not list:
            raise ValueError("persisted Mission evidence is invalid")
        if type(attempts_raw) is not list:
            raise ValueError("mission attempt state invalid")
        if type(evidence_raw) is not list:
            raise TypeError("mission_recovery_evidence must be a list")
        if type(replies_raw) is not list:
            raise TypeError("mission_worker_replies must be a list")
        if type(handoffs_raw) is not list:
            raise TypeError("mission_handoffs must be a list")
        if type(permission_bindings_raw) is not list:
            raise TypeError("mission_permission_bindings must be a list")
        if type(decisions_raw) is not list:
            raise TypeError("recovery_decisions must be a list")
        mission_ids: list[str] = []
        for raw in missions:
            if (
                type(raw) is not dict
                or not is_canonical_mission_id(raw.get("mission_id"))
                or raw.get("status") not in MISSION_STATUSES
            ):
                raise ValueError("persisted Mission evidence is invalid")
            mission_ids.append(raw["mission_id"])
        if len(mission_ids) != len(set(mission_ids)):
            raise ValueError("duplicate mission identity")
        self._strict_protocol_journal_event_ids()
        journal_events = self.all_events()
        _validated_protocol_event_outbox_ids(outbox)
        frozen_authority_events: dict[str, dict[str, object]] = {}
        for event in [*journal_events, *outbox]:
            try:
                validate_daemon_event_record(event)
            except LeaseError:
                raise ValueError("protocol event journal is malformed") from None
            event_id = str(event["event_id"])
            existing = frozen_authority_events.get(event_id)
            if existing is not None:
                if existing != event:
                    raise ValueError("frozen Mission event authority is invalid")
                continue
            frozen_authority_events[event_id] = event
        semantic_frozen_events: dict[str, dict[str, object]] = {}
        execution_frozen_events: dict[str, dict[str, object]] = {}
        for event in frozen_authority_events.values():
            event_type = event.get("event_type")
            if event_type == "mission_semantic_authority_frozen":
                payload = _validate_semantic_frozen_event_payload(
                    event.get("payload")
                )
                target = semantic_frozen_events
            elif event_type == "mission_execution_frozen":
                payload = _validate_execution_frozen_event_payload(
                    event.get("payload")
                )
                target = execution_frozen_events
            else:
                continue
            mission_id = str(payload["mission_id"])
            if mission_id not in set(mission_ids) or mission_id in target:
                raise ValueError("frozen Mission event authority is invalid")
            target[mission_id] = payload
        semantic_recovery_config: ProjectConfig | None = None
        for mission in missions:
            plan_id = mission.get("plan_id")
            plans = state.get("plans")
            plan_matches = (
                [
                    item
                    for item in plans
                    if type(item) is dict and item.get("plan_id") == plan_id
                ]
                if type(plan_id) is str and type(plans) is list
                else []
            )
            plan_body = (
                plan_matches[0].get("plan") if len(plan_matches) == 1 else None
            )
            plan_is_semantic = type(plan_body) is dict and (
                "semantic_authority" in plan_body
                or "semantic_steps" in plan_body
            )
            execution_snapshot = mission.get("execution_snapshot")
            snapshot_mission = (
                execution_snapshot.get("mission")
                if type(execution_snapshot) is dict
                else None
            )
            present_semantic = frozenset(
                field
                for field in SEMANTIC_MISSION_COMPACT_FIELDS
                if field in mission
            )
            mission_id = str(mission["mission_id"])
            semantic_anchor = semantic_frozen_events.get(mission_id)
            if (
                mission["status"] not in {"preparing", "running"}
                or (
                    not present_semantic
                    and not plan_is_semantic
                    and semantic_anchor is None
                    and not (
                        type(snapshot_mission) is dict
                        and "semantic_authority_schema_version" in snapshot_mission
                    )
                )
            ):
                continue
            try:
                if semantic_anchor is None:
                    raise ValueError
                execution_anchor = execution_frozen_events.get(mission_id)
                if execution_anchor is None:
                    raise ValueError
                if type(plan_id) is not str:
                    raise ValueError
                plan = self._unique_plan_record(state, plan_id)
                persisted_snapshot = validate_execution_snapshot(
                    mission.get("execution_snapshot")
                )
                if semantic_recovery_config is None:
                    semantic_recovery_config = load_config(self.root)
                current_snapshot = build_execution_snapshot_authority(
                    semantic_recovery_config,
                    mission,
                    plan,
                    execution_policy_snapshot(semantic_recovery_config),
                    memory_provenance=collect_execution_memory_provenance(
                        Path(semantic_recovery_config.root)
                    ),
                )
            except Exception:
                raise ValueError("semantic recovery authority drift") from None
            if (
                mission.get("snapshot_hash") != persisted_snapshot["execution_hash"]
                or mission.get("execution_authority_hash")
                != persisted_snapshot["execution_hash"]
                or current_snapshot != persisted_snapshot
                or semantic_anchor.get("plan_id") != plan_id
                or semantic_anchor.get("semantic_authority_hash")
                != mission.get("semantic_authority_hash")
                or semantic_anchor.get("compiled_task_hashes")
                != mission.get("compiled_task_hashes")
                or semantic_anchor.get("compiled_step_count")
                != len(persisted_snapshot["mission"]["steps"])
                or semantic_anchor.get("policy_snapshot_hash")
                != current_snapshot["policy_hash"]
                or semantic_anchor.get("preview_generation")
                != mission.get("preview_generation")
                or execution_anchor.get("snapshot_hash")
                != current_snapshot["execution_hash"]
            ):
                raise ValueError("semantic recovery authority drift")
        attempts = [_validate_mission_attempt_record(item) for item in attempts_raw]
        _require_unique_mission_attempt_lineage(attempts)
        attempt_by_id = {item["attempt_id"]: item for item in attempts}
        if any(item["mission_id"] not in set(mission_ids) for item in attempts):
            raise ValueError("mission attempt reference is invalid")
        evidence = [
            validate_mission_recovery_evidence_record(item) for item in evidence_raw
        ]
        evidence_missions = [item["mission_id"] for item in evidence]
        if len(evidence_missions) != len(set(evidence_missions)):
            raise ValueError("duplicate durable recovery evidence")
        for item in evidence:
            if item["mission_id"] not in set(mission_ids):
                raise ValueError("durable recovery Mission reference is invalid")
            attempt_id = item["attempt_id"]
            if attempt_id is not None:
                attempt = attempt_by_id.get(attempt_id)
                if (
                    attempt is None
                    or attempt["mission_id"] != item["mission_id"]
                    or attempt["agent_id"] != item["agent_id"]
                ):
                    raise ValueError("durable recovery attempt reference is invalid")
        replies = [validate_mission_reply_evidence_record(item) for item in replies_raw]
        handoffs = [
            validate_mission_handoff_evidence_record(item) for item in handoffs_raw
        ]
        permission_bindings = [
            validate_mission_permission_binding(item)
            for item in permission_bindings_raw
        ]
        for collection, label in ((replies, "reply"), (handoffs, "handoff")):
            identities = [item["attempt_id"] for item in collection]
            if len(identities) != len(set(identities)):
                raise ValueError(f"duplicate durable recovery {label} evidence")
            for item in collection:
                attempt = attempt_by_id.get(item["attempt_id"])
                if attempt is None or attempt["mission_id"] != item["mission_id"]:
                    raise ValueError(f"durable recovery {label} reference is invalid")
        permission_ids = [item["permission_id"] for item in permission_bindings]
        if len(permission_ids) != len(set(permission_ids)):
            raise ValueError("duplicate durable recovery permission evidence")
        for item in permission_bindings:
            attempt = attempt_by_id.get(item["attempt_id"])
            if attempt is None or attempt["mission_id"] != item["mission_id"]:
                raise ValueError("durable recovery permission reference is invalid")
        for item in decisions_raw:
            validate_recovery_record(item)
        self._validate_protocol_identities(state)
        self._validate_protocol_lineage(state)
        agent_sessions = [
            _validate_agent_session_record(item)
            for item in state.setdefault("agent_sessions", [])
        ]
        protocol_turns = [
            _validate_protocol_turn_record(item)
            for item in state.setdefault("protocol_turns", [])
        ]
        permission_requests = [
            _validate_permission_record(item)
            for item in state.setdefault("permission_requests", [])
        ]
        self._validate_protocol_transition_history(state)
        _validated_protocol_event_outbox_ids(outbox)
        recovery_audit_events = [
            event
            for event in [*journal_events, *outbox]
            if event.get("event_type")
            in {
                "mission_reply_evidence_recorded",
                "mission_handoff_evidence_recorded",
                "mission_permission_bound",
            }
        ]

        def require_audit_history(
            *,
            event_type: str,
            identity_key: str,
            record: dict[str, object],
            states: list[str],
        ) -> None:
            matching = [
                event
                for event in recovery_audit_events
                if event.get("event_type") == event_type
                and type(event.get("payload")) is dict
                and event["payload"].get(identity_key) == record[identity_key]
                and event["payload"].get("mission_id") == record["mission_id"]
                and event["payload"].get("attempt_id") == record["attempt_id"]
            ]
            observed = [event["payload"].get("state") for event in matching]
            if observed != states:
                raise ValueError("durable recovery evidence audit history is invalid")
            if any(
                event["payload"].get("semantic_step_hash")
                != record.get("semantic_step_hash")
                for event in matching
            ):
                raise ValueError("durable recovery evidence audit history is invalid")
            if "canonical_handoff" in record:
                expected_hash = canonical_snapshot_hash(record["canonical_handoff"])
                if any(
                    event["payload"].get("canonical_handoff_hash") != expected_hash
                    for event in matching
                ):
                    raise ValueError("durable recovery evidence audit history is invalid")

        for reply in replies:
            expected_states = ["received"]
            if reply["state"] in {"validated", "invalid"}:
                expected_states.append(reply["state"])
            require_audit_history(
                event_type="mission_reply_evidence_recorded",
                identity_key="reply_id",
                record=reply,
                states=expected_states,
            )
        for handoff in handoffs:
            expected_states = ["pending"]
            if handoff["state"] == "recorded":
                expected_states.append("recorded")
            require_audit_history(
                event_type="mission_handoff_evidence_recorded",
                identity_key="handoff_id",
                record=handoff,
                states=expected_states,
            )
        for binding in permission_bindings:
            matching = [
                event
                for event in recovery_audit_events
                if event.get("event_type") == "mission_permission_bound"
                and event.get("payload") == binding
            ]
            if len(matching) != 1:
                raise ValueError("durable recovery evidence audit history is invalid")
        validate_daemon_event_outbox(daemon_outbox)
        self._conversation_collections(state)
        claim_lineage = self._protocol_admission_claim_history(outbox, attempts)
        for mission_id in mission_ids:
            mission = next(item for item in missions if item["mission_id"] == mission_id)
            if mission["status"] not in {"completed", "stopped", "interrupted"}:
                recovery_facts_from_persisted_state(state, mission_id)
                continue
            has_terminal_lineage = any(
                item["mission_id"] == mission_id
                for collection in (
                    attempts,
                    evidence,
                    replies,
                    handoffs,
                    permission_bindings,
                )
                for item in collection
            )
            if has_terminal_lineage:
                terminal_facts = recovery_facts_from_persisted_state(
                    state, mission_id
                )
                terminal_decision = reconcile_gate(terminal_facts)
                force_stop_ambiguity = _is_force_stop_terminal_ambiguity(
                    mission,
                    attempts,
                    terminal_facts,
                    terminal_decision.classification,
                )
                if (
                    terminal_decision.classification != "terminal"
                    and not force_stop_ambiguity
                ):
                    raise ValueError("terminal Mission recovery integrity is invalid")
        authority = {
            "missions": missions,
            "mission_attempts": attempts,
            "mission_recovery_evidence": evidence,
            "mission_worker_replies": replies,
            "mission_handoffs": handoffs,
            "mission_permission_bindings": permission_bindings,
            "permission_requests": permission_requests,
            "agent_sessions": agent_sessions,
            "protocol_turns": protocol_turns,
            "protocol_state_transitions": state.setdefault(
                "protocol_state_transitions", []
            ),
            "conversation_sessions": state.setdefault("conversation_sessions", []),
            "conversation_turns": state.setdefault("conversation_turns", []),
            "conversation_preview_bindings": state.setdefault(
                "conversation_preview_bindings", []
            ),
            "conversation_state_transitions": state.setdefault(
                "conversation_state_transitions", []
            ),
            "recovery_audit_events": recovery_audit_events,
            "recovery_decisions": decisions_raw,
            "protocol_event_outbox": outbox,
            "daemon_event_outbox": daemon_outbox,
            "conversation_event_outbox": conversation_outbox,
            "claim_lineage": {
                key: list(value) for key, value in sorted(claim_lineage.items())
            },
        }
        # transport_updates are lineage-validated above but intentionally absent:
        # no recovery fact or decision is derived from streamed transport updates.
        return _recovery_authority_hash(authority)

    def load_recovery_snapshot(self) -> tuple[dict[str, Any], str]:
        """Read and validate one lock-consistent recovery authority snapshot."""
        with self._protocol_mutation_lock():
            state = self.load()
            token = self._validated_recovery_snapshot_locked(state)
            return copy.deepcopy(state), token

    def record_live_permission_recovery(
        self,
        *,
        authority: Mapping[str, object],
        daemon_instance_id: str,
    ) -> dict[str, object]:
        """Project one live in-process waiter without weakening startup recovery."""
        from .daemon.recovery import (
            RecoveryDecision,
            reconcile_gate,
            recovery_facts_from_persisted_state,
            validate_mission_permission_binding,
            validate_recovery_record,
        )

        if (
            type(authority) is not dict
            or set(authority) != {
                "permission_id",
                "attempt_id",
                "session_id",
                "generation",
            }
            or type(authority.get("permission_id")) is not str
            or re.fullmatch(r"prm_[a-z0-9]+", str(authority["permission_id"]))
            is None
            or type(authority.get("attempt_id")) is not str
            or re.fullmatch(r"mat_[0-9a-f]{12}", str(authority["attempt_id"]))
            is None
            or type(authority.get("session_id")) is not str
            or not authority["session_id"]
            or authority.get("generation") != 1
            or type(daemon_instance_id) is not str
            or not daemon_instance_id
        ):
            raise ValueError("live permission recovery authority is invalid")
        with self._protocol_mutation_lock():
            state = self.load()
            runtime = state.get("daemon_runtime")
            if (
                type(runtime) is not dict
                or runtime.get("instance_id") != daemon_instance_id
            ):
                raise ValueError("live permission recovery daemon authority drift")
            permission_id = str(authority["permission_id"])
            attempt_id = str(authority["attempt_id"])
            session_id = str(authority["session_id"])
            permissions = [
                item
                for item in state.get("permission_requests", [])
                if type(item) is dict and item.get("permission_id") == permission_id
            ]
            bindings = [
                validate_mission_permission_binding(item)
                for item in state.get("mission_permission_bindings", [])
                if type(item) is dict
                and item.get("permission_id") == permission_id
                and item.get("attempt_id") == attempt_id
            ]
            attempts = [
                _validate_mission_attempt_record(item)
                for item in state.get("mission_attempts", [])
                if type(item) is dict and item.get("attempt_id") == attempt_id
            ]
            sessions = [
                _validate_agent_session_record(item)
                for item in state.get("agent_sessions", [])
                if type(item) is dict and item.get("session_id") == session_id
            ]
            if len(permissions) != 1 or len(bindings) != 1 or len(attempts) != 1:
                raise ValueError("live permission recovery lineage is invalid")
            permission = _validate_permission_record(permissions[0])
            attempt = attempts[0]
            if (
                permission["session_id"] != session_id
                or bindings[0]["mission_id"] != attempt["mission_id"]
                or attempt["configured_transport"] != "acp"
                or attempt["state"] not in {"submitted", "running"}
                or len(sessions) != 1
                or sessions[0]["agent_id"] != attempt["agent_id"]
            ):
                raise ValueError("live permission recovery lineage is invalid")
            protocol_states = self._validate_protocol_transition_history(state)
            if (
                protocol_states.get(
                    ("permission", permission_id), permission["status"]
                )
                != "pending"
            ):
                raise ValueError("live permission recovery is not pending")
            facts = recovery_facts_from_persisted_state(
                state, str(attempt["mission_id"])
            )
            persisted_only = reconcile_gate(facts)
            if (
                facts.attempt_id != attempt_id
                or facts.permission_state != "pending"
                or persisted_only.classification != "ambiguous"
                or persisted_only.reason
                != "ACP Worker connection was lost across daemon restart"
            ):
                raise ValueError("live permission recovery state is invalid")
            live = RecoveryDecision(
                classification="waiting_human",
                reason="Worker permission is pending",
                mission_id=str(attempt["mission_id"]),
                attempt_id=attempt_id,
                next_transition=None,
            )
            now = utc_now()
            record = validate_recovery_record(
                {
                    "classification": live.classification,
                    "reason": live.reason,
                    "mission_id": live.mission_id,
                    "attempt_id": live.attempt_id,
                    "next_transition": live.next_transition,
                    "classified_at": now,
                }
            )
            decisions = state.setdefault("recovery_decisions", [])
            if type(decisions) is not list:
                raise ValueError("live permission recovery decisions are invalid")
            validated = [validate_recovery_record(item) for item in decisions]
            state["recovery_decisions"] = [
                item for item in validated if item["mission_id"] != live.mission_id
            ] + [record]
            self._append_recovery_audit_locked(
                state,
                "live_permission_recovery_projected",
                {
                    "mission_id": live.mission_id,
                    "attempt_id": attempt_id,
                    "permission_id": permission_id,
                    "daemon_instance_id": daemon_instance_id,
                },
            )
            self._atomic_save(state)
            return copy.deepcopy(record)

    def commit_recovery_decisions(
        self,
        decisions: list[object] | tuple[object, ...],
        *,
        expected_recovery_token: str,
    ) -> list[dict[str, object]]:
        """Atomically persist complete startup classifications and audit events."""
        from .daemon.recovery import (
            RecoveryDecision,
            reconcile_gate,
            recovery_facts_from_persisted_state,
            validate_recovery_record,
        )

        if (
            type(decisions) not in {list, tuple}
            or type(expected_recovery_token) is not str
            or re.fullmatch(r"sha256:[0-9a-f]{64}", expected_recovery_token) is None
        ):
            raise ValueError("recovery decisions are invalid")
        if any(not isinstance(item, RecoveryDecision) for item in decisions):
            raise ValueError("recovery decisions are invalid")
        mission_ids = [item.mission_id for item in decisions]
        if len(mission_ids) != len(set(mission_ids)):
            raise ValueError("duplicate recovery Mission identity")

        with self._protocol_mutation_lock():
            state = self.load()
            current_recovery_token = self._validated_recovery_snapshot_locked(state)
            if current_recovery_token != expected_recovery_token:
                raise ValueError("recovery authority drift")
            nonterminal_mission_ids = [
                item["mission_id"]
                for item in state.get("missions", [])
                if item.get("status") not in {"completed", "stopped", "interrupted"}
            ]
            if set(mission_ids) != set(nonterminal_mission_ids):
                raise ValueError("recovery decision evidence drift")
            expected_decisions = {
                mission_id: reconcile_gate(
                    recovery_facts_from_persisted_state(state, mission_id)
                )
                for mission_id in nonterminal_mission_ids
            }
            if any(expected_decisions[item.mission_id] != item for item in decisions):
                raise ValueError("recovery decision evidence drift")
            existing = state.setdefault("recovery_decisions", [])
            if type(existing) is not list:
                raise TypeError("recovery_decisions must be a list")
            for item in existing:
                validate_recovery_record(item)

            attempts_raw = state.setdefault("mission_attempts", [])
            if type(attempts_raw) is not list:
                raise ValueError("mission attempt state invalid")
            attempts = [_validate_mission_attempt_record(item) for item in attempts_raw]
            _require_unique_mission_attempt_lineage(attempts)
            outbox = state.setdefault("protocol_event_outbox", [])
            outbox_ids = _validated_protocol_event_outbox_ids(outbox)
            self._protocol_admission_claim_history(outbox, attempts)
            journal_ids = self._strict_protocol_journal_event_ids()

            missions = state.setdefault("missions", [])
            if type(missions) is not list:
                raise ValueError("persisted Mission evidence is invalid")
            persisted_missions: dict[str, dict[str, Any]] = {}
            for raw in missions:
                if type(raw) is not dict:
                    raise ValueError("persisted Mission evidence is invalid")
                mission_id = raw.get("mission_id")
                status = raw.get("status")
                if (
                    not is_canonical_mission_id(mission_id)
                    or status not in MISSION_STATUSES
                    or mission_id in persisted_missions
                ):
                    raise ValueError("persisted Mission evidence is invalid")
                persisted_missions[mission_id] = raw

            records: list[dict[str, object]] = []
            events: list[dict[str, Any]] = []
            candidate_event_ids: set[str] = set()
            for decision in decisions:
                mission = persisted_missions.get(decision.mission_id)
                if mission is None or mission.get("status") in {
                    "completed",
                    "stopped",
                    "interrupted",
                }:
                    raise ValueError("recovery Mission is not active")
                now = utc_now()
                record = validate_recovery_record(
                    {
                        "classification": decision.classification,
                        "reason": decision.reason,
                        "mission_id": decision.mission_id,
                        "attempt_id": decision.attempt_id,
                        "next_transition": decision.next_transition,
                        "classified_at": now,
                    }
                )
                event = asdict(
                    EventRecord(
                        event_id=new_id("evt"),
                        event_type="mission_recovery_classified",
                        created_at=now,
                        payload={
                            "attempt_id": decision.attempt_id,
                            "classification": decision.classification,
                            "mission_id": decision.mission_id,
                            "next_transition": decision.next_transition,
                            "reason": decision.reason,
                        },
                    )
                )
                try:
                    event_id = validate_daemon_event_record(event)
                except LeaseError:
                    raise ValueError("protocol event record is invalid") from None
                if (
                    event_id in outbox_ids
                    or event_id in journal_ids
                    or event_id in candidate_event_ids
                ):
                    raise ValueError("duplicate protocol event identity")
                candidate_event_ids.add(event_id)
                records.append(record)
                events.append(event)

            state["recovery_decisions"] = records
            outbox.extend(events)
            self._atomic_save(state)
            return copy.deepcopy(records)

    def _append_recovery_audit_locked(
        self, state: dict[str, Any], event_type: str, payload: dict[str, Any]
    ) -> None:
        event = asdict(EventRecord.create(event_type, payload))
        try:
            event_id = validate_daemon_event_record(event)
        except LeaseError:
            raise ValueError("protocol event record is invalid") from None
        outbox = state.setdefault("protocol_event_outbox", [])
        outbox_ids = _validated_protocol_event_outbox_ids(outbox)
        if event_id in outbox_ids or event_id in self._strict_protocol_journal_event_ids():
            raise ValueError("duplicate protocol event identity")
        outbox.append(event)

    def record_mission_reply_evidence(
        self,
        *,
        attempt_id: str,
        dispatch_key: str,
        state: str,
        expected_reply_id: str | None = None,
        canonical_handoff: object | None = None,
    ) -> dict[str, object]:
        from .daemon.recovery import validate_mission_reply_evidence_record

        if state not in {"received", "validated", "invalid"}:
            raise ValueError("mission reply evidence transition is invalid")
        with self._protocol_mutation_lock():
            persisted = self.load()
            self._validated_recovery_snapshot_locked(persisted)
            attempts = [
                _validate_mission_attempt_record(item)
                for item in persisted.setdefault("mission_attempts", [])
            ]
            matches = [item for item in attempts if item["attempt_id"] == attempt_id]
            if len(matches) != 1:
                raise ValueError("mission reply attempt reference is invalid")
            attempt = matches[0]
            if (
                attempt["dispatch_key"] != dispatch_key
                or attempt["state"] not in {"completed", "succeeded"}
            ):
                raise ValueError("mission reply attempt authority is invalid")
            records = persisted.setdefault("mission_worker_replies", [])
            validated = [validate_mission_reply_evidence_record(item) for item in records]
            current = [item for item in validated if item["attempt_id"] == attempt_id]
            if not current:
                if (
                    state != "received"
                    or expected_reply_id is not None
                    or canonical_handoff is None
                ):
                    raise ValueError("mission reply evidence transition is invalid")
                candidate = validate_mission_reply_evidence_record(
                    {
                        "mission_id": attempt["mission_id"],
                        "attempt_id": attempt_id,
                        "reply_id": new_id("mrp"),
                        "dispatch_key": dispatch_key,
                        "state": "received",
                        "canonical_handoff": canonical_handoff,
                        **(
                            {"semantic_step_hash": attempt["semantic_step_hash"]}
                            if "semantic_step_hash" in attempt
                            else {}
                        ),
                    }
                )
                records.append(candidate)
            elif len(current) == 1:
                prior = current[0]
                if (
                    expected_reply_id != prior["reply_id"]
                    or prior["dispatch_key"] != dispatch_key
                    or prior["state"] != "received"
                    or state not in {"validated", "invalid"}
                    or canonical_handoff is not None
                ):
                    raise ValueError("mission reply evidence transition is invalid")
                candidate = validate_mission_reply_evidence_record(
                    {**prior, "state": state}
                )
                index = next(
                    index
                    for index, item in enumerate(records)
                    if item.get("attempt_id") == attempt_id
                )
                records[index] = candidate
            else:
                raise ValueError("duplicate durable recovery reply evidence")
            self._append_recovery_audit_locked(
                persisted,
                "mission_reply_evidence_recorded",
                {
                    "attempt_id": attempt_id,
                    "mission_id": candidate["mission_id"],
                    "reply_id": candidate["reply_id"],
                    "state": candidate["state"],
                    "canonical_handoff_hash": canonical_snapshot_hash(
                        candidate["canonical_handoff"]
                    ),
                    **(
                        {"semantic_step_hash": candidate["semantic_step_hash"]}
                        if "semantic_step_hash" in candidate
                        else {}
                    ),
                },
            )
            self._atomic_save(persisted)
            return copy.deepcopy(candidate)

    def record_mission_handoff_evidence(
        self,
        *,
        attempt_id: str,
        reply_id: str,
        state: str,
        expected_handoff_id: str | None = None,
    ) -> dict[str, object]:
        from .daemon.recovery import (
            validate_mission_handoff_evidence_record,
            validate_mission_reply_evidence_record,
        )

        if state not in {"pending", "recorded"}:
            raise ValueError("mission handoff evidence transition is invalid")
        with self._protocol_mutation_lock():
            persisted = self.load()
            self._validated_recovery_snapshot_locked(persisted)
            replies = [
                validate_mission_reply_evidence_record(item)
                for item in persisted.setdefault("mission_worker_replies", [])
            ]
            reply_matches = [
                item
                for item in replies
                if item["attempt_id"] == attempt_id and item["reply_id"] == reply_id
            ]
            if len(reply_matches) != 1 or reply_matches[0]["state"] != "validated":
                raise ValueError("mission handoff reply authority is invalid")
            reply = reply_matches[0]
            records = persisted.setdefault("mission_handoffs", [])
            validated = [
                validate_mission_handoff_evidence_record(item) for item in records
            ]
            current = [item for item in validated if item["attempt_id"] == attempt_id]
            if not current:
                if state != "pending" or expected_handoff_id is not None:
                    raise ValueError("mission handoff evidence transition is invalid")
                candidate = validate_mission_handoff_evidence_record(
                    {
                        "mission_id": reply["mission_id"],
                        "attempt_id": attempt_id,
                        "handoff_id": new_id("hof"),
                        "reply_id": reply_id,
                        "state": "pending",
                        "canonical_handoff": reply["canonical_handoff"],
                        **(
                            {"semantic_step_hash": reply["semantic_step_hash"]}
                            if "semantic_step_hash" in reply
                            else {}
                        ),
                    }
                )
                records.append(candidate)
            elif len(current) == 1:
                prior = current[0]
                if (
                    expected_handoff_id != prior["handoff_id"]
                    or prior["reply_id"] != reply_id
                    or prior["state"] != "pending"
                    or state != "recorded"
                ):
                    raise ValueError("mission handoff evidence transition is invalid")
                candidate = validate_mission_handoff_evidence_record(
                    {**prior, "state": "recorded"}
                )
                if candidate["canonical_handoff"] != reply["canonical_handoff"]:
                    raise ValueError("mission handoff evidence content drift")
                index = next(
                    index
                    for index, item in enumerate(records)
                    if item.get("attempt_id") == attempt_id
                )
                records[index] = candidate
            else:
                raise ValueError("duplicate durable recovery handoff evidence")
            self._append_recovery_audit_locked(
                persisted,
                "mission_handoff_evidence_recorded",
                {
                    "attempt_id": attempt_id,
                    "handoff_id": candidate["handoff_id"],
                    "mission_id": candidate["mission_id"],
                    "reply_id": reply_id,
                    "state": candidate["state"],
                    "canonical_handoff_hash": canonical_snapshot_hash(
                        candidate["canonical_handoff"]
                    ),
                    **(
                        {"semantic_step_hash": candidate["semantic_step_hash"]}
                        if "semantic_step_hash" in candidate
                        else {}
                    ),
                },
            )
            self._atomic_save(persisted)
            return copy.deepcopy(candidate)

    def bind_mission_permission_evidence(
        self, *, attempt_id: str, permission_id: str
    ) -> dict[str, object]:
        from .daemon.recovery import validate_mission_permission_binding

        with self._protocol_mutation_lock():
            persisted = self.load()
            self._validated_recovery_snapshot_locked(persisted)
            attempts = [
                _validate_mission_attempt_record(item)
                for item in persisted.setdefault("mission_attempts", [])
            ]
            attempt_matches = [
                item for item in attempts if item["attempt_id"] == attempt_id
            ]
            if len(attempt_matches) != 1:
                raise ValueError("mission permission attempt reference is invalid")
            attempt = attempt_matches[0]
            permissions = [
                item
                for item in persisted.setdefault("permission_requests", [])
                if item.get("permission_id") == permission_id
            ]
            if len(permissions) != 1:
                raise ValueError("mission permission request reference is invalid")
            permission = permissions[0]
            sessions = [
                item
                for item in persisted.setdefault("agent_sessions", [])
                if item.get("session_id") == permission.get("session_id")
            ]
            if len(sessions) != 1 or sessions[0].get("agent_id") != attempt["agent_id"]:
                raise ValueError("mission permission agent scope mismatch")
            records = persisted.setdefault("mission_permission_bindings", [])
            validated = [validate_mission_permission_binding(item) for item in records]
            if any(item["permission_id"] == permission_id for item in validated):
                raise ValueError("duplicate durable recovery permission binding")
            candidate = validate_mission_permission_binding(
                {
                    "mission_id": attempt["mission_id"],
                    "attempt_id": attempt_id,
                    "permission_id": permission_id,
                }
            )
            records.append(candidate)
            self._append_recovery_audit_locked(
                persisted, "mission_permission_bound", dict(candidate)
            )
            self._atomic_save(persisted)
            return copy.deepcopy(candidate)

    def _transition_mission_attempt_admission(
        self,
        *,
        attempt_id: str,
        dispatch_key: str,
        target_state: str,
        expected_claim_id: str | None,
    ) -> dict[str, Any]:
        if (
            type(attempt_id) is not str
            or re.fullmatch(r"mat_[0-9a-f]{12}", attempt_id) is None
            or type(dispatch_key) is not str
            or re.fullmatch(r"dsp_[0-9a-f]{32}", dispatch_key) is None
            or target_state not in {"admitting", "prepared"}
            or (
                expected_claim_id is not None
                and (
                    type(expected_claim_id) is not str
                    or re.fullmatch(r"adm_[0-9a-f]{12}", expected_claim_id) is None
                )
            )
            or (target_state == "admitting" and expected_claim_id is not None)
            or (target_state == "prepared" and expected_claim_id is None)
        ):
            raise ValueError("mission attempt admission transition is invalid")
        with self._protocol_mutation_lock():
            state = self.load()
            attempts = state.get("mission_attempts", [])
            if type(attempts) is not list:
                raise ValueError("mission attempt state invalid")
            validated = [_validate_mission_attempt_record(item) for item in attempts]
            _require_unique_mission_attempt_lineage(validated)
            outbox = state.setdefault("protocol_event_outbox", [])
            known_claim_ids = {
                item["admission_claim_id"]
                for item in validated
                if item["admission_claim_id"] is not None
            }
            claim_history = self._protocol_admission_claim_history(
                outbox, validated
            )
            known_claim_ids.update(claim_history)
            matches = [item for item in validated if item["attempt_id"] == attempt_id]
            if len(matches) != 1:
                if not matches:
                    raise KeyError(attempt_id)
                raise ValueError("duplicate mission attempt identity")
            persisted = matches[0]
            if persisted["dispatch_key"] != dispatch_key:
                raise ValueError("mission attempt admission lineage drift")
            if (
                target_state == "prepared"
                and persisted["admission_claim_id"] != expected_claim_id
            ):
                raise ValueError("mission attempt admission claim drift")
            source_state = "prepared" if target_state == "admitting" else "admitting"
            if persisted["state"] != source_state:
                raise ValueError("mission attempt admission transition is invalid")
            now = utc_now()
            candidate_claim_id = (
                new_id("adm") if target_state == "admitting" else None
            )
            if candidate_claim_id is not None and candidate_claim_id in known_claim_ids:
                raise ValueError("duplicate mission admission claim identity")
            candidate = _validate_mission_attempt_record(
                {
                    **persisted,
                    "state": target_state,
                    "updated_at": (
                        now if target_state == "admitting" else persisted["created_at"]
                    ),
                    "admission_claim_id": candidate_claim_id,
                }
            )
            event_name = "claimed" if target_state == "admitting" else "released"
            event = EventRecord(
                event_id=new_id("evt"),
                event_type=f"mission_attempt_admission_{event_name}",
                created_at=now,
                payload={
                    "attempt_id": attempt_id,
                    "mission_id": candidate["mission_id"],
                    "step_id": candidate["step_id"],
                    "dispatch_key": dispatch_key,
                    "admission_claim_id": (
                        candidate["admission_claim_id"]
                        if target_state == "admitting"
                        else expected_claim_id
                    ),
                },
            )
            outbox_ids = _validated_protocol_event_outbox_ids(outbox)
            journal_ids = self._strict_protocol_journal_event_ids()
            event_summary = asdict(event)
            try:
                event_id = validate_daemon_event_record(event_summary)
            except LeaseError:
                raise ValueError("protocol event record is invalid") from None
            if event_id in outbox_ids or event_id in journal_ids:
                raise ValueError("duplicate protocol event identity")
            candidate_attempts = [
                candidate if item["attempt_id"] == attempt_id else item
                for item in validated
            ]
            self._protocol_admission_claim_history(
                [*outbox, event_summary], candidate_attempts
            )
            index = next(
                index
                for index, item in enumerate(attempts)
                if type(item) is dict and item.get("attempt_id") == attempt_id
            )
            attempts[index] = candidate
            outbox.append(event_summary)
            self._atomic_save(state)
            return copy.deepcopy(candidate)

    def interrupt_prepared_mission_attempt(
        self,
        *,
        attempt_id: str,
        dispatch_key: str,
        target_state: str,
        reason: str,
    ) -> dict[str, Any]:
        if (
            type(attempt_id) is not str
            or re.fullmatch(r"mat_[0-9a-f]{12}", attempt_id) is None
            or type(dispatch_key) is not str
            or re.fullmatch(r"dsp_[0-9a-f]{32}", dispatch_key) is None
            or target_state not in {"cancelled", "interrupted"}
            or type(reason) is not str
            or not reason.strip()
        ):
            raise ValueError("mission attempt pre-dispatch stop is invalid")
        with self._protocol_mutation_lock():
            state = self.load()
            attempts_raw = state.get("mission_attempts", [])
            if type(attempts_raw) is not list:
                raise ValueError("mission attempt state invalid")
            attempts = [_validate_mission_attempt_record(item) for item in attempts_raw]
            _require_unique_mission_attempt_lineage(attempts)
            outbox = state.setdefault("protocol_event_outbox", [])
            self._protocol_admission_claim_history(outbox, attempts)
            matches = [item for item in attempts if item["attempt_id"] == attempt_id]
            if len(matches) != 1:
                raise KeyError(attempt_id)
            persisted = matches[0]
            if (
                persisted["dispatch_key"] != dispatch_key
                or persisted["state"] != "prepared"
                or persisted["admission_claim_id"] is not None
                or persisted["receipt_summary"] is not None
            ):
                raise ValueError("mission attempt pre-dispatch stop is invalid")
            now = utc_now()
            candidate = _validate_mission_attempt_record(
                {
                    **persisted,
                    "state": target_state,
                    "updated_at": now,
                    "terminal_reason": reason,
                }
            )
            event = asdict(
                EventRecord(
                    event_id=new_id("evt"),
                    event_type="mission_attempt_pre_dispatch_stopped",
                    created_at=now,
                    payload={
                        "attempt_id": attempt_id,
                        "mission_id": candidate["mission_id"],
                        "step_id": candidate["step_id"],
                        "dispatch_key": dispatch_key,
                        "state": target_state,
                        "reason": reason,
                    },
                )
            )
            candidate_attempts = [
                candidate if item["attempt_id"] == attempt_id else item
                for item in attempts
            ]
            self._protocol_admission_claim_history(
                [*outbox, event], candidate_attempts
            )
            index = next(
                index
                for index, item in enumerate(attempts_raw)
                if item.get("attempt_id") == attempt_id
            )
            attempts_raw[index] = candidate
            outbox.append(event)
            self._atomic_save(state)
            return copy.deepcopy(candidate)

    def _protocol_admission_claim_history(
        self, outbox: object, attempts: list[dict[str, Any]]
    ) -> dict[str, tuple[str, str]]:
        _validated_protocol_event_outbox_ids(outbox)
        records: list[object] = []
        self._strict_protocol_journal_event_ids()
        journal_records_by_id: dict[str, dict[str, Any]] = {}
        journal = self._event_journal_source()
        if journal is not None:
            for line in journal.decode("utf-8").splitlines():
                if line.strip():
                    record = json.loads(line)
                    records.append(record)
                    journal_records_by_id[record["event_id"]] = record
        if type(outbox) is list:
            for record in outbox:
                durable = journal_records_by_id.get(record["event_id"])
                if durable is None:
                    records.append(record)
                    continue
                durable_canonical = json.dumps(
                    durable,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                pending_canonical = json.dumps(
                    record,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if durable_canonical != pending_canonical:
                    raise ValueError("protocol event replay conflict")
        return _validate_mission_admission_claim_history(records, attempts)

    def claim_mission_attempt_admission(
        self, *, attempt_id: str, dispatch_key: str
    ) -> dict[str, Any]:
        return self._transition_mission_attempt_admission(
            attempt_id=attempt_id,
            dispatch_key=dispatch_key,
            target_state="admitting",
            expected_claim_id=None,
        )

    def release_mission_attempt_admission(
        self,
        *,
        attempt_id: str,
        dispatch_key: str,
        expected_claim_id: str,
    ) -> dict[str, Any]:
        return self._transition_mission_attempt_admission(
            attempt_id=attempt_id,
            dispatch_key=dispatch_key,
            target_state="prepared",
            expected_claim_id=expected_claim_id,
        )

    def _transition_mission_attempt_receipt(
        self,
        *,
        attempt_id: str,
        dispatch_key: str,
        expected_claim_id: str,
        observed_dispatch_key: str | None,
        receipt_summary: str,
        target_state: str,
        reason: str | None,
    ) -> dict[str, Any]:
        if (
            type(attempt_id) is not str
            or re.fullmatch(r"mat_[0-9a-f]{12}", attempt_id) is None
            or type(dispatch_key) is not str
            or re.fullmatch(r"dsp_[0-9a-f]{32}", dispatch_key) is None
            or type(receipt_summary) is not str
            or not receipt_summary
            or type(expected_claim_id) is not str
            or re.fullmatch(r"adm_[0-9a-f]{12}", expected_claim_id) is None
            or (
                observed_dispatch_key is not None
                and (
                    type(observed_dispatch_key) is not str
                    or re.fullmatch(r"dsp_[0-9a-f]{32}", observed_dispatch_key)
                    is None
                )
            )
        ):
            raise ValueError("mission attempt receipt is invalid")
        if target_state not in {"submitted", "ambiguous"}:
            raise ValueError("mission attempt receipt transition is invalid")
        if target_state == "ambiguous" and reason not in {
            "receipt_persistence_unknown",
            "admission_outcome_unknown",
        } and not _is_acp_completion_ambiguity_reason(reason):
            raise ValueError("mission attempt ambiguity reason is invalid")
        if (
            target_state == "submitted"
            and (reason is not None or observed_dispatch_key != dispatch_key)
        ):
            raise ValueError("mission attempt receipt evidence is invalid")
        if (
            target_state == "ambiguous"
            and reason == "receipt_persistence_unknown"
            and observed_dispatch_key is None
        ):
            raise ValueError("mission attempt receipt ambiguity evidence is invalid")
        if (
            target_state == "ambiguous"
            and reason == "admission_outcome_unknown"
            and (
                observed_dispatch_key is not None
                or receipt_summary != "admission outcome unknown"
            )
        ):
            raise ValueError("mission attempt admission ambiguity evidence is invalid")
        if (
            target_state == "ambiguous"
            and _is_acp_completion_ambiguity_reason(reason)
            and observed_dispatch_key != dispatch_key
        ):
            raise ValueError("mission attempt completion ambiguity evidence is invalid")
        with self._protocol_mutation_lock():
            state = self.load()
            attempts = state.get("mission_attempts", [])
            if type(attempts) is not list:
                raise ValueError("mission attempt state invalid")
            validated = [_validate_mission_attempt_record(item) for item in attempts]
            _require_unique_mission_attempt_lineage(validated)
            outbox = state.setdefault("protocol_event_outbox", [])
            self._protocol_admission_claim_history(outbox, validated)
            matches = [item for item in validated if item["attempt_id"] == attempt_id]
            if len(matches) != 1:
                if not matches:
                    raise KeyError(attempt_id)
                raise ValueError("duplicate mission attempt identity")
            persisted = matches[0]
            if (
                _is_acp_completion_ambiguity_reason(reason)
                and persisted["configured_transport"] != "acp"
            ):
                raise ValueError("ACP completion authority invalid")
            if persisted["dispatch_key"] != dispatch_key:
                raise ValueError("mission attempt receipt lineage drift")
            if persisted["admission_claim_id"] != expected_claim_id:
                raise ValueError("mission attempt admission claim drift")
            if persisted["state"] == target_state:
                expected_reason = reason if target_state == "ambiguous" else None
                if (
                    persisted["receipt_summary"] == receipt_summary
                    and persisted["blocker"] == expected_reason
                    and persisted["terminal_reason"] == expected_reason
                ):
                    return persisted
                raise ValueError("mission attempt receipt conflict")
            if target_state == "submitted" or reason == "admission_outcome_unknown":
                allowed_states = {"admitting"}
            elif _is_acp_completion_ambiguity_reason(reason):
                allowed_states = {"submitted"}
            else:
                allowed_states = {"admitting", "submitted"}
            if persisted["state"] not in allowed_states:
                raise ValueError("mission attempt receipt transition is invalid")
            if (
                target_state == "ambiguous"
                and persisted["state"] == "submitted"
                and persisted["receipt_summary"] != receipt_summary
            ):
                raise ValueError("mission attempt receipt conflict")
            now = utc_now()
            candidate = {
                **persisted,
                "state": target_state,
                "updated_at": now,
                "receipt_summary": receipt_summary,
                "blocker": reason if target_state == "ambiguous" else None,
                "terminal_reason": reason if target_state == "ambiguous" else None,
            }
            candidate = _validate_mission_attempt_record(candidate)
            event_payload = {
                "attempt_id": attempt_id,
                "mission_id": candidate["mission_id"],
                "step_id": candidate["step_id"],
                "dispatch_key": dispatch_key,
                "admission_claim_id": expected_claim_id,
                "reason": reason,
            }
            if target_state == "ambiguous":
                event_payload.update(
                    {
                        "expected_dispatch_key": dispatch_key,
                        "observed_dispatch_key": observed_dispatch_key,
                    }
                )
            event = EventRecord(
                event_id=new_id("evt"),
                event_type=f"mission_attempt_{target_state}",
                created_at=now,
                payload=event_payload,
            )
            outbox_ids = _validated_protocol_event_outbox_ids(outbox)
            journal_ids = self._strict_protocol_journal_event_ids()
            event_summary = asdict(event)
            try:
                event_id = validate_daemon_event_record(event_summary)
            except LeaseError:
                raise ValueError("protocol event record is invalid") from None
            if event_id in outbox_ids or event_id in journal_ids:
                raise ValueError("duplicate protocol event identity")
            candidate_attempts = [
                candidate if item["attempt_id"] == attempt_id else item
                for item in validated
            ]
            self._protocol_admission_claim_history(
                [*outbox, event_summary], candidate_attempts
            )
            index = next(
                index
                for index, item in enumerate(attempts)
                if type(item) is dict and item.get("attempt_id") == attempt_id
            )
            attempts[index] = candidate
            outbox.append(event_summary)
            self._atomic_save(state)
            return copy.deepcopy(candidate)

    def record_mission_attempt_submitted(
        self,
        *,
        attempt_id: str,
        dispatch_key: str,
        expected_claim_id: str,
        receipt_summary: str,
    ) -> dict[str, Any]:
        return self._transition_mission_attempt_receipt(
            attempt_id=attempt_id,
            dispatch_key=dispatch_key,
            expected_claim_id=expected_claim_id,
            observed_dispatch_key=dispatch_key,
            receipt_summary=receipt_summary,
            target_state="submitted",
            reason=None,
        )

    def mark_acp_mission_attempt_completion_ambiguous(
        self,
        *,
        attempt_id: str,
        dispatch_key: str,
        expected_claim_id: str,
        receipt_summary: str,
        completion_stage: str,
    ) -> dict[str, Any]:
        reason = _acp_completion_ambiguity_reason(completion_stage)
        return self._transition_mission_attempt_receipt(
            attempt_id=attempt_id,
            dispatch_key=dispatch_key,
            expected_claim_id=expected_claim_id,
            observed_dispatch_key=dispatch_key,
            receipt_summary=receipt_summary,
            target_state="ambiguous",
            reason=reason,
        )

    def record_mission_attempt_result(
        self,
        *,
        attempt_id: str,
        dispatch_key: str,
        succeeded: bool,
        summary: str,
    ) -> dict[str, Any]:
        if type(succeeded) is not bool or type(summary) is not str or not summary:
            raise ValueError("mission attempt result invalid")
        with self._protocol_mutation_lock():
            state = self.load()
            attempts = [
                _validate_mission_attempt_record(item)
                for item in state.setdefault("mission_attempts", [])
            ]
            matches = [item for item in attempts if item["attempt_id"] == attempt_id]
            if (
                len(matches) != 1
                or matches[0]["dispatch_key"] != dispatch_key
                or matches[0]["state"] not in {"submitted", "running"}
            ):
                raise ValueError("mission attempt result authority invalid")
            candidate = _validate_mission_attempt_record(
                {
                    **matches[0],
                    "state": "succeeded" if succeeded else "failed",
                    "updated_at": utc_now(),
                    "receipt_summary": summary,
                    "terminal_reason": None if succeeded else "worker_failed",
                    "blocker": None if succeeded else "worker_failed",
                }
            )
            index = next(
                i for i, item in enumerate(state["mission_attempts"])
                if item.get("attempt_id") == attempt_id
            )
            state["mission_attempts"][index] = candidate
            self._append_recovery_audit_locked(
                state,
                "mission_attempt_result_recorded",
                {
                    "mission_id": candidate["mission_id"],
                    "attempt_id": attempt_id,
                    "dispatch_key": dispatch_key,
                    "state": candidate["state"],
                    **(
                        {"semantic_step_hash": candidate["semantic_step_hash"]}
                        if "semantic_step_hash" in candidate
                        else {}
                    ),
                },
            )
            self._atomic_save(state)
            return copy.deepcopy(candidate)

    def record_acp_mission_attempt_completion(
        self,
        *,
        attempt_id: str,
        dispatch_key: str,
        expected_claim_id: str,
        receipt_summary: str,
        succeeded: bool,
        summary: str,
        canonical_handoff: object | None = None,
    ) -> dict[str, object]:
        """Atomically commit one already-submitted ACP result and compact reply."""
        from .daemon.recovery import validate_mission_reply_evidence_record

        if (
            type(attempt_id) is not str
            or re.fullmatch(r"mat_[0-9a-f]{12}", attempt_id) is None
            or type(dispatch_key) is not str
            or re.fullmatch(r"dsp_[0-9a-f]{32}", dispatch_key) is None
            or type(expected_claim_id) is not str
            or re.fullmatch(r"adm_[0-9a-f]{12}", expected_claim_id) is None
            or type(receipt_summary) is not str
            or not receipt_summary
            or type(succeeded) is not bool
            or type(summary) is not str
            or not summary
        ):
            raise ValueError("ACP mission attempt completion is invalid")
        if canonical_handoff is not None:
            from .workflow import validate_canonical_handoff

            canonical = validate_canonical_handoff(
                canonical_handoff, expected_handoff_token=dispatch_key
            ).compact()
            if succeeded != (canonical["status"] == "completed"):
                raise ValueError("ACP mission attempt completion is invalid")
        else:
            canonical = None
            if succeeded:
                raise ValueError("ACP mission attempt completion is invalid")
        with self._protocol_mutation_lock():
            state = self.load()
            attempts_raw = state.setdefault("mission_attempts", [])
            if type(attempts_raw) is not list:
                raise ValueError("mission attempt state invalid")
            attempts = [_validate_mission_attempt_record(item) for item in attempts_raw]
            _require_unique_mission_attempt_lineage(attempts)
            outbox = state.setdefault("protocol_event_outbox", [])
            self._protocol_admission_claim_history(outbox, attempts)
            matches = [item for item in attempts if item["attempt_id"] == attempt_id]
            if (
                len(matches) != 1
                or matches[0]["dispatch_key"] != dispatch_key
                or matches[0]["admission_claim_id"] != expected_claim_id
                or matches[0]["configured_transport"] != "acp"
            ):
                raise ValueError("ACP mission attempt completion authority invalid")
            persisted = matches[0]
            replies = state.setdefault("mission_worker_replies", [])
            if type(replies) is not list:
                raise ValueError("mission reply evidence state invalid")
            validated_replies = [
                validate_mission_reply_evidence_record(item) for item in replies
            ]
            attempt_replies = [
                item for item in validated_replies if item["attempt_id"] == attempt_id
            ]
            expected_state = "succeeded" if succeeded else "failed"
            expected_summary = f"{receipt_summary}; result: {summary}"
            if persisted["state"] != "submitted":
                reply_matches = (
                    len(attempt_replies) == 1
                    and attempt_replies[0]["dispatch_key"] == dispatch_key
                    and attempt_replies[0]["state"] in {"received", "validated"}
                    and attempt_replies[0]["canonical_handoff"] == canonical
                )
                if (
                    persisted["state"] == expected_state
                    and persisted["receipt_summary"] == expected_summary
                    and persisted["terminal_reason"]
                    == (None if succeeded else "worker_failed")
                    and persisted["blocker"]
                    == (None if succeeded else "worker_failed")
                    and (reply_matches if canonical is not None else not attempt_replies)
                ):
                    return {
                        "attempt": copy.deepcopy(persisted),
                        "reply": copy.deepcopy(attempt_replies[0])
                        if canonical is not None
                        else None,
                        "receipt_summary": receipt_summary,
                    }
                raise ValueError("ACP mission attempt completion conflict")
            if persisted["receipt_summary"] != receipt_summary:
                raise ValueError("ACP mission attempt completion conflict")
            now = utc_now()
            candidate = _validate_mission_attempt_record(
                {
                    **persisted,
                    "state": "succeeded" if succeeded else "failed",
                    "updated_at": now,
                    "receipt_summary": expected_summary,
                    "terminal_reason": None if succeeded else "worker_failed",
                    "blocker": None if succeeded else "worker_failed",
                }
            )
            reply: dict[str, object] | None = None
            if attempt_replies:
                raise ValueError("duplicate durable recovery reply evidence")
            if canonical is not None:
                reply = validate_mission_reply_evidence_record(
                    {
                        "mission_id": candidate["mission_id"],
                        "attempt_id": attempt_id,
                        "reply_id": new_id("mrp"),
                        "dispatch_key": dispatch_key,
                        "state": "received",
                        "canonical_handoff": canonical,
                        **(
                            {"semantic_step_hash": candidate["semantic_step_hash"]}
                            if "semantic_step_hash" in candidate
                            else {}
                        ),
                    }
                )

            index = next(
                index
                for index, item in enumerate(attempts_raw)
                if type(item) is dict and item.get("attempt_id") == attempt_id
            )
            attempts_raw[index] = candidate
            self._append_recovery_audit_locked(
                state,
                "mission_attempt_result_recorded",
                {
                    "mission_id": candidate["mission_id"],
                    "attempt_id": attempt_id,
                    "dispatch_key": dispatch_key,
                    "state": candidate["state"],
                    **(
                        {"semantic_step_hash": candidate["semantic_step_hash"]}
                        if "semantic_step_hash" in candidate
                        else {}
                    ),
                },
            )
            if reply is not None:
                replies.append(reply)
                self._append_recovery_audit_locked(
                    state,
                    "mission_reply_evidence_recorded",
                    {
                        "attempt_id": attempt_id,
                        "mission_id": candidate["mission_id"],
                        "reply_id": reply["reply_id"],
                        "state": reply["state"],
                        "canonical_handoff_hash": canonical_snapshot_hash(
                            reply["canonical_handoff"]
                        ),
                        **(
                            {"semantic_step_hash": reply["semantic_step_hash"]}
                            if "semantic_step_hash" in reply
                            else {}
                        ),
                    },
                )
            self._atomic_save(state)
            return {
                "attempt": copy.deepcopy(candidate),
                "reply": copy.deepcopy(reply),
                "receipt_summary": receipt_summary,
            }

    def record_tmux_mission_attempt_completion(
        self,
        *,
        attempt_id: str,
        dispatch_key: str,
        succeeded: bool,
        summary: str,
        canonical_handoff: object | None = None,
    ) -> dict[str, object]:
        """Atomically commit one submitted tmux result and compact reply."""
        from .daemon.recovery import validate_mission_reply_evidence_record
        from .workflow import validate_canonical_handoff

        if (
            type(attempt_id) is not str
            or re.fullmatch(r"mat_[0-9a-f]{12}", attempt_id) is None
            or type(dispatch_key) is not str
            or re.fullmatch(r"dsp_[0-9a-f]{32}", dispatch_key) is None
            or type(succeeded) is not bool
            or type(summary) is not str
            or not summary
        ):
            raise ValueError("tmux mission attempt completion is invalid")
        canonical = None
        if canonical_handoff is not None:
            canonical = validate_canonical_handoff(
                canonical_handoff, expected_handoff_token=dispatch_key
            ).compact()
            if succeeded != (canonical["status"] == "completed"):
                raise ValueError("tmux mission attempt completion is invalid")
        elif succeeded:
            raise ValueError("tmux mission attempt completion is invalid")
        with self._protocol_mutation_lock():
            state = self.load()
            attempts_raw = state.setdefault("mission_attempts", [])
            attempts = [_validate_mission_attempt_record(item) for item in attempts_raw]
            _require_unique_mission_attempt_lineage(attempts)
            outbox = state.setdefault("protocol_event_outbox", [])
            self._protocol_admission_claim_history(outbox, attempts)
            matches = [item for item in attempts if item["attempt_id"] == attempt_id]
            if (
                len(matches) != 1
                or matches[0]["dispatch_key"] != dispatch_key
                or matches[0]["configured_transport"] != "tmux"
            ):
                raise ValueError("tmux mission attempt completion authority invalid")
            persisted = matches[0]
            replies = state.setdefault("mission_worker_replies", [])
            validated_replies = [
                validate_mission_reply_evidence_record(item) for item in replies
            ]
            attempt_replies = [
                item for item in validated_replies if item["attempt_id"] == attempt_id
            ]
            expected_state = "succeeded" if succeeded else "failed"
            if persisted["state"] != "submitted":
                reply_matches = (
                    len(attempt_replies) == 1
                    and attempt_replies[0]["dispatch_key"] == dispatch_key
                    and attempt_replies[0]["state"] in {"received", "validated"}
                    and attempt_replies[0]["canonical_handoff"] == canonical
                )
                if (
                    persisted["state"] == expected_state
                    and persisted["receipt_summary"] == summary
                    and persisted["terminal_reason"]
                    == (None if succeeded else "worker_failed")
                    and persisted["blocker"]
                    == (None if succeeded else "worker_failed")
                    and (reply_matches if canonical is not None else not attempt_replies)
                ):
                    return {
                        "attempt": copy.deepcopy(persisted),
                        "reply": copy.deepcopy(attempt_replies[0])
                        if canonical is not None
                        else None,
                    }
                raise ValueError("tmux mission attempt completion conflict")
            if attempt_replies:
                raise ValueError("duplicate durable recovery reply evidence")
            candidate = _validate_mission_attempt_record(
                {
                    **persisted,
                    "state": expected_state,
                    "updated_at": utc_now(),
                    "receipt_summary": summary,
                    "terminal_reason": None if succeeded else "worker_failed",
                    "blocker": None if succeeded else "worker_failed",
                }
            )
            reply = None
            if canonical is not None:
                reply = validate_mission_reply_evidence_record(
                    {
                        "mission_id": candidate["mission_id"],
                        "attempt_id": attempt_id,
                        "reply_id": new_id("mrp"),
                        "dispatch_key": dispatch_key,
                        "state": "received",
                        "canonical_handoff": canonical,
                        **(
                            {"semantic_step_hash": candidate["semantic_step_hash"]}
                            if "semantic_step_hash" in candidate
                            else {}
                        ),
                    }
                )
            index = next(
                index for index, item in enumerate(attempts_raw)
                if type(item) is dict and item.get("attempt_id") == attempt_id
            )
            attempts_raw[index] = candidate
            self._append_recovery_audit_locked(
                state,
                "mission_attempt_result_recorded",
                {
                    "mission_id": candidate["mission_id"],
                    "attempt_id": attempt_id,
                    "dispatch_key": dispatch_key,
                    "state": candidate["state"],
                    **(
                        {"semantic_step_hash": candidate["semantic_step_hash"]}
                        if "semantic_step_hash" in candidate
                        else {}
                    ),
                },
            )
            if reply is not None:
                replies.append(reply)
                self._append_recovery_audit_locked(
                    state,
                    "mission_reply_evidence_recorded",
                    {
                        "attempt_id": attempt_id,
                        "mission_id": candidate["mission_id"],
                        "reply_id": reply["reply_id"],
                        "state": reply["state"],
                        "canonical_handoff_hash": canonical_snapshot_hash(
                            reply["canonical_handoff"]
                        ),
                        **(
                            {"semantic_step_hash": reply["semantic_step_hash"]}
                            if "semantic_step_hash" in reply
                            else {}
                        ),
                    },
                )
            self._atomic_save(state)
            return {
                "attempt": copy.deepcopy(candidate),
                "reply": copy.deepcopy(reply),
            }

    def advance_mission_after_handoff(
        self, mission_id: str, *, attempt_id: str, handoff_id: str
    ) -> dict[str, Any]:
        from .daemon.recovery import validate_mission_handoff_evidence_record

        with self._protocol_mutation_lock():
            state = self.load()
            mission = self._unique_mission_record(state, mission_id)
            handoffs = [
                validate_mission_handoff_evidence_record(item)
                for item in state.setdefault("mission_handoffs", [])
            ]
            matches = [
                item for item in handoffs
                if item["attempt_id"] == attempt_id
                and item["handoff_id"] == handoff_id
                and item["mission_id"] == mission_id
                and item["state"] == "recorded"
            ]
            attempts = [
                _validate_mission_attempt_record(item)
                for item in state.setdefault("mission_attempts", [])
            ]
            attempt = next(
                (item for item in attempts if item["attempt_id"] == attempt_id), None
            )
            if (
                len(matches) != 1
                or attempt is None
                or attempt["state"] not in {"completed", "succeeded"}
                or mission.get("current_step", 0) + 1
                != int(attempt["step_id"].split("_")[1])
            ):
                raise ValueError("Mission handoff advance authority invalid")
            semantic_hash = attempt.get("semantic_step_hash")
            if semantic_hash is not None:
                from .daemon.service import validate_semantic_handoff_scope

                plan = self._unique_plan_record(state, str(mission.get("plan_id") or ""))
                plan_body = plan.get("plan")
                semantic_steps = (
                    plan_body.get("semantic_steps")
                    if type(plan_body) is dict
                    else None
                )
                position = int(attempt["step_id"].split("_")[1])
                semantic_step = (
                    semantic_steps[position - 1]
                    if type(semantic_steps) is list
                    and 0 < position <= len(semantic_steps)
                    else None
                )
                blocker = validate_semantic_handoff_scope(
                    semantic_step, matches[0]["canonical_handoff"]
                )
                if blocker is not None:
                    mission["status"] = "stopped"
                    mission["stop_reason"] = blocker
                    mission["updated_at"] = utc_now()
                    self._append_recovery_audit_locked(
                        state,
                        "semantic_authority_drift_detected",
                        {
                            "mission_id": mission_id,
                            "attempt_id": attempt_id,
                            "step_id": attempt["step_id"],
                            "agent_id": attempt["agent_id"],
                            "code": blocker,
                        },
                    )
                    self._atomic_save(state)
                    return copy.deepcopy(mission)
            mission["current_step"] = int(mission.get("current_step", 0)) + 1
            mission["status"] = "running"
            mission["updated_at"] = utc_now()
            # Recovery authority remains bound to the durable latest attempt
            # until the next attempt atomically replaces it.  The scheduler
            # derives logical step-local currentness separately.
            self._append_recovery_audit_locked(
                state, "mission_step_activated", {
                    "mission_id": mission_id,
                    "attempt_id": attempt_id,
                    "current_step": mission["current_step"],
                    **(
                        {"semantic_step_hash": semantic_hash}
                        if semantic_hash is not None
                        else {}
                    ),
                }
            )
            self._atomic_save(state)
            return copy.deepcopy(mission)

    def complete_admitted_mission(self, mission_id: str) -> dict[str, Any]:
        with self._protocol_mutation_lock():
            state = self.load()
            mission = self._unique_mission_record(state, mission_id)
            if (
                mission.get("daemon_admission", {}).get("state") != "admitted"
                or mission.get("current_step") != mission.get("step_count")
            ):
                raise ValueError("Mission completion authority invalid")
            mission.update({
                "status": "completed", "completed_at": utc_now(),
                "updated_at": utc_now(), "stop_reason": None,
            })
            self._append_recovery_audit_locked(
                state, "mission_completed", {"mission_id": mission_id}
            )
            self._atomic_save(state)
            return copy.deepcopy(mission)

    def mark_mission_attempt_ambiguous(
        self,
        *,
        attempt_id: str,
        dispatch_key: str,
        expected_claim_id: str,
        observed_dispatch_key: str | None = None,
        receipt_summary: str,
        reason: str,
    ) -> dict[str, Any]:
        if reason != "receipt_persistence_unknown":
            raise ValueError("mission attempt receipt ambiguity reason is invalid")
        return self._transition_mission_attempt_receipt(
            attempt_id=attempt_id,
            dispatch_key=dispatch_key,
            expected_claim_id=expected_claim_id,
            observed_dispatch_key=observed_dispatch_key,
            receipt_summary=receipt_summary,
            target_state="ambiguous",
            reason=reason,
        )

    def mark_mission_attempt_admission_ambiguous(
        self,
        *,
        attempt_id: str,
        dispatch_key: str,
        expected_claim_id: str,
        reason: str,
    ) -> dict[str, Any]:
        if reason != "admission_outcome_unknown":
            raise ValueError("mission attempt admission ambiguity reason is invalid")
        return self._transition_mission_attempt_receipt(
            attempt_id=attempt_id,
            dispatch_key=dispatch_key,
            expected_claim_id=expected_claim_id,
            observed_dispatch_key=None,
            receipt_summary="admission outcome unknown",
            target_state="ambiguous",
            reason=reason,
        )

    def claim_mission_execution(
        self,
        mission_id: str,
        *,
        resuming: bool,
        confirmed_at: str,
    ) -> dict[str, Any]:
        if not isinstance(resuming, bool):
            raise TypeError("resuming must be a boolean")
        if not isinstance(confirmed_at, str) or not confirmed_at:
            raise ValueError("confirmed_at must be a non-empty string")
        state = self.load()
        record = next(
            (
                item
                for item in state.setdefault("missions", [])
                if isinstance(item, dict) and item.get("mission_id") == mission_id
            ),
            None,
        )
        if record is None:
            raise KeyError(mission_id)
        status = record.get("status")
        if status in {"preparing", "running", "completed"}:
            return {"claimed": False, "mission": copy.deepcopy(record)}
        allowed = {"stopped", "interrupted"} if resuming else {"pending_confirmation"}
        if status not in allowed:
            raise ValueError("mission is not claimable")
        record.update(
            {
                "status": "preparing",
                "confirmed_at": record.get("confirmed_at") or confirmed_at,
                "stop_reason": None,
                "blockers": [],
                "can_start": False,
                "updated_at": utc_now(),
            }
        )
        self.save(state)
        return {"claimed": True, "mission": copy.deepcopy(record)}

    def update_mission(self, mission_id: str, /, **changes: Any) -> dict[str, Any]:
        state = self.load()
        record = next(
            (
                item
                for item in state.setdefault("missions", [])
                if isinstance(item, dict) and item.get("mission_id") == mission_id
            ),
            None,
        )
        if record is None:
            raise KeyError(mission_id)
        mutable_fields = {
            "status",
            "stop_reason",
            "can_start",
            "blockers",
            "workflow_run_id",
            "current_step",
            "confirmed_at",
        }
        unknown_fields = set(changes) - mutable_fields
        if unknown_fields:
            raise ValueError(
                f"immutable or unknown mission fields: {', '.join(sorted(unknown_fields))}"
            )
        current_status = record.get("status")
        target_status = changes.get("status", current_status)
        if current_status == "completed":
            if changes == {"status": "completed"}:
                return record
            raise ValueError("completed mission is terminal")
        if not mission_status_transition_allowed(current_status, target_status):
            raise ValueError(f"invalid mission status transition: {current_status} -> {target_status}")
        if "stop_reason" in changes and changes["stop_reason"] is not None and not isinstance(
            changes["stop_reason"], str
        ):
            raise ValueError("stop_reason must be a string or null")
        if "can_start" in changes and not isinstance(changes["can_start"], bool):
            raise ValueError("can_start must be a boolean")
        if "blockers" in changes:
            compact_blockers, invalid_blockers = compact_mission_blockers(
                changes["blockers"]
            )
            if invalid_blockers:
                raise ValueError("blockers must be a list of strings")
            changes["blockers"] = compact_blockers
        effective_can_start = changes.get("can_start", record.get("can_start"))
        effective_blockers = changes.get("blockers", record.get("blockers"))
        if effective_can_start is True and effective_blockers:
            raise ValueError("can_start requires empty blockers")
        if "workflow_run_id" in changes:
            workflow_run_id = changes["workflow_run_id"]
            if workflow_run_id is not None and not isinstance(workflow_run_id, str):
                raise ValueError("workflow_run_id must be a string or null")
            existing_run_id = record.get("workflow_run_id")
            if existing_run_id is not None and workflow_run_id != existing_run_id:
                raise ValueError("workflow_run_id cannot change once set")
        if "confirmed_at" in changes:
            confirmed_at = changes["confirmed_at"]
            if confirmed_at is not None and not isinstance(confirmed_at, str):
                raise ValueError("confirmed_at must be a string or null")
            existing_confirmed_at = record.get("confirmed_at")
            if existing_confirmed_at is not None and confirmed_at != existing_confirmed_at:
                raise ValueError("confirmed_at cannot change once set")
        if "current_step" in changes:
            current_step = changes["current_step"]
            step_count = record.get("step_count")
            if (
                not isinstance(current_step, int)
                or isinstance(current_step, bool)
                or not isinstance(step_count, int)
                or isinstance(step_count, bool)
                or current_step < record.get("current_step", 0)
                or current_step < 0
                or current_step > step_count
            ):
                raise ValueError("current_step must advance within 0..step_count")
        record.update(changes)
        record["updated_at"] = utc_now()
        if changes.get("status") == "completed" and not record.get("completed_at"):
            record["completed_at"] = record["updated_at"]
        self.save(state)
        return record

    def record_skill_load(
        self,
        *,
        agent_id: str,
        purpose: str,
        skill: dict[str, Any],
    ) -> dict[str, Any]:
        state = self.load()
        record = {
            "load_id": new_id("skl"),
            "agent_id": agent_id,
            "purpose": purpose,
            "name": skill.get("name"),
            "source": skill.get("source"),
            "path": skill.get("path"),
            "content_hash": skill.get("content_hash"),
            "content_snapshot": skill.get("content_snapshot"),
            "description": skill.get("description"),
            "required_tools": skill.get("required_tools") if isinstance(skill.get("required_tools"), list) else [],
            "planning_guidance": skill.get("planning_guidance")
            if isinstance(skill.get("planning_guidance"), list)
            else [],
            "risk": skill.get("risk"),
            "created_at": utc_now(),
        }
        state.setdefault("skill_loads", []).append(record)
        self.save(state)
        return record

    def record_skill_suggestion(
        self,
        *,
        name: str,
        summary: str,
        rationale: str,
        source: str,
        agent_id: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        state = self.load()
        record = {
            "suggestion_id": new_id("sgs"),
            "status": "pending",
            "name": name,
            "summary": summary,
            "rationale": rationale,
            "source": source,
            "agent_id": agent_id,
            "trace_id": trace_id,
            "draft_path": f".agentdeck/skills/{name}/SKILL.md",
            "created_at": utc_now(),
            "controls": [
                {
                    "kind": "inspect",
                    "label": "List skill suggestions",
                    "command": "agentdeck skills suggestions",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                }
            ],
        }
        state.setdefault("skill_suggestions", []).append(record)
        self.save(state)
        return record

    def record_memory_suggestion(
        self,
        *,
        summary: str,
        rationale: str,
        source: str,
        scope: str,
        agent_id: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        state = self.load()
        target = ".agentdeck/memory/global.md" if scope == "global" else ".agentdeck/memory/project.md"
        record = {
            "suggestion_id": new_id("mem"),
            "status": "pending",
            "scope": scope,
            "summary": summary,
            "rationale": rationale,
            "source": source,
            "agent_id": agent_id,
            "trace_id": trace_id,
            "target": target,
            "created_at": utc_now(),
            "controls": [
                {
                    "kind": "inspect",
                    "label": "List memory suggestions",
                    "command": "agentdeck memory suggestions",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                }
            ],
        }
        state.setdefault("memory_suggestions", []).append(record)
        self.save(state)
        return record

    def record_release(
        self,
        *,
        review_gate_status: str,
        artifact_count: int,
        review_reply_count: int,
        code_reviewer_id: str | None,
        round_reviewer_id: str | None,
        code_review_reply_id: str | None,
        round_review_reply_id: str | None,
    ) -> dict[str, Any]:
        state = self.load()
        releases = state.setdefault("releases", [])
        record = {
            "release_id": new_id("rel"),
            "round": len(releases) + 1,
            "status": "released",
            "review_gate_status": review_gate_status,
            "artifact_count": artifact_count,
            "review_reply_count": review_reply_count,
            "code_reviewer_id": code_reviewer_id,
            "round_reviewer_id": round_reviewer_id,
            "code_review_reply_id": code_review_reply_id,
            "round_review_reply_id": round_review_reply_id,
            "created_at": utc_now(),
        }
        releases.append(record)
        self.save(state)
        return record

    def list_releases(self) -> list[dict[str, Any]]:
        releases = self.load().get("releases", [])
        return [item for item in releases if isinstance(item, dict)]

    def list_events(self, limit: int = 20) -> list[dict[str, Any]]:
        events = self.all_events()
        if limit <= 0:
            return []
        return events[-limit:]

    def all_events(self) -> list[dict[str, Any]]:
        journal = self._event_journal_source()
        if journal is None:
            return []
        return [
            json.loads(line)
            for line in journal.decode("utf-8").splitlines()
            if line.strip()
        ]

    def bind_agent(self, binding: AgentRuntimeBinding) -> None:
        state = self.load()
        agents = state.setdefault("agents", {})
        agents[binding.agent_id] = asdict(binding)
        self.save(state)

    def mark_agent_stopped(self, agent_id: str) -> dict[str, Any]:
        state = self.load()
        agents = state.setdefault("agents", {})
        current = agents.get(
            agent_id,
            {
                "agent_id": agent_id,
                "pane_id": None,
                "session_name": None,
                "cwd": None,
                "status": "configured",
            },
        )
        current.update({"pane_id": None, "status": "stopped"})
        agents[agent_id] = current
        self.save(state)
        return current

    def mark_agent_released(self, agent_id: str) -> dict[str, Any]:
        state = self.load()
        agents = state.setdefault("agents", {})
        current = agents.get(
            agent_id,
            {
                "agent_id": agent_id,
                "pane_id": None,
                "session_name": None,
                "cwd": None,
                "status": "configured",
            },
        )
        current.update({"pane_id": None, "status": "released"})
        agents[agent_id] = current
        self.save(state)
        return current

    def mark_worktree_abandoned(self, message_id: str) -> list[str]:
        state = self.load()
        abandoned = state.setdefault("abandoned_worktrees", [])
        if message_id in abandoned:
            raise ValueError(f"worktree already abandoned: {message_id}")
        abandoned.append(message_id)
        self.save(state)
        return list(abandoned)

    def mark_worktree_merged(self, message_id: str) -> list[str]:
        # 重复 merge（already up to date）是合法操作，这里保持幂等不报错。
        state = self.load()
        merged = state.setdefault("merged_worktrees", [])
        if message_id not in merged:
            merged.append(message_id)
            self.save(state)
        return list(merged)

    def grant_delegation(self, agent_id: str, prefix: str) -> dict[str, Any]:
        state = self.load()
        delegations = state.setdefault("delegations", [])
        for item in delegations:
            if (
                item.get("agent_id") == agent_id
                and item.get("prefix") == prefix
                and not item.get("revoked_at")
            ):
                raise ValueError(f"delegation already active for {agent_id}: {prefix}")
        record = {
            "delegation_id": new_id("dlg"),
            "agent_id": agent_id,
            "prefix": prefix,
            "created_at": utc_now(),
            "revoked_at": None,
        }
        delegations.append(record)
        self.save(state)
        return dict(record)

    def revoke_delegation(self, delegation_id: str) -> dict[str, Any]:
        state = self.load()
        for item in state.get("delegations", []):
            if item.get("delegation_id") == delegation_id:
                if item.get("revoked_at"):
                    raise ValueError(f"delegation already revoked: {delegation_id}")
                item["revoked_at"] = utc_now()
                self.save(state)
                return dict(item)
        raise ValueError(f"unknown delegation: {delegation_id}")

    def mark_agent_stale(self, agent_id: str) -> dict[str, Any]:
        state = self.load()
        agents = state.setdefault("agents", {})
        current = agents.get(
            agent_id,
            {
                "agent_id": agent_id,
                "pane_id": None,
                "session_name": None,
                "cwd": None,
                "status": "configured",
            },
        )
        current.update({"pane_id": None, "status": "stale"})
        agents[agent_id] = current
        self.save(state)
        return current

    def agent_binding(self, agent_id: str) -> dict[str, Any] | None:
        return self.load().get("agents", {}).get(agent_id)

    def append_message(self, from_actor: str, to_agent: str, task: str, prompt: str) -> dict[str, Any]:
        state = self.load()
        messages = state.setdefault("messages", [])
        message = {
            "message_id": new_id("msg"),
            "from_actor": from_actor,
            "to_agent": to_agent,
            "task": task,
            "prompt": prompt,
            "status": "dispatched",
            "created_at": utc_now(),
        }
        messages.append(message)
        self.save(state)
        return message

    def record_plan(
        self,
        task: str,
        provider: str,
        model: str,
        plan: dict[str, Any],
        skill_context: dict[str, Any] | None = None,
        leader_generation: dict[str, object] | None = None,
        split_provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = self.load()
        record = {
            "plan_id": new_id("pln"),
            "task": task,
            "provider": provider,
            "provider_backend": leader_provider_backend(provider),
            "provider_transport": leader_provider_transport(provider),
            "leader_backend": leader_backend_identity(provider, model, bool(plan.get("dispatch_ready", False))),
            "model": model,
            "status": "planned",
            "dispatch_ready": bool(plan.get("dispatch_ready", False)),
            "skill_context": self._plan_skill_context(skill_context),
            "plan": plan,
            "created_at": utc_now(),
        }
        if split_provenance is not None:
            planner_provider, planner_model = split_provenance["planner_backend"]
            orchestrator_provider, orchestrator_model = split_provenance[
                "orchestrator_backend"
            ]
            record["planner_backend"] = leader_backend_identity(
                planner_provider, planner_model
            )
            record["orchestrator_backend"] = leader_backend_identity(
                orchestrator_provider, orchestrator_model
            )
            record["planner_brief"] = split_provenance["planner_brief"]
        if leader_generation is not None:
            steps = plan.get("steps")
            record["leader_generation"] = self._plan_leader_generation(
                leader_generation,
                provider=provider,
                model=model,
                step_count=len(steps) if isinstance(steps, list) else 0,
                plan=plan,
            )
        state.setdefault("plans", []).append(record)
        self.save(state)
        return record

    def build_plan_record(
        self,
        task: str,
        provider: str,
        model: str,
        plan: dict[str, Any],
        skill_context: dict[str, Any] | None = None,
        leader_generation: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        persisted_plan = (
            validated_compiled_semantic_plan(plan)
            if "semantic_authority" in plan or "semantic_steps" in plan
            else copy.deepcopy(plan)
        )
        record = {
            "plan_id": new_id("pln"),
            "task": task,
            "provider": provider,
            "provider_backend": leader_provider_backend(provider),
            "provider_transport": leader_provider_transport(provider),
            "leader_backend": leader_backend_identity(
                provider, model, bool(plan.get("dispatch_ready", False))
            ),
            "model": model,
            "status": "planned",
            "dispatch_ready": bool(plan.get("dispatch_ready", False)),
            "skill_context": self._plan_skill_context(skill_context),
            "plan": persisted_plan,
            "created_at": utc_now(),
        }
        if leader_generation is not None:
            steps = plan.get("steps")
            record["leader_generation"] = self._plan_leader_generation(
                leader_generation,
                provider=provider,
                model=model,
                step_count=len(steps) if isinstance(steps, list) else 0,
                plan=plan,
            )
        return record

    @staticmethod
    def _plan_leader_generation(
        value: object,
        *,
        provider: object,
        model: object,
        step_count: object,
        plan: object = None,
    ) -> dict[str, object]:
        """Return compact validated provenance, or a deterministic legacy projection."""

        def fail() -> None:
            raise ValueError("plan leader generation invalid")

        def contains_forbidden_key(candidate: object) -> bool:
            if type(candidate) is dict:
                for raw_key, nested in candidate.items():
                    if type(raw_key) is not str:
                        return True
                    normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", raw_key).lower().replace("-", "_")
                    parts = frozenset(part for part in normalized.split("_") if part)
                    if (
                        normalized == "api_key"
                        or parts.intersection(_PLAN_LEADER_FORBIDDEN_KEY_PARTS)
                        or contains_forbidden_key(nested)
                    ):
                        return True
            elif type(candidate) is list:
                return any(contains_forbidden_key(item) for item in candidate)
            return False

        if contains_forbidden_key(value):
            fail()
        if type(provider) is not str or not provider or type(model) is not str or not model:
            fail()
        if type(step_count) is not int or step_count < 1 or step_count > 64:
            fail()

        if value is _MISSING_PLAN_LEADER_GENERATION:
            backend = leader_provider_backend(provider)
            legacy_mode = {
                "api": "json_object",
                "cli": "prompt_only",
                "local": "local",
            }.get(backend, "local")
            return {
                "provider": provider,
                "model": model,
                "constraint_mode": legacy_mode,
                "schema_version": None,
                "schema_hash": None,
                "attempt_count": 1,
                "regeneration_used": False,
                "selected_agent_ids": [],
                "step_count": step_count,
            }

        if step_count < 2:
            fail()

        semantic_plan = type(plan) is dict and (
            "semantic_authority" in plan or "semantic_steps" in plan
        )
        selected_fields = (
            _PLAN_LEADER_GENERATION_BASE_FIELDS
            + _PLAN_LEADER_GENERATION_SEMANTIC_FIELDS
            if semantic_plan
            else _PLAN_LEADER_GENERATION_BASE_FIELDS
        )
        if type(value) is not dict or set(value) != set(selected_fields):
            fail()
        candidate = value
        mode = candidate.get("constraint_mode")
        schema_version = candidate.get("schema_version")
        schema_hash = candidate.get("schema_hash")
        attempt_count = candidate.get("attempt_count")
        regeneration_used = candidate.get("regeneration_used")
        selected_agent_ids = candidate.get("selected_agent_ids")
        if candidate.get("provider") != provider or type(candidate.get("provider")) is not str:
            fail()
        if candidate.get("model") != model or type(candidate.get("model")) is not str:
            fail()
        if type(mode) is not str or mode not in _PLAN_LEADER_GENERATION_MODES:
            fail()
        if mode == "native_json_schema":
            expected_schema_version = (
                SEMANTIC_LEADER_PLAN_SCHEMA_VERSION
                if semantic_plan
                else _PLAN_LEADER_SCHEMA_VERSION
            )
            if (
                schema_version != expected_schema_version
                or type(schema_version) is not str
                or type(schema_hash) is not str
                or re.fullmatch(r"sha256:[0-9a-f]{64}", schema_hash) is None
            ):
                fail()
        elif schema_version is not None or schema_hash is not None:
            fail()
        if semantic_plan:
            authority = plan.get("semantic_authority")
            try:
                provenance_authority = copy.deepcopy(authority)
                provenance_authority["proposed_effects"] = []
                expected_authority_hash = semantic_authority_hash(
                    provenance_authority
                )
            except (TypeError, ValueError):
                fail()
            if (
                candidate.get("semantic_authority_schema_version")
                != authority.get("schema_version")
                or candidate.get("semantic_authority_hash")
                != expected_authority_hash
            ):
                fail()
        if type(attempt_count) is not int or attempt_count < 1 or attempt_count > 2:
            fail()
        if type(regeneration_used) is not bool or regeneration_used is not (attempt_count == 2):
            fail()
        if (
            type(selected_agent_ids) is not list
            or not selected_agent_ids
            or any(type(item) is not str or not item for item in selected_agent_ids)
            or len(set(selected_agent_ids)) != len(selected_agent_ids)
        ):
            fail()
        if type(candidate.get("step_count")) is not int or candidate.get("step_count") != step_count:
            fail()
        body_steps = plan.get("steps") if type(plan) is dict else None
        if type(body_steps) is not list or len(body_steps) != step_count:
            fail()
        if any(
            type(item) is not dict
            or type(item.get("agent_id")) is not str
            or not item.get("agent_id")
            for item in body_steps
        ):
            fail()
        known_agent_ids = {
            item.get("agent_id")
            for item in body_steps
        }
        if set(selected_agent_ids) != known_agent_ids:
            fail()
        return {
            field: copy.deepcopy(candidate[field])
            for field in selected_fields
        }

    def list_plans(self) -> list[dict[str, Any]]:
        return list(self.load().get("plans", []))

    def plan_by_id(self, plan_id: str) -> dict[str, Any]:
        for plan in self.load().get("plans", []):
            if plan.get("plan_id") == plan_id:
                return plan
        raise KeyError(plan_id)

    def create_workflow_run(
        self,
        *,
        plan_id: str,
        plan_hash: str,
        timeout_seconds: int,
        authorized_steps: list[dict[str, Any]],
    ) -> dict[str, Any]:
        state = self.load()
        now = utc_now()
        record = {
            "run_id": new_id("wfr"),
            "plan_id": plan_id,
            "plan_hash": plan_hash,
            "status": "running",
            "current_step": 1,
            "step_count": len(authorized_steps),
            "timeout_seconds": timeout_seconds,
            "authorized_steps": authorized_steps,
            "turns": [],
            "stop_reason": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        state.setdefault("workflow_runs", []).append(record)
        self.save(state)
        return record

    def workflow_run_by_id(self, run_id: str) -> dict[str, Any]:
        for item in self.load().get("workflow_runs", []):
            if item.get("run_id") == run_id:
                return item
        raise KeyError(run_id)

    def update_workflow_run(self, run_id: str, **changes: Any) -> dict[str, Any]:
        state = self.load()
        record = next(
            (
                item
                for item in state.setdefault("workflow_runs", [])
                if item.get("run_id") == run_id
            ),
            None,
        )
        if record is None:
            raise KeyError(run_id)
        record.update(changes)
        record["updated_at"] = utc_now()
        if changes.get("status") == "completed" and not record.get("completed_at"):
            record["completed_at"] = record["updated_at"]
        self.save(state)
        return record

    def plan_status(self, plan_id: str) -> dict[str, Any]:
        state = self.load()
        plan_record = next((plan for plan in state.get("plans", []) if plan.get("plan_id") == plan_id), None)
        if plan_record is None:
            raise KeyError(plan_id)
        plan_body = plan_record.get("plan", {})
        steps = plan_body.get("steps", []) if isinstance(plan_body, dict) else []
        approvals = [item for item in state.get("approvals", []) if item.get("plan_id") == plan_id]
        approvals_by_step = {item.get("step"): item for item in approvals}
        status_counts = {
            "steps": len(steps) if isinstance(steps, list) else 0,
            "approvals": len(approvals),
            "pending": 0,
            "approved": 0,
            "rejected": 0,
            "dispatched": 0,
        }
        status_steps = []
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                approval = approvals_by_step.get(step.get("step"))
                approval_status = approval.get("status") if approval else "not_created"
                if approval_status in status_counts:
                    status_counts[approval_status] += 1
                status_item = {
                    "step": step.get("step"),
                    "agent_id": step.get("agent_id"),
                    "role": step.get("role"),
                    "task": step.get("task"),
                    "approval_id": approval.get("approval_id") if approval else None,
                    "approval_status": approval_status,
                    "message_id": approval.get("message_id") if approval else None,
                    "attempt_id": approval.get("attempt_id") if approval else None,
                    "job_id": approval.get("job_id") if approval else None,
                }
                if approval and approval.get("reason"):
                    status_item["reason"] = approval.get("reason")
                status_steps.append(status_item)
        return {
            "plan_id": plan_id,
            "task": plan_record.get("task"),
            "status": plan_record.get("status"),
            "provider": plan_record.get("provider"),
            "provider_backend": plan_record.get("provider_backend")
            or leader_provider_backend(str(plan_record.get("provider") or "")),
            "provider_transport": plan_record.get("provider_transport")
            or leader_provider_transport(str(plan_record.get("provider") or "")),
            "leader_backend": plan_record.get("leader_backend")
            or leader_backend_identity(
                str(plan_record.get("provider") or ""),
                str(plan_record.get("model") or ""),
                bool(plan_record.get("dispatch_ready", False)),
            ),
            "model": plan_record.get("model"),
            "created_at": plan_record.get("created_at"),
            "skill_context": self._plan_skill_context(plan_record.get("skill_context")),
            "counts": status_counts,
            "steps": status_steps,
        }

    def _plan_acceptance_criteria(self, plan_id: str) -> list[str] | None:
        state = self.load()
        plan = next(
            (item for item in state.get("plans", []) if item.get("plan_id") == plan_id),
            None,
        )
        if plan is None:
            return None
        brief = plan.get("planner_brief")
        if not isinstance(brief, dict):
            return None
        criteria = brief.get("acceptance_criteria")
        if not isinstance(criteria, list):
            return None
        return list(criteria)

    def leader_review(self, plan_id: str) -> dict[str, Any]:
        review = self._leader_review_core(plan_id)
        review["acceptance_criteria"] = self._plan_acceptance_criteria(plan_id)
        return review

    def _leader_review_core(self, plan_id: str) -> dict[str, Any]:
        status = self.plan_status(plan_id)
        leader_backend = status.get("leader_backend")
        state = self.load()
        replies = state.get("replies", [])
        replies_by_message = {reply.get("message_id"): reply for reply in replies}
        for step in status["steps"]:
            if step.get("approval_status") == "approved":
                return {
                    "plan_id": plan_id,
                    "next_action": "dispatch_approved",
                    "reason": "approved step is waiting for dispatch",
                    "approval_id": step.get("approval_id"),
                    "agent_id": step.get("agent_id"),
                    "counts": status["counts"],
                    "leader_backend": leader_backend,
                }
        dispatched_without_reply = []
        completed_replies = []
        for step in status["steps"]:
            message_id = step.get("message_id")
            if step.get("approval_status") != "dispatched" or not message_id:
                continue
            reply = replies_by_message.get(message_id)
            if reply is None:
                dispatched_without_reply.append(step)
            else:
                completed_replies.append(
                    {
                        "agent_id": step.get("agent_id"),
                        "message_id": message_id,
                        "reply_id": reply.get("reply_id"),
                    }
                )
        if dispatched_without_reply:
            step = dispatched_without_reply[0]
            return {
                "plan_id": plan_id,
                "next_action": "wait_for_reply",
                "reason": "dispatched step has no reply yet",
                "agent_id": step.get("agent_id"),
                "message_id": step.get("message_id"),
                "counts": status["counts"],
                "leader_backend": leader_backend,
            }
        pending_steps = [
            step
            for step in status["steps"]
            if step.get("approval_status") in (None, "pending")
        ]
        if completed_replies and pending_steps:
            # 部分派发：已有回复但仍有步骤等待人类审批,不得提前 summarize。
            return {
                "plan_id": plan_id,
                "next_action": "wait_for_approval",
                "reason": "replied steps exist but pending approvals remain",
                "counts": status["counts"],
                "leader_backend": leader_backend,
            }
        if completed_replies:
            return {
                "plan_id": plan_id,
                "next_action": "summarize",
                "reason": "all dispatched steps have replies",
                "replies": completed_replies,
                "counts": status["counts"],
                "leader_backend": leader_backend,
            }
        return {
            "plan_id": plan_id,
            "next_action": "wait_for_approval",
            "reason": "no approved or dispatched steps are ready",
            "counts": status["counts"],
            "leader_backend": leader_backend,
        }

    def record_chat_turn(
        self,
        mode: str,
        message: str,
        plan_id: str | None,
        next_command: str | None,
        provider: str | None = None,
        model: str | None = None,
        review: dict[str, Any] | None = None,
        action_id: str | None = None,
        action_kind: str | None = None,
    ) -> dict[str, Any]:
        state = self.load()
        turn = {
            "turn_id": new_id("cht"),
            "mode": mode,
            "message": message,
            "plan_id": plan_id,
            "next_command": next_command,
            "provider": provider,
            "model": model,
            "review": review,
            "action_id": action_id,
            "action_kind": action_kind,
            "created_at": utc_now(),
        }
        state.setdefault("chat_turns", []).append(turn)
        self.save(state)
        return turn

    def list_chat_turns(self) -> list[dict[str, Any]]:
        return list(self.load().get("chat_turns", []))

    def record_leader_error(
        self,
        mode: str,
        provider: str,
        model: str | None,
        task: str,
        error: str,
    ) -> dict[str, Any]:
        state = self.load()
        record = {
            "error_id": new_id("err"),
            "mode": mode,
            "provider": provider,
            "model": model,
            "task": task,
            "error": error,
            "created_at": utc_now(),
        }
        state.setdefault("leader_errors", []).append(record)
        self.save(state)
        return record

    def suggest_leader_action(self, plan_id: str | None = None) -> dict[str, Any]:
        state = self.load()
        plans = state.get("plans", [])
        if plan_id is None:
            if not plans:
                raise KeyError("no plans")
            plan_id = str(plans[-1]["plan_id"])
        status = self.plan_status(plan_id)
        if status["counts"]["approvals"] == 0:
            action = {
                "action_id": new_id("act"),
                "kind": "create_approvals",
                "status": "pending",
                "requires_confirmation": True,
                "plan_id": plan_id,
                "approval_id": None,
                "agent_id": None,
                "message_id": None,
                "command": f"agentdeck approval create-from-plan --plan-id {plan_id}",
                "reason": "plan has no approval records",
                "created_at": utc_now(),
            }
            return self._record_or_reuse_pending_leader_action(state, action)
        review = self.leader_review(plan_id)
        action = self._action_from_review(review)
        state = self.load()
        return self._record_or_reuse_pending_leader_action(state, action)

    def _record_or_reuse_pending_leader_action(self, state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
        actions = state.setdefault("leader_actions", [])
        existing = next((item for item in actions if self._same_pending_leader_action(item, action)), None)
        if existing is not None:
            return existing
        actions.append(action)
        self.save(state)
        return action

    @staticmethod
    def _same_pending_leader_action(existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
        if existing.get("status") != "pending":
            return False
        return all(
            existing.get(key) == candidate.get(key)
            for key in ["kind", "plan_id", "approval_id", "agent_id", "message_id"]
        )

    def _action_from_review(self, review: dict[str, Any]) -> dict[str, Any]:
        next_action = review.get("next_action")
        command = None
        if next_action == "dispatch_approved" and review.get("approval_id"):
            command = f"agentdeck approval dispatch --approval-id {review['approval_id']}"
        elif next_action == "wait_for_reply" and review.get("agent_id") and review.get("message_id"):
            command = f"agentdeck capture-reply --agent {review['agent_id']} --message-id {review['message_id']}"
        elif next_action == "summarize" and review.get("plan_id"):
            command = f"agentdeck leader summary --plan-id {review['plan_id']}"
        elif next_action == "wait_for_approval" and review.get("plan_id"):
            command = f"agentdeck approval list"
        return {
            "action_id": new_id("act"),
            "kind": next_action,
            "status": "pending",
            "requires_confirmation": True,
            "plan_id": review.get("plan_id"),
            "approval_id": review.get("approval_id"),
            "agent_id": review.get("agent_id"),
            "message_id": review.get("message_id"),
            "command": command,
            "reason": review.get("reason"),
            "created_at": utc_now(),
        }

    def list_leader_actions(self) -> list[dict[str, Any]]:
        return list(self.load().get("leader_actions", []))

    def leader_action_detail(self, action_id: str) -> dict[str, Any]:
        action = next((item for item in self.load().get("leader_actions", []) if item.get("action_id") == action_id), None)
        if action is None:
            raise KeyError(action_id)
        return {**action, **self._leader_action_detail_fields(action)}

    @staticmethod
    def _leader_action_detail_fields(action: dict[str, Any]) -> dict[str, Any]:
        action_id = str(action.get("action_id"))
        can_apply = action.get("status") == "pending" and action.get("kind") == "create_approvals"
        apply_blocker = None
        if action.get("status") != "pending":
            apply_blocker = f"leader action is not pending: {action_id}"
        elif action.get("kind") != "create_approvals":
            apply_blocker = "leader action requires explicit command"
        apply_command = f"agentdeck leader apply-action --action-id {action_id}" if can_apply else None
        return {
            "can_apply": can_apply,
            "preview_command": f"agentdeck leader action --action-id {action_id}",
            "controls": StateStore._leader_action_controls(
                action_id=action_id,
                apply_command=apply_command,
                explicit_command=action.get("command"),
                apply_blocker=apply_blocker,
            ),
            "apply_command": apply_command,
            "explicit_command": action.get("command"),
            "apply_blocker": apply_blocker,
        }

    @staticmethod
    def _leader_action_controls(
        *, action_id: str, apply_command: object, explicit_command: object, apply_blocker: object
    ) -> list[dict[str, Any]]:
        return [
            {
                "kind": "preview",
                "label": "Preview Leader action",
                "command": f"agentdeck leader action --action-id {action_id}",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "apply",
                "label": "Apply safe Leader action",
                "command": apply_command,
                "safety": "safe_apply",
                "enabled": apply_command is not None,
                "blocker": apply_blocker,
            },
            {
                "kind": "explicit",
                "label": "Run explicit command",
                "command": explicit_command,
                "safety": "explicit_runtime",
                "enabled": explicit_command is not None,
                "blocker": None,
            },
        ]

    def apply_leader_action(self, action_id: str) -> dict[str, Any]:
        state = self.load()
        action = next((item for item in state.get("leader_actions", []) if item.get("action_id") == action_id), None)
        if action is None:
            raise KeyError(action_id)
        if action.get("status") != "pending":
            raise ValueError(f"leader action is not pending: {action_id}")
        if action.get("kind") != "create_approvals":
            raise PermissionError(f"leader action requires explicit command: {action_id}")
        plan_id = str(action.get("plan_id"))
        approvals = self._create_approvals_from_plan_state(state, plan_id)
        action["status"] = "applied"
        action["applied_at"] = utc_now()
        self.save(state)
        return {
            "action": action,
            "result": {
                "plan_id": plan_id,
                "count": len(approvals),
                "approvals": approvals,
            },
        }

    def create_approvals_from_plan(self, plan_id: str) -> list[dict[str, Any]]:
        state = self.load()
        approvals = self._create_approvals_from_plan_state(state, plan_id)
        self.save(state)
        return approvals

    def create_chat_assignment_approval(self, agent_id: str, role: str, task: str) -> dict[str, Any]:
        state = self.load()
        approval = {
            "approval_id": new_id("apv"),
            "plan_id": None,
            "step": 1,
            "agent_id": agent_id,
            "role": role,
            "task": task,
            "risk": "human_requested",
            "status": "pending",
            "source": "leader_chat_task_assignment",
            "created_at": utc_now(),
        }
        state.setdefault("approvals", []).append(approval)
        self.save(state)
        return approval

    def _create_approvals_from_plan_state(self, state: dict[str, Any], plan_id: str) -> list[dict[str, Any]]:
        plan_record = next((plan for plan in state.get("plans", []) if plan.get("plan_id") == plan_id), None)
        if plan_record is None:
            raise KeyError(plan_id)
        existing = [item for item in state.setdefault("approvals", []) if item.get("plan_id") == plan_id]
        if existing:
            return existing
        plan_body = plan_record.get("plan", {})
        steps = plan_body.get("steps", []) if isinstance(plan_body, dict) else []
        approvals = []
        for step in steps:
            if not isinstance(step, dict) or not step.get("requires_approval", False):
                continue
            approval = {
                "approval_id": new_id("apv"),
                "plan_id": plan_id,
                "step": step.get("step"),
                "agent_id": step.get("agent_id"),
                "role": step.get("role"),
                "task": step.get("task"),
                "risk": step.get("risk"),
                "status": "pending",
                "created_at": utc_now(),
            }
            approvals.append(approval)
        state.setdefault("approvals", []).extend(approvals)
        return approvals

    def list_approvals(self) -> list[dict[str, Any]]:
        return list(self.load().get("approvals", []))

    def approval_by_id(self, approval_id: str) -> dict[str, Any]:
        for approval in self.load().get("approvals", []):
            if approval.get("approval_id") == approval_id:
                return approval
        raise KeyError(approval_id)

    def decide_approval(self, approval_id: str, status: str, reason: str | None = None) -> dict[str, Any]:
        state = self.load()
        approval = next((item for item in state.setdefault("approvals", []) if item.get("approval_id") == approval_id), None)
        if approval is None:
            raise KeyError(approval_id)
        approval["status"] = status
        approval["decided_at"] = utc_now()
        if reason:
            approval["reason"] = reason
        self.save(state)
        return approval

    def mark_approval_dispatched(self, approval_id: str, message_id: str, attempt_id: str, job_id: str) -> dict[str, Any]:
        state = self.load()
        approval = next((item for item in state.setdefault("approvals", []) if item.get("approval_id") == approval_id), None)
        if approval is None:
            raise KeyError(approval_id)
        approval["status"] = "dispatched"
        approval["message_id"] = message_id
        approval["attempt_id"] = attempt_id
        approval["job_id"] = job_id
        approval["dispatched_at"] = utc_now()
        self.save(state)
        return approval

    def create_dispatch_records(
        self,
        from_actor: str,
        to_agent: str,
        task: str,
        prompt: str,
        pane_id: str,
        prompt_skill_context: dict[str, Any] | None = None,
        message_id: str | None = None,
        worktree_path: str | None = None,
        worktree_branch: str | None = None,
        worktree_base_branch: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        state = self.load()
        message = {
            "message_id": message_id or new_id("msg"),
            "from_actor": from_actor,
            "to_agent": to_agent,
            "task": task,
            "prompt": prompt,
            "status": "dispatched",
            "created_at": utc_now(),
            "worktree_path": worktree_path,
            "worktree_branch": worktree_branch,
            "worktree_base_branch": worktree_base_branch,
        }
        if prompt_skill_context is not None:
            message["prompt_skill_context"] = self._plan_skill_context(prompt_skill_context)
        attempt = {
            "attempt_id": new_id("att"),
            "message_id": message["message_id"],
            "agent_id": to_agent,
            "status": "dispatched",
            "created_at": utc_now(),
        }
        job = {
            "job_id": new_id("job"),
            "message_id": message["message_id"],
            "attempt_id": attempt["attempt_id"],
            "agent_id": to_agent,
            "pane_id": pane_id,
            "status": "dispatched",
            "created_at": utc_now(),
        }
        inbox_item = {
            "inbox_id": new_id("inb"),
            "event_type": "task_request",
            "message_id": message["message_id"],
            "attempt_id": attempt["attempt_id"],
            "job_id": job["job_id"],
            "from_actor": from_actor,
            "to_agent": to_agent,
            "task": task,
            "status": "pending",
            "created_at": utc_now(),
        }
        state.setdefault("messages", []).append(message)
        state.setdefault("attempts", []).append(attempt)
        state.setdefault("jobs", []).append(job)
        state.setdefault("inbox", {}).setdefault(to_agent, []).append(inbox_item)
        self.save(state)
        return {
            "message": message,
            "attempt": attempt,
            "job": job,
            "inbox_item": inbox_item,
        }

    def inbox_items(self, agent_id: str) -> list[dict[str, Any]]:
        return list(self.load().get("inbox", {}).get(agent_id, []))

    def record_reply(self, from_agent: str, message_id: str, text: str) -> dict[str, Any]:
        state = self.load()
        messages = state.setdefault("messages", [])
        message = next((item for item in messages if item.get("message_id") == message_id), None)
        if message is None:
            raise KeyError(message_id)
        attempt = next(
            (
                item
                for item in state.setdefault("attempts", [])
                if item.get("message_id") == message_id and item.get("agent_id") == from_agent
            ),
            None,
        )
        job = next(
            (
                item
                for item in state.setdefault("jobs", [])
                if item.get("message_id") == message_id and item.get("agent_id") == from_agent
            ),
            None,
        )
        reply = {
            "reply_id": new_id("rep"),
            "message_id": message_id,
            "attempt_id": attempt.get("attempt_id") if attempt else None,
            "job_id": job.get("job_id") if job else None,
            "from_agent": from_agent,
            "to_actor": message.get("from_actor", "user"),
            "text": text,
            "created_at": utc_now(),
        }
        state.setdefault("replies", []).append(reply)
        artifacts = self._artifacts_from_reply(reply, text)
        state.setdefault("artifacts", []).extend(artifacts)
        message["status"] = "replied"
        if attempt:
            attempt["status"] = "completed"
        if job:
            job["status"] = "completed"
        to_actor = str(message.get("from_actor", "user"))
        if to_actor != "user":
            state.setdefault("inbox", {}).setdefault(to_actor, []).append(
                {
                    "inbox_id": new_id("inb"),
                    "event_type": "task_reply",
                    "message_id": message_id,
                    "attempt_id": reply["attempt_id"],
                    "job_id": reply["job_id"],
                    "reply_id": reply["reply_id"],
                    "from_agent": from_agent,
                    "to_agent": to_actor,
                    "task": message.get("task", ""),
                    "status": "pending",
                    "created_at": utc_now(),
                }
            )
        self.save(state)
        return {**reply, "artifacts": artifacts}

    @classmethod
    def _artifacts_from_reply(cls, reply: dict[str, Any], text: str) -> list[dict[str, Any]]:
        output_path = cls._structured_reply_value(text, "full_output_path")
        if not output_path:
            return []
        return [
            {
                "artifact_id": new_id("art"),
                "message_id": reply.get("message_id"),
                "attempt_id": reply.get("attempt_id"),
                "job_id": reply.get("job_id"),
                "reply_id": reply.get("reply_id"),
                "from_agent": reply.get("from_agent"),
                "path": output_path,
                "kind": cls._artifact_kind(output_path),
                "status": "created",
                "created_at": utc_now(),
            }
        ]

    @staticmethod
    def _structured_reply_value(text: str, key: str) -> str | None:
        prefix = f"{key}:"
        lines = text.splitlines()
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.lower().startswith(prefix):
                value = stripped[len(prefix):].strip().strip("\"'`")
                if value:
                    return value
                # 真实 agent 会把值写在字段下一行（YAML 缩进续行）；只接受
                # 缩进行作为续行，顶格行是下一个字段而不是值。
                for follow in lines[index + 1:]:
                    if not follow.strip():
                        continue
                    if follow[:1] in (" ", "\t"):
                        continuation = follow.strip().strip("\"'`")
                        return continuation or None
                    break
                return None
        return None

    @staticmethod
    def _artifact_kind(path: str) -> str:
        suffix = Path(path).suffix.lower()
        if suffix in {".md", ".markdown"}:
            return "markdown"
        if suffix == ".json":
            return "json"
        if suffix in {".txt", ".log"}:
            return "text"
        if suffix == ".py":
            return "python"
        return "file"

    def ack_inbox_item(self, agent_id: str, inbox_id: str) -> dict[str, Any]:
        state = self.load()
        items = state.setdefault("inbox", {}).setdefault(agent_id, [])
        item = next((entry for entry in items if entry.get("inbox_id") == inbox_id), None)
        if item is None:
            raise KeyError(inbox_id)
        head = next((entry for entry in items if entry.get("status") == "pending"), None)
        if head is not None and head.get("inbox_id") != inbox_id:
            raise ValueError(f"inbox item is not head: {inbox_id}; head is {head['inbox_id']}")
        item["status"] = "acked"
        item["acked_at"] = utc_now()
        self.save(state)
        return item

    def trace(self, query_id: str) -> dict[str, Any]:
        state = self.load()
        message_id = self._resolve_message_id(state, query_id)
        if message_id is None:
            raise KeyError(query_id)
        message = next(item for item in state.get("messages", []) if item.get("message_id") == message_id)
        attempts = [item for item in state.get("attempts", []) if item.get("message_id") == message_id]
        jobs = [item for item in state.get("jobs", []) if item.get("message_id") == message_id]
        replies = [item for item in state.get("replies", []) if item.get("message_id") == message_id]
        artifacts = [item for item in state.get("artifacts", []) if item.get("message_id") == message_id]
        inbox_items = []
        for items in state.get("inbox", {}).values():
            inbox_items.extend(item for item in items if item.get("message_id") == message_id)
        return {
            "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
            "query_id": query_id,
            "message": self._trace_message(message),
            "plan": self._trace_plan_for_message(state, message_id),
            "attempts": [self._trace_attempt(item) for item in attempts],
            "jobs": [self._trace_job(item) for item in jobs],
            "replies": [self._trace_reply(item) for item in replies],
            "artifacts": [self._trace_artifact(item) for item in artifacts],
            "inbox_items": [self._trace_inbox_item(item) for item in inbox_items],
            "controls": self._trace_controls(query_id),
        }

    @staticmethod
    def _trace_controls(query_id: Any) -> list[dict[str, Any]]:
        trace_command = StateStore._trace_command(query_id)
        return [
            {
                "kind": "inspect",
                "label": "Inspect trace",
                "command": trace_command,
                "safety": "inspect",
                "enabled": trace_command is not None,
                "blocker": None if trace_command is not None else "requires trace id",
            }
        ]

    @staticmethod
    def _trace_plan_for_message(state: dict[str, Any], message_id: str) -> dict[str, Any] | None:
        approval = next(
            (item for item in state.get("approvals", []) if item.get("message_id") == message_id and item.get("plan_id")),
            None,
        )
        if approval is None:
            return None
        plan_id = approval.get("plan_id")
        plan = next((item for item in state.get("plans", []) if item.get("plan_id") == plan_id), None)
        if plan is None:
            return None
        body = plan.get("plan", {})
        steps = body.get("steps", []) if isinstance(body, dict) else []
        return {
            "plan_id": plan.get("plan_id"),
            "task": plan.get("task"),
            "status": plan.get("status"),
            "provider": plan.get("provider"),
            "provider_backend": plan.get("provider_backend")
            or leader_provider_backend(str(plan.get("provider") or "")),
            "provider_transport": plan.get("provider_transport")
            or leader_provider_transport(str(plan.get("provider") or "")),
            "leader_backend": plan.get("leader_backend")
            or leader_backend_identity(
                str(plan.get("provider") or ""),
                str(plan.get("model") or ""),
                bool(plan.get("dispatch_ready", False)),
            ),
            "leader_generation": StateStore._plan_leader_generation(
                plan["leader_generation"]
                if "leader_generation" in plan
                else _MISSING_PLAN_LEADER_GENERATION,
                provider=plan.get("provider"),
                model=plan.get("model"),
                step_count=len(steps) if isinstance(steps, list) else 0,
                plan=body,
            ),
            "planner_backend": copy.deepcopy(plan.get("planner_backend")),
            "orchestrator_backend": copy.deepcopy(plan.get("orchestrator_backend")),
            "planner_brief": copy.deepcopy(plan.get("planner_brief")),
            "model": plan.get("model"),
            "dispatch_ready": plan.get("dispatch_ready"),
            "skill_context": StateStore._plan_skill_context(plan.get("skill_context")),
            "step_count": len(steps) if isinstance(steps, list) else 0,
            "created_at": plan.get("created_at"),
        }

    @staticmethod
    def _trace_message(message: dict[str, Any]) -> dict[str, Any]:
        return {
            "message_id": message.get("message_id"),
            "from_actor": message.get("from_actor"),
            "to_agent": message.get("to_agent"),
            "task": message.get("task"),
            "prompt": message.get("prompt"),
            "prompt_skill_context": StateStore._plan_skill_context(message.get("prompt_skill_context")),
            "status": message.get("status"),
            "created_at": message.get("created_at"),
        }

    @staticmethod
    def _trace_attempt(attempt: dict[str, Any]) -> dict[str, Any]:
        return {
            "attempt_id": attempt.get("attempt_id"),
            "message_id": attempt.get("message_id"),
            "agent_id": attempt.get("agent_id"),
            "status": attempt.get("status"),
            "created_at": attempt.get("created_at"),
        }

    @staticmethod
    def _trace_job(job: dict[str, Any]) -> dict[str, Any]:
        return {
            "job_id": job.get("job_id"),
            "message_id": job.get("message_id"),
            "attempt_id": job.get("attempt_id"),
            "agent_id": job.get("agent_id"),
            "pane_id": job.get("pane_id"),
            "status": job.get("status"),
            "created_at": job.get("created_at"),
        }

    @staticmethod
    def _trace_reply(reply: dict[str, Any]) -> dict[str, Any]:
        return {
            "reply_id": reply.get("reply_id"),
            "message_id": reply.get("message_id"),
            "attempt_id": reply.get("attempt_id"),
            "job_id": reply.get("job_id"),
            "from_agent": reply.get("from_agent"),
            "to_actor": reply.get("to_actor"),
            "text": reply.get("text"),
            "created_at": reply.get("created_at"),
        }

    @staticmethod
    def _trace_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
        return {
            "artifact_id": artifact.get("artifact_id"),
            "message_id": artifact.get("message_id"),
            "attempt_id": artifact.get("attempt_id"),
            "job_id": artifact.get("job_id"),
            "reply_id": artifact.get("reply_id"),
            "from_agent": artifact.get("from_agent"),
            "path": artifact.get("path"),
            "kind": artifact.get("kind"),
            "status": artifact.get("status"),
            "created_at": artifact.get("created_at"),
        }

    @staticmethod
    def _trace_inbox_item(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "inbox_id": item.get("inbox_id"),
            "event_type": item.get("event_type"),
            "message_id": item.get("message_id"),
            "attempt_id": item.get("attempt_id"),
            "job_id": item.get("job_id"),
            "reply_id": item.get("reply_id"),
            "from_actor": item.get("from_actor"),
            "from_agent": item.get("from_agent"),
            "to_agent": item.get("to_agent"),
            "task": item.get("task"),
            "status": item.get("status"),
            "created_at": item.get("created_at"),
        }

    def _resolve_message_id(self, state: dict[str, Any], query_id: str) -> str | None:
        for item in state.get("messages", []):
            if item.get("message_id") == query_id:
                return str(item["message_id"])
        for collection in ("attempts", "jobs", "replies"):
            for item in state.get(collection, []):
                if query_id in {
                    item.get("attempt_id"),
                    item.get("job_id"),
                    item.get("reply_id"),
                }:
                    return str(item["message_id"])
        for items in state.get("inbox", {}).values():
            for item in items:
                if item.get("inbox_id") == query_id:
                    return str(item["message_id"])
        for item in state.get("artifacts", []):
            if item.get("artifact_id") == query_id:
                return str(item["message_id"])
        return None

    @staticmethod
    def _status_counts(items: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in items:
            status = str(item.get("status", "unknown"))
            counts[status] = counts.get(status, 0) + 1
        return counts

    @staticmethod
    def _protocol_summary_items(
        records: object,
        *,
        identity_field: str,
        group_field: str,
        item_fields: tuple[str, ...],
        transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        if not isinstance(records, list):
            raise ValueError("protocol summary source must be a list")
        prepared: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        identities: set[str] = set()
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise ValueError(f"protocol summary item at index {index} must be an object")
            identity = record.get(identity_field)
            created_at = record.get("created_at")
            group = record.get(group_field)
            if not isinstance(identity, str) or not isinstance(created_at, str) or not isinstance(group, str):
                raise ValueError(f"invalid protocol summary item at index {index}")
            if identity in identities:
                raise ValueError(f"duplicate {identity_field}: {identity}")
            identities.add(identity)
            for field in item_fields:
                value = record.get(field)
                if field == "decision" and (
                    value is None or isinstance(value, str) and bool(value.strip())
                ):
                    continue
                if field == "reason" and (
                    value is None or isinstance(value, str) and bool(value.strip())
                ):
                    continue
                if field == "sequence" and isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    continue
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"invalid protocol summary field: {field}")
            item = {field: record.get(field) for field in item_fields}
            prepared.append(transform(record) if transform is not None else item)
            counts[group] = counts.get(group, 0) + 1
        prepared.sort(key=lambda item: (str(item.get("created_at", "")), str(item.get(identity_field, ""))))
        return prepared[-20:], {key: counts[key] for key in sorted(counts)}

    @staticmethod
    def _agent_session_summaries(records: object) -> dict[str, Any]:
        capability_fields = (
            "structured_sessions", "streaming_updates", "structured_tools",
            "permission_requests", "resume_session", "observable_terminal",
        )

        def compact(record: dict[str, Any]) -> dict[str, Any]:
            for field in ("session_id", "agent_id", "provider", "transport", "workspace", "created_at", "updated_at"):
                if not isinstance(record.get(field), str) or not record[field].strip():
                    raise ValueError(f"invalid agent session field: {field}")
            if record.get("state") not in AGENT_SESSION_STATES:
                raise ValueError("invalid agent session state")
            transport = record.get("transport")
            if type(transport) is not str or transport not in TRANSPORT_KINDS:
                raise ValueError("invalid agent session transport")
            native_session_id = record.get("native_session_id")
            if native_session_id is not None and (
                not isinstance(native_session_id, str) or not native_session_id.strip()
            ):
                raise ValueError("invalid agent session native_session_id")
            capabilities = record.get("capabilities")
            if (
                not isinstance(capabilities, dict)
                or set(capabilities) != set(capability_fields)
                or any(type(capabilities[field]) is not bool for field in capability_fields)
            ):
                raise ValueError("invalid agent session capabilities")
            return {
                "session_id": record.get("session_id"),
                "agent_id": record.get("agent_id"),
                "provider": record.get("provider"),
                "transport": record.get("transport"),
                "state": record.get("state"),
                "capabilities": {field: capabilities.get(field) for field in capability_fields},
                "native_session_present": bool(record.get("native_session_id")),
                "workspace": record.get("workspace"),
                "created_at": record.get("created_at"),
                "updated_at": record.get("updated_at"),
            }

        items, by_state = StateStore._protocol_summary_items(
            records, identity_field="session_id", group_field="state", item_fields=(), transform=compact,
        )
        return {"count": len(records), "by_state": by_state, "items": items}

    @staticmethod
    def _protocol_turn_summaries(records: object) -> dict[str, Any]:
        if isinstance(records, list):
            for record in records:
                if isinstance(record, dict) and record.get("state") not in TURN_STATES:
                    raise ValueError("invalid protocol turn state")
        items, by_state = StateStore._protocol_summary_items(
            records, identity_field="turn_id", group_field="state",
            item_fields=("turn_id", "session_id", "message_id", "state", "created_at", "updated_at"),
        )
        return {"count": len(records), "by_state": by_state, "items": items}

    @staticmethod
    def _transport_update_summaries(records: object) -> dict[str, Any]:
        if isinstance(records, list):
            for record in records:
                if isinstance(record, dict) and record.get("kind") not in UPDATE_KINDS:
                    raise ValueError("invalid transport update kind")
                if isinstance(record, dict) and (
                    type(record.get("sequence")) is not int or record["sequence"] < 0
                ):
                    raise ValueError("invalid transport update sequence")
        items, by_kind = StateStore._protocol_summary_items(
            records, identity_field="update_id", group_field="kind",
            item_fields=("update_id", "session_id", "turn_id", "sequence", "kind", "created_at"),
        )
        return {"count": len(records), "by_kind": by_kind, "items": items}

    @staticmethod
    def _permission_request_summaries(records: object) -> dict[str, Any]:
        if isinstance(records, list):
            for record in records:
                if isinstance(record, dict) and record.get("status") not in PERMISSION_STATES:
                    raise ValueError("invalid permission request status")
        items, by_status = StateStore._protocol_summary_items(
            records, identity_field="permission_id", group_field="status",
            item_fields=(
                "permission_id", "session_id", "turn_id", "tool_name", "risk", "status", "decision", "created_at",
            ),
        )
        return {
            "count": len(records),
            "pending_count": by_status.get("pending", 0),
            "by_status": by_status,
            "items": items,
        }

    @staticmethod
    def _protocol_transition_summaries(records: object) -> dict[str, Any]:
        item_fields = (
            "transition_id", "entity_type", "entity_id", "from_state",
            "to_state", "reason", "created_at",
        )
        items, by_entity_type = StateStore._protocol_summary_items(
            records,
            identity_field="transition_id",
            group_field="entity_type",
            item_fields=item_fields,
        )
        return {
            "count": len(records),
            "by_entity_type": by_entity_type,
            "items": items,
        }

    @staticmethod
    def _plan_summaries(
        plans: list[dict[str, Any]],
        semantic_by_plan: Mapping[object, dict[str, Any] | None] | None = None,
    ) -> dict[str, Any]:
        semantic_by_plan = semantic_by_plan or {}
        items = []
        for plan in plans:
            body = plan.get("plan", {})
            steps = body.get("steps", []) if isinstance(body, dict) else []
            items.append(
                {
                    "plan_id": plan.get("plan_id"),
                    "task": plan.get("task"),
                    "status": plan.get("status"),
                    "provider": plan.get("provider"),
                    "provider_backend": plan.get("provider_backend")
                    or leader_provider_backend(str(plan.get("provider") or "")),
                    "provider_transport": plan.get("provider_transport")
                    or leader_provider_transport(str(plan.get("provider") or "")),
                    "leader_backend": plan.get("leader_backend")
                    or leader_backend_identity(
                        str(plan.get("provider") or ""),
                        str(plan.get("model") or ""),
                        bool(plan.get("dispatch_ready", False)),
                    ),
                    "leader_generation": StateStore._plan_leader_generation(
                        plan["leader_generation"]
                        if "leader_generation" in plan
                        else _MISSING_PLAN_LEADER_GENERATION,
                        provider=plan.get("provider"),
                        model=plan.get("model"),
                        step_count=len(steps) if isinstance(steps, list) else 0,
                        plan=body,
                    ),
                    "semantic_authority": copy.deepcopy(
                        semantic_by_plan.get(id(plan))
                    ),
                    "planner_backend": copy.deepcopy(plan.get("planner_backend")),
                    "orchestrator_backend": copy.deepcopy(
                        plan.get("orchestrator_backend")
                    ),
                    "planner_brief": copy.deepcopy(plan.get("planner_brief")),
                    "model": plan.get("model"),
                    "dispatch_ready": plan.get("dispatch_ready"),
                    "skill_context": StateStore._plan_skill_context(plan.get("skill_context")),
                    "step_count": len(steps) if isinstance(steps, list) else 0,
                    "created_at": plan.get("created_at"),
                }
            )
        return {"count": len(items), "items": items}

    @staticmethod
    def _mission_summaries(
        missions: list[dict[str, Any]],
        semantic_by_mission: Mapping[object, dict[str, Any] | None] | None = None,
    ) -> dict[str, Any]:
        semantic_by_mission = semantic_by_mission or {}
        items: list[dict[str, Any]] = []
        source_count = len(missions) if isinstance(missions, list) else -1
        if not isinstance(missions, list):
            missions = []
        for mission in missions:
            if not isinstance(mission, dict):
                continue
            mission_id = mission.get("mission_id")
            if not is_canonical_mission_id(mission_id):
                continue
            status = mission.get("status")
            provider = mission.get("provider")
            model = mission.get("model")
            step_count = mission.get("step_count")
            current_step = mission.get("current_step", 0)
            if (
                mission.get("schema_version") != MISSION_SCHEMA_VERSION
                or status not in MISSION_STATUSES
                or not isinstance(provider, str)
                or not isinstance(model, str)
                or not isinstance(step_count, int)
                or isinstance(step_count, bool)
                or not isinstance(current_step, int)
                or isinstance(current_step, bool)
                or current_step < 0
                or current_step > step_count
            ):
                continue
            selected_agents, invalid_agents = compact_mission_worker_entries(
                mission.get("selected_agents"), kind="selected_agents"
            )
            startup_actions, invalid_actions = compact_mission_worker_entries(
                mission.get("startup_actions"), kind="startup_actions"
            )
            selected_ids = [item["agent_id"] for item in selected_agents]
            action_ids = [item["agent_id"] for item in startup_actions]
            workers_ready = (
                not invalid_agents
                and not invalid_actions
                and len(selected_agents) >= 2
                and len(startup_actions) >= 2
                and selected_ids == action_ids
            )
            raw_can_start = mission.get("can_start") is True
            blockers, invalid_blockers = compact_mission_blockers(
                mission.get("blockers")
            )
            if invalid_blockers and MISSION_INVALID_BLOCKERS_BLOCKER not in blockers:
                blockers.append(MISSION_INVALID_BLOCKERS_BLOCKER)
            if (invalid_agents or invalid_actions or raw_can_start) and not workers_ready:
                if "invalid mission worker summaries" not in blockers:
                    blockers.append("invalid mission worker summaries")
            commands = mission_commands(mission_id)
            daemon_admission = mission.get("daemon_admission")
            if not isinstance(daemon_admission, dict):
                daemon_admission = {
                    "state": "not_confirmed",
                    "snapshot_hash": None,
                    "blocker": "Mission is not confirmed for daemon admission",
                    "recovery_command": commands["confirmation_command"],
                    "updated_at": mission.get("updated_at"),
                }
            daemon_admission, invalid_daemon_admission = compact_daemon_admission(
                daemon_admission,
                mission_id=mission_id,
                confirmation_command=commands["confirmation_command"],
                safe_updated_at=mission.get("updated_at"),
            )
            if (
                invalid_daemon_admission
                and _INVALID_DAEMON_ADMISSION_BLOCKER not in blockers
            ):
                blockers.append(_INVALID_DAEMON_ADMISSION_BLOCKER)
            authority_state = daemon_mission_authority_state(mission)
            if (
                authority_state == "incomplete"
                and DAEMON_MISSION_RESUME_BLOCKER not in blockers
            ):
                blockers.append(DAEMON_MISSION_RESUME_BLOCKER)
            items.append(
                {
                    "mission_id": mission_id,
                    "schema_version": mission.get("schema_version"),
                    "user_message": mission.get("user_message"),
                    "status": status,
                    "stop_reason": mission.get("stop_reason"),
                    "can_start": raw_can_start and workers_ready and not blockers,
                    "can_resume": (
                        status in {MISSION_STATUSES[4], MISSION_STATUSES[5]}
                        and not blockers
                        and authority_state in {"legacy", "admitted"}
                    ),
                    "blockers": blockers,
                    "provider": provider,
                    "model": model,
                    "leader_backend": leader_backend_identity(provider, model),
                    "plan_id": mission.get("plan_id"),
                    "plan_hash": mission.get("plan_hash"),
                    "semantic_authority": copy.deepcopy(
                        semantic_by_mission.get(id(mission))
                    ),
                    "workflow_run_id": mission.get("workflow_run_id"),
                    "current_step": current_step,
                    "step_count": step_count,
                    "timeout_seconds": mission.get("timeout_seconds"),
                    "selected_agents": selected_agents,
                    "startup_actions": startup_actions,
                    "created_at": mission.get("created_at"),
                    "updated_at": mission.get("updated_at"),
                    "confirmed_at": mission.get("confirmed_at"),
                    "completed_at": mission.get("completed_at"),
                    "daemon_admission": daemon_admission,
                    **commands,
                }
            )
        return {
            "count": source_count,
            "by_status": StateStore._status_counts(items),
            "latest_id": items[-1]["mission_id"] if items else None,
            "items": items,
        }

    @staticmethod
    def _semantic_authority_projection(
        plan_record: Mapping[str, object],
        mission: Mapping[str, object] | None,
    ) -> dict[str, Any] | None:
        plan = plan_record.get("plan")
        semantic_active, semantic_plan, present = semantic_mission_provenance_shape(
            plan, mission if mission is not None else {}
        )
        if not semantic_active:
            return None
        if not semantic_plan:
            raise ValueError("semantic ProjectView provenance invalid")
        validated = validated_compiled_semantic_plan(plan)
        authority = validated["semantic_authority"]
        authority_hash = semantic_authority_hash(authority)
        task_hashes = [
            "sha256:" + hashlib.sha256(step["task"].encode("utf-8")).hexdigest()
            for step in validated["steps"]
        ]
        state = "preview"
        if mission is not None:
            if (
                present != SEMANTIC_MISSION_COMPACT_FIELDS
                or mission.get("semantic_authority_schema_version")
                != authority["schema_version"]
                or mission.get("semantic_authority_hash") != authority_hash
                or mission.get("compiled_task_hashes") != task_hashes
                or type(mission.get("preview_generation")) is not int
                or mission.get("preview_generation") != 1
            ):
                raise ValueError("semantic ProjectView provenance invalid")
            confirmed_at = mission.get("confirmed_at")
            status = mission.get("status")
            if status == MISSION_STATUSES[0]:
                if confirmed_at is not None:
                    raise ValueError("semantic ProjectView provenance invalid")
            elif status in MISSION_STATUSES[1:]:
                if type(confirmed_at) is not str or not confirmed_at:
                    raise ValueError("semantic ProjectView provenance invalid")
                try:
                    parsed = datetime.fromisoformat(confirmed_at)
                except ValueError:
                    raise ValueError("semantic ProjectView provenance invalid") from None
                if parsed.tzinfo is None:
                    raise ValueError("semantic ProjectView provenance invalid")
                state = "frozen"
            else:
                raise ValueError("semantic ProjectView provenance invalid")
        compact = compact_semantic_authority(
            authority,
            state=state,
            compiled_step_count=len(validated["semantic_steps"]),
            blockers=[],
        )
        if set(compact) != set(PROJECT_VIEW_SEMANTIC_AUTHORITY_FIELDS):
            raise ValueError("semantic ProjectView provenance invalid")
        return compact

    @staticmethod
    def _semantic_authority_projections(
        plans: object, missions: object
    ) -> tuple[dict[object, dict[str, Any] | None], dict[object, dict[str, Any] | None]]:
        if type(plans) is not list:
            raise ValueError("semantic ProjectView provenance invalid")
        mission_rows = missions if type(missions) is list else []
        valid_plan_ids: set[str] = set()
        for plan in plans:
            if not isinstance(plan, dict):
                continue
            plan_id = plan.get("plan_id")
            if type(plan_id) is str and plan_id:
                valid_plan_ids.add(plan_id)
        missions_by_plan: dict[str, list[dict[str, Any]]] = {}
        for mission in mission_rows:
            if not isinstance(mission, dict):
                continue
            plan_id = mission.get("plan_id")
            if type(plan_id) is str and plan_id:
                missions_by_plan.setdefault(plan_id, []).append(mission)
        by_plan: dict[object, dict[str, Any] | None] = {}
        by_mission: dict[object, dict[str, Any] | None] = {}
        for plan in plans:
            if not isinstance(plan, dict):
                continue
            plan_id = plan.get("plan_id")
            valid_plan_id = type(plan_id) is str and bool(plan_id)
            linked_missions = missions_by_plan.get(plan_id, []) if valid_plan_id else []
            linked_compacts = [
                StateStore._semantic_authority_projection(plan, mission)
                for mission in linked_missions
            ]
            plan_compact = (
                linked_compacts[0]
                if linked_compacts
                else StateStore._semantic_authority_projection(plan, None)
            )
            by_plan[id(plan)] = plan_compact
            for mission, mission_compact in zip(linked_missions, linked_compacts):
                by_mission[id(mission)] = mission_compact
        for plan_id, linked_missions in missions_by_plan.items():
            if plan_id in valid_plan_ids:
                continue
            for mission in linked_missions:
                _active, _semantic_plan, present = semantic_mission_provenance_shape(
                    None, mission
                )
                if present:
                    raise ValueError("semantic ProjectView provenance invalid")
                by_mission[id(mission)] = None
        for mission in mission_rows:
            if isinstance(mission, dict) and id(mission) not in by_mission:
                by_mission[id(mission)] = None
        return by_plan, by_mission

    @staticmethod
    def _plan_skill_context(skill_context: Any) -> dict[str, Any]:
        if not isinstance(skill_context, dict):
            return {"count": 0, "by_agent": {}, "by_source": {}, "items": []}
        items = []
        for raw_item in skill_context.get("items", []):
            if not isinstance(raw_item, dict):
                continue
            items.append(
                {
                    "load_id": raw_item.get("load_id"),
                    "agent_id": raw_item.get("agent_id"),
                    "purpose": raw_item.get("purpose"),
                    "name": raw_item.get("name"),
                    "source": raw_item.get("source"),
                    "path": raw_item.get("path"),
                    "content_hash": raw_item.get("content_hash"),
                    "description": raw_item.get("description"),
                "required_tools": raw_item.get("required_tools")
                    if isinstance(raw_item.get("required_tools"), list)
                    else [],
                    "planning_guidance": raw_item.get("planning_guidance")
                    if isinstance(raw_item.get("planning_guidance"), list)
                    else [],
                    "risk": raw_item.get("risk"),
                    "created_at": raw_item.get("created_at"),
                    "show_command": raw_item.get("show_command"),
                    "reload_command": raw_item.get("reload_command"),
                }
            )
        by_agent = skill_context.get("by_agent") if isinstance(skill_context.get("by_agent"), dict) else {}
        by_source = skill_context.get("by_source") if isinstance(skill_context.get("by_source"), dict) else {}
        return {
            "count": len(items),
            "by_agent": dict(by_agent),
            "by_source": dict(by_source),
            "items": items,
        }

    def _approval_summaries(self, approvals: list[dict[str, Any]]) -> dict[str, Any]:
        counts = self._status_counts(approvals)
        items = [
            {
                "approval_id": approval.get("approval_id"),
                "plan_id": approval.get("plan_id"),
                "step_index": approval.get("step_index"),
                "agent_id": approval.get("agent_id"),
                "task": approval.get("task"),
                "status": approval.get("status"),
                "message_id": approval.get("message_id"),
                "job_id": approval.get("job_id"),
            }
            for approval in approvals
        ]
        return {
            "count": len(items),
            "pending": counts.get("pending", 0),
            "approved": counts.get("approved", 0),
            "rejected": counts.get("rejected", 0),
            "dispatched": counts.get("dispatched", 0),
            "by_status": counts,
            "items": items,
        }

    def _message_summaries(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "count": len(messages),
            "by_status": self._status_counts(messages),
            "items": [
                {
                    "message_id": message.get("message_id"),
                    "from_actor": message.get("from_actor"),
                    "to_agent": message.get("to_agent"),
                    "task": message.get("task"),
                    "status": message.get("status"),
                    "created_at": message.get("created_at"),
                    "trace_command": self._trace_command(message.get("message_id")),
                    "prompt_skill_context": StateStore._plan_skill_context(message.get("prompt_skill_context")),
                    "worktree_path": message.get("worktree_path"),
                    "worktree_branch": message.get("worktree_branch"),
                    "worktree_base_branch": message.get("worktree_base_branch"),
                }
                for message in messages
            ],
        }

    def _job_summaries(self, jobs: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "count": len(jobs),
            "by_status": self._status_counts(jobs),
            "items": [
                {
                    "job_id": job.get("job_id"),
                    "message_id": job.get("message_id"),
                    "agent_id": job.get("agent_id"),
                    "status": job.get("status"),
                    "created_at": job.get("created_at"),
                    "trace_command": self._trace_command(job.get("job_id")),
                }
                for job in jobs
            ],
        }

    def _reply_summaries(self, replies: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "count": len(replies),
            "items": [
                {
                    "reply_id": reply.get("reply_id"),
                    "message_id": reply.get("message_id"),
                    "job_id": reply.get("job_id"),
                    "from_agent": reply.get("from_agent"),
                    "to_actor": reply.get("to_actor"),
                    "created_at": reply.get("created_at"),
                    "trace_command": self._trace_command(reply.get("reply_id")),
                }
                for reply in replies
            ],
        }

    def _release_summaries(self, releases: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "count": len(releases),
            "items": [
                {
                    "release_id": release.get("release_id"),
                    "round": release.get("round"),
                    "status": release.get("status"),
                    "review_gate_status": release.get("review_gate_status"),
                    "artifact_count": release.get("artifact_count"),
                    "review_reply_count": release.get("review_reply_count"),
                    "code_reviewer_id": release.get("code_reviewer_id"),
                    "round_reviewer_id": release.get("round_reviewer_id"),
                    "code_review_reply_id": release.get("code_review_reply_id"),
                    "round_review_reply_id": release.get("round_review_reply_id"),
                    "created_at": release.get("created_at"),
                    "trace_command": self._trace_command(release.get("round_review_reply_id")),
                }
                for release in releases
            ],
        }

    def artifact_summaries(self, artifacts: list[dict[str, Any]]) -> dict[str, Any]:
        by_kind: dict[str, int] = {}
        for artifact in artifacts:
            kind = str(artifact.get("kind", "unknown"))
            by_kind[kind] = by_kind.get(kind, 0) + 1
        return {
            "count": len(artifacts),
            "by_status": self._status_counts(artifacts),
            "by_kind": by_kind,
            "items": [
                {
                    "artifact_id": artifact.get("artifact_id"),
                    "message_id": artifact.get("message_id"),
                    "job_id": artifact.get("job_id"),
                    "reply_id": artifact.get("reply_id"),
                    "from_agent": artifact.get("from_agent"),
                    "path": artifact.get("path"),
                    "kind": artifact.get("kind"),
                    "status": artifact.get("status"),
                    "created_at": artifact.get("created_at"),
                    "trace_command": self._trace_command(
                        artifact.get("message_id") or artifact.get("job_id") or artifact.get("reply_id")
                    ),
                }
                for artifact in artifacts
            ],
        }

    @staticmethod
    def _trace_command(trace_id: Any) -> str | None:
        if trace_id is None:
            return None
        return f"agentdeck trace --id {trace_id}"

    @staticmethod
    def _chat_turn_summaries(chat_turns: list[dict[str, Any]]) -> dict[str, Any]:
        by_mode: dict[str, int] = {}
        items = []
        for turn in chat_turns:
            mode = str(turn.get("mode", "unknown"))
            by_mode[mode] = by_mode.get(mode, 0) + 1
            items.append(
                {
                    "turn_id": turn.get("turn_id"),
                    "mode": turn.get("mode"),
                    "message": turn.get("message"),
                    "plan_id": turn.get("plan_id"),
                    "next_command": turn.get("next_command"),
                    "action_id": turn.get("action_id"),
                    "action_kind": turn.get("action_kind"),
                    "created_at": turn.get("created_at"),
                }
            )
        return {"count": len(items), "by_mode": by_mode, "items": items}

    @staticmethod
    def _leader_error_summaries(leader_errors: list[dict[str, Any]]) -> dict[str, Any]:
        by_mode: dict[str, int] = {}
        items = []
        for error in leader_errors:
            mode = str(error.get("mode", "unknown"))
            by_mode[mode] = by_mode.get(mode, 0) + 1
            items.append(
                {
                    "error_id": error.get("error_id"),
                    "mode": error.get("mode"),
                    "provider": error.get("provider"),
                    "model": error.get("model"),
                    "task": error.get("task"),
                    "error": error.get("error"),
                    "created_at": error.get("created_at"),
                }
            )
        return {"count": len(items), "by_mode": by_mode, "items": items}

    @staticmethod
    def _leader_action_summaries(leader_actions: list[dict[str, Any]]) -> dict[str, Any]:
        by_kind: dict[str, int] = {}
        by_status: dict[str, int] = {}
        pending_actions = [item for item in leader_actions if item.get("status") == "pending"]
        recommended_action_id = pending_actions[-1].get("action_id") if pending_actions else None
        items = []
        for action in leader_actions:
            kind = str(action.get("kind", "unknown"))
            status = str(action.get("status", "unknown"))
            by_kind[kind] = by_kind.get(kind, 0) + 1
            by_status[status] = by_status.get(status, 0) + 1
            items.append(
                {
                    "action_id": action.get("action_id"),
                    "kind": action.get("kind"),
                    "status": action.get("status"),
                    "requires_confirmation": action.get("requires_confirmation"),
                    "plan_id": action.get("plan_id"),
                    "approval_id": action.get("approval_id"),
                    "agent_id": action.get("agent_id"),
                    "message_id": action.get("message_id"),
                    "command": action.get("command"),
                    "reason": action.get("reason"),
                    **StateStore._leader_action_detail_fields(action),
                    "is_recommended": action.get("action_id") == recommended_action_id,
                    "created_at": action.get("created_at"),
                }
            )
        return {
            "count": len(items),
            "by_kind": by_kind,
            "by_status": by_status,
            "recommended_action_id": recommended_action_id,
            "items": items,
        }

    @staticmethod
    def _skill_load_summaries(skill_loads: list[dict[str, Any]]) -> dict[str, Any]:
        by_agent: dict[str, int] = {}
        by_source: dict[str, int] = {}
        items = []
        for load in skill_loads:
            agent_id = str(load.get("agent_id") or "unknown")
            source = str(load.get("source") or "unknown")
            name = str(load.get("name") or "")
            purpose = str(load.get("purpose") or "")
            by_agent[agent_id] = by_agent.get(agent_id, 0) + 1
            by_source[source] = by_source.get(source, 0) + 1
            item = {
                "load_id": load.get("load_id"),
                "agent_id": load.get("agent_id"),
                "purpose": load.get("purpose"),
                "name": load.get("name"),
                "source": load.get("source"),
                "path": load.get("path"),
                "content_hash": load.get("content_hash"),
                "description": load.get("description"),
                "required_tools": load.get("required_tools") if isinstance(load.get("required_tools"), list) else [],
                "planning_guidance": load.get("planning_guidance")
                if isinstance(load.get("planning_guidance"), list)
                else [],
                "risk": load.get("risk"),
                "created_at": load.get("created_at"),
                "show_command": f"agentdeck skills show --name {shlex.quote(name)}" if name else None,
                "reload_command": StateStore._skill_reload_command(name, agent_id, purpose),
            }
            items.append(item)
        return {
            "count": len(items),
            "by_agent": by_agent,
            "by_source": by_source,
            "items": items,
        }

    @staticmethod
    def _skill_reload_command(name: str, agent_id: str, purpose: str) -> str | None:
        if not name:
            return None
        command = f"agentdeck skills load --name {shlex.quote(name)} --agent {shlex.quote(agent_id)}"
        if purpose:
            command = f"{command} --purpose {shlex.quote(purpose)}"
        return command

    def _inbox_summary(self, inbox: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        by_agent = {agent_id: len(items) for agent_id, items in inbox.items()}
        all_items = [item for items in inbox.values() for item in items]
        return {
            "total": len(all_items),
            "by_agent": by_agent,
            "by_status": self._status_counts(all_items),
            "heads": {agent_id: self._inbox_head_summary(items) for agent_id, items in inbox.items()},
        }

    @staticmethod
    def _inbox_head_summary(items: list[dict[str, Any]]) -> dict[str, Any] | None:
        head = next((item for item in items if item.get("status") == "pending"), None)
        if head is None:
            return None
        return {
            "inbox_id": head.get("inbox_id"),
            "event_type": head.get("event_type"),
            "message_id": head.get("message_id"),
            "reply_id": head.get("reply_id"),
            "from_actor": head.get("from_actor"),
            "from_agent": head.get("from_agent"),
            "to_agent": head.get("to_agent"),
            "task": head.get("task"),
            "status": head.get("status"),
            "created_at": head.get("created_at"),
        }

    def _recovery_summary(self, state: dict[str, Any], config: ProjectConfig) -> dict[str, Any]:
        approvals = state.get("approvals", [])
        leader_actions = state.get("leader_actions", [])
        leader_errors = state.get("leader_errors", [])
        agent_bindings = state.get("agents", {})
        inbox_items = [item for items in state.get("inbox", {}).values() for item in items]
        pending_leader_actions = [item for item in leader_actions if item.get("status") == "pending"]
        pending_approvals = [item for item in approvals if item.get("status") == "pending"]
        approved_approvals = [item for item in approvals if item.get("status") == "approved"]
        pending_inbox_items = [item for item in inbox_items if item.get("status") == "pending"]
        waiting_reply_review = self._latest_waiting_reply_review(state)
        stale_agents = [
            agent_id
            for agent_id, binding in agent_bindings.items()
            if isinstance(binding, dict) and binding.get("status") == "stale"
        ]
        recent_events = [self._event_summary(event) for event in self.list_events(5)]
        summary = {
            "status": "idle",
            "reason": "no pending recovery action",
            "next_command": None,
            "recommended_action": None,
            "pending": {
                "leader_actions": len(pending_leader_actions),
                "approvals": len(pending_approvals),
                "approved_approvals": len(approved_approvals),
                "inbox_items": len(pending_inbox_items),
                "leader_errors": len(leader_errors),
                "runtime_stale": len(stale_agents),
                "reply_waiting": 1 if waiting_reply_review else 0,
            },
            "leader_action": None,
            "latest_event": recent_events[-1] if recent_events else None,
            "recent_events": recent_events,
        }
        if pending_leader_actions:
            action = pending_leader_actions[-1]
            detail = self._leader_action_detail_fields(action)
            next_command = detail.get("apply_command") or action.get("command")
            summary.update(
                {
                    "status": "action_required",
                    "reason": f"pending leader action: {action.get('kind')}",
                    "next_command": next_command,
                    "recommended_action": self._recommended_action(
                        label="Apply safe Leader action" if detail.get("can_apply") else "Run explicit Leader action",
                        command=next_command,
                        safety="safe_apply" if detail.get("can_apply") else "explicit_runtime",
                        requires_explicit_user=not bool(detail.get("can_apply")),
                        source="leader_action",
                        target_id=action.get("action_id"),
                    ),
                    "leader_action": {
                        "action_id": action.get("action_id"),
                        "kind": action.get("kind"),
                        "command": action.get("command"),
                        "can_apply": detail.get("can_apply"),
                        "apply_command": detail.get("apply_command"),
                        "apply_blocker": detail.get("apply_blocker"),
                    },
                }
            )
        elif approved_approvals:
            approval_id = approved_approvals[0].get("approval_id")
            next_command = f"agentdeck approval dispatch --approval-id {approval_id}"
            summary.update(
                {
                    "status": "dispatch_ready",
                    "reason": "approved approval is waiting for dispatch",
                    "next_command": next_command,
                    "recommended_action": self._recommended_action(
                        label="Dispatch approved task",
                        command=next_command,
                        safety="explicit_runtime",
                        requires_explicit_user=True,
                        source="approval",
                        target_id=approval_id,
                    ),
                }
            )
        elif pending_approvals:
            summary.update(
                {
                    "status": "approval_required",
                    "reason": "pending approvals require human decision",
                    "next_command": "agentdeck approval list",
                    "recommended_action": self._recommended_action(
                        label="Review approvals",
                        command="agentdeck approval list",
                        safety="inspect",
                        requires_explicit_user=False,
                        source="approval",
                        target_id=pending_approvals[0].get("approval_id"),
                    ),
                }
            )
        elif stale_agents:
            stale_agent_id = stale_agents[0]
            summary.update(
                {
                    "status": "runtime_stale",
                    "reason": "agent runtime binding is stale",
                    "next_command": "agentdeck agent refresh",
                    "recommended_action": self._recommended_action(
                        label="Refresh stale runtime",
                        command="agentdeck agent refresh",
                        safety="inspect",
                        requires_explicit_user=False,
                        source="runtime",
                        target_id=stale_agent_id,
                    ),
                }
            )
        elif pending_inbox_items:
            inbox_item = pending_inbox_items[0]
            agent_id = self._inbox_item_agent_id(state.get("inbox", {}), inbox_item)
            next_command = f"agentdeck inbox --agent {agent_id}" if agent_id else "agentdeck status"
            summary.update(
                {
                    "status": "inbox_pending",
                    "reason": "agent inbox has pending items",
                    "next_command": next_command,
                    "recommended_action": self._recommended_action(
                        label="Inspect pending inbox",
                        command=next_command,
                        safety="inspect",
                        requires_explicit_user=False,
                        source="inbox",
                        target_id=inbox_item.get("inbox_id"),
                    ),
                }
            )
        elif waiting_reply_review:
            agent_id = waiting_reply_review.get("agent_id")
            message_id = waiting_reply_review.get("message_id")
            next_command = f"agentdeck capture-reply --agent {agent_id} --message-id {message_id}"
            summary.update(
                {
                    "status": "reply_waiting",
                    "reason": waiting_reply_review.get("reason"),
                    "next_command": next_command,
                    "recommended_action": self._recommended_action(
                        label="Capture pending reply",
                        command=next_command,
                        safety="explicit_runtime",
                        requires_explicit_user=True,
                        source="reply",
                        target_id=message_id,
                    ),
                }
            )
        elif leader_errors:
            error = leader_errors[-1]
            summary.update(
                {
                    "status": "leader_error",
                    "reason": "leader error requires inspection",
                    "next_command": "agentdeck status",
                    "recommended_action": self._recommended_action(
                        label="Inspect Leader error",
                        command="agentdeck status",
                        safety="inspect",
                        requires_explicit_user=False,
                        source="leader_error",
                        target_id=error.get("error_id"),
                    ),
                }
            )
        elif provider_setup := self._leader_provider_setup_action(config):
            summary.update(
                {
                    "status": "provider_setup_required",
                    "reason": f"configured Leader provider is not ready: {config.leader.provider}",
                    "next_command": provider_setup["command"],
                    "recommended_action": self._recommended_action(
                        label="Inspect Leader provider setup",
                        command=provider_setup["command"],
                        safety="inspect",
                        requires_explicit_user=False,
                        source="provider_health",
                        target_id=config.leader.provider,
                    ),
                }
            )
        if waiting_reply_review:
            awaited_message_id = waiting_reply_review.get("message_id")
            awaited_reply_file = self.deck_dir / "replies" / f"{awaited_message_id}.reply.txt"
            summary["reply_file_ready"] = bool(awaited_message_id) and awaited_reply_file.exists()
        return summary

    @staticmethod
    def _leader_provider_setup_action(config: ProjectConfig) -> dict[str, Any] | None:
        required_env = {
            "deepseek": "DEEPSEEK_API_KEY",
            "openai-compatible": "AGENTDECK_LEADER_API_KEY",
        }.get(config.leader.provider)
        cli_command = {
            "codex-cli": "codex",
            "claude-cli": "claude",
        }.get(config.leader.provider)
        if cli_command is not None:
            if shutil.which(cli_command):
                return None
            return {"command": "agentdeck doctor", "missing_command": cli_command}
        if required_env is None or os.environ.get(required_env):
            return None
        return {"command": "agentdeck doctor", "missing_env": required_env}

    def _latest_waiting_reply_review(self, state: dict[str, Any]) -> dict[str, Any] | None:
        plans = state.get("plans", [])
        if not plans:
            return None
        latest_plan_id = plans[-1].get("plan_id") if isinstance(plans[-1], dict) else None
        if not latest_plan_id:
            return None
        try:
            review = self.leader_review(str(latest_plan_id))
        except KeyError:
            return None
        if review.get("next_action") != "wait_for_reply":
            return None
        if not review.get("agent_id") or not review.get("message_id"):
            return None
        return review

    @staticmethod
    def _inbox_item_agent_id(inbox: dict[str, list[dict[str, Any]]], item: dict[str, Any]) -> str | None:
        to_agent = item.get("to_agent")
        if to_agent:
            return str(to_agent)
        inbox_id = item.get("inbox_id")
        for agent_id, items in inbox.items():
            if any(candidate is item or candidate.get("inbox_id") == inbox_id for candidate in items):
                return str(agent_id)
        return None

    @staticmethod
    def _recommended_action(
        label: str,
        command: object,
        safety: str,
        requires_explicit_user: bool,
        source: str,
        target_id: object,
    ) -> dict[str, Any] | None:
        if not command:
            return None
        return {
            "label": label,
            "command": command,
            "safety": safety,
            "requires_explicit_user": requires_explicit_user,
            "source": source,
            "target_id": target_id,
        }

    @staticmethod
    def _event_summary(event: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_id": event.get("event_id"),
            "event_type": event.get("event_type"),
            "created_at": event.get("created_at"),
        }

    @staticmethod
    def _memory_context_summary(root: Path) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        by_scope: dict[str, int] = {}
        for scope, relative_path in (
            ("project", ".agentdeck/memory/project.md"),
            ("global", ".agentdeck/memory/global.md"),
        ):
            path = root / relative_path
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            encoded = text.encode("utf-8")
            preview = next((line for line in text.splitlines() if line.strip()), "")
            items.append(
                {
                    "scope": scope,
                    "path": relative_path,
                    "exists": True,
                    "line_count": len(text.splitlines()),
                    "byte_count": len(encoded),
                    "content_hash": f"sha256:{hashlib.sha256(encoded).hexdigest()}",
                    "preview": preview,
                }
            )
            by_scope[scope] = by_scope.get(scope, 0) + 1
        return {"count": len(items), "by_scope": by_scope, "items": items}

    @staticmethod
    def _conversation_summary(state: dict[str, Any]) -> dict[str, Any]:
        collections = {
            key: state.get(key, [])
            for key in (
                "conversation_sessions",
                "conversation_turns",
                "conversation_preview_bindings",
            )
        }
        transitions = state.get("conversation_state_transitions", [])
        projection = validate_conversation_history(collections, transitions)
        sessions = collections["conversation_sessions"]
        turns = collections["conversation_turns"]
        previews = collections["conversation_preview_bindings"]
        latest_session = sessions[-1] if sessions else None
        latest_turn = turns[-1] if turns else None
        latest_conversation_id = (
            latest_session.get("conversation_id")
            if isinstance(latest_session, dict)
            else None
        )
        latest_turn_id = (
            latest_turn.get("turn_id") if isinstance(latest_turn, dict) else None
        )
        pending_preview_ids = set(
            projection["pending_preview_by_conversation"].values()
        )
        pending_preview = next(
            (
                {
                    "preview_id": item.get("preview_id"),
                    "preview_kind": item.get("preview_kind"),
                    "expires_at": item.get("expires_at"),
                }
                for item in reversed(previews)
                if isinstance(item, dict)
                and item.get("preview_id") in pending_preview_ids
            ),
            None,
        )
        ownership = [
            {"agent_id": agent_id, "state": ownership_state}
            for agent_id, ownership_state in sorted(
                projection["ownership_states"].items()
            )
        ]
        outbox = state.get("conversation_event_outbox", [])
        outbox_count = len(outbox) if isinstance(outbox, list) else 0
        reconciliation_decisions = state.get("worker_reconciliation_decisions", [])
        if type(reconciliation_decisions) is not list or any(
            type(item) is not dict for item in reconciliation_decisions
        ):
            raise ValueError("worker reconciliation decisions are invalid")
        ambiguous_workers = sorted(
            {
                str(item.get("agent_id"))
                for item in reconciliation_decisions
                if item.get("status") == "ambiguous"
                and type(item.get("agent_id")) is str
            }
        )
        blockers = ["conversation event outbox pending"] if outbox_count else []
        blockers.extend(
            f"worker reconciliation ambiguous: {agent_id}"
            for agent_id in ambiguous_workers
        )
        return {
            "session_count": len(sessions),
            "turn_count": len(turns),
            "preview_count": len(previews),
            "transition_count": len(transitions),
            "latest_conversation_id": latest_conversation_id,
            "latest_conversation_state": projection["conversation_states"].get(
                latest_conversation_id
            ),
            "latest_turn_id": latest_turn_id,
            "latest_turn_state": projection["turn_states"].get(latest_turn_id),
            "pending_preview": pending_preview,
            "ownership": ownership,
            "outbox_count": outbox_count,
            "blockers": blockers,
        }

    @staticmethod
    def _mission_recovery_summary(state: dict[str, Any]) -> dict[str, Any]:
        missions = [item for item in state.get("missions", []) if isinstance(item, dict)]
        if not missions:
            return {
                "mode": "mission_recovery",
                "mission_id": None,
                "classification": "terminal",
                "progress": {"completed": 0, "total": 0},
                "completed_steps": [],
                "recent_results": [],
                "active_step": None,
                "wait_reason": "no Mission requires recovery",
                "decision": {"kind": "none", "attempt_id": None, "controls": []},
                "trace_commands": [],
                "workspace_control": {
                    "kind": "inspect",
                    "label": "Open workbench",
                    "command": "agentdeck workbench",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
            }
        from .daemon.recovery import validate_recovery_record

        validated_decisions: list[dict[str, object]] = []
        for item in state.get("recovery_decisions", []):
            try:
                validated_decisions.append(validate_recovery_record(item))
            except ValueError:
                continue
        missions_by_id = {
            item.get("mission_id"): item
            for item in missions
            if isinstance(item.get("mission_id"), str)
        }
        decision = validated_decisions[-1] if validated_decisions else {}
        mission = missions_by_id.get(decision.get("mission_id"))
        if mission is None:
            active = [
                item for item in missions
                if item.get("status") not in {"completed", "stopped", "interrupted"}
            ]
            mission = (active or missions)[-1]
            decision = {}
        mission_id = mission.get("mission_id")
        classification = str(decision.get("classification") or (
            "terminal" if mission.get("status") in {"completed", "stopped", "interrupted"}
            else "blocked"
        ))
        reason = str(decision.get("reason") or "Mission recovery evidence is unavailable")
        attempt_id = decision.get("attempt_id")
        if classification == "waiting_human" and "permission" in reason.lower():
            decision_kind = "permission"
        elif classification == "resumable":
            decision_kind = "resume"
        elif classification == "terminal":
            decision_kind = "none"
        else:
            decision_kind = "inspect"
        completed = mission.get("current_step", 0)
        total = mission.get("step_count", 0)
        if type(completed) is not int or completed < 0:
            completed = 0
        if type(total) is not int or total < completed:
            total = completed
        status_command = (
            f"agentdeck mission status --mission-id {mission_id}"
            if isinstance(mission_id, str)
            else "agentdeck status"
        )
        snapshot = mission.get("execution_snapshot")
        snapshot_mission = snapshot.get("mission") if isinstance(snapshot, dict) else None
        raw_steps = snapshot_mission.get("steps") if isinstance(snapshot_mission, dict) else []
        steps: list[dict[str, object]] = []
        for item in raw_steps:
            expected_position = len(steps) + 1
            if (
                not isinstance(item, dict)
                or item.get("step_id") != f"step_{expected_position}"
                or item.get("position") != expected_position
                or not isinstance(item.get("agent_id"), str)
                or not item.get("agent_id")
                or not isinstance(item.get("role"), str)
                or not item.get("role")
            ):
                break
            steps.append(
                {
                    "step_id": item["step_id"],
                    "position": item["position"],
                    "agent_id": item["agent_id"],
                    "role": item["role"],
                    **(
                        {"semantic_step_hash": item["semantic_step_hash"]}
                        if "semantic_step_hash" in item
                        else {}
                    ),
                }
            )
        # Recovery progress only claims the compact frozen step facts that can
        # be proven. Legacy foreground Missions may have current_step history
        # without an execution snapshot; do not fabricate their step lineage.
        completed = min(completed, len(steps))
        completed_steps = copy.deepcopy(steps[:completed])
        active_step = (
            copy.deepcopy(steps[completed])
            if classification != "terminal" and completed < len(steps)
            else None
        )
        attempts: dict[object, dict[str, Any]] = {}
        for item in state.get("mission_attempts", []):
            if not isinstance(item, dict) or item.get("mission_id") != mission_id:
                continue
            try:
                validated_attempt = _validate_mission_attempt_record(item)
            except ValueError:
                continue
            matching_steps = [
                step for step in steps
                if step.get("step_id") == validated_attempt.get("step_id")
                and step.get("agent_id") == validated_attempt.get("agent_id")
            ]
            semantic_lineage = (
                "semantic_step_hash" in validated_attempt
                or any("semantic_step_hash" in step for step in steps)
            )
            if semantic_lineage and (
                len(matching_steps) != 1
                or validated_attempt.get("semantic_step_hash")
                != matching_steps[0].get("semantic_step_hash")
            ):
                raise ValueError("semantic recovery lineage is invalid")
            attempts[validated_attempt["attempt_id"]] = validated_attempt
        recent_results: list[dict[str, object]] = []
        from .daemon.recovery import validate_mission_reply_evidence_record

        for raw_reply in state.get("mission_worker_replies", []):
            try:
                reply = validate_mission_reply_evidence_record(raw_reply)
            except ValueError:
                continue
            if reply.get("mission_id") != mission_id or reply.get("state") != "validated":
                continue
            reply_attempt_id = reply.get("attempt_id")
            attempt = attempts.get(reply_attempt_id)
            if attempt is None or attempt.get("dispatch_key") != reply.get("dispatch_key"):
                continue
            if reply.get("semantic_step_hash") != attempt.get("semantic_step_hash"):
                raise ValueError("semantic recovery lineage is invalid")
            handoff = reply.get("canonical_handoff")
            if not isinstance(handoff, dict):
                continue
            summary = handoff.get("summary")
            verification = handoff.get("verification")
            artifacts = handoff.get("artifacts")
            recent_results.append(
                {
                    "attempt_id": reply_attempt_id,
                    "step_id": attempt.get("step_id"),
                    "agent_id": attempt.get("agent_id"),
                    "state": "validated",
                    "summary_hash": canonical_snapshot_hash(
                        {"summary": summary if isinstance(summary, str) else None}
                    ),
                    "verification_hash": canonical_snapshot_hash(
                        {"verification": verification if isinstance(verification, str) else None}
                    ),
                    "artifact_count": len(artifacts) if isinstance(artifacts, list) else 0,
                    **(
                        {"semantic_step_hash": reply["semantic_step_hash"]}
                        if "semantic_step_hash" in reply
                        else {}
                    ),
                }
            )
        recent_results = recent_results[-3:]
        trace_ids = [
            item.get("attempt_id") for item in recent_results
            if isinstance(item.get("attempt_id"), str)
        ]
        if isinstance(attempt_id, str) and attempt_id not in trace_ids:
            trace_ids.append(attempt_id)
        trace_commands = [f"agentdeck trace --id {item}" for item in trace_ids]
        permission_bindings = [
            item
            for item in state.get("mission_permission_bindings", [])
            if isinstance(item, dict)
            and item.get("mission_id") == mission_id
            and item.get("attempt_id") == attempt_id
            and isinstance(item.get("permission_id"), str)
        ]
        permission_id = (
            permission_bindings[0]["permission_id"]
            if len(permission_bindings) == 1
            else None
        )
        if (
            decision_kind == "permission"
            and isinstance(mission_id, str)
            and isinstance(attempt_id, str)
            and isinstance(permission_id, str)
        ):
            decision_controls = [
                {
                    "kind": "permission_preview",
                    "label": "Preview pending permission",
                    "command": (
                        f"agentdeck daemon permission-preview --mission-id {mission_id} "
                        f"--attempt-id {attempt_id} --permission-id {permission_id} "
                        "--decision approved"
                    ),
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                }
            ]
        elif decision_kind == "resume" and isinstance(mission_id, str):
            decision_controls = [
                {
                    "kind": "resume_preview",
                    "label": "Preview Mission resume",
                    "command": f"agentdeck mission resume --mission-id {mission_id} --confirm",
                    "safety": "explicit_user",
                    "enabled": True,
                    "blocker": None,
                }
            ]
        elif decision_kind == "none":
            decision_controls = []
        else:
            decision_controls = [
                {
                    "kind": "inspect",
                    "label": "Inspect Mission recovery",
                    "command": status_command,
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                }
            ]
        return {
            "mode": "mission_recovery",
            "mission_id": mission_id,
            "classification": classification,
            "progress": {"completed": completed, "total": total},
            "completed_steps": completed_steps,
            "recent_results": recent_results,
            "active_step": active_step,
            "wait_reason": reason,
            "decision": {
                "kind": decision_kind,
                "attempt_id": attempt_id if isinstance(attempt_id, str) else None,
                "controls": decision_controls,
            },
            "trace_commands": trace_commands,
            "workspace_control": {
                "kind": "inspect",
                "label": "Open workbench",
                "command": "agentdeck workbench",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
        }

    def project_view(self, config: ProjectConfig) -> ProjectView:
        state = self.load()
        # Preserve source-row validation precedence before cross-record lineage
        # checks, then validate every transition before deriving current state.
        self._agent_session_summaries(state.get("agent_sessions", []))
        self._protocol_turn_summaries(state.get("protocol_turns", []))
        self._transport_update_summaries(state.get("transport_updates", []))
        self._permission_request_summaries(state.get("permission_requests", []))
        self._validate_protocol_lineage(state)
        current_states = self._validate_protocol_transition_history(state)
        sessions = copy.deepcopy(state.get("agent_sessions", []))
        turns = copy.deepcopy(state.get("protocol_turns", []))
        permissions = copy.deepcopy(state.get("permission_requests", []))
        for entity_type, records, identity_field, state_field in (
            ("session", sessions, "session_id", "state"),
            ("turn", turns, "turn_id", "state"),
            ("permission", permissions, "permission_id", "status"),
        ):
            for record in records:
                identity = record.get(identity_field)
                record[state_field] = current_states.get(
                    (entity_type, identity), record.get(state_field)
                )
        agent_sessions = self._agent_session_summaries(sessions)
        protocol_turns = self._protocol_turn_summaries(turns)
        transport_updates = self._transport_update_summaries(state.get("transport_updates", []))
        permission_requests = self._permission_request_summaries(permissions)
        protocol_state_transitions = self._protocol_transition_summaries(
            state.get("protocol_state_transitions", [])
        )
        bindings = state.get("agents", {})
        agents = []
        for agent in config.agents:
            binding = bindings.get(
                agent.agent_id,
                {
                    "agent_id": agent.agent_id,
                    "pane_id": None,
                    "session_name": None,
                    "cwd": None,
                    "status": "configured",
                },
            )
            agents.append(
                {
                    "agent_id": agent.agent_id,
                    "role": agent.role,
                    "provider": agent.provider,
                    "command": agent.command,
                    "workspace_mode": agent.workspace_mode,
                    "role_prompt": agent.role_prompt,
                    "runtime": binding,
                }
            )
        leader = {
            "agent_id": config.leader.agent_id,
            "provider": config.leader.provider,
            "model": config.leader.model,
            "approval_mode": config.leader.approval_mode,
        }
        leader["leader_backend"] = leader_backend_identity(config.leader.provider, config.leader.model)
        leader["coordination_roles"] = leader_coordination_roles(config.leader.provider, config.leader.model)
        daemon_record = state.get("daemon_runtime")
        daemon_state = (
            str(daemon_record.get("state"))
            if isinstance(daemon_record, dict)
            else "stopped"
        )
        daemon_blockers = [] if daemon_state in {"ready", "busy", "idle_grace"} else ["project daemon is not running"]
        daemon_summary = {
            "state": daemon_state,
            "health": "unknown" if daemon_state in {"ready", "busy", "idle_grace"} else "unavailable",
            "client_count": 0,
            "controller_present": controller_lease_is_active(
                state.get("controller_lease"), now=datetime.now(timezone.utc)
            ),
            "idle_exit_pending": daemon_state == "idle_grace",
            "protocol_version": "daemon-rpc/v1",
            "compatibility": "unverified",
            "blockers": (
                ["daemon health requires endpoint verification"]
                if daemon_state in {"ready", "busy", "idle_grace"}
                else daemon_blockers
            ),
        }
        from .daemon.service import scheduler_summary_from_state

        scheduler_summary = scheduler_summary_from_state(state, config)
        raw_plans = state.get("plans", [])
        raw_missions = state.get("missions", [])
        semantic_by_plan, semantic_by_mission = self._semantic_authority_projections(
            raw_plans, raw_missions
        )
        return ProjectView(
            schema_version=PROJECT_VIEW_SCHEMA_VERSION,
            project=config.name,
            root=config.root,
            runtime_backend=config.runtime.backend,
            leader=leader,
            agents=agents,
            state_path=str(self.state_path),
            missions=self._mission_summaries(raw_missions, semantic_by_mission),
            plans=self._plan_summaries(raw_plans, semantic_by_plan),
            approvals=self._approval_summaries(state.get("approvals", [])),
            messages=self._message_summaries(state.get("messages", [])),
            jobs=self._job_summaries(state.get("jobs", [])),
            replies=self._reply_summaries(state.get("replies", [])),
            artifacts=self.artifact_summaries(state.get("artifacts", [])),
            releases=self._release_summaries(state.get("releases", [])),
            chat_turns=self._chat_turn_summaries(state.get("chat_turns", [])),
            leader_errors=self._leader_error_summaries(state.get("leader_errors", [])),
            leader_actions=self._leader_action_summaries(state.get("leader_actions", [])),
            skills=self._skill_load_summaries(state.get("skill_loads", [])),
            memory=self._memory_context_summary(self.root),
            agent_sessions=agent_sessions,
            protocol_turns=protocol_turns,
            transport_updates=transport_updates,
            permission_requests=permission_requests,
            protocol_state_transitions=protocol_state_transitions,
            conversation=self._conversation_summary(state),
            inbox=self._inbox_summary(state.get("inbox", {})),
            recovery=self._recovery_summary(state, config),
            mission_recovery=self._mission_recovery_summary(state),
            daemon=daemon_summary,
            scheduler=scheduler_summary,
        )


AUTHORITATIVE_STATE_MUTATION_METHODS = (
    "claim_mission_worker_start",
    "ack_inbox_item",
    "admit_mission_execution",
    "advance_mission_after_handoff",
    "append_message",
    "apply_leader_action",
    "authorize_mission_acp_effect",
    "bind_agent",
    "bind_mission_permission_evidence",
    "change_worker_ownership_with_governance_preview",
    "claim_mission_attempt_admission",
    "claim_mission_execution",
    "commit_controller_lease",
    "commit_conversation_mutation",
    "commit_recovery_decisions",
    "complete_admitted_mission",
    "consume_governance_preview",
    "create_approvals_from_plan",
    "create_chat_assignment_approval",
    "create_dispatch_records",
    "create_mission",
    "create_workflow_run",
    "decide_approval",
    "decide_permission_with_governance_preview",
    "flush_conversation_event_outbox",
    "flush_daemon_event_outbox",
    "flush_protocol_event_outbox",
    "force_stop_with_governance_preview",
    "freeze_mission_execution",
    "finish_mission_worker_start",
    "interrupt_prepared_mission_attempt",
    "mark_agent_released",
    "mark_agent_stale",
    "grant_delegation",
    "revoke_delegation",
    "mark_worktree_abandoned",
    "mark_worktree_merged",
    "mark_agent_stopped",
    "mark_approval_dispatched",
    "mark_acp_mission_attempt_completion_ambiguous",
    "mark_mission_attempt_admission_ambiguous",
    "mark_mission_attempt_ambiguous",
    "pause_mission_with_governance_preview",
    "prepare_mission_attempt",
    "record_acp_mission_attempt_completion",
    "record_acp_permission_pending",
    "record_agent_session",
    "record_chat_turn",
    "record_daemon_state",
    "record_governance_preview",
    "record_leader_error",
    "record_live_permission_recovery",
    "record_memory_suggestion",
    "record_mission_acp_permission_pending",
    "record_mission_attempt_result",
    "record_mission_attempt_submitted",
    "record_mission_handoff_evidence",
    "record_mission_not_admitted",
    "record_mission_reply_evidence",
    "record_permission_request",
    "record_plan",
    "record_protocol_transition",
    "record_protocol_turn",
    "record_release",
    "record_reply",
    "record_semantic_worker_dispatch_event",
    "record_skill_load",
    "record_skill_suggestion",
    "record_tmux_mission_attempt_completion",
    "record_transport_reroute_with_governance_preview",
    "record_transport_update",
    "record_worker_reconciliation_ambiguity",
    "release_mission_attempt_admission",
    "save",
    "suggest_leader_action",
    "transition_mission_with_governance_preview",
    "update_mission",
    "update_workflow_run",
)


def _locked_state_mutation(method: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(method)
    def wrapped(self: StateStore, *args: Any, **kwargs: Any) -> Any:
        with self._protocol_mutation_lock():
            return method(self, *args, **kwargs)

    setattr(wrapped, "_agentdeck_authoritative_mutation", True)
    return wrapped


for _mutation_method_name in AUTHORITATIVE_STATE_MUTATION_METHODS:
    setattr(
        StateStore,
        _mutation_method_name,
        _locked_state_mutation(getattr(StateStore, _mutation_method_name)),
    )


def agentdeck_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / CONFIG_DIR
