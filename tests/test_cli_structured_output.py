from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import stat
import subprocess

import pytest

from agentdeck.config import load_config, write_default_config
from agentdeck.conversation.leader_gateway import (
    CancellationToken,
    LeaderGateway,
    LeaderGatewayError,
    LeaderRequest,
)
from agentdeck.providers import LeaderPlanRequest
from agentdeck.orchestration.leader import LeaderOrchestrator
from agentdeck.providers.cli_subprocess import (
    MAX_CLI_LEADER_OUTPUT_BYTES,
    ClaudeCliProvider,
    CliLeaderProviderError,
    CodexCliProvider,
    cli_native_schema_ready,
)
from agentdeck.providers.plan_schema import (
    LEADER_PLAN_SCHEMA_VERSION,
    build_leader_plan_schema,
    canonical_leader_plan_schema_hash,
)


def _request(tmp_path: Path) -> LeaderPlanRequest:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    config = load_config(root)
    config = replace(
        config,
        agents=tuple(agent for agent in config.agents if agent.agent_id in {"planner", "reviewer"}),
    )
    return LeaderPlanRequest(
        task="implement, review, revise, and accept",
        config=config,
        model="gpt-test",
        selected_agent_ids=("planner", "reviewer"),
        step_count=4,
        timeout_seconds=180,
    )


def _valid_plan() -> dict[str, object]:
    return {
        "goal": "ship safely",
        "summary": "implementation, review, revision, and acceptance",
        "steps": [
            {
                "step": 1,
                "agent_id": "planner",
                "role": "planning",
                "task": "implement",
                "risk": "needs review",
                "requires_approval": True,
            },
            {
                "step": 2,
                "agent_id": "reviewer",
                "role": "review",
                "task": "review",
                "risk": "may find issues",
                "requires_approval": True,
            },
            {
                "step": 3,
                "agent_id": "planner",
                "role": "planning",
                "task": "revise",
                "risk": "needs re-review",
                "requires_approval": True,
            },
            {
                "step": 4,
                "agent_id": "reviewer",
                "role": "review",
                "task": "accept",
                "risk": "final verification",
                "requires_approval": True,
            },
        ],
    }


def _claude_envelope(
    structured_output: object | None = None,
    **metadata: object,
) -> dict[str, object]:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "structured_output": (
            _valid_plan() if structured_output is None else structured_output
        ),
        **metadata,
    }


def test_claude_plan_result_uses_native_schema_envelope_and_private_stdout(
    tmp_path: Path, monkeypatch
) -> None:
    request = _request(tmp_path)
    seen: dict[str, object] = {}
    real_create = ClaudeCliProvider._create_private_output

    def tracking_create(path: str):
        seen["output_path"] = Path(path)
        return real_create(path)

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        output_stat = os.fstat(kwargs["stdout"].fileno())
        assert stat.S_IMODE(output_stat.st_mode) == 0o600
        assert stat.S_IMODE(Path(seen["output_path"]).parent.stat().st_mode) == 0o700
        os.write(
            kwargs["stdout"].fileno(),
            json.dumps(_claude_envelope()).encode("utf-8"),
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(
        ClaudeCliProvider, "_create_private_output", staticmethod(tracking_create)
    )
    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    result = ClaudeCliProvider().plan_result(request)

    schema = json.dumps(
        build_leader_plan_schema(request),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    command = seen["command"]
    assert command == [
        "claude",
        "--model",
        "gpt-test",
        "--print",
        "--output-format",
        "json",
        "--permission-mode",
        "plan",
        "--json-schema",
        schema,
    ]
    assert seen["kwargs"]["stderr"] is subprocess.DEVNULL
    assert "capture_output" not in seen["kwargs"]
    assert result.plan["goal"] == "ship safely"
    assert result.leader_generation["constraint_mode"] == "native_json_schema"
    assert result.leader_generation["attempt_count"] == 1
    assert not Path(seen["output_path"]).exists()


@pytest.mark.parametrize(
    ("provider", "command", "help_text"),
    [
        (
            "codex-cli",
            ["codex", "exec", "--help"],
            "usage: codex exec --output-schema PATH --output-last-message PATH",
        ),
        (
            "claude-cli",
            ["claude", "--help"],
            "usage: claude --json-schema JSON --output-format json",
        ),
    ],
)
def test_cli_native_schema_probe_detects_required_help_flags_read_only(
    tmp_path: Path, monkeypatch, provider: str, command: list[str], help_text: str
) -> None:
    seen: dict[str, object] = {}

    def fake_run(actual_command, **kwargs):
        seen["command"] = actual_command
        seen["kwargs"] = kwargs
        assert kwargs["stdout"] is kwargs["stderr"]
        os.write(kwargs["stdout"].fileno(), help_text.encode("utf-8"))
        return subprocess.CompletedProcess(actual_command, 0)

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    assert cli_native_schema_ready(provider) == (True, None)
    assert seen["command"] == command
    assert seen["kwargs"]["timeout"] == 5
    assert seen["kwargs"]["check"] is False
    assert "cwd" not in seen["kwargs"]
    assert "input" not in seen["kwargs"]
    assert "capture_output" not in seen["kwargs"]


@pytest.mark.parametrize(
    ("provider", "scenario", "expected"),
    [
        (
            "unknown-cli",
            "unknown",
            "Leader CLI native JSON schema is unsupported",
        ),
        (
            "claude-cli",
            "missing_executable",
            "Leader CLI executable is not available",
        ),
        (
            "claude-cli",
            "missing_flag",
            "Leader CLI native JSON schema capability is unavailable",
        ),
        (
            "claude-cli",
            "timeout",
            "Leader CLI native JSON schema capability is unavailable",
        ),
        (
            "claude-cli",
            "nonzero",
            "Leader CLI native JSON schema capability is unavailable",
        ),
        (
            "claude-cli",
            "oserror",
            "Leader CLI native JSON schema capability is unavailable",
        ),
    ],
)
def test_cli_native_schema_probe_returns_only_fixed_blockers(
    monkeypatch, provider: str, scenario: str, expected: str
) -> None:
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        if scenario == "missing_executable":
            raise FileNotFoundError("SECRET_PATH")
        if scenario == "timeout":
            raise subprocess.TimeoutExpired(
                command, 5, output="SECRET_HELP", stderr="SECRET_HELP"
            )
        if scenario == "oserror":
            raise OSError("SECRET_HELP")
        os.write(kwargs["stdout"].fileno(), b"--json-schema SECRET_HELP")
        return subprocess.CompletedProcess(command, 9 if scenario == "nonzero" else 0)

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    ready, blocker = cli_native_schema_ready(provider)

    assert ready is False
    assert blocker == expected
    assert "SECRET" not in repr((ready, blocker))
    assert calls == (0 if scenario == "unknown" else 1)


@pytest.mark.parametrize(
    ("provider", "spoofed_help"),
    [
        (
            "claude-cli",
            "--json-schema-file --output-formatting",
        ),
        (
            "codex-cli",
            "--x-output-schema --output-last-message-suffix",
        ),
        (
            "codex-cli",
            "--output-schema-file --output-last-message-suffix",
        ),
        (
            "claude-cli",
            "---json-schema ---output-format",
        ),
        (
            "claude-cli",
            "x--json-schema x--output-format",
        ),
        (
            "codex-cli",
            "---output-schema ---output-last-message",
        ),
        (
            "codex-cli",
            "x--output-schema x--output-last-message",
        ),
        (
            "claude-cli",
            "--json-schema_ --output-format_",
        ),
        (
            "codex-cli",
            "--output-schema_ --output-last-message_",
        ),
    ],
)
def test_cli_native_schema_probe_rejects_required_flag_substring_spoofs(
    monkeypatch, provider: str, spoofed_help: str
) -> None:
    def fake_run(command, **kwargs):
        os.write(kwargs["stdout"].fileno(), spoofed_help.encode("utf-8"))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    assert cli_native_schema_ready(provider) == (
        False,
        "Leader CLI native JSON schema capability is unavailable",
    )


@pytest.mark.parametrize(
    "case",
    [
        "missing_structured_output",
        "outer_array",
        "wrong_type",
        "wrong_subtype",
        "wrong_is_error",
        "wrong_structured_output",
        "malformed",
        "duplicate_outer_key",
        "nan",
        "multiple_objects",
        "huge_integer",
        "deep_nesting",
    ],
)
def test_claude_rejects_invalid_native_output_envelopes(
    tmp_path: Path, monkeypatch, case: str
) -> None:
    request = _request(tmp_path)
    envelope = _claude_envelope()
    if case == "missing_structured_output":
        del envelope["structured_output"]
        payload = json.dumps(envelope)
    elif case == "outer_array":
        payload = json.dumps([envelope])
    elif case == "wrong_type":
        envelope["type"] = "assistant"
        payload = json.dumps(envelope)
    elif case == "wrong_subtype":
        envelope["subtype"] = "error"
        payload = json.dumps(envelope)
    elif case == "wrong_is_error":
        envelope["is_error"] = 0
        payload = json.dumps(envelope)
    elif case == "wrong_structured_output":
        envelope["structured_output"] = []
        payload = json.dumps(envelope)
    elif case == "malformed":
        payload = '{"type":"result"'
    elif case == "duplicate_outer_key":
        payload = json.dumps(envelope).replace(
            '"type": "result"',
            '"type": "SECRET", "type": "result"',
            1,
        )
    elif case == "nan":
        payload = json.dumps(envelope).replace('"risk": "needs review"', '"risk": NaN', 1)
    elif case == "huge_integer":
        payload = '{"type":"result","subtype":"success","is_error":false,' \
            '"structured_output":' + "9" * 5000 + "}"
    elif case == "deep_nesting":
        payload = '{"type":"result","subtype":"success","is_error":false,' \
            '"structured_output":' + "[" * 10_000 + "0" + "]" * 10_000 + "}"
    else:
        payload = json.dumps(envelope) + json.dumps(envelope)

    def fake_run(command, **kwargs):
        os.write(kwargs["stdout"].fileno(), payload.encode("utf-8"))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    with pytest.raises(CliLeaderProviderError) as raised:
        ClaudeCliProvider().plan_result(request)

    assert raised.value.stage == "json_parse"
    assert raised.value.diagnostic_code == "invalid_output_envelope"
    assert payload not in repr(_exception_values(raised.value))


def test_claude_rejects_exponent_overflow_in_ignored_envelope_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    request = _request(tmp_path)
    payload = json.dumps(_claude_envelope()).removesuffix("}") + ',"duration_ms":1e999}'

    def fake_run(command, **kwargs):
        os.write(kwargs["stdout"].fileno(), payload.encode("utf-8"))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    with pytest.raises(CliLeaderProviderError) as raised:
        ClaudeCliProvider().plan_result(request)

    assert raised.value.stage == "json_parse"
    assert raised.value.diagnostic_code == "invalid_output_envelope"
    _assert_exception_graph_redacted(raised.value, (payload, "inf", "Infinity"))


def test_claude_bounds_native_stdout_and_never_calls_fenced_parser(
    tmp_path: Path, monkeypatch
) -> None:
    request = _request(tmp_path)

    def fake_run(command, **kwargs):
        os.write(kwargs["stdout"].fileno(), b"x" * (MAX_CLI_LEADER_OUTPUT_BYTES + 1))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)
    monkeypatch.setattr(
        ClaudeCliProvider,
        "_load_json_plan",
        lambda *_args: pytest.fail("Claude native output must not use fenced parser"),
    )

    with pytest.raises(CliLeaderProviderError) as raised:
        ClaudeCliProvider().plan_result(request)

    assert raised.value.stage == "oversize"


def test_claude_ignores_official_envelope_metadata_without_persisting_secrets(
    tmp_path: Path, monkeypatch
) -> None:
    request = _request(tmp_path)
    secret = "SECRET_CLAUDE_SESSION_METADATA"

    def fake_run(command, **kwargs):
        payload = _claude_envelope(
            result=secret,
            session_id=secret,
            duration_ms=123,
            total_cost_usd=4.5,
        )
        os.write(kwargs["stdout"].fileno(), json.dumps(payload).encode("utf-8"))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)
    monkeypatch.setattr(
        ClaudeCliProvider,
        "_load_json_plan",
        lambda *_args: pytest.fail("Claude native output must not use fenced parser"),
    )

    result = ClaudeCliProvider().plan_result(request)

    assert result.plan == {
        **_valid_plan(),
        "approval_required": True,
        "dispatch_ready": False,
    }
    assert secret not in repr(result)


@pytest.mark.parametrize("scenario", ["timeout", "nonzero", "oserror"])
def test_claude_subprocess_failures_are_fixed_and_cleanup_private_stdout(
    tmp_path: Path, monkeypatch, scenario: str
) -> None:
    request = replace(_request(tmp_path), task="SECRET_CLAUDE_TASK")
    tracked: dict[str, Path] = {}
    real_create = ClaudeCliProvider._create_private_output

    def tracking_create(path: str):
        tracked["temp"] = Path(path).parent
        return real_create(path)

    def fake_run(command, **kwargs):
        if scenario == "timeout":
            raise subprocess.TimeoutExpired(
                command, 180, output="SECRET_STDOUT", stderr="SECRET_STDERR"
            )
        if scenario == "oserror":
            raise OSError("SECRET_OSERROR")
        os.write(kwargs["stdout"].fileno(), b"SECRET_OUTPUT")
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(ClaudeCliProvider, "_create_private_output", staticmethod(tracking_create))
    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    with pytest.raises(CliLeaderProviderError) as raised:
        ClaudeCliProvider().plan_result(request)

    assert raised.value.stage == ("timeout" if scenario == "timeout" else "nonzero")
    _assert_exception_graph_redacted(
        raised.value,
        ("SECRET", "claude", str(tmp_path)),
    )
    assert not tracked["temp"].exists()


def test_claude_plan_remains_dict_and_orchestrator_accepts_native_provenance(
    tmp_path: Path, monkeypatch
) -> None:
    request = _request(tmp_path)

    def fake_run(command, **kwargs):
        os.write(
            kwargs["stdout"].fileno(),
            json.dumps(_claude_envelope()).encode("utf-8"),
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)
    provider = ClaudeCliProvider()

    assert isinstance(provider.plan(request), dict)
    result = LeaderOrchestrator(request.config, provider).plan_result(
        request.task,
        request.model,
        selected_agent_ids=request.selected_agent_ids,
        step_count=request.step_count,
        timeout_seconds=request.timeout_seconds,
    )
    assert result.leader_generation["provider"] == "claude-cli"
    assert result.leader_generation["constraint_mode"] == "native_json_schema"
    assert result.leader_generation["attempt_count"] == 1


def test_codex_plan_result_uses_native_schema_file_and_cleans_temp_files(
    tmp_path: Path, monkeypatch
) -> None:
    request = _request(tmp_path)
    seen: dict[str, object] = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        schema_path = Path(command[command.index("--output-schema") + 1])
        result_path = Path(command[command.index("--output-last-message") + 1])
        seen["schema_path"] = schema_path
        seen["result_path"] = result_path
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        seen["schema"] = schema
        assert schema["properties"]["steps"]["minItems"] == 4
        assert schema["properties"]["steps"]["maxItems"] == 4
        assert stat.S_IMODE(schema_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(schema_path.parent.stat().st_mode) == 0o700
        assert not result_path.exists()
        result_path.write_text(json.dumps(_valid_plan()), encoding="utf-8")
        return subprocess.CompletedProcess(
            command, 0, stdout="SECRET_STATUS", stderr="SECRET_STDERR"
        )

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    result = CodexCliProvider().plan_result(request)

    assert result.plan["goal"] == "ship safely"
    assert result.leader_generation == {
        "provider": "codex-cli",
        "model": "gpt-test",
        "constraint_mode": "native_json_schema",
        "schema_version": LEADER_PLAN_SCHEMA_VERSION,
        "schema_hash": canonical_leader_plan_schema_hash(seen["schema"]),
        "attempt_count": 1,
        "regeneration_used": False,
        "selected_agent_ids": ["planner", "reviewer"],
        "step_count": 4,
    }
    command = seen["command"]
    assert command[-1] == "-"
    assert command.count("--output-schema") == 1
    assert command.count("--output-last-message") == 1
    assert command.count("gpt-test") == 1
    assert "shell" not in seen["kwargs"]
    assert "SECRET_STATUS" not in repr(result)
    assert "SECRET_STDERR" not in repr(result)
    assert not Path(seen["schema_path"]).exists()
    assert not Path(seen["result_path"]).exists()


def _output_paths(command: list[str]) -> tuple[Path, Path]:
    return (
        Path(command[command.index("--output-schema") + 1]),
        Path(command[command.index("--output-last-message") + 1]),
    )


def _exception_values(error: BaseException, limit: int = 32) -> list[object]:
    values: list[object] = []
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending and len(seen) < limit:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        values.extend(current.args)
        values.extend(vars(current).values())
        for linked in (current.__cause__, current.__context__):
            if isinstance(linked, BaseException):
                pending.append(linked)
    return values


def _assert_exception_graph_redacted(error: BaseException, secrets: tuple[str, ...]) -> None:
    rendered = repr(_exception_values(error))
    assert not any(secret in rendered for secret in secrets)


@pytest.mark.parametrize("boundary", ["provider", "gateway"])
@pytest.mark.parametrize(
    "scenario",
    ["timeout", "subprocess_oserror", "malformed", "semantic", "open", "read"],
)
def test_native_failures_remove_secret_bearing_exception_chains(
    tmp_path: Path, monkeypatch, boundary: str, scenario: str
) -> None:
    request = _request(tmp_path)
    secret = f"SECRET_{scenario.upper()}_{tmp_path}"
    request = replace(request, task=secret)
    tracked: dict[str, object] = {}

    def fake_run(command, **_kwargs):
        schema_path, result_path = _output_paths(command)
        tracked["temp"] = schema_path.parent
        if scenario == "timeout":
            raise subprocess.TimeoutExpired(
                command, 180, output=secret, stderr=secret
            )
        if scenario == "subprocess_oserror":
            raise OSError(secret)
        if scenario == "malformed":
            result_path.write_text('{"goal": "' + secret, encoding="utf-8")
        elif scenario == "semantic":
            plan = _valid_plan()
            plan["steps"][0]["role"] = secret
            result_path.write_text(json.dumps(plan), encoding="utf-8")
        elif scenario != "open":
            result_path.write_text(json.dumps(_valid_plan()), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout=None, stderr=None)

    real_open = os.open

    def secret_open(path, flags, mode=0o777, **kwargs):
        if scenario == "open" and os.fspath(path).endswith("result.json"):
            raise OSError(secret)
        descriptor = real_open(path, flags, mode, **kwargs)
        if os.fspath(path).endswith("result.json"):
            tracked["fd"] = descriptor
        return descriptor

    real_read = os.read

    def secret_read(descriptor: int, size: int) -> bytes:
        if scenario == "read" and descriptor == tracked.get("fd"):
            raise OSError(secret)
        return real_read(descriptor, size)

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)
    monkeypatch.setattr("agentdeck.providers.cli_subprocess.os.open", secret_open)
    monkeypatch.setattr("agentdeck.providers.cli_subprocess.os.read", secret_read)

    if boundary == "provider":
        with pytest.raises(CliLeaderProviderError) as raised:
            CodexCliProvider().plan_result(request)
        expected_stage = "timeout" if scenario == "timeout" else (
            "nonzero" if scenario == "subprocess_oserror" else (
            "schema" if scenario == "semantic" else "json_parse"
            )
        )
        assert raised.value.stage == expected_stage
        _assert_exception_graph_redacted(
            raised.value,
            (secret, str(tmp_path), "codex"),
        )
        assert not Path(tracked["temp"]).exists()
        return

    config = replace(
        request.config,
        leader=replace(request.config.leader, provider="codex-cli", model="gpt-test"),
    )
    with pytest.raises(LeaderGatewayError) as raised:
        LeaderGateway(
            provider_factory=lambda _name: CodexCliProvider(),
            which=lambda _name: "/safe/codex",
            leader_cli_probe=lambda _provider: (True, None),
        ).generate_mission(
            LeaderRequest(
                config,
                "mission",
                request.task,
                180,
                None,
                request.selected_agent_ids,
                request.step_count,
            ),
            CancellationToken(),
        )
    expected_stage = "timeout" if scenario == "timeout" else (
        "nonzero" if scenario == "subprocess_oserror" else (
        "schema" if scenario == "semantic" else "json_parse"
        )
    )
    assert raised.value.stage == expected_stage
    _assert_exception_graph_redacted(raised.value, (secret, str(tmp_path), "codex"))
    assert not Path(tracked["temp"]).exists()


def test_gateway_removes_generic_provider_exception_context(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    secret = f"SECRET_GENERIC_PROVIDER_{tmp_path}"
    config = replace(
        request.config,
        leader=replace(request.config.leader, provider="codex-cli", model="gpt-test"),
    )

    class FailedProvider:
        name = "codex-cli"

        def plan_result(self, _request):
            raise RuntimeError(secret)

    with pytest.raises(LeaderGatewayError) as raised:
        LeaderGateway(
            provider_factory=lambda _name: FailedProvider(),
            which=lambda _name: "/safe/codex",
            leader_cli_probe=lambda _provider: (True, None),
        ).generate_mission(
            LeaderRequest(
                config,
                "mission",
                request.task,
                180,
                None,
                request.selected_agent_ids,
                request.step_count,
            ),
            CancellationToken(),
        )

    assert raised.value.stage == "backend_failure"
    _assert_exception_graph_redacted(raised.value, (secret, str(tmp_path), "codex"))


@pytest.mark.parametrize("scenario", ["timeout", "malformed"])
def test_legacy_cli_failure_sanitization_keeps_claude_success_transport_unchanged(
    tmp_path: Path, monkeypatch, scenario: str
) -> None:
    request = _request(tmp_path)
    secret = f"SECRET_CLAUDE_{scenario}_{tmp_path}"
    request = replace(request, task=secret)

    def fake_run(command, **_kwargs):
        if scenario == "timeout":
            raise subprocess.TimeoutExpired(command, 120, output=secret, stderr=secret)
        return subprocess.CompletedProcess(command, 0, stdout=secret, stderr=secret)

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    with pytest.raises(CliLeaderProviderError) as raised:
        ClaudeCliProvider().plan_result(request)

    assert raised.value.stage == ("timeout" if scenario == "timeout" else "json_parse")
    _assert_exception_graph_redacted(raised.value, (secret, str(tmp_path), "claude"))


@pytest.mark.parametrize(
    ("stage", "code"),
    [
        (True, None),
        ([], None),
        ("SECRET_STAGE", None),
        ("schema", True),
        ("schema", "SECRET_CODE"),
        ("json_parse", "role_mismatch"),
        ("schema", "invalid_output_envelope"),
        ("timeout", "invalid_output_envelope"),
    ],
)
def test_cli_error_rejects_invalid_stage_and_diagnostic_combinations_without_echo(
    stage: object, code: object
) -> None:
    with pytest.raises(ValueError) as raised:
        CliLeaderProviderError(stage, code)
    rendered = repr(raised.value) + repr(vars(raised.value))
    assert "SECRET_STAGE" not in rendered
    assert "SECRET_CODE" not in rendered


@pytest.mark.parametrize(
    ("stage", "code"),
    [
        ("json_parse", None),
        ("json_parse", "invalid_output_envelope"),
        ("schema", None),
        ("schema", "role_mismatch"),
        ("timeout", None),
    ],
)
def test_cli_error_accepts_only_supported_stage_and_diagnostic_combinations(
    stage: str, code: str | None
) -> None:
    error = CliLeaderProviderError(stage, code)
    assert error.stage == stage
    assert error.diagnostic_code == code


@pytest.mark.parametrize(
    "case",
    [
        "duplicate_key",
        "nan",
        "infinity",
        "exponent_overflow",
        "top_level_extra",
        "step_extra",
    ],
)
def test_codex_strict_json_and_schema_parity_rejects_noncanonical_output(
    tmp_path: Path, monkeypatch, case: str
) -> None:
    request = _request(tmp_path)
    secret = f"SECRET_{case.upper()}"
    plan = _valid_plan()
    if case == "top_level_extra":
        plan[secret] = "extra"
        payload = json.dumps(plan)
    elif case == "step_extra":
        plan["steps"][0][secret] = "extra"
        payload = json.dumps(plan)
    elif case == "duplicate_key":
        payload = json.dumps(plan).replace(
            '"goal": "ship safely"',
            f'"goal": "{secret}", "goal": "ship safely"',
            1,
        )
    elif case == "nan":
        payload = json.dumps(plan).replace('"risk": "needs review"', '"risk": NaN', 1)
    elif case == "infinity":
        payload = json.dumps(plan).replace(
            '"risk": "needs review"', '"risk": Infinity', 1
        )
    else:
        payload = json.dumps(plan).replace(
            '"risk": "needs review"', '"risk": 1e999', 1
        )

    def fake_run(command, **_kwargs):
        _schema_path, result_path = _output_paths(command)
        result_path.write_text(payload, encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout=None, stderr=None)

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    with pytest.raises(CliLeaderProviderError) as raised:
        CodexCliProvider().plan_result(request)

    assert raised.value.stage == "json_parse"
    assert raised.value.diagnostic_code == "invalid_output_envelope"
    _assert_exception_graph_redacted(raised.value, (secret, payload))


@pytest.mark.parametrize(
    ("case", "payload"),
    [
        (
            "integer_digit_limit",
            b'{"goal":"x","summary":"x","steps":' + b"9" * 5000 + b"}",
        ),
        (
            "recursion_limit",
            b'{"goal":"x","summary":"x","steps":'
            + b"[" * 10_000
            + b"0"
            + b"]" * 10_000
            + b"}",
        ),
    ],
    ids=["integer_digit_limit", "recursion_limit"],
)
def test_codex_strict_decoder_bounds_large_integer_and_deep_nesting_failures(
    tmp_path: Path, monkeypatch, case: str, payload: bytes
) -> None:
    request = _request(tmp_path)

    def fake_run(command, **_kwargs):
        _schema_path, result_path = _output_paths(command)
        result_path.write_bytes(payload)
        return subprocess.CompletedProcess(command, 0, stdout=None, stderr=None)

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    with pytest.raises(CliLeaderProviderError) as raised:
        CodexCliProvider().plan_result(request)

    assert raised.value.stage == "json_parse"
    assert raised.value.diagnostic_code == "invalid_output_envelope"
    rendered = repr(_exception_values(raised.value))
    assert payload.decode("ascii") not in rendered
    assert "Exceeds the limit" not in rendered
    assert "maximum recursion" not in rendered
    assert "ValueError" not in rendered
    assert "RecursionError" not in rendered


def test_codex_discards_subprocess_diagnostics_at_the_os_boundary(
    tmp_path: Path, monkeypatch
) -> None:
    request = _request(tmp_path)
    seen: dict[str, object] = {}

    class AdversarialResult:
        returncode = 0

        @property
        def stdout(self):
            pytest.fail("Codex stdout must never be consumed")

        @property
        def stderr(self):
            pytest.fail("Codex stderr must never be consumed")

    def fake_run(command, **kwargs):
        seen.update(kwargs)
        _schema_path, result_path = _output_paths(command)
        result_path.write_text(json.dumps(_valid_plan()), encoding="utf-8")
        return AdversarialResult()

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    result = CodexCliProvider().plan_result(request)

    assert result.plan["goal"] == "ship safely"
    assert seen["stdout"] is subprocess.DEVNULL
    assert seen["stderr"] is subprocess.DEVNULL
    assert "capture_output" not in seen
    assert seen["text"] is True
    assert seen["check"] is False
    assert seen["timeout"] == 180
    assert seen["cwd"] == request.config.root
    assert isinstance(seen["input"], str)


def test_native_cli_prompts_match_schema_and_normalize_control_flags_locally(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)

    codex_prompt = CodexCliProvider()._prompt(request)
    claude_prompt = ClaudeCliProvider()._prompt(request)

    assert "Required schema: goal, summary, steps." in codex_prompt
    assert "approval_required and dispatch_ready are normalized locally" in codex_prompt
    assert "must not be output" in codex_prompt
    assert "Required schema: goal, summary, steps, approval_required, dispatch_ready." not in codex_prompt
    assert "Required schema: goal, summary, steps." in claude_prompt
    assert "approval_required and dispatch_ready are normalized locally" in claude_prompt
    assert "must not be output" in claude_prompt
    assert "Required schema: goal, summary, steps, approval_required, dispatch_ready." not in claude_prompt


@pytest.mark.parametrize(
    "case",
    ["hardlink", "path_replacement", "unsafe_owner", "post_read_drift", "size_mismatch"],
)
def test_codex_rejects_unstable_or_unowned_result_files(
    tmp_path: Path, monkeypatch, case: str
) -> None:
    request = _request(tmp_path)
    tracked: dict[str, object] = {}
    real_open = os.open
    real_fstat = os.fstat
    real_read = os.read

    def fake_run(command, **_kwargs):
        _schema_path, result_path = _output_paths(command)
        tracked["path"] = result_path
        payload = json.dumps(_valid_plan()).encode("utf-8")
        if case == "hardlink":
            target = tmp_path / "hardlink-target.json"
            target.write_bytes(payload)
            os.link(target, result_path)
        else:
            result_path.write_bytes(payload)
            os.chmod(result_path, 0o644)
        return subprocess.CompletedProcess(command, 0, stdout=None, stderr=None)

    def tracking_open(path, flags, mode=0o777, **kwargs):
        descriptor = real_open(path, flags, mode, **kwargs)
        if os.fspath(path).endswith("result.json"):
            tracked["fd"] = descriptor
        return descriptor

    class UnsafeOwnerStat:
        def __init__(self, original):
            self._original = original
            self.st_uid = original.st_uid + 1

        def __getattr__(self, name):
            return getattr(self._original, name)

    def controlled_fstat(descriptor):
        current = real_fstat(descriptor)
        if case == "unsafe_owner" and descriptor == tracked.get("fd"):
            return UnsafeOwnerStat(current)
        return current

    reads = 0

    def controlled_read(descriptor, size):
        nonlocal reads
        if descriptor != tracked.get("fd"):
            return real_read(descriptor, size)
        if case in {"hardlink", "unsafe_owner"}:
            pytest.fail("hardlinked or unowned result must be rejected before read")
        reads += 1
        if case == "size_mismatch" and reads == 1:
            return real_read(descriptor, 7)
        if case == "size_mismatch" and reads > 1:
            return b""
        chunk = real_read(descriptor, size)
        if reads == 1 and chunk:
            result_path = tracked["path"]
            if case == "path_replacement":
                Path(result_path).unlink()
                Path(result_path).write_text(json.dumps(_valid_plan()), encoding="utf-8")
            elif case == "post_read_drift":
                current = Path(result_path).stat()
                os.utime(
                    result_path,
                    ns=(current.st_atime_ns, current.st_mtime_ns + 1_000_000),
                )
        return chunk

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)
    monkeypatch.setattr("agentdeck.providers.cli_subprocess.os.open", tracking_open)
    monkeypatch.setattr("agentdeck.providers.cli_subprocess.os.fstat", controlled_fstat)
    monkeypatch.setattr("agentdeck.providers.cli_subprocess.os.read", controlled_read)
    if case == "size_mismatch":
        monkeypatch.setattr(
            "agentdeck.providers.cli_subprocess._strict_json_decode",
            lambda _payload: pytest.fail("size mismatch must fail before JSON decoding"),
        )

    with pytest.raises(CliLeaderProviderError) as raised:
        CodexCliProvider().plan_result(request)

    assert raised.value.stage == "json_parse"
    assert raised.value.diagnostic_code == "invalid_output_envelope"


def test_codex_normalizes_owned_single_link_result_mode_to_0600(
    tmp_path: Path, monkeypatch
) -> None:
    request = _request(tmp_path)
    real_fchmod = os.fchmod
    seen: dict[str, int] = {}

    def fake_run(command, **_kwargs):
        _schema_path, result_path = _output_paths(command)
        result_path.write_text(json.dumps(_valid_plan()), encoding="utf-8")
        os.chmod(result_path, 0o644)
        return subprocess.CompletedProcess(command, 0, stdout=None, stderr=None)

    def recording_fchmod(descriptor: int, mode: int) -> None:
        real_fchmod(descriptor, mode)
        seen["mode"] = stat.S_IMODE(os.fstat(descriptor).st_mode)

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)
    monkeypatch.setattr("agentdeck.providers.cli_subprocess.os.fchmod", recording_fchmod)

    result = CodexCliProvider().plan_result(request)

    assert result.plan["goal"] == "ship safely"
    assert seen["mode"] == 0o600


@pytest.mark.parametrize(
    ("prior_failure", "expected_stage"),
    [(False, "json_parse"), (True, "oversize")],
)
def test_result_descriptor_close_failure_is_sanitized_and_cannot_mask_prior_error(
    tmp_path: Path, monkeypatch, prior_failure: bool, expected_stage: str
) -> None:
    request = _request(tmp_path)
    secret = "SECRET_CLOSE_FAILURE"
    tracked: dict[str, int] = {}
    real_open = os.open
    real_close = os.close

    def fake_run(command, **_kwargs):
        _schema_path, result_path = _output_paths(command)
        if prior_failure:
            with result_path.open("wb") as output:
                output.truncate(MAX_CLI_LEADER_OUTPUT_BYTES + 1)
        else:
            result_path.write_text(json.dumps(_valid_plan()), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout=None, stderr=None)

    def tracking_open(path, flags, mode=0o777, **kwargs):
        descriptor = real_open(path, flags, mode, **kwargs)
        if os.fspath(path).endswith("result.json"):
            tracked["fd"] = descriptor
        return descriptor

    def failing_close(descriptor: int) -> None:
        if descriptor == tracked.get("fd"):
            raise OSError(secret)
        real_close(descriptor)

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)
    monkeypatch.setattr("agentdeck.providers.cli_subprocess.os.open", tracking_open)
    monkeypatch.setattr("agentdeck.providers.cli_subprocess.os.close", failing_close)

    try:
        with pytest.raises(CliLeaderProviderError) as raised:
            CodexCliProvider().plan_result(request)
    finally:
        if "fd" in tracked:
            real_close(tracked["fd"])

    assert raised.value.stage == expected_stage
    if expected_stage == "json_parse":
        assert raised.value.diagnostic_code == "invalid_output_envelope"
    _assert_exception_graph_redacted(raised.value, (secret,))


@pytest.mark.parametrize("unsafe_kind", ["missing", "symlink", "directory", "fifo"])
def test_codex_rejects_missing_or_unsafe_result_files_without_stdout_fallback(
    tmp_path: Path, monkeypatch, unsafe_kind: str
) -> None:
    request = _request(tmp_path)
    seen: dict[str, Path] = {}

    def fake_run(command, **_kwargs):
        schema_path, result_path = _output_paths(command)
        seen["schema"] = schema_path
        seen["result"] = result_path
        if unsafe_kind == "symlink":
            target = tmp_path / "SECRET_SYMLINK_TARGET"
            target.write_text(json.dumps(_valid_plan()), encoding="utf-8")
            result_path.symlink_to(target)
        elif unsafe_kind == "directory":
            result_path.mkdir()
        elif unsafe_kind == "fifo":
            os.mkfifo(result_path)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(_valid_plan()),
            stderr="SECRET_STDERR",
        )

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    with pytest.raises(CliLeaderProviderError) as raised:
        CodexCliProvider().plan_result(request)

    assert raised.value.stage == "json_parse"
    assert raised.value.diagnostic_code == "invalid_output_envelope"
    assert "SECRET" not in repr(raised.value) + repr(vars(raised.value))
    assert not seen["schema"].parent.exists()


def test_codex_rejects_result_larger_than_two_mib_by_stat(tmp_path: Path, monkeypatch) -> None:
    request = _request(tmp_path)
    seen: dict[str, Path] = {}

    def fake_run(command, **_kwargs):
        schema_path, result_path = _output_paths(command)
        seen["temp"] = schema_path.parent
        with result_path.open("wb") as output:
            output.truncate(MAX_CLI_LEADER_OUTPUT_BYTES + 1)
        return subprocess.CompletedProcess(command, 0, stdout="SECRET", stderr="SECRET")

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    with pytest.raises(CliLeaderProviderError) as raised:
        CodexCliProvider().plan_result(request)

    assert raised.value.stage == "oversize"
    assert not seen["temp"].exists()


@pytest.mark.parametrize("payload", [b"not-json", b"[]", b'{} {}', b"\xff"])
def test_codex_rejects_malformed_or_non_object_result_envelopes(
    tmp_path: Path, monkeypatch, payload: bytes
) -> None:
    request = _request(tmp_path)

    def fake_run(command, **_kwargs):
        _schema_path, result_path = _output_paths(command)
        result_path.write_bytes(payload)
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(_valid_plan()), stderr="SECRET_STDERR"
        )

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    with pytest.raises(CliLeaderProviderError) as raised:
        CodexCliProvider().plan_result(request)

    assert raised.value.stage == "json_parse"
    assert raised.value.diagnostic_code == "invalid_output_envelope"
    assert "SECRET_STDERR" not in repr(raised.value) + repr(vars(raised.value))


def test_codex_bounded_read_rejects_growth_beyond_stat_size(tmp_path: Path, monkeypatch) -> None:
    request = _request(tmp_path)

    def fake_run(command, **_kwargs):
        _schema_path, result_path = _output_paths(command)
        result_path.write_bytes(b"{}")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    real_read = os.read
    emitted = 0

    def growing_read(descriptor: int, size: int) -> bytes:
        nonlocal emitted
        if emitted <= MAX_CLI_LEADER_OUTPUT_BYTES:
            chunk = b"x" * min(size, MAX_CLI_LEADER_OUTPUT_BYTES + 1 - emitted)
            emitted += len(chunk)
            return chunk
        return real_read(descriptor, size)

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)
    monkeypatch.setattr("agentdeck.providers.cli_subprocess.os.read", growing_read)

    with pytest.raises(CliLeaderProviderError) as raised:
        CodexCliProvider().plan_result(request)

    assert raised.value.stage == "oversize"


@pytest.mark.parametrize("scenario", ["nonzero", "timeout"])
def test_codex_subprocess_failures_are_redacted_and_cleanup_temp_files(
    tmp_path: Path, monkeypatch, scenario: str
) -> None:
    request = _request(tmp_path)
    seen: dict[str, Path] = {}

    def fake_run(command, **_kwargs):
        schema_path, _result_path = _output_paths(command)
        seen["temp"] = schema_path.parent
        if scenario == "timeout":
            raise subprocess.TimeoutExpired(
                command, 180, output="SECRET_STATUS", stderr="SECRET_STDERR"
            )
        return subprocess.CompletedProcess(
            command, 7, stdout="SECRET_STATUS", stderr="SECRET_STDERR"
        )

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    with pytest.raises(CliLeaderProviderError) as raised:
        CodexCliProvider().plan_result(request)

    assert raised.value.stage == scenario
    rendered = repr(raised.value) + str(raised.value) + repr(vars(raised.value))
    assert "SECRET_STATUS" not in rendered
    assert "SECRET_STDERR" not in rendered
    assert "codex" not in rendered
    assert str(tmp_path) not in rendered
    assert not seen["temp"].exists()


def test_codex_schema_mismatch_preserves_only_exact_diagnostic_code(
    tmp_path: Path, monkeypatch
) -> None:
    request = _request(tmp_path)
    raw_value = "SECRET_UNSELECTED_AGENT"

    def fake_run(command, **_kwargs):
        _schema_path, result_path = _output_paths(command)
        plan = _valid_plan()
        plan["steps"][0]["agent_id"] = raw_value
        result_path.write_text(json.dumps(plan), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    with pytest.raises(CliLeaderProviderError) as raised:
        CodexCliProvider().plan_result(request)

    assert raised.value.stage == "schema"
    assert raised.value.diagnostic_code == "unknown_agent"
    assert raw_value not in repr(raised.value) + repr(vars(raised.value))


def test_codex_plan_remains_dict_and_orchestrator_accepts_native_provenance(
    tmp_path: Path, monkeypatch
) -> None:
    request = _request(tmp_path)

    def fake_run(command, **_kwargs):
        _schema_path, result_path = _output_paths(command)
        result_path.write_text(json.dumps(_valid_plan()), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)
    provider = CodexCliProvider()

    assert isinstance(provider.plan(request), dict)
    result = LeaderOrchestrator(request.config, provider).plan_result(
        request.task,
        request.model,
        selected_agent_ids=request.selected_agent_ids,
        step_count=request.step_count,
        timeout_seconds=request.timeout_seconds,
    )
    assert result.leader_generation["constraint_mode"] == "native_json_schema"
    assert result.leader_generation["attempt_count"] == 1
