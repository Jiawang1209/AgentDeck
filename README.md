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
- API-backed LLM provider adapter 骨架，首个目标兼容 DeepSeek/OpenAI-compatible 接口

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
agentdeck agent list
agentdeck agent spawn --agent planner
agentdeck agent capture --agent planner --lines 200
agentdeck agent send --agent planner --text "继续"
agentdeck agent stop --agent planner
agentdeck agent assign-role --agent planner --role "architecture planning" --role-prompt "你负责架构规划和任务拆解。"
agentdeck leader plan --task "设计自动 reply extraction"
agentdeck plan list
agentdeck plan show --plan-id pln_xxx
agentdeck approval create-from-plan --plan-id pln_xxx
agentdeck approval list
agentdeck approval approve --approval-id apv_xxx
agentdeck approval reject --approval-id apv_xxx --reason "范围过大"
agentdeck dispatch --agent planner --task "设计消息账本"
agentdeck inbox --agent planner
agentdeck reply --agent planner --message-id msg_xxx --text "status: completed"
agentdeck ack --agent planner --inbox-id inb_xxx
agentdeck trace --id msg_xxx
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
agentdeck leader plan --task "设计自动 reply extraction"
agentdeck plan list
agentdeck plan show --plan-id pln_xxx
agentdeck approval create-from-plan --plan-id pln_xxx
agentdeck approval list
agentdeck approval approve --approval-id apv_xxx
agentdeck dispatch --agent planner --task "设计消息账本"
agentdeck inbox --agent planner
agentdeck reply --agent planner --message-id msg_xxx --text "status: completed"
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

如果任务由另一个 agent 发起，reply 会作为 `task_reply` 投递到发起方 inbox。处理完 inbox item 后可以确认：

```bash
agentdeck ack --agent planner --inbox-id inb_xxx
```

可以用任意通信 ID 还原整条链路：

```bash
agentdeck trace --id msg_xxx
agentdeck trace --id att_xxx
agentdeck trace --id job_xxx
agentdeck trace --id rep_xxx
agentdeck trace --id inb_xxx
```

`trace` 会返回同一条 message lineage 下的 message、attempts、jobs、replies 和 inbox_items。后续会继续补自动 reply extraction 和更严格的 mailbox head-only ack。

## Leader Planning

AgentDeck 已提供第一版 plan-only Leader 能力：

```bash
agentdeck leader plan --task "设计自动 reply extraction"
agentdeck plan list
agentdeck plan show --plan-id pln_xxx
```

当前默认且仅支持本地 `fake` provider 生成确定性的结构化 plan，并写入 `.agentdeck/state/state.json` 的 `plans[]`。这个命令不会 dispatch、不会发送 tmux 输入、不会调用外部 LLM。未实现的真实 provider 会明确失败，而不是静默退回 fake。

`plan list` 返回计划摘要，适合给自然语言入口或 GUI 做列表视图；`plan show` 返回完整计划，适合审批前人工检查。

计划确认后，可以创建审批项：

```bash
agentdeck approval create-from-plan --plan-id pln_xxx
agentdeck approval list
agentdeck approval approve --approval-id apv_xxx
agentdeck approval reject --approval-id apv_xxx --reason "范围过大"
```

当前 Approval Gate MVP 只管理审批状态，不会自动 dispatch。下一阶段会在审批通过后把 plan step 转成受控 dispatch。

返回结果包含：

- `plan_id`
- `provider`
- `model`
- `status`
- `dispatch_ready`
- `plan.goal`
- `plan.steps[]`

后续接入 DeepSeek/OpenAI-compatible 或其他 API-backed provider 时，应复用同一个 provider 抽象和 plan schema。

Provider API key 后续会通过环境变量读取。以 DeepSeek 为例：

```bash
export DEEPSEEK_API_KEY="..."
```

当前版本只使用本地 `fake` provider 做 plan dry-run，不会主动调用外部 LLM。

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
