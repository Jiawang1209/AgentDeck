from __future__ import annotations

import json
import subprocess

from agentdeck.config import write_default_config, load_config
from agentdeck.providers import (
    ClaudeCliProvider,
    CodexCliProvider,
    DeepSeekProvider,
    LeaderPlanRequest,
    OpenAICompatibleProvider,
    leader_provider,
)


class FakeResponse:
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "goal": "构建 provider",
                                    "summary": "real provider plan",
                                    "steps": [
                                        {
                                            "step": 1,
                                            "agent_id": "planner",
                                            "role": "planning",
                                            "task": "设计 provider",
                                            "risk": "requires human review before dispatch",
                                            "requires_approval": True,
                                        }
                                    ],
                                    "approval_required": True,
                                    "dispatch_ready": False,
                                }
                            )
                        }
                    }
                ]
            }
        ).encode("utf-8")


class InvalidJsonPlanResponse:
    def __enter__(self) -> "InvalidJsonPlanResponse":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": "not-json"
                        }
                    }
                ]
            }
        ).encode("utf-8")


class UnsafeControlFlagsResponse:
    def __enter__(self) -> "UnsafeControlFlagsResponse":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": unsafe_control_flags_plan_stdout()
                        }
                    }
                ]
            }
        ).encode("utf-8")


def cli_plan_stdout() -> str:
    return json.dumps(
        {
            "goal": "CLI Leader",
            "summary": "plan from local CLI",
            "steps": [
                {
                    "step": 1,
                    "agent_id": "planner",
                    "role": "planning",
                    "task": "Plan the work",
                    "risk": "requires human review before dispatch",
                    "requires_approval": True,
                }
            ],
            "approval_required": True,
            "dispatch_ready": False,
        }
    )


def unsafe_control_flags_plan_stdout() -> str:
    return json.dumps(
        {
            "goal": "Unsafe flags",
            "summary": "provider tried to skip approval gates",
            "steps": [
                {
                    "step": 1,
                    "agent_id": "planner",
                    "role": "planning",
                    "task": "Plan the work",
                    "risk": "requires human review before dispatch",
                    "requires_approval": True,
                }
            ],
            "approval_required": False,
            "dispatch_ready": True,
        }
    )


def cli_plan_stdout_without_control_flags() -> str:
    return json.dumps(
        {
            "goal": "CLI Leader",
            "summary": "plan from local CLI",
            "steps": [
                {
                    "step": 1,
                    "agent_id": "planner",
                    "role": "planning",
                    "task": "Plan the work",
                    "risk": "requires human review before dispatch",
                    "requires_approval": True,
                }
            ],
        }
    )


def cli_plan_stdout_missing_step_agent_id() -> str:
    return json.dumps(
        {
            "goal": "Malformed provider plan",
            "summary": "provider omitted a required step field",
            "steps": [
                {
                    "step": 1,
                    "role": "planning",
                    "task": "Plan the work",
                    "risk": "requires human review before dispatch",
                    "requires_approval": True,
                }
            ],
            "approval_required": True,
            "dispatch_ready": False,
        }
    )


def test_openai_compatible_provider_requires_api_key(monkeypatch) -> None:
    monkeypatch.delenv("AGENTDECK_LEADER_API_KEY", raising=False)

    provider = OpenAICompatibleProvider()

    assert provider.doctor() == (False, "AGENTDECK_LEADER_API_KEY is not set; provider calls are disabled")


def test_deepseek_provider_uses_deepseek_env_and_openai_compatible_plan_shape(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_default_config(root)
    config = load_config(root)
    seen: dict[str, object] = {}
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://deepseek.example/v1")

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        seen["headers"] = dict(request.header_items())
        seen["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr("agentdeck.providers.openai_compatible.request.urlopen", fake_urlopen)

    provider = leader_provider("deepseek")
    plan = provider.plan(LeaderPlanRequest(task="DeepSeek 规划", config=config))

    assert isinstance(provider, DeepSeekProvider)
    assert provider.doctor() == (True, "DEEPSEEK_API_KEY is set")
    assert seen["url"] == "https://deepseek.example/v1/chat/completions"
    assert seen["timeout"] == 60
    assert seen["headers"]["Authorization"] == "Bearer deepseek-key"
    body = seen["body"]
    assert body["model"] == "deepseek-chat"
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system"
    assert '"role_prompt":' in body["messages"][0]["content"]
    assert "负责需求澄清、任务拆解、架构方案和风险识别" in body["messages"][0]["content"]
    assert body["messages"][1]["content"] == "DeepSeek 规划"
    assert plan["goal"] == "构建 provider"
    assert plan["dispatch_ready"] is False


def test_codex_cli_provider_runs_non_interactive_command_and_parses_json_plan(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_default_config(root)
    config = load_config(root)
    seen: dict[str, object] = {}

    def fake_run(command, input, text, capture_output, cwd, timeout, check):
        seen["command"] = command
        seen["input"] = input
        seen["text"] = text
        seen["capture_output"] = capture_output
        seen["cwd"] = cwd
        seen["timeout"] = timeout
        seen["check"] = check
        return subprocess.CompletedProcess(command, 0, stdout=cli_plan_stdout(), stderr="")

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)
    monkeypatch.setattr("agentdeck.providers.cli_subprocess.shutil.which", lambda name: f"/usr/bin/{name}")

    provider = leader_provider("codex-cli")
    plan = provider.plan(LeaderPlanRequest(task="让 Codex 做 Leader", config=config))

    assert isinstance(provider, CodexCliProvider)
    assert provider.doctor() == (True, "codex is available")
    assert seen["command"] == ["codex", "exec", "--sandbox", "read-only", "-"]
    assert "Return only a JSON object plan" in str(seen["input"])
    assert "You are the logical Leader Agent with agent_id=leader" in str(seen["input"])
    assert "Do not reuse worker tmux panes or claim a dedicated Leader pane" in str(seen["input"])
    assert '"role_prompt":' in str(seen["input"])
    assert "负责需求澄清、任务拆解、架构方案和风险识别" in str(seen["input"])
    assert "让 Codex 做 Leader" in str(seen["input"])
    assert seen["cwd"] == str(root)
    assert plan["goal"] == "CLI Leader"
    assert plan["steps"][0]["requires_approval"] is True


def test_codex_cli_provider_passes_requested_model_to_local_command(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_default_config(root)
    config = load_config(root)
    seen: dict[str, object] = {}

    def fake_run(command, **_kwargs):
        seen["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout=cli_plan_stdout(), stderr="")

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    provider = CodexCliProvider()
    provider.plan(LeaderPlanRequest(task="指定 Codex 模型", config=config, model="gpt-5-codex"))

    assert seen["command"] == [
        "codex",
        "--model",
        "gpt-5-codex",
        "exec",
        "--sandbox",
        "read-only",
        "-",
    ]


def test_claude_cli_provider_runs_print_command_and_parses_json_plan(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_default_config(root)
    config = load_config(root)
    seen: dict[str, object] = {}

    def fake_run(command, input, text, capture_output, cwd, timeout, check):
        seen["command"] = command
        seen["input"] = input
        seen["cwd"] = cwd
        return subprocess.CompletedProcess(command, 0, stdout=cli_plan_stdout(), stderr="")

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)
    monkeypatch.setattr("agentdeck.providers.cli_subprocess.shutil.which", lambda name: f"/usr/bin/{name}")

    provider = leader_provider("claude-cli")
    plan = provider.plan(LeaderPlanRequest(task="让 Claude 做 Leader", config=config))

    assert isinstance(provider, ClaudeCliProvider)
    assert provider.doctor() == (True, "claude is available")
    assert seen["command"] == ["claude", "--print", "--output-format", "text", "--permission-mode", "plan"]
    assert "Return only a JSON object plan" in str(seen["input"])
    assert "You are the logical Leader Agent with agent_id=leader" in str(seen["input"])
    assert "Do not reuse worker tmux panes or claim a dedicated Leader pane" in str(seen["input"])
    assert "让 Claude 做 Leader" in str(seen["input"])
    assert seen["cwd"] == str(root)
    assert plan["goal"] == "CLI Leader"


def test_claude_cli_provider_passes_requested_model_to_local_command(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_default_config(root)
    config = load_config(root)
    seen: dict[str, object] = {}

    def fake_run(command, **_kwargs):
        seen["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout=cli_plan_stdout(), stderr="")

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    provider = ClaudeCliProvider()
    provider.plan(LeaderPlanRequest(task="指定 Claude 模型", config=config, model="claude-sonnet-4-5"))

    assert seen["command"] == [
        "claude",
        "--model",
        "claude-sonnet-4-5",
        "--print",
        "--output-format",
        "text",
        "--permission-mode",
        "plan",
    ]


def test_cli_provider_extracts_fenced_json_plan_from_local_cli_output(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_default_config(root)
    config = load_config(root)

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"Here is the plan:\n```json\n{cli_plan_stdout()}\n```\n",
            stderr="",
        )

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    provider = CodexCliProvider()
    plan = provider.plan(LeaderPlanRequest(task="解析 CLI fenced JSON", config=config))

    assert plan["goal"] == "CLI Leader"
    assert plan["steps"][0]["requires_approval"] is True


def test_cli_provider_rejects_multiple_fenced_json_plans(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_default_config(root)
    config = load_config(root)

    def fake_run(command, **_kwargs):
        plan = cli_plan_stdout()
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"```json\n{plan}\n```\n```json\n{plan}\n```\n",
            stderr="",
        )

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    provider = CodexCliProvider()

    try:
        provider.plan(LeaderPlanRequest(task="多个 JSON plan", config=config))
    except RuntimeError as exc:
        assert str(exc) == "provider plan content contains multiple JSON plans"
    else:
        raise AssertionError("provider should reject ambiguous fenced JSON plans")


def test_cli_provider_normalizes_missing_plan_control_flags(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_default_config(root)
    config = load_config(root)

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=cli_plan_stdout_without_control_flags(), stderr="")

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    provider = CodexCliProvider()
    plan = provider.plan(LeaderPlanRequest(task="归一化 CLI plan", config=config))

    assert plan["approval_required"] is True
    assert plan["dispatch_ready"] is False


def test_cli_provider_forces_approval_gates_when_provider_returns_unsafe_control_flags(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_default_config(root)
    config = load_config(root)

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=unsafe_control_flags_plan_stdout(), stderr="")

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    provider = CodexCliProvider()
    plan = provider.plan(LeaderPlanRequest(task="收敛 CLI provider flags", config=config))

    assert plan["approval_required"] is True
    assert plan["dispatch_ready"] is False


def test_cli_provider_rejects_plan_steps_missing_required_schema_fields(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_default_config(root)
    config = load_config(root)

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=cli_plan_stdout_missing_step_agent_id(), stderr="")

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    provider = CodexCliProvider()

    try:
        provider.plan(LeaderPlanRequest(task="拒绝缺字段 CLI plan", config=config))
    except RuntimeError as exc:
        assert str(exc) == "provider plan step 1 missing required field: agent_id"
    else:
        raise AssertionError("provider should reject plan steps missing required schema fields")


def test_cli_provider_reports_subprocess_failure(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_default_config(root)
    config = load_config(root)

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 2, stdout="", stderr="not logged in")

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    provider = CodexCliProvider()

    try:
        provider.plan(LeaderPlanRequest(task="失败", config=config))
    except RuntimeError as exc:
        assert str(exc) == "codex-cli failed: not logged in"
    else:
        raise AssertionError("provider should reject failed CLI command")


def test_openai_compatible_provider_posts_chat_completion_and_parses_json_plan(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_default_config(root)
    config = load_config(root)
    seen: dict[str, object] = {}
    monkeypatch.setenv("AGENTDECK_LEADER_API_KEY", "test-key")
    monkeypatch.setenv("AGENTDECK_LEADER_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("AGENTDECK_LEADER_MODEL", "leader-model")

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        seen["headers"] = dict(request.header_items())
        seen["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr("agentdeck.providers.openai_compatible.request.urlopen", fake_urlopen)

    provider = leader_provider("openai-compatible")
    plan = provider.plan(LeaderPlanRequest(task="构建 provider", config=config))

    assert isinstance(provider, OpenAICompatibleProvider)
    assert seen["url"] == "https://llm.example/v1/chat/completions"
    assert seen["timeout"] == 60
    assert seen["headers"]["Authorization"] == "Bearer test-key"
    body = seen["body"]
    assert body["model"] == "leader-model"
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["content"] == "构建 provider"
    assert plan["goal"] == "构建 provider"
    assert plan["steps"][0]["agent_id"] == "planner"
    assert plan["dispatch_ready"] is False


def test_openai_compatible_provider_forces_approval_gates_when_provider_returns_unsafe_control_flags(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_default_config(root)
    config = load_config(root)
    monkeypatch.setenv("AGENTDECK_LEADER_API_KEY", "test-key")
    monkeypatch.setattr(
        "agentdeck.providers.openai_compatible.request.urlopen",
        lambda _request, timeout: UnsafeControlFlagsResponse(),
    )

    provider = OpenAICompatibleProvider()
    plan = provider.plan(LeaderPlanRequest(task="收敛 API provider flags", config=config))

    assert plan["approval_required"] is True
    assert plan["dispatch_ready"] is False


def test_openai_compatible_provider_rejects_plan_steps_missing_required_schema_fields(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_default_config(root)
    config = load_config(root)
    monkeypatch.setenv("AGENTDECK_LEADER_API_KEY", "test-key")

    class MissingStepFieldResponse:
        def __enter__(self) -> "MissingStepFieldResponse":
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": cli_plan_stdout_missing_step_agent_id(),
                            }
                        }
                    ]
                }
            ).encode("utf-8")

    monkeypatch.setattr(
        "agentdeck.providers.openai_compatible.request.urlopen",
        lambda _request, timeout: MissingStepFieldResponse(),
    )

    provider = OpenAICompatibleProvider()

    try:
        provider.plan(LeaderPlanRequest(task="拒绝缺字段 API plan", config=config))
    except RuntimeError as exc:
        assert str(exc) == "provider plan step 1 missing required field: agent_id"
    else:
        raise AssertionError("provider should reject plan steps missing required schema fields")


def test_openai_compatible_provider_uses_requested_model_over_environment(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_default_config(root)
    config = load_config(root)
    seen: dict[str, object] = {}
    monkeypatch.setenv("AGENTDECK_LEADER_API_KEY", "test-key")
    monkeypatch.setenv("AGENTDECK_LEADER_MODEL", "env-model")

    def fake_urlopen(request, timeout):
        seen["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr("agentdeck.providers.openai_compatible.request.urlopen", fake_urlopen)

    provider = OpenAICompatibleProvider()
    provider.plan(LeaderPlanRequest(task="指定 API 模型", config=config, model="explicit-model"))

    assert seen["body"]["model"] == "explicit-model"


def test_openai_compatible_provider_reports_invalid_json_plan(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_default_config(root)
    config = load_config(root)
    monkeypatch.setenv("AGENTDECK_LEADER_API_KEY", "test-key")
    monkeypatch.setattr(
        "agentdeck.providers.openai_compatible.request.urlopen",
        lambda _request, timeout: InvalidJsonPlanResponse(),
    )

    provider = OpenAICompatibleProvider()

    try:
        provider.plan(LeaderPlanRequest(task="构建 provider", config=config))
    except RuntimeError as exc:
        assert str(exc) == "provider plan content is not valid JSON"
    else:
        raise AssertionError("provider should reject invalid JSON plan content")
