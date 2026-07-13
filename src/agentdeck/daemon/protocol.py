"""Pure bounded daemon RPC envelopes and newline-delimited JSON framing.

``max_bytes`` always counts the complete encoded frame, including its final LF.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
import json
import math
from types import MappingProxyType
from typing import TypeAlias


DAEMON_RPC_PROTOCOL_VERSION = "daemon-rpc/v1"
READ_ONLY_METHODS = frozenset({"handshake", "status"})

JsonValue: TypeAlias = (
    None
    | bool
    | int
    | float
    | str
    | list["JsonValue"]
    | dict[str, "JsonValue"]
)

_REQUEST_FIELDS = {"request_id", "method", "params"}
_RESPONSE_FIELDS = {"request_id", "ok", "result", "error"}
_EVENT_FIELDS = {"event_id", "revision", "kind", "summary"}
_ERROR_FIELDS = {"code", "message"}
_HANDSHAKE_FIELDS = {
    "project_root_hash",
    "client_version",
    "protocol_version",
}
_MAX_JSON_NESTING = 64


class _FrozenJsonList(Sequence[object]):
    __slots__ = ("_items",)

    def __init__(self, items: Sequence[object]) -> None:
        object.__setattr__(self, "_items", tuple(items))

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("frozen JSON list is immutable")

    def __getitem__(self, index: int | slice) -> object:
        return self._items[index]

    def __len__(self) -> int:
        return len(self._items)

    def __eq__(self, other: object) -> bool:
        if type(other) not in {list, tuple, _FrozenJsonList}:
            return False
        return tuple(self) == tuple(other)  # type: ignore[arg-type]

    def __repr__(self) -> str:
        return repr(list(self._items))


class RpcProtocolError(ValueError):
    """A bounded protocol failure that never includes caller-controlled content."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class RpcRequest:
    request_id: str
    method: str
    params: dict[str, JsonValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", _detached_json(self.params))

    @classmethod
    def handshake(
        cls,
        *,
        request_id: str,
        project_root_hash: str,
        client_version: str,
        protocol_version: str = DAEMON_RPC_PROTOCOL_VERSION,
    ) -> "RpcRequest":
        request = cls(
            request_id=request_id,
            method="handshake",
            params={
                "project_root_hash": project_root_hash,
                "client_version": client_version,
                "protocol_version": protocol_version,
            },
        )
        _validate_request(request, READ_ONLY_METHODS)
        return request


@dataclass(frozen=True)
class RpcResponse:
    request_id: str
    ok: bool
    result: dict[str, JsonValue] | None
    error: dict[str, JsonValue] | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "result", _detached_json(self.result))
        object.__setattr__(self, "error", _detached_json(self.error))


@dataclass(frozen=True)
class RpcEvent:
    event_id: str
    revision: int
    kind: str
    summary: dict[str, JsonValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "summary", _detached_json(self.summary))


class _DuplicateJsonKey(ValueError):
    pass


def _error(code: str, message: str) -> RpcProtocolError:
    return RpcProtocolError(code, message)


def _valid_utf8_string(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _required_string(value: object) -> bool:
    return (
        type(value) is str
        and bool(value.strip())
        and _valid_utf8_string(value)
    )


def _invalid_json_value() -> RpcProtocolError:
    return _error(
        "invalid_json_value",
        "envelope contains an invalid JSON value",
    )


def _freeze_json(value: object, active: set[int], depth: int) -> object:
    if depth > _MAX_JSON_NESTING:
        raise _invalid_json_value()
    if type(value) in {dict, MappingProxyType}:
        identity = id(value)
        if identity in active:
            raise _invalid_json_value()
        active.add(identity)
        try:
            frozen = {
                key: _freeze_json(item, active, depth + 1)
                for key, item in value.items()  # type: ignore[union-attr]
            }
        finally:
            active.remove(identity)
        return MappingProxyType(frozen)
    if type(value) in {list, _FrozenJsonList}:
        identity = id(value)
        if identity in active:
            raise _invalid_json_value()
        active.add(identity)
        try:
            return _FrozenJsonList(
                [_freeze_json(item, active, depth + 1) for item in value]
            )
        finally:
            active.remove(identity)
    return value


def _detached_json(value: object) -> object:
    try:
        return _freeze_json(value, set(), 0)
    except RecursionError:
        raise _invalid_json_value() from None


def _is_json_value(value: object, active: set[int] | None = None) -> bool:
    if value is None or type(value) in {bool, int}:
        return True
    if type(value) is str:
        return _valid_utf8_string(value)
    if type(value) is float:
        return math.isfinite(value)
    if type(value) not in {list, _FrozenJsonList, dict, MappingProxyType}:
        return False

    active = active if active is not None else set()
    identity = id(value)
    if identity in active:
        return False
    active.add(identity)
    try:
        if type(value) in {list, _FrozenJsonList}:
            return all(_is_json_value(item, active) for item in value)
        return all(
            type(key) is str
            and _valid_utf8_string(key)
            and _is_json_value(item, active)
            for key, item in value.items()  # type: ignore[union-attr]
        )
    except RecursionError:
        return False
    finally:
        active.remove(identity)


def _valid_json_object(value: object) -> bool:
    return type(value) in {dict, MappingProxyType} and _is_json_value(value)


def _validate_allowed_methods(allowed_methods: Collection[str]) -> frozenset[str]:
    if isinstance(allowed_methods, (str, bytes)):
        raise _error("invalid_method_allowlist", "request method allowlist is invalid")
    try:
        methods = frozenset(allowed_methods)
    except TypeError:
        raise _error("invalid_method_allowlist", "request method allowlist is invalid") from None
    if any(not _required_string(method) for method in methods):
        raise _error("invalid_method_allowlist", "request method allowlist is invalid")
    return methods


def _validate_handshake_params(params: object) -> None:
    if (
        type(params) not in {dict, MappingProxyType}
        or set(params) != _HANDSHAKE_FIELDS
        or not all(_required_string(params[field]) for field in _HANDSHAKE_FIELDS)
        or not _is_json_value(params)
    ):
        raise _error("invalid_handshake", "handshake parameters are invalid")


def _validate_request(
    request: RpcRequest, allowed_methods: Collection[str]
) -> None:
    methods = _validate_allowed_methods(allowed_methods)
    if (
        not isinstance(request, RpcRequest)
        or not _required_string(request.request_id)
        or not _required_string(request.method)
        or not _valid_json_object(request.params)
    ):
        raise _error("invalid_request", "request envelope is invalid")
    if request.method not in methods:
        raise _error("method_not_allowed", "request method is not allowed")
    if request.method == "handshake":
        _validate_handshake_params(request.params)


def _validate_response(response: RpcResponse) -> None:
    valid = (
        isinstance(response, RpcResponse)
        and _required_string(response.request_id)
        and type(response.ok) is bool
    )
    if valid and response.ok:
        valid = _valid_json_object(response.result) and response.error is None
    elif valid:
        valid = (
            response.result is None
            and type(response.error) in {dict, MappingProxyType}
            and set(response.error) == _ERROR_FIELDS
            and _required_string(response.error.get("code"))
            and _required_string(response.error.get("message"))
            and _is_json_value(response.error)
        )
    if not valid:
        raise _error("invalid_response", "response envelope is invalid")


def _validate_event(event: RpcEvent) -> None:
    if (
        not isinstance(event, RpcEvent)
        or not _required_string(event.event_id)
        or type(event.revision) is not int
        or event.revision < 0
        or not _required_string(event.kind)
        or not _valid_json_object(event.summary)
    ):
        raise _error("invalid_event", "event envelope is invalid")


def _validate_max_bytes(max_bytes: int) -> None:
    if type(max_bytes) is not int or max_bytes <= 0:
        raise _error("invalid_max_bytes", "maximum frame size is invalid")


def _encode_object(payload: Mapping[str, object], *, max_bytes: int) -> bytes:
    _validate_max_bytes(max_bytes)
    try:
        encoded = json.dumps(
            _thaw_json(payload),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
    except (TypeError, ValueError, RecursionError):
        raise _invalid_json_value() from None
    if len(encoded) > max_bytes:
        raise _error("frame_too_large", "frame exceeds maximum size")
    return encoded


def _thaw_json(value: object) -> object:
    if type(value) in {dict, MappingProxyType}:
        return {
            key: _thaw_json(item)
            for key, item in value.items()  # type: ignore[union-attr]
        }
    if type(value) in {list, _FrozenJsonList}:
        return [_thaw_json(item) for item in value]
    return value


def encode_request(request: RpcRequest, *, max_bytes: int) -> bytes:
    _validate_request(request, READ_ONLY_METHODS)
    return _encode_object(
        {
            "request_id": request.request_id,
            "method": request.method,
            "params": request.params,
        },
        max_bytes=max_bytes,
    )


def encode_response(response: RpcResponse, *, max_bytes: int) -> bytes:
    _validate_response(response)
    return _encode_object(
        {
            "request_id": response.request_id,
            "ok": response.ok,
            "result": response.result,
            "error": response.error,
        },
        max_bytes=max_bytes,
    )


def encode_event(event: RpcEvent, *, max_bytes: int) -> bytes:
    _validate_event(event)
    return _encode_object(
        {
            "event_id": event.event_id,
            "revision": event.revision,
            "kind": event.kind,
            "summary": event.summary,
        },
        max_bytes=max_bytes,
    )


def _reject_constant(_: str) -> object:
    raise ValueError("non-finite number")


def _object_without_duplicates(
    pairs: Sequence[tuple[str, JsonValue]],
) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _decode_object(frame: bytes, *, max_bytes: int) -> dict[str, JsonValue]:
    _validate_max_bytes(max_bytes)
    if type(frame) is not bytes:
        raise _error("invalid_frame", "frame is invalid")
    if len(frame) > max_bytes:
        raise _error("frame_too_large", "frame exceeds maximum size")
    if not frame or not frame.endswith(b"\n"):
        raise _error("incomplete_frame", "frame is incomplete")
    if frame.count(b"\n") != 1:
        raise _error("invalid_frame", "frame must contain exactly one JSON document")

    try:
        document = frame[:-1].decode("utf-8")
    except UnicodeDecodeError:
        raise _error("invalid_utf8", "frame is not valid UTF-8") from None
    try:
        value = json.loads(
            document,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, _DuplicateJsonKey, ValueError, RecursionError):
        raise _error("invalid_json", "JSON document is invalid") from None
    if type(value) is not dict or not _is_json_value(value):
        raise _error("invalid_json_object", "JSON document must be an object")
    return value


def decode_request(
    frame: bytes,
    *,
    max_bytes: int,
    allowed_methods: Collection[str] = READ_ONLY_METHODS,
) -> RpcRequest:
    payload = _decode_object(frame, max_bytes=max_bytes)
    if set(payload) != _REQUEST_FIELDS:
        raise _error("invalid_request", "request envelope is invalid")
    request = RpcRequest(
        request_id=payload["request_id"],  # type: ignore[arg-type]
        method=payload["method"],  # type: ignore[arg-type]
        params=payload["params"],  # type: ignore[arg-type]
    )
    _validate_request(request, allowed_methods)
    return request


def decode_response(frame: bytes, *, max_bytes: int) -> RpcResponse:
    payload = _decode_object(frame, max_bytes=max_bytes)
    if set(payload) != _RESPONSE_FIELDS:
        raise _error("invalid_response", "response envelope is invalid")
    response = RpcResponse(
        request_id=payload["request_id"],  # type: ignore[arg-type]
        ok=payload["ok"],  # type: ignore[arg-type]
        result=payload["result"],  # type: ignore[arg-type]
        error=payload["error"],  # type: ignore[arg-type]
    )
    _validate_response(response)
    return response


def decode_event(frame: bytes, *, max_bytes: int) -> RpcEvent:
    payload = _decode_object(frame, max_bytes=max_bytes)
    if set(payload) != _EVENT_FIELDS:
        raise _error("invalid_event", "event envelope is invalid")
    event = RpcEvent(
        event_id=payload["event_id"],  # type: ignore[arg-type]
        revision=payload["revision"],  # type: ignore[arg-type]
        kind=payload["kind"],  # type: ignore[arg-type]
        summary=payload["summary"],  # type: ignore[arg-type]
    )
    _validate_event(event)
    return event


def negotiate_handshake(
    request: RpcRequest,
    *,
    project_root_hash: str,
    daemon_version: str,
    project_view_version: str,
) -> dict[str, JsonValue]:
    if not isinstance(request, RpcRequest) or request.method != "handshake":
        raise _error("invalid_handshake_request", "handshake request is invalid")
    _validate_request(request, {"handshake"})
    if not all(
        _required_string(value)
        for value in (project_root_hash, daemon_version, project_view_version)
    ):
        raise _error("invalid_handshake_context", "handshake context is invalid")

    compatible = (
        request.params["protocol_version"] == DAEMON_RPC_PROTOCOL_VERSION
        and request.params["project_root_hash"] == project_root_hash
    )
    return {
        "protocol_version": DAEMON_RPC_PROTOCOL_VERSION,
        "daemon_version": daemon_version,
        "project_view_schema_version": project_view_version,
        "compatible": compatible,
        "write_enabled": compatible,
        "capabilities": (
            ["status", "mutate", "subscribe"] if compatible else ["status"]
        ),
    }
