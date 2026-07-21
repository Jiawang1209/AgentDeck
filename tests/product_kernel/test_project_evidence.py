from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from agentdeck.adapters.project_evidence import (
    GitProjectEvidenceSource, ProjectEvidenceError,
)
from agentdeck.ports.project_evidence import ProjectEvidence


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ("git", "-C", str(root), *arguments), check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "AgentDeck Test")
    _git(tmp_path, "config", "user.email", "agentdeck@example.invalid")
    (tmp_path / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-qm", "initial")
    return tmp_path


@pytest.mark.parametrize("mutation", ("head", "index", "tracked", "untracked"))
def test_git_evidence_changes_for_every_project_drift(repo: Path, mutation: str) -> None:
    source = GitProjectEvidenceSource(project_root=repo, project_id="prj_1")
    baseline = source.capture()
    if mutation == "head":
        (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
        _git(repo, "add", "tracked.txt")
        _git(repo, "commit", "-qm", "next")
        field = "head_digest"
    elif mutation == "index":
        (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
        _git(repo, "add", "tracked.txt")
        field = "index_digest"
    elif mutation == "tracked":
        (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
        field = "tracked_worktree_digest"
    else:
        (repo / "untracked.txt").write_text("secret contents\n", encoding="utf-8")
        field = "untracked_names_digest"
    changed = source.capture()
    assert getattr(changed, field) != getattr(baseline, field)
    assert changed.digest != baseline.digest
    assert str(repo) not in repr(changed)
    assert "secret contents" not in repr(changed)


def test_mission_hash_cannot_construct_project_evidence() -> None:
    with pytest.raises(ValueError, match="provenance"):
        ProjectEvidence.from_untyped_digest("a" * 64)


def test_adapter_uses_only_bounded_argv_calls(repo: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(argv: tuple[str, ...]):
        calls.append(argv)
        return subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    evidence = GitProjectEvidenceSource(
        project_root=repo, project_id="prj_1", runner=runner,
    ).capture()
    assert evidence.provenance == "git-project-evidence/v1"
    assert calls and all(type(argv) is tuple and argv[:1] == ("git",) for argv in calls)
    assert not any("shell" in argument for argv in calls for argument in argv)


def test_non_git_and_symlink_roots_fail_content_free(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(ProjectEvidenceError) as raised:
        GitProjectEvidenceSource(project_root=plain, project_id="prj_1").capture()
    assert str(raised.value) in ProjectEvidenceError.ALLOWED
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ProjectEvidenceError):
        GitProjectEvidenceSource(project_root=link, project_id="prj_1")


def test_oversize_git_output_fails_without_echo(repo: Path) -> None:
    secret = b"secret-git-output"

    def runner(_argv: tuple[str, ...]):
        return subprocess.CompletedProcess((), 0, secret * (128 * 1024), b"")

    with pytest.raises(ProjectEvidenceError) as raised:
        GitProjectEvidenceSource(
            project_root=repo, project_id="prj_1", runner=runner,
        ).capture()
    assert str(raised.value) in ProjectEvidenceError.ALLOWED
    assert "secret-git-output" not in str(raised.value)

