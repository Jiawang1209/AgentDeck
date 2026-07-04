from __future__ import annotations

import json

from agentdeck.config import write_default_config, load_config
from agentdeck.providers import LeaderPlanRequest, OpenAICompatibleProvider, leader_provider


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


def test_openai_compatible_provider_requires_api_key(monkeypatch) -> None:
    monkeypatch.delenv("AGENTDECK_LEADER_API_KEY", raising=False)

    provider = OpenAICompatibleProvider()

    assert provider.doctor() == (False, "AGENTDECK_LEADER_API_KEY is not set; provider calls are disabled")


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
