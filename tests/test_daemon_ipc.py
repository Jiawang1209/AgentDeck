from __future__ import annotations

import asyncio
from pathlib import Path
import socket
import stat
import shutil
import sys
import tempfile
from types import SimpleNamespace

import pytest

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
    decode_request,
    encode_request,
)
from agentdeck.daemon.server import DaemonServer
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
        allowed_methods=METHODS,
        event_queue_size=queue_size,
        request_queue_size=queue_size,
        status_provider=lambda: {"mode": "daemon_status", "state": "ready"},
        mutation_handler=mutation_handler,
        lease_validator=lease_validator,
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
                assert calls == [{"method": "mission.pause", "mission_id": "mis_1"}]
            finally:
                await client.close()
        finally:
            await server.close()
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
                await server.wait_connection_count(1, timeout_seconds=1)
                assert (await fast.request("status", {}))["state"] == "ready"
            finally:
                await asyncio.gather(slow.close(), fast.close())
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
        for key in ("stdout", "stderr"):
            stream = call[key]
            assert Path(stream.name).is_relative_to(tmp_path / ".agentdeck" / "runtime")
            stream.close()

    _run(exercise())


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
