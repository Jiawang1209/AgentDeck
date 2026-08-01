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


def test_review_section_survives_config_writer_round_trip(tmp_path: Path) -> None:
    """`_dump_config` 是白名单式序列化;任何经它回写的 writer(例如
    `policy set-mode` 背后的 `update_leader_approval_mode`)都必须保留
    它不认识的配置段,否则 `[review]` 活不过一次模式切换。"""
    root = _root(tmp_path)
    _config_with(
        root,
        '[review]\nround_reviewer = "planner"\nreviewers = ["reviewer", "planner"]\n',
    )
    assert load_config(root).review.round_reviewer == "planner"

    update_leader_approval_mode(root, "approve")

    review = load_config(root).review
    assert review.round_reviewer == "planner"
    assert review.reviewers == ("reviewer", "planner")


def test_config_writers_preserve_leader_subroles_and_daemon(tmp_path: Path) -> None:
    """同一个白名单缺口此前已在吞掉 G2 的 `[leader.planner]` /
    `[leader.orchestrator]` 与 `[daemon]`——已落地功能的静默数据丢失。"""
    from agentdeck.config import update_autonomous_policy, update_leader_provider

    root = _root(tmp_path)
    _config_with(
        root,
        '[leader.planner]\nprovider = "deepseek"\nmodel = "deepseek-v4-pro"\n\n'
        '[leader.orchestrator]\nprovider = "claude-cli"\nmodel = "claude-fable-5"\n\n'
        "[daemon]\nidle_grace_seconds = 42\n",
    )
    before = load_config(root)
    assert before.leader.planner is not None
    assert before.daemon.idle_grace_seconds == 42

    update_leader_approval_mode(root, "approve")
    update_autonomous_policy(root, ("coder",), 3)
    update_leader_provider(root, "deepseek", "deepseek-v4-pro")

    after = load_config(root)
    assert after.leader.planner == before.leader.planner
    assert after.leader.orchestrator == before.leader.orchestrator
    assert after.daemon.idle_grace_seconds == 42


def test_config_writers_preserve_unknown_sections(tmp_path: Path) -> None:
    """未知/未来配置段也必须原样保留(白名单不该成为数据丢失面)。"""
    import tomllib

    root = _root(tmp_path)
    _config_with(root, '[future_thing]\nflag = true\ncount = 7\nnames = ["a", "b"]\n')
    update_leader_approval_mode(root, "approve")
    raw = tomllib.loads((root / ".agentdeck" / "config.toml").read_text(encoding="utf-8"))
    assert raw["future_thing"] == {"flag": True, "count": 7, "names": ["a", "b"]}
