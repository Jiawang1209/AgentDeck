from __future__ import annotations

from collections.abc import Iterator, Mapping
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

import pytest

from agentdeck.adapters.discovery import ReadinessState, discover_tools
from agentdeck.adapters.discovery_process import (
    MAX_EXECUTABLE_BYTES,
    ResolvedExecutable,
    VersionProbeOversize,
    _copy_execution_snapshot,
)


class HostileMapping(Mapping[object, object]):
    def __getitem__(self, key: object) -> object:
        raise KeyError(key)

    def __iter__(self) -> Iterator[object]:
        return iter(())

    def __len__(self) -> int:
        return 0

    def items(self) -> object:
        raise RuntimeError("token=hostile-discovery-marker")


class DuplicateMapping(HostileMapping):
    def __init__(self, value: object) -> None:
        self.value = value

    def items(self) -> object:
        return iter((("codex", self.value), ("codex", self.value)))


def make_script(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o700)


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_for_pid(path: Path, *, timeout: float = 1.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = path.read_text(encoding="ascii")
        except FileNotFoundError:
            time.sleep(0.01)
            continue
        if value.strip().isdigit():
            return int(value)
        time.sleep(0.01)
    raise AssertionError("child pid was not reported")


def assert_process_gone(pid: int, *, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_exists(pid):
            return
        time.sleep(0.01)
    raise AssertionError("version probe descendant survived cleanup")


def cleanup_pid(pid: int) -> None:
    if process_exists(pid):
        os.kill(pid, signal.SIGKILL)


def discovery_path(bin_dir: Path) -> str:
    return os.pathsep.join((str(bin_dir), "/bin", "/usr/bin"))


def test_default_runner_cleans_detached_stdio_descendant_after_success(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "detached.pid"
    make_script(
        tmp_path / "codex",
        f"/bin/sleep 30 >/dev/null 2>&1 &\n"
        f"printf '%s' \"$!\" > '{pid_file}'\n"
        "printf 'codex-cli 1.2.3\\n'",
    )

    facts = discover_tools(path=discovery_path(tmp_path), tools={"codex": "codex"})
    pid = wait_for_pid(pid_file)

    try:
        assert facts["codex"].version == "codex-cli 1.2.3"
        assert facts["codex"].readiness is ReadinessState.DISCOVERED
        assert_process_gone(pid)
    finally:
        cleanup_pid(pid)


def test_default_runner_cleans_pipe_inheriting_descendant_without_full_timeout(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "inherited.pid"
    make_script(
        tmp_path / "codex",
        f"/bin/sleep 30 &\nprintf '%s' \"$!\" > '{pid_file}'\n"
        "printf 'codex-cli 1.2.3\\n'",
    )
    started = time.monotonic()

    facts = discover_tools(path=discovery_path(tmp_path), tools={"codex": "codex"})
    elapsed = time.monotonic() - started
    pid = wait_for_pid(pid_file)

    try:
        assert elapsed < 1.0
        assert facts["codex"].version == "codex-cli 1.2.3"
        assert facts["codex"].diagnostics == ()
        assert_process_gone(pid)
    finally:
        cleanup_pid(pid)


@pytest.mark.parametrize(
    ("body", "diagnostic"),
    [
        ("/bin/sleep 30", "version_probe_timeout"),
        ("while :; do printf 'xxxxxxxxxxxxxxxx'; done", "version_probe_oversize"),
        ("printf 'token=raw-secret\\n' >&2\nexit 9", "version_probe_failed"),
    ],
)
def test_default_runner_failures_remain_bounded_and_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, diagnostic: str
) -> None:
    if diagnostic == "version_probe_timeout":
        monkeypatch.setattr("agentdeck.adapters.discovery_process.VERSION_TIMEOUT_SECONDS", 0.1)
    make_script(tmp_path / "codex", body)
    started = time.monotonic()

    facts = discover_tools(path=discovery_path(tmp_path), tools={"codex": "codex"})

    assert time.monotonic() - started < 1.0
    assert facts["codex"].version is None
    assert facts["codex"].diagnostics == (diagnostic,)
    assert "raw-secret" not in repr(facts["codex"])


def test_default_runner_does_not_leak_file_descriptors_across_repeated_runs(
    tmp_path: Path,
) -> None:
    make_script(tmp_path / "codex", "printf 'codex-cli 1.2.3\\n'")
    fd_root = Path("/dev/fd") if Path("/dev/fd").is_dir() else Path("/proc/self/fd")
    before = len(tuple(fd_root.iterdir()))

    for _ in range(30):
        facts = discover_tools(path=discovery_path(tmp_path), tools={"codex": "codex"})
        assert facts["codex"].version == "codex-cli 1.2.3"

    after = len(tuple(fd_root.iterdir()))
    assert after <= before + 1


@pytest.mark.parametrize("unsafe_path", [":missing", ".", "relative-bin"])
def test_unsafe_path_components_never_discover_project_cwd_impostors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_path: str,
) -> None:
    local_bin = tmp_path if unsafe_path != "relative-bin" else tmp_path / "relative-bin"
    local_bin.mkdir(exist_ok=True)
    make_script(local_bin / "codex", "printf 'codex-cli 9.9.9\\n'")
    monkeypatch.chdir(tmp_path)
    calls: list[str] = []

    def forbidden_probe(path: str) -> bool:
        calls.append(path)
        return True

    facts = discover_tools(
        path=unsafe_path,
        version_runner=lambda path: forbidden_probe(path),
        auth_probes={"codex": forbidden_probe},
        acp_probes={"codex": forbidden_probe},
        tools={"codex": "codex"},
    )

    assert facts["codex"].readiness is ReadinessState.MISSING
    assert facts["codex"].resolved_path is None
    assert calls == []


def test_safe_absolute_path_wins_after_unsafe_components_are_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    safe_bin = tmp_path / "safe-bin"
    safe_bin.mkdir()
    make_script(tmp_path / "codex", "printf 'codex-cli 9.9.9\\n'")
    make_script(safe_bin / "codex", "printf 'codex-cli 1.2.3\\n'")
    monkeypatch.chdir(tmp_path)
    calls: list[str] = []

    facts = discover_tools(
        path=os.pathsep.join((".", str(safe_bin))),
        version_runner=lambda path: calls.append(path) or b"codex-cli 1.2.3",
        tools={"codex": "codex"},
    )

    assert facts["codex"].resolved_path == str((safe_bin / "codex").resolve())
    assert calls == [str((safe_bin / "codex").resolve())]


def test_default_runner_rejects_path_swap_even_when_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = tmp_path / "codex"
    safe_backup = tmp_path / "safe-backup"
    impostor = tmp_path / "impostor"
    displaced_impostor = tmp_path / "displaced-impostor"
    make_script(original, "printf 'codex-cli 1.2.3\\n'")
    make_script(impostor, "printf 'codex-cli 9.9.9\\n'")
    real_popen = subprocess.Popen

    def swapping_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        original.rename(safe_backup)
        impostor.rename(original)
        try:
            process = real_popen(*args, **kwargs)  # type: ignore[arg-type]
            process.wait(timeout=1.0)
            return process
        finally:
            original.rename(displaced_impostor)
            safe_backup.rename(original)

    monkeypatch.setattr("agentdeck.adapters.discovery.subprocess.Popen", swapping_popen)

    facts = discover_tools(path=str(tmp_path), tools={"codex": "codex"})

    assert facts["codex"].version is None
    assert facts["codex"].resolved_path is None
    assert facts["codex"].readiness is ReadinessState.MISSING
    assert facts["codex"].diagnostics == ("resolved_path_changed",)


def test_default_identity_bound_runner_supports_native_executables(tmp_path: Path) -> None:
    native_true = Path("/usr/bin/true")
    if not native_true.is_file():
        pytest.skip("native true is unavailable")
    (tmp_path / "true").symlink_to(native_true)

    facts = discover_tools(path=str(tmp_path), tools={"true": "true"})

    assert facts["true"].resolved_path == str(native_true.resolve())
    assert facts["true"].version is None
    assert facts["true"].diagnostics == ("version_probe_empty",)


def test_default_runner_leaves_project_and_system_temp_unchanged(tmp_path: Path) -> None:
    make_script(tmp_path / "codex", "printf 'codex-cli 1.2.3\\n'")
    project_before = tuple(
        (path.relative_to(tmp_path), path.read_bytes())
        for path in sorted(tmp_path.rglob("*"))
        if path.is_file()
    )
    temp_root = Path(tempfile.gettempdir())
    temp_before = set(temp_root.glob("agentdeck-version-*"))

    facts = discover_tools(path=str(tmp_path), tools={"codex": "codex"})

    project_after = tuple(
        (path.relative_to(tmp_path), path.read_bytes())
        for path in sorted(tmp_path.rglob("*"))
        if path.is_file()
    )
    assert facts["codex"].version == "codex-cli 1.2.3"
    assert project_after == project_before
    assert set(temp_root.glob("agentdeck-version-*")) == temp_before


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"codex-cli 1.2.3", "codex-cli 1.2.3"),
        (b"tmux 3.5a", "tmux 3.5a"),
        (b"claude-agent-acp 0.58.1", "claude-agent-acp 0.58.1"),
        (b"2.1.37 (Claude Code)", "2.1.37"),
        (b"secret-tool 1.2.3", "secret-tool 1.2.3"),
        (b"codex 1.0 bearer sk-proj-private", "codex 1.0"),
        (b"codex-cli 1.2.3 /private/project token=raw", "codex-cli 1.2.3"),
        (b"codex-cli 1.2.3\n/path/to/tool\ntoken=raw", "codex-cli 1.2.3"),
    ],
)
def test_version_probe_retains_only_narrow_public_prefix(
    tmp_path: Path, raw: bytes, expected: str
) -> None:
    make_script(tmp_path / "codex", "printf 'unused\\n'")

    fact = discover_tools(
        path=str(tmp_path),
        version_runner=lambda _: raw,
        tools={"codex": "codex"},
    )["codex"]

    assert fact.version == expected
    assert fact.diagnostics == ()


@pytest.mark.parametrize("raw", [b"ready authenticated", b" codex 1.2.3", b"v1"])
def test_unrecognized_version_output_returns_only_fixed_diagnostic(
    tmp_path: Path, raw: bytes
) -> None:
    make_script(tmp_path / "codex", "printf 'unused\\n'")

    fact = discover_tools(
        path=str(tmp_path),
        version_runner=lambda _: raw,
        tools={"codex": "codex"},
    )["codex"]

    assert fact.version is None
    assert fact.diagnostics == ("version_probe_invalid",)
    assert raw.decode("ascii") not in repr(fact)


@pytest.mark.parametrize(
    "field", ["tools", "auth_probes", "acp_probes", "capability_metadata"]
)
def test_all_discovery_mappings_redact_hostile_items_before_probes(
    tmp_path: Path, field: str
) -> None:
    make_script(tmp_path / "codex", "printf 'codex-cli 1.2.3\\n'")
    calls: list[str] = []
    arguments: dict[str, object] = {
        "path": str(tmp_path),
        "version_runner": lambda path: calls.append(path) or b"codex-cli 1.2.3",
        "tools": {"codex": "codex"},
    }
    arguments[field] = HostileMapping()

    with pytest.raises(TypeError) as error:
        discover_tools(**arguments)  # type: ignore[arg-type]

    assert "hostile" not in str(error.value)
    assert "token" not in str(error.value)
    assert calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tools", "codex"),
        ("auth_probes", lambda _: True),
        ("acp_probes", lambda _: True),
        ("capability_metadata", ("leader",)),
    ],
)
def test_all_discovery_mappings_reject_duplicate_keys_before_probes(
    tmp_path: Path, field: str, value: object
) -> None:
    make_script(tmp_path / "codex", "printf 'codex-cli 1.2.3\\n'")
    calls: list[str] = []
    arguments: dict[str, object] = {
        "path": str(tmp_path),
        "version_runner": lambda path: calls.append(path) or b"codex-cli 1.2.3",
        "tools": {"codex": "codex"},
    }
    arguments[field] = DuplicateMapping(value)

    with pytest.raises(ValueError, match="contains duplicate keys"):
        discover_tools(**arguments)  # type: ignore[arg-type]

    assert calls == []


@pytest.mark.parametrize(
    ("name", "expected_argument"),
    [
        ("codex", "--version"),
        ("claude", "--version"),
        ("custom", "--version"),
    ],
)
def test_default_runner_uses_exact_per_tool_version_argument(
    tmp_path: Path, name: str, expected_argument: str
) -> None:
    make_script(
        tmp_path / name,
        f"if [ \"$1\" = '{expected_argument}' ]; then\n"
        f"  printf '{name} 1.2.3\\n'\n"
        "else\n  /bin/sleep 30\nfi",
    )
    started = time.monotonic()

    fact = discover_tools(path=str(tmp_path), tools={name: name})[name]

    assert time.monotonic() - started < 1.0
    assert fact.version == f"{name} 1.2.3"
    assert fact.diagnostics == ()


def test_snapshot_limit_covers_installed_claude_and_rejects_above_bound(
    tmp_path: Path,
) -> None:
    assert MAX_EXECUTABLE_BYTES == 512 * 1024 * 1024
    assert MAX_EXECUTABLE_BYTES > 242_445_680
    descriptor = os.open("/dev/null", os.O_RDONLY)
    executable = ResolvedExecutable(
        path="/dev/null",
        signature=(0, 0, 0, MAX_EXECUTABLE_BYTES + 1, 0, 0),
        descriptor=descriptor,
    )
    try:
        with pytest.raises(VersionProbeOversize):
            _copy_execution_snapshot(executable, tmp_path)
    finally:
        os.close(descriptor)


def test_default_runner_ignores_project_controlled_temp_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_script(tmp_path / "codex", "printf 'codex-cli 1.2.3\\n'")
    for name in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(name, str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", None)
    real_temporary_directory = tempfile.TemporaryDirectory
    created: list[Path] = []

    def tracking_temporary_directory(*args: object, **kwargs: object) -> object:
        instance = real_temporary_directory(*args, **kwargs)  # type: ignore[arg-type]
        created.append(Path(instance.name))
        return instance

    monkeypatch.setattr(
        "agentdeck.adapters.discovery_process.tempfile.TemporaryDirectory",
        tracking_temporary_directory,
    )
    project_before = set(tmp_path.iterdir())

    fact = discover_tools(path=str(tmp_path), tools={"codex": "codex"})["codex"]

    trusted_root = Path("/var/tmp").resolve(strict=True)
    assert fact.version == "codex-cli 1.2.3"
    assert created and all(path.parent.resolve() == trusted_root for path in created)
    assert set(tmp_path.iterdir()) == project_before
    assert all(not path.exists() for path in created)


def test_snapshot_digest_detects_same_inode_content_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "codex"
    make_script(executable, "printf 'codex-cli 1.2.3\\n'")
    original = executable.read_bytes()
    original_stat = executable.stat()
    real_pread = os.pread
    zero_offset_reads = 0

    def drifting_pread(descriptor: int, count: int, offset: int) -> bytes:
        nonlocal zero_offset_reads
        if offset != 0:
            return real_pread(descriptor, count, offset)
        zero_offset_reads += 1
        if zero_offset_reads != 2:
            return real_pread(descriptor, count, offset)
        executable.write_bytes(b"x" * len(original))
        os.utime(
            executable,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        drifted = real_pread(descriptor, count, offset)
        executable.write_bytes(original)
        os.utime(
            executable,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        return drifted

    monkeypatch.setattr("agentdeck.adapters.discovery_process.os.pread", drifting_pread)

    fact = discover_tools(path=str(tmp_path), tools={"codex": "codex"})["codex"]

    assert zero_offset_reads == 3
    assert fact.version is None
    assert fact.diagnostics == (
        "version_probe_failed",
        "resolved_path_changed",
    )
    assert executable.read_bytes() == original
