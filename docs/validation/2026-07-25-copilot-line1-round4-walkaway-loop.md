# Co-pilot Line 1 Round 4：一次确认走开的整环首验（PASS）

- 日期：2026-07-25 上午
- 项目：`~/Desktop/agentdeck-live-scratch`，plan `pln_04501873c31a`
- Leader：真实 DeepSeek API（`deepseek-v4-pro`，本轮起切换）
- Workers：coder=codex、reviewer=Claude Code（planner spawn 未派活）
- 任务：修复 round 3 reviewer 发现的 a11y 问题（焦点轮播隐藏页链接缺
  `tabindex="-1"`）
- 人工授权：user 在场逐步确认开跑；worker 本机只读验证类授权框按委托先例
  逐次放行（未用常设豁免）；顺序守卫缺口的绕行方案（A）由 user 现场拍板

## 结果：最小"走开"环在自然循环路线上首次闭合

```text
说目标 → DeepSeek 拆 2 步 → approve-plan 一次确认 →
wave1 派 coder（reviewer 按语义 blocked 保持 approved）→
coder TDD 修复（4 个授权框委托放行）→ 写文件通道回复 →
wave3 自动摄入 coder 回复 + 同 wave 派 reviewer →
reviewer 独立复核（重跑 coder 的 CDP 测试）→ 写文件通道回复 →
wave4 自动摄入 → stopped_reason=complete → summary ready → release 3/3
```

人工只出现在三类点上：整计划一次确认、worker 授权框放行、composer 卡壳
补回车。全程无手动 dispatch、无手动 capture-reply。

## 首验通过清单

- **观察点①（spawn tiled 布局）**：spawn-ready 后 4 pane 均匀平铺
  （73-74×16-17）、pane 标题=agent_id、`pane-border-status top` 生效。
- **`approval approve-plan --confirm`**：一次确认批准整计划 2 条审批。
- **run-loop 文件通道自动摄入 ×2**：wave3 摄入 coder 回复
  （`rep_afc158497a1e`）、wave4 摄入 reviewer 回复（`rep_f4da1db86bf7`），
  均 `captured_from=file`、自动入账、gate 正确推进到 `complete`。
- **blocked 语义**：未 spawn 的 reviewer 审批记 blocked 且保持 approved，
  spawn 后下一 wave 自动派发。
- **`agent release` 整环收尾 3/3**：pane 真实回收，账本全清。
- 修复真实落地：`index.html:1953-1954` tabindex 同步逻辑，SHA
  `de89cf01…` → `8ace2367…`；coder 留下可复用 CDP 测试
  `tests/focus-carousel-tab-order.mjs`；reviewer 独立重跑该测试通过、
  主 Banner 零回归。

## Live 发现（按优先级，候选下一批切片）

1. **provider HTTP 错误裸崩 CLI**：DeepSeek 返回 400 时 `leader plan` 直接
   traceback，未按项目规则写 `leader_errors[]` / `leader_provider_failed`
   事件（`providers/openai_compatible.py` 未捕获 `HTTPError`）。
2. **远端模型下线探测缺口**：DeepSeek 在 round 2→4 之间下线 `deepseek-chat`
   （现仅 `deepseek-v4-pro`/`v4-flash`）；doctor/provider_health 只查环境
   变量，无法发现远端模型失效。本轮用
   `leader set-provider --model deepseek-v4-pro` 显式切换。
3. **run-loop 无 step 顺序守卫**：wave 会把所有 approved-and-ready 审批
   一次全派（step 2 会在 step 1 完成前派给 reviewer）。本轮用"先 stop
   reviewer 制造 blocked"绕行（user 拍板方案 A）；正解=wave 只派发最早
   未完成 step 的审批。
4. **blocked gate 遮蔽文件摄入**：coder 回复文件已就绪时，wave2 因
   reviewer blocked 停在 `blocked`，摄入条件（仅 `waiting_for_reply` 触发）
   未命中——与 recovery 遮蔽同类；正解=摄入与 gate 解耦（存在待回复
   dispatched message 且文件就绪即摄入）。
5. **多行 dispatch prompt 尾部卡 Claude Code composer**：回复通道段落的
   最后一行留在输入框未提交，worker 静默空转（Context 0%），
   `waiting_for_input` 启发式探测不到（非确认框）；本轮人工补回车解开。
   正解方向=dispatch 后送达校验（capture 检查 composer 是否清空）或
   分段发送+提交确认。

## 结论

北极星产品成功测试的核心闭环（"说目标 → 确认一次 → 走开 → 回来看结果"）
首次在真实异构 agent 上立住。剩余人工点中，授权框放行是产品设计内的
安全边界；发现 3/4/5 修掉后，"走开"环的人工点将只剩授权框一类。
