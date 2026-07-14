from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import asdict
from pathlib import Path
import ast
from datetime import datetime, timedelta, timezone
import fcntl
import gc
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
import weakref

import pytest

from agentdeck.config import write_default_config
from agentdeck import state as state_module
from agentdeck import cli
from agentdeck import contracts as contracts_module
from agentdeck.models import EventRecord
from agentdeck.state import StateStore


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "protocol-mutation.lock"
    }


def _tree_facts(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _seed_m1_state_without_execution_snapshot(root: Path) -> None:
    (root / ".git").mkdir()
    write_default_config(root)
    store = StateStore(root)
    state = store.load()
    state["missions"] = [
        {
            "mission_id": "mis_131313131313",
            "schema_version": "mission/v1",
            "status": "interrupted",
            "current_step": 1,
            "step_count": 2,
            "execution_snapshot": None,
            "snapshot_hash": None,
        }
    ]
    store.save(state)


def test_old_mission_migration_preview_is_zero_write(tmp_path: Path) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    before = _tree_bytes(tmp_path)

    preview = state_module.migration_preview(tmp_path)

    assert preview["legacy_missions"][0]["mode"] == "inspect_only"
    assert _tree_bytes(tmp_path) == before


NOW = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)


def _seed_malicious_legacy_mission(root: Path) -> None:
    _seed_m1_state_without_execution_snapshot(root)
    store = StateStore.open_existing(root)
    state = store.load()
    state["missions"][0]["mission_id"] = "mis_bad;echo-pwn"
    store.save(state)


def test_malicious_legacy_mission_is_rejected_before_preview_or_confirm_writes(
    tmp_path: Path,
) -> None:
    _seed_malicious_legacy_mission(tmp_path)
    before = _tree_bytes(tmp_path)
    state_path = tmp_path / ".agentdeck" / "state" / "state.json"
    source_hash = "sha256:" + hashlib.sha256(state_path.read_bytes()).hexdigest()
    preview_id = f"mig_{source_hash.removeprefix('sha256:')[:12]}"
    expiry = NOW + timedelta(minutes=10)
    legacy = [{
        "mission_id": "mis_bad;echo-pwn",
        "mode": "inspect_only",
        "reason": "complete frozen execution authority is unavailable",
        "inspect_command": "agentdeck mission status --mission-id mis_bad;echo-pwn",
        "reconfirm_command": (
            "agentdeck leader chat --message \"Reconfirm legacy Mission "
            "mis_bad;echo-pwn as a new Mission preview\""
        ),
    }]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    changes = [
        {"path": key, "operation": "add", "value": []}
        for key in state_module._MIGRATION_ADDITIVE_COLLECTIONS
        if key not in state
    ]
    changes.extend([
        {"path": "schema_generation", "operation": "add", "value": "project-daemon-m2b/v1"},
        {"path": "legacy_mission_migrations", "operation": "add", "value": legacy},
        {"path": "migration_previews_consumed", "operation": "add", "value": [{
            "preview_id": preview_id, "source_hash": source_hash,
            "expires_at": expiry.isoformat(),
        }]},
    ])
    digest = state_module.canonical_snapshot_hash({
        "preview_id": preview_id, "source_hash": source_hash,
        "target_changes": changes, "legacy_missions": legacy,
        "expires_at": expiry.isoformat(),
    })

    with pytest.raises(ValueError, match="migration contract validation failed"):
        state_module.migration_preview(tmp_path, now=NOW)
    with pytest.raises(ValueError, match="migration contract validation failed"):
        state_module.confirm_migration(
            tmp_path, preview_id=preview_id, source_hash=source_hash,
            digest=digest, expires_at=expiry.isoformat(), confirm=True,
            now=NOW + timedelta(seconds=1),
        )

    assert _tree_bytes(tmp_path) == before
    assert not (tmp_path / ".agentdeck" / "backups").exists()


def test_state_directory_symlink_is_rejected_before_lock_backup_or_external_write(
    tmp_path: Path,
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    preview = state_module.migration_preview(tmp_path, now=NOW)
    state_dir = tmp_path / ".agentdeck" / "state"
    outside = tmp_path / "outside-state"
    state_dir.rename(outside)
    state_dir.symlink_to(outside, target_is_directory=True)
    outside_before = _tree_bytes(outside)

    with pytest.raises(ValueError, match="state directory"):
        _confirm(tmp_path, preview, now=NOW + timedelta(seconds=1))

    assert _tree_bytes(outside) == outside_before
    assert not (tmp_path / ".agentdeck" / "backups").exists()
    assert not any(".tmp" in path.name for path in outside.iterdir())
    assert (outside / "protocol-mutation.lock").is_file()


def test_partial_project_read_is_zero_write_and_first_mutation_creates_safe_state(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    write_default_config(tmp_path)
    state_dir = tmp_path / ".agentdeck" / "state"
    for child in state_dir.iterdir():
        child.unlink()
    state_dir.rmdir()
    before = _tree_bytes(tmp_path)
    store = StateStore.open_existing(tmp_path)

    state = store.load()

    assert type(state) is dict
    assert state["missions"] == []
    assert _tree_bytes(tmp_path) == before

    store.record_chat_turn("status", "created safely", None, None)

    assert state_dir.is_dir()
    persisted = store.load()
    assert persisted["chat_turns"][-1]["message"] == "created safely"


def test_public_mutation_rejects_symlinked_state_directory_without_external_write(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    write_default_config(tmp_path)
    state_dir = tmp_path / ".agentdeck" / "state"
    outside = tmp_path / "outside-state"
    state_dir.rename(outside)
    state_dir.symlink_to(outside, target_is_directory=True)
    outside_before = _tree_bytes(outside)

    with pytest.raises(ValueError, match="state directory"):
        StateStore.open_existing(tmp_path).record_chat_turn(
            "status", "must not escape", None, None
        )

    assert _tree_bytes(outside) == outside_before
    assert (outside / "protocol-mutation.lock").is_file()


@pytest.mark.parametrize(
    "unsafe_path",
    [
        ".agentdeck",
        "state",
        "events.jsonl",
        "approvals.jsonl",
        "protocol-mutation.lock",
    ],
)
def test_default_constructor_layout_is_dirfd_anchored_and_symlink_safe(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    (tmp_path / ".git").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel").write_text("unchanged\n", encoding="utf-8")
    if unsafe_path == ".agentdeck":
        (tmp_path / ".agentdeck").symlink_to(outside, target_is_directory=True)
    else:
        deck = tmp_path / ".agentdeck"
        deck.mkdir()
        if unsafe_path == "state":
            (deck / "state").symlink_to(outside, target_is_directory=True)
        else:
            state_dir = deck / "state"
            state_dir.mkdir()
            (state_dir / unsafe_path).symlink_to(outside / "sentinel")
    before = _tree_facts(outside)

    with pytest.raises((OSError, ValueError)):
        StateStore(tmp_path)

    assert _tree_facts(outside) == before


@pytest.mark.parametrize(
    "unsafe_path",
    [
        ".agentdeck",
        "state",
        "events.jsonl",
        "approvals.jsonl",
        "protocol-mutation.lock",
    ],
)
def test_default_constructor_rejects_non_directory_or_non_regular_layout_nodes(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    (tmp_path / ".git").mkdir()
    if unsafe_path == ".agentdeck":
        (tmp_path / ".agentdeck").write_text("not a directory\n", encoding="utf-8")
    else:
        deck = tmp_path / ".agentdeck"
        deck.mkdir()
        if unsafe_path == "state":
            (deck / "state").write_text("not a directory\n", encoding="utf-8")
        else:
            state_dir = deck / "state"
            state_dir.mkdir()
            (state_dir / unsafe_path).mkdir()

    with pytest.raises(ValueError, match="project layout"):
        StateStore(tmp_path)


def test_event_journal_read_and_append_reject_symlink_without_external_io(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path)
    journal = tmp_path / ".agentdeck" / "state" / "events.jsonl"
    outside = tmp_path / "outside-events.jsonl"
    outside.write_text('{"sentinel": true}\n', encoding="utf-8")
    journal.unlink()
    journal.symlink_to(outside)
    before = outside.read_bytes()

    with pytest.raises(ValueError, match="event journal is unsafe"):
        store.all_events()
    with pytest.raises(ValueError, match="event journal is unsafe"):
        store.append_event(EventRecord.create("must_not_escape", {}))

    assert outside.read_bytes() == before


def test_event_journal_replacement_between_open_and_append_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StateStore(tmp_path)
    journal = tmp_path / ".agentdeck" / "state" / "events.jsonl"
    detached = tmp_path / "detached-events.jsonl"
    original_verify = state_module._verify_regular_file_at
    replaced = False

    def replace_before_verify(
        directory_fd: int,
        name: str,
        descriptor: int,
        *,
        error: str,
    ) -> None:
        nonlocal replaced
        if name == "events.jsonl" and not replaced:
            journal.rename(detached)
            journal.write_bytes(b"")
            replaced = True
        original_verify(directory_fd, name, descriptor, error=error)

    monkeypatch.setattr(
        state_module, "_verify_regular_file_at", replace_before_verify
    )

    with pytest.raises(ValueError, match="event journal is unsafe"):
        store.append_event(EventRecord.create("must_not_reach_detached_inode", {}))

    assert detached.read_bytes() == b""
    assert journal.read_bytes() == b""


def test_event_journal_replacement_after_proof_never_appends_detached_inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StateStore(tmp_path)
    store.append_event(EventRecord.create("durable_before_race", {}))
    journal = tmp_path / ".agentdeck" / "state" / "events.jsonl"
    detached = tmp_path / "detached-after-proof-events.jsonl"
    before = journal.read_bytes()
    original_write = state_module.os.write
    raced = False

    def replace_after_proof(fd: int, payload: bytes | memoryview) -> int:
        nonlocal raced
        if not raced:
            journal.rename(detached)
            journal.write_bytes(before)
            raced = True
        return original_write(fd, payload)

    monkeypatch.setattr(state_module.os, "write", replace_after_proof)

    with pytest.raises(ValueError, match="event journal changed"):
        store.append_event(EventRecord.create("must_not_reach_detached_inode", {}))

    assert raced is True
    assert detached.read_bytes() == before
    assert journal.read_bytes() == before
    assert not any(
        path.name.startswith(".events.jsonl.")
        for path in journal.parent.iterdir()
    )


def test_post_proof_journal_race_preserves_outbox_and_retries_idempotently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StateStore(tmp_path)
    event = EventRecord.create("pending_post_proof_race", {})
    state = store.load()
    state["protocol_event_outbox"] = [asdict(event)]
    store.save(state)
    journal = tmp_path / ".agentdeck" / "state" / "events.jsonl"
    detached = tmp_path / "detached-outbox-events.jsonl"
    before = journal.read_bytes()
    original_write = state_module.os.write
    raced = False

    def replace_during_transaction(fd: int, payload: bytes | memoryview) -> int:
        nonlocal raced
        if not raced:
            journal.rename(detached)
            journal.write_bytes(before)
            raced = True
        return original_write(fd, payload)

    monkeypatch.setattr(state_module.os, "write", replace_during_transaction)

    with pytest.raises(ValueError, match="event journal changed"):
        store.flush_protocol_event_outbox()

    assert store.load()["protocol_event_outbox"] == [asdict(event)]
    assert detached.read_bytes() == before
    assert journal.read_bytes() == before
    assert not any(
        path.name.startswith(".events.jsonl.")
        for path in journal.parent.iterdir()
    )

    monkeypatch.setattr(state_module.os, "write", original_write)
    assert store.flush_protocol_event_outbox() == 1
    assert store.flush_protocol_event_outbox() == 0
    assert sum(
        item.get("event_id") == event.event_id for item in store.all_events()
    ) == 1


@pytest.mark.parametrize(
    ("outbox_name", "flush_name"),
    [
        ("daemon_event_outbox", "flush_daemon_event_outbox"),
        ("conversation_event_outbox", "flush_conversation_event_outbox"),
        ("protocol_event_outbox", "flush_protocol_event_outbox"),
    ],
)
def test_event_outbox_flush_rejects_symlink_and_preserves_pending_records(
    tmp_path: Path,
    outbox_name: str,
    flush_name: str,
) -> None:
    store = StateStore(tmp_path)
    event = EventRecord.create("pending_secure_flush", {"source": outbox_name})
    state = store.load()
    state[outbox_name] = [asdict(event)]
    store.save(state)
    journal = tmp_path / ".agentdeck" / "state" / "events.jsonl"
    outside = tmp_path / "outside-events.jsonl"
    outside.write_text('{"sentinel": true}\n', encoding="utf-8")
    journal.unlink()
    journal.symlink_to(outside)
    before = outside.read_bytes()

    with pytest.raises(ValueError, match="event journal is unsafe"):
        getattr(store, flush_name)()

    assert outside.read_bytes() == before
    assert store.load()[outbox_name] == [asdict(event)]


def test_protocol_mutation_lock_replacement_after_flock_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StateStore(tmp_path)
    lock_path = tmp_path / ".agentdeck" / "state" / "protocol-mutation.lock"
    original_flock = state_module.fcntl.flock
    replaced_lock = tmp_path / "detached-protocol-mutation.lock"
    replaced = False

    def replace_after_lock(fd: int, operation: int) -> None:
        nonlocal replaced
        original_flock(fd, operation)
        if (
            operation == fcntl.LOCK_EX
            and stat.S_ISREG(os.fstat(fd).st_mode)
            and not replaced
        ):
            lock_path.rename(replaced_lock)
            lock_path.write_bytes(b"replacement")
            replaced = True

    monkeypatch.setattr(state_module.fcntl, "flock", replace_after_lock)

    with pytest.raises(ValueError, match="protocol mutation lock changed"):
        store.record_chat_turn("status", "must not commit", None, None)

    assert store.load()["chat_turns"] == []


def test_protocol_mutation_directory_guard_prevents_post_proof_lock_split(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StateStore(tmp_path)
    lock_path = tmp_path / ".agentdeck" / "state" / "protocol-mutation.lock"
    detached_lock = tmp_path / "detached-after-proof.lock"
    proof_reached = threading.Event()
    release_first = threading.Event()
    second_finished = threading.Event()
    errors: list[BaseException] = []
    first_thread_id: int | None = None
    original_verify = state_module._verify_regular_file_at

    def pause_after_lock_proof(
        directory_fd: int,
        name: str,
        descriptor: int,
        *,
        error: str,
    ) -> None:
        original_verify(directory_fd, name, descriptor, error=error)
        if (
            error == "protocol mutation lock changed after acquisition"
            and threading.get_ident() == first_thread_id
        ):
            proof_reached.set()
            assert release_first.wait(timeout=2)

    monkeypatch.setattr(
        state_module, "_verify_regular_file_at", pause_after_lock_proof
    )

    def first_writer() -> None:
        nonlocal first_thread_id
        first_thread_id = threading.get_ident()
        try:
            store.record_chat_turn("status", "first-writer", None, None)
        except BaseException as exc:
            errors.append(exc)

    def second_writer() -> None:
        try:
            StateStore.open_existing(tmp_path).record_chat_turn(
                "status", "second-writer", None, None
            )
        except BaseException as exc:
            errors.append(exc)
        finally:
            second_finished.set()

    first = threading.Thread(target=first_writer, daemon=True)
    first.start()
    assert proof_reached.wait(timeout=1)
    lock_path.rename(detached_lock)
    lock_path.write_bytes(b"replacement")
    second = threading.Thread(target=second_writer, daemon=True)
    second.start()

    assert not second_finished.wait(timeout=0.2), (
        "a replacement lock inode must not split the authoritative lock domain"
    )
    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)
    assert "protocol mutation lock changed" in str(errors[0])
    assert [item["message"] for item in store.load()["chat_turns"]] == [
        "second-writer"
    ]


@pytest.mark.parametrize("destination", ["project", "external"])
def test_project_guard_blocks_state_directory_replacement_after_lock_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    destination: str,
) -> None:
    store = StateStore(tmp_path)
    state_dir = tmp_path / ".agentdeck" / "state"
    detached = (
        tmp_path / ".agentdeck" / "state.detached"
        if destination == "project"
        else tmp_path.parent / f"{tmp_path.name}-external-state-detached"
    )
    proof_reached = threading.Event()
    release_first = threading.Event()
    second_finished = threading.Event()
    errors: list[tuple[str, BaseException]] = []
    first_thread_id: int | None = None
    original_verify = state_module._verify_regular_file_at

    def pause_after_lock_proof(
        directory_fd: int,
        name: str,
        descriptor: int,
        *,
        error: str,
    ) -> None:
        original_verify(directory_fd, name, descriptor, error=error)
        if (
            error == "protocol mutation lock changed after acquisition"
            and threading.get_ident() == first_thread_id
        ):
            proof_reached.set()
            assert release_first.wait(timeout=2)

    monkeypatch.setattr(
        state_module, "_verify_regular_file_at", pause_after_lock_proof
    )

    def first_writer() -> None:
        nonlocal first_thread_id
        first_thread_id = threading.get_ident()
        try:
            store.record_chat_turn("status", "detached-writer", None, None)
        except BaseException as exc:
            errors.append(("first", exc))

    def second_writer() -> None:
        try:
            StateStore.open_existing(tmp_path).record_chat_turn(
                "status", "canonical-writer", None, None
            )
        except BaseException as exc:
            errors.append(("second", exc))
        finally:
            second_finished.set()

    first = threading.Thread(target=first_writer, daemon=True)
    second: threading.Thread | None = None
    try:
        first.start()
        assert proof_reached.wait(timeout=1)
        state_dir.rename(detached)
        shutil.copytree(detached, state_dir)
        second = threading.Thread(target=second_writer, daemon=True)
        second.start()

        assert not second_finished.wait(timeout=0.2), (
            "replacing state must not split the project mutation guard"
        )
    finally:
        release_first.set()
        first.join(timeout=2)
        if second is not None:
            second.join(timeout=2)

    assert not first.is_alive()
    assert second is not None and not second.is_alive()
    assert len(errors) == 1
    assert errors[0][0] == "first"
    assert isinstance(errors[0][1], ValueError)
    assert "state directory changed" in str(errors[0][1])
    canonical_messages = [
        item["message"]
        for item in StateStore.open_existing(tmp_path).load()["chat_turns"]
    ]
    assert canonical_messages == ["canonical-writer"]
    if destination == "external":
        shutil.rmtree(detached)


def test_daemon_outbox_state_detach_after_journal_proof_never_reports_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StateStore(tmp_path)
    event = EventRecord.create("pending_state_detach", {})
    state = store.load()
    state["daemon_event_outbox"] = [asdict(event)]
    store.save(state)
    state_dir = tmp_path / ".agentdeck" / "state"
    detached = tmp_path.parent / f"{tmp_path.name}-outbox-state-detached"
    original_write = state_module.os.write
    raced = False

    def detach_state_during_journal_effect(
        fd: int, payload: bytes | memoryview
    ) -> int:
        nonlocal raced
        if not raced:
            state_dir.rename(detached)
            shutil.copytree(
                detached,
                state_dir,
                ignore=shutil.ignore_patterns(".events.jsonl.*"),
            )
            raced = True
        return original_write(fd, payload)

    monkeypatch.setattr(
        state_module.os, "write", detach_state_during_journal_effect
    )
    try:
        with pytest.raises(ValueError, match="state directory changed"):
            store.flush_daemon_event_outbox()

        assert raced is True
        assert StateStore.open_existing(tmp_path).load()[
            "daemon_event_outbox"
        ] == [asdict(event)]
    finally:
        shutil.rmtree(detached)


def test_atomic_save_state_detach_after_precheck_never_reports_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StateStore(tmp_path)
    state_dir = tmp_path / ".agentdeck" / "state"
    detached = tmp_path.parent / f"{tmp_path.name}-atomic-state-detached"
    original_save = state_module._atomic_save_state_at
    raced = False

    def detach_state_during_atomic_save(
        state_fd: int, state: dict[str, object]
    ) -> None:
        nonlocal raced
        if not raced:
            state_dir.rename(detached)
            shutil.copytree(detached, state_dir)
            raced = True
        original_save(state_fd, state)

    monkeypatch.setattr(
        state_module, "_atomic_save_state_at", detach_state_during_atomic_save
    )
    try:
        with pytest.raises(ValueError, match="state directory changed"):
            store.record_chat_turn("status", "must-not-be-canonical", None, None)

        assert raced is True
        assert StateStore.open_existing(tmp_path).load()["chat_turns"] == []
    finally:
        shutil.rmtree(detached)


def test_legacy_state_lock_inode_blocks_new_authoritative_writer(
    tmp_path: Path,
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    lock_path = tmp_path / ".agentdeck" / "state" / "protocol-mutation.lock"
    lock_identity = (lock_path.stat().st_dev, lock_path.stat().st_ino)
    started = tmp_path / "legacy-writer-started"
    finished = tmp_path / "legacy-writer-finished"
    script = (
        "from pathlib import Path\n"
        "from agentdeck.state import StateStore\n"
        f"root = Path({str(tmp_path)!r})\n"
        f"Path({str(started)!r}).write_text('started')\n"
        "StateStore.open_existing(root).record_chat_turn("
        "'status', 'legacy-lock-compatible', None, None)\n"
        f"Path({str(finished)!r}).write_text('finished')\n"
    )

    with lock_path.open("r+b") as legacy_lock:
        fcntl.flock(legacy_lock.fileno(), fcntl.LOCK_EX)
        writer = subprocess.Popen(
            [sys.executable, "-c", script],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 2
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert started.exists()
        assert not finished.exists()
        assert writer.poll() is None
        fcntl.flock(legacy_lock.fileno(), fcntl.LOCK_UN)

    stdout, stderr = writer.communicate(timeout=2)
    assert (writer.returncode, stdout, stderr) == (0, "", "")
    assert finished.exists()
    assert (lock_path.stat().st_dev, lock_path.stat().st_ino) == lock_identity
    assert StateStore.open_existing(tmp_path).load()["chat_turns"][-1][
        "message"
    ] == "legacy-lock-compatible"


def test_state_source_provenance_survives_unrelated_loads_without_strong_state_refs(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path)
    held = store.load()
    held["plans"] = [{"plan_id": "pln_held_snapshot"}]
    fact_count = len(state_module._STATE_SOURCE_FACTS)
    for _ in range(700):
        StateStore.open_existing(tmp_path).load()

    gc.collect()
    assert len(state_module._STATE_SOURCE_FACTS) <= fact_count
    store.save(held)

    assert store.load()["plans"] == [{"plan_id": "pln_held_snapshot"}]
    assert all(
        not isinstance(value, dict)
        for value in state_module._STATE_SOURCE_FACTS.values()
    )


def test_state_source_provenance_is_gc_reclaimed_and_deepcopy_stable(
    tmp_path: Path,
) -> None:
    write_default_config(tmp_path)
    store = StateStore(tmp_path)
    state = store.load()
    token = state[state_module._STATE_SOURCE_TOKEN_KEY]
    token_ref = weakref.ref(token)
    clone = deepcopy(state)
    assert clone[state_module._STATE_SOURCE_TOKEN_KEY] is not token
    legacy_plan = store.build_plan_record(
        "deepcopy state provenance",
        "fake",
        "fake-plan",
        {
            "dispatch_ready": False,
            "steps": [
                {
                    "step": 1,
                    "agent_id": "planner",
                    "role": "planning",
                    "task": "preserve a legal legacy plan fixture",
                    "requires_approval": True,
                }
            ],
        },
    )
    legacy_plan["plan_id"] = "pln_deepcopy"
    clone["plans"] = [legacy_plan]
    store.save(clone)
    assert state_module._STATE_SOURCE_TOKEN_KEY not in store.state_path.read_text(
        encoding="utf-8"
    )
    assert state_module._STATE_SOURCE_TOKEN_KEY not in repr(
        store.project_view(state_module.load_config(tmp_path))
    )

    del state
    del clone
    del token
    gc.collect()

    assert token_ref() is None


@pytest.mark.parametrize("copier", [copy, deepcopy], ids=["shallow", "deep"])
def test_copied_state_branch_cannot_refresh_stale_original_provenance(
    tmp_path: Path,
    copier,
) -> None:
    store = StateStore(tmp_path)
    original = store.load()
    branch = copier(original)
    branch["plans"] = [{"plan_id": "pln_copy_branch"}]
    original["plans"] = [{"plan_id": "pln_stale_original"}]

    store.save(branch)

    assert (
        branch[state_module._STATE_SOURCE_TOKEN_KEY]
        is not original[state_module._STATE_SOURCE_TOKEN_KEY]
    )
    with pytest.raises(ValueError, match="state source facts drifted"):
        store.save(original)
    persisted = store.load()
    assert persisted["plans"] == [{"plan_id": "pln_copy_branch"}]
    assert state_module._STATE_SOURCE_TOKEN_KEY not in store.state_path.read_text(
        encoding="utf-8"
    )


def test_state_directory_replace_race_fails_before_backup_or_external_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    preview = state_module.migration_preview(tmp_path, now=NOW)
    state_dir = tmp_path / ".agentdeck" / "state"
    detached = tmp_path / ".agentdeck" / "detached-state"
    outside = tmp_path / "outside-state"
    outside.mkdir()
    outside_before = _tree_bytes(outside)
    original_verify = state_module._verify_project_state_anchor
    replaced = False

    def replace_before_verify(root: Path, deck_fd: int, state_fd: int) -> None:
        nonlocal replaced
        if not replaced:
            state_dir.rename(detached)
            state_dir.symlink_to(outside, target_is_directory=True)
            replaced = True
        original_verify(root, deck_fd, state_fd)

    monkeypatch.setattr(
        state_module, "_verify_project_state_anchor", replace_before_verify
    )

    with pytest.raises(ValueError, match="state directory"):
        _confirm(tmp_path, preview, now=NOW + timedelta(seconds=1))

    assert _tree_bytes(outside) == outside_before
    assert not (tmp_path / ".agentdeck" / "backups").exists()
    assert not any(".tmp" in path.name for path in detached.iterdir())


def test_migration_expiry_is_rechecked_after_waiting_for_mutation_lock(
    tmp_path: Path,
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    preview = state_module.migration_preview(
        tmp_path, now=NOW, expires_at=NOW + timedelta(milliseconds=150)
    )
    store = StateStore.open_existing(tmp_path)
    locked = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with store._protocol_mutation_lock():
            locked.set()
            release.wait(timeout=2)

    holder_thread = threading.Thread(target=holder)
    holder_thread.start()
    assert locked.wait(timeout=1)
    before = _tree_bytes(tmp_path)
    errors: list[BaseException] = []

    def confirmer() -> None:
        try:
            _confirm(tmp_path, preview, now=NOW)
        except BaseException as exc:
            errors.append(exc)

    confirm_thread = threading.Thread(target=confirmer)
    confirm_thread.start()
    time.sleep(0.25)
    release.set()
    holder_thread.join(timeout=2)
    confirm_thread.join(timeout=2)

    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)
    assert "expired" in str(errors[0])
    assert _tree_bytes(tmp_path) == before
    assert not (tmp_path / ".agentdeck" / "backups").exists()


def test_already_migrated_preview_is_read_only_noop_with_disabled_confirm(
    tmp_path: Path,
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    ready = state_module.migration_preview(tmp_path, now=NOW)
    _confirm(tmp_path, ready, now=NOW + timedelta(seconds=1))
    before = _tree_bytes(tmp_path)

    preview = state_module.migration_preview(tmp_path, now=NOW + timedelta(minutes=1))

    assert preview["status"] == "noop"
    assert preview["can_migrate"] is False
    assert preview["target_changes"] == []
    assert preview["confirm_command"] is None
    assert preview["controls"][1]["enabled"] is False
    assert preview["controls"][1]["blocker"] == "project is already migrated"
    assert _tree_bytes(tmp_path) == before


def test_partial_migration_preview_fails_safe_with_disabled_confirm(
    tmp_path: Path,
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    store = StateStore.open_existing(tmp_path)
    state = store.load()
    state["schema_generation"] = "project-daemon-m2b/v1"
    store.save(state)
    before = _tree_bytes(tmp_path)

    preview = state_module.migration_preview(tmp_path, now=NOW)

    assert preview["status"] == "blocked"
    assert preview["can_migrate"] is False
    assert preview["target_changes"] == []
    assert preview["confirm_command"] is None
    assert preview["controls"][1]["enabled"] is False
    assert "partial" in str(preview["controls"][1]["blocker"])
    assert _tree_bytes(tmp_path) == before


def test_migration_preview_binds_exact_source_changes_backup_and_expiry(
    tmp_path: Path,
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    state_path = tmp_path / ".agentdeck" / "state" / "state.json"
    preview = state_module.migration_preview(tmp_path, now=NOW)

    assert preview["source_hash"] == "sha256:" + hashlib.sha256(
        state_path.read_bytes()
    ).hexdigest()
    assert preview["backup_path"].startswith(".agentdeck/backups/mig_")
    assert not Path(str(preview["backup_path"])).is_absolute()
    assert preview["consume_once"] is True
    assert preview["expires_at"] == (NOW + timedelta(minutes=10)).isoformat()
    assert preview["digest"].startswith("sha256:")
    assert preview["confirm_command"].endswith(" --confirm")
    assert preview["legacy_missions"] == [
        {
            "mission_id": "mis_131313131313",
            "mode": "inspect_only",
            "reason": "complete frozen execution authority is unavailable",
            "inspect_command": (
                "agentdeck mission status --mission-id mis_131313131313"
            ),
            "reconfirm_command": (
                "agentdeck leader chat --message \"Reconfirm legacy Mission "
                "mis_131313131313 as a new Mission preview\""
            ),
        }
    ]
    assert all(item["operation"] == "add" for item in preview["target_changes"])


def _confirm(root: Path, preview: dict[str, object], *, now: datetime = NOW):
    return state_module.confirm_migration(
        root,
        preview_id=str(preview["preview_id"]),
        source_hash=str(preview["source_hash"]),
        digest=str(preview["digest"]),
        expires_at=str(preview["expires_at"]),
        confirm=True,
        now=now,
    )


def test_confirmed_migration_is_additive_backed_up_and_consumed_once(
    tmp_path: Path,
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    state_path = tmp_path / ".agentdeck" / "state" / "state.json"
    source = state_path.read_bytes()
    before_state = json.loads(source)
    before_state["controller_lease"] = {
        "lease_id": "SECRET-RUNTIME-LEASE",
        "state": "active",
    }
    state_path.write_text(
        json.dumps(before_state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    source = state_path.read_bytes()
    preview = state_module.migration_preview(tmp_path, now=NOW)

    result = _confirm(tmp_path, preview, now=NOW + timedelta(seconds=1))

    backup = tmp_path / str(preview["backup_path"])
    after = json.loads(state_path.read_text(encoding="utf-8"))
    assert result["mode"] == "migration_confirmed"
    backup_payload = json.loads(backup.read_text(encoding="utf-8"))
    assert backup_payload["source_hash"] == preview["source_hash"]
    assert backup_payload["affected_state"] == {
        str(item["path"]): {"present": False}
        for item in preview["target_changes"]
    }
    assert "SECRET-RUNTIME-LEASE" not in backup.read_text(encoding="utf-8")
    assert "execution_snapshot" not in backup.read_text(encoding="utf-8")
    assert after["missions"] == before_state["missions"]
    assert after["legacy_mission_migrations"][0]["mode"] == "inspect_only"
    assert after["migration_previews_consumed"][0]["preview_id"] == preview["preview_id"]
    assert "execution_snapshot" not in after["legacy_mission_migrations"][0]
    with pytest.raises(ValueError, match="consumed"):
        _confirm(tmp_path, preview, now=NOW + timedelta(seconds=2))


@pytest.mark.parametrize("mutation", ["unknown", "expired", "drift"])
def test_invalid_migration_confirmation_is_zero_write(
    tmp_path: Path, mutation: str
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    preview = state_module.migration_preview(tmp_path, now=NOW)
    kwargs = {
        "preview_id": str(preview["preview_id"]),
        "source_hash": str(preview["source_hash"]),
        "digest": str(preview["digest"]),
        "expires_at": str(preview["expires_at"]),
        "confirm": True,
        "now": NOW + timedelta(seconds=1),
    }
    if mutation == "unknown":
        kwargs["preview_id"] = "mig_ffffffffffff"
    elif mutation == "expired":
        kwargs["now"] = NOW + timedelta(minutes=11)
    else:
        state_path = tmp_path / ".agentdeck" / "state" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["drift"] = True
        state_path.write_text(json.dumps(state), encoding="utf-8")
    before = _tree_bytes(tmp_path)

    with pytest.raises(ValueError):
        state_module.confirm_migration(tmp_path, **kwargs)

    assert _tree_bytes(tmp_path) == before


def test_migration_save_failure_removes_backup_and_leaves_source_unchanged(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    preview = state_module.migration_preview(tmp_path, now=NOW)
    before = _tree_bytes(tmp_path)
    monkeypatch.setattr(
        state_module,
        "_atomic_save_state_at",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        _confirm(tmp_path, preview, now=NOW + timedelta(seconds=1))

    assert _tree_bytes(tmp_path) == before


def test_existing_backup_is_never_overwritten_and_state_stays_unchanged(
    tmp_path: Path,
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    preview = state_module.migration_preview(tmp_path, now=NOW)
    backup = tmp_path / str(preview["backup_path"])
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"existing backup\n")
    before = _tree_bytes(tmp_path)

    with pytest.raises(ValueError, match="backup already exists"):
        _confirm(tmp_path, preview, now=NOW + timedelta(seconds=1))

    assert _tree_bytes(tmp_path) == before


def test_migration_confirmation_holds_authoritative_lock_without_lost_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    preview = state_module.migration_preview(tmp_path, now=NOW)
    backup_started = threading.Event()
    writer_done = threading.Event()
    original_write = state_module._write_migration_backup

    def authoritative_writer() -> None:
        writer = StateStore.open_existing(tmp_path)
        assert backup_started.wait(timeout=2)
        with writer._protocol_mutation_lock():
            state = writer.load()
            state["concurrent_authoritative_write"] = "preserved"
            writer._atomic_save(state)
        writer_done.set()

    writer = threading.Thread(target=authoritative_writer, daemon=True)
    writer.start()

    def paused_backup(*args, **kwargs) -> None:
        backup_started.set()
        assert not writer_done.wait(timeout=0.2), (
            "authoritative writer must block until migration state commits"
        )
        original_write(*args, **kwargs)

    monkeypatch.setattr(state_module, "_write_migration_backup", paused_backup)

    _confirm(tmp_path, preview, now=NOW + timedelta(seconds=1))
    writer.join(timeout=2)

    assert not writer.is_alive()
    assert writer_done.is_set()
    state = StateStore.open_existing(tmp_path).load()
    assert state["schema_generation"] == "project-daemon-m2b/v1"
    assert state["concurrent_authoritative_write"] == "preserved"


def test_authoritative_state_transaction_excludes_cross_process_writer(
    tmp_path: Path,
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    started = tmp_path / "writer-started"
    finished = tmp_path / "writer-finished"
    script = (
        "from pathlib import Path\n"
        "from agentdeck.state import StateStore\n"
        f"root = Path({str(tmp_path)!r})\n"
        f"Path({str(started)!r}).write_text('started')\n"
        "StateStore.open_existing(root).record_chat_turn("
        "'status', 'cross-process-preserved', None, None)\n"
        f"Path({str(finished)!r}).write_text('finished')\n"
    )
    process: subprocess.Popen[str] | None = None
    store = StateStore.open_existing(tmp_path)
    with store._protocol_mutation_lock():
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 2
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert started.exists()
        assert not finished.exists()
        assert process.poll() is None

    assert process is not None
    stdout, stderr = process.communicate(timeout=2)
    assert (process.returncode, stdout, stderr) == (0, "", "")
    assert finished.exists()
    assert store.load()["chat_turns"][-1]["message"] == "cross-process-preserved"


@pytest.mark.parametrize("writer_kind", ["update_mission", "claim", "chat"])
def test_migration_and_common_rmw_writers_share_one_authoritative_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_kind: str,
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    preview = state_module.migration_preview(tmp_path, now=NOW)
    backup_started = threading.Event()
    release_backup = threading.Event()
    writer_done = threading.Event()
    errors: list[BaseException] = []
    original_write = state_module._write_migration_backup

    def paused_backup(*args, **kwargs):
        backup_started.set()
        assert release_backup.wait(timeout=2)
        return original_write(*args, **kwargs)

    monkeypatch.setattr(state_module, "_write_migration_backup", paused_backup)

    def migrate() -> None:
        try:
            _confirm(tmp_path, preview, now=NOW + timedelta(seconds=1))
        except BaseException as exc:
            errors.append(exc)

    migration_thread = threading.Thread(target=migrate)
    migration_thread.start()
    assert backup_started.wait(timeout=2)

    def write() -> None:
        try:
            writer = StateStore.open_existing(tmp_path)
            if writer_kind == "update_mission":
                writer.update_mission("mis_131313131313", stop_reason="writer-preserved")
            elif writer_kind == "claim":
                writer.claim_mission_execution(
                    "mis_131313131313", resuming=True,
                    confirmed_at="2026-07-14T08:00:01+00:00",
                )
            else:
                writer.record_chat_turn("status", "writer-preserved", None, None)
        except BaseException as exc:
            errors.append(exc)
        finally:
            writer_done.set()

    writer_thread = threading.Thread(target=write)
    writer_thread.start()
    assert not writer_done.wait(timeout=0.2)
    release_backup.set()
    migration_thread.join(timeout=2)
    writer_thread.join(timeout=2)

    assert errors == []
    state = StateStore.open_existing(tmp_path).load()
    assert state["schema_generation"] == "project-daemon-m2b/v1"
    if writer_kind == "update_mission":
        assert state["missions"][0]["stop_reason"] == "writer-preserved"
    elif writer_kind == "claim":
        assert state["missions"][0]["status"] == "preparing"
    else:
        assert state["chat_turns"][-1]["message"] == "writer-preserved"


def test_stale_public_save_cannot_overwrite_successful_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    store = StateStore.open_existing(tmp_path)
    stale = store.load()
    stale["public_writer"] = "must-not-overwrite"
    preview = state_module.migration_preview(tmp_path, now=NOW)
    backup_started = threading.Event()
    release_backup = threading.Event()
    save_error: list[BaseException] = []
    original_write = state_module._write_migration_backup

    def paused_backup(*args, **kwargs):
        backup_started.set()
        assert release_backup.wait(timeout=2)
        return original_write(*args, **kwargs)

    monkeypatch.setattr(state_module, "_write_migration_backup", paused_backup)
    migration_thread = threading.Thread(
        target=lambda: _confirm(tmp_path, preview, now=NOW + timedelta(seconds=1))
    )
    migration_thread.start()
    assert backup_started.wait(timeout=2)

    def stale_save() -> None:
        try:
            store.save(stale)
        except BaseException as exc:
            save_error.append(exc)

    save_thread = threading.Thread(target=stale_save)
    save_thread.start()
    time.sleep(0.1)
    release_backup.set()
    migration_thread.join(timeout=2)
    save_thread.join(timeout=2)

    assert len(save_error) == 1
    assert "source facts drifted" in str(save_error[0])
    state = store.load()
    assert state["schema_generation"] == "project-daemon-m2b/v1"
    assert "public_writer" not in state


def test_authoritative_state_writer_registry_covers_all_transitive_public_writes() -> None:
    source = Path(state_module.__file__).read_text(encoding="utf-8")
    module = ast.parse(source)
    state_store = next(
        node for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "StateStore"
    )
    calls: dict[str, set[str]] = {}
    for method in state_store.body:
        if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls[method.name] = set()
        for node in ast.walk(method):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "self"
            ):
                calls[method.name].add(node.func.attr)
    transitive_writers = {
        name for name, method_calls in calls.items()
        if method_calls & {"save", "_atomic_save"}
    }
    while True:
        expanded = transitive_writers | {
            name for name, method_calls in calls.items()
            if method_calls & transitive_writers
        }
        if expanded == transitive_writers:
            break
        transitive_writers = expanded
    public_entrypoints = {
        name for name in transitive_writers if not name.startswith("_")
    } | {"save"}

    assert set(state_module.AUTHORITATIVE_STATE_MUTATION_METHODS) == public_entrypoints
    assert all(
        not name.startswith("_")
        for name in state_module.AUTHORITATIVE_STATE_MUTATION_METHODS
    )
    assert "mission-execution.lock" not in source
    assert all(
        getattr(getattr(StateStore, name), "_agentdeck_authoritative_mutation", False)
        for name in state_module.AUTHORITATIVE_STATE_MUTATION_METHODS
    )


@pytest.mark.parametrize("symlink_level", ["backups", "preview"])
def test_migration_backup_rejects_symlink_escape_without_external_write(
    tmp_path: Path,
    symlink_level: str,
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    preview = state_module.migration_preview(tmp_path, now=NOW)
    state_path = tmp_path / ".agentdeck" / "state" / "state.json"
    before_state = state_path.read_bytes()
    outside = tmp_path / "outside"
    outside.mkdir()
    backups = tmp_path / ".agentdeck" / "backups"
    if symlink_level == "backups":
        backups.symlink_to(outside, target_is_directory=True)
    else:
        backups.mkdir()
        (backups / str(preview["preview_id"])).symlink_to(
            outside, target_is_directory=True
        )

    with pytest.raises(ValueError, match="backup.*symlink|backup.*directory"):
        _confirm(tmp_path, preview, now=NOW + timedelta(seconds=1))

    assert state_path.read_bytes() == before_state
    assert list(outside.iterdir()) == []


def test_rollback_replace_fsyncs_state_parent_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    preview = state_module.migration_preview(tmp_path, now=NOW)
    original_save = state_module._atomic_save_state_at
    original_replace = os.replace
    original_fsync = os.fsync
    rollback_replaced = False
    rollback_parent_fsynced = False

    def replace_then_fail(state_fd: int, state: dict[str, object]) -> None:
        original_save(state_fd, state)
        raise OSError("post replace failure")

    def tracking_replace(src, dst, *args, **kwargs) -> None:
        nonlocal rollback_replaced
        original_replace(src, dst, *args, **kwargs)
        if "migration-rollback" in os.fspath(src) or ".rollback-state.json." in os.fspath(src):
            rollback_replaced = True

    def tracking_fsync(fd: int) -> None:
        nonlocal rollback_parent_fsynced
        if rollback_replaced and stat.S_ISDIR(os.fstat(fd).st_mode):
            rollback_parent_fsynced = True
        original_fsync(fd)

    monkeypatch.setattr(state_module, "_atomic_save_state_at", replace_then_fail)
    monkeypatch.setattr(state_module.os, "replace", tracking_replace)
    monkeypatch.setattr(state_module.os, "fsync", tracking_fsync)

    with pytest.raises(OSError, match="post replace failure"):
        _confirm(tmp_path, preview, now=NOW + timedelta(seconds=1))

    assert rollback_replaced is True
    assert rollback_parent_fsynced is True


def test_successful_migration_fsyncs_backup_and_state_after_each_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    preview = state_module.migration_preview(tmp_path, now=NOW)
    original_replace = os.replace
    original_fsync = os.fsync
    phase: str | None = None
    durable = {
        "backup_file": False,
        "backup_parent": False,
        "state_parent": False,
    }

    def tracking_replace(src, dst, *args, **kwargs) -> None:
        nonlocal phase
        original_replace(src, dst, *args, **kwargs)
        if ".tmp_" in os.fspath(src) and dst == "state.json":
            phase = "state"
        elif kwargs.get("dst_dir_fd") is not None and dst == "state.json":
            phase = "backup"
        elif Path(os.fspath(dst)).name == "state.json":
            phase = "state"

    def tracking_fsync(fd: int) -> None:
        mode = os.fstat(fd).st_mode
        if phase == "backup":
            durable["backup_parent" if stat.S_ISDIR(mode) else "backup_file"] = True
        elif phase == "state" and stat.S_ISDIR(mode):
            durable["state_parent"] = True
        original_fsync(fd)

    monkeypatch.setattr(state_module.os, "replace", tracking_replace)
    monkeypatch.setattr(state_module.os, "fsync", tracking_fsync)

    _confirm(tmp_path, preview, now=NOW + timedelta(seconds=1))

    assert durable == {
        "backup_file": True,
        "backup_parent": True,
        "state_parent": True,
    }


def test_post_replace_save_failure_rolls_back_state_before_removing_backup(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    preview = state_module.migration_preview(tmp_path, now=NOW)
    before = _tree_bytes(tmp_path)
    original = state_module._atomic_save_state_at

    def replace_then_fail(state_fd: int, state: dict[str, object]) -> None:
        original(state_fd, state)
        raise OSError("post replace failure")

    monkeypatch.setattr(state_module, "_atomic_save_state_at", replace_then_fail)

    with pytest.raises(OSError, match="post replace failure"):
        _confirm(tmp_path, preview, now=NOW + timedelta(seconds=1))

    assert _tree_bytes(tmp_path) == before


def test_migration_cli_separates_read_only_preview_from_exact_confirm(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    monkeypatch.setattr(cli, "project_root", lambda: tmp_path)
    before = _tree_bytes(tmp_path)

    assert cli.main(["project", "migration-preview"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["mode"] == "migration_preview"
    assert _tree_bytes(tmp_path) == before

    assert cli.main(["project", "migrate"]) == 1
    assert "exact confirmation" in capsys.readouterr().err
    assert _tree_bytes(tmp_path) == before


def test_project_view_and_workbench_contracts_discover_mission_recovery_card(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    monkeypatch.setattr(cli, "project_root", lambda: tmp_path)

    assert cli.main(["contract", "project-view", "--example"]) == 0
    project_contract = json.loads(capsys.readouterr().out)
    assert "classification" in project_contract["mission_recovery_fields"]
    assert "decision" in project_contract["mission_recovery_fields"]
    assert project_contract["example_project_view"]["mission_recovery"]["mode"] == (
        "mission_recovery"
    )

    assert cli.main(["contract", "workbench", "--example"]) == 0
    workbench_contract = json.loads(capsys.readouterr().out)
    assert "mission_recovery_card" in workbench_contract["snapshot_fields"]
    assert workbench_contract["example_workbench"]["mission_recovery_card"] == (
        workbench_contract["example_workbench"]["project_view"]["mission_recovery"]
    )


def test_migration_contract_is_registered_gui_ready_and_in_workbench(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    monkeypatch.setattr(cli, "project_root", lambda: tmp_path)

    assert cli.main(["contract", "migration", "--example"]) == 0
    discovery = json.loads(capsys.readouterr().out)
    assert discovery["schema_version"] == "migration/v1"
    assert "source_hash" in discovery["preview_response_fields"]
    assert "consumed" in discovery["confirmed_response_fields"]
    assert contracts_module.validate_migration_contract(
        discovery["example_preview"]
    ) == {"ok": True, "errors": []}
    assert contracts_module.validate_migration_contract(
        discovery["example_confirmed"]
    ) == {"ok": True, "errors": []}

    assert cli.main(["contract", "list"]) == 0
    index = json.loads(capsys.readouterr().out)
    assert any(item["name"] == "migration" for item in index["contracts"])

    store = StateStore(tmp_path)
    state = store.load()
    state["missions"] = []
    store.save(state)
    assert cli.main(["workbench"]) == 0
    workbench = json.loads(capsys.readouterr().out)
    assert workbench["contracts_card"]["migration_contract"] == (
        "agentdeck contract migration"
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda card: card.update({"digest": "sha256:" + "f" * 64}),
        lambda card: card["target_changes"].append(
            {"path": "raw_prompt", "operation": "add", "value": "pwn"}
        ),
        lambda card: card["target_changes"][0].update({"value": {"raw": "pwn"}}),
        lambda card: card.update({
            "confirm_command": card["confirm_command"].replace("--confirm", "; echo pwn")
        }),
    ],
)
def test_migration_validator_rejects_digest_path_value_and_command_drift(
    tmp_path: Path,
    mutate,
) -> None:
    payload = contracts_module.migration_contract_response(
        tmp_path / "migration-schema.md", include_example=True
    )["example_preview"]
    mutate(payload)

    assert contracts_module.validate_migration_contract(payload)["ok"] is False


def test_confirmed_contract_is_validated_before_backup_or_state_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    preview = state_module.migration_preview(tmp_path, now=NOW)
    before = _tree_bytes(tmp_path)
    original = state_module._require_valid_migration_contract

    def reject_confirmed(payload: dict[str, object]) -> None:
        if payload.get("mode") == "migration_confirmed":
            raise ValueError("migration contract validation failed")
        original(payload)

    monkeypatch.setattr(
        state_module, "_require_valid_migration_contract", reject_confirmed
    )

    with pytest.raises(ValueError, match="migration contract validation failed"):
        _confirm(tmp_path, preview, now=NOW + timedelta(seconds=1))

    assert _tree_bytes(tmp_path) == before
    assert not (tmp_path / ".agentdeck" / "backups").exists()


def test_migration_cli_validates_payload_before_printing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    monkeypatch.setattr(cli, "project_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "migration_preview", lambda _root: {"mode": "migration_preview"})

    assert cli.main(["project", "migration-preview"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Migration contract validation failed" in captured.err

    monkeypatch.setattr(
        cli,
        "confirm_migration",
        lambda *_args, **_kwargs: {"mode": "migration_confirmed"},
    )
    assert cli.main(
        [
            "project", "migrate", "--preview-id", "mig_111111111111",
            "--source-hash", "sha256:" + "1" * 64,
            "--digest", "sha256:" + "2" * 64,
            "--expires-at", "2026-07-14T08:10:00+00:00", "--confirm",
        ]
    ) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Migration contract validation failed" in captured.err
