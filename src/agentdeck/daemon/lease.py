from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import math
import re
import threading
from types import MappingProxyType
from typing import Mapping
import uuid
import weakref


_CLIENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_LEASE_ID = re.compile(r"lse_[0-9a-f]{24}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_LEASE_FIELDS = frozenset(
    {
        "lease_id",
        "client_id",
        "issued_at",
        "expires_at",
        "last_renewed_at",
        "generation",
    }
)
_EVENT_FIELDS = frozenset({"event_id", "event_type", "created_at", "payload"})
_EVENT_ID = re.compile(r"evt_[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_EVENT_TYPE = re.compile(r"[a-z][a-z0-9_]{0,127}")
_TRANSITION_FACTORY_TOKEN = object()
_AUDIT_FACTORY_TOKEN = object()
_AUTHORIZATION_LOCK = threading.RLock()
_AUTHORIZED_TRANSITIONS: dict[
    int, tuple[weakref.ReferenceType[LeaseTransition], str, str]
] = {}


class LeaseError(RuntimeError):
    """A sanitized controller-lease rejection."""


@dataclass(frozen=True)
class ObserverRegistration:
    client_id: str
    registered_at: str
    can_mutate: bool = False


@dataclass(frozen=True)
class ControllerLease:
    lease_id: str
    client_id: str
    issued_at: str
    expires_at: str
    last_renewed_at: str
    generation: int

    def summary(self) -> dict[str, object]:
        return {
            "lease_id": self.lease_id,
            "client_id": self.client_id,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "last_renewed_at": self.last_renewed_at,
            "generation": self.generation,
        }


@dataclass(frozen=True)
class TakeoverPreview:
    requester: str
    current_lease_id: str
    current_generation: int
    previewed_at: str
    digest: str


@dataclass(frozen=True)
class LeaseAuditEvent:
    event_id: str
    event_type: str
    created_at: str
    payload: Mapping[str, object]
    _factory_capability: object | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not isinstance(self.payload, Mapping):
            raise LeaseError("invalid controller lease audit payload")
        frozen = _freeze_json(dict(self.payload))
        assert isinstance(frozen, Mapping)
        object.__setattr__(self, "payload", frozen)

    def summary(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "created_at": self.created_at,
            "payload": _thaw_json(self.payload),
        }


@dataclass(frozen=True)
class LeaseTransition:
    action: str
    previous: ControllerLease | None
    current: ControllerLease | None
    audit_event: LeaseAuditEvent
    _factory_capability: object | None = field(
        default=None, init=False, repr=False, compare=False
    )


def _freeze_json(value: object, *, depth: int = 0) -> object:
    if depth > 32:
        raise LeaseError("invalid daemon event outbox")
    if value is None or type(value) in {bool, int}:
        return value
    if type(value) is str:
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise LeaseError("invalid daemon event outbox")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise LeaseError("invalid daemon event outbox")
        return value
    if type(value) is list:
        return tuple(_freeze_json(item, depth=depth + 1) for item in value)
    if type(value) is dict:
        if any(
            type(key) is not str
            or any(0xD800 <= ord(character) <= 0xDFFF for character in key)
            for key in value
        ):
            raise LeaseError("invalid daemon event outbox")
        return MappingProxyType(
            {
                key: _freeze_json(item, depth=depth + 1)
                for key, item in value.items()
            }
        )
    raise LeaseError("invalid daemon event outbox")


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_thaw_json(item) for item in value]
    return value


def _client_id(value: object) -> str:
    if type(value) is not str or _CLIENT_ID.fullmatch(value) is None:
        raise LeaseError("invalid client identity")
    return value


def _now(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise LeaseError("time must be a timezone-aware datetime")
    try:
        offset = value.utcoffset()
    except (OverflowError, ValueError):
        raise LeaseError("time must be a timezone-aware datetime") from None
    if offset is None:
        raise LeaseError("time must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_timestamp(value: object, *, field: str) -> datetime:
    if type(value) is not str:
        raise LeaseError(f"invalid controller lease {field}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise LeaseError(f"invalid controller lease {field}") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LeaseError(f"invalid controller lease {field}")
    return parsed.astimezone(timezone.utc)


def _ttl(value: object) -> float:
    if type(value) not in {int, float}:
        raise LeaseError("controller lease TTL must be a positive finite number")
    try:
        ttl = float(value)
    except (OverflowError, ValueError):
        raise LeaseError("controller lease TTL must be a positive finite number") from None
    if not math.isfinite(ttl) or ttl <= 0:
        raise LeaseError("controller lease TTL must be a positive finite number")
    return ttl


def _assert_not_backward(now: datetime, baseline: datetime) -> None:
    if now < baseline:
        raise LeaseError("backward time is not allowed")


def _event(action: str, lease: ControllerLease, now: datetime) -> LeaseAuditEvent:
    audit_action = {
        "granted": "grant",
        "renewed": "renew",
        "expired": "expire",
        "released": "release",
        "taken_over": "takeover",
    }[action]
    event_id = (
        "evt_"
        + hashlib.sha256(
            f"controller-expire:{lease.lease_id}:{lease.generation}".encode("ascii")
        ).hexdigest()[:24]
        if action == "expired"
        else f"evt_{uuid.uuid4().hex[:24]}"
    )
    event = LeaseAuditEvent(
        event_id=event_id,
        event_type=f"controller_lease_{action}",
        created_at=_timestamp(now),
        payload=MappingProxyType(
            {
                "action": audit_action,
                "client_id": lease.client_id,
                "generation": lease.generation,
                "lease_id": lease.lease_id,
            }
        ),
    )
    object.__setattr__(event, "_factory_capability", _AUDIT_FACTORY_TOKEN)
    return event


def _transition(
    action: str,
    previous: ControllerLease | None,
    current: ControllerLease,
    audit_event: LeaseAuditEvent,
    *,
    authorization_facts: Mapping[str, object],
) -> LeaseTransition:
    transition = LeaseTransition(action, previous, current, audit_event)
    object.__setattr__(
        transition, "_factory_capability", _TRANSITION_FACTORY_TOKEN
    )
    authorization_json = json.dumps(
        dict(authorization_facts),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    fingerprint = _transition_fingerprint(transition, authorization_json)
    identity = id(transition)

    def discard(reference: weakref.ReferenceType[LeaseTransition]) -> None:
        with _AUTHORIZATION_LOCK:
            registered = _AUTHORIZED_TRANSITIONS.get(identity)
            if registered is not None and registered[0] is reference:
                _AUTHORIZED_TRANSITIONS.pop(identity, None)

    reference = weakref.ref(transition, discard)
    with _AUTHORIZATION_LOCK:
        _AUTHORIZED_TRANSITIONS[identity] = (
            reference,
            authorization_json,
            fingerprint,
        )
    return transition


def _transition_fingerprint(
    transition: LeaseTransition, authorization_json: str
) -> str:
    encoded = json.dumps(
        {
            "action": transition.action,
            "previous": (
                transition.previous.summary()
                if transition.previous is not None
                else None
            ),
            "current": (
                transition.current.summary()
                if transition.current is not None
                else None
            ),
            "audit_event": transition.audit_event.summary(),
            "authorization": authorization_json,
        },
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _transition_is_authorized(transition: LeaseTransition) -> bool:
    identity = id(transition)
    with _AUTHORIZATION_LOCK:
        registered = _AUTHORIZED_TRANSITIONS.get(identity)
        if registered is None or registered[0]() is not transition:
            return False
        _, authorization_json, expected_fingerprint = registered
        try:
            actual_fingerprint = _transition_fingerprint(
                transition, authorization_json
            )
        except (AttributeError, LeaseError, TypeError, ValueError):
            return False
        return hmac.compare_digest(expected_fingerprint, actual_fingerprint)


def _new_lease(
    *, client_id: str, now: datetime, ttl_seconds: float, generation: int
) -> ControllerLease:
    timestamp = _timestamp(now)
    try:
        expires_at = _timestamp(now + timedelta(seconds=ttl_seconds))
    except (OverflowError, ValueError):
        raise LeaseError("controller lease TTL is not representable") from None
    return ControllerLease(
        lease_id=f"lse_{uuid.uuid4().hex[:24]}",
        client_id=client_id,
        issued_at=timestamp,
        expires_at=expires_at,
        last_renewed_at=timestamp,
        generation=generation,
    )


def register_observer(*, client_id: str, now: datetime) -> ObserverRegistration:
    return ObserverRegistration(
        client_id=_client_id(client_id),
        registered_at=_timestamp(_now(now)),
    )


def grant_controller(
    *,
    client_id: str,
    now: datetime,
    ttl_seconds: int | float,
    previous: ControllerLease | None = None,
) -> LeaseTransition:
    requester = _client_id(client_id)
    current_time = _now(now)
    ttl = _ttl(ttl_seconds)
    if previous is not None:
        validate_controller_lease(previous)
        last_time = _parse_timestamp(previous.last_renewed_at, field="last_renewed_at")
        _assert_not_backward(current_time, last_time)
        if current_time < _parse_timestamp(previous.expires_at, field="expires_at"):
            raise LeaseError("controller lease is already held")
    generation = 1 if previous is None else previous.generation + 1
    lease = _new_lease(
        client_id=requester,
        now=current_time,
        ttl_seconds=ttl,
        generation=generation,
    )
    return _transition(
        "grant",
        previous,
        lease,
        _event("granted", lease, current_time),
        authorization_facts={"client_id": requester, "kind": "grant"},
    )


def validate_controller(
    lease: ControllerLease | None,
    *,
    lease_id: str,
    generation: int,
    now: datetime,
) -> bool:
    if lease is None:
        raise LeaseError("controller lease required")
    validate_controller_lease(lease)
    current_time = _now(now)
    if (
        type(lease_id) is not str
        or _LEASE_ID.fullmatch(lease_id) is None
        or type(generation) is not int
        or not hmac.compare_digest(lease.lease_id, lease_id)
        or lease.generation != generation
    ):
        raise LeaseError("stale controller lease")
    _assert_not_backward(
        current_time, _parse_timestamp(lease.last_renewed_at, field="last_renewed_at")
    )
    if current_time >= _parse_timestamp(lease.expires_at, field="expires_at"):
        raise LeaseError("controller lease expired")
    return True


def renew_controller(
    current: ControllerLease,
    *,
    lease_id: str,
    generation: int,
    now: datetime,
    ttl_seconds: int | float,
) -> LeaseTransition:
    current_time = _now(now)
    validate_controller(
        current, lease_id=lease_id, generation=generation, now=current_time
    )
    ttl = _ttl(ttl_seconds)
    try:
        expires_at = _timestamp(current_time + timedelta(seconds=ttl))
    except (OverflowError, ValueError):
        raise LeaseError("controller lease TTL is not representable") from None
    renewed = replace(
        current, last_renewed_at=_timestamp(current_time), expires_at=expires_at
    )
    return _transition(
        "renew",
        current,
        renewed,
        _event("renewed", renewed, current_time),
        authorization_facts={
            "generation": generation,
            "kind": "renew",
            "lease_id": lease_id,
        },
    )


def expire_controller(current: ControllerLease, *, now: datetime) -> LeaseTransition:
    validate_controller_lease(current)
    current_time = _now(now)
    _assert_not_backward(
        current_time, _parse_timestamp(current.last_renewed_at, field="last_renewed_at")
    )
    if current_time < _parse_timestamp(current.expires_at, field="expires_at"):
        raise LeaseError("controller lease has not expired")
    return _transition(
        "expire",
        current,
        current,
        _event("expired", current, current_time),
        authorization_facts={
            "expired_at": _timestamp(current_time),
            "generation": current.generation,
            "kind": "expire",
            "lease_id": current.lease_id,
        },
    )


def release_controller(
    current: ControllerLease,
    *,
    lease_id: str,
    generation: int,
    now: datetime,
) -> LeaseTransition:
    current_time = _now(now)
    validate_controller(
        current, lease_id=lease_id, generation=generation, now=current_time
    )
    released = replace(current, expires_at=_timestamp(current_time))
    return _transition(
        "release",
        current,
        released,
        _event("released", released, current_time),
        authorization_facts={
            "generation": generation,
            "kind": "release",
            "lease_id": lease_id,
        },
    )


def _takeover_digest(
    current: ControllerLease, requester: str, previewed_at: str
) -> str:
    encoded = json.dumps(
        {
            "current": current.summary(),
            "previewed_at": previewed_at,
            "requester": requester,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def preview_takeover(
    current: ControllerLease, *, requester: str, now: datetime
) -> TakeoverPreview:
    validate_controller_lease(current)
    requester_id = _client_id(requester)
    current_time = _now(now)
    _assert_not_backward(
        current_time, _parse_timestamp(current.last_renewed_at, field="last_renewed_at")
    )
    if requester_id == current.client_id:
        raise LeaseError("takeover requester already controls the lease")
    previewed_at = _timestamp(current_time)
    return TakeoverPreview(
        requester=requester_id,
        current_lease_id=current.lease_id,
        current_generation=current.generation,
        previewed_at=previewed_at,
        digest=_takeover_digest(current, requester_id, previewed_at),
    )


def confirm_takeover(
    current: ControllerLease,
    confirmation: TakeoverPreview,
    *,
    requester: str,
    now: datetime,
    ttl_seconds: int | float,
) -> LeaseTransition:
    validate_controller_lease(current)
    requester_id = _client_id(requester)
    current_time = _now(now)
    _assert_not_backward(
        current_time, _parse_timestamp(current.last_renewed_at, field="last_renewed_at")
    )
    try:
        preview_time = _parse_timestamp(
            confirmation.previewed_at, field="takeover preview time"
        )
        confirmation_matches = (
            isinstance(confirmation, TakeoverPreview)
            and confirmation.requester == requester_id
            and confirmation.current_lease_id == current.lease_id
            and type(confirmation.current_generation) is int
            and confirmation.current_generation == current.generation
            and type(confirmation.digest) is str
            and _SHA256.fullmatch(confirmation.digest) is not None
            and hmac.compare_digest(
                _takeover_digest(
                    current, requester_id, confirmation.previewed_at
                ),
                confirmation.digest,
            )
            and preview_time
            >= _parse_timestamp(current.last_renewed_at, field="last_renewed_at")
            and preview_time <= current_time
            and requester_id != current.client_id
        )
    except (AttributeError, LeaseError):
        confirmation_matches = False
    if not confirmation_matches:
        raise LeaseError("takeover confirmation mismatch")
    ttl = _ttl(ttl_seconds)
    taken = _new_lease(
        client_id=requester_id,
        now=current_time,
        ttl_seconds=ttl,
        generation=current.generation + 1,
    )
    return _transition(
        "takeover",
        current,
        taken,
        _event("taken_over", taken, current_time),
        authorization_facts={
            "confirmed_by": requester_id,
            "kind": "takeover",
            "preview": {
                "current_generation": confirmation.current_generation,
                "current_lease_id": confirmation.current_lease_id,
                "digest": confirmation.digest,
                "previewed_at": confirmation.previewed_at,
                "requester": confirmation.requester,
            },
        },
    )


def validate_controller_lease(lease: ControllerLease) -> ControllerLease:
    if not isinstance(lease, ControllerLease):
        raise LeaseError("invalid controller lease")
    if type(lease.lease_id) is not str or _LEASE_ID.fullmatch(lease.lease_id) is None:
        raise LeaseError("invalid controller lease lease_id")
    _client_id(lease.client_id)
    if type(lease.generation) is not int or lease.generation <= 0:
        raise LeaseError("invalid controller lease generation")
    issued = _parse_timestamp(lease.issued_at, field="issued_at")
    renewed = _parse_timestamp(lease.last_renewed_at, field="last_renewed_at")
    expires = _parse_timestamp(lease.expires_at, field="expires_at")
    if renewed < issued:
        raise LeaseError("invalid controller lease timestamp order")
    if expires < issued:
        raise LeaseError("invalid controller lease timestamp order")
    if expires < renewed:
        raise LeaseError("invalid controller lease timestamp order")
    return lease


def controller_lease_from_summary(value: object) -> ControllerLease | None:
    if value is None:
        return None
    if type(value) is not dict or set(value) != _LEASE_FIELDS:
        raise LeaseError("invalid persisted controller lease")
    lease = ControllerLease(
        lease_id=value["lease_id"],  # type: ignore[arg-type]
        client_id=value["client_id"],  # type: ignore[arg-type]
        issued_at=value["issued_at"],  # type: ignore[arg-type]
        expires_at=value["expires_at"],  # type: ignore[arg-type]
        last_renewed_at=value["last_renewed_at"],  # type: ignore[arg-type]
        generation=value["generation"],  # type: ignore[arg-type]
    )
    return validate_controller_lease(lease)


def validate_lease_transition(
    persisted: object, transition: LeaseTransition
) -> LeaseTransition:
    if (
        not isinstance(transition, LeaseTransition)
        or transition._factory_capability is not _TRANSITION_FACTORY_TOKEN
        or not _transition_is_authorized(transition)
    ):
        raise LeaseError("invalid controller lease transition")
    previous = controller_lease_from_summary(persisted)
    if transition.previous != previous:
        raise LeaseError("stale controller lease")
    current = validate_controller_lease(transition.current) if transition.current else None
    if current is None:
        raise LeaseError("invalid controller lease transition")
    if transition.action not in {"grant", "renew", "expire", "release", "takeover"}:
        raise LeaseError("invalid controller lease transition")
    event = transition.audit_event
    if (
        not isinstance(event, LeaseAuditEvent)
        or event._factory_capability is not _AUDIT_FACTORY_TOKEN
    ):
        raise LeaseError("invalid controller lease audit event")
    event_time = _parse_timestamp(event.created_at, field="audit time")
    expected_event_type = {
        "grant": "controller_lease_granted",
        "renew": "controller_lease_renewed",
        "expire": "controller_lease_expired",
        "release": "controller_lease_released",
        "takeover": "controller_lease_taken_over",
    }[transition.action]
    if event.event_type != expected_event_type or not re.fullmatch(r"evt_[0-9a-f]{24}", event.event_id):
        raise LeaseError("invalid controller lease audit event")
    if dict(event.payload) != {
        "action": transition.action,
        "client_id": current.client_id,
        "generation": current.generation,
        "lease_id": current.lease_id,
    }:
        raise LeaseError("invalid controller lease audit event")

    if previous is None:
        valid_edge = (
            transition.action == "grant"
            and current.generation == 1
            and current.issued_at == event.created_at
            and current.last_renewed_at == event.created_at
            and _parse_timestamp(current.expires_at, field="expires_at")
            > event_time
        )
    elif transition.action == "renew":
        valid_edge = (
            current.lease_id == previous.lease_id
            and current.client_id == previous.client_id
            and current.generation == previous.generation
            and current.issued_at == previous.issued_at
            and current.last_renewed_at == event.created_at
            and event_time
            >= _parse_timestamp(previous.last_renewed_at, field="last_renewed_at")
            and event_time
            < _parse_timestamp(previous.expires_at, field="expires_at")
            and _parse_timestamp(current.expires_at, field="expires_at") > event_time
        )
    elif transition.action == "expire":
        valid_edge = current == previous and event_time >= _parse_timestamp(
            previous.expires_at, field="expires_at"
        )
    elif transition.action == "release":
        valid_edge = (
            current.lease_id == previous.lease_id
            and current.client_id == previous.client_id
            and current.generation == previous.generation
            and current.issued_at == previous.issued_at
            and current.last_renewed_at == previous.last_renewed_at
            and current.expires_at == event.created_at
            and event_time
            >= _parse_timestamp(previous.last_renewed_at, field="last_renewed_at")
            and event_time
            < _parse_timestamp(previous.expires_at, field="expires_at")
        )
    elif transition.action in {"grant", "takeover"}:
        valid_edge = (
            current.generation == previous.generation + 1
            and current.lease_id != previous.lease_id
            and current.issued_at == event.created_at
            and current.last_renewed_at == event.created_at
            and _parse_timestamp(current.expires_at, field="expires_at") > event_time
            and event_time
            >= _parse_timestamp(previous.last_renewed_at, field="last_renewed_at")
            and (
                transition.action == "takeover"
                or event_time >= _parse_timestamp(previous.expires_at, field="expires_at")
            )
        )
    else:
        valid_edge = False
    if not valid_edge:
        raise LeaseError("invalid controller lease transition")
    return transition


def validate_daemon_event_outbox(value: object) -> set[str]:
    if type(value) is not list:
        raise TypeError("daemon_event_outbox must be a list")
    event_ids: list[str] = []
    for item in value:
        try:
            event_ids.append(validate_daemon_event_record(item))
        except LeaseError:
            raise LeaseError("invalid daemon event outbox item") from None
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("duplicate daemon event identity")
    return set(event_ids)


def validate_daemon_event_record(value: object) -> str:
    if type(value) is not dict or set(value) != _EVENT_FIELDS:
        raise LeaseError("invalid daemon event record")
    event_id = value["event_id"]
    event_type = value["event_type"]
    created_at = value["created_at"]
    payload = value["payload"]
    if (
        type(event_id) is not str
        or _EVENT_ID.fullmatch(event_id) is None
        or type(event_type) is not str
        or _EVENT_TYPE.fullmatch(event_type) is None
        or type(payload) is not dict
    ):
        raise LeaseError("invalid daemon event record")
    try:
        _parse_timestamp(created_at, field="daemon event created_at")
        _freeze_json(payload)
    except LeaseError:
        raise LeaseError("invalid daemon event record") from None
    return event_id
