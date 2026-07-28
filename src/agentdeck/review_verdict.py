"""G5 quantified review verdict schema, reply-line parser, and alignment.

A review-step worker may emit one `verdict: <single-line JSON>` line inside
its structured reply. AgentDeck only parses, validates, stores, and displays
that semantic judgement — it never scores work itself, and a verdict never
changes gate/approval/merge behavior. Replies without a verdict line keep
today's byte-identical path; an invalid verdict must not block reply
ingestion (callers record `review_verdict_invalid` and keep the reply).
See docs/superpowers/specs/2026-07-28-g5-quantified-review-design.md.
"""

from __future__ import annotations

import json
from json import JSONDecodeError
from typing import Any

REVIEW_VERDICT_SCHEMA_VERSION = "review-verdict/v1"
REVIEW_VERDICT_REQUIRED_FIELDS = ("schema_version", "criteria", "overall")
REVIEW_VERDICT_OPTIONAL_FIELDS = ("score", "notes")
REVIEW_VERDICT_CRITERION_VERDICTS = frozenset({"pass", "fail", "unknown"})
REVIEW_VERDICT_OVERALL_VALUES = frozenset({"pass", "fail", "needs_changes"})
_VERDICT_LINE_PREFIX = "verdict:"


def _invalid() -> ValueError:
    return ValueError("review verdict is invalid")


def validate_review_verdict(payload: object) -> dict[str, Any]:
    if type(payload) is not dict:
        raise _invalid()
    allowed = set(REVIEW_VERDICT_REQUIRED_FIELDS) | set(REVIEW_VERDICT_OPTIONAL_FIELDS)
    if not set(REVIEW_VERDICT_REQUIRED_FIELDS) <= set(payload) or set(payload) - allowed:
        raise _invalid()
    if payload["schema_version"] != REVIEW_VERDICT_SCHEMA_VERSION:
        raise _invalid()
    criteria = payload["criteria"]
    if type(criteria) is not list or not criteria:
        raise _invalid()
    for item in criteria:
        if type(item) is not dict:
            raise _invalid()
        if not {"criterion", "verdict"} <= set(item) or set(item) - {
            "criterion",
            "verdict",
            "evidence",
        }:
            raise _invalid()
        criterion = item["criterion"]
        if type(criterion) is not str or not criterion.strip():
            raise _invalid()
        if item["verdict"] not in REVIEW_VERDICT_CRITERION_VERDICTS:
            raise _invalid()
        if "evidence" in item and (
            type(item["evidence"]) is not str or not item["evidence"].strip()
        ):
            raise _invalid()
    if payload["overall"] not in REVIEW_VERDICT_OVERALL_VALUES:
        raise _invalid()
    if "score" in payload:
        score = payload["score"]
        if type(score) is not int or score < 0 or score > 100:
            raise _invalid()
    if "notes" in payload:
        notes = payload["notes"]
        if type(notes) is not str or not notes.strip():
            raise _invalid()
    return {key: payload[key] for key in payload}


def parse_verdict_line(text: str) -> dict[str, Any] | None:
    if type(text) is not str:
        raise _invalid()
    verdict_lines = [
        stripped[len(_VERDICT_LINE_PREFIX) :].strip()
        for line in text.splitlines()
        if (stripped := line.strip()).startswith(_VERDICT_LINE_PREFIX)
    ]
    if not verdict_lines:
        return None
    if len(verdict_lines) != 1:
        raise _invalid()
    try:
        payload = json.loads(verdict_lines[0])
    except JSONDecodeError:
        raise _invalid() from None
    return validate_review_verdict(payload)


def align_verdict_with_criteria(
    verdict: dict[str, Any], acceptance_criteria: list[str] | None
) -> dict[str, Any]:
    validated = validate_review_verdict(verdict)
    criteria = validated["criteria"]
    verdict_texts = [item["criterion"] for item in criteria]
    counts = {"pass": 0, "fail": 0, "unknown": 0}
    for item in criteria:
        counts[item["verdict"]] += 1
    if acceptance_criteria is None:
        unverified: list[str] = []
        extra: list[str] = []
    else:
        unverified = [
            criterion
            for criterion in acceptance_criteria
            if criterion not in verdict_texts
        ]
        extra = [
            criterion
            for criterion in verdict_texts
            if criterion not in acceptance_criteria
        ]
    return {
        "criteria_total": len(criteria),
        "passed": counts["pass"],
        "failed": counts["fail"],
        "unknown": counts["unknown"],
        "overall": validated["overall"],
        "score": validated.get("score"),
        "unverified": unverified,
        "extra": extra,
    }
