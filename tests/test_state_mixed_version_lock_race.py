from __future__ import annotations

from dataclasses import asdict
import fcntl
import json
import os
from pathlib import Path
import threading

import pytest

from agentdeck.models import EventRecord
from agentdeck import state as state_module
from agentdeck.state import StateStore


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
