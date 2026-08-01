from __future__ import annotations

from pathlib import Path

import pytest

from agentdeck.config import load_config, update_leader_approval_mode, write_default_config


def _config_with(root: Path, block: str) -> None:
    write_default_config(root)
    path = root / ".agentdeck" / "config.toml"
    path.write_text(path.read_text(encoding="utf-8") + "\n" + block, encoding="utf-8")


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


def test_review_config_defaults_are_empty(tmp_path: Path) -> None:
    root = _root(tmp_path)
    write_default_config(root)
    review = load_config(root).review
    assert review.round_reviewer is None
    assert review.reviewers == ()


def test_review_config_parses_both_keys(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _config_with(root, '[review]\nround_reviewer = "planner"\nreviewers = ["reviewer", "planner"]\n')
    review = load_config(root).review
    assert review.round_reviewer == "planner"
    assert review.reviewers == ("reviewer", "planner")


@pytest.mark.parametrize(
    "block",
    [
        '[review]\nround_reviewer = "ghost"\n',            # unknown agent
        '[review]\nreviewers = ["reviewer", "ghost"]\n',   # unknown member
        '[review]\nround_reviewer = ""\n',                 # empty string
        '[review]\nreviewers = ["reviewer", "reviewer"]\n',# duplicate member
        '[review]\nreviewers = "reviewer"\n',              # not a list
        '[review]\nreviewers = [""]\n',                    # empty member
        '[review]\nround_reviewer = 7\n',                  # wrong type
    ],
)
def test_review_config_fails_closed(tmp_path: Path, block: str) -> None:
    root = _root(tmp_path)
    _config_with(root, block)
    with pytest.raises(ValueError):
        load_config(root)


def test_empty_reviewers_list_is_allowed_and_means_off(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _config_with(root, "[review]\nreviewers = []\n")
    assert load_config(root).review.reviewers == ()


def test_KNOWN_GAP_review_section_is_dropped_by_config_writer_round_trip(
    tmp_path: Path,
) -> None:
    """Documents a pre-existing gap: `_dump_config` in config.py only
    re-emits a fixed whitelist of top-level tables (project/leader/agents/
    runtime/autonomous/skills). It does not know about `[review]` (nor
    `[daemon]`, which has the same gap already), so any writer that
    round-trips through `_dump_config(raw)` — e.g. `update_leader_approval_mode`,
    which backs `agentdeck policy set-mode` — silently drops a configured
    `[review]` section instead of preserving it.

    This test pins the *current* (unfortunate) behaviour so it cannot regress
    further unnoticed. Fixing `_dump_config` to preserve `[review]` is a
    deliberate design decision left to the human/plan owner — flagged here
    per the Task 1 discipline of "report the finding, do not silently
    redesign".
    """
    root = _root(tmp_path)
    _config_with(
        root,
        '[review]\nround_reviewer = "planner"\nreviewers = ["reviewer", "planner"]\n',
    )
    assert load_config(root).review.round_reviewer == "planner"

    update_leader_approval_mode(root, "approve")

    # KNOWN GAP: the [review] section did not survive the round trip.
    assert load_config(root).review.round_reviewer is None
    assert load_config(root).review.reviewers == ()
