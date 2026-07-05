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
- `dispatch`、`approval dispatch`、`reply` 和 `capture-reply` 的成功 JSON 输出必须包含 `trace_command`，指向对应 message/reply lineage；`approval dispatch` 成功响应还必须嵌入目标 agent 的同源 `inbox_card`，复用 `agentdeck inbox --agent <id>` 队列形状，但不得自动 ack 或连续派发；`reply` / `capture-reply` 当 reply 回流到某个 agent inbox 时也必须嵌入接收方 `inbox_card`，但不得自动 ack 或继续 review。
- `agentdeck ack --agent <id> --inbox-id <id>` 只能确认该 agent 最早的 pending inbox item，不得越过 head。
- `agentdeck trace --id <id>` 可用 message/attempt/job/reply/inbox 任意 ID 还原通信链路。
- `agentdeck events --limit <n>` 返回最近审计事件，不修改 state；`agentdeck events --since <event_id>` 返回 cursor 之后的审计事件和 cursor metadata，cursor 由 GUI/调用方持有，不得写入 AgentDeck state。
- `agentdeck status` 返回 ProjectView 只读摘要，包含 agents、plans、approvals、messages、jobs、replies、chat_turns、leader_actions、inbox 和 recovery，适合作为 GUI 与 Leader chat loop 的默认状态入口。
- ProjectView 详细字段契约见 `docs/contracts/project-view-schema.md`；当前 `schema_version` 是 `project-view/v1`，修改 status、recovery、GUI 或自然语言入口时必须同步该文档。
- ProjectView schema version 的源码单一来源是 `src/agentdeck/models.py` 的 `PROJECT_VIEW_SCHEMA_VERSION`。
- ProjectView contract payload 和 example fixture 维护在 `src/agentdeck/contracts.py`，需要复用时优先 import 该模块。
- Contract index 见 `docs/contracts/contract-index-schema.md`；`agentdeck contract list` 会返回所有 GUI 可消费契约的 discovery command、example command 和 schema 文档路径。新增契约命令时必须同步 `CONTRACT_INDEX_SPECS`、该文档和测试。
- Doctor diagnostics contract 见 `docs/contracts/doctor-schema.md`；`agentdeck contract doctor --example` 会返回稳定 doctor diagnostics 示例，字段常量和 example fixture 都在 `src/agentdeck/contracts.py`。doctor contract discovery 必须公开 workbench、Leader chat 和 Leader review contract 入口，供 GUI setup 面板跳转到主控制面契约。
- Events timeline contract 见 `docs/contracts/events-schema.md`；`agentdeck contract events --example` 会返回稳定 events timeline 示例，字段常量和 example fixture 都在 `src/agentdeck/contracts.py`。
- Leader chat response contract 见 `docs/contracts/leader-chat-schema.md`；`agentdeck contract leader-chat --example` 会返回包含 `leader_explanation` 的稳定响应示例。
- Workbench snapshot contract 见 `docs/contracts/workbench-schema.md`；`agentdeck contract workbench --example` 会返回稳定一屏工作台示例，字段常量和 validator 都在 `src/agentdeck/contracts.py`。
- Controls contract 见 `docs/contracts/controls-schema.md`；`agentdeck contract controls --example` 会返回稳定命令面板 card 示例，字段常量和 validator 都在 `src/agentdeck/contracts.py`。
- Agent runtime contract 见 `docs/contracts/agent-runtime-schema.md`；`agentdeck contract agent-runtime --example` 会返回稳定可见 tmux runtime 示例，包含 ready/spawn-ready/terminal/capture/refresh 响应字段，字段常量和 example fixture 都在 `src/agentdeck/contracts.py`。
- Leader actions queue contract 见 `docs/contracts/leader-actions-schema.md`；`agentdeck contract leader-actions --example` 会返回稳定队列示例，字段常量和 validator 都在 `src/agentdeck/contracts.py`。
- Leader review response contract 见 `docs/contracts/leader-review-schema.md`；`agentdeck contract leader-review --example` 会返回稳定 review 响应示例，字段常量、example fixture 和 validator 都在 `src/agentdeck/contracts.py`。修改 `leader review` 的 `next_command` 或 `controls[]` 时必须同步该 contract，并保持 live 输出通过 `validate_leader_review_contract()` 守门。
- Leader action detail contract 见 `docs/contracts/leader-action-schema.md`；`agentdeck contract leader-action --example` 会返回稳定 action detail 示例，字段常量和 validator 都在 `src/agentdeck/contracts.py`。
- Approval queue contract 见 `docs/contracts/approvals-schema.md`；`agentdeck contract approvals --example` 会返回稳定 approval queue 和 dispatch-ready 示例，字段常量和 validator 都在 `src/agentdeck/contracts.py`。
- Inbox queue contract 见 `docs/contracts/inbox-schema.md`；`agentdeck contract inbox --example` 会返回稳定 inbox 示例，字段常量和 validator 都在 `src/agentdeck/contracts.py`。
- Trace contract 见 `docs/contracts/trace-schema.md`；`agentdeck contract trace --example` 会返回稳定通信 lineage 示例，字段常量和 validator 都在 `src/agentdeck/contracts.py`。
- `validate_project_view_contract(payload)` 可校验任意 ProjectView-like payload 是否满足 v1 基础契约。
- `agentdeck status` 输出 JSON 前必须先用 `validate_project_view_contract()` 自校验；失败时只能返回错误，不输出半坏 ProjectView。
- `agentdeck contract project-view` 返回 ProjectView 契约发现元数据，不读取或修改项目 state；`--example` 会附带稳定示例，供 GUI 原型使用。
- `agentdeck contract list` 返回契约总目录，不读取或修改项目 state；GUI/TUI 启动时应优先用它发现可消费 contract，而不是在前端硬编码所有子命令。
- `agentdeck contract leader-chat` 返回自然语言 Leader chat 响应契约发现元数据，不读取或修改项目 state；`--example` 会附带稳定响应示例，供 GUI 原型使用。
- `agentdeck contract continue` 返回顶层 continue 恢复卡片契约发现元数据，不读取或修改项目 state；`--example` 会附带稳定恢复卡片示例，供 GUI 原型使用。
- `agentdeck contract workbench` 返回工作台快照契约发现元数据，不读取或修改项目 state；`--example` 会附带稳定一屏 workbench 示例，供 GUI/TUI 原型使用。
- `agentdeck contract controls` 返回独立命令面板契约发现元数据，不读取或修改项目 state；`--example` 会附带稳定 control registry card 示例，供 GUI/TUI 原型使用。
- `agentdeck contract agent-runtime` 返回 agent runtime 命令模板与 ready/spawn-ready/terminal/capture/refresh 响应字段，不读取 state、不 inspect tmux pane、不发送输入；`--example` 会附带稳定 runtime 示例，供 GUI/TUI 原型使用。
- `agentdeck agent refresh` 是显式 runtime reconciliation 命令；只检查 state 中记录为 `running` 的 pane 是否仍存在，丢失时标记为 `stale` 并写入 `agent_runtime_stale` 事件，不发送 tmux 输入、不推断任务完成。
- `agentdeck agent ready` 是只读 multi-agent runtime readiness card；它必须复用 workbench `runtime_card`，公开 total/running/not_running/all_running、所有 not-running agent 的 `spawn_commands`、`spawn_ready_command`、显式 `next_command`、`refresh_command` 和 `dispatch_ready_command`；多个 agent 未 running 时 `next_command` 必须是 `agentdeck agent spawn-ready --confirm`，只有一个 agent 未 running 时才是对应单 agent spawn，全部 running 时是 `agentdeck approval dispatch-ready --confirm`；不得写 state、追加事件、inspect tmux、spawn/stop/capture/send、refresh binding 或 dispatch approvals。
- `agentdeck agent spawn-ready --confirm` 是显式 runtime 批量启动命令；不带 `--confirm` 必须失败且不得写 state 或创建 pane；带 confirm 时只启动尚未 `running` 的 configured agents，跳过已 running agent，写入每个新 pane 的 `agent_spawned` 事件和一次 `agent_spawn_ready_completed` 事件，输出 `mode=agent_spawn_ready`、spawned/skipped 计数和逐 agent results。
- `agentdeck agent terminal --agent <id>` 是只读 visible pane 定位卡；它必须返回 tmux attach/select-pane 命令、capture/send/stop/inbox/refresh 命令和 runtime controls，不得 attach tmux、不读取 pane 输出、不发送输入、不写 state、不追加事件。
- `agentdeck workbench` 的 `runtime_card.agents[]` 必须公开 `terminal_command=agentdeck agent terminal --agent <id>`，并在 `controls[]` 中保留 `kind=terminal` / `Open terminal`；running agent 的 terminal control 可 enabled，未 running agent 的 terminal control 必须 disabled 且 blocker 为 `agent is not running`。
- `agentdeck controls` / workbench `control_registry[]` 必须保留 runtime scope 的 `kind=terminal` item，供 GUI/TUI 直接渲染打开终端入口；它是 inspect-only 终端卡入口，不代表 attach tmux、capture pane、send input 或写 state 的许可。
- `status.recovery` 必须把 `stale` runtime bindings 作为 `runtime_stale` 恢复状态暴露，`recommended_action.source=runtime` 且 `next_command=agentdeck agent refresh`；`pending.runtime_stale` 是 ProjectView recovery pending 契约字段。
- `agentdeck leader chat --message "继续"` 在 recovery source 为 `runtime` 时必须嵌入 `runtime_card`，复用 workbench runtime card 字段规则；它只展示 `agentdeck agent refresh` 入口，不自动 refresh、spawn、stop、capture 或发送 tmux 输入。
- `agentdeck leader chat --message "查看 runtime"` / `"查看终端"` 必须进入只读 `mode=runtime`，嵌入同一张 `runtime_card` 并建议 `agentdeck agent list`；`"打开 planner 终端"` / `"进入 coder pane"` 这类打开单个 visible pane 的意图必须进入只读 `mode=terminal`，嵌入 `terminal_card`，顶层 `next_command` 对齐 `terminal_card.attach_command`，不得 attach tmux、capture、send、stop 或写 runtime state；`"刷新 runtime"` 这类刷新绑定意图必须只建议显式 `agentdeck agent refresh`；`"启动所有 agent"` / `"启动全部 agent"` / `"prepare all agents"` 这类多 Agent 启动准备意图必须嵌入 `agent_ready_card`，复用 `agentdeck agent ready` 字段并把顶层 `next_command` 对齐到 card 的显式下一步：多个 agent 未 running 时是 `agentdeck agent spawn-ready --confirm`，单个未 running 时是对应单 agent spawn，全 running 时是 `agentdeck approval dispatch-ready --confirm`；`"启动 planner"` / `"spawn coder"` 这类明确启动单个 agent 的意图也必须进入 `mode=runtime`，但只能建议显式 `agentdeck agent spawn --agent <id>`；`"发送给 planner：继续"` / `"tell coder fix tests"` 这类给单个 running agent 发送输入的意图只能建议显式 `agentdeck agent send --agent <id> --text <text>`；`"停止 planner"` / `"stop coder"` 这类停止单个 running agent 的意图只能建议显式 `agentdeck agent stop --agent <id>`；目标未 spawn 时必须返回 `agent is not spawned: <id>` 且不能落入 plan/provider 路由；runtime command 建议必须在 `leader_explanation` / `intent_card` 中标记 `safety=explicit_runtime` 和 `requires_explicit_user=true`；它不创建 plan/action/approval/message/job/inbox，也不执行任何 runtime 操作。
- `agentdeck leader chat --message "查看 planner 输出"` / `"capture planner output"` 必须进入只读 `mode=capture`，嵌入 `capture_card` 并建议同一条 `agentdeck agent capture --agent <id> --lines 200`；它只读取已 spawn 的 visible pane，不创建 plan/action/approval/message/job/inbox、不 ack、不 dispatch、不 capture reply、不发送 tmux 输入；未 spawn 的 agent 必须返回 `agent is not spawned: <id>`，不能落入 plan/review/provider 路由。`"捕获 planner 对 msg_xxx 的回复"` / `"capture reply from planner for msg_xxx"` 这类 capture-reply 意图也必须进入 `mode=capture`，但只能嵌入同源 `trace_card` 并建议显式 `agentdeck capture-reply --agent <id> --message-id <msg_id>`；它不得读取 pane、不得写 reply、不得创建 message/job/inbox，`leader_explanation.action_kind` 必须是 `capture_reply`，`intent_card` next label 必须是 `Capture reply`，并标记 `safety=explicit_runtime` / `requires_explicit_user=true`。
- `agentdeck leader chat --message "派发当前审批"` 必须继续保持只读 `mode=approval`，但当存在 approved approval 时应嵌入 `dispatch_preview_card`，展示目标 agent/role/pane/task/dispatch_command/inbox_command/controls 和 blocker；如果 blocker 存在，card 内 dispatch control 和 `intent_card.controls[]` 的 next control 必须 disabled 并复用同一个 blocker；它只提供 explicit-runtime 执行前预览，不创建 message/job/inbox、不发送 tmux 输入。`agentdeck leader chat --message "派发所有已审批"` / `"dispatch all approvals"` 必须嵌入 `dispatch_batch_preview_card`，其中 `items[]` 复用单条 dispatch preview 字段并暴露每个 approval 的 inspect/dispatch controls 和 blocker，count/ready_count/blocked_count 必须和 items 一致；顶层 `next_command` 应指向显式 `agentdeck approval dispatch-ready --confirm`，但 chat 本身不得执行派发。
- `agentdeck leader chat --message "查看队列"` / `"查看控制面"` 必须进入只读 `mode=queue`，嵌入 workbench 同源 `queue_card` / `operator_card` 并展示 next/apply/explicit controls；顶层 `next_command` 必须对齐 `operator_card` 的主命令，因此多条 approved approvals 时应推荐 `agentdeck approval dispatch-ready --confirm`，且 `operator_card.controls[]` 中对应 control 必须是 `kind=dispatch_ready`；它不创建或应用 action、不审批、不派发、不 ack、不 refresh runtime、不发送 tmux 输入。
- `agentdeck leader chat --message "查看角色"` / `"查看分工"` 必须进入只读 `mode=role`，嵌入 workbench 同源 `role_card` 并展示 assign-role 命令和 `kind=assign_role` controls；`"把 planner 设为 架构师"` / `"set reviewer role to QA"` 这类自然语言角色指派也必须进入 `mode=role`，但只能建议显式 `agentdeck agent assign-role --agent <id> --role <role> --role-prompt <prompt>`，并在 `leader_explanation` / `intent_card` 中标记 `action_kind=role_assign`、`safety=explicit_user`、`requires_explicit_user=true`；它不修改 `.agentdeck/config.toml`、不创建 plan/action/approval/message/job/inbox、不发送 tmux 输入。
- `agentdeck leader chat --message "让 planner 规划 README 更新"` / `"指派 coder 修复测试"` 这类自然语言任务指派必须进入 `mode=approval`，只创建一条 `source=leader_chat_task_assignment` 的 pending approval，并把 `next_command` 指向该 approval 的 `approve_command`；它可以写 approval queue 和 chat turn，但不得创建 plan/leader action，不得 approve/dispatch，不得创建 message/job/inbox 或发送 tmux 输入；`leader_explanation.action_kind` 必须是 `approval_create`，`intent_card.read_only=false`，下一步 control 仍必须是 `safety=explicit_runtime` / `requires_explicit_user=true`。
- `agentdeck leader chat --message "查看账本"` / `"查看通信"` 必须进入只读 `mode=ledger`，嵌入 workbench 同源 `ledger_card` 和 `lineage_card`，展示 trace_commands 与最近通信路径；它不创建 plan/action/approval/message/job/inbox、不 ack、不 dispatch、不 capture reply、不读取 pane 输出、不发送 tmux 输入。
- `agentdeck leader chat --message "追踪 msg_xxx"` / `"trace job_xxx"` / `"查看 rep_xxx 链路"` 必须进入只读 `mode=trace`，嵌入同源 `trace_card` 并建议 `agentdeck trace --id <id>`；它不创建 plan/action/approval/message/job/inbox、不 ack、不 dispatch、不 capture reply、不读取 pane 输出、不发送 tmux 输入；未知 trace id 必须返回错误，不能落入 plan/review/provider 路由。
- `agentdeck leader chat --message "打开工作台"` / `"查看总览"` 必须进入只读 `mode=workbench`，嵌入完整 `workbench_card` 并通过 `validate_workbench_contract()` 校验；`agentdeck contract leader-chat` 必须公开 `workbench_control_registry_item_fields`，供自然语言壳发现嵌入 `workbench_card.control_registry[]` 的命令面板字段；它不创建 plan/action/approval/message/job/inbox、不 ack、不 approve、不 dispatch、不 refresh runtime、不 capture、不读取 pane 输出、不发送 tmux 输入。
- `agentdeck leader chat --message "帮助"` / `"help"` / `"你能做什么"` / `"命令面板"` 必须进入只读 `mode=help`，嵌入 `capability_card` 作为自然语言壳能力发现入口，并嵌入 `control_registry_card` 作为未来 GUI 命令面板快照；它必须暴露 plan/review/apply_action 这条 Leader 调度主线、policy 控制模式入口以及只读控制面能力，每个 capability item 必须带 GUI-ready `controls[]`，其中 plan control 必须指向显式 `agentdeck leader plan --task <goal>`，review control 必须指向 `agentdeck leader review --plan-id <plan_id>`，policy control 必须指向显式 `agentdeck policy set-mode --mode <mode>`；capability control 只能使用 `<goal>`、`<plan_id>`、`<action_id>`、`<agent_id>`、`<mode>` 占位符，所有包含占位符的 control 必须 disabled，并提供与占位符类型匹配的 blocker；`control_registry_card` 必须从同一次 workbench snapshot 派生，保留 leader/runtime/operator controls 的 safety/enabled/blocker，且 `default_command` 必须指向独立入口 `agentdeck controls`；help mode 本身不调用 provider、不创建 plan/action/approval/message/job/inbox、不读取 pane、不发送 tmux 输入。
- `agentdeck leader chat --message "切换到审批模式"` / `"回到 ask 模式"` / `"开启 autonomous"` 必须进入 `mode=policy`，嵌入 workbench 同源 `control_mode_card` 并建议显式 `agentdeck policy set-mode --mode <mode>`；它只记录 chat turn，不修改 `.agentdeck/config.toml`、不创建 plan/action/approval/message/job/inbox、不调用 provider、不发送 tmux 输入；`autonomous` 只能作为被策略命令拒绝的下一步建议。
- `agentdeck leader chat --message "切换 Leader 到 Codex CLI"` / `"使用 Claude Code 做 Leader"` / `"换成 DeepSeek Leader"` 必须进入只读 `mode=setup`，嵌入 workbench 同源 `provider_health`，并建议具体 `agentdeck leader set-provider --provider <provider> --model <model>`；`leader_explanation.action_kind` 必须是 `provider_switch`，`safety=explicit_user`，`requires_explicit_user=true`，`intent_card` 必须以 `provider_health` 为 embedded_card 并保留 `agentdeck doctor` inspect control；该模式只记录 chat turn，不修改 `.agentdeck/config.toml`、不调用当前或目标 provider、不创建 plan/action/approval/message/job/inbox、不发送 tmux 输入。
- 所有 `agentdeck leader chat` 响应都必须包含 `intent_card`，用于 GUI/自然语言壳解释 mode、matched_intent、route_source、embedded_card、read_only、next_command、requires_explicit_user 和 `controls[]`；intent controls 必须使用 `kind`、`label`、`command`、`safety`、`enabled`、`blocker`，其中 `kind=inspect` 必须是 `safety=inspect`，disabled control 必须提供 blocker，runtime、policy、approval、inbox ack 与只读观察类 action 的 next control 必须使用动作级 label，带 `<reason>` 等模板输入的 next control 必须 disabled 并提供匹配 blocker；新增 chat mode 时必须同步 `LEADER_CHAT_INTENT_CARD_FIELDS`、`LEADER_CHAT_INTENT_CONTROL_FIELDS`、`LEADER_CHAT_CAPABILITY_CARD_FIELDS`、`LEADER_CHAT_CAPABILITY_ITEM_FIELDS`、`capability_control_fields` discovery、validator、contract docs、README、HISTORY 和测试。
- `agentdeck contract leader-chat` 必须暴露 `capability_placeholder_fields` 和 `capability_placeholders`，让 GUI 机器发现 capability command 模板支持的 placeholder 白名单及 blocker；新增 placeholder 时必须同步 `LEADER_CHAT_CAPABILITY_PLACEHOLDERS`、validator、contract docs、README、HISTORY 和测试。
- `agentdeck contract leader-actions` 返回 Leader action queue 契约发现元数据，不读取或修改项目 state；`--example` 会附带稳定队列示例，供 GUI 原型使用。
- `agentdeck contract leader-action` 返回单个 Leader action 详情契约发现元数据，不读取或修改项目 state；`--example` 会附带稳定 action detail 示例，供 GUI 原型使用。
- `agentdeck contract approvals` 返回人类审批队列和 dispatch-ready 响应契约发现元数据，不读取或修改项目 state；`--example` 会附带稳定 approval queue 和 dispatch-ready 示例，供 GUI 原型使用。
- `agentdeck contract inbox` 返回单 agent mailbox 契约发现元数据，不读取或修改项目 state；`--example` 会附带稳定 inbox 示例，供 GUI 原型使用。
- `agentdeck contract trace` 返回通信 lineage 契约发现元数据，不读取或修改项目 state；`--example` 会附带稳定 trace 示例，供 GUI 原型使用。
- `agentdeck trace --id <id>` 输出前必须通过 `validate_trace_contract()` 自校验；失败时只能返回错误，不输出半坏 trace。
- `agentdeck leader chat` 输出 JSON 前必须通过 `validate_leader_chat_contract()` 自校验；校验失败时只能返回错误，不输出半坏 chat response，并必须写入 `leader_errors[]` 和 `leader_chat_contract_failed` 事件。
- `agentdeck leader actions` 输出 JSON 前必须通过 `validate_leader_actions_contract()` 自校验；校验失败时只能返回错误，不输出半坏 action queue；每个 action item 必须公开 `controls[]`、`preview_command`、can_apply、apply_command、explicit_command、apply_blocker 和 is_recommended。
- `agentdeck leader action --action-id <id>` 输出 JSON 前必须通过 `validate_leader_action_contract()` 自校验；校验失败时只能返回错误，不输出半坏 action detail；detail 必须公开 `preview_command`、can_apply、apply_command、explicit_command 和 apply_blocker。
- `agentdeck approval list` 输出 JSON 前必须通过 `validate_approval_contract()` 自校验；校验失败时只能返回错误，不输出半坏 approval queue；每个 approval item 必须公开 `controls[]`、`preview_command`、approve/reject/dispatch commands、can_dispatch 和 dispatch_blocker。
- `agentdeck inbox --agent <id>` 输出 JSON 前必须通过 `validate_inbox_contract()` 自校验；校验失败时只能返回错误，不输出半坏 inbox queue；每个 inbox item 必须公开 `controls[]`、`preview_command`、trace_command、ack_command、is_head、can_ack 和 ack_blocker。
- `status.recovery` 是当前恢复入口，包含 status/reason/next_command/recommended_action/pending/leader_action/latest_event/recent_events，用来判断下一步该继续什么；`recommended_action` 可直接驱动 GUI 的下一步按钮或检查入口，并通过 target_id 关联 action、approval 或 inbox item。
- 当没有 pending action、approval 或 inbox item 但存在 `leader_errors[]` 时，`status.recovery` 会返回 `status=leader_error`，并推荐 inspect 型 `agentdeck status`。
- `status.recovery.pending` 包含 `leader_errors` 计数，供 GUI 统一展示 Leader 错误数量。
- `agentdeck contract project-view` 通过 `recovery_pending_fields` 暴露 `recovery.pending` 必备字段；缺字段的 ProjectView 应被 validator 拒绝。
- `status.chat_turns.items` 包含 action_id/action_kind，可把自然语言 turn 关联回 leader_actions。
- `status.leader_actions` 包含 recommended_action_id，`items[]` 包含 controls[]/preview_command/can_apply/apply_command/explicit_command/apply_blocker/is_recommended，可直接驱动 GUI action 预览、按钮、阻塞提示和当前推荐项高亮。
- `status.messages.items[]`、`status.jobs.items[]` 和 `status.replies.items[]` 包含 `trace_command`；ProjectView contract discovery 会公开对应 item field lists，缺少 trace 入口的 summary item 应被 validator 拒绝。
- `agentdeck continue` 是顶层只读恢复入口；它必须先通过 ProjectView contract 守门，再通过 `validate_continue_contract()` 自校验，最后返回 recovery-driven 下一步卡片；当多条 approvals 已 approved 时，card 级 `next_command` 和 `recommended_action.command` 必须提升为显式 `agentdeck approval dispatch-ready --confirm`，但不得写 state、创建 action、apply action、dispatch 或发送 tmux 输入；`agentdeck contract continue` 会公开 `continue_card_fields`。
- `agentdeck workbench` 是 GUI/TUI 优先的一屏只读快照；它必须先通过 ProjectView contract 守门，再组合 project_view、leader_actions、leader_card、provider_health、runtime_card、role_card、ledger_card、lineage_card、queue_card、operator_card、audit_card、contracts_card、control_mode_card、change_summary、recovery、continue_card、active_queue_source、inbox_card、leader_inbox_card、approval_card 和 leader_action，并通过 `validate_workbench_contract()` 自校验；`control_mode_card` 必须从 Leader approval_mode 派生 ask/approve/autonomous 授权梯度，workbench 默认只读展示，不得自动修改策略或放权执行；`control_mode_card.active_controls[]` 必须包含 ask/approve/autonomous 三个具体 `set_mode` 控件，当前模式必须 disabled 并给出 `already current mode` blocker，approve 控件必须使用 `safety=explicit_user`，autonomous 控件必须 disabled 并给出未实现 blocker，`set_mode_command_template` 只能作为表单辅助；`agentdeck policy set-mode --mode ask|approve` 是唯一显式策略切换入口，必须只写 `leader.approval_mode` 并追加审计事件；`agentdeck policy set-mode --mode autonomous` 必须拒绝、保持配置不变并追加拒绝事件；`leader_card` 必须从 ProjectView leader 派生 agent_id/provider/model/approval_mode/api_backed、chat/continue/review/actions/status 入口命令和 controls[]，chat/review 模板 controls 必须 disabled 并提供 blocker，continue/actions/status controls 必须是只读 inspect 入口，且不得暴露 API key 或调用 provider；`provider_health` 必须从 Leader provider 和本地环境变量派生 supported/ready/missing_env/detail/doctor_command/doctor_contract/setup_commands/controls[]，只能暴露 env 名称和显式 `agentdeck leader set-provider` 切换命令，不能暴露密钥值或调用 provider；provider controls 必须使用 `kind=set_provider`、`safety=explicit_user`，当前 provider disabled 并给出 `already current provider` blocker；`runtime_card` 必须从 ProjectView agents/runtime 派生并公开 agent role/provider/workspace/status/pane/session/cwd、spawn/stop/capture/send template/inbox 命令和 controls[]，其中 capture 只读，send/spawn/stop 类 runtime control 只能由人类显式触发；`role_card` 必须从 ProjectView agents 派生 role/provider/workspace_mode/role_prompt/assign_command/controls[]，role controls 必须使用 `kind=assign_role`、`safety=explicit_user`，模板命令缺少 role/role_prompt 时必须 disabled 并给出 blocker；`ledger_card` 必须从 ProjectView messages/jobs/replies/inbox 派生并保留 trace_commands；`lineage_card` 必须从 ledger 摘要和可见 inbox cards 派生最近通信路径，保留 message/job/reply/inbox id、双方 actor/agent、task、status 和 trace_command，且不得成为第二套 ledger 或执行入口；`queue_card` 必须从 leader_actions/approvals/inbox/recovery next_command 派生队列总览；`operator_card` 必须从 recovery/recommended_action 和当前 active queue 派生，公开 controls[]/command/preview_command/safety/requires_explicit_user/apply_command/explicit_command/blocker 等人类操作字段；当 recovery 指向 approved approval dispatch 时，`operator_card` 必须从 ProjectView agents/runtime 派生 blocker，目标 agent 没有 running pane 时必须禁用 explicit control；当多条 approvals 已 approved 时，`operator_card.action_kind` 可以提升为 `approval_dispatch_ready`，`command`/`explicit_command` 必须指向 `agentdeck approval dispatch-ready --confirm`，匹配 control 必须使用 `kind=dispatch_ready`，并且仍不得自动执行；`control_registry` 必须从 leader/provider/policy/role/runtime/inbox/operator controls 派生为只读命令面板索引，保留 scope/card/kind/label/command/safety/enabled/blocker/agent_id，provider scope 必须保留 `kind=set_provider` items，role scope 必须保留 `kind=assign_role` items，inbox scope 必须保留 `inbox_card.items[].controls[]` 和 `leader_inbox_card.items[].controls[]` 的 preview/ack items，且不得成为第二套状态源或绕过 control 的 safety/blocker；`audit_card` 必须从 recovery latest/recent event summary 派生并公开 events_command；`contracts_card` 必须公开 `agentdeck contract list`、contract index schema、workbench/controls/agent-runtime/leader-chat/leader-review/project-view/events/doctor contract 入口，且不得读取 state 或执行任何 contract 命令；`change_summary` 必须按可选 `--since-event <event_id>` 从审计事件账本临时计算，不得保存 cursor 到 state；不得写 state、创建 chat turn、ack、approve、dispatch、capture reply、读取 pane 输出或发送 tmux 输入；`agentdeck workbench --watch --since-event <event_id> --interval <seconds>` 必须输出同一契约形状的 JSONL 状态流，每行都通过 `validate_workbench_contract()`，并可用 `--iterations <n>` 有界退出；`agentdeck contract workbench` 会公开 `snapshot_fields`、`leader_card_fields`、`leader_control_fields`、`control_mode_card_fields`、`control_mode_option_fields`、`control_mode_control_fields`、`provider_health_fields`、`runtime_card_fields`、`runtime_agent_fields`、`runtime_control_fields`、`role_card_fields`、`role_agent_fields`、`ledger_card_fields`、`lineage_card_fields`、`lineage_path_fields`、`queue_card_fields`、`operator_card_fields`、`audit_card_fields`、`contracts_card_fields`、`change_summary_fields`、`control_registry_item_fields`，且 `contracts_card_fields` 必须包含 `controls_contract`。
- `agentdeck controls` 是独立只读命令面板入口；它必须从同一次 workbench snapshot 派生 `control_registry_card`，复用 leader/provider/policy/role/runtime/inbox/operator controls 的 scope/card/kind/label/command/safety/enabled/blocker/agent_id，其中 provider scope 必须来自 `provider_health.controls[]` 并保留 `kind=set_provider` items，policy scope 必须来自 `control_mode_card.active_controls[]`，role scope 必须来自 `role_card.agents[].controls[]` 并保留 `kind=assign_role` items，inbox scope 必须来自可见 `inbox_card.items[].controls[]` 和固定 `leader_inbox_card.items[].controls[]` 并保留 `kind=preview` / `kind=ack` items，operator scope 的批量审批派发入口必须保留 `kind=dispatch_ready`，`source_command=agentdeck workbench`、`default_command=agentdeck controls`；不得成为第二套状态源，不得写 state、创建 chat turn、调用 provider、读取 pane 或执行任何 control/ack；输出前必须通过 `validate_control_registry_card_contract()` 自校验；`agentdeck contract controls` 必须公开 `control_registry_card_fields` 和 `control_registry_item_fields`，并在 `agentdeck contract list` 中可发现。
- `status.recovery.status=inbox_pending` 时，`next_command` 和 `recommended_action.command` 指向具体 `agentdeck inbox --agent <id>`，供 GUI/continue 直接打开对应 mailbox。
- `status.recovery.status=provider_setup_required` 时，`next_command` 和 `recommended_action.command` 指向 `agentdeck doctor`，`recommended_action.source` 是 `provider_health`；该状态只用于 setup/diagnostics，不得创建 plan、chat turn、approval、message、job、inbox 或发送 tmux 输入。
- `status.inbox.heads` 按 agent 暴露最早 pending inbox item；GUI/Leader 应优先用它判断当前可处理或可 ack 的 mailbox head。
- 后续升级为更严格的 reply block 标记。

## Leader Planning

- `agentdeck leader chat --message <text>` 是自然语言 Leader 入口 MVP；它读取 ProjectView 前必须通过 `validate_project_view_contract()` 守门，无 plan 时创建 plan-only 记录、持久化一条 safe `create_approvals` Leader action，并在响应前重新读取 ProjectView，使同次响应包含刚创建的 plan、chat turn 和 action queue；有 plan 时 review 最新 plan，并持久化或复用一条 `leader_actions[]` 建议；chat 输出包含顶层 `leader_actions`，且它等于同次响应的 `project_view.leader_actions`；chat 输出还包含 `leader_explanation`，用于解释当前推荐动作、safety 和是否需要人类显式确认；plan/review 输出包含 `recovery`，且 `next_command` 来自 `recovery.next_command`；当响应包含顶层 `leader_action` 时，必须同时包含从同一 action detail 派生的 `leader_action_card`，保留 action_id/kind/status/reason/preview_command/can_apply/apply_command/explicit_command/apply_blocker/controls，且不得成为第二套 action 状态源。
- `agentdeck leader chat --message "继续"`、`"继续吧"` 或 `"/continue"` 走 recovery-first 的 `mode=continue`，复用 `agentdeck continue` 的下一步卡片；顶层 `next_command`、`leader_explanation.next_command` 和 chat turn 记录必须对齐 `continue_card.next_command`；当 recovery 指向 pending inbox 时同时嵌入对应 agent 的 `inbox_card`，当 recovery 指向 approval queue 时同时嵌入 `approval_card`，多条 approved approvals 时 `continue_card.next_command` 必须指向显式 `agentdeck approval dispatch-ready --confirm`；只记录 chat turn，不创建新的 leader action、不 apply action、不 ack、不 approve、不 dispatch、不发送 tmux 输入；`agentdeck contract leader-chat` 会公开 `continue_card_fields`，example 会包含稳定 `continue_card`；嵌入的 `continue_card` 必须通过 `validate_continue_contract()` 校验，嵌入的队列卡片必须复用对应 queue validator。
- `agentdeck leader chat --message "检查 Leader provider 配置"`、`"doctor"` 或类似 setup/diagnostics 意图必须走只读 `mode=setup`，返回 `provider_health`、`recovery`、`next_command=agentdeck doctor` 和 `leader_explanation`；只记录 chat turn，不得调用 provider，不得创建 plan/leader action/approval/message/job/inbox，不得发送 tmux 输入；`provider_health` 必须复用 workbench provider health 字段，并公开 `doctor_contract` 和 `controls[]` 供 GUI 发现 doctor schema 与 provider switch 命令；`setup_commands` 只能包含 placeholder export 命令，且不得暴露密钥值。
- `agentdeck leader chat` capability help 必须包含 `provider_switch` 能力，命令模板为 `agentdeck leader set-provider --provider <provider> --model <model>`，`safety=explicit_user`，模板 control 必须 disabled 并使用 `<provider>` / `<model>` placeholder 白名单；help mode 不得执行 provider switch。
- `agentdeck leader chat --message "查看 planner 输出"` 这类 pane capture 意图走只读 `mode=capture`，返回 `capture_card`，字段必须匹配 `LEADER_CHAT_CAPTURE_CARD_FIELDS` / `capture_card_fields`；它只记录 chat turn 和读取目标 pane 输出，不创建 plan/leader action，不执行 ack、dispatch、capture reply 或发送 tmux 输入；未 spawn 的 agent 必须返回错误且不创建 chat turn 或 plan。`"捕获 planner 对 msg_xxx 的回复"` 这类 capture-reply 意图也走 `mode=capture`，但只嵌入 `trace_card` 和建议显式 `agentdeck capture-reply --agent <id> --message-id <msg_id>`，不得读取 pane 或写 reply。
- `agentdeck leader chat --message "查看 planner inbox"` / `"查看 leader inbox"` 这类 inbox 意图走只读 `mode=inbox`，复用 `agentdeck inbox --agent <id>` 的 queue shape 返回 `inbox_card`；`<id>` 可以是配置里的 worker agent，也可以是逻辑 Leader mailbox owner `leader`，用于查看 worker reply 回流，但不代表 Leader 有 tmux pane；`"查看当前 inbox"` / `"确认当前 inbox"` 可在 recovery 指向 pending inbox 时从 `target_id` 反查 mailbox owner，recovery 不是 inbox 时不得猜测目标 agent；包含 `追踪`、`trace` 或 `lineage` 且存在 pending head 时，`next_command` 可推荐该 head 的 `agentdeck trace --id <inbox_id>`，并在能解析 lineage 时嵌入同源 `trace_card`，让 `intent_card.embedded_card=trace_card` 直接展示通信证据链；包含 `确认`、`ack` 或 `acknowledge` 且 head 可 ack 时，`next_command` 可推荐该 head 的 `ack_command`，但 `leader_explanation.safety` 必须是 `explicit_runtime` 且 `requires_explicit_user=true`；该模式只记录 chat turn，不创建 plan/leader action，不执行 ack、不 dispatch、不 capture reply、不发送 tmux 输入；嵌入的 `inbox_card` 必须通过 `validate_inbox_contract()` 校验，嵌入的 `trace_card` 必须通过 `validate_trace_contract()` 校验。
- `agentdeck leader chat --message "追踪 msg_xxx"` 这类 direct trace 意图走只读 `mode=trace`，复用 `agentdeck trace --id <id>` 的 response shape 返回 `trace_card`；该模式只记录 chat turn，不创建 plan/leader action，不执行 ack、dispatch、capture reply 或发送 tmux 输入；嵌入的 `trace_card` 必须通过 `validate_trace_contract()` 校验，未知 trace id 必须返回错误且不创建 chat turn 或 plan。
- `agentdeck leader chat --message "查看审批"` 这类 approval 意图走只读 `mode=approval`，复用 `agentdeck approval list` 的 queue shape 返回 `approval_card`；包含 `批准` 或 `approve` 且存在 pending approval 时，`next_command` 可推荐第一条 pending approval 的 `approve_command`；包含 `拒绝`、`驳回` 或 `reject` 且存在 pending approval 时，`next_command` 可推荐第一条 pending approval 的 `reject_command`；包含 `派发` 或 `dispatch` 且存在 approved approval 时，`next_command` 可推荐第一条 approved approval 的 `dispatch_command`，并嵌入 `dispatch_preview_card` 作为 GUI-ready 执行前确认卡；当 `dispatch_preview_card.blocker` 非空时，card 内 dispatch control 与 intent next control 必须 disabled 且 blocker 相同；包含 `所有`、`全部`、`all` 或 `batch` 的批量派发意图必须返回 `dispatch_batch_preview_card`，items 复用单条 dispatch preview 字段和 controls，顶层 `next_command` 必须指向 `agentdeck approval dispatch-ready --confirm`，但 chat 不得自动派发；approve/reject/dispatch 建议的 `leader_explanation.safety` 必须是 `explicit_runtime` 且 `requires_explicit_user=true`；该模式只记录 chat turn，不创建 plan/leader action，不执行 approve/reject/dispatch-ready/dispatch、不创建 message/job/inbox、不发送 tmux 输入；嵌入的 `approval_card` 必须通过 `validate_approval_contract()` 校验，嵌入的 `dispatch_preview_card` 必须匹配 `LEADER_CHAT_DISPATCH_PREVIEW_CARD_FIELDS` / `dispatch_preview_card_fields`，嵌入的 `dispatch_batch_preview_card` 必须匹配 `LEADER_CHAT_DISPATCH_BATCH_PREVIEW_CARD_FIELDS` / `dispatch_batch_preview_card_fields`。
- `agentdeck leader chat --message "apply action <id>"` 会复用 safe apply-action 白名单；当前只允许应用 `create_approvals`，runtime action 必须继续显式命令执行；safe apply 完成后的 `next_command` 必须来自刷新后 `recovery.next_command`，并且当 safe apply 创建 approvals 时必须嵌入同源 `approval_card` 供 GUI/对话层展示审批队列，但不得自动 approve/reject/dispatch 或发送 tmux 输入。
- `agentdeck leader chat-history` 返回已持久化的 chat turns 摘要，用于恢复自然语言调度上下文；review turn 会包含 action_id/action_kind。
- `agentdeck leader plan --task <text>` 会写入 `.agentdeck/state/state.json` 的 `plans[]`。
- `agentdeck leader review --plan-id <id>` 会先通过 ProjectView contract 守门，再基于 plan status 和 replies 输出下一步建议；输出必须包含 `next_command` 和 GUI-ready `controls[]`，其中 `wait_for_reply` 推荐 `agentdeck capture-reply --agent <id> --message-id <id>` 但不执行 capture。
- `agentdeck leader next` 会先通过 ProjectView contract 守门，再把下一步建议写入 `leader_actions[]`，但不会执行命令；相同 pending action 已存在时会复用原 action_id。
- `agentdeck leader actions` 返回已持久化的 action queue 摘要，包含顶层 `recommended_action_id`、每项 `is_recommended`、`preview_command` 和 `controls[]`。
- `agentdeck leader action --action-id <id>` 返回单个 action 的只读详情，包含 `preview_command`、`can_apply`、`apply_command`、`explicit_command`、`apply_blocker`、当前 `recovery`、`recommended_action` 和 `matches_recommended_action`。
- `agentdeck leader apply-action --action-id <id>` 执行 safe apply 前必须通过 ProjectView contract 守门；当前只允许应用 `create_approvals`，dispatch/capture 类 action 必须继续由人类显式命令执行。
- `agentdeck plan list` 返回 plan 摘要，不包含完整 `plan` body。
- `agentdeck plan show --plan-id <id>` 返回完整 plan，用于审批前检查。
- `agentdeck plan status --plan-id <id>` 返回 plan step、approval 状态和 dispatch lineage 汇总。
- Provider 失败会写入 `leader_errors[]`，并通过 `agentdeck status` 暴露摘要；失败不能创建 plan、approval、message、job 或 inbox。
- `agentdeck doctor` 必须返回当前配置 Leader 的 `configured_leader` readiness 摘要，并让顶层 `ok` 受配置 provider readiness 影响；`configured_leader.setup_commands` 只能包含 placeholder export 命令，只能暴露缺失 env 名称，不能暴露密钥值。
- `agentdeck leader plan` 和 `agentdeck leader chat` 默认读取 `.agentdeck/config.toml` 的 `[leader] provider/model`；`fake` provider 是显式 dry-run provider，不调用外部 LLM。
- `agentdeck leader set-provider --provider <provider> --model <model>` 是持久切换默认 Leader provider 的显式命令；它只修改 `.agentdeck/config.toml` 的 `[leader] provider/model` 并追加 `leader_provider_updated` 事件，不调用 provider、不创建 plan/action/approval/message/job/inbox、不发送 tmux 输入；未知 provider 必须失败且不得修改配置。
- `deepseek` provider 通过 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL` 和 `DEEPSEEK_MODEL` 调用 OpenAI-compatible `/chat/completions`，但仍然只生成 plan。
- `openai-compatible` provider 通过 `AGENTDECK_LEADER_API_KEY`、`AGENTDECK_LEADER_BASE_URL` 和 `AGENTDECK_LEADER_MODEL` 调用 `/chat/completions`，但仍然只生成 plan。
- `codex-cli` / `claude-cli` provider 是 CLI-backed Leader provider：它们通过本地 `codex exec` / `claude --print` 非交互调用为 `agent_id=leader` 生成同一 JSON plan schema；stdout 可以是纯 JSON plan，也可以把唯一 JSON plan 包在 Markdown fenced `json` block 中；不得复用 worker tmux pane 作为 Leader，也不得自动创建 approval、dispatch 或发送 tmux 输入。
- CLI-backed Leader readiness 只检查本地命令是否存在并提供 `codex login` / `codex doctor` 或 `claude auth` / `claude doctor` setup commands；不得要求或暴露 API key。
- chat/plan-only 阶段不会写入 `messages`、`jobs` 或 `inbox`，也不会发送 tmux 输入。
- 后续其他 API-backed provider 必须复用 DeepSeek/OpenAI-compatible 的同一 plan schema。

## 审批规则

- `agentdeck approval create-from-plan --plan-id <id>` 会从 plan steps 创建 `approvals[]`。
- `agentdeck approval list` 可查看审批项。
- `agentdeck approval approve --approval-id <id>` 将审批项标记为 `approved`。
- `agentdeck approval reject --approval-id <id> --reason <text>` 将审批项标记为 `rejected`。
- `agentdeck approval dispatch --approval-id <id>` 只接受 `approved` 审批项，并把对应 plan step 派发到目标 agent。
- `agentdeck approval dispatch-ready --confirm` 是显式批量派发命令，只派发 approved 且目标 agent runtime ready 的审批项；blocked 项必须保留为 approved 并返回同一 result 字段集里的 blocker/dispatch_command；输出前必须通过 `validate_approval_dispatch_ready_contract()`；不带 `--confirm` 必须失败且不得写 state 或发送 tmux 输入。
- approval dispatch 默认是单步显式命令；批量派发只能通过 `dispatch-ready --confirm` 这种明确入口触发。

以下动作必须进入审批：

- 写文件。
- 删除或移动文件。
- 执行 destructive shell command。
- 向 agent pane 发送可执行输入。
- kill 或 respawn pane。
- git commit/push/merge/reset。
- 暴露远程访问或写入 credential。
