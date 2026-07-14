# AgentDeck Ultimate Goal Roadmap

> **New product north star:** [product-north-star.md](product-north-star.md) defines the approved protocol-native product direction. This document remains the historical capability roadmap and implementation trace; when the two differ, the product north star governs new product decisions.

> 目的：防止 AgentDeck 在连续开发中偏离终极目标。每一轮新功能都应该能映射到本文中的某个目标能力。

## 2026-07-13 Phase 3 M1 落地状态

Phase 3 M1 前台持续会话已经实现：裸 `agentdeck` 是真实 TTY 中的主交互入口，`leader chat --message` 退居脚本化/调试入口；ConversationSession 支持项目初始化 preview、显式 API/Agent-CLI Leader、确定性无 LLM intent、自然语言 Mission preview 与精确确认、ACP/tmux Worker transport 事实、single-writer ownership，以及 ProjectView/Workbench/contract 观察面。M1 复用现有 Mission、审批、workflow、ledger、trace 与恢复内核，没有替换历史能力。

## 2026-07-14 Phase 3 M2 落地状态

M2 project-local daemon 已实现：确认后的 frozen Mission 由单项目 authoritative
daemon 在客户端断开后继续推进；AgentDeck 通过显式 ACP/tmux transport 调度，
在前序 compact handoff 持久化后才启动下一 Worker，并在 permission、歧义、
ownership、drift 与 safety 边界暂停。bare `agentdeck` 可确定性重连，已有项目
通过显式 migration preview/confirm 迁移。九点 crash matrix 和 deterministic
product acceptance 通过；真实 CLI Leader/Worker rehearsal 的 blocker 如实记录于
`docs/validation/2026-07-13-phase3-m2-project-daemon.md`，未宣称 live PASS。

完整 transcript 恢复、A2A、remote daemon、global roaming、Workspace Client、
通知、自动 install/auth 与原生同会话 TUI attach 仍是后续独立里程碑。

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

Skill 与 Memory 是北极星的一等学习能力：AgentDeck 要像 WispTerm/Hermes 那样把可复用工作流沉淀为 skill，把长期项目事实和用户偏好沉淀为 memory；但所有 skill 都必须显式加载、记录 source/path/hash/content snapshot，并在每次 Leader 规划时把 compact skill provenance 固化到 plan 记录和 ProjectView。Skill 也是未来生态接口：内置 skill 用来沉淀稳定高频工作流，外源 skill 可以进入项目，但必须先经过只读 preview、hash/provenance 展示、显式 import、显式 load 和审计，不能静默安装或自动启用。Skill suggestion 在 MVP 阶段必须先进入 pending queue，经 `draft-preview` 或自然语言 `skill_create_preview` 审阅后只能由人类显式运行 `skills create --confirm` 写入项目 skill；Memory 同样必须先进入 pending suggestion queue，经 `apply-preview` 或自然语言 `memory_apply_preview` 审阅后只能由人类显式运行 `memory apply --confirm` 写入长期记忆。已应用 memory 只通过 ProjectView `memory`、workbench `memory_context_card` 和自然语言 `memory_context` 暴露 compact 摘要，不暴露全文，不自动注入 prompt，避免变成不可追溯的隐藏提示词或权限后门。

## 2. 分层角色北极星与图片差距计划

用户给出的目标图把 AgentDeck 的终局体验进一步收束为两条原则：

1. 用户交互与深度计划分开：`frontdesk` 只负责接待、澄清、转述、确认和最终汇报；真正消耗高推理 token 的 `planner` 只处理稳定 brief；任务分解、派发和聚合交给 `orchestrator`。
2. 模型负责语义，程序负责循环：状态读取、锁、轮次推进、异常停机、审批和提交规则由 AgentDeck 程序内核执行，不交给任何一个 LLM 自由发挥。

目标分层如下：

| 层级 | 角色 | 推荐 provider | 生命周期 | 责任边界 |
| --- | --- | --- | --- | --- |
| 用户交互层 | `frontdesk` | Gemini/Qwen/轻量 provider，未来也可用任意便宜模型 | 常驻，不 unload | 接收用户自然语言、澄清目标、压缩成 brief、展示最终汇报；不得创建 plan、审批、dispatch 或发送 tmux 输入 |
| 用户交互层 | `broker` 可选 | Gemini/Qwen/规则引擎 | 按需 | 过滤不阻塞的问题，为缺省值给出建议；不得代替人类审批 |
| 计划编排层 | `planner` | Codex 高推理档或其他强推理 provider | 常驻；每轮从文件/state 重水化，不靠聊天记忆 | 产出宏观计划、验收标准、风险和任务 brief；不直接 dispatch worker |
| 计划编排层 | `orchestrator` | Claude 或其他工具调用密集型 provider | 常驻 | 把 planner brief 拆成可执行任务，选择 worker、创建审批、聚合结果；不得绕过 approval gate |
| 程序化 loop | `agentic-loop-workflow` | AgentDeck Python 内核 | 每轮运行一次，可恢复 | 读取 authoritative state，驱动一轮 `plan -> approval -> dispatch -> capture -> review -> next`，状态异常时停止并要求人类处理 |
| 工作层 | `coder` | Claude/Codex/其他 CLI Agent | 任务级动态加载，完成后 release | 在隔离 worktree 或指定工作区完成任务包；只吃当前任务 brief、相关文件和必要 skill，不吃全局历史 |
| 工作层 | `code_reviewer` | Codex/其他审查型 provider | 任务级动态加载，完成后 release | 独立审查 coder 产物、测试和风险；与 coder 上下文隔离 |
| 验收层 | `round_reviewer` | Codex/其他审查型 provider | 按需 | 对 planner 的验收类型、整轮结果和是否继续下一轮打分 |

这张图对当前 AgentDeck 的差距要求：

- 已具备底座：provider-agnostic Leader、Codex/Claude CLI worker、tmux runtime、role prompt、审批队列、message/job/reply/inbox ledger、trace、skill/memory provenance、GUI-ready contract。
- 缺少 `frontdesk`：现在 `leader chat` 已经能做 help、run_start、skill/memory route，但还没有一个只读接待层把用户请求整理为 brief 并推荐下一步命令。
- `planner` 与 `orchestrator` 还混在一个 Leader 逻辑身份里：当前 Leader 能 plan/review/apply action，但还没有明确区分“宏观规划者”和“执行编排者”的 state、card 与 provider binding。
- loop 还不够程序化：已有 `run_start`、`run_progress`、`continue` 等卡片，但缺少一个明确的 run-once loop 命令，根据 authoritative state 推进一轮并在异常状态停止。
- worker 生命周期还不够任务级：现在可以 spawn/capture/send/stop，但还需要 worktree isolation、任务完成 release、上下文清零和产物归档。
- reviewer 分层还不完整：已有 learning review、trace、leader summary，但还缺少 task-level `code_reviewer` 和 round-level `round_reviewer` 的显式角色、卡片和验收分数。
- GUI 还缺少角色拓扑：workbench 已有很多卡片，但未来应展示 frontdesk/planner/orchestrator/coder/reviewer 的角色、provider、生命周期、当前任务和阻塞点。

执行路线：

### Phase G1: Frontdesk Intake

新增只读 `frontdesk` 自然语言入口和 `frontdesk_card`。它可以把用户原话整理为 intake summary、分类为 plan/run/help/skill/memory 等候选路径，并推荐显式下一步命令，例如 `agentdeck leader plan --task <goal>` 或 `agentdeck leader chat --message "帮助"`。

验收标准：

- 不调用 Leader provider。
- 不创建 plan/action/approval/message/job/inbox。
- 不读取 tmux，不发送 tmux 输入。
- 输出 GUI-ready `frontdesk_card`、`intent_card` 和可审计 chat turn。

### Phase G2: Planner / Orchestrator Split

把当前逻辑 Leader 拆成两个可配置语义角色：`planner` 负责高推理计划和验收标准，`orchestrator` 负责任务分解、审批创建、worker 选择和结果聚合。二者都仍是 `agent_id=leader` 体系下的逻辑子角色，不复用 worker pane。

验收标准：

- state 中能看到 planner brief 与 orchestrator actions 的来源。
- 两者都可绑定不同 provider/model。
- planner 输出不直接 dispatch；orchestrator 也必须经过 approval gate。

### Phase G3: Programmatic Run-Once Loop

新增程序化 loop 命令，只推进一轮：读取 plan/task/approval/job/inbox/trace 的 authoritative state，决定下一步应该 plan、等待审批、dispatch、capture、review、summarize 还是停止。

验收标准：

- loop 不依赖对话记忆判断状态。
- 每轮只有一个明确 state transition。
- 异常状态、缺少审批、worker 未就绪或 trace 不一致时停止并生成 blocker。

### Phase G4: Task-Scoped Worker Lifecycle

把 coder/reviewer 变成任务级动态节点：按任务准备 brief、必要文件、必要 skill 和 worktree；任务完成后 release pane/context，只保留 artifact、reply、trace 和 summary。

验收标准：

- coder 不需要读取全局历史。
- 每个任务能追踪 worktree、pane、skill snapshot、artifact 和测试结果。
- release 不会删除未经确认的用户改动。

### Phase G5: Reviewer Roles

增加 `code_reviewer` 和 `round_reviewer` 的显式角色与卡片。`code_reviewer` 审查单个任务产物；`round_reviewer` 对整轮结果、planner 验收标准和是否进入下一轮给出结论。

验收标准：

- reviewer 与 coder 上下文隔离。
- reviewer 输出进入 trace 和 inbox，而不是只停留在 pane 文本。
- round reviewer 可以阻止下一轮自动推进。

### Phase G6: Role Topology GUI

在 ProjectView/workbench contract 中形成角色拓扑，让 GUI/TUI 能显示 frontdesk、planner、orchestrator、coder、code_reviewer、round_reviewer 的 provider、生命周期、当前状态、阻塞点和下一步控制。

验收标准：

- GUI 不直接扫描 tmux 或私有 state 文件。
- 所有按钮来自 contract controls，保留 safety/enabled/blocker。
- 人类可以一眼看出哪个角色在思考、等待审批、执行、审查或已 release。

## 3. 为什么当前开发没有跑偏

当前已经实现的能力都对应终极目标中的底座：

| 已实现能力 | 对应终极目标 |
| --- | --- |
| `agent spawn/capture/send/stop` | 可见、可控、可恢复的多 Agent 终端 runtime |
| `role` / `role_prompt` / `assign-role` | Codex/Claude 等 Agent 能承担明确角色 |
| `dispatch` | Leader 或用户可把任务按角色投递给目标 Agent |
| `message -> attempt -> job -> inbox` | CCB 式多 Agent 通信账本的最小形态 |
| `reply` / `ack` | 请求-回复-确认闭环 |
| `trace` | 多 Agent 调试、审计和恢复所需的 lineage |
| `skills list/show/import-preview/import/load-preview/load/suggest/suggestions/draft-preview/create` 与 plan `skill_context` provenance | 可审计、可回放、可被 GUI 消费的 Skill Layer |
| ProjectView `memory` 与 `memory_context_card` | 已应用长期记忆的只读可见面，不做隐藏 prompt 注入 |
| `docs/reference-analysis/*` 中的 Hermes/WispTerm skill 分析 | Skill Layer 和后续外源 skill allowlist 的设计输入 |
| `HISTORY.md` | 项目自身开发过程可追溯 |

这些能力还不是最终产品体验，但它们是 Leader Agent、GUI、自动调度和审批系统需要依赖的基础设施。

## 4. 当前阶段边界

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

## 5. 下一阶段目标

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
- `agentdeck skills list` / `agentdeck skills show --name <name>` / `agentdeck skills import-preview --path <SKILL.md>` / `agentdeck skills import --path <SKILL.md>` / `agentdeck skills load-preview --name <name> --agent <id> --purpose <text>` / `agentdeck skills load --name <name>` / `agentdeck skills suggest` / `agentdeck skills suggestions` / `agentdeck skills draft-preview --suggestion-id <id>` / `agentdeck skills create --suggestion-id <id> --confirm`。
- `agentdeck memory suggest --summary <summary> --rationale <rationale> --source <source>` / `agentdeck memory suggestions` / `agentdeck memory apply-preview --suggestion-id <id>` / `agentdeck memory apply --suggestion-id <id> --confirm`。
- `agentdeck learn review --plan-id <id>` 只读复盘已有 plan/reply/artifact，生成显式 `skills suggest` / `memory suggest` 后续命令，不直接写 suggestion queue。
- `agentdeck contract learning-review` / `--example` 把学习回顾响应、skill suggestion、memory suggestion 和 control 字段暴露给 GUI/TUI discovery。
- `agentdeck leader chat --message "学习复盘 pln_xxx"` 以自然语言嵌入同源 `learning_review_card` 和过滤到 `scope=learning_review` 的 control registry，仍只建议显式 suggestion 命令，不自动入队。
- `agentdeck leader chat --message "创建 skill 建议 sgs_xxx"` 以自然语言嵌入只读 `skill_create_preview_card`，展示拟写入的 `SKILL.md`、hash、目标路径和显式 create 命令，但不创建文件、不修改 suggestion queue、不加载 skill。
- `agentdeck leader chat --message "预览 memory 建议 mem_xxx"` 以自然语言嵌入只读 `memory_apply_preview_card`，展示目标 memory 文件、拟追加 Markdown、是否会创建文件和显式 apply 命令，但不写 `.agentdeck/memory/*.md`、不更新 suggestion status、不注入 prompt。
- skill metadata：name、description、source、path、version/hash、allowed_placeholders、required_tools、risk。
- 每次 Leader/Worker 加载 skill 时，把 path、hash、content snapshot 和使用者写入 state，保证历史可回放。
- 支持外源 skill 目录或导入包，但默认先走只读 import preview，展示 source、target、hash、覆盖状态和 GUI-ready 控制项；显式 import/allowlist 后仍需先走可对话触发的 load preview，看清 agent、purpose、hash 和显式 load command，再由人类执行 load 才能进入 Leader/Worker 上下文，不自动执行远程安装脚本，也不静默把 skill 注入提示词。
- Hermes 式后台 reviewer 只能提出 pending `skill_suggestion`，进入 `skill_suggestions[]` 队列和审计事件；人类可以用 `draft-preview` 或自然语言 `skill_create_preview` 审阅拟写入的 `SKILL.md` 内容，再用显式 `skills create --confirm` 落地项目 skill。reviewer、preview 和 create 都不能自动加载或启用技能。
- Hermes 式后台 reviewer 只能提出 pending `memory_suggestion`，进入 `memory_suggestions[]` 队列和审计事件，不能直接写 `.agentdeck/memory/*.md`、不能自动注入 Leader/Worker prompt；长期 memory 只能经人类审阅 `apply-preview` 后显式 `memory apply --confirm` 落地。
- MVP 阶段的 reviewer 先以 `learn review` 的只读 learning card 形式存在：它只基于已有 run 事实生成候选命令，是否真正入队由人类显式执行。

验收标准：

- Leader plan 或 worker task 可以引用一个已加载 skill，并在 trace 中看到 skill snapshot。
- 同名 skill 更新后，历史 run 仍能还原当时使用的内容。
- 外源 skill 必须有 provenance、hash、人类确认入口和覆盖前 preview。
- skill 不能绕过 approval、runtime safety 或 tool 权限。

## 6. 每轮开发的防跑偏检查

每次开发前先问：

1. 这项能力服务 Leader 调度、多 Agent 通信、可见 runtime、审批、恢复、GUI 中的哪一个？
2. 是否服务可复用 skill、外源生态或可回放工作流？如果是，是否有 snapshot、provenance 和权限边界？
3. 是否能写入 state 并被 trace？
4. 是否需要更新 HISTORY？
5. 是否会绕过人类审批？
6. 是否把 tmux 当 runtime 后端，而不是业务事实源？
7. 是否过早引入 GUI、远程、provider 矩阵或自动学习复杂度？

如果一个功能答不上第 1 点，就先不要做。

## 7. 推荐下一步

下一步建议做 **Phase G1: Frontdesk Intake** 的第一刀：

```text
agentdeck leader chat --message "frontdesk <goal>"
```

它先不自动 planning，也不 dispatch，只把用户原话整理成只读 `frontdesk_card`，推荐显式下一步命令，例如 `agentdeck leader plan --task <goal>`。这样项目就从“自然语言入口直接落到 planner/provider”前进到“用户交互层与深度计划层分离”，为后续 planner/orchestrator split 打底。
