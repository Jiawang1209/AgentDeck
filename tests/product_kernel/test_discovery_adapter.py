from __future__ import annotations

from dataclasses import FrozenInstanceError
import os
from pathlib import Path
from types import MappingProxyType
from typing import NoReturn

import pytest

from agentdeck.adapters.discovery import ReadinessState, discover_tools


def make_executable(path: Path, output: str = "tool 1.0") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{output}'\n", encoding="utf-8")
    path.chmod(0o700)


def snapshot(path: Path) -> tuple[tuple[str, int, bytes], ...]:
    return tuple(
        (str(item.relative_to(path)), item.stat().st_mode, item.read_bytes())
        for item in sorted(path.rglob("*"))
        if item.is_file()
    )


def test_discovery_uses_given_path_without_writing_or_prompting(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    executable = bin_dir / "codex"
    make_executable(executable, "codex-cli 1.0")
    before = snapshot(tmp_path)
    calls: list[str] = []

    def fake_version_runner(resolved_path: str) -> bytes:
        calls.append(resolved_path)
        return b"codex-cli 1.0\n"

    facts = discover_tools(
        path=str(bin_dir),
        version_runner=fake_version_runner,
        tools={"codex": "codex"},
    )

    assert facts["codex"].resolved_path == str(executable.resolve())
    assert facts["codex"].version == "codex-cli 1.0"
    assert facts["codex"].readiness is ReadinessState.DISCOVERED
    assert facts["codex"].capabilities == ("manual_cli", "leader", "worker", "acp")
    assert calls == [str(executable.resolve())]
    assert snapshot(tmp_path) == before


def test_explicit_path_never_falls_back_to_process_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process_bin = tmp_path / "process-bin"
    make_executable(process_bin / "codex")
    monkeypatch.setenv("PATH", str(process_bin))

    facts = discover_tools(
        path=str(tmp_path / "explicit-empty-bin"),
        version_runner=lambda _: b"must not run",
        tools={"codex": "codex"},
    )

    assert facts["codex"].readiness is ReadinessState.MISSING
    assert facts["codex"].resolved_path is None


def test_missing_tool_does_not_run_any_probe(tmp_path: Path) -> None:
    calls: list[str] = []

    def forbidden_probe(resolved_path: str) -> bool:
        calls.append(resolved_path)
        return True

    facts = discover_tools(
        path=str(tmp_path),
        version_runner=lambda path: forbidden_probe(path),
        auth_probes={"codex": forbidden_probe},
        acp_probes={"codex": forbidden_probe},
        tools={"codex": "codex"},
    )

    assert facts["codex"].readiness is ReadinessState.MISSING
    assert calls == []


@pytest.mark.parametrize(
    ("authenticated", "acp_available", "expected"),
    [
        (False, False, ReadinessState.DISCOVERED),
        (True, False, ReadinessState.AUTHENTICATED),
        (False, True, ReadinessState.ACP_AVAILABLE),
        (True, True, ReadinessState.READY),
    ],
)
def test_readiness_combines_only_exact_passive_probe_facts(
    tmp_path: Path,
    authenticated: bool,
    acp_available: bool,
    expected: ReadinessState,
) -> None:
    make_executable(tmp_path / "codex")

    facts = discover_tools(
        path=str(tmp_path),
        version_runner=lambda _: b"codex 99 ready authenticated acp\n",
        auth_probes={"codex": lambda _: authenticated},
        acp_probes={"codex": lambda _: acp_available},
        tools={"codex": "codex"},
    )

    fact = facts["codex"]
    assert fact.authenticated is authenticated
    assert fact.acp_available is acp_available
    assert fact.readiness is expected


def test_probe_results_must_be_exact_booleans(tmp_path: Path) -> None:
    make_executable(tmp_path / "codex")

    facts = discover_tools(
        path=str(tmp_path),
        version_runner=lambda _: b"codex 1.0",
        auth_probes={"codex": lambda _: "true"},  # type: ignore[dict-item]
        acp_probes={"codex": lambda _: 1},  # type: ignore[dict-item]
        tools={"codex": "codex"},
    )

    fact = facts["codex"]
    assert fact.authenticated is False
    assert fact.acp_available is False
    assert fact.readiness is ReadinessState.DISCOVERED
    assert fact.diagnostics == ("authentication_probe_invalid", "acp_probe_invalid")


@pytest.mark.parametrize(
    ("runner", "diagnostic"),
    [
        (lambda _: (_ for _ in ()).throw(RuntimeError("token=secret")), "version_probe_failed"),
        (lambda _: (_ for _ in ()).throw(TimeoutError("token=secret")), "version_probe_timeout"),
        (lambda _: b"x" * 1025 + b"token=secret", "version_probe_oversize"),
        (lambda _: b"\xfftoken=secret", "version_probe_invalid_utf8"),
    ],
)
def test_version_failures_are_bounded_redacted_and_do_not_block_other_tools(
    tmp_path: Path, runner: object, diagnostic: str
) -> None:
    make_executable(tmp_path / "broken")
    make_executable(tmp_path / "healthy")

    def version_runner(resolved_path: str) -> bytes:
        if resolved_path.endswith("broken"):
            return runner(resolved_path)  # type: ignore[operator, no-any-return]
        return b"healthy 1.0"

    facts = discover_tools(
        path=str(tmp_path),
        version_runner=version_runner,
        tools={"broken": "broken", "healthy": "healthy"},
    )

    assert facts["broken"].version is None
    assert facts["broken"].diagnostics == (diagnostic,)
    assert "secret" not in repr(facts["broken"])
    assert len(repr(facts["broken"]).encode("utf-8")) <= 512
    assert facts["healthy"].version == "healthy 1.0"
    assert facts["healthy"].readiness is ReadinessState.DISCOVERED


def test_resolved_path_is_real_executable_and_not_runner_controlled(tmp_path: Path) -> None:
    executable = tmp_path / "real-codex"
    alias = tmp_path / "codex"
    make_executable(executable)
    alias.symlink_to(executable)

    facts = discover_tools(
        path=str(tmp_path),
        version_runner=lambda _: b"resolved_path=/secret/fake",
        tools={"codex": "codex"},
    )

    assert facts["codex"].resolved_path == str(executable.resolve())
    assert os.path.isabs(facts["codex"].resolved_path or "")
    assert os.access(facts["codex"].resolved_path or "", os.X_OK)


def test_executable_identity_drift_fails_closed_before_later_probes(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "codex"
    make_executable(executable, "original")
    passive_calls: list[str] = []

    def replacing_version_runner(resolved_path: str) -> bytes:
        Path(resolved_path).unlink()
        make_executable(Path(resolved_path), "replacement")
        return b"original 1.0"

    def passive_probe(resolved_path: str) -> bool:
        passive_calls.append(resolved_path)
        return True

    facts = discover_tools(
        path=str(tmp_path),
        version_runner=replacing_version_runner,
        auth_probes={"codex": passive_probe},
        acp_probes={"codex": passive_probe},
        tools={"codex": "codex"},
    )

    assert facts["codex"].readiness is ReadinessState.MISSING
    assert facts["codex"].resolved_path is None
    assert facts["codex"].diagnostics == ("resolved_path_changed",)
    assert passive_calls == []


def test_discovery_copies_mapping_inputs_and_returns_immutable_facts(tmp_path: Path) -> None:
    make_executable(tmp_path / "codex")
    tools = {"codex": "codex"}

    def version_runner(_: str) -> bytes:
        tools["codex"] = "rewritten"
        tools["secret"] = "secret"
        return b"codex 1.0"

    facts = discover_tools(path=str(tmp_path), version_runner=version_runner, tools=tools)

    assert isinstance(facts, MappingProxyType)
    assert tuple(facts) == ("codex",)
    with pytest.raises(TypeError):
        facts["other"] = facts["codex"]  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        facts["codex"].version = "changed"  # type: ignore[misc]


def test_auth_and_acp_probe_failures_are_redacted_and_isolated(tmp_path: Path) -> None:
    make_executable(tmp_path / "codex")
    make_executable(tmp_path / "claude")

    def secret_failure(_: str) -> NoReturn:
        raise RuntimeError("token=secret")

    facts = discover_tools(
        path=str(tmp_path),
        version_runner=lambda path: f"{Path(path).name} 1.0".encode("utf-8"),
        auth_probes={"codex": secret_failure, "claude": lambda _: True},
        acp_probes={"codex": secret_failure, "claude": lambda _: True},
        tools={"codex": "codex", "claude": "claude"},
    )

    assert facts["codex"].readiness is ReadinessState.DISCOVERED
    assert facts["codex"].diagnostics == (
        "authentication_probe_failed",
        "acp_probe_failed",
    )
    assert "secret" not in repr(facts["codex"])
    assert facts["claude"].readiness is ReadinessState.READY


def test_default_capability_metadata_covers_cli_acp_and_observer_backends(
    tmp_path: Path,
) -> None:
    facts = discover_tools(path=str(tmp_path), version_runner=lambda _: b"unused")

    assert tuple(facts) == ("codex", "claude", "tmux")
    assert facts["codex"].capabilities == ("manual_cli", "leader", "worker", "acp")
    assert facts["claude"].capabilities == ("manual_cli", "leader", "worker", "acp")
    assert facts["tmux"].capabilities == ("observer", "human_takeover")
