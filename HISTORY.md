# AgentDeck Development History

本文件记录 AgentDeck 每一次开发内容。约束：每次新增功能、文档规则、项目骨架、运行环境或用户可见行为变化，都必须同步更新本文件，并在同一次 commit 中提交。

## 2026-07-05

### Current - Suggest agent spawn through Leader chat

- 扩展 `agentdeck leader chat --message "启动 planner"` / `"spawn coder"`：自然语言入口现在会进入 `mode=runtime`，嵌入同源 `runtime_card`，并把 `next_command` 指向显式 `agentdeck agent spawn --agent <id>`。
- `leader_explanation` 和 `intent_card.controls[]` 会标记 `safety=explicit_runtime` 与 `requires_explicit_user=true`，让 GUI 或自然语言壳可以渲染启动按钮，但不会把启动当成自动执行。
- 保持安全边界：该模式只记录 chat turn，不创建 plan/action/approval/message/job/inbox，不执行 refresh/spawn/stop/capture，不读取 pane 输出，不发送 tmux 输入。
- 同步 README、`docs/contracts/leader-chat-schema.md`、CLAUDE.md、AGENT.md、capability card 和测试，让“查看 runtime”和“启动某个 agent”共享同一张 runtime card，但使用不同 safety。
- 完整验证：已先确认红测失败，`启动 planner` 最初误走 `mode=plan`；实现后发现并用红测捕获 `打开 planner inbox` 误进 `mode=runtime` 的路由风险，收窄 spawn 触发词后目标与回归测试通过；聚焦测试 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_suggests_agent_spawn_without_mutating_runtime tests/test_leader_cli.py::test_leader_chat_open_agent_inbox_does_not_trigger_spawn_intent tests/test_leader_cli.py::test_leader_chat_inspects_runtime_without_mutating_state tests/test_leader_cli.py::test_leader_chat_help_returns_capability_card_without_planning tests/test_leader_cli.py::test_leader_chat_inspects_agent_inbox_without_mutating_runtime tests/test_contracts.py::test_leader_chat_contract_response_includes_example_without_drift tests/test_agent_cli.py::test_contract_leader_chat_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_leader_chat_example_exports_gui_ready_response -q` 8 项通过；`conda run -n agentdeck pytest -q` 249 项通过；`conda run -n agentdeck python -m compileall src tests` 和 `git diff --check` 通过；临时项目 smoke 确认 `leader-chat-spawn-suggestion-smoke-ok mode=runtime next=agentdeck agent spawn --agent planner safety=explicit_runtime requires=True agents=0 messages=0 jobs=0`。

### Current - Block workbench dispatch operator when runtime is missing

- 扩展 `agentdeck workbench` 的 `operator_card`：当 recovery 指向 approved approval dispatch 且目标 agent 没有 running pane 时，卡片现在会暴露 `blocker=agent is not spawned: <agent>`，并禁用 explicit control。
- blocker 从同一份 ProjectView snapshot 的 approvals items 到 agents runtime 派生，不读取 tmux、不写 state、不执行 spawn/refresh/dispatch。
- 保持安全边界：workbench 仍不创建 chat turn、message、job、inbox，不 ack、不 approve、不 dispatch、不 capture reply、不读取 pane 输出、不发送 tmux 输入。
- 同步 `docs/contracts/workbench-schema.md`、README、CLAUDE.md、AGENT.md 和测试，保持 workbench 与 Leader chat dispatch preview 的 runtime 控制语义一致。
- 完整验证：已先确认红测失败，`operator_card.blocker` 最初为 `None`；实现后目标测试 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_workbench_blocks_dispatch_operator_when_approved_agent_is_not_spawned tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_agent_cli.py::test_workbench_surfaces_provider_setup_as_active_operator_source tests/test_agent_cli.py::test_workbench_surfaces_stale_runtime_as_active_operator_source -q` 4 项通过；聚焦契约测试 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_workbench_blocks_dispatch_operator_when_approved_agent_is_not_spawned tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_agent_cli.py::test_workbench_surfaces_provider_setup_as_active_operator_source tests/test_agent_cli.py::test_workbench_surfaces_stale_runtime_as_active_operator_source tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients -q` 6 项通过；`conda run -n agentdeck pytest -q` 247 项通过；`conda run -n agentdeck python -m compileall src tests` 和 `git diff --check` 通过；临时项目 smoke 确认 `workbench-dispatch-blocker-smoke-ok blocked_enabled=False blocked=agent is not spawned: planner ready_enabled=True pane=%42 messages=0 jobs=0`。

### Current - Block dispatch preview controls when runtime is missing

- 扩展 `dispatch_preview_card` 的 GUI 控制语义：当 approved approval 的目标 agent 没有 running pane 时，preview 仍展示显式 dispatch 命令和 blocker，但 `intent_card.controls[]` 的 next control 会 disabled，并复用同一个 blocker。
- 该行为避免自然语言壳或 GUI 把必然失败的 `agentdeck approval dispatch --approval-id <id>` 渲染成可确认按钮，同时仍保留命令和 blocker 供人类理解下一步要先 spawn/refresh runtime。
- 保持安全边界：本轮不执行 dispatch，不创建 message/job/inbox，不发送 tmux 输入，只调整 chat response 的可执行控制状态。
- 同步 README、`docs/contracts/leader-chat-schema.md`、CLAUDE.md、AGENT.md 和测试，记录 `dispatch_preview_card.blocker` 与 intent next control 的一致性。
- 完整验证：已先确认红测失败，未 spawn agent 时 `dispatch_preview_card.blocker=agent is not spawned: planner` 但 intent next control 仍 enabled；实现后目标测试 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_blocks_dispatch_preview_when_agent_is_not_spawned tests/test_leader_cli.py::test_leader_chat_suggests_dispatch_for_approved_approval_without_dispatching -q` 2 项通过；聚焦测试 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_blocks_dispatch_preview_when_agent_is_not_spawned tests/test_leader_cli.py::test_leader_chat_suggests_dispatch_for_approved_approval_without_dispatching tests/test_leader_cli.py::test_leader_chat_suggests_approve_for_pending_approval_without_approving tests/test_leader_cli.py::test_leader_chat_suggests_reject_for_pending_approval_without_rejecting tests/test_contracts.py::test_leader_chat_contract_payload_is_reusable_without_cli tests/test_contracts.py::test_leader_chat_contract_response_includes_example_without_drift tests/test_agent_cli.py::test_contract_leader_chat_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_leader_chat_example_exports_gui_ready_response -q` 8 项通过；`conda run -n agentdeck pytest -q` 246 项通过；`conda run -n agentdeck python -m compileall src tests` 和 `git diff --check` 通过；临时项目 smoke 确认 `leader-chat-dispatch-blocker-smoke-ok blocked_enabled=False blocked=agent is not spawned: planner ready_enabled=True pane=%42 messages=0 jobs=0`。

### Current - Preview approved dispatch through Leader chat

- 扩展 `agentdeck leader chat --message "派发当前审批"`：当存在 approved approval 时，approval-mode 响应现在会嵌入 `dispatch_preview_card`，作为 GUI-ready 的 explicit-runtime 执行前确认卡。
- `dispatch_preview_card` 暴露 approval_id、agent_id、agent_role、pane_id、runtime_status、task、dispatch_command、approval_command、inbox_command、requires_explicit_user、safety 和 blocker，让人类或 GUI 在真正派发前看到会发给谁、打到哪个 pane、执行后去哪里看 inbox。
- 保持安全边界：该模式仍只记录 chat turn，不执行 approve/reject/dispatch，不创建 message/job/inbox，不发送 tmux 输入；真正派发仍必须运行显式 `agentdeck approval dispatch --approval-id <id>`。
- 同步 `agentdeck contract leader-chat` 的 `dispatch_preview_card_fields`、validator、README、`docs/contracts/leader-chat-schema.md`、CLAUDE.md、AGENT.md 和测试，继续推进“ask/inspect -> explicit approve/dispatch”的控制梯度。
- 完整验证：已先确认红测失败，`派发当前审批` 最初缺少 `dispatch_preview_card`，contract discovery 最初缺少 `LEADER_CHAT_DISPATCH_PREVIEW_CARD_FIELDS`；实现后目标测试 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_suggests_dispatch_for_approved_approval_without_dispatching tests/test_contracts.py::test_leader_chat_contract_payload_is_reusable_without_cli tests/test_agent_cli.py::test_contract_leader_chat_discovers_schema_for_gui_clients -q` 3 项通过；聚焦测试 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_suggests_dispatch_for_approved_approval_without_dispatching tests/test_leader_cli.py::test_leader_chat_suggests_approve_for_pending_approval_without_approving tests/test_leader_cli.py::test_leader_chat_suggests_reject_for_pending_approval_without_rejecting tests/test_leader_cli.py::test_leader_chat_inspects_approval_queue_without_mutating_state tests/test_contracts.py::test_leader_chat_contract_payload_is_reusable_without_cli tests/test_contracts.py::test_leader_chat_contract_response_includes_example_without_drift tests/test_agent_cli.py::test_contract_leader_chat_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_leader_chat_example_exports_gui_ready_response -q` 8 项通过；`conda run -n agentdeck pytest -q` 245 项通过；`conda run -n agentdeck python -m compileall src tests` 和 `git diff --check` 通过；临时项目 smoke 确认 `leader-chat-dispatch-preview-smoke-ok mode=approval embedded=dispatch_preview_card approval=apv_f1afc7b39ca8 pane=%42 messages=0 jobs=0`。

### Current - Capture visible agent pane through Leader chat

- 扩展 `agentdeck leader chat --message "查看 planner 输出"` / `"capture planner output"`：自然语言入口现在会进入只读 `mode=capture`，读取已 spawn agent 的 visible tmux pane，并嵌入 GUI-ready `capture_card`。
- `capture_card` 包含 agent_id、pane_id、lines、capture_command 和 output；`intent_card.embedded_card=capture_card`，并建议同一条显式 `agentdeck agent capture --agent <id> --lines 200`。
- 新增未 spawn 保护：当请求的 agent 没有 runtime binding 时返回 `agent is not spawned: <agent_id>`，不会误落入 provider-backed planning，也不会创建 chat turn 或 plan。
- 保持安全边界：capture-mode 只记录 chat turn 和读取 pane 输出，不创建 plan/action/approval/message/job/inbox，不 ack、不 dispatch、不 capture reply、不发送 tmux 输入。
- 同步 `agentdeck contract leader-chat` 的 `capture_card_fields`、validator、README、`docs/contracts/leader-chat-schema.md`、CLAUDE.md、AGENT.md 和测试，让未来 GUI 可直接消费 pane snapshot。
- 完整验证：已先确认红测失败，`查看 planner 输出` 最初误走 `mode=plan`；实现后目标测试 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_captures_agent_output_as_read_only_card tests/test_leader_cli.py::test_leader_chat_rejects_capture_for_unspawned_agent_without_planning -q` 2 项通过；聚焦测试 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_captures_agent_output_as_read_only_card tests/test_leader_cli.py::test_leader_chat_rejects_capture_for_unspawned_agent_without_planning tests/test_leader_cli.py::test_leader_chat_inspects_runtime_without_mutating_state tests/test_contracts.py::test_leader_chat_contract_payload_is_reusable_without_cli tests/test_contracts.py::test_leader_chat_contract_response_includes_example_without_drift tests/test_agent_cli.py::test_contract_leader_chat_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_leader_chat_example_exports_gui_ready_response -q` 7 项通过；`conda run -n agentdeck pytest -q` 245 项通过；`conda run -n agentdeck python -m compileall src tests` 和 `git diff --check` 通过；临时项目 smoke 确认 `leader-chat-capture-smoke-ok mode=capture embedded=capture_card pane=%42 captured=[('%42', 200)] bad=agent is not spawned: coder`。

### Current - Route direct trace IDs through Leader chat

- 扩展 `agentdeck leader chat --message "追踪 msg_xxx"` / `"trace job_xxx"` / `"查看 rep_xxx 链路"`：自然语言入口现在会进入只读 `mode=trace`，嵌入同源 `trace_card`，并建议显式 `agentdeck trace --id <id>`。
- 新增 trace id 提取与保护边界：只在用户表达 trace/追踪/溯源/lineage/链路意图且包含 `msg_`、`att_`、`job_`、`rep_` 或 `inb_` ID 时触发；未知 trace id 会返回 `unknown trace id: <id>`，不会误落入 provider-backed planning。
- 保持安全边界：direct trace 只记录 chat turn 和只读 trace 证据，不创建 plan/action/approval/message/job/inbox，不 ack、不 dispatch、不 capture reply、不读取 tmux pane、不发送 tmux 输入。
- 同步 `docs/contracts/leader-chat-schema.md`、README、CLAUDE.md、AGENT.md 和测试，记录 direct trace mode 与 inbox trace mode 的差异。
- 完整验证：已先确认红测失败，`追踪 msg_trace_direct` 最初误走 `mode=plan`；实现后 direct trace 与 unknown trace id 目标测试 2 项通过；聚焦测试 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_traces_specific_communication_id_without_mutating_runtime tests/test_leader_cli.py::test_leader_chat_rejects_unknown_trace_id_without_planning tests/test_leader_cli.py::test_leader_chat_suggests_trace_for_current_inbox_head tests/test_contracts.py::test_leader_chat_contract_payload_is_reusable_without_cli tests/test_agent_cli.py::test_contract_leader_chat_discovers_schema_for_gui_clients -q` 5 项通过；`conda run -n agentdeck pytest -q` 243 项通过；`conda run -n agentdeck python -m compileall src tests` 和 `git diff --check` 通过；临时项目 smoke 确认 `leader-chat-direct-trace-smoke-ok mode=trace embedded=trace_card query=msg_smoke_direct message=msg_smoke_direct bad=unknown trace id: msg_missing`。

### Current - Embed trace card in Leader chat inbox trace mode

- 扩展 `agentdeck leader chat --message "追踪 planner 当前 inbox"`：inbox trace 意图现在仍保持只读 `mode=inbox`，但在能解析 pending head lineage 时会嵌入同源 `trace_card`。
- `trace_card` 复用 `agentdeck trace --id <id>` 的契约形状，包含 message、attempts、jobs、replies 和 inbox_items；`intent_card.embedded_card` 会优先指向 `trace_card`，让 GUI/自然语言壳直接展示通信证据链，而不是只显示 trace 命令。
- 同步 `agentdeck contract leader-chat` 的 `trace_card_fields` / trace nested fields、leader-chat validator、README、`docs/contracts/leader-chat-schema.md`、CLAUDE.md、AGENT.md 和测试。
- 保持安全边界：本轮只读嵌入 trace 证据，不创建 plan/action/approval/message/job/inbox，不 ack、不 dispatch、不 capture reply、不读取 tmux pane、不发送 tmux 输入。
- 完整验证：已先确认红测失败，inbox trace chat 最初缺少 `trace_card`，leader-chat contract 最初缺少 trace card 字段发现；实现后目标测试 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_suggests_trace_for_current_inbox_head tests/test_contracts.py::test_leader_chat_contract_payload_is_reusable_without_cli tests/test_contracts.py::test_leader_chat_contract_response_includes_example_without_drift tests/test_agent_cli.py::test_contract_leader_chat_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_leader_chat_example_exports_gui_ready_response -q` 5 项通过；`conda run -n agentdeck pytest -q` 241 项通过；`conda run -n agentdeck python -m compileall src tests` 和 `git diff --check` 通过；临时项目 smoke 确认 `leader-chat-trace-card-smoke-ok mode=inbox embedded=trace_card query=inb_smoke_trace message=msg_smoke_trace next=agentdeck trace --id inb_smoke_trace`。

### Current - Embed lineage card in Leader chat ledger mode

- 扩展 `agentdeck leader chat --message "查看账本"` / `"查看通信"`：ledger-mode 响应现在同时嵌入 workbench 同源的 `ledger_card` 和 `lineage_card`，让自然语言入口也能直接展示最近通信路径。
- `lineage_card` 继续保持只读投影：只复用 ProjectView / workbench 派生结果，不创建 plan/action/approval/message/job/inbox，不 ack、不 dispatch、不 capture reply、不读取 tmux pane、不发送 tmux 输入。
- 同步 `agentdeck contract leader-chat` 的 `lineage_card_fields` / `lineage_path_fields`、稳定 example、leader-chat validator、README、`docs/contracts/leader-chat-schema.md`、CLAUDE.md、AGENT.md 和测试。
- 完整验证：已先确认红测失败，ledger-mode chat 最初缺少 `lineage_card`，leader-chat contract example 最初缺少 lineage 字段发现；实现后目标测试 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_inspects_ledger_without_mutating_state tests/test_contracts.py::test_leader_chat_contract_response_includes_example_without_drift -q` 2 项通过；相关测试 `conda run -n agentdeck pytest tests/test_leader_cli.py tests/test_contracts.py::test_leader_chat_contract_response_includes_example_without_drift tests/test_agent_cli.py::test_contract_leader_chat_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_leader_chat_example_exports_gui_ready_response -q` 70 项通过；`conda run -n agentdeck pytest -q` 241 项通过；`conda run -n agentdeck python -m compileall src tests` 和 `git diff --check` 通过；临时项目 smoke 确认 `leader-chat-lineage-smoke-ok mode=ledger message_count=1 status=inbox_pending trace=agentdeck trace --id msg_chat_smoke`。

### Current - Surface communication lineage in workbench

- 扩展 `agentdeck workbench` 一屏快照：新增只读 `lineage_card`，从 ProjectView 的 messages/jobs/replies/inbox 摘要以及可见 inbox cards 派生最近通信路径，让 GUI 可以直接画出 Leader -> Worker -> Reply -> Inbox 的链路。
- `lineage_card.recent_paths[]` 保留 message/job/reply/inbox id、from_actor/to_agent/from_agent/to_actor、task、status 和 `trace_command`；它只是 ledger 的投影，不成为第二套通信账本，也不 ack、不 capture reply、不读取 tmux pane、不发送 tmux 输入。
- 同步 `agentdeck contract workbench` 的 `lineage_card_fields` / `lineage_path_fields`、稳定 workbench example、validator、README、`docs/contracts/workbench-schema.md`、CLAUDE.md、AGENT.md 和测试。
- 完整验证：已先确认红测失败，当前 workbench snapshot、contract payload/example 和 validator 最初缺少 `lineage_card` / `lineage_card_fields`；实现后目标测试 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_requires_lineage_card_fields -q` 4 项通过；相关测试 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_requires_lineage_card_fields tests/test_contracts.py::test_leader_chat_contract_response_includes_example_without_drift tests/test_agent_cli.py::test_contract_leader_chat_example_exports_gui_ready_response -q` 6 项通过；`conda run -n agentdeck pytest -q` 241 项通过；`conda run -n agentdeck python -m compileall src tests` 和 `git diff --check` 通过；临时项目 smoke 确认 `workbench-lineage-card-smoke-ok message_count=1 path_status=reply_pending_ack trace=agentdeck trace --id msg_smoke`。

### Current - Expose concrete control mode buttons

- 扩展 `control_mode_card.active_controls[]`：不再只暴露 `agentdeck policy set-mode --mode <mode>` 模板，而是直接给出 ask、approve、autonomous 三个具体 `set_mode` 控件，供 GUI/TUI 和自然语言壳渲染真实按钮。
- 明确按钮安全语义：当前模式会 disabled 并返回 `already current mode` blocker；`approve` 控件使用 `safety=explicit_user`；`autonomous` 控件继续 disabled，并保留 `autonomous execution policy is not implemented` blocker。`set_mode_command_template` 仅保留为表单式 UI 的辅助模板。
- 同步 `agentdeck controls` / `workbench` 的 command palette 语义、contract example、README、`docs/contracts/workbench-schema.md`、`docs/contracts/controls-schema.md`、CLAUDE.md、AGENT.md 和测试；本轮把“类似 Codex：可以 ask，也可以显式授权”的控制面从模板推进到可直接渲染的具体控件。
- 完整验证：已先确认红测失败，live workbench、controls 和 contract example 最初仍输出通用 `<mode>` 模板，缺少 ask/approve/autonomous 的具体按钮状态；实现后目标测试 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_agent_cli.py::test_policy_set_mode_updates_config_and_workbench_control_mode tests/test_agent_cli.py::test_controls_outputs_command_palette_without_mutating_state tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift -q` 4 项通过；相关测试 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_agent_cli.py::test_policy_set_mode_updates_config_and_workbench_control_mode tests/test_agent_cli.py::test_controls_outputs_command_palette_without_mutating_state tests/test_leader_cli.py::test_leader_chat_suggests_policy_mode_change_without_mutating_config tests/test_leader_cli.py::test_leader_chat_help_returns_capability_card_without_planning tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_controls_contract_response_includes_example_without_drift tests/test_contracts.py::test_leader_chat_contract_response_includes_example_without_drift -q` 8 项通过；`conda run -n agentdeck pytest -q` 240 项通过；`conda run -n agentdeck python -m compileall src tests` 和 `git diff --check` 通过；临时项目 smoke 确认 `control-mode-concrete-controls-smoke-ok policy_count=3 mode_after_switch=approve`。

### Current - Include policy controls in command palette

- 扩展 `agentdeck workbench` / `agentdeck controls` 的 `control_registry` 派生逻辑：现在会把 `control_mode_card.active_controls[]` 纳入全局命令面板，使用 `scope=policy`、`card=control_mode_card`，让 GUI/TUI 工具栏可以发现显式 `agentdeck policy set-mode --mode <mode>` 入口。
- 保持只读边界不变：`agentdeck controls` 仍只投影同一次 workbench snapshot，不写 state、不创建 chat turn、不调用 provider、不读取 pane、不执行任何 control。
- 同步 `docs/contracts/controls-schema.md`、`docs/contracts/workbench-schema.md`、README、CLAUDE.md、AGENT.md 和测试；本轮让 policy 控制模式入口同时出现在 workbench、controls 和 help-mode 嵌入 command palette 的同源索引里。
- 完整验证：已先确认红测失败，`agentdeck controls` 和 `workbench_example().control_registry` 最初都缺少 `scope=policy/card=control_mode_card/kind=set_mode`；实现后目标测试 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_controls_outputs_command_palette_without_mutating_state tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift -q` 2 项通过；相关测试 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_controls_outputs_command_palette_without_mutating_state tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_leader_cli.py::test_leader_chat_help_returns_capability_card_without_planning tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_controls_contract_response_includes_example_without_drift -q` 5 项通过；`conda run -n agentdeck pytest -q` 240 项通过；`conda run -n agentdeck python -m compileall src tests` 和 `git diff --check` 通过；临时项目 smoke 确认 `item_count=24`、`policy_count=2`，且 `set_mode_item.command=agentdeck policy set-mode --mode <mode>`、`safety=explicit_user`。

### Current - Route control mode through Leader chat

- 扩展 `agentdeck leader chat` 自然语言入口：`"切换到审批模式"`、`"回到 ask 模式"`、`"开启 autonomous"` 等控制模式意图现在进入 `mode=policy`，嵌入 workbench 同源 `control_mode_card`，并返回显式 `agentdeck policy set-mode --mode <mode>` 作为 `next_command`。
- 保持人类显式控制边界：policy chat 只记录 chat turn，不修改 `.agentdeck/config.toml`、不调用 provider、不创建 plan/action/approval/message/job/inbox、不发送 tmux 输入；`autonomous` 只作为会被策略命令拒绝的下一步建议。
- 扩展 `agentdeck contract leader-chat` 和 help `capability_card`：新增 `control_mode_card` 响应字段、control mode 字段 discovery、policy capability，以及 `<mode>` placeholder / `requires control mode` blocker，供 GUI 或自然语言壳渲染模式切换表单。
- 同步 README、`docs/contracts/leader-chat-schema.md`、CLAUDE.md、AGENT.md 和测试；本轮把“可以通过对话让 Leader 建议 ask/approve/autonomous，但不能绕过显式策略命令”的约束落到契约。
- 完整验证：已先确认红测失败，`切换到审批模式` 最初误路由到 `mode=approval`，`autonomous` 最初误路由到 `mode=plan`；实现后目标测试 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_suggests_policy_mode_change_without_mutating_config tests/test_leader_cli.py::test_leader_chat_suggests_autonomous_policy_command_but_keeps_it_blocked -q` 2 项通过；相关 help/contract 测试 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_suggests_policy_mode_change_without_mutating_config tests/test_leader_cli.py::test_leader_chat_suggests_autonomous_policy_command_but_keeps_it_blocked tests/test_leader_cli.py::test_leader_chat_help_returns_capability_card_without_planning -q` 3 项通过，`conda run -n agentdeck pytest tests/test_contracts.py::test_leader_chat_contract_payload_is_reusable_without_cli tests/test_contracts.py::test_leader_chat_contract_response_includes_example_without_drift tests/test_agent_cli.py::test_contract_leader_chat_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_leader_chat_example_exports_gui_ready_response -q` 4 项通过；`conda run -n agentdeck pytest tests/test_leader_cli.py tests/test_agent_cli.py tests/test_contracts.py -q` 225 项通过；`conda run -n agentdeck pytest -q` 240 项通过；`conda run -n agentdeck python -m compileall src tests` 和 `git diff --check` 通过；临时项目 smoke 确认 `chat_mode=policy`、`chat_next=agentdeck policy set-mode --mode approve`、`config_after_chat=confirm`、`config_after_policy=approve`、`autonomous_status=blocked`。

### Current - Add explicit control mode policy command

- 新增 `agentdeck policy set-mode --mode ask|approve|autonomous`：`ask` 会把 `leader.approval_mode` 写回 `confirm`，`approve` 会写回 `approve`，并在成功时追加 `policy_mode_updated` 审计事件。
- `autonomous` 仍保持未来能力占位：命令会返回失败、保持 `.agentdeck/config.toml` 不变，并追加 `policy_mode_rejected` 审计事件，避免“完全放权”在预算、allowlist 和审计门完成前偷跑。
- 更新 `control_mode_card.active_controls[]` 和 contract example，把 `agentdeck policy set-mode --mode <mode>` 暴露为已实现但仍需显式用户触发的策略入口；`agentdeck workbench` 继续保持只读，不自动切换策略、不 ack、不 approve、不 dispatch、不发送 tmux 输入。
- 同步 README、`docs/contracts/workbench-schema.md`、CLAUDE.md、AGENT.md 和测试；本轮把“类似 Codex：可以 ask，也可以显式授权”的产品约束落到真实 CLI、配置和审计账本上。
- 完整验证：已先确认红测失败，`policy` 顶层命令最初不存在；实现后目标测试 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_policy_set_mode_updates_config_and_workbench_control_mode tests/test_agent_cli.py::test_policy_set_mode_rejects_autonomous_without_mutating_config -q` 2 项通过；相关测试 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_policy_set_mode_updates_config_and_workbench_control_mode tests/test_agent_cli.py::test_policy_set_mode_rejects_autonomous_without_mutating_config tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift -q` 4 项通过；`conda run -n agentdeck pytest tests/test_agent_cli.py tests/test_contracts.py -q` 158 项通过；`conda run -n agentdeck pytest -q` 238 项通过；`conda run -n agentdeck python -m compileall src tests` 和 `git diff --check` 通过；临时项目 smoke 确认 `approve_payload_mode=approve`、`workbench_mode=approve`、`ask_config_restored=true`、`autonomous_exit=1`，并记录 `policy_mode_updated` / `policy_mode_rejected`。

### Current - Surface control mode in workbench

- 扩展 `agentdeck workbench` 一屏快照：新增只读 `control_mode_card`，把类似 Codex 的 ask/approve/autonomous 控制梯度显式暴露给 GUI/TUI 和自然语言壳。
- 当前默认从 `leader.approval_mode=confirm` 映射为 `current_mode=ask`，只允许计划、观察和建议命令；`approve` 描述已有审批 gated safe apply 路径；`autonomous` 作为未来带预算、allowlist、审计门的放权模式占位并保持 disabled。
- 保持安全边界不变：本轮不新增 `policy set-mode` 实现，不自动修改策略、不 ack、不 approve、不 dispatch、不发送 tmux 输入。
- 同步 `agentdeck contract workbench`、`docs/contracts/workbench-schema.md`、README、CLAUDE.md、AGENT.md 和测试；本轮把“可以 ask，也可以授权放权”的产品语义先落到可消费 contract 上。
- 完整验证：已先确认红测失败，`WORKBENCH_CONTROL_MODE_*` 字段常量和 workbench card 最初不存在；实现后目标测试 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_requires_control_mode_fields -q` 4 项通过；`conda run -n agentdeck pytest tests/test_agent_cli.py tests/test_contracts.py -q` 156 项通过；`conda run -n agentdeck pytest -q` 236 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时项目 in-process smoke 确认 `current_mode=ask`、`autonomous_enabled=false`，且 `agentdeck workbench` 不修改 state。

### Current - Surface Leader inbox in workbench

- 扩展 `agentdeck workbench` 一屏快照：新增固定 `leader_inbox_card`，始终复用 `agentdeck inbox --agent leader` 队列形状，让 GUI/TUI 或自然语言壳直接看到 worker 回流给 Leader 的 `task_reply`、trace 和 ack 入口。
- 保持 `inbox_card` 的 recovery-driven 语义不变；`leader_inbox_card` 只是同源 Leader mailbox 投影，不成为第二套 inbox 状态源。
- 同步 `agentdeck contract workbench`、`docs/contracts/workbench-schema.md`、README、CLAUDE.md、AGENT.md 和测试；本轮推进“worker 输出 -> Leader inbox -> workbench 一屏可见 -> 可恢复 review”的北极星主线。
- 结合用户反馈，README 明确控制模式应保持类似 Codex 的梯度：默认 ask/inspect，由人类显式审批或授权后再进入更高自治度执行。
- 完整验证：已先确认红测失败，workbench snapshot、contract payload/example 和 validator 最初缺少 `leader_inbox_card`；实现后目标测试 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_leader_inbox_card_when_worker_reply_returns_to_leader tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_requires_leader_inbox_card_contract -q` 4 项通过；`conda run -n agentdeck pytest tests/test_agent_cli.py tests/test_contracts.py -q` 155 项通过；`conda run -n agentdeck pytest -q` 235 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时项目 in-process smoke 确认 `leader_inbox_count=1`、`event_type=task_reply`，且仅显式 dispatch 触发 1 次 tmux send。

### Current - Embed inbox card after agent reply

- 扩展 `agentdeck reply` 和 `agentdeck capture-reply` 成功响应：当 worker reply 回流到某个 agent inbox（例如 `leader`）时，同步嵌入接收方 `inbox_card`，让 GUI/TUI 或自然语言壳立即看到 `task_reply`、trace 和 ack 入口。
- `inbox_card` 复用 `agentdeck inbox --agent <id>` 队列形状，并通过 `validate_inbox_contract()` 校验，不成为第二套 inbox 状态源。
- 保持安全边界不变：reply/capture-reply 只记录 reply 与回流 inbox，不自动 ack、不继续 review、不发送 tmux 输入。
- 同步 README、CLAUDE.md、AGENT.md 和测试；本轮推进了“worker 输出 -> Leader/发起方 inbox -> 可恢复 review”的北极星主线。
- 完整验证：已先确认红测失败，`reply` 成功响应最初缺少接收方 `inbox_card`；实现后目标测试 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_review_summarizes_when_all_dispatched_steps_have_replies -q` 1 项通过；`conda run -n agentdeck pytest tests/test_leader_cli.py -q` 65 项通过；`conda run -n agentdeck pytest -q` 233 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时项目 in-process smoke 确认 `task_reply` 回流到 `leader` inbox，`inbox_count=1`，且仅显式 dispatch 触发 1 次 tmux send。

### Current - Embed inbox card after approval dispatch

- 扩展 `agentdeck approval dispatch --approval-id <id>` 成功响应：除 `trace_command` 外，现在同步嵌入目标 agent 的 `inbox_card`，让 GUI/TUI 或自然语言壳立即看到 worker mailbox head、trace 和 ack 入口。
- `inbox_card` 复用 `agentdeck inbox --agent <id>` 队列形状，并通过 `validate_inbox_contract()` 校验，不成为第二套 inbox 状态源。
- 保持安全边界不变：`approval dispatch` 仍是单个显式 runtime 命令，不会自动连续派发 plan、不会 ack inbox、不会 capture reply。
- 同步 `docs/contracts/approvals-schema.md`、README、CLAUDE.md、AGENT.md 和测试；本轮推进了“审批通过 -> 可见 tmux runtime dispatch -> mailbox/trace 可恢复”的北极星主线。
- 完整验证：已先确认红测失败，`approval dispatch` 成功响应最初缺少 `inbox_card`；实现后目标测试 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_approval_dispatch_sends_approved_step_to_agent_and_records_lineage -q` 1 项通过；`conda run -n agentdeck pytest tests/test_leader_cli.py -q` 65 项通过；`conda run -n agentdeck pytest -q` 233 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时项目 in-process smoke 确认 `approval-dispatch-inbox-card-smoke-ok`。

### Current - Embed approval card after chat safe apply

- 扩展 `agentdeck leader chat --message "apply action <id>"` 的 safe apply 响应：应用 `create_approvals` 后，同一响应现在会嵌入同源 `approval_card`，让 GUI/TUI 或自然语言壳立即展示刚创建的人类审批队列。
- 保持安全边界不变：该模式仍只允许 safe `create_approvals`，不会自动 approve、reject、dispatch、capture reply、发送 tmux 输入或扩大 runtime action 白名单。
- 同步 `docs/contracts/leader-chat-schema.md`、README、CLAUDE.md、AGENT.md 和测试；`approval_card` 继续复用 `agentdeck approval list` 队列契约和 `validate_approval_contract()`，不成为第二套 approval 状态源。
- 完整验证：已先确认红测失败，chat safe apply 响应最初的 `approval_card` 为 `None`；实现后目标测试 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_applies_create_approvals_action_when_explicitly_requested -q` 1 项通过；`conda run -n agentdeck pytest tests/test_leader_cli.py -q` 65 项通过；`conda run -n agentdeck pytest -q` 233 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `chat-safe-apply-approval-card-smoke-ok`。

### Current - Add Leader action card to chat responses

- 扩展 `agentdeck leader chat` 响应：当响应包含顶层 `leader_action` 时，同步派生 `leader_action_card`，暴露 mode/title/action_id/kind/status/reason/preview_command/can_apply/apply_command/explicit_command/apply_blocker/controls，供 GUI/TUI 或自然语言壳直接渲染下一步动作卡。
- 保持 `leader_action_card` 从同一个 action detail 派生，不成为第二套 action 状态源；validator 要求有 `leader_action` 时必须有 `leader_action_card`，且 `leader_action_card.action_id` 与 `leader_action.action_id` 对齐，并校验 controls 字段。
- 同步 `agentdeck contract leader-chat` 的 `leader_action_card_fields`、example 字段、`docs/contracts/leader-chat-schema.md`、README、CLAUDE.md、AGENT.md 和测试；该卡片只描述预览/应用入口，不新增 dispatch、approval、tmux 发送或 provider 调用路径。
- 完整验证：已先确认红测失败，live chat 响应最初缺少 `leader_action_card`，leader-chat contract payload/example 最初缺少 `leader_action_card_fields` / `example_leader_action_card_fields`，validator 最初放过 `leader_action_card=null`；实现后目标测试 `conda run -n agentdeck pytest tests/test_contracts.py::test_validate_leader_chat_contract_requires_action_card_when_action_is_present tests/test_leader_cli.py::test_leader_chat_creates_plan_from_natural_language_without_dispatching tests/test_contracts.py::test_leader_chat_contract_payload_is_reusable_without_cli tests/test_contracts.py::test_leader_chat_contract_response_includes_example_without_drift -q` 4 项通过；相关测试 `conda run -n agentdeck pytest tests/test_leader_cli.py tests/test_contracts.py -q` 156 项通过；`conda run -n agentdeck pytest -q` 233 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `leader-action-card-smoke-ok`。

### Current - Point command palette default to controls

- 调整 `control_registry_card.default_command`：从 `agentdeck workbench` 改为独立入口 `agentdeck controls`，让 GUI/TUI 或自然语言壳可以直接刷新命令面板。
- 保持 `source_command=agentdeck workbench`，明确命令面板仍从同一次 workbench snapshot 派生，不成为第二套 control 状态源。
- 同步 `docs/contracts/controls-schema.md`、`docs/contracts/leader-chat-schema.md`、README、CLAUDE.md、AGENT.md 和测试；该调整不改变任何 control 执行路径，不写 state、不调用 provider、不读取 pane。
- 完整验证：已先确认红测失败，live `agentdeck controls`、controls contract example 和 Leader help 嵌入 `control_registry_card` 的 `default_command` 最初仍为 `agentdeck workbench`；实现后目标测试 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_controls_outputs_command_palette_without_mutating_state tests/test_contracts.py::test_controls_contract_response_includes_example_without_drift tests/test_leader_cli.py::test_leader_chat_help_returns_capability_card_without_planning -q` 3 项通过；相关测试 `conda run -n agentdeck pytest tests/test_agent_cli.py tests/test_contracts.py tests/test_leader_cli.py -q` 217 项通过；`conda run -n agentdeck pytest -q` 232 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `controls-default-command-smoke-ok`。

### Current - Surface controls contract in workbench

- 扩展 `workbench.contracts_card`：新增 `controls_contract=agentdeck contract controls`，让 GUI/TUI 从一屏工作台直接发现独立命令面板契约。
- 同步 `WORKBENCH_CONTRACTS_CARD_FIELDS`、live `_workbench_contracts_card()`、workbench example、`docs/contracts/workbench-schema.md`、README、CLAUDE.md、AGENT.md 和测试；该字段只做 contract discovery，不读取 state、不执行 contract 命令、不触发 control。
- 完整验证：已先确认红测失败，workbench contract fields、live `contracts_card` 和 workbench example 最初缺少 `controls_contract`；实现后目标测试 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift -q` 3 项通过；相关测试 `conda run -n agentdeck pytest tests/test_agent_cli.py tests/test_contracts.py tests/test_leader_cli.py -q` 217 项通过；`conda run -n agentdeck pytest -q` 232 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `workbench-controls-contract-smoke-ok`。

### Current - Add controls contract discovery

- 新增 `agentdeck contract controls` / `--example`，公开独立命令面板 `control_registry_card` 字段和 `control_registry_item_fields`，让 GUI/TUI 能通过 contract index 发现 `agentdeck controls` 的稳定响应形状。
- 将 `controls` 纳入 `agentdeck contract list`，新增 `docs/contracts/controls-schema.md`，并将通用 `CONTROL_REGISTRY_CARD_FIELDS` 作为 Leader chat 嵌入卡片和独立 controls contract 的共享字段来源。
- 同步 README、CLAUDE.md、AGENT.md 和测试；该 contract 只做 discovery，不读取 live state、不调用 provider、不读取 pane、不执行任何 control。
- 完整验证：已先确认红测失败，`CONTROL_REGISTRY_CARD_FIELDS`、`controls_contract_payload()` 和 `agentdeck contract controls` 最初不存在；实现后目标测试 `conda run -n agentdeck pytest tests/test_contracts.py::test_contract_index_response_is_reusable_without_cli tests/test_contracts.py::test_controls_contract_payload_is_reusable_without_cli tests/test_contracts.py::test_controls_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_control_registry_card_contract_accepts_example tests/test_agent_cli.py::test_contract_list_discovers_all_gui_contracts tests/test_agent_cli.py::test_contract_controls_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_controls_example_exports_gui_ready_response -q` 7 项通过；相关测试 `conda run -n agentdeck pytest tests/test_contracts.py tests/test_agent_cli.py tests/test_leader_cli.py -q` 217 项通过；`conda run -n agentdeck pytest -q` 232 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `controls-contract-smoke-ok`。

### Current - Add standalone controls command

- 新增 `agentdeck controls`：从同一次 `agentdeck workbench` snapshot 派生 `control_registry_card`，为 GUI/TUI 或自然语言壳提供独立只读命令面板入口。
- 新增 `validate_control_registry_card_contract()`，让独立 controls 输出复用 Leader chat `control_registry_card` 字段和 workbench `control_registry` item 字段校验，避免出现第二套 control 状态源。
- 同步 README、CLAUDE.md、AGENT.md 和测试；该命令只输出 leader/runtime/operator controls 的 scope/card/kind/label/command/safety/enabled/blocker/agent_id，不写 state、不创建 chat turn、不调用 provider、不读取 pane、不执行任何 control。
- 完整验证：已先确认红测失败，`agentdeck controls` 最初不是合法子命令；实现后目标测试 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_controls_outputs_command_palette_without_mutating_state -q` 1 项通过；相关测试 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_controls_outputs_command_palette_without_mutating_state tests/test_leader_cli.py::test_leader_chat_help_returns_capability_card_without_planning tests/test_contracts.py::test_leader_chat_contract_payload_is_reusable_without_cli -q` 3 项通过；相关套件 `conda run -n agentdeck pytest tests/test_agent_cli.py tests/test_contracts.py tests/test_leader_cli.py -q` 212 项通过；`conda run -n agentdeck pytest -q` 227 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `controls-command-smoke-ok`。

### Current - Add Leader chat control registry card

- 扩展 `agentdeck leader chat --message "帮助"` / `"命令面板"`：help mode 现在除 `capability_card` 外，还返回 `control_registry_card`，从同一次 workbench snapshot 派生 leader/runtime/operator controls，供 GUI 或自然语言壳直接渲染命令面板。
- 新增 `LEADER_CHAT_CONTROL_REGISTRY_CARD_FIELDS`、`control_registry_card_fields`、`example_control_registry_card_fields` 和 validator，要求 `item_count` 与 `items[]` 长度一致，并复用 workbench control registry item 字段。
- 同步 `docs/contracts/leader-chat-schema.md`、README、CLAUDE.md、AGENT.md 和测试；该 card 只描述命令入口，不新增执行路径，不调用 provider，不读取 pane，不发送 tmux 输入。
- 完整验证：已先确认红测失败，help payload 和 leader-chat contract 最初缺少 `control_registry_card` / `control_registry_card_fields`；实现后目标测试 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_help_returns_capability_card_without_planning tests/test_contracts.py::test_leader_chat_contract_payload_is_reusable_without_cli tests/test_contracts.py::test_leader_chat_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_leader_chat_contract_requires_control_registry_card_count tests/test_agent_cli.py::test_contract_leader_chat_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_leader_chat_example_exports_gui_ready_response -q` 6 项通过；相关测试 `conda run -n agentdeck pytest tests/test_contracts.py tests/test_agent_cli.py tests/test_leader_cli.py -q` 211 项通过；`conda run -n agentdeck pytest -q` 226 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `leader-chat-control-registry-card-smoke-ok`。

### Current - Surface workbench control registry in Leader chat contract

- 扩展 `agentdeck contract leader-chat`：新增 `workbench_control_registry_item_fields`，让 GUI/自然语言壳在只读取 Leader chat contract 时也能发现嵌入 `workbench_card.control_registry[]` 的命令面板 item 字段。
- `agentdeck contract leader-chat --example` 新增 `example_workbench_control_registry_item_fields`，并与稳定 `example_leader_chat.workbench_card.control_registry[0]` 对齐。
- 同步 `docs/contracts/leader-chat-schema.md`、README、CLAUDE.md、AGENT.md 和测试；该 discovery 只描述字段，不新增执行路径，不改变 workbench/Leader chat 的只读边界。
- 完整验证：已先确认红测失败，`workbench_control_registry_item_fields` / `example_workbench_control_registry_item_fields` 最初不存在；实现后目标测试 `conda run -n agentdeck pytest tests/test_contracts.py::test_leader_chat_contract_payload_is_reusable_without_cli tests/test_contracts.py::test_leader_chat_contract_response_includes_example_without_drift tests/test_agent_cli.py::test_contract_leader_chat_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_leader_chat_example_exports_gui_ready_response -q` 4 项通过；相关测试 `conda run -n agentdeck pytest tests/test_contracts.py tests/test_agent_cli.py tests/test_leader_cli.py -q` 210 项通过；`conda run -n agentdeck pytest -q` 225 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `leader-chat-workbench-control-registry-contract-smoke-ok`。

### Current - Add workbench control registry

- 扩展 `agentdeck workbench`：新增只读 `control_registry[]`，把 `leader_card.controls[]`、`runtime_card.agents[].controls[]` 和 `operator_card.controls[]` 汇总成 GUI/TUI 可直接渲染的命令面板索引。
- 每个 registry item 保留 `scope`、`card`、`kind`、`label`、`command`、`safety`、`enabled`、`blocker`、`agent_id`，不新增执行路径，不绕过原 control 的 safety/blocker，也不成为第二套状态源。
- `agentdeck contract workbench` 新增 `control_registry_item_fields`，并同步 workbench example、live `_workbench_snapshot_payload()`、validator、README、workbench schema、CLAUDE.md、AGENT.md 和测试。
- 完整验证：已先确认红测失败，`WORKBENCH_CONTROL_REGISTRY_ITEM_FIELDS` 最初不存在；实现后目标测试 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_requires_control_registry_item_fields -q` 4 项通过；修复 Leader chat workbench 嵌入时发现 idle operator explicit control disabled 却缺 blocker，并用 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_opens_workbench_snapshot_without_mutating_state tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_requires_control_registry_item_fields -q` 覆盖，5 项通过；相关测试 `conda run -n agentdeck pytest tests/test_agent_cli.py tests/test_contracts.py tests/test_leader_cli.py -q` 210 项通过；`conda run -n agentdeck pytest -q` 225 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `workbench-control-registry-smoke-ok`。

### Current - Add Leader controls to workbench leader card

- 扩展 workbench `leader_card`：新增 GUI-ready `controls[]`，覆盖 chat、continue、review、actions、status 五类 Leader 入口。
- `chat` 与 `review` controls 是带 `<text>` / `<plan_id>` 的模板命令，默认 disabled 并返回 blocker，避免 GUI 直接运行缺参命令；continue/actions/status 保持只读 inspect controls。
- `agentdeck contract workbench` 新增 `leader_control_fields`，并同步 workbench example、live `_workbench_leader_card()`、validator、README、workbench schema、CLAUDE.md、AGENT.md 和测试。
- 完整验证：已先确认红测失败，`WORKBENCH_LEADER_CONTROL_FIELDS` 最初不存在；实现后目标测试 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_requires_leader_control_fields -q` 4 项通过；相关测试 `conda run -n agentdeck pytest tests/test_agent_cli.py tests/test_contracts.py -q` 144 项通过；`conda run -n agentdeck pytest -q` 224 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `workbench-leader-controls-smoke-ok`。

### Current - Add Leader review template to workbench leader card

- 扩展 workbench `leader_card`：新增 `review_command_template=agentdeck leader review --plan-id <plan_id>`，让 GUI/TUI 能从 Leader 主卡直接渲染 plan review 入口模板。
- 同步 `WORKBENCH_LEADER_CARD_FIELDS`、workbench example、live `_workbench_leader_card()`、workbench schema、README、CLAUDE.md、AGENT.md 和测试。
- 完整验证：已先确认红测失败，workbench leader_card 最初缺少 `review_command_template`；实现后目标测试 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift -q` 3 项通过；相关测试 `conda run -n agentdeck pytest tests/test_agent_cli.py tests/test_contracts.py -q` 143 项通过；`conda run -n agentdeck pytest -q` 223 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `workbench-leader-review-template-smoke-ok`。

### Current - Surface Leader review contract in doctor

- 扩展 `agentdeck contract doctor` discovery payload：新增 `leader_review_contract=agentdeck contract leader-review`，让 GUI setup/diagnostics 页面能从 doctor schema 直接发现 Leader review 契约。
- 同步 `docs/contracts/doctor-schema.md`、README、CLAUDE.md、AGENT.md 和测试。
- 完整验证：已先确认红测失败，doctor contract 最初缺少 `leader_review_contract`；实现后目标测试 `conda run -n agentdeck pytest tests/test_contracts.py::test_doctor_contract_payload_is_reusable_without_cli tests/test_agent_cli.py::test_contract_doctor_discovers_schema_for_gui_clients -q` 2 项通过；相关测试 `conda run -n agentdeck pytest tests/test_agent_cli.py tests/test_contracts.py -q` 143 项通过；`conda run -n agentdeck pytest -q` 223 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `doctor-leader-review-contract-smoke-ok`。

### Current - Surface Leader review contract in workbench

- 扩展 `workbench.contracts_card`：新增 `leader_review_contract=agentdeck contract leader-review`，让 GUI/TUI 从一屏工作台直接发现 Leader review 响应契约。
- 同步 `WORKBENCH_CONTRACTS_CARD_FIELDS`、workbench example、live `_workbench_contracts_card()`、workbench schema、README、CLAUDE.md、AGENT.md 和测试。
- 完整验证：已先确认红测失败，workbench contracts_card 最初缺少 `leader_review_contract`；实现后目标测试 `conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift -q` 3 项通过；相关测试 `conda run -n agentdeck pytest tests/test_agent_cli.py tests/test_contracts.py -q` 143 项通过；`conda run -n agentdeck pytest -q` 223 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `workbench-leader-review-contract-smoke-ok`。

### Current - Validate Leader review live output

- 新增 `validate_leader_review_contract(payload)`，校验 `agentdeck leader review --plan-id <id>` 的必备 response 字段、`controls[]` 字段、`enabled` 布尔值，以及 `wait_for_reply` 的 `capture_reply` control 安全语义。
- `agentdeck leader review` 现在输出 JSON 前会通过 Leader review contract 自校验；校验失败时返回非 0、stderr 输出错误，且不打印半坏 review JSON。
- 同步 `README.md`、`docs/contracts/leader-review-schema.md`、`CLAUDE.md`、`AGENT.md` 和测试。
- 完整验证：已先确认红测失败，`validate_leader_review_contract` 最初不存在；实现后目标测试 `conda run -n agentdeck pytest tests/test_contracts.py::test_validate_leader_review_contract_accepts_example tests/test_contracts.py::test_validate_leader_review_contract_requires_response_and_control_fields tests/test_contracts.py::test_validate_leader_review_contract_rejects_non_list_controls tests/test_leader_cli.py::test_leader_review_refuses_contract_violation -q` 4 项通过；contract/leader 相关测试 `conda run -n agentdeck pytest tests/test_contracts.py tests/test_leader_cli.py -q` 149 项通过；`conda run -n agentdeck pytest -q` 223 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `leader-review-validator-smoke-ok`。

### Current - Add Leader review contract discovery

- 新增 `agentdeck contract leader-review` / `--example`，公开 `agentdeck leader review --plan-id <id>` 的 `response_fields` 和 `control_fields`，让 GUI/TUI 能发现 review `next_command` 与 `controls[]` 的形状。
- 将 `leader-review` 纳入 contract index，并新增 `docs/contracts/leader-review-schema.md`。
- 同步 `README.md`、`CLAUDE.md`、`AGENT.md` 和测试。
- 完整验证：已先确认红测失败，leader-review contract helper/CLI 子命令最初不存在；实现后目标测试 `conda run -n agentdeck pytest tests/test_contracts.py::test_contract_index_response_is_reusable_without_cli tests/test_contracts.py::test_leader_review_contract_payload_is_reusable_without_cli tests/test_contracts.py::test_leader_review_contract_response_includes_example_without_drift tests/test_agent_cli.py::test_contract_leader_review_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_leader_review_example_exports_gui_ready_response -q` 5 项通过；相关测试 `conda run -n agentdeck pytest tests/test_agent_cli.py tests/test_contracts.py tests/test_leader_cli.py -q` 204 项通过；`conda run -n agentdeck pytest -q` 219 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `leader-review-contract-smoke-ok`。

### Current - Add Leader review controls for reply capture

- 扩展 `agentdeck leader review --plan-id <id>` 输出：新增 `next_command` 和 GUI-ready `controls[]`，让 GUI/自然语言壳能直接渲染 review 建议而不必解析 `next_action`。
- `wait_for_reply` 现在会暴露只读 trace preview control 和 `capture_reply` control，命令为 `agentdeck capture-reply --agent <id> --message-id <id>`，标记 `safety=explicit_runtime`，但 review 本身仍不 capture pane、不写 reply、不创建 leader action。
- `summarize` 路径会暴露只读 `agentdeck plan status --plan-id <id>` next control。
- 同步 `README.md`、`CLAUDE.md`、`AGENT.md` 和测试。
- 完整验证：已先确认红测失败，leader review wait_for_reply 最初缺少 `next_command`；实现后目标测试 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_review_recommends_waiting_for_dispatched_reply tests/test_leader_cli.py::test_leader_review_summarizes_when_all_dispatched_steps_have_replies -q` 2 项通过；leader/dispatch 相关测试 `conda run -n agentdeck pytest tests/test_leader_cli.py tests/test_dispatch_cli.py -q` 75 项通过；`conda run -n agentdeck pytest -q` 215 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `leader-review-controls-smoke-ok` 且 review 未写 reply/leader action。

### Current - Guard Leader intent placeholder controls

- 加强 `intent_card.controls[]`：当 `next_command` 仍包含 `<reason>` 这类模板输入时，next control 会 disabled，并返回 `requires reason` blocker，避免 GUI 把缺参命令渲染成可直接执行按钮。
- 加强 `validate_leader_chat_contract()`：拒绝 enabled placeholder intent control，并要求 disabled placeholder control 的 blocker 与模板输入匹配。
- 同步 `README.md`、`docs/contracts/leader-chat-schema.md`、`CLAUDE.md`、`AGENT.md` 和测试。
- 完整验证：已先确认红测失败，reject chat 的 intent next control 最初 enabled 且 blocker 为 null，validator 最初允许 enabled placeholder intent control；实现后目标测试 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_suggests_reject_for_pending_approval_without_rejecting tests/test_contracts.py::test_validate_leader_chat_contract_requires_placeholder_intent_controls_disabled tests/test_contracts.py::test_validate_leader_chat_contract_requires_placeholder_intent_blocker_match -q` 3 项通过；leader/contract 相关测试 `conda run -n agentdeck pytest tests/test_leader_cli.py tests/test_contracts.py -q` 143 项通过；`conda run -n agentdeck pytest -q` 215 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `leader-chat-intent-placeholder-control-smoke-ok` 且 approval 仍为 pending。

### Current - Route approval reject chat to explicit command

- 扩展自然语言 approval 模式：`agentdeck leader chat --message "拒绝当前审批"` 会推荐第一条 pending approval 的 `reject_command`，并标记 `action_kind=approval_reject`、`safety=explicit_runtime`、`requires_explicit_user=true`。
- 保持人类审批边界：该 chat turn 只记录建议和 next_command，不修改 approval 状态、不创建 leader action、不 dispatch、不发送 tmux 输入。
- 同步 `README.md`、`docs/contracts/leader-chat-schema.md`、`CLAUDE.md`、`AGENT.md` 和测试。
- 完整验证：已先确认红测失败，approval chat 最初仍返回 `agentdeck approval list`；实现后 approval chat 目标测试 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_inspects_approval_queue_without_mutating_state tests/test_leader_cli.py::test_leader_chat_suggests_approve_for_pending_approval_without_approving tests/test_leader_cli.py::test_leader_chat_suggests_reject_for_pending_approval_without_rejecting tests/test_leader_cli.py::test_leader_chat_suggests_dispatch_for_approved_approval_without_dispatching -q` 4 项通过；leader/contract 相关测试 `conda run -n agentdeck pytest tests/test_leader_cli.py tests/test_contracts.py -q` 141 项通过；`conda run -n agentdeck pytest -q` 213 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `leader-chat-approval-reject-smoke-ok` 且 approval 仍为 pending。

### Current - Surface Leader chat contract in workbench

- 扩展 `workbench.contracts_card`：新增 `leader_chat_contract=agentdeck contract leader-chat`，让 GUI/TUI 从一屏工作台直接发现自然语言 Leader chat 响应契约和 capability placeholder discovery。
- 同步 `WORKBENCH_CONTRACTS_CARD_FIELDS`、workbench example、live `_workbench_contracts_card()`、contract docs、README、CLAUDE.md、AGENT.md 和测试。
- 完整验证：已先确认红测失败，workbench contracts_card 最初缺少 `leader_chat_contract`；实现后目标测试 `conda run -n agentdeck pytest tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state -q` 2 项通过；相关 contract/CLI 测试 `conda run -n agentdeck pytest tests/test_agent_cli.py tests/test_contracts.py -q` 134 项通过；`conda run -n agentdeck pytest -q` 212 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `workbench-leader-chat-contract-smoke-ok`。

### Current - Expose Leader capability placeholder discovery

- 扩展 `agentdeck contract leader-chat`：新增 `capability_placeholder_fields` 和 `capability_placeholders`，让 GUI 能机器发现 placeholder 白名单及其 blocker，而不是解析 Markdown 文档。
- 新增 `LEADER_CHAT_CAPABILITY_PLACEHOLDERS` 常量，并让 `_placeholder_blocker()` 复用同一份数据，避免 discovery、helper 和 validator 的 placeholder 规则漂移。
- 更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md`、`AGENT.md` 和契约 drift guard 测试。
- 完整验证：已先确认红测失败，contract discovery 最初缺少 `capability_placeholder_fields`；实现后目标测试 `conda run -n agentdeck pytest tests/test_contracts.py::test_leader_chat_contract_response_includes_example_without_drift -q` 1 项通过；leader/contract 扩展测试 `conda run -n agentdeck pytest tests/test_leader_cli.py tests/test_contracts.py -q` 140 项通过；`conda run -n agentdeck pytest -q` 212 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `leader-chat-placeholder-discovery-smoke-ok`。

### Current - Reject unknown Leader capability placeholders

- 加强 `validate_leader_chat_contract()`：capability control 的 placeholder 必须来自已知白名单，当前仅支持 `<goal>`、`<plan_id>`、`<action_id>` 和 `<agent_id>`，避免 GUI 接收到无法安全填参的未知模板。
- 新增契约红测覆盖未知 `<run_id>` placeholder 即使 disabled 且带 blocker 也会被拒绝，确保新增模板前必须同步 contract 规则。
- 更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md` 和 `AGENT.md`，明确 placeholder 白名单是 contract gate。
- 完整验证：已先确认红测失败，validator 最初允许未知 placeholder control；实现后目标测试 `conda run -n agentdeck pytest tests/test_contracts.py::test_validate_leader_chat_contract_rejects_unknown_placeholder_controls -q` 1 项通过；leader/contract 扩展测试 `conda run -n agentdeck pytest tests/test_leader_cli.py tests/test_contracts.py -q` 140 项通过；`conda run -n agentdeck pytest -q` 212 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `leader-help-placeholder-whitelist-smoke-ok`。

### Current - Match Leader capability placeholder blockers

- 加强 `validate_leader_chat_contract()`：placeholder capability control 的 blocker 必须与占位符类型匹配，例如 `<goal>` 对应 `requires goal text`，`<plan_id>` 对应 `requires plan_id`。
- 复用 `_placeholder_blocker()` 生成和校验 blocker，避免 capability helper 与 validator 维护两套缺参提示规则。
- 新增契约红测覆盖 `<plan_id>` control 使用错误 blocker 的情况；更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md` 和 `AGENT.md`。
- 完整验证：已先确认红测失败，validator 最初允许 placeholder control 使用错误 blocker；实现后目标测试 `conda run -n agentdeck pytest tests/test_contracts.py::test_validate_leader_chat_contract_requires_placeholder_blocker_match -q` 1 项通过；placeholder 回归测试 2 项通过；leader/contract 扩展测试 `conda run -n agentdeck pytest tests/test_leader_cli.py tests/test_contracts.py -q` 139 项通过；`conda run -n agentdeck pytest -q` 211 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `leader-help-placeholder-blockers-match-smoke-ok`。

### Current - Guard Leader capability placeholder controls

- 加强 `validate_leader_chat_contract()`：capability control 的命令只要包含 `<...>` 模板占位符，就必须保持 disabled，避免 GUI 把缺少参数的模板命令渲染成可直接执行按钮。
- 新增契约红测覆盖 `agentdeck leader plan --task <goal>` 这类 placeholder control 被错误 enabled 的情况；保持既有 blocker 规则继续要求 disabled control 说明缺少的输入。
- 更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md` 和 `AGENT.md`，明确 placeholder controls 是 contract gate，不只是 UI 展示建议。
- 完整验证：已先确认红测失败，validator 最初允许 enabled placeholder control；实现后目标测试 `conda run -n agentdeck pytest tests/test_contracts.py::test_validate_leader_chat_contract_requires_placeholder_controls_disabled -q` 1 项通过；leader/contract 扩展测试 `conda run -n agentdeck pytest tests/test_leader_cli.py tests/test_contracts.py -q` 138 项通过；`conda run -n agentdeck pytest -q` 210 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `leader-help-placeholder-controls-disabled-smoke-ok`。

### Current - Route Leader review capability to explicit command

- 调整 `capability_card` 的 `review` 能力：GUI-ready control 现在推荐 `agentdeck leader review --plan-id <plan_id>`，而不是复用自然语言 chat 模板，让命令面板能直接指向当前本地 Leader review loop。
- 扩展 capability control blocker 识别：带 `<plan_id>` 的模板命令会 disabled，并返回 `requires plan_id`，避免 GUI 在缺少计划 ID 时误触发 review。
- 更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md` 和 `AGENT.md`，明确 plan/review/apply_action 三个 Leader 调度能力各自对应的显式命令入口。
- 完整验证：已先确认红测失败，review capability 最初仍返回 `agentdeck leader chat --message <goal>`；实现后目标测试 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_help_returns_capability_card_without_planning -q` 1 项通过；leader/contract 扩展测试 `conda run -n agentdeck pytest tests/test_leader_cli.py tests/test_contracts.py -q` 137 项通过；`conda run -n agentdeck pytest -q` 209 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `leader-help-review-command-smoke-ok`。

### Current - Route Leader plan capability to explicit command

- 调整 `capability_card` 的 `plan` 能力：GUI-ready control 现在推荐 `agentdeck leader plan --task <goal>`，而不是复用 `agentdeck leader chat --message <goal>`，让命令面板把显式 Leader planning 入口和自然语言 chat 路由区分开。
- 保持 `<goal>` 模板 control disabled，并继续返回 `requires goal text` blocker，避免 GUI 在缺少任务文本时误执行 provider-backed planning。
- 更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md` 和 `AGENT.md`，明确 help mode 仍然只读，capability controls 只是可渲染命令描述。
- 完整验证：已先确认红测失败，help capability 最初仍返回 `agentdeck leader chat --message <goal>`；实现后目标测试 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_help_returns_capability_card_without_planning -q` 1 项通过；leader/contract 扩展测试 `conda run -n agentdeck pytest tests/test_leader_cli.py tests/test_contracts.py -q` 137 项通过；`conda run -n agentdeck pytest -q` 209 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `leader-help-plan-command-smoke-ok`。

### Current - Harden Leader capability control discovery

- 扩展 `agentdeck contract leader-chat`：新增 `capability_control_fields` 和 `example_capability_control_fields`，让 GUI 命令面板能发现 capability control 的稳定字段，而不是复用或猜测 intent control shape。
- 加强 `validate_leader_chat_contract()`：capability control 的 `command` 和 `safety` 必须与所属 capability item 保持一致，避免 GUI 渲染出与能力项语义不一致的按钮。
- 新增契约红测覆盖 discovery 字段缺失、control safety 漂移和 command 漂移；更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md` 和 `AGENT.md`。
- 完整验证：已先确认红测失败，contract discovery 最初缺少 `capability_control_fields`，validator 最初未拒绝 capability control safety/command 漂移；实现后目标测试 `conda run -n agentdeck pytest tests/test_contracts.py::test_leader_chat_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_leader_chat_contract_requires_capability_control_safety_match tests/test_contracts.py::test_validate_leader_chat_contract_requires_capability_control_command_match -q` 3 项通过；leader/contract 扩展测试 `conda run -n agentdeck pytest tests/test_leader_cli.py tests/test_contracts.py -q` 137 项通过；`conda run -n agentdeck pytest -q` 209 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；临时 git 项目 smoke 确认 `leader-chat-capability-control-discovery-smoke-ok`。

### Current - Add Leader capability controls

- 扩展 `capability_card.capabilities[]`：每个 capability item 现在都带 GUI-ready `controls[]`，复用 `kind`、`label`、`command`、`safety`、`enabled` 和 `blocker` 字段，方便未来 GUI 命令面板直接渲染按钮或模板输入入口。
- 对带 `<goal>`、`<action_id>` 或 `<agent_id>` 的模板命令，control 会 disabled，并返回 `requires goal text`、`requires action_id` 或 `requires agent_id` blocker；直接可检查的只读命令保持 enabled。
- 加强 `validate_leader_chat_contract()`：会拒绝缺少 capability control 字段、非 list controls、非对象 control，以及 disabled control 缺 blocker。
- 更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md` 和 `AGENT.md`，明确 capability controls 只是命令发现和 GUI 渲染描述，不代表自动执行许可。
- 完整验证：已先确认红测失败，capability item 最初缺少 `controls` 字段；实现后目标测试 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_help_returns_capability_card_without_planning tests/test_contracts.py::test_leader_chat_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_leader_chat_contract_requires_capability_control_fields -q` 3 项通过；leader/contract 扩展测试 `conda run -n agentdeck pytest tests/test_leader_cli.py tests/test_contracts.py -q` 135 项通过；`conda run -n agentdeck pytest -q` 207 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `leader-chat-capability-controls-smoke-ok`。

### Current - Expand Leader capability map for scheduling

- 扩展 `capability_card`：在 help mode 能力发现中补齐 `plan`、`review` 和 `apply_action`，让未来 GUI 命令面板能看到 API-backed Leader 调度、状态 review 和 safe apply 主线。
- 加强 capability item 语义校验：`plan` 必须使用 `safety=plan_only`，`review` 和 `apply_action` 必须使用 `safety=safe_apply`，避免 GUI 或自然语言壳把可改变状态的调度入口误标成 inspect。
- 更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md` 和 `AGENT.md`，明确 help mode 本身仍然只读，能力项只是命令发现，不代表自动执行许可。
- 完整验证：已先确认红测失败，help card 最初缺少 `plan` / `review` / `apply_action`，validator 最初未拒绝 `apply_action` 使用 `safety=inspect`；实现后目标测试 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_help_returns_capability_card_without_planning tests/test_contracts.py::test_validate_leader_chat_contract_requires_apply_capability_safety -q` 2 项通过；leader/contract 扩展测试 `conda run -n agentdeck pytest tests/test_leader_cli.py tests/test_contracts.py -q` 134 项通过；`conda run -n agentdeck pytest -q` 206 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `leader-chat-capability-scheduling-smoke-ok`。

### Current - Add Leader chat capability help mode

- 新增自然语言只读 help/capability 意图：`agentdeck leader chat --message "帮助"` / `"help"` / `"你能做什么"` / `"命令面板"` 会进入 `mode=help`，返回 `capability_card`，作为自然语言壳和未来 GUI 命令面板的能力发现入口。
- `capability_card` 由 `src/agentdeck/contracts.py` 的稳定 helper 生成，列出 continue、workbench、runtime、role、ledger、queue、approval、inbox 和 setup 能力，以及每项能力的示例说法、推荐命令、safety、显式用户要求和对应卡片。
- 扩展 Leader chat response contract：新增顶层 `capability_card`、`LEADER_CHAT_CAPABILITY_CARD_FIELDS`、`LEADER_CHAT_CAPABILITY_ITEM_FIELDS`、`capability_card_fields` / `capability_item_fields` 和 example 字段；`validate_leader_chat_contract()` 会拒绝 capability count 与列表长度不一致的响应。
- 更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md` 和 `AGENT.md`，明确 help mode 不调用 provider、不创建 plan/action/approval/message/job/inbox、不读取 pane、不发送 tmux 输入。
- 完整验证：已先确认红测失败，`帮助` 最初误走 `mode=plan`，contract example 最初缺少 `example_capability_card_fields`；实现后目标测试 `conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_help_returns_capability_card_without_planning tests/test_contracts.py::test_leader_chat_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_leader_chat_contract_requires_capability_count_match -q` 3 项通过；leader/contract 扩展测试 `conda run -n agentdeck pytest tests/test_leader_cli.py tests/test_contracts.py -q` 133 项通过；`conda run -n agentdeck pytest -q` 205 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `leader-chat-help-capability-smoke-ok`。

### Current - Harden Leader intent control validation

- 加强 `validate_leader_chat_contract()` 对 `intent_card.controls[]` 的语义校验：`kind=inspect` 必须使用 `safety=inspect`，disabled control 必须提供 blocker。
- 新增契约红测覆盖 inspect control 错误 safety 和 disabled control 缺 blocker，避免 GUI 或自然语言壳渲染出安全语义不明的按钮。
- 更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md` 和 `AGENT.md`，明确 intent controls 的安全字段和 blocker 是 contract gate，不只是展示建议。
- 完整验证：已先确认红测失败，两个目标测试最初都返回 `ok=True`；实现后目标测试 `conda run -n agentdeck pytest tests/test_contracts.py::test_validate_leader_chat_contract_requires_inspect_control_safety tests/test_contracts.py::test_validate_leader_chat_contract_requires_disabled_control_blocker -q` 通过；leader/contract 扩展测试 `conda run -n agentdeck pytest tests/test_leader_cli.py tests/test_contracts.py -q` 131 项通过；`conda run -n agentdeck pytest -q` 203 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；smoke 确认 `leader-chat-intent-control-validation-smoke-ok`。

### Current - Add Leader intent controls

- 扩展 `intent_card`：新增 `controls[]`，为 GUI/自然语言壳提供统一的下一步按钮描述，字段为 `kind`、`label`、`command`、`safety`、`enabled` 和 `blocker`。
- 新增嵌入卡片 inspect control 映射：当 chat 响应包含 `workbench_card`、`continue_card`、`runtime_card`、`ledger_card`、`role_card`、`queue_card`、`operator_card`、`approval_card` 或 `inbox_card` 时，`intent_card.controls[]` 会优先给出对应只读 inspect 命令。
- `_leader_chat_intent_card()` 会根据 chat 顶层 `next_command` 和 `leader_explanation.safety` 生成 `kind=next` 的 control；当没有下一步命令时 control 会 disabled，并返回 `next command unavailable` blocker。
- 扩展 Leader chat response contract：新增 `LEADER_CHAT_INTENT_CONTROL_FIELDS`、`intent_control_fields` 和 `example_intent_control_fields`，`validate_leader_chat_contract()` 会拒绝缺字段或非对象的 intent controls。
- 更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md` 和 `AGENT.md`，明确 intent controls 是可渲染命令描述，不代表自动执行许可。
- 完整验证：已先确认红测失败，目标测试最初缺少嵌入卡片 inspect control；实现后目标测试通过，leader/contract 扩展测试 `conda run -n agentdeck pytest tests/test_leader_cli.py tests/test_contracts.py -q` 129 项通过；`conda run -n agentdeck pytest -q` 201 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `leader-chat-intent-inspect-control-smoke-ok`。

### Current - Add Leader chat intent card

- 新增顶层 `intent_card`，所有 `agentdeck leader chat` 响应在输出前都会补齐自然语言路由卡，稳定暴露 mode、matched_intent、route_source、embedded_card、read_only、next_command 和 requires_explicit_user。
- `route_source` 用于区分本地规则路由、首次 provider-backed planning 和已有 plan 的 state review；`embedded_card` 指向 GUI 应优先渲染的顶层卡片，例如 `workbench_card`、`runtime_card`、`ledger_card` 或 `continue_card`。
- 扩展 Leader chat response contract：新增 `LEADER_CHAT_INTENT_CARD_FIELDS`、`intent_card_fields` 和 `example_intent_card_fields`，`validate_leader_chat_contract()` 会拒绝缺字段的 `intent_card`。
- 更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md` 和 `AGENT.md`，明确新增 chat mode 时必须同步 intent card、契约、文档、HISTORY 和测试。
- 完整验证：已先确认红测失败，目标测试最初因 `LEADER_CHAT_INTENT_CARD_FIELDS` 不存在而导入失败；`intent_card.next_command` mismatch 红测最初未被 validator 捕获；实现后目标测试通过，leader/contract 扩展测试 `conda run -n agentdeck pytest tests/test_leader_cli.py tests/test_contracts.py -q` 128 项通过；`conda run -n agentdeck pytest -q` 200 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `leader-chat-intent-card-smoke-ok`。

### Current - Add Leader workbench chat mode

- 新增自然语言只读 workbench 意图：`agentdeck leader chat --message "打开工作台"` / `"查看总览"` / `"dashboard"` 会进入 `mode=workbench`，嵌入完整 `workbench_card`，供未来 GUI/自然语言壳一跳拿到统一工作台快照。
- `workbench_card` 直接复用 `agentdeck workbench` 的快照契约，并在 `validate_leader_chat_contract()` 内通过 `validate_workbench_contract()` 校验，避免 Leader chat 和 workbench 出现两套 dashboard 字段规则。
- 扩展 Leader chat response contract：新增顶层 `workbench_card`，`agentdeck contract leader-chat` 现在公开 `workbench_card_fields` 和 `example_workbench_card_fields`。
- workbench chat mode 的 `next_command` 等于 `workbench_card.next_command`；它只记录 chat turn，不创建 plan/action/approval/message/job/inbox、不 ack、不 approve、不 dispatch、不 refresh runtime、不 capture、不读取 pane 输出、不发送 tmux 输入。
- 更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md` 和 `AGENT.md`，明确 workbench mode 是统一工作台展示入口，不代表自动执行许可。
- 完整验证：已先确认红测失败，`conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_opens_workbench_snapshot_without_mutating_state -q` 最初返回 `mode=plan`；契约红测最初因缺少 `workbench_card_fields` / `example_workbench_card_fields` 失败；实现后目标测试 4 项通过，leader/contract 扩展测试 `conda run -n agentdeck pytest tests/test_leader_cli.py tests/test_contracts.py -q` 126 项通过；`conda run -n agentdeck pytest -q` 198 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `leader-chat-workbench-mode-smoke-ok`。

### Current - Add Leader ledger chat mode

- 新增自然语言只读 ledger 意图：`agentdeck leader chat --message "查看账本"` / `"查看通信"` 会进入 `mode=ledger`，返回复用 workbench 投影的 `ledger_card`，展示 messages/jobs/replies/inbox 摘要和去重后的 `trace_commands`。
- 扩展 Leader chat response contract：新增顶层 `ledger_card`，`agentdeck contract leader-chat` 现在公开 `ledger_card_fields` 和 `example_ledger_card_fields`，供 GUI/自然语言壳发现通信账本字段。
- ledger chat mode 有 trace 时会把 `next_command` 指向第一条 `agentdeck trace --id <id>`，没有 trace 时回退到 `agentdeck workbench`；它只记录 chat turn，不创建 plan/action/approval/message/job/inbox、不 ack、不 dispatch、不 capture reply、不读取 pane 输出、不发送 tmux 输入。
- 更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md` 和 `AGENT.md`，明确 ledger mode 是通信账本展示和 trace 跳转入口，不代表自动执行许可。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_inspects_ledger_without_mutating_state -q` 最初返回 `mode=plan`；实现后目标测试通过，leader/contract 扩展测试 `conda run -n agentdeck pytest tests/test_leader_cli.py tests/test_contracts.py -q` 125 项通过；`conda run -n agentdeck pytest -q` 197 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `leader-chat-ledger-mode-smoke-ok`。

### Current - Add Leader role chat mode

- 新增自然语言只读 role 意图：`agentdeck leader chat --message "查看角色"` / `"查看分工"` 会进入 `mode=role`，返回复用 workbench 投影的 `role_card`，展示每个 agent 的 role、provider、workspace_mode、role_prompt 和可复制的 `assign_command`。
- 扩展 Leader chat response contract：新增顶层 `role_card`，`agentdeck contract leader-chat` 现在公开 `role_card_fields`、`role_agent_fields`、`example_role_card_fields` 和 `example_role_agent_fields`，供 GUI/自然语言壳发现角色配置字段。
- `validate_leader_chat_contract()` 会校验 role card 和 role agent 字段；role chat mode 只记录 chat turn，不修改 `.agentdeck/config.toml`、不创建 plan/action/approval/message/job/inbox、不发送 tmux 输入。
- 更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md` 和 `AGENT.md`，明确 role mode 是角色展示和显式 assign-role 入口，不代表自动配置许可。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_inspects_roles_without_mutating_state -q` 最初返回 `mode=plan`；实现后目标测试通过，leader/contract 扩展测试 `conda run -n agentdeck pytest tests/test_leader_cli.py tests/test_contracts.py -q` 124 项通过；`conda run -n agentdeck pytest -q` 196 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `leader-chat-role-mode-smoke-ok`。

### Current - Add Leader queue chat mode

- 新增自然语言只读 queue/operator 意图：`agentdeck leader chat --message "查看队列"` / `"查看控制面"` 会进入 `mode=queue`，返回复用 workbench 投影的 `queue_card` 和 `operator_card`，并展示当前 active queue、next_command、preview/apply/explicit controls 和 blocker。
- 扩展 Leader chat response contract：新增顶层 `queue_card` 和 `operator_card`，`agentdeck contract leader-chat` 现在公开 `queue_card_fields`、`operator_card_fields`、`example_queue_card_fields` 和 `example_operator_card_fields`，供 GUI/自然语言壳发现队列控制面字段。
- 抽出 `_active_queue_source()`，让 workbench 和 Leader chat queue mode 共享同一套 active queue 判断；`validate_leader_chat_contract()` 会校验 queue/operator card 字段，并要求它们的 `next_command` 与 chat 顶层一致。
- queue chat mode 只记录 chat turn，不创建或应用 Leader action、不 approve/reject/dispatch、不 ack inbox、不 refresh runtime、不发送 tmux 输入；实际可执行入口仍通过 `operator_card.controls[]` 显式展示。
- 更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md` 和 `AGENT.md`，明确 queue/operator 是展示和显式控制入口，不代表自动执行许可。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_inspects_queue_without_applying_action -q` 最初返回 `mode=review`；实现后目标测试通过，leader/contract 扩展测试 `conda run -n agentdeck pytest tests/test_leader_cli.py tests/test_contracts.py -q` 123 项通过；`conda run -n agentdeck pytest -q` 195 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `leader-chat-queue-mode-smoke-ok`。

### Current - Add Leader runtime chat mode

- 新增自然语言只读 runtime 意图：`agentdeck leader chat --message "查看 runtime"` / `"查看终端"` 会进入 `mode=runtime`，返回复用 workbench runtime 投影的 `runtime_card`，并建议 `next_command=agentdeck agent list`。
- 新增 `_chat_wants_runtime()` 和 runtime explanation 分支，让 Leader chat 可以把可见 tmux runtime 作为一等对话入口，而不是只在 `continue` 恢复 stale runtime 时展示。
- runtime chat mode 只记录 chat turn，不创建 plan、leader action、approval、message、job 或 inbox，不执行 refresh、spawn、stop、capture，也不发送 tmux 输入；具体 runtime controls 仍由 `runtime_card.controls[]` 暴露给人类显式执行。
- 更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md` 和 `AGENT.md`，明确 runtime mode 复用 workbench runtime card，并保持只读边界。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_inspects_runtime_without_mutating_state -q` 最初返回 `mode=plan`；实现后目标测试通过，leader/contract 扩展测试 `conda run -n agentdeck pytest tests/test_leader_cli.py tests/test_contracts.py -q` 122 项通过；`conda run -n agentdeck pytest -q` 194 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `leader-chat-runtime-mode-smoke-ok`。

### Current - Embed runtime card in Leader continue

- 扩展 Leader chat response contract：新增顶层 `runtime_card`，`agentdeck contract leader-chat` 现在公开 `runtime_card_fields` 和 `example_runtime_card_fields`，供 GUI/自然语言壳发现 runtime 恢复字段。
- 扩展 `agentdeck leader chat --message "继续"`：当 ProjectView recovery 的 `recommended_action.source=runtime` 时，响应会嵌入复用 workbench runtime 投影的 `runtime_card`，并继续返回 `continue_card`、`recovery` 和 `next_command=agentdeck agent refresh`。
- 其他 leader chat 模式显式返回 `runtime_card=null`，保持 response contract 稳定；`validate_leader_chat_contract()` 会校验 runtime card 字段，但不会执行 refresh、spawn、stop、capture 或发送 tmux 输入。
- 更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md` 和 `AGENT.md`，明确 runtime card 是展示和显式恢复入口，不代表自动 runtime 操作。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_continue_embeds_runtime_card_for_stale_runtime tests/test_contracts.py::test_leader_chat_contract_payload_is_reusable_without_cli tests/test_contracts.py::test_leader_chat_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_leader_chat_contract_accepts_example -q` 最初因 `runtime_card` 和 `runtime_card_fields` 缺失失败；实现后目标测试 4 项通过，leader/contract 扩展测试 121 项通过；`conda run -n agentdeck pytest -q` 193 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `leader-chat-runtime-card-smoke-ok`。

### Current - Surface stale runtime recovery

- 扩展 ProjectView recovery：state 中存在 `status=stale` 的 agent runtime binding 时，`status.recovery` 会返回 `status=runtime_stale`、`next_command=agentdeck agent refresh`，并把 `recommended_action.source` 标记为 `runtime`。
- 扩展 `recovery.pending`：新增 `runtime_stale` 计数，并同步 `PROJECT_VIEW_RECOVERY_PENDING_FIELDS`、ProjectView example fixture、validator 和 contract 测试，供 GUI 做字段兼容检查。
- 扩展 workbench operator：`runtime` 进入 `active_queue_source` / `operator_card.action_kind`，主操作卡片会把 stale runtime 的 preview/explicit command 都指向 `agentdeck agent refresh`。
- 更新 `docs/contracts/project-view-schema.md`、`docs/contracts/workbench-schema.md`、`README.md`、`CLAUDE.md` 和 `AGENT.md`，明确 `runtime_stale` 是可恢复状态，不代表自动发送 tmux 输入或自动重启 agent。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_status_recovery_matrix_for_gui_actions tests/test_contracts.py::test_validate_project_view_contract_reports_missing_recovery_pending_field tests/test_contracts.py::test_project_view_example_matches_contract_field_lists tests/test_agent_cli.py::test_contract_project_view_example_exports_gui_ready_status -q` 最初因 stale runtime 仍返回 idle 且 `runtime_stale` pending 字段不存在失败；补充 workbench 红测后确认 `active_queue_source` 仍为 `none`；实现后 focused 测试 5 项通过；`conda run -n agentdeck pytest -q` 192 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `runtime-stale-recovery-smoke-ok`。

### Current - Add agent runtime refresh

- 新增 `agentdeck agent refresh`，显式检查 state 中记录为 `running` 的 tmux pane 是否仍存在；丢失的 pane 会被标记为 `stale`，并写入 `agent_runtime_stale` 审计事件。
- 扩展 runtime backend：`RuntimeBackend` / `TmuxBackend` 新增 `pane_exists()`，tmux backend 通过 `display-message -p -t <pane_id> "#{pane_id}"` 判断 pane 是否仍可寻址。
- 扩展 workbench 与 agent-runtime contract：`runtime_card` 新增 `refresh_command`，`agentdeck contract agent-runtime` 新增 `refresh_command`、`refresh_response_fields` 和 `refresh_agent_fields`，`--example` 同步包含稳定 refresh 示例。
- 更新 `docs/contracts/agent-runtime-schema.md`、`docs/contracts/workbench-schema.md`、`README.md`、`CLAUDE.md` 和 `AGENT.md`，明确 refresh 是显式 runtime reconciliation，不发送 tmux 输入、不推断任务完成。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_agent_refresh_marks_missing_running_pane_as_stale tests/test_agent_cli.py::test_contract_agent_runtime_discovers_schema_for_gui_clients tests/test_contracts.py::test_agent_runtime_contract_payload_is_reusable_without_cli tests/test_contracts.py::test_agent_runtime_contract_response_includes_example_without_drift tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients -q` 最初因 `AGENT_RUNTIME_REFRESH_AGENT_FIELDS` 等新常量不存在失败；实现后目标测试组 6 项通过；`conda run -n agentdeck pytest -q` 191 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；真实 tmux 临时项目 smoke 确认 `agent-refresh-stale-smoke-ok`。

### Current - Add agent runtime contract discovery

- 新增 `agentdeck contract agent-runtime` 和 `agentdeck contract agent-runtime --example`，为 GUI/TUI/自然语言入口发现 `agent list/spawn/capture/send/stop` 命令模板、capture 响应字段和 runtime control 字段。
- 在 `src/agentdeck/contracts.py` 中新增 `AGENT_RUNTIME_AGENT_ITEM_FIELDS`、`AGENT_RUNTIME_CAPTURE_RESPONSE_FIELDS`、`agent_runtime_contract_payload()`、`agent_runtime_contract_response()` 和 `agent_runtime_example()`，复用 workbench runtime control 字段，避免可见 tmux runtime 出现两套按钮语义。
- 扩展 contract index 和 workbench `contracts_card`：`agent-runtime` 进入 `agentdeck contract list`，`agentdeck workbench` 也会暴露 `agent_runtime_contract`，让 GUI 可以从一屏快照继续发现 agent runtime 命令契约。
- 新增 `docs/contracts/agent-runtime-schema.md`，并同步更新 `docs/contracts/contract-index-schema.md`、`docs/contracts/workbench-schema.md`、`README.md`、`CLAUDE.md` 和 `AGENT.md`，明确该 contract 只做 discovery，不读取 state、不 inspect tmux pane、不发送输入。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_contracts.py::test_agent_runtime_contract_payload_is_reusable_without_cli tests/test_contracts.py::test_agent_runtime_contract_response_includes_example_without_drift tests/test_agent_cli.py::test_contract_agent_runtime_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_agent_runtime_example_exports_gui_ready_runtime_contract -q` 最初因 `AGENT_RUNTIME_AGENT_ITEM_FIELDS` 等新 helper 不存在失败；实现后目标测试组 7 项通过；`conda run -n agentdeck pytest -q` 190 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `agent-runtime-contract-smoke-ok`。

### Current - Add workbench runtime control fields

- 扩展 workbench contract discovery：新增 `WORKBENCH_RUNTIME_CONTROL_FIELDS`，并在 `agentdeck contract workbench` 输出 `runtime_control_fields`，让 GUI/TUI 可以发现 runtime `controls[]` item 的稳定字段列表。
- 加强 `validate_workbench_contract()`：runtime agent 的 `controls[]` 必须是列表，首个 control item 必须包含 `kind`、`label`、`command`、`safety`、`enabled` 和 `blocker`；缺字段会返回明确错误。
- 更新 `docs/contracts/workbench-schema.md`、`README.md`、`CLAUDE.md` 和 `AGENT.md`，把 `runtime_control_fields` 纳入 workbench 契约说明。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_requires_runtime_control_fields -q` 最初因 `WORKBENCH_RUNTIME_CONTROL_FIELDS` 不存在失败；实现后目标测试通过；同一目标测试组 3 项通过；`conda run -n agentdeck pytest -q` 186 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `workbench-runtime-control-fields-ok`。

### Current - Add workbench runtime controls

- 扩展 `agentdeck workbench` 的 `runtime_card.agents[]`：新增 `controls[]`，让 GUI/TUI 可以直接按按钮模型渲染可见 tmux runtime 的 capture、send、stop、spawn 和 inbox 操作。
- 运行中的 agent 会暴露 enabled 的 capture/send/stop/inbox controls；未运行的 agent 会暴露 enabled 的 spawn/inbox controls，并把 capture/send/stop 标记为 disabled 且带 `agent is not running` blocker。
- 扩展 workbench contract：`WORKBENCH_RUNTIME_AGENT_FIELDS`、`agentdeck contract workbench` 的 `runtime_agent_fields` 和 `workbench_example()` 现在包含 runtime `controls[]`；validator 会拒绝缺失 runtime agent controls 的 payload。
- 更新 `docs/contracts/workbench-schema.md`、`README.md`、`CLAUDE.md` 和 `AGENT.md`，明确 GUI 应优先渲染 `controls[]`，且 send/spawn/stop 类 runtime control 仍需要人类显式触发。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_requires_runtime_agent_controls -q` 最初因 `runtime_agent_fields` 和 live/example runtime agent 缺少 `controls` 失败；实现后目标测试通过；同一目标测试组 4 项通过；`conda run -n agentdeck pytest -q` 185 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `workbench-runtime-controls-ok`。

### Current - Add workbench runtime pane commands

- 扩展 `agentdeck workbench` 的 `runtime_card.agents[]`：新增 `capture_command` 和 `send_command_template`，让 GUI/TUI 可以从可见 tmux runtime 面板直接渲染“查看输出”和“显式发送输入”控制入口。
- 扩展 workbench contract：`WORKBENCH_RUNTIME_AGENT_FIELDS` 和 `agentdeck contract workbench` 的 `runtime_agent_fields` 现在包含 `capture_command` 与 `send_command_template`；`workbench_example()` 同步输出稳定示例。
- 更新 `docs/contracts/workbench-schema.md`、`README.md`、`CLAUDE.md` 和 `AGENT.md`，明确 `capture_command` 是只读观察命令，`send_command_template` 必须由人类显式填入文本并执行，不得自动发送 tmux 输入。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift -q` 最初因 `runtime_agent_fields` 缺少 `capture_command`/`send_command_template` 且 runtime agent payload 缺字段失败；实现后目标测试通过；`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_requires_runtime_agent_fields -q` 4 项通过；`conda run -n agentdeck pytest -q` 184 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `workbench-runtime-commands-ok`。

### Current - Add workbench contract discovery card

- 扩展 `agentdeck workbench`：新增顶层 `contracts_card`，让 GUI/TUI 从单次 workbench 快照即可发现 `agentdeck contract list`、contract index schema、workbench/project-view/events/doctor contract 入口。
- 扩展 workbench contract：新增 `WORKBENCH_CONTRACTS_CARD_FIELDS`，并在 `agentdeck contract workbench` 暴露 `contracts_card_fields`；`validate_workbench_contract()` 现在会拒绝缺失 contracts card 字段的 workbench payload。
- 更新 `docs/contracts/workbench-schema.md`、`README.md`、`CLAUDE.md` 和 `AGENT.md`，明确 `contracts_card` 是只读 contract discovery metadata，不读取 state、不 inspect tmux pane、不调用 provider、不执行 contract 命令。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_requires_contracts_card_fields -q` 最初因 `WORKBENCH_CONTRACTS_CARD_FIELDS` 不存在失败；实现后目标测试通过；`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_requires_contracts_card_fields tests/test_contracts.py::test_validate_workbench_contract_accepts_example -q` 5 项通过；`conda run -n agentdeck pytest -q` 184 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `workbench-contracts-card-ok`。

### Current - Add contract discovery index

- 新增 `agentdeck contract list`，为 GUI/TUI/自然语言入口提供统一契约总目录，返回所有可消费 contract 的 discovery command、example command、本地 schema 文档路径和存在性。
- 在 `src/agentdeck/contracts.py` 中新增 `CONTRACT_INDEX_RESPONSE_FIELDS`、`CONTRACT_INDEX_ITEM_FIELDS`、`CONTRACT_INDEX_SPECS` 和 `contract_index_response()`，让 CLI 与外部复用代码共享同一份 contract index 定义。
- 新增 `docs/contracts/contract-index-schema.md`，明确 contract index 是只读 discovery 入口，不读取 `.agentdeck/` state、不 inspect tmux pane、不调用 Leader provider、不修改项目。
- 更新 `README.md`、`CLAUDE.md` 和 `AGENT.md`，要求新增 GUI-consumable contract 时同步 contract index、文档和测试。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_list_discovers_all_gui_contracts tests/test_contracts.py::test_contract_index_response_is_reusable_without_cli -q` 最初因 `contract_index_response` 和 `CONTRACT_INDEX_ITEM_FIELDS` 不存在失败；实现后目标测试通过；`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_list_discovers_all_gui_contracts tests/test_contracts.py::test_contract_index_response_is_reusable_without_cli tests/test_agent_cli.py::test_contract_project_view_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_events_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients -q` 5 项通过；`conda run -n agentdeck pytest -q` 183 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `contract-list-ok`。

### Current - Add events contract discovery

- 新增 `agentdeck contract events` 和 `agentdeck contract events --example`，为 GUI/TUI 暴露审计事件时间线的 response、cursor metadata 和 event item 字段。
- 新增 `docs/contracts/events-schema.md`，明确 `agentdeck events --limit`、`agentdeck events --since`、`cursor_found=false` fallback、以及 cursor 由 GUI/调用方持有且不写入 AgentDeck state。
- 在 `src/agentdeck/contracts.py` 中新增 `EVENTS_RESPONSE_FIELDS`、`EVENTS_CURSOR_FIELDS`、`EVENTS_EVENT_ITEM_FIELDS`、`events_contract_payload()`、`events_contract_response()` 和 `events_example()`，让 CLI discovery 和可复用模块输出一致。
- 更新 `README.md`、`CLAUDE.md` 和 `AGENT.md`，把 events timeline 纳入正式 contract discovery 清单。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_events_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_events_example_exports_gui_ready_timeline tests/test_contracts.py::test_events_contract_payload_is_reusable_without_cli tests/test_contracts.py::test_events_contract_response_includes_example_without_drift -q` 最初因 `events_contract_payload` 和 `EVENTS_CURSOR_FIELDS` 不存在失败；实现后目标测试通过；`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_events_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_events_example_exports_gui_ready_timeline tests/test_contracts.py::test_events_contract_payload_is_reusable_without_cli tests/test_contracts.py::test_events_contract_response_includes_example_without_drift tests/test_agent_cli.py::test_events_since_returns_events_after_cursor_with_metadata -q` 通过；`conda run -n agentdeck pytest -q` 181 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时项目 smoke 确认 `events-contract-example-ok` 和 `events-contract-live-ok`。

### Current - Add event timeline cursor queries

- 扩展 `agentdeck events`：新增 `--since <event_id>`，让 GUI/TUI 在 `workbench.change_summary.has_new_events=true` 后，可以拉取 cursor 之后的完整审计事件详情。
- `events --since` 返回 `since_event_id`、`latest_event_id`、`cursor_found` 和受 `--limit` 限制的 `events`；当 cursor 不存在时返回 `cursor_found=false` 并回退到事件尾部。
- 保持兼容：不带 `--since` 的 `agentdeck events --limit <n>` 输出形状不变；cursor 由 GUI/调用方持有，不写入 AgentDeck state。
- 更新 `README.md`、`docs/contracts/project-view-schema.md`、`CLAUDE.md` 和 `AGENT.md`，明确事件游标是只读查询，不改变审计账本。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_events_since_returns_events_after_cursor_with_metadata tests/test_agent_cli.py::test_events_since_missing_cursor_returns_limited_tail_and_marks_cursor_missing -q` 最初因 argparse 不认识 `--since` 失败；实现后目标事件测试通过；`conda run -n agentdeck pytest tests/test_agent_cli.py::test_events_lists_recent_event_tail tests/test_agent_cli.py::test_events_since_returns_events_after_cursor_with_metadata tests/test_agent_cli.py::test_events_since_missing_cursor_returns_limited_tail_and_marks_cursor_missing tests/test_agent_cli.py::test_events_returns_empty_list_when_log_is_missing tests/test_agent_cli.py::test_workbench_since_event_summarizes_new_audit_events_without_mutating_state -q` 通过；`conda run -n agentdeck pytest -q` 177 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时项目 smoke 确认 `events-since-ok` 和 `events-since-missing-ok`。

### Current - Add workbench event cursor summary

- 扩展 `agentdeck workbench`：新增 `--since-event <event_id>` 和顶层 `change_summary`，让 GUI/TUI 可以用审计事件游标判断当前快照相对上一帧是否有新事件。
- `change_summary` 包含 `since_event_id`、`latest_event_id`、`has_new_events`、`new_event_count` 和 `new_events`；它只从 `events.jsonl` 临时计算，不保存 cursor、不写 state。
- watch 模式可组合 `--watch --since-event <event_id> --interval <seconds>`，每行 JSONL 都保留同一契约形状并通过 `validate_workbench_contract()`。
- 更新 workbench contract 字段、example fixture、validator/live workbench 测试，以及 `README.md`、`docs/contracts/workbench-schema.md`、`CLAUDE.md` 和 `AGENT.md`。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_agent_cli.py::test_workbench_since_event_summarizes_new_audit_events_without_mutating_state tests/test_agent_cli.py::test_workbench_since_latest_event_reports_no_new_events tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_requires_change_summary_fields -q` 最初因 `WORKBENCH_CHANGE_SUMMARY_FIELDS` 不存在失败；实现后目标测试通过；同一目标测试组 6 项通过；`conda run -n agentdeck pytest -q` 175 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时项目 smoke 确认 `workbench-since-event-ok` 和 `workbench-watch-since-event-ok`。

### Current - Add workbench watch stream

- 扩展 `agentdeck workbench`：新增 `--watch`、`--iterations <n>` 和 `--interval <seconds>`，让未来 GUI/TUI 可以订阅连续的一屏工作台快照 JSONL。
- watch 模式每轮重新读取 ProjectView、组合 workbench snapshot，并通过 `validate_workbench_contract()` 校验后输出一行紧凑 JSON；默认单次 `agentdeck workbench` 仍输出 pretty JSON，保持兼容。
- `--watch` 仍严格只读：不写 state、不创建 chat turn、不创建/应用 leader action、不 ack、不 approve/dispatch、不 capture reply、不读取 pane、不发送 tmux 输入；`--iterations` 便于测试、脚本和 GUI smoke 有界退出。
- 更新 `README.md`、`docs/contracts/workbench-schema.md`、`CLAUDE.md` 和 `AGENT.md`，明确 GUI 应使用 `agentdeck workbench --watch --interval <seconds>` 作为状态流入口。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_workbench_watch_outputs_jsonl_snapshots_without_mutating_state -q` 最初因 argparse 不认识 `--watch --iterations --interval` 失败；实现后目标测试通过；`conda run -n agentdeck pytest tests/test_agent_cli.py::test_workbench_watch_outputs_jsonl_snapshots_without_mutating_state tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_agent_cli.py::test_workbench_surfaces_provider_setup_as_active_operator_source -q` 通过；`conda run -n agentdeck pytest -q` 172 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时项目 smoke 确认 `workbench-watch-jsonl-ok`。

### Current - Add workbench operator controls

- 扩展 `agentdeck workbench` 的 `operator_card`：新增 `controls[]`，让一屏主操作区与 leader action / approval / inbox 队列 item 共用同一套 GUI 按钮描述模型。
- `operator_card.controls[]` 包含 preview、可选 apply、explicit control；每个 control 包含 `kind`、`label`、`command`、`safety`、`enabled` 和 `blocker`，只描述人类操作，不自动执行。
- 保留既有 `preview_command`、`apply_command`、`explicit_command` 和 `blocker` 兼容字段；workbench 仍只读，不写 state、不 ack/approve/dispatch、不读取 pane、不发送 tmux 输入。
- 更新 workbench contract 字段、example fixture、validator/live workbench 测试，以及 `README.md`、`docs/contracts/workbench-schema.md`、`CLAUDE.md` 和 `AGENT.md`。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_agent_cli.py::test_workbench_surfaces_provider_setup_as_active_operator_source tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_requires_operator_fields -q` 最初因 `operator_card` 缺少 `controls` 失败；实现后目标测试通过；`conda run -n agentdeck pytest -q` 通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时项目 smoke 确认 `operator-controls-ok` 与 `provider-operator-controls-ok`。

### Current - Add queue item controls

- 扩展 leader action、approval 和 inbox 三类 queue item：新增 `controls[]`，为 GUI/TUI 提供统一按钮描述对象。
- 每个 control 包含 `kind`、`label`、`command`、`safety`、`enabled` 和 `blocker`；preview control 始终是只读 inspect，apply/approve/reject/dispatch/ack/explicit control 仍保持人类显式执行边界。
- 保留既有 `preview_command`、`apply_command`、`approve_command`、`ack_command` 等兼容字段；`controls[]` 是新增消费层，不自动执行任何命令、不读取 pane、不发送 tmux 输入。
- 更新 approvals/inbox/leader-actions/ProjectView contract 字段、example fixture、validator、live queue 测试，以及 `README.md`、`docs/contracts/*.md`、`CLAUDE.md` 和 `AGENT.md`。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_contracts.py::test_approval_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_approval_contract_requires_gui_action_fields tests/test_contracts.py::test_inbox_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_inbox_contract_requires_head_ack_fields tests/test_contracts.py::test_leader_actions_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_leader_actions_contract_requires_applyability_fields tests/test_agent_cli.py::test_contract_approvals_example_exports_gui_ready_queue tests/test_agent_cli.py::test_contract_inbox_example_exports_gui_ready_queue tests/test_agent_cli.py::test_contract_leader_actions_example_exports_gui_ready_queue tests/test_leader_cli.py::test_leader_actions_lists_persisted_actions tests/test_leader_cli.py::test_leader_chat_suggests_approve_for_pending_approval_without_approving tests/test_leader_cli.py::test_leader_chat_suggests_ack_for_current_inbox_head_without_acknowledging -q` 最初因 queue items 缺少 `controls` 失败；实现后目标测试通过；`conda run -n agentdeck pytest -q` 通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时项目 smoke 确认 `leader-controls-ok`、`approval-controls-ok` 和 `inbox-controls-ok`。

### Current - Add leader action preview commands

- 扩展 ProjectView `leader_actions.items[]`、`agentdeck leader actions` 和 `agentdeck leader action --action-id <id>`：新增 `preview_command`，统一 action / approval / inbox 三类队列的“只读预览优先、显式执行随后”模型。
- `leader_action.preview_command` 指向 `agentdeck leader action --action-id <action_id>`，复用只读 action detail，不创建 plan、不 apply action、不创建 approval、不 dispatch、不发送 tmux 输入。
- 更新 leader action/detail contract 字段、example fixture、validator、live action queue/detail 测试，以及 `README.md`、`docs/contracts/project-view-schema.md`、`docs/contracts/leader-actions-schema.md`、`docs/contracts/leader-action-schema.md`、`CLAUDE.md` 和 `AGENT.md`。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_contracts.py::test_leader_action_contract_response_includes_example_without_drift tests/test_contracts.py::test_leader_actions_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_leader_actions_contract_requires_applyability_fields tests/test_agent_cli.py::test_contract_leader_action_example_exports_gui_ready_detail tests/test_agent_cli.py::test_contract_leader_actions_example_exports_gui_ready_queue tests/test_leader_cli.py::test_leader_actions_lists_persisted_actions tests/test_leader_cli.py::test_leader_action_show_outputs_full_action_with_applyability -q` 最初因 leader action 缺少 `preview_command` 失败；实现后目标测试通过；`conda run -n agentdeck pytest -q` 通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时项目 smoke 确认 `leader-actions-preview-command-ok` 与 `leader-action-preview-command-ok`。

### Current - Add queue item preview commands

- 扩展 approval queue 和 inbox queue item：新增 `preview_command`，让 GUI/TUI 可以先渲染只读预览按钮，再渲染 approve/dispatch/ack 等显式执行按钮。
- `approval.preview_command` 指向 `agentdeck approval list`；`inbox.preview_command` 指向对应 `agentdeck trace --id <inbox_id>`，复用通信账本 lineage，不读取 pane、不发送 tmux 输入、不自动 ack/approve/dispatch。
- 更新 approvals/inbox contract 字段、example fixture、validator、自然语言 approval/inbox card 测试，以及 `README.md`、`docs/contracts/approvals-schema.md`、`docs/contracts/inbox-schema.md`、`CLAUDE.md` 和 `AGENT.md`。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_contracts.py::test_approval_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_approval_contract_requires_gui_action_fields tests/test_contracts.py::test_inbox_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_inbox_contract_requires_head_ack_fields tests/test_agent_cli.py::test_contract_approvals_example_exports_gui_ready_queue tests/test_agent_cli.py::test_contract_inbox_example_exports_gui_ready_queue tests/test_leader_cli.py::test_leader_chat_suggests_approve_for_pending_approval_without_approving tests/test_leader_cli.py::test_leader_chat_suggests_ack_for_current_inbox_head_without_acknowledging -q` 最初因 queue item 缺少 `preview_command` 失败；实现后目标测试通过；`conda run -n agentdeck pytest -q` 通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时项目 smoke 确认 `approval-preview-command-ok` 与 `inbox-preview-command-ok`。

### Current - Expose doctor contract from provider health

- 扩展 workbench/setup-mode `provider_health`：新增 `doctor_contract="agentdeck contract doctor"`，让 GUI/TUI 不只知道下一步运行 `agentdeck doctor`，也能直接发现 doctor diagnostics schema。
- `doctor_contract` 随 fake、unsupported 和真实 Leader provider health 一起输出；它不读取 env、不调用 provider、不暴露 API key。
- 更新 workbench contract 字段、example fixture、自然语言 setup diagnostics 测试，以及 `README.md`、`docs/contracts/workbench-schema.md`、`docs/contracts/leader-chat-schema.md`、`CLAUDE.md` 和 `AGENT.md`。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_surfaces_provider_setup_as_active_operator_source tests/test_leader_cli.py::test_leader_chat_setup_intent_surfaces_provider_diagnostics_without_planning -q` 最初因 `provider_health` 缺少 `doctor_contract` 失败；实现后目标测试通过；`conda run -n agentdeck pytest -q` 通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时项目 smoke 确认 `workbench-doctor-contract-ok` 与 `setup-chat-doctor-contract-ok`。

### Current - Add doctor contract discovery

- 新增 `agentdeck contract doctor` 和 `agentdeck contract doctor --example`，为 GUI/TUI 暴露 doctor diagnostics 的 response_fields、configured_leader_fields 和 provider_check_fields。
- 在 `src/agentdeck/contracts.py` 中新增 doctor contract payload/response/example helper，保持 CLI discovery 输出与可复用模块输出一致。
- 新增 `docs/contracts/doctor-schema.md`，明确 doctor 是只读本地诊断入口，不调用 Leader provider，不暴露真实 API key，`setup_commands` 只能包含 placeholder。
- `agentdeck doctor` 顶层新增 `doctor_command`，让 live output、contract response fields 和 example fixture 一致，方便 GUI 渲染刷新/重跑入口。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，把 doctor contract 纳入统一 contract discovery 列表。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_doctor_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_doctor_example_exports_gui_ready_diagnostics tests/test_contracts.py::test_doctor_contract_payload_is_reusable_without_cli tests/test_contracts.py::test_doctor_contract_response_includes_example_without_drift -q` 最初因 `doctor_contract_payload` 不存在而 import 失败；实现后 doctor contract 目标测试 4 项通过；`conda run -n agentdeck pytest -q` 171 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时项目 smoke 确认 `doctor-contract-example-ok` 与 `doctor-live-command-ok`。

### Current - Add doctor setup commands

- 扩展 `agentdeck doctor` 的 `configured_leader`：新增 `setup_commands`，复用 workbench/setup-mode 的 provider placeholder export 命令。
- `configured_leader.setup_commands` 与 `provider_health.setup_commands` 保持一致，只包含占位符，不读取、回显或保存真实 API key；即使本地已设置真实 key，doctor 输出也必须保持 placeholder。
- 更新 doctor CLI 测试，覆盖缺少 `DEEPSEEK_API_KEY`、设置 env 后 ready=true，以及真实 key 不会出现在 doctor JSON 输出。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，明确 doctor 是同一 provider setup guidance 的诊断入口，不能暴露密钥值。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_doctor_reports_openai_compatible_provider_state tests/test_agent_cli.py::test_doctor_reports_configured_leader_ready_when_env_is_set tests/test_agent_cli.py::test_doctor_configured_leader_never_exposes_real_provider_key -q` 最初因 `configured_leader` 缺少 `setup_commands` 失败；实现后 doctor 目标测试 3 项通过；`conda run -n agentdeck pytest -q` 167 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时项目 smoke 确认 `doctor-setup-commands-missing-ok` 与 `doctor-setup-commands-secret-redacted-ok`。

### Current - Add provider setup commands

- 扩展 workbench/setup-mode `provider_health`：新增 `setup_commands`，为 DeepSeek 和 OpenAI-compatible Leader provider 提供可复制的 placeholder export 命令。
- `setup_commands` 只包含占位符，例如 `"<your-deepseek-api-key>"`，不得读取、回显或保存真实 API key；即使本地已设置真实 key，输出也必须保持 placeholder。
- 更新 workbench contract 字段、example fixture 和 validator，要求 `provider_health.setup_commands` 是 list。
- 更新自然语言 setup diagnostics 测试，覆盖缺少 `DEEPSEEK_API_KEY` 时返回 DeepSeek placeholder exports，以及设置真实 key 后不会把 key 泄露到 JSON 输出。
- 更新 `README.md`、`docs/contracts/leader-chat-schema.md`、`docs/contracts/workbench-schema.md`、`CLAUDE.md` 与 `AGENT.md`，明确 `setup_commands` 是人类复制后自行编辑的安全提示，不是自动环境修改。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_leader_cli.py::test_leader_chat_setup_intent_surfaces_provider_diagnostics_without_planning tests/test_leader_cli.py::test_leader_chat_setup_commands_never_expose_real_provider_key -q` 最初因 provider_health 缺少 `setup_commands` 失败；实现后目标测试 4 项通过，workbench contract/example 相关测试 2 项通过；`conda run -n agentdeck pytest -q` 166 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时项目 smoke 确认 `setup-commands-missing-ok` 与 `setup-commands-secret-redacted-ok`。

### Current - Add natural-language setup diagnostics

- 扩展 `agentdeck leader chat`：当人类输入 `doctor`、`检查 Leader provider 配置`、`诊断环境变量` 等 setup/diagnostics 意图时，进入只读 `mode=setup`。
- `mode=setup` 返回 `provider_health`、`recovery`、`next_command=agentdeck doctor` 和 `leader_explanation`；它只记录 chat turn，不调用配置的 Leader provider，不创建 plan、leader action、approval、message、job 或 inbox，也不发送 tmux 输入。
- 增强 workbench/provider health 字段：在 provider readiness 之外加入 agent_id、model、approval_mode 和 api_backed，使 GUI 可以把 provider setup 直接渲染为当前 Leader 的诊断卡片。
- 收窄 setup 意图识别，避免 `用配置 Leader 对话` 这类普通自然语言请求误判为 setup；普通请求仍会按配置 provider/model 进入 plan。
- 更新 `README.md`、`docs/contracts/leader-chat-schema.md`、`docs/contracts/workbench-schema.md`、`CLAUDE.md` 与 `AGENT.md`，明确 setup-mode 是自然语言诊断入口，不能泄露密钥值或绕过人类控制。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_setup_intent_surfaces_provider_diagnostics_without_planning -q` 最初因 chat 尝试调用缺少 `DEEPSEEK_API_KEY` 的 provider 而失败；实现后目标测试通过；相关 `tests/test_leader_cli.py` 55 项通过，workbench/contract 目标测试 4 项通过；`conda run -n agentdeck pytest -q` 165 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时项目 smoke 确认 `leader-chat-setup-diagnostics-ok` 与 `leader-chat-setup-history-ok`。

### Current - Add configured Leader readiness to doctor

- 扩展 `agentdeck doctor`：新增 `configured_leader`，从 `.agentdeck/config.toml` 的 `[leader]` 派生当前 Leader provider/model/approval_mode 的 readiness 摘要。
- `configured_leader` 公开 agent_id、provider、model、approval_mode、supported、ready、missing_env 和 detail，只暴露缺失 env 名称，不暴露密钥值。
- 调整 doctor 顶层 `ok`：除了 tmux 和 config 存在外，还要求当前配置 Leader provider ready；默认 DeepSeek 缺少 `DEEPSEEK_API_KEY` 时 `agentdeck doctor` 会返回非 0。
- 补充 CLI 测试，覆盖缺失 `DEEPSEEK_API_KEY` 时 `configured_leader.ready=false`、顶层 `ok=false`，以及设置 env 后 `configured_leader.ready=true`。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，明确 doctor 是 provider setup recovery 推荐的诊断面，不能暴露密钥值。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_doctor_reports_openai_compatible_provider_state tests/test_agent_cli.py::test_doctor_reports_configured_leader_ready_when_env_is_set -q` 最初因 doctor 缺少 `configured_leader` 且顶层 `ok` 未受 provider readiness 影响失败；实现后 doctor 目标测试 2 项通过；`conda run -n agentdeck pytest tests/test_agent_cli.py tests/test_provider_openai_compatible.py tests/test_leader_cli.py -q` 100 项通过；`conda run -n agentdeck pytest -q` 164 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时项目 smoke 确认 `doctor-configured-leader-missing-ok` 与 `doctor-configured-leader-ready-ok`。

### Current - Surface provider setup in recovery

- 扩展 ProjectView recovery：当没有 pending leader action、approval、inbox item 或 leader error，但配置的 API-backed Leader provider 缺少本地环境变量时，返回 `status=provider_setup_required`。
- `agentdeck continue` 现在会在该状态下推荐 `agentdeck doctor`，`recommended_action.source=provider_health`，帮助用户先完成 provider setup，而不是直接触发会失败的 `leader plan/chat`。
- `agentdeck workbench` 现在会把 `provider_health` 作为 `active_queue_source`，并在 `operator_card` 中暴露 `agentdeck doctor` 的 preview/explicit command，供 GUI/TUI 直接渲染 provider setup 操作面。
- 更新 `docs/contracts/project-view-schema.md`、`docs/contracts/workbench-schema.md`、`README.md`、`CLAUDE.md` 与 `AGENT.md`，明确 provider setup recovery 是只读诊断入口，不创建 plan/chat turn/approval/message/job/inbox，也不发送 tmux 输入。
- 保持优先级边界：已有 leader action、approval、inbox 或 leader error 仍优先于 provider setup；provider ready 时空状态仍为 idle。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_continue_surfaces_provider_setup_when_configured_leader_is_not_ready tests/test_agent_cli.py::test_workbench_surfaces_provider_setup_as_active_operator_source -q` 最初显示 recovery 仍为 `idle`；实现后 provider setup 目标测试 2 项通过；`conda run -n agentdeck pytest tests/test_agent_cli.py tests/test_contracts.py tests/test_leader_cli.py -q` 148 项通过；`conda run -n agentdeck pytest -q` 163 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时项目 smoke 确认 `continue-provider-setup-ok` 与 `workbench-provider-setup-ok`。

### Current - Use configured Leader provider by default

- 将 `agentdeck leader plan` 与 `agentdeck leader chat` 的默认 provider/model 从硬编码 `fake/fake-plan` 改为读取 `.agentdeck/config.toml` 的 `[leader] provider/model`。
- 新项目默认配置仍是 `deepseek` / `deepseek-chat`，因此自然语言 plan/chat 入口现在与 workbench 的 `leader_card`、`provider_health` 和 API-backed Leader 北极星保持一致。
- 保留本地 dry-run 能力：需要 fake provider 时显式传入 `--provider fake --model fake-plan`。
- 补充 CLI 测试，覆盖 plan/chat 不传 provider 时使用配置中的 DeepSeek provider/model；同时调整旧 dry-run 测试，使其显式表达 fake provider 意图。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，明确默认读取配置，fake 是显式 dry-run，不得自动 dispatch 或发送 tmux 输入。
- 保持安全边界：配置默认 provider 只影响 plan/chat 生成来源，仍不创建 approval、不 dispatch、不写 message/job/inbox、不发送 tmux 输入；provider 失败仍进入 `leader_errors[]`。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_plan_defaults_to_configured_leader_provider_and_model tests/test_leader_cli.py::test_leader_chat_defaults_to_configured_leader_provider_and_model -q` 最初显示实际 provider 仍是 `fake`；实现后 `tests/test_leader_cli.py` 54 项通过，provider/leader/agent 相关测试 97 项通过；`conda run -n agentdeck pytest -q` 161 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时项目 smoke 确认 `config-default-leader-ok`。

### Current - Enable DeepSeek provider planning

- 将 `deepseek` 从仅 doctor/health 可见的 provider 边界推进为真实 plan-only Leader provider：`leader_provider("deepseek")` 现在返回 `DeepSeekProvider`。
- `DeepSeekProvider` 复用 OpenAI-compatible `/chat/completions` plan schema，使用 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL` 和 `DEEPSEEK_MODEL`，默认 base URL 为 `https://api.deepseek.com/v1`，默认 model 为 `deepseek-chat`。
- 扩展 provider 与 CLI 测试，覆盖 DeepSeek env、请求 URL/header/body、JSON plan 解析、`agentdeck leader plan --provider deepseek` 只生成 plan 不 dispatch，以及未知 provider 仍明确失败。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，明确 DeepSeek 与 OpenAI-compatible 都只能生成 plan/chat turn，不得绕过 approval 或发送 tmux 输入。
- 保持安全边界：真实 DeepSeek provider 失败仍走 `leader_errors[]` 和 `leader_provider_failed`，成功也只写 plan，不写 approval/message/job/inbox，不发送 tmux 输入。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_provider_openai_compatible.py::test_deepseek_provider_uses_deepseek_env_and_openai_compatible_plan_shape tests/test_leader_cli.py::test_leader_plan_uses_deepseek_provider_without_dispatching tests/test_leader_cli.py::test_leader_plan_rejects_unknown_provider -q` 最初因 `leader_provider("deepseek")` unsupported 失败；随后补充 `DEEPSEEK_BASE_URL` 红测并确认默认 URL 覆盖 env 的失败；实现后 provider/leader 目标测试 7 项通过；`conda run -n agentdeck pytest -q` 159 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时项目 smoke 确认 `deepseek-plan-smoke-ok`。

### Current - Add provider health to workbench snapshot

- 扩展 `agentdeck workbench`：新增 `provider_health`，从 ProjectView leader provider 与本地环境变量派生 GUI/TUI 可渲染的 Leader provider 健康卡。
- `provider_health` 公开 provider、supported、ready、missing_env、detail 和 doctor_command，帮助 GUI 显示当前 API-backed Leader 是否已经具备本地调用条件。
- 扩展 workbench contract：新增 `WORKBENCH_PROVIDER_HEALTH_FIELDS`，并在 `agentdeck contract workbench` 暴露 `provider_health_fields`。
- 补充 CLI 与 contract 测试，覆盖 provider health discovery、默认 deepseek 缺失 `DEEPSEEK_API_KEY` 的 ready=false 投影、example 防漂移、validator 缺字段拒绝和 boolean 类型拒绝。
- 更新 `docs/contracts/workbench-schema.md`、`README.md`、`CLAUDE.md` 与 `AGENT.md`，明确 provider_health 只暴露缺失 env 名称，不暴露密钥值、不调用 provider。
- 保持安全边界：本轮仍只读，不写 state、不创建 chat turn、不 ack、不 approve、不 dispatch、不 capture reply、不读取 pane 输出、不发送 tmux 输入。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_accepts_example tests/test_contracts.py::test_validate_workbench_contract_requires_provider_health_fields tests/test_contracts.py::test_validate_workbench_contract_requires_provider_health_booleans -q` 最初因 `WORKBENCH_PROVIDER_HEALTH_FIELDS` 未出现在 contract 中失败；实现后 provider_health 目标测试 6 项通过；`conda run -n agentdeck pytest tests/test_contracts.py tests/test_agent_cli.py -q` 92 项通过；`conda run -n agentdeck pytest -q` 157 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `contract-provider-health-ok` 与 `workbench-provider-health-ok`。

### Current - Add leader card to workbench snapshot

- 扩展 `agentdeck workbench`：新增 `leader_card`，从 ProjectView leader 配置派生 GUI/TUI 可渲染的 API-backed Leader LLM 配置卡。
- `leader_card` 公开 agent_id、provider、model、approval_mode、api_backed，以及 chat/continue/actions/status 入口命令，帮助 GUI 展示当前 Leader 调度入口。
- 扩展 workbench contract：新增 `WORKBENCH_LEADER_CARD_FIELDS`，并在 `agentdeck contract workbench` 暴露 `leader_card_fields`。
- 补充 CLI 与 contract 测试，覆盖 leader card discovery、默认 deepseek Leader 投影、example 防漂移、validator 缺字段拒绝和只读状态不变性。
- 更新 `docs/contracts/workbench-schema.md`、`README.md`、`CLAUDE.md` 与 `AGENT.md`，明确 leader_card 不暴露 API key、不调用 provider、不创建 plan 或 action。
- 保持安全边界：本轮仍只读，不写 state、不创建 chat turn、不 ack、不 approve、不 dispatch、不 capture reply、不读取 pane 输出、不发送 tmux 输入。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_accepts_example tests/test_contracts.py::test_validate_workbench_contract_requires_leader_fields -q` 最初因 `WORKBENCH_LEADER_CARD_FIELDS` 未出现在 contract 中失败；实现后 leader_card 目标测试 5 项通过；`conda run -n agentdeck pytest tests/test_contracts.py tests/test_agent_cli.py -q` 90 项通过；`conda run -n agentdeck pytest -q` 155 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `contract-leader-ok` 与 `workbench-leader-ok`。

### Current - Add queue card to workbench snapshot

- 扩展 `agentdeck workbench`：新增 `queue_card`，从 ProjectView 的 leader_actions、approvals、inbox 和 recovery next_command 派生 GUI/TUI 可渲染的待处理队列总览。
- `queue_card` 公开 active_queue_source、next_command、leader_actions、approvals、inbox 和 refresh_command，供 GUI 渲染顶部队列状态条。
- 扩展 workbench contract：新增 `WORKBENCH_QUEUE_CARD_FIELDS`，并在 `agentdeck contract workbench` 暴露 `queue_card_fields`。
- 补充 CLI 与 contract 测试，覆盖 queue card discovery、pending inbox 队列投影、example 防漂移、validator 缺字段拒绝和只读状态不变性。
- 更新 `docs/contracts/workbench-schema.md`、`README.md`、`CLAUDE.md` 与 `AGENT.md`，明确 queue_card 是队列总览，不是执行入口。
- 保持安全边界：本轮仍只读，不写 state、不创建 chat turn、不 ack、不 approve、不 dispatch、不 capture reply、不读取 pane 输出、不发送 tmux 输入。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_accepts_example tests/test_contracts.py::test_validate_workbench_contract_requires_queue_fields -q` 最初因 `WORKBENCH_QUEUE_CARD_FIELDS` 未出现在 contract 中失败；实现后 queue_card 目标测试 5 项通过；`conda run -n agentdeck pytest tests/test_contracts.py tests/test_agent_cli.py -q` 89 项通过；`conda run -n agentdeck pytest -q` 154 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `contract-queue-ok` 与 `workbench-queue-ok`。

### Current - Add role card to workbench snapshot

- 扩展 `agentdeck workbench`：新增 `role_card`，从 ProjectView agents 派生 GUI/TUI 可渲染的角色化多 Agent 配置卡。
- `role_card` 公开 count、assign_command_template 和 agents[]；每个 agent 包含 agent_id、role、provider、workspace_mode、role_prompt 和可复制的 `assign_command`。
- 扩展 workbench contract：新增 `WORKBENCH_ROLE_CARD_FIELDS` 与 `WORKBENCH_ROLE_AGENT_FIELDS`，并在 `agentdeck contract workbench` 暴露 `role_card_fields` 与 `role_agent_fields`。
- 补充 CLI 与 contract 测试，覆盖 role card discovery、planner 角色投影、example 防漂移、validator 缺字段拒绝和只读状态不变性。
- 更新 `docs/contracts/workbench-schema.md`、`README.md`、`CLAUDE.md` 与 `AGENT.md`，明确 role_card 只来自 ProjectView agents 配置，不读取 pane、不 dispatch、不写角色配置。
- 保持安全边界：本轮仍只读，不写 state、不创建 chat turn、不 ack、不 approve、不 dispatch、不 capture reply、不读取 pane 输出、不发送 tmux 输入。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_accepts_example tests/test_contracts.py::test_validate_workbench_contract_requires_role_agent_fields -q` 最初因 `WORKBENCH_ROLE_AGENT_FIELDS` 未出现在 contract 中失败；实现后 role_card 目标测试 5 项通过；`conda run -n agentdeck pytest tests/test_contracts.py tests/test_agent_cli.py -q` 88 项通过；`conda run -n agentdeck pytest -q` 153 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `contract-role-ok` 与 `workbench-role-ok`。

### Current - Add audit card to workbench snapshot

- 扩展 `agentdeck workbench`：新增 `audit_card`，从 ProjectView recovery 的 latest_event 和 recent_events 派生 GUI/TUI 可渲染的最近审计卡。
- `audit_card` 公开 latest_event、recent_events、event_count 和 `events_command=agentdeck events --limit 20`，供 GUI 从一屏状态进入完整事件时间线。
- 扩展 workbench contract：新增 `WORKBENCH_AUDIT_CARD_FIELDS`，并在 `agentdeck contract workbench` 暴露 `audit_card_fields`。
- 补充 CLI 与 contract 测试，覆盖 audit card discovery、recent event 投影、example 防漂移、validator 缺字段拒绝和只读状态不变性。
- 更新 `docs/contracts/workbench-schema.md`、`README.md`、`CLAUDE.md` 与 `AGENT.md`，明确 audit_card 只来自 ProjectView recovery 事件摘要，不读取 pane、不写 state。
- 保持安全边界：本轮仍只读，不写 state、不创建 chat turn、不 ack、不 approve、不 dispatch、不 capture reply、不读取 pane 输出、不发送 tmux 输入。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_accepts_example tests/test_contracts.py::test_validate_workbench_contract_requires_audit_fields -q` 最初因 `WORKBENCH_AUDIT_CARD_FIELDS` 未出现在 contract 中失败；实现后 audit_card 目标测试 5 项通过；`conda run -n agentdeck pytest tests/test_contracts.py tests/test_agent_cli.py -q` 87 项通过；`conda run -n agentdeck pytest -q` 152 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `contract-audit-ok` 与 `workbench-audit-ok`。

### Current - Add preview command to workbench operator card

- 扩展 `agentdeck workbench` 的 `operator_card`：新增 `preview_command`，为 GUI/TUI 提供执行前的只读预览入口。
- `preview_command` 会按 action_kind 派生：leader_action 指向 `agentdeck leader action --action-id <id>`，inbox 指向 `agentdeck trace --id <inbox_id>`，approval 指向 `agentdeck approval list`，未知状态回退到 `agentdeck status`。
- 扩展 workbench contract：`WORKBENCH_OPERATOR_CARD_FIELDS` 新增 `preview_command`，example fixture 与 validator 一并覆盖。
- 补充 CLI 与 contract 测试，覆盖 operator card discovery、inbox preview 投影、example 防漂移和 validator 缺字段拒绝。
- 更新 `docs/contracts/workbench-schema.md`、`README.md`、`CLAUDE.md` 与 `AGENT.md`，明确 GUI 应优先渲染 `preview_command` 作为安全首击，再让人类显式执行 action。
- 保持安全边界：本轮仍只读，不写 state、不创建 chat turn、不 ack、不 approve、不 dispatch、不 capture reply、不读取 pane 输出、不发送 tmux 输入。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_accepts_example tests/test_contracts.py::test_validate_workbench_contract_requires_operator_fields -q` 最初因 `preview_command` 未出现在 contract/example/workbench payload 中失败；实现后 preview_command 目标测试 5 项通过；`conda run -n agentdeck pytest tests/test_contracts.py tests/test_agent_cli.py -q` 86 项通过；`conda run -n agentdeck pytest -q` 151 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `contract-preview-ok` 与 `workbench-preview-ok`。

### Current - Add operator card to workbench snapshot

- 扩展 `agentdeck workbench`：新增 `operator_card`，从 recovery recommended_action、continue_card 和 active queue 派生 GUI/TUI 可渲染的人类操作卡。
- `operator_card` 公开 status、reason、label、command、next_command、safety、requires_explicit_user、source、target_id、active_queue_source、action_kind、can_apply、apply_command、explicit_command 和 blocker，供 GUI 渲染下一步按钮、风险提示和阻塞原因。
- 扩展 workbench contract：新增 `WORKBENCH_OPERATOR_CARD_FIELDS`，并在 `agentdeck contract workbench` 暴露 `operator_card_fields`。
- 补充 CLI 与 contract 测试，覆盖 operator card discovery、inbox recovery 投影、example 防漂移、validator 缺字段拒绝和只读状态不变性。
- 更新 `docs/contracts/workbench-schema.md`、`README.md`、`CLAUDE.md` 与 `AGENT.md`，明确 operator_card 是人类操作描述，不是自动执行许可。
- 保持安全边界：本轮不写 state、不创建 chat turn、不 ack、不 approve、不 dispatch、不 capture reply、不读取 pane 输出、不发送 tmux 输入。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_accepts_example tests/test_contracts.py::test_validate_workbench_contract_requires_operator_fields -q` 最初因 `WORKBENCH_OPERATOR_CARD_FIELDS` 未出现在 contract 中失败；实现后 operator card 目标测试 5 项通过；`conda run -n agentdeck pytest tests/test_contracts.py tests/test_agent_cli.py -q` 86 项通过；`conda run -n agentdeck pytest -q` 151 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `contract-workbench-operator-ok` 与 `workbench-operator-ok`。

### Current - Add communication ledger card to workbench snapshot

- 扩展 `agentdeck workbench`：新增 `ledger_card`，从 ProjectView 的 messages、jobs、replies 和 inbox 摘要派生 GUI/TUI 可渲染的通信账本投影。
- `ledger_card` 复用 ProjectView summary shape，保留每条 message/job/reply 的 `trace_command`，并增加去重后的 `trace_commands` 快捷入口列表。
- 扩展 workbench contract：新增 `WORKBENCH_LEDGER_CARD_FIELDS`，并在 `agentdeck contract workbench` 暴露 `ledger_card_fields`。
- 补充 CLI 与 contract 测试，覆盖 ledger card discovery、messages/jobs/replies/inbox 投影、trace command 汇总、只读边界和 validator 缺 trace 拒绝。
- 更新 `docs/contracts/workbench-schema.md`、`README.md`、`CLAUDE.md` 与 `AGENT.md`，明确 ledger_card 只来自 ProjectView，不读取 pane 输出，不复制长 prompt。
- 保持安全边界：本轮不写 state、不创建 chat turn、不 ack、不 approve、不 dispatch、不 capture reply、不读取 pane 输出、不发送 tmux 输入。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_runtime_ledger_and_active_inbox_cards_without_mutating_state -q` 最初因 `ledger_card` 未出现在 contract/workbench payload 中失败；实现后 ledger card 目标测试 5 项通过；`conda run -n agentdeck pytest tests/test_contracts.py tests/test_agent_cli.py -q` 85 项通过；`conda run -n agentdeck pytest -q` 150 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `contract-workbench-ledger-ok` 与 `workbench-ledger-ok`。

### Current - Add runtime card to workbench snapshot

- 扩展 `agentdeck workbench`：新增 `runtime_card`，从 ProjectView 的 `runtime_backend` 和 `agents[]` 派生可渲染的 tmux runtime 投影。
- `runtime_card.agents[]` 公开 agent_id、role、provider、workspace_mode、status、pane_id、session_name、cwd，以及 `spawn_command`、`stop_command`、`inbox_command`，供 GUI/TUI 直接渲染 agent runtime 控制面。
- 扩展 workbench contract：新增 `WORKBENCH_RUNTIME_CARD_FIELDS`、`WORKBENCH_RUNTIME_AGENT_FIELDS`，并在 `agentdeck contract workbench` 暴露 `runtime_card_fields` 与 `runtime_agent_fields`。
- 补充 CLI 与 contract 测试，覆盖 runtime card discovery、running/configured 状态统计、agent runtime 字段、命令建议和 validator 缺字段拒绝。
- 更新 `docs/contracts/workbench-schema.md`、`README.md`、`CLAUDE.md` 与 `AGENT.md`，明确 runtime_card 只来自 ProjectView，不读取 pane 输出，不把 tmux pane 当业务事实源。
- 保持安全边界：本轮不写 state、不创建 chat turn、不 ack、不 approve、不 dispatch、不 capture reply、不读取 pane 输出、不发送 tmux 输入。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_runtime_and_active_inbox_cards_without_mutating_state -q` 最初因 `runtime_card` 未出现在 contract/workbench payload 中失败；实现后 runtime card 目标测试 5 项通过；`conda run -n agentdeck pytest tests/test_contracts.py tests/test_agent_cli.py -q` 84 项通过；`conda run -n agentdeck pytest -q` 149 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `contract-workbench-runtime-ok` 与 `workbench-runtime-ok`。

### Current - Add read-only workbench snapshot for GUI/TUI recovery

- 新增 `agentdeck workbench`：面向未来 GUI/TUI 的只读一屏快照，组合 ProjectView、leader_actions、recovery、continue_card、active_queue_source、inbox_card、approval_card 和 leader_action。
- 新增 workbench contract：`WORKBENCH_SNAPSHOT_FIELDS`、`workbench_contract_payload()`、`workbench_contract_response()`、`workbench_example()` 和 `validate_workbench_contract()`，并新增 `agentdeck contract workbench --example`。
- 新增 `docs/contracts/workbench-schema.md`，明确 workbench 是 ProjectView 的组合投影，不是第二套状态源，也不是执行入口。
- 补充 CLI 与 contract 测试，覆盖 workbench contract discovery、inbox recovery 快照、example 防漂移、validator 复用 continue_card 校验和 ProjectView summary 一致性。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，将 workbench 记录为 GUI/TUI 优先的一屏只读入口。
- 保持安全边界：本轮只读快照不创建 plan、不记录 chat turn、不 ack、不 approve、不 dispatch、不 capture reply、不发送 tmux 输入。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_contract_workbench_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_workbench_embeds_active_inbox_card_without_mutating_state -q` 最初因 `workbench` / `contract workbench` 未注册失败；实现后 workbench 目标测试 6 项通过；`conda run -n agentdeck pytest tests/test_contracts.py tests/test_agent_cli.py -q` 83 项通过；`conda run -n agentdeck pytest -q` 148 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `contract-workbench-ok` 与 `workbench-inbox-ok`。

### Current - Embed recovery queue cards in Leader continue chat

- 扩展 `leader chat --message "继续"` 的 recovery-first 响应：当 `status.recovery.recommended_action.source=inbox` 时，同时嵌入对应 agent 的 `inbox_card`；当 source 为 `approval` 时，同时嵌入 `approval_card`。
- 新增 `_leader_chat_recovery_cards()` 与 `_inbox_agent_id_for_item()`，复用既有 `agentdeck inbox --agent <id>` 和 `agentdeck approval list` 的 queue payload shape，不新增第二套 GUI contract。
- 补充 continue-mode 测试，覆盖 approval dispatch-ready 与 inbox pending 两条恢复路径，并确认只记录 chat turn，不 ack inbox、不 approve/dispatch、不创建 message/job、不发送 tmux 输入。
- 更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md` 与 `AGENT.md`，明确自然语言“继续”可直接携带可渲染队列卡片，同时仍保持显式人类执行边界。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_continue_returns_recovery_card_without_creating_action tests/test_leader_cli.py::test_leader_chat_continue_embeds_inbox_card_for_pending_inbox -q` 最初因 `approval_card` / `inbox_card` 仍为 `None` 失败；实现后目标 leader-chat/contract 测试 3 项通过；`conda run -n agentdeck pytest -q` 142 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认自然语言 `继续` 分别返回 `continue-approval-card-ok` 与 `continue-inbox-card-ok`。

### Current - Point inbox recovery at concrete mailbox commands

- 调整 `status.recovery.status=inbox_pending` 的下一步命令：从宽泛的 `agentdeck status` 改为具体 `agentdeck inbox --agent <id>`。
- `recommended_action.command` 与 `next_command` 保持一致，仍为 inspect 安全级别，并继续用 pending inbox item 的 `inbox_id` 作为 `target_id`。
- 新增 `_inbox_item_agent_id()`，优先使用 inbox item 的 `to_agent`，并兼容旧 state 中缺少 `to_agent` 时从 inbox map owner 反查 agent id。
- 更新 `docs/contracts/project-view-schema.md`、`README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 GUI/continue 可直接打开具体 mailbox。
- 保持安全边界：本轮只调整只读 recovery 推荐命令，不 ack inbox、不 dispatch、不 capture reply、不发送 tmux 输入。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_agent_cli.py::test_status_recovery_matrix_for_gui_actions -q` 最初因 `recommended_action.command` 仍为 `agentdeck status` 失败；实现后 recovery/continue 目标测试 2 项通过；ProjectView contract 目标测试 2 项通过；`conda run -n agentdeck pytest -q` 141 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `agentdeck status` 返回 `status-inbox-command-ok`，`agentdeck continue` 返回 `continue-inbox-command-ok`。

### Current - Suggest approved approval dispatch through Leader chat

- 新增 `leader chat` approval dispatch 意图识别：`派发当前审批` 会在存在 approved approval 时建议第一条 approved approval 的 `dispatch_command`。
- dispatch 意图仍保持 `mode=approval`，并在 `leader_explanation` 中标记 `action_kind=approval_dispatch`、`safety=explicit_runtime`、`requires_explicit_user=true`。
- `leader chat` 只建议 approved approval 的 dispatch 命令，不执行 `approval dispatch`，不创建 message/job/inbox，不发送 tmux 输入。
- 更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md` 与 `AGENT.md`，明确 approval chat 可以推荐 `dispatch_command`，但执行仍必须由人显式运行。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_suggests_dispatch_for_approved_approval_without_dispatching -q` 最初因 `next_command` 仍为 `agentdeck approval list` 失败；实现后 approval dispatch/inspect/approve 目标测试 3 项通过；leader-chat contract 目标测试 2 项通过；`conda run -n agentdeck pytest -q` 141 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `派发当前审批` 返回 `approval-dispatch-ok`，并确认 dispatch 未执行、message/job/inbox 仍为空。

### Current - Route approval intents through Leader chat

- 扩展 `leader chat` response contract，新增正式 `approval_card` 字段，并在 `validate_leader_chat_contract()` 中复用 `validate_approval_contract()` 校验嵌入 approval queue。
- 新增 `leader chat` 的只读 `mode=approval`：`查看审批` 会返回 `approval_card` 并建议 `agentdeck approval list`。
- 新增 approval approve 意图：`批准当前审批` 会在存在 pending approval 时建议第一条 pending approval 的 `approve_command`，并标记 `safety=explicit_runtime`、`requires_explicit_user=true`。
- 将 `agentdeck approval list` 的 queue payload 抽成 `_approval_queue_payload()`，让 CLI approval list 与自然语言审批视图共享同一 shape。
- 更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 approval chat 模式、`approval_card` 校验和只读/显式执行边界。
- 保持安全边界：本轮自然语言入口只建议 approval list/approve 命令并记录 chat turn，不执行 approve/reject/dispatch，不创建 plan、不创建 leader action、不发送 tmux 输入。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_inspects_approval_queue_without_mutating_state tests/test_leader_cli.py::test_leader_chat_suggests_approve_for_pending_approval_without_approving -q` 最初因 chat 误走 `mode=review` 失败；实现后 approval chat 目标测试 2 项通过；leader-chat contract 目标测试 3 项通过；`conda run -n agentdeck pytest -q` 140 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `查看审批` 返回 `approval-chat-ok`，`批准当前审批` 返回 `approval-approve-ok`，并确认第一条 approval 仍为 pending。

### Current - Suggest inbox ack commands through Leader chat

- 新增 `leader chat` inbox ack 意图识别：`确认 planner 当前 inbox` 会复用 `inbox_card.items[0].ack_command` 作为 `next_command`。
- ack 意图仍保持 `mode=inbox`，并在 `leader_explanation` 中标记 `action_kind=inbox_ack`、`safety=explicit_runtime`、`requires_explicit_user=true`。
- `leader chat` 只建议当前 head 的 ack 命令，不执行 `ack`，不修改 inbox item 状态，不创建 plan 或 leader action，不 dispatch、不 capture reply、不发送 tmux 输入。
- 更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md` 与 `AGENT.md`，明确 inbox chat 可以推荐 `ack_command`，但执行仍必须由人显式运行。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_suggests_ack_for_current_inbox_head_without_acknowledging -q` 最初因 `next_command` 仍为 `agentdeck inbox --agent planner` 失败；实现后 inbox chat 目标测试 3 项通过；leader-chat contract 目标测试 2 项通过；`conda run -n agentdeck pytest -q` 138 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `agentdeck leader chat --message "确认 planner 当前 inbox"` 返回 `ack-chat-ok`，并确认 inbox item 仍为 pending。

### Current - Route natural-language inbox inspection through Leader chat

- 扩展 `leader chat` response contract，新增正式 `inbox_card` 字段，并在 `validate_leader_chat_contract()` 中复用 `validate_inbox_contract()` 校验嵌入 inbox queue。
- 新增 `leader chat` 的只读 `mode=inbox`：`查看 planner inbox` 会返回 `inbox_card` 并建议 `agentdeck inbox --agent planner`。
- 新增 inbox trace 意图：`追踪 planner 当前 inbox` 会在存在 pending head 时建议该 head 的 `agentdeck trace --id <inbox_id>`。
- 将 `agentdeck inbox --agent <id>` 的 queue payload 抽成 `_inbox_queue_payload()`，让 CLI inbox 与自然语言 inbox 视图共享同一 shape。
- 更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 inbox chat 模式、`inbox_card` 校验和只读边界。
- 保持安全边界：本轮自然语言入口只建议 inspect/trace 命令并记录 chat turn，不创建 plan、不创建 leader action、不 ack inbox、不 dispatch、不 capture reply、不发送 tmux 输入。
- 完整验证：先确认红测失败，`conda run -n agentdeck pytest tests/test_leader_cli.py::test_leader_chat_inspects_agent_inbox_without_mutating_runtime tests/test_leader_cli.py::test_leader_chat_suggests_trace_for_current_inbox_head -q` 最初因 chat 误走 `mode=plan` 失败；实现后目标 leader chat/inbox 测试 4 项通过；leader-chat contract 目标测试 4 项通过；`conda run -n agentdeck pytest -q` 137 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `agentdeck leader chat --message "查看 planner inbox"` 返回 `inbox-chat-ok`，`agentdeck leader chat --message "追踪 planner 当前 inbox"` 返回 `inbox-trace-ok`。

### Current - Discover and validate inbox queue contract

- 新增 `INBOX_QUEUE_FIELDS`、`INBOX_ITEM_FIELDS`、`inbox_contract_payload()`、`inbox_contract_response()`、`inbox_example()` 和 `validate_inbox_contract()`，为单 agent mailbox 建立可复用契约。
- 新增 `agentdeck contract inbox` 和 `agentdeck contract inbox --example`，供 GUI、自然语言壳或外部集成发现 inbox queue 字段。
- `agentdeck inbox --agent <id>` 现在会把 raw inbox item 标准化为固定字段，并为每项补充 `trace_command`、`ack_command`、`is_head`、`can_ack` 和 `ack_blocker`。
- `agentdeck inbox --agent <id>` 输出前会通过 `validate_inbox_contract()` 自校验；校验失败时返回非 0 且不输出半坏 inbox queue。
- 新增 `docs/contracts/inbox-schema.md`，记录 inbox queue shape、head-only ack 语义和只读边界。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 inbox contract discovery 与输出自校验规则。
- 保持安全边界：本轮只增加只读 contract discovery、inbox 字段标准化与输出校验，不 ack inbox、不 dispatch、不 capture reply、不发送 tmux 输入、不改变 state mutation 语义。
- 完整验证：`conda run -n agentdeck pytest tests/test_contracts.py::test_inbox_contract_payload_is_reusable_without_cli tests/test_contracts.py::test_inbox_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_inbox_contract_accepts_example tests/test_contracts.py::test_validate_inbox_contract_requires_head_ack_fields -q` 4 项通过；inbox contract/CLI 目标测试 4 项通过；`conda run -n agentdeck pytest -q` 135 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `agentdeck contract inbox --example` 返回 `contract-ok`，`agentdeck inbox --agent planner` 返回 `inbox-ok` 且 pending item 包含正确的 `trace_command` 与 `ack_command`。

### Current - Discover and validate approval queue contract

- 新增 `APPROVAL_QUEUE_FIELDS`、`APPROVAL_ITEM_FIELDS`、`approval_contract_payload()`、`approval_contract_response()`、`approval_example()` 和 `validate_approval_contract()`，为人类审批队列建立可复用契约。
- 新增 `agentdeck contract approvals` 和 `agentdeck contract approvals --example`，供 GUI、自然语言壳或外部集成发现 approval queue 字段。
- `agentdeck approval list` 现在每个 approval item 都包含 `approve_command`、`reject_command`、`dispatch_command`、`can_dispatch` 和 `dispatch_blocker`，并在输出前通过 `validate_approval_contract()` 自校验。
- 新增 `docs/contracts/approvals-schema.md`，记录 approval queue shape、GUI 控制字段和只读边界。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 approval queue contract discovery 与输出自校验规则。
- 保持安全边界：本轮只增加只读 contract discovery、审批队列字段补齐与输出校验，不 approve、不 reject、不 dispatch、不发送 tmux 输入、不改变 state mutation 语义。
- 完整验证：`conda run -n agentdeck pytest tests/test_contracts.py -q` 36 项通过；approvals 相关 CLI 目标测试 4 项通过；`conda run -n agentdeck pytest -q` 128 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `agentdeck contract approvals --example` 返回 `contract-ok`，`agentdeck approval list` 返回 `approval-ok` 且审批项包含 `approve_command/can_dispatch/dispatch_blocker`。

### Current - Discover and validate Leader actions queue contract

- 新增 `LEADER_ACTIONS_LIST_FIELDS`、`leader_actions_contract_payload()`、`leader_actions_contract_response()`、`leader_actions_example()` 和 `validate_leader_actions_contract()`，为 Leader action queue 建立可复用契约。
- 新增 `agentdeck contract leader-actions` 和 `agentdeck contract leader-actions --example`，供 GUI、自然语言壳或外部集成发现 action queue 字段。
- `agentdeck leader actions` 现在每个 action item 都包含 `can_apply`、`apply_command`、`explicit_command`、`apply_blocker` 和 `is_recommended`，并在输出前通过 `validate_leader_actions_contract()` 自校验。
- 新增 `docs/contracts/leader-actions-schema.md`，记录队列 shape、ProjectView `leader_actions.items[]` 字段复用关系和只读边界。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 Leader actions queue contract discovery 与输出自校验规则。
- 保持安全边界：本轮只增加只读 contract discovery、队列字段补齐与输出校验，不 apply action、不创建 approval、不 dispatch、不发送 tmux 输入、不改变 state mutation 语义。
- 完整验证：`conda run -n agentdeck pytest tests/test_contracts.py -q` 32 项通过；leader-actions 相关 CLI 目标测试 4 项通过；`conda run -n agentdeck pytest -q` 121 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `agentdeck contract leader-actions --example` 返回 `contract-ok`，`agentdeck leader actions` 返回 `queue-ok` 且队列推荐项包含 `can_apply/apply_command`。

### Current - Discover and validate Leader action detail contract

- 新增 `LEADER_ACTION_DETAIL_FIELDS`、`leader_action_contract_payload()`、`leader_action_contract_response()`、`leader_action_example()` 和 `validate_leader_action_contract()`，为单个 Leader action 详情建立可复用契约。
- 新增 `agentdeck contract leader-action` 和 `agentdeck contract leader-action --example`，供 GUI、自然语言壳或外部集成发现 action detail 字段。
- `agentdeck leader action --action-id <id>` 现在输出前会通过 `validate_leader_action_contract()` 自校验；校验失败时返回非 0 且不输出半坏 action detail。
- 新增 `docs/contracts/leader-action-schema.md`，记录 action detail shape、recovery/recommended_action 关系、`matches_recommended_action` 语义和只读边界。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 Leader action detail contract discovery 与输出自校验规则。
- 保持安全边界：本轮只增加只读 contract discovery 与输出校验，不 apply action、不创建 approval、不 dispatch、不发送 tmux 输入、不改变 state mutation 语义。
- 完整验证：`conda run -n agentdeck pytest tests/test_contracts.py -q` 28 项通过；leader-action 相关 CLI 目标测试 3 项通过；`conda run -n agentdeck pytest -q` 114 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；临时 git 项目 smoke 确认 `agentdeck contract leader-action --example` 返回 `contract-ok`，`agentdeck leader action --action-id <id>` 返回 `detail-ok` 且匹配当前 recovery 推荐 action。

### Current - Reuse continue card validator in Leader chat

- `validate_leader_chat_contract()` 现在会对嵌入的 `continue_card` 复用 `validate_continue_contract()`，并把嵌套错误前缀为 `continue_card:`。
- 防止自然语言 `继续` 响应接受一张独立 `agentdeck continue` 会拒绝的恢复卡片。
- 新增测试先确认删除 `continue_card.pending.leader_errors` 曾被错误放行；实现后目标测试转绿。
- 更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 Leader chat continue card 必须复用同一套恢复卡片 validator。
- 保持安全边界：本轮只强化 contract validation，不改变 runtime、审批、dispatch、tmux 输入或 state 写入语义。
- 完整验证：`conda run -n agentdeck pytest tests/test_contracts.py -q` 24 项通过；`conda run -n agentdeck pytest -q` 107 项通过；`conda run -n agentdeck python -m compileall src tests` 通过；`git diff --check` 通过；smoke 确认正常 `leader_chat_example()` 通过，删除 `continue_card.pending.leader_errors` 后返回 `continue_card: missing pending field: leader_errors`。

### Current - Validate continue recovery card output

- 新增 `validate_continue_contract()`，用于校验 `agentdeck continue` 输出是否满足 `CONTINUE_CARD_FIELDS`、`mode=continue`、ProjectView schema version、recommended_action 和 pending 字段契约。
- `agentdeck continue` 现在在输出 JSON 前先通过 ProjectView contract 守门，再通过 continue card contract 自校验；校验失败时返回非 0 且不输出半坏恢复卡片。
- 新增 CLI 测试，模拟 `_continue_card_payload()` 丢失 `next_command`，确认命令报错 `Continue card contract validation failed` 且 stdout 为空。
- 更新 `docs/contracts/continue-card-schema.md`、`README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 continue card 输出前必须自校验。
- 保持安全边界：本轮只增加恢复卡片自校验，不改变 `agentdeck continue` 的只读语义，不创建 action、不 apply、不 dispatch、不发送 tmux 输入。
- 本地验证：先运行目标测试看到 `validate_continue_contract` 缺失的红灯；实现后目标测试 3 项通过，continue/contract 相关测试 27 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 106 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `agentdeck continue` 输出 `mode=continue/status=action_required`，且 `validate_continue_contract()` 返回 ok。

### Current - Discover continue recovery card contract

- 新增 `agentdeck contract continue`，用于只读发现顶层 `agentdeck continue` 的恢复卡片契约。
- `agentdeck contract continue --example` 现在返回稳定 `example_continue_card` 和 `example_continue_card_fields`，供 GUI 或外部集成直接消费。
- 新增 `docs/contracts/continue-card-schema.md`，记录 continue card shape、ProjectView 依赖和只读边界。
- `continue_example()` 现在复用 ProjectView 示例派生稳定恢复卡片，`leader_chat_example()` 也复用同一份 continue card 示例，避免字段漂移。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 `agentdeck contract continue` 是只读契约发现入口。
- 保持安全边界：本轮只扩展 contract discovery、example 和文档，不改变 `agentdeck continue`、`leader chat`、审批、dispatch、trace 或 tmux 执行语义。
- 本地验证：先运行目标测试看到 `continue_contract_payload` 缺失的红灯；实现后目标测试 4 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 103 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，CLI smoke 确认 `agentdeck contract continue --example` 返回 `continue_command=agentdeck continue`、`contract_exists=true`、example 字段列表与 card 匹配，且 `mode=continue`。

### Current - Discover Leader chat continue card contract

- `agentdeck contract leader-chat` 现在公开 `continue_card_fields`，让 GUI 和自然语言壳可以发现 continue-mode 的 recovery card 字段。
- `agentdeck contract leader-chat --example` 现在返回 `example_continue_card_fields`，并把稳定示例切换为 `mode=continue`，包含 `continue_card`。
- `LEADER_CHAT_RESPONSE_FIELDS` 现在包含 `continue_card`，`validate_leader_chat_contract()` 会校验响应中出现的 `continue_card` 字段是否满足 `CONTINUE_CARD_FIELDS`。
- 更新 `docs/contracts/leader-chat-schema.md`、`README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 continue card 是机器可发现契约。
- 保持安全边界：本轮只扩展 contract discovery、example 和 validator，不改变 `agentdeck continue`、`leader chat`、审批、dispatch、trace 或 tmux 执行语义。
- 本地验证：先运行目标测试看到 `CONTINUE_CARD_FIELDS` 缺失的红灯；实现后 `tests/test_contracts.py` 19 项通过，leader-chat contract CLI 相关 3 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 99 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，CLI smoke 确认 `agentdeck contract leader-chat --example` 返回 `continue_card_fields`、`example_mode=continue`、`response_fields` 包含 `continue_card`，且 example 字段列表与 `continue_card` 匹配。

### Current - Route Leader chat continue through recovery card

- `agentdeck leader chat --message "继续"`、`"继续吧"` 和 `"/continue"` 现在进入 recovery-first 的 `mode=continue`。
- continue-mode chat 会复用 `agentdeck continue` 的下一步卡片，返回 `continue_card`、`recovery`、`next_command` 和 `leader_explanation`，方便自然语言壳和 GUI 直接展示当前恢复入口。
- continue-mode 只记录一条 `chat_turns[]`，不会调用 `leader_review()` 创建新的 `leader_actions[]`，不会 apply action、dispatch 或发送 tmux 输入。
- 抽出 `_continue_card_payload()`，让顶层 `agentdeck continue` 和自然语言 continue 共用同一张 recovery card，避免两套字段漂移。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录自然语言“继续”的 recovery-first 边界。
- 保持安全边界：本轮只调整自然语言 continue 的只读恢复解释，不改变普通 plan/review、显式 apply-action、审批、dispatch、trace 或 tmux 执行语义。
- 本地验证：先运行目标测试看到 `"继续"` 仍返回 `mode=review` 的红灯；实现后目标测试 2 项通过，`tests/test_leader_cli.py tests/test_agent_cli.py` 67 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 99 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `leader chat --message "继续"` 返回 `mode=continue`、`continue_card.status=dispatch_ready`、`project_view.leader_actions.count=0`，且当前 chat turn 的 action_id 为 `null`。

### Current - Add read-only continue recovery card

- 新增顶层命令 `agentdeck continue`，把 ProjectView `status.recovery` 整理成一张下一步卡片，返回 status、reason、next_command、recommended_action、pending、project_view_command，以及可选的 `leader_action` 详情和 `action_detail_command`。
- `agentdeck continue` 会先通过 `validate_project_view_contract()` 守门；ProjectView 漂移时返回非 0 且不输出半坏下一步建议。
- 对 `source=leader_action` 的 recommended action，`continue` 会复用 `leader_action_detail()` 暴露 can_apply/apply_command/explicit_command/apply_blocker，方便 GUI 或自然语言壳在执行前展示详情。
- 新增只读性测试，确认 `continue` 不修改 `.agentdeck/state/state.json`，不创建 action、不 apply action、不 dispatch、不发送 tmux 输入。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 `agentdeck continue` 是只读恢复入口，不是自动执行器。
- 保持安全边界：本轮只新增只读恢复卡片，不改变 `leader next`、`leader chat`、审批、dispatch、trace 或 tmux 执行语义。
- 本地验证：先运行目标测试看到 `agentdeck continue` 命令不存在的红灯；实现后目标测试 2 项通过，`tests/test_agent_cli.py tests/test_leader_cli.py` 67 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 99 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `agentdeck continue` 返回 `status=action_required`、safe apply next_command、leader_action.can_apply=true，且 state 文件 hash 不变。

### Current - Surface ProjectView summary trace commands

- `agentdeck status` 的 `messages.items[]`、`jobs.items[]` 和 `replies.items[]` 现在都会包含 `trace_command`，让 GUI、人类和 Leader chat loop 可以从 ProjectView 摘要直接跳到通信 lineage。
- `agentdeck contract project-view` 现在公开 `message_item_fields`、`job_item_fields` 和 `reply_item_fields`，`--example` 同步公开对应 `example_*_item_fields`，方便 GUI 做 discovery 和字段兼容检查。
- `project_view_example()` 现在包含稳定的 message/job/reply 示例，避免契约发现只覆盖空 summary。
- `validate_project_view_contract()` 现在会拒绝缺失 `trace_command` 的 message/job/reply summary item，防止 GUI-facing ProjectView 悄悄丢失追踪入口。
- 更新 `docs/contracts/project-view-schema.md`、`README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 ProjectView summary trace command 是稳定契约。
- 保持安全边界：本轮只扩展只读 status/contract/example/validator 字段，不改变 dispatch、reply、trace、审批或 tmux 执行语义。
- 本地验证：先运行目标测试看到 `PROJECT_VIEW_*_ITEM_FIELDS` 缺失的红灯；实现后目标测试 6 项通过，`tests/test_contracts.py tests/test_agent_cli.py` 43 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 97 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `agentdeck status` 的 message/job/reply summary 都返回 `trace_command`。

### Current - Return trace commands from communication outputs

- `dispatch`、`approval dispatch`、`reply` 和 `capture-reply` 的成功 JSON 输出现在都会包含 `trace_command`。
- dispatch 类输出的 `trace_command` 指向对应 `message_id`，reply 类输出的 `trace_command` 指向对应 `reply_id`，方便 GUI、人类和 Leader 直接打开 lineage。
- 新增 `_trace_command()` helper，统一生成 `agentdeck trace --id <id>`，避免多个命令手写漂移。
- 扩展 dispatch/reply/capture-reply/approval dispatch 红灯测试，先确认成功输出缺少 `trace_command`，再实现统一输出。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录通信命令输出必须携带 trace 入口。
- 保持安全边界：本轮只扩展成功响应字段，不改变 dispatch/reply/capture 的执行语义，不新增自动审批或自动派发。
- 本地验证：目标测试 4 项通过，`tests/test_dispatch_cli.py tests/test_leader_cli.py` 51 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 96 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `agentdeck reply` 输出的 `trace_command` 指向新建 `reply_id`。

### Current - Continue after Leader chat safe apply

- `agentdeck leader chat --message "apply action <id>"` 应用 safe `create_approvals` action 后，现在会从刷新后的 ProjectView recovery 读取下一步命令。
- apply-action chat 响应的顶层 `next_command` 和 `leader_explanation.next_command` 现在会等于 `recovery.next_command`，创建审批队列后会指向 `agentdeck approval list`。
- 同次写入的 `chat_turns[]` 会记录 apply 后的 next_command，方便自然语言历史和 GUI 从 safe apply 继续进入审批检查。
- 扩展 leader chat 红灯测试，先确认 apply-action 响应顶层 `next_command` 仍为 `None`，再实现与 recovery 对齐。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录 safe apply 完成后必须从 recovery 继续给出下一步。
- 保持安全边界：本轮只补齐 apply 后的只读恢复入口，不自动 approve、不 dispatch、不发送 tmux 输入。
- 本地验证：先运行 `test_leader_chat_applies_create_approvals_action_when_explicitly_requested` 看到 `next_command=None` 的红灯；实现后同一测试通过，`tests/test_leader_cli.py tests/test_contracts.py` 59 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 96 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认 `apply_action` 响应进入 `approval_required`，顶层/解释层/chat_turn 的 next_command 均为 `agentdeck approval list`，且 `messages/jobs` 仍为 0。

### Current - Queue safe action after Leader chat plan

- `agentdeck leader chat --message <text>` 在无 plan 时创建 plan-only 记录后，现在会立即持久化或复用一条 safe `create_approvals` Leader action。
- plan-mode chat 响应现在会返回 `leader_action`、`leader_actions.recommended_action_id`、`recovery.status=action_required`，且 `next_command` 来自 `recovery.next_command` 的 safe `agentdeck leader apply-action --action-id <id>`。
- 同次写入的 `chat_turns[]` 会记录 action_id/action_kind，让 GUI 或自然语言历史可以从首次 plan turn 直接跳到 action queue。
- `leader_explanation` 在 plan mode 下会说明推荐的 `create_approvals` action，`safety=safe_apply`，`requires_explicit_user=false`；这只表示可安全创建审批队列，不表示自动 dispatch。
- 扩展 leader chat 红灯测试，先确认 plan-mode `leader_action` 仍为 `None`，再实现 action queue 推荐和 recovery 对齐。
- 更新 `README.md`、`CLAUDE.md` 与 `AGENT.md`，记录首次 chat plan 会持久化 safe action，但不会创建 approval、不会 dispatch、不会发送 tmux 输入。
- 保持安全边界：本轮只写入 Leader action 建议，不应用 action、不创建 approval、不 dispatch、不发送 tmux 输入。
- 本地验证：先运行 `test_leader_chat_creates_plan_from_natural_language_without_dispatching` 看到 `leader_action` 为 `None` 的红灯；实现后同一测试通过，`tests/test_leader_cli.py tests/test_contracts.py` 59 项通过。
- 完整验证：`conda run -n agentdeck pytest -q` 96 项通过，`conda run -n agentdeck python -m compileall src tests` 通过，临时 git 项目 smoke 确认首次 chat 返回 `create_approvals` safe action、`recovery.status=action_required`、`next_command` 等于 `apply_command`，且 `approvals/messages/jobs` 仍为 0。

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
