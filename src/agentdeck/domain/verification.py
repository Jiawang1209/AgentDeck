"""AgentDeck-owned deterministic grading of durable Evidence facts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from agentdeck.domain.mission import TaskRuntimeState, TaskSpec


_MAX_TEXT_BYTES = 4 * 1024
_MAX_VERIFICATION_BYTES = 64 * 1024
_MAX_VERIFICATION_FACTS = 2_048


def _valid_text(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        return len(value.encode("utf-8")) <= _MAX_TEXT_BYTES
    except UnicodeEncodeError:
        return False


class VerificationGrade(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class EvidenceFact:
    criterion: str
    fact: str
    reason: str

    def __post_init__(self) -> None:
        if (
            not _valid_text(self.criterion)
            or self.fact not in {"check_passed", "check_failed"}
            or not _valid_text(self.reason)
        ):
            raise ValueError("verification evidence invalid")


@dataclass(frozen=True, slots=True)
class CriterionResult:
    criterion: str
    mandatory: bool
    grade: VerificationGrade
    reason: str

    def __post_init__(self) -> None:
        if not (
            _valid_text(self.criterion)
            and type(self.mandatory) is bool
            and type(self.grade) is VerificationGrade
            and _valid_text(self.reason)
        ):
            raise ValueError("verification result invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "criterion": self.criterion,
            "mandatory": self.mandatory,
            "grade": self.grade.value,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class VerificationResult:
    task_id: str
    criteria: tuple[CriterionResult, ...]
    aggregate_state: TaskRuntimeState

    def __post_init__(self) -> None:
        if (
            not _valid_text(self.task_id)
            or not self.criteria
            or not all(type(item) is CriterionResult for item in self.criteria)
            or self.aggregate_state
            not in {
                TaskRuntimeState.COMPLETED,
                TaskRuntimeState.FAILED,
                TaskRuntimeState.PAUSED,
            }
        ):
            raise ValueError("verification result invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "criteria": [item.to_dict() for item in self.criteria],
            "aggregate_state": self.aggregate_state.value,
        }


def verify_task(
    task: TaskSpec,
    evidence: tuple[EvidenceFact, ...],
) -> VerificationResult:
    """Grade every frozen mandatory criterion from closed durable facts only."""

    if type(task) is not TaskSpec or not isinstance(evidence, tuple) or not all(
        type(item) is EvidenceFact for item in evidence
    ):
        raise ValueError("verification input invalid")
    if len(evidence) > _MAX_VERIFICATION_FACTS or sum(
        len(item.criterion.encode("utf-8"))
        + len(item.fact.encode("utf-8"))
        + len(item.reason.encode("utf-8"))
        for item in evidence
    ) > _MAX_VERIFICATION_BYTES:
        raise ValueError("verification input invalid")
    known = set(task.acceptance_criteria)
    if any(item.criterion not in known for item in evidence):
        raise ValueError("verification input invalid")
    if any(
        sum(
            len(item.reason.encode("utf-8")) + 2
            for item in evidence
            if item.criterion == criterion
        )
        > _MAX_TEXT_BYTES
        for criterion in task.acceptance_criteria
    ):
        raise ValueError("verification input invalid")

    results: list[CriterionResult] = []
    for criterion in task.acceptance_criteria:
        matches = tuple(item for item in evidence if item.criterion == criterion)
        failed = tuple(item for item in matches if item.fact == "check_failed")
        passed = tuple(item for item in matches if item.fact == "check_passed")
        if failed:
            results.append(
                CriterionResult(
                    criterion,
                    True,
                    VerificationGrade.FAIL,
                    "; ".join(item.reason for item in failed),
                )
            )
        elif passed:
            results.append(
                CriterionResult(
                    criterion,
                    True,
                    VerificationGrade.PASS,
                    "; ".join(item.reason for item in passed),
                )
            )
        else:
            results.append(
                CriterionResult(
                    criterion,
                    True,
                    VerificationGrade.UNAVAILABLE,
                    "durable evidence unavailable",
                )
            )

    grades = {item.grade for item in results}
    aggregate = (
        TaskRuntimeState.FAILED
        if VerificationGrade.FAIL in grades
        else TaskRuntimeState.PAUSED
        if VerificationGrade.UNAVAILABLE in grades
        else TaskRuntimeState.COMPLETED
    )
    return VerificationResult(task.task_id, tuple(results), aggregate)


__all__ = [
    "CriterionResult",
    "EvidenceFact",
    "VerificationGrade",
    "VerificationResult",
    "verify_task",
]
