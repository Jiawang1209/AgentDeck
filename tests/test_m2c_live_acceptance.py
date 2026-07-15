from __future__ import annotations

import json
import asyncio
import copy
import ctypes
import ctypes.util
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
import socket
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
from agentdeck.daemon.lifecycle import (
    DaemonIdentityError,
    bind_daemon_endpoint,
    daemon_endpoint,
    project_root_hash,
)
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
        "probe_residual_process",
        "probe_scope_unverified",
        "executable_identity_drift",
    }
)
_LIVE_TASK_AUTHORITY_FIELDS = (
    "phase_order",
    "worker_order",
    "artifact_all_steps",
    "implementation_draft",
    "review_target",
    "revision_transition",
    "acceptance_target",
)
_LIVE_MISSION_STATUSES = frozenset(
    {
        "pending_confirmation",
        "preparing",
        "running",
        "completed",
        "stopped",
        "interrupted",
    }
)
_LIVE_ATTEMPT_STATES = frozenset(
    {
        "prepared",
        "admitting",
        "submitted",
        "running",
        "completed",
        "succeeded",
        "failed",
        "cancelled",
        "interrupted",
        "ambiguous",
    }
)
_LIVE_REPLY_STATES = frozenset({"received", "validated", "invalid"})
_LIVE_HANDOFF_STATES = frozenset({"pending", "recorded"})
_LIVE_HANDOFF_STATUSES = frozenset({"completed", "blocked", "failed"})
_LIVE_PERMISSION_STATES = frozenset({"pending", "approved", "denied", "expired"})
_LIVE_AGENT_IDS = frozenset({"claude-worker", "codex-worker"})
_LIVE_TRANSPORTS = frozenset({"acp", "tmux"})
_LIVE_ACTIVE_ATTEMPT_STATES = frozenset(
    {"prepared", "admitting", "submitted", "running"}
)
_LIVE_FAILED_ATTEMPT_STATES = frozenset(
    {"failed", "cancelled", "interrupted"}
)
_LIVE_DIAGNOSTIC_CLASSIFICATIONS = frozenset(
    {
        "leader_task_authority_missing",
        "worker_effect_not_requested",
        "worker_attempt_failed",
        "worker_attempt_active",
        "permission_state_inconsistent",
    }
)


def _exact_live_steps() -> list[dict[str, str]]:
    return [
        {
            "agent_id": "claude-worker",
            "task": "implementation: create artifact.txt with draft-v1",
        },
        {
            "agent_id": "codex-worker",
            "task": "review: require artifact.txt to contain accepted-v2",
        },
        {
            "agent_id": "claude-worker",
            "task": "revision: change artifact.txt from draft-v1 to accepted-v2",
        },
        {
            "agent_id": "codex-worker",
            "task": "acceptance: verify artifact.txt contains accepted-v2",
        },
    ]


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


@dataclass(frozen=True)
class _ExecutableSeal:
    path: Path
    device: int
    inode: int
    owner: int
    mode: int
    size: int
    mtime_ns: int
    content_hash: str


@dataclass(frozen=True)
class _ProbeOutcome:
    ok: bool
    output: bytes
    blocker: str | None = None


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


def _seal_executable(value: str | None) -> _ExecutableSeal | None:
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
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            closed_facts = os.fstat(descriptor)
            final_path = path.lstat()
        finally:
            os.close(descriptor)
    except OSError:
        return None
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_dev != metadata.st_dev
        or opened.st_ino != metadata.st_ino
        or final_path.st_dev != opened.st_dev
        or final_path.st_ino != opened.st_ino
        or (
            closed_facts.st_dev,
            closed_facts.st_ino,
            closed_facts.st_uid,
            stat.S_IMODE(closed_facts.st_mode),
            closed_facts.st_size,
            closed_facts.st_mtime_ns,
        )
        != (
            opened.st_dev,
            opened.st_ino,
            opened.st_uid,
            stat.S_IMODE(opened.st_mode),
            opened.st_size,
            opened.st_mtime_ns,
        )
    ):
        return None
    return _ExecutableSeal(
        path=path,
        device=opened.st_dev,
        inode=opened.st_ino,
        owner=opened.st_uid,
        mode=stat.S_IMODE(opened.st_mode),
        size=opened.st_size,
        mtime_ns=opened.st_mtime_ns,
        content_hash=digest.hexdigest(),
    )


def _verify_executable_seal(seal: _ExecutableSeal) -> None:
    current = _seal_executable(str(seal.path))
    if current != seal:
        raise _live_failure("executable_identity_drift")


def _verify_all_executable_seals(seals: dict[str, _ExecutableSeal]) -> None:
    for name in sorted(seals):
        _verify_executable_seal(seals[name])


def _sealed_argv(seal: _ExecutableSeal, *args: str) -> list[str]:
    _verify_executable_seal(seal)
    return [str(seal.path), *args]


def _write_controlled_launcher(
    name: str,
    source: _ExecutableSeal,
    destination: Path,
) -> _ExecutableSeal:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise _live_failure("executable_identity_drift")
    _verify_executable_seal(source)
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    if stat.S_IMODE(destination.stat().st_mode) != 0o700:
        raise _live_failure("executable_identity_drift")
    launcher = destination / name
    expected = {
        "device": source.device,
        "inode": source.inode,
        "owner": source.owner,
        "mode": source.mode,
        "size": source.size,
        "mtime_ns": source.mtime_ns,
        "content_hash": source.content_hash,
    }
    payload = (
        f"#!{sys.executable}\n"
        "import hashlib, os, stat, sys\n"
        f"PATH = {str(source.path)!r}\n"
        f"EXPECTED = {expected!r}\n"
        "def facts(value):\n"
        "    return {\n"
        "        'device': value.st_dev, 'inode': value.st_ino,\n"
        "        'owner': value.st_uid, 'mode': stat.S_IMODE(value.st_mode),\n"
        "        'size': value.st_size, 'mtime_ns': value.st_mtime_ns,\n"
        "    }\n"
        "try:\n"
        "    fd = os.open(PATH, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))\n"
        "    first = os.fstat(fd)\n"
        "    digest = hashlib.sha256()\n"
        "    while True:\n"
        "        chunk = os.read(fd, 1024 * 1024)\n"
        "        if not chunk: break\n"
        "        digest.update(chunk)\n"
        "    second = os.fstat(fd)\n"
        "    current = os.lstat(PATH)\n"
        "    valid = (stat.S_ISREG(first.st_mode) and facts(first) == facts(second)\n"
        "             and facts(second) == {k: EXPECTED[k] for k in facts(second)}\n"
        "             and current.st_dev == first.st_dev and current.st_ino == first.st_ino\n"
        "             and digest.hexdigest() == EXPECTED['content_hash'])\n"
        "    os.close(fd)\n"
        "    if not valid: os._exit(126)\n"
        "    os.execve(PATH, [PATH, *sys.argv[1:]], dict(os.environ))\n"
        "except (OSError, ValueError):\n"
        "    os._exit(126)\n"
    )
    try:
        descriptor = os.open(
            launcher,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o500,
        )
        try:
            encoded = payload.encode("utf-8")
            offset = 0
            while offset < len(encoded):
                offset += os.write(descriptor, encoded[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        launcher.chmod(0o500)
    except OSError:
        raise _live_failure("executable_identity_drift") from None
    _verify_executable_seal(source)
    sealed = _seal_executable(str(launcher))
    if sealed is None or sealed.mode != 0o500:
        raise _live_failure("executable_identity_drift")
    return sealed


def _limit_probe_output() -> None:
    resource.setrlimit(resource.RLIMIT_FSIZE, (PROBE_OUTPUT_LIMIT, PROBE_OUTPUT_LIMIT))


def _bounded_probe(
    seal: _ExecutableSeal,
    args: tuple[str, ...],
    *,
    cwd: Path,
    env: dict[str, str],
    signal_group: Any = os.killpg,
    scope_sealer: Any = None,
    descendant_provider: Any = None,
    popen_factory: Any = subprocess.Popen,
) -> _ProbeOutcome:
    if _kernel_process_birth(os.getpid()) is None or _kernel_process_uid(os.getpid()) is None:
        return _ProbeOutcome(False, b"", "probe_scope_unverified")
    baseline_pids = _all_process_pids()
    if baseline_pids is None:
        return _ProbeOutcome(False, b"", "probe_scope_unverified")
    marker = hashlib.sha256(os.urandom(32)).hexdigest()
    probe_env = dict(env)
    probe_env["AGENTDECK_M2C_PROBE_SCOPE"] = marker
    scope_sealer = scope_sealer or _seal_process_group
    descendant_provider = descendant_provider or _probe_descendant_pids
    with tempfile.TemporaryFile() as output:
        process: subprocess.Popen[bytes] | None = None
        try:
            command = _sealed_argv(seal, *args)
            process = popen_factory(
                command,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                close_fds=True,
                start_new_session=True,
                preexec_fn=_limit_probe_output,
                cwd=cwd,
                env=probe_env,
            )
            scope = scope_sealer(process.pid)
        except _LiveHarnessFailure as exc:
            if process is not None:
                with __import__("contextlib").suppress(
                    subprocess.TimeoutExpired,
                    subprocess.SubprocessError,
                ):
                    process.wait(timeout=1)
            blocker = (
                "executable_identity_drift"
                if "executable_identity_drift" in str(exc)
                else "probe_scope_unverified"
            )
            return _ProbeOutcome(False, b"", blocker)
        except OSError:
            return _ProbeOutcome(False, b"")
        descendants: dict[int, _ProcessIdentity] = {}
        scope_verified = True
        timed_out = False
        deadline = time.monotonic() + PROBE_TIMEOUT_SECONDS
        while True:
            observed = descendant_provider(process.pid)
            if observed is None:
                scope_verified = False
            else:
                for pid in sorted(observed):
                    if pid in descendants or not _process_alive(pid):
                        continue
                    try:
                        descendants[pid] = _seal_process_identity(pid)
                    except _LiveHarnessFailure:
                        if _process_alive(pid):
                            scope_verified = False
            if process.poll() is not None:
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(0.01)
        marked = _scan_probe_marker(marker, baseline_pids)
        if marked is None:
            scope_verified = False
        else:
            for identity in marked:
                descendants.setdefault(identity.pid, identity)
        cleanup = _terminate_process_group(scope, signal_group=signal_group)
        descendant_cleanup = _terminate_process_identities(
            tuple(descendants.values())
        )
        with __import__("contextlib").suppress(subprocess.TimeoutExpired):
            process.wait(timeout=2)
        quiescent = False
        for _ in range(5):
            marked = _scan_probe_marker(marker, baseline_pids)
            if marked is None:
                scope_verified = False
                break
            if not marked:
                quiescent = True
                break
            extra_cleanup = _terminate_process_identities(marked)
            if extra_cleanup["code"] is not None:
                scope_verified = False
                break
            time.sleep(0.05)
        if not quiescent:
            scope_verified = False
        if (
            not scope_verified
            or descendant_cleanup["code"] is not None
        ):
            return _ProbeOutcome(False, b"", "probe_scope_unverified")
        if (
            cleanup["code"] is not None
            or _process_scope_residual_count(scope)
            or descendant_cleanup["residual_process_count"]
        ):
            return _ProbeOutcome(False, b"", "probe_residual_process")
        try:
            _verify_executable_seal(seal)
        except _LiveHarnessFailure:
            return _ProbeOutcome(False, b"", "executable_identity_drift")
        if timed_out:
            return _ProbeOutcome(False, b"")
        output.seek(0)
        payload = output.read(PROBE_OUTPUT_LIMIT + 1)
        if marker.encode("ascii") in payload:
            return _ProbeOutcome(False, b"", "probe_scope_unverified")
    if process.returncode != 0 or len(payload) > PROBE_OUTPUT_LIMIT:
        return _ProbeOutcome(False, b"")
    return _ProbeOutcome(True, payload)


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
    lines = [item.strip() for item in text.splitlines() if item.strip()]
    line = next((item for item in lines if not item.startswith("WARNING:")), "")
    line = line.replace(str(Path.home()), "<home>")
    line = re.sub(r"[/\\]+[^ ]*", " <path>", line)
    line = re.sub(r"[^A-Za-z0-9 ._+():<>-]", " ", line)
    line = re.sub(r"\s+", " ", line).strip()[:120]
    return line or None


def _resolved_probe_seal(name: str, env_name: str) -> _ExecutableSeal | None:
    configured = os.environ.get(env_name)
    if configured is not None:
        return _seal_executable(configured)
    return _seal_executable(shutil.which(name))


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
    resolved: dict[str, _ExecutableSeal | None] = {}
    for name, env_name, _help_args, _version_args in TOOL_SPECS:
        configured = os.environ.get(env_name)
        resolved[name] = (
            _seal_executable(configured)
            if require_explicit_paths
            else _resolved_probe_seal(name, env_name)
        )
    probe_env = _probe_environment(
        isolation,
        tuple(seal.path for seal in resolved.values() if seal is not None),
    )
    for name, env_name, help_args, version_args in TOOL_SPECS:
        executable = resolved[name]
        unavailable = name.replace("-", "_") + "_unavailable"
        tool_ready = executable is not None
        version: str | None = None
        if executable is None:
            blockers.append(unavailable)
        else:
            tool_probe_env = dict(probe_env)
            if name == "codex":
                tool_probe_env["CODEX_HOME"] = str(
                    isolation.temporary / "codex-home"
                )
            version_probe = _bounded_probe(
                executable, version_args, cwd=project, env=tool_probe_env
            )
            if version_probe.blocker is not None:
                blockers.append(version_probe.blocker)
            version = (
                _sanitized_version(version_probe.output)
                if version_probe.ok
                else None
            )
            if version is None:
                blockers.append(unavailable)
                tool_ready = False
            if help_args is not None:
                help_probe = _bounded_probe(
                    executable, help_args, cwd=project, env=tool_probe_env
                )
                if help_probe.blocker is not None:
                    blockers.append(help_probe.blocker)
                required = (
                    {"--output-schema", "--output-last-message"}
                    if name == "codex"
                    else {"--json-schema", "--output-format"}
                )
                capability_blocker = f"{name}_native_schema_unavailable"
                if not help_probe.ok or not required.issubset(
                    _option_names(help_probe.output)
                ):
                    blockers.append(capability_blocker)
                    tool_ready = False
        tools.append(
            {
                "name": name,
                "executable_basename": (
                    executable.path.name if executable is not None else name
                ),
                "version": version,
                "ready": tool_ready,
            }
        )
    time.sleep(0.05)
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


@dataclass(frozen=True)
class _ProcessIdentity:
    pid: int
    pgid: int
    birth_fingerprint: str


@dataclass(frozen=True)
class _ProcessGroupScope:
    pgid: int
    leader: _ProcessIdentity


@dataclass(frozen=True)
class _FileIdentity:
    device: int
    inode: int
    owner: int
    mode: int
    size: int
    content_hash: str | None = None


@dataclass(frozen=True)
class _DaemonCleanupIdentity:
    metadata: tuple[tuple[str, object], ...]
    metadata_file: _FileIdentity
    socket_file: _FileIdentity
    process_scope: _ProcessGroupScope
    descendant_identities: tuple[_ProcessIdentity, ...]


@dataclass
class _LiveCleanupAuthority:
    process_scopes: list[_ProcessGroupScope]
    daemon: _DaemonCleanupIdentity | None = None


def _state_cardinalities_from_state(state: object) -> dict[str, int]:
    fields = (
        "plans",
        "missions",
        "mission_attempts",
        "permission_requests",
        "mission_handoffs",
        "mission_worker_replies",
    )
    if type(state) is not dict:
        return {field: -1 for field in fields}
    return {
        field: len(state.get(field, []))
        if type(state.get(field, [])) in {list, dict}
        else -1
        for field in fields
    }


def _closed_enum(value: object, allowed: frozenset[str]) -> str:
    return value if type(value) is str and value in allowed else "unknown"


def _dict_records(value: object) -> list[dict[str, object]] | None:
    if type(value) is not list or not all(type(item) is dict for item in value):
        return None
    return value


def _live_ledger_diagnostic(
    state: object,
    *,
    code: str,
    task_authority: dict[str, bool] | None,
) -> dict[str, object]:
    durable = state if type(state) is dict else {}
    mission_records = _dict_records(durable.get("missions"))
    attempt_records = _dict_records(durable.get("mission_attempts"))
    reply_records = _dict_records(durable.get("mission_worker_replies"))
    handoff_records = _dict_records(durable.get("mission_handoffs"))
    permission_records = _dict_records(durable.get("permission_requests"))
    collections_valid = all(
        records is not None
        for records in (
            mission_records,
            attempt_records,
            reply_records,
            handoff_records,
            permission_records,
        )
    )
    missions = mission_records or []
    attempts = attempt_records or []
    replies = reply_records or []
    handoffs = handoff_records or []
    permissions = permission_records or []
    attempt = attempts[-1] if attempts else {}

    def opaque_identity(value: object) -> str | None:
        return value if type(value) is str and value else None

    mission_id = opaque_identity(attempt.get("mission_id"))
    attempt_id = opaque_identity(attempt.get("attempt_id"))
    attempt_matches = (
        [
            item
            for item in attempts
            if opaque_identity(item.get("mission_id")) == mission_id
            and opaque_identity(item.get("attempt_id")) == attempt_id
        ]
        if mission_id is not None and attempt_id is not None
        else []
    )
    mission_matches = (
        [
            item
            for item in missions
            if opaque_identity(item.get("mission_id")) == mission_id
        ]
        if mission_id is not None
        else []
    )
    mission = mission_matches[0] if len(mission_matches) == 1 else {}
    reply_matches = (
        [
            item
            for item in replies
            if opaque_identity(item.get("mission_id")) == mission_id
            and opaque_identity(item.get("attempt_id")) == attempt_id
        ]
        if mission_id is not None and attempt_id is not None
        else []
    )
    reply = reply_matches[0] if len(reply_matches) == 1 else {}
    dispatch_keys_agree = True
    if "dispatch_key" in attempt and "dispatch_key" in reply:
        attempt_dispatch_key = opaque_identity(attempt.get("dispatch_key"))
        reply_dispatch_key = opaque_identity(reply.get("dispatch_key"))
        dispatch_keys_agree = (
            attempt_dispatch_key is not None
            and reply_dispatch_key is not None
            and attempt_dispatch_key == reply_dispatch_key
        )
    reply_id = opaque_identity(reply.get("reply_id"))
    handoff_matches = (
        [
            item
            for item in handoffs
            if opaque_identity(item.get("mission_id")) == mission_id
            and opaque_identity(item.get("attempt_id")) == attempt_id
            and opaque_identity(item.get("reply_id")) == reply_id
        ]
        if mission_id is not None
        and attempt_id is not None
        and reply_id is not None
        else []
    )
    handoff = handoff_matches[0] if len(handoff_matches) == 1 else {}
    lineage_valid = (
        mission_records is not None
        and attempt_records is not None
        and reply_records is not None
        and handoff_records is not None
        and mission_id is not None
        and attempt_id is not None
        and len(attempt_matches) == 1
        and len(mission_matches) == 1
        and len(reply_matches) == 1
        and dispatch_keys_agree
        and reply_id is not None
        and len(handoff_matches) == 1
    )
    if not lineage_valid:
        mission = {}
        reply = {}
        handoff = {}
    canonical_handoff = handoff.get("canonical_handoff")
    if type(canonical_handoff) is not dict:
        canonical_handoff = {}

    step_position = attempt.get("step_position")
    if type(step_position) is not int or not 1 <= step_position <= 4:
        step_id = attempt.get("step_id")
        match = re.fullmatch(r"step_([1-4])", step_id) if type(step_id) is str else None
        step_position = int(match.group(1)) if match is not None else 0

    mission_status = _closed_enum(mission.get("status"), _LIVE_MISSION_STATUSES)
    attempt_state = _closed_enum(attempt.get("state"), _LIVE_ATTEMPT_STATES)
    reply_state = _closed_enum(reply.get("state"), _LIVE_REPLY_STATES)
    handoff_state = _closed_enum(handoff.get("state"), _LIVE_HANDOFF_STATES)
    handoff_status = _closed_enum(
        canonical_handoff.get("status"), _LIVE_HANDOFF_STATUSES
    )
    permission_states = [
        _closed_enum(item.get("status"), _LIVE_PERMISSION_STATES)
        for item in permissions
    ]

    if (
        code == "native_schema_task_authority_invalid"
        and task_authority is not None
        and not all(task_authority.values())
    ):
        classification = "leader_task_authority_missing"
    elif not collections_valid or not lineage_valid:
        classification = "permission_state_inconsistent"
    elif permissions:
        classification = "permission_state_inconsistent"
    elif attempt_state in _LIVE_ACTIVE_ATTEMPT_STATES:
        classification = "worker_attempt_active"
    elif attempt_state in _LIVE_FAILED_ATTEMPT_STATES:
        classification = "worker_attempt_failed"
    elif (
        attempt_state in {"completed", "succeeded"}
        and reply_state == "validated"
        and handoff_state == "recorded"
        and handoff_status == "completed"
    ):
        classification = "worker_effect_not_requested"
    else:
        classification = "permission_state_inconsistent"
    assert classification in _LIVE_DIAGNOSTIC_CLASSIFICATIONS

    return {
        "classification": classification,
        "mission_status": mission_status,
        "step_position": step_position,
        "agent_id": _closed_enum(attempt.get("agent_id"), _LIVE_AGENT_IDS),
        "configured_transport": _closed_enum(
            attempt.get("configured_transport"), _LIVE_TRANSPORTS
        ),
        "attempt_state": attempt_state,
        "reply_state": reply_state,
        "handoff_state": handoff_state,
        "handoff_status": handoff_status,
        "permission_count": (
            len(permissions) if permission_records is not None else -1
        ),
        "permission_states": (
            permission_states if permission_records is not None else []
        ),
    }


def _live_task_authority_checks(steps: object) -> dict[str, bool]:
    exact_shape = (
        type(steps) is list
        and len(steps) == 4
        and all(type(step) is dict for step in steps)
    )
    if exact_shape:
        assert type(steps) is list
        tasks = tuple(
            step.get("task", "")
            if type(step.get("task")) is str
            else ""
            for step in steps
        )
        phase_tasks = tuple(task.lower() for task in tasks)
        agents = tuple(step.get("agent_id") for step in steps)
    else:
        tasks = ("", "", "", "")
        phase_tasks = tasks
        agents = ()
    return {
        "phase_order": exact_shape
        and all(
            phase in task
            for phase, task in zip(
                ("implementation", "review", "revision", "acceptance"),
                phase_tasks,
                strict=True,
            )
        ),
        "worker_order": exact_shape
        and agents
        == ("claude-worker", "codex-worker", "claude-worker", "codex-worker"),
        "artifact_all_steps": exact_shape
        and all("artifact.txt" in task for task in tasks),
        "implementation_draft": exact_shape and "draft-v1" in tasks[0],
        "review_target": exact_shape and "accepted-v2" in tasks[1],
        "revision_transition": exact_shape
        and "draft-v1" in tasks[2]
        and "accepted-v2" in tasks[2],
        "acceptance_target": exact_shape and "accepted-v2" in tasks[3],
    }


def _closed_task_authority(value: object) -> dict[str, bool] | None:
    if (
        type(value) is not dict
        or tuple(value) != _LIVE_TASK_AUTHORITY_FIELDS
        or not all(type(item) is bool for item in value.values())
    ):
        return None
    return {field: value[field] for field in _LIVE_TASK_AUTHORITY_FIELDS}


def _live_failure(
    code: str,
    *,
    store: StateStore | None = None,
    capture: _PtyTail | None = None,
    output: bytes | None = None,
    task_authority: object = None,
) -> _LiveHarnessFailure:
    diagnostic: dict[str, object] = {"stage": "live_acceptance", "code": code}
    closed_task_authority = _closed_task_authority(task_authority)
    if store is not None:
        state = store.load()
        diagnostic["cardinalities"] = _state_cardinalities_from_state(state)
        diagnostic["ledger"] = _live_ledger_diagnostic(
            state,
            code=code,
            task_authority=closed_task_authority,
        )
    if capture is not None:
        diagnostic["pty"] = capture.diagnostic()
    if output is not None:
        diagnostic["output"] = {
            "byte_count": len(output),
            "truncated": False,
            "sha256": hashlib.sha256(output).hexdigest(),
        }
    if closed_task_authority is not None:
        diagnostic["task_authority"] = closed_task_authority
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


def _require_live_task_authority(
    steps: object,
    *,
    store: StateStore,
    capture: _PtyTail,
) -> None:
    checks = _live_task_authority_checks(steps)
    if not all(checks.values()):
        raise _live_failure(
            "native_schema_task_authority_invalid",
            store=store,
            capture=capture,
            task_authority=checks,
        )


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
    tmux: _ExecutableSeal,
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
    _verify_executable_seal(tmux)
    code, output = _bounded_project_command(
        argv, cwd=cwd, timeout=10, env=env
    )
    _verify_executable_seal(tmux)
    if code != 0:
        raise _live_failure("pane_select_failed", output=output)
    verify_code, verify_output = _bounded_project_command(
        [
            *_sealed_argv(tmux),
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
    _verify_executable_seal(tmux)


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


def _drain_pty(
    master: int,
    capture: _PtyTail,
    *,
    max_bytes: int = 256 * 1024,
    max_chunks: int = 16,
    max_duration_seconds: float = 0.01,
    deadline: float | None = None,
) -> None:
    drained = 0
    chunks = 0
    drain_deadline = time.monotonic() + max_duration_seconds
    if deadline is not None:
        drain_deadline = min(drain_deadline, deadline)
    while (
        drained < max_bytes
        and chunks < max_chunks
        and time.monotonic() < drain_deadline
    ):
        readable, _, _ = select.select([master], [], [], 0)
        if not readable:
            return
        try:
            chunk = os.read(master, min(65536, max_bytes - drained))
        except OSError:
            return
        if not chunk:
            return
        capture.add(chunk)
        drained += len(chunk)
        chunks += 1


def _wait_for_pty_prompt(
    process: subprocess.Popen[bytes],
    master: int,
    capture: _PtyTail,
    count: int,
    *,
    timeout_seconds: float = 30,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        _drain_pty(master, capture, deadline=deadline)
        if capture.tail.count(b"agentdeck> ") >= count:
            return
        if process.poll() is not None:
            raise _live_failure("bare_pty_exited", capture=capture)
        time.sleep(0.05)
    raise _live_failure("bare_pty_prompt_timeout", capture=capture)


class _DarwinProcBSDInfo(ctypes.Structure):
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


_DARWIN_PROC_PIDINFO: Any = None
_DARWIN_PROC_PIDINFO_LOADED = False


def _darwin_process_info(pid: int) -> _DarwinProcBSDInfo | None:
    global _DARWIN_PROC_PIDINFO, _DARWIN_PROC_PIDINFO_LOADED
    try:
        if not _DARWIN_PROC_PIDINFO_LOADED:
            library_name = (
                ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib"
            )
            library = ctypes.CDLL(library_name, use_errno=True)
            _DARWIN_PROC_PIDINFO = library.proc_pidinfo
            _DARWIN_PROC_PIDINFO.argtypes = [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint64,
                ctypes.c_void_p,
                ctypes.c_int,
            ]
            _DARWIN_PROC_PIDINFO.restype = ctypes.c_int
            _DARWIN_PROC_PIDINFO_LOADED = True
        proc_pidinfo = _DARWIN_PROC_PIDINFO
        if proc_pidinfo is None:
            return None
        info = _DarwinProcBSDInfo()
        size = ctypes.sizeof(_DarwinProcBSDInfo)
        returned = proc_pidinfo(
            pid,
            3,
            0,
            ctypes.byref(info),
            size,
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    if returned != size or info.pbi_pid != pid:
        return None
    return info


def _kernel_process_birth(pid: int) -> str | None:
    if type(pid) is not int or pid <= 0:
        return None
    if sys.platform.startswith("linux"):
        try:
            payload = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
            remainder = payload[payload.rfind(")") + 2 :].split()
            start_ticks = int(remainder[19])
        except (OSError, UnicodeDecodeError, ValueError, IndexError):
            return None
        return f"linux:{start_ticks}"
    if sys.platform == "darwin":
        info = _darwin_process_info(pid)
        if info is None:
            return None
        return f"darwin:{info.pbi_start_tvsec}:{info.pbi_start_tvusec}"
    return None


def _kernel_process_uid(pid: int) -> int | None:
    if sys.platform.startswith("linux"):
        try:
            return os.stat(f"/proc/{pid}").st_uid
        except OSError:
            return None
    if sys.platform == "darwin":
        info = _darwin_process_info(pid)
        return int(info.pbi_uid) if info is not None else None
    return None


def _process_birth_fingerprint(
    pid: int,
    *,
    kernel_birth_provider: Any = _kernel_process_birth,
) -> str | None:
    birth = kernel_birth_provider(pid)
    uid = _kernel_process_uid(pid)
    try:
        pgid = os.getpgid(pid)
    except OSError:
        return None
    if type(birth) is not str or not birth or type(uid) is not int or pgid <= 1:
        return None
    payload = json.dumps(
        [pid, uid, pgid, birth],
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _seal_process_identity(
    pid: int,
    *,
    fingerprint_provider: Any = _process_birth_fingerprint,
) -> _ProcessIdentity:
    if type(pid) is not int or pid <= 1:
        raise _live_failure("process_cleanup_authority_unverified")
    try:
        pgid = os.getpgid(pid)
    except OSError:
        raise _live_failure("process_cleanup_authority_unverified") from None
    fingerprint = fingerprint_provider(pid)
    if type(fingerprint) is not str or not fingerprint:
        raise _live_failure("process_cleanup_authority_unverified")
    return _ProcessIdentity(pid=pid, pgid=pgid, birth_fingerprint=fingerprint)


def _seal_process_group(
    leader_pid: int,
    *,
    fingerprint_provider: Any = _process_birth_fingerprint,
) -> _ProcessGroupScope:
    leader = _seal_process_identity(
        leader_pid,
        fingerprint_provider=fingerprint_provider,
    )
    if leader.pgid <= 1:
        raise _live_failure("process_cleanup_authority_unverified")
    return _ProcessGroupScope(pgid=leader.pgid, leader=leader)


def _process_group_pids(pgid: int) -> set[int]:
    try:
        completed = subprocess.run(
            ["/bin/ps", "-axo", "pid=,pgid="],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    members: set[int] = set()
    for line in completed.stdout.decode("ascii", errors="ignore").splitlines():
        parts = line.split()
        if len(parts) == 2 and all(part.isdigit() for part in parts):
            pid, candidate_group = map(int, parts)
            if candidate_group == pgid and _process_alive(pid):
                members.add(pid)
    return members


def _process_identity_matches(
    identity: _ProcessIdentity,
    *,
    fingerprint_provider: Any = _process_birth_fingerprint,
) -> bool:
    if not _process_alive(identity.pid):
        return False
    try:
        current_group = os.getpgid(identity.pid)
    except OSError:
        return False
    return (
        current_group == identity.pgid
        and fingerprint_provider(identity.pid) == identity.birth_fingerprint
    )


def _process_scope_residual_count(scope: _ProcessGroupScope) -> int:
    return len(_process_group_pids(scope.pgid))


def _terminate_process_group(
    scope: _ProcessGroupScope,
    *,
    signal_group: Any = os.killpg,
    fingerprint_provider: Any = _process_birth_fingerprint,
) -> dict[str, object]:
    members = _process_group_pids(scope.pgid)
    if not members:
        return {"code": None, "residual_process_count": 0}
    leader_alive = _process_alive(scope.leader.pid)
    if leader_alive and not _process_identity_matches(
        scope.leader,
        fingerprint_provider=fingerprint_provider,
    ):
        return {
            "code": "process_cleanup_authority_unverified",
            "residual_process_count": len(members),
        }
    member_identities: list[_ProcessIdentity] = []
    for pid in sorted(members):
        try:
            member_identities.append(
                _seal_process_identity(pid, fingerprint_provider=fingerprint_provider)
            )
        except _LiveHarnessFailure:
            if _process_alive(pid):
                return {
                    "code": "process_cleanup_authority_unverified",
                    "residual_process_count": len(members),
                }
    failed = False
    for signal_number in (signal.SIGTERM, signal.SIGKILL):
        current_members = _process_group_pids(scope.pgid)
        if not current_members:
            break
        if not any(
            _process_identity_matches(
                item,
                fingerprint_provider=fingerprint_provider,
            )
            for item in member_identities
        ):
            return {
                "code": "process_cleanup_authority_unverified",
                "residual_process_count": len(current_members),
            }
        try:
            signal_group(scope.pgid, signal_number)
        except (OSError, PermissionError):
            failed = True
            break
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and _process_group_pids(scope.pgid):
            time.sleep(0.05)
    residual = _process_scope_residual_count(scope)
    return {
        "code": (
            "pty_process_group_cleanup_failed" if failed or residual else None
        ),
        "residual_process_count": residual,
    }


def _stop_pty(
    process: subprocess.Popen[bytes],
    master: int,
    scope: _ProcessGroupScope,
) -> list[str]:
    failures: list[str] = []
    try:
        cleanup = _terminate_process_group(scope)
        if cleanup["code"] is not None:
            failures.append(str(cleanup["code"]))
        with __import__("contextlib").suppress(subprocess.TimeoutExpired):
            process.wait(timeout=2)
    except (OSError, subprocess.SubprocessError):
        failures.append("pty_process_group_cleanup_failed")
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


def _all_process_pids() -> set[int] | None:
    try:
        completed = subprocess.run(
            ["/bin/ps", "-axo", "pid="],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return {
        int(item)
        for item in completed.stdout.decode("ascii", errors="ignore").split()
        if item.isdigit()
    }


def _darwin_process_environment(pid: int) -> dict[str, str] | None:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        sysctl = libc.sysctl
        sysctl.argtypes = [
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        sysctl.restype = ctypes.c_int
        mib = (ctypes.c_int * 3)(1, 49, pid)
        size = ctypes.c_size_t(0)
        if sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0:
            return None
        if size.value < 8 or size.value > 16 * 1024 * 1024:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        if sysctl(mib, 3, buffer, ctypes.byref(size), None, 0) != 0:
            return None
        raw = bytes(buffer.raw[: size.value])
        argc = int.from_bytes(raw[:4], byteorder=sys.byteorder, signed=True)
        if argc < 0 or argc > 1_000_000:
            return None
        index = raw.find(b"\0", 4)
        if index < 0:
            return None
        index += 1
        while index < len(raw) and raw[index] == 0:
            index += 1
        for _ in range(argc):
            end = raw.find(b"\0", index)
            if end < 0:
                return None
            index = end + 1
        environment: dict[str, str] = {}
        while index < len(raw):
            end = raw.find(b"\0", index)
            if end < 0 or end == index:
                break
            item = raw[index:end]
            if b"=" in item:
                key, value = item.split(b"=", 1)
                environment[key.decode("utf-8", errors="ignore")] = value.decode(
                    "utf-8", errors="ignore"
                )
            index = end + 1
        return environment
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _process_environment(pid: int) -> dict[str, str] | None:
    if sys.platform.startswith("linux"):
        try:
            raw = Path(f"/proc/{pid}/environ").read_bytes()
        except OSError:
            return None
        environment: dict[str, str] = {}
        for item in raw.split(b"\0"):
            if b"=" not in item:
                continue
            key, value = item.split(b"=", 1)
            environment[key.decode("utf-8", errors="ignore")] = value.decode(
                "utf-8", errors="ignore"
            )
        return environment
    if sys.platform == "darwin":
        return _darwin_process_environment(pid)
    return None


def _scan_probe_marker(
    marker: str,
    baseline_pids: set[int],
) -> tuple[_ProcessIdentity, ...] | None:
    pids = _all_process_pids()
    if pids is None:
        return None
    identities: list[_ProcessIdentity] = []
    for pid in sorted(pids - baseline_pids):
        if pid == os.getpid() or not _process_alive(pid):
            continue
        uid = _kernel_process_uid(pid)
        if uid != os.getuid():
            continue
        environment = _process_environment(pid)
        if environment is None:
            if _process_alive(pid):
                return None
            continue
        if environment.get("AGENTDECK_M2C_PROBE_SCOPE") != marker:
            continue
        try:
            identities.append(_seal_process_identity(pid))
        except _LiveHarnessFailure:
            if _process_alive(pid):
                return None
    return tuple(identities)


def _probe_descendant_pids(root_pid: int) -> set[int] | None:
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
        return None
    if completed.returncode != 0:
        return None
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


def _descendant_pids(root_pid: int) -> set[int]:
    return _probe_descendant_pids(root_pid) or set()


def _terminate_process_identities(
    identities: tuple[_ProcessIdentity, ...],
    *,
    signal_process: Any = os.kill,
    fingerprint_provider: Any = _process_birth_fingerprint,
) -> dict[str, object]:
    for signal_number in (signal.SIGTERM, signal.SIGKILL):
        active = [item for item in identities if _process_alive(item.pid)]
        if not active:
            break
        for identity in active:
            if not _process_identity_matches(
                identity,
                fingerprint_provider=fingerprint_provider,
            ):
                return {
                    "code": "process_cleanup_authority_unverified",
                    "residual_process_count": len(active),
                }
        failed = False
        for identity in active:
            try:
                signal_process(identity.pid, signal_number)
            except ProcessLookupError:
                continue
            except (OSError, PermissionError):
                failed = True
                break
        if failed:
            return {
                "code": "process_cleanup_authority_unverified",
                "residual_process_count": sum(
                    _process_alive(item.pid) for item in identities
                ),
            }
        deadline = time.monotonic() + 3
        while (
            time.monotonic() < deadline
            and any(_process_alive(item.pid) for item in active)
        ):
            time.sleep(0.05)
    residual = sum(_process_alive(item.pid) for item in identities)
    return {
        "code": "probe_residual_process" if residual else None,
        "residual_process_count": residual,
    }


def _read_daemon_authority_facts(
    root: Path,
) -> tuple[dict[str, object], _FileIdentity, _FileIdentity]:
    binding = None
    runtime_fd: int | None = None
    metadata_fd: int | None = None
    try:
        binding = bind_daemon_endpoint(root)
        binding.assert_runtime_identity()
        runtime_fd = binding.duplicate_runtime_fd()
        metadata_fd = os.open(
            "daemon.json",
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=runtime_fd,
        )
        metadata_stat = os.fstat(metadata_fd)
        metadata_mode = stat.S_IMODE(metadata_stat.st_mode)
        if (
            not stat.S_ISREG(metadata_stat.st_mode)
            or metadata_stat.st_uid != os.getuid()
            or metadata_mode & 0o077
            or metadata_stat.st_size < 2
            or metadata_stat.st_size > 64 * 1024
        ):
            raise _live_failure("daemon_cleanup_authority_unverified")
        payload = b""
        while len(payload) <= 64 * 1024:
            chunk = os.read(metadata_fd, min(8192, 64 * 1024 + 1 - len(payload)))
            if not chunk:
                break
            payload += chunk
        if len(payload) > 64 * 1024 or len(payload) != metadata_stat.st_size:
            raise _live_failure("daemon_cleanup_authority_unverified")
        decoded = json.loads(payload)
        if (
            type(decoded) is not dict
            or set(decoded) != {
                "instance_id",
                "project_root_hash",
                "start_nonce_hash",
                "pid",
            }
            or type(decoded["instance_id"]) is not str
            or not str(decoded["instance_id"]).startswith("dmn_")
            or decoded["project_root_hash"] != project_root_hash(root)
            or type(decoded["start_nonce_hash"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", str(decoded["start_nonce_hash"]))
            is None
            or type(decoded["pid"]) is not int
            or int(decoded["pid"]) <= 1
        ):
            raise _live_failure("daemon_cleanup_authority_unverified")
        socket_stat = os.stat(
            "daemon.sock",
            dir_fd=runtime_fd,
            follow_symlinks=False,
        )
        socket_mode = stat.S_IMODE(socket_stat.st_mode)
        if (
            not stat.S_ISSOCK(socket_stat.st_mode)
            or socket_stat.st_uid != os.getuid()
            or socket_mode & 0o077
        ):
            raise _live_failure("daemon_cleanup_authority_unverified")
        binding.assert_runtime_identity()
        return (
            dict(decoded),
            _FileIdentity(
                device=metadata_stat.st_dev,
                inode=metadata_stat.st_ino,
                owner=metadata_stat.st_uid,
                mode=metadata_mode,
                size=metadata_stat.st_size,
                content_hash=hashlib.sha256(payload).hexdigest(),
            ),
            _FileIdentity(
                device=socket_stat.st_dev,
                inode=socket_stat.st_ino,
                owner=socket_stat.st_uid,
                mode=socket_mode,
                size=socket_stat.st_size,
            ),
        )
    except _LiveHarnessFailure:
        raise
    except (
        DaemonIdentityError,
        FileNotFoundError,
        NotADirectoryError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        raise _live_failure("daemon_cleanup_authority_unverified") from None
    finally:
        if metadata_fd is not None:
            os.close(metadata_fd)
        if runtime_fd is not None:
            os.close(runtime_fd)
        if binding is not None:
            binding.close()


async def _verified_daemon_handshake(
    root: Path, metadata: dict[str, object]
) -> bool | None:
    client: DaemonClient | None = None
    try:
        client = await DaemonClient.connect_verified(root, timeout_seconds=5)
        return client.instance_id == metadata["instance_id"]
    except Exception:
        return None
    finally:
        if client is not None:
            await client.close()


def _default_daemon_handshake(
    root: Path, metadata: dict[str, object]
) -> bool | None:
    return asyncio.run(_verified_daemon_handshake(root, metadata))


def _seal_daemon_cleanup_identity(
    root: Path,
    *,
    handshake_verifier: Any | None = None,
    fingerprint_provider: Any = _process_birth_fingerprint,
) -> _DaemonCleanupIdentity:
    verifier = handshake_verifier or (
        lambda metadata: _default_daemon_handshake(root, metadata)
    )
    metadata, metadata_file, socket_file = _read_daemon_authority_facts(root)
    if verifier(metadata) is not True:
        raise _live_failure("daemon_cleanup_authority_unverified")
    repeated = _read_daemon_authority_facts(root)
    if repeated != (metadata, metadata_file, socket_file):
        raise _live_failure("daemon_cleanup_authority_unverified")
    process_scope = _seal_process_group(
        int(metadata["pid"]),
        fingerprint_provider=fingerprint_provider,
    )
    if process_scope.pgid != process_scope.leader.pid:
        raise _live_failure("daemon_cleanup_authority_unverified")
    descendants: list[_ProcessIdentity] = []
    for pid in sorted(_descendant_pids(process_scope.leader.pid)):
        try:
            descendants.append(
                _seal_process_identity(pid, fingerprint_provider=fingerprint_provider)
            )
        except _LiveHarnessFailure:
            if _process_alive(pid):
                raise _live_failure("daemon_cleanup_authority_unverified") from None
    return _DaemonCleanupIdentity(
        metadata=tuple(sorted(metadata.items())),
        metadata_file=metadata_file,
        socket_file=socket_file,
        process_scope=process_scope,
        descendant_identities=tuple(descendants),
    )


def _daemon_cleanup_authority_matches(
    root: Path,
    identity: _DaemonCleanupIdentity,
    *,
    handshake_verifier: Any | None = None,
    fingerprint_provider: Any = _process_birth_fingerprint,
) -> bool:
    try:
        metadata, metadata_file, socket_file = _read_daemon_authority_facts(root)
    except _LiveHarnessFailure:
        return False
    if (
        tuple(sorted(metadata.items())) != identity.metadata
        or metadata_file != identity.metadata_file
        or socket_file != identity.socket_file
        or not _process_identity_matches(
            identity.process_scope.leader,
            fingerprint_provider=fingerprint_provider,
        )
    ):
        return False
    if any(
        _process_alive(item.pid)
        and not _process_identity_matches(
            item,
            fingerprint_provider=fingerprint_provider,
        )
        for item in identity.descendant_identities
    ):
        return False
    try:
        if os.getpgid(identity.process_scope.leader.pid) != identity.process_scope.pgid:
            return False
    except OSError:
        return False
    verifier = handshake_verifier or (
        lambda current: _default_daemon_handshake(root, current)
    )
    handshake = verifier(metadata)
    return handshake is not False


def _terminate_daemon_tree(
    identity: _DaemonCleanupIdentity,
    *,
    fingerprint_provider: Any = _process_birth_fingerprint,
    signal_process: Any = os.kill,
    signal_group: Any = os.killpg,
) -> dict[str, object]:
    descendants: list[_ProcessIdentity] = []
    for pid in sorted(_descendant_pids(identity.process_scope.leader.pid)):
        try:
            descendants.append(
                _seal_process_identity(pid, fingerprint_provider=fingerprint_provider)
            )
        except _LiveHarnessFailure:
            if _process_alive(pid):
                return {
                    "code": "daemon_cleanup_authority_unverified",
                    "residual_process_count": _process_scope_residual_count(
                        identity.process_scope
                    )
                    + 1,
                }
    failed = False
    for signal_number in (signal.SIGTERM, signal.SIGKILL):
        for process_identity in reversed(descendants):
            if _process_identity_matches(
                process_identity,
                fingerprint_provider=fingerprint_provider,
            ):
                try:
                    signal_process(process_identity.pid, signal_number)
                except (OSError, PermissionError):
                    failed = True
        if _process_group_pids(identity.process_scope.pgid):
            try:
                signal_group(identity.process_scope.pgid, signal_number)
            except (OSError, PermissionError):
                failed = True
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            group_residual = _process_scope_residual_count(identity.process_scope)
            descendant_residual = sum(
                _process_identity_matches(
                    item,
                    fingerprint_provider=fingerprint_provider,
                )
                for item in descendants
                if item.pgid != identity.process_scope.pgid
            )
            if group_residual + descendant_residual == 0:
                break
            time.sleep(0.05)
    residual = _process_scope_residual_count(identity.process_scope) + sum(
        _process_identity_matches(item, fingerprint_provider=fingerprint_provider)
        for item in descendants
        if item.pgid != identity.process_scope.pgid
    )
    return {
        "code": "daemon_process_group_cleanup_failed" if failed or residual else None,
        "residual_process_count": residual,
    }


def _stop_or_terminate_daemon(
    *,
    root: Path,
    identity: _DaemonCleanupIdentity,
    stop_daemon: Any,
    handshake_verifier: Any | None = None,
    fingerprint_provider: Any = _process_birth_fingerprint,
    signal_process: Any | None = None,
) -> dict[str, object]:
    fallback_used = False
    try:
        stop_daemon()
    except Exception:
        fallback_used = True
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _process_scope_residual_count(
        identity.process_scope
    ):
        if fallback_used:
            break
        time.sleep(0.05)
    residual = _process_scope_residual_count(identity.process_scope)
    if residual:
        fallback_used = True
        if not _daemon_cleanup_authority_matches(
            root,
            identity,
            handshake_verifier=handshake_verifier,
            fingerprint_provider=fingerprint_provider,
        ):
            return {
                "code": "daemon_cleanup_authority_unverified",
                "fallback_used": True,
                "residual_process_count": residual,
            }
        cleanup = _terminate_daemon_tree(
            identity,
            fingerprint_provider=fingerprint_provider,
            signal_process=signal_process or os.kill,
            signal_group=(
                (lambda pgid, sig: signal_process(-pgid, sig))
                if signal_process is not None
                else os.killpg
            ),
        )
        residual = int(cleanup["residual_process_count"])
        if cleanup["code"] is not None or residual:
            return {
                "code": str(cleanup["code"] or "daemon_process_group_cleanup_failed"),
                "fallback_used": True,
                "residual_process_count": residual,
            }
    return {
        "code": None,
        "fallback_used": fallback_used,
        "residual_process_count": residual,
    }


def _cleanup_exact_tmux(
    *,
    tmux: _ExecutableSeal,
    socket_name: str,
    session_name: str,
    cwd: Path,
    env: dict[str, str],
    socket_paths: tuple[Path, ...],
) -> dict[str, object]:
    try:
        _bounded_project_command(
            _sealed_argv(
                tmux, "-L", socket_name, "kill-session", "-t", session_name
            ),
            cwd=cwd,
            timeout=10,
            env=env,
        )
        _verify_executable_seal(tmux)
        _bounded_project_command(
            _sealed_argv(tmux, "-L", socket_name, "kill-server"),
            cwd=cwd,
            timeout=10,
            env=env,
        )
        _verify_executable_seal(tmux)
        code, _output = _bounded_project_command(
            _sealed_argv(
                tmux, "-L", socket_name, "has-session", "-t", session_name
            ),
            cwd=cwd,
            timeout=10,
            env=env,
        )
    except _LiveHarnessFailure:
        return {
            "reachable": True,
            "socket_paths_present": sum(path.exists() for path in socket_paths),
            "identity_drift": True,
        }
    result: dict[str, object] = {
        "reachable": code == 0,
        "socket_paths_present": sum(path.exists() for path in socket_paths),
    }
    try:
        _verify_executable_seal(tmux)
    except _LiveHarnessFailure:
        result["identity_drift"] = True
    return result


def _derive_residual_audit(
    *,
    tracked_pids: set[int] | None = None,
    tracked_process_scopes: tuple[_ProcessGroupScope, ...] = (),
    endpoint_paths: tuple[Path, ...],
    tmux_reachable: bool,
    tmux_socket_paths: tuple[Path, ...],
) -> dict[str, object]:
    process_count = sum(_process_alive(pid) for pid in (tracked_pids or set()))
    process_count += sum(
        _process_scope_residual_count(scope) for scope in tracked_process_scopes
    )
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


def _remove_sealed_daemon_endpoint(
    root: Path, identity: _DaemonCleanupIdentity
) -> bool:
    binding = None
    runtime_fd: int | None = None
    metadata_fd: int | None = None
    try:
        metadata, metadata_file, socket_file = _read_daemon_authority_facts(root)
        if (
            tuple(sorted(metadata.items())) != identity.metadata
            or metadata_file != identity.metadata_file
            or socket_file != identity.socket_file
        ):
            return False
        binding = bind_daemon_endpoint(root)
        binding.assert_runtime_identity()
        if not binding.unlink_socket_if_identity(
            (identity.socket_file.device, identity.socket_file.inode)
        ):
            return False
        runtime_fd = binding.duplicate_runtime_fd()
        metadata_fd = os.open(
            "daemon.json",
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=runtime_fd,
        )
        current = os.fstat(metadata_fd)
        if (
            current.st_dev,
            current.st_ino,
            current.st_uid,
            stat.S_IMODE(current.st_mode),
            current.st_size,
        ) != (
            identity.metadata_file.device,
            identity.metadata_file.inode,
            identity.metadata_file.owner,
            identity.metadata_file.mode,
            identity.metadata_file.size,
        ):
            return False
        os.unlink("daemon.json", dir_fd=runtime_fd)
        return True
    except (_LiveHarnessFailure, DaemonIdentityError, OSError):
        return False
    finally:
        if metadata_fd is not None:
            os.close(metadata_fd)
        if runtime_fd is not None:
            os.close(runtime_fd)
        if binding is not None:
            binding.close()


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _write_live_config(
    root: Path, paths: dict[str, _ExecutableSeal], *, session_name: str
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
command = {_toml_string(str(paths["claude"].path))}
transport = "acp"
transport_command = [{_toml_string(str(paths["claude-agent-acp"].path))}]
workspace_mode = "shared"
role_prompt = "Implement only the exact frozen Mission task. Request edit permission before changing artifact.txt. Return one compact AgentDeck handoff."

[[agents]]
agent_id = "codex-worker"
role = "review"
provider = "codex"
command = {_toml_string(str(paths["codex"].path))}
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


def _explicit_live_paths() -> dict[str, _ExecutableSeal]:
    paths: dict[str, _ExecutableSeal] = {}
    for name, env_name, _help, _version in TOOL_SPECS:
        path = _seal_executable(os.environ.get(env_name))
        if path is None or path.path.name != name:
            raise _live_failure(f"{name.replace('-', '_')}_path_invalid")
        paths[name] = path
    return paths


def _create_and_confirm_live_mission(
    root: Path,
    store: StateStore,
    *,
    env: dict[str, str],
    cleanup_authority: _LiveCleanupAuthority | None = None,
    executable_seals: dict[str, _ExecutableSeal] | None = None,
    openpty_factory: Any = pty.openpty,
    popen_factory: Any = subprocess.Popen,
    scope_sealer: Any = None,
) -> tuple[str, _PtyTail, dict[str, object]]:
    if _kernel_process_birth(os.getpid()) is None or _kernel_process_uid(os.getpid()) is None:
        raise _live_failure("process_cleanup_authority_unverified", store=store)
    scope_sealer = scope_sealer or _seal_process_group
    capture = _PtyTail()
    master: int | None = None
    slave: int | None = None
    process: subprocess.Popen[bytes] | None = None
    process_scope: _ProcessGroupScope | None = None
    try:
        if executable_seals is not None:
            _verify_all_executable_seals(executable_seals)
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
            process_scope = scope_sealer(process.pid)
            if cleanup_authority is not None:
                cleanup_authority.process_scopes.append(process_scope)
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
        _require_live_task_authority(steps, store=store, capture=capture)
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
        if process is not None and master is not None and process_scope is not None:
            failures.extend(_stop_pty(process, master, process_scope))
            master = None
        elif process is not None:
            if process.poll() is None:
                failures.append("pty_process_scope_unverified")
            else:
                with __import__("contextlib").suppress(
                    subprocess.SubprocessError
                ):
                    process.wait(timeout=0)
        if master is not None:
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
        if executable_seals is not None:
            _verify_all_executable_seals(executable_seals)


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
    tmux: _ExecutableSeal,
    *,
    session_name: str,
    cleanup_env: dict[str, str],
    tmux_socket_paths: tuple[Path, ...],
    cleanup_authority: _LiveCleanupAuthority,
    executable_seals: dict[str, _ExecutableSeal],
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
        for seal in executable_seals.values():
            try:
                _verify_executable_seal(seal)
            except _LiveHarnessFailure:
                cleanup_failures.append("executable_identity_drift")
        endpoint = daemon_endpoint(root)
        endpoint_present = endpoint.socket_path.exists() or endpoint.metadata_path.exists()
        if endpoint_present and cleanup_authority.daemon is None:
            try:
                cleanup_authority.daemon = _seal_daemon_cleanup_identity(root)
            except _LiveHarnessFailure:
                cleanup_failures.append("daemon_cleanup_authority_unverified")
        daemon_identity = cleanup_authority.daemon
        if daemon_identity is not None:
            daemon_cleanup = _stop_or_terminate_daemon(
                root=root,
                identity=daemon_identity,
                stop_daemon=lambda: asyncio.run(_stop_live_daemon(root)),
            )
            if daemon_cleanup["code"] is not None:
                cleanup_failures.append(str(daemon_cleanup["code"]))
            elif (
                (endpoint.socket_path.exists() or endpoint.metadata_path.exists())
                and not _remove_sealed_daemon_endpoint(root, daemon_identity)
            ):
                cleanup_failures.append("daemon_endpoint_cleanup_failed")
        elif endpoint.socket_path.exists() or endpoint.metadata_path.exists():
            cleanup_failures.append("daemon_cleanup_authority_unverified")
        for scope in cleanup_authority.process_scopes:
            process_cleanup = _terminate_process_group(scope)
            if process_cleanup["code"] is not None:
                cleanup_failures.append(str(process_cleanup["code"]))
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
        if tmux_cleanup.get("identity_drift") is True:
            cleanup_failures.append("executable_identity_drift")
        try:
            shutil.rmtree(parent)
        except OSError:
            cleanup_failures.append("project_cleanup_failed")
        if parent.exists():
            cleanup_failures.append("project_retained")
        audit.update(
            _derive_residual_audit(
                tracked_process_scopes=tuple(
                    cleanup_authority.process_scopes
                    + ([daemon_identity.process_scope] if daemon_identity else [])
                ),
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


def _remove_live_setup_parent(parent: Path) -> bool:
    try:
        if parent.exists():
            shutil.rmtree(parent)
        return parent.exists()
    except BaseException:
        return True


def _run_live_acceptance_in_project(
    paths: dict[str, _ExecutableSeal], parent: Path
) -> dict[str, object]:
    try:
        return _run_live_acceptance_in_project_guarded(paths, parent)
    except BaseException as exc:
        cleanup_failed = _remove_live_setup_parent(parent)
        if isinstance(exc, _LiveHarnessFailure) or not isinstance(exc, Exception):
            if cleanup_failed:
                exc.add_note(
                    json.dumps(
                        {
                            "stage": "live_setup_cleanup",
                            "codes": ["live_setup_cleanup_failed"],
                        },
                        sort_keys=True,
                    )
                )
            raise
        raise _live_failure(
            "live_setup_cleanup_failed" if cleanup_failed else "live_setup_failed"
        ) from None


def _run_live_acceptance_in_project_guarded(
    paths: dict[str, _ExecutableSeal], parent: Path
) -> dict[str, object]:
    source_paths = paths
    root = parent / "repo"
    root.mkdir(mode=0o700)
    session_name = "agentdeck-m2c-" + parent.name[-8:]
    artifact = root / "artifact.txt"
    tmux_temporary = parent / "tmux-tmp"
    tmux_temporary.mkdir(mode=0o700)
    runtime_bin = parent / "runtime-bin"
    runtime_bin.mkdir(mode=0o700)
    launch_paths = {
        name: _write_controlled_launcher(name, source, runtime_bin)
        for name, source in source_paths.items()
    }
    all_seals = {
        **{f"source:{name}": seal for name, seal in source_paths.items()},
        **{f"launcher:{name}": seal for name, seal in launch_paths.items()},
    }
    tmux_socket_paths = (
        tmux_temporary / f"tmux-{os.getuid()}" / session_name,
    )
    cleanup_env = {
        "HOME": str(parent),
        "TMPDIR": str(parent),
        "TMUX_TMPDIR": str(tmux_temporary),
        "PATH": str(runtime_bin) + os.pathsep + "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
    }
    cleanup_authority = _LiveCleanupAuthority(process_scopes=[])
    with _live_resource_guard(
        root,
        parent,
        launch_paths["tmux"],
        session_name=session_name,
        cleanup_env=cleanup_env,
        tmux_socket_paths=tmux_socket_paths,
        cleanup_authority=cleanup_authority,
        executable_seals=all_seals,
    ) as cleanup_audit:
        _verify_all_executable_seals(all_seals)
        preflight_before = _tree_snapshot(root)
        preflight = _live_preflight(root, require_explicit_paths=True)
        _require_live(_tree_snapshot(root) == preflight_before, "preflight_wrote_project")
        _require_live(preflight["ready"] is True, "preflight_blocked")
        checkout = Path(__file__).resolve().parents[1]
        _require_live(
            checkout not in root.parents and root not in checkout.parents,
            "project_not_disposable",
        )
        paths = launch_paths
        _verify_all_executable_seals(all_seals)
        env = dict(os.environ)
        env["PATH"] = str(runtime_bin) + os.pathsep + env.get("PATH", "")
        env["TMUX_TMPDIR"] = str(tmux_temporary)
        _verify_all_executable_seals(all_seals)
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
        _verify_all_executable_seals(all_seals)
        _write_live_config(root, paths, session_name=session_name)
        _verify_all_executable_seals(all_seals)
        store = StateStore(root)
        mission_id, capture, admitted = _create_and_confirm_live_mission(
            root,
            store,
            env=env,
            cleanup_authority=cleanup_authority,
            executable_seals=all_seals,
        )
        cleanup_authority.daemon = _seal_daemon_cleanup_identity(root)
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
        _verify_all_executable_seals(all_seals)
        _confirm_pending_permission(root, store)
        _verify_all_executable_seals(all_seals)
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
        _verify_all_executable_seals(all_seals)
        _observe_exact_pane(
            pane_controls[0],
            tmux=paths["tmux"],
            socket_name=config.runtime.socket_name,
            cwd=root,
            env=env,
        )
        _verify_all_executable_seals(all_seals)
        taken = asyncio.run(
            _govern_live_worker(root, method="worker.takeover")
        )
        _require_live(
            taken.get("ownership") == "human_owned",
            "takeover_failed",
            store=store,
        )
        _verify_all_executable_seals(all_seals)
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
        _verify_all_executable_seals(all_seals)
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
        _verify_all_executable_seals(paths)
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


def test_live_task_authority_accepts_only_exact_fixed_scenario() -> None:
    checks = _live_task_authority_checks(_exact_live_steps())

    assert tuple(checks) == _LIVE_TASK_AUTHORITY_FIELDS
    assert checks == {field: True for field in _LIVE_TASK_AUTHORITY_FIELDS}


@pytest.mark.parametrize(
    ("step_index", "old", "new", "failed_check"),
    [
        (0, "draft-v1", "initial-draft", "implementation_draft"),
        (1, "accepted-v2", "reviewed-v2", "review_target"),
        (2, "draft-v1", "initial-draft", "revision_transition"),
        (2, "accepted-v2", "reviewed-v2", "revision_transition"),
        (3, "accepted-v2", "reviewed-v2", "acceptance_target"),
    ],
)
def test_live_task_authority_rejects_missing_fixed_token(
    step_index, old, new, failed_check,
) -> None:
    steps = _exact_live_steps()
    steps[step_index]["task"] = steps[step_index]["task"].replace(old, new)

    checks = _live_task_authority_checks(steps)

    assert checks[failed_check] is False
    assert all(
        value is True for name, value in checks.items() if name != failed_check
    )


@pytest.mark.parametrize("step_index", range(4))
def test_live_task_authority_rejects_missing_artifact_at_each_step(
    step_index,
) -> None:
    steps = _exact_live_steps()
    steps[step_index]["task"] = steps[step_index]["task"].replace(
        "artifact.txt", "artifact.bin"
    )

    checks = _live_task_authority_checks(steps)

    assert checks["artifact_all_steps"] is False
    assert all(
        value is True
        for name, value in checks.items()
        if name != "artifact_all_steps"
    )


@pytest.mark.parametrize(
    ("step_index", "phase"),
    tuple(enumerate(("implementation", "review", "revision", "acceptance"))),
)
def test_live_task_authority_rejects_phase_drift_at_each_step(
    step_index, phase,
) -> None:
    steps = _exact_live_steps()
    steps[step_index]["task"] = steps[step_index]["task"].replace(phase, "drift")

    checks = _live_task_authority_checks(steps)

    assert checks["phase_order"] is False
    assert all(value is True for name, value in checks.items() if name != "phase_order")


@pytest.mark.parametrize("step_index", range(4))
def test_live_task_authority_rejects_worker_drift_at_each_step(step_index) -> None:
    steps = _exact_live_steps()
    steps[step_index]["agent_id"] = (
        "codex-worker"
        if steps[step_index]["agent_id"] == "claude-worker"
        else "claude-worker"
    )

    checks = _live_task_authority_checks(steps)

    assert checks["worker_order"] is False
    assert all(value is True for name, value in checks.items() if name != "worker_order")


@pytest.mark.parametrize(
    ("step_index", "token", "failed_check"),
    [
        (0, "artifact.txt", "artifact_all_steps"),
        (0, "draft-v1", "implementation_draft"),
        (3, "accepted-v2", "acceptance_target"),
    ],
)
def test_live_task_authority_rejects_uppercase_fixed_token(
    step_index, token, failed_check,
) -> None:
    steps = _exact_live_steps()
    steps[step_index]["task"] = steps[step_index]["task"].replace(
        token, token.upper()
    )

    checks = _live_task_authority_checks(steps)

    assert checks[failed_check] is False
    assert all(
        value is True for name, value in checks.items() if name != failed_check
    )


def test_require_live_task_authority_is_preconfirmation_zero_write(tmp_path) -> None:
    store = StateStore(tmp_path)
    steps = _exact_live_steps()
    steps[2]["task"] = steps[2]["task"].replace("accepted-v2", "reviewed-v2")
    before = _tree_snapshot(tmp_path)

    with pytest.raises(_LiveHarnessFailure) as error:
        _require_live_task_authority(steps, store=store, capture=_PtyTail())

    diagnostic = json.loads(str(error.value))
    assert diagnostic["code"] == "native_schema_task_authority_invalid"
    assert diagnostic["task_authority"]["revision_transition"] is False
    assert set(diagnostic["task_authority"]) == set(_LIVE_TASK_AUTHORITY_FIELDS)
    assert _tree_snapshot(tmp_path) == before
    state = store.load()
    assert state["mission_attempts"] == []
    assert state["permission_requests"] == []


def test_live_mission_task_authority_blocks_real_confirmation_path(
    tmp_path, monkeypatch,
) -> None:
    store = StateStore(tmp_path)
    store.load()
    store.all_events()
    actual_state_before = copy.deepcopy(store.load())
    actual_events_before = copy.deepcopy(store.all_events())
    project_tree_before = _tree_snapshot(tmp_path)
    steps = _exact_live_steps()
    steps[2]["task"] = steps[2]["task"].replace("accepted-v2", "ACCEPTED-V2")
    previewed = {
        "missions": [{"mission_id": "msn_task_authority"}],
        "plans": [
            {
                "plan": {"steps": steps},
                "leader_generation": {"constraint_mode": "native_json_schema"},
            }
        ],
        "conversation_preview_bindings": [{"mission_id": "msn_task_authority"}],
        "mission_attempts": [],
        "permission_requests": [],
    }
    before = copy.deepcopy(previewed)
    prompt_numbers: list[int] = []
    writes: list[bytes] = []

    class FakeProcess:
        pid = 987654

    def fake_wait_for_state(_store, predicate, **_kwargs):
        assert predicate(previewed)
        return previewed

    def fake_wait_for_prompt(_process, _master, _capture, prompt_number):
        prompt_numbers.append(prompt_number)

    def fake_write(_descriptor, payload):
        writes.append(payload)
        return len(payload)

    def fake_stop_pty(_process, master, _scope):
        os.close(master)
        return []

    read_fd, write_fd = os.pipe()
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "_wait_for_state", fake_wait_for_state)
    monkeypatch.setattr(module, "_wait_for_pty_prompt", fake_wait_for_prompt)
    monkeypatch.setattr(module, "_stop_pty", fake_stop_pty)
    monkeypatch.setattr(os, "write", fake_write)

    with pytest.raises(_LiveHarnessFailure) as error:
        _create_and_confirm_live_mission(
            tmp_path,
            store,
            env={},
            openpty_factory=lambda: (read_fd, write_fd),
            popen_factory=lambda *_args, **_kwargs: FakeProcess(),
            scope_sealer=lambda _pid: _ProcessGroupScope(
                pgid=987654,
                leader=_ProcessIdentity(
                    pid=987654,
                    pgid=987654,
                    birth_fingerprint="fixed-birth",
                ),
            ),
        )

    diagnostic = json.loads(str(error.value))
    assert diagnostic["code"] == "native_schema_task_authority_invalid"
    assert prompt_numbers == [1]
    assert len(writes) == 1
    assert writes[0].endswith("共4轮。\n".encode("utf-8"))
    assert "确认执行当前预览".encode("utf-8") not in writes[0]
    assert previewed == before
    actual_state_after = store.load()
    actual_events_after = store.all_events()
    project_tree_after = _tree_snapshot(tmp_path)
    assert actual_state_after == actual_state_before
    assert actual_events_after == actual_events_before
    assert project_tree_after == project_tree_before
    assert actual_state_after["mission_attempts"] == []
    assert actual_state_after["permission_requests"] == []


def test_live_failure_rejects_nonclosed_task_authority_without_stringifying() -> None:
    class RejectedValue:
        def __str__(self) -> str:
            raise AssertionError("rejected value was stringified")

        def __repr__(self) -> str:
            raise AssertionError("rejected value was stringified")

    rejected = {field: True for field in _LIVE_TASK_AUTHORITY_FIELDS}
    rejected["revision_transition"] = RejectedValue()

    diagnostic = json.loads(
        str(_live_failure("native_schema_task_authority_invalid", task_authority=rejected))
    )

    assert "task_authority" not in diagnostic


def test_closed_task_authority_requires_exact_key_order_and_bools() -> None:
    accepted = {field: True for field in _LIVE_TASK_AUTHORITY_FIELDS}
    reordered = dict(reversed(tuple(accepted.items())))
    integer_value = dict(accepted)
    integer_value["phase_order"] = 1

    assert _closed_task_authority(accepted) == accepted
    assert _closed_task_authority(reordered) is None
    assert _closed_task_authority(integer_value) is None


class _StaticLiveStore:
    def __init__(self, state: dict[str, object], *, reject_second_load: bool = False):
        self.state = state
        self.reject_second_load = reject_second_load
        self.load_count = 0

    def load(self) -> dict[str, object]:
        self.load_count += 1
        if self.reject_second_load and self.load_count > 1:
            raise AssertionError("live failure loaded durable state more than once")
        return self.state


def _live_diagnostic_state(
    *,
    mission_status: object = "running",
    attempt_state: object = "succeeded",
    reply_state: object = "validated",
    handoff_state: object = "recorded",
    handoff_status: object = "completed",
    permissions: object = None,
) -> dict[str, object]:
    mission_id = "mis_secret_mission"
    attempt_id = "mat_secret_attempt"
    reply_id = "mrp_secret_reply"
    dispatch_key = "dsp_secret_dispatch"
    secret = "SECRET model output /Users/private/.credentials"
    return {
        "plans": [{"summary": secret}],
        "missions": [
            {
                "mission_id": mission_id,
                "status": mission_status,
                "goal": secret,
            }
        ],
        "mission_attempts": [
            {
                "mission_id": mission_id,
                "attempt_id": attempt_id,
                "step_id": "step_1",
                "agent_id": "claude-worker",
                "configured_transport": "acp",
                "state": attempt_state,
                "dispatch_key": dispatch_key,
                "blocker": secret,
                "terminal_reason": secret,
                "task": secret,
                "prompt": secret,
            }
        ],
        "mission_worker_replies": [
            {
                "mission_id": mission_id,
                "attempt_id": attempt_id,
                "reply_id": reply_id,
                "dispatch_key": dispatch_key,
                "state": reply_state,
                "summary": secret,
                "acp_update": secret,
                "provider_output": secret,
                "pty_text": secret,
            }
        ],
        "mission_handoffs": [
            {
                "mission_id": mission_id,
                "attempt_id": attempt_id,
                "reply_id": reply_id,
                "state": handoff_state,
                "canonical_handoff": {
                    "status": handoff_status,
                    "summary": secret,
                    "verification": secret,
                    "risks": secret,
                    "next_steps": secret,
                },
            }
        ],
        "permission_requests": (
            [] if permissions is None else permissions
        ),
    }


_LIVE_LEDGER_KEYS = {
    "classification",
    "mission_status",
    "step_position",
    "agent_id",
    "configured_transport",
    "attempt_state",
    "reply_state",
    "handoff_state",
    "handoff_status",
    "permission_count",
    "permission_states",
}


def test_live_diagnostic_classifications_are_exactly_locked() -> None:
    assert globals().get("_LIVE_DIAGNOSTIC_CLASSIFICATIONS") == frozenset(
        {
            "leader_task_authority_missing",
            "worker_effect_not_requested",
            "worker_attempt_failed",
            "worker_attempt_active",
            "permission_state_inconsistent",
        }
    )


@pytest.mark.parametrize("attempt_state", ("succeeded", "completed"))
def test_live_failure_classifies_completed_effect_without_permission(
    attempt_state: str,
) -> None:
    state = _live_diagnostic_state(
        mission_status="completed",
        attempt_state=attempt_state,
    )

    diagnostic = json.loads(
        str(
            _live_failure(
                "first_permission_timeout",
                store=_StaticLiveStore(state),
            )
        )
    )

    assert diagnostic["ledger"] == {
        "classification": "worker_effect_not_requested",
        "mission_status": "completed",
        "step_position": 1,
        "agent_id": "claude-worker",
        "configured_transport": "acp",
        "attempt_state": attempt_state,
        "reply_state": "validated",
        "handoff_state": "recorded",
        "handoff_status": "completed",
        "permission_count": 0,
        "permission_states": [],
    }


def test_live_failure_classifies_active_worker_attempt() -> None:
    state = _live_diagnostic_state(
        attempt_state="running",
        reply_state="received",
        handoff_state="pending",
        handoff_status="blocked",
    )

    diagnostic = json.loads(
        str(_live_failure("first_permission_timeout", store=_StaticLiveStore(state)))
    )

    assert diagnostic["ledger"]["classification"] == "worker_attempt_active"


@pytest.mark.parametrize("attempt_state", ("failed", "cancelled", "interrupted"))
def test_live_failure_classifies_failed_worker_attempt(attempt_state: str) -> None:
    state = _live_diagnostic_state(
        attempt_state=attempt_state,
        reply_state="invalid",
        handoff_state="pending",
        handoff_status="failed",
    )

    diagnostic = json.loads(
        str(
            _live_failure(
                "mission_completion_timeout",
                store=_StaticLiveStore(state),
            )
        )
    )

    assert diagnostic["ledger"]["classification"] == "worker_attempt_failed"


@pytest.mark.parametrize("attempt_state", ("running", "failed"))
def test_live_failure_prioritizes_any_permission_as_inconsistent(attempt_state: str) -> None:
    state = _live_diagnostic_state(
        attempt_state=attempt_state,
        permissions=[{"status": "pending", "target": "SECRET /Users/private"}],
    )

    diagnostic = json.loads(
        str(_live_failure("first_permission_timeout", store=_StaticLiveStore(state)))
    )

    assert diagnostic["ledger"]["classification"] == "permission_state_inconsistent"
    assert diagnostic["ledger"]["permission_count"] == 1
    assert diagnostic["ledger"]["permission_states"] == ["pending"]


def test_live_failure_prioritizes_closed_missing_task_authority() -> None:
    task_authority = {field: True for field in _LIVE_TASK_AUTHORITY_FIELDS}
    task_authority["revision_transition"] = False
    state = _live_diagnostic_state(
        attempt_state="running",
        permissions=[{"status": "pending"}],
    )
    mission = state["missions"][0]
    assert type(mission) is dict
    mission["mission_id"] = "mis_crossed"

    diagnostic = json.loads(
        str(
            _live_failure(
                "native_schema_task_authority_invalid",
                store=_StaticLiveStore(state),
                task_authority=task_authority,
            )
        )
    )

    assert diagnostic["ledger"]["classification"] == "leader_task_authority_missing"


def test_live_failure_closes_malformed_ledger_values_without_leaking() -> None:
    secret = "SECRET /Users/private mat_secret dsp_secret summary blocker"
    state = _live_diagnostic_state(
        mission_status=secret,
        attempt_state=secret,
        reply_state=secret,
        handoff_state=secret,
        handoff_status=secret,
        permissions=[{"status": secret, "tool": secret}],
    )
    attempt = state["mission_attempts"][0]
    assert type(attempt) is dict
    attempt["step_id"] = "step_99"
    attempt["agent_id"] = secret
    attempt["configured_transport"] = secret

    rendered = str(_live_failure("unknown_failure", store=_StaticLiveStore(state)))
    diagnostic = json.loads(rendered)

    assert diagnostic["ledger"] == {
        "classification": "permission_state_inconsistent",
        "mission_status": "unknown",
        "step_position": 0,
        "agent_id": "unknown",
        "configured_transport": "unknown",
        "attempt_state": "unknown",
        "reply_state": "unknown",
        "handoff_state": "unknown",
        "handoff_status": "unknown",
        "permission_count": 1,
        "permission_states": ["unknown"],
    }
    assert set(diagnostic["ledger"]) == _LIVE_LEDGER_KEYS
    for forbidden in (
        "SECRET",
        "/Users",
        "mat_",
        "dsp_",
        "summary",
        "task",
        "goal",
        "blocker",
        "terminal_reason",
        "target",
        "tool",
        "prompt",
        "pty_text",
        "acp_update",
        "provider_output",
        "verification",
        "risks",
        "next_steps",
        "credentials",
    ):
        assert forbidden not in rendered


def test_live_failure_rejects_unrelated_reply_and_handoff_evidence() -> None:
    state = _live_diagnostic_state(mission_status="completed")
    reply = state["mission_worker_replies"][0]
    handoff = state["mission_handoffs"][0]
    assert type(reply) is dict
    assert type(handoff) is dict
    reply["attempt_id"] = "mat_unrelated_reply"
    handoff["attempt_id"] = "mat_unrelated_handoff"

    diagnostic = json.loads(
        str(_live_failure("first_permission_timeout", store=_StaticLiveStore(state)))
    )

    assert diagnostic["ledger"]["classification"] == "permission_state_inconsistent"
    assert diagnostic["ledger"]["reply_state"] == "unknown"
    assert diagnostic["ledger"]["handoff_state"] == "unknown"


@pytest.mark.parametrize(
    "mutation",
    ("crossed_mission", "reply_cross_mission", "wrong_handoff_reply", "dispatch_drift"),
)
def test_live_failure_fails_closed_incomplete_durable_lineage(mutation: str) -> None:
    state = _live_diagnostic_state(mission_status="completed")
    mission = state["missions"][0]
    reply = state["mission_worker_replies"][0]
    handoff = state["mission_handoffs"][0]
    assert type(mission) is dict
    assert type(reply) is dict
    assert type(handoff) is dict
    if mutation == "crossed_mission":
        mission["mission_id"] = "mis_crossed"
    elif mutation == "reply_cross_mission":
        reply["mission_id"] = "mis_crossed"
    elif mutation == "wrong_handoff_reply":
        handoff["reply_id"] = "mrp_crossed"
    else:
        reply["dispatch_key"] = "dsp_crossed"

    rendered = str(
        _live_failure("first_permission_timeout", store=_StaticLiveStore(state))
    )
    diagnostic = json.loads(rendered)

    assert diagnostic["ledger"]["classification"] == "permission_state_inconsistent"
    assert diagnostic["ledger"]["mission_status"] == "unknown"
    assert diagnostic["ledger"]["reply_state"] == "unknown"
    assert diagnostic["ledger"]["handoff_state"] == "unknown"
    for forbidden in ("mis_", "mat_", "mrp_", "dsp_", "SECRET", "/Users"):
        assert forbidden not in rendered


@pytest.mark.parametrize("attempt_state", ("running", "failed"))
def test_live_failure_requires_valid_lineage_for_attempt_classification(
    attempt_state: str,
) -> None:
    state = _live_diagnostic_state(attempt_state=attempt_state)
    mission = state["missions"][0]
    assert type(mission) is dict
    mission["mission_id"] = "mis_crossed"

    diagnostic = json.loads(
        str(_live_failure("first_permission_timeout", store=_StaticLiveStore(state)))
    )

    assert diagnostic["ledger"]["classification"] == "permission_state_inconsistent"
    assert diagnostic["ledger"]["mission_status"] == "unknown"
    assert diagnostic["ledger"]["reply_state"] == "unknown"
    assert diagnostic["ledger"]["handoff_state"] == "unknown"


@pytest.mark.parametrize(
    "mutation",
    ("missing_attempt_mission", "duplicate_mission", "missing_reply_id", "duplicate_handoff"),
)
def test_live_failure_rejects_missing_or_ambiguous_lineage_identity(
    mutation: str,
) -> None:
    state = _live_diagnostic_state(mission_status="completed")
    attempt = state["mission_attempts"][0]
    reply = state["mission_worker_replies"][0]
    assert type(attempt) is dict
    assert type(reply) is dict
    if mutation == "missing_attempt_mission":
        attempt.pop("mission_id")
    elif mutation == "duplicate_mission":
        state["missions"].append(dict(state["missions"][0]))
    elif mutation == "missing_reply_id":
        reply.pop("reply_id")
    else:
        state["mission_handoffs"].append(dict(state["mission_handoffs"][0]))

    diagnostic = json.loads(
        str(_live_failure("first_permission_timeout", store=_StaticLiveStore(state)))
    )

    assert diagnostic["ledger"]["classification"] == "permission_state_inconsistent"
    assert diagnostic["ledger"]["mission_status"] == "unknown"
    assert diagnostic["ledger"]["reply_state"] == "unknown"
    assert diagnostic["ledger"]["handoff_state"] == "unknown"


@pytest.mark.parametrize(
    "collection_name",
    (
        "missions",
        "mission_attempts",
        "mission_worker_replies",
        "mission_handoffs",
        "permission_requests",
    ),
)
def test_live_failure_fails_closed_any_malformed_ledger_collection(
    collection_name: str,
) -> None:
    state = _live_diagnostic_state(mission_status="completed")
    collection = state[collection_name]
    assert type(collection) is list
    collection.insert(0, "SECRET /Users/private mat_bad dsp_bad summary")

    rendered = str(
        _live_failure("first_permission_timeout", store=_StaticLiveStore(state))
    )
    diagnostic = json.loads(rendered)

    assert diagnostic["ledger"]["classification"] == "permission_state_inconsistent"
    assert diagnostic["ledger"]["permission_count"] == (
        -1 if collection_name == "permission_requests" else 0
    )
    assert diagnostic["ledger"]["permission_states"] == []
    assert diagnostic["cardinalities"][collection_name] == (
        1 if collection_name == "permission_requests" else 2
    )
    for forbidden in ("SECRET", "/Users", "mat_", "dsp_", "summary"):
        assert forbidden not in rendered


@pytest.mark.parametrize(
    ("malformed_permissions", "expected_cardinality"),
    (
        ({}, 0),
        ("SECRET /Users/private mat_bad dsp_bad summary", -1),
        (["SECRET /Users/private mat_bad dsp_bad summary"], 1),
    ),
)
def test_live_failure_fails_closed_malformed_permission_collection(
    malformed_permissions: object,
    expected_cardinality: int,
) -> None:
    state = _live_diagnostic_state(mission_status="completed")
    state["permission_requests"] = malformed_permissions

    rendered = str(
        _live_failure("first_permission_timeout", store=_StaticLiveStore(state))
    )
    diagnostic = json.loads(rendered)

    assert diagnostic["ledger"]["classification"] == "permission_state_inconsistent"
    assert diagnostic["ledger"]["attempt_state"] == "succeeded"
    assert diagnostic["ledger"]["reply_state"] == "validated"
    assert diagnostic["ledger"]["handoff_state"] == "recorded"
    assert diagnostic["ledger"]["handoff_status"] == "completed"
    assert diagnostic["ledger"]["permission_count"] == -1
    assert diagnostic["ledger"]["permission_states"] == []
    assert diagnostic["cardinalities"]["permission_requests"] == expected_cardinality
    for forbidden in ("SECRET", "/Users", "mat_", "dsp_", "summary"):
        assert forbidden not in rendered


def test_live_failure_uses_one_state_snapshot_for_cardinalities_and_ledger() -> None:
    state = _live_diagnostic_state(mission_status="stopped", attempt_state="failed")
    store = _StaticLiveStore(state, reject_second_load=True)

    diagnostic = json.loads(str(_live_failure("first_permission_timeout", store=store)))

    assert store.load_count == 1
    assert diagnostic["cardinalities"]["mission_attempts"] == 1
    assert diagnostic["ledger"]["attempt_state"] == "failed"
    assert diagnostic["ledger"]["mission_status"] == "stopped"


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


def test_codex_probe_uses_temporary_guarded_home_without_writes(
    tmp_path, monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    codex = fake_bin / "codex"
    codex.write_text(
        "#!/bin/sh\n"
        "case \"${CODEX_HOME:-}\" in\n"
        "  \"$TMPDIR\"/*) ;;\n"
        "  *) mkdir -p \"$HOME/.codex/tmp/arg0/leaked\";;\n"
        "esac\n"
        "printf 'WARNING: helper aliases unavailable\\n' >&2\n"
        "case \"$1\" in\n"
        "  exec) echo --output-schema --output-last-message;;\n"
        "  *) echo codex-cli 0.131.0;;\n"
        "esac\n",
        encoding="utf-8",
    )
    codex.chmod(0o700)
    monkeypatch.setenv("AGENTDECK_M2C_CODEX", str(codex))
    for name, env_name, _help, _version in TOOL_SPECS[1:]:
        executable = fake_bin / name
        executable.write_text(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  --help) echo --json-schema --output-format;;\n"
            "  -V) echo tmux 3.6a;;\n"
            "  *) echo 1.0.0;;\n"
            "esac\n",
            encoding="utf-8",
        )
        executable.chmod(0o700)
        monkeypatch.setenv(env_name, str(executable))
    isolation = _prepare_probe_isolation(project)
    before = _roots_snapshot((project, *isolation.roots))

    payload = _live_preflight(
        project,
        require_explicit_paths=True,
        isolation=isolation,
    )

    assert payload["ready"] is True
    assert payload["blockers"] == []
    assert payload["tools"][0]["version"] == "codex-cli 0.131.0"
    assert _roots_snapshot((project, *isolation.roots)) == before


def test_sanitized_version_skips_leading_warning() -> None:
    assert _sanitized_version(
        b"WARNING: helper aliases unavailable\ncodex-cli 0.131.0\n"
    ) == "codex-cli 0.131.0"


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
    tmux_seal = _seal_executable(str(fake_tmux))
    assert tmux_seal is not None
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
        tmux=tmux_seal,
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
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 5
    while not child_pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    try:
        result = _terminate_process_group(_seal_process_group(parent.pid))
        assert result == {"code": None, "residual_process_count": 0}
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
    tmux_seal = _seal_executable(str(fake_tmux))
    assert tmux_seal is not None

    result = _cleanup_exact_tmux(
        tmux=tmux_seal,
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


def test_pty_cleanup_kills_child_after_session_leader_exits(tmp_path) -> None:
    child_pid_file = tmp_path / "pty-child.pid"
    process = subprocess.Popen(
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
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    child_pid: int | None = None
    try:
        scope = _seal_process_group(process.pid)
        deadline = time.monotonic() + 5
        while not child_pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        process.terminate()
        process.wait(timeout=5)
        result = _terminate_process_group(scope)
        audit = _derive_residual_audit(
            tracked_process_scopes=(scope,),
            endpoint_paths=(),
            tmux_reachable=False,
            tmux_socket_paths=(),
        )
        assert result == {"code": None, "residual_process_count": 0}
        assert audit["residual_process_count"] == 0
        assert not _process_alive(child_pid)
    finally:
        if process.poll() is None:
            process.kill()
        if child_pid is not None:
            with __import__("contextlib").suppress(ProcessLookupError):
                os.kill(child_pid, signal.SIGKILL)


def test_pty_group_kill_failure_is_compact_and_residual_is_derived(tmp_path) -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(60)"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        scope = _seal_process_group(process.pid)
        result = _terminate_process_group(
            scope,
            signal_group=lambda _pgid, _signal: (_ for _ in ()).throw(
                PermissionError("SECRET pid/path/command")
            ),
        )
        assert result["code"] == "pty_process_group_cleanup_failed"
        assert result["residual_process_count"] > 0
        assert "SECRET" not in repr(result)
    finally:
        if process.poll() is None:
            process.kill()
        with __import__("contextlib").suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)


def _daemon_fixture(
    root: Path,
    *,
    pid: int,
) -> socket.socket:
    endpoint = daemon_endpoint(root)
    endpoint.metadata_path.parent.mkdir(parents=True, mode=0o700)
    endpoint.metadata_path.write_text(
        json.dumps(
            {
                "instance_id": "dmn_test",
                "project_root_hash": project_root_hash(root),
                "start_nonce_hash": "a" * 64,
                "pid": pid,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    endpoint.metadata_path.chmod(0o600)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(endpoint.socket_path))
    os.chmod(endpoint.socket_path, 0o600)
    return server


def test_daemon_tampered_pid_never_signals_unrelated_process(tmp_path) -> None:
    root = Path(tempfile.mkdtemp(prefix="m2c-auth-", dir="/tmp"))
    daemon = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(60)"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(60)"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    server: socket.socket | None = None
    signalled: list[tuple[int, int]] = []
    try:
        server = _daemon_fixture(root, pid=daemon.pid)
        identity = _seal_daemon_cleanup_identity(
            root,
            handshake_verifier=lambda metadata: metadata["pid"] == daemon.pid,
        )
        endpoint = daemon_endpoint(root)
        tampered = json.loads(endpoint.metadata_path.read_text(encoding="utf-8"))
        tampered["pid"] = unrelated.pid
        endpoint.metadata_path.write_text(json.dumps(tampered), encoding="utf-8")
        result = _stop_or_terminate_daemon(
            root=root,
            identity=identity,
            stop_daemon=lambda: (_ for _ in ()).throw(OSError("stop failed")),
            signal_process=lambda pid, sig: signalled.append((pid, sig)),
        )
        assert result["code"] == "daemon_cleanup_authority_unverified"
        assert result["residual_process_count"] > 0
        assert signalled == []
        assert _process_alive(unrelated.pid)
        assert str(unrelated.pid) not in repr(result)
        assert str(endpoint.metadata_path) not in repr(result)
    finally:
        if server is not None:
            server.close()
        for process in (daemon, unrelated):
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.parametrize("endpoint_kind", ["symlink", "directory"])
def test_daemon_metadata_symlink_or_nonregular_is_never_sealed(
    tmp_path, endpoint_kind,
) -> None:
    endpoint = daemon_endpoint(tmp_path)
    endpoint.metadata_path.parent.mkdir(parents=True, mode=0o700)
    target = tmp_path / "outside.json"
    target.write_text("{}", encoding="utf-8")
    if endpoint_kind == "symlink":
        endpoint.metadata_path.symlink_to(target)
    else:
        endpoint.metadata_path.mkdir()
    with pytest.raises(_LiveHarnessFailure) as error:
        _seal_daemon_cleanup_identity(
            tmp_path,
            handshake_verifier=lambda _metadata: True,
        )
    assert "daemon_cleanup_authority_unverified" in str(error.value)
    assert str(target) not in str(error.value)


def test_daemon_birth_mismatch_never_signals(tmp_path) -> None:
    root = Path(tempfile.mkdtemp(prefix="m2c-auth-", dir="/tmp"))
    daemon = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(60)"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    server: socket.socket | None = None
    signalled: list[tuple[int, int]] = []
    try:
        server = _daemon_fixture(root, pid=daemon.pid)
        identity = _seal_daemon_cleanup_identity(
            root,
            handshake_verifier=lambda _metadata: True,
            fingerprint_provider=lambda _pid: "birth-a",
        )
        result = _stop_or_terminate_daemon(
            root=root,
            identity=identity,
            stop_daemon=lambda: (_ for _ in ()).throw(OSError("stop failed")),
            fingerprint_provider=lambda _pid: "birth-b",
            signal_process=lambda pid, sig: signalled.append((pid, sig)),
        )
        assert result["code"] == "daemon_cleanup_authority_unverified"
        assert signalled == []
        assert _process_alive(daemon.pid)
    finally:
        if server is not None:
            server.close()
        if daemon.poll() is None:
            daemon.kill()
        with __import__("contextlib").suppress(subprocess.TimeoutExpired):
            daemon.wait(timeout=5)
        shutil.rmtree(root, ignore_errors=True)


def test_valid_sealed_daemon_identity_kills_exact_tree(tmp_path) -> None:
    root = Path(tempfile.mkdtemp(prefix="m2c-auth-", dir="/tmp"))
    child_pid_file = tmp_path / "daemon-child.pid"
    daemon = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import subprocess,sys,time; "
                "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],start_new_session=True); "
                f"open({str(child_pid_file)!r},'w').write(str(child.pid)); "
                "time.sleep(60)"
            ),
        ],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    child_pid: int | None = None
    server: socket.socket | None = None
    try:
        deadline = time.monotonic() + 5
        while not child_pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        server = _daemon_fixture(root, pid=daemon.pid)
        identity = _seal_daemon_cleanup_identity(
            root,
            handshake_verifier=lambda _metadata: True,
        )
        result = _stop_or_terminate_daemon(
            root=root,
            identity=identity,
            stop_daemon=lambda: (_ for _ in ()).throw(OSError("stop failed")),
            handshake_verifier=lambda _metadata: True,
        )
        assert result == {
            "code": None,
            "fallback_used": True,
            "residual_process_count": 0,
        }
        assert not _process_alive(daemon.pid)
        assert not _process_alive(child_pid)
    finally:
        if server is not None:
            server.close()
        for pid in ((child_pid,) if child_pid is not None else ()) + (daemon.pid,):
            with __import__("contextlib").suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        with __import__("contextlib").suppress(subprocess.TimeoutExpired):
            daemon.wait(timeout=2)
        shutil.rmtree(root, ignore_errors=True)


def test_flooding_pty_yields_to_deadline_and_cleans_group(tmp_path) -> None:
    master, slave = pty.openpty()
    process: subprocess.Popen[bytes] | None = None
    scope: _ProcessGroupScope | None = None
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import os; payload=b'x'*4096\nwhile True: os.write(1,payload)",
            ],
            stdin=subprocess.DEVNULL,
            stdout=slave,
            stderr=slave,
            close_fds=True,
            start_new_session=True,
        )
        scope = _seal_process_group(process.pid)
        os.close(slave)
        slave = -1
        with pytest.raises(_LiveHarnessFailure) as error:
            _wait_for_pty_prompt(
                process,
                master,
                _PtyTail(),
                1,
                timeout_seconds=0.2,
            )
        assert "bare_pty_prompt_timeout" in str(error.value)
        assert time.monotonic() - started < 2
    finally:
        if scope is not None:
            _terminate_process_group(scope)
            assert _process_scope_residual_count(scope) == 0
        elif process is not None:
            if process.poll() is None:
                process.kill()
        if process is not None:
            with __import__("contextlib").suppress(subprocess.TimeoutExpired):
                process.wait(timeout=2)
        for descriptor in (master, slave):
            if descriptor >= 0:
                with __import__("contextlib").suppress(OSError):
                    os.close(descriptor)


def test_kernel_birth_identity_is_precise_and_stable() -> None:
    birth = _kernel_process_birth(os.getpid())
    assert type(birth) is str and birth
    assert _kernel_process_birth(os.getpid()) == birth
    first = _process_birth_fingerprint(
        os.getpid(), kernel_birth_provider=lambda _pid: "kernel-birth-a"
    )
    second = _process_birth_fingerprint(
        os.getpid(), kernel_birth_provider=lambda _pid: "kernel-birth-b"
    )
    assert first != second


def test_unavailable_kernel_birth_fails_closed_without_signal() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(60)"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    scope = _seal_process_group(
        process.pid,
        fingerprint_provider=lambda _pid: "sealed-kernel-birth",
    )
    signalled: list[tuple[int, int]] = []
    try:
        result = _terminate_process_group(
            scope,
            fingerprint_provider=lambda _pid: None,
            signal_group=lambda pgid, sig: signalled.append((pgid, sig)),
        )
        assert result["code"] == "process_cleanup_authority_unverified"
        assert result["residual_process_count"] > 0
        assert signalled == []
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_successful_probe_reaps_same_group_children(tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    monkeypatch.setenv("HOME", str(real_home))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    child_pid_file = tmp_path / "probe-children.pid"
    script = (
        f"#!{sys.executable}\n"
        "import os,sys,time\n"
        "child=os.fork()\n"
        "if child == 0:\n"
        "    time.sleep(60)\n"
        "    os._exit(0)\n"
        f"with open({str(child_pid_file)!r},'a',encoding='ascii') as stream: stream.write(str(child)+'\\n')\n"
        "args=set(sys.argv[1:])\n"
        "if 'exec' in args: text='codex-cli 1.0 --output-schema --output-last-message\\n'\n"
        "elif '--help' in args: text='claude 1.0 --json-schema --output-format\\n'\n"
        "else: text='m2c-tool 1.0\\n'\n"
        "os.write(1,text.encode('ascii'))\n"
    )
    children: list[int] = []
    try:
        for name, env_name, _help, _version in TOOL_SPECS:
            executable = fake_bin / name
            executable.write_text(script, encoding="utf-8")
            executable.chmod(0o700)
            monkeypatch.setenv(env_name, str(executable))
        payload = _live_preflight(project, require_explicit_paths=True)
        children = [
            int(item)
            for item in child_pid_file.read_text(encoding="ascii").splitlines()
        ]
        assert payload["ready"] is True
        assert payload["blockers"] == []
        assert children
        assert all(not _process_alive(pid) for pid in children)
        assert not any(real_home.iterdir())
    finally:
        for pid in children:
            with __import__("contextlib").suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


def test_probe_cleanup_failure_is_compact_and_blocks_ready(tmp_path) -> None:
    executable = tmp_path / "probe"
    child_pid_file = tmp_path / "probe-child.pid"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os,time\n"
        "child=os.fork()\n"
        "if child == 0:\n"
        "    time.sleep(60)\n"
        "    os._exit(0)\n"
        f"open({str(child_pid_file)!r},'w').write(str(child))\n"
        "os.write(1,b'probe 1.0\\n')\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    seal = _seal_executable(str(executable))
    assert seal is not None
    child_pid: int | None = None
    try:
        outcome = _bounded_probe(
            seal,
            ("--version",),
            cwd=tmp_path,
            env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
            signal_group=lambda _pgid, _signal: (_ for _ in ()).throw(
                PermissionError("SECRET path command")
            ),
        )
        child_pid = int(child_pid_file.read_text(encoding="ascii"))
        assert outcome.blocker == "probe_residual_process"
        assert "SECRET" not in repr(outcome)
        assert not _process_alive(child_pid)
    finally:
        if child_pid is not None:
            with __import__("contextlib").suppress(ProcessLookupError):
                os.kill(child_pid, signal.SIGKILL)


@pytest.mark.parametrize("replacement", ["inode", "content"])
def test_executable_seal_rejects_replacement_before_use(
    tmp_path, replacement,
) -> None:
    executable = tmp_path / "tool"
    marker = tmp_path / "project-init.marker"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    seal = _seal_executable(str(executable))
    assert seal is not None
    if replacement == "inode":
        alternate = tmp_path / "alternate"
        alternate.write_text("#!/bin/sh\ntouch replaced\n", encoding="utf-8")
        alternate.chmod(0o700)
        alternate.replace(executable)
    else:
        executable.write_text("#!/bin/sh\ntouch replaced\n", encoding="utf-8")
        executable.chmod(0o700)
    with pytest.raises(_LiveHarnessFailure) as error:
        _verify_all_executable_seals({"tool": seal})
        marker.write_text("initialized", encoding="utf-8")
    assert "executable_identity_drift" in str(error.value)
    assert not marker.exists()
    with pytest.raises(_LiveHarnessFailure):
        _sealed_argv(seal, "--version")


def test_post_preflight_executable_replacement_blocks_project_init(
    tmp_path, monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    script = (
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  exec) echo 'codex 1.0 --output-schema --output-last-message';;\n"
        "  --help) echo 'claude 1.0 --json-schema --output-format';;\n"
        "  *) echo 'tool 1.0';;\n"
        "esac\n"
    )
    for name, env_name, _help, _version in TOOL_SPECS:
        executable = fake_bin / name
        executable.write_text(script, encoding="utf-8")
        executable.chmod(0o700)
        monkeypatch.setenv(env_name, str(executable))
    seals = _explicit_live_paths()
    payload = _live_preflight(project, require_explicit_paths=True)
    assert payload["ready"] is True
    codex = fake_bin / "codex"
    replacement = fake_bin / "codex-new"
    replacement.write_text("#!/bin/sh\ntouch forbidden\n", encoding="utf-8")
    replacement.chmod(0o700)
    replacement.replace(codex)
    marker = tmp_path / "project-init.marker"
    with pytest.raises(_LiveHarnessFailure) as error:
        _verify_all_executable_seals(seals)
        marker.write_text("initialized", encoding="utf-8")
    assert "executable_identity_drift" in str(error.value)
    assert not marker.exists()


def test_probe_fast_exit_after_scope_seal_failure_emits_no_signal(tmp_path) -> None:
    executable = tmp_path / "probe"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    seal = _seal_executable(str(executable))
    assert seal is not None
    signalled: list[tuple[int, int]] = []
    spawned: list[subprocess.Popen[bytes]] = []

    def popen(*args, **kwargs):
        process = subprocess.Popen(*args, **kwargs)
        spawned.append(process)
        return process

    outcome = _bounded_probe(
        seal,
        (),
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        scope_sealer=lambda _pid: (_ for _ in ()).throw(
            _live_failure("process_cleanup_authority_unverified")
        ),
        popen_factory=popen,
        signal_group=lambda pgid, sig: signalled.append((pgid, sig)),
    )

    assert outcome.blocker == "probe_scope_unverified"
    assert signalled == []
    assert len(spawned) == 1 and spawned[0].returncode == 0


def test_probe_live_unsealable_process_emits_no_guessed_signal(tmp_path) -> None:
    executable = tmp_path / "probe"
    executable.write_text(
        f"#!{sys.executable}\nimport time\ntime.sleep(60)\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    seal = _seal_executable(str(executable))
    assert seal is not None
    signalled: list[tuple[int, int]] = []
    spawned: list[subprocess.Popen[bytes]] = []

    def popen(*args, **kwargs):
        process = subprocess.Popen(*args, **kwargs)
        spawned.append(process)
        return process

    try:
        outcome = _bounded_probe(
            seal,
            (),
            cwd=tmp_path,
            env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
            scope_sealer=lambda _pid: (_ for _ in ()).throw(
                _live_failure("process_cleanup_authority_unverified")
            ),
            popen_factory=popen,
            signal_group=lambda pgid, sig: signalled.append((pgid, sig)),
        )
        assert outcome.blocker == "probe_scope_unverified"
        assert signalled == []
        assert len(spawned) == 1 and spawned[0].poll() is None
    finally:
        for process in spawned:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


def test_live_mission_scope_seal_failure_emits_no_guessed_signal(
    tmp_path, monkeypatch,
) -> None:
    spawned: list[subprocess.Popen[bytes]] = []
    signalled: list[tuple[int, int]] = []

    def popen(*args, **kwargs):
        process = subprocess.Popen(*args, **kwargs)
        spawned.append(process)
        return process

    monkeypatch.setattr(
        os,
        "killpg",
        lambda pgid, sig: signalled.append((pgid, sig)),
    )
    try:
        with pytest.raises(_LiveHarnessFailure):
            _create_and_confirm_live_mission(
                tmp_path,
                StateStore(tmp_path),
                env=dict(os.environ),
                popen_factory=popen,
                scope_sealer=lambda _pid: (_ for _ in ()).throw(
                    _live_failure("process_cleanup_authority_unverified")
                ),
            )
        assert signalled == []
    finally:
        for process in spawned:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


def test_probe_cleans_forked_setsid_descendant_after_parent_exits_zero(
    tmp_path,
) -> None:
    child_pid_file = tmp_path / "setsid-child.pid"
    executable = tmp_path / "probe"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os,time\n"
        "child=os.fork()\n"
        "if child == 0:\n"
        "    os.setsid()\n"
        "    time.sleep(60)\n"
        "    os._exit(0)\n"
        f"open({str(child_pid_file)!r},'w').write(str(child))\n"
        "time.sleep(0.001)\n"
        "os.write(1,b'probe 1.0\\n')\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    seal = _seal_executable(str(executable))
    assert seal is not None
    child_pid: int | None = None
    try:
        outcome = _bounded_probe(
            seal,
            (),
            cwd=tmp_path,
            env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        )
        child_pid = int(child_pid_file.read_text(encoding="ascii"))
        assert outcome.ok is True
        assert outcome.blocker is None
        assert not _process_alive(child_pid)
    finally:
        if child_pid is not None and _process_alive(child_pid):
            os.kill(child_pid, signal.SIGKILL)


def test_probe_fails_closed_when_descendant_enumeration_unavailable(
    tmp_path,
) -> None:
    executable = tmp_path / "probe"
    executable.write_text("#!/bin/sh\nsleep 0.1\n", encoding="utf-8")
    executable.chmod(0o700)
    seal = _seal_executable(str(executable))
    assert seal is not None

    outcome = _bounded_probe(
        seal,
        (),
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        descendant_provider=lambda _pid: None,
    )

    assert outcome.blocker == "probe_scope_unverified"


def test_controlled_launcher_rejects_source_replacement_before_invocation(
    tmp_path,
) -> None:
    source = tmp_path / "provider"
    replacement_marker = tmp_path / "replacement.marker"
    source.write_text("#!/bin/sh\nprintf 'original\\n'\n", encoding="utf-8")
    source.chmod(0o700)
    source_seal = _seal_executable(str(source))
    assert source_seal is not None
    wrappers = tmp_path / "wrappers"
    wrappers.mkdir(mode=0o700)
    wrapper = _write_controlled_launcher("provider", source_seal, wrappers)
    original = subprocess.run(
        _sealed_argv(wrapper),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
        check=False,
    )
    assert original.returncode == 0
    assert original.stdout == b"original\n"
    replacement = tmp_path / "replacement"
    replacement.write_text(
        "#!/bin/sh\n" + f"touch {shlex.quote(str(replacement_marker))}\n",
        encoding="utf-8",
    )
    replacement.chmod(0o700)
    replacement.replace(source)

    completed = subprocess.run(
        _sealed_argv(wrapper),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
        check=False,
    )

    assert completed.returncode != 0
    assert completed.stdout == b""
    assert not replacement_marker.exists()
    assert wrapper.mode == 0o500


@pytest.mark.parametrize("failure_index", [0, 2])
def test_live_setup_launcher_failure_removes_entire_parent(
    tmp_path, monkeypatch, failure_index,
) -> None:
    parent = tmp_path / f"agentdeck-m2c-live-{failure_index}"
    parent.mkdir(mode=0o700)
    source_bin = tmp_path / f"source-bin-{failure_index}"
    source_bin.mkdir(mode=0o700)
    paths: dict[str, _ExecutableSeal] = {}
    for name, _env_name, _help, _version in TOOL_SPECS:
        executable = source_bin / name
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o700)
        seal = _seal_executable(str(executable))
        assert seal is not None
        paths[name] = seal
    real_writer = _write_controlled_launcher
    calls = 0

    def fail_at_index(name, source, destination):
        nonlocal calls
        current = calls
        calls += 1
        if current == failure_index:
            raise _live_failure("launcher_setup_blocked")
        return real_writer(name, source, destination)

    monkeypatch.setattr(
        sys.modules[__name__],
        "_write_controlled_launcher",
        fail_at_index,
    )

    with pytest.raises(_LiveHarnessFailure) as error:
        _run_live_acceptance_in_project(paths, parent)

    assert "launcher_setup_blocked" in str(error.value)
    assert not parent.exists()


def test_live_setup_cleanup_failure_keeps_original_blocker_compact(
    tmp_path, monkeypatch,
) -> None:
    parent = tmp_path / "agentdeck-m2c-live-cleanup-failure"
    parent.mkdir(mode=0o700)
    executable = tmp_path / "codex"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    seal = _seal_executable(str(executable))
    assert seal is not None
    monkeypatch.setattr(
        sys.modules[__name__],
        "_write_controlled_launcher",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            _live_failure("launcher_setup_blocked")
        ),
    )
    monkeypatch.setattr(
        shutil,
        "rmtree",
        lambda _path: (_ for _ in ()).throw(OSError("SECRET cleanup path")),
    )

    with pytest.raises(_LiveHarnessFailure) as error:
        _run_live_acceptance_in_project(
            {name: seal for name, _env, _help, _version in TOOL_SPECS},
            parent,
        )

    assert "launcher_setup_blocked" in str(error.value)
    assert "live_setup_cleanup_failed" in repr(error.value.__notes__)
    assert "SECRET" not in repr(error.value.__notes__)


class _CleanupAbort(BaseException):
    pass


@pytest.mark.parametrize(
    ("interrupt_type", "failure_index"),
    [
        (KeyboardInterrupt, 0),
        (KeyboardInterrupt, 2),
        (SystemExit, 0),
        (SystemExit, 2),
    ],
)
def test_live_setup_interrupt_removes_parent_and_reraises_same_object(
    tmp_path, monkeypatch, interrupt_type, failure_index,
) -> None:
    parent = tmp_path / (
        f"agentdeck-m2c-live-{interrupt_type.__name__}-{failure_index}"
    )
    parent.mkdir(mode=0o700)
    source_bin = tmp_path / (
        f"source-{interrupt_type.__name__}-{failure_index}"
    )
    source_bin.mkdir(mode=0o700)
    paths: dict[str, _ExecutableSeal] = {}
    for name, _env_name, _help, _version in TOOL_SPECS:
        executable = source_bin / name
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o700)
        seal = _seal_executable(str(executable))
        assert seal is not None
        paths[name] = seal
    real_writer = _write_controlled_launcher
    interruption = interrupt_type("stop setup")
    calls = 0

    def interrupt_at_index(name, source, destination):
        nonlocal calls
        current = calls
        calls += 1
        if current == failure_index:
            raise interruption
        return real_writer(name, source, destination)

    monkeypatch.setattr(
        sys.modules[__name__],
        "_write_controlled_launcher",
        interrupt_at_index,
    )

    with pytest.raises(interrupt_type) as raised:
        _run_live_acceptance_in_project(paths, parent)

    assert raised.value is interruption
    assert not parent.exists()


@pytest.mark.parametrize(
    ("interrupt_type", "cleanup_error_type"),
    [
        (KeyboardInterrupt, OSError),
        (SystemExit, OSError),
        (KeyboardInterrupt, _CleanupAbort),
        (SystemExit, _CleanupAbort),
    ],
)
def test_live_setup_interrupt_cleanup_failure_adds_only_compact_note(
    tmp_path, monkeypatch, interrupt_type, cleanup_error_type,
) -> None:
    parent = tmp_path / f"agentdeck-m2c-live-{interrupt_type.__name__}-cleanup"
    parent.mkdir(mode=0o700)
    executable = tmp_path / f"tool-{interrupt_type.__name__}"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    seal = _seal_executable(str(executable))
    assert seal is not None
    interruption = interrupt_type("stop setup")
    monkeypatch.setattr(
        sys.modules[__name__],
        "_write_controlled_launcher",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(interruption),
    )
    monkeypatch.setattr(
        shutil,
        "rmtree",
        lambda _path, **_kwargs: (_ for _ in ()).throw(
            cleanup_error_type("SECRET cleanup path")
        ),
    )

    with pytest.raises(interrupt_type) as raised:
        _run_live_acceptance_in_project(
            {name: seal for name, _env, _help, _version in TOOL_SPECS},
            parent,
        )

    assert raised.value is interruption
    assert "live_setup_cleanup_failed" in repr(interruption.__notes__)
    assert "SECRET" not in repr(interruption.__notes__)


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
