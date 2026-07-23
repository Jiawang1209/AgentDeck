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
        "--no-session-persistence",
        "--json-schema",
        schema,
    ]
    assert command.count("--no-session-persistence") == 1
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
            "usage: claude --json-schema JSON --output-format json "
            "--no-session-persistence",
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
        (
            "claude-cli",
            "oversize",
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
        os.write(
            kwargs["stdout"].fileno(),
            b"x" * (MAX_CLI_LEADER_OUTPUT_BYTES + 1)
            if scenario == "oversize"
            else b"--json-schema SECRET_HELP",
        )
        return subprocess.CompletedProcess(command, 9 if scenario == "nonzero" else 0)

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    ready, blocker = cli_native_schema_ready(provider)

    assert ready is False
    assert blocker == expected
    assert "SECRET" not in repr((ready, blocker))
    assert calls == (0 if scenario == "unknown" else 1)


def test_claude_native_probe_blocks_without_no_session_persistence(
    monkeypatch,
) -> None:
    def fake_run(command, **kwargs):
        os.write(
            kwargs["stdout"].fileno(),
            b"--json-schema JSON --output-format json",
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    assert cli_native_schema_ready("claude-cli") == (
        False,
        "Leader CLI native JSON schema capability is unavailable",
    )


@pytest.mark.parametrize(
    ("provider", "spoofed_help"),
    [
        (
            "claude-cli",
            "--json-schema-file --output-formatting --no-session-persistence",
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
            "---json-schema ---output-format --no-session-persistence",
        ),
        (
            "claude-cli",
            "x--json-schema x--output-format --no-session-persistence",
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
            "--json-schema_ --output-format_ --no-session-persistence",
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


@pytest.mark.parametrize("path_attack", ["replacement", "unlink"])
def test_claude_rejects_stdout_path_detached_from_creation_inode(
    tmp_path: Path, monkeypatch, path_attack: str
) -> None:
    request = _request(tmp_path)
    tracked: dict[str, Path] = {}
    real_create = ClaudeCliProvider._create_private_output

    def tracking_create(path: str):
        tracked["path"] = Path(path)
        return real_create(path)

    def fake_run(command, **kwargs):
        payload = json.dumps(_claude_envelope()).encode("utf-8")
        os.write(kwargs["stdout"].fileno(), payload)
        tracked["path"].unlink()
        if path_attack == "replacement":
            tracked["path"].write_bytes(payload)
            tracked["path"].chmod(0o600)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(
        ClaudeCliProvider, "_create_private_output", staticmethod(tracking_create)
    )
    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    with pytest.raises(CliLeaderProviderError) as raised:
        ClaudeCliProvider().plan_result(request)

    assert raised.value.stage == "json_parse"
    assert raised.value.diagnostic_code == "invalid_output_envelope"
    assert not tracked["path"].exists()


@pytest.mark.parametrize("path_attack", ["replacement", "unlink"])
def test_cli_native_probe_rejects_help_path_detached_from_creation_inode(
    monkeypatch, path_attack: str
) -> None:
    tracked: dict[str, Path] = {}
    real_create = ClaudeCliProvider._create_private_output
    payload = (
        b"--json-schema --output-format --no-session-persistence"
    )

    def tracking_create(path: str):
        tracked["path"] = Path(path)
        return real_create(path)

    def fake_run(command, **kwargs):
        os.write(kwargs["stdout"].fileno(), payload)
        tracked["path"].unlink()
        if path_attack == "replacement":
            tracked["path"].write_bytes(payload)
            tracked["path"].chmod(0o600)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(
        ClaudeCliProvider, "_create_private_output", staticmethod(tracking_create)
    )
    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    assert cli_native_schema_ready("claude-cli") == (
        False,
        "Leader CLI native JSON schema capability is unavailable",
    )
    assert not tracked["path"].exists()


def test_cli_native_probe_private_sink_close_failure_is_fixed_and_redacted(
    monkeypatch,
) -> None:
    secret = "SECRET_PROBE_CLOSE"
    real_create = ClaudeCliProvider._create_private_output

    class CloseFailureSink:
        def __init__(self, sink) -> None:
            self._sink = sink

        def __getattr__(self, name):
            return getattr(self._sink, name)

        def close(self) -> None:
            self._sink.close()
            raise OSError(secret)

    def failing_create(path: str):
        return CloseFailureSink(real_create(path))

    def fake_run(command, **kwargs):
        os.write(
            kwargs["stdout"].fileno(),
            b"--json-schema --output-format --no-session-persistence",
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(
        ClaudeCliProvider, "_create_private_output", staticmethod(failing_create)
    )
    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    result = cli_native_schema_ready("claude-cli")

    assert result == (
        False,
        "Leader CLI native JSON schema capability is unavailable",
    )
    assert secret not in repr(result)


def test_claude_rejects_private_sink_mutation_during_same_fd_read(
    tmp_path: Path, monkeypatch
) -> None:
    request = _request(tmp_path)
    tracked: dict[str, object] = {}
    real_read = os.read

    def fake_run(command, **kwargs):
        tracked["fd"] = kwargs["stdout"].fileno()
        os.write(
            kwargs["stdout"].fileno(),
            json.dumps(_claude_envelope()).encode("utf-8"),
        )
        return subprocess.CompletedProcess(command, 0)

    attempt = 0
    mutated_attempts: set[int] = set()

    original_fake_run = fake_run

    def counted_run(command, **kwargs):
        nonlocal attempt
        attempt += 1
        tracked["attempt"] = attempt
        return original_fake_run(command, **kwargs)

    def mutating_read(descriptor: int, size: int) -> bytes:
        chunk = real_read(descriptor, size)
        current_attempt = tracked.get("attempt")
        if (
            descriptor == tracked.get("fd")
            and chunk
            and isinstance(current_attempt, int)
            and current_attempt not in mutated_attempts
        ):
            mutated_attempts.add(current_attempt)
            os.lseek(descriptor, 0, os.SEEK_END)
            os.write(descriptor, b"x")
        return chunk

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", counted_run)
    monkeypatch.setattr("agentdeck.providers.cli_subprocess.os.read", mutating_read)

    with pytest.raises(CliLeaderProviderError) as raised:
        ClaudeCliProvider().plan_result(request)

    assert raised.value.stage == "json_parse"
    assert raised.value.diagnostic_code == "invalid_output_envelope"
    assert mutated_attempts == {1}
    assert raised.value.attempt_count == 1


@pytest.mark.parametrize(
    ("prior_failure", "expected_stage"),
    [(False, "json_parse"), (True, "oversize")],
)
def test_claude_private_sink_close_failure_cannot_mask_prior_error(
    tmp_path: Path,
    monkeypatch,
    prior_failure: bool,
    expected_stage: str,
) -> None:
    request = _request(tmp_path)
    secret = "SECRET_CLAUDE_CLOSE"
    real_create = ClaudeCliProvider._create_private_output

    class CloseFailureSink:
        def __init__(self, sink) -> None:
            self._sink = sink

        def __getattr__(self, name):
            return getattr(self._sink, name)

        def close(self) -> None:
            self._sink.close()
            raise OSError(secret)

    def failing_create(path: str):
        return CloseFailureSink(real_create(path))

    def fake_run(command, **kwargs):
        payload = (
            b"x" * (MAX_CLI_LEADER_OUTPUT_BYTES + 1)
            if prior_failure
            else json.dumps(_claude_envelope()).encode("utf-8")
        )
        os.write(kwargs["stdout"].fileno(), payload)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(
        ClaudeCliProvider, "_create_private_output", staticmethod(failing_create)
    )
    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    with pytest.raises(CliLeaderProviderError) as raised:
        ClaudeCliProvider().plan_result(request)

    assert raised.value.stage == expected_stage
    if expected_stage == "json_parse":
        assert raised.value.diagnostic_code == "invalid_output_envelope"
    _assert_exception_graph_redacted(raised.value, (secret, str(tmp_path), "claude"))


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
        assert schema["properties"]["steps"]["minItems"] == 1
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


@pytest.mark.parametrize("provider_class", [CodexCliProvider, ClaudeCliProvider])
def test_native_workspace_creation_failure_is_fixed_and_redacted(
    tmp_path: Path, monkeypatch, provider_class
) -> None:
    request = _request(tmp_path)
    secret = f"SECRET_WORKSPACE_CREATE_{tmp_path}"
    prefixes: list[str | None] = []

    def failing_mkdtemp(*_args, **kwargs):
        prefixes.append(kwargs.get("prefix"))
        raise OSError(secret)

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.tempfile.mkdtemp", failing_mkdtemp)

    with pytest.raises(CliLeaderProviderError) as raised:
        provider_class().plan_result(request)

    assert raised.value.stage == "json_parse"
    assert raised.value.diagnostic_code == "invalid_output_envelope"
    assert prefixes == ["agentdeck-leader-"]
    assert raised.value.attempt_count == 1
    assert raised.value.retryable is False
    _assert_exception_graph_redacted(raised.value, (secret, str(tmp_path)))


def test_native_probe_and_gateway_block_on_workspace_creation_failure(
    tmp_path: Path, monkeypatch
) -> None:
    request = _request(tmp_path)
    secret = f"SECRET_PROBE_WORKSPACE_CREATE_{tmp_path}"
    prefixes: list[str | None] = []

    def failing_mkdtemp(*_args, **kwargs):
        prefixes.append(kwargs.get("prefix"))
        raise OSError(secret)

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.tempfile.mkdtemp", failing_mkdtemp)

    assert cli_native_schema_ready("claude-cli") == (
        False,
        "Leader CLI native JSON schema capability is unavailable",
    )
    status = LeaderGateway(which=lambda _name: "/safe/claude").describe(
        replace(request.config.leader, provider="claude-cli", model="claude-test")
    )
    assert status.readiness == "blocked"
    assert status.blockers == (
        "Leader CLI native JSON schema capability is unavailable",
    )
    assert prefixes == ["agentdeck-leader-probe-", "agentdeck-leader-probe-"]


@pytest.mark.parametrize("provider_class", [CodexCliProvider, ClaudeCliProvider])
def test_native_workspace_cleanup_failure_after_success_is_fixed_and_redacted(
    tmp_path: Path, monkeypatch, provider_class
) -> None:
    request = _request(tmp_path)
    secret = f"SECRET_WORKSPACE_CLEANUP_{tmp_path}"
    cleanup_calls = 0

    def fake_run(command, **kwargs):
        if "--output-last-message" in command:
            _schema_path, result_path = _output_paths(command)
            result_path.write_text(json.dumps(_valid_plan()), encoding="utf-8")
        else:
            os.write(
                kwargs["stdout"].fileno(),
                json.dumps(_claude_envelope()).encode("utf-8"),
            )
        return subprocess.CompletedProcess(command, 0)

    def failing_rmtree(*_args, **_kwargs):
        nonlocal cleanup_calls
        cleanup_calls += 1
        raise OSError(secret)

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)
    monkeypatch.setattr("agentdeck.providers.cli_subprocess.shutil.rmtree", failing_rmtree)

    with pytest.raises(CliLeaderProviderError) as raised:
        provider_class().plan_result(request)

    assert raised.value.stage == "json_parse"
    assert raised.value.diagnostic_code == "invalid_output_envelope"
    assert cleanup_calls == 1
    assert raised.value.attempt_count == 1
    assert raised.value.retryable is False
    _assert_exception_graph_redacted(raised.value, (secret, str(tmp_path)))


def test_native_probe_cleanup_failure_after_success_is_fixed(
    monkeypatch,
) -> None:
    cleanup_calls = 0

    def fake_run(command, **kwargs):
        os.write(
            kwargs["stdout"].fileno(),
            b"--json-schema --output-format --no-session-persistence",
        )
        return subprocess.CompletedProcess(command, 0)

    def failing_rmtree(*_args, **_kwargs):
        nonlocal cleanup_calls
        cleanup_calls += 1
        raise OSError("SECRET_PROBE_CLEANUP")

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)
    monkeypatch.setattr("agentdeck.providers.cli_subprocess.shutil.rmtree", failing_rmtree)

    assert cli_native_schema_ready("claude-cli") == (
        False,
        "Leader CLI native JSON schema capability is unavailable",
    )
    assert cleanup_calls == 1


def test_native_probe_cleanup_cannot_mask_missing_executable(monkeypatch) -> None:
    cleanup_calls = 0

    def fake_run(command, **_kwargs):
        raise FileNotFoundError(command[0])

    def failing_rmtree(*_args, **_kwargs):
        nonlocal cleanup_calls
        cleanup_calls += 1
        raise OSError("SECRET_PROBE_CLEANUP")

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)
    monkeypatch.setattr("agentdeck.providers.cli_subprocess.shutil.rmtree", failing_rmtree)

    assert cli_native_schema_ready("claude-cli") == (
        False,
        "Leader CLI executable is not available",
    )
    assert cleanup_calls == 1


@pytest.mark.parametrize("provider_class", [CodexCliProvider, ClaudeCliProvider])
@pytest.mark.parametrize(
    ("scenario", "expected_stage", "expected_code"),
    [
        ("timeout", "timeout", None),
        ("nonzero", "nonzero", None),
        ("json_parse", "json_parse", "invalid_output_envelope"),
        ("schema", "schema", "role_mismatch"),
    ],
)
def test_native_workspace_cleanup_cannot_mask_prior_failure(
    tmp_path: Path,
    monkeypatch,
    provider_class,
    scenario: str,
    expected_stage: str,
    expected_code: str | None,
) -> None:
    request = _request(tmp_path)
    secret = f"SECRET_TIMEOUT_CLEANUP_{tmp_path}"
    cleanup_calls = 0
    subprocess_calls = 0

    def fake_run(command, **kwargs):
        nonlocal subprocess_calls
        subprocess_calls += 1
        if scenario == "timeout":
            raise subprocess.TimeoutExpired(
                command, 180, output=secret, stderr=secret
            )
        if scenario == "nonzero":
            return subprocess.CompletedProcess(command, 9)
        if "--output-last-message" in command:
            _schema_path, result_path = _output_paths(command)
            if scenario == "json_parse":
                result_path.write_text("not-json", encoding="utf-8")
            else:
                plan = _valid_plan()
                plan["steps"][0]["role"] = "wrong-role"
                result_path.write_text(json.dumps(plan), encoding="utf-8")
        else:
            if scenario == "json_parse":
                envelope = {"type": "invalid"}
            else:
                plan = _valid_plan()
                plan["steps"][0]["role"] = "wrong-role"
                envelope = _claude_envelope()
                envelope["structured_output"] = plan
            os.write(kwargs["stdout"].fileno(), json.dumps(envelope).encode("utf-8"))
        return subprocess.CompletedProcess(command, 0)

    def failing_rmtree(*_args, **_kwargs):
        nonlocal cleanup_calls
        cleanup_calls += 1
        raise OSError(secret)

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)
    monkeypatch.setattr("agentdeck.providers.cli_subprocess.shutil.rmtree", failing_rmtree)

    with pytest.raises(CliLeaderProviderError) as raised:
        provider_class().plan_result(request)

    assert raised.value.stage == expected_stage
    assert raised.value.diagnostic_code == expected_code
    assert subprocess_calls == 1
    assert cleanup_calls == 1
    assert raised.value.attempt_count == 1
    assert raised.value.retryable is False
    _assert_exception_graph_redacted(raised.value, (secret, str(tmp_path)))


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
    assert 0 < seen["timeout"] <= CodexCliProvider.timeout
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

    reads = 0

    def fake_run(command, **_kwargs):
        nonlocal reads
        reads = 0
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
    assert raised.value.attempt_count == 1


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
    assert raised.value.attempt_count == 1
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


@pytest.mark.parametrize(
    ("outcomes", "expected_calls", "expected_stage", "expected_attempts"),
    [
        (("json_parse", "success"), 2, None, 2),
        (("schema", "success"), 2, None, 2),
        (("json_parse", "json_parse"), 2, "json_parse", 2),
        (("schema", "schema"), 2, "schema", 2),
        (("nonzero",), 1, "nonzero", 1),
        (("timeout",), 1, "timeout", 1),
        (("oversize",), 1, "oversize", 1),
    ],
)
def test_codex_native_regeneration_matrix_is_bounded_and_same_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcomes: tuple[str, ...],
    expected_calls: int,
    expected_stage: str | None,
    expected_attempts: int,
) -> None:
    request = _request(tmp_path)
    calls: list[tuple[list[str], str, float, bytes]] = []
    raw_secret = "SECRET_FIRST_PROVIDER_OUTPUT"

    def fake_run(command, **kwargs):
        call_number = len(calls)
        outcome = outcomes[call_number]
        schema_path, result_path = _output_paths(command)
        calls.append(
            (list(command), kwargs["input"], kwargs["timeout"], schema_path.read_bytes())
        )
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"], output=raw_secret)
        if outcome == "nonzero":
            return subprocess.CompletedProcess(command, 7, stdout=raw_secret, stderr=raw_secret)
        if outcome == "oversize":
            with result_path.open("wb") as output:
                output.truncate(MAX_CLI_LEADER_OUTPUT_BYTES + 1)
        elif outcome == "json_parse":
            result_path.write_text(raw_secret, encoding="utf-8")
        elif outcome == "schema":
            invalid = _valid_plan()
            invalid["steps"].append(
                {
                    "step": 5,
                    "agent_id": "planner",
                    "role": "planning",
                    "task": "extra step beyond authority",
                    "risk": "needs review",
                    "requires_approval": True,
                }
            )
            result_path.write_text(json.dumps(invalid), encoding="utf-8")
        else:
            result_path.write_text(json.dumps(_valid_plan()), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout=raw_secret, stderr=raw_secret)

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)
    if expected_stage is None:
        result = CodexCliProvider().plan_result(request)
        assert result.leader_generation["attempt_count"] == expected_attempts
        assert result.leader_generation["regeneration_used"] is (expected_attempts == 2)
        assert result.leader_generation["selected_agent_ids"] == ["planner", "reviewer"]
        assert result.leader_generation["step_count"] == 4
        assert result.leader_generation["schema_hash"] == canonical_leader_plan_schema_hash(
            build_leader_plan_schema(request)
        )
    else:
        with pytest.raises(CliLeaderProviderError) as raised:
            CodexCliProvider().plan_result(request)
        assert raised.value.stage == expected_stage
        assert raised.value.attempt_count == expected_attempts
        assert raised.value.constraint_mode == "native_json_schema"
        _assert_exception_graph_redacted(raised.value, (raw_secret, str(tmp_path)))

    assert len(calls) == expected_calls
    assert len(calls) <= 2
    if len(calls) == 2:
        first_argv, first_prompt, _, first_schema = calls[0]
        second_argv, second_prompt, _, second_schema = calls[1]

        def stable_command(argv: list[str]) -> tuple[str, ...]:
            stable = list(argv)
            for flag in ("--output-schema", "--output-last-message"):
                index = stable.index(flag)
                stable[index + 1] = "<private-resource>"
            return tuple(stable)

        assert stable_command(first_argv) == stable_command(second_argv)
        assert first_schema == second_schema
        assert first_schema == json.dumps(
            build_leader_plan_schema(request),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        assert request.task in second_prompt
        assert "Regenerate the complete plan" in second_prompt
        assert raw_secret not in second_prompt
        assert "--output-schema" not in second_prompt
        assert str(Path(first_argv[first_argv.index("--output-schema") + 1]).parent) not in second_prompt
        assert "invalid_step_count" in second_prompt or outcomes[0] == "json_parse"
    for argv, _prompt, _timeout, _schema in calls:
        schema_path = Path(argv[argv.index("--output-schema") + 1])
        assert not schema_path.parent.exists()


@pytest.mark.parametrize("provider_class", [CodexCliProvider, ClaudeCliProvider])
def test_native_regeneration_uses_one_total_deadline_and_remaining_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider_class
) -> None:
    request = replace(_request(tmp_path), timeout_seconds=10)
    timeouts: list[float] = []
    monotonic_values = iter((100.0, 100.0, 100.0, 104.0, 104.0, 104.0))
    monkeypatch.setattr(
        "agentdeck.providers.cli_subprocess.time.monotonic",
        lambda: next(monotonic_values),
    )

    def fake_run(command, **kwargs):
        timeouts.append(kwargs["timeout"])
        if provider_class is CodexCliProvider:
            _schema_path, result_path = _output_paths(command)
            result_path.write_text(
                "not-json" if len(timeouts) == 1 else json.dumps(_valid_plan()),
                encoding="utf-8",
            )
        else:
            payload = _claude_envelope() if len(timeouts) == 2 else {"type": "bad"}
            kwargs["stdout"].write(json.dumps(payload).encode("utf-8"))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)
    result = provider_class().plan_result(request)
    assert result.leader_generation["attempt_count"] == 2
    assert timeouts == [10.0, 6.0]
    assert max(timeouts) <= 10.0


def test_native_regeneration_skips_second_attempt_when_deadline_is_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = replace(_request(tmp_path), timeout_seconds=5)
    calls = 0
    monotonic_values = iter((10.0, 10.0, 10.0, 15.0))
    monkeypatch.setattr(
        "agentdeck.providers.cli_subprocess.time.monotonic",
        lambda: next(monotonic_values),
    )

    def fake_run(command, **_kwargs):
        nonlocal calls
        calls += 1
        _schema_path, result_path = _output_paths(command)
        result_path.write_text("not-json", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)
    with pytest.raises(CliLeaderProviderError) as raised:
        CodexCliProvider().plan_result(request)
    assert calls == 1
    assert raised.value.stage == "timeout"
    assert raised.value.attempt_count == 1


@pytest.mark.parametrize(
    "value",
    [True, False, 0, -1, float("nan"), float("inf"), 10**1000, "5"],
)
def test_native_planning_rejects_invalid_total_budget_without_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    request = replace(_request(tmp_path), timeout_seconds=value)
    monkeypatch.setattr(
        "agentdeck.providers.cli_subprocess.subprocess.run",
        lambda *_args, **_kwargs: pytest.fail("invalid budget must not start subprocess"),
    )
    with pytest.raises(ValueError) as raised:
        CodexCliProvider().plan_result(request)
    assert str(raised.value) == "CLI Leader planning timeout must be a positive number"


@pytest.mark.parametrize(
    ("attempt_count", "constraint_mode"),
    [(-1, None), (3, None), (True, None), ("1", None), (0, True), (0, "secret")],
)
def test_cli_error_rejects_unsafe_attempt_metadata(
    attempt_count: object, constraint_mode: object
) -> None:
    with pytest.raises(ValueError) as raised:
        CliLeaderProviderError(
            "schema",
            "invalid_step_count",
            attempt_count=attempt_count,
            constraint_mode=constraint_mode,
        )
    assert str(raised.value) in {
        "invalid CLI Leader attempt count",
        "invalid CLI Leader constraint mode",
    }


def test_cli_error_with_attempt_count_is_new_frozen_and_redacted() -> None:
    original = CliLeaderProviderError(
        "schema",
        "invalid_step_count",
        constraint_mode="native_json_schema",
    )
    regenerated = original.with_attempt_count(2)
    assert regenerated is not original
    assert regenerated.stage == original.stage == "schema"
    assert regenerated.diagnostic_code == original.diagnostic_code == "invalid_step_count"
    assert regenerated.constraint_mode == original.constraint_mode == "native_json_schema"
    assert regenerated.attempt_count == 2
    assert regenerated.__cause__ is None
    assert regenerated.__context__ is None
    with pytest.raises(AttributeError):
        regenerated.stage = "SECRET_RAW_STAGE"
    _assert_exception_graph_redacted(regenerated, ("SECRET_RAW_STAGE",))


def test_cli_error_without_retry_is_new_sanitized_and_preserves_diagnostic() -> None:
    original = CliLeaderProviderError(
        "schema",
        "invalid_step_count",
        attempt_count=1,
        constraint_mode="native_json_schema",
        retryable=True,
    )
    terminal = original.without_retry()
    assert terminal is not original
    assert terminal.stage == original.stage == "schema"
    assert terminal.diagnostic_code == original.diagnostic_code == "invalid_step_count"
    assert terminal.attempt_count == original.attempt_count == 1
    assert terminal.constraint_mode == original.constraint_mode == "native_json_schema"
    assert original.retryable is True
    assert terminal.retryable is False
    assert terminal.__cause__ is None
    assert terminal.__context__ is None


@pytest.mark.parametrize("code", ["native_schema_unavailable", "authority_invalid"])
def test_native_local_schema_capability_errors_are_not_regenerated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    request = _request(tmp_path)
    calls = 0

    def fail_attempt(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise CliLeaderProviderError("schema", code)

    monkeypatch.setattr(CodexCliProvider, "_native_attempt", fail_attempt)
    with pytest.raises(CliLeaderProviderError) as raised:
        CodexCliProvider().plan_result(request)
    assert calls == 1
    assert raised.value.diagnostic_code == code
    assert raised.value.attempt_count == 1


@pytest.mark.parametrize(
    ("stage", "code"),
    [
        ("json_parse", None),
        ("schema", None),
        ("schema", "authority_invalid"),
        ("schema", "native_schema_unavailable"),
        ("timeout", None),
        ("nonzero", None),
        ("oversize", None),
        ("cancelled", None),
    ],
)
def test_cli_error_rejects_retryable_local_or_nonsemantic_combinations(
    stage: str, code: str | None
) -> None:
    with pytest.raises(ValueError) as raised:
        CliLeaderProviderError(stage, code, retryable=True)
    assert str(raised.value) == "invalid CLI Leader retryable combination"


@pytest.mark.parametrize(
    ("stage", "code"),
    [
        ("json_parse", "invalid_output_envelope"),
        ("schema", "missing_required_field"),
        ("schema", "invalid_top_level_type"),
        ("schema", "invalid_string_field"),
        ("schema", "invalid_step_type"),
        ("schema", "invalid_step_count"),
        ("schema", "invalid_step_numbering"),
        ("schema", "unknown_agent"),
        ("schema", "role_mismatch"),
        ("schema", "approval_not_required"),
    ],
)
def test_cli_error_accepts_only_provider_output_retryable_combinations(
    stage: str, code: str
) -> None:
    error = CliLeaderProviderError(stage, code, retryable=True)
    copied = error.with_attempt_count(2)
    assert error.retryable is True
    assert copied.retryable is True
    assert copied.attempt_count == 2


@pytest.mark.parametrize("retryable", [0, 1, None, "true", [], object()])
def test_cli_error_retryable_requires_exact_bool(retryable: object) -> None:
    with pytest.raises(ValueError) as raised:
        CliLeaderProviderError(
            "json_parse",
            "invalid_output_envelope",
            retryable=retryable,
        )
    assert str(raised.value) == "invalid CLI Leader retryable flag"


@pytest.mark.parametrize(
    ("selected_agent_ids", "step_count"),
    [
        (("planner", "reviewer"), None),
        (None, 4),
        (("planner", "unknown"), 4),
        (("planner", "reviewer"), True),
        (("planner", "reviewer"), 1),
        (("planner", "reviewer"), 65),
    ],
)
def test_native_schema_authority_failures_are_safe_before_any_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selected_agent_ids: tuple[str, ...] | None,
    step_count: int | None,
) -> None:
    request = replace(
        _request(tmp_path),
        selected_agent_ids=selected_agent_ids,
        step_count=step_count,
    )
    monkeypatch.setattr(
        "agentdeck.providers.cli_subprocess.subprocess.run",
        lambda *_args, **_kwargs: pytest.fail("invalid authority must not start subprocess"),
    )
    with pytest.raises(CliLeaderProviderError) as raised:
        CodexCliProvider().plan_result(request)
    error = raised.value
    assert error.stage == "schema"
    assert error.diagnostic_code == "authority_invalid"
    assert error.attempt_count == 0
    assert error.constraint_mode == "native_json_schema"
    assert error.retryable is False
    _assert_exception_graph_redacted(error, ("unknown", str(tmp_path)))


def test_native_deadline_addition_must_remain_finite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = replace(_request(tmp_path), timeout_seconds=1e308)
    provider = CodexCliProvider()
    provider.timeout = 1e308
    monkeypatch.setattr(
        "agentdeck.providers.cli_subprocess.time.monotonic", lambda: 1e308
    )
    monkeypatch.setattr(
        "agentdeck.providers.cli_subprocess.subprocess.run",
        lambda *_args, **_kwargs: pytest.fail("infinite deadline must not start subprocess"),
    )
    with pytest.raises(ValueError) as raised:
        provider.plan_result(request)
    assert str(raised.value) == "CLI Leader planning timeout must be a positive number"


def _write_native_success(provider_class, command, kwargs) -> None:
    if provider_class is CodexCliProvider:
        _schema_path, result_path = _output_paths(command)
        result_path.write_text(json.dumps(_valid_plan()), encoding="utf-8")
    else:
        kwargs["stdout"].write(json.dumps(_claude_envelope()).encode("utf-8"))


@pytest.mark.parametrize("provider_class", [CodexCliProvider, ClaudeCliProvider])
def test_native_setup_time_is_removed_from_subprocess_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider_class
) -> None:
    request = replace(_request(tmp_path), timeout_seconds=10)
    monotonic_values = iter((100.0, 100.0, 103.0, 104.0))
    observed_timeouts: list[float] = []
    monkeypatch.setattr(
        "agentdeck.providers.cli_subprocess.time.monotonic",
        lambda: next(monotonic_values),
    )

    def fake_run(command, **kwargs):
        observed_timeouts.append(kwargs["timeout"])
        _write_native_success(provider_class, command, kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)
    result = provider_class().plan_result(request)
    assert result.leader_generation["attempt_count"] == 1
    assert observed_timeouts == [7.0]


@pytest.mark.parametrize("provider_class", [CodexCliProvider, ClaudeCliProvider])
def test_native_setup_deadline_exhaustion_stops_before_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider_class
) -> None:
    request = replace(_request(tmp_path), timeout_seconds=10)
    monotonic_values = iter((100.0, 100.0, 110.0))
    monkeypatch.setattr(
        "agentdeck.providers.cli_subprocess.time.monotonic",
        lambda: next(monotonic_values),
    )
    subprocess_calls = 0

    def fake_run(*_args, **_kwargs):
        nonlocal subprocess_calls
        subprocess_calls += 1
        pytest.fail("expired setup must not launch subprocess")

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)
    with pytest.raises(CliLeaderProviderError) as raised:
        provider_class().plan_result(request)
    assert subprocess_calls == 0
    assert raised.value.stage == "timeout"
    assert raised.value.attempt_count == 1
    assert raised.value.retryable is False


@pytest.mark.parametrize("provider_class", [CodexCliProvider, ClaudeCliProvider])
def test_native_late_success_is_rejected_after_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider_class
) -> None:
    request = replace(_request(tmp_path), timeout_seconds=10)
    monotonic_values = iter((100.0, 100.0, 100.0, 110.0))
    subprocess_calls = 0
    monkeypatch.setattr(
        "agentdeck.providers.cli_subprocess.time.monotonic",
        lambda: next(monotonic_values),
    )

    def fake_run(command, **kwargs):
        nonlocal subprocess_calls
        subprocess_calls += 1
        _write_native_success(provider_class, command, kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)
    with pytest.raises(CliLeaderProviderError) as raised:
        provider_class().plan_result(request)
    assert subprocess_calls == 1
    assert raised.value.stage == "timeout"
    assert raised.value.attempt_count == 1
    assert raised.value.retryable is False


@pytest.mark.parametrize("provider_class", [CodexCliProvider, ClaudeCliProvider])
def test_native_retry_deadline_exhaustion_stops_before_second_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider_class
) -> None:
    request = replace(_request(tmp_path), timeout_seconds=10)
    monotonic_values = iter((100.0, 100.0, 100.0, 110.0))
    subprocess_calls = 0
    monkeypatch.setattr(
        "agentdeck.providers.cli_subprocess.time.monotonic",
        lambda: next(monotonic_values),
    )

    def fake_run(command, **kwargs):
        nonlocal subprocess_calls
        subprocess_calls += 1
        if provider_class is CodexCliProvider:
            _schema_path, result_path = _output_paths(command)
            result_path.write_text("not-json", encoding="utf-8")
        else:
            kwargs["stdout"].write(b"not-json")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)
    with pytest.raises(CliLeaderProviderError) as raised:
        provider_class().plan_result(request)
    assert subprocess_calls == 1
    assert raised.value.stage == "timeout"
    assert raised.value.attempt_count == 1
    assert raised.value.retryable is False


@pytest.mark.parametrize(
    ("outcomes", "expected_calls", "expected_stage"),
    [
        (("json_parse", "success"), 2, None),
        (("schema", "success"), 2, None),
        (("json_parse", "json_parse"), 2, "json_parse"),
        (("schema", "schema"), 2, "schema"),
        (("nonzero",), 1, "nonzero"),
    ],
)
def test_claude_regeneration_matrix_preserves_exact_native_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcomes: tuple[str, ...],
    expected_calls: int,
    expected_stage: str | None,
) -> None:
    request = _request(tmp_path)
    raw_secret = "SECRET_FIRST_CLAUDE_ENVELOPE"
    calls: list[tuple[list[str], str, str]] = []

    def fake_run(command, **kwargs):
        outcome = outcomes[len(calls)]
        calls.append((list(command), kwargs["input"], kwargs["stdout"].path))
        if outcome == "nonzero":
            return subprocess.CompletedProcess(command, 9, stdout=raw_secret, stderr=raw_secret)
        if outcome == "json_parse":
            kwargs["stdout"].write(raw_secret.encode("utf-8"))
        elif outcome == "schema":
            invalid = _valid_plan()
            invalid["steps"].append(
                {
                    "step": 5,
                    "agent_id": "planner",
                    "role": "planning",
                    "task": "extra step beyond authority",
                    "risk": "needs review",
                    "requires_approval": True,
                }
            )
            kwargs["stdout"].write(
                json.dumps(_claude_envelope(invalid)).encode("utf-8")
            )
        else:
            kwargs["stdout"].write(json.dumps(_claude_envelope()).encode("utf-8"))
        return subprocess.CompletedProcess(command, 0, stdout=raw_secret, stderr=raw_secret)

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)
    if expected_stage is None:
        result = ClaudeCliProvider().plan_result(request)
        assert result.leader_generation["attempt_count"] == 2
        assert result.leader_generation["regeneration_used"] is True
    else:
        with pytest.raises(CliLeaderProviderError) as raised:
            ClaudeCliProvider().plan_result(request)
        assert raised.value.stage == expected_stage
        assert raised.value.attempt_count == expected_calls
        _assert_exception_graph_redacted(raised.value, (raw_secret, str(tmp_path)))

    assert len(calls) == expected_calls
    assert len(calls) <= 2
    schema_argument = json.dumps(
        build_leader_plan_schema(request),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    for command, _prompt, sink_path in calls:
        assert command[0] == "claude"
        assert command[command.index("--model") + 1] == request.model
        assert "--no-session-persistence" in command
        assert command[command.index("--json-schema") + 1] == schema_argument
        assert not Path(sink_path).parent.exists()
    if len(calls) == 2:
        assert calls[0][0] == calls[1][0]
        assert calls[0][2] != calls[1][2]
        assert request.task in calls[1][1]
        assert "Regenerate the complete plan" in calls[1][1]
        assert raw_secret not in calls[1][1]
