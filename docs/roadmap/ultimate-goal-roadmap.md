# AgentDeck Ultimate Goal Roadmap

> 目的：防止 AgentDeck 在连续开发中偏离终极目标。每一轮新功能都应该能映射到本文中的某个目标能力。

## 1. 终极目标

AgentDeck 的终极目标不是“做一堆 tmux 命令”，而是做一个 local-first 多智能体工作台：

```text
Human Operator
  -> API-backed Leader LLM
  -> role-aware Codex / Claude / other CLI Agents
  -> visible tmux runtime today, GUI later
  -> auditable message/job/reply/inbox ledger
  -> approval-gated execution
  -> recoverable project work history
```

用户应该能用自然语言启动一个任务，由 Leader Agent 理解目标、拆解计划、指派角色、调度多个 Agent、观察结果、要求验证，并在关键动作前让人类审批。

## 2. 为什么当前开发没有跑偏

当前已经实现的能力都对应终极目标中的底座：

| 已实现能力 | 对应终极目标 |
| --- | --- |
| `agent spawn/capture/send/stop` | 可见、可控、可恢复的多 Agent 终端 runtime |
| `role` / `role_prompt` / `assign-role` | Codex/Claude 等 Agent 能承担明确角色 |
| `dispatch` | Leader 或用户可把任务按角色投递给目标 Agent |
| `message -> attempt -> job -> inbox` | CCB 式多 Agent 通信账本的最小形态 |
| `reply` / `ack` | 请求-回复-确认闭环 |
| `trace` | 多 Agent 调试、审计和恢复所需的 lineage |
| `HISTORY.md` | 项目自身开发过程可追溯 |

这些能力还不是最终产品体验，但它们是 Leader Agent、GUI、自动调度和审批系统需要依赖的基础设施。

## 3. 当前阶段边界

当前阶段是 **MVP Control Plane**，重点是把底层契约做稳：

- agent identity
- role assignment
- tmux runtime binding
- message ledger
- inbox and trace
- local project state

当前阶段不应该优先做：

- 完整 GUI
- 远程 relay
- 自动学习系统
- 多 provider 大矩阵
- 自研终端模拟器
- 自动执行高风险文件操作

这些不是不要做，而是必须等 control plane 稳定后再做。

## 4. 下一阶段目标

### Phase A: Leader Agent MVP

目标：让任意可通过 API 调用的 LLM 作为 Leader Agent 参与进来，而不是用户手动 dispatch 每一步。DeepSeek 可以作为首个默认适配目标，但架构不能绑定到 DeepSeek。

应实现：

- `agentdeck leader plan --task <text>`
- provider client 抽象
- DeepSeek 或 OpenAI-compatible provider 作为首个实现
- plan schema：目标、子任务、目标 agent、风险、需要审批的动作
- plan 写入 state
- 人类确认后再 dispatch

验收标准：

- 输入一个任务，Leader 能输出结构化 plan。
- plan 不直接执行。
- plan 中每个子任务都能映射到 agent role。

### Phase B: Approval Gate

目标：所有危险动作都经过人类确认。

应实现：

- `approvals[]` state
- `agentdeck approval list`
- `agentdeck approval approve --id <id>`
- dispatch 前可选择 dry-run / approval-required
- send/stop/reply extraction 等动作进入审计日志

验收标准：

- 系统不会自动 kill pane、写文件、提交 git、发送执行命令。
- 每个危险动作可 trace 到 human approval。

### Phase C: Reply Extraction

目标：减少手动复制 `reply --text`。

应实现：

- `agentdeck capture-reply --agent <id> --message-id <id>`
- 从 pane capture 中提取最近结构化回复
- 写入 `replies[]`
- 回流 sender inbox

验收标准：

- Worker 在 pane 中输出结构化结果后，系统能把它记录成 reply。
- Leader 可以读取 reply 并继续调度。

### Phase D: Multi-Agent Run Loop

目标：从单次 dispatch 升级为任务运行循环。

应实现：

- `agentdeck run --task <text>`
- Leader plan -> human approval -> dispatch workers
- worker reply -> leader review -> next dispatch or final summary
- `trace` 可覆盖整个 run

验收标准：

- 一个任务可以经过 planner/coder/reviewer 多角色协作。
- 人类能在每个关键节点暂停、查看、批准、终止。

### Phase E: ProjectView and GUI

目标：把 CLI control plane 映射到可视化工作台。

应实现：

- `agentdeck project-view`
- 输出 agents、runtime、messages、jobs、inbox、approvals、trace summary
- Web/GUI 只消费 ProjectView，不直接扫描 tmux

验收标准：

- GUI 能显示每个 Agent 的角色、状态、pane、任务、inbox、trace。
- GUI 不成为第二套状态源。

## 5. 每轮开发的防跑偏检查

每次开发前先问：

1. 这项能力服务 Leader 调度、多 Agent 通信、可见 runtime、审批、恢复、GUI 中的哪一个？
2. 是否能写入 state 并被 trace？
3. 是否需要更新 HISTORY？
4. 是否会绕过人类审批？
5. 是否把 tmux 当 runtime 后端，而不是业务事实源？
6. 是否过早引入 GUI、远程、provider 矩阵或自动学习复杂度？

如果一个功能答不上第 1 点，就先不要做。

## 6. 推荐下一步

下一步建议做 **Phase A: Leader Agent MVP** 的第一刀：

```text
agentdeck leader plan --task <text>
```

它先不自动 dispatch，只调用配置的 Leader LLM provider 或 dry-run fake provider 生成结构化 plan，并写入 state。这样项目就从“用户手动调度多个 agent”前进到“Leader Agent 开始规划多 agent 协作”。
