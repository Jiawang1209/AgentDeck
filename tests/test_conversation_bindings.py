from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from agentdeck.conversation.bindings import (
    PreviewBindingError,
    consume_preview_binding,
    execution_digest,
    validate_preview_execution,
)
from agentdeck.conversation.models import build_preview_binding_record


NOW = datetime(2026, 7, 13, 0, 0, tzinfo=timezone.utc)


def _facts(**overrides: object) -> dict[str, object]:
    facts: dict[str, object] = {
        "control_kind": "mission_confirm",
        "project_root": "/tmp/project",
        "project_hash": "1" * 64,
        "leader_backend": {
            "provider": "fake",
            "model": "deterministic",
            "transport": "local",
        },
        "action_id": "mis_example",
        "action_hash": "2" * 64,
    }
    facts.update(overrides)
    return facts


def _semantic_facts(**overrides: object) -> dict[str, object]:
    facts: dict[str, object] = {
        "control_kind": "mission_confirm",
        "project_root": "/tmp/project",
        "leader_provider": "fake",
        "leader_model": "deterministic",
        "action_id": "mis_deadbeefcafe",
        "action_hash": "sha256:" + "1" * 64,
        "semantic_authority_hash": "sha256:" + "2" * 64,
        "compiled_task_hashes": ["sha256:" + "3" * 64, "sha256:" + "4" * 64],
        "policy_snapshot_hash": "sha256:" + "5" * 64,
        "preview_generation": 1,
    }
    facts.update(overrides)
    return facts


def _binding(*, facts: dict[str, object] | None = None, expires_at: str = "2026-07-13T01:00:00+00:00") -> dict[str, object]:
    execution_facts = facts or _facts()
    binding = build_preview_binding_record(
        "p1",
        conversation_id="c1",
        turn_id="t1",
        preview_kind="mission_confirm",
        execution_digest=execution_digest(execution_facts),
        expires_at=expires_at,
        created_at="2026-07-13T00:00:00+00:00",
    )
    binding["state"] = "pending"
    return binding


def test_execution_digest_is_canonical_for_mapping_key_order() -> None:
    first = _facts()
    second = dict(reversed(list(first.items())))

    assert execution_digest(first) == execution_digest(second)
    assert len(execution_digest(first)) == 64


def test_validate_preview_accepts_exact_current_facts() -> None:
    binding = _binding()

    validated = validate_preview_execution(binding, _facts(), now=NOW)

    assert validated["preview_id"] == "p1"
    assert validated["state"] == "pending"


def test_semantic_preview_execution_facts_are_exactly_the_ten_bound_fields() -> None:
    facts = _semantic_facts()

    assert tuple(facts) == (
        "control_kind",
        "project_root",
        "leader_provider",
        "leader_model",
        "action_id",
        "action_hash",
        "semantic_authority_hash",
        "compiled_task_hashes",
        "policy_snapshot_hash",
        "preview_generation",
    )


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("control_kind", "other_control"),
        ("project_root", "/tmp/other"),
        ("leader_provider", "other"),
        ("leader_model", "other-model"),
        ("action_id", "mis_cafebabefeed"),
        ("action_hash", "sha256:" + "6" * 64),
        ("semantic_authority_hash", "sha256:" + "7" * 64),
        ("compiled_task_hashes", ["sha256:" + "8" * 64]),
        ("policy_snapshot_hash", "sha256:" + "9" * 64),
        ("preview_generation", 2),
    ],
)
def test_semantic_preview_binding_rejects_each_changed_fact(
    field: str, changed: object
) -> None:
    binding = _binding(facts=_semantic_facts())

    with pytest.raises(PreviewBindingError, match="preview state drift"):
        validate_preview_execution(
            binding,
            _semantic_facts(**{field: changed}),
            now=NOW,
        )


@pytest.mark.parametrize(
    ("binding_update", "fact_update", "message"),
    [
        ({"state": "consumed"}, {}, "preview is not pending"),
        ({"expires_at": "2026-07-12T23:59:59+00:00"}, {}, "preview expired"),
        ({}, {"project_hash": "3" * 64}, "preview state drift"),
        (
            {},
            {
                "leader_backend": {
                    "provider": "fake",
                    "model": "changed",
                    "transport": "local",
                }
            },
            "preview state drift",
        ),
        ({}, {"action_hash": "4" * 64}, "preview state drift"),
    ],
)
def test_validate_preview_rejects_non_current_binding_without_mutation(
    binding_update: dict[str, object],
    fact_update: dict[str, object],
    message: str,
) -> None:
    binding = _binding()
    binding.update(binding_update)
    facts = _facts(**fact_update)
    before = deepcopy(binding)

    with pytest.raises(PreviewBindingError, match=message):
        validate_preview_execution(binding, facts, now=NOW)

    assert binding == before


def test_expiry_boundary_is_fail_closed() -> None:
    binding = _binding(expires_at="2026-07-13T00:00:00+00:00")

    with pytest.raises(PreviewBindingError, match="preview expired"):
        validate_preview_execution(binding, _facts(), now=NOW)


def test_consume_returns_new_record_and_can_only_happen_once() -> None:
    binding = _binding()
    before = deepcopy(binding)

    consumed = consume_preview_binding(binding, _facts(), now=NOW)

    assert binding == before
    assert consumed["state"] == "consumed"
    assert consumed["consumed_at"] == "2026-07-13T00:00:00+00:00"
    with pytest.raises(PreviewBindingError, match="preview is not pending"):
        consume_preview_binding(consumed, _facts(), now=NOW)


def test_digest_rejects_non_json_facts_instead_of_stringifying_them() -> None:
    with pytest.raises(ValueError, match="execution facts must be canonical JSON"):
        execution_digest({"unsafe": object()})
