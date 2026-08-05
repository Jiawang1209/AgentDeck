"""这句话是在**问**,还是在**派活**。

2026-08-05 live:user 在 GUI 对话窗里问"你是什么模型？",拿回来的是一份项目
状态汇报。`leader chat` 是意图路由器,认不出的说法就落到兜底路径上(有 plan
就 review、没 plan 就造 plan)——于是一个问题被回答成了一份进度报告。这是本
仓库一直在消灭的那一类:把一件不成立的事说成成立。

判据**刻意窄**,因为判错的代价不对称:

  漏判(问句被当成任务)  → 回到今天的行为,没有回归。
  误判(任务被当成问句)  → "帮我把测试跑绿"不再生成计划,主线断掉。

所以这里只认**明确的疑问标记**——句尾问号,或句尾的"吗/呢"——绝不猜测意图。
"怎么把测试跑绿"形式上是疑问、意图上是任务,它**不该**被判成问句;这类形式
与意图分叉的说法一律留给既有路径。

纯模块:零 IO、零 LLM,不 import cli/state/config。它只回答"这句话像不像在
提问",不决定任何人有没有权限做任何事。
"""
from __future__ import annotations


# 句尾问号。半角与全角都算,后面允许残留空白。
_QUESTION_MARKS = ("?", "？")

# 句尾语气词:现代汉语里这两个字收尾几乎只用于发问。
# 刻意不收"吧"(祈使/征询,"帮我跑一下吧"是派活)、不收"啊/呀"(语气,不是疑问)。
_QUESTION_PARTICLES = ("吗", "呢")


def looks_like_question(text: str) -> bool:
    """这句话是否带有明确的疑问标记。

    只看**句尾**:中间出现问号(引用、URL、示例命令)不代表这句话在提问,
    否则 "把 `curl 'x?y=1'` 的输出写进 README" 会被误判成问句。
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.endswith(_QUESTION_MARKS):
        return True
    # 语气词收尾时可能带标点("能跑吗。"),先剥掉尾部标点再看。
    trailing = stripped.rstrip("。.!！~ ")
    return trailing.endswith(_QUESTION_PARTICLES)
