from __future__ import annotations

import json

import pytest

from agentdeck.kernel.diagnostics import Diagnostic, Severity, diagnostic
from agentdeck.product import presenter
from agentdeck.product.presenter import (
    DIAGNOSTIC_JSON_FIELDS,
    DIAGNOSTIC_SCHEMA_VERSION,
    diagnostic_json,
    present_diagnostic,
)


_CODES = (
    "leader_authentication_failed",
    "acp_protocol_mismatch",
    "mission_preview_drift",
    "worker_outcome_unknown",
    "review_scope_invalid",
    "acceptance_evidence_missing",
    "permission_denied",
    "storage_recovery_failed",
    "tmux_observer_degraded",
)


@pytest.mark.parametrize("code", _CODES)
def test_every_non_success_has_complete_error_card(code: str) -> None:
    card = present_diagnostic(diagnostic(code))

    assert all(
        getattr(card, field)
        for field in (
            "what_happened", "why", "completed", "not_completed",
            "protection", "recovery_actions", "identity",
        )
    )


def test_diagnostic_factory_rejects_unknown_code() -> None:
    with pytest.raises(ValueError):
        diagnostic("unknown_code")


def test_present_diagnostic_derives_fields_from_the_diagnostic_fact() -> None:
    fact = diagnostic("permission_denied", attempt_id="att_1")
    card = present_diagnostic(fact)

    assert card.what_happened == fact.summary
    assert card.why == fact.cause
    assert card.not_completed == fact.impact
    assert card.recovery_actions == fact.recovery_actions
    assert "att_1" in card.identity


def test_present_diagnostic_identity_is_never_empty_without_lineage() -> None:
    fact = diagnostic("permission_denied")
    card = present_diagnostic(fact)

    assert card.identity


def test_present_diagnostic_rejects_non_diagnostic_input() -> None:
    with pytest.raises(TypeError):
        present_diagnostic(object())  # type: ignore[arg-type]


def test_diagnostic_json_has_exactly_the_closed_field_set() -> None:
    fact = diagnostic("storage_recovery_failed")
    out = diagnostic_json(fact)

    assert set(json.loads(out)) == DIAGNOSTIC_JSON_FIELDS
    assert json.loads(out)["schema_version"] == DIAGNOSTIC_SCHEMA_VERSION
    assert json.loads(out)["schema_version"] == "diagnostic/v1"


def test_diagnostic_json_redacts_absolute_paths_and_raw_stderr() -> None:
    fact = Diagnostic.create(
        code="storage_recovery_failed",
        stage="storage",
        severity=Severity.ERROR,
        actor="agentdeck",
        summary="The project store could not be opened.",
        cause="See /Users/private/x for raw stderr: boom in the log.",
        impact="No new writes were accepted.",
        protection="No partial state was treated as authoritative.",
        recovery_actions=("Inspect the project database at /Users/private/x.",),
        retryable=False,
        outcome_known=False,
        occurred_at="2026-07-22T00:00:00+00:00",
    )

    out = diagnostic_json(fact)

    assert "/Users/private" not in out
    assert "raw stderr" not in out


def test_diagnostic_json_rejects_unknown_field_set(monkeypatch) -> None:
    fact = diagnostic("permission_denied")
    monkeypatch.setattr(
        presenter, "DIAGNOSTIC_JSON_FIELDS", frozenset({"schema_version"})
    )

    with pytest.raises(ValueError):
        diagnostic_json(fact)
