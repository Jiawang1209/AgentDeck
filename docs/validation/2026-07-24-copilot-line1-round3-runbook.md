# Co-pilot Line 1 Round 3 Runbook：审查意见返工（人工授权）

目标：用真实项目证明**迭代环**——reviewer 的审查清单经账本流回 coder 返工，
每步人工确认。同场 live 验证 2026-07-24 新增的文件通道回复与等待态显性化。

前置：round 2 已完成的 `~/Desktop/agentdeck-live-scratch`（plan `pln_1c1536be2374`、
审查报告 `.agentdeck/artifacts/review-index-html-2026-07-23.md`）。安全边界与
round 2 runbook 相同：每个 live 步骤人类显式授权；不自动安装/认证；不 push。

## 步骤

1. 确认 runtime（如 pane 已失效先 `agentdeck agent refresh` 再 `agentdeck agent
   spawn-ready --confirm`；本轮起 spawn 应自动 tiled + 名字标签——观察点 ①）。
2. 直接派发返工任务给 coder（无需新 plan，单步任务走 leader chat 任务指派或
   `agentdeck dispatch`）：

   ```bash
   agentdeck leader chat --message "让 coder 按 .agentdeck/artifacts/review-index-html-2026-07-23.md 的'五、改进优先级建议'完成还原度收尾：头条左栏补轮播、Banner 减为 2 张、text-shadow 改 #004B27、:first-child 高亮改 aria-current，并在浏览器复验后按格式返回"
   agentdeck approval list && agentdeck approval approve --approval-id apv_xxx
   agentdeck approval dispatch --approval-id apv_xxx
   ```

3. 观察 worker 执行：
   - **观察点 ②（文件通道）**：dispatch prompt 尾部应含"回复通道"段落；worker 完成后
     检查 `.agentdeck/replies/<message_id>.reply.txt` 是否被真实 worker 写出。
   - **观察点 ③（等待态）**：worker 弹权限确认框时，`agentdeck agent capture --agent
     coder` 应返回 `waiting_for_input: true` 和命中的 `waiting_hint` 行。
4. 回收与复核：

   ```bash
   agentdeck capture-reply --agent coder --message-id msg_xxx   # 期望 captured_from=file
   agentdeck leader review --plan-id <plan>   # 部分派发时应停在 wait_for_approval（观察点 ④）
   ```

5. 如需 reviewer 复核返工结果，重复 approve→dispatch→capture 一轮后 `leader summary`。

## 成功判据

- 迭代环闭合：审查报告 → 人类确认 → coder 返工 → 回收入账 → （可选）复核 → 汇总，
  全程账本可 trace。
- 观察点 ①–④ 至少验证文件通道（②）与等待态（③）各一次；任何不符合预期的行为
  按惯例记入新的 live finding 文档。
