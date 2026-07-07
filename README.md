# AgentDeck: Local Multi-Agent Terminal Workbench

AgentDeck 是一个正在搭建中的本地多智能体终端工作台。它的目标是让任意可通过 API 调用的 LLM 作为 Leader Agent，把任务分发给多个 Worker Agent，按需加载可审计 skill，并在 tmux 可见终端中执行、观察、审批、恢复和审计。DeepSeek 可以作为首个默认 provider，但不是架构绑定点。控制模式会保持类似 Codex 的梯度：默认可 ask/inspect，由人类显式审批或授权后再进入更高自治度的执行。

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
- Leader provider adapter，支持本地 `fake`、DeepSeek、OpenAI-compatible、Codex CLI 和 Claude Code CLI plan provider

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
agentdeck continue
agentdeck workbench
agentdeck controls
agentdeck skills list
agentdeck skills show --name planning
agentdeck skills import-preview --path /path/to/SKILL.md
agentdeck skills import --path /path/to/SKILL.md
agentdeck skills load-preview --name planning --agent planner --purpose "decompose implementation work"
agentdeck skills load --name planning --agent leader --purpose "plan decomposition"
agentdeck skills suggest --name incident-review --summary "Review incident response evidence." --rationale "repeatable review checklist" --source human
agentdeck skills suggestions
agentdeck memory suggest --summary "Keep approval-gated worker dispatch." --rationale "project safety preference" --source human
agentdeck memory suggestions
agentdeck memory apply-preview --suggestion-id mem_xxx
agentdeck memory apply --suggestion-id mem_xxx --confirm
agentdeck learn review --plan-id pln_xxx
agentdeck contract list
agentdeck contract memory
agentdeck contract memory --example
agentdeck contract learning-review
agentdeck contract learning-review --example
agentdeck contract agent-runtime
agentdeck contract agent-runtime --example
agentdeck contract project-view
agentdeck contract project-view --example
agentdeck contract leader-chat
agentdeck contract leader-chat --example
agentdeck contract continue
agentdeck contract continue --example
agentdeck contract controls
agentdeck contract controls --example
agentdeck contract leader-actions
agentdeck contract leader-actions --example
agentdeck contract leader-review
agentdeck contract leader-review --example
agentdeck contract leader-action
agentdeck contract leader-action --example
agentdeck contract approvals
agentdeck contract approvals --example
agentdeck contract inbox
agentdeck contract inbox --example
agentdeck contract trace
agentdeck contract trace --example
agentdeck contract artifacts
agentdeck contract artifacts --example
agentdeck leader status
agentdeck agent list
agentdeck agent ready
agentdeck agent spawn-ready --confirm
agentdeck agent spawn --agent planner
agentdeck agent terminal --agent planner
agentdeck agent capture --agent planner --lines 200
agentdeck agent send --agent planner --text "继续"
agentdeck agent refresh
agentdeck agent stop --agent planner
agentdeck agent assign-role --agent planner --role "architecture planning" --role-prompt "你负责架构规划和任务拆解。"
agentdeck leader chat --message "帮我设计自动 reply extraction"
agentdeck leader chat --message "查看 Leader 状态"
agentdeck leader chat --message "查看已加载技能"
agentdeck leader chat --message "查看 skill 建议"
agentdeck leader chat --message "学习复盘 pln_xxx"
agentdeck leader chat --message "预览导入 skill /path/to/SKILL.md"
agentdeck leader chat --message "预览加载 skill planning 给 planner 用于 decompose implementation work"
agentdeck leader chat --message "查看运行进度"
agentdeck leader chat --message "查看产物"
agentdeck leader chat --message "打开工作台"
agentdeck leader chat --message "查看账本"
agentdeck leader chat --message "查看角色"
agentdeck leader chat --message "查看队列"
agentdeck leader chat --message "查看 runtime"
agentdeck leader chat --message "刷新 runtime"
agentdeck leader chat --message "启动所有 agent"
agentdeck leader chat --message "启动 planner"
agentdeck leader chat --message "打开 planner 终端"
agentdeck leader chat --message "发送给 planner：继续"
agentdeck leader chat --message "停止 planner"
agentdeck leader chat --message "查看 planner 输出"
agentdeck leader chat --message "查看 planner inbox"
agentdeck leader chat --message "追踪 planner 当前 inbox"
agentdeck leader chat --message "查看当前 inbox"
agentdeck leader chat --message "追踪 msg_xxx"
agentdeck leader chat --message "追踪 art_xxx"
agentdeck leader chat --message "确认 planner 当前 inbox"
agentdeck leader chat --message "确认当前 inbox"
agentdeck leader chat --message "查看审批"
agentdeck leader chat --message "批准当前审批"
agentdeck leader chat --message "派发当前审批"
agentdeck leader chat --message "派发所有已审批"
agentdeck leader chat --message "切换到审批模式"
agentdeck leader chat --message "回到 ask 模式"
agentdeck leader chat --message "开启 autonomous 完全放权"
agentdeck leader chat --message "切换 Leader 到 Codex CLI"
agentdeck leader chat --message "使用 Claude Code 做 Leader"
agentdeck leader chat-history
agentdeck leader set-provider --provider codex-cli --model codex-default
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
agentdeck approval dispatch-ready --confirm
agentdeck dispatch --agent planner --task "设计消息账本"
agentdeck inbox --agent planner
agentdeck reply --agent planner --message-id msg_xxx --text "status: completed"
agentdeck capture-reply --agent planner --message-id msg_xxx
agentdeck ack --agent planner --inbox-id inb_xxx
agentdeck trace --id msg_xxx
agentdeck events --limit 20
agentdeck artifacts
```

`agentdeck trace --id <id>` 和自然语言 `agentdeck leader chat --message "追踪 msg_xxx"` 都会返回同源通信链路卡；trace 卡片包含只读 `controls[]`，自然语言 trace 响应还会附带过滤到 `scope=trace` / `card=trace_card` 的 `control_registry_card`，方便 GUI/TUI 直接高亮“Inspect trace”命令。它们都只是检查入口，不会自动执行 trace、capture、ack、dispatch 或 tmux 输入。

`agentdeck doctor` 会检查 tmux、项目配置和当前配置的 Leader provider readiness。输出里的 `configured_leader` 包含 agent_id、provider、model、approval_mode、provider_backend、provider_transport、`leader_backend`、supported、ready、missing_env、detail、command_path 和 setup_commands；顶层还会给出 `deepseek`、`openai_compatible`、`codex_cli` 和 `claude_cli` 四个 provider check，方便 GUI 一次性渲染所有 Leader backend 的可用性。API-backed provider 的 `provider_backend=api`、`provider_transport=http`、`command_path=null`；CLI-backed provider 的 `provider_backend=cli`、`provider_transport=subprocess`，可用时显示实际命令路径，例如 `/opt/bin/codex`；fake provider 使用 `local/local`。`configured_leader.leader_backend` 只是 setup provenance，不代表 provider ready、tmux pane 或执行授权。它只暴露缺失的环境变量名、CLI command path 和 placeholder setup 命令，不暴露密钥值。顶层 `ok=false` 表示当前项目还缺少运行前置条件，例如默认 DeepSeek Leader 缺少 `DEEPSEEK_API_KEY`。

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

`agentdeck skills list` 会发现内置 skill 和 `.agentdeck/skills/<name>/SKILL.md` 项目本地 skill，并返回 `import_command_template`、列表级 `controls[]` 以及每个 skill 的 show/load `controls[]`，方便 GUI 直接渲染 skill registry；`agentdeck skills import-preview --path /path/to/SKILL.md` 会只读解析外源 `SKILL.md`，返回 source、目标项目路径、hash、是否覆盖，以及 import / force_import / show_after_import controls，不复制文件、不追加事件、不 load、不调用 provider、不读取 tmux 或修改 runtime；自然语言 `agentdeck leader chat --message "预览导入 skill /path/to/SKILL.md"` 会复用同一张 `skill_import_preview_card`，只记录 chat turn 和审计事件，并把下一步停在显式 import/force import 命令。`agentdeck skills import --path /path/to/SKILL.md` 会把外部 `SKILL.md` 显式复制到项目 `.agentdeck/skills/<name>/SKILL.md` 并追加 `skill_imported` 审计事件，默认不覆盖同名 skill，必须加 `--force` 才能覆盖。`agentdeck skills show --name <name>` 只读返回 skill 内容、source、path、hash、required_tools、risk 和同源 controls；`agentdeck skills load-preview --name <name> --agent <id> --purpose <text>` 与自然语言 `agentdeck leader chat --message "预览加载 skill planning 给 planner 用于 decompose implementation work"` 会先返回只读 `skill_load_preview` / `skill_load_preview_card`，展示目标 agent、用途、skill summary、显式 load command 和 show/load controls，不写入 `skill_loads[]`、不追加 `skill_loaded`、不调用 provider、不读取 tmux、不创建 plan/action/approval/message/job/inbox；`agentdeck skills load --name <name>` 才会把该 skill 的 path/source/hash/content snapshot、调用者和用途写入 `skill_loads[]`，并追加 `skill_loaded` 审计事件。`agentdeck skills suggest --name <name> --summary <summary> --rationale <rationale> --source <source>` 会把可复用工作流沉淀为 pending suggestion，写入 `skill_suggestions[]` 并追加 `skill_suggested` 审计事件，但不创建 `SKILL.md`、不 import、不 load、不调用 provider、不读取 tmux；`agentdeck skills suggestions` 与自然语言 `agentdeck leader chat --message "查看 skill 建议"` 都只读列出 suggestion queue，并通过 `skill_suggestions_card` 暴露 count、pending_count、items 和 inspect controls，供 GUI 或人类决定后续是否手动创建/导入/load；`agentdeck skills draft-preview --suggestion-id <id>` 会把 pending suggestion 只读转换为拟写入的 `SKILL.md` 内容、hash、目标路径和显式 create control，不创建文件、不更新 suggestion 状态、不追加事件、不 import/load、不调用 provider 或读取 tmux；自然语言 `agentdeck leader chat --message "创建 skill 建议 sgs_xxx"` 会进入只读 `mode=skill_create_preview`，嵌入同源 `skill_create_preview_card` 并返回显式 `agentdeck skills create --suggestion-id <id> --confirm`，但不会创建文件或修改 suggestion 队列；`agentdeck skills create --suggestion-id <id> --confirm` 才会把同一份草稿写入项目 `.agentdeck/skills/<name>/SKILL.md`，标记 suggestion 为 `created` 并追加 `skill_created`，但不自动 load、不调用 provider、不读取 tmux 或修改 runtime。`agentdeck memory suggest --summary <summary> --rationale <rationale> --source <source>` 会把值得长期记住的项目事实或偏好写入 pending `memory_suggestions[]` 并追加 `memory_suggested` 审计事件，`agentdeck memory suggestions` 只读列出队列，并在每条 pending item 上返回 `apply_preview` / `apply_memory` controls；`agentdeck memory apply-preview --suggestion-id <id>` 会只读展示目标 `.agentdeck/memory/*.md`、是否会创建文件、将追加的 Markdown 内容和显式 apply control，不写 memory 文件、不修改 suggestion 状态、不追加事件、不注入 Leader/Worker prompt、不调用 provider 或读取 tmux；`agentdeck memory apply --suggestion-id <id> --confirm` 才会把同一份 Markdown 追加到 memory 文件，更新 suggestion 为 `applied`，写入 `applied_at` / `applied_path`，并追加 `memory_applied` 审计事件，缺少 `--confirm` 或重复 apply 都会拒绝。`agentdeck learn review --plan-id <id>` 是 Hermes 式学习复盘的只读入口：它复用现有 Leader summary / plan status / reply / artifact 事实，生成 GUI-ready `skills suggest` 与 `memory suggest` 后续命令，但不写 `skill_suggestions[]`、不写 `memory_suggestions[]`、不调用 provider、不读取 tmux、不自动创建 skill 或 memory；自然语言 `agentdeck leader chat --message "学习复盘 pln_xxx"` 会嵌入同源 `learning_review_card`，附带过滤到 `scope=learning_review` 的 `control_registry_card`，并把 selection 指向 `suggest_skill` control，但仍不执行任何 suggestion 命令。Skill load 不调用 provider、不读取 tmux、不发送输入、不改变权限；它会进入 ProjectView 的 `skills` 摘要，并在 `agentdeck workbench` 与自然语言 `agentdeck leader chat --message "查看已加载技能"` 中作为 `skill_context_card` 暴露给 Leader/GUI 使用。`agentdeck leader plan`、自然语言 plan 和 `agentdeck run --task` 会把 compact loaded skill 摘要传入 API-backed / CLI-backed Leader prompt，并把同一份 compact `skill_context` 固化到 plan record、ProjectView `plans.items[]` 和 `agentdeck plan status`；这些位置不会传完整 `content_snapshot`，也不会把 skill 当成 dispatch 或执行授权。`agentdeck dispatch` 和 `agentdeck approval dispatch` 会把目标 agent 已显式加载的 skill `content_snapshot` 注入该 worker 的任务 prompt，让 Worker 能真正按 skill 工作；同时 message 会保存 compact `prompt_skill_context`，并通过 ProjectView `messages.items[]`、workbench `ledger_card.messages.items[]` 和 `trace` 暴露给 GUI/审计使用。

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
agentdeck workbench
agentdeck leader status
agentdeck controls
agentdeck controls --scope runtime --enabled-only
agentdeck controls --card terminal_session_card
agentdeck controls --query terminal
agentdeck controls --control-id leader:leader_card:chat:leader:6fd87159ea
agentdeck agent list
agentdeck agent stop --agent planner
agentdeck leader chat --message "帮我设计自动 reply extraction"
agentdeck leader chat --message "查看 Leader 状态"
agentdeck leader chat --message "查看已加载技能"
agentdeck leader chat --message "查看 skill 建议"
agentdeck leader chat --message "学习复盘 pln_xxx"
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
agentdeck approval dispatch-ready --confirm
agentdeck dispatch --agent planner --task "设计消息账本"
agentdeck inbox --agent planner
agentdeck reply --agent planner --message-id msg_xxx --text "status: completed"
agentdeck capture-reply --agent planner --message-id msg_xxx
agentdeck ack --agent planner --inbox-id inb_xxx
agentdeck trace --id msg_xxx
python -m compileall src
```

## Agent Runtime Commands

当前 tmux runtime MVP 已支持九个 agent 操作命令：

```bash
agentdeck agent list
agentdeck agent ready
agentdeck agent spawn-ready --confirm
agentdeck agent spawn --agent planner
agentdeck agent terminal --agent planner
agentdeck agent capture --agent planner --lines 200
agentdeck agent send --agent planner --text "继续"
agentdeck agent refresh
agentdeck agent stop --agent planner
```

这些命令的约束：

- `agent_id` 来自 `.agentdeck/config.toml`。
- `ready` 是只读启动准备卡：复用 workbench runtime card，汇总 total/running/not_running/all_running，列出所有尚未 running agent 的 `spawn_commands` 和 `spawn_ready_command`，并暴露 GUI-ready `controls[]`；当多个 agent 未 running 时，`next_command` 会指向显式 `agentdeck agent spawn-ready --confirm`，只有一个未 running 时指向对应单 agent spawn，全部 running 时指向 `agentdeck approval dispatch-ready --confirm`。
- `spawn-ready --confirm` 会显式批量启动所有尚未 `running` 的 configured agents，跳过已 running 的 pane，逐个写入 `agent_spawned` 事件，并追加一次 `agent_spawn_ready_completed` 事件；不带 `--confirm` 必须失败且不得写 state 或创建 pane。
- `spawn` 会创建项目 tmux session，并记录 `agent_id -> pane_id` 绑定。
- `spawn` 会拒绝重复启动已经处于 `running` 状态且已有 `pane_id` 的 agent。
- `terminal` 是只读可见终端卡：返回 tmux attach/select-pane 命令、capture/send/stop/inbox/refresh 命令和 controls，方便人类或 GUI 打开对应 pane；它不读取 pane 输出、不发送输入、不写 state。
- `capture` 和 `send` 只面向已经 spawn 的 agent。
- `refresh` 会显式检查 state 中记录为 `running` 的 tmux pane 是否仍存在；丢失的 pane 会被标记为 `stale`，并写入审计事件。
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

`agentdeck workbench` 的 `role_card.agents[]` 会同时暴露每个 agent 的 `assign_command` 和 disabled `controls[]` 模板；`agentdeck controls` 会把它们索引为 `scope=role` / `kind=assign_role`。自然语言 `agentdeck leader chat --message "查看角色"` 会附带过滤到 `role_card` 的 `control_registry_card`；自然语言角色指派会让 registry selection 指向目标 agent 的 disabled assign-role 模板，同时把填好参数后的显式 `agentdeck agent assign-role ...` 放在顶层 `next_command` 和 intent next control。GUI 可以把这个 control 渲染成角色编辑表单，但必须先由人类填写具体 `role` 和 `role_prompt`，再显式运行完成后的命令。

`agentdeck workbench` 的 `leader_card.controls[]` 也会进入 `agentdeck controls`，以 `scope=leader` 暴露 `chat`、`continue`、`review`、`actions`、`refresh`、`leader_status` 和完整 ProjectView `status`。其中 `refresh` 与 `leader_status` 都指向窄版 `agentdeck leader status`：`refresh` 适合 GUI 顶栏刷新当前 Leader/provider/recovery 状态，`leader_status` 适合命令面板打开窄版状态入口；二者都是 inspect control，不会调用 provider、读取 tmux 或写 state。

`agentdeck workbench` 的 `leader_summary_card.controls[]` 会进入 `agentdeck controls`，以 `scope=leader_summary` / `card=leader_summary_card` 暴露 `summary`、`plan_status`、`review` 和 `trace` 这些只读收尾控件；自然语言 summary mode 会附带同源 `control_registry_card`，selection 指向 `kind=summary` 的 `agentdeck leader summary --plan-id <id>` inspect control。

`agentdeck workbench` 的 `run_progress_card.controls[]` 会进入 `agentdeck controls`，以 `scope=run_progress` / `card=run_progress_card` 暴露 `plan_status`、`review`、`approval_queue`、`next`、`continue` 和 `workbench` 控件；自然语言 run-progress mode 会附带同源 `control_registry_card`，selection 指向 `kind=plan_status` 的 `agentdeck plan status --plan-id <id>` inspect control，让 GUI/TUI 可以高亮当前进度卡入口，同时保留顶层 `next_command` 作为真正下一步。

`agentdeck workbench` 的 `agent_ready_card.controls[]` 会进入 `agentdeck controls`，以 `scope=agent_ready` 暴露 `inspect`、`spawn_ready`、`refresh_runtime` 和必要时的 `dispatch_ready`；自然语言 `agentdeck leader chat --message "启动所有 agent"` 和 `"启动 planner"` 都会返回 `startup_preview_card`，把顶层显式启动命令展开成待启动 agent 清单、单 agent spawn 命令、terminal 命令、blocker 和 per-agent controls，供 GUI 在执行前确认；单 agent spawn 响应会以 `runtime_action_card(action=spawn)` 作为 primary card，附带 `startup_preview_card` 作为执行前 checklist，并返回过滤到 `runtime_action_card` 的 `control_registry_card`，用 `selection.selected_control` 指向顶层 `next_command` 对应的 spawn control，且 `selection.next_command` 等于 `runtime_action_card.command`；`agentdeck workbench` 的 `terminal_session_card.controls[]` 会进入 `agentdeck controls`，以 `scope=terminal_session` 暴露 `attach_session`、`open_controls` 和 `refresh_runtime`；每个 `terminal_session_card.terminals[]` item 自带的 GUI-ready `controls[]` 也会进入同一 scope，以 `kind=select_pane` / `agent_id=<agent>` 渲染单 pane 选择按钮。GUI 可以把它渲染成项目级终端工具条或命令面板分区：attach/open/select-pane 是 inspect，refresh/spawn 是 explicit_runtime；这些 item 只提供可见命令，不会自动 attach tmux、select pane、refresh runtime、spawn pane 或写 state。

自然语言 `agentdeck leader chat --message "启动 planner"`、`"刷新 runtime"`、`"发送给 planner：继续"` 和 `"停止 planner"` 现在都会返回 `runtime_action_card`，把项目级 refresh 或目标 agent、role、runtime status、pane_id、待发送文本、spawn/stop 动作、精确 `agentdeck agent spawn --agent <id>` / `agentdeck agent refresh` / `agentdeck agent send --agent <id> --text <text>` / `agentdeck agent stop --agent <id>` 命令和 inspect/spawn/refresh/send/stop controls 结构化出来；同一响应会附带 `runtime_card`、`terminal_session_card` 和过滤到 `runtime_action_card` 的 `control_registry_card`，单 agent spawn 还会附带 `startup_preview_card` 作为 checklist，让 GUI 可以渲染启动/刷新/发送/停止前确认按钮和目标 runtime 上下文。它仍只建议显式命令，不自动 spawn pane、不自动 refresh runtime、不发送 tmux 输入、不停止 pane、不创建 plan/action/approval/message/job/inbox，也不修改 runtime state。

当前通信路径是 MVP 形态：

```text
Human/Leader -> dispatch -> message/attempt/job/inbox -> tmux pane -> reply -> sender inbox -> ack
```

每次 dispatch 会写入 `.agentdeck/state/state.json` 的 `messages`、`attempts`、`jobs` 和目标 agent 的 `inbox`，并追加 `task_dispatched` 事件。若目标 agent 有 loaded skills，dispatch prompt 会包含这些 skill 的 full content snapshot；message 同时保存 compact `prompt_skill_context`，并在 `agentdeck status` 的 `messages.items[]`、`agentdeck workbench` 的 `ledger_card.messages.items[]` 和 `agentdeck trace` 中展示，避免 GUI 解析长 prompt 或复制完整 skill 内容。`dispatch` 和 `approval dispatch` 的 JSON 输出会包含 `trace_command`，指向同一条 message lineage；`approval dispatch` 成功响应还会嵌入目标 agent 的 `inbox_card`，复用 `agentdeck inbox --agent <id>` 队列形状，让 GUI 或自然语言壳立刻看到 worker mailbox head、trace 和 ack 入口。可以查看某个 agent 的 inbox：

```bash
agentdeck inbox --agent planner
```

`agentdeck inbox --agent <id>` 是单个 agent mailbox 的只读入口。每个 item 会包含 `controls[]`、`preview_command`、`trace_command`、`ack_command`、`is_head`、`can_ack` 和 `ack_blocker`；`controls[]` 是 GUI-ready 按钮列表，`preview_command` 指向只读 lineage 预览，`ack_command` 仍然需要人类显式执行。输出前会通过 `validate_inbox_contract()` 自校验。契约见 `docs/contracts/inbox-schema.md`，可用 `agentdeck contract inbox --example` 发现。

自然语言入口也复用同一套 mailbox/trace 契约：`agentdeck leader chat --message "追踪 planner 当前 inbox"` 会保持只读 `mode=inbox`，返回 `inbox_card`，并在能解析当前 pending head 时嵌入 `trace_card`。`intent_card.embedded_card` 会指向 `trace_card`，GUI 或终端壳可以直接渲染通信证据链，同时仍把 `next_command` 保持为显式 `agentdeck trace --id <inbox_id>`。

Agent 完成任务后，可以先用手动命令把回复写入账本：

```bash
agentdeck reply --agent planner --message-id msg_xxx --text "status: completed"
```

也可以从 agent pane 最近输出中捕获最后一个 `status:` 开头的结构化回复块：

```bash
agentdeck capture-reply --agent planner --message-id msg_xxx
```

如果任务由另一个 agent 发起，reply 会作为 `task_reply` 投递到发起方 inbox。`reply` 和 `capture-reply` 的 JSON 输出也会包含 `trace_command`，指向刚记录的 reply lineage；当结构化回复块包含 `full_output_path: <path>` 时，AgentDeck 会把该路径登记为 `artifacts[]` 产物摘要，并在成功响应与后续 `agentdeck status` / `agentdeck workbench` 账本里暴露，但不会读取文件内容。当 reply 回流到某个 agent inbox 时，成功响应会嵌入接收方的 `inbox_card`，复用 `agentdeck inbox --agent <id>` 队列形状，让 GUI 或自然语言壳立刻看到 task_reply、trace、artifact 和 ack 入口。处理完 inbox item 后可以确认：

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
agentdeck trace --id art_xxx
agentdeck trace --id inb_xxx
```

`trace` 会返回同一条 message lineage 下的 schema_version、message、plan、attempts、jobs、replies、artifacts 和 inbox_items；`message.prompt_skill_context` 会显示 dispatch 时注入给 Worker 的 compact skill provenance，如果该 message 来自已审批 plan dispatch，`plan` 还会暴露 plan provenance 和规划时可见的 compact `skill_context`，但这些 compact 字段仍不包含完整 `content_snapshot`。`artifact_id` 也能作为查询入口，但 trace 只返回产物路径摘要，不读取文件内容。输出前会通过 `validate_trace_contract()` 自校验，失败时不会打印半坏 trace。自然语言入口可以直接追踪具体通信 ID：`agentdeck leader chat --message "追踪 msg_xxx"` 或 `"追踪 art_xxx"` 会进入只读 `mode=trace`，嵌入同源 `trace_card`，并保持 `next_command=agentdeck trace --id <id>`；如果 ID 不存在，chat 会返回 `unknown trace id: <id>`，不会误创建 plan。后续会继续补更严格的 reply block 标记。

也可以直接查看当前项目的产物索引：

```bash
agentdeck artifacts
agentdeck leader chat --message "查看产物"
agentdeck contract artifacts --example
```

`agentdeck artifacts` 是只读产物索引，输出同源 ProjectView `artifacts` 摘要、`trace_command_template`、ProjectView contract、trace contract 入口和 GUI-ready `controls[]`；它不会读取产物文件内容，不读取 tmux pane，不调用 Leader provider，也不会修改 state。自然语言入口 `agentdeck leader chat --message "查看产物"` 会进入只读 `mode=artifacts`，嵌入同一张 `artifacts_card`，并把下一步保持为 `agentdeck artifacts`；同一响应还会附带过滤到 `scope=artifacts` / `card=artifacts_card` 的 `control_registry_card`，selection 指向 `kind=inspect` 的 `agentdeck artifacts` control。输出前会通过 `validate_artifacts_contract()` 自校验，GUI/TUI 可以用 `agentdeck contract artifacts` 发现字段形状，再用每个 item 的 `trace_command` 或 `agentdeck trace --id <artifact_id>` 跳回完整通信链路。

## ProjectView and Status

`agentdeck status` 是当前面向 CLI、自然语言入口和未来 GUI 的统一只读 ProjectView。它会返回项目配置、Leader、agents runtime binding、state_path，以及 plans、approvals、messages、jobs、replies、artifacts、chat_turns、leader_errors、leader_actions、skills、inbox、recovery 的轻量摘要。顶层 `leader.leader_backend` 会暴露当前配置 provider/model 的 normalized logical Leader identity，供 GUI 在没有 plan 时也能识别 fake、API-backed 或 CLI-backed Leader 来源；它不是 tmux pane 绑定、provider readiness 证明或执行授权。

`agentdeck leader status` 是面向终端和未来 GUI 的窄版 Leader 状态卡。它先校验 ProjectView，再复用 workbench 的 `provider_health`，输出当前 logical Leader、provider readiness/setup、latest_plan、队列计数、recovery、next_command 和 inspect/next controls。它不调用 Leader provider、不读取 tmux pane、不写 state、不创建 plan/action/approval/message/job/inbox，也不执行推荐命令；适合 GUI 顶栏或自然语言壳快速回答“Leader 现在能不能规划，下一步该看 doctor、审批、inbox 还是继续”。自然语言 `agentdeck leader chat --message "查看 Leader 状态"`、`"刷新 Leader 状态"`、`"Leader 概览"`、`"leader refresh"` 或 `"leader overview"` 会进入只读 `mode=leader_status`，嵌入同一张 `leader_status_card`，并把顶层 `provider_health` / `next_command` 与状态卡对齐；同一响应还会附带过滤到 `leader_card` 的 `control_registry_card`，selection 指向 `kind=refresh` 的 `agentdeck leader status` control，方便 GUI 顶栏和命令面板高亮同一个刷新动作。该入口只记录 chat turn，不调用 provider、不读取 tmux、不创建调度对象。

详细字段契约见 `docs/contracts/project-view-schema.md`。当前契约版本为 `schema_version: "project-view/v1"`。`agentdeck contract list` 会返回所有 GUI 可消费契约的发现命令、example 命令和本地文档路径，方便 GUI 启动时做能力 discovery；字段契约见 `docs/contracts/contract-index-schema.md`。`agentdeck contract project-view` 会返回契约版本、文档路径和关键字段摘要，方便 GUI 或外部集成做 discovery；加 `--example` 会附带一份 GUI-ready ProjectView 示例。`agentdeck contract doctor` 会发现本地诊断字段，`--example` 会附带稳定 doctor diagnostics 示例。`agentdeck contract events` 会发现审计时间线字段，`--example` 会附带稳定 events timeline 示例。`agentdeck contract run` 会发现 `agentdeck run --task <text>` 的 approval-gated start card 字段和 `agentdeck run --plan-id <id>` 的 read-only progress card 字段，`--example` 会附带稳定 run_start/run_progress 示例。`agentdeck contract workbench` 会发现工作台快照字段，包括嵌入 `agentdeck agent ready` 的 `agent_ready_card_fields`、项目级 `terminal_session_card_fields`、`terminal_session_control_fields`、`skill_context_card_fields`、`skill_suggestions_card_fields` 和 `memory_suggestions_card_fields`，`--example` 会附带稳定的一屏 workbench 示例。`agentdeck contract controls` 会发现独立命令面板 card、filter、item 和 group 字段，`--example` 会附带稳定 control registry card 示例。`agentdeck contract skills` 会发现 `skills list/show/import-preview/import/load/suggest/suggestions/draft-preview/create` 的 registry 响应、skill/suggestion item 和 GUI control 字段，`--example` 会附带稳定 skill registry 示例。`agentdeck contract agent-runtime` 会发现 `agent list/ready/spawn-ready/spawn/terminal/capture/send/refresh/stop` 的命令模板、ready/spawn-ready/terminal/capture/refresh 响应字段和 runtime control 字段，`--example` 会附带稳定的可见 tmux runtime 示例。`agentdeck contract leader-chat` 会发现自然语言 Leader chat 响应字段，包括 `leader_status_card_fields` / `leader_status_queue_fields`、`skill_context_card_fields`、`skill_suggestions_card_fields`、`memory_suggestions_card_fields`、setup-mode `provider_health_fields`、workbench 嵌入的 `terminal_session_card_fields` / `terminal_session_control_fields` / `terminal_session_item_fields`、inbox trace intent 可选嵌入的 `trace_card` 字段和 summary intent 可嵌入的 `leader_summary_card` 字段，`--example` 会附带包含 `leader_explanation`、Leader status card、skill context、skill suggestions、memory suggestions、provider health 与 terminal session 示例字段的稳定响应示例。`agentdeck contract leader-status` 会发现 `agentdeck leader status` 的只读 Leader 状态卡字段，包括 `provider_health_fields`、latest plan、queue fields、recovery 和 controls，`--example` 会附带稳定状态卡示例。`agentdeck contract leader-actions` 会发现 Leader action queue 字段，`--example` 会附带稳定队列示例。`agentdeck contract leader-review` 会发现 `agentdeck leader review --plan-id <id>` 的 response 字段、`leader_backend_fields` 和 `controls[]` 字段，`--example` 会附带稳定 review 响应示例。`agentdeck contract leader-summary` 会发现 `agentdeck leader summary --plan-id <id>` 的 response、`leader_backend_fields`、steps、artifacts 和 controls 字段，`--example` 会附带稳定 summary 响应示例。`agentdeck contract leader-action` 会发现单个 Leader action 详情字段，`--example` 会附带稳定 action detail 示例。`agentdeck contract approvals` 会发现人类审批队列字段和 dispatch-ready 批量派发响应字段，`--example` 会附带稳定 approval queue 与 dispatch-ready 示例。`agentdeck contract inbox` 会发现单 agent mailbox 字段，`--example` 会附带稳定 inbox 示例。`agentdeck contract trace` 会发现通信 lineage 的 message/plan/attempt/job/reply/artifact/inbox 字段，`--example` 会附带稳定 trace 示例。`agentdeck contract artifacts` 会发现只读产物索引字段，`--example` 会附带稳定 artifacts 示例。

`agentdeck contract memory` 会发现 `memory suggest/suggestions/apply-preview/apply` 的建议、队列、只读预览、显式写入响应字段和 GUI control 字段，`--example` 会附带稳定 memory suggestion/apply 示例。这个 contract 是 AgentDeck 北极星里“可学习但不失控”的长期记忆接口：可以容纳人类、Leader 或 Worker 提出的 memory 候选，但最终写入必须由显式确认命令完成。

`agentdeck contract learning-review` 会发现 `agentdeck learn review --plan-id <id>` 的只读学习回顾响应字段、skill suggestion 字段、memory suggestion 字段和 GUI control 字段，`--example` 会附带稳定 learning review 示例。这个 contract 把 Hermes 式后台 reviewer 和 WispTerm 式 skill/memory 沉淀接到 AgentDeck 北极星：review 可以提出 `agentdeck skills suggest ... --source learn-review` 与 `agentdeck memory suggest ... --source learn-review` 后续命令，但不会自动创建 skill、写 memory、安装外源内容、调用 provider 或读取 tmux pane。live 输出和自然语言 `learning_review_card` 都会通过 `validate_learning_review_contract()` 守门，失败时不打印半坏 JSON。

GUI、自然语言入口和恢复工具应优先按这些契约消费 `agentdeck doctor`、`agentdeck events`、`agentdeck run`、`agentdeck workbench`、`agentdeck controls`、`agentdeck skills list/import-preview/import/show/load`、`agentdeck skills suggestions`、`agentdeck skills draft-preview`、`agentdeck skills create --confirm`、`agentdeck memory suggestions`、`agentdeck memory apply-preview`、`agentdeck memory apply --confirm`、`agentdeck learn review`、`agentdeck agent list`、`agentdeck agent ready`、`agentdeck agent spawn-ready --confirm`、`agentdeck agent terminal --agent <id>`、`agentdeck agent refresh`、`agentdeck status`、`agentdeck artifacts`、`agentdeck leader chat`、`agentdeck leader status`、`agentdeck leader actions`、`agentdeck leader review`、`agentdeck leader summary`、`agentdeck leader action`、`agentdeck approval list`、`agentdeck inbox` 和 `agentdeck trace`，不要把 tmux pane 或 state 文件当成第二套状态源。

`status.messages.items[]`、`status.jobs.items[]`、`status.replies.items[]` 和 `status.artifacts.items[]` 会包含 `trace_command`，GUI 可以直接把摘要行或产物行链接到 `agentdeck trace --id <id>`，不用散读 state 或拼接命令。artifact 摘要只暴露 artifact id、关联 message/job/reply id、from_agent、path、kind、status 和 created_at，不读取文件内容。

`status.inbox.heads` 会按 agent 暴露最早的 `pending` inbox item；没有待处理 item 的 agent 会返回 `null`。GUI 和 Leader chat loop 可以用它直接显示每个 agent 当前必须先处理或 ack 的 mailbox head。

`status.leader_actions` 会包含 `recommended_action_id`，每个 `items[]` 会包含 `controls[]`、`preview_command`、`can_apply`、`apply_command`、`explicit_command`、`apply_blocker` 和 `is_recommended`，GUI 可以直接按 `controls[]` 渲染只读预览、安全 apply、显式命令按钮、阻塞提示和当前推荐项高亮。

`status.chat_turns.items` 会包含 review/apply turn 关联的 `action_id` 和 `action_kind`，GUI 可以从自然语言对话历史直接跳转到对应 action。

`status.recovery` 会汇总当前恢复入口：`status`、`reason`、`next_command`、`recommended_action`、pending 计数、可应用的 `leader_action`，以及最近审计事件摘要。`recommended_action` 包含 label、command、safety、requires_explicit_user、source 和 target_id，GUI 可以用它直接渲染下一步按钮或检查入口，并把按钮关联回 action、approval、runtime、inbox item 或 provider setup。GUI 和 Leader chat loop 可以优先用 recovery 判断“现在该继续什么”，而不需要散读 state 或自行推断。

当 recovery 进入 `inbox_pending` 时，`next_command` 和 `recommended_action.command` 会指向具体 mailbox，例如 `agentdeck inbox --agent planner`，而不是退回宽泛的 `agentdeck status`。

当 recovery 进入 `runtime_stale` 时，`next_command` 和 `recommended_action.command` 会指向 `agentdeck agent refresh`，`recommended_action.source=runtime`，`target_id` 是第一个 stale agent id，帮助 GUI 或自然语言壳先校准可见 tmux runtime。

如果没有 pending action、approval 或 inbox item，但存在 `leader_errors[]`，`status.recovery` 会返回 `status=leader_error`，并推荐 `agentdeck status` 作为 inspect 动作，帮助 GUI 或人类先检查 Leader 错误。

如果没有 pending action、approval、inbox item 或 leader error，但配置的 API-backed Leader provider 缺少本地环境变量，`status.recovery` 会返回 `status=provider_setup_required`，并推荐 `agentdeck doctor`。GUI 可以把它渲染成 provider setup/diagnostics 入口，避免用户直接触发会失败的 `leader plan/chat`。

`status.recovery.pending` 也会包含 `leader_errors` 和 `runtime_stale` 计数，让 GUI 可以在统一恢复面显示还有多少 Leader 错误或 stale runtime 待检查。

`agentdeck contract project-view` 会通过 `recovery_pending_fields` 公开 `recovery.pending` 的必备字段，GUI 可以据此做字段兼容检查。

`agentdeck events --limit 20` 会读取 `.agentdeck/state/events.jsonl` 的最近事件，用于 GUI 审计时间线和调试恢复。GUI 持有上一帧事件游标时，可以用 `agentdeck events --since <event_id>` 拉取 cursor 之后的事件详情；如果 cursor 不存在，响应会返回 `cursor_found=false` 并回退到受 `--limit` 限制的事件尾部。

`agentdeck workbench` 是面向未来 GUI/TUI 的只读一屏快照。它会先校验 ProjectView，再组合 `project_view`、`leader_actions`、`leader_card`、`provider_health`、`runtime_card`、`agent_ready_card`、`terminal_session_card`、`role_card`、`ledger_card`、`lineage_card`、`queue_card`、`operator_card`、`audit_card`、`artifacts_card`、`skill_context_card`、`skill_suggestions_card`、`memory_suggestions_card`、`leader_summary_card`、`contracts_card`、`control_mode_card`、`recovery`、`continue_card`、`active_queue_source`、`run_progress_card`，以及当前 recovery 指向的 `inbox_card`、固定 Leader mailbox 投影 `leader_inbox_card`、`approval_card` 或 `leader_action`。`leader_card.leader_backend` 会暴露当前配置 provider/model 的 normalized logical Leader identity，让 GUI 一进入工作台即可区分 fake、API-backed 或 CLI-backed Leader 来源；该字段不是 tmux pane 绑定、readiness 证明或执行授权。`ledger_card.messages.items[].prompt_skill_context` 会复用 ProjectView message 摘要中的 compact worker skill provenance，帮助 GUI 在通信账本中直接显示“这次任务给 worker 注入了哪些 skill”。`skill_suggestions_card` 会把 pending skill 建议队列直接带到一屏工作台，并在 control registry 里暴露 `agentdeck skills suggestions` inspect control，方便 GUI 审阅 Hermes 式 skill 沉淀结果但不自动创建、导入或加载 skill。`memory_suggestions_card` 会把 pending memory 建议队列直接带到一屏工作台，暴露 `agentdeck memory suggestions` inspect control、只读 `agentdeck memory apply-preview --suggestion-id <id>` 控件和显式 `agentdeck memory apply --suggestion-id <id> --confirm` 控件，方便 GUI 审阅长期记忆候选并把最终写入交给人类显式命令；渲染 workbench 本身不创建或修改 `.agentdeck/memory/*.md`，也不注入 prompt。`control_mode_card` 把类似 Codex 的授权梯度显式化：`ask` 表示只计划、观察和建议命令；`approve` 表示沿用已有的人类审批与 safe apply；`autonomous` 目前是 disabled，仅作为未来带预算、allowlist 和审计门的放权模式占位。`provider_health` 会从 Leader provider 和本地环境变量派生 `provider_backend`、`provider_transport`、同源 `leader_backend`、supported、ready、missing_env、detail、doctor_command、doctor_contract、setup_commands 和 `controls[]`；这些 `controls[]` 是显式 provider 控制入口，覆盖 fake、DeepSeek、OpenAI-compatible、Codex CLI 和 Claude CLI，每个 provider 同时提供 `kind=set_provider` 普通切换、`kind=guarded_set_provider` 预检切换和 `kind=setup_provider` 安装/登录/placeholder export 修复命令，guarded 命令带 `--require-ready`；当前 provider switch disabled 并显示 `already current provider`，其他 provider 控件需要人类显式运行，不暴露密钥值、不调用 provider。其余 runtime、queue、ledger、artifact、skill suggestion、memory suggestion、contract 和 watch 行为仍保持只读：不创建 plan、不记录 chat turn、不 ack、不 approve、不 dispatch、不 capture reply、不读取 pane 输出、不发送 tmux 输入、不创建或加载 skill、不直接写长期 memory。

`validate_workbench_contract()` 会拒绝 `provider_health.controls[]` 里非 `safety=explicit_user` 的 provider 切换/设置控件、没有指向 `agentdeck leader set-provider --provider ...` 的 provider 切换控件、`kind=guarded_set_provider` 但缺少 `--require-ready` 的控件、`kind=setup_provider` 但 command 不在 provider setup allowlist 中的控件，以及缺少 blocker 的 disabled provider control，确保 GUI 里的 Leader provider 切换和修复入口始终是人类显式动作。

这些摘要和事件读取只用于观察和恢复，不修改 state、不发送 tmux 输入，也不包含完整长 prompt。GUI 或 Leader chat loop 应优先读取 `agentdeck status`，再按需调用 `plan show`、`plan status`、`trace` 或 `events` 获取细节。

## Leader Planning

AgentDeck 已提供第一版 plan-only Leader 能力：

```bash
agentdeck leader chat --message "帮我设计自动 reply extraction"
agentdeck leader chat --message "查看 Leader 状态"
agentdeck leader chat --message "刷新 Leader 状态"
agentdeck leader chat --message "Leader 概览"
agentdeck leader chat --message "查看运行进度"
agentdeck leader chat --message "继续"
agentdeck leader chat --message "打开工作台"
agentdeck leader chat --message "查看账本"
agentdeck leader chat --message "查看角色"
agentdeck leader chat --message "查看队列"
agentdeck leader chat --message "查看 runtime"
agentdeck leader chat --message "启动所有 agent"
agentdeck leader chat --message "启动 planner"
agentdeck leader chat --message "发送给 planner：继续"
agentdeck leader chat --message "停止 planner"
agentdeck leader chat --message "查看 planner inbox"
agentdeck leader chat --message "追踪 planner 当前 inbox"
agentdeck leader chat --message "查看当前 inbox"
agentdeck leader chat --message "确认 planner 当前 inbox"
agentdeck leader chat --message "确认当前 inbox"
agentdeck leader chat --message "查看审批"
agentdeck leader chat --message "批准当前审批"
agentdeck leader chat --message "派发当前审批"
agentdeck leader chat --message "派发所有已审批"
agentdeck leader chat-history
agentdeck continue
agentdeck run --task "实现一个功能"
agentdeck workbench
agentdeck controls
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

`agentdeck run --task <text>` 是 Phase D run loop 的 approval-gated 启动入口：它会调用配置的 Leader provider 生成 plan，立即为需要审批的 steps 创建 pending approvals，并返回 GUI-ready `run_start` card。`agentdeck leader chat --message "开始运行 <goal>"` 会复用同一条 run_start 路径和同一份 `validate_run_start_contract()`，只是把入口换成自然语言，并在 chat 响应里嵌入 `run_start_card`。`agentdeck run --plan-id <id>` 会返回只读 `run_progress` card，聚合 plan status、Leader review、run-specific approval queue 和下一步显式 command；`agentdeck leader chat --message "查看运行进度"` 会默认查看最新 plan，`"查看运行进度 <plan_id>"` 则查看指定 plan，并复用同一张 `run_progress_card`，同时附带过滤到 `run_progress_card` 的 `control_registry_card`，selection 指向该 plan 的只读 `plan_status` control。这些模式都不会自动 approve、dispatch、capture pane、ack inbox 或发送 tmux 输入；下一步由人类显式推进。

`agentdeck leader plan` 和 `agentdeck leader chat` 默认读取 `.agentdeck/config.toml` 的 `[leader] provider/model`；新项目默认是 `deepseek` / `deepseek-chat`。也可以显式使用 `--provider fake --model fake-plan` 做本地 dry-run，用 `--provider openai-compatible` 调用通用 OpenAI-compatible `/chat/completions` API，或用 `--provider codex-cli` / `--provider claude-cli` 调用本地已登录的 Codex CLI / Claude Code CLI 作为 Leader 推理后端。真实 provider 会把 `--model <model>` 透传给 backend：API-backed provider 写入请求体 `model`，CLI-backed Leader 写入本地 `codex` / `claude` 的 `--model` 参数，而不是只记录在 state；stdout 可以是纯 JSON plan，也可以是唯一 Markdown fenced `json` block；planning prompt 会把可用 worker 的 `agent_id`、`role`、`role_prompt`、provider、workspace mode，以及 `step` 必须按 `1..n` 连续编号、只能使用列出的 worker agent_id、role 必须完全复制 worker 配置这些规则一起交给 provider。解析后会通过同一份 provider plan schema validator，强制顶层包含非空字符串 `goal`、非空字符串 `summary` 和非空 `steps[]`，强制每个 step 包含从 1 开始连续且唯一的正整数 `step`、非空字符串 `agent_id`、`role`、`task`、`risk`、`requires_approval`，且 `agent_id` 必须指向当前配置中的 worker agent，`role` 必须匹配该 worker agent 的配置角色，强制每个 step 都 `requires_approval=true`，并归一化顶层 `approval_required=true`、`dispatch_ready=false`。保存后的 plan、ProjectView plan summary、`agentdeck plan list` 和 run card 会暴露 `provider_backend=local|api|cli|unknown` 与 `provider_transport=local|http|subprocess|unknown`，并额外提供统一 `leader_backend` 对象，明确 `agent_id=leader`、`runtime_kind=logical_leader`、`pane_backed=false`、`pane_id=null`、reasoning backend 和审批控制位，供 GUI/审计面展示计划来源；它们不是授权或执行语义。所有模式都不会 dispatch、不会发送 tmux 输入。

当你还没有配置 CLI-backed Leader 时，可以直接对自然语言入口说 `agentdeck leader chat --message "配置 Codex CLI Leader"`、`"登录 Codex CLI"` 或 `"安装 Claude CLI"`。这类请求会进入只读 `mode=setup`，返回当前 `provider_health`、结构化 `provider_setup_card`、过滤到 provider scope 的 `control_registry_card`，以及同源目标 `provider_switch_card`；`next_command` 仍是 allowlist 中第一条 setup 命令，例如 `codex login` 或 `claude auth`，而 `provider_switch_card.command` 给出配置完成后由人类显式运行的 `agentdeck leader set-provider --provider ...`。`provider_setup_card` 汇总 target provider/model、setup_commands、recommended_command、recommended_control_id、followup_switch_command 和 explicit_user controls；`control_registry_card` 会用推荐 setup control 的 `control_id` 进一步选中该命令，让 `selection.selected_control` 和 `selection.next_command` 对齐顶层 `next_command`。如果 setup 文本包含 `"必须可用"`、`"先预检"` 或类似 require-ready 语义，follow-up switch command 会追加 `--require-ready`，并在目标 CLI 不可用时把 switch control disabled，同时保留 `claude auth` / `claude doctor` 这类 setup controls。`leader_explanation.action_kind=provider_setup`，`intent_card.secondary_embedded_cards=["provider_setup_card","provider_switch_card","control_registry_card"]`。它只建议命令，不修改 `.agentdeck/config.toml`，不调用当前或目标 provider，不创建 plan/action/approval/message/job/inbox，也不发送 tmux 输入。

`agentdeck continue` 是顶层只读恢复入口。它会先校验 ProjectView，再把 `status.recovery` 整理成一张下一步卡片，并在输出前通过 `validate_continue_contract()` 自校验。卡片包含 status、reason、next_command、recommended_action、pending 计数、project_view_command，以及可选的 `leader_action` 详情和 `action_detail_command`；当存在多条 approved approvals 时，continue card 会把卡片级 `next_command` 和 `recommended_action.command` 提升为显式 `agentdeck approval dispatch-ready --confirm`，与 workbench/operator 的批量派发入口对齐。它不创建 plan、不写入 `leader_actions[]`、不 apply action、不 dispatch、不发送 tmux 输入，适合终端用户、自然语言壳和 GUI 在任何时刻询问“现在该继续什么”。

`agentdeck contract continue` 会公开这张恢复卡片的 `continue_card_fields`；`--example` 会返回稳定 `example_continue_card`，供 GUI 或外部集成发现 `agentdeck continue` 的响应形状。

`agentdeck controls` 是独立只读命令面板入口。它从同一次 `agentdeck workbench` snapshot 派生 `control_registry_card`，输出 mode、title、source_command、default_command、filters、selection、item_count、items[]、group_count 和 groups[]；其中 `source_command=agentdeck workbench` 表示命令面板来自 workbench 快照，`default_command=agentdeck controls` 表示 GUI/TUI 可以直接刷新独立命令面板。GUI 可以用 `--scope <scope>`、`--card <card>`、`--query <text>`、`--control-id <control_id>` 和 `--enabled-only` 请求局部只读视图，例如 `agentdeck controls --scope runtime --enabled-only`、`agentdeck controls --scope terminal_session --enabled-only`、`agentdeck controls --card terminal_session_card`、`agentdeck controls --query terminal` 或 `agentdeck controls --control-id <control_id>`；`filters.active_filter_keys` 会按 `scope`、`card`、`query`、`control_id`、`enabled_only` 的稳定顺序列出当前生效过滤器，供 GUI/TUI 直接渲染过滤 chip；`filters.item_count_before_filter` 会保留过滤前 item 数，过滤后再派生 `items[]` 和 `groups[]`，因此分组始终和当前投影一致。每个 item 保留 leader/provider/policy/terminal_session/role/runtime/inbox/operator controls 的 scope、card、kind、label、command、safety、enabled、blocker、agent_id 和 deterministic `control_id`；`control_id` 可作为 GUI/TUI 稳定 render key 或审计关联键，并可用于精确重新定位某个 control item，但不是授权令牌，不能覆盖 `enabled`、`safety` 或 `blocker`。`selection` 从 `filters.control_id` 和过滤后的 `items[]` 派生，暴露 requested_control_id、matched、matched_count、selected_control、blocker 和 next_command，方便 GUI 打开详情抽屉、展示未命中原因，或在命中 enabled control 时渲染一个候选下一步；未请求 control id 时 `blocker=null` 且 `next_command=null`，命中 disabled control 时 `blocker=null` 且 `next_command=null`，源 registry 不存在该 id 时 `blocker="control_id not found"`，该 id 存在但被当前过滤条件排除时 `blocker="control_id filtered out"`；它不是第二套状态源，也不授权执行。groups[] 按 scope/card 从同一批过滤后的 items 派生，提供 group_id、label、item_count、enabled_count、disabled_count 和组内 items，供 GUI 直接渲染命令面板分区，但不成为第二套状态源。provider scope 来自 `provider_health.controls[]`，用于渲染具体的 `agentdeck leader set-provider --provider <provider> --model <model>` 普通入口、带 `--require-ready` 的 guarded 入口，以及 `kind=setup_provider` 的 provider 安装/登录/placeholder export 修复命令，当前 provider switch disabled，其他 provider 需要 `explicit_user`，validator 会拒绝非 `explicit_user`、没有指向 `agentdeck leader set-provider --provider ...`、`guarded_set_provider` 缺少 `--require-ready`、`setup_provider` command 不在 provider setup allowlist 中，或 disabled 但缺少 blocker 的 provider item；policy scope 来自 `control_mode_card.active_controls[]`，用于渲染具体的 `agentdeck policy set-mode --mode ask|approve|autonomous` 入口，当前模式 disabled，autonomous blocked，validator 会拒绝没有指向 `agentdeck policy set-mode --mode ...` 的 policy item、enabled 但非 `explicit_user` 的 policy item，或 disabled 但缺少 blocker 的 policy item；terminal_session scope 同时来自 `terminal_session_card.controls[]` 和 `terminal_session_card.terminals[].controls[]`，用于渲染 `kind=attach_session`、`kind=open_controls`、`kind=refresh_runtime` 项目级终端入口，以及带 `agent_id` 的 `kind=select_pane` 单 pane 选择入口；validator 会拒绝非 tmux attach、非 `agentdeck controls` open-controls、非 `agentdeck agent refresh` refresh-runtime、refresh-runtime 非 `explicit_runtime`、select-pane 非 `inspect`、enabled select-pane 非 tmux select-pane、disabled select-pane command 非空，或 disabled 但缺少 blocker 的 terminal session item；role scope 来自 `role_card.agents[].controls[]`，用于渲染 `kind=assign_role` 的角色编辑表单，模板命令在缺少 role/role_prompt 时 disabled，validator 会拒绝没有指向 `agentdeck agent assign-role --agent ...` 或 disabled 但缺少 blocker 的 role item；inbox scope 来自当前可见 `inbox_card.items[].controls[]` 和固定 `leader_inbox_card.items[].controls[]`，用于渲染 `kind=preview` 的 trace 入口和 `kind=ack` 的显式确认入口，validator 会拒绝没有指向 `agentdeck trace --id ...` 的 preview item 或没有指向 `agentdeck ack --agent ...` 的 ack item；runtime scope 中的打开终端入口使用 `kind=terminal`，GUI 应优先使用该 kind 识别动作，而不是解析 `Open terminal` 文案；operator scope 中的批量审批派发入口使用 `kind=dispatch_ready`，GUI 应优先使用该 kind 识别动作，而不是解析 `Dispatch ready approvals` 文案。自然语言 approval dispatch 响应会额外返回过滤到 `dispatch_preview_card` 或 `dispatch_batch_preview_card` 的 `control_registry_card`，selection 指向顶层 dispatch/dispatch-ready control；这张卡片是该响应的局部命令面板，不改变独立 `agentdeck controls` 的 workbench 来源。validator 会要求 `scope=dispatch_preview` 的 dispatch item 使用 `agentdeck approval dispatch --approval-id ...` 和 `safety=explicit_runtime`，要求 `scope=dispatch_batch_preview` 的 dispatch_ready item 使用 `agentdeck approval dispatch-ready --confirm` 和 `safety=explicit_runtime`，并要求 disabled dispatch controls 带 blocker。它不创建 chat turn、不写 state、不调用 provider、不读取 pane，也不执行任何 control 或 ack；过滤、搜索和 control-id 精确定位参数只缩小只读投影，不授权任何命令，适合 GUI/TUI 或自然语言壳直接渲染命令面板。

`agentdeck contract controls` 会公开独立命令面板的 `control_registry_card_fields`、`control_registry_filter_fields`、`control_registry_selection_fields`、`control_registry_item_fields` 和 `control_registry_group_fields`，并指向 `agentdeck contract workbench` 与 `agentdeck contract leader-chat`，说明 live 命令和 help-mode 嵌入卡片都复用同一份 control registry 形状；`--example` 会返回稳定 `example_control_registry_card`。

`leader chat` 是自然语言入口 MVP。它会先读取并校验 `agentdeck status` 的 ProjectView：如果 ProjectView 不满足 `project-view/v1` 契约，chat 会返回非 0，且不会创建 plan 或 chat turn；如果人类明确说 `开始运行 <goal>`、`开始执行 <goal>` 或 `/run <goal>`，chat 会进入 `mode=run_start`，调用配置的 Leader provider 生成 plan，立即创建 pending approvals，并嵌入同源 `run_start_card`，但不会创建 `leader_actions[]`、不会自动 approve/dispatch/capture/ack/send tmux；如果人类说 `查看运行进度`，chat 会进入只读 `mode=run_progress`，省略 plan_id 时默认使用最新 plan，带 `<plan_id>` 时使用指定 plan，复用 `agentdeck run --plan-id <id>` 的 `run_progress_card`，展示 plan status、Leader review、run-specific approval queue 和下一步显式 command，并附带过滤到 `scope=run_progress` / `card=run_progress_card` 的 `control_registry_card`，selection 指向只读 plan status control；它只记录 chat turn，不写 run state、不 dispatch、不读取 pane。如果还没有任何 plan，进度查询会返回非 0 并报告 `no plans available for run progress`，不会把这句话误当成新目标来创建 plan。普通目标在当前还没有 plan 时会创建 plan-only 记录，并持久化一条可安全应用的 `create_approvals` Leader action，然后在响应前重新读取 ProjectView，让同一次 chat 响应包含刚创建的 plan、chat turn 和 action queue；如果已有 plan，就 review 最新 plan、持久化或复用一条 `leader_actions[]` 建议，然后重新读取 ProjectView 的 `status.recovery` 作为恢复决策源。chat 输出会在顶层返回与 `project_view.leader_actions` 相同的 `leader_actions` 摘要，并返回 `leader_explanation` 说明当前模式、推荐 action、reason、next_command、safety 和是否需要人类显式确认；同时返回 `intent_card` 作为 GUI-ready 路由卡，稳定暴露 mode、matched_intent、route_source、embedded_card、read_only、next_command、requires_explicit_user 和 `controls[]`，让自然语言壳可以解释“这句话被路由到哪里”并渲染下一步按钮。响应契约见 `docs/contracts/leader-chat-schema.md`，可通过 `agentdeck contract leader-chat` 发现；该契约也公开 `skill_create_preview_card_fields`，让 GUI/TUI 能发现自然语言 `创建 skill 建议 sgs_xxx` 的只读预览和显式 create 命令。输出 JSON 前也会通过 `validate_leader_chat_contract()` 自校验，失败会写入 `leader_errors[]` 和 `leader_chat_contract_failed` 事件。plan/review 输出都会返回 `recovery`，并让 `next_command` 等于 `recovery.next_command`；`leader_action` 包含 `can_apply`、`apply_command`、`explicit_command` 和 `apply_blocker`，同时派生 `leader_action_card` 暴露 mode/title/action_id/kind/status/reason/preview/apply/explicit/blocker/controls，方便 GUI 或对话层直接展示安全预览与执行按钮，但不成为第二套 action 状态源。每次 chat turn 都会写入 `.agentdeck/state/state.json` 的 `chat_turns[]`，并可通过 `leader chat-history` 查看；plan/review turn 会记录 action_id/action_kind。它不会自动 dispatch、不会发送 tmux 输入。

当人类输入 `agentdeck leader chat --message "帮助"`、`"help"`、`"/help"`、`"你能做什么"`、`"有哪些能力"`、`"命令面板"`、`"commands"` 或 `"capabilities"` 时，chat 会进入只读 `mode=help`：它返回 `capability_card`，列出当前自然语言入口支持的 plan、review、apply_action、continue、leader_status、workbench、runtime、role、ledger、audit、artifacts、queue、approval、inbox、policy、provider_switch 和 setup 能力，以及每项能力的示例说法、推荐命令、safety、是否需要显式用户动作、对应卡片和 `controls[]`；其中 `leader_status.example_messages` 会包含 `查看 Leader 状态`、`刷新 Leader 状态`、`Leader 概览`、`leader status`、`leader refresh` 和 `leader overview`，让自然语言壳能主动展示同一张只读状态卡的中英文入口。同时返回 `control_registry_card`，从同一次 workbench snapshot 派生出 leader/provider/runtime/operator control registry，供 GUI 或自然语言壳直接渲染命令面板，且该 card 的 `default_command` 指向 `agentdeck controls`，并带有同一套 `filters` / `groups[]` 投影字段。自然语言也可以表达只读过滤和搜索，例如 `agentdeck leader chat --message "命令面板 runtime enabled only"` 会返回 runtime scope 的 enabled controls，`agentdeck leader chat --message "命令面板 搜索 terminal"` 会返回 query 匹配的 controls，但仍不执行任何 control。`plan` 使用 `safety=plan_only`，其 control 指向显式 `agentdeck leader plan --task <goal>`；`review` 和 `apply_action` 使用 `safety=safe_apply`，其中 review control 指向 `agentdeck leader review --plan-id <plan_id>`，apply_action control 指向 `agentdeck leader apply-action --action-id <action_id>`；`leader_status` 使用 `safety=inspect`，control 指向只读 `agentdeck leader status` 并声明 `leader_status_card`；`policy` 使用 `safety=explicit_user`，control 指向 `agentdeck policy set-mode --mode <mode>`；`provider_switch` 使用 `safety=explicit_user`，control 指向 `agentdeck leader set-provider --provider <provider> --model <model>`；只读视图能力使用 `safety=inspect`；模板 control 只能使用 `<goal>`、`<plan_id>`、`<action_id>`、`<agent_id>`、`<mode>`、`<provider>` 或 `<model>`，必须 disabled，并返回与占位符匹配的 blocker 说明缺少的具体输入。该模式建议 `agentdeck workbench`，只记录 chat turn，不调用 Leader provider，不创建 plan/action/approval/message/job/inbox，不读取 tmux pane，不发送 tmux 输入，适合作为未来 GUI 命令面板和自然语言壳的能力发现入口；能力项、controls 和 registry items 只是命令发现，不代表自动执行许可。

当人类输入 `agentdeck leader chat --message "切换到审批模式"`、`"回到 ask 模式"` 或包含 `autonomous` / `"完全放权"` 的控制模式意图时，chat 会进入 `mode=policy`：它嵌入与 workbench 同源的 `control_mode_card`，附带过滤到 `scope=policy` / `card=control_mode_card` 的 `control_registry_card`，并把 `next_command` 设置为显式的 `agentdeck policy set-mode --mode ask|approve|autonomous`。对应 `intent_card.controls[]` next label 会使用 `Switch to ask mode`、`Switch to approval mode` 或 `Request autonomous mode`，让 GUI 可以清楚渲染 ask/审批/放权梯度。registry selection 会指向目标 mode 的 `kind=set_mode` control：ask/approve 可用时 `selection.next_command` 等于顶层 `next_command`，autonomous 仍是 disabled control，保留 blocker 且 `selection.next_command=null`。该模式只记录 chat turn，不修改 `.agentdeck/config.toml`，不创建 plan/action/approval/message/job/inbox，不调用 provider，不发送 tmux 输入；真正切换策略仍必须由人类显式运行 `agentdeck policy set-mode ...`，其中 autonomous 仍会被策略命令拒绝并记录审计事件。

当人类输入 `agentdeck leader chat --message "切换 Leader 到 Codex CLI"`、`"使用 Claude Code 做 Leader"`、`"换成 DeepSeek Leader"` 这类 provider switch 意图时，chat 会进入只读 `mode=setup`：它嵌入同源 `provider_health`，并把 `next_command` 设置为具体的 `agentdeck leader set-provider --provider <provider> --model <model>`。如果话里包含 `"要求可用"`、`"先预检"`、`"必须可用"` 或类似 require-ready 语义，`next_command` 会追加 `--require-ready`，让后续显式命令在目标 backend 不 ready 时拒绝写配置；同时 `provider_switch_card` 的 guarded provider control 会在 `target_readiness.ready=false` 时 disabled，并给出 `target provider is not ready` blocker，且额外暴露来自 `target_readiness.setup_commands` 的 `kind=setup` controls，避免 GUI 把不可用目标渲染成可执行切换按钮，也让人类能直接看到下一步修复命令。`leader_explanation.action_kind=provider_switch`，`safety=explicit_user`，`requires_explicit_user=true`；`intent_card.controls[]` 会先给出只读 `agentdeck doctor` 检查入口，再给出 `Switch Leader provider` next control。该模式只记录 chat turn，不修改 `.agentdeck/config.toml`，不调用当前或目标 provider，不创建 plan/action/approval/message/job/inbox，不发送 tmux 输入；真正切换仍必须由人类显式运行返回的 `leader set-provider` 命令。

当人类输入 `agentdeck leader chat --message "继续"`、`"继续吧"` 或 `"/continue"` 时，chat 会进入 recovery-first 的 `mode=continue`：它复用 `agentdeck continue` 的下一步卡片，返回 `continue_card`、`recovery`、`next_command` 和解释信息；顶层 `next_command`、`leader_explanation.next_command` 和 chat turn 记录都必须对齐 `continue_card.next_command`。如果 recovery 指向 pending inbox，会同时嵌入对应 agent 的 `inbox_card`；如果 recovery 指向 approval queue，会同时嵌入 `approval_card`；如果存在多条 approved approvals，`continue_card.next_command` 会指向显式 `agentdeck approval dispatch-ready --confirm`；如果 recovery 指向 stale runtime，会同时嵌入同源 `runtime_card` 和 `terminal_session_card`，并让 `intent_card.secondary_embedded_cards=["runtime_card","terminal_session_card"]`，让 GUI 或自然语言壳在恢复页直接渲染 `agentdeck agent refresh` 入口和项目级终端条。该模式只记录一条 chat turn，不创建新的 `leader_actions[]`，也不执行任何 action、refresh、spawn、stop、capture、attach/select-pane 或 tmux 输入。需要让 Leader 重新 review 并排队 action 时，可以输入更具体的目标或继续使用 `agentdeck leader next`。

当人类输入 `agentdeck leader chat --message "总结当前计划"`、`"汇总结果"`、`"summary"` 或 `"summarize"` 这类 summary 意图时，chat 会进入只读 `mode=summary`：它要求最新 plan 的本地 `leader review` 已经是 `next_action=summarize`，然后嵌入同源 `leader_summary_card`，复用 `agentdeck leader summary --plan-id <id>` 的 response shape，展示 replies、artifacts、每个 step 的 trace 入口和 summary controls。同一响应会附带过滤到 `scope=leader_summary` / `card=leader_summary_card` 的 `control_registry_card`，selection 指向 `kind=summary` 的 `agentdeck leader summary --plan-id <id>` inspect control，方便 GUI/TUI 在命令面板里高亮最终汇总入口。它只记录 chat turn，不创建新的 `leader_actions[]`，不调用 provider，不创建 approval/message/job/reply/artifact/inbox，不读取 pane、不 capture reply、不 dispatch、不 ack、不发送 tmux 输入；如果当前 plan 还没准备好总结，会直接返回明确错误，不会落到 provider-backed planning。

当人类输入 `agentdeck leader chat --message "打开工作台"`、`"查看总览"`、`"dashboard"` 或 `"workbench"` 这类全局工作台意图时，chat 会进入只读 `mode=workbench`：它嵌入一张完整 `workbench_card`，该卡片复用 `agentdeck workbench` 的快照契约，包含 leader/provider/runtime/role/ledger/queue/operator/audit/contracts/change_summary 等 GUI-ready 投影。`next_command` 等于 `workbench_card.next_command`，该模式只记录 chat turn，不创建 plan/action/approval/message/job/inbox，不 ack、不 approve、不 dispatch、不 refresh runtime、不 capture、不读取 pane 输出、不发送 tmux 输入。

当人类输入 `agentdeck leader chat --message "查看账本"`、`"查看通信"`、`"ledger"` 或 `"trace commands"` 这类 ledger 意图时，chat 会进入只读 `mode=ledger`：它复用 workbench 的 `ledger_card` 和 `lineage_card`，返回 messages/jobs/replies/inbox 摘要、去重后的 `trace_commands`，以及最近通信路径；如果有 trace，`next_command` 会指向第一条 `agentdeck trace --id <id>`，否则建议 `agentdeck workbench`。同一响应会附带过滤到 `scope=ledger` / `card=ledger_card` 的 `control_registry_card`，selection 指向 `ledger_card.controls[]` 里的 `kind=inspect` / `command=agentdeck workbench` 只读入口，`intent_card.secondary_embedded_cards=["control_registry_card"]`；这让 GUI 能打开通信账本卡，同时仍保留顶层 trace 跳转建议。该模式只记录 chat turn，不创建 plan/action/approval/message/job/inbox，不 ack、不 dispatch、不 capture reply、不读取 pane 输出、不执行 trace/workbench 命令、不发送 tmux 输入。

当人类输入 `agentdeck leader chat --message "查看审计"`、`"最近事件"`、`"audit"` 或 `"recent events"` 这类 audit 意图时，chat 会进入只读 `mode=audit`：它复用 workbench 的 `audit_card`，返回 latest_event、recent_events、event_count、`events_command=agentdeck events --limit 20` 和 inspect control，并把 `next_command` 指向同一条事件时间线命令；同一响应会附带过滤到 `scope=audit` / `card=audit_card` 的 `control_registry_card`，selection 指向 `kind=inspect` 的 `agentdeck events --limit 20` control，`intent_card.secondary_embedded_cards=["control_registry_card"]`。该模式只记录 chat turn 和对应审计事件，不创建 plan/action/approval/message/job/inbox，不 ack、不 approve、不 dispatch、不 capture、不读取 pane 输出、不发送 tmux 输入，也不执行事件命令。

当人类输入 `agentdeck leader chat --message "查看产物"`、`"artifacts"`、`"输出文件"` 或 `"交付物"` 这类产物索引意图时，chat 会进入只读 `mode=artifacts`：它复用 `agentdeck artifacts` 的 `artifacts_card`，只返回 artifact 摘要、trace 模板、contract 入口和 inspect control，不读取产物文件内容。该模式只记录 chat turn 和对应审计事件，不创建 plan/action/approval/message/job/reply/artifact/inbox，不调用 provider、不读取 pane、不发送 tmux 输入；`intent_card.embedded_card=artifacts_card`，`intent_card.secondary_embedded_cards=["control_registry_card"]`，inspect/next control 都指向 `agentdeck artifacts`，并且 `control_registry_card.selection` 会选中同一个 artifacts inspect control。

当人类输入 `agentdeck leader chat --message "追踪 msg_xxx"`、`"trace job_xxx"`、`"追踪 art_xxx"` 或 `"查看 rep_xxx 链路"` 这类具体通信 ID 追踪意图时，chat 会进入只读 `mode=trace`：它复用 `agentdeck trace --id <id>` 的 `trace_card`，返回该 message lineage 下的 message、attempts、jobs、replies、artifacts 和 inbox_items，`intent_card.controls[]` next label 会使用 `Inspect trace`。该模式只记录 chat turn，不创建 plan/action/approval/message/job/inbox，不 ack、不 dispatch、不 capture reply、不读取 pane 输出、不发送 tmux 输入；未知 ID 会返回错误，不会落到 provider-backed planning。

当人类输入 `agentdeck leader chat --message "查看角色"`、`"查看分工"`、`"roles"` 或 `"assign-role"` 这类 role 意图时，chat 会进入只读 `mode=role`：它复用 workbench 的 `role_card`，返回每个 agent 的 role、provider、workspace_mode、role_prompt、可复制的 `assign_command` 和 GUI-ready `controls[]`。当人类输入 `"把 planner 设为 架构师"`、`"让 coder 担任 实现工程师"` 或 `"set reviewer role to QA"` 这类自然语言角色指派时，chat 仍进入 `mode=role`，但 `next_command` 会变成具体的 `agentdeck agent assign-role --agent <id> --role <role> --role-prompt <prompt>`，`leader_explanation.action_kind=role_assign`，`intent_card.controls[]` 的 next label 为 `Assign role`，并标记 `safety=explicit_user` / `requires_explicit_user=true`。该模式只记录 chat turn，不创建 plan、leader action、approval、message、job、inbox，不修改配置，也不发送 tmux 输入；真正修改角色仍必须由人类显式运行返回的 `agentdeck agent assign-role ...`。

当人类输入 `agentdeck leader chat --message "查看队列"`、`"查看 actions"`、`"查看控制面"` 或 `"下一步按钮"` 这类 queue/operator 意图时，chat 会进入只读 `mode=queue`：它复用 workbench 的 `queue_card` 和 `operator_card`，返回当前 active queue、pending 统计、next_command、preview/apply/explicit controls 和 blocker。operator controls 的 command、enabled 和 blocker 都必须和同一张 operator card 顶层字段对齐，因此 GUI 可以直接渲染按钮和不可用原因，而不需要重新推断。该模式会把顶层 `next_command` 对齐到 operator 的主命令；因此多条 approvals 已 approved 时，自然语言控制面也会推荐 `agentdeck approval dispatch-ready --confirm`，并在 `operator_card.controls[]` 中暴露 `kind=dispatch_ready`、label=`Dispatch ready approvals`；该批量派发 control 只有在 operator 顶层 blocker 为空时才 enabled，且 blocker 文案必须和顶层 blocker 一致。响应还会附带过滤到 `operator_card` 的 `control_registry_card`，selection 指向顶层 `next_command` 对应的 operator control，且 `selection.next_command` 必须等于顶层 `next_command`，供 GUI 同屏渲染主操作按钮和命令面板选中项。该模式只记录 chat turn，不创建新的 `leader_actions[]`，不 apply action、不 approve/reject/dispatch、不 ack、不 refresh runtime、不发送 tmux 输入；按钮是否能安全 apply 仍由 `operator_card.controls[]`、`control_registry_card.selection` 和 `leader_explanation.safety` 显式表达。

当人类输入 `agentdeck leader chat --message "检查 Leader provider 配置"`、`"doctor"`、`"诊断环境变量"` 这类 setup/diagnostics 意图时，chat 会进入只读 `mode=setup`：它返回 `provider_health`、`recovery`、`next_command=agentdeck doctor` 和 `leader_explanation`，不会调用配置的 Leader provider，也不会创建 plan、leader action、approval、message、job、inbox 或发送 tmux 输入。`provider_health` 与 workbench provider health 使用同一组字段，包含当前 Leader 的 agent_id、provider、model、approval_mode、api_backed、provider_backend、provider_transport、同源 `leader_backend`、supported、ready、missing_env、detail、command_path、doctor_command、doctor_contract、setup_commands 和 `controls[]`；`leader_backend` 只是 setup provenance，不表示 readiness、tmux pane 绑定或执行授权；`controls[]` 只提供显式 `agentdeck leader set-provider` 切换命令，不自动切换、不调用 provider；当人类输入 `"切换 Leader 到 Codex CLI"`、`"使用 Claude Code 做 Leader"` 或带 `"要求可用"` 的 provider switch 意图时，响应还会包含 `provider_switch_card`，展示 current provider/model、target provider/model、target normalized `leader_backend`、与 `agentdeck leader set-provider --require-ready` 同源的 `target_readiness`、`require_ready`、显式 `command`、`diagnostics_command=agentdeck doctor`、`mutates_config=false` 和 GUI-ready controls；`target_readiness.provider/model` 必须匹配 target provider/model，`target_readiness.leader_backend` 必须匹配 target normalized `leader_backend`，inspect control 必须匹配 `diagnostics_command`，provider control 必须按 `require_ready` 使用 `set_provider` 或 `guarded_set_provider`，且 `require_ready=true` 加目标不可用时 guarded control 必须 disabled 并带 `target provider is not ready` blocker，同时提供与 `target_readiness.setup_commands` 对齐的 `kind=setup` controls；此时 `intent_card.embedded_card=provider_health` 且 `secondary_embedded_cards=["provider_switch_card"]`，GUI 可以同时渲染当前 provider 诊断和目标切换确认面板；真正修改配置仍必须由人类显式运行 `command`。`agentdeck contract doctor` 也会公开 workbench、Leader chat 和 Leader review contract 入口，供 GUI setup 页面直接跳转到主要控制面契约；`setup_commands` 只能是可复制后由人类自行编辑的 placeholder export 命令，只暴露缺失 env 名称，不暴露密钥值。

当人类输入 `agentdeck leader chat --message "查看 runtime"`、`"查看终端"`、`"查看智能体"` 或包含 `tmux` / `pane` 的 runtime 意图时，chat 会进入只读 `mode=runtime`：它返回复用 workbench runtime 投影的 `runtime_card`，同时返回同源顶层 `terminal_session_card`，并让 `intent_card.embedded_card=runtime_card`、`intent_card.secondary_embedded_cards=["terminal_session_card"]`，供 GUI/TUI 直接渲染项目级 tmux terminal strip，并建议 `agentdeck agent list`。当人类输入 `"打开 planner 终端"`、`"进入 coder pane"` 或 `"attach reviewer"` 这类打开某个可见 pane 的意图时，chat 会进入只读 `mode=terminal`，嵌入 `terminal_card`，返回 tmux attach/select-pane 命令和 capture/send/stop/inbox 控件；同一响应会附带过滤到 `scope=terminal` / `card=terminal_card` 的 `control_registry_card`，selection 指向 `kind=terminal` 的 tmux attach control，`intent_card.secondary_embedded_cards=["control_registry_card"]`，但不会 attach/select pane、读取 pane 输出、发送输入或修改 runtime state。当人类输入 `"刷新 runtime"`、`"runtime refresh"` 或 `"刷新终端"` 这类刷新运行时绑定的意图时，chat 会建议显式 `agentdeck agent refresh`，但不会检查 tmux 或修改 state。当人类输入 `"启动所有 agent"`、`"启动全部 agent"`、`"prepare all agents"` 或 `"agent ready"` 这类多 Agent 启动准备意图时，chat 会额外嵌入 `agent_ready_card`，复用 `agentdeck agent ready` 的 ready response shape，展示 total/running/not_running/all_running、所有待启动 agent 的 `spawn_commands` 和 `spawn_ready_command`，并让 `intent_card.embedded_card=agent_ready_card`、`intent_card.secondary_embedded_cards=["startup_preview_card","runtime_card","terminal_session_card","control_registry_card"]`，方便 GUI 同屏渲染启动清单、runtime 状态、项目级终端条和已选中的启动 control；顶层 `next_command` 会对齐到 card 的显式下一步：多个 agent 未 running 时推荐 `agentdeck agent spawn-ready --confirm`，只有一个未 running 时推荐单 agent spawn，全部 running 时推荐 `agentdeck approval dispatch-ready --confirm`。当人类明确输入 `"启动 planner"`、`"开启 coder"` 或 `"spawn reviewer"` 这类启动某个 agent 的意图时，chat 仍进入 `mode=runtime`，以 `runtime_action_card(action=spawn)` 作为 primary card，并在 `intent_card.secondary_embedded_cards` 中列出 `runtime_card`、`startup_preview_card`、`terminal_session_card` 和 `control_registry_card`；`control_registry_card` 会过滤到 `runtime_action_card`，selection 指向显式 spawn control，`startup_preview_card` 只作为目标 agent 的执行前 checklist。`leader_explanation.safety=explicit_runtime` 且 `requires_explicit_user=true`。当人类输入 `"发送给 planner：继续"`、`"tell coder fix tests"` 这类给某个已 running agent 发送输入的意图时，chat 会建议显式 `agentdeck agent send --agent <id> --text <text>`；当人类输入 `"停止 planner"` 或 `"stop coder"` 这类停止某个已 running agent 的意图时，chat 会建议显式 `agentdeck agent stop --agent <id>`；这些 runtime explicit action 的 `intent_card.controls[]` next label 会使用 `Open terminal`、`Refresh runtime`、`Spawn ready agents`、`Spawn <agent>`、`Send input to <agent>` 或 `Stop <agent>`，方便 GUI 直接渲染按钮而不用解析命令字符串。目标未 spawn 时会返回 `agent is not spawned: <id>`，不会落到 provider-backed planning。`runtime_card` 包含每个 agent 的 runtime status、pane_id、session_name、cwd、refresh/spawn/capture/send/stop/inbox 命令和 `controls[]`；`terminal_session_card` 包含 session attach、refresh、project controls 和每个 terminal item 的 `controls[]` select-pane 控件，running agent 的 select-pane 控件 enabled，未 running agent 的控件 disabled 并带 `agent is not running` blocker。二者都只是可见命令投影，`secondary_embedded_cards` 也只是渲染提示，不代表自动 attach tmux、select pane、refresh、capture、send 或写 state 的许可。该模式只记录 chat turn，不创建 plan、leader action、approval、message、job、inbox，也不执行 refresh、spawn、stop、capture 或发送 tmux 输入。

单 Agent 启动、刷新 runtime、发送输入和停止 agent 意图会额外把 `runtime_action_card` 作为 `intent_card.embedded_card`，并在 `secondary_embedded_cards` 中列出 `runtime_card`、`terminal_session_card` 和 `control_registry_card`；单 Agent 启动还会同时列出 `startup_preview_card`。`runtime_action_card.controls[]` 的 `kind=spawn` / `kind=refresh_runtime` / `kind=send` / `kind=stop` 必须与顶层 `next_command` 完全一致，`control_registry_card.selection.selected_control` 也会指向同一个 spawn/refresh/send/stop control，且 `selection.next_command` 必须等于 `runtime_action_card.command`；GUI 可以据此展示“准备启动哪个 agent、刷新哪个 runtime、操作哪个 pane、发送什么文本或停止哪个 agent、复制/运行哪条命令”，但不能把该卡片当成自动 spawn、自动 refresh、自动发送或自动停止授权。

当人类输入 `agentdeck leader chat --message "查看 planner 输出"`、`"capture planner output"` 或其他明确查看某个 agent pane 输出的意图时，chat 会进入只读 `mode=capture`：它读取已 spawn 的 tmux pane，返回 `capture_card`，包含 agent_id、pane_id、lines、capture_command、output 和 GUI-ready `controls[]`，并建议同一条 `agentdeck agent capture --agent <id> --lines 200`，`intent_card.controls[]` next label 会使用 `Capture agent output`。同一响应还会附带过滤到 `scope=capture` / `card=capture_card` 的 `control_registry_card`，selection 指向 `kind=inspect` 的 recapture control，方便 GUI/TUI 在输出快照旁边高亮同一条只读重新抓取命令。该模式只记录 chat turn，不创建 plan/action/approval/message/job/inbox，不 ack、不 dispatch、不 capture-reply、不发送 tmux 输入；未 spawn 的 agent 会返回 `agent is not spawned: <id>`，不会落入 provider-backed planning。当人类输入 `"捕获 planner 对 msg_xxx 的回复"`、`"回收 planner 对 msg_xxx 的结果"` 或 `"capture reply from planner for msg_xxx"` 这类 capture-reply 意图时，chat 也进入 `mode=capture`，但不会读取 pane，也不会写 reply；它嵌入同源 `trace_card`，并把 `next_command` 设置为显式 `agentdeck capture-reply --agent <id> --message-id <msg_id>`，`intent_card.controls[]` next label 为 `Capture reply`，标记 `safety=explicit_runtime` / `requires_explicit_user=true`。当 latest `leader_review` 已经是 `wait_for_reply` 时，用户也可以说 `"捕获当前回复"` 或 `"回收当前结果"`，chat 会从该 review 中解析当前 agent/message 并返回同一条显式 `capture-reply` 命令；如果 review 不是等待回复，就不会猜测目标。真正捕获 pane 输出并把 reply 入账，仍必须由人类显式运行返回的 `capture-reply` 命令。

当人类输入 `agentdeck leader chat --message "查看 planner inbox"` 或 `"查看 leader inbox"` 这类 inbox 意图时，chat 会进入只读 `mode=inbox`：它复用 `agentdeck inbox --agent <id>` 的队列 shape 返回 `inbox_card`，并建议 `agentdeck inbox --agent <id>`，对应 next label 为 `Open inbox`；同一响应会附带过滤到 `scope=inbox` / `card=inbox_card` 的 `control_registry_card`，普通查看会展示当前 head 的 preview/ack controls 但不选中某个执行项。`<id>` 可以是配置里的 worker agent，也可以是逻辑 Leader mailbox owner `leader`，用于查看 worker reply 回流，但不代表 Leader 有 tmux pane。当 ProjectView recovery 已经指向 pending inbox 时，人类也可以输入 `"查看当前 inbox"`、`"追踪当前 inbox"` 或 `"确认当前 inbox"`，chat 会从 recovery 的 `target_id` 反查 mailbox owner，并复用同一张 `inbox_card`；如果 recovery 不是 inbox，则不会猜测目标 agent。当输入包含 `追踪`、`trace` 或 `lineage` 时，且该 mailbox 有 pending head，`next_command` 会变成该 head 的 `agentdeck trace --id <inbox_id>`，对应 next label 为 `Inspect trace`，registry selection 会选中同一条 preview control；当输入包含 `确认`、`ack` 或 `acknowledge` 且该 head 可 ack 时，`next_command` 会变成该 head 的 `ack_command`，registry selection 会选中同一条 ack control，`intent_card.controls[]` next label 会使用 `Acknowledge inbox item`，并标记 `safety=explicit_runtime` 与 `requires_explicit_user=true`。该模式只记录 chat turn，不执行 ack、不 dispatch、不 capture reply、不发送 tmux 输入。

当人类输入 `agentdeck leader chat --message "让 planner 规划 README 更新"`、`"指派 coder 修复测试"` 或 `"ask reviewer to review docs"` 这类明确给某个 agent 分配任务的自然语言时，chat 会进入 `mode=approval`，创建一条 `source=leader_chat_task_assignment` 的 pending approval，嵌入同源 `approval_card`，并把 `next_command` 设置为该 approval 的显式 `approve_command`；它不会创建 plan/leader action，不会 approve、dispatch、创建 message/job/inbox 或发送 tmux 输入。`leader_explanation.action_kind=approval_create`，`intent_card.read_only=false`，但下一步仍标记 `safety=explicit_runtime` / `requires_explicit_user=true`，因为真正进入 runtime 仍需要人类批准和派发。

当人类输入 `agentdeck leader chat --message "查看审批"` 这类 approval 意图时，chat 会进入只读 `mode=approval`：它复用 `agentdeck approval list` 的队列 shape 返回 `approval_card`，并建议 `agentdeck approval list`；当输入包含 `批准` 或 `approve` 且存在 pending approval 时，`next_command` 会变成第一条 pending approval 的 `approve_command`；当输入包含 `拒绝`、`驳回` 或 `reject` 且存在 pending approval 时，`next_command` 会变成第一条 pending approval 的 `reject_command`；当输入包含 `派发` 或 `dispatch` 且存在 approved approval 时，`next_command` 会变成第一条 approved approval 的 `dispatch_command`，并额外嵌入 `dispatch_preview_card`，展示 approval_id、agent_id、agent_role、pane_id、runtime_status、task、dispatch_command、approval_command、inbox_command、controls、safety、requires_explicit_user 和 blocker，让 GUI/自然语言壳在真正执行前看到会派发给谁、打到哪个 pane、执行后去哪里看 inbox；同一响应会附带过滤到 `dispatch_preview_card` 的 `control_registry_card`，`selection.selected_control` 指向顶层 dispatch control，`intent_card.secondary_embedded_cards=["approval_card","control_registry_card"]`；如果 runtime 不可用，`dispatch_preview_card.blocker` 会同步禁用 card 内 dispatch control、registry selection 和 `intent_card.controls[]` 里的 next control。当输入包含 `派发所有已审批`、`派发全部审批`、`dispatch all approvals` 或 `batch dispatch` 这类批量意图时，chat 会嵌入 `dispatch_batch_preview_card`，把每条 approved approval 映射成同样的 dispatch preview item，并汇总 count/ready_count/blocked_count；每个 item 都带 inspect/dispatch controls，blocked item 的 dispatch control 会 disabled 并复用 blocker；顶层 `next_command` 会指向显式 `agentdeck approval dispatch-ready --confirm`，同一响应会附带过滤到 `dispatch_batch_preview_card` 的 `control_registry_card`，selection 指向 `kind=dispatch_ready` control，但 chat 本身仍不执行派发。approve/reject/dispatch 建议都会标记 `safety=explicit_runtime` 与 `requires_explicit_user=true`，对应 next label 会使用 `Approve approval`、`Reject approval`、`Dispatch approval` 或 `Dispatch ready approvals`，方便 GUI 直接渲染人类审批按钮。只读 approval 意图只记录 chat turn，不执行 approve/reject/dispatch-ready/dispatch、不创建 message/job/inbox、不发送 tmux 输入。

`agentdeck contract leader-chat` 会公开 `intent_card_fields`、`intent_control_fields`、`leader_action_card_fields`、`leader_summary_card_fields`、`capability_card_fields`、`capability_item_fields`、`capability_control_fields`、`capability_placeholder_fields`、`capability_placeholders`、`control_registry_card_fields`、`control_registry_filter_fields`、`control_registry_group_fields`、`continue_card_fields`、`capture_card_fields`、`terminal_card_fields`、`dispatch_preview_card_fields`、`dispatch_batch_preview_card_fields`、`dispatch_batch_preview_item_fields`、`startup_preview_card_fields`、`startup_preview_item_fields`、`agent_ready_card_fields`、`runtime_card_fields`、`terminal_session_card_fields`、`terminal_session_control_fields`、`terminal_session_item_fields`、`queue_card_fields`、`operator_card_fields`、`role_card_fields`、`role_agent_fields`、`ledger_card_fields`、`lineage_card_fields`、`lineage_path_fields`、`audit_card_fields`、`artifacts_card_fields`、`artifact_summary_fields`、`artifact_item_fields`、`trace_card_fields`、`trace_message_fields`、`trace_attempt_fields`、`trace_job_fields`、`trace_reply_fields`、`trace_artifact_fields`、`trace_inbox_item_fields`、`workbench_card_fields` 和 `workbench_control_registry_item_fields`，`--example` 会返回稳定的 continue-mode 示例和 `example_intent_card_fields` / `example_intent_control_fields` / `example_leader_action_card_fields` / `example_leader_summary_card_fields` / `example_capability_card_fields` / `example_capability_item_fields` / `example_capability_control_fields` / `example_capability_placeholder_fields` / `example_control_registry_card_fields` / `example_continue_card_fields` / `example_terminal_card_fields` / `example_dispatch_preview_card_fields` / `example_dispatch_batch_preview_card_fields` / `example_dispatch_batch_preview_item_fields` / `example_startup_preview_card_fields` / `example_startup_preview_item_fields` / `example_agent_ready_card_fields` / `example_runtime_card_fields` / `example_terminal_session_card_fields` / `example_terminal_session_control_fields` / `example_terminal_session_item_fields` / `example_queue_card_fields` / `example_operator_card_fields` / `example_role_card_fields` / `example_role_agent_fields` / `example_ledger_card_fields` / `example_lineage_card_fields` / `example_lineage_path_fields` / `example_audit_card_fields` / `example_artifacts_card_fields` / `example_workbench_card_fields` / `example_workbench_control_registry_item_fields`，供 GUI 或自然语言壳发现恢复、Leader action 卡片字段、Leader summary 卡片字段、控制卡片字段、help-mode 命令面板字段、嵌入 workbench 命令面板字段、trace card 字段、pane terminal/capture/dispatch preview 字段、多 Agent startup preview 字段、项目级 terminal session 字段、多 Agent runtime readiness 字段和模板 placeholder 白名单。chat 响应里的 `intent_card` 必须包含完整字段，`intent_card.controls[]` 使用 `kind`、`label`、`command`、`safety`、`enabled` 和 `blocker`；GUI 应直接渲染 `label` 而不是解析 `command`，runtime、policy、approval 和只读观察类 next label 会按动作命名；有嵌入卡片时可以先给出只读 `inspect` control，再给出 `next` control，避免自然语言路由和 GUI 状态解释分叉；存在 `intent_card.next_command` 时必须有 `kind=next` control，且该 control 的 `command` 必须等于 `intent_card.next_command`；带 `<reason>` 等模板输入的 next control 必须 disabled，并给出匹配 blocker。`validate_leader_chat_contract()` 会强制 `kind=inspect` 必须使用 `safety=inspect`，要求 disabled control 提供 blocker，并拒绝缺失或漂移的 next control、enabled placeholder intent control 或 blocker 错配，避免 GUI 渲染出安全语义不明的按钮；同时会校验 `leader_action_card.action_id` 与 `leader_action.action_id` 对齐、校验 `leader_action_card.controls[]` 字段，校验 `leader_summary_card` 复用 `validate_leader_summary_contract()`，也会校验 `capability_card.capability_count` 与 `capabilities[]` 长度一致、校验 capability item 的 `controls[]` 字段、强制 capability control 的 `command` / `safety` 与所属 item 对齐、拒绝未知/启用/blocker 错配的 placeholder capability control，并强制 `plan` / `review` / `apply_action` 能力项使用与调度语义一致的 safety；也会校验 `control_registry_card.filters`、`control_registry_card.item_count` 与 items 长度一致，并要求 `groups[]` 与过滤后的 items 保持一致。chat 响应里的 `continue_card` 必须复用 `validate_continue_contract()` 校验，避免自然语言“继续”和独立 `agentdeck continue` 出现两套恢复卡片规则。chat 响应里的 `terminal_card` 必须满足 `terminal_card_fields`，避免自然语言打开终端和独立 `agentdeck agent terminal` 出现两套 pane 定位字段；`capture_card` 必须满足 `capture_card_fields`，避免自然语言 pane capture 和独立 `agentdeck agent capture` 出现两套可见输出字段；`dispatch_preview_card` 必须满足 `dispatch_preview_card_fields`，其中 dispatch control 必须匹配 dispatch_command、explicit_runtime safety、enabled 状态和 blocker；`dispatch_batch_preview_card` 必须满足 `dispatch_batch_preview_card_fields` 且校验 count/ready_count/blocked_count 与 items 一致，并保持 `safety=explicit_runtime` 与 `requires_explicit_user=true`；`startup_preview_card` 必须满足 `startup_preview_card_fields`，校验 count/ready_count/blocked_count 与 items 一致，并保持顶层 spawn_ready control 与 `spawn_ready_command` 对齐。chat 响应里的 `agent_ready_card` 必须复用 `agentdeck agent ready` 的 ready response 字段，并嵌入同源 `runtime_card`，避免自然语言“启动所有 agent”和独立 runtime readiness 出现两套准备状态。chat 响应里的 `terminal_session_card` 必须复用 workbench terminal session 字段规则，避免自然语言 runtime/terminal strip 和 `agentdeck workbench` 出现两套项目级终端条规则。chat 响应里的 `inbox_card` 必须复用 `validate_inbox_contract()` 校验，避免自然语言 inbox 视图和独立 `agentdeck inbox` 出现两套 mailbox 规则。chat 响应里的 `approval_card` 必须复用 `validate_approval_contract()` 校验，避免自然语言审批视图和独立 `agentdeck approval list` 出现两套 approval 规则。chat 响应里的 `artifacts_card` 必须复用 `validate_artifacts_contract()` 校验，避免自然语言产物视图和独立 `agentdeck artifacts` 出现两套 artifacts 规则。chat 响应里的 `runtime_card`、`queue_card`、`operator_card`、`role_card`、`ledger_card`、`lineage_card` 和 `audit_card` 必须复用 workbench 字段规则，`workbench_card` 必须复用 `validate_workbench_contract()` 校验，避免自然语言控制面和 `agentdeck workbench` 出现两套 runtime/queue/operator/role/ledger/audit/workbench 规则；`control_registry_card.items[]` 和嵌入的 `workbench_card.control_registry[]` 只作为命令面板索引，不代表自动执行许可。

当人类明确输入 `agentdeck leader chat --message "apply action act_xxx"` 或 `--message "/apply-action act_xxx"` 时，chat 会复用 `leader apply-action` 的安全白名单。当前只会应用 `create_approvals`，并会拒绝 dispatch/capture 等 runtime action。safe apply 完成后，chat 响应会从刷新后的 recovery 继续返回下一步，例如 `agentdeck approval list`，并嵌入同源 `approval_card` 展示刚创建的审批队列，让 GUI 或对话层可以立刻进入审批检查；它不会自动 approve、reject、dispatch 或发送 tmux 输入。

`plan list` 返回计划摘要，适合给自然语言入口或 GUI 做列表视图；`plan show` 返回完整计划，适合审批前人工检查；`plan status` 汇总每个 step 的 approval 状态、message_id、attempt_id 和 job_id，适合恢复任务进度。

`leader review` 会先校验 ProjectView，再使用本地 deterministic 规则读取 plan status 和 replies，输出下一步建议：`dispatch_approved`、`wait_for_reply`、`summarize` 或 `wait_for_approval`。输出会带 `next_command` 和 GUI-ready `controls[]`；当下一步是 `wait_for_reply` 时，control 会建议 `agentdeck capture-reply --agent <id> --message-id <id>` 并标记 `safety=explicit_runtime`，但不会自动 capture pane 或写入 reply。对应的 ProjectView recovery 也会在没有更高优先级队列时返回 `status=reply_waiting`，使 `agentdeck continue` 和 workbench `operator_card` 直接暴露 trace preview 与 `capture_reply` 显式控件；自然语言 `agentdeck leader chat --message "继续"` 也会在该状态下嵌入同一条 message lineage 的 `trace_card`，并让 `intent_card.embedded_card=trace_card`，但仍只建议显式 `capture-reply` 命令。当下一步是 `summarize` 时，`next_command` 会指向 `agentdeck leader summary --plan-id <id>`；该命令只读聚合已有 replies/artifacts，返回每个 step 的 reply_text、artifact 摘要和 trace control，不调用 provider、不写 state。契约见 `docs/contracts/leader-review-schema.md` 和 `docs/contracts/leader-summary-schema.md`，可用 `agentdeck contract leader-review --example` / `agentdeck contract leader-summary --example` 发现字段；输出前会分别通过 `validate_leader_review_contract()` / `validate_leader_summary_contract()` 自校验，失败时不打印半坏 JSON。后续接入真实 Leader LLM 时应复用该输出结构。

`leader next` 会先校验 ProjectView，再把下一步建议持久化到 `leader_actions[]`，例如创建 approvals 或派发 approved step 的命令。它只记录 pending action，不执行命令；如果相同 pending action 已存在，会复用原 action_id，不重复污染 queue。`leader actions` 可查看已记录的 action queue，并通过 `recommended_action_id` 与每项 `is_recommended` 标记当前 recovery 推荐项；每项也包含 `controls[]`、`preview_command`、`can_apply`、`apply_command`、`explicit_command` 和 `apply_blocker`，输出前会通过 `validate_leader_actions_contract()` 自校验。契约见 `docs/contracts/leader-actions-schema.md`，可用 `agentdeck contract leader-actions --example` 发现。

`leader action --action-id <id>` 返回单个 action 的详情，包括 `preview_command`、`can_apply`、`apply_command`、`explicit_command` 和 `apply_blocker`。它会附带当前 `recovery`、`recommended_action` 和 `matches_recommended_action`，方便 GUI 判断这个 action 是否就是当前恢复入口推荐的下一步。它是只读入口，输出前会通过 `validate_leader_action_contract()` 自校验；契约见 `docs/contracts/leader-action-schema.md`，可用 `agentdeck contract leader-action --example` 发现。适合 GUI、自然语言 Leader 或人类在执行前确认当前 action 为什么能或不能安全应用。

`leader apply-action --action-id <id>` 是显式确认入口。它会先校验 ProjectView；当前只允许应用 `create_approvals`，会创建 approval queue 并把 action 标记为 `applied`。`dispatch_approved`、`wait_for_reply` 等 action 仍会被拒绝，必须由人类运行对应的 `approval dispatch` 或 `capture-reply` 命令。

计划确认后，可以创建审批项：

```bash
agentdeck approval create-from-plan --plan-id pln_xxx
agentdeck approval list
agentdeck approval approve --approval-id apv_xxx
agentdeck approval reject --approval-id apv_xxx --reason "范围过大"
agentdeck approval dispatch --approval-id apv_xxx
agentdeck approval dispatch-ready --confirm
```

`agentdeck approval list` 是人类审批队列的只读入口。每个 approval item 会包含 `approve_command`、`reject_command`、`dispatch_command`、`can_dispatch` 和 `dispatch_blocker`，方便 GUI 渲染审批按钮和阻塞原因；输出前会通过 `validate_approval_contract()` 自校验。契约见 `docs/contracts/approvals-schema.md`，可用 `agentdeck contract approvals --example` 发现 approval queue 以及 `dispatch-ready` 的 response/result 字段。真正的 approve/reject/dispatch 仍必须由人类显式运行对应命令。

`approval dispatch` 只接受 `approved` 状态的审批项，并把对应 plan step 转成现有 `dispatch -> message/attempt/job/inbox` 链路。成功响应会包含 `trace_command` 和目标 agent 的 `inbox_card`，方便 GUI 直接跳到 worker mailbox；它仍然是显式命令，不会自动连续派发整个 plan，也不会 ack inbox。

`approval dispatch-ready --confirm` 是显式批量派发入口：它只处理当前所有 `approved` 且目标 agent runtime ready 的审批项，逐项复用单条 `approval dispatch` 的 message/attempt/job/inbox/tmux 账本路径；目标 agent 未启动或 runtime 不可用的审批项会保留为 `approved`，并在 `results[]` 中返回完整字段集，包括 `message_id`、`trace_command`、`blocker` 和对应 `dispatch_command`。输出前会通过 `validate_approval_dispatch_ready_contract()` 自校验。不带 `--confirm` 时命令返回非 0 且不会写 state 或发送 tmux 输入。

当 workbench 发现多条 approved approvals 时，`operator_card` 会把主显式按钮切到同一条 `agentdeck approval dispatch-ready --confirm`，方便 GUI 从总览页直接渲染批量派发入口；workbench 仍然只读，不会执行该命令。

返回结果包含：

- `plan_id`
- `provider`
- `model`
- `status`
- `dispatch_ready`
- `plan.goal`
- `plan.steps[]`

DeepSeek、OpenAI-compatible、Codex CLI 和 Claude Code CLI provider 使用同一个 Leader provider 抽象和 plan schema，并要求后端返回 JSON object plan。

如果真实 provider 返回非法 JSON、缺少顶层 `goal` / `summary` / `steps`、`goal` / `summary` 不是非空字符串、缺少非空 steps、某个 step 缺少 `step` / `agent_id` / `role` / `task` / `risk` / `requires_approval`，`step` 不是正整数、重复或没有按 `1..n` 连续编号，`agent_id` / `role` / `task` / `risk` 不是非空字符串，`agent_id` 不在当前配置的 worker agent 列表中，`role` 不匹配该 agent 的配置角色，或某个 step 没有 `requires_approval: true`，CLI 会明确失败并把错误写入 `.agentdeck/state/state.json` 的 `leader_errors[]`。失败不会创建 plan、approval、message、job 或 inbox。

配置环境变量：

```bash
export DEEPSEEK_API_KEY="..."
# 可选：
export DEEPSEEK_BASE_URL="https://api.deepseek.com/v1"
export DEEPSEEK_MODEL="deepseek-chat"

# 或使用通用 OpenAI-compatible provider：
export AGENTDECK_LEADER_API_KEY="..."
export AGENTDECK_LEADER_BASE_URL="https://api.deepseek.com/v1"
export AGENTDECK_LEADER_MODEL="deepseek-chat"
```

本地 CLI-backed Leader 不需要 API key，但要求本机命令已登录并在 PATH 上：

```bash
codex login
codex doctor

claude auth
claude doctor
```

示例：

```bash
agentdeck leader plan --provider deepseek --model deepseek-chat --task "规划下一步开发"
agentdeck leader chat --provider deepseek --model deepseek-chat --message "帮我规划下一步"

agentdeck leader plan --provider openai-compatible --model "$AGENTDECK_LEADER_MODEL" --task "规划下一步开发"
agentdeck leader chat --provider openai-compatible --model "$AGENTDECK_LEADER_MODEL" --message "帮我规划下一步"

agentdeck leader plan --provider codex-cli --model codex-default --task "规划下一步开发"
agentdeck leader chat --provider claude-cli --model claude-default --message "帮我规划下一步"
```

临时试用某个 provider 时使用 `--provider`；要把它持久设为项目默认 Leader，使用：

```bash
agentdeck leader set-provider --provider codex-cli --model codex-default
agentdeck leader set-provider --provider claude-cli --model claude-default
agentdeck leader set-provider --provider claude-cli --model claude-default --require-ready
agentdeck doctor
```

`leader set-provider` 会回显目标 Leader backend 的 `ready`、`supported`、`missing_env`、`detail`、`command_path` 和 `setup_commands`，方便终端或 GUI 立即展示切换后的可用性。默认情况下它仍允许人类显式切换到暂未 ready 的 provider，然后通过 `agentdeck doctor` / `setup_commands` 修复环境；如果加上 `--require-ready`，目标 provider 不可用时会拒绝写入配置、追加 `leader_provider_update_rejected` 审计事件，并保持 `.agentdeck/config.toml` 不变。该命令不调用 provider、不创建 plan、不审批、不派发。

真实 provider 仍然只生成 plan 或 chat turn，不会自动创建 approval 或派发任务。Leader planning prompt 会把每个 worker 的 `agent_id`、`role`、`role_prompt`、provider 和 workspace mode 一起交给 provider，并明确要求 step 编号为 `1..n`、只能使用列出的 worker `agent_id`、每个 `role` 必须完全复制对应 worker role，让 DeepSeek、OpenAI-compatible、Codex CLI 或 Claude CLI 都能按角色职责拆分任务。`codex-cli` / `claude-cli` 是 `agent_id=leader` 这个逻辑 Leader 的本地推理后端，不会复用 `planner`、`coder` 或 `reviewer` 的 worker pane，也不会让 Leader 自动拥有一个 tmux pane；CLI prompt 会明确声明它只是本地 subprocess reasoning backend，不能声称自己占用或复用任何 worker tmux pane。`agentdeck leader plan/chat --model <model>` 或 `.agentdeck/config.toml` 中的 `[leader].model` 会进入真实 backend：API-backed provider 使用请求体 `model`，CLI-backed Leader 使用本地 `codex` / `claude` 的 `--model` 参数；CLI-backed Leader 可以解析纯 JSON stdout，或解析 Markdown fenced `json` block 中的 JSON plan；解析后仍必须通过同一 provider plan schema validator，确保 plan 有可展示的非空 `goal` / `summary`、每个 step 都具备从 1 开始连续且唯一的正整数序号、指向已配置 worker agent 的 `agent_id`、匹配该 agent 配置的 `role`，以及非空 task/risk 调度字段，并停在人类审批门前。

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
- 每次开发前要对照 [终极目标路线图](/Users/liuyue/Desktop/Github_repos/multi-agent-explore/docs/roadmap/ultimate-goal-roadmap.md)，确认功能没有偏离 Leader Agent 调度、多 Agent 通信、可见 runtime、审批、恢复、可审计 skill 或未来 GUI 这几条主线。
- `References/` 是本地研究材料，不纳入 git。
- README、CLAUDE.md、AGENT.md 和架构文档要跟代码同步。
- 所有开发命令默认先执行 `conda activate agentdeck`。
- 不在 MVP 阶段重写终端模拟器。
- 不在没有审批模型前实现自动写文件、kill pane、push、remote relay。
