from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, replace
from types import MappingProxyType

import pytest

from agentdeck.ports.worker import WorkerEvent, WorkerResult, validate_worker_reason
from product_kernel.fakes import FakeWorker
from product_kernel.worker_contract import assert_worker_contract, task_request


def test_fake_worker_passes_shared_contract() -> None:
    asyncio.run(assert_worker_contract(FakeWorker.successful))


def test_worker_event_is_frozen_and_defensively_freezes_payload() -> None:
    source = {"summary": "safe", "nested": {"count": 1}}
    event = _event(payload=source)
    source["summary"] = "changed"
    source["nested"]["count"] = 2

    assert event.payload["summary"] == "safe"
    assert event.payload["nested"]["count"] == 1
    with pytest.raises(TypeError):
        event.payload["summary"] = "mutated"
    with pytest.raises(FrozenInstanceError):
        event.kind = "failed"


class _HostileMapping(dict):
    def items(self):
        raise RuntimeError("PRIVATE-MAPPING-FAILURE")


class _HostileProxySource(Mapping):
    def __len__(self):
        return 1

    def __iter__(self):
        raise RuntimeError("RAW-PROXY-MARKER")

    def __getitem__(self, key):
        raise RuntimeError("RAW-PROXY-MARKER")


class _WrongLineageFake(FakeWorker):
    async def cancel_task(self, handle, *, reason):
        await super().cancel_task(handle, reason=reason)
        self._cancel_event = replace(self._cancel_event, task_id="tsk_wrong")


def test_shared_contract_rejects_wrong_cancellation_lineage() -> None:
    with pytest.raises(AssertionError):
        asyncio.run(assert_worker_contract(_WrongLineageFake.successful))


def test_fake_worker_permission_blocks_progress_and_late_response() -> None:
    async def scenario() -> None:
        worker = FakeWorker.successful()
        handle = await worker.start_task(task_request())
        stream = worker.stream_events(handle).__aiter__()
        assert (await anext(stream)).kind == "started"
        permission = await anext(stream)
        request_id = permission.payload["permission_request_id"]
        with pytest.raises(ValueError, match="pending permission response required"):
            await anext(stream)
        with pytest.raises(ValueError, match="result unavailable"):
            await worker.collect_result(handle)
        await worker.respond_permission(
            handle, permission_request_id=request_id, allowed=True, reason="approved"
        )
        assert [event.kind async for event in stream] == ["progress", "completed"]
        assert (await worker.collect_result(handle)).status == "completed"
        with pytest.raises(ValueError, match="task is terminal"):
            await worker.respond_permission(
                handle, permission_request_id=request_id, allowed=True, reason="late"
            )

    asyncio.run(scenario())


def test_fake_worker_cancel_clears_pending_permission() -> None:
    async def scenario() -> None:
        worker = FakeWorker.successful()
        handle = await worker.start_task(task_request())
        stream = worker.stream_events(handle).__aiter__()
        assert (await anext(stream)).kind == "started"
        permission = await anext(stream)
        await worker.cancel_task(handle, reason="cancel while permission pending")
        assert [event.kind async for event in stream] == ["cancelled"]
        with pytest.raises(ValueError, match="task is terminal"):
            await worker.respond_permission(
                handle, permission_request_id=permission.payload["permission_request_id"],
                allowed=True, reason="late",
            )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"kind": "terminal_pixel_truth"}, "kind"),
        ({"transport": "pty"}, "transport"),
        ({"sequence": 0}, "sequence"),
        ({"timestamp": "not-a-time"}, "timestamp"),
        ({"payload": {"raw_frame": "private"}}, "payload"),
    ],
)
def test_worker_event_rejects_unstable_or_sensitive_facts(changes, message) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        _event(**changes)


def test_fake_worker_cancel_and_permission_response_keep_exact_lineage() -> None:
    async def scenario() -> None:
        worker = FakeWorker.successful()
        handle = await worker.start_task(task_request())
        stream = worker.stream_events(handle).__aiter__()
        assert (await anext(stream)).kind == "started"
        permission = await anext(stream)
        assert permission.kind == "permission_requested"
        await worker.respond_permission(
            handle,
            permission_request_id=permission.payload["permission_request_id"],
            allowed=False,
            reason="outside_scope",
        )
        await worker.cancel_task(handle, reason="user_cancelled")
        events = [event async for event in stream]
        result = await worker.collect_result(handle)

        assert worker.permission_responses == (
            ("ses_1", "perm_1", False, "outside_scope"),
        )
        assert events[-1].kind == "cancelled"
        assert result.status == "cancelled"
        assert result.session_id == "ses_1"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({key: "RAW-SPEC-MARKER"}, "payload contains sensitive content")
        for key in (
            "openaiApiKey", "openai-api-key", "apiKeyValue", "rawFramePayload",
            "clientSecret", "openaiApiTokenValue", "authToken", "accessTokenValue", "session_token",
            "privateKeyMaterial", "refreshToken", "idToken", "bearerToken",
            "credential_source", "authorization", "cookie", "sessionCookie",
            "set_cookie", "set-cookie", "sshKey", "ssh_key", "ssh-key",
        )
    ] + [
        ({"message": value}, "payload contains sensitive content")
        for value in (
            "openaiApiKey=RAW-SPEC-MARKER", "Authorization: RAW-SPEC-MARKER",
            "Cookie: session=RAW-SPEC-MARKER",
            "Set-Cookie: session=RAW-SPEC-MARKER",
            'Bearer RAW-SPEC-MARKER', '-----BEGIN PRIVATE KEY-----',
            '{"access_token":"RAW-SPEC-MARKER"}',
        )
    ] + [
        ({"headers": {key: "RAW-SPEC-MARKER"}}, "payload contains sensitive content")
        for key in ("Cookie", "Set-Cookie", "ssh_key")
    ] + [
        (_HostileMapping(), "payload must use built-in JSON containers"),
        ({"count": -(2**63) - 1}, "payload integer is outside SQLite range"),
        ({"count": 2**63}, "payload integer is outside SQLite range"),
    ],
)
def test_worker_payload_rejects_unsafe_facts_without_echo(payload, error) -> None:
    for target in ("event", "result"):
        with pytest.raises(ValueError) as raised:
            _payload_target(target, payload)
        assert str(raised.value) == error
        assert "RAW-SPEC-MARKER" not in str(raised.value)


def test_worker_payload_accepts_sqlite_integer_edges_and_bool() -> None:
    payload = {
        "minimum": -(2**63), "maximum": 2**63 - 1, "ok": True,
        "token_count": 4, "tokenizer_mode": "safe",
    }
    for target in ("event", "result"):
        assert dict(_payload_target(target, payload).payload) == payload


@pytest.mark.parametrize(
    "secret",
    [
        "sk-proj-" + "A" * 32,
        "sk-ant-" + "B" * 32,
        "ghp_" + "C" * 36,
        "github_pat_" + "D" * 32,
        "AKIA" + "E" * 16,
        "AIza" + "F" * 35,
        "ssh-ed25519 " + "A" * 32,
        "-----BEGIN OPENSSH PRIVATE KEY-----\nPRIVATE-MATERIAL",
        "Cookie: session=COOKIE-MATERIAL",
        "Set-Cookie: session=COOKIE-MATERIAL",
        "ssh_key=SSH-KEY-MATERIAL",
    ],
)
def test_worker_payload_and_reason_reject_bare_credentials_without_echo(
    secret: str,
) -> None:
    for target in ("event", "result"):
        with pytest.raises(ValueError) as raised:
            _payload_target(target, {"message": secret})
        assert str(raised.value) == "payload contains sensitive content"
        assert secret not in str(raised.value)

    with pytest.raises(ValueError) as raised:
        validate_worker_reason(f"blocked output: {secret}")
    assert str(raised.value) == "reason contains sensitive content"
    assert secret not in str(raised.value)


@pytest.mark.parametrize(
    "text",
    [
        "Document Cookie and Set-Cookie header handling.",
        "The sk-proj- prefix identifies a token family.",
        "Read credentials from the approved store.",
        "SSH keys must never be logged.",
    ],
)
def test_worker_payload_and_reason_allow_safe_credential_discussion(text: str) -> None:
    for target in ("event", "result"):
        assert _payload_target(target, {"message": text}).payload["message"] == text
    assert validate_worker_reason(text) == text


@pytest.mark.parametrize(
    "key",
    [
        "authorization_latency_ms", "api_token_count", "access_token_count",
        "raw_frame_count", "cookie_count", "set_cookie_total", "ssh_key_bytes",
        "ssh_key_latency_ms",
    ],
)
def test_sensitive_metric_keys_require_exact_numeric_or_bool(key) -> None:
    for target in ("event", "result"):
        for value in (1, 1.5, True):
            assert _payload_target(target, {key: value}).payload[key] == value
        with pytest.raises(ValueError, match="payload contains sensitive content"):
            _payload_target(target, {key: "RAW-METRIC-MARKER"})


@pytest.mark.parametrize("key", ["cookie_status", "set_cookie_code", "ssh_key_length"])
def test_sensitive_numeric_keys_without_metric_suffix_are_rejected(key: str) -> None:
    for target in ("event", "result"):
        with pytest.raises(ValueError, match="payload contains sensitive content"):
            _payload_target(target, {key: 1})


def test_frozen_payload_can_be_safely_resnapshotted() -> None:
    event = _event(payload={"nested": {"count": 1}})
    replaced = replace(event, event_id="evt_2", sequence=2)
    result = _payload_target("result", event.payload)
    assert replaced.payload == event.payload == result.payload
    assert replaced.payload is not event.payload


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"value": float("nan")}, "payload floats must be finite"),
        ({"value": float("inf")}, "payload floats must be finite"),
        ({"value": float("-inf")}, "payload floats must be finite"),
        ({"value": "\ud800"}, "payload text must be valid UTF-8"),
        ({str(index): index for index in range(256)}, "payload exceeds maximum items"),
        (MappingProxyType(_HostileProxySource()), "payload mapping snapshot invalid"),
    ],
)
def test_payload_limits_fail_closed_for_event_and_result(payload, error) -> None:
    for target in ("event", "result"):
        with pytest.raises(ValueError) as raised:
            _payload_target(target, payload)
        assert str(raised.value) == error
        assert "RAW-PROXY-MARKER" not in str(raised.value)


def test_payload_depth_limit_fails_closed() -> None:
    payload = {}
    for _ in range(9):
        payload = {"nested": payload}
    for target in ("event", "result"):
        with pytest.raises(ValueError, match="payload exceeds maximum depth"):
            _payload_target(target, payload)


def _payload_target(target, payload):
    lineage = dict(
        session_id="ses_1", agent_id="agt_1", task_id="tsk_1", attempt_id="att_1"
    )
    if target == "result":
        return WorkerResult(**lineage, status="completed", payload=payload)
    return _event(**lineage, payload=payload)


def _event(**changes):
    values = dict(
        event_id="evt_1", session_id="ses_1", agent_id="agt_1", task_id="tsk_1",
        attempt_id="att_1", transport="acp", sequence=1, kind="progress",
        timestamp="2026-07-19T00:00:00+00:00", payload={},
    )
    values.update(changes)
    return WorkerEvent(**values)
