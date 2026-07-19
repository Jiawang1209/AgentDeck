from dataclasses import FrozenInstanceError
from copy import deepcopy
import asyncio
import json
import traceback

import pytest

from agentdeck.kernel.execution import (
    AcceptanceResult,
    Attempt,
    Evidence,
    EvidenceKind,
    FindingSeverity,
    ResultError,
)
from agentdeck.kernel.execution_semantics import (
    ReviewResult,
    RevisionResult,
    materialize_revision,
    validate_acceptance,
)
from agentdeck.application.execution_records import (
    CommittedEvidence,
    EvidenceAuthority,
    EvidenceLineageError,
    command_id,
    stage_id,
)
from product_kernel.test_execution_coordinator import Harness, ScriptedWorker


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


def test_review_result_preserves_every_finding_without_flattening() -> None:
    result = ReviewResult.from_mapping({
        "summary": "two independent findings",
        "findings": [finding("rfn_1"), finding("rfn_2", severity="warning")],
    })

    assert [item.finding_id for item in result.findings] == ["rfn_1", "rfn_2"]
    assert result.findings[0].summary == "verified review finding"


def test_review_result_rejects_duplicate_finding_ids() -> None:
    with pytest.raises(ResultError, match="review result is invalid"):
        ReviewResult.from_mapping({
            "summary": "duplicate", "findings": [finding("rfn_1"), finding("rfn_1")],
        })


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


def revision_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "summary": "revision complete", "base": "base", "head": "head",
        "diff_hash": "b" * 64, "resolved_finding_ids": ["rfn_1"],
        "evidence_ids": ["ev_review"],
    }
    payload.update(changes)
    return payload


def test_revision_result_is_closed_and_binds_authoritative_finding_ids() -> None:
    result = RevisionResult.from_mapping(
        revision_payload(), accepted_finding_ids=("rfn_1",),
        expected_evidence_ids=("ev_review",),
    )

    assert result.resolved_finding_ids == ("rfn_1",)
    assert result.evidence_ids == ("ev_review",)


@pytest.mark.parametrize("changes", [
    {"resolved_finding_ids": []},
    {"resolved_finding_ids": ["rfn_1", "rfn_1"]},
    {"resolved_finding_ids": ["rfn_1", "rfn_extra"]},
    {"resolved_finding_ids": ["rfn_other"]},
    {"evidence_ids": []},
    {"unexpected": "raw worker extension"},
])
def test_revision_result_rejects_missing_duplicate_extra_or_mismatched_authority(
    changes,
) -> None:
    with pytest.raises(ResultError, match="revision result is invalid"):
        RevisionResult.from_mapping(
            revision_payload(**changes), accepted_finding_ids=("rfn_1",),
            expected_evidence_ids=("ev_review",),
        )


@pytest.mark.parametrize("reported", [
    [], ["ev_one"], ["ev_two", "ev_one"], ["ev_one", "ev_one"],
    ["ev_one", "ev_two", "ev_extra"],
])
def test_revision_result_requires_exact_ordered_authoritative_evidence(
    reported,
) -> None:
    with pytest.raises(ResultError, match="revision result is invalid"):
        RevisionResult.from_mapping(
            revision_payload(evidence_ids=reported),
            accepted_finding_ids=("rfn_1",),
            expected_evidence_ids=("ev_one", "ev_two"),
        )


def test_revision_result_accepts_exact_ordered_authoritative_evidence() -> None:
    result = RevisionResult.from_mapping(
        revision_payload(evidence_ids=["ev_one", "ev_two"]),
        accepted_finding_ids=("rfn_1",),
        expected_evidence_ids=("ev_one", "ev_two"),
    )

    assert result.evidence_ids == ("ev_one", "ev_two")


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


@pytest.mark.parametrize(("stage", "reference", "code"), [
    ("revision", "ev_invented_revision", "worker_result_invalid"),
    ("acceptance", "ev_invented_acceptance", "evidence_lineage_invalid"),
    ("revision", "future", "worker_result_invalid"),
    ("acceptance", "future", "evidence_lineage_invalid"),
])
def test_revision_and_acceptance_reject_nonexistent_or_future_evidence(
    stage, reference, code,
) -> None:
    harness = Harness()
    if reference == "future":
        future_task = next(
            task for task in harness.draft.tasks if task.name == "acceptance"
        )
        reference = stage_id("ev_", harness.confirmed, future_task, "1")
    if stage == "revision":
        harness.results[stage]["evidence_ids"] = [reference]
    else:
        harness.results[stage]["evidence_by_criterion"] = {
            criterion: [reference]
            for criterion in harness.draft.acceptance_criteria
        }

    result = asyncio.run(harness.run())

    assert result.diagnostic.code == code
    assert harness.started_tasks[-1] == stage
    assert reference not in result.diagnostic.cause


def test_acceptance_result_rejects_unexpected_outer_fields_before_commit() -> None:
    harness = Harness()
    harness.results["acceptance"]["raw_worker_extension"] = "hostile-marker"

    result = asyncio.run(harness.run())

    assert result.diagnostic.code == "acceptance_evidence_missing"
    assert result.evidence[-1].kind.value == "diff_identity"
    assert "hostile-marker" not in result.diagnostic.cause


def test_evidence_authority_rejects_a_reference_bound_to_the_wrong_attempt() -> None:
    evidence = Evidence.create("ev_prior", EvidenceKind.ARTIFACT_HASH, {
        "artifact_reference": "patch", "content_hash": "a" * 64,
    })
    authority = EvidenceAuthority(
        (CommittedEvidence(evidence, "msn_current", "tsk_prior", "att_wrong"),),
        "msn_current", "tsk_prior",
        (Attempt.pending("att_real", "tsk_prior", 1).start().complete("done"),),
    )

    with pytest.raises(EvidenceLineageError, match="evidence lineage is invalid"):
        authority.validate(("ev_prior",))


def test_revision_request_carries_only_complete_accepted_finding_authority() -> None:
    harness = Harness()
    accepted = dict(harness.results["review"]["findings"][0])
    accepted.update(summary="accepted finding detail", criterion="fix exact bug")
    rejected = dict(accepted, finding_id="rfn_rejected", scope="outside",
                    summary="rejected audit detail")
    harness.results["review"]["findings"] = [accepted, rejected]

    result = asyncio.run(harness.run())

    review_instruction = json.loads(harness.requests[1].instruction)
    revision_instruction = json.loads(harness.requests[2].instruction)
    assert review_instruction["attempt"]["attempt_id"] == harness.requests[1].attempt_id
    assert review_instruction["incoming_handoff"] == {
        "canonical_content": result.handoffs[0].canonical_content,
        "content_hash": result.handoffs[0].content_hash,
    }
    assert revision_instruction["incoming_handoff"] == {
        "canonical_content": result.handoffs[1].canonical_content,
        "content_hash": result.handoffs[1].content_hash,
    }
    authority = revision_instruction["authoritative_revision_task"]
    assert authority == result.revision_task.canonical_payload()
    assert set(authority) == {
        "task_id", "created_by", "confirmed_scope", "accepted_finding_ids",
        "accepted_findings",
    }
    finding = authority["accepted_findings"][0]
    assert finding == {
        "finding_id": "rfn_1", "scope": "project", "severity": "warning",
        "summary": "accepted finding detail", "criterion": "fix exact bug",
        "evidence_lineage": {
            "review_evidence_id": result.handoffs[1].verification_evidence_ids[0],
            "reference_evidence_ids": [result.evidence[0].evidence_id],
        },
    }
    assert "rfn_rejected" not in json.dumps(authority)
    assert "rejected audit detail" not in json.dumps(authority)
    assert any("rfn_rejected" in item.canonical_content for item in result.evidence)
    rejected_evidence_id = next(
        item.evidence_id for item in result.evidence
        if "rfn_rejected" in item.canonical_content
    )
    assert rejected_evidence_id not in json.dumps(revision_instruction)


def test_reviser_derives_resolved_ids_from_complete_authoritative_findings() -> None:
    harness = Harness()

    result = asyncio.run(harness.run())

    assert result.diagnostic is None
    assert result.revision_task.accepted_finding_ids == ("rfn_1",)


def test_revision_rejects_rejected_review_evidence_before_acceptance() -> None:
    harness = Harness()
    accepted = dict(harness.results["review"]["findings"][0])
    rejected = dict(accepted, finding_id="rfn_rejected", scope="outside",
                    summary="rejected-secret-marker")
    harness.results["review"]["findings"] = [accepted, rejected]
    original = harness.result_payload

    def rejected_reference(name):
        payload = original(name)
        if name == "revision":
            payload["evidence_ids"] = [next(
                identity for (kind, identity), facts in harness.store.aggregates.items()
                if kind == "evidence"
                and "rfn_rejected" in facts["canonical_evidence_facts"]
            )]
        return payload

    harness.result_payload = rejected_reference
    result = asyncio.run(harness.run())

    assert result.diagnostic.code == "worker_result_invalid"
    assert harness.started_tasks == ["implementation", "review", "revision"]
    assert "rejected-secret-marker" not in result.diagnostic.cause


class ReplaySeedingWorker(ScriptedWorker):
    async def collect_result(self, handle):
        result = await super().collect_result(handle)
        replay = self._harness.terminal_replay
        if self._task_name == replay["stage"]:
            self._harness.store.commands[replay["key"]] = deepcopy(replay["command"])
            self._harness.store.aggregates.update(deepcopy(replay["aggregates"]))
        return result


def _review_replay():
    donor = Harness()
    second = dict(donor.results["review"]["findings"][0])
    second["finding_id"] = "rfn_2"
    donor.results["review"]["findings"].append(second)
    result = asyncio.run(donor.run())
    task = donor.draft.tasks[1]
    attempt, handoff = result.attempts[1], result.handoffs[1]
    evidence_ids = tuple(
        identity for (kind, identity), facts in donor.store.aggregates.items()
        if kind == "evidence" and facts["attempt_id"] == attempt.attempt_id
    )
    command = {
        "mission_id": donor.confirmed.mission_id,
        "mission_version": donor.confirmed.version, "task_id": task.task_id,
        "attempt_id": attempt.attempt_id, "evidence_ids": list(evidence_ids),
        "handoff_id": handoff.handoff_id,
    }
    ids = (("attempts", attempt.attempt_id), *(
        ("evidence", identity) for identity in evidence_ids
    ), ("handoffs", handoff.handoff_id))
    aggregates = {key: deepcopy(donor.store.aggregates[key]) for key in ids}
    key = (command_id("terminal", donor.confirmed, task, 1),
           "execution_stage_committed")
    assert donor.store.commands[key] == command
    return command, aggregates, key


def _corrupt_replay(kind, command, aggregates):
    if kind.startswith("missing_"):
        aggregate = {"missing_attempt": "attempts", "missing_evidence": "evidence",
                     "missing_handoff": "handoffs"}[kind]
        aggregates.pop(next(key for key in aggregates if key[0] == aggregate))
    elif kind.startswith("mismatched_"):
        aggregate = kind.removeprefix("mismatched_") + (
            "s" if kind != "mismatched_evidence" else ""
        )
        key = next(key for key in aggregates if key[0] == aggregate)
        field = {"attempts": "state", "evidence": "kind",
                 "handoffs": "target_task_id"}[aggregate]
        aggregates[key][field] = "drifted"
    elif kind == "partial_ids":
        command["evidence_ids"] = command["evidence_ids"][:1]
    elif kind == "reordered_ids":
        command["evidence_ids"].reverse()
    elif kind == "extra_ids":
        command["evidence_ids"].append("ev_extra")
        source = next(value for key, value in aggregates.items() if key[0] == "evidence")
        aggregates[("evidence", "ev_extra")] = dict(source, evidence_id="ev_extra")
    else:
        command.clear(); command.update(attempt_id="att_old", evidence_id="ev_old")


@pytest.mark.parametrize("corruption", [
    "missing_attempt", "missing_evidence", "missing_handoff",
    "mismatched_attempt", "mismatched_evidence", "mismatched_handoff",
    "partial_ids", "reordered_ids", "extra_ids", "old_result",
])
def test_terminal_replay_requires_complete_exact_durable_bundle(corruption) -> None:
    command, aggregates, key = _review_replay()
    _corrupt_replay(corruption, command, aggregates)
    harness = Harness()
    harness.results["review"]["findings"].append(dict(
        harness.results["review"]["findings"][0], finding_id="rfn_2"
    ))
    harness.terminal_replay = {"stage": "review", "command": command,
                               "aggregates": aggregates, "key": key}
    harness.service._worker_factory = lambda task: ReplaySeedingWorker(harness, task.name)

    result = asyncio.run(harness.run())

    assert result.diagnostic.code == "stage_bundle_persistence_failed"
    assert result.diagnostic.cause == "terminal execution bundle did not commit"
    assert harness.started_tasks == ["implementation", "review"]
    assert len(result.evidence) == 1 and len(result.handoffs) == 1


def test_terminal_replay_accepts_only_the_complete_exact_durable_bundle() -> None:
    command, aggregates, key = _review_replay()
    harness = Harness()
    harness.results["review"]["findings"].append(dict(
        harness.results["review"]["findings"][0], finding_id="rfn_2"
    ))
    harness.terminal_replay = {"stage": "review", "command": command,
                               "aggregates": aggregates, "key": key}
    harness.service._worker_factory = lambda task: ReplaySeedingWorker(harness, task.name)

    result = asyncio.run(harness.run())

    assert result.diagnostic is None
    assert harness.started_tasks == ["implementation", "review", "revision", "acceptance"]
    assert tuple(item.evidence_id for item in result.evidence[1:3]) == tuple(
        command["evidence_ids"]
    )
