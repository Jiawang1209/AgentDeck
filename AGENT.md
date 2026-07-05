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
- `dispatch`、`approval dispatch`、`reply` 和 `capture-reply` 的成功 JSON 输出必须包含 `trace_command`，指向对应 message/reply lineage。
- `agentdeck ack --agent <id> --inbox-id <id>` 只能确认该 agent 最早的 pending inbox item，不得越过 head。
- `agentdeck trace --id <id>` 可用 message/attempt/job/reply/inbox 任意 ID 还原通信链路。
- `agentdeck events --limit <n>` 返回最近审计事件，不修改 state。
- `agentdeck status` 返回 ProjectView 只读摘要，包含 agents、plans、approvals、messages、jobs、replies、chat_turns、leader_actions、inbox 和 recovery，适合作为 GUI 与 Leader chat loop 的默认状态入口。
- ProjectView 详细字段契约见 `docs/contracts/project-view-schema.md`；当前 `schema_version` 是 `project-view/v1`，修改 status、recovery、GUI 或自然语言入口时必须同步该文档。
- ProjectView schema version 的源码单一来源是 `src/agentdeck/models.py` 的 `PROJECT_VIEW_SCHEMA_VERSION`。
- ProjectView contract payload 和 example fixture 维护在 `src/agentdeck/contracts.py`，需要复用时优先 import 该模块。
- Leader chat response contract 见 `docs/contracts/leader-chat-schema.md`；`agentdeck contract leader-chat --example` 会返回包含 `leader_explanation` 的稳定响应示例。
- Workbench snapshot contract 见 `docs/contracts/workbench-schema.md`；`agentdeck contract workbench --example` 会返回稳定一屏工作台示例，字段常量和 validator 都在 `src/agentdeck/contracts.py`。
- Leader actions queue contract 见 `docs/contracts/leader-actions-schema.md`；`agentdeck contract leader-actions --example` 会返回稳定队列示例，字段常量和 validator 都在 `src/agentdeck/contracts.py`。
- Leader action detail contract 见 `docs/contracts/leader-action-schema.md`；`agentdeck contract leader-action --example` 会返回稳定 action detail 示例，字段常量和 validator 都在 `src/agentdeck/contracts.py`。
- Approval queue contract 见 `docs/contracts/approvals-schema.md`；`agentdeck contract approvals --example` 会返回稳定 approval queue 示例，字段常量和 validator 都在 `src/agentdeck/contracts.py`。
- Inbox queue contract 见 `docs/contracts/inbox-schema.md`；`agentdeck contract inbox --example` 会返回稳定 inbox 示例，字段常量和 validator 都在 `src/agentdeck/contracts.py`。
- Trace contract 见 `docs/contracts/trace-schema.md`；`agentdeck contract trace --example` 会返回稳定通信 lineage 示例，字段常量和 validator 都在 `src/agentdeck/contracts.py`。
- `validate_project_view_contract(payload)` 可校验任意 ProjectView-like payload 是否满足 v1 基础契约。
- `agentdeck status` 输出 JSON 前必须先用 `validate_project_view_contract()` 自校验；失败时只能返回错误，不输出半坏 ProjectView。
- `agentdeck contract project-view` 返回 ProjectView 契约发现元数据，不读取或修改项目 state；`--example` 会附带稳定示例，供 GUI 原型使用。
- `agentdeck contract leader-chat` 返回自然语言 Leader chat 响应契约发现元数据，不读取或修改项目 state；`--example` 会附带稳定响应示例，供 GUI 原型使用。
- `agentdeck contract continue` 返回顶层 continue 恢复卡片契约发现元数据，不读取或修改项目 state；`--example` 会附带稳定恢复卡片示例，供 GUI 原型使用。
- `agentdeck contract workbench` 返回工作台快照契约发现元数据，不读取或修改项目 state；`--example` 会附带稳定一屏 workbench 示例，供 GUI/TUI 原型使用。
- `agentdeck contract leader-actions` 返回 Leader action queue 契约发现元数据，不读取或修改项目 state；`--example` 会附带稳定队列示例，供 GUI 原型使用。
- `agentdeck contract leader-action` 返回单个 Leader action 详情契约发现元数据，不读取或修改项目 state；`--example` 会附带稳定 action detail 示例，供 GUI 原型使用。
- `agentdeck contract approvals` 返回人类审批队列契约发现元数据，不读取或修改项目 state；`--example` 会附带稳定 approval queue 示例，供 GUI 原型使用。
- `agentdeck contract inbox` 返回单 agent mailbox 契约发现元数据，不读取或修改项目 state；`--example` 会附带稳定 inbox 示例，供 GUI 原型使用。
- `agentdeck contract trace` 返回通信 lineage 契约发现元数据，不读取或修改项目 state；`--example` 会附带稳定 trace 示例，供 GUI 原型使用。
- `agentdeck trace --id <id>` 输出前必须通过 `validate_trace_contract()` 自校验；失败时只能返回错误，不输出半坏 trace。
- `agentdeck leader chat` 输出 JSON 前必须通过 `validate_leader_chat_contract()` 自校验；校验失败时只能返回错误，不输出半坏 chat response，并必须写入 `leader_errors[]` 和 `leader_chat_contract_failed` 事件。
- `agentdeck leader actions` 输出 JSON 前必须通过 `validate_leader_actions_contract()` 自校验；校验失败时只能返回错误，不输出半坏 action queue。
- `agentdeck leader action --action-id <id>` 输出 JSON 前必须通过 `validate_leader_action_contract()` 自校验；校验失败时只能返回错误，不输出半坏 action detail。
- `agentdeck approval list` 输出 JSON 前必须通过 `validate_approval_contract()` 自校验；校验失败时只能返回错误，不输出半坏 approval queue。
- `agentdeck inbox --agent <id>` 输出 JSON 前必须通过 `validate_inbox_contract()` 自校验；校验失败时只能返回错误，不输出半坏 inbox queue。
- `status.recovery` 是当前恢复入口，包含 status/reason/next_command/recommended_action/pending/leader_action/latest_event/recent_events，用来判断下一步该继续什么；`recommended_action` 可直接驱动 GUI 的下一步按钮或检查入口，并通过 target_id 关联 action、approval 或 inbox item。
- 当没有 pending action、approval 或 inbox item 但存在 `leader_errors[]` 时，`status.recovery` 会返回 `status=leader_error`，并推荐 inspect 型 `agentdeck status`。
- `status.recovery.pending` 包含 `leader_errors` 计数，供 GUI 统一展示 Leader 错误数量。
- `agentdeck contract project-view` 通过 `recovery_pending_fields` 暴露 `recovery.pending` 必备字段；缺字段的 ProjectView 应被 validator 拒绝。
- `status.chat_turns.items` 包含 action_id/action_kind，可把自然语言 turn 关联回 leader_actions。
- `status.leader_actions` 包含 recommended_action_id，`items[]` 包含 can_apply/apply_command/explicit_command/apply_blocker/is_recommended，可直接驱动 GUI action 按钮、阻塞提示和当前推荐项高亮。
- `status.messages.items[]`、`status.jobs.items[]` 和 `status.replies.items[]` 包含 `trace_command`；ProjectView contract discovery 会公开对应 item field lists，缺少 trace 入口的 summary item 应被 validator 拒绝。
- `agentdeck continue` 是顶层只读恢复入口；它必须先通过 ProjectView contract 守门，再通过 `validate_continue_contract()` 自校验，最后返回 recovery-driven 下一步卡片；不得写 state、创建 action、apply action、dispatch 或发送 tmux 输入；`agentdeck contract continue` 会公开 `continue_card_fields`。
- `agentdeck workbench` 是 GUI/TUI 优先的一屏只读快照；它必须先通过 ProjectView contract 守门，再组合 project_view、leader_actions、leader_card、provider_health、runtime_card、role_card、ledger_card、queue_card、operator_card、audit_card、recovery、continue_card、active_queue_source、inbox_card、approval_card 和 leader_action，并通过 `validate_workbench_contract()` 自校验；`leader_card` 必须从 ProjectView leader 派生 agent_id/provider/model/approval_mode/api_backed 和 chat/continue/actions/status 入口命令，且不得暴露 API key 或调用 provider；`provider_health` 必须从 Leader provider 和本地环境变量派生 supported/ready/missing_env/detail/doctor_command，只能暴露 env 名称，不能暴露密钥值或调用 provider；`runtime_card` 必须从 ProjectView agents/runtime 派生并公开 agent role/provider/workspace/status/pane/session/cwd 与 spawn/stop/inbox 命令；`role_card` 必须从 ProjectView agents 派生 role/provider/workspace_mode/role_prompt/assign_command；`ledger_card` 必须从 ProjectView messages/jobs/replies/inbox 派生并保留 trace_commands；`queue_card` 必须从 leader_actions/approvals/inbox/recovery next_command 派生队列总览；`operator_card` 必须从 recovery/recommended_action 和当前 active queue 派生，公开 command/preview_command/safety/requires_explicit_user/apply_command/explicit_command/blocker 等人类操作字段，但不得自动执行；`audit_card` 必须从 recovery latest/recent event summary 派生并公开 events_command；不得写 state、创建 chat turn、ack、approve、dispatch、capture reply、读取 pane 输出或发送 tmux 输入；`agentdeck contract workbench` 会公开 `snapshot_fields`、`leader_card_fields`、`provider_health_fields`、`runtime_card_fields`、`runtime_agent_fields`、`role_card_fields`、`role_agent_fields`、`ledger_card_fields`、`queue_card_fields`、`operator_card_fields` 和 `audit_card_fields`。
- `status.recovery.status=inbox_pending` 时，`next_command` 和 `recommended_action.command` 指向具体 `agentdeck inbox --agent <id>`，供 GUI/continue 直接打开对应 mailbox。
- `status.inbox.heads` 按 agent 暴露最早 pending inbox item；GUI/Leader 应优先用它判断当前可处理或可 ack 的 mailbox head。
- 后续升级为更严格的 reply block 标记。

## Leader Planning

- `agentdeck leader chat --message <text>` 是自然语言 Leader 入口 MVP；它读取 ProjectView 前必须通过 `validate_project_view_contract()` 守门，无 plan 时创建 plan-only 记录、持久化一条 safe `create_approvals` Leader action，并在响应前重新读取 ProjectView，使同次响应包含刚创建的 plan、chat turn 和 action queue；有 plan 时 review 最新 plan，并持久化或复用一条 `leader_actions[]` 建议；chat 输出包含顶层 `leader_actions`，且它等于同次响应的 `project_view.leader_actions`；chat 输出还包含 `leader_explanation`，用于解释当前推荐动作、safety 和是否需要人类显式确认；plan/review 输出包含 `recovery`，且 `next_command` 来自 `recovery.next_command`。
- `agentdeck leader chat --message "继续"`、`"继续吧"` 或 `"/continue"` 走 recovery-first 的 `mode=continue`，复用 `agentdeck continue` 的下一步卡片；当 recovery 指向 pending inbox 时同时嵌入对应 agent 的 `inbox_card`，当 recovery 指向 approval queue 时同时嵌入 `approval_card`；只记录 chat turn，不创建新的 leader action、不 apply action、不 ack、不 approve、不 dispatch、不发送 tmux 输入；`agentdeck contract leader-chat` 会公开 `continue_card_fields`，example 会包含稳定 `continue_card`；嵌入的 `continue_card` 必须通过 `validate_continue_contract()` 校验，嵌入的队列卡片必须复用对应 queue validator。
- `agentdeck leader chat --message "查看 planner inbox"` 这类 inbox 意图走只读 `mode=inbox`，复用 `agentdeck inbox --agent <id>` 的 queue shape 返回 `inbox_card`；包含 `追踪`、`trace` 或 `lineage` 且存在 pending head 时，`next_command` 可推荐该 head 的 `agentdeck trace --id <inbox_id>`；包含 `确认`、`ack` 或 `acknowledge` 且 head 可 ack 时，`next_command` 可推荐该 head 的 `ack_command`，但 `leader_explanation.safety` 必须是 `explicit_runtime` 且 `requires_explicit_user=true`；该模式只记录 chat turn，不创建 plan/leader action，不执行 ack、不 dispatch、不 capture reply、不发送 tmux 输入；嵌入的 `inbox_card` 必须通过 `validate_inbox_contract()` 校验。
- `agentdeck leader chat --message "查看审批"` 这类 approval 意图走只读 `mode=approval`，复用 `agentdeck approval list` 的 queue shape 返回 `approval_card`；包含 `批准` 或 `approve` 且存在 pending approval 时，`next_command` 可推荐第一条 pending approval 的 `approve_command`；包含 `派发` 或 `dispatch` 且存在 approved approval 时，`next_command` 可推荐第一条 approved approval 的 `dispatch_command`；approve/dispatch 建议的 `leader_explanation.safety` 必须是 `explicit_runtime` 且 `requires_explicit_user=true`；该模式只记录 chat turn，不创建 plan/leader action，不执行 approve/reject/dispatch、不发送 tmux 输入；嵌入的 `approval_card` 必须通过 `validate_approval_contract()` 校验。
- `agentdeck leader chat --message "apply action <id>"` 会复用 safe apply-action 白名单；当前只允许应用 `create_approvals`，runtime action 必须继续显式命令执行；safe apply 完成后的 `next_command` 必须来自刷新后 `recovery.next_command`。
- `agentdeck leader chat-history` 返回已持久化的 chat turns 摘要，用于恢复自然语言调度上下文；review turn 会包含 action_id/action_kind。
- `agentdeck leader plan --task <text>` 会写入 `.agentdeck/state/state.json` 的 `plans[]`。
- `agentdeck leader review --plan-id <id>` 会先通过 ProjectView contract 守门，再基于 plan status 和 replies 输出下一步建议。
- `agentdeck leader next` 会先通过 ProjectView contract 守门，再把下一步建议写入 `leader_actions[]`，但不会执行命令；相同 pending action 已存在时会复用原 action_id。
- `agentdeck leader actions` 返回已持久化的 action queue 摘要，包含顶层 `recommended_action_id` 和每项 `is_recommended`。
- `agentdeck leader action --action-id <id>` 返回单个 action 的只读详情，包含 `can_apply`、`apply_command`、`explicit_command`、`apply_blocker`、当前 `recovery`、`recommended_action` 和 `matches_recommended_action`。
- `agentdeck leader apply-action --action-id <id>` 执行 safe apply 前必须通过 ProjectView contract 守门；当前只允许应用 `create_approvals`，dispatch/capture 类 action 必须继续由人类显式命令执行。
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
