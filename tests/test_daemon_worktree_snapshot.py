from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentdeck.daemon.service import ServiceError, _bounded_worktree_snapshot


def test_worktree_snapshot_prunes_daemon_and_git_trees_before_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "visible.txt").write_text("visible\n", encoding="utf-8")
    (tmp_path / ".git" / "objects").mkdir(parents=True)
    (tmp_path / ".git" / "objects" / "secret").write_bytes(b"git payload")
    (tmp_path / ".agentdeck" / "state").mkdir(parents=True)
    (tmp_path / ".agentdeck" / "state" / "secret").write_bytes(
        b"daemon payload"
    )
    excluded_inodes = {
        (tmp_path / ".git").stat().st_ino,
        (tmp_path / ".agentdeck").stat().st_ino,
    }
    original_scandir = os.scandir

    def forbidden_rglob(_self: Path, _pattern: str):
        raise AssertionError("snapshot must not enumerate the full tree with rglob")

    def checked_scandir(path: str | bytes | os.PathLike[str] | int | None = None):
        if isinstance(path, int) and os.fstat(path).st_ino in excluded_inodes:
            raise AssertionError("snapshot traversed an excluded root")
        return original_scandir(path)

    monkeypatch.setattr(Path, "rglob", forbidden_rglob)
    monkeypatch.setattr(os, "scandir", checked_scandir)

    snapshot = _bounded_worktree_snapshot(tmp_path)

    assert list(snapshot["manifest"]) == ["visible.txt"]


def test_worktree_snapshot_rejects_oversize_file_before_payload_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    oversized = tmp_path / "oversized.bin"
    oversized.write_bytes(b"x" * (32 * 1024 * 1024 + 1))
    original_read_bytes = Path.read_bytes

    def forbidden_payload_read(path: Path) -> bytes:
        if path == oversized:
            raise AssertionError("oversize payload was read")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", forbidden_payload_read)

    with pytest.raises(ServiceError, match="exceeds bounded limits"):
        _bounded_worktree_snapshot(tmp_path)


def test_worktree_snapshot_rejects_more_than_4096_items(tmp_path: Path) -> None:
    for index in range(4097):
        (tmp_path / f"item-{index:04d}").touch()

    with pytest.raises(ServiceError, match="worktree is unsupported"):
        _bounded_worktree_snapshot(tmp_path)


def test_worktree_snapshot_never_reads_symlink_swapped_external_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    victim = root / "victim.txt"
    victim.write_text("safe\n", encoding="utf-8")
    external = tmp_path / "external.txt"
    external.write_text("external secret\n", encoding="utf-8")
    external_read = False
    swapped = False
    original_read_bytes = Path.read_bytes
    original_open = os.open

    def race_old_path_reader(path: Path) -> bytes:
        nonlocal external_read, swapped
        if path == victim:
            victim.unlink()
            victim.symlink_to(external)
            swapped = True
            external_read = True
        return original_read_bytes(path)

    def race_anchored_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if path == "victim.txt" and dir_fd is not None and not swapped:
            victim.unlink()
            victim.symlink_to(external)
            swapped = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(Path, "read_bytes", race_old_path_reader)
    monkeypatch.setattr(os, "open", race_anchored_open)

    with pytest.raises(ServiceError, match="worktree is unsupported"):
        _bounded_worktree_snapshot(root)

    assert swapped is True
    assert external_read is False
