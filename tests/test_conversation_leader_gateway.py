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
from agentdeck.providers.cli_subprocess import CliLeaderProviderError
from agentdeck.providers.plan_schema import (
    ProviderPlanValidationError,
    build_leader_generation_provenance,
    build_leader_plan_schema,
)


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


def _configured_plan(config) -> dict[str, object]:
    return {
        "goal": "demo",
        "summary": "configured plan",
        "steps": [
            {
                "step": index,
                "agent_id": agent.agent_id,
                "role": agent.role,
                "task": f"step {index}",
                "risk": "review",
                "requires_approval": True,
            }
            for index, agent in enumerate(config.agents, start=1)
        ],
    }


def _config(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    write_default_config(tmp_path)
    return load_config(tmp_path)


def test_legacy_leader_config_derives_explicit_backend_identity(tmp_path: Path) -> None:
    config = _config(tmp_path)
    probe_calls: list[str] = []

    def ready_probe(provider: str) -> tuple[bool, str | None]:
        probe_calls.append(provider)
        return True, None

    gateway = LeaderGateway(
        which=lambda _name: "/bin/tool", leader_cli_probe=ready_probe
    )

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
    assert cli.capabilities == ("plan", "native_json_schema")
    assert probe_calls == ["codex-cli"]


def test_cli_subprocess_native_probe_blocks_without_constructing_provider(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    config = replace(
        config,
        leader=replace(config.leader, provider="claude-cli", model="claude-test"),
    )
    blocker = "Leader CLI native JSON schema capability is unavailable"
    constructed: list[str] = []
    gateway = LeaderGateway(
        provider_factory=lambda name: constructed.append(name),  # type: ignore[arg-type,return-value]
        which=lambda _name: "/bin/claude",
        leader_cli_probe=lambda provider: (False, blocker),
    )

    status = gateway.describe(config.leader)

    assert status.readiness == "blocked"
    assert status.capabilities == ("plan",)
    assert status.blockers == (blocker,)
    with pytest.raises(LeaderGatewayError) as raised:
        gateway.generate_mission(
            LeaderRequest(config, "mission", "structured mission task", 180, None),
            CancellationToken(),
        )
    assert raised.value.stage == "backend_blocked"
    assert constructed == []


def test_cli_subprocess_missing_executable_skips_native_probe(tmp_path: Path) -> None:
    config = _config(tmp_path)
    leader = replace(config.leader, provider="codex-cli", model="gpt-test")

    status = LeaderGateway(
        which=lambda _name: None,
        leader_cli_probe=lambda _provider: pytest.fail(
            "native probe must not run without executable"
        ),
    ).describe(leader)

    assert status.readiness == "blocked"
    assert status.capabilities == ("plan",)
    assert status.blockers == ("Leader CLI executable is not available",)


def test_unknown_cli_provider_uses_fixed_unsupported_probe_blocker(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    leader = replace(
        config.leader,
        provider="future-cli",
        model="future-test",
        backend_kind="agent_cli",
        transport="cli_subprocess",
    )

    status = LeaderGateway(
        which=lambda _name: pytest.fail("unknown provider has no executable mapping"),
        leader_cli_probe=lambda provider: (
            False,
            "Leader CLI native JSON schema is unsupported",
        ),
    ).describe(leader)

    assert status.readiness == "blocked"
    assert status.capabilities == ("plan",)
    assert status.blockers == ("Leader CLI native JSON schema is unsupported",)


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
    assert candidate.leader_generation == {
        "provider": "fake",
        "model": "fake-plan",
        "constraint_mode": "local",
        "schema_version": None,
        "schema_hash": None,
        "attempt_count": 1,
        "regeneration_used": False,
        "selected_agent_ids": ["planner", "reviewer"],
        "step_count": 2,
    }


def test_gateway_copies_only_safe_cli_diagnostics(tmp_path: Path) -> None:
    secret = "SECRET token=/Users/private/provider-output"
    provider_error = CliLeaderProviderError(
        "schema",
        "role_mismatch",
        attempt_count=2,
        constraint_mode="native_json_schema",
    )
    provider_error.__cause__ = RuntimeError(secret)

    class FailedProvider:
        name = "codex-cli"

        def plan_result(self, _request):
            raise provider_error

    base = _config(tmp_path)
    config = replace(
        base,
        leader=replace(base.leader, provider="codex-cli", model="gpt-test"),
    )
    gateway = LeaderGateway(
        provider_factory=lambda _name: FailedProvider(),
        which=lambda _name: "/safe/codex",
        leader_cli_probe=lambda _provider: (True, None),
    )

    with pytest.raises(LeaderGatewayError) as raised:
        gateway.generate_mission(
            LeaderRequest(
                config,
                secret,
                secret,
                180,
                None,
                ("planner", "reviewer"),
                2,
            ),
            CancellationToken(),
        )

    error = raised.value
    assert error.__dict__ == {
        "stage": "schema",
        "diagnostic_code": "role_mismatch",
        "attempt_count": 2,
        "constraint_mode": "native_json_schema",
    }
    rendered = repr(error) + repr(error.__dict__) + str(error)
    assert secret not in rendered
    assert error.__cause__ is None
    assert error.__context__ is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"stage": True},
        {"stage": "hostile"},
        {"stage": "schema", "diagnostic_code": True},
        {"stage": "schema", "diagnostic_code": "hostile"},
        {"stage": "schema", "attempt_count": True},
        {"stage": "schema", "attempt_count": -1},
        {"stage": "schema", "attempt_count": 3},
        {"stage": "schema", "constraint_mode": True},
        {"stage": "schema", "constraint_mode": "hostile"},
    ],
)
def test_gateway_error_rejects_invalid_safe_diagnostics_without_echo(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError) as raised:
        LeaderGatewayError(**kwargs)  # type: ignore[arg-type]
    assert str(raised.value) == "invalid Leader Gateway diagnostics"


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


def test_gateway_carries_json_object_candidate_provenance(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config = replace(
        config,
        leader=replace(config.leader, provider="deepseek", model="deepseek-chat"),
    )
    provider = _Provider()
    provider.name = "deepseek"
    provider.constraint_mode = "json_object"

    candidate = LeaderGateway(
        provider_factory=lambda _name: provider
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

    assert candidate.leader_generation["provider"] == "deepseek"
    assert candidate.leader_generation["constraint_mode"] == "json_object"
    assert candidate.leader_generation["schema_hash"] is None


def test_gateway_rejects_authority_before_provider_generation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    calls = 0

    class Provider:
        name = "fake"

        def plan(self, _request):
            nonlocal calls
            calls += 1
            return _plan()

    with pytest.raises(LeaderGatewayError) as raised:
        LeaderGateway(provider_factory=lambda _name: Provider()).generate_mission(
            LeaderRequest(
                config,
                "mission",
                "structured mission task",
                180,
                None,
                selected_agent_ids=("planner", "missing"),
                step_count=2,
            ),
            CancellationToken(),
        )

    assert calls == 0
    assert raised.value.__dict__ == {
        "stage": "schema",
        "diagnostic_code": "authority_invalid",
        "attempt_count": 0,
        "constraint_mode": "prompt_only",
    }


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
            schema = build_leader_plan_schema(request)
            return LeaderPlanResult(
                plan=_plan(),
                leader_generation=build_leader_generation_provenance(
                    request=request,
                    provider=self.name,
                    constraint_mode="native_json_schema",
                    schema=schema,
                ),
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
    assert candidate.leader_generation["constraint_mode"] == "native_json_schema"
    assert candidate.leader_generation["schema_version"] == "leader-plan/v1"


@pytest.mark.parametrize(
    ("plan", "message"),
    [
        (["not", "a", "plan"], "provider plan content must be a JSON object"),
        ({"goal": "incomplete"}, "provider plan missing required field: summary"),
        (
            {**_plan(), "steps": _plan()["steps"][:1]},
            "provider plan must include exactly 2 steps",
        ),
        (
            {
                **_plan(),
                "steps": [
                    _plan()["steps"][0],
                    {
                        **_plan()["steps"][1],
                        "agent_id": "coder",
                        "role": "implementation",
                    },
                ],
            },
            "provider plan step 2 agent_id is not selected by mission authority: coder",
        ),
    ],
)
def test_orchestrator_validates_native_plan_before_accepting_result(
    tmp_path: Path,
    plan: object,
    message: str,
) -> None:
    config = _config(tmp_path)

    class NativeProvider:
        name = "fake"

        def __init__(self) -> None:
            self.native_calls = 0
            self.legacy_calls = 0

        def plan_result(self, request: LeaderPlanRequest) -> LeaderPlanResult:
            self.native_calls += 1
            schema = build_leader_plan_schema(request)
            return LeaderPlanResult(
                plan=plan,  # type: ignore[arg-type]
                leader_generation=build_leader_generation_provenance(
                    request=request,
                    provider=self.name,
                    constraint_mode="native_json_schema",
                    schema=schema,
                ),
            )

        def plan(self, _request: LeaderPlanRequest) -> dict[str, object]:
            self.legacy_calls += 1
            return _plan()

    provider = NativeProvider()
    with pytest.raises(ProviderPlanValidationError, match=message):
        LeaderOrchestrator(config, provider).plan_result(
            "structured mission task",
            selected_agent_ids=("planner", "reviewer"),
            step_count=2,
            timeout_seconds=180,
        )

    assert provider.native_calls == 1
    assert provider.legacy_calls == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "incomplete",
        "provider",
        "model",
        "schema_hash",
        "boolean_attempt",
        "prompt_only_schema_spoof",
    ],
)
def test_orchestrator_rejects_untrusted_native_generation_provenance(
    tmp_path: Path,
    mutation: str,
) -> None:
    config = _config(tmp_path)

    class NativeProvider:
        name = "fake"

        def __init__(self) -> None:
            self.native_calls = 0
            self.legacy_calls = 0

        def plan_result(self, request: LeaderPlanRequest) -> LeaderPlanResult:
            self.native_calls += 1
            schema = build_leader_plan_schema(request)
            constraint_mode = (
                "prompt_only"
                if mutation == "prompt_only_schema_spoof"
                else "native_json_schema"
            )
            provenance = build_leader_generation_provenance(
                request=request,
                provider=self.name,
                constraint_mode=constraint_mode,
                schema=schema if constraint_mode == "native_json_schema" else None,
            )
            if mutation == "incomplete":
                provenance = {"constraint_mode": "native_json_schema"}
            elif mutation == "provider":
                provenance["provider"] = "spoofed"
            elif mutation == "model":
                provenance["model"] = "spoofed"
            elif mutation == "schema_hash":
                provenance["schema_hash"] = "sha256:" + "0" * 64
            elif mutation == "boolean_attempt":
                provenance["attempt_count"] = True
            else:
                provenance["schema_version"] = "leader-plan/v1"
                provenance["schema_hash"] = "sha256:" + "0" * 64
            return LeaderPlanResult(plan=_plan(), leader_generation=provenance)

        def plan(self, _request: LeaderPlanRequest) -> dict[str, object]:
            self.legacy_calls += 1
            return _plan()

    provider = NativeProvider()
    with pytest.raises(
        ProviderPlanValidationError,
        match="leader provider plan result provenance is invalid",
    ):
        LeaderOrchestrator(config, provider).plan_result(
            "structured mission task",
            "fake-plan",
            selected_agent_ids=("planner", "reviewer"),
            step_count=2,
            timeout_seconds=180,
        )

    assert provider.native_calls == 1
    assert provider.legacy_calls == 0


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


def test_orchestrator_resolves_legacy_authority_before_validating_plan(tmp_path: Path) -> None:
    config = _config(tmp_path)

    class LegacyProvider:
        name = "fake"

        def plan(self, _request: LeaderPlanRequest) -> dict[str, object]:
            plan = _plan()
            plan["steps"] = plan["steps"][:1]
            return plan

    with pytest.raises(
        ProviderPlanValidationError,
        match="provider plan must include exactly 3 steps",
    ):
        LeaderOrchestrator(config, LegacyProvider()).plan_result(
            "structured mission task"
        )


def test_orchestrator_rejects_legacy_native_schema_claim(tmp_path: Path) -> None:
    config = _config(tmp_path)

    class LegacyProvider:
        name = "fake"
        constraint_mode = "native_json_schema"

        def plan(self, _request: LeaderPlanRequest) -> dict[str, object]:
            return _configured_plan(config)

    with pytest.raises(
        ProviderPlanValidationError,
        match="legacy leader provider cannot claim native JSON schema",
    ) as raised:
        LeaderOrchestrator(config, LegacyProvider()).plan_result(
            "structured mission task"
        )

    assert raised.value.code == "native_schema_unavailable"


@pytest.mark.parametrize(
    ("provider_name", "constraint_mode"),
    [
        ("fake", "local"),
        ("openai-compatible", "json_object"),
        ("codex-cli", "prompt_only"),
    ],
)
def test_orchestrator_preserves_valid_legacy_constraint_modes(
    tmp_path: Path,
    provider_name: str,
    constraint_mode: str,
) -> None:
    config = _config(tmp_path)

    class LegacyProvider:
        name = provider_name

        def plan(self, _request: LeaderPlanRequest) -> dict[str, object]:
            return _configured_plan(config)

    provider = LegacyProvider()
    provider.constraint_mode = constraint_mode
    result = LeaderOrchestrator(config, provider).plan_result(
        "structured mission task"
    )

    assert result.leader_generation["provider"] == provider_name
    assert result.leader_generation["constraint_mode"] == constraint_mode
    assert result.leader_generation["schema_hash"] is None


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
        LeaderGateway(
            provider_factory=factory,
            which=lambda _name: "/bin/codex",
            leader_cli_probe=lambda _provider: (True, None),
        ).generate_mission(
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
            selected_agent_ids=("planner", "reviewer"),
            step_count=2,
        ),
        CancellationToken(),
    )

    assert candidate.provider == "claude-cli"
    assert candidate.plan["steps"][1]["agent_id"] == "reviewer"
    assert candidate.leader_generation == {
        "provider": "claude-cli",
        "model": "claude-sonnet",
        "constraint_mode": "prompt_only",
        "schema_version": None,
        "schema_hash": None,
        "attempt_count": 1,
        "regeneration_used": False,
        "selected_agent_ids": ["planner", "reviewer"],
        "step_count": 2,
    }
    assert json.dumps(candidate.leader_generation) not in json.dumps(_plan())
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
