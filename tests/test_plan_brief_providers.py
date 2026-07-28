from __future__ import annotations

import json
import subprocess

import pytest

from agentdeck.providers.cli_subprocess import (
    ClaudeCliProvider,
    CliLeaderProviderError,
    CodexCliProvider,
)
from agentdeck.providers.deepseek import DeepSeekProvider
from agentdeck.providers.openai_compatible import OpenAICompatibleProvider


def _brief() -> dict[str, object]:
    return {
        "goal": "完成 README 自动化",
        "acceptance_criteria": ["README 包含新命令", "测试全绿"],
        "risks": [],
        "macro_steps": ["梳理命令", "更新文档", "验证"],
    }


class _ApiResponse:
    def __init__(self, content: object) -> None:
        self._body = json.dumps(
            {"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]}
        ).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_ApiResponse":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def _mock_api(monkeypatch, content: object) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("AGENTDECK_LEADER_API_KEY", "test-key")

    def fake_urlopen(http_request, timeout):
        calls.append(
            {
                "url": http_request.full_url,
                "body": json.loads(http_request.data.decode("utf-8")),
                "headers": dict(http_request.headers),
                "timeout": timeout,
            }
        )
        return _ApiResponse(content)

    monkeypatch.setattr(
        "agentdeck.providers.openai_compatible.request.urlopen", fake_urlopen
    )
    return calls


def test_api_plan_brief_returns_validated_brief(monkeypatch) -> None:
    calls = _mock_api(monkeypatch, _brief())

    brief = OpenAICompatibleProvider(timeout=30).plan_brief(
        task="完成 README 自动化", model="brief-model", skill_context=None
    )

    assert brief == _brief()
    assert len(calls) == 1
    body = calls[0]["body"]
    assert body["model"] == "brief-model"
    assert body["response_format"] == {"type": "json_object"}
    system_message = body["messages"][0]
    assert system_message["role"] == "system"
    assert "planner sub-role" in system_message["content"]
    assert "Do not assign" in system_message["content"]
    assert body["messages"][1] == {"role": "user", "content": "完成 README 自动化"}


def test_api_plan_brief_requires_api_key(monkeypatch) -> None:
    monkeypatch.delenv("AGENTDECK_LEADER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="AGENTDECK_LEADER_API_KEY"):
        OpenAICompatibleProvider(timeout=30).plan_brief(task="目标")


def test_api_plan_brief_rejects_invalid_brief_schema(monkeypatch) -> None:
    _mock_api(monkeypatch, {"goal": "", "steps": []})
    with pytest.raises(ValueError, match="planner brief schema is invalid"):
        OpenAICompatibleProvider(timeout=30).plan_brief(task="目标")


def test_api_plan_brief_rejects_non_json_content(monkeypatch) -> None:
    monkeypatch.setenv("AGENTDECK_LEADER_API_KEY", "test-key")

    class _BadResponse:
        def read(self) -> bytes:
            return json.dumps(
                {"choices": [{"message": {"content": "not json"}}]}
            ).encode("utf-8")

        def __enter__(self) -> "_BadResponse":
            return self

        def __exit__(self, *args: object) -> bool:
            return False

    monkeypatch.setattr(
        "agentdeck.providers.openai_compatible.request.urlopen",
        lambda *_args, **_kwargs: _BadResponse(),
    )
    with pytest.raises(RuntimeError, match="not valid JSON"):
        OpenAICompatibleProvider(timeout=30).plan_brief(task="目标")


def test_deepseek_plan_brief_uses_deepseek_env(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        DeepSeekProvider(timeout=30).plan_brief(task="目标")


def _mock_cli(monkeypatch, stdout: str, returncode: int = 0) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def fake_run(command, **kwargs):
        calls.append({"command": list(command), "input": kwargs.get("input")})
        return subprocess.CompletedProcess(
            command, returncode=returncode, stdout=stdout, stderr=""
        )

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)
    return calls


def test_codex_plan_brief_parses_raw_json(monkeypatch) -> None:
    calls = _mock_cli(monkeypatch, json.dumps(_brief(), ensure_ascii=False))

    brief = CodexCliProvider().plan_brief(task="完成 README 自动化", model="brief-model")

    assert brief == _brief()
    assert calls[0]["command"][:2] == ["codex", "--model"]
    assert "brief-model" in calls[0]["command"]
    assert "planner sub-role" in calls[0]["input"]
    assert "完成 README 自动化" in calls[0]["input"]


def test_codex_plan_brief_parses_fenced_json(monkeypatch) -> None:
    fenced = "prose\n```json\n" + json.dumps(_brief(), ensure_ascii=False) + "\n```\n"
    _mock_cli(monkeypatch, fenced)

    assert CodexCliProvider().plan_brief(task="目标") == _brief()


def test_claude_plan_brief_unwraps_result_envelope(monkeypatch) -> None:
    envelope = json.dumps(
        {"type": "result", "result": json.dumps(_brief(), ensure_ascii=False)}
    )
    calls = _mock_cli(monkeypatch, envelope)

    brief = ClaudeCliProvider().plan_brief(task="目标")

    assert brief == _brief()
    assert calls[0]["command"][0] == "claude"


def test_cli_plan_brief_nonzero_exit_fails_closed(monkeypatch) -> None:
    _mock_cli(monkeypatch, "", returncode=1)
    with pytest.raises(CliLeaderProviderError):
        CodexCliProvider().plan_brief(task="目标")


def test_cli_plan_brief_invalid_schema_fails_closed(monkeypatch) -> None:
    _mock_cli(monkeypatch, json.dumps({"goal": "", "steps": []}))
    with pytest.raises(ValueError, match="planner brief schema is invalid"):
        CodexCliProvider().plan_brief(task="目标")


def test_cli_plan_brief_non_json_fails_closed(monkeypatch) -> None:
    _mock_cli(monkeypatch, "not json at all")
    with pytest.raises(CliLeaderProviderError):
        CodexCliProvider().plan_brief(task="目标")
