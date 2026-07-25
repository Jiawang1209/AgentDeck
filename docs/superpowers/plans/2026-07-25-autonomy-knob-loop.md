# 自主度旋钮收敛循环（自主 /loop 执行，2026-07-25）

来源：round 3 文件通道 2/2 真实验证后，user 拍板放行"run-loop 文件通道
自动回收"fork（对话授权），并修复 recovery 信号遮蔽。切片 3（live 整环
验证：fresh spawn 观察点① + approve-plan + run-loop + release）等 user
在场，不在本 loop 内。

## 硬边界（同前轮）

- 绝不 `git push`；不调真实 LLM；不 spawn/触碰真实 tmux pane（文件通道
  回收只读 reply 文件，不读 pane、不发输入）。
- 严格 TDD：RED → GREEN → 全量 `pytest tests/ -q` 绿（pipefail）+
  compileall → HISTORY 同 commit。
- 契约同步：run-loop/project-view schema、CLAUDE.md 安全边界描述、
  contracts.py validator/fields、README（如提及）、测试全部同步。
- 产品 fork 停下记阻塞；完成即收尾（PushNotification + stop）。

## 切片清单

### [ ] 1. run-loop 文件通道自动回收（自主度旋钮关键档）

- 现象：run-loop 停在 `waiting_for_reply` 即交还人工 capture-reply，是
  "确认一次走开"链条上唯一断点；文件通道 2/2 真实验证后，reply 文件存在
  = worker 显式宣告完成，回收无需猜测 pane。
- 目标：run-loop 在 gate 得到 `waiting_for_reply` 且该 message 的
  `.agentdeck/replies/<message_id>.reply.txt` 存在时，同 wave 内复用
  capture-reply 的文件通道摄入路径（record_reply + artifacts + 事件 +
  回流 inbox），追加 `run_loop_reply_captured` 汇总事件，然后重算 review/
  gate 一次并按新 gate 停下；响应新增 `captured_replies[]`（message_id/
  reply_id/agent_id/trace_command）。文件不存在行为不变（仍停
  `waiting_for_reply` 交人工）。绝不读 pane、绝不发送 tmux 输入；单次
  wave 内最多回收一轮（不无限循环）。
- 同步：run-loop schema "never captures" 边界改写为"绝不从 pane 捕获/
  绝不推断完成；仅当 worker 通过文件通道显式宣告完成时在 wave 内摄入"；
  CLAUDE.md run-loop 段同步；`captured_replies` 为可选字段（必需集不变）
  或按需入 fields——以 validator 现状定；测试覆盖 文件就绪回收/未就绪
  停下/回收后 gate 推进 三态。
- 授权依据：user 2026-07-25 对话明确批准该 fork。

### [ ] 2. recovery 跨状态暴露 reply_file_ready（遮蔽修复）

- 现象：round 3 发现 1——worker 已写回复文件时，recovery 因未 ack 收件停
  在 `inbox_pending`，`reply_file_ready` 不可见。
- 目标：recovery 只要存在 waiting reply review（有 dispatched message 等
  回复），无论当前 status 是 `inbox_pending` 还是 `reply_waiting`，都携带
  `reply_file_ready`；其余状态不带。既有 next_command/优先级不变（不重排
  recovery 状态机）。同步 project-view schema 文字与测试。

## 阻塞

（运行中遇 fork 在此记录）

## 完成判定

两切片 `[x]` → 全部 commit → PushNotification 汇总 → stop。切片 3 的
live 整环验证由 user 在场时另行执行。
