# Co-pilot Line 1 · 首次 live 尝试与发现（2026-07-23）

## 尝试内容
- 分支：`copilot-line-1`（Desktop 代码，`PYTHONPATH` 钉到 `src`，`python -m agentdeck`）。
- 隔离 scratch 项目：`/Users/liuyue/Desktop/agentdeck-live-scratch`（`agentdeck project init`，
  agents = planner(codex)/coder(codex)/reviewer(claude)）。
- 真实 Leader：DeepSeek（`--provider deepseek`，key 由人类内联提供，未落盘）。
- 任务：`实现一个 Python 函数 slugify(text) 把字符串转成 URL slug，并用 pytest 写全面测试`。
- 命令：`... python -m agentdeck leader plan --task "<slugify>" --provider deepseek`。

## 结果
- **真实 DeepSeek Leader 可达、且真的返回了一个计划**（API 调用成功、有响应）。
- 但被本项目**自己的 plan 校验器拒绝**，stderr：
  `leader provider failed: provider plan must include exactly 3 steps`。
- 拒绝发生在校验阶段（`src/agentdeck/providers/plan_schema.py`），返回的计划未被捕获，
  **DeepSeek 实际拆了几步没有记录**（人类选择不单独 capture）。

## 诊断
- `leader_plan_authority()`（`plan_schema.py:55`）在未显式指定时把 `step_count`
  设为 `len(config.agents)`（=3），`build_leader_plan_schema()` 据此把 steps 的
  `minItems == maxItems == step_count`，`plan_schema.py:383` 再断言
  `len(steps) != step_count` 即报 “must include exactly N steps”。
- 即：**强制“每个配置 agent 正好一步”**。DeepSeek 面对 slugify 很可能自然拆成
  2 步（coder 实现 + reviewer 审查），因而被打回。
- 这与本项目重写线的整体诊断同构：**卡点是我们自己的刚性约束在拧反 agent 天性，
  不是“用 agent 当 Leader”本身。** 这次是在 Desktop 线上被廉价、具体地复现。

## 结论（human 决定）
- 今晚**已拿到关键正向结果**：真实 API Leader 可达且自然返回计划。
- **停在此**，把“放松步数约束”作为**下一个正经 TDD 切片**，不在 live 中途仓促改深层
  schema。

## 下一个切片（待做）
- **放松 plan 步数约束**：把“正好 N 步”改为“1..N 步范围”，同时保留全部安全不变量
  （每步 `agent_id` ∈ 已配置 agent、每步 `requires_approval=True`、步号 1..k 连续、
  有上界）。
- 影响面（需一并处理）：`build_leader_plan_schema` 的 `minItems/maxItems`、
  `plan_schema.py:383` 的精确计数断言、schema 内容 hash（provenance）、以及一批现存
  步数断言测试（如 `tests/test_leader_plan_schema.py`）。**只动 legacy/非 semantic 路径，
  不碰 `semantic_authority` 路径。**
- 完成后：重跑本 live（slugify），继续走 approval → spawn → dispatch → capture →
  review → summary 一整轮。

## 安全备注
- 本次使用的 DeepSeek key 曾在会话中明文出现（未进文件/git/shell history）。建议轮换。
