# AgentDeck Ultimate Goal Roadmap

> 目的：防止 AgentDeck 在连续开发中偏离终极目标。每一轮新功能都应该能映射到本文中的某个目标能力。

## 1. 终极目标

AgentDeck 的终极目标不是“做一堆 tmux 命令”，而是做一个 local-first 多智能体工作台：

```text
Human Operator
  -> API-backed Leader LLM
  -> role-aware Codex / Claude / other CLI Agents
  -> auditable built-in and external skills
  -> visible tmux runtime today, GUI later
  -> auditable message/job/reply/inbox ledger
  -> approval-gated execution
  -> recoverable project work history
```

用户应该能用自然语言启动一个任务，由 Leader Agent 理解目标、按需加载可审计 skill、拆解计划、指派角色、调度多个 Agent、观察结果、要求验证，并在关键动作前让人类审批。

Skill 与 Memory 是北极星的一等学习能力：AgentDeck 要像 WispTerm/Hermes 那样把可复用工作流沉淀为 skill，把长期项目事实和用户偏好沉淀为 memory；但所有 skill 都必须显式加载、记录 source/path/hash/content snapshot，并在每次 Leader 规划时把 compact skill provenance 固化到 plan 记录和 ProjectView。Memory 在 MVP 阶段必须先进入 pending suggestion queue，经 `apply-preview` 审阅后只能由人类显式运行 `memory apply --confirm` 写入长期记忆，且不会自动注入 prompt，避免变成不可追溯的隐藏提示词或权限后门。

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
| `skills list/show/import-preview/import/load-preview/load/suggest/suggestions` 与 plan `skill_context` provenance | 可审计、可回放、可被 GUI 消费的 Skill Layer |
| `docs/reference-analysis/*` 中的 Hermes/WispTerm skill 分析 | Skill Layer 和后续外源 skill allowlist 的设计输入 |
| `HISTORY.md` | 项目自身开发过程可追溯 |

这些能力还不是最终产品体验，但它们是 Leader Agent、GUI、自动调度和审批系统需要依赖的基础设施。

## 3. 当前阶段边界

当前阶段是 **MVP Control Plane**，重点是把底层契约做稳：

- agent identity
- role assignment
- tmux runtime binding
- explicit skill registry and skill snapshot contract
- plan-level skill provenance for Leader/GUI/audit replay
- message ledger
- inbox and trace
- local project state

当前阶段不应该优先做：

- 完整 GUI
- 远程 relay
- 自动学习系统，尤其是自动改写或自动安装技能
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

### Phase F: Skill Layer

目标：吸收 WispTerm 的 skill snapshot 思路和 Hermes 的技能整理/学习闭环，但先做可审计、显式加载、可回放的 Skill Layer，而不是让模型静默改写自己的行为。

应实现：

- `skills/<name>/SKILL.md` 本地技能目录。
- 内置少量基础技能，例如 planning、debugging、code-review、verification。
- `agentdeck skills list` / `agentdeck skills show --name <name>` / `agentdeck skills import-preview --path <SKILL.md>` / `agentdeck skills import --path <SKILL.md>` / `agentdeck skills load-preview --name <name> --agent <id> --purpose <text>` / `agentdeck skills load --name <name>` / `agentdeck skills suggest` / `agentdeck skills suggestions`。
- `agentdeck memory suggest --summary <summary> --rationale <rationale> --source <source>` / `agentdeck memory suggestions` / `agentdeck memory apply-preview --suggestion-id <id>` / `agentdeck memory apply --suggestion-id <id> --confirm`。
- `agentdeck learn review --plan-id <id>` 只读复盘已有 plan/reply/artifact，生成显式 `skills suggest` / `memory suggest` 后续命令，不直接写 suggestion queue。
- `agentdeck contract learning-review` / `--example` 把学习回顾响应、skill suggestion、memory suggestion 和 control 字段暴露给 GUI/TUI discovery。
- `agentdeck leader chat --message "学习复盘 pln_xxx"` 以自然语言嵌入同源 `learning_review_card` 和过滤到 `scope=learning_review` 的 control registry，仍只建议显式 suggestion 命令，不自动入队。
- skill metadata：name、description、source、path、version/hash、allowed_placeholders、required_tools、risk。
- 每次 Leader/Worker 加载 skill 时，把 path、hash、content snapshot 和使用者写入 state，保证历史可回放。
- 支持外源 skill 目录或导入包，但默认先走只读 import preview，展示 source、target、hash、覆盖状态和 GUI-ready 控制项；显式 import/allowlist 后仍需先走可对话触发的 load preview，看清 agent、purpose、hash 和显式 load command，再由人类执行 load 才能进入 Leader/Worker 上下文，不自动执行远程安装脚本，也不静默把 skill 注入提示词。
- Hermes 式后台 reviewer 只能提出 pending `skill_suggestion`，进入 `skill_suggestions[]` 队列和审计事件，不能直接创建、覆盖、删除、导入、加载或自动启用技能。
- Hermes 式后台 reviewer 只能提出 pending `memory_suggestion`，进入 `memory_suggestions[]` 队列和审计事件，不能直接写 `.agentdeck/memory/*.md`、不能自动注入 Leader/Worker prompt；长期 memory 只能经人类审阅 `apply-preview` 后显式 `memory apply --confirm` 落地。
- MVP 阶段的 reviewer 先以 `learn review` 的只读 learning card 形式存在：它只基于已有 run 事实生成候选命令，是否真正入队由人类显式执行。

验收标准：

- Leader plan 或 worker task 可以引用一个已加载 skill，并在 trace 中看到 skill snapshot。
- 同名 skill 更新后，历史 run 仍能还原当时使用的内容。
- 外源 skill 必须有 provenance、hash、人类确认入口和覆盖前 preview。
- skill 不能绕过 approval、runtime safety 或 tool 权限。

## 5. 每轮开发的防跑偏检查

每次开发前先问：

1. 这项能力服务 Leader 调度、多 Agent 通信、可见 runtime、审批、恢复、GUI 中的哪一个？
2. 是否服务可复用 skill、外源生态或可回放工作流？如果是，是否有 snapshot、provenance 和权限边界？
3. 是否能写入 state 并被 trace？
4. 是否需要更新 HISTORY？
5. 是否会绕过人类审批？
6. 是否把 tmux 当 runtime 后端，而不是业务事实源？
7. 是否过早引入 GUI、远程、provider 矩阵或自动学习复杂度？

如果一个功能答不上第 1 点，就先不要做。

## 6. 推荐下一步

下一步建议做 **Phase A: Leader Agent MVP** 的第一刀：

```text
agentdeck leader plan --task <text>
```

它先不自动 dispatch，只调用配置的 Leader LLM provider 或 dry-run fake provider 生成结构化 plan，并写入 state。这样项目就从“用户手动调度多个 agent”前进到“Leader Agent 开始规划多 agent 协作”。
