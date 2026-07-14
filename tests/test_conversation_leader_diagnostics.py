from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import subprocess

import pytest

from agentdeck.config import load_config, write_default_config
from agentdeck.conversation.leader_gateway import (
    CancellationToken,
    LeaderGateway,
    LeaderGatewayError,
    LeaderRequest,
)
from agentdeck.conversation.session import ConversationSession
from agentdeck.mission_orchestration import LeaderMissionCandidate
from agentdeck.models import AgentSpec
from agentdeck.providers import LeaderPlanRequest
from agentdeck.providers.cli_subprocess import CliLeaderProviderError, CodexCliProvider
from agentdeck.state import StateStore


def _project(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    write_default_config(tmp_path)
    path = tmp_path / ".agentdeck" / "config.toml"
    text = path.read_text(encoding="utf-8")
    text = text.replace('provider = "deepseek"', 'provider = "fake"', 1)
    text = text.replace('model = "deepseek-chat"', 'model = "fake-plan"', 1)
    path.write_text(text, encoding="utf-8")
    return load_config(tmp_path), StateStore(tmp_path)


def _plan() -> dict[str, object]:
    return {
        "goal": "two workers",
        "summary": "strict sequence",
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


@pytest.mark.parametrize(
    ("scenario", "expected_stage"),
    [
        ("timeout", "timeout"),
        ("nonzero", "nonzero"),
        ("json_parse", "json_parse"),
        ("schema", "schema"),
        ("oversize", "oversize"),
    ],
)
def test_cli_leader_failures_are_typed_stage_only_and_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    expected_stage: str,
) -> None:
    config, _store = _project(tmp_path)
    secrets = {
        "stderr": "SECRET_STDERR /Users/private/tool",
        "stdout": "SECRET_STDOUT token=hidden",
        "prompt": "SECRET_PROMPT password=hidden",
    }

    def fake_run(command, **_kwargs):
        if scenario == "timeout":
            raise subprocess.TimeoutExpired(command, 120, output=secrets["stdout"], stderr=secrets["stderr"])
        if scenario == "nonzero":
            return subprocess.CompletedProcess(command, 17, stdout=secrets["stdout"], stderr=secrets["stderr"])
        if scenario == "json_parse":
            return subprocess.CompletedProcess(command, 0, stdout=secrets["stdout"], stderr="")
        if scenario == "schema":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"goal": secrets["prompt"], "summary": "bad", "steps": []}),
                stderr="",
            )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=secrets["stdout"] + "x" * (2 * 1024 * 1024),
            stderr="",
        )

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    with pytest.raises(CliLeaderProviderError) as raised:
        CodexCliProvider().plan(
            LeaderPlanRequest(task=secrets["prompt"], config=config, model="gpt-test")
        )

    assert raised.value.stage == expected_stage
    rendered = repr(raised.value) + str(raised.value) + repr(raised.value.__dict__)
    assert expected_stage in rendered
    assert not any(secret in rendered for secret in secrets.values())
    assert "codex" not in rendered
    assert str(tmp_path) not in rendered


@pytest.mark.parametrize(
    "stage", ["timeout", "nonzero", "json_parse", "schema", "oversize"]
)
def test_gateway_preserves_typed_cli_failure_stage(tmp_path: Path, stage: str) -> None:
    config, _store = _project(tmp_path)
    config = replace(
        config,
        leader=replace(config.leader, provider="codex-cli", model="gpt-test"),
    )

    class FailedProvider:
        def plan(self, _request):
            raise CliLeaderProviderError(stage)

    with pytest.raises(LeaderGatewayError) as raised:
        LeaderGateway(
            provider_factory=lambda _name: FailedProvider(),
            which=lambda _name: "/safe/executable",
        ).generate_mission(
            LeaderRequest(config, "mission", "task", 180, None), CancellationToken()
        )

    assert raised.value.stage == stage
    assert str(raised.value) == f"Leader planning failed at stage: {stage}"


@pytest.mark.parametrize(("stage", "terminal"), [("timeout", "failed"), ("cancelled", "cancelled")])
def test_session_immediately_surfaces_durable_leader_terminal_stage(
    tmp_path: Path, stage: str, terminal: str
) -> None:
    config, store = _project(tmp_path)

    class FailedGateway:
        def generate_mission(self, _request, _cancel):
            raise LeaderGatewayError(stage)

    response = ConversationSession(
        root=tmp_path,
        config=config,
        store=store,
        leader_gateway=FailedGateway(),
    ).handle("让 planner 和 reviewer 协作完成任务，共2轮")

    assert response.kind == terminal
    assert response.payload == {
        "blocker": f"Leader planning failed at stage: {stage}",
        "stage": stage,
    }
    assert store.project_view(config).conversation["latest_turn_state"] == terminal
    event = store.all_events()[-1]
    assert event["event_type"] == "conversation_turn_terminal"
    assert event["payload"]["state"] == terminal
    assert event["payload"]["stage"] == stage
    persisted = json.dumps(store.load(), ensure_ascii=False)
    assert "Traceback" not in persisted
    assert "exception" not in persisted.lower()


def test_conversation_leader_receives_only_uniquely_requested_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, store = _project(tmp_path)
    auditor = AgentSpec(
        agent_id="auditor",
        role="audit",
        provider="codex-cli",
        command="codex",
    )
    config = replace(config, agents=(*config.agents, auditor))
    captured: dict[str, object] = {}

    class CapturingGateway:
        def generate_mission(self, request, _cancel):
            captured["agent_ids"] = tuple(agent.agent_id for agent in request.config.agents)
            captured["planning_task"] = request.planning_task
            return LeaderMissionCandidate(
                provider=request.config.leader.provider,
                model=request.config.leader.model,
                user_message=request.user_message,
                plan=_plan(),
                timeout_seconds=request.timeout_seconds,
                selected_agent_ids=request.selected_agent_ids,
                step_count=request.step_count,
            )

    monkeypatch.setattr(
        "agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}"
    )
    response = ConversationSession(
        root=tmp_path,
        config=config,
        store=store,
        leader_gateway=CapturingGateway(),
    ).handle("让 planner 和 reviewer 协作完成任务，共2轮")

    assert response.kind == "mission_preview"
    assert captured["agent_ids"] == ("planner", "reviewer")
    assert "planner, reviewer" in str(captured["planning_task"])
    assert "auditor" not in str(captured["planning_task"])
    assert [item["agent_id"] for item in response.payload["selected_agents"]] == [
        "planner",
        "reviewer",
    ]
