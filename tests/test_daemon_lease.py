from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path

import pytest

from agentdeck.config import write_default_config
from agentdeck.daemon.lease import (
    LeaseAuditEvent,
    LeaseError,
    LeaseTransition,
    confirm_takeover,
    expire_controller,
    grant_controller,
    preview_takeover,
    register_observer,
    release_controller,
    renew_controller,
    validate_controller,
)
from agentdeck.models import EventRecord
from agentdeck.state import StateStore


NOW = datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc)


def _store(tmp_path: Path) -> StateStore:
    write_default_config(tmp_path)
    return StateStore(tmp_path)


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_observer_registration_is_frozen_and_has_no_controller_authority() -> None:
    observer = register_observer(client_id="client-observer", now=NOW)

    assert observer.client_id == "client-observer"
    assert observer.registered_at == "2026-07-13T10:00:00+00:00"
    assert observer.can_mutate is False
    with pytest.raises(FrozenInstanceError):
        observer.client_id = "client-controller"  # type: ignore[misc]


def test_first_controller_grant_is_frozen_and_compact() -> None:
    transition = grant_controller(client_id="client-a", now=NOW, ttl_seconds=30)
    lease = transition.current

    assert lease is not None
    assert lease.client_id == "client-a"
    assert lease.generation == 1
    assert lease.issued_at == "2026-07-13T10:00:00+00:00"
    assert lease.last_renewed_at == lease.issued_at
    assert lease.expires_at == "2026-07-13T10:00:30+00:00"
    assert set(lease.summary()) == {
        "lease_id",
        "client_id",
        "issued_at",
        "expires_at",
        "last_renewed_at",
        "generation",
    }
    with pytest.raises(FrozenInstanceError):
        lease.client_id = "client-b"  # type: ignore[misc]


def test_only_current_generation_and_lease_id_can_mutate() -> None:
    lease = grant_controller(client_id="client-a", now=NOW, ttl_seconds=30).current
    assert lease is not None

    assert validate_controller(
        lease, lease_id=lease.lease_id, generation=lease.generation, now=NOW
    )
    with pytest.raises(LeaseError, match="stale controller lease"):
        validate_controller(
            lease,
            lease_id=lease.lease_id,
            generation=lease.generation - 1,
            now=NOW,
        )
    with pytest.raises(LeaseError, match="stale controller lease"):
        validate_controller(
            lease, lease_id="lse_wrong", generation=lease.generation, now=NOW
        )


def test_renew_extends_current_lease_without_changing_generation() -> None:
    granted = grant_controller(client_id="client-a", now=NOW, ttl_seconds=30)
    current = granted.current
    assert current is not None

    renewed = renew_controller(
        current,
        lease_id=current.lease_id,
        generation=current.generation,
        now=NOW + timedelta(seconds=10),
        ttl_seconds=40,
    )

    assert renewed.current is not None
    assert renewed.current.lease_id == current.lease_id
    assert renewed.current.generation == current.generation
    assert renewed.current.last_renewed_at == "2026-07-13T10:00:10+00:00"
    assert renewed.current.expires_at == "2026-07-13T10:00:50+00:00"


def test_expiry_and_release_revoke_mutation_but_preserve_generation() -> None:
    current = grant_controller(client_id="client-a", now=NOW, ttl_seconds=30).current
    assert current is not None

    expired = expire_controller(current, now=NOW + timedelta(seconds=30))
    assert expired.current == current
    with pytest.raises(LeaseError, match="controller lease expired"):
        validate_controller(
            expired.current,
            lease_id=current.lease_id,
            generation=current.generation,
            now=NOW + timedelta(seconds=30),
        )

    released = release_controller(
        current,
        lease_id=current.lease_id,
        generation=current.generation,
        now=NOW + timedelta(seconds=5),
    )
    assert released.current is not None
    assert released.current.generation == current.generation
    assert released.current.expires_at == "2026-07-13T10:00:05+00:00"
    with pytest.raises(LeaseError, match="controller lease expired"):
        validate_controller(
            released.current,
            lease_id=current.lease_id,
            generation=current.generation,
            now=NOW + timedelta(seconds=5),
        )


def test_new_grant_after_release_increments_generation() -> None:
    first = grant_controller(client_id="client-a", now=NOW, ttl_seconds=30).current
    assert first is not None
    released = release_controller(
        first,
        lease_id=first.lease_id,
        generation=first.generation,
        now=NOW + timedelta(seconds=5),
    ).current
    assert released is not None

    second = grant_controller(
        client_id="client-b",
        now=NOW + timedelta(seconds=6),
        ttl_seconds=30,
        previous=released,
    ).current

    assert second is not None
    assert second.generation == 2
    assert second.lease_id != first.lease_id


def test_takeover_requires_exact_preview_and_increments_generation() -> None:
    current = grant_controller(client_id="client-a", now=NOW, ttl_seconds=30).current
    assert current is not None
    preview = preview_takeover(current, requester="client-b", now=NOW)

    taken = confirm_takeover(
        current,
        preview,
        requester="client-b",
        now=NOW,
        ttl_seconds=30,
    )

    assert taken.current is not None
    assert taken.current.client_id == "client-b"
    assert taken.current.generation == current.generation + 1
    assert taken.current.lease_id != current.lease_id


def test_takeover_rejects_changed_requester_stale_lease_and_bad_digest() -> None:
    current = grant_controller(client_id="client-a", now=NOW, ttl_seconds=30).current
    assert current is not None
    preview = preview_takeover(current, requester="client-b", now=NOW)

    for requester, confirmation, lease in (
        ("client-c", preview, current),
        ("client-b", replace(preview, digest="0" * 64), current),
        (
            "client-b",
            replace(preview, previewed_at="2026-07-13T10:00:01+00:00"),
            current,
        ),
        ("client-b", preview, replace(current, generation=2)),
    ):
        with pytest.raises(LeaseError, match="takeover confirmation mismatch"):
            confirm_takeover(
                lease,
                confirmation,
                requester=requester,
                now=NOW,
                ttl_seconds=30,
            )


def test_takeover_digest_binds_the_exact_preview_timestamp() -> None:
    current = grant_controller(client_id="client-a", now=NOW, ttl_seconds=30).current
    assert current is not None
    preview = preview_takeover(
        current, requester="client-b", now=NOW + timedelta(seconds=1)
    )
    changed = replace(
        preview, previewed_at="2026-07-13T10:00:02+00:00"
    )

    with pytest.raises(LeaseError, match="takeover confirmation mismatch"):
        confirm_takeover(
            current,
            changed,
            requester="client-b",
            now=NOW + timedelta(seconds=3),
            ttl_seconds=30,
        )


@pytest.mark.parametrize(
    "bad_now",
    [
        datetime(2026, 7, 13, 10, 0),
        "2026-07-13T10:00:00+00:00",
    ],
)
def test_time_must_be_timezone_aware_datetime(bad_now: object) -> None:
    with pytest.raises((TypeError, LeaseError), match="time"):
        grant_controller(client_id="client-a", now=bad_now, ttl_seconds=30)  # type: ignore[arg-type]


@pytest.mark.parametrize("ttl", [True, False, 0, -1, math.inf, math.nan, "30"])
def test_ttl_rejects_bool_nonfinite_nonpositive_and_nonnumber(ttl: object) -> None:
    with pytest.raises((TypeError, LeaseError), match="TTL"):
        grant_controller(client_id="client-a", now=NOW, ttl_seconds=ttl)  # type: ignore[arg-type]


def test_unrepresentable_ttl_is_rejected_with_sanitized_error() -> None:
    with pytest.raises(LeaseError, match="TTL"):
        grant_controller(client_id="client-a", now=NOW, ttl_seconds=1e300)

    with pytest.raises(LeaseError, match="TTL"):
        grant_controller(client_id="client-a", now=NOW, ttl_seconds=10**10000)


def test_renew_release_and_takeover_reject_backward_time() -> None:
    current = grant_controller(client_id="client-a", now=NOW, ttl_seconds=30).current
    assert current is not None
    earlier = NOW - timedelta(microseconds=1)

    with pytest.raises(LeaseError, match="backward time"):
        renew_controller(
            current,
            lease_id=current.lease_id,
            generation=current.generation,
            now=earlier,
            ttl_seconds=30,
        )
    with pytest.raises(LeaseError, match="backward time"):
        release_controller(
            current,
            lease_id=current.lease_id,
            generation=current.generation,
            now=earlier,
        )
    with pytest.raises(LeaseError, match="backward time"):
        preview_takeover(current, requester="client-b", now=earlier)


def test_errors_are_sanitized_and_never_echo_untrusted_identifiers() -> None:
    secret = "secret-client-terminal-transcript"
    with pytest.raises(LeaseError) as captured:
        grant_controller(client_id=secret + "\n", now=NOW, ttl_seconds=30)

    assert secret not in str(captured.value)

    lease = grant_controller(client_id="client-a", now=NOW, ttl_seconds=30).current
    assert lease is not None
    with pytest.raises(LeaseError) as stale:
        validate_controller(
            lease,
            lease_id="雪" + secret,
            generation=lease.generation,
            now=NOW,
        )
    assert secret not in str(stale.value)


def test_state_store_atomically_commits_compact_lease_and_audit_event(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    transition = grant_controller(client_id="client-a", now=NOW, ttl_seconds=30)

    result = store.commit_controller_lease(transition)

    state = store.load()
    assert result == transition.current.summary()  # type: ignore[union-attr]
    assert state["controller_lease"] == transition.current.summary()  # type: ignore[union-attr]
    assert len(state["daemon_event_outbox"]) == 1
    event = state["daemon_event_outbox"][0]
    assert set(event) == {"event_id", "event_type", "created_at", "payload"}
    assert event["event_type"] == "controller_lease_granted"
    assert event["payload"] == {
        "action": "grant",
        "client_id": "client-a",
        "generation": 1,
        "lease_id": transition.current.lease_id,  # type: ignore[union-attr]
    }
    assert "terminal" not in repr(state["controller_lease"])


@pytest.mark.parametrize("action", ["release", "takeover"])
def test_state_store_rejects_publicly_constructed_privileged_transition(
    tmp_path: Path, action: str
) -> None:
    store = _store(tmp_path)
    granted = grant_controller(client_id="client-a", now=NOW, ttl_seconds=30)
    store.commit_controller_lease(granted)
    assert granted.current is not None
    if action == "release":
        built = release_controller(
            granted.current,
            lease_id=granted.current.lease_id,
            generation=granted.current.generation,
            now=NOW + timedelta(seconds=1),
        )
    else:
        preview = preview_takeover(
            granted.current, requester="client-b", now=NOW + timedelta(seconds=1)
        )
        built = confirm_takeover(
            granted.current,
            preview,
            requester="client-b",
            now=NOW + timedelta(seconds=1),
            ttl_seconds=30,
        )
    manual = LeaseTransition(
        action=built.action,
        previous=built.previous,
        current=built.current,
        audit_event=built.audit_event,
    )
    before = _snapshot(tmp_path)

    with pytest.raises(LeaseError, match="transition"):
        store.commit_controller_lease(manual)

    assert _snapshot(tmp_path) == before


def test_public_audit_event_detaches_and_freezes_caller_payload() -> None:
    payload = {
        "action": "release",
        "client_id": "client-a",
        "generation": 1,
        "lease_id": "lse_" + "a" * 24,
    }
    event = LeaseAuditEvent(
        event_id="evt_" + "b" * 24,
        event_type="controller_lease_released",
        created_at="2026-07-13T10:00:01+00:00",
        payload=payload,
    )

    payload["client_id"] = "client-b"
    assert event.payload["client_id"] == "client-a"
    with pytest.raises(TypeError):
        event.payload["client_id"] = "client-c"  # type: ignore[index]


def test_state_store_validates_persisted_lease_inside_lock_and_zero_writes_stale(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    first = grant_controller(client_id="client-a", now=NOW, ttl_seconds=30)
    store.commit_controller_lease(first)
    assert first.current is not None
    stale_renewal = renew_controller(
        first.current,
        lease_id=first.current.lease_id,
        generation=first.current.generation,
        now=NOW + timedelta(seconds=1),
        ttl_seconds=30,
    )
    current_renewal = renew_controller(
        first.current,
        lease_id=first.current.lease_id,
        generation=first.current.generation,
        now=NOW + timedelta(seconds=2),
        ttl_seconds=30,
    )
    store.commit_controller_lease(current_renewal)
    before = _snapshot(tmp_path)

    with pytest.raises(LeaseError, match="stale controller lease"):
        store.commit_controller_lease(stale_renewal)

    assert _snapshot(tmp_path) == before


def test_invalid_transition_is_full_tree_zero_write(tmp_path: Path) -> None:
    store = _store(tmp_path)
    transition = grant_controller(client_id="client-a", now=NOW, ttl_seconds=30)
    invalid = replace(
        transition,
        current=replace(transition.current, generation=True),  # type: ignore[arg-type]
    )
    before = _snapshot(tmp_path)

    with pytest.raises((TypeError, LeaseError), match="transition|generation"):
        store.commit_controller_lease(invalid)

    assert _snapshot(tmp_path) == before


def test_forged_first_grant_timestamp_mismatch_is_full_tree_zero_write(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    valid = grant_controller(client_id="client-a", now=NOW, ttl_seconds=30)
    assert valid.current is not None
    forged = replace(
        valid,
        current=replace(
            valid.current,
            issued_at="2026-07-13T10:00:01+00:00",
            last_renewed_at="2026-07-13T10:00:01+00:00",
            expires_at="2026-07-13T10:00:31+00:00",
        ),
    )
    before = _snapshot(tmp_path)

    with pytest.raises(LeaseError, match="transition"):
        store.commit_controller_lease(forged)

    assert _snapshot(tmp_path) == before


def test_forged_renewal_cannot_revive_expired_persisted_lease(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    granted = grant_controller(client_id="client-a", now=NOW, ttl_seconds=30)
    store.commit_controller_lease(granted)
    assert granted.current is not None
    valid = renew_controller(
        granted.current,
        lease_id=granted.current.lease_id,
        generation=granted.current.generation,
        now=NOW + timedelta(seconds=10),
        ttl_seconds=30,
    )
    assert valid.current is not None
    forged_at = "2026-07-13T10:00:31+00:00"
    forged = replace(
        valid,
        current=replace(
            valid.current,
            last_renewed_at=forged_at,
            expires_at="2026-07-13T10:01:01+00:00",
        ),
        audit_event=replace(valid.audit_event, created_at=forged_at),
    )
    before = _snapshot(tmp_path)

    with pytest.raises(LeaseError, match="transition"):
        store.commit_controller_lease(forged)

    assert _snapshot(tmp_path) == before


def test_forged_release_cannot_release_an_already_expired_persisted_lease(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    granted = grant_controller(client_id="client-a", now=NOW, ttl_seconds=30)
    store.commit_controller_lease(granted)
    assert granted.current is not None
    valid = release_controller(
        granted.current,
        lease_id=granted.current.lease_id,
        generation=granted.current.generation,
        now=NOW + timedelta(seconds=5),
    )
    assert valid.current is not None
    forged_at = "2026-07-13T10:00:30+00:00"
    forged = replace(
        valid,
        current=replace(valid.current, expires_at=forged_at),
        audit_event=replace(valid.audit_event, created_at=forged_at),
    )
    before = _snapshot(tmp_path)

    with pytest.raises(LeaseError, match="transition"):
        store.commit_controller_lease(forged)

    assert _snapshot(tmp_path) == before


def test_forged_backward_renewal_is_rejected_without_writing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = grant_controller(client_id="client-a", now=NOW, ttl_seconds=30)
    store.commit_controller_lease(first)
    assert first.current is not None
    valid = renew_controller(
        first.current,
        lease_id=first.current.lease_id,
        generation=first.current.generation,
        now=NOW + timedelta(seconds=10),
        ttl_seconds=30,
    )
    store.commit_controller_lease(valid)
    assert valid.current is not None
    forged_current = replace(
        valid.current,
        last_renewed_at="2026-07-13T10:00:05+00:00",
        expires_at="2026-07-13T10:00:35+00:00",
    )
    forged_event = replace(
        valid.audit_event,
        event_id="evt_" + "a" * 24,
        created_at="2026-07-13T10:00:05+00:00",
    )
    forged = replace(
        valid,
        previous=valid.current,
        current=forged_current,
        audit_event=forged_event,
    )
    before = _snapshot(tmp_path)

    with pytest.raises(LeaseError, match="transition"):
        store.commit_controller_lease(forged)

    assert _snapshot(tmp_path) == before


def test_legacy_state_gets_additive_daemon_lease_fields_on_first_commit(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    legacy = store.load()
    legacy.pop("controller_lease", None)
    legacy.pop("daemon_event_outbox", None)
    store.save(legacy)

    transition = grant_controller(client_id="client-a", now=NOW, ttl_seconds=30)
    store.commit_controller_lease(transition)

    state = store.load()
    assert state["controller_lease"]["generation"] == 1
    assert len(state["daemon_event_outbox"]) == 1


def test_state_store_rejects_malformed_existing_daemon_outbox_without_writing(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    state = store.load()
    state["daemon_event_outbox"] = {"not": "a-list"}
    store.save(state)
    before = _snapshot(tmp_path)

    with pytest.raises(TypeError, match="daemon_event_outbox"):
        store.commit_controller_lease(
            grant_controller(client_id="client-a", now=NOW, ttl_seconds=30)
        )

    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "bad_item",
    [
        {"event_id": "evt_bad"},
        {
            "event_id": "evt_" + "a" * 24,
            "event_type": "controller_lease_granted",
            "created_at": "not-a-time",
            "payload": {},
            "extra": True,
        },
        {
            "event_id": 7,
            "event_type": "controller_lease_granted",
            "created_at": "2026-07-13T10:00:00+00:00",
            "payload": {},
        },
        {
            "event_id": "evt_" + "c" * 24,
            "event_type": "controller_lease_granted",
            "created_at": "2026-07-13T10:00:00+00:00",
            "payload": [],
        },
    ],
)
def test_state_store_rejects_malformed_daemon_outbox_items_without_writing(
    tmp_path: Path, bad_item: dict[str, object]
) -> None:
    store = _store(tmp_path)
    state = store.load()
    state["daemon_event_outbox"] = [bad_item]
    store.save(state)
    before = _snapshot(tmp_path)

    with pytest.raises((TypeError, LeaseError, ValueError), match="daemon.*outbox"):
        store.commit_controller_lease(
            grant_controller(client_id="client-a", now=NOW, ttl_seconds=30)
        )

    assert _snapshot(tmp_path) == before


def test_state_store_rejects_duplicate_daemon_outbox_event_ids_without_writing(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    transition = grant_controller(client_id="client-a", now=NOW, ttl_seconds=30)
    item = transition.audit_event.summary()
    state = store.load()
    state["daemon_event_outbox"] = [item, item]
    store.save(state)
    before = _snapshot(tmp_path)

    with pytest.raises(ValueError, match="duplicate daemon event identity"):
        store.commit_controller_lease(transition)

    assert _snapshot(tmp_path) == before


def test_repeated_expiry_is_zero_write_while_audit_remains_in_outbox(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    granted = grant_controller(client_id="client-a", now=NOW, ttl_seconds=30)
    store.commit_controller_lease(granted)
    assert granted.current is not None
    first = expire_controller(granted.current, now=NOW + timedelta(seconds=30))
    store.commit_controller_lease(first)
    repeated = expire_controller(granted.current, now=NOW + timedelta(seconds=31))
    before = _snapshot(tmp_path)

    with pytest.raises(ValueError, match="duplicate daemon event identity"):
        store.commit_controller_lease(repeated)

    assert _snapshot(tmp_path) == before


def test_repeated_expiry_is_zero_write_after_outbox_flush_and_restart(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    granted = grant_controller(client_id="client-a", now=NOW, ttl_seconds=30)
    store.commit_controller_lease(granted)
    assert granted.current is not None
    first = expire_controller(granted.current, now=NOW + timedelta(seconds=30))
    store.commit_controller_lease(first)
    state = store.load()
    expiry_item = state["daemon_event_outbox"][-1]
    store.append_event(EventRecord(**expiry_item))
    state["daemon_event_outbox"] = []
    store.save(state)
    restarted = StateStore.open_existing(tmp_path)
    repeated = expire_controller(granted.current, now=NOW + timedelta(seconds=31))
    before = _snapshot(tmp_path)

    with pytest.raises(ValueError, match="duplicate daemon event identity"):
        restarted.commit_controller_lease(repeated)

    assert _snapshot(tmp_path) == before


def test_corrupt_event_journal_fails_closed_without_lease_write(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.events_path.write_text("{not-json}\n", encoding="utf-8")
    transition = grant_controller(client_id="client-a", now=NOW, ttl_seconds=30)
    before = _snapshot(tmp_path)

    with pytest.raises(ValueError, match="daemon event journal"):
        store.commit_controller_lease(transition)

    assert _snapshot(tmp_path) == before


def test_state_store_rejects_malformed_persisted_lease_with_sanitized_error(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    state = store.load()
    state["controller_lease"] = {
        "lease_id": 7,
        "client_id": "client-a",
        "issued_at": "2026-07-13T10:00:00+00:00",
        "expires_at": "2026-07-13T10:00:30+00:00",
        "last_renewed_at": "2026-07-13T10:00:00+00:00",
        "generation": 1,
    }
    store.save(state)
    before = _snapshot(tmp_path)

    with pytest.raises(LeaseError, match="lease_id"):
        store.commit_controller_lease(
            grant_controller(client_id="client-a", now=NOW, ttl_seconds=30)
        )

    assert _snapshot(tmp_path) == before
