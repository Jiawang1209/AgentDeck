"""Sealed passive ACP readiness evidence and execution projection."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from types import MappingProxyType
from typing import Final
from weakref import WeakValueDictionary

from agentdeck.adapters.codex_app_server_probe import (
    FROZEN_CODEX_VERSION, FROZEN_SERVER_VERSION, FROZEN_STABLE_SCHEMA_DIGEST,
)
from agentdeck.adapters.discovery_process import MAX_VERSION_BYTES


MAX_PATH_BYTES: Final = 4096
CLAUDE_ADAPTER_VERSION: Final = "claude-agent-acp 0.58.1"
_MAX_ENVIRONMENT_BYTES: Final = 64 * 1024
_MAX_ENVIRONMENT_ITEMS: Final = 64
_ENVIRONMENT_NAME: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_VERSION: Final = re.compile(
    r"^[A-Za-z][A-Za-z0-9._+-]{0,63} "
    r"[0-9]{1,6}(?:\.[0-9]{1,6}){1,3}$"
)
_ISSUED: WeakValueDictionary[int, AdapterReadiness] = WeakValueDictionary()


@dataclass(frozen=True)
class AdapterDiagnostic:
    code: str


@dataclass(frozen=True, slots=True, weakref_slot=True)
class AdapterReadiness:
    backend_id: str
    ready: bool
    command: tuple[str, ...] | None
    version: str | None
    diagnostic: AdapterDiagnostic | None
    fallbacks: tuple[str, ...] = ()
    cli_path: str | None = None
    cli_version: str | None = None
    adapter_path: str | None = None
    adapter_version: str | None = None
    schema_digest: str | None = None
    environment: tuple[tuple[str, str], ...] = ()


def public_text(value: object, *, maximum: int, trimmed: bool) -> bool:
    if type(value) is not str or not value or trimmed and value != value.strip():
        return False
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return False
    return len(encoded) <= maximum and all(char.isprintable() for char in value)


def exact_absolute_path(value: object, basename: str | None = None) -> bool:
    if not public_text(value, maximum=MAX_PATH_BYTES, trimmed=False):
        return False
    path = Path(value)
    return path.is_absolute() and (basename is None or path.name == basename)


def canonical_project_root(value: object) -> str:
    if not exact_absolute_path(value):
        raise ValueError("project_root must be a canonical absolute path")
    try:
        resolved = str(Path(value).resolve(strict=False))
    except OSError:
        raise ValueError("project_root must be a canonical absolute path") from None
    if resolved != value:
        raise ValueError("project_root must be a canonical absolute path")
    return value


def canonical_named_version(value: object, name: str) -> bool:
    return (
        public_text(value, maximum=MAX_VERSION_BYTES, trimmed=True)
        and _VERSION.fullmatch(value) is not None
        and value.startswith(f"{name} ")
    )


def canonical_backend_version(backend_id: object, value: object) -> bool:
    if type(backend_id) is not str:
        return False
    if backend_id == "codex-cli":
        return type(value) is str and value == FROZEN_SERVER_VERSION
    return backend_id == "claude-cli" and canonical_named_version(
        value, "claude-cli"
    )


def blocked_readiness(backend_id: str, code: str) -> AdapterReadiness:
    return AdapterReadiness(
        backend_id, False, None, None, AdapterDiagnostic(code),
    )


def _issue_readiness(
    *, backend_id: str, command: tuple[str, ...], version: str,
    cli_path: str, cli_version: str, adapter_path: str,
    adapter_version: str, schema_digest: str | None,
    environment: tuple[tuple[str, str], ...],
) -> AdapterReadiness:
    value = AdapterReadiness(
        backend_id=backend_id, ready=True, command=command, version=version,
        diagnostic=None, fallbacks=(), cli_path=cli_path,
        cli_version=cli_version, adapter_path=adapter_path,
        adapter_version=adapter_version, schema_digest=schema_digest,
        environment=environment,
    )
    _ISSUED[id(value)] = value
    return value


def verified_readiness(value: object, backend_id: str) -> bool:
    if (
        type(value) is not AdapterReadiness
        or _ISSUED.get(id(value)) is not value
        or type(value.backend_id) is not str or value.backend_id != backend_id
        or value.ready is not True or value.diagnostic is not None
        or type(value.fallbacks) is not tuple or value.fallbacks != ()
        or type(value.command) is not tuple
        or type(value.environment) is not tuple
        or not canonical_backend_version(backend_id, value.version)
    ):
        return False
    if backend_id == "codex-cli":
        encoded = json.dumps(
            [value.cli_path, "app-server"], separators=(",", ":")
        )
        return (
            value.command == (
                value.adapter_path, "--app-server-command-json", encoded,
            )
            and exact_absolute_path(value.cli_path, "codex")
            and type(value.cli_version) is str
            and value.cli_version == FROZEN_CODEX_VERSION
            and exact_absolute_path(value.adapter_path, "agentdeck-codex-acp")
            and type(value.adapter_version) is str
            and value.adapter_version == FROZEN_SERVER_VERSION
            and type(value.schema_digest) is str
            and value.schema_digest == FROZEN_STABLE_SCHEMA_DIGEST
            and value.environment == ()
        )
    return (
        backend_id == "claude-cli"
        and value.command == (value.adapter_path,)
        and exact_absolute_path(value.cli_path, "claude")
        and canonical_named_version(value.cli_version, "claude-cli")
        and value.version == value.cli_version
        and exact_absolute_path(value.adapter_path, "claude-agent-acp")
        and value.adapter_version == CLAUDE_ADAPTER_VERSION
        and value.schema_digest is None
        and value.environment == (("CLAUDE_CODE_EXECUTABLE", value.cli_path),)
    )


def execution_command(
    readiness: AdapterReadiness, *, model: str,
) -> tuple[str, ...]:
    if not verified_readiness(readiness, readiness.backend_id):
        raise ValueError("ACP adapter is not ready")
    if not public_text(model, maximum=4096, trimmed=True):
        raise ValueError("ACP model is invalid")
    assert readiness.command is not None
    if readiness.backend_id == "claude-cli":
        return readiness.command
    return readiness.command + ("--model", model,)


def bounded_environment(
    value: Mapping[str, str] | Sequence[tuple[str, str]] | None,
) -> Mapping[str, str]:
    items = () if value is None else tuple(value.items() if isinstance(value, Mapping) else value)
    if len(items) > _MAX_ENVIRONMENT_ITEMS:
        raise ValueError("ACP environment is invalid")
    copied: dict[str, str] = {}
    total = 0
    for item in items:
        if type(item) is not tuple or len(item) != 2:
            raise ValueError("ACP environment is invalid")
        key, entry = item
        if type(key) is not str or _ENVIRONMENT_NAME.fullmatch(key) is None:
            raise ValueError("ACP environment is invalid")
        if not public_text(entry, maximum=MAX_PATH_BYTES, trimmed=False):
            raise ValueError("ACP environment is invalid")
        total += len(key.encode("utf-8")) + len(entry.encode("utf-8"))
        if total > _MAX_ENVIRONMENT_BYTES or key in copied:
            raise ValueError("ACP environment is invalid")
        copied[key] = entry
    return MappingProxyType(copied)


def merged_environment(
    value: Mapping[str, str] | Sequence[tuple[str, str]] | None,
) -> Mapping[str, str]:
    merged = dict(os.environ)
    merged.update(bounded_environment(value))
    return MappingProxyType(merged)


__all__ = [
    "AdapterDiagnostic", "AdapterReadiness", "CLAUDE_ADAPTER_VERSION",
    "blocked_readiness", "bounded_environment", "canonical_backend_version",
    "canonical_named_version", "canonical_project_root", "exact_absolute_path",
    "execution_command", "merged_environment",
    "public_text", "verified_readiness",
]
