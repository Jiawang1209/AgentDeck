"""问句判定 —— 刻意窄。

2026-08-05 live:user 在 GUI 对话窗里问"你是什么模型？",拿回来的是一份项目
状态汇报。`leader chat` 是意图路由器,认不出的话就落到"有 plan 就 review"
那条兜底路径上——于是**一个问题被回答成了一份进度报告**。这属于本仓库一直
在消灭的那一类:把一件不成立的事说成成立。

判错的代价是不对称的:
  - 漏判(问句被当成任务)→ 回到今天的行为,没有回归。
  - 误判(任务被当成问句)→ "帮我把测试跑绿"不再生成计划,主线断掉。

所以判据只认**明确的疑问标记**,不做意图猜测:句尾问号,或句尾的"吗/呢"。
"怎么把测试跑绿"形式上是疑问、意图上是任务——它**不该**被判成问句,这条
边界有测试钉住。
"""
from __future__ import annotations

import pytest

from agentdeck.conversational import looks_like_question


@pytest.mark.parametrize(
    "text",
    [
        "你是什么模型？",
        "你是什么模型?",
        "What model are you?",
        "这个项目现在能跑吗",
        "这个项目现在能跑吗？",
        "接下来该做什么呢",
        "  为什么会超时？  ",
    ],
)
def test_clear_questions_are_recognized(text: str) -> None:
    assert looks_like_question(text) is True


@pytest.mark.parametrize(
    "text",
    [
        # 主线:任务指令绝不能被当成问句,否则不再生成计划。
        "帮我把测试跑绿",
        "帮我复刻这个网站的首页",
        "开启多智能体编排，codex 做 A，claude 做 B",
        # 形式上疑问、意图上是任务——刻意漏判,回到今天的行为即可。
        "怎么把测试跑绿",
        "如何实现自动 reply extraction",
        "",
        "   ",
    ],
)
def test_tasks_and_ambiguous_phrasing_are_not_questions(text: str) -> None:
    assert looks_like_question(text) is False


def test_non_string_input_is_rejected() -> None:
    with pytest.raises(TypeError):
        looks_like_question(None)  # type: ignore[arg-type]


def test_question_mark_inside_a_task_does_not_count() -> None:
    # 只看句尾:中间出现问号(引用、路径、示例)不代表这句话在提问。
    assert looks_like_question("把 `curl 'x?y=1'` 的输出写进 README") is False
