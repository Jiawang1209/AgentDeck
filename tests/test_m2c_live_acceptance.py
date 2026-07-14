from __future__ import annotations

import json
import asyncio
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path
import pty
import re
import resource
import select
import shutil
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any

import pytest

from agentdeck import cli as cli_module
from agentdeck.config import load_config
from agentdeck.contracts import validate_trace_contract, validate_workbench_contract
from agentdeck.daemon.client import DaemonClient
from agentdeck.daemon.lifecycle import daemon_endpoint, project_root_hash
from agentdeck.state import StateStore


LIVE = os.environ.get("AGENTDECK_M2C_LIVE") == "1"
PROBE_TIMEOUT_SECONDS = 5
PROBE_OUTPUT_LIMIT = 256 * 1024
TOOL_SPECS = (
    ("codex", "AGENTDECK_M2C_CODEX", ("exec", "--help"), ("--version",)),
    ("claude", "AGENTDECK_M2C_CLAUDE", ("--help",), ("--version",)),
    (
        "claude-agent-acp",
        "AGENTDECK_M2C_CLAUDE_ACP",
        None,
        ("--version",),
    ),
    ("tmux", "AGENTDECK_M2C_TMUX", None, ("-V",)),
)
BLOCKER_CODES = frozenset(
    {
        "codex_unavailable",
        "codex_native_schema_unavailable",
        "claude_unavailable",
        "claude_native_schema_unavailable",
        "claude_agent_acp_unavailable",
        "tmux_unavailable",
        "probe_wrote_files",
    }
)


@dataclass(frozen=True)
class _ProbeIsolation:
    home: Path
    config: Path
    cache: Path
    data: Path
    temporary: Path

    @property
    def roots(self) -> tuple[Path, ...]:
        return (self.home, self.config, self.cache, self.data, self.temporary)


def _tree_snapshot(root: Path) -> dict[str, tuple[str, int, str]]:
    snapshot: dict[str, tuple[str, int, str]] = {}
    root_metadata = root.lstat()
    snapshot["."] = (
        "directory",
        root_metadata.st_mode & 0o777,
        str(root_metadata.st_mtime_ns),
    )
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            snapshot[relative] = (
                "directory",
                metadata.st_mode & 0o777,
                str(metadata.st_mtime_ns),
            )
        elif stat.S_ISREG(metadata.st_mode):
            snapshot[relative] = (
                "file",
                metadata.st_mode & 0o777,
                __import__("hashlib").sha256(path.read_bytes()).hexdigest(),
            )
        else:
            snapshot[relative] = ("other", metadata.st_mode & 0o777, "")
    return snapshot


def _roots_snapshot(
    roots: tuple[Path, ...]
) -> dict[str, dict[str, tuple[str, int, str]]]:
    return {str(index): _tree_snapshot(root) for index, root in enumerate(roots)}


def _prepare_probe_isolation(project: Path) -> _ProbeIsolation:
    base = project.parent / "m2c-preflight-isolation"
    isolation = _ProbeIsolation(
        home=base / "home",
        config=base / "xdg-config",
        cache=base / "xdg-cache",
        data=base / "xdg-data",
        temporary=base / "tmp",
    )
    for root in isolation.roots:
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
    return isolation


def _probe_environment(
    isolation: _ProbeIsolation, executables: tuple[Path, ...]
) -> dict[str, str]:
    path_parts = list(dict.fromkeys(str(item.parent) for item in executables))
    path_parts.extend(("/usr/bin", "/bin"))
    return {
        "HOME": str(isolation.home),
        "XDG_CONFIG_HOME": str(isolation.config),
        "XDG_CACHE_HOME": str(isolation.cache),
        "XDG_DATA_HOME": str(isolation.data),
        "TMPDIR": str(isolation.temporary),
        "PATH": os.pathsep.join(path_parts),
        "LANG": "C",
        "LC_ALL": "C",
    }


def _safe_executable(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        return None
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
            return None
        if metadata.st_mode & 0o111 == 0:
            return None
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            opened = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        return None
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_dev != metadata.st_dev
        or opened.st_ino != metadata.st_ino
    ):
        return None
    return path


def _limit_probe_output() -> None:
    resource.setrlimit(resource.RLIMIT_FSIZE, (PROBE_OUTPUT_LIMIT, PROBE_OUTPUT_LIMIT))


def _bounded_probe(
    command: list[str], *, cwd: Path, env: dict[str, str]
) -> tuple[bool, bytes]:
    with tempfile.TemporaryFile() as output:
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                close_fds=True,
                start_new_session=True,
                preexec_fn=_limit_probe_output,
                cwd=cwd,
                env=env,
            )
        except OSError:
            return False, b""
        try:
            process.wait(timeout=PROBE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            with __import__("contextlib").suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=PROBE_TIMEOUT_SECONDS)
            return False, b""
        output.seek(0)
        payload = output.read(PROBE_OUTPUT_LIMIT + 1)
    if process.returncode != 0 or len(payload) > PROBE_OUTPUT_LIMIT:
        return False, b""
    return True, payload


def _option_names(payload: bytes) -> set[str]:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return set()
    return set(
        re.findall(
            r"(?<![A-Za-z0-9_-])--[A-Za-z0-9][A-Za-z0-9-]*"
            r"(?![A-Za-z0-9_-])",
            text,
        )
    )


def _sanitized_version(payload: bytes) -> str | None:
    text = payload.decode("utf-8", errors="replace")
    line = next((item.strip() for item in text.splitlines() if item.strip()), "")
    line = line.replace(str(Path.home()), "<home>")
    line = re.sub(r"[/\\]+[^ ]*", " <path>", line)
    line = re.sub(r"[^A-Za-z0-9 ._+():<>-]", " ", line)
    line = re.sub(r"\s+", " ", line).strip()[:120]
    return line or None


def _resolved_probe_path(name: str, env_name: str) -> Path | None:
    configured = os.environ.get(env_name)
    if configured is not None:
        return _safe_executable(configured)
    return _safe_executable(shutil.which(name))


def _live_preflight(
    project: Path,
    *,
    require_explicit_paths: bool = False,
    isolation: _ProbeIsolation | None = None,
) -> dict[str, object]:
    isolation = isolation or _prepare_probe_isolation(project)
    before = _roots_snapshot((project, *isolation.roots))
    blockers: list[str] = []
    tools: list[dict[str, object]] = []
    resolved: list[Path] = []
    for name, env_name, _help_args, _version_args in TOOL_SPECS:
        configured = os.environ.get(env_name)
        executable = (
            _safe_executable(configured)
            if require_explicit_paths
            else _resolved_probe_path(name, env_name)
        )
        if executable is not None:
            resolved.append(executable)
    probe_env = _probe_environment(isolation, tuple(resolved))
    for name, env_name, help_args, version_args in TOOL_SPECS:
        configured = os.environ.get(env_name)
        executable = (
            _safe_executable(configured)
            if require_explicit_paths
            else _resolved_probe_path(name, env_name)
        )
        unavailable = name.replace("-", "_") + "_unavailable"
        tool_ready = executable is not None
        version: str | None = None
        if executable is None:
            blockers.append(unavailable)
        else:
            version_ok, version_output = _bounded_probe(
                [str(executable), *version_args], cwd=project, env=probe_env
            )
            version = _sanitized_version(version_output) if version_ok else None
            if version is None:
                blockers.append(unavailable)
                tool_ready = False
            if help_args is not None:
                help_ok, help_output = _bounded_probe(
                    [str(executable), *help_args], cwd=project, env=probe_env
                )
                required = (
                    {"--output-schema", "--output-last-message"}
                    if name == "codex"
                    else {"--json-schema", "--output-format"}
                )
                capability_blocker = f"{name}_native_schema_unavailable"
                if not help_ok or not required.issubset(_option_names(help_output)):
                    blockers.append(capability_blocker)
                    tool_ready = False
        tools.append(
            {
                "name": name,
                "executable_basename": (
                    executable.name if executable is not None else name
                ),
                "version": version,
                "ready": tool_ready,
            }
        )
    if _roots_snapshot((project, *isolation.roots)) != before:
        blockers.append("probe_wrote_files")
    unique_blockers = list(dict.fromkeys(blockers))
    return {
        "schema_version": "m2c-live-preflight/v1",
        "mode": "m2c_live_preflight",
        "ready": not unique_blockers,
        "probe_timeout_seconds": PROBE_TIMEOUT_SECONDS,
        "tools": tools,
        "blockers": unique_blockers,
    }


def _validate_preflight_payload(payload: object) -> list[str]:
    errors: list[str] = []
    if type(payload) is not dict or set(payload) != {
        "schema_version",
        "mode",
        "ready",
        "probe_timeout_seconds",
        "tools",
        "blockers",
    }:
        return ["preflight shape invalid"]
    if payload["schema_version"] != "m2c-live-preflight/v1":
        errors.append("schema_version invalid")
    if payload["mode"] != "m2c_live_preflight":
        errors.append("mode invalid")
    blockers = payload["blockers"]
    if (
        type(blockers) is not list
        or any(type(item) is not str or item not in BLOCKER_CODES for item in blockers)
        or len(set(blockers)) != len(blockers)
    ):
        errors.append("blockers invalid")
    if type(payload["ready"]) is not bool or payload["ready"] != (not blockers):
        errors.append("ready invalid")
    if payload["probe_timeout_seconds"] != 5:
        errors.append("timeout invalid")
    tools = payload["tools"]
    if type(tools) is not list or len(tools) != 4:
        errors.append("tools invalid")
    else:
        for tool in tools:
            if type(tool) is not dict or set(tool) != {
                "name", "executable_basename", "version", "ready"
            }:
                errors.append("tool shape invalid")
                continue
            if type(tool["name"]) is not str or type(tool["executable_basename"]) is not str:
                errors.append("tool identity invalid")
            if tool["version"] is not None and (
                type(tool["version"]) is not str or len(tool["version"]) > 120
            ):
                errors.append("tool version invalid")
            if type(tool["ready"]) is not bool:
                errors.append("tool ready invalid")
    return errors


@dataclass
class _PtyTail:
    byte_count: int = 0
    truncated: bool = False
    tail: bytes = b""

    def __post_init__(self) -> None:
        self._digest = hashlib.sha256()

    def add(self, chunk: bytes) -> None:
        self.byte_count += len(chunk)
        self._digest.update(chunk)
        combined = self.tail + chunk
        if len(combined) > 64 * 1024:
            self.truncated = True
            combined = combined[-64 * 1024 :]
        self.tail = combined

    def diagnostic(self) -> dict[str, object]:
        return {
            "byte_count": self.byte_count,
            "truncated": self.truncated,
            "sha256": self._digest.hexdigest(),
        }


class _LiveHarnessFailure(AssertionError):
    pass


def _state_cardinalities(store: StateStore) -> dict[str, int]:
    state = store.load()
    fields = (
        "plans",
        "missions",
        "mission_attempts",
        "permission_requests",
        "mission_handoffs",
        "mission_worker_replies",
    )
    return {
        field: len(state.get(field, []))
        if type(state.get(field, [])) in {list, dict}
        else -1
        for field in fields
    }


def _live_failure(
    code: str,
    *,
    store: StateStore | None = None,
    capture: _PtyTail | None = None,
    output: bytes | None = None,
) -> _LiveHarnessFailure:
    diagnostic: dict[str, object] = {"stage": "live_acceptance", "code": code}
    if store is not None:
        diagnostic["cardinalities"] = _state_cardinalities(store)
    if capture is not None:
        diagnostic["pty"] = capture.diagnostic()
    if output is not None:
        diagnostic["output"] = {
            "byte_count": len(output),
            "truncated": False,
            "sha256": hashlib.sha256(output).hexdigest(),
        }
    return _LiveHarnessFailure(json.dumps(diagnostic, sort_keys=True))


def _require_live(
    condition: bool,
    code: str,
    *,
    store: StateStore | None = None,
    capture: _PtyTail | None = None,
) -> None:
    if not condition:
        raise _live_failure(code, store=store, capture=capture)


def _bounded_project_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: int = 20,
    env: dict[str, str] | None = None,
    output_limit: int = 2 * 1024 * 1024,
) -> tuple[int, bytes]:
    with tempfile.TemporaryFile() as output:
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                timeout=timeout,
                check=False,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired):
            return -1, b""
        output.seek(0)
        payload = output.read(output_limit + 1)
    if len(payload) > output_limit:
        return -1, b""
    return completed.returncode, payload


def _json_project_command(
    command: list[str], *, cwd: Path, timeout: int = 20
) -> dict[str, object]:
    code, payload = _bounded_project_command(command, cwd=cwd, timeout=timeout)
    if code != 0:
        raise _live_failure("project_command_failed")
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _live_failure("project_command_invalid_json") from None
    if type(decoded) is not dict:
        raise _live_failure("project_command_invalid_json")
    return decoded


def _observe_exact_pane(
    control: dict[str, object],
    *,
    tmux: Path,
    socket_name: str,
    cwd: Path,
    env: dict[str, str],
) -> None:
    command = control.get("command")
    if (
        control.get("kind") != "select_pane"
        or control.get("enabled") is not True
        or type(command) is not str
    ):
        raise _live_failure("pane_control_invalid")
    try:
        argv = shlex.split(command)
    except ValueError:
        raise _live_failure("pane_control_invalid") from None
    if (
        len(argv) != 6
        or argv[:4] != ["tmux", "-L", socket_name, "select-pane"]
        or argv[4] != "-t"
        or re.fullmatch(r"%[0-9]+", argv[5]) is None
    ):
        raise _live_failure("pane_control_invalid")
    pane_id = argv[5]
    code, output = _bounded_project_command(
        argv, cwd=cwd, timeout=10, env=env
    )
    if code != 0:
        raise _live_failure("pane_select_failed", output=output)
    verify_code, verify_output = _bounded_project_command(
        [
            str(tmux),
            "-L",
            socket_name,
            "display-message",
            "-p",
            "-t",
            pane_id,
            "#{pane_id}",
        ],
        cwd=cwd,
        timeout=10,
        env=env,
    )
    try:
        verified = verify_output.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError:
        verified = ""
    if verify_code != 0 or verified != pane_id:
        raise _live_failure("pane_target_verification_failed", output=verify_output)


def _wait_for_state(
    store: StateStore,
    predicate: Any,
    *,
    timeout: int = 180,
    code: str,
    capture: _PtyTail | None = None,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = store.load()
        try:
            if predicate(state):
                return state
        except (KeyError, TypeError, ValueError):
            pass
        time.sleep(0.1)
    raise _live_failure(code, store=store, capture=capture)


def _drain_pty(master: int, capture: _PtyTail) -> None:
    while True:
        readable, _, _ = select.select([master], [], [], 0)
        if not readable:
            return
        try:
            chunk = os.read(master, 65536)
        except OSError:
            return
        if not chunk:
            return
        capture.add(chunk)


def _wait_for_pty_prompt(
    process: subprocess.Popen[bytes],
    master: int,
    capture: _PtyTail,
    count: int,
) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        _drain_pty(master, capture)
        if capture.tail.count(b"agentdeck> ") >= count:
            return
        if process.poll() is not None:
            raise _live_failure("bare_pty_exited", capture=capture)
        time.sleep(0.05)
    raise _live_failure("bare_pty_prompt_timeout", capture=capture)


def _stop_pty(process: subprocess.Popen[bytes], master: int) -> list[str]:
    failures: list[str] = []
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if process.poll() is None:
            failures.append("pty_process_alive")
    except (OSError, subprocess.SubprocessError):
        failures.append("pty_process_cleanup_failed")
    try:
        os.close(master)
    except OSError:
        failures.append("pty_descriptor_cleanup_failed")
    return failures


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _process_alive(pid: int) -> bool:
    if not _pid_alive(pid):
        return False
    try:
        completed = subprocess.run(
            ["/bin/ps", "-o", "stat=", "-p", str(pid)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    status = completed.stdout.decode("ascii", errors="ignore").strip()
    return bool(status) and not status.startswith("Z")


def _terminate_managed_pids(pids: set[int]) -> list[int]:
    targets = {pid for pid in pids if type(pid) is int and pid > 1 and _process_alive(pid)}
    for signal_number in (signal.SIGTERM, signal.SIGKILL):
        for pid in targets:
            if _process_alive(pid):
                with __import__("contextlib").suppress(ProcessLookupError):
                    os.kill(pid, signal_number)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if not any(_process_alive(pid) for pid in targets):
                break
            time.sleep(0.05)
    return sorted(pid for pid in targets if _process_alive(pid))


def _descendant_pids(root_pid: int) -> set[int]:
    try:
        completed = subprocess.run(
            ["/bin/ps", "-axo", "pid=,ppid="],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    children: dict[int, set[int]] = {}
    for line in completed.stdout.decode("ascii", errors="ignore").splitlines():
        parts = line.split()
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            continue
        pid, parent = map(int, parts)
        children.setdefault(parent, set()).add(pid)
    descendants: set[int] = set()
    pending = list(children.get(root_pid, ()))
    while pending:
        pid = pending.pop()
        if pid in descendants:
            continue
        descendants.add(pid)
        pending.extend(children.get(pid, ()))
    return descendants


def _stop_or_terminate_daemon(
    *,
    daemon_pid: int,
    managed_pids: set[int],
    stop_daemon: Any,
) -> dict[str, object]:
    fallback_used = False
    try:
        stop_daemon()
    except Exception:
        fallback_used = True
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(
        _process_alive(pid) for pid in managed_pids
    ):
        if fallback_used:
            break
        time.sleep(0.05)
    alive = [pid for pid in managed_pids if _process_alive(pid)]
    if alive:
        fallback_used = True
        descendants = {pid for pid in alive if pid != daemon_pid}
        _terminate_managed_pids(descendants)
        alive = _terminate_managed_pids({daemon_pid, *descendants})
    return {"fallback_used": fallback_used, "alive_pids": alive}


def _cleanup_exact_tmux(
    *,
    tmux: Path,
    socket_name: str,
    session_name: str,
    cwd: Path,
    env: dict[str, str],
    socket_paths: tuple[Path, ...],
) -> dict[str, object]:
    _bounded_project_command(
        [str(tmux), "-L", socket_name, "kill-session", "-t", session_name],
        cwd=cwd,
        timeout=10,
        env=env,
    )
    _bounded_project_command(
        [str(tmux), "-L", socket_name, "kill-server"],
        cwd=cwd,
        timeout=10,
        env=env,
    )
    code, _output = _bounded_project_command(
        [str(tmux), "-L", socket_name, "has-session", "-t", session_name],
        cwd=cwd,
        timeout=10,
        env=env,
    )
    return {
        "reachable": code == 0,
        "socket_paths_present": sum(path.exists() for path in socket_paths),
    }


def _derive_residual_audit(
    *,
    tracked_pids: set[int],
    endpoint_paths: tuple[Path, ...],
    tmux_reachable: bool,
    tmux_socket_paths: tuple[Path, ...],
) -> dict[str, object]:
    process_count = sum(_process_alive(pid) for pid in tracked_pids)
    resource_count = (
        sum(path.exists() for path in endpoint_paths)
        + int(tmux_reachable)
        + sum(path.exists() for path in tmux_socket_paths)
    )
    return {
        "cleanup": (
            "complete" if process_count == 0 and resource_count == 0 else "incomplete"
        ),
        "residual_process_count": process_count,
        "residual_resource_count": resource_count,
    }


def _remove_verified_daemon_endpoint(
    *,
    socket_path: Path,
    metadata_path: Path,
    expected: dict[str, object],
) -> bool:
    try:
        current = json.loads(metadata_path.read_text(encoding="utf-8"))
        if type(current) is not dict or any(
            current.get(field) != expected.get(field)
            for field in (
                "instance_id",
                "project_root_hash",
                "start_nonce_hash",
                "pid",
            )
        ):
            return False
        metadata_stat = metadata_path.lstat()
        if not stat.S_ISREG(metadata_stat.st_mode) or metadata_path.is_symlink():
            return False
        if socket_path.exists():
            socket_stat = socket_path.lstat()
            if not stat.S_ISSOCK(socket_stat.st_mode) or socket_path.is_symlink():
                return False
            socket_path.unlink()
        metadata_path.unlink()
        return True
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _write_live_config(
    root: Path, paths: dict[str, Path], *, session_name: str
) -> None:
    config = f'''[project]
name = "m2c-live"

[leader]
agent_id = "leader"
provider = "codex-cli"
model = "gpt-5.4"
approval_mode = "confirm"

[[agents]]
agent_id = "claude-worker"
role = "implementation"
provider = "claude"
command = {_toml_string(str(paths["claude"]))}
transport = "acp"
transport_command = [{_toml_string(str(paths["claude-agent-acp"]))}]
workspace_mode = "shared"
role_prompt = "Implement only the exact frozen Mission task. Request edit permission before changing artifact.txt. Return one compact AgentDeck handoff."

[[agents]]
agent_id = "codex-worker"
role = "review"
provider = "codex"
command = {_toml_string(str(paths["codex"]))}
transport = "tmux"
workspace_mode = "shared"
role_prompt = "Review only the exact frozen Mission task. Do not edit artifact.txt. Return one compact AgentDeck handoff."

[runtime]
backend = "tmux"
session_name = {_toml_string(session_name)}
socket_name = {_toml_string(session_name)}

[daemon]
idle_grace_seconds = 600
start_timeout_seconds = 30
controller_ttl_seconds = 3600
max_frame_bytes = 1048576
'''
    (root / ".agentdeck" / "config.toml").write_text(config, encoding="utf-8")


def _explicit_live_paths() -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for name, env_name, _help, _version in TOOL_SPECS:
        path = _safe_executable(os.environ.get(env_name))
        if path is None or path.name != name:
            raise _live_failure(f"{name.replace('-', '_')}_path_invalid")
        paths[name] = path
    return paths


def _create_and_confirm_live_mission(
    root: Path,
    store: StateStore,
    *,
    env: dict[str, str],
    openpty_factory: Any = pty.openpty,
    popen_factory: Any = subprocess.Popen,
) -> tuple[str, _PtyTail, dict[str, object]]:
    capture = _PtyTail()
    master: int | None = None
    slave: int | None = None
    process: subprocess.Popen[bytes] | None = None
    try:
        master, slave = openpty_factory()
        try:
            process = popen_factory(
                [sys.executable, "-m", "agentdeck"],
                cwd=root,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                close_fds=True,
                start_new_session=True,
                env=env,
            )
        except (OSError, subprocess.SubprocessError):
            raise _live_failure(
                "bare_pty_spawn_failed", store=store, capture=capture
            ) from None
        finally:
            if slave is not None:
                try:
                    os.close(slave)
                except OSError:
                    pass
                slave = None
        assert process is not None and master is not None
        _wait_for_pty_prompt(process, master, capture, 1)
        request = (
            "让 claude-worker 和 codex-worker 严格串行完成4轮。"
            "阶段必须精确为 implementation、review、revision、acceptance："
            "第一轮 claude-worker 创建 artifact.txt 且内容为 draft-v1 换行；"
            "第二轮 codex-worker 只读审查并要求 accepted-v2；"
            "第三轮 claude-worker 将 artifact.txt 精确改为 accepted-v2 换行；"
            "第四轮 codex-worker 只读验收精确字节。共4轮。\n"
        )
        os.write(master, request.encode("utf-8"))
        previewed = _wait_for_state(
            store,
            lambda state: len(state.get("missions", [])) == 1
            and len(state.get("plans", [])) == 1
            and bool(state.get("conversation_preview_bindings")),
            code="mission_preview_timeout",
            capture=capture,
        )
        mission = previewed["missions"][0]
        mission_id = str(mission["mission_id"])
        plan = previewed["plans"][0]["plan"]
        steps = plan.get("steps", []) if type(plan) is dict else []
        expected_agents = [
            "claude-worker", "codex-worker", "claude-worker", "codex-worker"
        ]
        _require_live(
            len(steps) == 4
            and [item.get("agent_id") for item in steps] == expected_agents,
            "native_schema_phase_authority_invalid",
            store=store,
            capture=capture,
        )
        expected_phases = ("implementation", "review", "revision", "acceptance")
        _require_live(
            all(
                phase in str(step.get("task", "")).lower()
                for phase, step in zip(expected_phases, steps, strict=True)
            ),
            "native_schema_semantic_phases_invalid",
            store=store,
            capture=capture,
        )
        generation = previewed["plans"][0].get("leader_generation")
        _require_live(
            type(generation) is dict
            and generation.get("constraint_mode") == "native_json_schema",
            "native_schema_provenance_missing",
            store=store,
            capture=capture,
        )
        _wait_for_pty_prompt(process, master, capture, 2)
        os.write(master, "确认执行当前预览\n".encode("utf-8"))
        admitted = _wait_for_state(
            store,
            lambda state: state.get("missions")
            and type(state["missions"][0].get("execution_snapshot")) is dict
            and state["missions"][0].get("daemon_admission", {}).get("state")
            == "admitted",
            code="mission_admission_timeout",
            capture=capture,
        )
        consumed = [
            event for event in store.all_events()
            if event.get("event_type") == "conversation_preview_consumed"
            and event.get("payload", {}).get("mission_id") == mission_id
        ]
        _require_live(
            len(consumed) == 1,
            "mission_preview_not_consumed_exactly_once",
            store=store,
            capture=capture,
        )
        return mission_id, capture, admitted
    except _LiveHarnessFailure:
        raise
    except Exception:
        raise _live_failure("bare_pty_failed", store=store, capture=capture) from None
    finally:
        failures: list[str] = []
        if process is not None and master is not None:
            failures.extend(_stop_pty(process, master))
            master = None
        elif master is not None:
            try:
                os.close(master)
            except OSError:
                failures.append("pty_descriptor_cleanup_failed")
            master = None
        if slave is not None:
            try:
                os.close(slave)
            except OSError:
                failures.append("pty_descriptor_cleanup_failed")
        if failures:
            primary = sys.exception()
            if primary is None:
                raise _live_failure(failures[0], store=store, capture=capture)
            primary.add_note(
                json.dumps({"stage": "pty_cleanup", "codes": failures}, sort_keys=True)
            )


def _confirm_pending_permission(root: Path, store: StateStore) -> None:
    config = load_config(root)
    view = asdict(store.project_view(config))
    decision = view.get("mission_recovery", {}).get("decision")
    controls = decision.get("controls", []) if type(decision) is dict else []
    _require_live(len(controls) == 1, "permission_control_missing", store=store)
    preview_command = str(controls[0].get("command", ""))
    preview = _json_project_command(shlex.split(preview_command), cwd=root)
    _require_live(
        set(preview) == {
            "mode", "mission_id", "attempt_id", "permission_id", "decision",
            "preview_id", "confirmation_handle", "expires_at", "confirm_command",
        }
        and type(preview.get("confirmation_handle")) is str
        and str(preview["confirmation_handle"]).startswith("pcf_")
        and "lse_" not in repr(preview),
        "permission_preview_contract_invalid",
        store=store,
    )
    confirmed = _json_project_command(
        shlex.split(str(preview["confirm_command"])), cwd=root
    )
    _require_live(
        confirmed.get("state") == "approved"
        and confirmed.get("confirmation_handle") == preview["confirmation_handle"]
        and "lse_" not in repr(confirmed),
        "permission_confirmation_failed",
        store=store,
    )


async def _govern_live_worker(
    root: Path,
    *,
    method: str,
    reported_changes: dict[str, object] | None = None,
) -> dict[str, object]:
    client = await DaemonClient.connect_verified(root, timeout_seconds=10)
    try:
        lease = await client.request(
            "controller.acquire", {"client_id": f"m2c-live-{method}"}
        )
        authority = {
            "lease_id": str(lease["lease_id"]),
            "lease_generation": int(lease["generation"]),
        }
        params: dict[str, object] = {"agent_id": "codex-worker"}
        if reported_changes is not None:
            params["reported_changes"] = reported_changes
        preview = await client.request(method, params, **authority)
        result = await client.request(
            method,
            {**params, "preview_id": preview["preview_id"]},
            **authority,
        )
        await client.request(
            "controller.release",
            {
                "lease_id": lease["lease_id"],
                "generation": lease["generation"],
            },
            **authority,
        )
        return result
    finally:
        await client.close()


async def _stop_live_daemon(root: Path) -> None:
    client = await DaemonClient.connect_verified(root, timeout_seconds=10)
    try:
        lease = await client.request(
            "controller.acquire", {"client_id": "m2c-live-cleanup"}
        )
        await client.request(
            "daemon.stop",
            {
                "lease_id": lease["lease_id"],
                "generation": lease["generation"],
            },
            lease_id=str(lease["lease_id"]),
            lease_generation=int(lease["generation"]),
        )
    finally:
        await client.close()


@contextmanager
def _live_resource_guard(
    root: Path,
    parent: Path,
    tmux: Path,
    *,
    session_name: str,
    cleanup_env: dict[str, str],
    tmux_socket_paths: tuple[Path, ...],
):
    cleanup_failures: list[str] = []
    audit: dict[str, object] = {
        "cleanup": "pending",
        "residual_process_count": None,
        "residual_resource_count": None,
    }
    try:
        yield audit
    finally:
        endpoint = daemon_endpoint(root)
        daemon_pid: int | None = None
        managed_pids: set[int] = set()
        metadata_verified = False
        verified_metadata: dict[str, object] | None = None
        if endpoint.metadata_path.is_file():
            try:
                metadata = json.loads(endpoint.metadata_path.read_text(encoding="utf-8"))
                candidate_pid = metadata.get("pid") if type(metadata) is dict else None
                if (
                    type(candidate_pid) is int
                    and candidate_pid > 1
                    and type(metadata.get("instance_id")) is str
                    and str(metadata["instance_id"]).startswith("dmn_")
                    and type(metadata.get("start_nonce_hash")) is str
                    and re.fullmatch(
                        r"[0-9a-f]{64}", str(metadata["start_nonce_hash"])
                    )
                    is not None
                    and metadata.get("project_root_hash") == project_root_hash(root)
                ):
                    daemon_pid = candidate_pid
                    metadata_verified = True
                    verified_metadata = dict(metadata)
                    managed_pids = {daemon_pid, *_descendant_pids(daemon_pid)}
                else:
                    cleanup_failures.append("daemon_identity_unverified")
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                cleanup_failures.append("daemon_metadata_invalid")
        if metadata_verified and daemon_pid is not None:
            daemon_cleanup = _stop_or_terminate_daemon(
                daemon_pid=daemon_pid,
                managed_pids=managed_pids,
                stop_daemon=lambda: asyncio.run(_stop_live_daemon(root)),
            )
            if daemon_cleanup["alive_pids"]:
                cleanup_failures.append("daemon_process_retained")
            elif (
                (endpoint.socket_path.exists() or endpoint.metadata_path.exists())
                and verified_metadata is not None
                and not _remove_verified_daemon_endpoint(
                    socket_path=endpoint.socket_path,
                    metadata_path=endpoint.metadata_path,
                    expected=verified_metadata,
                )
            ):
                cleanup_failures.append("daemon_endpoint_cleanup_failed")
        elif endpoint.socket_path.exists() or endpoint.metadata_path.exists():
            cleanup_failures.append("daemon_cleanup_unverified")
        tmux_cleanup = _cleanup_exact_tmux(
            tmux=tmux,
            socket_name=session_name,
            session_name=session_name,
            cwd=root,
            env=cleanup_env,
            socket_paths=tmux_socket_paths,
        )
        if tmux_cleanup["reachable"] or tmux_cleanup["socket_paths_present"]:
            cleanup_failures.append("tmux_cleanup_incomplete")
        try:
            shutil.rmtree(parent)
        except OSError:
            cleanup_failures.append("project_cleanup_failed")
        if parent.exists():
            cleanup_failures.append("project_retained")
        audit.update(
            _derive_residual_audit(
                tracked_pids=managed_pids,
                endpoint_paths=(endpoint.socket_path, endpoint.metadata_path),
                tmux_reachable=bool(tmux_cleanup["reachable"]),
                tmux_socket_paths=tmux_socket_paths,
            )
        )
        if audit["cleanup"] != "complete":
            cleanup_failures.append("cleanup_residuals_retained")
        if cleanup_failures:
            primary = sys.exception()
            if primary is None:
                raise _live_failure(cleanup_failures[0])
            primary.add_note(
                json.dumps(
                    {"stage": "cleanup", "codes": cleanup_failures},
                    sort_keys=True,
                )
            )


def _run_live_acceptance() -> dict[str, object]:
    parent: Path | None = None
    try:
        paths = _explicit_live_paths()
        parent = Path(
            tempfile.mkdtemp(prefix="agentdeck-m2c-live-", dir="/tmp")
        ).resolve()
        return _run_live_acceptance_in_project(paths, parent)
    except _LiveHarnessFailure:
        raise
    except Exception:
        cleanup_failed = False
        if parent is not None and parent.exists():
            try:
                shutil.rmtree(parent)
            except OSError:
                cleanup_failed = True
        raise _live_failure(
            "live_setup_cleanup_failed" if cleanup_failed else "live_setup_failed"
        ) from None


def _run_live_acceptance_in_project(
    paths: dict[str, Path], parent: Path
) -> dict[str, object]:
    root = parent / "repo"
    root.mkdir(mode=0o700)
    session_name = "agentdeck-m2c-" + parent.name[-8:]
    artifact = root / "artifact.txt"
    tmux_temporary = parent / "tmux-tmp"
    tmux_temporary.mkdir(mode=0o700)
    tmux_socket_paths = (
        tmux_temporary / f"tmux-{os.getuid()}" / session_name,
    )
    cleanup_env = {
        "HOME": str(parent),
        "TMPDIR": str(parent),
        "TMUX_TMPDIR": str(tmux_temporary),
        "PATH": str(paths["tmux"].parent) + os.pathsep + "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
    }
    with _live_resource_guard(
        root,
        parent,
        paths["tmux"],
        session_name=session_name,
        cleanup_env=cleanup_env,
        tmux_socket_paths=tmux_socket_paths,
    ) as cleanup_audit:
        preflight_before = _tree_snapshot(root)
        preflight = _live_preflight(root, require_explicit_paths=True)
        _require_live(_tree_snapshot(root) == preflight_before, "preflight_wrote_project")
        _require_live(preflight["ready"] is True, "preflight_blocked")
        checkout = Path(__file__).resolve().parents[1]
        _require_live(
            checkout not in root.parents and root not in checkout.parents,
            "project_not_disposable",
        )
        runtime_bin = parent / "runtime-bin"
        runtime_bin.mkdir(mode=0o700)
        for name in ("codex", "claude", "tmux"):
            wrapper = runtime_bin / name
            wrapper.write_text(
                "#!/bin/sh\nexec " + shlex.quote(str(paths[name])) + ' "$@"\n',
                encoding="utf-8",
            )
            wrapper.chmod(0o700)
        env = dict(os.environ)
        env["PATH"] = str(runtime_bin) + os.pathsep + env.get("PATH", "")
        env["TMUX_TMPDIR"] = str(tmux_temporary)
        code, _ = _bounded_project_command(
            ["git", "init", "-q"], cwd=root, timeout=10, env=env
        )
        _require_live(code == 0, "git_init_failed")
        code, _ = _bounded_project_command(
            [sys.executable, "-m", "agentdeck", "project", "init"],
            cwd=root,
            timeout=20,
            env=env,
        )
        _require_live(code == 0, "project_init_failed")
        _write_live_config(root, paths, session_name=session_name)
        store = StateStore(root)
        mission_id, capture, admitted = _create_and_confirm_live_mission(
            root, store, env=env
        )
        _require_live(
            admitted["missions"][0]["daemon_admission"]["state"] == "admitted",
            "admission_not_durable",
            store=store,
            capture=capture,
        )
        first_pending = _wait_for_state(
            store,
            lambda state: len(state.get("permission_requests", [])) == 1
            and state["permission_requests"][0].get("status") == "pending",
            code="first_permission_timeout",
        )
        _require_live(
            len(first_pending.get("mission_attempts", [])) == 1,
            "first_attempt_cardinality_invalid",
            store=store,
        )
        _confirm_pending_permission(root, store)
        _wait_for_state(
            store,
            lambda state: len(state.get("permission_requests", [])) == 2
            and len(state.get("mission_attempts", [])) == 3
            and state["permission_requests"][1].get("status") == "pending"
            and state["mission_attempts"][1].get("state") == "succeeded",
            code="third_stage_safe_window_timeout",
        )
        config = load_config(root)
        view = asdict(store.project_view(config))
        workbench = cli_module._workbench_snapshot_payload(view, store)
        _require_live(
            validate_workbench_contract(workbench)["ok"] is True,
            "workbench_contract_invalid",
            store=store,
        )
        terminals = workbench["terminal_session_card"]["terminals"]
        codex_terminal = next(
            (item for item in terminals if item.get("agent_id") == "codex-worker"),
            None,
        )
        pane_controls = (
            [
                control
                for control in codex_terminal.get("controls", [])
                if control.get("kind") == "select_pane"
                and control.get("enabled") is True
            ]
            if type(codex_terminal) is dict
            else []
        )
        _require_live(
            len(pane_controls) == 1,
            "codex_pane_control_missing",
            store=store,
        )
        _observe_exact_pane(
            pane_controls[0],
            tmux=paths["tmux"],
            socket_name=config.runtime.socket_name,
            cwd=root,
            env=env,
        )
        taken = asyncio.run(
            _govern_live_worker(root, method="worker.takeover")
        )
        _require_live(
            taken.get("ownership") == "human_owned",
            "takeover_failed",
            store=store,
        )
        returned = asyncio.run(
            _govern_live_worker(
                root,
                method="worker.return-control",
                reported_changes={"summary": "no human changes", "paths": []},
            )
        )
        _require_live(
            returned.get("ownership") == "agentdeck_owned",
            "return_control_failed",
            store=store,
        )
        _confirm_pending_permission(root, store)
        completed = _wait_for_state(
            store,
            lambda state: state.get("missions")
            and state["missions"][0].get("status") == "completed",
            code="mission_completion_timeout",
            timeout=300,
        )
        attempts = completed.get("mission_attempts", [])
        handoffs = completed.get("mission_handoffs", [])
        replies = completed.get("mission_worker_replies", [])
        _require_live(
            len(attempts) == 4
            and all(item.get("state") == "succeeded" for item in attempts),
            "attempt_terminal_facts_invalid",
            store=store,
        )
        _require_live(
            len(handoffs) == 4
            and all(item.get("state") == "recorded" for item in handoffs),
            "canonical_handoff_facts_invalid",
            store=store,
        )
        _require_live(
            len(replies) == 4
            and all(item.get("state") == "validated" for item in replies),
            "reply_facts_invalid",
            store=store,
        )
        events = store.all_events()
        predecessor_links = 0
        for index in range(1, 4):
            predecessor = attempts[index - 1]["attempt_id"]
            successor = attempts[index]["attempt_id"]
            handoff_positions = [
                position
                for position, event in enumerate(events)
                if event.get("event_type") == "mission_handoff_evidence_recorded"
                and event.get("payload", {}).get("attempt_id") == predecessor
            ]
            submit_positions = [
                position
                for position, event in enumerate(events)
                if event.get("event_type") == "mission_attempt_submitted"
                and event.get("payload", {}).get("attempt_id") == successor
            ]
            _require_live(
                len(handoff_positions) == len(submit_positions) == 1,
                "inter_stage_event_cardinality_invalid",
                store=store,
            )
            predecessor_links += int(handoff_positions[0] < submit_positions[0])
        _require_live(
            predecessor_links == 3,
            "inter_stage_links_invalid",
            store=store,
        )
        expected = b"accepted-v2\n"
        try:
            artifact_bytes = artifact.read_bytes() if artifact.is_file() else None
        except OSError:
            raise _live_failure("artifact_read_failed", store=store) from None
        _require_live(
            artifact_bytes == expected,
            "artifact_bytes_invalid",
            store=store,
        )
        mission = completed["missions"][0]
        permissions = completed.get("permission_requests", [])
        _require_live(
            mission.get("snapshot_hash")
            == mission.get("execution_snapshot", {}).get("execution_hash")
            and mission.get("daemon_admission", {}).get("state") == "admitted"
            and all(item.get("receipt_summary") for item in attempts),
            "snapshot_admission_receipt_disagreement",
            store=store,
        )
        _require_live(
            len(permissions) == 2
            and all(item.get("status") == "approved" for item in permissions),
            "permission_facts_invalid",
            store=store,
        )
        final_view = asdict(store.project_view(config))
        final_workbench = cli_module._workbench_snapshot_payload(final_view, store)
        _require_live(
            final_view["missions"]["items"][-1]["status"] == "completed"
            and final_workbench["mission_card"]["status"] == "completed"
            and final_workbench["ledger_card"]["messages"] == final_view["messages"]
            and final_workbench["ledger_card"]["jobs"] == final_view["jobs"]
            and final_workbench["ledger_card"]["replies"] == final_view["replies"]
            and final_workbench["ledger_card"]["artifacts"] == final_view["artifacts"],
            "project_view_workbench_ledger_disagreement",
            store=store,
        )
        mission_status = _json_project_command(
            ["agentdeck", "mission", "status", "--mission-id", mission_id],
            cwd=root,
        )
        _require_live(
            mission_status.get("status") == "completed"
            and mission_status.get("mission_id") == mission_id,
            "mission_status_disagreement",
            store=store,
        )
        try:
            traces = [
                store.trace(str(attempt["attempt_id"])) for attempt in attempts
            ]
        except (KeyError, TypeError, ValueError):
            raise _live_failure("trace_unavailable", store=store) from None
        _require_live(
            all(validate_trace_contract(trace)["ok"] is True for trace in traces),
            "trace_contract_disagreement",
            store=store,
        )
        _require_live(
            len(
                [
                    event for event in events
                    if event.get("event_type") == "mission_attempt_submitted"
                    and event.get("payload", {}).get("mission_id") == mission_id
                ]
            ) == 4,
            "event_timeline_disagreement",
            store=store,
        )
        commit_code, commit_output = _bounded_project_command(
            ["git", "rev-parse", "HEAD"], cwd=checkout, timeout=10
        )
        frozen_commit = commit_output.decode("ascii", errors="ignore").strip()
        _require_live(
            commit_code == 0
            and re.fullmatch(r"[0-9a-f]{40,64}", frozen_commit) is not None,
            "frozen_commit_unavailable",
        )
        evidence = {
            "result": "PASS",
            "frozen_agentdeck_commit": frozen_commit,
            "tools": preflight["tools"],
            "attempt_count": len(attempts),
            "canonical_handoff_count": len(handoffs),
            "inter_stage_link_count": predecessor_links,
            "permission_count": len(completed.get("permission_requests", [])),
            "artifact_byte_count": len(expected),
            "artifact_sha256": hashlib.sha256(expected).hexdigest(),
            "cleanup": cleanup_audit,
        }
        _require_live(
            all("/" not in str(tool.get("version") or "") for tool in evidence["tools"]),
            "evidence_version_not_sanitized",
        )
        return evidence


def test_m2c_live_preflight_is_read_only(tmp_path, monkeypatch) -> None:
    project = tmp_path / "preflight-project"
    project.mkdir()
    before = _tree_snapshot(project)

    payload = _live_preflight(project)

    assert _tree_snapshot(project) == before
    assert _validate_preflight_payload(payload) == []
    print(json.dumps(payload, sort_keys=True))
    assert "AGENTDECK_M2C_" not in repr(payload)
    assert str(os.path.expanduser("~")) not in repr(payload)

    unsafe_target = tmp_path / "unsafe-target"
    unsafe_target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    unsafe_target.chmod(0o700)
    unsafe_link = tmp_path / "claude"
    unsafe_link.symlink_to(unsafe_target)
    monkeypatch.setenv("AGENTDECK_M2C_CODEX", "relative-codex")
    monkeypatch.setenv("AGENTDECK_M2C_CLAUDE", str(unsafe_link))
    monkeypatch.delenv("AGENTDECK_M2C_CLAUDE_ACP", raising=False)
    monkeypatch.delenv("AGENTDECK_M2C_TMUX", raising=False)
    rejected = _live_preflight(project, require_explicit_paths=True)
    assert rejected["ready"] is False
    assert rejected["blockers"] == [
        "codex_unavailable",
        "claude_unavailable",
        "claude_agent_acp_unavailable",
        "tmux_unavailable",
    ]
    assert _validate_preflight_payload(rejected) == []
    assert _tree_snapshot(project) == before


def test_preflight_isolates_probe_writes_from_real_home(
    tmp_path, monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    monkeypatch.setenv("HOME", str(real_home))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    script = (
        "#!/bin/sh\n"
        "touch \"$HOME/probe-write\"\n"
        "case \"$1\" in\n"
        "  exec) echo --output-schema --output-last-message;;\n"
        "  --help) echo --json-schema --output-format;;\n"
        "  *) echo m2c-tool-1.0;;\n"
        "esac\n"
    )
    for name, env_name, _help, _version in TOOL_SPECS:
        executable = fake_bin / name
        executable.write_text(script, encoding="utf-8")
        executable.chmod(0o700)
        monkeypatch.setenv(env_name, str(executable))
    isolation = _prepare_probe_isolation(project)
    before = _roots_snapshot((project, *isolation.roots))

    payload = _live_preflight(
        project,
        require_explicit_paths=True,
        isolation=isolation,
    )

    assert payload["ready"] is False
    assert "probe_wrote_files" in payload["blockers"]
    assert not (real_home / "probe-write").exists()
    assert _roots_snapshot((project, *isolation.roots)) != before


def test_exact_pane_control_executes_and_verifies_target(
    tmp_path,
) -> None:
    log = tmp_path / "tmux.log"
    fake_tmux = tmp_path / "tmux"
    fake_tmux.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(log))}\n"
        "if [ \"$3\" = display-message ]; then printf '%%42\\n'; fi\n",
        encoding="utf-8",
    )
    fake_tmux.chmod(0o700)
    env = {
        "PATH": str(tmp_path) + os.pathsep + "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
    }

    _observe_exact_pane(
        {
            "kind": "select_pane",
            "enabled": True,
            "command": "tmux -L exact-socket select-pane -t %42",
        },
        tmux=fake_tmux,
        socket_name="exact-socket",
        cwd=tmp_path,
        env=env,
    )

    assert log.read_text(encoding="utf-8").splitlines() == [
        "-L exact-socket select-pane -t %42",
        "-L exact-socket display-message -p -t %42 #{pane_id}",
    ]


def test_daemon_stop_failure_terminates_tracked_child_tree(tmp_path) -> None:
    child_pid_file = tmp_path / "child.pid"
    parent = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import subprocess,sys,time; "
                "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); "
                f"open({str(child_pid_file)!r},'w').write(str(child.pid)); "
                "time.sleep(60)"
            ),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 5
    while not child_pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    try:
        result = _stop_or_terminate_daemon(
            daemon_pid=parent.pid,
            managed_pids={parent.pid, child_pid},
            stop_daemon=lambda: (_ for _ in ()).throw(OSError("SECRET_PATH")),
        )
        assert result == {"fallback_used": True, "alive_pids": []}
        assert not _process_alive(parent.pid)
        assert not _process_alive(child_pid)
    finally:
        for pid in (child_pid, parent.pid):
            with __import__("contextlib").suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        with __import__("contextlib").suppress(subprocess.TimeoutExpired):
            parent.wait(timeout=2)


def test_tmux_cleanup_targets_only_exact_socket(tmp_path) -> None:
    log = tmp_path / "tmux.log"
    target = tmp_path / "target.alive"
    other = tmp_path / "other.alive"
    target.write_text("alive", encoding="utf-8")
    other.write_text("alive", encoding="utf-8")
    fake_tmux = tmp_path / "tmux"
    fake_tmux.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(log))}\n"
        f"if [ \"$2\" = target-socket ] && [ \"$3\" = kill-server ]; then rm -f {shlex.quote(str(target))}; fi\n"
        f"if [ \"$2\" = target-socket ] && [ \"$3\" = has-session ] && [ ! -f {shlex.quote(str(target))} ]; then exit 1; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_tmux.chmod(0o700)

    result = _cleanup_exact_tmux(
        tmux=fake_tmux,
        socket_name="target-socket",
        session_name="target-session",
        cwd=tmp_path,
        env={
            "PATH": str(tmp_path) + os.pathsep + "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "TMPDIR": str(tmp_path),
        },
        socket_paths=(target,),
    )

    assert result == {"reachable": False, "socket_paths_present": 0}
    assert not target.exists()
    assert other.exists()
    assert all("other" not in line for line in log.read_text().splitlines())


def test_pty_spawn_failure_closes_descriptors_and_is_compact(tmp_path) -> None:
    opened: list[int] = []

    def openpty():
        master, slave = pty.openpty()
        opened.extend((master, slave))
        return master, slave

    def fail_popen(*_args, **_kwargs):
        raise OSError("SECRET_PATH terminal text")

    with pytest.raises(AssertionError) as error:
        _create_and_confirm_live_mission(
            tmp_path,
            StateStore(tmp_path),
            env={},
            openpty_factory=openpty,
            popen_factory=fail_popen,
        )
    assert "bare_pty_spawn_failed" in str(error.value)
    assert "SECRET" not in str(error.value)
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_residual_count_is_derived_and_blocks_on_live_pid(tmp_path) -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    endpoint = tmp_path / "daemon.sock"
    endpoint.write_text("present", encoding="utf-8")
    try:
        audit = _derive_residual_audit(
            tracked_pids={process.pid},
            endpoint_paths=(endpoint,),
            tmux_reachable=False,
            tmux_socket_paths=(),
        )
        assert audit["residual_process_count"] == 1
        assert audit["residual_resource_count"] == 1
        assert audit["cleanup"] == "incomplete"
    finally:
        process.kill()
        process.wait(timeout=2)


@pytest.mark.skipif(
    not LIVE,
    reason="set AGENTDECK_M2C_LIVE=1 for real M2c acceptance",
)
def test_real_four_stage_m2c_acceptance() -> None:
    evidence = _run_live_acceptance()
    assert evidence["result"] == "PASS"
    assert evidence["attempt_count"] == 4
    assert evidence["canonical_handoff_count"] == 4
    assert evidence["inter_stage_link_count"] == 3
    assert evidence["cleanup"] == {
        "cleanup": "complete",
        "residual_process_count": 0,
        "residual_resource_count": 0,
    }
