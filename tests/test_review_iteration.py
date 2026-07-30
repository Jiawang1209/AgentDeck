from __future__ import annotations

from pathlib import Path

import pytest

from agentdeck.config import load_config, write_default_config


def _write_config(root: Path, autonomous_block: str) -> None:
    write_default_config(root)
    path = root / ".agentdeck" / "config.toml"
    path.write_text(
        path.read_text(encoding="utf-8") + "\n[autonomous]\n" + autonomous_block,
        encoding="utf-8",
    )


def test_max_review_rounds_defaults_to_2(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_default_config(root)
    config = load_config(root)
    assert config.autonomous.max_review_rounds == 2


def test_max_review_rounds_parses_from_config(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_config(root, 'allowed_agents = ["coder"]\nmax_approvals = 3\nmax_review_rounds = 5\n')
    config = load_config(root)
    assert config.autonomous.max_review_rounds == 5


def test_max_review_rounds_zero_is_valid(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_config(root, "max_review_rounds = 0\n")
    assert load_config(root).autonomous.max_review_rounds == 0


@pytest.mark.parametrize("bad", ['max_review_rounds = -1\n', 'max_review_rounds = "two"\n'])
def test_max_review_rounds_invalid_fails_closed(tmp_path: Path, bad: str) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_config(root, bad)
    with pytest.raises(ValueError):
        load_config(root)
