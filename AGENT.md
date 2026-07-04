# AGENT.md

## AgentDeck Agent Contract

本项目中的 agent 是有名字、有角色、有 runtime 绑定、有消息队列、有权限边界的工作单元。

## Agent 类型

### Leader Agent

职责：

- 理解用户目标。
- 读取项目上下文。
- 通过 `agentdeck leader chat --message <text>` 接收自然语言输入，并基于 ProjectView 决定创建 plan 或 review 最新 plan。
- 拆解任务。
- 用 `agentdeck leader plan --task <text>` 生成 plan-only 记录。
- 分配 Worker。
- 请求人类审批。
- 汇总 Worker 结果。
- 触发验证。
- 输出最终结论。

默认 provider 边界：Leader 使用 API-backed LLM provider 抽象。DeepSeek/OpenAI-compatible 可以作为初始候选，但 Leader 逻辑不能绑定到单一 provider。

Leader 不应该：

- 把 Worker 的完整长输出全部塞进上下文。
- 跳过 plan 和审批直接自动 dispatch。
- 在没有审批的情况下执行破坏性命令。
- 在 Worker 修改文件后不重新读取就直接汇总。
- 把 provider 特定字段泄漏到 orchestration 核心。

### Worker Agent

职责：

- 接收明确任务。
- 在有限工具集内执行。
- 返回结构化结果。
- 记录读写文件和验证结果。

Worker 输出建议格式：

```markdown
status: completed | blocked | failed
summary: 简短结果
files_read:
  - path
files_written:
  - path
verification:
  - command: ...
    result: passed | failed | not_run
risks:
  - ...
full_output_path: .agentdeck/artifacts/...
```

Worker 不应该：

- 默认写长期 memory。
- 直接询问用户。
- 私自派生更多子代理。
- 绕过 Leader 执行 git push、merge、reset、kill pane 等动作。

## Runtime 绑定

业务 ID 和 runtime handle 分离：

- `agent_id`: 本项目业务 ID，例如 `coder`。
- `role`: 任务角色，例如 `implementation`。
- `provider`: agent 使用的模型或 CLI 类型。
- `pane_id`: tmux runtime handle。
- `session_name`: tmux session。
- `cwd`: 工作目录。

## 消息规则

- 面向用户展示 agent name，不展示 provider 细节。
- 每个 agent 应配置 `role` 和 `role_prompt`，dispatch 时会把角色说明注入任务 prompt。
- 角色可以通过 `.agentdeck/config.toml` 编辑，也可以通过 `agentdeck agent assign-role` 写回配置。
- task request 和 task reply 都进入 mailbox。
- 每个 agent 同时只消费一个 active task。
- 所有 job/reply/event 都要可 trace。
- 当前 MVP 通信路径是 `dispatch -> message/attempt/job/inbox -> tmux pane -> reply -> sender inbox -> ack`。
- `agentdeck inbox --agent <id>` 可查看某个 agent 收到的 task request。
- `agentdeck reply --agent <id> --message-id <id> --text <text>` 可把 agent 结果记录为 reply。
- `agentdeck capture-reply --agent <id> --message-id <id>` 可从 pane 最近输出捕获最后一个 `status:` 结构化回复块。
- `agentdeck ack --agent <id> --inbox-id <id>` 只能确认该 agent 最早的 pending inbox item，不得越过 head。
- `agentdeck trace --id <id>` 可用 message/attempt/job/reply/inbox 任意 ID 还原通信链路。
- `agentdeck status` 返回 ProjectView 只读摘要，包含 agents、plans、approvals、messages、jobs、replies、chat_turns、leader_actions 和 inbox，适合作为 GUI 与 Leader chat loop 的默认状态入口。
- `status.inbox.heads` 按 agent 暴露最早 pending inbox item；GUI/Leader 应优先用它判断当前可处理或可 ack 的 mailbox head。
- 后续升级为更严格的 reply block 标记。

## Leader Planning

- `agentdeck leader chat --message <text>` 是自然语言 Leader 入口 MVP；它会读取 ProjectView，无 plan 时创建 plan-only 记录，有 plan 时 review 最新 plan，并持久化或复用一条 `leader_actions[]` 建议。
- `agentdeck leader chat-history` 返回已持久化的 chat turns 摘要，用于恢复自然语言调度上下文；review turn 会包含 action_id/action_kind。
- `agentdeck leader plan --task <text>` 会写入 `.agentdeck/state/state.json` 的 `plans[]`。
- `agentdeck leader review --plan-id <id>` 会基于 plan status 和 replies 输出下一步建议。
- `agentdeck leader next` 会把下一步建议写入 `leader_actions[]`，但不会执行命令；相同 pending action 已存在时会复用原 action_id。
- `agentdeck leader actions` 返回已持久化的 action queue 摘要。
- `agentdeck leader action --action-id <id>` 返回单个 action 的只读详情，包含 `can_apply`、`apply_command`、`explicit_command` 和 `apply_blocker`。
- `agentdeck leader apply-action --action-id <id>` 当前只允许应用 `create_approvals`，dispatch/capture 类 action 必须继续由人类显式命令执行。
- `agentdeck plan list` 返回 plan 摘要，不包含完整 `plan` body。
- `agentdeck plan show --plan-id <id>` 返回完整 plan，用于审批前检查。
- `agentdeck plan status --plan-id <id>` 返回 plan step、approval 状态和 dispatch lineage 汇总。
- Provider 失败会写入 `leader_errors[]`，并通过 `agentdeck status` 暴露摘要；失败不能创建 plan、approval、message、job 或 inbox。
- 默认 `fake` provider 是本地 dry-run provider，不调用外部 LLM。
- `openai-compatible` provider 通过 `AGENTDECK_LEADER_API_KEY`、`AGENTDECK_LEADER_BASE_URL` 和 `AGENTDECK_LEADER_MODEL` 调用 `/chat/completions`，但仍然只生成 plan。
- chat/plan-only 阶段不会写入 `messages`、`jobs` 或 `inbox`，也不会发送 tmux 输入。
- 后续 DeepSeek/OpenAI-compatible 或其他 API-backed provider 必须复用同一 plan schema。

## 审批规则

- `agentdeck approval create-from-plan --plan-id <id>` 会从 plan steps 创建 `approvals[]`。
- `agentdeck approval list` 可查看审批项。
- `agentdeck approval approve --approval-id <id>` 将审批项标记为 `approved`。
- `agentdeck approval reject --approval-id <id> --reason <text>` 将审批项标记为 `rejected`。
- `agentdeck approval dispatch --approval-id <id>` 只接受 `approved` 审批项，并把对应 plan step 派发到目标 agent。
- approval dispatch 是单步显式命令，不会自动连续派发整个 plan。

以下动作必须进入审批：

- 写文件。
- 删除或移动文件。
- 执行 destructive shell command。
- 向 agent pane 发送可执行输入。
- kill 或 respawn pane。
- git commit/push/merge/reset。
- 暴露远程访问或写入 credential。
