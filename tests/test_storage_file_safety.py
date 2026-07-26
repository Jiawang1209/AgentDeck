from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentdeck.storage.file_safety import (
    FileSafetyError,
    create_private_file,
    file_identity,
    require_file_identity,
    require_safe_file,
)


def test_regular_file_passes_and_identity_is_stable(tmp_path: Path) -> None:
    target = tmp_path / "state.db"
    target.write_bytes(b"")
    identity = require_safe_file(target, "database")
    assert identity == file_identity(target)
    require_file_identity(target, identity)


def test_symlink_is_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real.db"
    real.write_bytes(b"")
    link = tmp_path / "link.db"
    link.symlink_to(real)
    with pytest.raises(FileSafetyError, match="symlink"):
        require_safe_file(link, "database")


def test_hard_link_is_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real.db"
    real.write_bytes(b"")
    hard = tmp_path / "hard.db"
    os.link(real, hard)
    with pytest.raises(FileSafetyError, match="hard link"):
        require_safe_file(hard, "database")


def test_identity_change_is_detected(tmp_path: Path) -> None:
    target = tmp_path / "state.db"
    target.write_bytes(b"")
    identity = require_safe_file(target, "database")
    target.unlink()
    target.write_bytes(b"")  # 新 inode
    with pytest.raises(FileSafetyError, match="identity"):
        require_file_identity(target, identity)


def test_create_private_file_is_exclusive_and_0600(tmp_path: Path) -> None:
    target = tmp_path / "state.db"
    identity = create_private_file(target)
    assert identity == file_identity(target)
    assert (target.stat().st_mode & 0o777) == 0o600
    with pytest.raises(FileExistsError):
        create_private_file(target)
