from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from types import MappingProxyType

from agentdeck.adapters.discovery_process import (
    MAX_EXECUTABLE_BYTES,
    MAX_TMUX_METADATA_BYTES,
    MAX_VERSION_BYTES,
    ResolvedExecutable,
    VersionProbeInvalid,
    VersionProbeOversize,
    bounded_tmux_version_reader,
    bounded_version_runner,
    descriptor_digest,
    executable_signature,
)
from agentdeck.adapters.input_snapshot import snapshot_mapping


VersionRunner = Callable[[str], str | bytes]
PassiveProbe = Callable[[str], bool]

_MAX_PATH_BYTES = 4096
_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_COMMAND_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_CAPABILITY_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_VERSION_PREFIX = re.compile(
    r"^(?:(?P<name>[A-Za-z][A-Za-z0-9._+-]{0,63})[ \t]+)?"
    r"(?P<version>[0-9]{1,6}(?:\.[0-9]{1,6}){1,3}(?:[A-Za-z][0-9]{0,4})?)"
    r"(?=$|[ \t\r\n(])"
)
_DEFAULT_VERSION_ARGUMENTS = ("--version",)
_TOOL_VERSION_ARGUMENTS = MappingProxyType(
    {
        "codex": ("--version",),
        "claude": ("--version",),
    }
)

_DEFAULT_TOOLS = MappingProxyType(
    {
        "codex": "codex",
        "claude": "claude",
        "tmux": "tmux",
    }
)
_DEFAULT_CAPABILITIES = MappingProxyType(
    {
        "codex": ("manual_cli", "leader", "worker", "acp"),
        "claude": ("manual_cli", "leader", "worker", "acp"),
        "tmux": ("observer", "human_takeover"),
    }
)


class ReadinessState(StrEnum):
    MISSING = "missing"
    DISCOVERED = "discovered"
    AUTHENTICATED = "authenticated"
    ACP_AVAILABLE = "acp_available"
    READY = "ready"


@dataclass(frozen=True)
class ToolDiscovery:
    name: str
    command: str
    resolved_path: str | None
    version: str | None
    authenticated: bool
    acp_available: bool
    readiness: ReadinessState
    capabilities: tuple[str, ...]
    diagnostics: tuple[str, ...] = ()


def discover_tools(
    *,
    path: str | None = None,
    version_runner: VersionRunner | None = None,
    auth_probes: Mapping[str, PassiveProbe] | None = None,
    acp_probes: Mapping[str, PassiveProbe] | None = None,
    tools: Mapping[str, str] | None = None,
    capability_metadata: Mapping[str, tuple[str, ...]] | None = None,
) -> Mapping[str, ToolDiscovery]:
    search_path = _search_path(path)
    tool_commands = _copy_tools(_DEFAULT_TOOLS if tools is None else tools)
    authentication = _copy_probes(auth_probes)
    acp = _copy_probes(acp_probes)
    capabilities = _copy_capabilities(capability_metadata)
    discovered: dict[str, ToolDiscovery] = {}
    for name, command in tool_commands.items():
        tool_capabilities = capabilities.get(
            name, _DEFAULT_CAPABILITIES.get(name, ("manual_cli",))
        )
        executable = _find_executable(
            command,
            search_path,
            maximum_bytes=(
                MAX_TMUX_METADATA_BYTES if name == "tmux" else MAX_EXECUTABLE_BYTES
            ),
        )
        if executable is None:
            discovered[name] = _missing_fact(name, command, tool_capabilities)
            continue
        try:
            discovered[name] = _discover_tool(
                name,
                command,
                tool_capabilities,
                executable,
                search_path=search_path,
                version_arguments=_TOOL_VERSION_ARGUMENTS.get(
                    name, _DEFAULT_VERSION_ARGUMENTS
                ),
                version_runner=version_runner,
                authentication_probe=authentication.get(name),
                acp_probe=acp.get(name),
            )
        finally:
            os.close(executable.descriptor)
    return MappingProxyType(discovered)


def _discover_tool(
    name: str,
    command: str,
    capabilities: tuple[str, ...],
    executable: ResolvedExecutable,
    *,
    search_path: str,
    version_arguments: tuple[str, ...],
    version_runner: VersionRunner | None,
    authentication_probe: PassiveProbe | None,
    acp_probe: PassiveProbe | None,
) -> ToolDiscovery:
    runner = version_runner or (
        lambda _: _default_version_output(
            name,
            executable,
            version_arguments=version_arguments,
            search_path=search_path,
        )
    )
    version, version_diagnostics = _probe_version(runner, executable.path)
    if not _same_executable(executable):
        return _missing_fact(
            name,
            command,
            capabilities,
            diagnostics=version_diagnostics + ("resolved_path_changed",),
        )
    authenticated, auth_diagnostics = _probe_boolean(
        authentication_probe, executable.path, kind="authentication"
    )
    if not _same_executable(executable):
        return _missing_fact(
            name,
            command,
            capabilities,
            diagnostics=version_diagnostics
            + auth_diagnostics
            + ("resolved_path_changed",),
        )
    acp_available, acp_diagnostics = _probe_boolean(
        acp_probe, executable.path, kind="acp"
    )
    diagnostics = version_diagnostics + auth_diagnostics + acp_diagnostics
    if not _same_executable(executable):
        return _missing_fact(
            name,
            command,
            capabilities,
            diagnostics=diagnostics + ("resolved_path_changed",),
        )
    return ToolDiscovery(
        name=name,
        command=command,
        resolved_path=executable.path,
        version=version,
        authenticated=authenticated,
        acp_available=acp_available,
        readiness=_readiness(authenticated, acp_available),
        capabilities=capabilities,
        diagnostics=diagnostics,
    )


def _default_version_output(
    name: str,
    executable: ResolvedExecutable,
    *,
    version_arguments: tuple[str, ...],
    search_path: str,
) -> bytes:
    if name == "tmux":
        return bounded_tmux_version_reader(executable)
    return bounded_version_runner(
        executable,
        arguments=version_arguments,
        search_path=search_path,
    )


def _search_path(path: str | None) -> str:
    raw_path = os.environ.get("PATH", "") if path is None else path
    if type(raw_path) is not str:
        raise TypeError("discovery PATH must be a string or None")
    _bounded_utf8(
        raw_path, maximum=_MAX_PATH_BYTES, message="discovery PATH is invalid"
    )
    safe_components = tuple(
        component
        for component in raw_path.split(os.pathsep)
        if component and os.path.isabs(component)
    )
    return os.pathsep.join(safe_components)


def _copy_tools(tools: Mapping[str, str]) -> Mapping[str, str]:
    copied: dict[str, str] = {}
    for name, command in snapshot_mapping(tools, label="discovery tools"):
        if type(name) is not str or not _TOOL_NAME.fullmatch(name):
            raise ValueError("discovery tool name is invalid")
        if type(command) is not str or not _COMMAND_NAME.fullmatch(command):
            raise ValueError("discovery command is invalid")
        copied[name] = command
    return MappingProxyType(copied)


def _copy_probes(
    probes: Mapping[str, PassiveProbe] | None,
) -> Mapping[str, PassiveProbe]:
    if probes is None:
        return MappingProxyType({})
    copied: dict[str, PassiveProbe] = {}
    for name, probe in snapshot_mapping(probes, label="discovery probes"):
        if type(name) is not str or not _TOOL_NAME.fullmatch(name):
            raise ValueError("discovery probe name is invalid")
        if not callable(probe):
            raise TypeError("discovery probe must be callable")
        copied[name] = probe
    return MappingProxyType(copied)


def _copy_capabilities(
    metadata: Mapping[str, tuple[str, ...]] | None,
) -> Mapping[str, tuple[str, ...]]:
    if metadata is None:
        return MappingProxyType({})
    copied: dict[str, tuple[str, ...]] = {}
    for name, values in snapshot_mapping(metadata, label="capability metadata"):
        if type(name) is not str or not _TOOL_NAME.fullmatch(name):
            raise ValueError("capability tool name is invalid")
        if type(values) is not tuple or any(
            type(value) is not str or not _CAPABILITY_NAME.fullmatch(value)
            for value in values
        ):
            raise ValueError("capability metadata is invalid")
        copied[name] = tuple(values)
    return MappingProxyType(copied)


def _find_executable(
    command: str, search_path: str, *, maximum_bytes: int
) -> ResolvedExecutable | None:
    if not search_path:
        return None
    candidate = shutil.which(command, path=search_path)
    if candidate is None:
        return None
    try:
        resolved = Path(candidate).resolve(strict=True)
        encoded = str(resolved).encode("utf-8", errors="strict")
        details = resolved.stat()
    except (OSError, UnicodeError):
        return None
    if (
        not resolved.is_absolute()
        or len(encoded) > _MAX_PATH_BYTES
        or not stat.S_ISREG(details.st_mode)
        or not os.access(resolved, os.X_OK)
    ):
        return None
    descriptor = -1
    try:
        descriptor = os.open(
            resolved,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        signature = executable_signature(details)
        if executable_signature(os.fstat(descriptor)) != signature:
            os.close(descriptor)
            return None
        initial_digest = (
            descriptor_digest(descriptor, signature[3])
            if signature[3] <= maximum_bytes
            else None
        )
        if executable_signature(os.fstat(descriptor)) != signature:
            os.close(descriptor)
            return None
    except (OSError, RuntimeError):
        if descriptor >= 0:
            os.close(descriptor)
        return None
    return ResolvedExecutable(
        path=str(resolved),
        signature=signature,
        descriptor=descriptor,
        initial_digest=initial_digest,
    )


def _same_executable(executable: ResolvedExecutable) -> bool:
    try:
        path = Path(executable.path)
        details = path.stat()
        return (
            path.is_absolute()
            and path.resolve(strict=True) == path
            and stat.S_ISREG(details.st_mode)
            and os.access(path, os.X_OK)
            and executable_signature(details) == executable.signature
        )
    except OSError:
        return False


def _probe_version(
    runner: VersionRunner, resolved_path: str
) -> tuple[str | None, tuple[str, ...]]:
    try:
        raw = runner(resolved_path)
    except (TimeoutError, subprocess.TimeoutExpired):
        return None, ("version_probe_timeout",)
    except VersionProbeInvalid:
        return None, ("version_probe_invalid",)
    except VersionProbeOversize:
        return None, ("version_probe_oversize",)
    except Exception:
        return None, ("version_probe_failed",)
    if type(raw) is str:
        try:
            encoded = raw.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            return None, ("version_probe_invalid_utf8",)
    elif type(raw) is bytes:
        encoded = raw
    else:
        return None, ("version_probe_invalid",)
    if len(encoded) > MAX_VERSION_BYTES:
        return None, ("version_probe_oversize",)
    try:
        decoded = encoded.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None, ("version_probe_invalid_utf8",)
    if not decoded.strip():
        return None, ("version_probe_empty",)
    match = _VERSION_PREFIX.match(decoded)
    if match is None:
        return None, ("version_probe_invalid",)
    version = match.group("version")
    name = match.group("name")
    rendered = f"{name} {version}" if name else version
    return rendered, ()


def _probe_boolean(
    probe: PassiveProbe | None,
    resolved_path: str,
    *,
    kind: str,
) -> tuple[bool, tuple[str, ...]]:
    if probe is None:
        return False, ()
    try:
        value = probe(resolved_path)
    except Exception:
        return False, (f"{kind}_probe_failed",)
    if type(value) is not bool:
        return False, (f"{kind}_probe_invalid",)
    return value, ()


def _readiness(authenticated: bool, acp_available: bool) -> ReadinessState:
    if authenticated and acp_available:
        return ReadinessState.READY
    if authenticated:
        return ReadinessState.AUTHENTICATED
    if acp_available:
        return ReadinessState.ACP_AVAILABLE
    return ReadinessState.DISCOVERED


def _missing_fact(
    name: str,
    command: str,
    capabilities: tuple[str, ...],
    *,
    diagnostics: tuple[str, ...] = (),
) -> ToolDiscovery:
    return ToolDiscovery(
        name=name,
        command=command,
        resolved_path=None,
        version=None,
        authenticated=False,
        acp_available=False,
        readiness=ReadinessState.MISSING,
        capabilities=capabilities,
        diagnostics=diagnostics,
    )


def _bounded_utf8(value: str, *, maximum: int, message: str) -> None:
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise ValueError(message) from None
    if len(encoded) > maximum:
        raise ValueError(message)
