from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys

import pytest

from agentdeck.config import load_config, write_default_config
from agentdeck.conversation.leader_gateway import (
    CancellationToken,
    LeaderGateway,
    LeaderGatewayError,
    LeaderRequest,
)
from agentdeck.providers import LeaderPlanRequest


def _plan() -> dict[str, object]:
    return {
        "goal": "demo",
        "summary": "serial demo",
        "steps": [
            {
                "step": 1,
                "agent_id": "planner",
                "role": "planning",
                "task": "plan",
                "risk": "review",
                "requires_approval": True,
            },
            {
                "step": 2,
                "agent_id": "reviewer",
                "role": "review",
                "task": "review",
                "risk": "review",
                "requires_approval": True,
            },
        ],
    }


def _config(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    write_default_config(tmp_path)
    return load_config(tmp_path)


def test_legacy_leader_config_derives_explicit_backend_identity(tmp_path: Path) -> None:
    config = _config(tmp_path)
    gateway = LeaderGateway(which=lambda _name: "/bin/tool")

    api = gateway.describe(config.leader)
    cli = gateway.describe(replace(config.leader, provider="codex-cli", model="gpt-5.5"))

    assert (api.backend_kind, api.transport, api.readiness) == ("api", "http", "ready")
    assert (cli.backend_kind, cli.transport, cli.readiness) == (
        "agent_cli",
        "cli_subprocess",
        "ready",
    )
    assert api.fallback == {"automatic": False, "transport": None}
    assert cli.fallback == {"automatic": False, "transport": None}


def test_explicit_acp_leader_requires_nonempty_command(tmp_path: Path) -> None:
    config = _config(tmp_path)
    leader = replace(
        config.leader,
        provider="claude-cli",
        backend_kind="agent_cli",
        transport="acp",
        transport_command=(),
    )

    status = LeaderGateway(which=lambda _name: None).describe(leader)

    assert status.readiness == "blocked"
    assert status.blockers == ("ACP Leader requires transport_command",)


class _Provider:
    name = "fake"

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    def plan(self, request: LeaderPlanRequest) -> dict[str, object]:
        self.calls += 1
        if self.error:
            raise self.error
        assert request.task == "structured mission task"
        return _plan()


def test_gateway_generates_candidate_through_exact_provider_once(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config = replace(config, leader=replace(config.leader, provider="fake", model="fake-plan"))
    provider = _Provider()
    gateway = LeaderGateway(provider_factory=lambda name: provider)

    candidate = gateway.generate_mission(
        LeaderRequest(
            config=config,
            user_message="multi-agent mission",
            planning_task="structured mission task",
            timeout_seconds=180,
            skill_context={"count": 0, "items": []},
            selected_agent_ids=("planner", "reviewer"),
            step_count=2,
        ),
        CancellationToken(),
    )

    assert provider.calls == 1
    assert candidate.provider == "fake"
    assert candidate.model == "fake-plan"
    assert candidate.plan["goal"] == "demo"
    assert candidate.selected_agent_ids == ("planner", "reviewer")
    assert candidate.step_count == 2


def test_gateway_failure_never_tries_another_backend(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config = replace(
        config,
        leader=replace(config.leader, provider="codex-cli", model="gpt-5.5"),
    )
    provider = _Provider(RuntimeError("SECRET backend failure"))
    requested: list[str] = []

    def factory(name: str):
        requested.append(name)
        return provider

    with pytest.raises(LeaderGatewayError, match="Leader backend failed") as raised:
        LeaderGateway(provider_factory=factory, which=lambda _name: "/bin/codex").generate_mission(
            LeaderRequest(config, "mission", "structured mission task", 180, None),
            CancellationToken(),
        )

    assert requested == ["codex-cli"]
    assert "SECRET" not in str(raised.value)


def test_cancelled_request_does_not_construct_provider(tmp_path: Path) -> None:
    config = _config(tmp_path)
    token = CancellationToken()
    token.cancel()

    with pytest.raises(LeaderGatewayError, match="Leader request cancelled"):
        LeaderGateway(
            provider_factory=lambda _name: pytest.fail("provider must not be constructed")
        ).generate_mission(
            LeaderRequest(config, "mission", "structured mission task", 180, None), token
        )


def test_acp_leader_runs_new_prompt_and_returns_one_json_candidate(tmp_path: Path) -> None:
    config = _config(tmp_path)
    fixture = Path(__file__).parent / "fixtures" / "fake_acp_agent.py"
    config = replace(
        config,
        leader=replace(
            config.leader,
            provider="claude-cli",
            model="claude-sonnet",
            backend_kind="agent_cli",
            transport="acp",
            transport_command=(sys.executable, str(fixture), "stream_end_turn"),
        ),
    )
    provider_calls: list[str] = []
    gateway = LeaderGateway(
        provider_factory=lambda name: provider_calls.append(name),  # type: ignore[arg-type,return-value]
        which=lambda name: name,
    )

    candidate = gateway.generate_mission(
        LeaderRequest(
            config,
            "multi-agent mission",
            json.dumps(_plan()),
            180,
            None,
        ),
        CancellationToken(),
    )

    assert candidate.provider == "claude-cli"
    assert candidate.plan["steps"][1]["agent_id"] == "reviewer"
    assert provider_calls == []


def test_acp_failure_does_not_fall_back_to_provider(tmp_path: Path) -> None:
    config = _config(tmp_path)
    fixture = Path(__file__).parent / "fixtures" / "fake_acp_agent.py"
    config = replace(
        config,
        leader=replace(
            config.leader,
            provider="claude-cli",
            backend_kind="agent_cli",
            transport="acp",
            transport_command=(sys.executable, str(fixture), "malformed_frame"),
        ),
    )
    calls: list[str] = []

    with pytest.raises(LeaderGatewayError, match="ACP Leader backend failed"):
        LeaderGateway(
            provider_factory=lambda name: calls.append(name),  # type: ignore[arg-type,return-value]
            which=lambda name: name,
            request_timeout=0.2,
        ).generate_mission(
            LeaderRequest(config, "mission", json.dumps(_plan()), 180, None),
            CancellationToken(),
        )

    assert calls == []


@pytest.mark.parametrize(
    "leader_lines",
    [
        'backend_kind = "api"\ntransport = "acp"\ntransport_command = ["agent"]',
        'backend_kind = "agent_cli"\ntransport = "http"',
        'backend_kind = "agent_cli"\ntransport = "acp"\ntransport_command = []',
        'backend_kind = "agent_cli"\ntransport = "acp"\ntransport_command = "agent"',
    ],
)
def test_config_rejects_invalid_explicit_leader_combinations(
    tmp_path: Path, leader_lines: str
) -> None:
    _config(tmp_path)
    path = tmp_path / ".agentdeck" / "config.toml"
    text = path.read_text(encoding="utf-8")
    text = text.replace('approval_mode = "confirm"', f'approval_mode = "confirm"\n{leader_lines}')
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="invalid Leader backend configuration"):
        load_config(tmp_path)
