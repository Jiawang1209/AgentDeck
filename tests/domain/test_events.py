from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from agentdeck.domain.events import MAX_EVENT_BYTES, MAX_JSON_DEPTH, DomainEvent


HASH = "sha256:" + ("a" * 64)


def client_event(**overrides: object) -> DomainEvent:
    values: dict[str, object] = {
        "event_id": "evt_client_1",
        "kind": "mission_requested",
        "command_id": "cmd_1",
        "expected_revision": 0,
        "actor": {"actor_id": "human_1", "kind": "human"},
        "payload": {"mission": {"title": "实现 durable kernel"}},
        "created_at": "2026-07-18T00:00:00Z",
    }
    values.update(overrides)
    return DomainEvent.client_command(**values)  # type: ignore[arg-type]


def adapter_event(**overrides: object) -> DomainEvent:
    values: dict[str, object] = {
        "event_id": "evt_adapter_1",
        "kind": "worker_progressed",
        "adapter_event_id": "adapter_evt_1",
        "mission_id": "mis_1",
        "mission_version": "mv_1",
        "task_id": "tsk_1",
        "attempt_id": "att_1",
        "session_id": "ses_1",
        "sequence": 0,
        "integrity_hash": HASH,
        "payload": {"status": "working"},
        "created_at": "2026-07-18T00:00:01Z",
    }
    values.update(overrides)
    return DomainEvent.adapter_event(**values)  # type: ignore[arg-type]


def internal_event(**overrides: object) -> DomainEvent:
    values: dict[str, object] = {
        "event_id": "evt_internal_1",
        "kind": "recovery_requested",
        "internal_trigger_id": "trigger_1",
        "source_revision": 3,
        "source_snapshot_id": "snapshot_1",
        "payload": {"reason": "daemon_restart"},
        "created_at": "2026-07-18T00:00:02Z",
    }
    values.update(overrides)
    return DomainEvent.internal_trigger(**values)  # type: ignore[arg-type]


def test_builds_client_command_event_with_closed_provenance() -> None:
    event = client_event()

    assert event.trigger_kind == "client_command"
    assert event.to_dict()["provenance"] == {
        "actor": {"actor_id": "human_1", "kind": "human"},
        "command_id": "cmd_1",
        "expected_revision": 0,
    }


def test_builds_adapter_event_with_closed_provenance() -> None:
    event = adapter_event()

    assert event.trigger_kind == "adapter_event"
    assert event.to_dict()["provenance"] == {
        "adapter_event_id": "adapter_evt_1",
        "attempt_id": "att_1",
        "integrity_hash": HASH,
        "mission_id": "mis_1",
        "mission_version": "mv_1",
        "sequence": 0,
        "session_id": "ses_1",
        "task_id": "tsk_1",
    }


def test_builds_internal_trigger_event_with_closed_provenance() -> None:
    event = internal_event()

    assert event.trigger_kind == "internal_trigger"
    assert event.to_dict()["provenance"] == {
        "internal_trigger_id": "trigger_1",
        "source_revision": 3,
        "source_snapshot_id": "snapshot_1",
    }


@pytest.mark.parametrize(
    ("build", "unexpected", "message"),
    [
        (
            client_event,
            {"adapter_event_id": "adapter_evt_1"},
            "client command provenance invalid",
        ),
        (adapter_event, {"command_id": "cmd_1"}, "adapter event provenance invalid"),
        (
            internal_event,
            {"attempt_id": "att_1"},
            "internal trigger provenance invalid",
        ),
    ],
)
def test_rejects_cross_trigger_provenance_fields(
    build: object, unexpected: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=f"^{message}$"):
        build(**unexpected)  # type: ignore[operator]


@pytest.mark.parametrize(
    ("build", "overrides", "message"),
    [
        (client_event, {"expected_revision": -1}, "client command provenance invalid"),
        (
            client_event,
            {"expected_revision": True},
            "client command provenance invalid",
        ),
        (adapter_event, {"sequence": -1}, "adapter event provenance invalid"),
        (adapter_event, {"sequence": False}, "adapter event provenance invalid"),
    ],
)
def test_rejects_invalid_revision_and_sequence(
    build: object, overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=f"^{message}$"):
        build(**overrides)  # type: ignore[operator]


@pytest.mark.parametrize(
    "integrity_hash",
    ["", "sha256:ABC", "sha256:" + ("A" * 64), "sha256:" + ("a" * 63)],
)
def test_rejects_invalid_integrity_hash(integrity_hash: str) -> None:
    with pytest.raises(ValueError, match="^adapter event provenance invalid$"):
        adapter_event(integrity_hash=integrity_hash)


@pytest.mark.parametrize(
    "payload",
    [
        {"value": 1.5},
        {"nested": [{"value": 1.5}]},
        {1: "non-string-key"},
        {"value": b"bytes"},
        {"value": (1, 2)},
    ],
)
def test_rejects_non_canonical_json_payload_values(payload: object) -> None:
    with pytest.raises(ValueError, match="^domain event payload invalid$"):
        client_event(payload=payload)


def test_rejects_cyclic_payload() -> None:
    payload: list[object] = []
    payload.append(payload)

    with pytest.raises(ValueError, match="^domain event payload invalid$"):
        client_event(payload={"cycle": payload})


def test_rejects_payload_beyond_maximum_depth() -> None:
    payload: object = "leaf"
    for _ in range(MAX_JSON_DEPTH + 2):
        payload = [payload]

    with pytest.raises(ValueError, match="^domain event payload invalid$"):
        client_event(payload=payload)


def test_rejects_oversize_payload() -> None:
    with pytest.raises(ValueError, match="^domain event payload invalid$"):
        client_event(payload={"value": "x" * MAX_EVENT_BYTES})


def test_rejects_invalid_actor_json_as_client_provenance() -> None:
    with pytest.raises(ValueError, match="^client command provenance invalid$"):
        client_event(actor={"weight": 1.5})


def test_constructor_inputs_are_deeply_detached_and_frozen() -> None:
    actor = {"actor_id": "human_1", "scopes": ["mission"]}
    payload = {"nested": {"items": [1, "two"]}}

    event = client_event(actor=actor, payload=payload)
    actor["scopes"].append("admin")  # type: ignore[union-attr]
    payload["nested"]["items"].append(3)  # type: ignore[index,union-attr]

    copied_actor = event.to_dict()["provenance"]["actor"]  # type: ignore[index]
    assert copied_actor["scopes"] == ["mission"]  # type: ignore[index]
    assert event.to_dict()["payload"] == {"nested": {"items": [1, "two"]}}
    with pytest.raises(TypeError):
        event.payload["new"] = True  # type: ignore[index]
    with pytest.raises(TypeError):
        event.provenance["actor"]["scopes"][0] = "changed"  # type: ignore[index]


def test_to_dict_returns_a_fresh_mutable_deep_copy() -> None:
    event = client_event()

    first = event.to_dict()
    first["payload"]["mission"]["title"] = "changed"  # type: ignore[index]
    first["provenance"]["actor"]["actor_id"] = "changed"  # type: ignore[index]

    second = event.to_dict()
    copied_mission = second["payload"]["mission"]  # type: ignore[index]
    assert copied_mission["title"] == "实现 durable kernel"  # type: ignore[index]
    assert second["provenance"]["actor"]["actor_id"] == "human_1"  # type: ignore[index]


def test_canonical_bytes_are_stable_utf8_sorted_and_compact() -> None:
    event = client_event(payload={"中文": "值", "a": [1, True, None]})

    first = event.canonical_bytes()
    second = event.canonical_bytes()

    assert first == second
    assert first == json.dumps(
        event.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert "中文" in first.decode("utf-8")


def test_domain_event_dataclass_fields_cannot_be_reassigned() -> None:
    event = internal_event()

    with pytest.raises(FrozenInstanceError):
        event.kind = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"event_id": ""}, "domain event metadata invalid"),
        ({"kind": ""}, "domain event metadata invalid"),
        ({"created_at": ""}, "domain event metadata invalid"),
        ({"event_id": "bad\ud800"}, "domain event metadata invalid"),
    ],
)
def test_rejects_empty_or_non_utf8_metadata(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=f"^{message}$"):
        client_event(**overrides)
