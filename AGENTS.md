# AGENTS.md

本文件帮助 Codex 或其他 coding agent 快速理解本项目。

## 项目定位

AgentDeck 是一个 local-first 多智能体终端工作台。目标是用任意可通过 API 调用的 LLM 做 Leader Agent，调度多个 Worker Agent，在 tmux 可见终端里执行任务，并通过消息账本、审批、状态存储和 ProjectView 保持可审计、可恢复。DeepSeek 可以作为首个默认 provider，但不是架构绑定点。

Skill 是北极星的一部分：AgentDeck 后续应支持内置 skill、项目本地 skill 和显式 allowlist 的外源 skill，但 skill 必须通过 Skill Registry 加载并记录 path/source、hash、content snapshot、调用者和用途；skill 是可审计工作流上下文，不是绕过 approval、runtime safety 或 tool 权限的后门。

北极星 Skill 诉求：AgentDeck 应该逐步形成可内置、可外源、可建议、可审阅、可加载、可回放的 skill 生态。内置 skill 用来沉淀稳定高频工作流；外源 skill 可以来自用户、本地目录或未来 marketplace，但必须先经过只读 preview、hash/provenance 展示、显式 import、显式 load 和审计。任何 agent 都不得把外源 `SKILL.md` 静默安装、自动启用、当作权限授权，或绕过 human approval。

Skill Registry MVP：`agentdeck skills list` 必须只读发现内置 skill 和 `.agentdeck/skills/<name>/SKILL.md` 项目本地 skill，并暴露列表级 import control 和每个 skill 的 show/load controls；`agentdeck skills import-preview --path <SKILL.md>` 必须只读解析外源 skill，返回 source、目标项目路径、hash、覆盖状态、只读 allowlist 状态（`source_allowlisted`/`enforcement_active`/`import_blocked`）和 import/force/show controls，不复制文件、不追加事件、不 load、不阻断；`agentdeck skills import --path <SKILL.md>` 必须显式把外部 skill 复制进项目 `.agentdeck/skills/<name>/SKILL.md` 并追加 `skill_imported` 审计事件，默认拒绝覆盖，只有 `--force` 可覆盖；import 必须对 `[skills] allowed_sources` 做 opt-in 强制：allowlist 为空时不强制（向后兼容，行为不变），allowlist 非空且 source 不在任一 allowed source 下时必须拒绝导入（不复制、不写事件、返回非 0，stderr 提示加入 allowlist 或加 `--allow-unlisted`），只有显式 `--allow-unlisted` 逃生阀可越过；两条路径都通过 `skill_imported.allowlisted` / `.allow_unlisted` 审计；catalog/sources/import-preview 保持只读非阻断，只有 `skills import` 强制；`agentdeck skills show --name <name>` 必须只读返回 skill metadata、hash、content 和同源 controls；`agentdeck skills load-preview --name <name> --agent <id> --purpose <text>` 必须只读返回目标 agent、purpose、skill summary、显式 load command、show/load controls，以及只读依赖提示 `unmet_dependencies`（该 skill 声明的 `depends_on` 中未出现在已发现 skill 里的名字，复用 `resolve_skill_dependencies`）和 `has_dependency_cycle`（bool）；该依赖提示只是信息展示，不得阻断 preview、不得 auto-load/auto-import 任何依赖，也不写 `skill_loads[]`、不追加 `skill_loaded`；`agentdeck skills load --name <name> --agent <id> --purpose <text>` 才能写入 `skill_loads[]` 和 `skill_loaded` 审计事件，并保存 content snapshot；`agentdeck skills suggest --name <name> --summary <summary> --rationale <rationale> --source <source>` 只能写入 pending `skill_suggestions[]` 和 `skill_suggested` 审计事件；`agentdeck skills suggestions` 只能只读列出 suggestion queue，并为 pending item 派生 draft-preview control；`agentdeck skills draft-preview --suggestion-id <id>` 只能只读生成拟写入 `SKILL.md` 的内容、hash、目标路径和显式 create control，不写文件、不更新 suggestion status；`agentdeck leader chat --message "创建 skill 建议 sgs_xxx"` 只能进入只读 `mode=skill_create_preview`，嵌入 `skill_create_preview_card` 并返回显式 `agentdeck skills create --suggestion-id <id> --confirm`，不得创建文件或修改 suggestion queue；`agentdeck skills create --suggestion-id <id> --confirm` 是 suggestion 草稿落地为项目 skill 的唯一路径，必须要求 `--confirm`，写入 `SKILL.md`、标记 suggestion 为 `created` 并追加 `skill_created`，但不得自动 load、调用 provider、读取 tmux 或修改 approval/runtime state。skills 命令不得调用 provider、读取 tmux、发送输入、修改 approval/runtime state，外源 skill 不得静默安装或启用，suggestion 不得绕过 create 确认直接生成 `SKILL.md`，import/create 后仍必须显式 load 才能进入 Leader/Worker 上下文。

Memory Suggestion MVP：`agentdeck memory suggest --summary <summary> --rationale <rationale> --source <source>` 只能写入 pending `memory_suggestions[]` 和 `memory_suggested` 审计事件；`agentdeck memory suggestions` 只能只读列出 queue，并为每条 pending item 派生 `apply_preview` / `apply_memory` controls，不写长期 memory、不更新 status、不追加 `memory_applied`；`agentdeck memory apply-preview --suggestion-id <id>` 只能只读返回目标 memory 文件、是否会创建、proposed_append、显式 apply command 和 apply control，不得写 `.agentdeck/memory/*.md`、不得更新 suggestion status、不得追加事件；`agentdeck memory apply --suggestion-id <id> --confirm` 是当前唯一长期 memory 写入路径，必须要求 `--confirm`，追加同一份 proposed Markdown 到 `.agentdeck/memory/project.md` 或 `.agentdeck/memory/global.md`，更新 suggestion 为 `applied`，写入 `applied_at` / `applied_path`，并追加 `memory_applied` 审计事件；未知 suggestion、缺少 `--confirm` 或非 pending suggestion 必须拒绝且不得重复写入。`agentdeck leader chat --message "查看 memory 建议"` 必须进入 `mode=memory_suggestions`，嵌入只读 `memory_suggestions_card`，复用 `agentdeck memory suggestions` 的 pending queue 语义；`agentdeck leader chat --message "预览 memory 建议 mem_xxx"` 必须进入 `mode=memory_apply_preview`，嵌入只读 `memory_apply_preview_card`，复用 `agentdeck memory apply-preview` 的目标文件、proposed_append 和显式 apply command 语义；`agentdeck workbench` 必须从 pending `memory_suggestions[]` 派生同源 `memory_suggestions_card`，暴露 `agentdeck memory suggestions` inspect control、`apply_preview_command_template` 和 item 级 `apply_preview` / `apply_memory` controls。memory suggestion preview/apply-preview/list/chat/workbench 不得创建或修改 `.agentdeck/memory/*.md`，不得调用 provider、读取 tmux、发送输入、修改 approval/runtime state，不得自动注入 Leader/Worker prompt；只有显式 `memory apply --confirm` 可以落地长期 memory。

Applied memory context：已应用的 `.agentdeck/memory/project.md` / `global.md` 只能以 compact 摘要进入 ProjectView `memory`、workbench `memory_context_card` 和自然语言 `mode=memory_context`。摘要字段限制为 scope/path/exists/line_count/byte_count/content_hash/preview，不得包含全文，不得自动注入 API-backed 或 CLI-backed Leader prompt，不得注入 Worker dispatch prompt；`agentdeck leader chat --message "查看长期记忆"` 只能只读展示 `memory_context_card`、记录 chat turn 和审计事件，不得调用 provider、读取 tmux、创建 plan/action/approval/message/job/inbox 或改变 runtime/approval state。

Learning review MVP：`agentdeck learn review --plan-id <id>` 是 Hermes 式后台 reviewer 的只读前置面，只能复用现有 plan status、Leader summary、reply 和 artifact 事实，生成 GUI-ready `agentdeck skills suggest ... --source learn-review` 与 `agentdeck memory suggest ... --source learn-review` 后续命令；它不得写 `skill_suggestions[]`、不得写 `memory_suggestions[]`、不得创建/导入/load skill、不得写 `.agentdeck/memory/*.md`、不得调用 provider、不得读取 tmux、不得创建 plan/action/approval/message/job/inbox。真正进入 pending queue 仍必须由人类显式运行 suggestion 命令。`agentdeck learn review` 输出和自然语言 `learning_review_card` 都必须通过 `validate_learning_review_contract()` 守门；失败时不得打印半坏 JSON。`agentdeck leader chat --message "学习复盘 pln_xxx"` 必须进入只读 `mode=learning_review`，嵌入同源 `learning_review_card`，附带过滤到 `scope=learning_review` / `card=learning_review_card` 的 `control_registry_card`，selection 指向 `suggest_skill` control；它只记录 chat turn，不得执行 suggestion 命令、写 skill/memory queue、调用 provider、读取 tmux 或创建调度对象。`agentdeck contract learning-review` / `--example` 必须把这一路径作为 GUI 可发现契约暴露，并同步 `docs/contracts/learning-review-schema.md`、contract index、workbench `contracts_card`、README、HISTORY 和测试。

Loaded skill context 必须进入 ProjectView `skills` 摘要、workbench `skill_context_card`、自然语言 `mode=skill_context` 和真实 Leader provider planning prompt；传给 API-backed / CLI-backed Leader prompt 的只能是 compact 摘要（load_id、agent_id、purpose、name、source、path、content_hash、description、required_tools、risk），不得包含完整 `content_snapshot`。同一份 compact context 必须随 `leader plan`、自然语言 plan 和 `run --task` 固化到 plan record、ProjectView `plans.items[]` 和 `agentdeck plan status`，作为可审计 provenance。`agentdeck leader chat --message "查看已加载技能"` 只能展示已加载 skill、记录 chat turn 和审计事件，不得调用 provider、读取 tmux、安装或改写 skill、创建 plan/action/approval/message/job/inbox 或改变 runtime/approval state；`agentdeck leader chat --message "查看 skill 建议"` 必须进入 `mode=skill_suggestions`，嵌入只读 `skill_suggestions_card`，复用 `agentdeck skills suggestions` 的 pending queue 语义，只建议 inspect 命令，不得创建 `SKILL.md`、import、load、调用 provider、读取 tmux、创建 plan/action/approval/message/job/inbox 或改变 runtime/approval state；`agentdeck leader chat --message "创建 skill 建议 sgs_xxx"` 必须进入 `mode=skill_create_preview`，嵌入只读 `skill_create_preview_card`，复用 `agentdeck skills draft-preview` 的草稿/hash/目标路径语义，只返回显式 create 命令，不得写文件、更新 suggestion status、import/load skill、调用 provider、读取 tmux 或创建调度对象；`agentdeck leader chat --message "预览导入 skill <SKILL.md>"` 必须进入 `mode=skill_import_preview`，嵌入 `skill_import_preview_card`，复用 `agentdeck skills import-preview --path <SKILL.md>` 的 source/target/hash/overwrite/controls 语义，只建议显式 import 或 force import 命令，不得复制文件、load skill、调用 provider、读取 tmux、创建 plan/action/approval/message/job/inbox 或改变 runtime/approval state；`agentdeck leader chat --message "预览加载 skill <name> 给 <agent_id> 用于 <purpose>"` 必须进入 `mode=skill_load_preview`，嵌入 `skill_load_preview_card`，只建议显式 `agentdeck skills load ...` 命令，不得写 `skill_loads[]`、不得追加 `skill_loaded`、不得创建 plan/action/approval/message/job/inbox；provider planning prompt 也不得把 skill 当成 dispatch 或执行授权。

Worker dispatch skill 边界：`agentdeck dispatch` 和 `agentdeck approval dispatch` 只能把目标 agent 已显式加载的 skill `content_snapshot` 注入该 worker 的任务 prompt；不得注入其它 agent 的 skill。message、ProjectView `messages.items[]`、workbench `ledger_card.messages.items[]` 和 trace 必须保存 compact `prompt_skill_context` 作为 provenance，不能把该字段当成权限授权；审批、runtime safety 和 tool 权限仍由原有 gate 控制。

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
agentdeck workbench
agentdeck controls
agentdeck agent list
agentdeck agent stop --agent planner
agentdeck agent assign-role --agent planner --role "architecture planning" --role-prompt "你负责架构规划和任务拆解。"
agentdeck leader chat --message "帮我设计自动 reply extraction"
agentdeck leader chat --message "查看 planner 输出"
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
- 每次开发前先对照 `docs/roadmap/ultimate-goal-roadmap.md`，确认功能服务 Leader Agent、多 Agent 通信、可见 runtime、审批、恢复、可审计 skill 或 GUI 主线。
- GUI、自然语言入口和 Leader chat loop 应优先消费 `agentdeck status` 的 ProjectView 摘要；顶层 `leader.leader_backend` 必须作为当前配置 Leader provider/model 的 logical identity 来源，每个 `plans.items[].leader_backend` 必须作为历史 plan 的同源 provenance，不表示 tmux pane、readiness 或授权；不要直接散读 state 文件作为主入口。
- ProjectView 字段契约维护在 `docs/contracts/project-view-schema.md`；当前 `schema_version` 是 `project-view/v1`，任何 GUI、recovery 或自然语言入口改动都要保持该文档同步。
- ProjectView schema version 的源码单一来源是 `src/agentdeck/models.py` 的 `PROJECT_VIEW_SCHEMA_VERSION`；不要在 Python 源码里重复手写版本字符串。
- ProjectView contract discovery payload 和 example fixture 的源码入口是 `src/agentdeck/contracts.py`；CLI 只负责调用它。
- Contract index 维护在 `docs/contracts/contract-index-schema.md`，发现入口是 `agentdeck contract list`；`CONTRACT_INDEX_SPECS`、payload helper 和测试都在 `src/agentdeck/contracts.py` / `tests/` 中。新增 GUI-consumable contract 时必须同步索引。
- Doctor diagnostics contract 维护在 `docs/contracts/doctor-schema.md`，发现入口是 `agentdeck contract doctor`；payload 和 example fixture 也在 `src/agentdeck/contracts.py`。doctor configured_leader 必须携带同源 normalized `leader_backend`，该字段只表示 setup provenance，不表示 provider readiness、tmux pane 或执行授权；doctor contract discovery 必须公开 workbench、Leader chat 和 Leader review contract 入口，供 GUI setup 面板跳转到主控制面契约。
- Events timeline contract 维护在 `docs/contracts/events-schema.md`，发现入口是 `agentdeck contract events`；payload 和 example fixture 也在 `src/agentdeck/contracts.py`。
- Run contract 维护在 `docs/contracts/run-schema.md`，发现入口是 `agentdeck contract run`；payload、example fixture 和 `validate_run_start_contract()` 也在 `src/agentdeck/contracts.py`。
- Run-loop contract 维护在 `docs/contracts/run-loop-schema.md`，发现入口是 `agentdeck contract run-loop`；payload、example fixture、`RUN_LOOP_RESPONSE_FIELDS`、`RUN_LOOP_STOP_REASONS` 和 `validate_run_loop_contract()` 也在 `src/agentdeck/contracts.py`，并已注册进 `CONTRACT_INDEX_SPECS` / `agentdeck contract list`；纯 gate 诊断 `run_loop_gate()` 在 `src/agentdeck/autonomy.py`。修改 `agentdeck run-loop` 的 response 字段、`stop_reasons`、安全边界或 validator 时必须同步该 contract、contract index、README、HISTORY 和测试。
- Run-loop-all（并行调度器）contract 维护在 `docs/contracts/run-loop-all-schema.md`，发现入口是 `agentdeck contract run-loop-all`；`RUN_LOOP_ALL_RESPONSE_FIELDS`、`RUN_LOOP_ALL_PLAN_FIELDS`、`run_loop_all_example`、`run_loop_all_contract_response` 和 `validate_run_loop_all_contract()` 也在 `src/agentdeck/contracts.py`，并已注册进 `CONTRACT_INDEX_SPECS` / `agentdeck contract list`；引擎 `_run_loop_all` / `_busy_agents` 在 `src/agentdeck/cli.py`。修改 `agentdeck run-loop --all` 的 response 字段、round-robin/共享预算/skip-on-contention 语义、安全边界或 validator 时必须同步该 contract、contract index、README、HISTORY 和测试。
- Leader chat response contract 维护在 `docs/contracts/leader-chat-schema.md`，发现入口是 `agentdeck contract leader-chat`；payload 和 example fixture 也在 `src/agentdeck/contracts.py`。自然语言 `leader_status` mode 必须嵌入 `leader_status_card` 并复用 `agentdeck leader status` 同源字段；自然语言 run-start mode 必须嵌入 `run_start_card` 并复用 `validate_run_start_contract()`；自然语言 run-progress mode 必须嵌入 `run_progress_card`、附带过滤到 `run_progress_card` 的 `control_registry_card`、selection 指向 `agentdeck plan status --plan-id <id>` inspect control，并复用同一个 validator；自然语言 learning_review mode 必须嵌入 `learning_review_card`、附带过滤到 `learning_review_card` 的 `control_registry_card`、selection 指向 `suggest_skill` control，并复用 `agentdeck learn review --plan-id <id>` 同源 response 字段；自然语言 summary mode 必须嵌入 `leader_summary_card`、附带过滤到 `leader_summary_card` 的 `control_registry_card`、selection 指向 `agentdeck leader summary --plan-id <id>` inspect control，并复用 `validate_leader_summary_contract()`；自然语言 ledger mode 必须嵌入 workbench 同源 `ledger_card` 和 `lineage_card`、附带过滤到 `ledger_card` 的 `control_registry_card`、selection 指向 `agentdeck workbench` inspect control，并复用 ledger card validator；自然语言 audit mode 必须嵌入 workbench 同源 `audit_card`、附带过滤到 `audit_card` 的 `control_registry_card`、selection 指向 `agentdeck events --limit 20` inspect control，并复用 audit card validator；自然语言 artifacts mode 必须嵌入同源 `artifacts_card`、附带过滤到 `artifacts_card` 的 `control_registry_card`、selection 指向 `agentdeck artifacts` inspect control，并复用 `validate_artifacts_contract()`；自然语言 memory suggestions mode 必须嵌入 `memory_suggestions_card` 并复用 `agentdeck memory suggestions` 同源字段；自然语言 memory apply preview mode 必须嵌入 `memory_apply_preview_card` 并复用 `agentdeck memory apply-preview` 同源字段；修改 leader_status/run/learning_review/summary/ledger/audit/artifacts/memory_suggestions/memory_apply_preview intent、嵌入 card 或 `intent_card` 控件时必须同步 leader-chat contract、README、HISTORY 和测试。
- 自然语言 trace mode 必须嵌入同源 `trace_card`、附带过滤到 `scope=trace` / `card=trace_card` 的 `control_registry_card`，并让 selection 指向 `agentdeck trace --id <id>` inspect control；它只读展示通信 lineage，不得执行 trace、capture、ack、dispatch、读取 pane 或发送 tmux 输入。
- Workbench snapshot contract 维护在 `docs/contracts/workbench-schema.md`，发现入口是 `agentdeck contract workbench`；payload、example fixture 和 `validate_workbench_contract()` 也在 `src/agentdeck/contracts.py`。Workbench 的 `agent_ready_card` 必须复用 `agentdeck agent ready` 响应形状和同一份 `runtime_card`，并通过 agent runtime ready validator 校验；它只读派生 startup readiness，不得 inspect tmux、spawn/refresh/dispatch、capture pane、send input 或写 state；`terminal_session_card` 必须从同一份 `runtime_card` 和项目 tmux 配置派生 project-level attach/select-pane affordances，并通过 terminal session validator 校验；每个 `terminals[]` item 必须暴露 `controls[]` 的 `kind=select_pane` / `safety=inspect` 控件，command 必须匹配同 item 的 `select_pane_command`，disabled item 必须保留 blocker；它只读展示终端入口，不得 attach tmux、select pane、capture、send、refresh、spawn/stop 或写 state；Workbench 的 `skill_suggestions_card` 必须从 pending `skill_suggestions[]` 派生，复用 `agentdeck skills suggestions` 语义，暴露 `agentdeck skills suggestions` / `agentdeck status` inspect controls，不得创建 `SKILL.md`、import、load、调用 provider、读取 tmux 或写 runtime/approval state；Workbench 的 `memory_suggestions_card` 必须从 pending `memory_suggestions[]` 派生，复用 `agentdeck memory suggestions` 语义，暴露 `agentdeck memory suggestions` / `agentdeck status` inspect controls、只读 `apply-preview` 控件和显式 `memory apply --confirm` 控件；渲染 workbench 不得创建或修改 `.agentdeck/memory/*.md`、注入 prompt、调用 provider、读取 tmux 或写 runtime/approval state；Workbench 的 `run_progress_card` 必须复用最新 plan 的 `agentdeck run --plan-id <id>` 响应形状和 `validate_run_start_contract()`，没有 plan 时为 `null`；`artifacts_card` 必须复用 `agentdeck artifacts` 响应形状和 `validate_artifacts_contract()`，暴露 `kind=inspect` / `command=agentdeck artifacts` / `safety=inspect` control，只展示 ProjectView artifact 摘要，不读取产物文件内容；`leader_summary_card` 只在 latest plan ready-to-summarize 时复用 `agentdeck leader summary --plan-id <id>` 和 `validate_leader_summary_contract()`，否则为 `null`。
- Controls contract 维护在 `docs/contracts/controls-schema.md`，发现入口是 `agentdeck contract controls`；payload、example fixture 和 `validate_control_registry_card_contract()` 也在 `src/agentdeck/contracts.py`，并复用 workbench control registry item 字段。controls contract 必须公开 card/filter/selection/item/group 字段；`filters.active_filter_keys` 必须按 `scope`、`card`、`query`、`control_id`、`enabled_only` 稳定顺序派生，供 GUI/TUI 渲染过滤 chip；无过滤时 `filters.item_count_before_filter` 必须等于 `item_count`；每个 control registry item 必须带 deterministic `control_id`，供 GUI/TUI 稳定渲染和审计关联，但它不是授权令牌，不能绕过 `enabled`、`safety` 或 `blocker`；`selection.blocker` 只解释未命中 control_id 等选择态，`selection.next_command` 只投影 enabled 选中项的命令，二者都不得用于执行授权。
- Skills contract 维护在 `docs/contracts/skills-schema.md`，发现入口是 `agentdeck contract skills`；payload 和 example fixture 也在 `src/agentdeck/contracts.py`。它必须公开 `skills list/show/import-preview/import/load/suggest/suggestions/draft-preview/create` 响应字段、skill item/suggestion item 字段和 skill control 字段；修改 Skill Registry 输出、import-preview/import/load/suggest/suggestions/draft-preview/create safety 或 show/load/draft/create controls 时必须同步该 contract、contract index、README、HISTORY 和测试。
- Skill dependencies（decision B slice 1，read-only）：`SKILL.md` frontmatter 可声明 `depends_on` 列表（复用 `_metadata_list`，与 `required_tools` 同源），只解析进 `SkillSnapshot.depends_on`，不改 `summary()`，仅作元数据，不自动 load、不自动 import。`agentdeck skills deps --name <name>` 只读解析 discovered skills（内置 + 项目本地）之间的传递依赖，输出 `mode=skills_deps`、`depends_on`、`resolved`、`missing`、`has_cycle`（+ `cycle` 路径）、拓扑 `order` 和 inspect-only `show` controls，并在打印前通过 `validate_skills_deps_contract()` 守门；它不 load、不 import、不写 state、不调用 provider、不读取 tmux，未知 `--name` 必须非 0 且无输出。`depends_on` 的版本约束（B-ver）、remote/marketplace 依赖是后续产品 fork，必须先 STOP + 询问 human。
- Skill dependency auto-load（decision B-auto）：`agentdeck skills load-plan --name <name> --agent <id>` 必须只读预览依赖 load 计划，复用 `resolve_skill_dependencies` + 该 agent 已有 `skill_loads`，输出 `mode=skill_load_plan`、deps-first 拓扑 `order`（每项 `{name,status,source}`）、`to_load`/`already_loaded`/`missing`/`has_cycle`/`cycle`/`blockers`/`can_load`/`confirm_command` 和 inspect-only `show` controls，经 `validate_skill_load_plan_contract()` 守门；它不写 state、不追加事件、不 load/import、不调用 provider、不读取 tmux，未知 skill/agent 必须非 0。`agentdeck skills load --name <name> --agent <id> --with-deps --confirm` 才能按 `to_load` deps-first 逐个 `store.record_skill_load` + `skill_loaded` 事件并追加一条 `skill_deps_loaded` 汇总，输出 `mode=skill_deps_loaded`；`--with-deps` 必须要求 `--confirm`（否则拒绝、零写），缺失依赖或依赖环必须拒绝且零写（绝不 auto-import——缺失依赖是硬 blocker，import 仍走既有显式 allowlist-gated 流程），绝不静默；单 skill `skills load`（无 `--with-deps`）行为必须逐字节保持不变。修改 load-plan/load --with-deps 输出、安全边界或 controls 时必须同步 skills contract、README、HISTORY、handoff 和测试。
- Skill dependency version pinning（decision B-ver）：`depends_on` 条目可选锁定内容 hash `name@sha256:<hex>`；纯 `name` 表示任意版本（行为不变）。resolver 必须用纯 helper `_parse_dep(entry)` 解释每个 raw 条目（首个 `@` 切分，空 pin 忽略），`SkillSnapshot.depends_on` 仍存 raw 条目。`resolve_skill_dependencies` 必须新增 `version_mismatch: list[{name, expected, actual}]`：当被锁定的依赖存在但 `content_hash` 与 pin 不符时记入，该依赖既不进 `resolved` 也不进 `missing`，且作为 blocker leaf 不递归；`resolved`/`missing`/`order`/cycle 语义在其它方面不变。`skills deps` 必须输出 `version_mismatch`（加入 `SKILLS_DEPS_RESPONSE_FIELDS` + validator），`skills load-plan` 必须把 `version_mismatch` 加入 payload 和 `blockers`（`"version mismatch: <name> expected <pin>"`，加入 `SKILL_LOAD_PLAN_RESPONSE_FIELDS` + validator），`can_load` 因此在 mismatch 时为 false，`skills load --with-deps --confirm` 必须像 missing/cycle 一样硬阻断、零写。pin 是纯内容 hash 等值——确定性、本地、无网络；不得 auto-fix、auto-import 或静默 load。修改 pin 解析、`version_mismatch` 输出、安全边界或 controls 时必须同步 skills contract、README、HISTORY、handoff 和测试。
- Skill dependency semver ranges（decision semver）：`SKILL.md` frontmatter 可声明 `version: X.Y.Z`（默认 `0.0.0`，加入 `SkillSnapshot.summary()` 和 `SKILLS_SKILL_ITEM_FIELDS` + example fixture）。`depends_on` 条目 `name@<spec>` 中，凡 `<spec>` 不以 `sha256:` 开头即为 **semver range**，与依赖声明的 `version` 比对（`sha256:` 仍走 B-ver 内容 hash，纯 `name` 仍表示任意版本）。支持子集：bare/`==` 精确、`>=` `>` `<=` `<`、caret `^X.Y.Z`（= `>=X.Y.Z` 且 `< (X+1).0.0`）、逗号 AND（`>=1.2,<2.0`，全部成立）；版本为 `MAJOR[.MINOR[.PATCH]]`，缺省补 0。不支持（`.x`/`*` 通配、`~`、pre-release、`||` OR）或任何无法解析的 range 一律 fail-safe 视为硬 blocker，绝不静默通过。comparator 是纯 stdlib `parse_version` + `version_satisfies`（`skills.py`），确定性、本地、无网络、不引第三方库。resolver 对存在依赖分类 spec：`sha256:` → 内容 hash，否则 → `version_satisfies(dep.version, spec)`，不满足即记入 `version_mismatch`（新增 `reason` 键：`content hash mismatch` / `version range not satisfied`；`name/expected/actual` 与 B-ver 兼容）并作为 blocker leaf 不递归；`version_mismatch` 继续经 `skills deps` / `load-plan` blockers / `load --with-deps` 硬阻断、零写。lockfile 是下一个独立 spec；remote/marketplace 依赖仍是 STOP + 询问 human 的 fork。修改 `version` frontmatter、comparator、resolver 分类、安全边界或 controls 时必须同步 skills contract、README、HISTORY、handoff 和测试。
- Skill dependency lockfile（decision lockfile）：`agentdeck skills lock --name <name>` 是显式写路径，复用 `resolve_skill_dependencies` + `discover_skills` 冻结该 skill 已解析的依赖树到 `.agentdeck/skill-locks/<name>.json`（专用目录，不在 `.agentdeck/skills/` 下，`discover_skills` 不会拾取），内容为 `{name, locked_at, dependencies:[{name, content_hash, version}]}`（`dependencies` = resolver deps-first `order` 去掉 root）；成功时写 lockfile 并追加一条 `skill_locked` 审计事件 `{name, dependency_count}`，输出 `mode=skill_locked`（字段 `SKILL_LOCK_RESPONSE_FIELDS`），经 `validate_skill_lock_contract()` 守门；依赖树存在任何 blocker（missing / cycle / version_mismatch）必须**拒绝**——不写 lockfile、不写事件、非 0，未知 `--name` 同样非 0 无写。re-lock 覆盖既有 lockfile（事件记录每次写）。`agentdeck skills lock-verify --name <name>` 是**全只读** drift 报告，输出 `mode=skill_lock_verify`（字段 `SKILL_LOCK_VERIFY_RESPONSE_FIELDS`：`locked/in_sync/changed/added/removed/blockers`），经 `validate_skill_lock_verify_contract()` 守门：无 lockfile → `locked=false` + hint + 退出 0；否则重新解析并与 lock diff（`changed` = hash/version 变化、`added` = 新增依赖、`removed` = 消失依赖，另带当前 `blockers`），`in_sync` 仅当四者皆空为真；它不写 state、不写事件、不改 lockfile。lockfile 本切片是**advisory** drift 检测，**不**改变 `skills deps`/`load` 的解析行为（enforce 是后续切片）；本地、无网络、无第三方库。下一个依赖项 remote/marketplace（C）必须 STOP + 询问 human，绝不在 loop 里开工。修改 lock/lock-verify 输出、lockfile 格式、安全边界或 controls 时必须同步 skills contract、README、HISTORY、handoff 和测试。
- Memory contract 维护在 `docs/contracts/memory-schema.md`，发现入口是 `agentdeck contract memory`；payload 和 example fixture 也在 `src/agentdeck/contracts.py`。它必须公开 `memory suggest/suggestions/apply-preview/apply` 响应字段、suggestion item 字段和 memory control 字段；修改长期记忆建议、只读预览、显式 apply 或 GUI controls 时必须同步该 contract、contract index、workbench contracts_card、README、HISTORY 和
