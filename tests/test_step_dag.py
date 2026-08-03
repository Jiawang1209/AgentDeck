"""Pure-module matrix for the derived step DAG.

See docs/superpowers/specs/2026-08-03-dag-step-dependencies-design.md.
"""
from __future__ import annotations

import pytest

from agentdeck.step_dag import (
    STEP_DAG_RULE,
    ancestors_for,
    dependencies_for,
    derive_step_ancestors,
    derive_step_dependencies,
)


def _linear(count: int) -> list[dict[str, object]]:
    return [{"step": number, "agent_id": f"a{number}"} for number in range(1, count + 1)]


def _member(number: int, group: int, member: int) -> dict[str, object]:
    return {
        "step": number,
        "agent_id": f"r{member}",
        "review_group": group,
        "review_group_member": member,
    }


def test_module_is_pure() -> None:
    """零 IO 的结构性保证:不 import cli/state/config。"""
    import agentdeck.step_dag as module

    source = module.__file__
    text = open(source, encoding="utf-8").read()
    for forbidden in ("from agentdeck.cli", "from agentdeck.state", "from agentdeck.config",
                      "import agentdeck.cli", "import agentdeck.state", "import agentdeck.config"):
        assert forbidden not in text


def test_empty_steps_derive_nothing() -> None:
    assert derive_step_dependencies([]) == {}
    assert derive_step_ancestors([]) == {}


def test_linear_plan_is_a_chain() -> None:
    """普通 step N 依赖 [N-1]——与今天的顺序守卫逐字节等价的那一族。"""
    steps = _linear(3)
    assert derive_step_dependencies(steps) == {1: [], 2: [1], 3: [2]}
    assert derive_step_ancestors(steps) == {1: [], 2: [1], 3: [1, 2]}


def test_review_group_members_fan_out_from_the_step_before_the_group() -> None:
    steps = [*_linear(2), _member(3, 1, 0), _member(4, 1, 1)]
    dependencies = derive_step_dependencies(steps)
    assert dependencies[3] == [2]
    assert dependencies[4] == [2]
    ancestors = derive_step_ancestors(steps)
    assert ancestors[3] == [1, 2]
    assert ancestors[4] == [1, 2]


def test_step_after_a_group_waits_for_every_member() -> None:
    """组之后的普通 step 不能只等编号相邻的那个成员。"""
    steps = [*_linear(2), _member(3, 1, 0), _member(4, 1, 1), {"step": 5, "agent_id": "z"}]
    dependencies = derive_step_dependencies(steps)
    assert dependencies[5] == [3, 4]
    assert derive_step_ancestors(steps)[5] == [1, 2, 3, 4]


def test_group_at_the_head_has_no_dependencies() -> None:
    steps = [_member(1, 1, 0), _member(2, 1, 1), {"step": 3, "agent_id": "z"}]
    dependencies = derive_step_dependencies(steps)
    assert dependencies[1] == []
    assert dependencies[2] == []
    assert dependencies[3] == [1, 2]
    assert derive_step_ancestors(steps)[3] == [1, 2]


def test_multiple_groups_chain_group_to_group() -> None:
    steps = [
        {"step": 1, "agent_id": "c"},
        _member(2, 1, 0), _member(3, 1, 1),
        _member(4, 2, 0), _member(5, 2, 1),
    ]
    dependencies = derive_step_dependencies(steps)
    assert dependencies == {1: [], 2: [1], 3: [1], 4: [2, 3], 5: [2, 3]}
    ancestors = derive_step_ancestors(steps)
    assert ancestors[4] == [1, 2, 3]
    assert ancestors[5] == [1, 2, 3]


def test_malformed_steps_are_ignored_not_guessed() -> None:
    steps = [{"step": 1}, "junk", {"step": None}, {"step": True}, {"step": 2}]
    assert derive_step_dependencies(steps) == {1: [], 2: [1]}


def test_lookup_helpers_fall_back_to_the_linear_chain() -> None:
    """plan step 表里没有的编号回落到旧守卫的等价形状,绝不放宽。"""
    assert dependencies_for({}, 1) == []
    assert dependencies_for({}, 4) == [3]
    assert ancestors_for({}, 1) == []
    assert ancestors_for({}, 4) == [1, 2, 3]
    assert ancestors_for({4: [2]}, 4) == [2]


def test_rule_name_is_a_single_source() -> None:
    assert STEP_DAG_RULE == "linear_plus_review_group_fanout"


@pytest.mark.parametrize("count", [1, 2, 5])
def test_ancestors_of_a_linear_plan_are_every_earlier_step(count: int) -> None:
    ancestors = derive_step_ancestors(_linear(count))
    for number in range(1, count + 1):
        assert ancestors[number] == list(range(1, number))
