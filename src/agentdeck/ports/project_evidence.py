"""Closed proof value for exact Git project state."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Protocol


_ID = re.compile(r"prj_[a-z0-9_]{1,250}", re.ASCII)
_DIGEST = re.compile(r"[0-9a-f]{64}", re.ASCII)
_PROVENANCE = "git-project-evidence/v1"


@dataclass(frozen=True)
class ProjectEvidence:
    provenance: str
    project_id: str
    root_identity_digest: str
    head_digest: str
    index_digest: str
    tracked_worktree_digest: str
    untracked_names_digest: str
    digest: str

    def __post_init__(self) -> None:
        if self.provenance != _PROVENANCE:
            raise ValueError("project evidence provenance is invalid")
        if type(self.project_id) is not str or not _ID.fullmatch(self.project_id):
            raise ValueError("project evidence identity is invalid")
        components = (
            self.root_identity_digest, self.head_digest, self.index_digest,
            self.tracked_worktree_digest, self.untracked_names_digest,
        )
        if any(type(value) is not str or not _DIGEST.fullmatch(value) for value in components):
            raise ValueError("project evidence digest is invalid")
        if type(self.digest) is not str or not _DIGEST.fullmatch(self.digest):
            raise ValueError("project evidence digest is invalid")
        if self.digest != project_evidence_digest(
            self.provenance, self.project_id, *components,
        ):
            raise ValueError("project evidence digest does not match its provenance")

    @classmethod
    def from_untyped_digest(cls, _digest: str) -> "ProjectEvidence":
        raise ValueError("project evidence requires typed Git provenance")


def project_evidence_digest(
    provenance: str, project_id: str, root_identity_digest: str,
    head_digest: str, index_digest: str, tracked_worktree_digest: str,
    untracked_names_digest: str,
) -> str:
    canonical = json.dumps({
        "head_digest": head_digest, "index_digest": index_digest,
        "project_id": project_id, "provenance": provenance,
        "root_identity_digest": root_identity_digest,
        "tracked_worktree_digest": tracked_worktree_digest,
        "untracked_names_digest": untracked_names_digest,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8", "strict")
    return sha256(canonical).hexdigest()


class ProjectEvidenceSource(Protocol):
    def capture(self) -> ProjectEvidence: ...


__all__ = [
    "ProjectEvidence", "ProjectEvidenceSource", "project_evidence_digest",
]
