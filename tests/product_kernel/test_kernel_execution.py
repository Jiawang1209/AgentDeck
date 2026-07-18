from dataclasses import FrozenInstanceError, replace
import json

import pytest

import agentdeck.kernel.execution as execution_module
from agentdeck.kernel.execution import (
    AcceptanceResult,
    Attempt,
    AttemptState,
    Evidence,
    EvidenceKind,
    FindingSeverity,
    Handoff,
    ResultError,
    ReviewFinding,
)


PROHIBITED_RETRY_REASONS = (
    "known_test_failure",
    "permission_denied",
    "scope_insufficiency",
    "login_loss",
    "unexplained_project_drift",
)
NON_ALLOWLIST_RETRY_REASONS = (
    "known_test_failure ",
    " login_loss",
    "KNOWN_TEST_FAILURE",
    "mystery",
)


class HostileCanonicalEquality:
    def __ne__(self, other: object) -> bool:
        return False


class CanonicalStringSubclass(str):
    pass


class EvidenceMembershipProbe(list[str]):
    def __init__(self, values: list[str], calls: list[str], label: str) -> None:
        super().__init__(values)
        self.calls = calls
        self.label = label

    def __contains__(self, value: object) -> bool:
        self.calls.append(self.label)
        return super().__contains__(value)


def test_attempt_states_are_exactly_the_declared_execution_states() -> None:
    assert {state.value for state in AttemptState} == {
        "pending",
        "running",
        "awaiting_approval",
        "human_controlled",
        "completed",
        "failed",
        "cancelled",
        "interrupted",
        "outcome_unknown",
    }


def test_attempt_follows_the_active_execution_lifecycle_immutably() -> None:
    pending = Attempt.pending("att_1", "tsk_1", 1)
    running = pending.start()
    awaiting = running.await_approval()
    resumed = awaiting.resume()
    human = resumed.take_human_control()
    returned = human.resume()
    completed = returned.complete("implementation finished")

    assert pending.state is AttemptState.PENDING
    assert running.state is AttemptState.RUNNING
    assert awaiting.state is AttemptState.AWAITING_APPROVAL
    assert human.state is AttemptState.HUMAN_CONTROLLED
    assert completed.state is AttemptState.COMPLETED
    assert completed.result_summary == "implementation finished"
    assert completed.reason is None
    with pytest.raises(FrozenInstanceError):
        pending.state = AttemptState.RUNNING  # type: ignore[misc]


@pytest.mark.parametrize(
    ("source", "target"),
    (
        (AttemptState.PENDING, AttemptState.COMPLETED),
        (AttemptState.PENDING, AttemptState.AWAITING_APPROVAL),
        (AttemptState.RUNNING, AttemptState.PENDING),
        (AttemptState.AWAITING_APPROVAL, AttemptState.COMPLETED),
        (AttemptState.HUMAN_CONTROLLED, AttemptState.AWAITING_APPROVAL),
    ),
)
def test_attempt_rejects_illegal_nonterminal_transitions(
    source: AttemptState, target: AttemptState
) -> None:
    attempts = {
        AttemptState.PENDING: Attempt.pending("att_1", "tsk_1", 1),
        AttemptState.RUNNING: Attempt.pending("att_1", "tsk_1", 1).start(),
        AttemptState.AWAITING_APPROVAL: Attempt.pending(
            "att_1", "tsk_1", 1
        ).start().await_approval(),
        AttemptState.HUMAN_CONTROLLED: Attempt.pending(
            "att_1", "tsk_1", 1
        ).start().take_human_control(),
    }

    with pytest.raises(ResultError, match="illegal attempt transition"):
        attempts[source].transition(target)


@pytest.mark.parametrize(
    "attempt",
    (
        Attempt.pending("att_1", "tsk_1", 1).start().complete("done"),
        Attempt.pending("att_2", "tsk_1", 1).start().fail("worker_failed"),
        Attempt.pending("att_3", "tsk_1", 1).cancel("user_cancelled"),
        Attempt.pending("att_4", "tsk_1", 1).start().interrupt("safe_exit"),
        Attempt.pending("att_5", "tsk_1", 1).start().unknown_outcome(
            "disconnect_after_side_effect"
        ),
    ),
)
def test_terminal_attempts_cannot_transition(attempt: Attempt) -> None:
    with pytest.raises(ResultError, match="terminal attempt"):
        attempt.transition(AttemptState.RUNNING)


def test_retry_creates_new_attempt_and_preserves_failure() -> None:
    first = Attempt.pending("att_1", "tsk_1", 1).fail("worker_failed")
    second = first.retry("att_2")

    assert first.state is AttemptState.FAILED
    assert first.reason == "worker_failed"
    assert first.retryable is True
    assert second == Attempt.pending("att_2", "tsk_1", 2)


@pytest.mark.parametrize(
    "attempt",
    (
        Attempt.pending("att_1", "tsk_1", 1),
        Attempt.pending("att_1", "tsk_1", 1).start().complete("done"),
        Attempt.pending("att_1", "tsk_1", 1).start().fail(
            "permission_denied", retryable=False
        ),
        Attempt.pending("att_1", "tsk_1", 1).start().fail("known_test_failure"),
        Attempt.pending("att_1", "tsk_1", 1).start().fail("login_loss"),
        Attempt.pending("att_1", "tsk_1", 1).start().unknown_outcome(
            "transport_lost"
        ),
    ),
)
def test_retry_rejects_nonretryable_or_unknown_outcomes(attempt: Attempt) -> None:
    with pytest.raises(ResultError, match="not explicitly retryable"):
        attempt.retry("att_2")


@pytest.mark.parametrize("reason", PROHIBITED_RETRY_REASONS)
def test_fail_rejects_hostile_retryable_true_for_prohibited_causes(
    reason: str,
) -> None:
    running = Attempt.pending("att_1", "tsk_1", 1).start()

    with pytest.raises(ResultError, match="prohibited failure.*retryable"):
        running.fail(reason, retryable=True)


@pytest.mark.parametrize("reason", (*PROHIBITED_RETRY_REASONS, "outcome_unknown"))
def test_direct_attempt_rejects_retryable_prohibited_failure(reason: str) -> None:
    with pytest.raises(ResultError, match="prohibited failure.*retryable"):
        Attempt(
            "att_1",
            "tsk_1",
            1,
            AttemptState.FAILED,
            reason=reason,
            retryable=True,
        )


@pytest.mark.parametrize("reason", NON_ALLOWLIST_RETRY_REASONS)
def test_fail_rejects_retryable_true_outside_exact_positive_allowlist(
    reason: str,
) -> None:
    running = Attempt.pending("att_1", "tsk_1", 1).start()

    with pytest.raises(ResultError, match="failure.*retryable"):
        running.fail(reason, retryable=True)


@pytest.mark.parametrize("reason", NON_ALLOWLIST_RETRY_REASONS)
def test_direct_attempt_rejects_retryable_true_outside_exact_allowlist(
    reason: str,
) -> None:
    with pytest.raises(ResultError, match="failure.*retryable"):
        Attempt(
            "att_1",
            "tsk_1",
            1,
            AttemptState.FAILED,
            reason=reason,
            retryable=True,
        )


@pytest.mark.parametrize("reason", NON_ALLOWLIST_RETRY_REASONS)
def test_non_allowlisted_failure_can_be_recorded_explicitly_nonretryable(
    reason: str,
) -> None:
    failed = Attempt.pending("att_1", "tsk_1", 1).start().fail(
        reason, retryable=False
    )

    assert failed.reason == reason
    assert failed.retryable is False


@pytest.mark.parametrize("reason", PROHIBITED_RETRY_REASONS)
def test_prohibited_failure_defaults_nonretryable_and_cannot_retry(reason: str) -> None:
    failed = Attempt.pending("att_1", "tsk_1", 1).start().fail(reason)

    assert failed.retryable is False
    with pytest.raises(ResultError, match="not explicitly retryable"):
        failed.retry("att_2")


def test_retry_rejects_same_identity_and_ordinal_overflow() -> None:
    failed = Attempt.pending("att_1", "tsk_1", 1).fail("worker_failed")
    overflow = Attempt.pending("att_max", "tsk_1", 2**63 - 1).fail(
        "worker_failed"
    )

    with pytest.raises(ResultError, match="new attempt_id"):
        failed.retry("att_1")
    with pytest.raises(ResultError, match="ordinal"):
        overflow.retry("att_next")


@pytest.mark.parametrize(
    "constructor",
    (
        lambda: Attempt.pending("att_1", "tsk_1", True),
        lambda: Attempt.pending("att_1", "tsk_1", 2**63),
        lambda: Attempt.pending("att_\ud800", "tsk_1", 1),
        lambda: Attempt("att_1", "tsk_1", 1, "running"),
        lambda: Attempt(
            "att_1", "tsk_1", 1, AttemptState.COMPLETED,
            reason="failure", result_summary="done"
        ),
        lambda: Attempt("att_1", "tsk_1", 1, AttemptState.FAILED),
        lambda: Attempt(
            "att_1", "tsk_1", 1, AttemptState.PENDING, retryable=True
        ),
    ),
)
def test_attempt_public_constructors_close_invalid_state_and_type_paths(
    constructor: object,
) -> None:
    with pytest.raises((TypeError, ValueError, ResultError)):
        constructor()  # type: ignore[operator]


def test_evidence_kinds_are_explicit_typed_facts() -> None:
    assert {kind.value for kind in EvidenceKind} == {
        "test_exit_status",
        "diff_identity",
        "artifact_hash",
        "review_finding",
        "acceptance_result",
        "human_decision",
    }


def test_evidence_copies_recursive_json_into_canonical_immutable_payload() -> None:
    payload = {"command": "pytest -q", "exit_status": 0}
    evidence = Evidence.create(
        "ev_test", EvidenceKind.TEST_EXIT_STATUS, payload
    )
    payload["exit_status"] = 1

    assert evidence.kind is EvidenceKind.TEST_EXIT_STATUS
    assert json.loads(evidence.canonical_content) == {
        "command": "pytest -q",
        "exit_status": 0,
    }
    assert hash(evidence)
    with pytest.raises(FrozenInstanceError):
        evidence.kind = EvidenceKind.HUMAN_DECISION  # type: ignore[misc]


def test_evidence_is_deterministic_across_mapping_order() -> None:
    first = Evidence.create(
        "ev_test",
        EvidenceKind.TEST_EXIT_STATUS,
        {"command": "pytest", "exit_status": 0},
    )
    second = Evidence.create(
        "ev_test",
        EvidenceKind.TEST_EXIT_STATUS,
        {"exit_status": 0, "command": "pytest"},
    )

    assert first == second
    assert first.canonical_content == second.canonical_content


@pytest.mark.parametrize(
    ("payload", "error"),
    (
        ({"command": "pytest", "exit_status": True}, TypeError),
        ({"command": "pytest", "exit_status": 2**63}, ValueError),
        ({"command": "pytest", "exit_status": 0, "raw": "model output"}, ResultError),
        ({"command": "pytest", "exit_status": object()}, TypeError),
        ({"command": "\ud800", "exit_status": 0}, ValueError),
        ({1: "not a JSON object key"}, TypeError),
    ),
)
def test_evidence_rejects_noncanonical_or_untyped_payloads(
    payload: object, error: type[Exception]
) -> None:
    with pytest.raises(error):
        Evidence.create("ev_test", EvidenceKind.TEST_EXIT_STATUS, payload)


def test_evidence_rejects_unknown_kind_and_direct_canonical_tampering() -> None:
    evidence = Evidence.create(
        "ev_test",
        EvidenceKind.TEST_EXIT_STATUS,
        {"command": "pytest", "exit_status": 0},
    )

    with pytest.raises(TypeError):
        Evidence.create("ev_test", "test_exit_status", {})  # type: ignore[arg-type]
    with pytest.raises(ResultError):
        replace(evidence, payload="{}")


def test_review_finding_evidence_create_rejects_direct_self_reference() -> None:
    finding = ReviewFinding(
        "rfn_1", "project", FindingSeverity.BLOCKING,
        "self-backed finding", "criterion", ("ev_self",),
    )

    with pytest.raises(ResultError, match="self-reference"):
        Evidence.create(
            "ev_self", EvidenceKind.REVIEW_FINDING, finding.canonical_projection()
        )


def test_review_finding_evidence_direct_constructor_rejects_self_reference() -> None:
    finding = ReviewFinding(
        "rfn_1", "project", FindingSeverity.BLOCKING,
        "self-backed finding", "criterion", ("ev_self",),
    )
    payload = json.dumps(
        finding.canonical_projection(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    with pytest.raises(ResultError, match="self-reference"):
        Evidence("ev_self", EvidenceKind.REVIEW_FINDING, payload)


def self_referencing_acceptance() -> AcceptanceResult:
    return AcceptanceResult.create(
        ("criterion",), {"criterion": ("ev_self",)}, accepted=True
    )


def test_acceptance_evidence_create_rejects_direct_self_reference() -> None:
    result = self_referencing_acceptance()

    with pytest.raises(ResultError, match="self-reference"):
        Evidence.create(
            "ev_self", EvidenceKind.ACCEPTANCE_RESULT, result.canonical_projection()
        )


def test_acceptance_evidence_factory_rejects_direct_self_reference() -> None:
    with pytest.raises(ResultError, match="self-reference"):
        Evidence.acceptance(
            "ev_self",
            result=self_referencing_acceptance(),
            source_kind=EvidenceKind.ACCEPTANCE_RESULT,
        )


def test_acceptance_evidence_direct_constructor_rejects_self_reference() -> None:
    payload = json.dumps(
        self_referencing_acceptance().canonical_projection(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    with pytest.raises(ResultError, match="self-reference"):
        Evidence("ev_self", EvidenceKind.ACCEPTANCE_RESULT, payload)


def test_acceptance_self_reference_scan_is_linear_and_short_circuiting() -> None:
    calls: list[str] = []
    evidence_by_criterion = {
        f"criterion_{index}": EvidenceMembershipProbe(
            ["ev_target"] if index == 0 else [f"ev_{index}"],
            calls,
            f"criterion_{index}",
        )
        for index in range(128)
    }
    payload = {"evidence_by_criterion": evidence_by_criterion}

    helper = getattr(execution_module, "_references_evidence_id")
    assert helper(EvidenceKind.ACCEPTANCE_RESULT, payload, "ev_target") is True
    assert calls == ["criterion_0"]


def test_worker_prose_cannot_satisfy_acceptance() -> None:
    with pytest.raises(ResultError, match="typed evidence"):
        Evidence.acceptance("looks good", source_kind="worker_message")


def test_review_finding_is_scoped_and_evidence_backed() -> None:
    source = ["ev_test", "ev_diff"]
    finding = ReviewFinding(
        finding_id="rfn_1",
        scope="project",
        severity=FindingSeverity.BLOCKING,
        summary="The regression suite fails.",
        criterion="all tests pass",
        evidence_ids=source,
    )
    source.clear()

    assert finding.evidence_ids == ("ev_test", "ev_diff")
    assert finding.severity is FindingSeverity.BLOCKING
    with pytest.raises(FrozenInstanceError):
        finding.scope = "external"  # type: ignore[misc]


@pytest.mark.parametrize(
    "constructor",
    (
        lambda: ReviewFinding(
            "rfn_1", "project", "blocking", "summary", "criterion", ("ev_1",)
        ),
        lambda: ReviewFinding(
            "rfn_1", "project", FindingSeverity.BLOCKING,
            "summary", "criterion", ()
        ),
        lambda: ReviewFinding(
            "rfn_1", "project", FindingSeverity.BLOCKING,
            "summary", "criterion", ("ev_1", "ev_1")
        ),
        lambda: ReviewFinding(
            "rfn_1", "project", FindingSeverity.BLOCKING,
            "\ud800", "criterion", ("ev_1",)
        ),
        lambda: ReviewFinding(
            "rfn_1", "project", FindingSeverity.BLOCKING,
            "summary", "criterion", ("worker says okay",)
        ),
    ),
)
def test_review_finding_rejects_unknown_severity_or_untyped_evidence(
    constructor: object,
) -> None:
    with pytest.raises((TypeError, ValueError, ResultError)):
        constructor()  # type: ignore[operator]


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
