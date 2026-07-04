# AgentDeck Development History

本文件记录 AgentDeck 每一次开发内容。约束：每次新增功能、文档规则、项目骨架、运行环境或用户可见行为变化，都必须同步更新本文件，并在同一次 commit 中提交。

## 2026-07-05

### Current - Refresh Leader chat plan ProjectView

- `agentdeck leader chat --message <text>` 在无 plan 时创建 plan-only 记录后，现在会重新读取并校验 ProjectView，再组装 chat 响应。
- plan-mode chat 响应里的 `project_view.plans` 会包含刚创建的 plan，`project_view.chat_turns` 也会包含同次写入的 chat turn，方便 GUI/自然语言层无需额外 status 调用即可渲染最新状态。
- 顶层 `leader_actions` 继续保持等于同次响应的 `project_view.leader_actions`，plan mode 仍不创建 approval、不 dispatch、不发送 tmux 输入。
- 扩展 leader chat 红灯测试，先确认 plan-mode 响应仍返回旧 ProjectView（`plans.count=0`），再实现刷新后断言 `plans.count=1` 且包含当前 `plan_id/turn_id`。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 plan-mode chat 响应必须返回刷新后的 ProjectView。
- 保持安全边界：本轮只刷新只读响应快照，不扩大 safe apply 白名单、不应用 action、不创建 approval、不 dispatch、不发送 tmux 输入。
- 本地验证：先运行 `test_leader_chat_creates_plan_from_natural_language_without_dispatching` 看到 `project_view.plans.count` 仍为 0 的红灯；实现后同一测试通过，`tests/test_leader_cli.py tests/test_contracts.py` 59 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 96 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 plan-mode chat 响应 `plans.count=1`、`plan_id/turn_id` 对齐，且 `messages/jobs` 仍为 0。

### Current - Discover Trace communication contract

- 新增 `agentdeck contract trace` 只读 discovery 入口，返回通信 lineage 的 top-level、message、attempt、job、reply 和 inbox item 字段列表。
- 新增 `agentdeck contract trace --example`，输出稳定 GUI-ready trace fixture，展示 `task_request` 与 `task_reply` 如何归入同一条 message lineage。
- 在 `src/agentdeck/contracts.py` 中新增 trace 字段常量、`trace_contract_payload()`、`trace_contract_response()`、`trace_example()` 和 `validate_trace_contract()`。
- `agentdeck trace --id <id>` 现在会输出 `schema_version`，并在打印 JSON 前通过 `validate_trace_contract()` 自校验；失败时返回非 0 且不输出半坏 trace。
- `StateStore.trace()` 现在会归一化 message/attempt/job/reply/inbox item 字段，缺失方向字段以 `null` 呈现，方便 GUI 稳定渲染通信账本。
- 新增 `docs/contracts/trace-schema.md`，并更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 trace contract discovery、自校验和只读边界。
- 保持安全边界：本轮只扩展只读 contract discovery、trace 输出字段和 validator，不应用 action、不创建 approval、不 dispatch、不发送 tmux 输入。
- 本地验证：先运行 trace contract 目标测试看到缺少 `TRACE_*` 常量和 `trace_contract_payload()` 的 import 红灯；实现后目标测试 7 项通过，真实 dispatch/reply trace validator 测试通过。
- 完整验证：`conda run -n agentdeck pytest -q` 96 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `agentdeck contract trace --example` 输出 `contract_exists=true`、`trace_command` 正确、example schema 对齐且包含 `task_request/task_reply`。

### Current - Discover ProjectView recovery pending fields

- 新增 `PROJECT_VIEW_RECOVERY_PENDING_FIELDS`，把 `recovery.pending` 的必备字段纳入机器可发现契约。
- `agentdeck contract project-view` 现在返回 `recovery_pending_fields`；`--example` 也会返回 `example_recovery_pending_fields`。
- `validate_project_view_contract()` 现在会校验 `recovery.pending.leader_actions/approvals/approved_approvals/inbox_items/leader_errors`，缺字段会返回明确错误。
- 扩展 contract 红灯测试，先验证缺少 `PROJECT_VIEW_RECOVERY_PENDING_FIELDS` 和删除 `pending.leader_errors` 未被 validator 保护，再实现 discovery/validator。
- 更新 `README.md`、`docs/contracts/project-view-schema.md`、`CLAUDE.md` 与 `AGENT.md`，记录 `recovery_pending_fields`。
- 保持安全边界：本轮只扩展只读 contract discovery 和 validator，不读取 live state、不修改 `.agentdeck/state`、不发送 tmux 输入。
- 本地验证：先运行 ProjectView contract 目标测试看到缺少 `PROJECT_VIEW_RECOVERY_PENDING_FIELDS` 的 import 红灯；实现后目标测试 6 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 89 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时目录 smoke 确认 contract 输出 `recovery_pending_fields`、example 含 `pending.leader_errors`、删除该字段会被 validator 拒绝。

### Current - Count Leader errors in ProjectView recovery pending

- `status.recovery.pending` 现在包含 `leader_errors` 计数，让 GUI 可以在统一恢复面展示还有多少 Leader 错误待检查。
- `project_view_example()` 的 recovery fixture 同步包含 `pending.leader_errors=0`，保持 contract example 与真实 status 输出一致。
- 扩展 recovery 相关红灯测试，确认普通 action recovery 输出 `leader_errors=0`，只有 leader error 时输出 `leader_errors=1`。
- 更新 `README.md`、`docs/contracts/project-view-schema.md`、`CLAUDE.md` 与 `AGENT.md`，记录 `pending.leader_errors` 的展示语义。
- 保持安全边界：本轮只扩展只读 ProjectView recovery pending 计数，不应用 action、不创建 approval、不 dispatch、不发送 tmux 输入。
- 本地验证：先运行 recovery 两条目标测试看到缺少 `pending.leader_errors` 的红灯；实现后同一测试 2 项通过，ProjectView example 相关测试 2 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 88 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认只有 leader error 时 `recovery.pending.leader_errors=1`、`recommended_action.source=leader_error`。

### Current - Surface Leader errors in ProjectView recovery

- `status.recovery` 现在会在没有 pending leader action、approval 或 inbox item，但存在 `leader_errors[]` 时返回 `status=leader_error`。
- `leader_error` recovery 会推荐 `agentdeck status`，`recommended_action.source=leader_error`，`target_id` 指向最新 leader error id，方便 GUI/人类从统一恢复入口检查 Leader 错误。
- 新增 status 红灯测试，确认只有 leader error 时 recovery 不再停留在 `idle`。
- 更新 `README.md`、`docs/contracts/project-view-schema.md`、`CLAUDE.md` 与 `AGENT.md`，记录 leader error recovery 语义和优先级。
- 保持安全边界：本轮只扩展只读 ProjectView recovery，不应用 action、不创建 approval、不 dispatch、不发送 tmux 输入。
- 本地验证：先运行 `test_status_recovery_surfaces_leader_errors_when_no_work_is_pending` 看到 recovery 仍为 `idle` 的红灯；实现后同一测试通过，ProjectView/contract 相关测试 15 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 88 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认只有 leader error 时 `recovery.status=leader_error`、`recommended_action.source=leader_error`、`target_id=err_smoke`。

### Current - Record Leader chat contract failures

- `agentdeck leader chat` 的 response contract 校验失败现在会写入 `.agentdeck/state/state.json` 的 `leader_errors[]`，`provider` 标记为 `agentdeck-contract`，`mode` 使用失败响应的 chat mode。
- 同一失败会追加 `leader_chat_contract_failed` 事件，记录 error_id、mode、message_length 和 error_count，方便 GUI/恢复工具从事件时间线解释为什么没有 chat JSON。
- 扩展 CLI 红灯测试：monkeypatch 生成缺少 `leader_explanation.safety` 的响应时，命令必须拒绝输出、写入 leader error、追加事件。
- 更新 `README.md`、`docs/contracts/leader-chat-schema.md`、`CLAUDE.md` 与 `AGENT.md`，记录 chat response contract failure 的可审计边界。
- 保持安全边界：本轮只增强错误入账和审计事件，不扩大 safe apply 白名单、不自动 dispatch、不发送 tmux 输入。
- 本地验证：先运行 `test_leader_chat_refuses_invalid_chat_response_before_printing` 看到 `leader_errors` 为空的红灯；实现后同一测试通过，`tests/test_contracts.py tests/test_leader_cli.py` 54 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 87 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认坏 chat response 返回 1，写入 `leader_errors[0].provider=agentdeck-contract`，事件类型为 `leader_chat_contract_failed`。

### Current - Self-validate Leader chat responses

- 新增 `validate_leader_chat_contract()`，校验 `agentdeck leader chat` 响应字段、`leader_explanation` 字段、内嵌 `project_view` v1 契约，以及顶层 `leader_actions` 与 `project_view.leader_actions` 是否一致。
- `agentdeck leader chat` 现在会在输出 JSON 前调用 leader-chat contract validator；校验失败时返回非 0、stderr 输出错误，并且不打印半坏 chat response。
- plan/apply_action 响应补齐 `recovery`、`leader_action`、`review`、`next_command` 等稳定字段，让三种 chat mode 更符合 `leader-chat` discovery contract。
- 新增 validator 单元测试：example 可通过、缺失 `leader_explanation.safety` 会报错、内嵌 ProjectView 漂移会带 `project_view:` 前缀报错。
- 新增 CLI 红灯测试：monkeypatch 生成缺少 `safety` 的解释块时，`leader chat` 必须拒绝输出并报告 `Leader chat contract validation failed`。
- 更新 `README.md`、`docs/contracts/leader-chat-schema.md`、`docs/contracts/project-view-schema.md`、`CLAUDE.md` 与 `AGENT.md`，记录 chat response 自校验边界。
- 保持安全边界：本轮只新增输出契约校验，不扩大 safe apply 白名单、不自动 dispatch、不发送 tmux 输入。
- 本地验证：先运行 leader-chat validator/CLI 目标测试看到缺少 `validate_leader_chat_contract` 的 import 红灯；实现后目标测试 4 项通过，`tests/test_contracts.py tests/test_leader_cli.py` 54 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 87 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `leader chat` plan 响应可被 `validate_leader_chat_contract()` 校验为 ok，且包含 `recovery`、`leader_action`、对齐的 `leader_actions`。

### Current - Discover Leader chat response contract

- 新增 `agentdeck contract leader-chat` 只读 discovery 入口，返回自然语言 Leader chat 响应字段、`leader_explanation` 字段、契约文档路径和 ProjectView contract 关联。
- 新增 `agentdeck contract leader-chat --example`，输出稳定 GUI-ready chat response fixture，包含 `project_view`、`leader_actions`、`leader_explanation`、`review`、`recovery` 和 `leader_action`。
- 在 `src/agentdeck/contracts.py` 中新增 `LEADER_CHAT_RESPONSE_FIELDS`、`LEADER_CHAT_EXPLANATION_FIELDS`、`leader_chat_contract_payload()`、`leader_chat_contract_response()` 和 `leader_chat_example()`，让 CLI 与测试复用同一契约源。
- 新增 `docs/contracts/leader-chat-schema.md`，说明 chat 响应不是第二状态源，`project_view` 仍是状态真相，`leader_explanation` 只是解释层。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录新 discovery 命令和接手约束。
- 保持安全边界：本轮只新增只读 contract discovery 和稳定 example，不读取 live state、不修改 `.agentdeck/state`、不发送 tmux 输入。
- 本地验证：先运行 leader-chat contract 目标测试看到缺少 `LEADER_CHAT_EXPLANATION_FIELDS` / `leader_chat_contract_payload` 的 import 红灯；实现后 contract 目标测试 5 项通过，contract 相关测试 13 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 83 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时目录 smoke 确认 `agentdeck contract leader-chat --example` 输出 contract_exists、explanation_fields、review 示例、`leader_actions == project_view.leader_actions`、`safety=safe_apply` 和 `recommended_action_id=act_example`。

### Current - Explain Leader chat recommendations

- `agentdeck leader chat` 的 plan/review/apply_action 响应现在都会返回 `leader_explanation`，面向自然语言 shell 和未来 GUI 解释当前模式、推荐 action、reason、next_command、safety 和是否需要人类显式确认。
- review 解释会从 ProjectView recovery 的 `recommended_action` 派生 `recommended_action_id/safety/requires_explicit_user`，让 GUI 可以在同一响应中展示“为什么推荐这个 action”。
- apply_action 解释会标记 `safe_apply_completed` 并返回 `result_count`，说明 safe apply 已经创建多少 approval 记录。
- 扩展 chat 相关测试，先验证 plan/review/apply_action 响应缺少 `leader_explanation` 的红灯，再实现只读解释 helper。
- 更新 `README.md`、`docs/contracts/project-view-schema.md`、`CLAUDE.md` 与 `AGENT.md`，记录 `leader_explanation` 字段和边界。
- 保持安全边界：本轮只扩展 chat 响应解释层，不新增自动 dispatch、不发送 tmux 输入、不扩大 safe apply 白名单。
- 本地验证：先运行 chat 四条目标测试看到 `KeyError: 'leader_explanation'` 红灯；实现后同一测试 4 项通过，`tests/test_leader_cli.py` 40 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 78 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 review 响应的 `leader_explanation.recommended_action_id` 等于 `leader_action.action_id`，`safety=safe_apply`，`next_command` 与响应顶层一致。

### Current - Surface leader action queue in Leader chat responses

- `agentdeck leader chat` 的 plan/review/apply_action 三种响应现在都会在顶层返回 `leader_actions`，并保持它与同次响应的 `project_view.leader_actions` 一致。
- 自然语言入口和未来 GUI 可以从一次 chat 响应直接渲染 action queue、推荐 action、apply 按钮和 blocker，不需要额外调用 `agentdeck status` 或重新解析 state。
- 扩展 chat 相关测试，先验证 plan/review/apply_action 响应缺少顶层 `leader_actions` 的红灯，再实现最小 payload 映射。
- 更新 `README.md`、`docs/contracts/project-view-schema.md`、`CLAUDE.md` 与 `AGENT.md`，记录 chat 响应里的 action queue 契约。
- 保持安全边界：本轮只扩展只读 chat payload，不应用新的 action 类型、不自动 dispatch、不发送 tmux 输入。
- 本地验证：先运行 chat 四条目标测试看到 `KeyError: 'leader_actions'` 红灯；实现后同一测试 4 项通过，`tests/test_leader_cli.py` 40 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 78 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 review 响应 `leader_actions == project_view.leader_actions`、推荐 action id 存在、`items[0].is_recommended=True`。

### Current - Validate ProjectView leader action recommendation fields

- 新增 `PROJECT_VIEW_LEADER_ACTIONS_FIELDS` 和 `PROJECT_VIEW_LEADER_ACTION_ITEM_FIELDS`，把 ProjectView leader action 队列字段纳入可复用契约定义。
- `agentdeck contract project-view` 现在会发现 `leader_actions_fields` 和 `leader_action_item_fields`；`--example` 也会输出对应 example 字段列表。
- `validate_project_view_contract()` 现在会校验 `leader_actions.recommended_action_id` 和 `leader_actions.items[].is_recommended` 等 GUI 依赖字段。
- 新增 validator 红灯测试，删除 `recommended_action_id/is_recommended` 时必须返回明确错误。
- 更新 `docs/contracts/project-view-schema.md` 和 CLI/module contract 测试，记录新的 discovery 字段。
- 保持安全边界：本轮只扩展只读 contract metadata 和 validator，不读取 live state、不修改 `.agentdeck/state`、不发送 tmux 输入。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_contracts.py::test_validate_project_view_contract_reports_missing_leader_action_recommendation_fields -q` 看到 validator 放过坏 payload；实现后同一测试通过，contracts 相关测试 4 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 78 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，非项目临时目录 smoke 确认 discovery 输出 leader action 字段列表、example 校验通过、删除 `recommended_action_id` 后 validator 报错。

### Current - Sync ProjectView example leader action recommendation fields

- `agentdeck contract project-view --example` 的 `example_project_view.leader_actions` 现在包含 `recommended_action_id`，示例 action item 也包含 `is_recommended=true`。
- `project_view_example()` fixture 与真实 `agentdeck status` 的 ProjectView leader action 推荐字段保持一致，方便 GUI 原型直接使用 example。
- 扩展 `test_contract_project_view_example_exports_gui_ready_status` 与 `test_project_view_example_matches_contract_field_lists`，先验证 example 缺少推荐字段的红灯，再实现 fixture 更新。
- 更新 `docs/contracts/project-view-schema.md`，在 example 片段中展示 leader action 推荐字段。
- 保持安全边界：本轮只更新只读 contract example fixture，不读取 live state、不修改 `.agentdeck/state`、不发送 tmux 输入。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_project_view_example_exports_gui_ready_status tests/test_contracts.py::test_project_view_example_matches_contract_field_lists -q` 看到缺少 `recommended_action_id`；实现后同一测试 2 项通过，contract/example 相关测试 10 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 77 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，非项目临时目录 smoke 确认 contract example 输出 `recommended_action_id=act_example`、`is_recommended=True`、`recovery.recommended_action.target_id=act_example`。

### Current - Mark recommended action in ProjectView leader actions

- `agentdeck status` 的 ProjectView `leader_actions` 摘要现在会从 pending leader actions 中派生顶层 `recommended_action_id`，与 `status.recovery` 的 pending leader action 优先级保持一致。
- ProjectView `leader_actions.items[]` 新增 `is_recommended`，让未来 GUI 默认状态面也能直接高亮当前 recovery 推荐 action。
- 扩展 `test_status_includes_project_state_summaries`，先验证 status 缺少 `recommended_action_id/is_recommended` 的红灯，再实现 ProjectView 输出。
- 更新 `README.md`、`docs/contracts/project-view-schema.md`、`CLAUDE.md` 与 `AGENT.md`，记录 ProjectView 和 `leader actions` CLI 均暴露推荐项标记。
- 保持安全边界：本轮只扩展只读 ProjectView 摘要，不应用 action、不创建 approval、不 dispatch、不发送 tmux 输入。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_status_includes_project_state_summaries -q` 看到 status 缺少推荐项字段；实现后同一测试 1 项通过，ProjectView/leader actions/contract 相关测试 11 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 77 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `status.leader_actions.recommended_action_id=<action_id>`、`items[0].is_recommended=True` 且与 `recovery.recommended_action.target_id` 一致。

### Current - Mark recommended action in Leader action queue

- `agentdeck leader actions` 现在会读取并校验 ProjectView，从 `recovery.recommended_action.target_id` 派生顶层 `recommended_action_id`。
- action queue 每个 item 新增 `is_recommended`，用于 GUI 或自然语言层在列表页直接高亮当前 recovery 推荐动作。
- 扩展 `test_leader_actions_lists_persisted_actions`，先验证缺少 `recommended_action_id` 的红灯，再确认 pending `create_approvals` action 被标记为推荐项。
- 更新 `README.md`、`docs/contracts/project-view-schema.md`、`CLAUDE.md` 与 `AGENT.md`，记录 action queue 推荐项标记。
- 保持安全边界：本轮只扩展只读 action queue 输出，不应用 action、不创建 approval、不 dispatch、不发送 tmux 输入。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_actions_lists_persisted_actions -q` 看到缺少 `recommended_action_id`；实现后同一测试 1 项通过，action queue/detail/ProjectView 相关测试 5 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 77 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `leader actions` 输出 `recommended_action_id=<action_id>`、`actions[0].is_recommended=True` 且与 `leader next` 生成的 action 匹配。

### Current - Add recovery context to Leader action detail

- `agentdeck leader action --action-id <id>` 现在会先读取并校验 ProjectView，然后在 action 详情中附带当前 `recovery`、`recommended_action` 和 `matches_recommended_action`。
- 当当前 recovery 推荐的 `target_id` 等于该 action_id 时，`matches_recommended_action=true`，方便 GUI 判断详情页按钮是否就是当前恢复入口推荐动作。
- 新增 `test_leader_action_show_includes_recovery_recommended_action_match`，锁定 pending `create_approvals` action 详情与 recovery 推荐动作的对齐关系。
- 更新 `README.md`、`docs/contracts/project-view-schema.md`、`CLAUDE.md` 与 `AGENT.md`，记录 action detail 的 recovery 对照输出。
- 保持安全边界：本轮只扩展只读详情输出，不应用 action、不创建 approval、不 dispatch、不发送 tmux 输入。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_action_show_includes_recovery_recommended_action_match -q` 看到缺少 `recovery`；实现后同一测试 1 项通过，action detail/recovery 相关测试 6 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 77 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `leader action` 输出 `matches_recommended_action=True`、`recommended_action.target_id=<action_id>`、`recovery.status=action_required`。

### Current - Gate Leader apply-action on ProjectView validation

- `agentdeck leader apply-action --action-id <id>` 现在会在应用 safe action 前复用 `_project_view_payload_or_error()`，只有 ProjectView 满足 `project-view/v1` 契约后才会调用 `store.apply_leader_action()`。
- 新增 `test_leader_apply_action_refuses_invalid_project_view_before_applying`，锁定状态面无效时不得创建 approvals、不得把 leader action 标记为 applied、不得写入 message 或 job。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录显式 safe apply 前也必须通过 ProjectView contract 守门。
- 保持安全边界：本轮只收紧 safe apply 的状态面校验，不新增自动 dispatch、不扩大可 apply action 白名单、不发送 tmux 输入。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_apply_action_refuses_invalid_project_view_before_applying -q` 看到旧实现返回 0；实现后同一测试 1 项通过，apply-action/chat apply 相关测试 6 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 76 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `leader apply-action` 返回 `kind=create_approvals/status=applied/approval_count=3`。

### Current - Gate Leader review and next on ProjectView validation

- `agentdeck leader review --plan-id <id>` 和 `agentdeck leader next [--plan-id <id>]` 现在会在读取项目后先复用 `_project_view_payload_or_error()`，只有 ProjectView 满足 `project-view/v1` 契约后才继续做 review 或写入 `leader_actions[]`。
- 新增 `break_project_view_recovery()` 测试辅助，并复用到 chat/review/next 的坏 ProjectView 场景，减少测试漂移。
- 新增 `test_leader_next_refuses_invalid_project_view_before_recording_action`，锁定状态面无效时不得写入 leader action、approval、message 或 job。
- 新增 `test_leader_review_refuses_invalid_project_view_before_recommending_next_step`，锁定状态面无效时 review 不输出下一步建议。
- 更新 `README.md`、`docs/contracts/project-view-schema.md`、`CLAUDE.md` 与 `AGENT.md`，记录 Leader review/next 也必须通过 ProjectView contract 守门。
- 保持安全边界：本轮只收紧 Leader 决策入口，不新增自动 dispatch、不创建 approval、不发送 tmux 输入。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_next_refuses_invalid_project_view_before_recording_action tests/test_leader_cli.py::test_leader_review_refuses_invalid_project_view_before_recommending_next_step -q` 看到两条测试均因命令返回 0 失败；实现后同一测试 2 项通过，Leader next/review/chat 相关测试 7 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 75 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `leader next` 返回 `kind=create_approvals/status=pending` 且 `leader review` 返回 `next_action=wait_for_approval`。

### Current - Gate Leader chat on ProjectView validation

- `agentdeck leader chat --message <text>` 现在会在读取 ProjectView 后先复用 `validate_project_view_contract()` 守门，再进入 plan/review/apply-action 分支。
- 新增 CLI 内部 `_project_view_payload_or_error()`，让 `agentdeck status` 和 Leader chat 共用同一套 ProjectView payload 生成、校验和 stderr 错误格式。
- 当 ProjectView 违反 `project-view/v1` 基础契约时，chat 返回非 0，不输出半坏 JSON，也不会创建 plan、chat_turn、message、job 或 inbox。
- 新增 `test_leader_chat_refuses_invalid_project_view_before_planning`，用缺失 `recovery` 的 ProjectView 模拟状态面漂移，锁定自然语言入口的失败边界。
- 更新 `README.md`、`docs/contracts/project-view-schema.md`、`CLAUDE.md` 与 `AGENT.md`，记录 Leader chat 必须通过 ProjectView 合约守门。
- 保持安全边界：本轮只收紧自然语言入口的状态面校验，不新增自动 dispatch、不创建 approval、不发送 tmux 输入。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_refuses_invalid_project_view_before_planning -q` 看到 chat 未拒绝缺 `recovery` 的 ProjectView 且返回 0；实现后同一测试 1 项通过，Leader chat/status 相关测试 7 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 73 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `leader chat` 返回 `mode=plan` 且嵌入的 `project_view` 可被 validator 校验为 `{'ok': True, 'errors': []}`。

### Current - Add ProjectView status self-validation

- `agentdeck status` 现在会在输出 JSON 前调用 `validate_project_view_contract()` 自校验 ProjectView。
- 当 ProjectView 违反 `project-view/v1` 基础契约时，status 返回非 0、错误写入 stderr，并且不输出半坏的 ProjectView JSON。
- 新增 `test_status_refuses_project_view_contract_violation`，用缺失 `recovery` 的 ProjectView 模拟内部漂移，锁定 CLI 自校验行为。
- 更新 `docs/contracts/project-view-schema.md`、`CLAUDE.md` 与 `AGENT.md`，记录 status 自校验是 GUI、Leader chat loop 和恢复入口的输出前守门规则。
- 保持安全边界：本轮只校验 status payload，不读取额外 live state、不修改 `.agentdeck/state`、不发送 tmux 输入。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_status_refuses_project_view_contract_violation -q` 看到 status 未拒绝缺 `recovery` 的 ProjectView 且返回 0；实现后同一测试 1 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 72 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `agentdeck status` 输出可被 `validate_project_view_contract()` 校验为 `{'ok': True, 'errors': []}`。

### Current - Add ProjectView contract validator

- 新增 `agentdeck.contracts.validate_project_view_contract(payload)`，用于校验任意 ProjectView-like payload 是否满足 `project-view/v1` 基础契约。
- validator 返回 `{"ok": bool, "errors": [...]}`，覆盖 schema_version、top-level fields、recovery fields 和非空 recommended_action fields。
- 扩展 `tests/test_contracts.py`，先验证 validator 缺失的红灯，再覆盖 example 通过、缺少 top-level field、schema version mismatch 三类场景。
- 扩展 `tests/test_agent_cli.py`，让 contract example 和真实 `agentdeck status` contract smoke 都复用 validator。
- 更新 `docs/contracts/project-view-schema.md`、`CLAUDE.md` 与 `AGENT.md`，记录 validator 作为 GUI、测试和外部集成的 v1 基础校验入口。
- 保持安全边界：validator 只检查传入 payload，不读取 live state、不修改 `.agentdeck/state`、不发送 tmux 输入。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_contracts.py -q` 看到 `validate_project_view_contract` 缺失；实现后 `tests/test_contracts.py` 7 项通过，validator 复用相关测试 9 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 71 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，模块 smoke 确认 example payload 校验通过、删除 recovery 后返回 `missing top-level field: recovery`。

## 2026-07-04

### Current - Add ProjectView field constants

- 新增 `PROJECT_VIEW_TOP_LEVEL_FIELDS`、`PROJECT_VIEW_RECOVERY_FIELDS` 和 `PROJECT_VIEW_RECOMMENDED_ACTION_FIELDS`，作为 ProjectView contract 字段列表的模块级不可变定义。
- `project_view_contract_payload()` 改为从这些常量生成 JSON list，保持 CLI 输出兼容，同时减少内联列表重复。
- 扩展 `tests/test_contracts.py`，先验证字段常量缺失的红灯，再确认 discovery payload 复用这些常量。
- 更新 `docs/contracts/project-view-schema.md`，记录字段常量的源码入口。
- 保持安全边界：本轮只收敛契约字段定义，不改变 ProjectView 输出、不读取 live state、不修改 `.agentdeck/state`、不发送 tmux 输入。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_contracts.py -q` 看到字段常量缺失；实现后同一测试 4 项通过，`tests/test_contracts.py tests/test_agent_cli.py tests/test_leader_cli.py` 55 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 68 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，模块 smoke 确认 contract payload 的三组字段列表均来自对应字段常量。

### Current - Reuse ProjectView contract fields in status tests

- 调整 `tests/test_agent_cli.py` 的 ProjectView contract/status 测试，改为从 `agentdeck.contracts.project_view_contract_payload()` 读取 top-level、recovery 和 recommended_action 字段列表。
- `test_contract_project_view_discovers_schema_for_gui_clients` 不再手写字段列表，直接和 contract 模块输出比对。
- `test_status_matches_project_view_contract_for_gui_clients` 也复用 contract 模块字段列表，减少 status 测试、contract discovery 和 example fixture 之间的漂移点。
- 保持安全边界：本轮只重构测试护栏，不改变 CLI 输出、不修改 state、不发送 tmux 输入。
- 本地验证：`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_project_view_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_status_matches_project_view_contract_for_gui_clients tests/test_agent_cli.py::test_contract_project_view_cli_matches_contract_module -q` 3 项通过，`tests/test_contracts.py tests/test_agent_cli.py tests/test_leader_cli.py` 55 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 68 项通过，`conda run -n agentdeck python -m compileall src tests` 通过。

### Current - Add ProjectView contract module parity

- 新增 `agentdeck.contracts.project_view_contract_response()`，统一生成 `agentdeck contract project-view` 的默认输出和 `--example` 输出。
- `cli.py` 的 contract 命令改为直接调用 `project_view_contract_response()`，不再在命令层手动拼接 example 字段。
- 扩展 `tests/test_contracts.py`，覆盖默认 response 与 `project_view_contract_payload()` 一致、example response 与 `project_view_example()` 字段一致。
- 扩展 `tests/test_agent_cli.py`，新增 CLI/module parity 测试，直接比较 `agentdeck contract project-view --example` 输出和 `project_view_contract_response(..., include_example=True)`。
- 更新 `docs/contracts/project-view-schema.md`，记录 CLI discovery 命令使用 `project_view_contract_response()` 作为统一输出源。
- 保持安全边界：本轮只统一只读契约输出路径，不改变 ProjectView 字段、不读取 live state、不修改 `.agentdeck/state`、不发送 tmux 输入。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_contracts.py -q` 看到 `project_view_contract_response` 缺失；实现后 contracts/CLI parity 测试 5 项通过，`tests/test_agent_cli.py tests/test_leader_cli.py` 51 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 68 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，非项目临时目录 smoke 确认 CLI `--example` 输出与 `project_view_contract_response(..., include_example=True)` 完全一致。

### Current - Split ProjectView contract module

- 新增 `src/agentdeck/contracts.py`，把 ProjectView contract discovery payload 和 GUI-ready example fixture 从 `cli.py` 拆成可复用模块。
- `agentdeck contract project-view` 继续保持原有输出，CLI 只负责定位文档路径、处理 `--example` 和打印 JSON。
- 新增 `tests/test_contracts.py`，直接测试 `project_view_contract_payload()` 和 `project_view_example()` 可在不经过 CLI 的情况下复用。
- 保留 `tests/test_agent_cli.py` 的 CLI discovery/example 测试，确保命令行为不回退。
- 更新 `docs/contracts/project-view-schema.md`、`CLAUDE.md` 与 `AGENT.md`，记录 contract payload 和 example fixture 的源码入口。
- 保持安全边界：本轮是模块拆分，不改变契约字段、不读取 live state、不修改 `.agentdeck/state`、不发送 tmux 输入。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_contracts.py -q` 看到 `agentdeck.contracts` 模块不存在；实现后 `tests/test_contracts.py` 和 contract CLI 测试 4 项通过，`tests/test_agent_cli.py tests/test_leader_cli.py` 50 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 65 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，非项目临时目录 smoke 确认 CLI `--example` 与 `agentdeck.contracts.project_view_example()` 的 schema version 一致。

### Current - Add ProjectView schema version guard

- 新增 `src/agentdeck/models.py::PROJECT_VIEW_SCHEMA_VERSION`，作为 ProjectView schema version 的源码单一来源。
- `agentdeck status`、`agentdeck contract project-view` 和 `agentdeck contract project-view --example` 均改为引用同一常量，避免 Python 源码中重复手写 `project-view/v1`。
- 扩展 `tests/test_agent_cli.py`，先让 contract/status 测试引用 `cli.PROJECT_VIEW_SCHEMA_VERSION` 看到常量缺失红灯，再实现常量与源码替换。
- 使用 `rg "project-view/v1" src tests -n` 验证源码与测试中只剩 `src/agentdeck/models.py` 的单一常量定义。
- 更新 `docs/contracts/project-view-schema.md`、`CLAUDE.md` 与 `AGENT.md`，记录 schema version 的源码单一来源。
- 保持安全边界：本轮只收敛契约版本常量，不改变 ProjectView 字段、不修改 state、不发送 tmux 输入。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_project_view_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_project_view_example_exports_gui_ready_status tests/test_agent_cli.py::test_status_matches_project_view_contract_for_gui_clients -q` 看到 `PROJECT_VIEW_SCHEMA_VERSION` 缺失；实现后同一测试 3 项通过，`tests/test_agent_cli.py tests/test_leader_cli.py` 50 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 63 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，`rg "project-view/v1" src tests -n` 确认源码/测试只剩 `PROJECT_VIEW_SCHEMA_VERSION` 常量定义，非项目临时目录 smoke 确认 discovery 与 example schema version 一致。

### Current - Add ProjectView example drift guard

- 扩展 `agentdeck contract project-view --example` 输出，新增 `example_top_level_fields`、`example_recovery_fields` 和 `example_recommended_action_fields`。
- 这些字段从 `example_project_view` fixture 自身派生，并在测试中与 discovery 元数据字段列表比对，防止文档、discovery 和 example 三者漂移。
- 扩展 `tests/test_agent_cli.py::test_contract_project_view_example_exports_gui_ready_status`，先验证缺少 `example_top_level_fields` 的红灯，再实现字段摘要；测试使用集合比较避免 JSON key 排序影响。
- 更新 `docs/contracts/project-view-schema.md`，记录 `example_*_fields` 的 drift guard 语义。
- 保持安全边界：本轮只增加只读契约元数据和测试护栏，不读取 live state、不修改 `.agentdeck/state`、不发送 tmux 输入。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_project_view_example_exports_gui_ready_status -q` 看到缺少 `example_top_level_fields`；实现并修正 JSON key 顺序断言后同一测试 1 项通过，contract/status/leader 相关测试 38 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 63 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，非项目临时目录 smoke 确认 `example_top_level_fields`、`example_recovery_fields`、`example_recommended_action_fields` 分别与 discovery 字段列表一致。

### Current - Add ProjectView contract example fixture

- 扩展只读命令 `agentdeck contract project-view --example`，在契约 discovery 输出中附带稳定的 GUI-ready ProjectView 示例。
- 示例包含 `schema_version`、tmux runtime agent、leader action、chat turn、recovery 和 `recommended_action`，使用固定 ID 方便 GUI 原型和自动化测试引用。
- 默认 `agentdeck contract project-view` 输出保持 discovery 元数据，不附带示例。
- 保持安全边界：example fixture 是确定性静态示例，不读取 live project state、不修改 `.agentdeck/state`、不发送 tmux 输入。
- 扩展 `tests/test_agent_cli.py`，先验证 `--example` 未识别的红灯，再实现示例输出；同时确认默认 discovery 输出仍通过。
- 更新 `README.md`、`docs/contracts/project-view-schema.md`、`CLAUDE.md` 与 `AGENT.md`，记录 `--example` 的 GUI 原型用途。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_project_view_example_exports_gui_ready_status -q` 看到 `--example` 未识别；实现后 contract 相关测试 2 项通过，`tests/test_agent_cli.py tests/test_leader_cli.py` 50 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 63 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，非项目临时目录 smoke 确认 `agentdeck contract project-view --example` 返回 `example=true`、`example_project_view.schema_version=project-view/v1` 和 `recommended_action.target_id=act_example`。

### Current - Add ProjectView contract discovery command

- 新增只读命令 `agentdeck contract project-view`，用于 GUI 或外部集成发现 ProjectView 契约。
- 输出包含 `schema_version`、`status_command`、`contract_path`、`contract_exists`、`top_level_fields`、`recovery_fields` 和 `recommended_action_fields`。
- 命令不要求项目已初始化，不读取或修改 `.agentdeck/state`，只返回仓库内契约文档和字段摘要。
- 扩展 `tests/test_agent_cli.py`，先验证 `contract` 子命令不存在的红灯，再实现 discovery 输出。
- 更新 `README.md`、`docs/contracts/project-view-schema.md`、`CLAUDE.md` 与 `AGENT.md`，记录契约 discovery 入口。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_project_view_discovers_schema_for_gui_clients -q` 看到 `contract` 无效；实现后同一测试 1 项通过，`tests/test_agent_cli.py tests/test_leader_cli.py` 49 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 62 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，非项目临时目录 smoke 确认 `agentdeck contract project-view` 返回 `schema_version=project-view/v1`、`contract_exists=true` 和完整 `recommended_action_fields`。

### Current - Add ProjectView contract smoke test

- 扩展 ProjectView 顶层输出，新增 `schema_version: "project-view/v1"`，为未来 GUI 和自然语言入口提供明确契约版本。
- 新增 `tests/test_agent_cli.py::test_status_matches_project_view_contract_for_gui_clients`，将临时 contract smoke 固化为自动化测试。
- 测试会检查 `docs/contracts/project-view-schema.md` 存在，并验证 `agentdeck status` 输出包含契约声明的 top-level、`recovery` 和 `recommended_action` 关键字段。
- 同步更新 `docs/contracts/project-view-schema.md`、`README.md`、`CLAUDE.md` 与 `AGENT.md`，记录当前 ProjectView schema version。
- 保持安全边界：本轮只增加契约版本和测试护栏，不改变 dispatch、approval、tmux 或 state mutation 行为。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_status_matches_project_view_contract_for_gui_clients -q` 看到缺少 `schema_version`；实现后同一测试 1 项通过，ProjectView/Leader 相关测试 40 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 61 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `schema_version=project-view/v1` 且 `recommended_action` 包含 label/command/safety/requires_explicit_user/source/target_id。

### Current - Add ProjectView contract document

- 新增 `docs/contracts/project-view-schema.md`，把 `agentdeck status` 的 ProjectView 作为 CLI、自然语言 Leader、恢复工具和未来 GUI 的统一只读状态契约沉淀下来。
- 文档覆盖 top-level shape、agents runtime binding、summary blocks、leader_actions、chat_turns、inbox、recovery、事件时间线和 GUI consumer rules。
- 明确 `recovery.recommended_action` 的状态矩阵、safety、requires_explicit_user、source 和 target_id 语义，避免未来 GUI 自行解析命令或散读 state。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，将 ProjectView 契约文档列为 GUI/recovery/自然语言入口改动的同步对象。
- 保持安全边界：本轮只新增文档契约，不改变 runtime 行为、不修改 state、不发送 tmux 输入。
- 完整验证：`conda run -n agentdeck pytest -q` 60 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 contract smoke 确认 ProjectView 顶层字段、`recovery` 字段和 `recommended_action` 字段均存在。

### Current - Add recovery state matrix target IDs

- 扩展 `status.recovery.recommended_action`，新增 `target_id` 字段，用于把 GUI 推荐动作关联回具体的 leader action、approval 或 inbox item。
- 补齐 recovery 状态矩阵测试，覆盖 `action_required`、`dispatch_ready`、`approval_required`、`inbox_pending` 和 `idle`。
- `dispatch_ready` 会指向 approved approval，`approval_required` 会指向 pending approval，`inbox_pending` 会指向 pending inbox head，`idle` 保持 `recommended_action=null`。
- 保持安全边界：target_id 只是只读关联元数据，不执行动作、不修改 state、不发送 tmux 输入。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 `recommended_action.target_id` 的 GUI 契约。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_status_includes_recovery_summary tests/test_agent_cli.py::test_status_recovery_matrix_for_gui_actions -q` 看到 `target_id` 缺失；实现后同一测试 2 项通过，ProjectView/Leader 相关测试 38 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 60 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `recommended_action.target_id` 与 `leader_action.action_id` 匹配且 `safety=safe_apply`。

### Current - Add recovery recommended action metadata

- 扩展 `agentdeck status` 的 `status.recovery`，新增 `recommended_action` 字段，为未来 GUI 提供可直接渲染的下一步动作元数据。
- `recommended_action` 包含 `label`、`command`、`safety`、`requires_explicit_user` 和 `source`，区分 safe apply、显式 runtime 动作和只读检查入口。
- 对 pending `create_approvals` leader action，recovery 会继续推荐安全的 `agentdeck leader apply-action --action-id <id>`，并标记 `safety=safe_apply`、`requires_explicit_user=false`。
- 对 dispatch、approval list 和 inbox inspect 状态，也统一生成 recommended_action，避免 GUI 自行推断命令安全语义。
- 保持安全边界：recommended_action 只描述动作，不执行动作、不修改 state、不发送 tmux 输入。
- 扩展 `tests/test_agent_cli.py`，先验证缺少 `recommended_action` 的红灯，再实现 recovery 动作元数据。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 recovery recommended_action 的 GUI 契约。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_status_includes_recovery_summary -q` 看到 `recommended_action` 缺失；实现后同一测试 1 项通过，ProjectView/Leader 相关测试 37 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 59 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `recovery.recommended_action` 返回 `label=Apply safe Leader action`、`safety=safe_apply`、`source=leader_action` 且 command 与 `next_command` 一致。

### Current - Route Leader chat through recovery summary

- 调整 `agentdeck leader chat --message <text>` 的 review 分支：持久化或复用 `leader_actions[]` 后重新读取 ProjectView，并把 `status.recovery` 作为自然语言继续的恢复决策源。
- review 输出新增 `recovery` 字段，`next_command` 改为来自 `recovery.next_command`；对于可安全应用的 `create_approvals` action，会推荐 `agentdeck leader apply-action --action-id <id>`。
- 持久化的 `chat_turns[].next_command` 同步记录 recovery 推荐命令，方便 `leader chat-history` 和 GUI 复原自然语言上下文。
- 保持安全边界：chat review 不创建 approval、不 dispatch、不发送 tmux 输入；runtime action 仍必须显式命令执行。
- 扩展 `tests/test_leader_cli.py`，先验证 chat review 缺少 `recovery` 的红灯，再实现 recovery 驱动的 next_command。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录自然语言入口必须以 `status.recovery` 为恢复决策源。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_persists_create_approvals_action_for_existing_plan -q` 看到 `KeyError: 'recovery'`；实现后同一测试 1 项通过，`tests/test_leader_cli.py` 35 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 59 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 chat review 的 `next_command == recovery.next_command`、`chat-history` 记录同一命令、`recovery_status=action_required`。

### Current - Add ProjectView recovery summary

- 扩展 `agentdeck status` 的 ProjectView，新增只读 `recovery` 区块，集中暴露当前恢复状态、原因、建议下一条命令、pending 计数、可应用 leader action 和最近审计事件摘要。
- `status.recovery` 优先使用 pending leader action 作为下一步，其次识别已批准待 dispatch、待审批和 pending inbox，最后返回 idle。
- 保持安全边界：recovery 只从 state/events 派生，不修改 state、不创建 event、不发送 tmux 输入。
- 扩展 `tests/test_agent_cli.py`，先验证缺少 `recovery` 的红灯，再实现 ProjectView recovery 契约。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，要求 GUI 和 Leader chat loop 优先用 `status.recovery` 判断“现在该继续什么”。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_status_includes_recovery_summary -q` 看到 `KeyError: 'recovery'`；实现后同一测试 1 项通过。
- 完整验证：`conda run -n agentdeck pytest tests/test_agent_cli.py::test_status_includes_project_state_summaries tests/test_agent_cli.py::test_status_includes_recovery_summary -q` 2 项通过，`conda run -n agentdeck pytest -q` 59 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `status.recovery.status=action_required`、`leader_action_kind=create_approvals`、`latest_event=leader_chat_turn`。

### Current - Add audit events tail command

- 新增只读命令 `agentdeck events [--limit <n>]`，读取 `.agentdeck/state/events.jsonl` 的最近事件，默认返回最近 20 条。
- 输出包含 `count`、`limit` 和 `events[]`，保持 JSONL 原始事件字段，适合 GUI 审计时间线、调试和恢复。
- `StateStore.list_events()` 支持事件文件不存在时返回空列表，`limit <= 0` 返回空列表。
- 保持安全边界：events 命令不修改 state、不创建 event、不发送 tmux 输入。
- 扩展 `tests/test_agent_cli.py`，覆盖事件 tail、limit 参数和缺失事件文件的空结果。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 events 作为只读审计入口。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_events_lists_recent_event_tail tests/test_agent_cli.py::test_events_returns_empty_list_when_log_is_missing -q` 看到 `events` 子命令不存在；实现后同一测试 2 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 58 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `agentdeck events --limit 2` 返回 `project_initialized` 和 `leader_plan_created`。

### Current - Add ProjectView chat turn action links

- 扩展 `agentdeck status` 的 ProjectView，`chat_turns.items[]` 现在包含 `action_id` 和 `action_kind`。
- GUI 或自然语言入口只读取 `agentdeck status` 时，可以从 review/apply chat turn 直接跳转到对应 `leader_actions[]` item。
- 保持 `agentdeck status` 只读，不修改 state、不创建 event、不发送 tmux 输入。
- 扩展 `tests/test_agent_cli.py`，覆盖 ProjectView chat turn 的 action_id/action_kind 摘要；复跑 `leader chat-history` 测试确认 CLI history 仍保留 action link。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 ProjectView chat_turns 到 action queue 的 GUI 契约。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_status_includes_project_state_summaries -q` 看到 `chat_turns.items` 缺少 action_id/action_kind；实现后同一测试与 chat-history 测试 2 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 56 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `status.chat_turns.items[0].action_id` 与 `status.leader_actions.items[0].action_id` 匹配，action_kind 为 `create_approvals`。

### Current - Add ProjectView Leader action details

- 扩展 `agentdeck status` 的 ProjectView，`leader_actions.items[]` 现在包含 `can_apply`、`apply_command`、`explicit_command` 和 `apply_blocker`。
- GUI 或自然语言入口只读取 `agentdeck status` 时，也能直接展示 safe apply 按钮、显式命令和 runtime action 阻塞原因。
- 抽出 `StateStore._leader_action_detail_fields()`，让 `leader action --action-id` 与 ProjectView 使用同一套可执行性判断，避免逻辑漂移。
- 保持 `agentdeck status` 只读，不修改 state、不创建 event、不发送 tmux 输入。
- 扩展 `tests/test_agent_cli.py`，覆盖 status leader_actions 的可执行性字段；复跑 leader action detail 测试确认单 action 详情不回退。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 ProjectView 中 leader action detail 的 GUI 契约。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_status_includes_project_state_summaries -q` 看到 `leader_actions.items` 缺少可执行性字段；实现后同一测试与 leader action detail 测试 3 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 56 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `agentdeck status` 的 `leader_actions.items[0]` 包含 `can_apply=True`、apply_command 和 `apply_blocker=None`。

### Current - Add safe Leader chat action apply

- 新增对话式显式确认入口：`agentdeck leader chat --message "apply action <action_id>"`，也支持 `/apply-action <action_id>` 和中文 `应用 action <action_id>`。
- chat apply 复用现有 `StateStore.apply_leader_action()` 安全白名单，当前只允许应用 `create_approvals`，会创建 approvals 并把 action 标记为 `applied`。
- dispatch/capture 等 runtime action 仍会被拒绝，并返回 `leader action requires explicit command: <action_id>`；不会发送 tmux 输入、不会创建 message/job。
- 成功 apply 会写入 `chat_turns[]` 的 `apply_action` turn，并追加 `leader_action_applied` 与 `leader_chat_turn` 事件。
- 扩展 `tests/test_leader_cli.py`，覆盖 chat apply create_approvals 成功，以及 chat apply dispatch_approved 被拒绝。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 chat apply 的明确格式和安全边界。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_applies_create_approvals_action_when_explicitly_requested tests/test_leader_cli.py::test_leader_chat_refuses_runtime_action_apply_request -q` 看到 chat 把 apply 文本当普通 review；实现后同一测试与原有 apply-action 安全边界测试 4 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 56 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `leader chat --message "apply action <id>"` 输出 `mode=apply_action`、action 标记 applied、创建 3 个 approvals，messages/jobs 仍为 0。

### Current - Return actionable Leader chat action detail

- `agentdeck leader chat --message <text>` 在 review 模式下返回的 `leader_action` 从轻量摘要升级为 action detail。
- `leader_action` 现在包含 `can_apply`、`apply_command`、`explicit_command` 和 `apply_blocker`，自然语言入口和未来 GUI 可以直接展示安全 apply 按钮或显式命令/阻塞原因。
- `create_approvals` action 会在 chat 输出中标记 `can_apply=True` 并给出 `agentdeck leader apply-action ...`；`dispatch_approved` 等 runtime action 会标记 `can_apply=False` 并保留人类显式命令。
- 保持安全边界：chat 仍然只 review 和持久化/复用 action，不创建 approval、不 dispatch、不发送 tmux 输入。
- 扩展 `tests/test_leader_cli.py`，覆盖 chat review 的 create_approvals 和 dispatch_approved action detail 字段。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 chat 输出的 `leader_action` 已可直接用于 GUI/对话层执行提示。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_reviews_latest_plan_instead_of_creating_another_plan tests/test_leader_cli.py::test_leader_chat_persists_create_approvals_action_for_existing_plan -q` 看到 `leader_action` 缺少 `can_apply`；实现后同一测试与 action detail 测试 4 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 54 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `leader chat --message "下一步"` 输出 `leader_action.kind=create_approvals`、`can_apply=True`、有 apply_command，且 approvals/messages/jobs 仍为 0。

### Current - Link Leader chat to action queue

- `agentdeck leader chat --message <text>` 在已有 plan 的 review 模式下，现在会调用 `suggest_leader_action()`，持久化或复用一条 `leader_actions[]` 建议。
- chat review 输出新增 `leader_action` 摘要，并继续保留 `next_command`，让自然语言入口、CLI 和未来 GUI 共享同一条可恢复 action queue。
- `chat_turns[]` 的 review turn 现在记录 `action_id` 和 `action_kind`，`agentdeck leader chat-history` 摘要也会返回这两个字段。
- 覆盖无 approvals 时的 `create_approvals` action，以及已有 approved approval 时的 `dispatch_approved` action；两者都不创建 approval、不 dispatch、不发送 tmux 输入。
- 扩展 `tests/test_leader_cli.py`，覆盖 chat review 返回 leader_action、chat_turn 记录 action_id/action_kind，以及已有 plan 但未审批时创建 create_approvals action。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录自然语言 chat review 与 action queue 的连接关系。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_reviews_latest_plan_instead_of_creating_another_plan tests/test_leader_cli.py::test_leader_chat_persists_create_approvals_action_for_existing_plan -q` 看到 payload 缺少 `leader_action`；实现后同一测试与 chat-history 测试 3 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 54 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `leader chat --message "下一步"` 输出 `leader_action.kind=create_approvals`，chat_turn action_id 与 leader_action 匹配，leader_actions 为 1，approvals/messages/jobs 仍为 0。

### Current - Deduplicate pending Leader next actions

- 将 `agentdeck leader next` 收紧为幂等 action suggestion：相同 pending action 已存在时复用原 action_id，不重复追加到 `leader_actions[]`。
- 去重 key 包含 kind、plan_id、approval_id、agent_id 和 message_id，确保同一 plan 阶段的 create_approvals、dispatch_approved、wait_for_reply 等建议不会污染 queue。
- 已 applied 或非 pending 的历史 action 不参与复用，后续阶段仍可生成新的 pending action。
- 保持安全边界：`leader next` 仍然只记录/复用 action，不创建 approval、不 dispatch、不发送 tmux 输入。
- 扩展 `tests/test_leader_cli.py`，覆盖重复 `create_approvals` 和重复 `dispatch_approved` 建议复用同一 action_id。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 `leader next` 的幂等队列语义。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_next_reuses_existing_pending_create_approvals_action tests/test_leader_cli.py::test_leader_next_reuses_existing_pending_dispatch_action -q` 看到重复调用生成不同 action_id；实现后同一测试与相邻 leader next/actions 测试 5 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 53 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认连续两次 `leader next` 返回相同 action_id，`leader_actions` 只有 1 条，且 approvals/messages/jobs 仍为 0。

### Current - Add ProjectView inbox heads

- 扩展 `agentdeck status` 的 ProjectView，`inbox` 摘要现在包含 `heads`，按 agent 暴露最早的 `pending` inbox item。
- `heads` 使用 head-only mailbox 语义：已 ack 历史 item 会被跳过，没有 pending item 的 agent 返回 `null`。
- head 摘要只包含 inbox_id、event_type、message_id、reply_id、from_actor/from_agent、to_agent、task、status 和 created_at 等 GUI/Leader 需要的轻量字段。
- 保持 `agentdeck status` 只读：不修改 state、不发送 tmux 输入、不创建 event。
- 扩展 `tests/test_agent_cli.py`，覆盖多 agent inbox 中 pending head、已 ack 历史 item 跳过，以及无 pending head 的 agent。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 `status.inbox.heads` 作为 GUI/Leader 判断 mailbox head 的默认入口。
- 本地验证：先运行 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_status_includes_project_state_summaries -q` 看到 `status.inbox` 缺少 `heads`；实现后同一测试通过。
- 完整验证：`conda run -n agentdeck pytest -q` 51 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `status.inbox.heads.planner.task` 为第一条 pending task，只有已 ack 历史项的 coder head 为 `null`。

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
