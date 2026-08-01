"""Leader 精修回炉任务(`plan rework --refine`)—— Task 1:纯推导 + writer override。

Spec: docs/superpowers/specs/2026-08-01-leader-refined-rework-design.md
本文件只覆盖纯模块(prompt 构造 + 产出校验)与 locked writer 的 override
接线;provider 方法与 CLI `--refine` 是后续 task。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentdeck.review_iteration import (
    MAX_REWORK_TASK_CHARS,
    REFINE_SKIP_REASONS,
    REVIEW_ITERATION_ORIGIN,
    REWORK_TASK_FOOTER,
    build_refine_prompt,
    derive_review_iteration,
    validate_refined_task,
)

from test_review_iteration import _seed_store, _verdict  # noqa: E402


def _members() -> list[dict]:
    return [
        {
            "agent_id": "reviewer",
            "step": 2,
            "reply_id": "rg0",
            "verdict": _verdict(
                "fail",
                criteria=[
                    {"criterion": "tests pass", "verdict": "fail", "evidence": "2 failing"},
                    {"criterion": "a11y kept", "verdict": "pass"},
                ],
            ),
            "text": "reviewer 的原始回复正文",
        },
        {
            "agent_id": "planner",
            "step": 3,
            "reply_id": "rg1",
            "verdict": _verdict("pass", criteria=[{"criterion": "scope", "verdict": "pass"}]),
            "text": "planner 觉得没问题",
        },
    ]


# --------------------------------------------------------------------------
# 纯模块:prompt 构造
# --------------------------------------------------------------------------


def test_refine_skip_reasons_is_the_closed_enum() -> None:
    assert REFINE_SKIP_REASONS == (
        "provider_error",
        "invalid_output",
        "state_changed",
        "unsupported_provider",
    )


def test_build_refine_prompt_carries_task_criteria_and_signed_sections() -> None:
    prompt = build_refine_prompt(
        original_task="build the widget",
        verdict=_verdict("fail"),
        members=_members(),
    )
    assert "build the widget" in prompt
    # 每条 fail 标准与其证据
    assert "tests pass" in prompt
    assert "2 failing" in prompt
    # 每位**非 pass** reviewer 的署名段(pass 成员不进 prompt)
    assert "### reviewer reviewer" in prompt
    assert "reviewer 的原始回复正文" in prompt
    assert "planner 觉得没问题" not in prompt


def test_build_refine_prompt_never_asks_for_json() -> None:
    """产出是**纯文本**返工任务;prompt 绝不能诱导模型输出 JSON
    (否则校验必然不合格,精修永远回落模板)。"""
    prompt = build_refine_prompt(
        original_task="build the widget",
        verdict=_verdict("fail"),
        members=_members(),
    )
    assert "JSON" not in prompt and "json" not in prompt


def test_build_refine_prompt_without_group_members() -> None:
    """无组(单 reviewer)路径:调用方合成单成员列表;members 为空也不炸。"""
    prompt = build_refine_prompt(
        original_task="build the widget", verdict=_verdict("fail"), members=None
    )
    assert "build the widget" in prompt
    assert "tests pass" in prompt


def test_build_refine_prompt_is_pure_text_and_deterministic() -> None:
    args = dict(original_task="t", verdict=_verdict("fail"), members=_members())
    assert build_refine_prompt(**args) == build_refine_prompt(**args)
    assert isinstance(build_refine_prompt(**args), str)


# --------------------------------------------------------------------------
# 纯模块:产出校验
# --------------------------------------------------------------------------


def test_validate_refined_task_appends_the_fixed_commit_instruction() -> None:
    out = validate_refined_task("修 A、修 B")
    assert out.startswith("修 A、修 B")
    assert out.rstrip().endswith(REWORK_TASK_FOOTER)
    assert REWORK_TASK_FOOTER == "修复后 commit 到任务分支。"


def test_validate_refined_task_does_not_double_append_footer() -> None:
    out = validate_refined_task(f"修 A、修 B\n{REWORK_TASK_FOOTER}")
    assert out.count(REWORK_TASK_FOOTER) == 1


@pytest.mark.parametrize("bad", ["", "   \n\t ", None, 42, b"bytes", ["x"], {"a": 1}])
def test_validate_refined_task_rejects_empty_or_non_str(bad) -> None:
    with pytest.raises(ValueError):
        validate_refined_task(bad)


def test_validate_refined_task_rejects_over_budget_text() -> None:
    with pytest.raises(ValueError):
        validate_refined_task("x" * (MAX_REWORK_TASK_CHARS + 1))
    # 上限按**追加收尾指令后的最终文本**计:回炉任务的长度不变量是对落地
    # 文本的,不是对模型原文的(fail-closed)。
    with pytest.raises(ValueError):
        validate_refined_task("x" * MAX_REWORK_TASK_CHARS)
    ok = validate_refined_task("x" * (MAX_REWORK_TASK_CHARS - len(REWORK_TASK_FOOTER) - 1))
    assert len(ok) <= MAX_REWORK_TASK_CHARS


# --------------------------------------------------------------------------
# writer:override 接线
# --------------------------------------------------------------------------


def _events(root: Path) -> list[dict]:
    path = root / ".agentdeck" / "state" / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _rework_event(root: Path) -> dict:
    return [e for e in _events(root) if e["event_type"] == "plan_rework_appended"][-1]


def _appended(store) -> list[dict]:
    steps = store.load()["plans"][0]["plan"]["steps"]
    return [s for s in steps if s.get("origin") == REVIEW_ITERATION_ORIGIN]


def test_writer_uses_override_when_reply_matches(tmp_path) -> None:
    root, store = _seed_store(tmp_path, "fail")
    result = store.append_review_iteration(
        "pln_1",
        2,
        source="explicit",
        rework_task_override="精修后的返工任务",
        override_for_reply="rep_new",
    )
    assert result["ok"] is True
    assert result["refined"] is True
    assert "refine_skipped_reason" not in result

    rework = _appended(store)[0]
    assert rework["task"] == "精修后的返工任务"
    assert rework["task_source"] == "leader_refined"
    # 复审步永不带该键(复审任务逐字节复用原 review step)
    assert "task_source" not in _appended(store)[1]
    # 派发读的是 approval.task —— 精修文本必须一并落到审批项
    approvals = [a for a in store.load()["approvals"] if a["approval_id"] in result["approval_ids"]]
    assert approvals[0]["task"] == "精修后的返工任务"

    event = _rework_event(root)
    assert event["payload"]["refined"] is True
    assert "refine_skipped_reason" not in event["payload"]


def test_writer_falls_back_to_template_when_state_drifted(tmp_path) -> None:
    """锁外预览与锁内推导之间状态漂移(override_for_reply 不匹配)→
    用模板并如实报告,绝不把针对旧 verdict 的精修文本贴到新一轮上。"""
    root, store = _seed_store(tmp_path, "fail")
    result = store.append_review_iteration(
        "pln_1",
        2,
        source="explicit",
        rework_task_override="针对旧 verdict 的精修文本",
        override_for_reply="rep_stale",
    )
    assert result["ok"] is True
    assert result["refined"] is False
    assert result["refine_skipped_reason"] == "state_changed"

    rework = _appended(store)[0]
    assert "针对旧 verdict 的精修文本" not in rework["task"]
    assert "task_source" not in rework
    assert rework["task"].rstrip().endswith(REWORK_TASK_FOOTER)  # 模板版

    event = _rework_event(root)
    assert event["payload"]["refined"] is False
    assert event["payload"]["refine_skipped_reason"] == "state_changed"


def test_writer_treats_missing_override_for_reply_as_drift(tmp_path) -> None:
    _root, store = _seed_store(tmp_path, "fail")
    result = store.append_review_iteration(
        "pln_1", 2, source="explicit", rework_task_override="精修文本"
    )
    assert result["refined"] is False
    assert result["refine_skipped_reason"] == "state_changed"
    assert "精修文本" not in _appended(store)[0]["task"]


def test_writer_without_override_is_byte_identical_to_template(tmp_path) -> None:
    """回归钉:不传 override 时状态与今天逐字节一致——追加的 step 必须
    等于纯推导的输出(无 `task_source` 键、任务文本一字不差)。"""
    _root, store = _seed_store(tmp_path, "fail")
    expected = derive_review_iteration(store.load(), "pln_1", 2, None)

    result = store.append_review_iteration("pln_1", 2, source="explicit")

    assert result["refined"] is False
    assert "refine_skipped_reason" not in result
    appended = _appended(store)
    assert appended[0] == expected["rework_step"]
    assert appended[1:] == list(expected["review_steps"])
    assert "task_source" not in appended[0]


def test_writer_refusal_carries_no_refine_keys(tmp_path) -> None:
    """拒绝路径零写、形状不变(既有断言 `result == {ok, reason}` 的钉子)。"""
    _root, store = _seed_store(tmp_path, "pass")
    assert store.append_review_iteration(
        "pln_1", 2, source="explicit", rework_task_override="x", override_for_reply="rep_new"
    ) == {"ok": False, "reason": "verdict_pass"}


def test_writer_rejects_malformed_override_zero_write(tmp_path) -> None:
    """override 必须是调用方已 `validate_refined_task` 过的文本;半坏输入
    是编程错误,fail-closed 抛错且零写(绝不静默落地半坏任务)。"""
    root, store = _seed_store(tmp_path, "fail")
    before = store.load()
    events_before = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    for bad in ("", "   ", 42, "y" * (MAX_REWORK_TASK_CHARS + 1)):
        with pytest.raises(ValueError):
            store.append_review_iteration(
                "pln_1", 2, source="explicit",
                rework_task_override=bad, override_for_reply="rep_new",
            )
    assert store.load() == before
    assert (root / ".agentdeck" / "state" / "events.jsonl").read_text(
        encoding="utf-8"
    ) == events_before


def test_override_is_keyword_only(tmp_path) -> None:
    """位置参数绝不能滑进 override 槽位(既有四参调用点必须原样安全)。"""
    _root, store = _seed_store(tmp_path, "fail")
    with pytest.raises(TypeError):
        store.append_review_iteration("pln_1", 2, "explicit", None, "精修文本", "rep_new")


def test_writer_never_reaches_a_provider() -> None:
    """spec 硬约束:writer 永不调用 provider(锁内绝不做秒级 I/O)。

    ①纯模块不 import 任何 provider;②locked writer 的方法体里没有
    provider 调用——精修文本只能由锁外调用方以 override 注入。"""
    import ast
    import inspect
    import textwrap

    import agentdeck.review_iteration as pure
    from agentdeck.state import StateStore

    tree = ast.parse(Path(pure.__file__).read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
            imported += [f"{node.module or ''}.{alias.name}" for alias in node.names]
    assert not any("provider" in name for name in imported), imported

    writer = inspect.unwrap(StateStore.append_review_iteration)
    body = ast.parse(textwrap.dedent(inspect.getsource(writer)))
    identifiers = [
        node.id if isinstance(node, ast.Name) else node.attr
        for node in ast.walk(body)
        if isinstance(node, (ast.Name, ast.Attribute))
    ]
    assert not any("provider" in name.lower() for name in identifiers), identifiers
