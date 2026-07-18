from dataclasses import replace
import json

import pytest

from agentdeck.kernel.execution import (
    AcceptanceResult,
    Evidence,
    EvidenceKind,
    Handoff,
    ResultError,
)


class HostileCanonicalEquality:
    def __ne__(self, other: object) -> bool:
        return False


class CanonicalStringSubclass(str):
    pass


def test_handoff_hash_covers_lineage_evidence_artifacts_and_known_issues() -> None:
    artifacts = ["artifact://patch"]
    evidence_ids = ["ev_1"]
    issues = ["one warning remains"]
    handoff = Handoff.create(
        "hnd_1",
        "att_1",
        "tsk_2",
        "done",
        evidence_ids,
        artifact_references=artifacts,
        known_issues=issues,
    )
    artifacts.clear()
    evidence_ids.clear()
    issues.clear()

    projection = json.loads(handoff.canonical_content)
    assert projection == {
        "artifact_references": ["artifact://patch"],
        "handoff_id": "hnd_1",
        "known_issues": ["one warning remains"],
        "result_summary": "done",
        "source_attempt_id": "att_1",
        "target_task_id": "tsk_2",
        "verification_evidence_ids": ["ev_1"],
    }
    assert len(handoff.content_hash) == 64
    assert handoff.content_hash == handoff.content_hash.lower()
    assert hash(handoff)


def test_plan_minimum_handoff_constructor_is_supported() -> None:
    handoff = Handoff.create("hnd_1", "att_1", "tsk_2", "done", ("ev_1",))

    assert handoff.artifact_references == ()
    assert handoff.known_issues == ()
    assert handoff.verification_evidence_ids == ("ev_1",)


@pytest.mark.parametrize(
    "constructor",
    (
        lambda: Handoff.create("hnd_1", "att_1", "att_1", "done", ("ev_1",)),
        lambda: Handoff.create("hnd_1", "att_1", "tsk_2", "done", ()),
        lambda: Handoff.create(
            "hnd_1", "att_1", "tsk_2", "done", ("ev_1", "ev_1")
        ),
        lambda: Handoff.create(
            "hnd_1", "att_1", "tsk_2", "done", ("worker prose",)
        ),
        lambda: Handoff.create("hnd_1", "att_1", "tsk_2", "\ud800", ("ev_1",)),
    ),
)
def test_handoff_rejects_invalid_lineage_and_evidence(constructor: object) -> None:
    with pytest.raises((TypeError, ValueError, ResultError)):
        constructor()  # type: ignore[operator]


def test_handoff_direct_constructor_rejects_hash_or_projection_tampering() -> None:
    handoff = Handoff.create("hnd_1", "att_1", "tsk_2", "done", ("ev_1",))

    with pytest.raises(ResultError, match="content_hash"):
        replace(handoff, content_hash="0" * 64)
    with pytest.raises(ResultError, match="canonical"):
        replace(handoff, canonical_content="{}")
    with pytest.raises(ValueError):
        replace(handoff, content_hash="A" * 64)


@pytest.mark.parametrize(
    ("hostile", "error"),
    (
        (lambda canonical: HostileCanonicalEquality(), TypeError),
        (lambda canonical: CanonicalStringSubclass(canonical), TypeError),
        (lambda canonical: object(), TypeError),
        (lambda canonical: "\ud800", ValueError),
    ),
)
def test_handoff_rejects_hostile_canonical_content_before_comparison(
    hostile: object, error: type[Exception]
) -> None:
    handoff = Handoff.create("hnd_1", "att_1", "tsk_2", "done", ("ev_1",))
    canonical_content = hostile(handoff.canonical_content)  # type: ignore[operator]

    with pytest.raises(error):
        Handoff(
            handoff.handoff_id,
            handoff.source_attempt_id,
            handoff.target_task_id,
            handoff.result_summary,
            handoff.verification_evidence_ids,
            handoff.artifact_references,
            handoff.known_issues,
            canonical_content,  # type: ignore[arg-type]
            handoff.content_hash,
        )


@pytest.mark.parametrize(
    "hostile_hash",
    (
        HostileCanonicalEquality(),
        CanonicalStringSubclass("0" * 64),
        object(),
    ),
)
def test_handoff_content_hash_direct_path_remains_exact_string_only(
    hostile_hash: object,
) -> None:
    handoff = Handoff.create("hnd_1", "att_1", "tsk_2", "done", ("ev_1",))

    with pytest.raises(TypeError):
        replace(handoff, content_hash=hostile_hash)  # type: ignore[arg-type]


def test_acceptance_requires_typed_evidence_for_every_declared_criterion() -> None:
    criteria = ["build passes", "review findings resolved"]
    mapping = {
        "review findings resolved": ["ev_review"],
        "build passes": ["ev_test", "ev_artifact"],
    }
    result = AcceptanceResult.create(criteria, mapping, accepted=True)
    criteria.clear()
    mapping["build passes"].clear()

    assert result.criteria == ("build passes", "review findings resolved")
    assert result.evidence_by_criterion == (
        ("build passes", ("ev_test", "ev_artifact")),
        ("review findings resolved", ("ev_review",)),
    )
    assert result.accepted is True
    assert result.failure_reason is None
    assert hash(result)


def test_acceptance_mapping_is_deterministic_across_mapping_order() -> None:
    first = AcceptanceResult.create(
        ("a", "b"), {"a": ("ev_a",), "b": ("ev_b",)}, accepted=True
    )
    second = AcceptanceResult.create(
        ("a", "b"), {"b": ("ev_b",), "a": ("ev_a",)}, accepted=True
    )

    assert first == second


@pytest.mark.parametrize(
    "mapping",
    (
        {"a": ("ev_a",)},
        {"a": ("ev_a",), "b": ("ev_b",), "c": ("ev_c",)},
        {"a": (), "b": ("ev_b",)},
        {"a": ("ev_a", "ev_a"), "b": ("ev_b",)},
        {"a": ("worker says okay",), "b": ("ev_b",)},
    ),
)
def test_acceptance_rejects_missing_extra_empty_duplicate_or_prose_evidence(
    mapping: object,
) -> None:
    with pytest.raises(ResultError, match="evidence"):
        AcceptanceResult.create(("a", "b"), mapping, accepted=True)


def test_failed_acceptance_requires_an_explicit_failure_reason() -> None:
    mapping = {"criterion": ("ev_failure",)}

    with pytest.raises(ResultError, match="failure_reason"):
        AcceptanceResult.create(("criterion",), mapping, accepted=False)
    with pytest.raises(ResultError, match="accepted result"):
        AcceptanceResult.create(
            ("criterion",), mapping, accepted=True, failure_reason="not accepted"
        )

    failed = AcceptanceResult.create(
        ("criterion",), mapping, accepted=False, failure_reason="criterion failed"
    )
    assert failed.accepted is False
    assert failed.failure_reason == "criterion failed"


def test_acceptance_evidence_can_be_wrapped_as_typed_evidence() -> None:
    result = AcceptanceResult.create(
        ("criterion",), {"criterion": ("ev_test",)}, accepted=True
    )
    evidence = Evidence.acceptance(
        "ev_acceptance",
        result=result,
        source_kind=EvidenceKind.ACCEPTANCE_RESULT,
    )

    assert evidence.kind is EvidenceKind.ACCEPTANCE_RESULT
    assert json.loads(evidence.canonical_content)["accepted"] is True
