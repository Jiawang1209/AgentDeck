from __future__ import annotations

import json

import pytest

from agentdeck.review_verdict import (
    REVIEW_VERDICT_SCHEMA_VERSION,
    align_verdict_with_criteria,
    parse_verdict_line,
    validate_review_verdict,
)


def _verdict(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "review-verdict/v1",
        "criteria": [
            {"criterion": "README 包含新命令", "verdict": "pass", "evidence": "L120 新增段落"},
            {"criterion": "测试全绿", "verdict": "fail"},
            {"criterion": "文档链接有效", "verdict": "unknown"},
        ],
        "overall": "needs_changes",
        "score": 62,
        "notes": "测试缺一个用例",
    }
    payload.update(overrides)
    return payload


def test_valid_verdict_passes_and_minimal_verdict_passes() -> None:
    assert validate_review_verdict(_verdict()) == _verdict()
    minimal = {
        "schema_version": REVIEW_VERDICT_SCHEMA_VERSION,
        "criteria": [{"criterion": "只有一条", "verdict": "pass"}],
        "overall": "pass",
    }
    assert validate_review_verdict(minimal) == minimal


@pytest.mark.parametrize(
    "mutate",
    [
        {"schema_version": "review-verdict/v2"},
        {"criteria": []},
        {"criteria": "all pass"},
        {"criteria": [{"criterion": "", "verdict": "pass"}]},
        {"criteria": [{"criterion": "x", "verdict": "maybe"}]},
        {"criteria": [{"criterion": "x", "verdict": "pass", "extra": 1}]},
        {"criteria": [{"criterion": "x", "verdict": "pass", "evidence": ""}]},
        {"overall": "ok"},
        {"score": True},
        {"score": -1},
        {"score": 101},
        {"score": "62"},
        {"notes": ""},
        {"notes": 7},
        {"unexpected": "key"},
    ],
)
def test_invalid_verdicts_fail_closed(mutate: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="review verdict is invalid"):
        validate_review_verdict(_verdict(**mutate))


@pytest.mark.parametrize("missing", ["schema_version", "criteria", "overall"])
def test_missing_required_fields_fail_closed(missing: str) -> None:
    payload = _verdict()
    del payload[missing]
    with pytest.raises(ValueError, match="review verdict is invalid"):
        validate_review_verdict(payload)


def test_non_dict_payload_fails_closed() -> None:
    with pytest.raises(ValueError, match="review verdict is invalid"):
        validate_review_verdict([1, 2])


def test_parse_returns_none_without_verdict_line() -> None:
    text = "status: completed\nsummary: 修复完成\nfull_output_path: out.md\n"
    assert parse_verdict_line(text) is None
    assert parse_verdict_line("") is None


def test_parse_extracts_single_verdict_line_amid_structured_reply() -> None:
    verdict = _verdict()
    text = (
        "status: completed\n"
        "summary: 复核完成\n"
        f"verdict: {json.dumps(verdict, ensure_ascii=False)}\n"
        "full_output_path: review.md\n"
    )
    assert parse_verdict_line(text) == verdict


def test_parse_duplicate_verdict_lines_fail_closed() -> None:
    line = f"verdict: {json.dumps(_verdict(), ensure_ascii=False)}"
    with pytest.raises(ValueError, match="review verdict is invalid"):
        parse_verdict_line(f"{line}\n{line}\n")


@pytest.mark.parametrize(
    "payload_text",
    ["not json", json.dumps(["list"]), json.dumps({"schema_version": "review-verdict/v1"})],
)
def test_parse_bad_verdict_payload_fails_closed(payload_text: str) -> None:
    with pytest.raises(ValueError, match="review verdict is invalid"):
        parse_verdict_line(f"verdict: {payload_text}\n")


def test_align_counts_covered_unverified_and_extra() -> None:
    summary = align_verdict_with_criteria(
        validate_review_verdict(_verdict()),
        ["README 包含新命令", "测试全绿", "性能不回退"],
    )
    assert summary == {
        "criteria_total": 3,
        "passed": 1,
        "failed": 1,
        "unknown": 1,
        "overall": "needs_changes",
        "score": 62,
        "unverified": ["性能不回退"],
        "extra": ["文档链接有效"],
    }


def test_align_without_plan_criteria_degrades_gracefully() -> None:
    summary = align_verdict_with_criteria(validate_review_verdict(_verdict()), None)
    assert summary["criteria_total"] == 3
    assert summary["unverified"] == []
    assert summary["extra"] == []
    assert summary["score"] == 62


def test_align_minimal_verdict_has_null_score() -> None:
    minimal = validate_review_verdict(
        {
            "schema_version": REVIEW_VERDICT_SCHEMA_VERSION,
            "criteria": [{"criterion": "唯一标准", "verdict": "pass"}],
            "overall": "pass",
        }
    )
    summary = align_verdict_with_criteria(minimal, ["唯一标准"])
    assert summary["score"] is None
    assert summary["passed"] == 1
    assert summary["unverified"] == []
    assert summary["extra"] == []
