from __future__ import annotations

from pathlib import Path

import pytest

from agentdeck.config import (
    leader_split_enabled,
    load_config,
    resolved_orchestrator_backend,
    resolved_planner_backend,
    write_default_config,
)
from agentdeck.models import LeaderSubroleConfig


def _write_config(tmp_path: Path, extra: str = "") -> Path:
    write_default_config(tmp_path)
    if extra:
        config_path = tmp_path / ".agentdeck" / "config.toml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8") + "\n" + extra,
            encoding="utf-8",
        )
    return tmp_path


def test_default_config_has_no_subroles_and_falls_back(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    leader = config.leader
    assert leader.planner is None
    assert leader.orchestrator is None
    assert leader_split_enabled(leader) is False
    assert resolved_planner_backend(leader) == (leader.provider, leader.model)
    assert resolved_orchestrator_backend(leader) == (leader.provider, leader.model)


def test_planner_and_orchestrator_sections_parse(tmp_path: Path) -> None:
    extra = (
        "[leader.planner]\n"
        'provider = "codex-cli"\n'
        'model = "gpt-5.5"\n'
        "\n"
        "[leader.orchestrator]\n"
        'provider = "claude-cli"\n'
        'model = "claude-opus-5"\n'
    )
    leader = load_config(_write_config(tmp_path, extra)).leader
    assert leader.planner == LeaderSubroleConfig(provider="codex-cli", model="gpt-5.5")
    assert leader.orchestrator == LeaderSubroleConfig(
        provider="claude-cli", model="claude-opus-5"
    )
    assert leader_split_enabled(leader) is True
    assert resolved_planner_backend(leader) == ("codex-cli", "gpt-5.5")
    assert resolved_orchestrator_backend(leader) == ("claude-cli", "claude-opus-5")


def test_partial_subrole_falls_back_per_field(tmp_path: Path) -> None:
    extra = "[leader.planner]\n" 'provider = "codex-cli"\n'
    leader = load_config(_write_config(tmp_path, extra)).leader
    assert leader.planner == LeaderSubroleConfig(provider="codex-cli", model=None)
    assert leader.orchestrator is None
    assert leader_split_enabled(leader) is True
    assert resolved_planner_backend(leader) == ("codex-cli", leader.model)
    assert resolved_orchestrator_backend(leader) == (leader.provider, leader.model)


def test_empty_subrole_section_enables_split_with_full_fallback(tmp_path: Path) -> None:
    extra = "[leader.orchestrator]\n"
    leader = load_config(_write_config(tmp_path, extra)).leader
    assert leader.orchestrator == LeaderSubroleConfig(provider=None, model=None)
    assert leader_split_enabled(leader) is True
    assert resolved_orchestrator_backend(leader) == (leader.provider, leader.model)


def test_non_table_subrole_fails_closed(tmp_path: Path) -> None:
    write_default_config(tmp_path)
    config_path = tmp_path / ".agentdeck" / "config.toml"
    text = config_path.read_text(encoding="utf-8")
    text = text.replace("[leader]\n", '[leader]\nplanner = "codex-cli"\n', 1)
    config_path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="invalid Leader planner configuration"):
        load_config(tmp_path)


def test_unknown_subrole_key_fails_closed(tmp_path: Path) -> None:
    extra = "[leader.planner]\n" 'providr = "codex-cli"\n'
    with pytest.raises(ValueError, match="invalid Leader planner configuration"):
        load_config(_write_config(tmp_path, extra))


@pytest.mark.parametrize(
    "value",
    ['provider = ""', "provider = 3", 'model = ""', "model = 17"],
)
def test_invalid_subrole_values_fail_closed(tmp_path: Path, value: str) -> None:
    extra = "[leader.orchestrator]\n" + value + "\n"
    with pytest.raises(ValueError, match="invalid Leader orchestrator configuration"):
        load_config(_write_config(tmp_path, extra))
