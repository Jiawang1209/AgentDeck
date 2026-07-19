from dataclasses import FrozenInstanceError
import traceback

import pytest

from agentdeck.kernel.execution import (
    AcceptanceResult,
    FindingSeverity,
    ResultError,
)
from agentdeck.kernel.execution_semantics import (
    ReviewResult,
    materialize_revision,
    validate_acceptance,
)


def finding(
    finding_id: str,
    *,
    scope: str = "src",
    evidence: tuple[str, ...] = ("ev_diff",),
    severity: str = "blocking",
) -> dict[str, object]:
    return {
        "finding_id": finding_id,
        "scope": scope,
        "severity": severity,
        "summary": "verified review finding",
        "criterion": "approved behavior",
        "evidence_ids": list(evidence),
    }


def test_revision_task_contains_only_in_scope_evidence_backed_findings() -> None:
    result = materialize_revision(
        findings=[
            finding("rfn_1"),
            finding("rfn_2", scope="/outside", evidence=("ev_note",)),
            finding("rfn_3", evidence=()),
        ],
        confirmed_scope=("src",),
    )

    assert [item.finding_id for item in result.findings] == ["rfn_1"]
    assert [item.finding_id for item in result.rejected] == ["rfn_2", "rfn_3"]
    assert [item.reason for item in result.rejected] == [
        "outside_confirmed_scope",
        "typed_evidence_missing",
    ]


def test_revision_scope_matching_is_segment_aware_and_normalized() -> None:
    result = materialize_revision(
        findings=[
            finding("rfn_child", scope="src/agentdeck"),
            finding("rfn_prefix", scope="src-other"),
            finding("rfn_escape", scope="src/../outside"),
        ],
        confirmed_scope=("src",),
    )

    assert [item.finding_id for item in result.findings] == ["rfn_child"]
    assert [item.finding_id for item in result.rejected] == [
        "rfn_prefix",
        "rfn_escape",
    ]


def test_review_result_validator_is_closed_frozen_and_copies_input() -> None:
    payload = {"summary": "review complete", "findings": [finding("rfn_1")]}
    result = ReviewResult.from_mapping(payload)
    payload["findings"].clear()  # type: ignore[union-attr]

    assert result.summary == "review complete"
    assert result.findings[0].severity is FindingSeverity.BLOCKING
    assert result.findings[0].evidence_ids == ("ev_diff",)
    with pytest.raises(FrozenInstanceError):
        result.summary = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(unexpected="worker command"),
        lambda payload: payload.pop("summary"),
        lambda payload: payload.update(findings="not a list"),
        lambda payload: payload["findings"][0].update(raw_protocol="frame"),
    ],
)
def test_review_result_rejects_every_nonclosed_shape_without_raw_content(
    mutate,
) -> None:
    marker = "attacker-controlled-review-marker"
    payload = {"summary": "review complete", "findings": [finding("rfn_1")]}
    mutate(payload)
    payload["marker"] = marker

    with pytest.raises(ResultError) as raised:
        ReviewResult.from_mapping(payload)

    assert marker not in "".join(traceback.format_exception(raised.value))


def test_review_result_rejects_unbounded_text() -> None:
    with pytest.raises(ResultError, match="review result is invalid"):
        ReviewResult.from_mapping({
            "summary": "x" * (64 * 1024 + 1),
            "findings": [finding("rfn_1")],
        })


def test_acceptance_maps_typed_evidence_to_every_criterion() -> None:
    with pytest.raises(ResultError, match="criterion mobile missing evidence"):
        validate_acceptance(
            criteria=("desktop", "mobile"),
            mappings={"desktop": ("ev_browser",)},
        )


def test_acceptance_validator_returns_the_closed_typed_value() -> None:
    mappings = {"desktop": ["ev_browser"], "mobile": ["ev_mobile"]}
    result = validate_acceptance(
        criteria=("desktop", "mobile"), mappings=mappings,
    )
    mappings["desktop"].clear()

    assert type(result) is AcceptanceResult
    assert result.evidence_by_criterion == (
        ("desktop", ("ev_browser",)),
        ("mobile", ("ev_mobile",)),
    )
    assert result.accepted is True


def test_acceptance_validator_rejects_extra_or_untyped_evidence() -> None:
    with pytest.raises(ResultError, match="unexpected evidence criterion"):
        validate_acceptance(
            criteria=("desktop",),
            mappings={"desktop": ("ev_browser",), "other": ("ev_other",)},
        )
    with pytest.raises(ResultError, match="typed evidence"):
        validate_acceptance(
            criteria=("desktop",), mappings={"desktop": ("looks good",)},
        )


class HostileEvidenceValue:
    def __bool__(self) -> bool:
        raise RuntimeError("attacker-controlled-acceptance-marker")


def test_acceptance_validator_contains_hostile_value_exceptions() -> None:
    with pytest.raises(ResultError) as raised:
        validate_acceptance(
            criteria=("desktop",), mappings={"desktop": HostileEvidenceValue()},
        )

    assert "attacker-controlled-acceptance-marker" not in "".join(
        traceback.format_exception(raised.value)
    )
