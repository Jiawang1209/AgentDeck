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
from agentdeck.orchestration.leader import LeaderOrchestrator
from agentdeck.providers import LeaderPlanRequest, LeaderPlanResult
from agentdeck.providers.plan_schema import ProviderPlanValidationError


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


def test_gateway_passes_frozen_authority_and_deadline_to_provider(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config = replace(
        config,
        leader=replace(config.leader, provider="fake", model="fake-plan"),
    )
    seen: list[LeaderPlanRequest] = []

    class RecordingProvider:
        name = "fake"

        def plan(self, request: LeaderPlanRequest) -> dict[str, object]:
            seen.append(request)
            return _plan()

    candidate = LeaderGateway(
        provider_factory=lambda _name: RecordingProvider()
    ).generate_mission(
        LeaderRequest(
            config,
            "mission",
            "structured mission task",
            180,
            None,
            selected_agent_ids=("planner", "reviewer"),
            step_count=2,
        ),
        CancellationToken(),
    )

    assert seen[0].selected_agent_ids == ("planner", "reviewer")
    assert seen[0].step_count == 2
    assert seen[0].timeout_seconds == 180
    assert candidate.selected_agent_ids == seen[0].selected_agent_ids
    assert candidate.step_count == seen[0].step_count


def test_gateway_uses_native_plan_result_once_without_legacy_fallback(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config = replace(
        config,
        leader=replace(config.leader, provider="fake", model="fake-plan"),
    )

    class NativeProvider:
        name = "fake"

        def __init__(self) -> None:
            self.calls = 0

        def plan_result(self, request: LeaderPlanRequest) -> LeaderPlanResult:
            self.calls += 1
            assert request.timeout_seconds == 180
            return LeaderPlanResult(
                plan=_plan(),
                leader_generation={"constraint_mode": "native_json_schema"},
            )

        def plan(self, _request: LeaderPlanRequest) -> dict[str, object]:
            pytest.fail("legacy plan fallback must not be called")

    provider = NativeProvider()
    candidate = LeaderGateway(provider_factory=lambda _name: provider).generate_mission(
        LeaderRequest(
            config,
            "mission",
            "structured mission task",
            180,
            None,
            selected_agent_ids=("planner", "reviewer"),
            step_count=2,
        ),
        CancellationToken(),
    )

    assert provider.calls == 1
    assert candidate.plan["goal"] == "demo"


def test_orchestrator_preserves_plain_plan_and_wraps_legacy_result(tmp_path: Path) -> None:
    config = _config(tmp_path)

    class LegacyProvider:
        name = "recording"

        def plan(self, _request: LeaderPlanRequest) -> dict[str, object]:
            return _plan()

    orchestrator = LeaderOrchestrator(config, LegacyProvider())
    result = orchestrator.plan_result(
        "structured mission task",
        "fake-plan",
        selected_agent_ids=("planner", "reviewer"),
        step_count=2,
        timeout_seconds=180,
    )
    plan = orchestrator.plan(
        "structured mission task",
        "fake-plan",
        selected_agent_ids=("planner", "reviewer"),
        step_count=2,
        timeout_seconds=180,
    )

    assert isinstance(result, LeaderPlanResult)
    assert result.leader_generation == {
        "provider": "recording",
        "model": "fake-plan",
        "constraint_mode": "local",
        "schema_version": None,
        "schema_hash": None,
        "attempt_count": 1,
        "regeneration_used": False,
        "selected_agent_ids": ["planner", "reviewer"],
        "step_count": 2,
    }
    assert isinstance(plan, dict)
    assert plan["goal"] == "demo"


def test_orchestrator_validates_malformed_legacy_provider_result(tmp_path: Path) -> None:
    config = _config(tmp_path)

    class MalformedProvider:
        name = "fake"

        def plan(self, _request: LeaderPlanRequest) -> dict[str, object]:
            return {"goal": "incomplete"}

    with pytest.raises(ProviderPlanValidationError, match="missing required field: summary"):
        LeaderOrchestrator(config, MalformedProvider()).plan_result(
            "structured mission task",
            selected_agent_ids=("planner", "reviewer"),
            step_count=2,
            timeout_seconds=180,
        )


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
