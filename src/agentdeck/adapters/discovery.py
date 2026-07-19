from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
import json
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
from agentdeck.adapters.codex_app_server_probe import (
    FROZEN_CODEX_VERSION, FROZEN_SERVER_VERSION, FROZEN_STABLE_SCHEMA_DIGEST,
)
from agentdeck.adapters.adapter_readiness import (
    AdapterDiagnostic, AdapterReadiness, CLAUDE_ADAPTER_VERSION,
    MAX_PATH_BYTES as _MAX_PATH_BYTES,
    blocked_readiness, canonical_backend_version, canonical_named_version,
    exact_absolute_path, _issue_readiness,
)


VersionRunner = Callable[[str], str | bytes]
PassiveProbe = Callable[[str], bool]

_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_COMMAND_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_CAPABILITY_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_VERSION_PREFIX = re.compile(
    r"^(?:(?P<name>[A-Za-z][A-Za-z0-9._+-]{0,63})[ \t]+)?"
    r"(?P<version>[0-9]{1,6}(?:\.[0-9]{1,6}){1,3}(?:[A-Za-z][0-9]{0,4})?)"
    r"(?=$|[ \t\r\n(])"
)
_DEFAULT_VERSION_ARGUMENTS = ("--version",)
_TOOL_VERSION_ARGUMENTS = MappingProxyType({
    "codex": ("--version",), "claude": ("--version",),
})
_DEFAULT_TOOLS = MappingProxyType({
    "codex": "codex", "claude": "claude", "tmux": "tmux",
})
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

@dataclass(frozen=True)
class CodexAdapterFacts:
    cli_path: str | None; cli_version: str | None
    app_server_available: bool; app_server_version: str | None
    bridge_path: str | None; schema_digest: str | None
@dataclass(frozen=True)
class ClaudeAdapterFacts:
    cli_path: str | None; cli_version: str | None; authenticated: bool
    adapter_path: str | None; adapter_version: str | None
def _adapter_blocked(backend_id: str, code: str) -> AdapterReadiness:
    return blocked_readiness(backend_id, code)

def _named(path: object, name: str) -> bool:
    return exact_absolute_path(path, name)

def _canonical_version(value: object, name: str) -> bool:
    return canonical_named_version(value, name)

def canonical_adapter_version(backend_id: object, value: object) -> bool:
    return canonical_backend_version(backend_id, value)

def classify_codex(facts: CodexAdapterFacts) -> AdapterReadiness:
    """Classify injected passive facts; never probe or start the adapter."""
    if type(facts) is not CodexAdapterFacts or not _named(facts.cli_path, "codex"):
        return _adapter_blocked("codex-cli", "codex_cli_missing")
    if facts.app_server_available is not True:
        return _adapter_blocked("codex-cli", "codex_app_server_missing")
    if not _named(facts.bridge_path, "agentdeck-codex-acp"):
        return _adapter_blocked("codex-cli", "codex_acp_bridge_missing")
    if type(facts.cli_version) is not str or facts.cli_version != FROZEN_CODEX_VERSION or not canonical_adapter_version("codex-cli", facts.app_server_version):
        return _adapter_blocked("codex-cli", "codex_app_server_version_drift")
    if type(facts.schema_digest) is not str or facts.schema_digest != FROZEN_STABLE_SCHEMA_DIGEST:
        return _adapter_blocked("codex-cli", "codex_app_server_schema_drift")
    encoded = json.dumps([facts.cli_path, "app-server"], separators=(",", ":"))
    return _issue_readiness(
        backend_id="codex-cli", command=(
            facts.bridge_path, "--app-server-command-json", encoded,
        ),
        version=FROZEN_SERVER_VERSION, cli_path=facts.cli_path,
        cli_version=facts.cli_version, adapter_path=facts.bridge_path,
        adapter_version=facts.app_server_version,
        schema_digest=facts.schema_digest, environment=(),
    )


def classify_claude(facts: ClaudeAdapterFacts) -> AdapterReadiness:
    """Classify explicit CLI login and adapter facts without authentication I/O."""
    if type(facts) is not ClaudeAdapterFacts or not _named(facts.cli_path, "claude"):
        return _adapter_blocked("claude-cli", "claude_cli_missing")
    if facts.authenticated is not True:
        return _adapter_blocked("claude-cli", "claude_authentication_missing")
    if not _named(facts.adapter_path, "claude-agent-acp"):
        return _adapter_blocked("claude-cli", "claude_acp_missing")
    if facts.adapter_version is None:
        return _adapter_blocked("claude-cli", "claude_acp_missing")
    if type(facts.adapter_version) is not str or facts.adapter_version != CLAUDE_ADAPTER_VERSION:
        return _adapter_blocked("claude-cli", "claude_acp_version_drift")
    if not canonical_adapter_version("claude-cli", facts.cli_version):
        return _adapter_blocked("claude-cli", "claude_cli_missing")
    return _issue_readiness(
        backend_id="claude-cli", command=(facts.adapter_path,),
        version=facts.cli_version, cli_path=facts.cli_path,
        cli_version=facts.cli_version, adapter_path=facts.adapter_path,
        adapter_version=facts.adapter_version, schema_digest=None,
        environment=(("CLAUDE_CODE_EXECUTABLE", facts.cli_path),),
    )


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
