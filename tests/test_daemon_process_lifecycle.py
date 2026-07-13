from __future__ import annotations

import json
import math
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
from typing import Mapping

import pytest

import agentdeck.daemon.lifecycle as lifecycle
from agentdeck.daemon.lifecycle import (
    DaemonEndpoint,
    DaemonIdentityError,
    DaemonOwnership,
    acquire_daemon_ownership,
    can_stop_daemon,
    cleanup_daemon_endpoint,
    daemon_endpoint,
    daemon_keepalive_reasons,
    project_root_hash,
    reconcile_endpoint,
)


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    os.replace(temporary, path)


def _proof_from_file(
    metadata: Mapping[str, object], proof_path: Path
) -> Mapping[str, object] | None:
    del metadata
    try:
        value = json.loads(proof_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _contend_for_daemon(
    project: str,
    gate: multiprocessing.synchronize.Barrier,
    results: multiprocessing.queues.Queue,
) -> None:
    root = Path(project)
    proof_path = root / ".agentdeck" / "runtime" / "health-proof.json"
    gate.wait()
    try:
        ownership = acquire_daemon_ownership(
            root,
            start_nonce=f"nonce-{os.getpid()}",
            health_probe=lambda metadata: _proof_from_file(metadata, proof_path),
            wait_timeout_seconds=2.0,
            poll_interval_seconds=0.01,
        )
        if ownership.role == "owner":
            _atomic_json(proof_path, ownership.health_proof())
            time.sleep(0.35)
        results.put((ownership.role, ownership.instance_id, None))
        ownership.release()
    except Exception as exc:  # pragma: no cover - emitted to the parent assertion
        results.put(("error", "", f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"))


def _run_two_start_contenders(project: Path) -> list[tuple[str, str, str | None]]:
    context = multiprocessing.get_context("spawn")
    gate = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(
            target=_contend_for_daemon,
            args=(str(project), gate, results),
        )
        for _ in range(2)
    ]
    try:
        for process in processes:
            process.start()
        output = [results.get(timeout=5) for _ in processes]
        for process in processes:
            process.join(timeout=5)
            assert process.exitcode == 0
        return output
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            if process.pid is not None:
                process.join(timeout=5)
        results.close()
        results.join_thread()


def _metadata(
    root: Path,
    *,
    pid: int,
    instance_id: str = "dmn_existing",
    nonce_hash: str = "a" * 64,
    root_hash: str | None = None,
) -> dict[str, object]:
    return {
        "instance_id": instance_id,
        "project_root_hash": root_hash or project_root_hash(root),
        "start_nonce_hash": nonce_hash,
        "pid": pid,
    }


def _swap_runtime_after_validation(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    outside: Path,
) -> Path:
    runtime = project / ".agentdeck" / "runtime"
    parked = project / ".agentdeck" / "runtime-parked"
    original = lifecycle._reject_endpoint_symlinks
    swapped = False

    def swap_after_validation(endpoint: object) -> None:
        nonlocal swapped
        original(endpoint)
        if not swapped:
            swapped = True
            runtime.rename(parked)
            runtime.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(lifecycle, "_reject_endpoint_symlinks", swap_after_validation)
    return parked


def test_project_endpoint_uses_canonical_project_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(project, target_is_directory=True)

    direct = daemon_endpoint(project)
    via_alias = daemon_endpoint(alias)

    assert direct == via_alias == DaemonEndpoint(
        metadata_path=project / ".agentdeck" / "runtime" / "daemon.json",
        socket_path=project / ".agentdeck" / "runtime" / "daemon.sock",
        lock_path=project / ".agentdeck" / "runtime" / "daemon.lock",
    )
    assert project_root_hash(project) == project_root_hash(alias)


def test_concurrent_startup_elects_one_verified_owner(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    results = _run_two_start_contenders(project)

    assert [item[2] for item in results] == [None, None]
    assert sorted(item[0] for item in results) == ["follower", "owner"]
    assert len({item[1] for item in results}) == 1


def test_follower_rejects_metadata_without_matching_health_proof(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    owner = acquire_daemon_ownership(
        project,
        start_nonce="owner-nonce",
        health_probe=lambda metadata: metadata,
    )
    try:
        with pytest.raises(DaemonIdentityError, match="verified daemon"):
            acquire_daemon_ownership(
                project,
                start_nonce="follower-nonce",
                health_probe=lambda metadata: {
                    **metadata,
                    "healthy": True,
                    "start_nonce_hash": "0" * 64,
                },
                wait_timeout_seconds=1,
                poll_interval_seconds=0.005,
            )
    finally:
        cleanup_daemon_endpoint(owner)


def test_owner_metadata_is_compact_atomic_and_nonce_is_hashed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    owner = acquire_daemon_ownership(
        project,
        start_nonce="secret-start-nonce",
        health_probe=lambda metadata: metadata,
    )
    try:
        metadata = json.loads(owner.endpoint.metadata_path.read_text(encoding="utf-8"))
        assert set(metadata) == {
            "instance_id",
            "project_root_hash",
            "start_nonce_hash",
            "pid",
        }
        assert owner.health_proof() == metadata | {"healthy": True}
        assert "secret-start-nonce" not in owner.endpoint.metadata_path.read_text(
            encoding="utf-8"
        )
        assert not list(owner.endpoint.metadata_path.parent.glob("*.tmp"))
    finally:
        cleanup_daemon_endpoint(owner)


def test_reconcile_dead_owner_removes_stale_metadata_and_socket(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    endpoint = daemon_endpoint(project)
    endpoint.metadata_path.parent.mkdir(parents=True)
    _atomic_json(endpoint.metadata_path, _metadata(project, pid=999_999_999))
    endpoint.socket_path.write_text("stale", encoding="utf-8")

    assert reconcile_endpoint(
        project,
        expected_project_hash=project_root_hash(project),
        health_probe=lambda metadata: None,
    )
    assert not endpoint.metadata_path.exists()
    assert not endpoint.socket_path.exists()


def test_stale_metadata_never_kills_or_unlinks_unverified_live_process(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    endpoint = daemon_endpoint(project)
    endpoint.metadata_path.parent.mkdir(parents=True)
    innocent = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    try:
        _atomic_json(
            endpoint.metadata_path,
            _metadata(project, pid=innocent.pid, nonce_hash="b" * 64),
        )
        endpoint.socket_path.write_text("unverified", encoding="utf-8")

        assert not reconcile_endpoint(
            project,
            expected_project_hash=project_root_hash(project),
            health_probe=lambda metadata: {
                **metadata,
                "healthy": True,
                "start_nonce_hash": "c" * 64,
            },
        )
        assert innocent.poll() is None
        assert endpoint.metadata_path.exists()
        assert endpoint.socket_path.exists()
    finally:
        innocent.terminate()
        innocent.wait(timeout=5)


def test_reconcile_requires_startup_lock_before_stale_cleanup(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    owner = acquire_daemon_ownership(
        project,
        start_nonce="owner-nonce",
        health_probe=lambda metadata: metadata,
    )
    owner.endpoint.socket_path.write_text("owned", encoding="utf-8")
    try:
        assert not reconcile_endpoint(
            project,
            expected_project_hash=project_root_hash(project),
            health_probe=lambda metadata: None,
        )
        assert owner.endpoint.metadata_path.exists()
        assert owner.endpoint.socket_path.exists()
    finally:
        cleanup_daemon_endpoint(owner)


def test_cleanup_removes_only_endpoint_owned_by_exact_instance(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    owner = acquire_daemon_ownership(
        project,
        start_nonce="owner-nonce",
        health_probe=lambda metadata: metadata,
    )
    owner.endpoint.socket_path.write_text("owned", encoding="utf-8")
    replacement = _metadata(
        project,
        pid=os.getpid(),
        instance_id="dmn_replacement",
        nonce_hash="d" * 64,
    )
    _atomic_json(owner.endpoint.metadata_path, replacement)

    assert not cleanup_daemon_endpoint(owner)
    assert owner.endpoint.metadata_path.exists()
    assert owner.endpoint.socket_path.exists()
    owner.release()


def test_cleanup_matching_owner_removes_metadata_and_socket(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    owner = acquire_daemon_ownership(
        project,
        start_nonce="owner-nonce",
        health_probe=lambda metadata: metadata,
    )
    owner.endpoint.socket_path.write_text("owned", encoding="utf-8")

    assert cleanup_daemon_endpoint(owner)
    assert not owner.endpoint.metadata_path.exists()
    assert not owner.endpoint.socket_path.exists()
    assert owner.endpoint.lock_path.exists()


def test_cleanup_rejects_lock_inode_replaced_between_stat_and_second_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    owner = acquire_daemon_ownership(
        project,
        start_nonce="owner-nonce",
        health_probe=lambda metadata: metadata,
    )
    owner.endpoint.socket_path.write_text("owned", encoding="utf-8")
    real_open = lifecycle.os.open
    real_flock = lifecycle.fcntl.flock
    replacement_lock_fd: int | None = None
    replaced = False

    def replace_before_second_open(
        path: object, flags: int, *args: object, **kwargs: object
    ) -> int:
        nonlocal replacement_lock_fd, replaced
        if (
            not replaced
            and Path(path).name == "daemon.lock"
            and not flags & os.O_CREAT
        ):
            replaced = True
            owner.endpoint.lock_path.unlink()
            replacement_lock_fd = real_open(
                owner.endpoint.lock_path,
                os.O_RDWR | os.O_CREAT,
                0o600,
            )
            real_flock(replacement_lock_fd, lifecycle.fcntl.LOCK_EX)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(lifecycle.os, "open", replace_before_second_open)
    try:
        assert not cleanup_daemon_endpoint(owner)
        assert owner.endpoint.metadata_path.exists()
        assert owner.endpoint.socket_path.exists()
        assert owner._capability.released
    finally:
        if replacement_lock_fd is not None:
            real_flock(replacement_lock_fd, lifecycle.fcntl.LOCK_UN)
            os.close(replacement_lock_fd)


def test_existing_healthy_unlock_failure_closes_fds_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    original_owner = acquire_daemon_ownership(
        project,
        start_nonce="owner-nonce",
        health_probe=lambda metadata: metadata,
    )
    proof = original_owner.health_proof()
    original_owner.release()

    real_flock = lifecycle.fcntl.flock
    real_fdopen = lifecycle.os.fdopen
    retained_streams: list[object] = []
    unlock_attempts = 0

    def retaining_fdopen(*args: object, **kwargs: object) -> object:
        stream = real_fdopen(*args, **kwargs)
        retained_streams.append(stream)
        return stream

    def fail_unlock_once(descriptor: int, operation: int) -> None:
        nonlocal unlock_attempts
        if operation == lifecycle.fcntl.LOCK_UN:
            unlock_attempts += 1
            if unlock_attempts == 1:
                raise OSError("unlock failed")
        real_flock(descriptor, operation)

    monkeypatch.setattr(lifecycle.os, "fdopen", retaining_fdopen)
    monkeypatch.setattr(lifecycle.fcntl, "flock", fail_unlock_once)

    with pytest.raises(OSError, match="unlock failed"):
        acquire_daemon_ownership(
            project,
            start_nonce="new-nonce",
            health_probe=lambda metadata: proof,
        )

    assert unlock_attempts == 1
    assert retained_streams
    assert all(getattr(stream, "closed") for stream in retained_streams)


def test_forged_owner_cannot_remove_live_owner_endpoint(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    owner = acquire_daemon_ownership(
        project,
        start_nonce="owner-nonce",
        health_probe=lambda metadata: metadata,
    )
    owner.endpoint.socket_path.write_text("owned", encoding="utf-8")
    try:
        with pytest.raises(TypeError, match="cannot be constructed directly"):
            DaemonOwnership(
                role="owner",
                instance_id=owner.instance_id,
                endpoint=owner.endpoint,
                project_root_hash=owner.project_root_hash,
                start_nonce_hash=owner.start_nonce_hash,
                pid=owner.pid,
            )
        assert owner.endpoint.metadata_path.exists()
        assert owner.endpoint.socket_path.exists()
    finally:
        cleanup_daemon_endpoint(owner)


def test_follower_and_released_owner_cannot_remove_endpoint(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    owner = acquire_daemon_ownership(
        project,
        start_nonce="owner-nonce",
        health_probe=lambda metadata: metadata,
    )
    owner.endpoint.socket_path.write_text("owned", encoding="utf-8")
    follower = acquire_daemon_ownership(
        project,
        start_nonce="follower-nonce",
        health_probe=lambda metadata: owner.health_proof(),
        wait_timeout_seconds=1,
        poll_interval_seconds=0.01,
    )
    assert follower.role == "follower"
    assert not cleanup_daemon_endpoint(follower)
    assert owner.endpoint.metadata_path.exists()
    owner.release()
    assert not cleanup_daemon_endpoint(owner)
    assert owner.endpoint.metadata_path.exists()
    assert owner.endpoint.socket_path.exists()


def test_runtime_symlink_is_rejected_without_external_write(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    agentdeck = project / ".agentdeck"
    agentdeck.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (agentdeck / "runtime").symlink_to(outside, target_is_directory=True)

    with pytest.raises(DaemonIdentityError, match="symlink"):
        acquire_daemon_ownership(
            project,
            start_nonce="owner-nonce",
            health_probe=lambda metadata: metadata,
        )

    assert list(outside.iterdir()) == []
    assert (agentdeck / "runtime").is_symlink()


@pytest.mark.parametrize("endpoint_name", ["daemon.json", "daemon.sock"])
def test_endpoint_symlink_is_rejected_without_write_or_delete(
    tmp_path: Path, endpoint_name: str
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    runtime = project / ".agentdeck" / "runtime"
    runtime.mkdir(parents=True)
    outside = tmp_path / "outside-data"
    outside.write_text("do-not-touch", encoding="utf-8")
    endpoint_link = runtime / endpoint_name
    endpoint_link.symlink_to(outside)

    with pytest.raises(DaemonIdentityError, match="symlink"):
        acquire_daemon_ownership(
            project,
            start_nonce="owner-nonce",
            health_probe=lambda metadata: metadata,
        )

    assert outside.read_text(encoding="utf-8") == "do-not-touch"
    assert endpoint_link.is_symlink()
    assert not (runtime / "daemon.lock").exists()


def test_runtime_swap_after_validation_cannot_write_outside_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    runtime = project / ".agentdeck" / "runtime"
    runtime.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    parked = _swap_runtime_after_validation(monkeypatch, project, outside)

    with pytest.raises(DaemonIdentityError, match="runtime"):
        acquire_daemon_ownership(
            project,
            start_nonce="owner-nonce",
            health_probe=lambda metadata: metadata,
        )

    assert list(outside.iterdir()) == []
    assert parked.is_dir()


def test_runtime_swap_after_validation_cannot_reconcile_outside_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    runtime = project / ".agentdeck" / "runtime"
    runtime.mkdir(parents=True)
    _atomic_json(runtime / "daemon.json", _metadata(project, pid=999_999_999))
    (runtime / "daemon.sock").write_text("old", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_metadata = _metadata(project, pid=999_999_999)
    _atomic_json(outside / "daemon.json", outside_metadata)
    (outside / "daemon.sock").write_text("outside", encoding="utf-8")
    _swap_runtime_after_validation(monkeypatch, project, outside)

    with pytest.raises(DaemonIdentityError, match="runtime"):
        reconcile_endpoint(
            project,
            expected_project_hash=project_root_hash(project),
            health_probe=lambda metadata: None,
        )

    assert json.loads((outside / "daemon.json").read_text(encoding="utf-8")) == outside_metadata
    assert (outside / "daemon.sock").read_text(encoding="utf-8") == "outside"


def test_runtime_swap_after_validation_cannot_cleanup_outside_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    owner = acquire_daemon_ownership(
        project,
        start_nonce="owner-nonce",
        health_probe=lambda metadata: metadata,
    )
    owner.endpoint.socket_path.write_text("old", encoding="utf-8")
    metadata = json.loads(owner.endpoint.metadata_path.read_text(encoding="utf-8"))
    outside = tmp_path / "outside"
    outside.mkdir()
    _atomic_json(outside / "daemon.json", metadata)
    (outside / "daemon.sock").write_text("outside", encoding="utf-8")
    _swap_runtime_after_validation(monkeypatch, project, outside)

    assert not cleanup_daemon_endpoint(owner)
    assert json.loads((outside / "daemon.json").read_text(encoding="utf-8")) == metadata
    assert (outside / "daemon.sock").read_text(encoding="utf-8") == "outside"


def test_lock_fd_is_closed_when_fdopen_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    real_open = lifecycle.os.open
    captured: list[int] = []

    def tracking_open(path: object, *args: object, **kwargs: object) -> int:
        descriptor = real_open(path, *args, **kwargs)
        if Path(path).name == "daemon.lock":
            captured.append(descriptor)
        return descriptor

    def failing_fdopen(*args: object, **kwargs: object) -> object:
        raise OSError("fdopen failed")

    monkeypatch.setattr(lifecycle.os, "open", tracking_open)
    monkeypatch.setattr(lifecycle.os, "fdopen", failing_fdopen)

    with pytest.raises(OSError, match="fdopen failed"):
        acquire_daemon_ownership(
            project,
            start_nonce="owner-nonce",
            health_probe=lambda metadata: metadata,
        )

    assert captured
    for descriptor in captured:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_lock_fd_is_closed_when_initial_flock_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    real_open = lifecycle.os.open
    real_fdopen = lifecycle.os.fdopen
    captured: list[int] = []
    retained_streams: list[object] = []

    def tracking_open(path: object, *args: object, **kwargs: object) -> int:
        descriptor = real_open(path, *args, **kwargs)
        if Path(path).name == "daemon.lock":
            captured.append(descriptor)
        return descriptor

    def retaining_fdopen(*args: object, **kwargs: object) -> object:
        stream = real_fdopen(*args, **kwargs)
        retained_streams.append(stream)
        return stream

    def failing_flock(descriptor: int, operation: int) -> None:
        del descriptor, operation
        raise OSError("flock failed")

    monkeypatch.setattr(lifecycle.os, "open", tracking_open)
    monkeypatch.setattr(lifecycle.os, "fdopen", retaining_fdopen)
    monkeypatch.setattr(lifecycle.fcntl, "flock", failing_flock)

    with pytest.raises(OSError, match="flock failed"):
        acquire_daemon_ownership(
            project,
            start_nonce="owner-nonce",
            health_probe=lambda metadata: metadata,
        )

    assert captured
    for descriptor in captured:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("wait_timeout_seconds", True),
        ("wait_timeout_seconds", "1"),
        ("wait_timeout_seconds", math.nan),
        ("wait_timeout_seconds", math.inf),
        ("wait_timeout_seconds", -math.inf),
        ("wait_timeout_seconds", 0),
        ("wait_timeout_seconds", -1),
        ("wait_timeout_seconds", 0.5),
        ("wait_timeout_seconds", 301),
        ("poll_interval_seconds", False),
        ("poll_interval_seconds", "0.1"),
        ("poll_interval_seconds", math.nan),
        ("poll_interval_seconds", math.inf),
        ("poll_interval_seconds", -math.inf),
        ("poll_interval_seconds", 0),
        ("poll_interval_seconds", -0.1),
    ],
)
def test_ownership_wait_bounds_reject_invalid_values_before_endpoint_write(
    tmp_path: Path, field: str, value: object
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    arguments: dict[str, object] = {
        "wait_timeout_seconds": 1,
        "poll_interval_seconds": 0.01,
    }
    arguments[field] = value

    with pytest.raises((TypeError, ValueError), match=field):
        acquire_daemon_ownership(
            project,
            start_nonce="owner-nonce",
            health_probe=lambda metadata: metadata,
            **arguments,
        )

    assert not (project / ".agentdeck").exists()


def test_poll_interval_cannot_exceed_bounded_wait_without_endpoint_write(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    with pytest.raises(ValueError, match="poll_interval_seconds"):
        acquire_daemon_ownership(
            project,
            start_nonce="owner-nonce",
            health_probe=lambda metadata: metadata,
            wait_timeout_seconds=1,
            poll_interval_seconds=2,
        )

    assert not (project / ".agentdeck").exists()


@pytest.mark.parametrize(
    ("view", "expected"),
    [
        ({}, ()),
        ({"client_count": 2}, ("clients_connected",)),
        ({"active_mission_count": 1}, ("active_mission",)),
        ({"active_worker_count": 1}, ("active_worker",)),
        ({"pending_approval_count": 1}, ("pending_approval",)),
        ({"pending_permission_count": 1}, ("pending_permission",)),
        ({"pending_reply_count": 1}, ("pending_reply",)),
        ({"pending_recovery_decision_count": 1}, ("pending_recovery",)),
        ({"pending_decision_count": 1}, ("pending_decision",)),
        ({"ambiguous_decision_count": 1}, ("pending_ambiguity",)),
        ({"outbox_count": 1}, ("outbox_pending",)),
        ({"recovery_active": True}, ("recovery_active",)),
        ({"safe_shutdown_active": True}, ("safe_shutdown_active",)),
        ({"atomic_write_active": True}, ("atomic_write_active",)),
    ],
)
def test_keepalive_reasons_cover_every_task_driven_lifetime_fact(
    view: Mapping[str, object], expected: tuple[str, ...]
) -> None:
    assert daemon_keepalive_reasons(view) == expected
    assert can_stop_daemon(view) is (not expected)


def test_keepalive_reasons_are_stable_deduplicated_and_fail_closed() -> None:
    view = {
        "client_count": 1,
        "active_mission_count": 2,
        "active_worker_count": 3,
        "pending_approval_count": 1,
        "pending_permission_count": 1,
        "pending_reply_count": 1,
        "pending_recovery_decision_count": 1,
        "pending_decision_count": 1,
        "ambiguous_decision_count": 1,
        "outbox_count": 1,
        "recovery_active": True,
        "safe_shutdown_active": True,
        "atomic_write_active": True,
    }
    assert daemon_keepalive_reasons(view) == (
        "clients_connected",
        "active_mission",
        "active_worker",
        "pending_approval",
        "pending_permission",
        "pending_reply",
        "pending_recovery",
        "pending_decision",
        "pending_ambiguity",
        "outbox_pending",
        "recovery_active",
        "safe_shutdown_active",
        "atomic_write_active",
    )
    with pytest.raises(ValueError, match="client_count"):
        daemon_keepalive_reasons({"client_count": -1})
    with pytest.raises(TypeError, match="recovery_active"):
        daemon_keepalive_reasons({"recovery_active": 1})
