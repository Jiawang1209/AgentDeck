from __future__ import annotations

import pytest

from agentdeck.providers.planner_brief import (
    PLANNER_BRIEF_REQUIRED_FIELDS,
    PLANNER_BRIEF_SCHEMA_VERSION,
    build_planner_prompt,
    planner_brief_snapshot,
    validate_planner_brief,
)


def _valid_brief() -> dict[str, object]:
    return {
        "goal": "自动化 README 更新",
        "acceptance_criteria": ["README 包含新命令", "全部测试通过"],
        "risks": ["文档与实现漂移"],
        "macro_steps": ["梳理现有命令", "更新文档", "验证一致性"],
    }


def test_valid_brief_passes_and_returns_same_fields() -> None:
    brief = validate_planner_brief(_valid_brief())
    assert brief == _valid_brief()
    assert PLANNER_BRIEF_REQUIRED_FIELDS == (
        "goal",
        "acceptance_criteria",
        "risks",
        "macro_steps",
    )


def test_empty_risks_list_is_allowed() -> None:
    payload = _valid_brief()
    payload["risks"] = []
    assert validate_planner_brief(payload)["risks"] == []


@pytest.mark.parametrize("missing", PLANNER_BRIEF_REQUIRED_FIELDS)
def test_missing_field_fails_closed(missing: str) -> None:
    payload = _valid_brief()
    del payload[missing]
    with pytest.raises(ValueError, match="planner brief schema is invalid"):
        validate_planner_brief(payload)


def test_unknown_key_fails_closed() -> None:
    payload = _valid_brief()
    payload["steps"] = []
    with pytest.raises(ValueError, match="planner brief schema is invalid"):
        validate_planner_brief(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("goal", ""),
        ("goal", "   "),
        ("goal", 7),
        ("acceptance_criteria", []),
        ("acceptance_criteria", "done"),
        ("acceptance_criteria", ["ok", ""]),
        ("acceptance_criteria", ["ok", 3]),
        ("risks", "none"),
        ("risks", [""]),
        ("macro_steps", []),
        ("macro_steps", ["ok", "  "]),
    ],
)
def test_invalid_field_values_fail_closed(field: str, value: object) -> None:
    payload = _valid_brief()
    payload[field] = value
    with pytest.raises(ValueError, match="planner brief schema is invalid"):
        validate_planner_brief(payload)


def test_non_dict_payload_fails_closed() -> None:
    with pytest.raises(ValueError, match="planner brief schema is invalid"):
        validate_planner_brief(["not", "a", "dict"])


def test_snapshot_is_deterministic_and_hashed() -> None:
    first = planner_brief_snapshot(validate_planner_brief(_valid_brief()))
    second = planner_brief_snapshot(validate_planner_brief(_valid_brief()))
    assert first == second
    assert first["schema_version"] == PLANNER_BRIEF_SCHEMA_VERSION
    for field in PLANNER_BRIEF_REQUIRED_FIELDS:
        assert first[field] == _valid_brief()[field]
    assert isinstance(first["content_hash"], str) and len(first["content_hash"]) == 64

    changed = _valid_brief()
    changed["goal"] = "另一个目标"
    other = planner_brief_snapshot(validate_planner_brief(changed))
    assert other["content_hash"] != first["content_hash"]


def test_prompt_contains_goal_fields_and_no_agent_assignment_rule() -> None:
    prompt = build_planner_prompt("自动化 README 更新")
    assert "自动化 README 更新" in prompt
    for field in PLANNER_BRIEF_REQUIRED_FIELDS:
        assert field in prompt
    assert "JSON" in prompt
    assert "agent" in prompt.lower()


def test_prompt_embeds_compact_skill_context() -> None:
    prompt = build_planner_prompt("目标", skill_context=None)
    assert "Loaded skills" in prompt
