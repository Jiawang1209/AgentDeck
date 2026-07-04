# AgentDeck: Local Multi-Agent Terminal Workbench

AgentDeck 是一个正在搭建中的本地多智能体终端工作台。它的目标是让任意可通过 API 调用的 LLM 作为 Leader Agent，把任务分发给多个 Worker Agent，并在 tmux 可见终端中执行、观察、审批、恢复和审计。DeepSeek 可以作为首个默认 provider，但不是架构绑定点。

本项目不是要从零重写终端模拟器，而是先把四类能力融合起来：

- WispTerm 式 AI 原生终端控制面。
- Claude Codex Bridge 式多 Agent 通信、mailbox 和 tmux panes。
- Hermes Agent 式 Leader/Worker 隔离、工具注册表、skills/memory 和 guardrails。
- tmux 式长期运行、可见、可输入、可读取、可恢复的终端 runtime。

详细架构见：[docs/architecture/multi-agent-terminal-design.md](/Users/liuyue/Desktop/Github_repos/multi-agent-explore/docs/architecture/multi-agent-terminal-design.md)。

终极目标路线图见：[docs/roadmap/ultimate-goal-roadmap.md](/Users/liuyue/Desktop/Github_repos/multi-agent-explore/docs/roadmap/ultimate-goal-roadmap.md)。

## 技术栈

当前骨架选择：

- Python 3.12，使用 Miniforge/conda 环境 `agentdeck`
- 标准库 CLI，无强制第三方依赖
- tmux 作为第一 runtime backend
- TOML 配置
- JSON/JSONL 初始状态存储，后续迁移到 SQLite
- API-backed LLM provider adapter，支持本地 `fake` 和 OpenAI-compatible plan provider

未来可扩展：

- 本地 daemon / loopback API
- Web 或桌面 GUI
- control mode watcher
- SQLite message bureau
- skill snapshot 和 MCP adapter

## 当前能力

已提供最小可运行骨架：

```bash
conda activate agentdeck
agentdeck doctor
agentdeck project init
agentdeck status
agentdeck contract project-view
agentdeck contract project-view --example
agentdeck contract leader-chat
agentdeck contract leader-chat --example
agentdeck agent list
agentdeck agent spawn --agent planner
agentdeck agent capture --agent planner --lines 200
agentdeck agent send --agent planner --text "继续"
agentdeck agent stop --agent planner
agentdeck agent assign-role --agent planner --role "architecture planning" --role-prompt "你负责架构规划和任务拆解。"
agentdeck leader chat --message "帮我设计自动 reply extraction"
agentdeck leader chat-history
agentdeck leader plan --task "设计自动 reply extraction"
agentdeck leader review --plan-id pln_xxx
agentdeck leader next
agentdeck leader actions
agentdeck leader action --action-id act_xxx
agentdeck leader apply-action --action-id act_xxx
agentdeck plan list
agentdeck plan show --plan-id pln_xxx
agentdeck plan status --plan-id pln_xxx
agentdeck approval create-from-plan --plan-id pln_xxx
agentdeck approval list
agentdeck approval approve --approval-id apv_xxx
agentdeck approval reject --approval-id apv_xxx --reason "范围过大"
agentdeck approval dispatch --approval-id apv_xxx
agentdeck dispatch --agent planner --task "设计消息账本"
agentdeck inbox --agent planner
agentdeck reply --agent planner --message-id msg_xxx --text "status: completed"
agentdeck capture-reply --agent planner --message-id msg_xxx
agentdeck ack --agent planner --inbox-id inb_xxx
agentdeck trace --id msg_xxx
agentdeck events --limit 20
```

`project init` 会创建：

```text
.agentdeck/
  config.toml
  state/
    state.json
    events.jsonl
    approvals.jsonl
  logs/
    agents/
  artifacts/
  skills/
```

## 快速开始

在项目根目录创建并激活开发环境：

```bash
conda env create -f environment.yml
conda activate agentdeck
python -m pip install -e .
```

之后所有开发命令默认都在 `agentdeck` 环境中运行：

```bash
agentdeck doctor
agentdeck project init
agentdeck status
agentdeck agent list
agentdeck agent stop --agent planner
agentdeck leader chat --message "帮我设计自动 reply extraction"
agentdeck leader chat-history
agentdeck leader plan --task "设计自动 reply extraction"
agentdeck leader review --plan-id pln_xxx
agentdeck plan list
agentdeck plan show --plan-id pln_xxx
agentdeck plan status --plan-id pln_xxx
agentdeck approval create-from-plan --plan-id pln_xxx
agentdeck approval list
agentdeck approval approve --approval-id apv_xxx
agentdeck approval dispatch --approval-id apv_xxx
agentdeck dispatch --agent planner --task "设计消息账本"
agentdeck inbox --agent planner
agentdeck reply --agent planner --message-id msg_xxx --text "status: completed"
agentdeck capture-reply --agent planner --message-id msg_xxx
agentdeck ack --agent planner --inbox-id inb_xxx
agentdeck trace --id msg_xxx
python -m compileall src
```

## Agent Runtime Commands

当前 tmux runtime MVP 已支持五个 agent 操作命令：

```bash
agentdeck agent list
agentdeck agent spawn --agent planner
agentdeck agent capture --agent planner --lines 200
agentdeck agent send --agent planner --text "继续"
agentdeck agent stop --agent planner
```

这些命令的约束：

- `agent_id` 来自 `.agentdeck/config.toml`。
- `spawn` 会创建项目 tmux session，并记录 `agent_id -> pane_id` 绑定。
- `spawn` 会拒绝重复启动已经处于 `running` 状态且已有 `pane_id` 的 agent。
- `capture` 和 `send` 只面向已经 spawn 的 agent。
- `stop` 会 kill 对应 tmux pane，并把该 agent 标记为 `stopped`。
- `send` 是人工执行的显式命令，后续自动调度前还会加入审批队列。
- runtime binding 与事件会写入 `.agentdeck/state/`。

## Role Assignment and Dispatch

AgentDeck 支持两种角色指派方式：

1. 直接编辑 `.agentdeck/config.toml` 中每个 `[[agents]]` 的 `role` 与 `role_prompt`。
2. 使用 CLI 写回配置：

```bash
agentdeck agent assign-role \
  --agent planner \
  --role "architecture planning" \
  --role-prompt "你负责架构规划、任务拆解和风险识别。"
```

`dispatch` 会把 agent 的 `role`、`role_prompt`、当前任务和结构化输出格式组合成一段 prompt，发送到对应的 tmux pane：

```bash
agentdeck dispatch --agent planner --task "设计消息账本"
```

当前通信路径是 MVP 形态：

```text
Human/Leader -> dispatch -> message/attempt/job/inbox -> tmux pane -> reply -> sender inbox -> ack
```

每次 dispatch 会写入 `.agentdeck/state/state.json` 的 `messages`、`attempts`、`jobs` 和目标 agent 的 `inbox`，并追加 `task_dispatched` 事件。可以查看某个 agent 的 inbox：

```bash
agentdeck inbox --agent planner
```

Agent 完成任务后，可以先用手动命令把回复写入账本：

```bash
agentdeck reply --agent planner --message-id msg_xxx --text "status: completed"
```

也可以从 agent pane 最近输出中捕获最后一个 `status:` 开头的结构化回复块：

```bash
agentdeck capture-reply --agent planner --message-id msg_xxx
```

如果任务由另一个 agent 发起，reply 会作为 `task_reply` 投递到发起方 inbox。处理完 inbox item 后可以确认：

```bash
agentdeck ack --agent planner --inbox-id inb_xxx
```

`ack` 使用 head-only mailbox 语义：只能确认该 agent inbox 中最早的 `pending` item。若试图越过 head 确认后面的 item，CLI 会返回当前 head id，避免多 Agent 回复和任务请求被乱序消费。

可以用任意通信 ID 还原整条链路：

```bash
agentdeck trace --id msg_xxx
agentdeck trace --id att_xxx
agentdeck trace --id job_xxx
agentdeck trace --id rep_xxx
agentdeck trace --id inb_xxx
```

`trace` 会返回同一条 message lineage 下的 message、attempts、jobs、replies 和 inbox_items。后续会继续补更严格的 reply block 标记。

## ProjectView and Status

`agentdeck status` 是当前面向 CLI、自然语言入口和未来 GUI 的统一只读 ProjectView。它会返回项目配置、Leader、agents runtime binding、state_path，以及 plans、approvals、messages、jobs、replies、chat_turns、leader_errors、leader_actions、inbox、recovery 的轻量摘要。

详细字段契约见 `docs/contracts/project-view-schema.md`。当前契约版本为 `schema_version: "project-view/v1"`。`agentdeck contract project-view` 会返回契约版本、文档路径和关键字段摘要，方便 GUI 或外部集成做 discovery；加 `--example` 会附带一份 GUI-ready ProjectView 示例。`agentdeck contract leader-chat` 会发现自然语言 Leader chat 响应字段，`--example` 会附带包含 `leader_explanation` 的稳定响应示例。GUI、自然语言入口和恢复工具应优先按这些契约消费 `agentdeck status` 和 `agentdeck leader chat`，不要把 tmux pane 或 state 文件当成第二套状态源。

`status.inbox.heads` 会按 agent 暴露最早的 `pending` inbox item；没有待处理 item 的 agent 会返回 `null`。GUI 和 Leader chat loop 可以用它直接显示每个 agent 当前必须先处理或 ack 的 mailbox head。

`status.leader_actions` 会包含 `recommended_action_id`，每个 `items[]` 会包含 `can_apply`、`apply_command`、`explicit_command`、`apply_blocker` 和 `is_recommended`，GUI 可以直接根据 ProjectView 渲染 action 按钮、阻塞提示和当前推荐项高亮。

`status.chat_turns.items` 会包含 review/apply turn 关联的 `action_id` 和 `action_kind`，GUI 可以从自然语言对话历史直接跳转到对应 action。

`status.recovery` 会汇总当前恢复入口：`status`、`reason`、`next_command`、`recommended_action`、pending 计数、可应用的 `leader_action`，以及最近审计事件摘要。`recommended_action` 包含 label、command、safety、requires_explicit_user、source 和 target_id，GUI 可以用它直接渲染下一步按钮或检查入口，并把按钮关联回 action、approval 或 inbox item。GUI 和 Leader chat loop 可以优先用 recovery 判断“现在该继续什么”，而不需要散读 state 或自行推断。

`agentdeck events --limit 20` 会读取 `.agentdeck/state/events.jsonl` 的最近事件，用于 GUI 审计时间线和调试恢复。

这些摘要和事件读取只用于观察和恢复，不修改 state、不发送 tmux 输入，也不包含完整长 prompt。GUI 或 Leader chat loop 应优先读取 `agentdeck status`，再按需调用 `plan show`、`plan status`、`trace` 或 `events` 获取细节。

## Leader Planning

AgentDeck 已提供第一版 plan-only Leader 能力：

```bash
agentdeck leader chat --message "帮我设计自动 reply extraction"
agentdeck leader chat --message "继续"
agentdeck leader chat-history
agentdeck leader plan --task "设计自动 reply extraction"
agentdeck leader review --plan-id pln_xxx
agentdeck leader next
agentdeck leader actions
agentdeck leader action --action-id act_xxx
agentdeck leader apply-action --action-id act_xxx
agentdeck plan list
agentdeck plan show --plan-id pln_xxx
agentdeck plan status --plan-id pln_xxx
```

当前默认使用本地 `fake` provider 生成确定性的结构化 plan，并写入 `.agentdeck/state/state.json` 的 `plans[]`。也可以显式使用 `--provider openai-compatible` 调用 OpenAI-compatible `/chat/completions` API。两种模式都不会 dispatch、不会发送 tmux 输入。

`leader chat` 是自然语言入口 MVP。它会先读取并校验 `agentdeck status` 的 ProjectView：如果 ProjectView 不满足 `project-view/v1` 契约，chat 会返回非 0，且不会创建 plan 或 chat turn；如果当前还没有 plan，就把 message 当作目标创建 plan-only 记录；如果已有 plan，就 review 最新 plan、持久化或复用一条 `leader_actions[]` 建议，然后重新读取 ProjectView 的 `status.recovery` 作为恢复决策源。chat 输出会在顶层返回与 `project_view.leader_actions` 相同的 `leader_actions` 摘要，并返回 `leader_explanation` 说明当前模式、推荐 action、reason、next_command、safety 和是否需要人类显式确认；响应契约见 `docs/contracts/leader-chat-schema.md`，可通过 `agentdeck contract leader-chat` 发现，输出 JSON 前也会通过 `validate_leader_chat_contract()` 自校验，失败会写入 `leader_errors[]` 和 `leader_chat_contract_failed` 事件。review 输出还会返回 `recovery`，并让 `next_command` 等于 `recovery.next_command`；`leader_action` 包含 `can_apply`、`apply_command`、`explicit_command` 和 `apply_blocker`，方便 GUI 或对话层直接展示执行按钮与阻塞原因。每次 chat turn 都会写入 `.agentdeck/state/state.json` 的 `chat_turns[]`，并可通过 `leader chat-history` 查看；review turn 会记录 action_id/action_kind。它不会创建 approval、不会 dispatch、不会发送 tmux 输入。

当人类明确输入 `agentdeck leader chat --message "apply action act_xxx"` 或 `--message "/apply-action act_xxx"` 时，chat 会复用 `leader apply-action` 的安全白名单。当前只会应用 `create_approvals`，并会拒绝 dispatch/capture 等 runtime action。

`plan list` 返回计划摘要，适合给自然语言入口或 GUI 做列表视图；`plan show` 返回完整计划，适合审批前人工检查；`plan status` 汇总每个 step 的 approval 状态、message_id、attempt_id 和 job_id，适合恢复任务进度。

`leader review` 会先校验 ProjectView，再使用本地 deterministic 规则读取 plan status 和 replies，输出下一步建议：`dispatch_approved`、`wait_for_reply`、`summarize` 或 `wait_for_approval`。后续接入真实 Leader LLM 时应复用该输出结构。

`leader next` 会先校验 ProjectView，再把下一步建议持久化到 `leader_actions[]`，例如创建 approvals 或派发 approved step 的命令。它只记录 pending action，不执行命令；如果相同 pending action 已存在，会复用原 action_id，不重复污染 queue。`leader actions` 可查看已记录的 action queue，并通过 `recommended_action_id` 与每项 `is_recommended` 标记当前 recovery 推荐项。

`leader action --action-id <id>` 返回单个 action 的详情，包括 `can_apply`、`apply_command`、`explicit_command` 和 `apply_blocker`。它会附带当前 `recovery`、`recommended_action` 和 `matches_recommended_action`，方便 GUI 判断这个 action 是否就是当前恢复入口推荐的下一步。它是只读入口，适合 GUI、自然语言 Leader 或人类在执行前确认当前 action 为什么能或不能安全应用。

`leader apply-action --action-id <id>` 是显式确认入口。它会先校验 ProjectView；当前只允许应用 `create_approvals`，会创建 approval queue 并把 action 标记为 `applied`。`dispatch_approved`、`wait_for_reply` 等 action 仍会被拒绝，必须由人类运行对应的 `approval dispatch` 或 `capture-reply` 命令。

计划确认后，可以创建审批项：

```bash
agentdeck approval create-from-plan --plan-id pln_xxx
agentdeck approval list
agentdeck approval approve --approval-id apv_xxx
agentdeck approval reject --approval-id apv_xxx --reason "范围过大"
agentdeck approval dispatch --approval-id apv_xxx
```

`approval dispatch` 只接受 `approved` 状态的审批项，并把对应 plan step 转成现有 `dispatch -> message/attempt/job/inbox` 链路。它仍然是显式命令，不会自动连续派发整个 plan。

返回结果包含：

- `plan_id`
- `provider`
- `model`
- `status`
- `dispatch_ready`
- `plan.goal`
- `plan.steps[]`

OpenAI-compatible provider 使用同一个 provider 抽象和 plan schema，并要求模型返回 JSON object plan。

如果真实 provider 返回非法 JSON、缺少 steps 或某个 step 没有 `requires_approval: true`，CLI 会明确失败并把错误写入 `.agentdeck/state/state.json` 的 `leader_errors[]`。失败不会创建 plan、approval、message、job 或 inbox。

配置环境变量：

```bash
export AGENTDECK_LEADER_API_KEY="..."
export AGENTDECK_LEADER_BASE_URL="https://api.deepseek.com/v1"
export AGENTDECK_LEADER_MODEL="deepseek-chat"
```

示例：

```bash
agentdeck leader plan --provider openai-compatible --model "$AGENTDECK_LEADER_MODEL" --task "规划下一步开发"
agentdeck leader chat --provider openai-compatible --model "$AGENTDECK_LEADER_MODEL" --message "帮我规划下一步"
```

真实 provider 仍然只生成 plan 或 chat turn，不会自动创建 approval 或派发任务。

## 设计原则

- Local-first：本地项目、本地状态、本地终端优先。
- Human-in-the-loop：危险操作必须可审批、可追踪。
- Agent-first：用户面向 agent name，而不是 provider 或 pane id。
- Visible runtime：Worker 运行在可见终端里，人类可以随时接管。
- Recoverable state：任务、消息、job、reply、artifact 都要能追溯。
- Small core：核心保持窄，工具、skills、provider、runtime 通过边界扩展。

## 参考分析

四份参考仓库分析保存在：

- [WispTerm 分析](/Users/liuyue/Desktop/Github_repos/multi-agent-explore/docs/reference-analysis/wispterm-main.md)
- [Claude Codex Bridge 分析](/Users/liuyue/Desktop/Github_repos/multi-agent-explore/docs/reference-analysis/claude-codex-bridge-main.md)
- [Hermes Agent 分析](/Users/liuyue/Desktop/Github_repos/multi-agent-explore/docs/reference-analysis/hermes-agent-main.md)
- [tmux 分析](/Users/liuyue/Desktop/Github_repos/multi-agent-explore/docs/reference-analysis/tmux-master.md)

## 开发约束

- 每次新增功能或用户可见行为变化都要 commit。
- 每次开发内容都要同步写入 [HISTORY.md](/Users/liuyue/Desktop/Github_repos/multi-agent-explore/HISTORY.md)，并和对应改动放在同一次 commit 中。
- 每次开发前要对照 [终极目标路线图](/Users/liuyue/Desktop/Github_repos/multi-agent-explore/docs/roadmap/ultimate-goal-roadmap.md)，确认功能没有偏离 Leader Agent 调度、多 Agent 通信、可见 runtime、审批或恢复这几条主线。
- `References/` 是本地研究材料，不纳入 git。
- README、CLAUDE.md、AGENT.md 和架构文档要跟代码同步。
- 所有开发命令默认先执行 `conda activate agentdeck`。
- 不在 MVP 阶段重写终端模拟器。
- 不在没有审批模型前实现自动写文件、kill pane、push、remote relay。
