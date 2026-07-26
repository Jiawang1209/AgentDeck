"""Filesystem identity safety for SQLite database files.

Ported from the rewrite donor (`adapters/sqlite_schema.py`, commit 6790e29f):
refuse symlinks and hard links, create database files exclusively with 0600,
and detect inode swaps between open and transaction time.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

FileIdentity = tuple[int, int, bool, bool]


class FileSafetyError(ValueError):
    """A database file failed filesystem identity or safety checks."""


def file_identity(path: Path) -> FileIdentity:
    info = path.lstat()
    return (info.st_dev, info.st_ino, stat.S_ISREG(info.st_mode), info.st_nlink == 1)


def require_safe_file(path: Path, label: str) -> FileIdentity:
    identity = file_identity(path)
    if path.is_symlink():
        raise FileSafetyError(f"{label} path must not be a symlink")
    if not identity[2]:
        raise FileSafetyError(f"{label} path must be a regular file")
    if not identity[3]:
        raise FileSafetyError(f"{label} path must not be a hard link")
    return identity


def create_private_file(path: Path) -> FileIdentity:
    """Create the file exclusively (0600, O_NOFOLLOW) and return its identity."""
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    os.close(descriptor)
    return require_safe_file(path, "database")


def require_file_identity(path: Path, expected: FileIdentity) -> None:
    try:
        current = file_identity(path)
    except OSError as error:
        raise FileSafetyError("file identity is unavailable") from error
    if current != expected or not current[2] or not current[3]:
        raise FileSafetyError("file identity changed during open or transaction")
