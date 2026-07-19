"""Bounded passive identity and schema probe for the frozen Codex app-server."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Final


FROZEN_CODEX_VERSION: Final = "codex-cli 0.131.0"
FROZEN_SERVER_VERSION: Final = "0.131.0"
FROZEN_STABLE_SCHEMA_DIGEST: Final = (
    "91fae2120975b74d2d02184de2d8fed5f90770ce5009f308bbcaeec02dedcc23"
)
_SCHEMA_NAME: Final = "codex_app_server_protocol.v2.schemas.json"
_MAX_SCHEMA_BYTES: Final = 8 * 1024 * 1024
_MAX_PROBE_OUTPUT: Final = 4096
_PROBE_TIMEOUT: Final = 10.0


class ProbeFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class BridgeDiagnostic:
    code: str


@dataclass(frozen=True)
class CodexBridgeReadiness:
    ready: bool
    version: str | None
    schema_digest: str | None
    diagnostic: BridgeDiagnostic | None


def command_argv(value: Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise TypeError("command must be an argv sequence")
    result = tuple(value)
    if not result or any(type(part) is not str or not part for part in result):
        raise ValueError("command must contain nonempty argv strings")
    return result


def _directory_bytes(directory: Path) -> int:
    total = 0
    try:
        for root, _directories, files in os.walk(directory):
            for name in files:
                path = Path(root) / name
                if path.is_symlink():
                    raise ProbeFailure
                try:
                    total += path.stat().st_size
                except FileNotFoundError:
                    continue
                if total > _MAX_SCHEMA_BYTES:
                    raise ProbeFailure
    except OSError:
        raise ProbeFailure from None
    return total


def _stop(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    time.sleep(0.05)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1)


def _schema_limited(command: tuple[str, ...]) -> tuple[str, ...]:
    script = (
        "import os,resource,sys;"
        "limit=int(sys.argv[1]);"
        "resource.setrlimit(resource.RLIMIT_FSIZE,(limit,limit));"
        "os.execvp(sys.argv[2],sys.argv[2:])"
    )
    return (sys.executable, "-c", script, str(_MAX_SCHEMA_BYTES), *command)


def _run_bounded(command: tuple[str, ...], *, directory: Path | None = None) -> bytes:
    process: subprocess.Popen[bytes] | None = None
    reader: threading.Thread | None = None
    output = bytearray()
    oversized = threading.Event()
    reader_failed = threading.Event()
    try:
        process = subprocess.Popen(
            _schema_limited(command) if directory is not None else command,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
        if process.stdout is None:
            raise ProbeFailure

        def drain() -> None:
            try:
                while len(output) <= _MAX_PROBE_OUTPUT:
                    remaining = _MAX_PROBE_OUTPUT + 1 - len(output)
                    chunk = process.stdout.read(min(1024, remaining))
                    if not chunk:
                        return
                    output.extend(chunk)
                oversized.set()
            except (OSError, ValueError):
                reader_failed.set()

        reader = threading.Thread(target=drain, name="codex-probe-stdout", daemon=True)
        reader.start()
        deadline = time.monotonic() + _PROBE_TIMEOUT
        while process.poll() is None:
            if oversized.is_set() or reader_failed.is_set():
                raise ProbeFailure
            if directory is not None:
                _directory_bytes(directory)
            if time.monotonic() >= deadline:
                raise ProbeFailure
            time.sleep(0.005)
        reader.join(timeout=1)
        if reader.is_alive() or reader_failed.is_set() or oversized.is_set():
            raise ProbeFailure
        if process.returncode != 0 or len(output) > _MAX_PROBE_OUTPUT:
            raise ProbeFailure
        if directory is not None:
            _directory_bytes(directory)
        return bytes(output)
    except (OSError, subprocess.SubprocessError, ProbeFailure):
        raise ProbeFailure from None
    finally:
        if process is not None:
            _stop(process)
            if process.stdout is not None:
                process.stdout.close()
        if reader is not None:
            reader.join(timeout=1)


def probe_codex_bridge(
    app_server_command: Sequence[str], *,
    expected_version: str = FROZEN_CODEX_VERSION,
    expected_digest: str = FROZEN_STABLE_SCHEMA_DIGEST,
) -> CodexBridgeReadiness:
    """Verify frozen CLI/schema without auth, model, turn, or persistent writes."""
    command = command_argv(app_server_command)
    if command[-1] != "app-server":
        raise ValueError("app-server command must end with app-server")
    try:
        version = _run_bounded((*command[:-1], "--version")).decode(
            "utf-8", "strict",
        ).strip()
    except (UnicodeDecodeError, ProbeFailure):
        return CodexBridgeReadiness(
            False, None, None, BridgeDiagnostic("codex_app_server_probe_failed"),
        )
    if version != expected_version:
        return CodexBridgeReadiness(
            False, None, None, BridgeDiagnostic("codex_app_server_version_drift"),
        )
    try:
        with tempfile.TemporaryDirectory(prefix="agentdeck-codex-schema-") as raw:
            directory = Path(raw)
            _run_bounded(
                (*command, "generate-json-schema", "--out", raw),
                directory=directory,
            )
            schema_path = directory / _SCHEMA_NAME
            size = schema_path.stat().st_size
            if not 0 < size <= _MAX_SCHEMA_BYTES:
                raise ProbeFailure
            payload = json.loads(schema_path.read_text("utf-8"))
            canonical = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            )
            digest = sha256(canonical.encode("utf-8", "strict")).hexdigest()
    except (OSError, UnicodeError, ValueError, ProbeFailure):
        return CodexBridgeReadiness(
            False, version, None, BridgeDiagnostic("codex_app_server_probe_failed"),
        )
    if digest != expected_digest:
        return CodexBridgeReadiness(
            False, version, None, BridgeDiagnostic("codex_app_server_schema_drift"),
        )
    return CodexBridgeReadiness(True, version, digest, None)


__all__ = [
    "BridgeDiagnostic", "CodexBridgeReadiness", "FROZEN_CODEX_VERSION",
    "FROZEN_SERVER_VERSION", "FROZEN_STABLE_SCHEMA_DIGEST", "command_argv",
    "probe_codex_bridge",
]
