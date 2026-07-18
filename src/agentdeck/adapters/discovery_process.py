from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import time


MAX_VERSION_BYTES = 1024
VERSION_TIMEOUT_SECONDS = 5.0
MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024
MAX_TMUX_METADATA_BYTES = 16 * 1024 * 1024
_MAX_VERSION_ARGUMENTS = 4
_MAX_VERSION_ARGUMENT_BYTES = 64
_TRUSTED_TEMP_ROOT = Path("/var/tmp")
_TMUX_OUTPUT_MARKER = b"\x00tmux %s\n\x00"
_TMUX_VERSION_WINDOW_BYTES = 512
_STRICT_DOTTED_VERSION = re.compile(
    rb"[0-9]{1,6}(?:\.[0-9]{1,6}){1,3}(?:[A-Za-z][0-9]{0,4})?"
)


@dataclass(frozen=True)
class ResolvedExecutable:
    path: str
    signature: tuple[int, int, int, int, int, int]
    descriptor: int
    initial_digest: bytes | None = None


class VersionProbeOversize(Exception):
    pass


class VersionProbeInvalid(Exception):
    pass


def executable_signature(
    details: os.stat_result,
) -> tuple[int, int, int, int, int, int]:
    return (
        details.st_dev,
        details.st_ino,
        details.st_mode,
        details.st_size,
        details.st_mtime_ns,
        details.st_ctime_ns,
    )


def descriptor_digest(descriptor: int, size: int) -> bytes:
    digest = hashlib.sha256()
    offset = 0
    while offset < size:
        chunk = os.pread(descriptor, min(65536, size - offset), offset)
        if not chunk:
            raise RuntimeError("executable content changed")
        digest.update(chunk)
        offset += len(chunk)
    return digest.digest()


def bounded_version_runner(
    executable: ResolvedExecutable,
    *,
    arguments: tuple[str, ...],
    search_path: str,
) -> bytes:
    version_arguments = _validate_version_arguments(arguments)
    environment = {
        "HOME": "/nonexistent",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": search_path,
    }
    with tempfile.TemporaryDirectory(
        prefix="agentdeck-version-", dir=_trusted_temp_root()
    ) as temporary:
        directory = Path(temporary)
        os.chmod(directory, 0o700)
        snapshot = _copy_execution_snapshot(executable, directory)
        process = subprocess.Popen(
            [executable.path, *version_arguments],
            executable=str(snapshot),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            cwd="/",
            env=environment,
            start_new_session=True,
        )
        return _collect_version_output(process)


def bounded_tmux_version_reader(
    executable: ResolvedExecutable,
) -> bytes:
    if executable_signature(os.fstat(executable.descriptor)) != executable.signature:
        raise RuntimeError("tmux metadata identity changed")
    size = executable.signature[3]
    if size > MAX_TMUX_METADATA_BYTES:
        raise VersionProbeOversize
    if executable.initial_digest is None:
        raise RuntimeError("tmux metadata digest unavailable")
    content = bytearray()
    digest = hashlib.sha256()
    offset = 0
    while offset < size:
        chunk = os.pread(executable.descriptor, min(65536, size - offset), offset)
        if not chunk:
            raise RuntimeError("tmux metadata source changed")
        content.extend(chunk)
        digest.update(chunk)
        offset += len(chunk)
    source_digest = _source_digest(executable)
    if (
        executable_signature(os.fstat(executable.descriptor)) != executable.signature
        or not hmac.compare_digest(digest.digest(), executable.initial_digest)
        or not hmac.compare_digest(source_digest, executable.initial_digest)
        or not hmac.compare_digest(digest.digest(), source_digest)
    ):
        raise RuntimeError("tmux metadata validation failed")
    marker_position = content.find(_TMUX_OUTPUT_MARKER)
    if marker_position < 0 or content.find(
        _TMUX_OUTPUT_MARKER, marker_position + len(_TMUX_OUTPUT_MARKER)
    ) >= 0:
        raise VersionProbeInvalid
    window_start = max(0, marker_position - _TMUX_VERSION_WINDOW_BYTES)
    window = content[window_start:marker_position]
    fields = bytes(window).split(b"\x00")
    if window_start:
        fields = fields[1:]
    versions = [
        field for field in fields if _STRICT_DOTTED_VERSION.fullmatch(field)
    ]
    if len(versions) != 1:
        raise VersionProbeInvalid
    return b"tmux " + versions[0]


def _copy_execution_snapshot(
    executable: ResolvedExecutable, directory: Path
) -> Path:
    size = executable.signature[3]
    if size > MAX_EXECUTABLE_BYTES:
        raise VersionProbeOversize
    if executable.initial_digest is None:
        raise RuntimeError("execution snapshot digest unavailable")
    if executable_signature(os.fstat(executable.descriptor)) != executable.signature:
        raise RuntimeError("execution snapshot source changed")
    snapshot = directory / "tool"
    output = os.open(snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o500)
    try:
        snapshot_digest = hashlib.sha256()
        offset = 0
        while offset < size:
            chunk = os.pread(executable.descriptor, min(65536, size - offset), offset)
            if not chunk:
                raise RuntimeError("execution snapshot source changed")
            snapshot_digest.update(chunk)
            written = 0
            while written < len(chunk):
                count = os.write(output, chunk[written:])
                if count <= 0:
                    raise RuntimeError("execution snapshot write failed")
                written += count
            offset += len(chunk)
    finally:
        os.close(output)
    os.chmod(snapshot, 0o500)
    details = snapshot.stat()
    source_digest = _source_digest(executable)
    if (
        executable_signature(os.fstat(executable.descriptor)) != executable.signature
        or not hmac.compare_digest(
            snapshot_digest.digest(), executable.initial_digest
        )
        or not hmac.compare_digest(source_digest, executable.initial_digest)
        or not hmac.compare_digest(snapshot_digest.digest(), source_digest)
        or stat.S_IMODE(directory.stat().st_mode) != 0o700
        or tuple(directory.iterdir()) != (snapshot,)
        or not stat.S_ISREG(details.st_mode)
        or stat.S_IMODE(details.st_mode) != 0o500
        or details.st_nlink != 1
        or details.st_size != size
    ):
        raise RuntimeError("execution snapshot validation failed")
    return snapshot


def _source_digest(executable: ResolvedExecutable) -> bytes:
    digest = hashlib.sha256()
    offset = 0
    size = executable.signature[3]
    while offset < size:
        chunk = os.pread(executable.descriptor, min(65536, size - offset), offset)
        if not chunk:
            raise RuntimeError("execution snapshot source changed")
        digest.update(chunk)
        offset += len(chunk)
    return digest.digest()


def _validate_version_arguments(arguments: tuple[str, ...]) -> tuple[str, ...]:
    if type(arguments) is not tuple or not 1 <= len(arguments) <= _MAX_VERSION_ARGUMENTS:
        raise TypeError("version arguments must be a bounded tuple")
    for argument in arguments:
        if type(argument) is not str or not argument or "\x00" in argument:
            raise ValueError("version argument is invalid")
        try:
            encoded = argument.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise ValueError("version argument is invalid") from None
        if len(encoded) > _MAX_VERSION_ARGUMENT_BYTES:
            raise ValueError("version argument is invalid")
    return arguments


def _trusted_temp_root() -> Path:
    try:
        root = _TRUSTED_TEMP_ROOT.resolve(strict=True)
        details = root.stat()
    except OSError:
        raise RuntimeError("trusted temp root is unavailable") from None
    if (
        not root.is_absolute()
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != 0
        or not details.st_mode & stat.S_ISVTX
    ):
        raise RuntimeError("trusted temp root is unavailable")
    return root


def _collect_version_output(process: subprocess.Popen[bytes]) -> bytes:
    stdout = process.stdout
    stderr = process.stderr
    selector: selectors.BaseSelector | None = None
    try:
        if stdout is None or stderr is None:
            raise RuntimeError("version probe pipes unavailable")
        selector = selectors.DefaultSelector()
        selector.register(stdout, selectors.EVENT_READ, "stdout")
        selector.register(stderr, selectors.EVENT_READ, "stderr")
        output = bytearray()
        total_bytes = 0
        deadline = time.monotonic() + VERSION_TIMEOUT_SECONDS
        while selector.get_map():
            if process.poll() is not None:
                _signal_process_group(process.pid, signal.SIGTERM)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("version probe timed out")
            for key, _ in selector.select(min(remaining, 0.02)):
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                total_bytes += len(chunk)
                if total_bytes > MAX_VERSION_BYTES:
                    raise VersionProbeOversize
                if key.data == "stdout":
                    output.extend(chunk)
        return_code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        if return_code != 0:
            raise RuntimeError("version probe failed")
        return bytes(output)
    finally:
        try:
            _cleanup_process_group(process)
        finally:
            _safe_close(selector)
            _safe_close(stdout)
            _safe_close(stderr)


def _safe_close(resource: object | None) -> None:
    if resource is None:
        return
    try:
        resource.close()  # type: ignore[attr-defined]
    except Exception:
        pass


def _cleanup_process_group(process: subprocess.Popen[bytes]) -> None:
    _signal_process_group(process.pid, signal.SIGTERM)
    deadline = time.monotonic() + 0.1
    while time.monotonic() < deadline:
        if not _process_group_exists(process.pid):
            break
        time.sleep(0.005)
    if _process_group_exists(process.pid):
        _signal_process_group(process.pid, signal.SIGKILL)
    try:
        process.wait(timeout=0.1)
    except subprocess.TimeoutExpired:
        _signal_process_group(process.pid, signal.SIGKILL)
        process.wait(timeout=0.1)


def _signal_process_group(process_group: int, signal_number: int) -> None:
    try:
        os.killpg(process_group, signal_number)
    except (ProcessLookupError, PermissionError):
        pass


def _process_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True
