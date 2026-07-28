from __future__ import annotations

import pytest

from agentdeck.models import AgentSpec, LeaderConfig, ProjectConfig, RuntimeConfig
from agentdeck.providers.base import (
    LeaderPlanRequest,
    leader_planner_brief_prompt_lines,
)
from agentdeck.providers.cli_subprocess import ClaudeCliProvider, CodexCliProvider
from agentdeck.providers.openai_compatible import OpenAICompatibleProvider


def _config() -> ProjectConfig:
    return ProjectConfig(
        name="demo",
        root="/tmp/demo",
        leader=LeaderConfig(),
        agents=(
            AgentSpec(agent_id="planner", role="planning", provider="codex", command="codex"),
            AgentSpec(agent_id="coder", role="coding", provider="claude", command="claude"),
        ),
        runtime=RuntimeConfig(),
    )


def _brief() -> dict[str, object]:
    return {
        "goal": "完成 README 自动化",
        "acceptance_criteria": ["README 包含新命令", "测试全绿"],
        "risks": ["文档漂移"],
        "macro_steps": ["梳理命令", "更新文档", "验证"],
    }


def _prompts(request: LeaderPlanRequest) -> list[str]:
    return [
        OpenAICompatibleProvider()._system_prompt(request),
        CodexCliProvider()._prompt(request),
        ClaudeCliProvider()._prompt(request),
    ]


def test_real_provider_prompts_embed_planner_brief() -> None:
    request = LeaderPlanRequest(task="t", config=_config(), planner_brief=_brief())
    for prompt in _prompts(request):
        assert "Planner brief" in prompt
        assert "完成 README 自动化" in prompt
        assert "README 包含新命令" in prompt
        assert "not execution authorization" in prompt


def test_real_provider_prompts_unchanged_without_brief() -> None:
    request = LeaderPlanRequest(task="t", config=_config())
    for prompt in _prompts(request):
        assert "Planner brief" not in prompt


def test_brief_prompt_lines_empty_for_none() -> None:
    assert leader_planner_brief_prompt_lines(None) == []


def test_brief_prompt_lines_ignore_non_dict() -> None:
    assert leader_planner_brief_prompt_lines("brief") == []  # type: ignore[arg-type]


def test_brief_prompt_lines_compact_and_guarded() -> None:
    lines = leader_planner_brief_prompt_lines(_brief())
    joined = "\n".join(lines)
    assert "完成 README 自动化" in joined
    assert "macro_steps" in joined
    assert "not execution authorization" in joined
