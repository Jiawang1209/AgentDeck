# Leader Refined Rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** `agentdeck plan rework --plan-id <id> --confirm --refine` 用 Leader
provider 把审查意见提炼成回炉任务;失败一律回落确定性模板并如实报告。

**Architecture:** 纯模块出 prompt 与产出校验;provider 侧只加一个
`refine_rework_task(task, feedback, model) -> str` (三处实现覆盖全部
provider:`cli_subprocess` 基类、`openai_compatible` 基类、`fake`);
CLI 在**锁外**推导 → 调 provider → 校验,再把结果作为 `rework_task_override`
传给 locked writer,writer 锁内重新推导并只在 `triggered_by_reply` 一致时
采用(漂移则回落模板)。**writer 永不调用 provider;run-loop 无 refine 入口。**

**Spec:** `docs/superpowers/specs/2026-08-01-leader-refined-rework-design.md`

**Discipline:** `conda run -n agentdeck …`;严格 TDD;每 task 一 commit 且
带 `HISTORY.md` 顶部条目;不 push;无 co-author trailer;不 stage `.omc/`。
**定向回归必须包含 `tests/test_cli_structured_output.py` 与
`tests/test_provider_openai_compatible.py`**(2026-08-01 教训:改 provider
层时这两个文件里的对抗性守卫是最容易漏跑的)。

---

## Task 1: 纯推导 + writer override

**Files:** `src/agentdeck/review_iteration.py`, `src/agentdeck/state.py`,
`tests/test_refined_rework.py`(新), `HISTORY.md`

- [ ] **Step 1: 失败测试**——`tests/test_refined_rework.py`:
  - `build_refine_prompt(original_task, verdict, members)` 返回含原任务、
    每条 fail 标准、每位非 pass reviewer 署名段的纯文本;不含任何指令性
    "输出 JSON"字样(产出是纯文本任务)。
  - `validate_refined_task(text)`:非空 str 且 ≤ `MAX_REWORK_TASK_CHARS`
    → 返回规范化文本(末尾自动追加固定 commit 指令,不依赖模型自觉);
    空/非 str/超长 → 抛 `ValueError`。
  - writer:`append_review_iteration(..., rework_task_override="X",
    override_for_reply=<正确 reply_id>)` → rework step task == "X"、
    `task_source == "leader_refined"`、返回 `refined is True`;
    `override_for_reply` 不匹配 → 用模板、`refined is False`、
    `refine_skipped_reason == "state_changed"`;不传 override → 与今天
    逐字节一致(无 `task_source` 键、`refined is False`)。
  - 事件 `plan_rework_appended` 带 `refined`,漂移时带
    `refine_skipped_reason`。
- [ ] **Step 2: RED** — `pytest tests/test_refined_rework.py -q`
- [ ] **Step 3: 实现**
  - `review_iteration.py`:`REFINE_SKIP_REASONS = ("provider_error",
    "invalid_output", "state_changed", "unsupported_provider")`;
    `build_refine_prompt(...)`;`validate_refined_task(text)`(复用
    `MAX_REWORK_TASK_CHARS` 与模板同一 commit 收尾常量)。
  - `state.py` `append_review_iteration` 增两个关键字参数;采用 override
    仅当 `derived["triggered_by_reply"] == override_for_reply`;结果与
    事件加 `refined` / 可选 `refine_skipped_reason`;rework step 采用
    override 时加 `task_source="leader_refined"`(模板路径不加该键)。
- [ ] **Step 4: GREEN** — `pytest tests/test_refined_rework.py
  tests/test_review_iteration.py tests/test_review_group.py
  tests/test_plan_rework_cli.py -q`
- [ ] **Step 5: commit** `feat: add refined rework override to the iteration writer`

## Task 2: provider `refine_rework_task`

**Files:** `src/agentdeck/providers/{base,fake,cli_subprocess,openai_compatible}.py`,
`tests/test_refined_rework.py`, `HISTORY.md`

- [ ] **Step 1: 失败测试**
  - fake:返回确定性文本(含原任务关键词),可断言。
  - CLI 基类:monkeypatch `subprocess.run` 返回文本 → 得到 stdout 文本;
    非零退出 → 抛 `CliLeaderProviderError`(沿用既有 stage 语义)。
    **不得触碰 codex stderr**(OS 边界丢弃不变量)。
  - API 基类:monkeypatch 传输层 → 得到 message content 文本。
  - 未实现该方法的 provider(若有)→ 调用方按 `unsupported_provider`
    回落(在 Task 3 测)。
- [ ] **Step 2: RED**
- [ ] **Step 3: 实现**——三处各加
  `refine_rework_task(*, task: str, feedback: str, model: str | None) -> str`,
  复用各自既有的 prompt/传输/超时机制,**只返回纯文本**,不解析 JSON、
  不重试(spec:一次调用,不合格即由调用方回落)。
- [ ] **Step 4: GREEN** — `pytest tests/test_refined_rework.py
  tests/test_provider_openai_compatible.py tests/test_cli_structured_output.py
  tests/test_leader_cli.py -q`
- [ ] **Step 5: commit** `feat: add provider refine_rework_task`

## Task 3: CLI `--refine` + 契约 + 文档 + 全量

**Files:** `src/agentdeck/cli.py`, `src/agentdeck/contracts.py`,
`docs/contracts/plan-rework-schema.md`, `CLAUDE.md`, `README.md`,
`tests/test_refined_rework.py`, `HISTORY.md`

- [ ] **Step 1: 失败测试**
  - `--refine` 无 `--confirm` → 拒绝零写非 0。
  - 精修成功(fake provider):payload `refined is True`、step task 来自
    provider、`task_source=leader_refined`。
  - 回落矩阵:provider 抛异常 → `refined False` +
    `refine_skipped_reason="provider_error"` + `leader_provider_failed`
    已记 + 退出 0;返回空/超长 → `invalid_output`;provider 无该方法 →
    `unsupported_provider`。
  - 无 `--refine` 时**绝不**调用 provider(注入会 fail 的假 provider 断言)。
  - run-loop 路径无 refine 入口:`grep` 断言 + 引擎钩子调用签名不含 override。
- [ ] **Step 2: RED**
- [ ] **Step 3: 实现**——argparse 加 `--refine`;命令内锁外推导 →
  provider → 校验 → 带 override 调 writer;payload 加 `refined` 与可选
  `refine_skipped_reason`;`PLAN_REWORK_RESPONSE_FIELDS` 加 `refined`,
  validator 校验 bool 与可选枚举;example 同步;plan-rework-schema、
  CLAUDE.md(迭代闭环 bullet 追加一句)、README 同步。
- [ ] **Step 4: 全量阶梯**——定向套件(含 cli_structured_output 与
  provider_openai_compatible)→ `compileall` → `git diff --check` →
  全量 `pytest tests/ -q`(预期 ~4910+ passed, 3 skipped, 0 failed)。
- [ ] **Step 5: commit** `feat: add plan rework --refine`

## Post-plan

- handoff 更新;live 验证可搭下一轮 round(scratch 配 DeepSeek Leader,
  跑一次 `plan rework --refine` 对比模板版与精修版任务文本)。
- 非目标重申:run-loop 自主段用精修、精修复审任务、多轮精修、让模型改
  step 结构。
