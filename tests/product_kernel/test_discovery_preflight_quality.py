from __future__ import annotations

import hashlib
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

import pytest

import agentdeck.adapters.discovery as discovery_adapter
from agentdeck.adapters.discovery import ReadinessState, discover_tools
from agentdeck.adapters.discovery_process import (
    MAX_TMUX_METADATA_BYTES,
    ResolvedExecutable,
    VERSION_TIMEOUT_SECONDS,
    bounded_tmux_version_reader,
    executable_signature,
)


_TMUX_OUTPUT_MARKER = b"\x002c:CDdf:hlL:NqS:T:uUvV\x00256\x00:,\x00tmux %s\n\x00"


def write_tmux_fixture(path: Path, *versions: bytes, marker: bool = True) -> None:
    path.write_bytes(
        b"\xcf\xfa\xed\xfe@rpath/libevent.dylib\0@loader_path/../lib/\0"
        + b"\0".join(versions)
        + (b"\0" + _TMUX_OUTPUT_MARKER if marker else b"\0")
    )
    path.chmod(0o700)


def wait_for_pid_files(*paths: Path) -> tuple[int, ...]:
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        values = tuple(
            path.read_text(encoding="ascii") if path.exists() else "" for path in paths
        )
        if all(value.strip().isdigit() for value in values):
            return tuple(int(value) for value in values)
        time.sleep(0.01)
    raise AssertionError("probe pids were not reported")


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def assert_processes_gone(*pids: int) -> None:
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if not any(process_exists(pid) for pid in pids):
            return
        time.sleep(0.01)
    raise AssertionError("version probe process group survived setup failure")


def test_default_sealed_version_child_timeout_is_five_seconds() -> None:
    assert VERSION_TIMEOUT_SECONDS == 5.0


def test_loader_relative_tmux_uses_identity_bound_metadata_without_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmux = tmp_path / "tmux"
    write_tmux_fixture(tmux, b"3.7")

    def forbidden_exec(*args: object, **kwargs: object) -> object:
        raise AssertionError("loader-relative tmux must not execute")

    monkeypatch.setattr(
        "agentdeck.adapters.discovery_process.subprocess.Popen", forbidden_exec
    )

    fact = discover_tools(path=str(tmp_path), tools={"tmux": "tmux"})["tmux"]

    assert fact.version == "tmux 3.7"
    assert fact.readiness is ReadinessState.DISCOVERED
    assert fact.diagnostics == ()


@pytest.mark.parametrize(
    ("versions", "marker"),
    [
        ((b"3.7", b"9.9"), True),
        ((b"3.7", b"3.7"), True),
        ((), True),
        ((b"3.7",), False),
    ],
)
def test_tmux_static_metadata_rejects_ambiguous_or_missing_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    versions: tuple[bytes, ...],
    marker: bool,
) -> None:
    tmux = tmp_path / "tmux"
    write_tmux_fixture(tmux, *versions, marker=marker)
    monkeypatch.setattr(
        "agentdeck.adapters.discovery_process.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("tmux metadata failure must not execute"),
    )

    fact = discover_tools(path=str(tmp_path), tools={"tmux": "tmux"})["tmux"]

    assert fact.version is None
    assert fact.diagnostics == ("version_probe_invalid",)


def test_tmux_static_metadata_rejects_oversize_without_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmux = tmp_path / "tmux"
    tmux.touch()
    tmux.chmod(0o700)
    tmux.write_bytes(b"tmux")
    os.truncate(tmux, MAX_TMUX_METADATA_BYTES + 1)
    monkeypatch.setattr(
        "agentdeck.adapters.discovery_process.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("oversize tmux must not execute"),
    )

    fact = discover_tools(path=str(tmp_path), tools={"tmux": "tmux"})["tmux"]

    assert fact.version is None
    assert fact.diagnostics == ("version_probe_oversize",)


@pytest.mark.skipif(sys.platform != "darwin", reason="conda tmux regression is macOS")
def test_available_conda_tmux_static_metadata_without_exec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmux = Path(sys.prefix) / "bin" / "tmux"
    if not tmux.is_file():
        pytest.skip("conda tmux is unavailable")
    monkeypatch.setattr(
        "agentdeck.adapters.discovery_process.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("real conda tmux must not execute"),
    )

    fact = discover_tools(path=str(tmux.parent), tools={"tmux": "tmux"})["tmux"]

    assert fact.resolved_path == str(tmux.resolve())
    assert fact.version is not None and fact.version.startswith("tmux ")
    assert fact.diagnostics == ()


def test_tmux_static_metadata_rechecks_descriptor_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmux = tmp_path / "tmux"
    write_tmux_fixture(tmux, b"3.7")
    descriptor = os.open(tmux, os.O_RDONLY)
    signature = executable_signature(os.fstat(descriptor))
    executable = ResolvedExecutable(
        str(tmux), signature, descriptor, hashlib.sha256(tmux.read_bytes()).digest()
    )
    checks = 0

    def changing_signature(
        details: os.stat_result,
    ) -> tuple[int, int, int, int, int, int]:
        nonlocal checks
        checks += 1
        if checks == 1:
            return signature
        return (*signature[:-1], signature[-1] + 1)

    monkeypatch.setattr(
        "agentdeck.adapters.discovery_process.executable_signature", changing_signature
    )
    try:
        with pytest.raises(RuntimeError, match="metadata validation failed"):
            bounded_tmux_version_reader(executable)
    finally:
        os.close(descriptor)

    assert checks == 2


@pytest.mark.parametrize("failure_point", ["constructor", "first", "second"])
def test_selector_setup_failure_reaps_parent_and_descendant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_point: str
) -> None:
    parent_file = tmp_path / "parent.pid"
    child_file = tmp_path / "child.pid"
    codex = tmp_path / "codex"
    codex.write_text(
        "#!/bin/sh\n"
        f"printf '%s' \"$$\" > '{parent_file}'\n"
        "/bin/sleep 30 &\n"
        f"printf '%s' \"$!\" > '{child_file}'\n"
        "wait\n",
        encoding="utf-8",
    )
    codex.chmod(0o700)
    real_selector = selectors.DefaultSelector
    real_popen = subprocess.Popen
    created_selectors: list[FailingSelector] = []
    processes: list[object] = []

    class FailingSelector:
        def __init__(self) -> None:
            self.inner = real_selector()
            self.registrations = 0
            self.closed = False

        def register(self, *args: object, **kwargs: object) -> object:
            self.registrations += 1
            if failure_point == "first" and self.registrations == 1:
                wait_for_pid_files(parent_file, child_file)
                raise OSError("selector setup failed")
            if failure_point == "second" and self.registrations == 2:
                wait_for_pid_files(parent_file, child_file)
                raise OSError("selector setup failed")
            return self.inner.register(*args, **kwargs)

        def close(self) -> None:
            self.closed = True
            self.inner.close()

    def selector_factory() -> FailingSelector:
        if failure_point == "constructor":
            wait_for_pid_files(parent_file, child_file)
            raise OSError("selector setup failed")
        selector = FailingSelector()
        created_selectors.append(selector)
        return selector

    def tracking_popen(*args: object, **kwargs: object) -> object:
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(
        "agentdeck.adapters.discovery_process.selectors.DefaultSelector",
        selector_factory,
    )
    monkeypatch.setattr(
        "agentdeck.adapters.discovery_process.subprocess.Popen",
        tracking_popen,
    )

    fact = discover_tools(path=str(tmp_path), tools={"codex": "codex"})["codex"]
    parent_pid, child_pid = wait_for_pid_files(parent_file, child_file)
    try:
        assert fact.version is None
        assert fact.diagnostics == ("version_probe_failed",)
        assert_processes_gone(parent_pid, child_pid)
        assert len(processes) == 1
        process = processes[0]
        assert process.stdout.closed  # type: ignore[union-attr]
        assert process.stderr.closed  # type: ignore[union-attr]
        if failure_point != "constructor":
            assert created_selectors[0].closed
    finally:
        if process_exists(parent_pid):
            os.killpg(parent_pid, signal.SIGKILL)
        if process_exists(child_pid):
            os.kill(child_pid, signal.SIGKILL)


@pytest.mark.parametrize("name", ["codex", "tmux"])
def test_initial_digest_rejects_same_signature_rewrite_before_probe_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    executable = tmp_path / name
    if name == "tmux":
        write_tmux_fixture(executable, b"3.7")
        changed = executable.read_bytes().replace(b"3.7", b"9.9")
    else:
        executable.write_bytes(b"#!/bin/sh\nprintf 'codex-cli 1.2.3\\n'\n")
        executable.chmod(0o700)
        changed = executable.read_bytes().replace(b"1.2.3", b"9.9.9")
    original_stat = executable.stat()
    real_output = discovery_adapter._default_version_output

    # Hold every visible signature field stable to isolate the initial digest gate.
    def stable_signature(details: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            details.st_dev,
            details.st_ino,
            details.st_mode,
            details.st_size,
            details.st_mtime_ns,
        )

    def rewrite_then_probe(*args: object, **kwargs: object) -> bytes:
        executable.write_bytes(changed)
        os.utime(
            executable,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        return real_output(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(discovery_adapter, "executable_signature", stable_signature)
    monkeypatch.setattr(
        "agentdeck.adapters.discovery_process.executable_signature", stable_signature
    )
    monkeypatch.setattr(discovery_adapter, "_default_version_output", rewrite_then_probe)

    fact = discover_tools(path=str(tmp_path), tools={name: name})[name]

    assert fact.version is None
    assert fact.readiness is ReadinessState.DISCOVERED
    assert fact.diagnostics == ("version_probe_failed",)


def test_executable_signature_includes_ctime(tmp_path: Path) -> None:
    executable = tmp_path / "tool"
    executable.write_bytes(b"tool")

    signature = executable_signature(executable.stat())

    assert signature[-1] == executable.stat().st_ctime_ns
    assert len(signature) == 6
