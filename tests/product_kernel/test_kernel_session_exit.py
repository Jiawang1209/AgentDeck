from dataclasses import FrozenInstanceError
from hashlib import sha256

import pytest

import agentdeck.kernel.session as session_module
from agentdeck.kernel.execution import AttemptState
from agentdeck.kernel.session import ExitAttemptSnapshot, ExitRequest


def _snapshot(**changes: object) -> ExitAttemptSnapshot:
    values: dict[str, object] = {
        "attempt_id": "att_1",
        "task_id": "tsk_1",
        "agent_instance_id": "agt_1",
        "ordinal": 1,
        "state": AttemptState.RUNNING,
        "acp_session_id": "acp_1",
        "effect_observed": False,
        "durable_fingerprint": "a" * 64,
    }
    values.update(changes)
    return ExitAttemptSnapshot(**values)  # type: ignore[arg-type]


def test_exit_attempt_snapshot_has_exact_canonical_shape_and_hash() -> None:
    snapshot = _snapshot()

    assert snapshot.canonical_facts() == {
        "attempt_id": "att_1",
        "task_id": "tsk_1",
        "agent_instance_id": "agt_1",
        "ordinal": 1,
        "state": "running",
        "acp_session_id": "acp_1",
        "effect_observed": False,
        "durable_fingerprint": "a" * 64,
    }
    assert snapshot.canonical_bytes() == (
        b'{"acp_session_id":"acp_1","agent_instance_id":"agt_1",'
        b'"attempt_id":"att_1","durable_fingerprint":"'
        + b"a" * 64
        + b'","effect_observed":false,"ordinal":1,"state":"running",'
        b'"task_id":"tsk_1"}'
    )
    assert snapshot.content_hash == sha256(snapshot.canonical_bytes()).hexdigest()


def test_exit_attempt_snapshot_is_frozen_and_returns_detached_facts() -> None:
    snapshot = _snapshot()
    facts = snapshot.canonical_facts()
    facts["state"] = "completed"

    assert snapshot.canonical_facts()["state"] == "running"
    with pytest.raises(FrozenInstanceError):
        snapshot.ordinal = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    "changes",
    (
        {"attempt_id": "bad"},
        {"attempt_id": "att_"},
        {"attempt_id": "att_" + "x" * 252},
        {"task_id": "tsk_\ud800"},
        {"agent_instance_id": "bad"},
        {"ordinal": 0},
        {"ordinal": 2**63},
        {"ordinal": True},
        {"state": AttemptState.COMPLETED},
        {"state": "running"},
        {"acp_session_id": " "},
        {"acp_session_id": "x" * 256},
        {"effect_observed": 1},
        {"durable_fingerprint": "A" * 64},
        {"durable_fingerprint": "a" * 63},
    ),
)
def test_snapshot_rejects_unknown_state_unbounded_or_mutable_facts(
    changes: dict[str, object],
) -> None:
    with pytest.raises((TypeError, ValueError)):
        _snapshot(**changes)


def test_snapshot_accepts_nullable_bounded_attempt_facts() -> None:
    snapshot = _snapshot(
        agent_instance_id=None,
        acp_session_id=None,
        durable_fingerprint=None,
        state=AttemptState.HUMAN_CONTROLLED,
        ordinal=2**63 - 1,
    )

    assert snapshot.canonical_facts()["agent_instance_id"] is None
    assert snapshot.canonical_facts()["durable_fingerprint"] is None
    assert len(snapshot.canonical_bytes()) <= 4_096


def test_exit_request_binds_exact_snapshot_hash_and_normalized_time() -> None:
    snapshot = _snapshot()
    request = ExitRequest(
        request_id="xrt_" + "1" * 32,
        attempt=snapshot,
        attempt_hash=snapshot.content_hash,
        requested_at="2026-07-19T11:00:00+08:00",
    )

    assert request.attempt is snapshot
    assert request.attempt_hash == snapshot.content_hash
    assert request.requested_at == "2026-07-19T03:00:00+00:00"
    with pytest.raises(FrozenInstanceError):
        request.request_id = "xrt_" + "2" * 32  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("request_id", "xrt_" + "A" * 32),
        ("request_id", "xrt_" + "1" * 31),
        ("request_id", "exit_" + "1" * 32),
        ("attempt_hash", "A" * 64),
        ("attempt_hash", "f" * 64),
        ("requested_at", "2026-07-19T03:00:00"),
        ("requested_at", "2026-07-19T03:00:00." + "0" * 80 + "+00:00"),
    ),
)
def test_exit_request_rejects_invalid_identity_hash_or_time(
    field: str, value: str,
) -> None:
    snapshot = _snapshot()
    values = {
        "request_id": "xrt_" + "1" * 32,
        "attempt": snapshot,
        "attempt_hash": snapshot.content_hash,
        "requested_at": "2026-07-19T03:00:00+00:00",
    }
    values[field] = value
    with pytest.raises((TypeError, ValueError)):
        ExitRequest(**values)  # type: ignore[arg-type]


def test_exit_request_compares_the_exact_hash_in_constant_time(monkeypatch) -> None:
    snapshot = _snapshot()
    compared: list[tuple[str, str]] = []

    def compare(left: str, right: str) -> bool:
        compared.append((left, right))
        return True

    monkeypatch.setattr(session_module, "compare_digest", compare)
    ExitRequest(
        "xrt_" + "1" * 32,
        snapshot,
        snapshot.content_hash,
        "2026-07-19T03:00:00+00:00",
    )

    assert compared == [(snapshot.content_hash, snapshot.content_hash)]
