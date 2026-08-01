import ast
from pathlib import Path

import pytest

from agentdeck.frontdesk import (
    FRONTDESK_CANDIDATE_FIELDS,
    FRONTDESK_CONFIDENCES,
    FRONTDESK_ROUTE_SAFETY,
    FRONTDESK_ROUTES,
    classify_frontdesk,
    frontdesk_goal,
)


def routes_of(message: str) -> list[str]:
    return [item["route"] for item in classify_frontdesk(message)]


def candidate_for(message: str, route: str) -> dict:
    for item in classify_frontdesk(message):
        if item["route"] == route:
            return item
    raise AssertionError(f"route {route} not classified for message {message!r}")


def test_frontdesk_routes_is_a_closed_enum() -> None:
    assert FRONTDESK_ROUTES == ("plan", "run", "status", "help", "skill", "memory")


def test_frontdesk_confidence_is_a_small_deterministic_enum() -> None:
    assert FRONTDESK_CONFIDENCES == ("high", "medium", "low")


def test_frontdesk_candidate_fields_are_frozen() -> None:
    assert FRONTDESK_CANDIDATE_FIELDS == ("route", "label", "command", "confidence", "rationale")


def test_frontdesk_route_safety_covers_every_route_with_known_levels() -> None:
    assert set(FRONTDESK_ROUTE_SAFETY) == set(FRONTDESK_ROUTES)
    assert FRONTDESK_ROUTE_SAFETY["plan"] == "plan_only"
    assert FRONTDESK_ROUTE_SAFETY["run"] == "approval_gated"
    for route in ("status", "help", "skill", "memory"):
        assert FRONTDESK_ROUTE_SAFETY[route] == "inspect"


def test_frontdesk_goal_strips_frontdesk_prefixes() -> None:
    assert frontdesk_goal("frontdesk 帮我梳理多 Agent 分层开发") == "帮我梳理多 Agent 分层开发"
    assert frontdesk_goal("/frontdesk: Build a smoke test") == "Build a smoke test"
    assert frontdesk_goal("前台接待 设计消息账本") == "设计消息账本"
    assert frontdesk_goal("梳理需求") == ""
    assert frontdesk_goal("frontdesk") == ""
    assert frontdesk_goal("   ") == ""


def test_every_candidate_exposes_exactly_the_frozen_fields() -> None:
    for message in [
        "",
        "frontdesk",
        "frontdesk 帮我规划多 Agent 分层开发",
        "开始运行 冒烟测试",
        "现在什么状态",
        "我想加个 skill",
        "记住这个决定",
        "帮助",
    ]:
        candidates = classify_frontdesk(message)
        assert candidates, message
        for candidate in candidates:
            assert tuple(candidate) == FRONTDESK_CANDIDATE_FIELDS
            assert candidate["route"] in FRONTDESK_ROUTES
            assert candidate["confidence"] in FRONTDESK_CONFIDENCES
            assert isinstance(candidate["label"], str) and candidate["label"]
            assert isinstance(candidate["command"], str) and candidate["command"]
            assert isinstance(candidate["rationale"], str) and candidate["rationale"]


def test_plan_route_is_high_on_explicit_planning_wording() -> None:
    candidate = candidate_for("frontdesk 帮我规划多 Agent 分层开发", "plan")
    assert candidate["confidence"] == "high"
    assert candidate["command"] == "agentdeck leader plan --task '帮我规划多 Agent 分层开发'"
    assert candidate["label"] == "Create Leader plan"
    assert candidate["rationale"] == 'matched "规划"'


def test_plan_route_is_medium_when_only_goal_text_is_present() -> None:
    candidate = candidate_for("frontdesk 帮我梳理多 Agent 分层开发", "plan")
    assert candidate["confidence"] == "medium"
    assert candidate["command"] == "agentdeck leader plan --task '帮我梳理多 Agent 分层开发'"
    assert candidate["rationale"] == "goal text present without an explicit planning keyword"


def test_plan_route_is_absent_without_goal_text() -> None:
    assert "plan" not in routes_of("frontdesk")
    assert "plan" not in routes_of("梳理需求")


def test_run_route_needs_both_start_tone_and_goal_text() -> None:
    strong = candidate_for("开始运行 冒烟测试", "run")
    assert strong["confidence"] == "high"
    assert strong["command"] == "agentdeck run --task '开始运行 冒烟测试'"
    assert strong["label"] == "Start approval-gated run"
    assert strong["rationale"] == 'matched "开始运行"'

    weak = candidate_for("跑一下 冒烟测试", "run")
    assert weak["confidence"] == "medium"
    assert weak["rationale"] == 'matched "跑一下"'

    # start tone with no extractable goal never produces a run candidate
    assert "run" not in routes_of("frontdesk")
    # goal text with no start tone never produces a run candidate
    assert "run" not in routes_of("frontdesk 帮我规划多 Agent 分层开发")


def test_status_route_strong_and_weak_hits() -> None:
    strong = candidate_for("现在什么状态", "status")
    assert strong["confidence"] == "high"
    assert strong["command"] == "agentdeck status"
    assert strong["label"] == "Inspect project status"
    assert strong["rationale"] == 'matched "状态"'

    weak = candidate_for("这个项目现在怎么样", "status")
    assert weak["confidence"] == "medium"
    assert weak["rationale"] == 'matched "现在怎么样"'


def test_skill_route_strong_and_weak_hits() -> None:
    strong = candidate_for("我想加个 skill", "skill")
    assert strong["confidence"] == "high"
    assert strong["command"] == "agentdeck skills list"
    assert strong["label"] == "List skills"
    assert strong["rationale"] == 'matched "skill"'

    assert candidate_for("看看技能有哪些", "skill")["rationale"] == 'matched "技能"'
    weak = candidate_for("固化一条工作流", "skill")
    assert weak["confidence"] == "medium"
    assert weak["rationale"] == 'matched "工作流"'


def test_memory_route_strong_and_weak_hits() -> None:
    strong = candidate_for("记住这个决定", "memory")
    assert strong["confidence"] == "high"
    assert strong["command"] == "agentdeck memory suggestions"
    assert strong["label"] == "Review memory suggestions"
    assert strong["rationale"] == 'matched "记住"'

    weak = candidate_for("别忘了这条约定", "memory")
    assert weak["confidence"] == "medium"
    assert weak["rationale"] == 'matched "别忘了"'


def test_help_route_strong_and_weak_hits() -> None:
    strong = candidate_for("帮助", "help")
    assert strong["confidence"] == "high"
    assert strong["command"] == 'agentdeck leader chat --message "帮助"'
    assert strong["label"] == "Open Leader help"
    assert strong["rationale"] == 'matched "帮助"'

    weak = candidate_for("这个东西怎么用", "help")
    assert weak["confidence"] == "medium"
    assert weak["rationale"] == 'matched "怎么用"'


def test_help_is_the_low_confidence_fallback_when_nothing_else_matches() -> None:
    fallback = classify_frontdesk("frontdesk")
    assert fallback == [
        {
            "route": "help",
            "label": "Open Leader help",
            "command": 'agentdeck leader chat --message "帮助"',
            "confidence": "low",
            "rationale": "no route keyword matched",
        }
    ]


def test_empty_message_returns_only_the_help_fallback() -> None:
    for message in ["", "   ", "\n"]:
        assert classify_frontdesk(message) == [
            {
                "route": "help",
                "label": "Open Leader help",
                "command": 'agentdeck leader chat --message "帮助"',
                "confidence": "low",
                "rationale": "no route keyword matched",
            }
        ]


def test_help_fallback_is_absent_when_another_route_matches() -> None:
    assert "help" not in routes_of("frontdesk 帮我规划多 Agent 分层开发")
    assert "help" not in routes_of("现在什么状态")


def test_candidates_are_sorted_by_confidence_descending() -> None:
    # 开始运行 is a strong run marker; the goal text alone only yields a medium plan
    assert routes_of("开始运行 冒烟测试") == ["run", "plan"]
    assert routes_of("现在什么状态") == ["status", "plan"]
    assert routes_of("我想加个 skill") == ["skill", "plan"]
    assert routes_of("记住这个决定") == ["memory", "plan"]

    ranks = {"high": 0, "medium": 1, "low": 2}
    for message in ["开始运行 冒烟测试", "帮我规划一下并记住这个决定", "看看技能和状态"]:
        confidences = [ranks[item["confidence"]] for item in classify_frontdesk(message)]
        assert confidences == sorted(confidences), message


def test_confidence_ties_break_on_the_declared_route_order() -> None:
    # both plan and run land on medium here, so the FRONTDESK_ROUTES order decides
    assert routes_of("跑一下 冒烟测试") == ["plan", "run"]
    # status/skill/memory all strong: declared order is status, skill, memory
    assert routes_of("看看状态、技能和记忆") == ["status", "skill", "memory", "plan"]


def test_multiple_strong_routes_all_appear_once() -> None:
    candidates = classify_frontdesk("规划一下并记住这个决定")
    routes = [item["route"] for item in candidates]
    assert routes == ["plan", "memory"]
    assert len(routes) == len(set(routes))


def test_rationale_only_names_a_token_that_occurs_in_the_message() -> None:
    for message in [
        "开始运行 冒烟测试",
        "现在什么状态",
        "我想加个 skill",
        "记住这个决定",
        "帮助",
        "跑一下 冒烟测试",
    ]:
        for candidate in classify_frontdesk(message):
            rationale = candidate["rationale"]
            if not rationale.startswith('matched "'):
                assert rationale in (
                    "goal text present without an explicit planning keyword",
                    "no route keyword matched",
                )
                continue
            token = rationale[len('matched "') : -1]
            assert token
            assert token in message.lower(), (message, token)


def test_classification_is_deterministic_across_repeated_calls() -> None:
    message = "开始运行 冒烟测试并记住这个决定"
    first = classify_frontdesk(message)
    for _ in range(5):
        assert classify_frontdesk(message) == first


def test_classification_never_mutates_shared_state_between_calls() -> None:
    first = classify_frontdesk("现在什么状态")
    first[0]["route"] = "tampered"
    second = classify_frontdesk("现在什么状态")
    assert second[0]["route"] == "status"


def test_frontdesk_module_is_pure_with_no_agentdeck_or_io_imports() -> None:
    source_path = Path(__file__).resolve().parents[1] / "src" / "agentdeck" / "frontdesk.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
            if node.level:  # relative import => sibling agentdeck module
                imported.append(f".{node.module or ''}")

    assert imported, "expected at least the stdlib imports the module needs"
    for name in imported:
        assert not name.startswith("."), f"pure module must not import siblings: {name}"
        root = name.split(".")[0]
        assert root not in {"agentdeck", "cli", "state", "config", "contracts"}, name
        assert root in {"__future__", "re", "shlex"}, name

    # no filesystem / network / subprocess / env access anywhere in the module
    forbidden = {"open", "input", "print", "exec", "eval"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden, node.func.id


@pytest.mark.parametrize("route", FRONTDESK_ROUTES)
def test_every_declared_route_is_reachable(route: str) -> None:
    messages = {
        "plan": "frontdesk 帮我规划多 Agent 分层开发",
        "run": "开始运行 冒烟测试",
        "status": "现在什么状态",
        "help": "帮助",
        "skill": "我想加个 skill",
        "memory": "记住这个决定",
    }
    assert route in routes_of(messages[route])
