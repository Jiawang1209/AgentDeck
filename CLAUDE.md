# CLAUDE.md

本文件帮助 Claude Code 或其他 coding agent 快速理解本项目。

## 项目定位

AgentDeck 是一个 local-first 多智能体终端工作台。目标是用任意可通过 API 调用的 LLM 做 Leader Agent，调度多个 Worker Agent，在 tmux 可见终端里执行任务，并通过消息账本、审批、状态存储和 ProjectView 保持可审计、可恢复。DeepSeek 可以作为首个默认 provider，但不是架构绑定点。

核心设计文档：

- `docs/architecture/multi-agent-terminal-design.md`
- `docs/roadmap/ultimate-goal-roadmap.md`
- `docs/reference-analysis/*.md`

## 当前技术栈

- Python 3.12
- Miniforge/conda 环境：`agentdeck`
- 标准库 CLI
- tmux runtime backend
- TOML 配置
- JSON/JSONL 状态骨架

## 环境准备

```bash
conda env create -f environment.yml
conda activate agentdeck
python -m pip install -e .
```

如果环境已存在：

```bash
conda activate agentdeck
python -m pip install -e .
```

## 常用命令

```bash
conda activate agentdeck
agentdeck doctor
agentdeck project init
agentdeck status
agentdeck agent list
agentdeck agent stop --agent planner
agentdeck agent assign-role --agent planner --role "architecture planning" --role-prompt "你负责架构规划和任务拆解。"
agentdeck leader chat --message "帮我设计自动 reply extraction"
agentdeck leader chat --message "查看 planner inbox"
agentdeck leader chat --message "追踪 planner 当前 inbox"
agentdeck leader chat --message "确认 planner 当前 inbox"
agentdeck leader chat --message "查看审批"
agentdeck leader chat --message "批准当前审批"
agentdeck leader chat --message "派发当前审批"
agentdeck leader chat-history
agentdeck leader plan --task "设计自动 reply extraction"
agentdeck leader review --plan-id pln_xxx
agentdeck leader next
agentdeck leader actions
agentdeck leader action --action-id act_xxx
agentdeck leader apply-action --action-id act_xxx
agentdeck plan list
agentdeck plan show --plan-id pln_xxx
agentdeck events --limit 20
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
pytest tests/test_agent_cli.py -q
pytest tests/test_dispatch_cli.py -q
python -m compileall src
```

所有开发、验证和 CLI 调试都应在 `agentdeck` 环境中执行。

安装为本地命令后：

```bash
conda activate agentdeck
agentdeck doctor
```

## 目录约定

```text
src/agentdeck/
  cli.py              # CLI dispatch
  config.py           # .agentdeck/config.toml
  models.py           # dataclasses for project/agent/message/job
  state.py            # JSON/JSONL state store
  runtime/            # runtime backend interfaces and tmux backend
  providers/          # LLM provider adapters
  orchestration/      # Leader/Worker planning skeleton
docs/
  architecture/
  reference-analysis/
```

Runtime state 默认写到 `.agentdeck/`，不要提交该目录。

## 开发规则

- 每次新增功能或用户可见行为变化都要 commit。
- 每次开发内容都要同步更新 `HISTORY.md`，并和对应代码/文档改动放在同一次 commit 中。
- 每次开发前先对照 `docs/roadmap/ultimate-goal-roadmap.md`，确认功能服务 Leader Agent、多 Agent 通信、可见 runtime、审批、恢复或 GUI 主线。
- GUI、自然语言入口和 Leader chat loop 应优先消费 `agentdeck status` 的 ProjectView 摘要；不要直接散读 state 文件作为主入口。
- ProjectView 字段契约维护在 `docs/contracts/project-view-schema.md`；当前 `schema_version` 是 `project-view/v1`，任何 GUI、recovery 或自然语言入口改动都要保持该文档同步。
- ProjectView schema version 的源码单一来源是 `src/agentdeck/models.py` 的 `PROJECT_VIEW_SCHEMA_VERSION`；不要在 Python 源码里重复手写版本字符串。
- ProjectView contract discovery payload 和 example fixture 的源码入口是 `src/agentdeck/contracts.py`；CLI 只负责调用它。
- Contract index 维护在 `docs/contracts/contract-index-schema.md`，发现入口是 `agentdeck contract list`；`CONTRACT_INDEX_SPECS`、payload helper 和测试都在 `src/agentdeck/contracts.py` / `tests/` 中。新增 GUI-consumable contract 时必须同步索引。
- Doctor diagnostics contract 维护在 `docs/contracts/doctor-schema.md`，发现入口是 `agentdeck contract doctor`；payload 和 example fixture 也在 `src/agentdeck/contracts.py`。
- Events timeline contract 维护在 `docs/contracts/events-schema.md`，发现入口是 `agentdeck contract events`；payload 和 example fixture 也在 `src/agentdeck/contracts.py`。
- Leader chat response contract 维护在 `docs/contracts/leader-chat-schema.md`，发现入口是 `agentdeck contract leader-chat`；payload 和 example fixture 也在 `src/agentdeck/contracts.py`。
- Workbench snapshot contract 维护在 `docs/contracts/workbench-schema.md`，发现入口是 `agentdeck contract workbench`；payload、example fixture 和 `validate_workbench_contract()` 也在 `src/agentdeck/contracts.py`。
- Agent runtime contract 维护在 `docs/contracts/agent-runtime-schema.md`，发现入口是 `agentdeck contract agent-runtime`；payload 和 example fixture 也在 `src/agentdeck/contracts.py`，并公开 capture/refresh 响应字段。
- `agentdeck agent refresh` 是显式 runtime reconciliation 命令；只检查 state 中记录为 `running` 的 pane 是否仍存在，丢失时标记为 `stale` 并写入 `agent_runtime_stale` 事件，不发送 tmux 输入、不推断任务完成。
- `status.recovery` 必须把 `stale` runtime bindings 作为 `runtime_stale` 恢复状态暴露，`recommended_action.source=runtime` 且 `next_command=agentdeck agent refresh`；`pending.runtime_stale` 是 ProjectView recovery pending 契约字段。
- `agentdeck leader chat --message "继续"` 在 recovery source 为 `runtime` 时必须嵌入 `runtime_card`，复用 workbench runtime card 字段规则；它只展示 `agentdeck agent refresh` 入口，不自动 refresh、spawn、stop、capture 或发送 tmux 输入。
- `agentdeck leader chat --message "查看 runtime"` / `"查看终端"` 必须进入只读 `mode=runtime`，嵌入同一张 `runtime_card` 并建议 `agentdeck agent list`；它不创建 plan/action/approval/message/job/inbox，也不执行任何 runtime 操作。
- `agentdeck leader chat --message "查看队列"` / `"查看控制面"` 必须进入只读 `mode=queue`，嵌入 workbench 同源 `queue_card` / `operator_card` 并展示 next/apply/explicit controls；它不创建或应用 action、不审批、不派发、不 ack、不 refresh runtime、不发送 tmux 输入。
- `agentdeck leader chat --message "查看角色"` / `"查看分工"` 必须进入只读 `mode=role`，嵌入 workbench 同源 `role_card` 并展示 assign-role 命令；它不修改 `.agentdeck/config.toml`、不创建 plan/action/approval/message/job/inbox、不发送 tmux 输入。
- `agentdeck leader chat --message "查看账本"` / `"查看通信"` 必须进入只读 `mode=ledger`，嵌入 workbench 同源 `ledger_card` 并展示 trace_commands；它不创建 plan/action/approval/message/job/inbox、不 ack、不 dispatch、不 capture reply、不读取 pane 输出、不发送 tmux 输入。
- `agentdeck leader chat --message "打开工作台"` / `"查看总览"` 必须进入只读 `mode=workbench`，嵌入完整 `workbench_card` 并通过 `validate_workbench_contract()` 校验；它不创建 plan/action/approval/message/job/inbox、不 ack、不 approve、不 dispatch、不 refresh runtime、不 capture、不读取 pane 输出、不发送 tmux 输入。
- `agentdeck leader chat --message "帮助"` / `"help"` / `"你能做什么"` / `"命令面板"` 必须进入只读 `mode=help`，嵌入 `capability_card` 作为自然语言壳和未来 GUI 命令面板的能力发现入口；它必须暴露 plan/review/apply_action 这条 Leader 调度主线以及只读控制面能力，每个 capability item 必须带 GUI-ready `controls[]`；help mode 本身不调用 provider、不创建 plan/action/approval/message/job/inbox、不读取 pane、不发送 tmux 输入。
- 所有 `agentdeck leader chat` 响应都必须包含 `intent_card`，用于 GUI/自然语言壳解释 mode、matched_intent、route_source、embedded_card、read_only、next_command、requires_explicit_user 和 `controls[]`；intent controls 必须使用 `kind`、`label`、`command`、`safety`、`enabled`、`blocker`，其中 `kind=inspect` 必须是 `safety=inspect`，disabled control 必须提供 blocker；新增 chat mode 时必须同步 `LEADER_CHAT_INTENT_CARD_FIELDS`、`LEADER_CHAT_INTENT_CONTROL_FIELDS`、`LEADER_CHAT_CAPABILITY_CARD_FIELDS`、`LEADER_CHAT_CAPABILITY_ITEM_FIELDS`、`capability_control_fields` discovery、validator、contract docs、README、HISTORY 和测试。
- Leader actions queue contract 维护在 `docs/contracts/leader-actions-schema.md`，发现入口是 `agentdeck contract leader-actions`；payload、example fixture 和 `validate_leader_actions_contract()` 也在 `src/agentdeck/contracts.py`。
- Leader action detail contract 维护在 `docs/contracts/leader-action-schema.md`，发现入口是 `agentdeck contract leader-action`；payload、example fixture 和 `validate_leader_action_contract()` 也在 `src/agentdeck/contracts.py`。
- Approval queue contract 维护在 `docs/contracts/approvals-schema.md`，发现入口是 `agentdeck contract approvals`；payload、example fixture 和 `validate_approval_contract()` 也在 `src/agentdeck/contracts.py`。
- Inbox queue contract 维护在 `docs/contracts/inbox-schema.md`，发现入口是 `agentdeck contract inbox`；payload、example fixture 和 `validate_inbox_contract()` 也在 `src/agentdeck/contracts.py`。
- Trace contract 维护在 `docs/contracts/trace-schema.md`，发现入口是 `agentdeck contract trace`；payload、example fixture 和 `validate_trace_contract()` 也在 `src/agentdeck/contracts.py`。
- `src/agentdeck/contracts.py::validate_project_view_contract()` 是 ProjectView-like payload 的 v1 基础契约校验入口。
- `agentdeck status` 必须在输出 JSON 前调用 `validate_project_view_contract()` 自校验；校验失败时返回非 0 且不得输出半坏的 ProjectView。
- `agentdeck contract list` 是只读契约总目录，供 GUI/TUI 启动时读取所有 contract discovery 命令、example 命令和文档路径；不得读取或修改 live state。
- `agentdeck contract project-view` 是只读契约发现入口，供 GUI 或外部集成读取 schema version、契约文档路径和关键字段列表；`--example` 会返回稳定 ProjectView 示例，不代表 live state。
- `agentdeck contract events` 是只读契约发现入口，供 GUI 或外部集成读取事件时间线 response/cursor/event item 字段；`--example` 会返回稳定 events timeline 示例，不读取或修改 live state。
- `agentdeck contract leader-chat` 是只读契约发现入口，供 GUI 或外部集成读取 `leader chat` 响应字段和 `leader_explanation` 字段；`--example` 会返回稳定 chat 响应示例，不读取或修改 live state。
- `agentdeck contract continue` 是只读契约发现入口，供 GUI 或外部集成读取 `agentdeck continue` 的恢复卡片字段；`--example` 会返回稳定 continue card 示例，不读取或修改 live state。
- `agentdeck contract workbench` 是只读契约发现入口，供 GUI 或外部集成读取 `agentdeck workbench` 的一屏快照字段；`--example` 会返回稳定 workbench 示例，不读取或修改 live state。
- `agentdeck contract leader-actions` 是只读契约发现入口，供 GUI 或外部集成读取 Leader action queue 字段；`--example` 会返回稳定队列示例，不读取或修改 live state。
- `agentdeck contract leader-action` 是只读契约发现入口，供 GUI 或外部集成读取单个 Leader action 详情字段；`--example` 会返回稳定 action detail 示例，不读取或修改 live state。
- `agentdeck contract approvals` 是只读契约发现入口，供 GUI 或外部集成读取人类审批队列字段；`--example` 会返回稳定 approval queue 示例，不读取或修改 live state。
- `agentdeck contract inbox` 是只读契约发现入口，供 GUI 或外部集成读取单 agent mailbox 字段；`--example` 会返回稳定 inbox 示例，不读取或修改 live state。
- `agentdeck contract trace` 是只读契约发现入口，供 GUI 或外部集成读取通信 lineage 的 message/attempt/job/reply/inbox 字段；`--example` 会返回稳定 trace 示例，不读取或修改 live state。
- `agentdeck trace --id <id>` 输出 JSON 前必须通过 `validate_trace_contract()` 自校验；失败时返回非 0 且不得输出半坏 trace。
- `agentdeck leader chat` 输出 JSON 前必须通过 `validate_leader_chat_contract()` 自校验；校验失败时返回非 0、不得输出半坏 chat response，并必须写入 `leader_errors[]` 和 `leader_chat_contract_failed` 事件。
- `agentdeck leader actions` 输出 JSON 前必须通过 `validate_leader_actions_contract()` 自校验；校验失败时返回非 0 且不得输出半坏 action queue；每个 action item 必须公开 `controls[]`、`preview_command`、can_apply、apply_command、explicit_command、apply_blocker 和 is_recommended。
- `agentdeck leader action --action-id <id>` 输出 JSON 前必须通过 `validate_leader_action_contract()` 自校验；校验失败时返回非 0 且不得输出半坏 action detail；detail 必须公开 `preview_command`、can_apply、apply_command、explicit_command 和 apply_blocker。
- `agentdeck approval list` 输出 JSON 前必须通过 `validate_approval_contract()` 自校验；校验失败时返回非 0 且不得输出半坏 approval queue；每个 approval item 必须公开 `controls[]`、`preview_command`、approve/reject/dispatch commands、can_dispatch 和 dispatch_blocker。
- `agentdeck inbox --agent <id>` 输出 JSON 前必须通过 `validate_inbox_contract()` 自校验；校验失败时返回非 0 且不得输出半坏 inbox queue；每个 inbox item 必须公开 `controls[]`、`preview_command`、trace_command、ack_command、is_head、can_ack 和 ack_blocker。
- `agentdeck events --limit <n>` 是只读事件时间线入口，用于审计和 GUI 最近事件列表；`agentdeck events --since <event_id>` 返回 cursor 之后的审计事件和 cursor metadata，cursor 由 GUI/调用方持有，不得写入 AgentDeck state。
- `agentdeck status` 的 `recovery` 是默认恢复入口，必须保持只读，并暴露 status/reason/next_command/recommended_action/pending/leader_action/latest_event/recent_events；`recommended_action` 必须说明 label/command/safety/requires_explicit_user/source/target_id。
- 当没有 pending action、approval 或 inbox item 但存在 `leader_errors[]` 时，`agentdeck status.recovery` 必须返回 `status=leader_error` 和 inspect 型 recommended_action。
- `agentdeck status.recovery.pending` 必须包含 `leader_errors` 计数，供 GUI 统一展示 Leader 错误数量。
- `agentdeck contract project-view` 必须通过 `recovery_pending_fields` 暴露 `recovery.pending` 必备字段；validator 必须拒绝缺失字段的 ProjectView。
- `agentdeck status` 的 `chat_turns.items` 必须保留 action_id/action_kind，供 GUI 从自然语言 turn 跳转到 action。
- `agentdeck status` 的 `leader_actions` 必须保留 recommended_action_id，`items[]` 必须保留 controls[]/preview_command/can_apply/apply_command/explicit_command/apply_blocker/is_recommended，供 GUI 和对话层展示安全动作预览、执行按钮与当前推荐项。
- `agentdeck status` 的 `messages.items[]`、`jobs.items[]` 和 `replies.items[]` 必须保留 `trace_command`；`agentdeck contract project-view` 必须公开对应 item field lists，validator 必须拒绝缺失 trace 入口的 summary item。
- `agentdeck continue` 是顶层只读恢复入口；它必须先通过 ProjectView contract 守门，再通过 `validate_continue_contract()` 自校验，最后返回 recovery-driven 下一步卡片；不得写 state、创建 action、apply action、dispatch 或发送 tmux 输入；`agentdeck contract continue` 必须公开 `continue_card_fields`。
- `agentdeck workbench` 是 GUI/TUI 优先的一屏只读快照；它必须先通过 ProjectView contract 守门，再组合 project_view、leader_actions、leader_card、provider_health、runtime_card、role_card、ledger_card、queue_card、operator_card、audit_card、contracts_card、change_summary、recovery、continue_card、active_queue_source、inbox_card、approval_card 和 leader_action，并通过 `validate_workbench_contract()` 自校验；`leader_card` 必须从 ProjectView leader 派生 agent_id/provider/model/approval_mode/api_backed 和 chat/continue/actions/status 入口命令，且不得暴露 API key 或调用 provider；`provider_health` 必须从 Leader provider 和本地环境变量派生 supported/ready/missing_env/detail/doctor_command/doctor_contract，只能暴露 env 名称，不能暴露密钥值或调用 provider；`runtime_card` 必须从 ProjectView agents/runtime 派生并公开 agent role/provider/workspace/status/pane/session/cwd、spawn/stop/capture/send template/inbox 命令和 controls[]，其中 capture 只读，send/spawn/stop 类 runtime control 只能由人类显式触发；`role_card` 必须从 ProjectView agents 派生 role/provider/workspace_mode/role_prompt/assign_command；`ledger_card` 必须从 ProjectView messages/jobs/replies/inbox 派生并保留 trace_commands；`queue_card` 必须从 leader_actions/approvals/inbox/recovery next_command 派生队列总览；`operator_card` 必须从 recovery/recommended_action 和当前 active queue 派生，公开 controls[]/command/preview_command/safety/requires_explicit_user/apply_command/explicit_command/blocker 等人类操作字段，但不得自动执行；`audit_card` 必须从 recovery latest/recent event summary 派生并公开 events_command；`contracts_card` 必须公开 `agentdeck contract list`、contract index schema、workbench/project-view/events/doctor contract 入口，且不得读取 state 或执行任何 contract 命令；`change_summary` 必须按可选 `--since-event <event_id>` 从审计事件账本临时计算，不得保存 cursor 到 state；不得写 state、创建 chat turn、ack、approve、dispatch、capture reply、读取 pane 输出或发送 tmux 输入；`agentdeck workbench --watch --since-event <event_id> --interval <seconds>` 必须输出同一契约形状的 JSONL 状态流，每行都通过 `validate_workbench_contract()`，并可用 `--iterations <n>` 有界退出；`agentdeck contract workbench` 必须公开 `snapshot_fields`、`leader_card_fields`、`provider_health_fields`、`runtime_card_fields`、`runtime_agent_fields`、`runtime_control_fields`、`role_card_fields`、`role_agent_fields`、`ledger_card_fields`、`queue_card_fields`、`operator_card_fields`、`audit_card_fields`、`contracts_card_fields` 和 `change_summary_fields`。
- `agentdeck status.recovery.status=inbox_pending` 时，`next_command` 和 `recommended_action.command` 必须指向具体 `agentdeck inbox --agent <id>`，供 GUI/continue 直接打开对应 mailbox。
- `agentdeck status.recovery.status=provider_setup_required` 时，`next_command` 和 `recommended_action.command` 必须指向 `agentdeck doctor`，`recommended_action.source` 必须是 `provider_health`；该状态只用于 setup/diagnostics，不得创建 plan、chat turn、approval、message、job、inbox 或发送 tmux 输入。
- `agentdeck doctor` 必须返回当前配置 Leader 的 `configured_leader` readiness 摘要，并让顶层 `ok` 受配置 provider readiness 影响；`configured_leader.setup_commands` 只能包含 placeholder export 命令，只能暴露缺失 env 名称，不能暴露密钥值。
- `agentdeck status` 的 `inbox.heads` 是 mailbox head-only 语义的只读入口；显示或 ack inbox 前优先读取它。
- `agentdeck leader chat --message <text>` 是当前自然语言入口 MVP：它读取 ProjectView 前必须通过 `validate_project_view_contract()` 守门；无 plan 时创建 plan-only 记录、持久化一条 safe `create_approvals` Leader action，并在响应前重新读取 ProjectView，使同次响应包含刚创建的 plan、chat turn 和 action queue；有 plan 时 review 最新 plan，并持久化或复用一条 `leader_actions[]` 建议；chat 输出必须包含顶层 `leader_actions`，且它必须等于同次响应的 `project_view.leader_actions`；chat 输出还必须包含 `leader_explanation`，说明 mode、summary、reason、next_command、recommended_action_id、action_kind、action_status、safety 和 requires_explicit_user；plan/review 输出必须包含 `recovery`，且 `next_command` 必须来自 `status.recovery.next_command`；每轮会写入 `chat_turns[]`，可用 `agentdeck leader chat-history` 查看。
- `agentdeck leader chat --message "继续"`、`"继续吧"` 或 `"/continue"` 必须走 recovery-first 的 `mode=continue`，复用 `agentdeck continue` 的下一步卡片；当 recovery 指向 pending inbox 时必须同时嵌入对应 agent 的 `inbox_card`，当 recovery 指向 approval queue 时必须同时嵌入 `approval_card`；只记录 chat turn，不得创建新的 leader action、apply action、ack、approve、dispatch 或发送 tmux 输入；`agentdeck contract leader-chat` 必须公开 `continue_card_fields`，example 必须包含稳定 `continue_card`；嵌入的 `continue_card` 必须通过 `validate_continue_contract()` 校验，嵌入的队列卡片必须复用对应 queue validator。
- `agentdeck leader chat --message "检查 Leader provider 配置"`、`"doctor"` 或类似 setup/diagnostics 意图必须走只读 `mode=setup`，返回 `provider_health`、`recovery`、`next_command=agentdeck doctor` 和 `leader_explanation`；只记录 chat turn，不得调用 provider，不得创建 plan/leader action/approval/message/job/inbox，不得发送 tmux 输入；`provider_health` 必须复用 workbench provider health 字段，并公开 `doctor_contract` 供 GUI 发现 doctor schema；`setup_commands` 只能包含 placeholder export 命令，且不得暴露密钥值。
- `agentdeck leader chat --message "查看 planner inbox"` 这类 inbox 意图必须走只读 `mode=inbox`，复用 `agentdeck inbox --agent <id>` 的 queue shape 返回 `inbox_card`；包含 `追踪`、`trace` 或 `lineage` 且存在 pending head 时，`next_command` 可推荐该 head 的 `agentdeck trace --id <inbox_id>`；包含 `确认`、`ack` 或 `acknowledge` 且 head 可 ack 时，`next_command` 可推荐该 head 的 `ack_command`，但 `leader_explanation.safety` 必须是 `explicit_runtime` 且 `requires_explicit_user=true`；该模式只记录 chat turn，不得创建 plan/leader action，不得执行 ack、dispatch、capture reply 或发送 tmux 输入；嵌入的 `inbox_card` 必须通过 `validate_inbox_contract()` 校验。
- `agentdeck leader chat --message "查看审批"` 这类 approval 意图必须走只读 `mode=approval`，复用 `agentdeck approval list` 的 queue shape 返回 `approval_card`；包含 `批准` 或 `approve` 且存在 pending approval 时，`next_command` 可推荐第一条 pending approval 的 `approve_command`；包含 `派发` 或 `dispatch` 且存在 approved approval 时，`next_command` 可推荐第一条 approved approval 的 `dispatch_command`；approve/dispatch 建议的 `leader_explanation.safety` 必须是 `explicit_runtime` 且 `requires_explicit_user=true`；该模式只记录 chat turn，不得创建 plan/leader action，不得执行 approve/reject/dispatch 或发送 tmux 输入；嵌入的 `approval_card` 必须通过 `validate_approval_contract()` 校验。
- `agentdeck leader chat --message "apply action <id>"` 只能复用 safe apply-action 白名单；不得通过 chat 自动应用 dispatch/capture 类 action；safe apply 完成后的 `next_command` 必须来自刷新后 `recovery.next_command`，例如进入 `agentdeck approval list`。
- `agentdeck leader plan` 和 `agentdeck leader chat` 默认读取 `.agentdeck/config.toml` 的 `[leader] provider/model`；需要本地 dry-run 时必须显式使用 `--provider fake --model fake-plan`。
- 真实 Leader API 可以使用 `agentdeck leader plan/chat --provider deepseek`，环境变量为 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL` 和 `DEEPSEEK_MODEL`；也可以使用 `--provider openai-compatible`，环境变量为 `AGENTDECK_LEADER_API_KEY`、`AGENTDECK_LEADER_BASE_URL` 和 `AGENTDECK_LEADER_MODEL`；真实 provider 也只能生成 plan，不得绕过审批。
- 真实 provider 失败必须记录到 `leader_errors[]` 和 `leader_provider_failed` 事件；不要让异常崩溃 CLI，也不要半写入 plan。
- 自然语言任务调度优先从 `agentdeck leader plan --task <text>` 生成 plan-only 记录开始；不要跳过 plan 直接自动 dispatch。
- `agentdeck leader review --plan-id <id>` 是当前本地 Leader review loop，进入 review 前必须通过 ProjectView contract 守门，用于基于 approval/dispatch/reply 状态建议下一步。
- `agentdeck leader next` 进入 action queue 前必须通过 ProjectView contract 守门；它只把下一步建议写入 `leader_actions[]`，不得直接执行 action；相同 pending action 已存在时会复用原 action_id；执行仍需人类显式运行对应 approval/dispatch/capture 命令；`agentdeck leader actions` 必须暴露 `recommended_action_id`、每项 `is_recommended`、`preview_command` 和 `controls[]`。
- `agentdeck leader action --action-id <id>` 是 action 执行前的只读详情入口；输出必须包含当前 `recovery`、`recommended_action` 和 `matches_recommended_action`，apply 前必须检查 `preview_command`、`can_apply`、`explicit_command` 和 `apply_blocker`。
- `agentdeck leader apply-action --action-id <id>` 执行 safe apply 前必须通过 ProjectView contract 守门；当前只允许应用 `create_approvals`，不得自动应用 dispatch/capture 类 action。
- 审批、dispatch 或恢复任务前优先用 `agentdeck plan list`、`agentdeck plan show --plan-id <id>` 和 `agentdeck plan status --plan-id <id>` 检查计划。
- 使用 `agentdeck approval create-from-plan --plan-id <id>` 创建审批项，使用 `approval approve/reject` 更新状态；只有 approved approval 才能通过 `agentdeck approval dispatch --approval-id <id>` 派发。
- `dispatch`、`approval dispatch`、`reply` 和 `capture-reply` 的成功 JSON 输出必须包含 `trace_command`，指向对应 message/reply lineage，供 GUI 和人类直接追踪。
- Worker 输出结构化结果后，优先使用 `agentdeck capture-reply --agent <id> --message-id <id>` 从 pane 回收入账；手动 `reply` 作为兜底。
- 使用 `agentdeck ack --agent <id> --inbox-id <id>` 时只能确认该 agent 最早的 pending inbox item；非 head item 必须等待前序 item ack 后再处理。
- 先更新架构/README/agent 文档，再扩展行为。
- 所有开发命令默认先激活 `agentdeck` conda 环境。
- `References/` 只读学习，不纳入 git，不直接复制大段源码。
- 不要重写终端模拟器；MVP 复用 tmux。
- 不要绕过审批执行危险操作。
- 不要把 provider、runtime、state、orchestration 逻辑混在一个文件里。
- Worker 写过文件后，Leader 汇总前必须重新读取相关文件。

## 优先级

1. 保持本地可运行。
2. 保持状态可追踪。
3. 保持人类可审批。
4. 保持 runtime 可见。
5. 再考虑 GUI、remote、MCP、skills 自动化。
