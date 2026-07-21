"""Argv-only bounded Git Adapter for exact project evidence."""

from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
import os
import stat
import subprocess

from agentdeck.ports.project_evidence import (
    ProjectEvidence, project_evidence_digest,
)


_MAX_OUTPUT = 1024 * 1024
_PROVENANCE = "git-project-evidence/v1"
Runner = Callable[[tuple[str, ...]], object]


class ProjectEvidenceError(RuntimeError):
    ALLOWED = frozenset({
        "project_evidence_root_invalid", "project_evidence_root_drifted",
        "project_evidence_git_unavailable", "project_evidence_git_failed",
        "project_evidence_output_invalid", "project_evidence_output_oversize",
    })

    def __init__(self, code: str) -> None:
        if code not in self.ALLOWED:
            raise ValueError("unknown project evidence error")
        self.code = code
        super().__init__(code)


def _default_runner(argv: tuple[str, ...]) -> object:
    return subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class GitProjectEvidenceSource:
    def __init__(
        self, *, project_root: Path, project_id: str, runner: Runner = _default_runner,
    ) -> None:
        root = Path(project_root)
        if root.is_symlink() or not root.is_dir():
            raise ProjectEvidenceError("project_evidence_root_invalid")
        resolved = root.resolve(strict=True)
        if root.absolute() != resolved:
            raise ProjectEvidenceError("project_evidence_root_invalid")
        if type(project_id) is not str:
            raise ProjectEvidenceError("project_evidence_root_invalid")
        if not callable(runner):
            raise TypeError("Git evidence runner must be callable")
        self._root, self._project_id, self._runner = resolved, project_id, runner
        self._identity = self._root_identity()
        root_digest = sha256(
            f"{self._identity[0]}:{self._identity[1]}:{project_id}".encode("ascii")
        ).hexdigest()
        self._root_digest = root_digest

    def capture(self) -> ProjectEvidence:
        self._require_identity()
        root_output = self._run("rev-parse", "--show-toplevel")
        try:
            reported = Path(root_output.rstrip(b"\n").decode("utf-8", "strict"))
        except (UnicodeError, ValueError):
            raise ProjectEvidenceError("project_evidence_output_invalid") from None
        if reported != self._root:
            raise ProjectEvidenceError("project_evidence_root_drifted")
        head = self._run("rev-parse", "--verify", "HEAD")
        index = self._run("ls-files", "--stage", "-z")
        tracked = self._run("diff", "--no-ext-diff", "--binary", "--no-color", "--")
        untracked = self._run("ls-files", "--others", "--exclude-standard", "-z")
        self._require_identity()
        parts = tuple(sha256(value).hexdigest() for value in (
            head, index, tracked, untracked,
        ))
        digest = project_evidence_digest(
            _PROVENANCE, self._project_id, self._root_digest, *parts,
        )
        return ProjectEvidence(
            _PROVENANCE, self._project_id, self._root_digest, *parts, digest,
        )

    def _run(self, *arguments: str) -> bytes:
        argv = ("git", "-C", str(self._root), *arguments)
        try:
            result = self._runner(argv)
            returncode = result.returncode
            stdout = result.stdout
        except Exception:
            raise ProjectEvidenceError("project_evidence_git_unavailable") from None
        if type(returncode) is not int or returncode != 0:
            raise ProjectEvidenceError("project_evidence_git_failed")
        if type(stdout) is not bytes:
            raise ProjectEvidenceError("project_evidence_output_invalid")
        if len(stdout) > _MAX_OUTPUT:
            raise ProjectEvidenceError("project_evidence_output_oversize")
        return stdout

    def _root_identity(self) -> tuple[int, int]:
        try:
            info = self._root.lstat()
        except OSError:
            raise ProjectEvidenceError("project_evidence_root_invalid") from None
        if (
            self._root.is_symlink() or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
        ):
            raise ProjectEvidenceError("project_evidence_root_invalid")
        return info.st_dev, info.st_ino

    def _require_identity(self) -> None:
        try:
            current = self._root_identity()
        except ProjectEvidenceError:
            raise ProjectEvidenceError("project_evidence_root_drifted") from None
        if current != self._identity:
            raise ProjectEvidenceError("project_evidence_root_drifted")


__all__ = ["GitProjectEvidenceSource", "ProjectEvidenceError"]
