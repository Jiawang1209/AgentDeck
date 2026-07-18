from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from agentdeck.storage import (
    ProjectWriterLease,
    SQLiteMissionStore,
    WriterLeaseError,
)


def test_second_writer_is_rejected_with_stable_diagnostic(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    with ProjectWriterLease.acquire(root):
        with pytest.raises(
            WriterLeaseError,
            match="^another project writer is active$",
        ):
            ProjectWriterLease.acquire(root)


def test_lease_lock_is_owner_only(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    with ProjectWriterLease.acquire(root) as lease:
        assert lease.active is True
        assert stat.S_IMODE(
            (root / ".agentdeck" / "state.db.lock").stat().st_mode
        ) == 0o600
    assert lease.active is False


def test_closed_foreign_and_fake_leases_cannot_open_mutating_store(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    closed = ProjectWriterLease.acquire(first)
    closed.close()
    with pytest.raises(WriterLeaseError, match="active matching writer lease required"):
        SQLiteMissionStore.create(first, lease=closed, project_id="prj_1")

    with ProjectWriterLease.acquire(first) as foreign:
        with pytest.raises(
            WriterLeaseError,
            match="active matching writer lease required",
        ):
            SQLiteMissionStore.create(second, lease=foreign, project_id="prj_2")

    with pytest.raises(WriterLeaseError, match="active matching writer lease required"):
        SQLiteMissionStore.create(
            first,
            lease=object(),  # type: ignore[arg-type]
            project_id="prj_1",
        )


def test_one_lease_cannot_expose_two_writer_store_connections(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    with ProjectWriterLease.acquire(root) as lease:
        first = SQLiteMissionStore.create(root, lease=lease, project_id="prj_1")
        second: SQLiteMissionStore | None = None
        try:
            with pytest.raises(
                WriterLeaseError,
                match="writer lease already owns a store",
            ):
                second = SQLiteMissionStore.open(root, lease=lease)
        finally:
            if second is not None:
                second.close()
            first.close()

        with SQLiteMissionStore.open(root, lease=lease) as reopened:
            assert reopened.project_id == "prj_1"


def test_lease_cannot_close_or_release_flock_while_store_is_active(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    lease = ProjectWriterLease.acquire(root)
    store = SQLiteMissionStore.create(root, lease=lease, project_id="prj_1")
    try:
        with pytest.raises(
            WriterLeaseError,
            match="^writer lease owns an active store$",
        ) as raised:
            lease.close()
        assert raised.value.__cause__ is None
        assert lease.active is True
        with pytest.raises(
            WriterLeaseError,
            match="^another project writer is active$",
        ):
            ProjectWriterLease.acquire(root)
    finally:
        store.close()
        lease.close()

    with ProjectWriterLease.acquire(root) as replacement:
        assert replacement.active is True


def test_replaced_project_root_invalidates_lease(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    displaced = tmp_path / "displaced"

    lease = ProjectWriterLease.acquire(root)
    try:
        root.rename(displaced)
        root.mkdir()
        with pytest.raises(
            WriterLeaseError,
            match="active matching writer lease required",
        ):
            SQLiteMissionStore.create(root, lease=lease, project_id="prj_1")
    finally:
        lease.close()


@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_unsafe_lock_path_fails_closed(tmp_path: Path, kind: str) -> None:
    root = tmp_path / kind
    root.mkdir()
    state_dir = root / ".agentdeck"
    state_dir.mkdir(mode=0o700)
    lock_path = state_dir / "state.db.lock"
    if kind == "symlink":
        target = tmp_path / "target"
        target.write_text("outside", encoding="utf-8")
        lock_path.symlink_to(target)
    else:
        lock_path.mkdir()

    with pytest.raises(
        WriterLeaseError,
        match="writer lease path invalid",
    ) as raised:
        ProjectWriterLease.acquire(root)
    assert raised.value.__cause__ is None
    assert str(root) not in str(raised.value)


def test_symlinked_state_directory_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / ".agentdeck").symlink_to(outside, target_is_directory=True)

    with pytest.raises(WriterLeaseError, match="writer lease path invalid"):
        ProjectWriterLease.acquire(root)


def test_lock_replacement_invalidates_active_lease(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    lease = ProjectWriterLease.acquire(root)
    lock_path = root / ".agentdeck" / "state.db.lock"
    old_lock = root / ".agentdeck" / "old.lock"
    try:
        lock_path.rename(old_lock)
        lock_path.write_bytes(b"replacement")
        os.chmod(lock_path, 0o600)
        with pytest.raises(
            WriterLeaseError,
            match="active matching writer lease required",
        ):
            SQLiteMissionStore.create(root, lease=lease, project_id="prj_1")
    finally:
        lease.close()


def test_state_directory_replacement_invalidates_active_lease(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    lease = ProjectWriterLease.acquire(root)
    state_dir = root / ".agentdeck"
    displaced = root / ".agentdeck-displaced"
    try:
        state_dir.rename(displaced)
        state_dir.mkdir(mode=0o700)
        os.link(displaced / "state.db.lock", state_dir / "state.db.lock")
        with pytest.raises(
            WriterLeaseError,
            match="active matching writer lease required",
        ):
            SQLiteMissionStore.create(root, lease=lease, project_id="prj_1")
    finally:
        lease.close()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires os.fork")
def test_fork_child_cleanup_cannot_release_parent_writer_lock(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    lease = ProjectWriterLease.acquire(root)
    store = SQLiteMissionStore.create(root, lease=lease, project_id="prj_1")
    read_fd, write_fd = os.pipe()
    child_pid = os.fork()
    if child_pid == 0:
        os.close(read_fd)
        validation_failed = False
        try:
            try:
                _ = store.project_id
            except WriterLeaseError as exc:
                validation_failed = str(exc) == "writer lease process mismatch"
            store.close()
            lease.close()
            os.write(write_fd, b"1" if validation_failed else b"0")
        except BaseException:
            os.write(write_fd, b"E")
        finally:
            os.close(write_fd)
            os._exit(0)

    os.close(write_fd)
    child_result = os.read(read_fd, 1)
    os.close(read_fd)
    _, child_status = os.waitpid(child_pid, 0)
    assert os.WIFEXITED(child_status)
    assert child_result == b"1"

    competing: ProjectWriterLease | None = None
    try:
        with pytest.raises(
            WriterLeaseError,
            match="^another project writer is active$",
        ):
            competing = ProjectWriterLease.acquire(root)
    finally:
        if competing is not None:
            competing.close()
        store.close()
        lease.close()

    with ProjectWriterLease.acquire(root) as replacement:
        assert replacement.active is True
