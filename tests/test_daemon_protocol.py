from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agentdeck.daemon.protocol import (
    DAEMON_RPC_PROTOCOL_VERSION,
    READ_ONLY_METHODS,
    RpcEvent,
    RpcProtocolError,
    RpcRequest,
    RpcResponse,
    decode_event,
    decode_request,
    decode_response,
    encode_event,
    encode_request,
    encode_response,
    negotiate_handshake,
)


MAX_BYTES = 4096


def test_round_trips_all_envelopes_without_mutating_inputs() -> None:
    params = {"nested": {"items": [1, True, None, "value"]}}
    result = {"status": {"state": "ready"}}
    summary = {"counts": [1, 2], "current": None}
    request = RpcRequest(request_id="req_1", method="status", params=params)
    response = RpcResponse(request_id="req_1", ok=True, result=result, error=None)
    event = RpcEvent(event_id="evt_1", revision=0, kind="state_changed", summary=summary)

    request_frame = encode_request(request, max_bytes=MAX_BYTES)
    response_frame = encode_response(response, max_bytes=MAX_BYTES)
    event_frame = encode_event(event, max_bytes=MAX_BYTES)

    assert decode_request(request_frame, max_bytes=MAX_BYTES) == request
    assert decode_response(response_frame, max_bytes=MAX_BYTES) == response
    assert decode_event(event_frame, max_bytes=MAX_BYTES) == event
    assert params == {"nested": {"items": [1, True, None, "value"]}}
    assert result == {"status": {"state": "ready"}}
    assert summary == {"counts": [1, 2], "current": None}


def test_envelopes_are_frozen_and_detached_from_constructor_inputs() -> None:
    params = {"nested": {"items": [1]}}
    request = RpcRequest(request_id="req_1", method="status", params=params)
    response = RpcResponse(
        request_id="req_1",
        ok=True,
        result={"nested": {"items": [1]}},
        error=None,
    )
    event = RpcEvent(
        event_id="evt_1",
        revision=1,
        kind="changed",
        summary={"nested": {"items": [1]}},
    )
    params["nested"]["items"].append(2)  # type: ignore[index,union-attr]

    assert request.params == {"nested": {"items": [1]}}
    with pytest.raises(FrozenInstanceError):
        request.method = "handshake"  # type: ignore[misc]
    with pytest.raises(TypeError):
        request.params["nested"]["items"][0] = 2  # type: ignore[index,union-attr]
    with pytest.raises(TypeError):
        response.result["nested"]["items"][0] = 2  # type: ignore[index,union-attr]
    with pytest.raises(TypeError):
        event.summary["nested"]["new"] = True  # type: ignore[index,union-attr]


def test_encoding_is_canonical_utf8_with_stable_recursive_key_order() -> None:
    request = RpcRequest(
        request_id="req_1",
        method="status",
        params={"z": "终端", "a": {"y": 2, "x": 1}},
    )

    assert encode_request(request, max_bytes=MAX_BYTES) == (
        '{"method":"status","params":{"a":{"x":1,"y":2},"z":"终端"},'
        '"request_id":"req_1"}\n'
    ).encode("utf-8")


def test_handshake_constructor_binds_exact_provenance_and_negotiates() -> None:
    request = RpcRequest.handshake(
        request_id="req_handshake",
        project_root_hash="root-hash",
        client_version="1.4.0",
    )

    assert request.params == {
        "client_version": "1.4.0",
        "project_root_hash": "root-hash",
        "protocol_version": DAEMON_RPC_PROTOCOL_VERSION,
    }
    assert negotiate_handshake(
        request,
        project_root_hash="root-hash",
        daemon_version="2.0.0",
        project_view_version="project-view/v1",
    ) == {
        "protocol_version": DAEMON_RPC_PROTOCOL_VERSION,
        "daemon_version": "2.0.0",
        "project_view_schema_version": "project-view/v1",
        "compatible": True,
        "write_enabled": True,
        "capabilities": ["status", "mutate", "subscribe"],
    }


@pytest.mark.parametrize(
    ("protocol_version", "root_hash"),
    [
        ("daemon-rpc/v2", "root-hash"),
        (DAEMON_RPC_PROTOCOL_VERSION, "other-root"),
    ],
)
def test_handshake_incompatibility_is_read_only(
    protocol_version: str, root_hash: str
) -> None:
    request = RpcRequest.handshake(
        request_id="req_handshake",
        project_root_hash=root_hash,
        client_version="arbitrary-client-provenance",
        protocol_version=protocol_version,
    )

    facts = negotiate_handshake(
        request,
        project_root_hash="root-hash",
        daemon_version="2.0.0",
        project_view_version="project-view/v1",
    )

    assert facts["compatible"] is False
    assert facts["write_enabled"] is False
    assert facts["capabilities"] == ["status"]


def test_client_version_is_required_provenance_but_does_not_gate_v1() -> None:
    with pytest.raises(RpcProtocolError, match="handshake parameters are invalid"):
        RpcRequest.handshake(
            request_id="req_1",
            project_root_hash="root-hash",
            client_version="",
        )

    request = RpcRequest.handshake(
        request_id="req_2",
        project_root_hash="root-hash",
        client_version="not-a-package-version",
    )
    assert negotiate_handshake(
        request,
        project_root_hash="root-hash",
        daemon_version="daemon",
        project_view_version="project-view/v1",
    )["compatible"] is True


def test_handshake_rejects_non_handshake_and_inexact_parameters() -> None:
    status = RpcRequest(request_id="req_1", method="status", params={})
    with pytest.raises(RpcProtocolError, match="handshake request is invalid"):
        negotiate_handshake(
            status,
            project_root_hash="root",
            daemon_version="daemon",
            project_view_version="view",
        )

    extra = RpcRequest(
        request_id="req_2",
        method="handshake",
        params={
            "project_root_hash": "root",
            "client_version": "client",
            "protocol_version": DAEMON_RPC_PROTOCOL_VERSION,
            "token": "must-not-be-accepted",
        },
    )
    with pytest.raises(RpcProtocolError, match="handshake parameters are invalid"):
        negotiate_handshake(
            extra,
            project_root_hash="root",
            daemon_version="daemon",
            project_view_version="view",
        )


def test_handshake_negotiation_revalidates_the_bound_request_id() -> None:
    request = RpcRequest(
        request_id="",
        method="handshake",
        params={
            "project_root_hash": "root",
            "client_version": "client",
            "protocol_version": DAEMON_RPC_PROTOCOL_VERSION,
        },
    )

    with pytest.raises(RpcProtocolError, match="request envelope is invalid"):
        negotiate_handshake(
            request,
            project_root_hash="root",
            daemon_version="daemon",
            project_view_version="view",
        )


def test_request_decode_rejects_unknown_fields_missing_fields_and_methods() -> None:
    with pytest.raises(RpcProtocolError, match="request envelope is invalid"):
        decode_request(
            b'{"request_id":"r","method":"status","params":{},"extra":1}\n',
            max_bytes=MAX_BYTES,
        )
    with pytest.raises(RpcProtocolError, match="request envelope is invalid"):
        decode_request(b'{"request_id":"r","method":"status"}\n', max_bytes=MAX_BYTES)
    with pytest.raises(RpcProtocolError, match="request method is not allowed"):
        decode_request(
            b'{"request_id":"r","method":"mutate","params":{}}\n',
            max_bytes=MAX_BYTES,
        )
    assert READ_ONLY_METHODS == frozenset({"handshake", "status"})


def test_request_decode_uses_explicit_method_allowlist() -> None:
    frame = b'{"request_id":"r","method":"custom_read","params":{}}\n'
    request = decode_request(
        frame,
        max_bytes=MAX_BYTES,
        allowed_methods={"custom_read"},
    )
    assert request.method == "custom_read"


def test_frame_limit_includes_the_newline_at_exact_boundary() -> None:
    request = RpcRequest(request_id="r", method="status", params={})
    frame = encode_request(request, max_bytes=MAX_BYTES)

    assert encode_request(request, max_bytes=len(frame)) == frame
    assert decode_request(frame, max_bytes=len(frame)) == request
    with pytest.raises(RpcProtocolError, match="frame exceeds maximum size"):
        encode_request(request, max_bytes=len(frame) - 1)
    with pytest.raises(RpcProtocolError, match="frame exceeds maximum size"):
        decode_request(frame, max_bytes=len(frame) - 1)


@pytest.mark.parametrize("max_bytes", [0, -1, True, 1.5])
def test_frame_limit_must_be_a_strictly_positive_integer(max_bytes: object) -> None:
    request = RpcRequest(request_id="r", method="status", params={})
    with pytest.raises(RpcProtocolError, match="maximum frame size is invalid"):
        encode_request(request, max_bytes=max_bytes)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "frame",
    [
        b"",
        b'{"request_id":"r","method":"status","params":{}}',
        b'{}\n{}\n',
        b'{"request_id":"r","method":"status","params":{}}\ntrailing',
        b"\xff\n",
        b'{"request_id":"r","method":"status","params":}\n',
        b"[]\n",
    ],
)
def test_decode_rejects_invalid_incomplete_multiple_or_malformed_frames(
    frame: bytes,
) -> None:
    with pytest.raises(RpcProtocolError):
        decode_request(frame, max_bytes=MAX_BYTES)


def test_decode_rejects_duplicate_keys_and_nonfinite_numbers() -> None:
    with pytest.raises(RpcProtocolError, match="JSON document is invalid"):
        decode_request(
            b'{"request_id":"r","request_id":"r2","method":"status","params":{}}\n',
            max_bytes=MAX_BYTES,
        )
    with pytest.raises(RpcProtocolError, match="JSON document is invalid"):
        decode_request(
            b'{"request_id":"r","method":"status","params":{"value":NaN}}\n',
            max_bytes=MAX_BYTES,
        )


@pytest.mark.parametrize(
    ("decoder", "frame"),
    [
        (
            decode_request,
            b'{"request_id":"r","method":"status","params":{"x":"\\ud800"}}\n',
        ),
        (
            decode_response,
            b'{"request_id":"r","ok":true,"result":{"x":"\\ud800"},"error":null}\n',
        ),
        (
            decode_event,
            b'{"event_id":"e","revision":0,"kind":"changed","summary":{"x":"\\ud800"}}\n',
        ),
        (
            decode_request,
            b'{"request_id":"r","method":"status","params":{"\\ud800":1}}\n',
        ),
        (
            decode_response,
            b'{"request_id":"r","ok":true,"result":{"\\ud800":1},"error":null}\n',
        ),
        (
            decode_event,
            b'{"event_id":"e","revision":0,"kind":"changed","summary":{"\\ud800":1}}\n',
        ),
    ],
)
def test_decode_rejects_strings_that_cannot_be_canonical_utf8(
    decoder: object, frame: bytes
) -> None:
    with pytest.raises(RpcProtocolError, match="JSON document must be an object"):
        decoder(frame, max_bytes=MAX_BYTES)  # type: ignore[operator]


@pytest.mark.parametrize(
    "envelope",
    [
        RpcRequest(request_id="", method="status", params={}),
        RpcRequest(request_id="r", method="", params={}),
        RpcRequest(request_id="r", method="status", params={"bad": {1, 2}}),
        RpcRequest(request_id="r", method="status", params={"bad": (1, 2)}),  # type: ignore[dict-item]
        RpcRequest(request_id="r", method="status", params={"bad": float("inf")}),
        RpcRequest(request_id="r", method="status", params={1: "bad"}),  # type: ignore[dict-item]
    ],
)
def test_encode_rejects_invalid_identifiers_and_nested_non_json_values(
    envelope: RpcRequest,
) -> None:
    with pytest.raises(RpcProtocolError):
        encode_request(envelope, max_bytes=MAX_BYTES)


@pytest.mark.parametrize(
    "response",
    [
        RpcResponse(request_id="r", ok=True, result=None, error=None),
        RpcResponse(request_id="r", ok=True, result={}, error={"code": "x", "message": "x"}),
        RpcResponse(request_id="r", ok=False, result={}, error={"code": "x", "message": "x"}),
        RpcResponse(request_id="r", ok=False, result=None, error=None),
        RpcResponse(request_id="r", ok=False, result=None, error={"code": "x"}),
        RpcResponse(
            request_id="r",
            ok=False,
            result=None,
            error={"code": "x", "message": "x", "details": "secret"},
        ),
        RpcResponse(request_id="r", ok=1, result={}, error=None),  # type: ignore[arg-type]
    ],
)
def test_response_invariants_are_exact(response: RpcResponse) -> None:
    with pytest.raises(RpcProtocolError, match="response envelope is invalid"):
        encode_response(response, max_bytes=MAX_BYTES)


def test_error_response_round_trip_uses_compact_error_shape() -> None:
    response = RpcResponse(
        request_id="req_1",
        ok=False,
        result=None,
        error={"code": "blocked", "message": "request is blocked"},
    )
    assert decode_response(encode_response(response, max_bytes=MAX_BYTES), max_bytes=MAX_BYTES) == response


@pytest.mark.parametrize("revision", [-1, True, 1.5])
def test_event_revision_is_a_nonnegative_integer_without_bool(revision: object) -> None:
    event = RpcEvent(
        event_id="evt_1",
        revision=revision,  # type: ignore[arg-type]
        kind="changed",
        summary={},
    )
    with pytest.raises(RpcProtocolError, match="event envelope is invalid"):
        encode_event(event, max_bytes=MAX_BYTES)


def test_protocol_errors_are_stable_and_do_not_echo_secret_payloads() -> None:
    secret = "SECRET_MARKER_93af"
    frame = (
        '{"request_id":"r","method":"unknown","params":{"token":"'
        + secret
        + '"}}\n'
    ).encode()

    with pytest.raises(RpcProtocolError) as captured:
        decode_request(frame, max_bytes=MAX_BYTES)

    assert captured.value.code == "method_not_allowed"
    assert str(captured.value) == "request method is not allowed"
    assert secret not in str(captured.value)
