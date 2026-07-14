from __future__ import annotations

from dataclasses import asdict
import fcntl
import json
import multiprocessing
import os
from pathlib import Path
import threading

import pytest

from agentdeck.models import EventRecord
from agentdeck import state as state_module
from agentdeck.state import StateStore


def _run_identical_atomic_state_race(root_text: str, result_text: str) -> None:
    root = Path(root_text)
    result_path = Path(result_text)
    store = StateStore(root)
    store.save(store.load())
    original_atomic_save = state_module._atomic_save_state_at
    b_inode: int | None = None

    def raced_atomic_save(state_fd: int, state: dict[str, object]) -> None:
        nonlocal b_inode
        if b_inode is None:
            _replace_legacy_lock(root, "identical-state-before-effect")
            path = root / ".agentdeck" / "state" / "state.json"
            temporary = path.with_name(".legacy-identical-state.tmp")
            temporary.write_bytes(state_module._encoded_state(state))
            os.replace(temporary, path)
            b_inode = path.stat().st_ino
        original_atomic_save(state_fd, state)

    state_module._atomic_save_state_at = raced_atomic_save
    try:
        store.record_chat_turn("status", "current-writer-A", None, None)
    except ValueError as exc:
        canonical = root / ".agentdeck" / "state" / "state.json"
        result_path.write_text(
            json.dumps(
                {
                    "error": str(exc),
                    "b_inode": b_inode,
                    "canonical_inode": canonical.stat().st_ino,
                }
            ),
            encoding="utf-8",
        )


def _run_identical_atomic_journal_race(root_text: str, result_text: str) -> None:
    root = Path(root_text)
    result_path = Path(result_text)
    store = StateStore(root)
    pending = EventRecord.create("current_pending", {})
    state = store.load()
    state["protocol_event_outbox"] = [asdict(pending)]
    store.save(state)
    journal = root / ".agentdeck" / "state" / "events.jsonl"
    original_append = state_module._append_event_journal_at
    b_inode: int | None = None

    def raced_append(state_fd: int, payload: bytes) -> None:
        nonlocal b_inode
        if b_inode is None:
            _replace_legacy_lock(root, "identical-journal-before-effect")
            temporary = journal.with_name(".legacy-identical-journal.tmp")
            temporary.write_bytes(journal.read_bytes() + payload)
            os.replace(temporary, journal)
            b_inode = journal.stat().st_ino
        original_append(state_fd, payload)

    state_module._append_event_journal_at = raced_append
    try:
        store.flush_protocol_event_outbox()
    except ValueError as exc:
        result_path.write_text(
            json.dumps(
                {
                    "error": str(exc),
                    "b_inode": b_inode,
                    "canonical_inode": journal.stat().st_ino,
                    "outbox": StateStore.open_existing(root).load()[
                        "protocol_event_outbox"
                    ],
                    "pending": asdict(pending),
                }
            ),
            encoding="utf-8",
        )


def _run_race_child(target, tmp_path: Path) -> dict[str, object]:
    result_path = tmp_path / "child-result.json"
    process = multiprocessing.get_context("spawn").Process(
        target=target,
        args=(str(tmp_path), str(result_path)),
    )
    process.start()
    process.join(timeout=3)
    if process.is_alive():
        process.terminate()
        process.join(timeout=3)
        pytest.fail("mixed-version race child timed out")
    assert process.exitcode == 0
    return json.loads(result_path.read_text(encoding="utf-8"))


def _replace_legacy_lock(root: Path, suffix: str) -> None:
    lock = root / ".agentdeck" / "state" / "protocol-mutation.lock"
    detached = root / f"detached-{suffix}.lock"
    lock.rename(detached)
    lock.write_bytes(b"replacement legacy authority")


def _legacy_write_state(root: Path, marker: str, *, atomic: bool = False) -> None:
    path = root / ".agentdeck" / "state" / "state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["mixed_version_writer"] = marker
    encoded = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if not atomic:
        path.write_text(encoded, encoding="utf-8")
        return
    temporary = path.with_name(f".legacy-{marker}.tmp")
    temporary.write_text(encoded, encoding="utf-8")
    os.replace(temporary, path)


@pytest.mark.parametrize(
    "race_stage", ["after_preproof", "after_cas_before_replace", "post_effect"]
)
def test_mixed_version_lock_replacement_never_loses_legacy_state_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    race_stage: str,
) -> None:
    store = StateStore(tmp_path)
    store.save(store.load())
    original_atomic_save = state_module._atomic_save_state_at
    original_replace = state_module.os.replace
    original_verify = StateStore._verify_current_mutation_anchor
    injected = False

    def inject_legacy(*, atomic: bool = False) -> None:
        nonlocal injected
        if injected:
            return
        injected = True
        _replace_legacy_lock(tmp_path, race_stage)
        if atomic:
            path = tmp_path / ".agentdeck" / "state" / "state.json"
            state = json.loads(path.read_text(encoding="utf-8"))
            state["mixed_version_writer"] = race_stage
            temporary = path.with_name(f".legacy-{race_stage}.tmp")
            temporary.write_text(
                json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            original_replace(temporary, path)
        else:
            _legacy_write_state(tmp_path, race_stage)

    if race_stage == "after_preproof":
        def raced_atomic_save(state_fd: int, state: dict[str, object]) -> None:
            inject_legacy()
            original_atomic_save(state_fd, state)

        monkeypatch.setattr(state_module, "_atomic_save_state_at", raced_atomic_save)
    elif race_stage == "after_cas_before_replace":
        def raced_replace(source, destination, *args, **kwargs):
            if destination == "state.json" and str(source).startswith(".state.json."):
                inject_legacy()
            return original_replace(source, destination, *args, **kwargs)

        monkeypatch.setattr(state_module.os, "replace", raced_replace)
    else:
        def raced_verify(self: StateStore):
            anchored = original_verify(self)
            state = json.loads(
                (tmp_path / ".agentdeck" / "state" / "state.json").read_text(
                    encoding="utf-8"
                )
            )
            if any(
                isinstance(item, dict) and item.get("message") == "current-writer-A"
                for item in state.get("chat_turns", [])
            ):
                inject_legacy(atomic=True)
            return anchored

        monkeypatch.setattr(StateStore, "_verify_current_mutation_anchor", raced_verify)

    with pytest.raises(ValueError, match="protocol mutation lock changed"):
        store.record_chat_turn("status", "current-writer-A", None, None)

    canonical = StateStore.open_existing(tmp_path).load()
    assert canonical["mixed_version_writer"] == race_stage
    if race_stage != "post_effect":
        assert not any(
            item.get("message") == "current-writer-A"
            for item in canonical.get("chat_turns", [])
            if isinstance(item, dict)
        )


def test_rollback_waits_for_replacement_legacy_lock_and_restores_its_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StateStore(tmp_path)
    store.save(store.load())
    original_replace = state_module.os.replace
    legacy_written = threading.Event()
    a_installed = threading.Event()
    release_legacy = threading.Event()
    legacy_done = threading.Event()
    errors: list[BaseException] = []
    injected = False

    def legacy_writer() -> None:
        lock = tmp_path / ".agentdeck" / "state" / "protocol-mutation.lock"
        with lock.open("r+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            _legacy_write_state(tmp_path, "locked-legacy-B")
            legacy_written.set()
            assert release_legacy.wait(timeout=2)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        legacy_done.set()

    def raced_replace(source, destination, *args, **kwargs):
        nonlocal injected
        if (
            not injected
            and destination == "state.json"
            and str(source).startswith(".state.json.")
        ):
            injected = True
            _replace_legacy_lock(tmp_path, "held-by-legacy")
            worker = threading.Thread(target=legacy_writer, daemon=True)
            worker.start()
            assert legacy_written.wait(timeout=1)
            result = original_replace(source, destination, *args, **kwargs)
            a_installed.set()
            return result
        return original_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(state_module.os, "replace", raced_replace)

    def current_writer() -> None:
        try:
            store.record_chat_turn("status", "current-writer-A", None, None)
        except BaseException as exc:
            errors.append(exc)

    writer = threading.Thread(target=current_writer, daemon=True)
    writer.start()
    assert a_installed.wait(timeout=1)
    assert writer.is_alive(), "A must wait on the replacement legacy lock before rollback"
    release_legacy.set()
    writer.join(timeout=2)

    assert not writer.is_alive()
    assert legacy_done.wait(timeout=1)
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)
    assert "protocol mutation lock changed" in str(errors[0])
    canonical = StateStore.open_existing(tmp_path).load()
    assert canonical["mixed_version_writer"] == "locked-legacy-B"
    assert not any(
        item.get("message") == "current-writer-A"
        for item in canonical.get("chat_turns", [])
        if isinstance(item, dict)
    )


def test_atomic_legacy_state_write_after_initial_cas_wins_before_current_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StateStore(tmp_path)
    store.save(store.load())
    original_atomic_save = state_module._atomic_save_state_at
    injected = False

    def raced_atomic_save(state_fd: int, state: dict[str, object]) -> None:
        nonlocal injected
        if not injected:
            injected = True
            _replace_legacy_lock(tmp_path, "atomic-state-before-effect")
            lock = tmp_path / ".agentdeck" / "state" / "protocol-mutation.lock"
            with lock.open("r+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                _legacy_write_state(
                    tmp_path, "atomic-state-before-effect", atomic=True
                )
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        original_atomic_save(state_fd, state)

    monkeypatch.setattr(state_module, "_atomic_save_state_at", raced_atomic_save)

    with pytest.raises(ValueError, match="protocol mutation lock changed"):
        store.record_chat_turn("status", "current-writer-A", None, None)

    canonical = StateStore.open_existing(tmp_path).load()
    assert canonical["mixed_version_writer"] == "atomic-state-before-effect"
    assert not any(
        item.get("message") == "current-writer-A"
        for item in canonical.get("chat_turns", [])
        if isinstance(item, dict)
    )


@pytest.mark.parametrize("race_stage", ["before_replace", "post_effect"])
def test_mixed_version_journal_race_preserves_legacy_event_and_outbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    race_stage: str,
) -> None:
    store = StateStore(tmp_path)
    pending = EventRecord.create("current_pending", {})
    state = store.load()
    state["protocol_event_outbox"] = [asdict(pending)]
    store.save(state)
    journal = tmp_path / ".agentdeck" / "state" / "events.jsonl"
    original_replace = state_module.os.replace
    original_fsync = state_module.os.fsync
    injected = False
    legacy_event = EventRecord.create("legacy_event", {"writer": "B"})
    legacy_line = (
        json.dumps(asdict(legacy_event), ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")

    def inject_legacy(*, atomic: bool = False) -> None:
        nonlocal injected
        if injected:
            return
        injected = True
        _replace_legacy_lock(tmp_path, f"journal-{race_stage}")
        if atomic:
            temporary = journal.with_name(f".legacy-journal-{race_stage}.tmp")
            temporary.write_bytes(journal.read_bytes() + legacy_line)
            original_replace(temporary, journal)
        else:
            with journal.open("ab") as handle:
                handle.write(legacy_line)

    if race_stage == "before_replace":
        def raced_replace(source, destination, *args, **kwargs):
            if destination == "events.jsonl" and str(source).startswith(".events.jsonl."):
                inject_legacy()
            return original_replace(source, destination, *args, **kwargs)

        monkeypatch.setattr(state_module.os, "replace", raced_replace)
    else:
        def raced_fsync(fd: int) -> None:
            original_fsync(fd)
            if injected:
                return
            try:
                named = os.stat("events.jsonl", dir_fd=fd, follow_symlinks=False)
            except (FileNotFoundError, NotADirectoryError):
                return
            if named.st_size > 0 and pending.event_id.encode("utf-8") in journal.read_bytes():
                inject_legacy(atomic=True)

        monkeypatch.setattr(state_module.os, "fsync", raced_fsync)

    with pytest.raises(ValueError, match="protocol mutation lock changed"):
        store.flush_protocol_event_outbox()

    events = StateStore.open_existing(tmp_path).all_events()
    assert sum(item.get("event_id") == legacy_event.event_id for item in events) == 1
    assert sum(item.get("event_id") == pending.event_id for item in events) == (
        1 if race_stage == "post_effect" else 0
    )
    assert StateStore.open_existing(tmp_path).load()["protocol_event_outbox"] == [
        asdict(pending)
    ]


def test_atomic_legacy_journal_write_after_initial_cas_wins_before_current_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StateStore(tmp_path)
    pending = EventRecord.create("current_pending", {})
    state = store.load()
    state["protocol_event_outbox"] = [asdict(pending)]
    store.save(state)
    journal = tmp_path / ".agentdeck" / "state" / "events.jsonl"
    legacy_event = EventRecord.create("legacy_event", {"writer": "atomic-B"})
    legacy_line = (
        json.dumps(asdict(legacy_event), ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    original_append = state_module._append_event_journal_at
    injected = False

    def raced_append(state_fd: int, payload: bytes) -> None:
        nonlocal injected
        if not injected:
            injected = True
            _replace_legacy_lock(tmp_path, "atomic-journal-before-effect")
            lock = tmp_path / ".agentdeck" / "state" / "protocol-mutation.lock"
            with lock.open("r+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                temporary = journal.with_name(
                    ".legacy-journal-atomic-before-effect.tmp"
                )
                temporary.write_bytes(journal.read_bytes() + legacy_line)
                os.replace(temporary, journal)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        original_append(state_fd, payload)

    monkeypatch.setattr(state_module, "_append_event_journal_at", raced_append)

    with pytest.raises(ValueError, match="protocol mutation lock changed"):
        store.flush_protocol_event_outbox()

    events = StateStore.open_existing(tmp_path).all_events()
    assert sum(item.get("event_id") == legacy_event.event_id for item in events) == 1
    assert sum(item.get("event_id") == pending.event_id for item in events) == 0
    assert StateStore.open_existing(tmp_path).load()["protocol_event_outbox"] == [
        asdict(pending)
    ]


def test_identical_atomic_state_replacement_does_not_self_deadlock_or_rollback_b(
    tmp_path: Path,
) -> None:
    result = _run_race_child(_run_identical_atomic_state_race, tmp_path)

    assert result["error"] == "protocol mutation lock changed during state mutation"
    assert result["canonical_inode"] == result["b_inode"]


def test_identical_atomic_journal_replacement_does_not_self_deadlock_or_clear_outbox(
    tmp_path: Path,
) -> None:
    result = _run_race_child(_run_identical_atomic_journal_race, tmp_path)

    assert result["error"] == "protocol mutation lock changed during state mutation"
    assert result["canonical_inode"] == result["b_inode"]
    assert result["outbox"] == [result["pending"]]
