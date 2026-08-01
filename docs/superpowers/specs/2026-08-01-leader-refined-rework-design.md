# Leader 精修回炉任务设计(`plan rework --refine`)

Status: frozen(user 拍板 2026-08-01:仅显式命令加 `--refine`;
provider 失败回落确定性模板并如实报告)
Prior art: review 迭代闭环
(`docs/superpowers/specs/2026-07-30-review-iteration-loop-design.md`)、
review group(`2026-08-01-review-group-round-reviewer-design.md`)

## 问题

迭代闭环的回炉任务是**确定性模板**(fail 标准原文 + reviewer 回复原文 +
固定 commit 指令,零 LLM)。Round 13 证明模板已足够好——coder 一次修对。
但模板是"原样转发":当 reviewer 的意见冗长、分散在多个 reviewer 的段落
里、或夹杂与本次修复无关的观察时,实现者仍需自己筛。让 Leader 把这些
意见**提炼**成一份聚焦的返工任务,是一个有价值但**可选**的增强。

## 用户拍板(2026-08-01)

1. **只给显式命令加 `--refine`**:
   `agentdeck plan rework --plan-id <id> --confirm --refine` 才调用 Leader
   provider。**run-loop 引擎钩子永不走这条路**——"run-loop 绝不调用 Leader
   provider"这条已 live 验证的不变量完整保留,walk-away 自主段的失败面
   不增加。
2. **provider 失败回落确定性模板并如实报告**:provider 异常、超时、
   返回不合格内容时,用模板版完成追加,响应标 `refined: false` +
   `refine_skipped_reason`(复用 2026-08-01 落地的 CLI 失败闭合枚举,
   API-backed provider 与格式问题另有对应码)。命令仍**成功**——迭代
   不因 provider 抖动而阻断,模板版本身就是可用产物。

## 设计

### 精修的输入与输出

- 输入(全部来自**已入账**数据,不新读文件、不读 pane):被审实现 step
  的原任务、本轮 fail/needs_changes 的验收标准原文与证据、组内每位
  非 pass reviewer 的回复原文(与模板同源,截断上限相同)。
- 调用:复用既有 Leader provider 抽象(`leader_provider(name)`),
  与 `leader plan` 同一个配置来源(`[leader]` provider/model;
  `[leader.planner]` 等子段不参与——精修不是 planning 段)。
- 输出契约:纯文本返工任务,**不是** plan JSON。校验(fail-closed):
  非空字符串、长度 ≤ `MAX_REWORK_TASK_CHARS`(与模板同一上限)、
  必须包含"commit 到任务分支"的固定收尾指令(由程序在校验后**追加**,
  不依赖模型自觉);任何一条不满足即判定不合格 → 回落模板。
- 结果只作为 rework step 的 `task` 文本;step 的 agent/role/编号/
  provenance(`origin`/`round`/`triggered_by_reply`/`iteration_kind`)
  与模板路径**完全一致**。

### provenance 与审计

- 追加的 rework step 增可选 `task_source` ∈ {`template`(缺省,
  省略即视为 template), `leader_refined`};re-review step 不带该键。
- `plan_rework_appended` 事件增 `refined`(bool)与可选
  `refine_skipped_reason`(闭合枚举);`plan rework` 响应增 `refined`
  与可选 `refine_skipped_reason`,并纳入 plan-rework contract 与
  validator。
- provider 调用失败照旧写 `leader_errors[]` 与 `leader_provider_failed`
  (`mode=plan_rework_refine`),**不吞异常**——但不阻断追加。

### 安全边界

- `--refine` 必须与 `--confirm` 同时给;单给 `--refine` 拒绝且零写。
- run-loop / run-loop-host / `--all` 的钩子**永不**传 refine——代码层面
  不提供该参数入口,测试钉住。
- 精修只改**回炉任务文本**;触发条件、预算、审批语义、step 顺序守卫、
  merge gate 全部不变。
- provider 输出只进 step task(与人类会读到的模板任务同性质),不进
  gate、不授权任何执行;失败原因只记闭合枚举码,不留存 provider 原文
  (复用 2026-08-01 的 CLI 失败分类)。
- 精修**不重试**:一次调用,不合格即回落。避免把命令变成不确定时长。

## 非目标

- run-loop 自主段使用精修(显式非目标,见拍板 1)。
- 精修 re-review 步的任务(复审任务必须逐字节复用原 review step)。
- 多轮精修 / 让模型决定是否需要回炉 / 让模型改动 step 结构(agent、
  数量、顺序)——结构永远由确定性推导决定。

## 测试要点

- `--refine` 无 `--confirm` 拒绝零写;run-loop 路径不存在 refine 入口。
- 精修成功:step task 来自 provider、`task_source=leader_refined`、
  响应与事件 `refined: true`、固定 commit 收尾指令仍在。
- 回落矩阵:provider 抛异常 / 返回空 / 返回超长 / 返回非字符串 →
  模板版落地、`refined: false` + 对应 `refine_skipped_reason`、命令
  退出 0、`leader_provider_failed` 已记(异常场景)。
- 模板路径(无 `--refine`)逐字节不变;plan-rework contract 与
  validator 同步;缺省 plan 的 `task_source` 省略不破坏既有投影。
