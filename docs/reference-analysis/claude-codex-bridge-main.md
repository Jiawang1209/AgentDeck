# CCB 参考仓库深度研究报告

> 审查对象：`References/claude_codex_bridge-main`  
> 审查方式：subagent 只读源码分析，主线程整理落盘。  
> 重点：多 Agent 通信、daemon 控制面、tmux 可见运行时、mailbox/message-bureau 设计。

## 1. 项目一句话定位

CCB 是一个以 `.ccb` 项目为边界、以 agent name 为第一身份、以 tmux 可见终端为执行面、以 Python `ccbd` daemon 为控制面、以 mailbox/message-bureau 为通信内核的多 provider 多智能体本地工作台。

## 2. 技术栈与运行形态

- 语言与分发：npm 包 `@seemseam/ccb`，Node 入口很薄，`bin/ccb.js` / `bin/ask.js` 转交 Python runner；主实现是 Python。
- CLI 形态：`ccb` 是统一入口，`ask` 是兼容 alias，核心命令包括 `ask`、`pend`、`watch`、`queue`、`inbox`、`ack`、`retry`、`resubmit`、`project_view`、`restart`、`kill`。
- 后台 daemon：每个项目由 `ccbd` 作为项目唯一后端，通过 Unix socket RPC 接收 CLI/sidebar/MCP/mobile 请求。
- Rust helper：`ccb-runtime-accelerator` 加速 Codex log 观察与进程 baseline；`ccb-rs-helper` 做 JSON/存储/原生输出等热路径辅助；`ccb-agent-sidebar` 是 Rust TUI sidebar。
- 终端运行时：以 tmux 为主，支持项目专属 socket/session/window/pane namespace，pane 通过 tmux user options 标记归属。
- 通信与存储：JSONL append-only stores 保存 jobs/events/submissions/messages/attempts/replies/inbound events；JSON 保存 mailbox summary、leases、runtime records、execution state。
- 支持 provider：README/package 声明 Codex、Claude、Gemini、Kimi、MiMo、Qwen、Cursor、Copilot、Crush、Kiro、Pi、Z.ai、OpenCode、Antigravity、Droid 等 15 类 CLI family。
- 测试：pytest 覆盖配置、daemon/socket、dispatcher、mailbox、message bureau、tmux、provider binding、runtime restore、completion、retry/recover；Rust sidebar/helper 也有单元测试。

## 3. 源码结构地图

- `bin/`：npm 暴露的 CLI wrapper。`ccb.js`、`ask.js` 只调用 `ccb-npm-runner`；`ask.py` 进入 `lib/ask_cli/main.py`。
- `lib/cli/`：命令解析、上下文构建、daemon 连接、输出渲染。`phase2_runtime/handlers_ask.py`、`handlers_mailbox.py` 将命令委托给 service。
- `lib/ccbd/`：项目 daemon。`app.py` 组装生命周期；`socket_client.py` / `socket_server.py` 定义 RPC；`handlers/` 是 RPC operation 到 service 的适配层。
- `lib/ccbd/services/dispatcher.py` 与 `dispatcher_runtime/`：任务提交、队列、运行、轮询、完成、取消、retry、resubmit、reply delivery、comms recover。
- `lib/message_bureau/`：通信事实账本。保存 `MessageRecord`、`AttemptRecord`、`ReplyRecord`、`CallbackEdgeRecord`，并提供 queue/inbox/trace 控制视图。
- `lib/mailbox_kernel/`：邮箱内核。保存 `MailboxRecord`、`InboundEventRecord`、`DeliveryLease`，实现 claim/consume/ack/rebuild summary。
- `lib/jobs/`：job/submission/event append-only store。
- `lib/provider_core/`：provider catalog、manifest、协议包装、runtime identity、session binding evidence。
- `lib/provider_execution/`：统一 execution service，负责 start/poll/cancel/restore provider jobs。
- `lib/provider_backends/`：Codex/OpenCode/Claude/Gemini/Kimi/Qwen 等 provider-specific execution、session、completion 逻辑。
- `lib/completion/`：detector/selector/tracker 抽象，把 provider 输出转成统一 `CompletionDecision`。
- `lib/terminal_runtime/`：tmux backend、pane 查询/输入/respawn/log/layout/theme/identity。
- `lib/ccbd/services/project_namespace_runtime/`：项目 tmux namespace 的创建、materialize topology、reflow、patch、destroy。
- `mcp/ccb-delegation/`：MCP stdio server，把 `ccb_ask_agent`、`ccb_pend_agent`、`ccb_ping_agent` 暴露给远程/外部 agent。
- `lib/mobile_gateway/` 与 `tools/ccb-agent-sidebar/`：移动端和 sidebar 控制面，复用 ccbd socket、ProjectView、tmux terminal history/input。

## 4. 多 Agent 通信机制

### 4.1 agent 配置与身份

CCB 明确采用 agent-first：公开目标是 `agent_name`，provider 只是 agent 属性。配置可通过 v2 `[windows]` 拓扑隐式生成 agent，例如：

```toml
[windows]
main = "main:codex"
work = "worker1:codex(worktree), worker2:claude(worktree)"
review = "reviewer:claude, qa:gemini"
```

`lib/agents/config_loader_runtime/parsing_runtime/topology.py` 解析 layout leaf，并把 `worker1:codex(worktree)` 转为 `AgentSpec`。`AgentSpec` 包含 provider、workspace mode、runtime mode、restore、permission、queue policy、env、provider profile、dispatch disabled 等。

sender 推断由 `lib/cli/ask_sender.py` 完成：优先 `CCB_CALLER_ACTOR`，其次 runtime dir/session id，最后 workspace actor，否则 `user`。这使 agent pane 内执行 `/ask` 时能自动识别“谁在发消息”。

### 4.2 MessageEnvelope

CLI 到 daemon 的边界对象是 `MessageEnvelope`：

- `project_id`
- `to_agent`
- `from_actor`
- `body`
- `task_id`
- `reply_to`
- `message_type`
- `delivery_scope`
- `silence_on_success`
- `route_options`
- `body_artifact`

模型层会强校验：`to_agent=all` 必须是 broadcast；单 target 必须是 single；agent 名和 actor 名统一归一化。这是一个重要设计点：不是只相信 CLI parser，MCP/sidebar/mobile 走同一 socket 时也不能提交结构不一致的消息。

### 4.3 dispatcher/jobs

`ccbd/handlers/submit.py` 把 RPC payload 重建成 `MessageEnvelope`，调用 `JobDispatcher.submit()`。

提交路径：

1. `submission_service._plan_agent_submission()` 验证 sender、body artifact、callback、targets、dispatch availability。
2. broadcast 会生成 `submission_id`；single 没有 submission id。
3. 每个 target 生成 `_JobDraft`。
4. `submission_recording._submit_plan()` 生成 `job_id`，写 `JobRecord`，追加 `job_accepted` 或 `job_queued` event。
5. dispatcher 内存 state 记录 per-agent queue/active。
6. message bureau 记录 logical message 与 attempt lineage。

`JobRecord` 是执行事实：job id、agent、provider、request、status、terminal decision、workspace、target kind/name、provider options。它不是通信系统的全部，只是一次具体执行。

### 4.4 message bureau 与 mailbox

CCB 的多 agent 通信真正强点在 message bureau + mailbox kernel。

message bureau 记录三类主对象：

- `MessageRecord`：逻辑消息，一次 ask 或 broadcast 的通信主语。
- `AttemptRecord`：某 agent 对某 message 的一次执行尝试，retry 会新增 attempt。
- `ReplyRecord`：attempt 终态产生的回复。

mailbox kernel 记录三类运行对象：

- `MailboxRecord`：某 agent inbox 的 summary/head/lease version。
- `InboundEventRecord`：真正排在 inbox 中的可消费事件，类型包括 `task_request` 和 `task_reply`。
- `DeliveryLease`：投递/消费租约。

提交后，message bureau 为每个 job 写入 `AttemptRecord`，并向目标 agent inbox 写 `InboundEventRecord(event_type=TASK_REQUEST, payload_ref=job:<job_id>)`。dispatcher tick 启动 job 时，不是随便 pop 队列，而是从 mailbox head claim 可执行 request job，保证 per-agent inbound 串行。

### 4.5 ask / pend / watch

- `ask`：默认异步提交，返回 `accepted job=...` 和 `[CCB_ASYNC_SUBMITTED ...]`。它不直接等 provider 结束。
- `watch`：按 cursor 轮询 daemon `watch`，拿 job events、terminal status、visible reply。
- `pend`：先查 mailbox head 是否有 reply，再查 job latest；如果 caller inbox 有 reply head，会把 mailbox reply overlay 到输出。
- `queue`：读取 message-bureau mailbox summary，展示 agent queue depth、pending reply count、active event、runtime state。
- `inbox`：展示某 agent inbox head 和 detail items。
- `ack`：只能确认 head event，且不能 ack 已被 automatic reply delivery 接管的 reply。
- `trace`：从 `sub_`、`msg_`、`att_`、`rep_`、`job_` 任意 ID 重建 lineage。

### 4.6 reply 回流

job 完成后，completion decision 进入 finalization。message bureau 会：

1. 将 attempt 标 terminal。
2. 写 `ReplyRecord`。
3. 如果 caller 是 agent mailbox owner，把 reply 包装成 `InboundEventRecord(event_type=TASK_REPLY, payload_ref=reply:<reply_id>)` 投递到 caller inbox。
4. 刷新 mailbox summary。
5. 若有 callback edge，则可能先回 parent agent continuation，而不是直接回 original caller。

这非常关键：reply 不是“直接写到发起者 pane”，而是进入发起者 inbox，和任务请求共享串行消费通道。因此 agent 收到其他 agent 的回复时，也是一个可控、可 ack、可 trace、可恢复的入站事件。

### 4.7 广播

broadcast `to_agent=all` 会解析为当前 alive 且未 dispatch-disabled 的 agents，并排除发送者自身。一个 logical message 产生多个 jobs/attempts，`reply_policy.expected_reply_count` 记录预期回复数。它支持 `A -> B,C`、`A,B -> C` 等复杂协作，但单 agent 仍保持串行。

### 4.8 retry / resubmit

- `retry <job_id|attempt_id>`：保留原 message lineage，要求目标 attempt 已 terminal、不是 completed、且是该 agent 最新 attempt。它创建新 job 和新 attempt，并追加到原 submission。
- `resubmit <message_id>`：创建新的 message/submission chain，重发原请求到原 target agents。
- retry body 策略很细：如果旧 job 已进入 provider context，如 `anchor_seen` 或 `reply_started`，retry 可能只发送 `continue`，避免重复 prompt。
- 自动 retry 在 finalization 阶段评估 message retry policy、failure reason、max attempts、runtime resume 支持。

### 4.9 completion 与 runtime binding

`ExecutionService` 启动 provider adapter，provider 产出 `ProviderSubmission` 和 `CompletionItem`，`CompletionTrackerService` 根据 provider manifest 选择 detector/selector，最终产出 `CompletionDecision`。

runtime binding 由 `AgentRuntime` 提供：workspace path、backend type、runtime ref、session ref、pane id、health、binding source。`start_running_job()` 在执行前会构造 `ProviderRuntimeContext`，并在 `require_actionable_runtime_binding_for_execution=True` 时要求 pane-backed agent 有可执行绑定，避免 job 停在“看似 running 但没有真实投递通道”的状态。

### 4.10 daemon/project namespace

所有通信都绑定 `project_id` 和 `.ccb` anchor。`PathLayout` 计算 project id、runtime state root、socket path、agent mailbox path、target jobs path。ccbd service graph 把 registry、runtime service、supervisor、dispatcher、completion tracker、project view、health monitor 统一挂到一个项目 daemon 上。这样多项目并行时不会共享 socket、tmux server 或 runtime state。

## 5. tmux 多 Agent 终端实现

### 5.1 启动与布局

CCB 的前台布局来自 `.ccb/ccb.config` 中的 windows topology。`materialize_topology()` 会：

1. 准备项目专属 tmux server/socket。
2. 创建或确保 long-lived tmux session。
3. 为每个 window 创建 tmux window。
4. materialize sidebar、agent layout、tool window。
5. 对每个 pane 写入 CCB identity user options。
6. 刷新 ProjectView/sidebar UI。

旧的 `prepare_tmux_start_layout()` 也支持从当前 pane split 出 agent panes，但文档和 namespace 代码明显在向项目专属 tmux namespace 收敛。

### 5.2 pane/window 标识

`apply_ccb_pane_identity()` 为 pane 写入：

- `@ccb_project_id`
- `@ccb_agent`
- `@ccb_role`
- `@ccb_slot`
- `@ccb_window`
- `@ccb_sidebar_instance`
- `@ccb_namespace_epoch`
- `@ccb_managed_by`

这使得恢复、清理、ProjectView、sidebar focus 不依赖 pane title 文本，而是读 tmux user options。

### 5.3 send/input/capture

`TmuxTextSender` 不用 `send-keys -l` 直接塞长文本，而是：

1. 清理 copy mode。
2. `load-buffer` 写入临时 buffer。
3. `paste-buffer -p` 到目标 pane。
4. 延迟后发送 Enter。
5. 删除 buffer。

这比逐字符注入稳定，也更适合长 prompt、中文、换行文本。

capture 使用 `capture-pane -p -S -N`，并清理 ANSI。mobile terminal history、project view、pane status 都会用 tmux capture 读取可见状态。

### 5.4 恢复

恢复分两层：

- provider execution restore：`ExecutionStateStore` 保存 active submission、runtime context、pending items、pending decision。daemon 重启时 `restore_running_jobs()` 尝试 adapter resume；若 terminal pending 则直接 complete；若不能恢复则标记 incomplete，要求 resubmit。
- tmux pane/namespace recovery：文档定义 local pane respawn、slot-local replacement、workspace window reflow、project session remount 四层恢复。实现已有 `tmux_respawn_service.py`、project namespace reflow/patch/remove/move/additive 模块、runtime supervision loop。

`tmux_respawn_service.py` 使用 `respawn-pane`，设置 `remain-on-exit`，重放 shell command，并对 transient tmux error 做等待重试。

### 5.5 socket/session 隔离

`PathLayout` 和 project namespace 设计把 socket 与项目绑定。项目专属 tmux server/socket + session + namespace epoch 是 CCB 避免跨项目污染的关键。`ccb kill` 的语义也收敛为销毁项目 namespace，而不是 best-effort 清点随机 pane。

## 6. MCP/远程/移动或 sidebar 能力

- MCP `ccb-delegation`：提供 `ccb_ask_agent`、`ccb_pend_agent`、`ccb_ping_agent`，底层直接调用 CLI services，因此复用同一 daemon/socket/MessageEnvelope 语义。
- Sidebar：Rust `ccb-agent-sidebar` 只读 `project_view`，并通过 socket 执行 focus/restart/cancel/comms_recover/dismiss/reload。README 明确它不是通用 tmux scanner，ProjectView 才是 UI authority。
- Mobile gateway：提供 project registry、terminal attach/history/input/file upload/download，tmux terminal history 用 `capture-pane`，pane message 用 tmux `send-keys`。安全边界是 loopback gateway + pairing/device token + scoped terminal token。
- Cloudflare/Tailscale remote：文档强调 gateway 仍只监听 loopback，公网通道只负责 HTTPS/WSS route，不暴露 tmux socket path/session name/pane authority。
- ProjectView：是多控制面的统一视图，整合 project、namespace、windows、agents、comms、cache sequence/ttl。

## 7. 技术优势

1. agent-first 身份模型清晰：用户和内部控制面都围绕 agent name，而不是 provider 名或 pane id。
2. job 与 message 分层：`JobRecord` 管执行，`MessageRecord/AttemptRecord/ReplyRecord` 管通信 lineage，便于 retry/resubmit/trace。
3. mailbox 串行消费：每个 agent 的入站事件只有一个 active head，避免多个任务同时塞进同一 agent pane。
4. reply 回流统一：reply 也变成 caller inbox 的 `task_reply`，不是旁路 stdout 或直接 pane 注入。
5. append-only 账本：job/event/message/attempt/reply/inbound event 都是 JSONL，可审计、可重建、可恢复。
6. 可见执行面：每个 agent 是真实 tmux pane，用户可以直接接管、观察、复制、调试。
7. 可控控制面：CLI、MCP、sidebar、mobile 都通过 ccbd socket，不各自扫描 tmux 做决策。
8. provider 差异被封装：Codex/OpenCode 走结构化日志/存储绑定，native CLI provider 走 subprocess artifact，不硬套同一完成规则。
9. completion 抽象强：detector/selector/tracker 将 provider 输出统一成 `CompletionDecision`，finalization 只消费统一终态。
10. runtime binding 严格：执行前要求 workspace/session/pane 等可操作绑定，降低“任务已 running 但没有投递”的假象。
11. tmux identity 稳定：pane user options 记录 project/agent/slot/window/epoch，恢复与 UI 不依赖标题字符串。
12. retry 语义细：manual retry 保留 message lineage，且根据 anchor/reply_started 决定是否发送 `continue`。
13. ProjectView 适合作为 UI API：sidebar/mobile 不必重新拼状态，避免多个控制面状态不一致。
14. Rust 只做热路径辅助：Python 保持业务权威，Rust 加速 JSONL tail、Codex observe、storage scan，架构边界相对克制。

## 8. 复杂度/风险/不适合直接照搬的点

1. 模块数量和层级非常多，早期项目照搬会快速失控。
2. message bureau + mailbox + dispatcher 三套状态容易出现一致性 bug，需要大量测试和诊断工具支撑。
3. tmux namespace 恢复复杂，slot replacement/reflow/remount 的边界需要长期打磨。
4. provider-specific completion 很难维护，每个 provider 的日志/存储/session 格式变化都会影响完成检测。
5. JSONL append-only 易增长，需要 storage doctor、summary、清理策略，否则长期运行会膨胀。
6. callback/reply delivery/retry/resubmit 组合复杂，用户心智和 UI 呈现都不简单。
7. 移动端/远程控制扩大安全面，pairing token、terminal token、file upload scope 必须严控。
8. Python daemon + tmux + provider CLI + Rust helper 的跨进程调试成本高。
9. `pend`、`watch`、`inbox`、`queue` 语义多，新用户容易混淆“job 结果”和“mailbox reply”。
10. 代码中仍有历史文档/命名残留，如 askd、旧双后端、WezTerm 计划，参考时必须以当前实现为准。
11. 自动恢复如果过度激进，可能误 cancel 仍在工作的 agent；CCB 通过 diagnostics/block reason 缓解，但复杂度很高。
12. 直接复用其 provider matrix 不现实，MVP 只应支持 1-2 个 provider。

## 9. 对本项目的可学习内容

应作为核心学习：

- agent name 是唯一用户可见目标。
- provider 是 agent 属性，不是公开通信目标。
- 控制面必须有项目级 daemon/socket 或等价单一 authority。
- `message -> attempt -> job -> completion -> reply -> inbox` 的分层很值得吸收。
- 每 agent 入站串行消费是多智能体协作的底层安全阀。
- reply 进入 caller inbox，而不是直接注入 pane。
- 所有任务状态都要持久化，并能 trace。
- UI/sidebar/mobile/MCP 应复用同一 ProjectView/control API。
- tmux pane 只作为执行资源，不能作为系统唯一状态权威。
- completion 应是 provider-specific detector，而不是通用“终端静默 N 秒”。

可以简化：

- 先不做 callback edge。
- 先不做 automatic reply delivery job。
- 先不做 mobile gateway/file upload。
- 先不做 15 provider，只做 Codex/Claude 或一个 fake provider。
- 先不做 Rust accelerator。
- 先不用复杂 namespace patch/reflow，MVP 支持重启整个项目 session 即可。
- 先用 SQLite 替代大量 JSONL/JSON 分散文件，等审计需求明确再拆。

## 10. 建议本项目如何吸收 CCB

### MVP

- 建一个项目级 daemon，提供 Unix socket/HTTP local API。
- 配置只支持 `agents` 和简单 `layout`。
- 支持 2-4 个 agent，每个 agent 一个 tmux pane。
- 实现 `ask <agent> <message>` 异步提交。
- 数据模型最少包含 `messages`、`attempts`、`jobs`、`events`、`replies`。
- 每个 agent 只有一个 active job，队列串行。
- 实现 `watch job_id`、`queue`、`inbox`、`ack`、`trace job_id`。
- Provider 先支持 fake + 一个真实 CLI。
- UI 先只做 ProjectView JSON，不急着做 sidebar。

### Phase 2

- 引入 message bureau 正式语义：broadcast、reply policy、retry policy。
- 引入 mailbox head 和 delivery lease，reply 回流 caller inbox。
- 引入 provider-specific completion detector。
- 引入 runtime binding：workspace path、pane id、session id、runtime health。
- 引入 tmux pane identity user options。
- 增加 manual retry/resubmit/cancel。
- 加一个轻量 sidebar，必须只消费 ProjectView。
- 增加 doctor：daemon/socket、tmux namespace、agent runtime、mailbox summary、job lineage。

### Phase 3

- 增加 callback continuation。
- 增加自动 retry 和 comms recover。
- 增加 project namespace epoch、slot replacement、workspace reflow。
- 增加移动/远程控制，但仅在控制面稳定后做。
- 根据性能瓶颈再考虑 Rust helper，而不是先上多语言架构。
- 增加 artifact spill、file transfer、long reply storage。
- 扩展 provider catalog，保持 provider adapter 插件化。

## 11. 证据索引

1. `References/claude_codex_bridge-main/package.json`：npm 包名、版本、bin、provider matrix。
2. `References/claude_codex_bridge-main/README.md`：项目定位、支持 CLI、快速启动、mobile/rich/workbench 描述。
3. `References/claude_codex_bridge-main/README_zh.md`：中文定位与 `.ccb/ccb.config` v2 windows 示例。
4. `References/claude_codex_bridge-main/bin/ccb.js`：Node CLI wrapper。
5. `References/claude_codex_bridge-main/bin/ask.py`：`ask` 兼容入口进入 Python。
6. `References/claude_codex_bridge-main/lib/ask_cli/main.py`：`ask` alias 转 `ccb ask` phase2。
7. `References/claude_codex_bridge-main/lib/cli/parser_runtime/ask.py`：ask flags、route、stdin、job action 解析。
8. `References/claude_codex_bridge-main/lib/cli/ask_sender.py`：sender 推断逻辑。
9. `References/claude_codex_bridge-main/lib/cli/services/ask.py`：ask submit/watch 服务。
10. `References/claude_codex_bridge-main/lib/cli/services/pend.py`：pend 将 mailbox reply overlay 到 job 状态。
11. `References/claude_codex_bridge-main/lib/ccbd/api_models_runtime/messages.py`：`MessageEnvelope` 定义与 scope 校验。
12. `References/claude_codex_bridge-main/lib/ccbd/api_models_runtime/records.py`：`JobRecord`、`SubmissionRecord`、`JobEvent`。
13. `References/claude_codex_bridge-main/lib/ccbd/handlers/submit.py`：RPC submit handler。
14. `References/claude_codex_bridge-main/lib/ccbd/services/dispatcher.py`：dispatcher facade 与依赖组合。
15. `References/claude_codex_bridge-main/lib/ccbd/services/dispatcher_runtime/submission_service.py`：提交规划、target 校验、body artifact 校验。
16. `References/claude_codex_bridge-main/lib/ccbd/services/dispatcher_runtime/submission_recording.py`：job/submission/message-bureau 写入。
17. `References/claude_codex_bridge-main/lib/ccbd/services/dispatcher_runtime/lifecycle.py`：submit/retry/resubmit 主语义。
18. `References/claude_codex_bridge-main/lib/ccbd/services/dispatcher_runtime/routing.py`：target resolution、broadcast 排除 sender、watch payload。
19. `References/claude_codex_bridge-main/lib/ccbd/services/dispatcher_runtime/lifecycle_start_runtime/queue.py`：从 mailbox claim request/reply delivery job。
20. `References/claude_codex_bridge-main/lib/ccbd/services/dispatcher_runtime/lifecycle_start_runtime/start.py`：启动 running job 与 execution service。
21. `References/claude_codex_bridge-main/lib/message_bureau/models.py`：message/attempt/reply 记录模型。
22. `References/claude_codex_bridge-main/lib/message_bureau/facade.py`：message bureau facade。
23. `References/claude_codex_bridge-main/lib/message_bureau/facade_recording_submission.py`：submission 转 message/attempt/inbound event。
24. `References/claude_codex_bridge-main/lib/message_bureau/facade_recording_terminal_replies.py`：reply record 与 caller inbox 回流。
25. `References/claude_codex_bridge-main/lib/message_bureau/callback_edges.py`：callback parent/child/continuation edge。
26. `References/claude_codex_bridge-main/lib/mailbox_kernel/models.py`：mailbox/inbound/lease 模型。
27. `References/claude_codex_bridge-main/lib/mailbox_kernel/service.py`：claim/ack/consume/summary API。
28. `References/claude_codex_bridge-main/lib/mailbox_kernel/store.py`：mailbox JSON 与 inbound JSONL store。
29. `References/claude_codex_bridge-main/lib/message_bureau/control_queue_runtime/ack.py`：ack head-only 与 reply-delivery 保护。
30. `References/claude_codex_bridge-main/lib/message_bureau/control_queue_runtime/views_runtime/agent.py`：agent queue detail。
31. `References/claude_codex_bridge-main/lib/jobs/store.py`：job/event/submission JSONL 存储。
32. `References/claude_codex_bridge-main/lib/storage/paths.py`：project id、runtime root、socket/cache/path layout。
33. `References/claude_codex_bridge-main/lib/provider_execution/service.py`：execution start/cancel/restore/poll。
34. `References/claude_codex_bridge-main/lib/provider_backends/codex/execution.py`：Codex provider adapter。
35. `References/claude_codex_bridge-main/lib/provider_backends/codex/execution_runtime/start.py`：Codex active pane prompt delivery。
36. `References/claude_codex_bridge-main/lib/provider_backends/native_cli_support/execution.py`：native CLI subprocess provider 模式。
37. `References/claude_codex_bridge-main/lib/completion/tracker.py`：completion tracker 选择 detector/selector。
38. `References/claude_codex_bridge-main/lib/terminal_runtime/tmux_send.py`：tmux buffer paste 文本投递。
39. `References/claude_codex_bridge-main/lib/terminal_runtime/tmux_panes.py`：pane query/mutation service。
40. `References/claude_codex_bridge-main/lib/terminal_runtime/tmux_identity.py`：pane user option identity。
41. `References/claude_codex_bridge-main/lib/ccbd/services/project_namespace_runtime/materialize_topology.py`：项目 tmux namespace materialize。
42. `References/claude_codex_bridge-main/lib/terminal_runtime/tmux_respawn_service.py`：pane respawn 恢复。
43. `References/claude_codex_bridge-main/mcp/ccb-delegation/server_runtime_tools.py`：MCP ask/pend/ping 工具。
44. `References/claude_codex_bridge-main/lib/mobile_gateway/terminal.py`：mobile tmux terminal history/input。
45. `References/claude_codex_bridge-main/tools/ccb-agent-sidebar/src/client.rs`：sidebar 通过 ccbd socket 读 ProjectView 和控制项目。
46. `References/claude_codex_bridge-main/docs/manuals/developer-guide/chapters/04-communication.tex`：通信链路官方开发者手册。
47. `References/claude_codex_bridge-main/docs/agent-mailbox-kernel-design.md`：邮箱内核设计不变量。
48. `References/claude_codex_bridge-main/docs/ccbd-pane-recovery-continuous-attach-plan.md`：pane 恢复分层。
49. `References/claude_codex_bridge-main/test/test_message_bureau_control_queue.py`：queue/inbox/ack 控制视图测试。
50. `References/claude_codex_bridge-main/test/test_terminal_runtime_tmux_panes.py`：tmux pane query/user option/capture 测试。
