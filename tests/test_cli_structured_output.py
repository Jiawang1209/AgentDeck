from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import stat
import subprocess

import pytest

from agentdeck.config import load_config, write_default_config
from agentdeck.providers import LeaderPlanRequest
from agentdeck.orchestration.leader import LeaderOrchestrator
from agentdeck.providers.cli_subprocess import (
    MAX_CLI_LEADER_OUTPUT_BYTES,
    CliLeaderProviderError,
    CodexCliProvider,
)
from agentdeck.providers.plan_schema import (
    LEADER_PLAN_SCHEMA_VERSION,
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
