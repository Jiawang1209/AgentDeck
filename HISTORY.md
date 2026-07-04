# AgentDeck Development History

本文件记录 AgentDeck 每一次开发内容。约束：每次新增功能、文档规则、项目骨架、运行环境或用户可见行为变化，都必须同步更新本文件，并在同一次 commit 中提交。

## 2026-07-04

### Current - Enforce head-only inbox ack

- 将 `agentdeck ack --agent <id> --inbox-id <id>` 收紧为 head-only mailbox 语义：只能确认该 agent 最早的 `pending` inbox item。
- 非 head item 会明确失败，返回 `inbox item is not head: <id>; head is <head_id>`，并保持 inbox 状态不变。
- 已 ack 的历史 item 会保留在 inbox 账本中；后续 ack 会自动寻找下一条 pending item 作为 head。
- 扩展 `tests/test_dispatch_cli.py`，覆盖非 head ack 被拒绝、head ack 后下一条 item 才能继续 ack。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 head-only ack 规则，并移除“后续补 head-only ack”的旧表述。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_dispatch_cli.py::test_ack_rejects_non_head_pending_inbox_item -q` 看到第二条 inbox item 被错误 ack；实现后同一测试与原有 ack 测试均通过。
- 完整验证：`conda run -n agentdeck pytest -q` 51 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认越序 ack 返回 1 且提示 head id，按顺序 ack 后两条 inbox item 均为 `acked`。

### Current - Add Leader action detail view

- 新增 `agentdeck leader action --action-id <id>`，用于查看单个 persisted Leader action 的完整只读详情。
- action detail 现在返回 `can_apply`、`apply_command`、`explicit_command` 和 `apply_blocker`，让 CLI、自然语言 Leader 与未来 GUI 能明确展示下一步为什么能或不能安全应用。
- `create_approvals` pending action 会暴露安全 apply 命令；`dispatch_approved`、`wait_for_reply` 等 runtime action 会标记为不可 apply，并保留人类必须显式执行的命令。
- 未知 action id 会明确返回 `unknown leader action: <id>`。
- 扩展 `tests/test_leader_cli.py`，覆盖可 apply 的 create_approvals 详情、不可 apply 的 dispatch 详情，以及未知 action id。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 `leader action` 查看入口和 apply 前检查规则。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_action_show_outputs_full_action_with_applyability tests/test_leader_cli.py::test_leader_action_show_marks_dispatch_action_as_not_applyable tests/test_leader_cli.py::test_leader_action_show_rejects_unknown_action_id -q` 看到 `leader action` 子命令不存在；实现后同一测试 3 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 50 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `leader action` 返回 `create_approvals`、`can_apply=True`、`apply_command`，且 approvals/messages/jobs 仍为 0。

### Current - Add safe Leader action apply

- 新增 `agentdeck leader apply-action --action-id <id>`，作为 action queue 的显式确认入口。
- 当前只允许应用 `create_approvals` action：创建 approvals，并把 action 标记为 `applied`、写入 `applied_at`。
- `dispatch_approved` 等需要发送 tmux 输入或影响 runtime 的 action 会被拒绝，必须继续由人类显式运行 `agentdeck approval dispatch ...`。
- 重复 apply 已应用 action 会明确失败，不重复创建 approvals。
- 新增 `leader_action_applied` 事件，记录 action_id、kind、plan_id 和 result_count。
- 扩展 `tests/test_leader_cli.py`，覆盖 apply create_approvals、重复 apply 失败、dispatch action 拒绝和未派发安全边界。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 safe apply-action 用法与限制。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_apply_action_creates_approvals_and_marks_action_applied tests/test_leader_cli.py::test_leader_apply_action_rejects_already_applied_action tests/test_leader_cli.py::test_leader_apply_action_refuses_dispatch_action -q` 看到 `apply-action` 子命令不存在；实现后同一测试通过。
- 完整验证：`conda run -n agentdeck pytest -q` 47 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 apply-action 创建 3 个 approvals、action 标记 applied，messages/jobs 仍为 0。

### Current - Add Leader next action queue

- 新增 `agentdeck leader next [--plan-id <id>]`，基于最新 plan 或指定 plan 计算下一步，并持久化为 `leader_actions[]`。
- 新增 `agentdeck leader actions`，列出已持久化的 action queue。
- `leader next` 支持无 approval 时建议 `create_approvals`，以及 approved step 时建议 `dispatch_approved`。
- 每个 action 记录 action_id、kind、status、requires_confirmation、plan_id、approval_id、agent_id、message_id、command、reason 和 created_at。
- 扩展 ProjectView，`agentdeck status` 现在包含 `leader_actions` 的 count、by_kind、by_status 和 items 摘要。
- 保持人类审批边界：`leader next` 只记录 pending action，不执行 approval create、dispatch、capture 或其他命令。
- 扩展 `tests/test_leader_cli.py` 和 `tests/test_agent_cli.py`，覆盖 action 生成、actions 列表、未派发安全边界和 status 摘要。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 Leader next action queue 用法与边界。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_next_records_create_approvals_action_without_executing tests/test_leader_cli.py::test_leader_next_records_dispatch_action_without_dispatching tests/test_leader_cli.py::test_leader_actions_lists_persisted_actions -q` 看到 `leader next` 子命令不存在；实现后同一测试通过。
- 完整验证：`conda run -n agentdeck pytest -q` 44 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `leader next` 生成 `create_approvals` action，`leader actions` 和 `status.leader_actions` 均可见，approvals/messages/jobs 仍为 0。

### Current - Add Leader provider failure diagnostics

- 新增 `leader_errors[]` 状态账本，用于记录 Leader provider 失败的 error_id、mode、provider、model、task、error 和 created_at。
- 扩展 `agentdeck leader plan/chat`，捕获 provider `RuntimeError`，明确输出 `leader provider failed: ...`，并追加 `leader_provider_failed` 事件。
- 扩展 ProjectView，`agentdeck status` 现在包含 `leader_errors` 的 count、by_mode 和 items 摘要。
- 扩展 OpenAI-compatible provider 诊断，把模型返回的非法 JSON plan 转成稳定错误 `provider plan content is not valid JSON`。
- Provider 失败保持安全边界：不创建 plan、approval、message、job 或 inbox。
- 扩展 `tests/test_provider_openai_compatible.py`、`tests/test_leader_cli.py` 和 `tests/test_agent_cli.py`，覆盖非法 JSON 诊断、CLI 失败恢复和 status 摘要。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 provider failure diagnostics 与 `leader_errors[]`。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_provider_openai_compatible.py::test_openai_compatible_provider_reports_invalid_json_plan tests/test_leader_cli.py::test_leader_plan_records_provider_error_without_dispatching tests/test_agent_cli.py::test_status_includes_project_state_summaries -q` 看到 JSONDecodeError 泄漏、CLI 崩溃和 `leader_errors` 缺失；实现后同一测试通过。
- 完整验证：`conda run -n agentdeck pytest -q` 41 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认缺少 API key 时 openai-compatible plan 退出 1、`leader_errors.count` 为 1，plans/messages/jobs 均为 0。

### Current - Add OpenAI-compatible Leader provider

- 新增 `OpenAICompatibleProvider`，通过标准库 `urllib` 调用 OpenAI-compatible `/chat/completions`，不引入第三方依赖。
- 新增 provider 环境变量：`AGENTDECK_LEADER_API_KEY`、`AGENTDECK_LEADER_BASE_URL`、`AGENTDECK_LEADER_MODEL`。
- 扩展 `leader_provider("openai-compatible")`，`agentdeck leader plan/chat --provider openai-compatible` 可使用真实 API-backed Leader 生成 plan。
- Provider 要求模型返回 JSON object plan，并校验 steps 存在且每个 step 必须 `requires_approval: true`。
- 扩展 `agentdeck doctor`，输出 `openai_compatible` provider 配置状态。
- 保持安全边界：真实 provider 仍然只写入 plan/chat_turn，不创建 approval、不 dispatch、不发送 tmux 输入。
- 新增 `tests/test_provider_openai_compatible.py`，覆盖缺 API key、请求构造、响应解析和 plan schema；扩展 CLI 测试覆盖 openai-compatible plan 不派发。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 OpenAI-compatible provider 用法和审批边界。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_provider_openai_compatible.py tests/test_leader_cli.py::test_leader_plan_uses_openai_compatible_provider_without_dispatching -q` 看到 `OpenAICompatibleProvider` 不存在；实现后同一测试通过。
- 完整验证：`conda run -n agentdeck pytest -q` 39 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `agentdeck doctor` 输出 `openai_compatible` 状态且 `agentdeck leader --help` 仍包含 chat/plan。

### Current - Persist Leader chat turns

- 新增 `chat_turns[]` 状态账本，`agentdeck leader chat --message <text>` 每轮都会记录 turn_id、mode、message、plan_id、next_command、provider/model、review 和 created_at。
- 新增 `agentdeck leader chat-history`，返回已持久化 chat turns 的轻量摘要，方便恢复自然语言调度上下文。
- 扩展 ProjectView，`agentdeck status` 现在包含 chat_turns 的 count、by_mode 和 items 摘要。
- 扩展 `StateStore.record_chat_turn()`、`StateStore.list_chat_turns()` 与 ProjectView 聚合，保持 chat history 只读查看，不触发 dispatch。
- 扩展 `tests/test_leader_cli.py` 和 `tests/test_agent_cli.py`，覆盖 chat turn 持久化、chat-history 列表和 status 摘要。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 chat history 和 ProjectView 契约。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_creates_plan_from_natural_language_without_dispatching tests/test_leader_cli.py::test_leader_chat_reviews_latest_plan_instead_of_creating_another_plan tests/test_leader_cli.py::test_leader_chat_history_lists_persisted_turns tests/test_agent_cli.py::test_status_includes_project_state_summaries -q` 看到 turn_id、chat-history 和 chat_turns 摘要缺失；实现后同一测试通过。
- 完整验证：`conda run -n agentdeck pytest -q` 35 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `leader chat-history` 返回 2 个 turns 且 `agentdeck status` 的 `chat_turns.count` 为 2。

### Current - Add Leader chat loop MVP

- 新增 `agentdeck leader chat --message <text>`，作为自然语言 Leader 入口 MVP。
- chat loop 会读取 ProjectView：无 plan 时创建 plan-only 记录；已有 plan 时 review 最新 plan 并返回下一条建议命令。
- chat loop 保持人类审批边界，不创建 approval、不 dispatch、不发送 tmux 输入、不写入 messages/jobs/inbox。
- 输出包含 mode、message、project_view、plan_id、review 和 next_command，方便未来 GUI 或对话入口消费。
- 新增 `leader_chat_turn` 事件，记录 chat turn 的 mode、plan_id、provider/model 和 message_length。
- 扩展 `tests/test_leader_cli.py`，覆盖自然语言创建 plan 和继续 review 最新 plan 两条路径。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 Leader chat loop 用法与边界。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_creates_plan_from_natural_language_without_dispatching tests/test_leader_cli.py::test_leader_chat_reviews_latest_plan_instead_of_creating_another_plan -q` 看到 `leader chat` 子命令不存在；实现后同一测试通过。
- 完整验证：`conda run -n agentdeck pytest -q` 34 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认第一轮 chat 输出 `plan`、审批后第二轮 chat 输出 `review` 和显式 `approval dispatch` 建议。

### Current - Expand ProjectView status summaries

- 扩展 `agentdeck status` 的 ProjectView 输出，新增 plans、approvals、messages、jobs、replies 和 inbox 摘要。
- ProjectView 现在包含 count、by_status、by_agent 和轻量 items，可作为未来 GUI 与自然语言 Leader chat loop 的统一只读状态入口。
- 扩展 `StateStore.project_view()`，只聚合 state，不修改 state、不发送 tmux 输入、不读取 pane 输出。
- 扩展 `tests/test_agent_cli.py`，覆盖 status 会返回 plan、approval、message、job、reply 和 inbox 摘要。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 ProjectView/Status 契约。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_status_includes_project_state_summaries -q` 看到 `plans` 字段缺失；实现后同一测试通过。
- 完整验证：`conda run -n agentdeck pytest -q` 32 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `agentdeck status` 输出 plans、approvals、messages、jobs、replies 和 inbox 摘要。

### Current - Add Leader review loop MVP

- 新增 `agentdeck leader review --plan-id <id>`，基于 plan status、approval 状态和 replies 输出下一步建议。
- 扩展 `StateStore.leader_review()`，当前使用本地 deterministic 规则，不调用外部 LLM。
- review 支持 `dispatch_approved`、`wait_for_reply`、`summarize` 和 `wait_for_approval` 四种 next_action。
- `dispatch_approved` 返回待派发 approval_id 和 agent_id；`wait_for_reply` 返回待回收 message_id；`summarize` 返回已完成 replies 摘要。
- 扩展 `tests/test_leader_cli.py`，覆盖 approved 待派发、dispatched 待回复、已回复可总结和 unknown plan 错误。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 Leader review loop 用法。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_review_recommends_next_dispatch_when_pending_approved_step_exists tests/test_leader_cli.py::test_leader_review_recommends_waiting_for_dispatched_reply tests/test_leader_cli.py::test_leader_review_summarizes_when_all_dispatched_steps_have_replies tests/test_leader_cli.py::test_leader_review_rejects_unknown_plan_id -q` 看到 `leader review` 子命令不存在；实现后同一测试通过。
- 完整验证：`conda run -n agentdeck pytest -q` 31 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，`agentdeck leader --help` 显示 review 子命令，临时 git 项目 smoke 确认无审批时返回 `wait_for_approval`。

### Current - Add reply capture MVP

- 新增 `agentdeck capture-reply --agent <id> --message-id <id>`，从已绑定 agent pane 捕获最近输出并提取最后一个 `status:` 开头的结构化回复块。
- capture-reply 复用 `StateStore.record_reply()`，因此会更新 message、attempt、job 状态，并在 agent-to-agent 场景回流 sender inbox。
- 新增 `reply_captured` 事件，记录 reply_id、message_id、from_agent、pane_id 和 captured_lines。
- 当 pane 输出中没有结构化 `status:` 回复块时，命令明确失败且不写入 `replies[]`。
- 扩展 `tests/test_dispatch_cli.py`，覆盖成功捕获最新结构化回复和无结构化回复失败。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 capture-reply 用法。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_dispatch_cli.py::test_capture_reply_extracts_latest_structured_reply_from_agent_output tests/test_dispatch_cli.py::test_capture_reply_rejects_output_without_structured_status -q` 看到 `capture-reply` 子命令不存在；实现后同一测试通过。
- 完整验证：`conda run -n agentdeck pytest -q` 27 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，`agentdeck --help` 显示 capture-reply 子命令。

### Current - Add plan status view

- 新增 `agentdeck plan status --plan-id <id>`，汇总 plan、approval 状态和 dispatch lineage。
- 扩展 `StateStore.plan_status()`，按 step 返回 agent、role、task、approval_id、approval_status、message_id、attempt_id、job_id 和 reject reason。
- status 输出包含 counts：steps、approvals、pending、approved、rejected、dispatched。
- 该命令是只读视图，不修改 state、不发送 tmux 输入，面向自然语言入口、GUI 和恢复任务进度。
- 扩展 `tests/test_leader_cli.py`，覆盖 dispatched/rejected/pending 混合状态，以及 unknown plan 错误。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 plan status 工作流。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_plan_status_summarizes_approvals_and_dispatch_lineage tests/test_leader_cli.py::test_plan_status_rejects_unknown_plan_id -q` 看到 `plan status` 子命令不存在；实现后同一测试通过。
- 完整验证：`conda run -n agentdeck pytest -q` 25 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，`agentdeck plan --help` 显示 status 子命令，临时 git 项目 smoke 确认 pending approvals 可汇总到 plan status。

### Current - Add approved approval dispatch

- 新增 `agentdeck approval dispatch --approval-id <id>`，将已批准的 approval step 转成现有 `dispatch -> message/attempt/job/inbox` 链路。
- dispatch 前会检查 approval 必须为 `approved`，目标 agent 必须存在且已绑定 running pane。
- 派发后会把 approval 标记为 `dispatched`，并记录 message_id、attempt_id、job_id 和 dispatched_at。
- 复用现有 `build_dispatch_prompt()` 与 `StateStore.create_dispatch_records()`，不引入第二套消息模型。
- 扩展 `tests/test_leader_cli.py`，覆盖 pending approval 不能 dispatch，以及 approved approval 会写入 lineage 并发送到目标 pane。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 approved approval dispatch 工作流。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_approval_dispatch_rejects_unapproved_item tests/test_leader_cli.py::test_approval_dispatch_sends_approved_step_to_agent_and_records_lineage -q` 看到 `approval dispatch` 子命令不存在；实现后同一测试通过。
- 完整验证：`conda run -n agentdeck pytest -q` 23 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，`agentdeck approval --help` 显示 dispatch 子命令，临时 git 项目 smoke 确认 pending approval dispatch 被拒绝且 `messages/jobs` 仍为 0。

### Current - Add approval gate MVP

- 新增 `agentdeck approval create-from-plan --plan-id <id>`，从 Leader plan 的 requires_approval steps 创建 `approvals[]`。
- 新增 `agentdeck approval list`、`agentdeck approval approve --approval-id <id>`、`agentdeck approval reject --approval-id <id> --reason <text>`，支持审批项状态流转。
- 扩展 `StateStore.create_approvals_from_plan()`、`StateStore.list_approvals()` 与 `StateStore.decide_approval()`，记录 approval_id、plan_id、step、agent、task、risk、status、reason 和 decided_at。
- Approval Gate MVP 只管理审批状态，不自动 dispatch、不发送 tmux 输入、不创建 message/job。
- 扩展 `tests/test_leader_cli.py`，覆盖从 plan 创建审批项、审批列表、approve/reject 和 unknown id 错误。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录审批工作流。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_approval_create_from_plan_generates_step_approvals tests/test_leader_cli.py::test_approval_list_and_decisions_update_status tests/test_leader_cli.py::test_approval_commands_reject_unknown_ids -q` 看到 `approval` 子命令不存在；实现后同一测试通过。
- 完整验证：`conda run -n agentdeck pytest -q` 21 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 approval 状态可从 pending 流转到 approved/rejected，且 `messages/jobs` 仍为 0。

### Current - Add plan inspection CLI

- 新增 `agentdeck plan list`，返回 `.agentdeck/state/state.json` 中已保存 plan 的摘要列表，包括 `plan_id`、task、provider、model、status、dispatch_ready、step_count 和 created_at。
- 新增 `agentdeck plan show --plan-id <id>`，按 plan_id 返回完整 plan，作为审批和 dispatch 前的人工检查入口。
- 扩展 `StateStore.list_plans()` 与 `StateStore.plan_by_id()`，保持 plan inspection 为只读行为，不修改 state、不触发 tmux runtime。
- 扩展 `tests/test_leader_cli.py`，覆盖 plan list、plan show 和 unknown plan 错误。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 plan inspection 工作流。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_plan_list_outputs_plan_summaries tests/test_leader_cli.py::test_plan_show_outputs_full_plan_by_id tests/test_leader_cli.py::test_plan_show_rejects_unknown_plan_id -q` 看到 `plan` 子命令不存在；实现后同一测试通过。
- 完整验证：`conda run -n agentdeck pytest -q` 18 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `plan list` 返回摘要且 `plan show` 返回完整 planner/coder/reviewer steps。

### Current - Add Leader plan MVP

- 新增 `agentdeck leader plan --task <text>`，让 Leader 先生成 plan-only 结构化计划，并写入 `.agentdeck/state/state.json` 的 `plans[]`。
- 新增 provider 抽象与本地 `fake` Leader provider；当前默认 dry-run，不调用外部 LLM，不 dispatch，不发送 tmux 输入。
- 暂时拒绝未实现的真实 provider，例如 `--provider deepseek` 会明确失败，避免把 fake dry-run 误报成真实 LLM 调用。
- 扩展 `LeaderOrchestrator.plan()` 与 `StateStore.record_plan()`，记录 `plan_id`、provider、model、status、dispatch_ready 和 plan steps。
- 新增 `tests/test_leader_cli.py`，用 TDD 验证 `leader plan` 会创建 plan，但不会创建 message/job/inbox。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 plan-only Leader 工作流和 provider-agnostic 边界。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_plan_creates_structured_plan_without_dispatching -q` 看到 `leader` 子命令不存在；实现后同一测试通过。
- 完整验证：`conda run -n agentdeck pytest -q` 15 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `agentdeck leader plan` 写入 1 个 plan 且 `messages/jobs` 仍为 0。

### Current - Add ultimate goal roadmap

- 新增 `docs/roadmap/ultimate-goal-roadmap.md`，明确 AgentDeck 的终极目标是任意 API-backed Leader LLM 调度多角色 Codex/Claude/其他 CLI Agent，并通过可见 runtime、通信账本、审批和恢复能力形成本地多智能体工作台。
- 明确 DeepSeek 只是首个默认 provider 候选，不是架构绑定点；后续 Leader provider 必须通过抽象边界接入。
- 将已实现能力映射回终极目标，说明 `agent runtime`、`role_prompt`、`dispatch`、`reply/ack`、`trace` 为什么是底座而不是偏离。
- 定义后续 Phase A-E：Leader Agent MVP、Approval Gate、Reply Extraction、Multi-Agent Run Loop、ProjectView/GUI。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，要求每轮开发前对照 roadmap 做防跑偏检查，并保持 provider-agnostic Leader 边界。
- 本地验证：检查 roadmap、README、CLAUDE、AGENT、HISTORY 更新，并运行 `git diff --check`、`conda run -n agentdeck pytest -q`、`conda run -n agentdeck python -m compileall src tests`。

### Current - Add communication trace command

- 新增 `agentdeck trace --id <id>`，支持用 `message_id`、`attempt_id`、`job_id`、`reply_id` 或 `inbox_id` 还原同一条通信链路。
- 扩展 `StateStore.trace()`，返回 message、attempts、jobs、replies 和 inbox_items。
- 扩展 `tests/test_dispatch_cli.py`，覆盖从 `reply_id` 反查完整 lineage。
- 更新 `README.md`、`CLAUDE.md`、`AGENT.md` 和本 history，记录 trace 用法。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_dispatch_cli.py -q`，看到 trace 测试失败；实现后同一测试文件 7 项通过。
- 真实 trace smoke：临时项目中完成 `dispatch -> reply` 后，用 `agentdeck trace --id <reply_id>` 返回 message、attempts、jobs、replies、inbox_items。

### Current - Add reply and ack communication loop

- 新增 `agentdeck reply --agent <id> --message-id <id> --text <text>`，将某个 agent 对 message 的结果写入 `replies[]`。
- reply 会把对应 `message` 标记为 `replied`，把关联 `attempt` 和 `job` 标记为 `completed`。
- 当 message 的 `from_actor` 是另一个 agent 时，reply 会作为 `task_reply` 投递到发起方 inbox。
- 新增 `agentdeck ack --agent <id> --inbox-id <id>`，将 inbox item 标记为 `acked`。
- `dispatch` 新增 `--from-agent`，用于表达 agent-to-agent 任务委派。
- 扩展 `tests/test_dispatch_cli.py`，覆盖 reply 记录、task_reply 回流 sender inbox、ack inbox item。
- 更新 `README.md`、`CLAUDE.md`、`AGENT.md` 和本 history，记录新的请求-回复-确认通信路径。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_dispatch_cli.py -q`，看到 2 个测试失败；实现后同一测试文件 6 项通过。
- 真实账本 smoke：临时项目中 `dispatch --from-agent coder --agent planner`，再 `reply --agent planner`，随后 `inbox --agent coder` 出现 `task_reply`，执行 `ack --agent coder` 后该 inbox item 状态变为 `acked`。

### Current - Add dispatch ledger and inbox view

- 将 dispatch 从单一 `message` 记录升级为最小通信账本：`message -> attempt -> job -> inbox task_request`。
- 扩展 `StateStore`，新增 `create_dispatch_records()` 和 `inbox_items()`。
- 新增 `agentdeck inbox --agent <id>`，查看某个 agent 的 inbox task request。
- 扩展 `tests/test_dispatch_cli.py`，覆盖 dispatch 后 `messages`、`attempts`、`jobs`、`inbox` 的关联关系，以及 `inbox` CLI 输出。
- 更新 `README.md`、`CLAUDE.md`、`AGENT.md` 和本 history，记录新的通信路径。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_dispatch_cli.py -q`，看到 2 个测试失败；实现后同一测试文件 4 项通过。
- 真实 tmux smoke：临时项目 spawn planner 后 dispatch `设计消息账本`，`agentdeck inbox --agent planner` 返回 1 条 `task_request`，并包含 `message_id`、`attempt_id`、`job_id`。

### Current - Add role-aware dispatch MVP

- 为 `AgentSpec` 和默认 `.agentdeck/config.toml` 增加 `role_prompt`，让 agent 不只显示 provider，也显示可注入的角色职责。
- 新增 `agentdeck agent assign-role --agent <id> --role <role> --role-prompt <prompt>`，支持通过 CLI 或后续 Leader 写回 agent 角色配置。
- 新增 `agentdeck dispatch --agent <id> --task <task>`，将 agent 的 `role`、`role_prompt`、任务和结构化输出格式拼成 prompt，发送到已运行的 tmux pane。
- 在 state 中新增 `messages` 记录，dispatch 会写入 `message_id`、`from_actor`、`to_agent`、`task`、`prompt` 和 `status`。
- 新增 `task_dispatched` 与 `agent_role_assigned` 事件。
- 新增 `tests/test_dispatch_cli.py`，覆盖默认 role_prompt、assign-role 写回配置、dispatch 发送角色任务并记录消息。
- 更新 `README.md` 与 `CLAUDE.md`，说明配置式角色指派、CLI 交互式角色指派和当前 MVP 通信路径。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_dispatch_cli.py -q`，看到 3 个测试失败；实现后新测试 3 项通过，全量 `conda run -n agentdeck pytest -q` 9 项通过，`python -m compileall src tests` 通过。
- 真实 tmux smoke：在临时项目中把 planner 命令改为 `sh`，spawn 后运行 `agentdeck dispatch --agent planner --task "设计消息账本"`，capture 中可见角色任务 prompt 内容，随后清理 tmux session。

### a3cd63c - Add agent stop and spawn lifecycle guard

- 新增 `agentdeck agent stop --agent <id>`，通过 tmux `kill-pane` 停止已绑定 agent pane，并把 state 中的 agent 标记为 `stopped`。
- 新增重复 spawn 保护：当 agent 已处于 `running` 且已有 `pane_id` 时，`agentdeck agent spawn` 会拒绝创建第二个 pane。
- 扩展 `StateStore`，增加 `mark_agent_stopped()`。
- 扩展 `RuntimeBackend` / `TmuxBackend`，增加 `kill_pane()`。
- 扩展 `tests/test_agent_cli.py`，覆盖重复 spawn guard 和 stop 生命周期。
- 更新 `README.md` 与 `CLAUDE.md`，补充 stop 命令和生命周期约束。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_agent_cli.py -q`，看到 2 个测试失败；实现后重新运行，同一测试文件 6 项通过。
- 真实 tmux smoke：在临时项目中 spawn planner 得到 pane `%1`，运行 `agentdeck agent stop --agent planner` 后返回 `status: stopped`，`agent list` 中 planner 的 `pane_id` 为 `null`。

### abb3ccd - Add tmux agent runtime CLI MVP

- 新增 `agentdeck agent list`，展示配置中的 agent 及其 runtime binding。
- 新增 `agentdeck agent spawn --agent <id>`，通过 tmux backend 创建项目 session、spawn agent pane，并记录 `agent_id -> pane_id`。
- 新增 `agentdeck agent capture --agent <id> --lines <n>`，从已绑定 pane 读取最近输出。
- 新增 `agentdeck agent send --agent <id> --text <text>`，向已绑定 pane 发送人工指定输入，并记录事件。
- 新增 `tests/test_agent_cli.py`，使用 fake tmux backend 覆盖 list/spawn/capture/send。
- 新增 `pytest.ini`，把 pytest 默认扫描范围限制到 `tests/`，避免误扫本地 `References/` 参考仓库。
- 更新 `.gitignore`，忽略 pytest 缓存目录。
- 更新 `README.md`，补充 agent runtime MVP 命令和约束。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_agent_cli.py -q` 看到 4 个测试因缺少 `agent` 子命令失败；实现后运行 `conda run -n agentdeck pytest -q`，4 项通过。
- 真实 tmux smoke：在临时项目中把 planner 命令改为 `sh -lc 'printf agentdeck-smoke; sleep 5'`，运行 `agentdeck agent spawn --agent planner` 后得到 pane `%1`，`agentdeck agent capture --agent planner --lines 20` 读到 `agentdeck-smoke`，随后清理 tmux session。

### 477c1da - Add persistent development history

- 新增 `HISTORY.md`，作为每次开发内容的持久记录。
- 回填前三次 commit 的开发内容、涉及文件和验证证据。
- 更新 `README.md` 与 `CLAUDE.md`，明确每次开发都必须同步更新 `HISTORY.md`，并与对应改动放在同一次 commit 中。
- 本地验证：运行 `git diff --check`，确认无空白格式问题。

### ae9f421 - Add conda environment for AgentDeck development

- 新增 `environment.yml`，标准化本项目开发环境为 Miniforge/conda 环境 `agentdeck`。
- 约定 Python 版本为 3.12，并纳入 `pip`、`setuptools`、`tmux`、`pytest`。
- 更新 `README.md`，把快速开始和开发命令统一为先执行 `conda activate agentdeck`。
- 更新 `CLAUDE.md`，要求所有开发、验证和 CLI 调试都在 `agentdeck` 环境中执行。
- 更新 `.gitignore`，忽略 editable install 生成的 `*.egg-info/`。
- 本地验证：创建 `agentdeck` 环境，安装 editable 包，运行 `agentdeck doctor`、`agentdeck status`、`python -m compileall src`。

### a3fd8ef - Add AgentDeck architecture and project skeleton

- 新增 `docs/architecture/multi-agent-terminal-design.md`，把 WispTerm、Claude Codex Bridge、Hermes Agent、tmux 四份参考分析融合成可执行架构。
- 新增 `README.md`、`CLAUDE.md`、`AGENT.md`，建立项目说明、agent 协作规则和开发约束。
- 新增 Python 包骨架 `src/agentdeck/`，包含 CLI、配置、状态、模型、tmux runtime、DeepSeek provider、Leader orchestrator 边界。
- 新增 `pyproject.toml`，提供 `agentdeck` console script。
- 更新 `.gitignore`，忽略 `.agentdeck/`、`__pycache__/`、`*.py[cod]`。
- 本地验证：`python -m compileall src`、`python -m agentdeck project init`、`python -m agentdeck doctor`、`python -m agentdeck status`。

### 2603c99 - Add reference repository analysis reports

- 新增四份参考仓库深度研究报告：
  - `docs/reference-analysis/wispterm-main.md`
  - `docs/reference-analysis/claude-codex-bridge-main.md`
  - `docs/reference-analysis/hermes-agent-main.md`
  - `docs/reference-analysis/tmux-master.md`
- 分析内容覆盖技术栈、源码结构、核心机制、优势、风险、可学习内容和对 AgentDeck 的分阶段建议。
- 新增 `.gitignore`，避免把 `References/`、zip 包和 `.DS_Store` 纳入 git。
- 本地验证：检查四份 Markdown 文件存在、统计行数、检查 heading，并确认 git 工作区只包含预期文档。
