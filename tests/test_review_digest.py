"""Pure classification of a bound review commit against the branch today."""
from __future__ import annotations

from pathlib import Path

from agentdeck.review_digest import (
    REVIEW_DIGEST_STATES,
    UNVERIFIABLE_REASONS,
    classify_review_binding,
    summarize_review_bindings,
)


def test_module_is_pure() -> None:
    text = Path("src/agentdeck/review_digest.py").read_text(encoding="utf-8")
    for forbidden in ("import subprocess", "from .cli", "from .state", "from .config", "open("):
        assert forbidden not in text


def test_same_commit_is_a_match() -> None:
    assert classify_review_binding("abc123", "abc123") == {"state": "match", "reason": None}


def test_moved_branch_is_drift() -> None:
    assert classify_review_binding("abc123", "def456") == {"state": "drift", "reason": None}


def test_unrecorded_commit_is_unverifiable_not_a_match() -> None:
    assert classify_review_binding(None, "def456") == {
        "state": "unverifiable", "reason": "not_recorded",
    }


def test_unresolvable_branch_is_unverifiable() -> None:
    assert classify_review_binding("abc123", None) == {
        "state": "unverifiable", "reason": "branch_missing",
    }


def test_missing_repository_beats_the_other_reasons() -> None:
    assert classify_review_binding("abc123", None, git_available=False) == {
        "state": "unverifiable", "reason": "no_git_repo",
    }


def test_states_and_reasons_are_closed() -> None:
    assert REVIEW_DIGEST_STATES == ("match", "drift", "unverifiable")
    assert UNVERIFIABLE_REASONS == ("not_recorded", "branch_missing", "no_git_repo")


def _item(**kw):
    base = {
        "message_id": "msg_1", "agent_id": "reviewer", "step": 3,
        "base_branch": "agentdeck/coder/msg_0", "base_commit": "abc123",
        "resolved_commit": "abc123",
    }
    base.update(kw)
    return base


def test_summary_counts_and_stays_silent_when_everything_matches() -> None:
    summary = summarize_review_bindings([_item(), _item(message_id="msg_2", step=4)])
    assert summary["count"] == 2
    assert summary["match"] == 2
    assert summary["drift"] == 0
    assert summary["unverifiable"] == 0
    assert summary["blocker"] is None
    assert [b["state"] for b in summary["bindings"]] == ["match", "match"]


def test_summary_blocks_on_drift_and_names_both_commits() -> None:
    summary = summarize_review_bindings([_item(resolved_commit="def4567890")])
    assert summary["drift"] == 1
    assert "agentdeck/coder/msg_0" in summary["blocker"]
    assert "abc123" in summary["blocker"]
    assert "def4567" in summary["blocker"]
    assert "auto-merge withheld" in summary["blocker"]


def test_summary_blocks_when_a_recorded_binding_cannot_be_verified() -> None:
    summary = summarize_review_bindings([_item(resolved_commit=None)])
    assert summary["unverifiable"] == 1
    assert "cannot verify" in summary["blocker"]


def test_summary_does_not_block_on_plans_that_predate_the_binding() -> None:
    # An unrecorded binding is the ONE deliberate fail-open: blocking it would
    # withhold every in-flight plan. It must still read as "not recorded".
    summary = summarize_review_bindings([_item(base_commit=None, resolved_commit="def456")])
    assert summary["unverifiable"] == 1
    assert summary["blocker"] is None
    assert summary["bindings"][0]["reason"] == "not_recorded"


def test_drift_outranks_an_unverifiable_sibling_in_the_blocker() -> None:
    # Two problems at once: the sentence must name the one that proves the code
    # moved, not the one that merely could not be read.
    summary = summarize_review_bindings([
        _item(step=3, resolved_commit=None),
        _item(message_id="msg_2", step=4, resolved_commit="def4567890"),
    ])
    assert summary["drift"] == 1
    assert summary["unverifiable"] == 1
    assert "but that branch is now at" in summary["blocker"]


def test_no_bindings_is_not_a_blocker() -> None:
    summary = summarize_review_bindings([])
    assert summary["count"] == 0
    assert summary["blocker"] is None
