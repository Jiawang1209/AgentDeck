from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from inspect import signature
from uuid import UUID

import pytest

from agentdeck.adapters.system_clock import SystemClock
from agentdeck.kernel.diagnostics import Diagnostic, Severity
from agentdeck.kernel.events import DomainEvent, FactArray, FactObject
from .fakes import FrozenClock


OCCURRED_AT = "2026-07-18T00:00:00+00:00"


def create_diagnostic(**changes: object) -> Diagnostic:
    facts: dict[str, object] = {
        "code": "leader_unavailable",
        "stage": "discovery",
        "severity": Severity.ERROR,
        "actor": "codex",
        "summary": "Codex is not ready.",
        "cause": "The configured executable was not found.",
        "impact": "The Mission did not start.",
        "protection": "No fallback transport was selected.",
        "recovery_actions": ("Run /setup.",),
        "retryable": True,
        "outcome_known": True,
        "occurred_at": OCCURRED_AT,
    }
    facts.update(changes)
    return Diagnostic.create(**facts)


def test_diagnostic_and_event_are_clocked_immutable_facts() -> None:
    clock = FrozenClock(datetime(2026, 7, 18, tzinfo=timezone.utc))
    diagnostic = Diagnostic.create(
        code="leader_unavailable",
        stage="discovery",
        severity=Severity.ERROR,
        actor="codex",
        summary="Codex is not ready.",
        cause="The configured executable was not found.",
        impact="The Mission did not start.",
        protection="No fallback transport was selected.",
        recovery_actions=("Run /setup.",),
        retryable=True,
        outcome_known=True,
        occurred_at=clock.now().isoformat(),
    )
    event = DomainEvent.create(
        kind="diagnostic_recorded",
        aggregate_type="product_session",
        aggregate_id="psn_test",
        payload={"summary": diagnostic.summary, "code": diagnostic.code},
        occurred_at=clock.now().isoformat(),
    )

    assert diagnostic.occurred_at == "2026-07-18T00:00:00+00:00"
    assert event.occurred_at == "2026-07-18T00:00:00+00:00"
    assert event.payload == (
        ("code", "leader_unavailable"),
        ("summary", "Codex is not ready."),
    )
    with pytest.raises(FrozenInstanceError):
        diagnostic.code = "changed"
    with pytest.raises(FrozenInstanceError):
        event.kind = "changed"


def test_event_recursively_freezes_payload_aliases_into_hashable_facts() -> None:
    nested_mapping = {"z": 2, "a": [True, None]}
    nested_list = ["first", nested_mapping]
    payload = {"nested": {"items": nested_list}, "count": 1}

    event = DomainEvent.create(
        kind="diagnostic_recorded",
        aggregate_type="product_session",
        aggregate_id="psn_test",
        payload=payload,
        occurred_at=OCCURRED_AT,
    )
    nested_list.append("late")
    nested_mapping["z"] = 99
    nested_mapping["a"].append(False)

    assert event.payload == (
        ("count", 1),
        (
            "nested",
            FactObject(
                items=(
                    (
                        "items",
                        FactArray(
                            items=(
                                "first",
                                FactObject(
                                    items=(
                                        ("a", FactArray(items=(True, None))),
                                        ("z", 2),
                                    )
                                ),
                            )
                        ),
                    ),
                )
            ),
        ),
    )
    assert hash(event)


def test_event_preserves_nested_object_and_array_semantics() -> None:
    object_event = DomainEvent.create(
        kind="fact_recorded",
        aggregate_type="product_session",
        aggregate_id="psn_test",
        payload={"x": {"a": 1}},
        occurred_at=OCCURRED_AT,
    )
    array_event = DomainEvent.create(
        kind="fact_recorded",
        aggregate_type="product_session",
        aggregate_id="psn_test",
        payload={"x": [["a", 1]]},
        occurred_at=OCCURRED_AT,
    )
    same_identity_array_event = replace(array_event, event_id=object_event.event_id)

    assert object_event.payload != array_event.payload
    assert object_event != same_identity_array_event
    assert hash(object_event)
    assert hash(same_identity_array_event)


@pytest.mark.parametrize(
    ("payload", "error"),
    (
        ({"value": object()}, TypeError),
        ({1: "not a string key"}, TypeError),
        ({"value": float("nan")}, ValueError),
    ),
)
def test_event_rejects_noncanonical_payload_values(
    payload: object, error: type[Exception]
) -> None:
    with pytest.raises(error):
        DomainEvent.create(
            kind="diagnostic_recorded",
            aggregate_type="product_session",
            aggregate_id="psn_test",
            payload=payload,
            occurred_at=OCCURRED_AT,
        )


def test_event_direct_constructor_rejects_mutable_nested_payload() -> None:
    with pytest.raises(TypeError):
        DomainEvent(
            event_id="evt_test",
            kind="diagnostic_recorded",
            aggregate_type="product_session",
            aggregate_id="psn_test",
            payload=(("mutable", []),),
            occurred_at=OCCURRED_AT,
        )


def test_diagnostic_factory_is_explicit_and_copies_recovery_actions() -> None:
    actions = ["Run /setup."]

    diagnostic = create_diagnostic(recovery_actions=actions)
    actions.append("Do not leak into the fact.")

    assert tuple(signature(Diagnostic.create).parameters) == (
        "code",
        "stage",
        "severity",
        "actor",
        "summary",
        "cause",
        "impact",
        "protection",
        "recovery_actions",
        "retryable",
        "outcome_known",
        "occurred_at",
        "mission_id",
        "task_id",
        "attempt_id",
        "trace_id",
    )
    assert diagnostic.recovery_actions == ("Run /setup.",)
    assert hash(diagnostic)


@pytest.mark.parametrize(
    "changes",
    (
        {"severity": "error"},
        {"retryable": 1},
        {"outcome_known": 0},
        {"recovery_actions": ("Run /setup.", 1)},
        {"code": 1},
        {"trace_id": 1},
    ),
)
def test_diagnostic_rejects_invalid_fact_types(changes: dict[str, object]) -> None:
    with pytest.raises(TypeError):
        create_diagnostic(**changes)


def test_fact_factories_normalize_aware_times_to_utc() -> None:
    occurred_at = "2026-07-18T08:00:00+08:00"

    diagnostic = create_diagnostic(occurred_at=occurred_at)
    event = DomainEvent.create(
        kind="diagnostic_recorded",
        aggregate_type="product_session",
        aggregate_id="psn_test",
        payload={"code": diagnostic.code},
        occurred_at=occurred_at,
    )

    assert diagnostic.occurred_at == OCCURRED_AT
    assert event.occurred_at == OCCURRED_AT


@pytest.mark.parametrize("occurred_at", ("not-a-time", "2026-07-18T00:00:00"))
def test_fact_factories_reject_malformed_or_naive_times(occurred_at: str) -> None:
    with pytest.raises(ValueError):
        create_diagnostic(occurred_at=occurred_at)
    with pytest.raises(ValueError):
        DomainEvent.create(
            kind="diagnostic_recorded",
            aggregate_type="product_session",
            aggregate_id="psn_test",
            payload={},
            occurred_at=occurred_at,
        )


def test_system_clock_is_utc_aware_and_event_id_is_uuid_hex() -> None:
    now = SystemClock().now()
    event = DomainEvent.create(
        kind="diagnostic_recorded",
        aggregate_type="product_session",
        aggregate_id="psn_test",
        payload={},
        occurred_at=now.isoformat(),
    )

    assert now.tzinfo is timezone.utc
    assert now.utcoffset() is not None
    assert event.event_id.startswith("evt_")
    parsed = UUID(hex=event.event_id.removeprefix("evt_"))
    assert parsed.hex == event.event_id.removeprefix("evt_")
    assert parsed.version == 4
