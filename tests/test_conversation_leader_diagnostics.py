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
    leader_gateway_diagnostics,
)
from agentdeck.conversation.session import ConversationSession
from agentdeck.mission_orchestration import LeaderMissionCandidate
from agentdeck.models import AgentSpec
from agentdeck.providers import LeaderPlanRequest, LeaderPlanResult
from agentdeck.providers.cli_subprocess import CliLeaderProviderError, CodexCliProvider
from agentdeck.providers.plan_schema import (
    build_leader_generation_provenance,
    build_leader_plan_schema,
)
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
        result_path = Path(command[command.index("--output-last-message") + 1])
        if scenario == "schema":
            result_path.write_text(
                json.dumps({"goal": secrets["prompt"], "summary": "bad", "steps": []}),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=secrets["stdout"],
                stderr="",
            )
        with result_path.open("wb") as output:
            output.truncate(2 * 1024 * 1024 + 1)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=secrets["stdout"],
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
        "diagnostic_code": None,
        "attempt_count": 0,
        "constraint_mode": "prompt_only",
    }
    assert store.project_view(config).conversation["latest_turn_state"] == terminal
    event = store.all_events()[-1]
    assert event["event_type"] == "conversation_turn_terminal"
    assert event["payload"] == {
        "conversation_id": event["payload"]["conversation_id"],
        "turn_id": event["payload"]["turn_id"],
        "state": terminal,
        "stage": stage,
        "diagnostic_code": None,
        "attempt_count": 0,
        "constraint_mode": "prompt_only",
    }
    state = store.load()
    for key in (
        "plans",
        "missions",
        "mission_attempts",
        "protocol_sessions",
        "messages",
        "jobs",
        "inboxes",
        "artifacts",
        "approvals",
    ):
        assert state.get(key, []) == []
    persisted = json.dumps(store.load(), ensure_ascii=False)
    assert "Traceback" not in persisted
    assert "exception" not in persisted.lower()


def test_session_persists_exact_safe_cli_diagnostics_without_retryable(
    tmp_path: Path,
) -> None:
    config, store = _project(tmp_path)

    class FailedGateway:
        def generate_mission(self, _request, _cancel):
            raise LeaderGatewayError(
                "schema",
                "role_mismatch",
                attempt_count=2,
                constraint_mode="native_json_schema",
            )

    response = ConversationSession(
        root=tmp_path,
        config=config,
        store=store,
        leader_gateway=FailedGateway(),
    ).handle("让 planner 和 reviewer 协作完成任务，共2轮")

    assert response.payload == {
        "blocker": "Leader planning failed at stage: schema",
        "stage": "schema",
        "diagnostic_code": "role_mismatch",
        "attempt_count": 2,
        "constraint_mode": "native_json_schema",
    }
    event = store.all_events()[-1]
    assert set(event["payload"]) == {
        "conversation_id",
        "turn_id",
        "state",
        "stage",
        "diagnostic_code",
        "attempt_count",
        "constraint_mode",
    }
    assert "retryable" not in json.dumps(store.load(), ensure_ascii=False)


def test_session_rejects_invalid_authority_before_gateway_call(tmp_path: Path) -> None:
    config, store = _project(tmp_path)
    calls = 0

    class RecordingGateway:
        def generate_mission(self, _request, _cancel):
            nonlocal calls
            calls += 1
            raise AssertionError("gateway must not be called")

    response = ConversationSession(
        root=tmp_path,
        config=config,
        store=store,
        leader_gateway=RecordingGateway(),
    ).handle("让 missing-worker 和 planner 协作完成任务，共2轮")

    assert calls == 0
    assert response.payload == {
        "blocker": "Leader planning failed at stage: schema",
        "stage": "schema",
        "diagnostic_code": "authority_invalid",
        "attempt_count": 0,
        "constraint_mode": "prompt_only",
    }


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
                leader_generation=build_leader_generation_provenance(
                    request=LeaderPlanRequest(
                        task=request.planning_task,
                        config=request.config,
                        model=request.config.leader.model,
                        skill_context=request.skill_context,
                        selected_agent_ids=request.selected_agent_ids,
                        step_count=request.step_count,
                        timeout_seconds=request.timeout_seconds,
                    ),
                    provider=request.config.leader.provider,
                    constraint_mode="local",
                ),
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


@pytest.mark.parametrize(
    ("constraint_mode", "attempt_count", "scenario", "expected_stage"),
    [
        ("local", 1, "cancelled", "cancelled"),
        ("local", 1, "serialization", "schema"),
        ("local", 1, "oversize", "oversize"),
        ("native_json_schema", 2, "cancelled", "cancelled"),
        ("native_json_schema", 2, "serialization", "schema"),
        ("native_json_schema", 2, "oversize", "oversize"),
    ],
)
def test_gateway_post_generation_failures_preserve_validated_generation_facts(
    tmp_path: Path,
    constraint_mode: str,
    attempt_count: int,
    scenario: str,
    expected_stage: str,
) -> None:
    config, _store = _project(tmp_path)
    provider_name = "fake" if constraint_mode == "local" else "codex-cli"
    model = "fake-plan" if constraint_mode == "local" else "gpt-test"
    config = replace(
        config,
        leader=replace(config.leader, provider=provider_name, model=model),
        agents=tuple(
            agent
            for agent in config.agents
            if agent.agent_id in {"planner", "reviewer"}
        ),
    )
    cancel = CancellationToken()
    raw_secret = f"SECRET_POST_GENERATION_{scenario}_{tmp_path}"

    class ResultProvider:
        name = provider_name

        def plan_result(self, request: LeaderPlanRequest) -> LeaderPlanResult:
            plan = _plan()
            if scenario == "cancelled":
                cancel.cancel()
            elif scenario == "serialization":
                plan["unsafe_extra"] = object()
            elif scenario == "oversize":
                plan["oversize_extra"] = raw_secret + ("x" * (2 * 1024 * 1024))
            schema = (
                build_leader_plan_schema(request)
                if constraint_mode == "native_json_schema"
                else None
            )
            return LeaderPlanResult(
                plan=plan,
                leader_generation=build_leader_generation_provenance(
                    request=request,
                    provider=provider_name,
                    constraint_mode=constraint_mode,
                    schema=schema,
                    attempt_count=attempt_count,
                ),
            )

    with pytest.raises(LeaderGatewayError) as raised:
        LeaderGateway(
            provider_factory=lambda _name: ResultProvider(),
            which=lambda _name: "/safe/executable",
            leader_cli_probe=lambda _name: (True, None),
        ).generate_mission(
            LeaderRequest(
                config,
                "mission",
                "task",
                180,
                None,
                ("planner", "reviewer"),
                2,
            ),
            cancel,
        )

    error = raised.value
    assert error.stage == expected_stage
    assert error.diagnostic_code is None
    assert error.attempt_count == attempt_count
    assert error.constraint_mode == constraint_mode
    rendered = repr(error) + repr(vars(error)) + repr(error.args)
    assert raw_secret not in rendered
    assert str(tmp_path) not in rendered


@pytest.mark.parametrize(
    ("selected_agent_ids", "step_count"),
    [
        (("planner", "reviewer"), None),
        (None, 2),
        (("planner", "unknown"), 2),
        (("planner", "planner"), 2),
        (("planner", "reviewer"), True),
        (("planner", "reviewer"), 1),
        (("planner", "reviewer"), 65),
    ],
)
def test_gateway_rejects_invalid_frozen_authority_before_backend_boundary(
    tmp_path: Path,
    selected_agent_ids: tuple[str, ...] | None,
    step_count: int | None,
) -> None:
    config, _store = _project(tmp_path)
    config = replace(
        config,
        leader=replace(config.leader, provider="codex-cli", model="gpt-test"),
    )
    calls = {"which": 0, "probe": 0, "factory": 0, "plan": 0}

    class NeverProvider:
        name = "codex-cli"

        def plan(self, _request):
            calls["plan"] += 1
            raise AssertionError("invalid authority must not reach provider plan")

    def which(_name):
        calls["which"] += 1
        return "/safe/executable"

    def probe(_name):
        calls["probe"] += 1
        return True, None

    def factory(_name):
        calls["factory"] += 1
        return NeverProvider()

    with pytest.raises(LeaderGatewayError) as raised:
        LeaderGateway(
            provider_factory=factory,
            which=which,
            leader_cli_probe=probe,
        ).generate_mission(
            LeaderRequest(
                config,
                "mission",
                "task",
                180,
                None,
                selected_agent_ids,
                step_count,
            ),
            CancellationToken(),
        )

    assert calls == {"which": 0, "probe": 0, "factory": 0, "plan": 0}
    assert leader_gateway_diagnostics(raised.value) == {
        "stage": "schema",
        "diagnostic_code": "authority_invalid",
        "attempt_count": 0,
        "constraint_mode": "prompt_only",
    }
