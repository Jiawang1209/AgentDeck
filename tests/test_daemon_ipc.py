from __future__ import annotations

import asyncio
from collections.abc import Mapping
import gc
import os
from pathlib import Path
import socket
import stat
import shutil
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

import pytest

import agentdeck.daemon.client as client_module
import agentdeck.daemon.lifecycle as lifecycle_module
import agentdeck.daemon.server as server_module
from agentdeck import __version__
from agentdeck.daemon.client import (
    DaemonClient,
    DaemonClientError,
    DaemonUnavailable,
    connect_or_start,
)
from agentdeck.daemon.lifecycle import (
    acquire_daemon_ownership,
    cleanup_daemon_endpoint,
    daemon_endpoint,
    project_root_hash,
)
from agentdeck.daemon.lease import LeaseError
from agentdeck.daemon.protocol import (
    DAEMON_RPC_PROTOCOL_VERSION,
    RpcEvent,
    RpcProtocolError,
    RpcRequest,
    RpcResponse,
    decode_request,
    encode_request,
    encode_response,
)
from agentdeck.daemon.server import DaemonServer
from agentdeck.daemon.server import DaemonClientRequestError
from agentdeck.daemon.service import ProjectDaemonService
from agentdeck.models import PROJECT_VIEW_SCHEMA_VERSION, DaemonConfig


MAX_FRAME = 4096
METHODS = frozenset({"handshake", "status", "subscribe", "mission.pause"})


@pytest.fixture
def short_project() -> Path:
    # Darwin caps AF_UNIX path names at roughly 104 bytes. Keep the real
    # project-local endpoint short rather than masking that platform boundary.
    project = Path(tempfile.mkdtemp(prefix="ad-ipc-", dir="/tmp")).resolve()
    try:
        yield project
    finally:
        shutil.rmtree(project, ignore_errors=True)


def _run(coroutine):
    return asyncio.run(coroutine)


def _owner(project: Path):
    return acquire_daemon_ownership(
        project,
        start_nonce="ipc-test-nonce",
        health_probe=lambda metadata: metadata,
    )


async def _running_server(
    project: Path,
    *,
    queue_size: int = 8,
    mutation_handler=None,
    lease_validator=None,
    mutation_response_sent_handler=None,
    allowed_methods=METHODS,
    lease_exempt_methods=frozenset(),
    read_timeout_seconds: float = 1,
    write_timeout_seconds: float = 1,
) -> tuple[object, DaemonServer]:
    owner = _owner(project)
    server = DaemonServer(
        endpoint=owner.endpoint.socket_path,
        instance_id=owner.instance_id,
        project_root_hash=owner.project_root_hash,
        start_nonce_hash=owner.start_nonce_hash,
        daemon_version=__version__,
        project_view_schema_version=PROJECT_VIEW_SCHEMA_VERSION,
        max_frame_bytes=MAX_FRAME,
        allowed_methods=allowed_methods,
        lease_exempt_methods=lease_exempt_methods,
        event_queue_size=queue_size,
        request_queue_size=queue_size,
        status_provider=lambda: {"mode": "daemon_status", "state": "ready"},
        mutation_handler=mutation_handler,
        lease_validator=lease_validator,
        mutation_response_sent_handler=mutation_response_sent_handler,
        read_timeout_seconds=read_timeout_seconds,
        write_timeout_seconds=write_timeout_seconds,
    )
    await server.start()
    return owner, server


def test_protocol_default_remains_read_only_but_explicit_allowlist_encodes() -> None:
    request = RpcRequest("req_mutate", "mission.pause", {"mission_id": "mis_1"})
    with pytest.raises(RpcProtocolError, match="not allowed"):
        encode_request(request, max_bytes=MAX_FRAME)

    frame = encode_request(request, max_bytes=MAX_FRAME, allowed_methods=METHODS)
    assert decode_request(frame, max_bytes=MAX_FRAME, allowed_methods=METHODS) == request


def test_verified_client_handshake_status_and_request_correlation(short_project: Path) -> None:
    async def exercise() -> None:
        owner, server = await _running_server(short_project)
        try:
            client = await DaemonClient.connect_verified(
                short_project,
                max_frame_bytes=MAX_FRAME,
                timeout_seconds=1,
            )
            try:
                assert client.compatible is True
                assert client.instance_id == owner.instance_id
                first, second = await asyncio.gather(
                    client.request("status", {}), client.request("status", {})
                )
                assert first["mode"] == second["mode"] == "daemon_status"
                assert first["instance_id"] == owner.instance_id
            finally:
                await client.close()
        finally:
            await server.close()
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_activity_generation_is_monotonic_for_accept_and_valid_requests_only(
    short_project: Path,
) -> None:
    async def exercise() -> None:
        owner, server = await _running_server(short_project)
        try:
            assert server.activity_generation == 0
            client = await DaemonClient.connect_verified(
                short_project, max_frame_bytes=MAX_FRAME, timeout_seconds=1
            )
            after_connect = server.activity_generation
            assert after_connect >= 3  # accept + handshake + verified status
            await client.request("status", {})
            assert server.activity_generation == after_connect + 1
            await client.subscribe()
            assert server.activity_generation == after_connect + 2
            await client.close()
            await server.wait_connection_count(0, timeout_seconds=1)
            assert server.activity_generation == after_connect + 2
        finally:
            await server.close()
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_verified_client_rejects_metadata_symlink(short_project: Path) -> None:
    async def exercise() -> None:
        owner, server = await _running_server(short_project)
        metadata = owner.endpoint.metadata_path
        original = metadata.read_bytes()
        outside = short_project / "outside-metadata.json"
        outside.write_bytes(original)
        metadata.unlink()
        metadata.symlink_to(outside)
        try:
            with pytest.raises(DaemonUnavailable):
                await DaemonClient.connect_verified(
                    short_project,
                    max_frame_bytes=MAX_FRAME,
                    timeout_seconds=1,
                )
        finally:
            metadata.unlink(missing_ok=True)
            metadata.write_bytes(original)
            await server.close()
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_verified_client_rejects_runtime_swap_during_connect(
    short_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        owner, server = await _running_server(short_project)
        runtime = owner.endpoint.socket_path.parent
        parked = runtime.with_name("runtime-parked")
        outside = short_project / "outside-runtime"
        outside.mkdir()
        original = lifecycle_module.DaemonEndpointBinding.assert_socket_identity
        swapped = False

        def swap_then_assert(binding: object, identity: tuple[int, int]) -> None:
            nonlocal swapped
            if not swapped:
                swapped = True
                runtime.rename(parked)
                runtime.symlink_to(outside, target_is_directory=True)
            original(binding, identity)

        monkeypatch.setattr(
            lifecycle_module.DaemonEndpointBinding,
            "assert_socket_identity",
            swap_then_assert,
        )
        try:
            with pytest.raises(DaemonUnavailable):
                await DaemonClient.connect_verified(
                    short_project,
                    max_frame_bytes=MAX_FRAME,
                    timeout_seconds=1,
                )
            assert swapped is True
        finally:
            if runtime.is_symlink():
                runtime.unlink()
                parked.rename(runtime)
            await server.close()
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_post_connect_identity_failure_cleanup_uses_original_deadline(
    short_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        owner, server = await _running_server(short_project)
        real_connect = DaemonClient.connect.__func__
        real_assert = lifecycle_module.DaemonEndpointBinding.assert_socket_identity
        assertion_count = 0

        async def delayed_connect(cls: type[DaemonClient], *args: object, **kwargs: object):
            client = await real_connect(cls, *args, **kwargs)

            async def blocked_wait_closed() -> None:
                await asyncio.Event().wait()

            client._writer.wait_closed = blocked_wait_closed  # type: ignore[method-assign]
            await asyncio.sleep(0.04)
            return client

        def fail_post_connect(binding: object, identity: tuple[int, int]) -> None:
            nonlocal assertion_count
            assertion_count += 1
            if assertion_count == 2:
                raise lifecycle_module.DaemonIdentityError("daemon socket identity changed")
            real_assert(binding, identity)

        monkeypatch.setattr(DaemonClient, "connect", classmethod(delayed_connect))
        monkeypatch.setattr(
            lifecycle_module.DaemonEndpointBinding,
            "assert_socket_identity",
            fail_post_connect,
        )
        started = asyncio.get_running_loop().time()
        try:
            with pytest.raises(DaemonUnavailable):
                await DaemonClient.connect_verified(
                    short_project,
                    max_frame_bytes=MAX_FRAME,
                    timeout_seconds=0.06,
                )
            elapsed = asyncio.get_running_loop().time() - started
            assert elapsed < 0.085
        finally:
            await server.close()
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_connect_phases_share_one_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phase_delay = 0.02
    timeout = 0.055
    root_hash = "a" * 64
    nonce_hash = "b" * 64

    class SlowTransport:
        def __init__(self) -> None:
            self.frames: asyncio.Queue[bytes] = asyncio.Queue()
            self.closed = False

        def write(self, payload: bytes) -> None:
            request = decode_request(
                payload, max_bytes=MAX_FRAME, allowed_methods=METHODS
            )
            if request.method == "handshake":
                result = {
                    "protocol_version": DAEMON_RPC_PROTOCOL_VERSION,
                    "daemon_version": __version__,
                    "project_view_schema_version": PROJECT_VIEW_SCHEMA_VERSION,
                    "compatible": True,
                    "write_enabled": True,
                    "capabilities": ["status", "mutate", "subscribe"],
                }
            else:
                result = {
                    "mode": "daemon_status",
                    "compatible": True,
                    "protocol_version": DAEMON_RPC_PROTOCOL_VERSION,
                    "project_view_schema_version": PROJECT_VIEW_SCHEMA_VERSION,
                    "instance_id": "dmn_slow",
                    "project_root_hash": root_hash,
                    "start_nonce_hash": nonce_hash,
                }
            self.frames.put_nowait(
                encode_response(
                    RpcResponse(request.request_id, True, result, None),
                    max_bytes=MAX_FRAME,
                )
            )

        async def drain(self) -> None:
            await asyncio.sleep(phase_delay)

        async def readuntil(self, separator: bytes) -> bytes:
            assert separator == b"\n"
            await asyncio.sleep(phase_delay)
            return await self.frames.get()

        def close(self) -> None:
            self.closed = True

        async def wait_closed(self) -> None:
            return None

    async def exercise() -> None:
        transport = SlowTransport()

        async def slow_open(*args: object, **kwargs: object):
            del args, kwargs
            await asyncio.sleep(phase_delay)
            return transport, transport

        monkeypatch.setattr(client_module.asyncio, "open_unix_connection", slow_open)
        started = asyncio.get_running_loop().time()
        with pytest.raises(DaemonUnavailable, match="unverified"):
            await DaemonClient.connect(
                Path("/tmp/unused.sock"),
                expected_project_root_hash=root_hash,
                expected_start_nonce_hash=nonce_hash,
                max_frame_bytes=MAX_FRAME,
                timeout_seconds=timeout,
            )
        elapsed = asyncio.get_running_loop().time() - started
        assert elapsed <= timeout + 0.025
        assert transport.closed is True

    _run(exercise())


def test_incompatible_client_is_minimal_status_only(short_project: Path) -> None:
    async def exercise() -> None:
        owner, server = await _running_server(short_project)
        try:
            client = await DaemonClient.connect(
                owner.endpoint.socket_path,
                expected_project_root_hash=owner.project_root_hash,
                expected_start_nonce_hash=owner.start_nonce_hash,
                protocol_version="daemon-rpc/v999",
                max_frame_bytes=MAX_FRAME,
                timeout_seconds=1,
            )
            try:
                assert client.compatible is False
                status = await client.request("status", {})
                assert set(status) == {
                    "mode",
                    "compatible",
                    "protocol_version",
                    "project_view_schema_version",
                    "instance_id",
                    "project_root_hash",
                    "start_nonce_hash",
                }
                with pytest.raises(DaemonClientError, match="incompatible"):
                    await client.request("mission.pause", {"mission_id": "mis_1"})
            finally:
                await client.close()
        finally:
            await server.close()
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_observer_mutation_requires_current_lease_before_handler(short_project: Path) -> None:
    calls: list[dict[str, object]] = []

    def validate(lease_id: str, generation: int) -> None:
        if (lease_id, generation) != ("lse_current", 7):
            raise ValueError("controller lease required")

    async def mutate(method: str, params: dict[str, object]) -> dict[str, object]:
        calls.append({"method": method, **params})
        return {"paused": True}

    async def exercise() -> None:
        owner, server = await _running_server(
            short_project, mutation_handler=mutate, lease_validator=validate
        )
        try:
            client = await DaemonClient.connect_verified(
                    short_project, max_frame_bytes=MAX_FRAME, timeout_seconds=1
            )
            try:
                with pytest.raises(DaemonClientError, match="controller lease required"):
                    await client.request("mission.pause", {"mission_id": "mis_1"})
                assert calls == []
                result = await client.request(
                    "mission.pause",
                    {"mission_id": "mis_1"},
                    lease_id="lse_current",
                    lease_generation=7,
                )
                assert result == {"paused": True}
                assert len(calls) == 1
                assert calls[0]["method"] == "mission.pause"
                assert calls[0]["mission_id"] == "mis_1"
                assert dict(calls[0]["_lease"]) == {  # type: ignore[arg-type]
                    "lease_id": "lse_current", "generation": 7,
                }
            finally:
                await client.close()
        finally:
            await server.close()
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_only_controller_acquire_and_scoped_permission_confirm_can_be_lease_exempt(
    short_project: Path,
) -> None:
    base = {
        "endpoint": short_project / "daemon.sock",
        "instance_id": "dmn_test",
        "project_root_hash": "a" * 64,
        "start_nonce_hash": "b" * 64,
        "daemon_version": __version__,
        "project_view_schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "max_frame_bytes": MAX_FRAME,
        "status_provider": lambda: {},
    }
    with pytest.raises(ValueError, match="lease-exempt"):
        DaemonServer(
            **base,
            allowed_methods=METHODS,
            lease_exempt_methods={"controller.acquire"},
        )
    with pytest.raises(ValueError, match="lease-exempt"):
        DaemonServer(
            **base,
            allowed_methods=METHODS,
            lease_exempt_methods={"mission.pause"},
        )
    DaemonServer(
        **base,
        allowed_methods=METHODS | {"permission.confirm-handle"},
        lease_exempt_methods={"permission.confirm-handle"},
    )

    calls: list[tuple[str, dict[str, object]]] = []

    async def mutate(method: str, params: dict[str, object]) -> dict[str, object]:
        calls.append((method, params))
        return {"lease_id": "lse_" + "1" * 24, "generation": 1}

    async def exercise() -> None:
        allowed = METHODS | {
            "controller.acquire", "controller.renew", "controller.release",
            "permission.confirm-handle", "permission.decide",
        }
        owner, server = await _running_server(
            short_project,
            mutation_handler=mutate,
            lease_validator=lambda lease_id, generation: (
                None
                if (lease_id, generation) == ("lse_" + "1" * 24, 1)
                else (_ for _ in ()).throw(LeaseError("stale controller lease"))
            ),
            allowed_methods=allowed,
            lease_exempt_methods={
                "controller.acquire", "permission.confirm-handle"
            },
        )
        try:
            client = await DaemonClient.connect_verified(
                short_project, max_frame_bytes=MAX_FRAME, timeout_seconds=1
            )
            try:
                acquired = await client.request(
                    "controller.acquire", {"client_id": "client-a"}
                )
                assert set(acquired) == {"lease_id", "generation"}
                assert acquired["generation"] == 1
                assert await client.request(
                    "permission.confirm-handle", {"handle": "pcf_" + "2" * 24}
                ) == {"lease_id": "lse_" + "1" * 24, "generation": 1}
                with pytest.raises(
                    DaemonClientError, match="controller lease required"
                ):
                    await client.request("permission.decide", {})
                with pytest.raises(
                    DaemonClientError, match="controller lease required"
                ):
                    await client.request("controller.renew", {})
                renewed = await client.request(
                    "controller.renew",
                    {},
                    lease_id="lse_" + "1" * 24,
                    lease_generation=1,
                )
                assert renewed["generation"] == 1
                with pytest.raises(
                    DaemonClientError, match="controller lease required"
                ):
                    await client.request("controller.release", {})
                released = await client.request(
                    "controller.release",
                    {},
                    lease_id="lse_" + "1" * 24,
                    lease_generation=1,
                )
                assert released["generation"] == 1
            finally:
                await client.close()
        finally:
            await server.close()
            cleanup_daemon_endpoint(owner)
        assert calls[0] == ("controller.acquire", {"client_id": "client-a"})
        assert [method for method, _params in calls[1:]] == [
            "permission.confirm-handle", "controller.renew", "controller.release",
        ]
        assert calls[1][1] == {"handle": "pcf_" + "2" * 24}
        for _method, params in calls[2:]:
            assert dict(params["_lease"]) == {  # type: ignore[arg-type]
                "lease_id": "lse_" + "1" * 24, "generation": 1,
            }

    _run(exercise())


def test_failed_mutation_never_runs_response_sent_hook(short_project: Path) -> None:
    hook_called = False

    async def fail_mutation(
        _method: str, _params: dict[str, object]
    ) -> dict[str, object]:
        raise OSError("simulated durable flush failure")

    def after_response(_method: str, _result: dict[str, object]) -> None:
        nonlocal hook_called
        hook_called = True

    async def exercise() -> None:
        owner, server = await _running_server(
            short_project,
            mutation_handler=fail_mutation,
            lease_validator=lambda _lease_id, _generation: None,
            mutation_response_sent_handler=after_response,
        )
        try:
            client = await DaemonClient.connect_verified(
                short_project, max_frame_bytes=MAX_FRAME, timeout_seconds=1
            )
            try:
                with pytest.raises(DaemonClientError, match="request failed"):
                    await client.request(
                        "mission.pause",
                        {},
                        lease_id="lse_current",
                        lease_generation=1,
                    )
                assert hook_called is False
            finally:
                await client.close()
        finally:
            await server.close()
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_mutation_response_sent_hook_runs_after_ack_is_flushed(
    short_project: Path,
) -> None:
    response_flushed = False
    hook_called = asyncio.Event()

    async def mutate(_method: str, _params: dict[str, object]) -> dict[str, object]:
        return {"paused": True}

    def after_response(method: str, result: dict[str, object]) -> None:
        assert method == "mission.pause"
        assert result == {"paused": True}
        assert response_flushed is True
        hook_called.set()

    async def exercise() -> None:
        nonlocal response_flushed
        owner, server = await _running_server(
            short_project,
            mutation_handler=mutate,
            lease_validator=lambda _lease_id, _generation: None,
            mutation_response_sent_handler=after_response,
        )
        original_send_response = server._send_response

        async def record_flush(connection, response):
            nonlocal response_flushed
            await original_send_response(connection, response)
            if response.request_id != "req_handshake":
                response_flushed = True

        server._send_response = record_flush  # type: ignore[method-assign]
        try:
            client = await DaemonClient.connect_verified(
                short_project, max_frame_bytes=MAX_FRAME, timeout_seconds=1
            )
            try:
                result = await client.request(
                    "mission.pause",
                    {"mission_id": "mis_1"},
                    lease_id="lse_current",
                    lease_generation=7,
                )
                assert result == {"paused": True}
                await asyncio.wait_for(hook_called.wait(), timeout=1)
            finally:
                await client.close()
        finally:
            await server.close()
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_client_eof_does_not_cancel_lease_validated_mutation(
    short_project: Path,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()

    async def mutate(method: str, params: dict[str, object]) -> dict[str, object]:
        del method, params
        started.set()
        await release.wait()
        completed.set()
        return {"completed": True}

    async def exercise() -> None:
        owner, server = await _running_server(
            short_project,
            mutation_handler=mutate,
            lease_validator=lambda lease_id, generation: None,
        )
        client = await DaemonClient.connect_verified(
            short_project, max_frame_bytes=MAX_FRAME
        )
        request = asyncio.create_task(
            client.request(
                "mission.pause", {}, lease_id="lse_current", lease_generation=1
            )
        )
        try:
            await asyncio.wait_for(started.wait(), timeout=0.5)
            await client.close()
            release.set()
            await asyncio.wait_for(completed.wait(), timeout=0.5)
            result = await asyncio.gather(request, return_exceptions=True)
            assert isinstance(result[0], DaemonClientError)
            await server.wait_for_mutations(timeout_seconds=0.5)
            assert server.active_mutation_task_count == 0
        finally:
            release.set()
            await asyncio.gather(request, return_exceptions=True)
            await server.close()
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_sent_request_cancellation_tombstones_late_response(
    short_project: Path,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def mutate(method: str, params: dict[str, object]) -> dict[str, object]:
        del method, params
        started.set()
        await release.wait()
        return {"late": True}

    async def exercise() -> None:
        owner, server = await _running_server(
            short_project,
            mutation_handler=mutate,
            lease_validator=lambda lease_id, generation: None,
        )
        try:
            client = await DaemonClient.connect_verified(
                short_project, max_frame_bytes=MAX_FRAME
            )
            try:
                request = asyncio.create_task(
                    client.request(
                        "mission.pause",
                        {},
                        lease_id="lse_current",
                        lease_generation=1,
                    )
                )
                await asyncio.wait_for(started.wait(), timeout=0.5)
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request
                release.set()
                await server.wait_for_mutations(timeout_seconds=0.5)
                await asyncio.sleep(0)
                assert (await client.request("status", {}))["state"] == "ready"
            finally:
                await client.close()
        finally:
            release.set()
            await server.close()
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_unsent_timeout_storm_cannot_evict_real_sent_tombstone(
    short_project: Path,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def mutate(method: str, params: dict[str, object]) -> dict[str, object]:
        del method, params
        started.set()
        await release.wait()
        return {"late": True}

    async def exercise() -> None:
        owner, server = await _running_server(
            short_project,
            mutation_handler=mutate,
            lease_validator=lambda lease_id, generation: None,
        )
        client = await DaemonClient.connect_verified(
            short_project, max_frame_bytes=MAX_FRAME
        )
        client.request_timeout_seconds = 0.04
        sent = asyncio.create_task(
            client.request(
                "mission.pause", {}, lease_id="lse_current", lease_generation=1
            )
        )
        try:
            await asyncio.wait_for(started.wait(), timeout=0.5)
            await client._write_lock.acquire()
            unsent = [
                asyncio.create_task(client.request("status", {}))
                for _ in range(140)
            ]
            results = await asyncio.gather(*unsent, return_exceptions=True)
            assert all(
                isinstance(result, DaemonClientError)
                and "timed out" in str(result)
                for result in results
            )
            with pytest.raises(DaemonClientError, match="timed out"):
                await sent
            client._write_lock.release()
            release.set()
            await server.wait_for_mutations(timeout_seconds=0.5)
            await asyncio.sleep(0)
            assert (await client.request("status", {}))["state"] == "ready"
        finally:
            if client._write_lock.locked():
                client._write_lock.release()
            release.set()
            await asyncio.gather(sent, return_exceptions=True)
            await client.close()
            await server.close()
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_reader_eof_atomically_fails_pending_and_unbounded_event_waiters(
    short_project: Path,
) -> None:
    status_release = asyncio.Event()
    status_calls = 0

    async def status() -> dict[str, object]:
        nonlocal status_calls
        status_calls += 1
        if status_calls > 1:
            await status_release.wait()
        return {"mode": "daemon_status", "state": "ready"}

    async def exercise() -> None:
        owner = _owner(short_project)
        server = DaemonServer(
            endpoint=owner.endpoint.socket_path,
            instance_id=owner.instance_id,
            project_root_hash=owner.project_root_hash,
            start_nonce_hash=owner.start_nonce_hash,
            daemon_version=__version__,
            project_view_schema_version=PROJECT_VIEW_SCHEMA_VERSION,
            max_frame_bytes=MAX_FRAME,
            allowed_methods=METHODS,
            status_provider=status,
        )
        await server.start()
        client = await DaemonClient.connect_verified(
            short_project, max_frame_bytes=MAX_FRAME
        )
        pending = asyncio.create_task(client.request("status", {}))
        event_wait = asyncio.create_task(client.next_event())
        try:
            while status_calls < 2:
                await asyncio.sleep(0)
            await server.close()
            results = await asyncio.wait_for(
                asyncio.gather(pending, event_wait, return_exceptions=True), timeout=0.5
            )
            assert all(isinstance(item, DaemonClientError) for item in results)
            started = asyncio.get_running_loop().time()
            with pytest.raises(DaemonClientError, match="connection closed"):
                await client.request("status", {})
            assert asyncio.get_running_loop().time() - started < 0.02
        finally:
            status_release.set()
            await client.close()
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_stale_lease_is_rejected_before_mutation_with_sanitized_reason(
    short_project: Path,
) -> None:
    calls: list[str] = []

    def validate(lease_id: str, generation: int) -> None:
        del lease_id, generation
        raise LeaseError("stale controller lease")

    async def mutate(method: str, params: dict[str, object]) -> dict[str, object]:
        del params
        calls.append(method)
        return {}

    async def exercise() -> None:
        owner, server = await _running_server(
            short_project, mutation_handler=mutate, lease_validator=validate
        )
        try:
            client = await DaemonClient.connect_verified(
                short_project, max_frame_bytes=MAX_FRAME
            )
            try:
                with pytest.raises(DaemonClientError, match="stale controller lease"):
                    await client.request(
                        "mission.pause",
                        {},
                        lease_id="lse_old",
                        lease_generation=1,
                    )
                assert calls == []
            finally:
                await client.close()
        finally:
            await server.close()
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_queued_mutation_revalidates_the_exact_lease_at_execution_time(
    short_project: Path,
) -> None:
    authority = {"lease_id": "lse_current", "generation": 1}
    writes: list[str] = []

    def validate(lease_id: str, generation: int) -> None:
        if (lease_id, generation) != (
            authority["lease_id"], authority["generation"]
        ):
            raise LeaseError("stale controller lease")

    async def exercise() -> None:
        owner = _owner(short_project)
        service: ProjectDaemonService

        async def mutate(method: str, params: dict[str, object]) -> dict[str, object]:
            def apply() -> dict[str, object]:
                lease = params.get("_lease")
                if not isinstance(lease, Mapping):
                    raise DaemonClientRequestError(
                        "controller lease required", "lease_required"
                    )
                try:
                    validate(lease.get("lease_id"), lease.get("generation"))  # type: ignore[arg-type]
                except LeaseError as exc:
                    raise DaemonClientRequestError(
                        str(exc), "lease_required"
                    ) from None
                writes.append(method)
                return {"mutated": True}

            result = await service.submit_mutation(apply)
            assert isinstance(result, dict)
            return result

        server = DaemonServer(
            endpoint=owner.endpoint.socket_path,
            instance_id=owner.instance_id,
            project_root_hash=owner.project_root_hash,
            start_nonce_hash=owner.start_nonce_hash,
            daemon_version=__version__,
            project_view_schema_version=PROJECT_VIEW_SCHEMA_VERSION,
            max_frame_bytes=MAX_FRAME,
            allowed_methods=METHODS,
            status_provider=lambda: {"mode": "daemon_status", "state": "ready"},
            mutation_handler=mutate,
            lease_validator=validate,
        )
        service = ProjectDaemonService(
            server=server,
            reconcile_all=lambda: None,
            flush_safe_outboxes=lambda: None,
            load_scheduler_facts=lambda: None,
            apply_transition=lambda _decision: None,
        )
        await service.start()
        client = await DaemonClient.connect_verified(
            short_project, max_frame_bytes=MAX_FRAME
        )
        request = asyncio.create_task(
            client.request(
                "mission.pause",
                {"mission_id": "mis_queued"},
                lease_id="lse_current",
                lease_generation=1,
            )
        )
        try:
            for _ in range(20):
                if service._queue.qsize() == 1:
                    break
                await asyncio.sleep(0)
            assert service._queue.qsize() == 1
            authority.update({"lease_id": "lse_replacement", "generation": 2})
            await service.tick()
            with pytest.raises(DaemonClientError, match="stale controller lease"):
                await request
            assert writes == []
        finally:
            await asyncio.gather(request, return_exceptions=True)
            await client.close()
            await service.close()
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_concurrent_observers_subscribe_and_receive_same_event(short_project: Path) -> None:
    async def exercise() -> None:
        owner, server = await _running_server(short_project)
        try:
            one, two = await asyncio.gather(
                DaemonClient.connect_verified(short_project, max_frame_bytes=MAX_FRAME),
                DaemonClient.connect_verified(short_project, max_frame_bytes=MAX_FRAME),
            )
            try:
                await asyncio.gather(one.subscribe(), two.subscribe())
                server.publish_event(
                    RpcEvent("evt_1", 1, "daemon_progress", {"step": "one"})
                )
                event_one, event_two = await asyncio.gather(
                    one.next_event(timeout_seconds=1),
                    two.next_event(timeout_seconds=1),
                )
                assert event_one == event_two
                assert event_one.event_id == "evt_1"
            finally:
                await asyncio.gather(one.close(), two.close())
        finally:
            await server.close()
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_slow_subscriber_is_dropped_without_blocking_other_clients(short_project: Path) -> None:
    async def exercise() -> None:
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        loop_errors: list[dict[str, object]] = []
        loop.set_exception_handler(lambda _loop, context: loop_errors.append(context))
        owner, server = await _running_server(short_project, queue_size=2)
        try:
            slow = await DaemonClient.connect_verified(
                short_project, max_frame_bytes=MAX_FRAME
            )
            fast = await DaemonClient.connect_verified(
                short_project, max_frame_bytes=MAX_FRAME
            )
            try:
                await slow.subscribe()
                for revision in range(1, 5):
                    server.publish_event(
                        RpcEvent(
                            f"evt_{revision}",
                            revision,
                            "daemon_progress",
                            {"revision": revision},
                        )
                    )
                for revision in range(5, 1005):
                    server.publish_event(
                        RpcEvent(
                            f"evt_{revision}",
                            revision,
                            "daemon_progress",
                            {"revision": revision},
                        )
                    )
                assert server.pending_close_task_count <= 1
                await server.wait_connection_count(1, timeout_seconds=1)
                assert (await fast.request("status", {}))["state"] == "ready"
            finally:
                await asyncio.gather(slow.close(), fast.close())
        finally:
            await server.close()
            cleanup_daemon_endpoint(owner)
            gc.collect()
            await asyncio.sleep(0)
            loop.set_exception_handler(previous_handler)
        assert loop_errors == []

    _run(exercise())


def test_silent_client_is_closed_by_finite_handshake_timeout(short_project: Path) -> None:
    async def exercise() -> None:
        owner, server = await _running_server(
            short_project, read_timeout_seconds=0.05
        )
        try:
            reader, writer = await asyncio.open_unix_connection(owner.endpoint.socket_path)
            assert await asyncio.wait_for(reader.read(), timeout=0.5) == b""
            assert await server.wait_connection_count(0, timeout_seconds=0.5) == 0
            writer.close()
            await writer.wait_closed()
        finally:
            await server.close()
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_server_shutdown_awaits_connection_handlers(short_project: Path) -> None:
    async def exercise() -> None:
        owner, server = await _running_server(
            short_project, read_timeout_seconds=10
        )
        reader, writer = await asyncio.open_unix_connection(owner.endpoint.socket_path)
        try:
            await server.wait_connection_count(1, timeout_seconds=0.5)
            await server.close()
            assert await asyncio.wait_for(reader.read(), timeout=0.5) == b""
            assert server.active_handler_task_count == 0
        finally:
            writer.close()
            await writer.wait_closed()
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_client_request_deadline_includes_blocked_write_drain(
    short_project: Path,
) -> None:
    async def exercise() -> None:
        owner, server = await _running_server(short_project)
        try:
            client = await DaemonClient.connect_verified(
                short_project, max_frame_bytes=MAX_FRAME, timeout_seconds=0.05
            )
            release = asyncio.Event()

            async def blocked_drain() -> None:
                await release.wait()

            client._writer.drain = blocked_drain  # type: ignore[method-assign]
            try:
                with pytest.raises(DaemonClientError, match="timed out"):
                    await client.request("status", {})
            finally:
                release.set()
                await client.close()
        finally:
            await server.close()
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_server_write_drain_has_finite_timeout(short_project: Path) -> None:
    class BlockingWriter:
        def write(self, payload: bytes) -> None:
            del payload

        async def drain(self) -> None:
            await asyncio.sleep(60)

    async def exercise() -> None:
        owner, server = await _running_server(
            short_project, write_timeout_seconds=0.05
        )
        try:
            connection = SimpleNamespace(
                write_lock=asyncio.Lock(), closed=False, writer=BlockingWriter()
            )
            with pytest.raises(ConnectionError, match="write timed out"):
                await server._send_bytes(connection, b"{}\n")
        finally:
            await server.close()
            cleanup_daemon_endpoint(owner)

    _run(exercise())


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b'{"request_id":"secret-token"',
        b"x" * (MAX_FRAME + 1) + b"\n",
        b'{"request_id":"secret-token","method":"unknown","params":{}}\n',
    ],
)
def test_bad_first_frame_closes_cleanly_without_echoing_input(
    short_project: Path, payload: bytes
) -> None:
    async def exercise() -> None:
        owner, server = await _running_server(short_project)
        try:
            reader, writer = await asyncio.open_unix_connection(owner.endpoint.socket_path)
            if payload:
                writer.write(payload)
                await writer.drain()
            writer.write_eof()
            received = await asyncio.wait_for(reader.read(), timeout=1)
            assert b"secret-token" not in received
            assert await server.wait_connection_count(0, timeout_seconds=1) == 0
            writer.close()
            await writer.wait_closed()
        finally:
            await server.close()
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_server_cleanup_unlinks_only_the_socket_inode_it_bound(short_project: Path) -> None:
    async def exercise() -> None:
        owner, server = await _running_server(short_project)
        replacement = owner.endpoint.socket_path.with_name("replacement.sock")
        replacement_socket = socket.socket(socket.AF_UNIX)
        try:
            replacement_socket.bind(str(replacement))
            owner.endpoint.socket_path.unlink()
            replacement.rename(owner.endpoint.socket_path)
            await server.close()
            assert owner.endpoint.socket_path.exists()
            assert stat.S_ISSOCK(owner.endpoint.socket_path.lstat().st_mode)
        finally:
            replacement_socket.close()
            owner.endpoint.socket_path.unlink(missing_ok=True)
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_server_prebinds_socket_instead_of_delegating_path_replacement(
    short_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        owner = _owner(short_project)
        real_start = server_module.asyncio.start_unix_server
        path_delegated = False
        attacker = socket.socket(socket.AF_UNIX)

        async def inject_if_path_is_delegated(*args: object, **kwargs: object):
            nonlocal path_delegated
            path = kwargs.get("path")
            if path is not None:
                path_delegated = True
                attacker.bind(str(path))
            return await real_start(*args, **kwargs)

        monkeypatch.setattr(
            server_module.asyncio, "start_unix_server", inject_if_path_is_delegated
        )
        server = DaemonServer(
            endpoint=owner.endpoint.socket_path,
            instance_id=owner.instance_id,
            project_root_hash=owner.project_root_hash,
            start_nonce_hash=owner.start_nonce_hash,
            daemon_version=__version__,
            project_view_schema_version=PROJECT_VIEW_SCHEMA_VERSION,
            max_frame_bytes=MAX_FRAME,
            allowed_methods=METHODS,
            status_provider=lambda: {"mode": "daemon_status"},
        )
        try:
            await server.start()
            assert path_delegated is False
        finally:
            await server.close()
            attacker.close()
            owner.endpoint.socket_path.unlink(missing_ok=True)
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_server_bind_runtime_swap_never_chmods_or_unlinks_external_file(
    short_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        owner = _owner(short_project)
        runtime = owner.endpoint.socket_path.parent
        parked = runtime.with_name("runtime-parked")
        outside = short_project / "outside-runtime"
        outside.mkdir()
        outside_socket = outside / "daemon.sock"
        outside_socket.write_text("external", encoding="utf-8")
        outside_socket.chmod(0o644)
        original = lifecycle_module.DaemonEndpointBinding.assert_socket_identity
        swapped = False

        def swap_then_assert(binding: object, identity: tuple[int, int]) -> None:
            nonlocal swapped
            if not swapped:
                swapped = True
                runtime.rename(parked)
                runtime.symlink_to(outside, target_is_directory=True)
            original(binding, identity)

        monkeypatch.setattr(
            lifecycle_module.DaemonEndpointBinding,
            "assert_socket_identity",
            swap_then_assert,
        )
        server = DaemonServer(
            endpoint=owner.endpoint.socket_path,
            instance_id=owner.instance_id,
            project_root_hash=owner.project_root_hash,
            start_nonce_hash=owner.start_nonce_hash,
            daemon_version=__version__,
            project_view_schema_version=PROJECT_VIEW_SCHEMA_VERSION,
            max_frame_bytes=MAX_FRAME,
            allowed_methods=METHODS,
            status_provider=lambda: {"mode": "daemon_status"},
        )
        try:
            with pytest.raises(Exception):
                await server.start()
            assert swapped is True
            assert outside_socket.read_text(encoding="utf-8") == "external"
            assert stat.S_IMODE(outside_socket.stat().st_mode) == 0o644
        finally:
            await server.close()
            if runtime.is_symlink():
                runtime.unlink()
                parked.rename(runtime)
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_server_prebind_runtime_swap_writes_only_held_runtime(
    short_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        owner = _owner(short_project)
        runtime = owner.endpoint.socket_path.parent
        parked = runtime.with_name("runtime-parked")
        outside = short_project / "outside-runtime"
        outside.mkdir()
        original = getattr(
            lifecycle_module.DaemonEndpointBinding,
            "duplicate_runtime_fd",
            None,
        )
        swapped = False

        def swap_before_bind(binding: object) -> int:
            nonlocal swapped
            swapped = True
            runtime.rename(parked)
            runtime.symlink_to(outside, target_is_directory=True)
            assert original is not None
            return original(binding)

        monkeypatch.setattr(
            lifecycle_module.DaemonEndpointBinding,
            "duplicate_runtime_fd",
            swap_before_bind,
            raising=False,
        )
        server = DaemonServer(
            endpoint=owner.endpoint.socket_path,
            instance_id=owner.instance_id,
            project_root_hash=owner.project_root_hash,
            start_nonce_hash=owner.start_nonce_hash,
            daemon_version=__version__,
            project_view_schema_version=PROJECT_VIEW_SCHEMA_VERSION,
            max_frame_bytes=MAX_FRAME,
            allowed_methods=METHODS,
            status_provider=lambda: {"mode": "daemon_status"},
        )
        try:
            with pytest.raises(Exception):
                await server.start()
            assert swapped is True
            assert not (outside / "daemon.sock").exists()
            assert not (parked / "daemon.sock").exists()
        finally:
            await server.close()
            if runtime.is_symlink():
                runtime.unlink()
                parked.rename(runtime)
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_server_start_rejects_multithreaded_process_without_global_mutation(
    short_project: Path,
) -> None:
    async def exercise() -> None:
        owner = _owner(short_project)
        stop = threading.Event()
        helper = threading.Thread(target=stop.wait, daemon=True)
        helper.start()
        before_cwd = os.getcwd()
        before_umask = os.umask(0)
        os.umask(before_umask)
        server = DaemonServer(
            endpoint=owner.endpoint.socket_path,
            instance_id=owner.instance_id,
            project_root_hash=owner.project_root_hash,
            start_nonce_hash=owner.start_nonce_hash,
            daemon_version=__version__,
            project_view_schema_version=PROJECT_VIEW_SCHEMA_VERSION,
            max_frame_bytes=MAX_FRAME,
            allowed_methods=METHODS,
            status_provider=lambda: {"mode": "daemon_status"},
        )
        try:
            with pytest.raises(Exception, match="single-thread"):
                await server.start()
            after_umask = os.umask(0)
            os.umask(after_umask)
            assert after_umask == before_umask
            assert os.getcwd() == before_cwd
            assert not owner.endpoint.socket_path.exists()
        finally:
            stop.set()
            helper.join(timeout=1)
            await server.close()
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_server_socket_is_created_0600_without_chmod_replacement_window(
    short_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        owner = _owner(short_project)
        outside = short_project / "outside-target"
        outside.write_text("external", encoding="utf-8")
        outside.chmod(0o644)
        original = getattr(
            lifecycle_module.DaemonEndpointBinding,
            "assert_socket_mode",
            None,
        )
        injected = False

        def replace_before_mode_check(
            binding: object, identity: tuple[int, int], mode: int
        ) -> None:
            nonlocal injected
            injected = True
            endpoint = binding.endpoint.socket_path
            endpoint.unlink()
            endpoint.symlink_to(outside)
            assert original is not None
            original(binding, identity, mode)

        monkeypatch.setattr(
            lifecycle_module.DaemonEndpointBinding,
            "assert_socket_mode",
            replace_before_mode_check,
            raising=False,
        )
        before = os.umask(0)
        os.umask(before)
        before_cwd = os.getcwd()
        server = DaemonServer(
            endpoint=owner.endpoint.socket_path,
            instance_id=owner.instance_id,
            project_root_hash=owner.project_root_hash,
            start_nonce_hash=owner.start_nonce_hash,
            daemon_version=__version__,
            project_view_schema_version=PROJECT_VIEW_SCHEMA_VERSION,
            max_frame_bytes=MAX_FRAME,
            allowed_methods=METHODS,
            status_provider=lambda: {"mode": "daemon_status"},
        )
        try:
            with pytest.raises(Exception):
                await server.start()
            after = os.umask(0)
            os.umask(after)
            assert after == before
            assert os.getcwd() == before_cwd
            assert injected is True
            assert stat.S_IMODE(outside.stat().st_mode) == 0o644
            assert outside.read_text(encoding="utf-8") == "external"
        finally:
            await server.close()
            owner.endpoint.socket_path.unlink(missing_ok=True)
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_connect_or_start_uses_argv_python_and_project_local_logs(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        spawned: list[dict[str, object]] = []

        class FakeProcess:
            pass

        async def spawn(*argv: str, **kwargs: object):
            spawned.append({"argv": argv, **kwargs})
            raise RuntimeError("stop after spawn inspection")

        config = SimpleNamespace(
            daemon=DaemonConfig(start_timeout_seconds=1, max_frame_bytes=MAX_FRAME)
        )
        with pytest.raises(RuntimeError, match="spawn inspection"):
            await connect_or_start(tmp_path, config, spawn_factory=spawn)

        assert len(spawned) == 1
        call = spawned[0]
        assert call["argv"][0] == sys.executable
        assert call["argv"][1:4] == ("-m", "agentdeck", "_daemon")
        assert "shell" not in call
        assert call["start_new_session"] is True
        assert Path(call["cwd"]).resolve() == tmp_path.resolve()
        assert call["stdout"] is asyncio.subprocess.DEVNULL
        assert call["stderr"] is asyncio.subprocess.DEVNULL
        environment = call["env"]
        runtime = tmp_path / ".agentdeck" / "runtime"
        assert environment["AGENTDECK_DAEMON_STDOUT_LOG"] == str(
            runtime / "daemon.stdout.log"
        )
        assert environment["AGENTDECK_DAEMON_STDERR_LOG"] == str(
            runtime / "daemon.stderr.log"
        )
        assert int(environment["AGENTDECK_DAEMON_LOG_CAP_BYTES"]) > 0

    _run(exercise())


def test_default_detached_spawn_reaper_registry_clears_after_child_exit() -> None:
    baseline_threads = threading.active_count()

    async def exercise() -> tuple[object, int]:
        process = await client_module._spawn_detached_process(
            sys.executable,
            "-c",
            "import time; time.sleep(0.1)",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        assert client_module._detached_reaper_count() == 1
        return process, process.pid

    process, pid = _run(exercise())
    deadline = time.monotonic() + 2
    while client_module._detached_reaper_count() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert process.returncode == 0
    assert client_module._detached_reaper_count() == 0
    assert threading.active_count() == baseline_threads
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_connect_or_start_cancellation_never_terminates_spawned_process(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        process = SimpleNamespace(terminate_calls=0, kill_calls=0)

        async def spawn(*argv: str, **kwargs: object):
            del argv, kwargs
            return process

        config = SimpleNamespace(
            daemon=DaemonConfig(start_timeout_seconds=2, max_frame_bytes=MAX_FRAME)
        )
        task = asyncio.create_task(
            connect_or_start(tmp_path, config, spawn_factory=spawn, retry_interval=0.01)
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.terminate_calls == process.kill_calls == 0

    _run(exercise())


def test_request_wait_is_bounded_when_handler_stalls(short_project: Path) -> None:
    async def mutate(method: str, params: dict[str, object]) -> dict[str, object]:
        del method, params
        await asyncio.sleep(0.15)
        return {"unreachable": True}

    async def exercise() -> None:
        owner, server = await _running_server(
            short_project,
            mutation_handler=mutate,
            lease_validator=lambda lease_id, generation: None,
        )
        try:
            client = await DaemonClient.connect_verified(
                short_project,
                max_frame_bytes=MAX_FRAME,
                timeout_seconds=0.05,
            )
            try:
                with pytest.raises(DaemonClientError, match="timed out"):
                    await client.request(
                        "mission.pause",
                        {"mission_id": "mis_1"},
                        lease_id="lse_current",
                        lease_generation=1,
                    )
                await asyncio.sleep(0.2)
                assert (await client.request("status", {}))["state"] == "ready"
            finally:
                await client.close()
        finally:
            await server.close()
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_connect_or_start_rejects_log_symlink_without_external_write(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        outside = tmp_path / "outside"
        outside.write_text("keep", encoding="utf-8")
        runtime = tmp_path / ".agentdeck" / "runtime"
        runtime.mkdir(parents=True)
        (runtime / "daemon.stdout.log").symlink_to(outside)
        config = SimpleNamespace(
            daemon=DaemonConfig(start_timeout_seconds=1, max_frame_bytes=MAX_FRAME)
        )

        async def never_spawn(*argv: str, **kwargs: object):
            raise AssertionError((argv, kwargs))

        with pytest.raises(DaemonUnavailable, match="symlink"):
            await connect_or_start(tmp_path, config, spawn_factory=never_spawn)
        assert outside.read_text(encoding="utf-8") == "keep"

    _run(exercise())


def test_spawn_lock_identity_mismatch_closes_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_open = client_module.os.open
    real_stat = client_module.os.stat
    captured: list[int] = []

    def tracking_open(path: object, *args: object, **kwargs: object) -> int:
        descriptor = real_open(path, *args, **kwargs)
        if path == "daemon.spawn.lock":
            captured.append(descriptor)
        return descriptor

    def mismatching_stat(path: object, *args: object, **kwargs: object):
        value = real_stat(path, *args, **kwargs)
        if path == "daemon.spawn.lock":
            return SimpleNamespace(
                st_mode=value.st_mode,
                st_dev=value.st_dev,
                st_ino=value.st_ino + 1,
            )
        return value

    monkeypatch.setattr(client_module.os, "open", tracking_open)
    monkeypatch.setattr(client_module.os, "stat", mismatching_stat)
    config = SimpleNamespace(
        daemon=DaemonConfig(start_timeout_seconds=1, max_frame_bytes=MAX_FRAME)
    )

    with pytest.raises(DaemonUnavailable, match="identity changed"):
        _run(connect_or_start(tmp_path, config))
    assert captured
    for descriptor in captured:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_healthy_daemon_connect_skips_spawn_lock_and_log_validation(
    short_project: Path,
) -> None:
    async def exercise() -> None:
        owner, server = await _running_server(short_project)
        outside = short_project / "outside"
        outside.write_text("keep", encoding="utf-8")
        (owner.endpoint.socket_path.parent / "daemon.stdout.log").symlink_to(outside)

        async def never_spawn(*argv: str, **kwargs: object):
            raise AssertionError((argv, kwargs))

        config = SimpleNamespace(
            daemon=DaemonConfig(start_timeout_seconds=1, max_frame_bytes=MAX_FRAME)
        )
        try:
            client = await connect_or_start(
                short_project, config, spawn_factory=never_spawn
            )
            await client.close()
            assert outside.read_text(encoding="utf-8") == "keep"
            assert not (owner.endpoint.socket_path.parent / "daemon.spawn.lock").exists()
        finally:
            (owner.endpoint.socket_path.parent / "daemon.stdout.log").unlink(
                missing_ok=True
            )
            await server.close()
            cleanup_daemon_endpoint(owner)

    _run(exercise())


def test_connect_or_start_reaches_real_fixture_subprocess(short_project: Path) -> None:
    async def exercise() -> None:
        processes: list[asyncio.subprocess.Process] = []

        async def spawn(*argv: str, **kwargs: object):
            del argv
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(Path(__file__).parent / "fixtures" / "fake_daemon_server.py"),
                "--project",
                str(short_project),
                "--lifetime",
                "0.5",
                **kwargs,
            )
            processes.append(process)
            return process

        config = SimpleNamespace(
            daemon=DaemonConfig(start_timeout_seconds=2, max_frame_bytes=MAX_FRAME)
        )
        client = await connect_or_start(
            short_project, config, spawn_factory=spawn, retry_interval=0.01
        )
        try:
            assert client.compatible is True
            assert (await client.request("status", {}))["state"] == "ready"
        finally:
            await client.close()
        assert len(processes) == 1
        assert await asyncio.wait_for(processes[0].wait(), timeout=2) == 0

    _run(exercise())


def test_concurrent_connect_or_start_spawns_once_and_shares_instance(
    short_project: Path,
) -> None:
    async def exercise() -> None:
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        loop_errors: list[dict[str, object]] = []
        loop.set_exception_handler(lambda _loop, context: loop_errors.append(context))
        processes: list[asyncio.subprocess.Process] = []
        spawn_calls = 0

        async def spawn(*argv: str, **kwargs: object):
            nonlocal spawn_calls
            del argv
            spawn_calls += 1
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(Path(__file__).parent / "fixtures" / "fake_daemon_server.py"),
                "--project",
                str(short_project),
                "--lifetime",
                "0.7",
                **kwargs,
            )
            processes.append(process)
            return process

        config = SimpleNamespace(
            daemon=DaemonConfig(start_timeout_seconds=2, max_frame_bytes=MAX_FRAME)
        )
        try:
            clients = await asyncio.gather(
                *(connect_or_start(
                    short_project, config, spawn_factory=spawn, retry_interval=0.01
                ) for _ in range(4))
            )
            try:
                assert spawn_calls == 1
                assert len({client.instance_id for client in clients}) == 1
            finally:
                await asyncio.gather(*(client.close() for client in clients))
            assert await asyncio.wait_for(processes[0].wait(), timeout=2) == 0
            gc.collect()
            await asyncio.sleep(0)
            assert loop_errors == []
        finally:
            loop.set_exception_handler(previous_handler)

    _run(exercise())


def test_connect_or_start_refreshes_remaining_after_failed_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        calls = 0

        async def slow_second_connect(
            cls: type[DaemonClient], root: Path, **kwargs: object
        ) -> DaemonClient:
            nonlocal calls
            del cls, root, kwargs
            calls += 1
            if calls == 2:
                await asyncio.sleep(0.06)
            raise DaemonUnavailable("not ready")

        async def never_spawn(*args: object, **kwargs: object):
            raise AssertionError((args, kwargs))

        monkeypatch.setattr(
            DaemonClient, "connect_verified", classmethod(slow_second_connect)
        )
        monkeypatch.setattr(client_module, "_validate_log_targets", lambda root: None)
        monkeypatch.setattr(client_module, "_try_spawn_lock", lambda root: None)
        config = SimpleNamespace(
            daemon=SimpleNamespace(
                start_timeout_seconds=0.05,
                max_frame_bytes=MAX_FRAME,
            )
        )
        started = asyncio.get_running_loop().time()
        with pytest.raises(DaemonUnavailable, match="ready"):
            await connect_or_start(
                tmp_path,
                config,
                spawn_factory=never_spawn,
                retry_interval=0.05,
            )
        elapsed = asyncio.get_running_loop().time() - started
        assert elapsed <= 0.085
        assert calls == 2

    _run(exercise())


def test_child_rotating_stdio_remains_capped_across_lifetime_and_restart(
    short_project: Path,
) -> None:
    async def exercise() -> None:
        cap = 4096
        config = SimpleNamespace(
            daemon=DaemonConfig(start_timeout_seconds=2, max_frame_bytes=MAX_FRAME)
        )
        processes: list[asyncio.subprocess.Process] = []

        async def spawn(*argv: str, **kwargs: object):
            del argv
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(Path(__file__).parent / "fixtures" / "fake_daemon_server.py"),
                "--project",
                str(short_project),
                "--lifetime",
                "0.2",
                "--spam-bytes",
                str(cap * 4),
                **kwargs,
            )
            processes.append(process)
            return process

        for _ in range(2):
            client = await connect_or_start(
                short_project,
                config,
                spawn_factory=spawn,
                retry_interval=0.01,
                log_cap_bytes=cap,
            )
            await client.close()
            assert await asyncio.wait_for(processes[-1].wait(), timeout=2) == 0

        logs = sorted(
            (short_project / ".agentdeck" / "runtime").glob("daemon.*.log*")
        )
        assert 1 <= len(logs) <= 4
        assert sum(path.stat().st_size for path in logs) <= cap
        assert all(not path.is_symlink() for path in logs)

    _run(exercise())
