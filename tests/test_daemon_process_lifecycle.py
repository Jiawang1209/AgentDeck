from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Mapping

import pytest

from agentdeck.daemon.lifecycle import (
    DaemonEndpoint,
    DaemonIdentityError,
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
        results.put(("error", "", f"{type(exc).__name__}: {exc}"))


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
    for process in processes:
        process.start()
    output = [results.get(timeout=5) for _ in processes]
    for process in processes:
        process.join(timeout=5)
        assert process.exitcode == 0
    return output


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
                wait_timeout_seconds=0.05,
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
