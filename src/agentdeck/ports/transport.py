"""Bounded asynchronous Agent transport values behind synchronous Ports."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from time import monotonic
from typing import Protocol

from agentdeck.ports.leader import LeaderFailure, LeaderFailureCode


_MAX_TEXT_BYTES = 1024 * 1024
_MAX_TRANSPORT_BYTES = 8 * 1024 * 1024
_MAX_TRANSPORT_TIMEOUT = 120.0


class TransportFailureCode(StrEnum):
    INITIALIZATION_FAILED = "initialization_failed"
    PROTOCOL_MISMATCH = "protocol_mismatch"
    CAPABILITY_MISSING = "capability_missing"
    SESSION_FAILED = "session_failed"
    PROMPT_FAILED = "prompt_failed"
    RESPONSE_OVERSIZE = "response_oversize"
    TIMEOUT = "timeout"
    DISCONNECTED = "disconnected"
    PERMISSION_INVALID = "permission_invalid"
    UNEXPECTED_SIDE_EFFECT = "unexpected_side_effect"
    CANCELLATION_FAILED = "cancellation_failed"
    STRUCTURED_RESULT_MISSING = "structured_result_missing"
    STRUCTURED_RESULT_INVALID = "structured_result_invalid"


_FAILURE_CATEGORY = {
    TransportFailureCode.RESPONSE_OVERSIZE: LeaderFailureCode.OVERSIZE,
    TransportFailureCode.TIMEOUT: LeaderFailureCode.TIMEOUT,
    TransportFailureCode.CANCELLATION_FAILED: LeaderFailureCode.CANCELLATION,
    TransportFailureCode.STRUCTURED_RESULT_MISSING: LeaderFailureCode.SCHEMA,
    TransportFailureCode.STRUCTURED_RESULT_INVALID: LeaderFailureCode.SCHEMA,
}


class TransportFailure(LeaderFailure):
    """Stable content-free failure from an Agent transport."""

    def __init__(self, code: TransportFailureCode | str) -> None:
        try:
            checked = code if type(code) is TransportFailureCode else TransportFailureCode(code)
        except (TypeError, ValueError):
            raise ValueError("unknown transport failure code") from None
        self.transport_code = checked
        self.code = _FAILURE_CATEGORY.get(checked, LeaderFailureCode.TRANSPORT)
        RuntimeError.__init__(self, f"ACP transport failed: {checked.value}")


class TransportDeadline:
    """One monotonic budget shared by all operations in a transport lifecycle."""

    def __init__(self, timeout_seconds: float) -> None:
        self._expires_at = monotonic() + timeout_seconds

    def remaining(self) -> float:
        return max(0.0, self._expires_at - monotonic())


class TransportUpdateKind(StrEnum):
    MESSAGE = "message"
    ARTIFACT = "artifact"
    PROGRESS = "progress"
    TOOL = "tool"
    PERMISSION = "permission"


def _text(value: object, field: str, *, maximum: int = _MAX_TEXT_BYTES) -> str:
    if type(value) is not str:
        raise TypeError(f"{field} must be a string")
    failed = False
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        failed = True
        encoded = b""
    if failed or not value.strip() or len(encoded) > maximum:
        raise ValueError(f"{field} must be nonempty bounded UTF-8") from None
    return value


def transport_argv(value: object) -> tuple[str, ...]:
    if type(value) not in {tuple, list} or not value:
        raise ValueError("ACP command must be a nonempty argv sequence")
    copied = tuple(value)
    invalid = any(
        type(item) is not str or not item or "\x00" in item for item in copied
    )
    try:
        oversized = any(
            len(item.encode("utf-8", "strict")) > 16 * 1024 for item in copied
        ) if not invalid else False
    except UnicodeEncodeError:
        oversized = True
    if invalid or oversized:
        raise ValueError("ACP command must be a bounded argv sequence") from None
    return copied


def transport_project_root(value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError("ACP project root must be a nonempty string")
    try:
        if len(value.encode("utf-8", "strict")) > 16 * 1024 or "\x00" in value:
            raise ValueError
    except (UnicodeEncodeError, ValueError):
        raise ValueError("ACP project root must be bounded UTF-8") from None
    return value


def transport_byte_bound(value: object) -> int:
    if type(value) is not int or not 1024 <= value <= _MAX_TRANSPORT_BYTES:
        raise ValueError("ACP max_bytes must be a positive response bound")
    return value


def transport_timeout(value: object) -> float:
    if type(value) not in {int, float}:
        raise ValueError("ACP timeout must be positive")
    checked = float(value)
    if not isfinite(checked) or not 0 < checked <= _MAX_TRANSPORT_TIMEOUT:
        raise ValueError("ACP timeout must be positive")
    return checked


def close_transport_awaitable(value: object) -> None:
    """Best-effort cleanup after an awaitable cannot become a task."""
    try:
        close = getattr(value, "close", None)
        if callable(close):
            close()
    except BaseException:
        return


@dataclass(frozen=True)
class TransportCapabilities:
    protocol_version: int
    embedded_context: bool
    load_session: bool
    resume_session: bool

    def __post_init__(self) -> None:
        if type(self.protocol_version) is not int or self.protocol_version <= 0:
            raise ValueError("protocol_version must be positive")
        if any(type(value) is not bool for value in (
            self.embedded_context, self.load_session, self.resume_session,
        )):
            raise TypeError("transport capability flags must be bool")


@dataclass(frozen=True)
class TransportSession:
    session_id: str

    def __post_init__(self) -> None:
        _text(self.session_id, "session_id", maximum=4096)


@dataclass(frozen=True)
class TransportPromptPart:
    kind: str
    text: str
    uri: str | None = None
    mime_type: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"text", "resource"}:
            raise ValueError("prompt part kind must be text or resource")
        _text(self.text, "prompt text")
        if self.kind == "text" and (self.uri is not None or self.mime_type is not None):
            raise ValueError("text prompt part cannot carry resource metadata")
        if self.kind == "resource":
            _text(self.uri, "resource uri", maximum=4096)
            _text(self.mime_type, "resource MIME type", maximum=256)

    @classmethod
    def text(cls, text: str) -> "TransportPromptPart":
        return cls(kind="text", text=text)

    @classmethod
    def resource(
        cls, *, uri: str, mime_type: str, text: str
    ) -> "TransportPromptPart":
        return cls(kind="resource", text=text, uri=uri, mime_type=mime_type)


@dataclass(frozen=True)
class TransportArtifact:
    uri: str
    mime_type: str
    text: str

    def __post_init__(self) -> None:
        _text(self.uri, "artifact uri", maximum=4096)
        _text(self.mime_type, "artifact MIME type", maximum=256)
        _text(self.text, "artifact text")


@dataclass(frozen=True)
class TransportPermissionRequest:
    request_id: str
    tool_call_id: str
    options: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        _text(self.request_id, "permission request_id", maximum=256)
        _text(self.tool_call_id, "permission tool_call_id", maximum=4096)
        if type(self.options) is not tuple or not self.options:
            raise ValueError("permission options must be a nonempty tuple")
        for option in self.options:
            if type(option) is not tuple or len(option) != 2:
                raise TypeError("permission option must be an id/kind tuple")
            _text(option[0], "permission option id", maximum=256)
            _text(option[1], "permission option kind", maximum=64)


@dataclass(frozen=True)
class TransportPermissionDecision:
    request_id: str
    allowed: bool
    reason: str

    def __post_init__(self) -> None:
        _text(self.request_id, "permission decision request_id", maximum=256)
        if type(self.allowed) is not bool:
            raise TypeError("permission decision allowed must be bool")
        _text(self.reason, "permission decision reason", maximum=4096)


@dataclass(frozen=True)
class TransportUpdate:
    session_id: str
    sequence: int
    kind: TransportUpdateKind
    text: str | None = None
    artifact: TransportArtifact | None = None
    permission: TransportPermissionRequest | None = None

    def __post_init__(self) -> None:
        _text(self.session_id, "update session_id", maximum=4096)
        if type(self.sequence) is not int or self.sequence <= 0:
            raise ValueError("update sequence must be positive")
        if type(self.kind) is not TransportUpdateKind:
            raise TypeError("update kind must be TransportUpdateKind")
        expected = {
            TransportUpdateKind.MESSAGE: (self.text is not None, self.artifact is None, self.permission is None),
            TransportUpdateKind.ARTIFACT: (self.text is None, self.artifact is not None, self.permission is None),
            TransportUpdateKind.PERMISSION: (self.text is None, self.artifact is None, self.permission is not None),
        }
        if self.kind in expected and not all(expected[self.kind]):
            raise ValueError("transport update payload does not match kind")
        if self.kind in {TransportUpdateKind.PROGRESS, TransportUpdateKind.TOOL} and any(
            value is not None for value in (self.text, self.artifact, self.permission)
        ):
            raise ValueError("transport signal update cannot carry payload")
        if self.text is not None:
            _text(self.text, "update text")


@dataclass(frozen=True)
class TransportPromptResult:
    stop_reason: str

    def __post_init__(self) -> None:
        _text(self.stop_reason, "prompt stop_reason", maximum=128)


class TransportPort(Protocol):
    async def initialize(self) -> TransportCapabilities: ...
    async def new_session(self) -> TransportSession: ...
    async def resume_session(self, session: TransportSession) -> TransportSession: ...
    async def prompt(
        self, session: TransportSession, parts: tuple[TransportPromptPart, ...]
    ) -> TransportPromptResult: ...
    def stream_updates(self, session: TransportSession) -> AsyncIterator[TransportUpdate]: ...
    async def respond_permission(
        self, session: TransportSession, decision: TransportPermissionDecision
    ) -> None: ...
    async def cancel(self, session: TransportSession) -> None: ...


__all__ = [
    "TransportArtifact", "TransportCapabilities", "TransportFailure",
    "TransportDeadline", "TransportFailureCode", "TransportPermissionDecision",
    "TransportPermissionRequest", "TransportPort", "TransportPromptPart",
    "TransportPromptResult", "TransportSession", "TransportUpdate",
    "TransportUpdateKind", "close_transport_awaitable", "transport_argv", "transport_byte_bound",
    "transport_project_root", "transport_timeout",
]
