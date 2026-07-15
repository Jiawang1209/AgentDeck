# AgentDeck

**一个 local-first、可治理、协议原生的多智能体工作台。**

AgentDeck 把自然语言目标变成可审阅的 Mission，协调真实的 Codex、Claude 和其他 Agent，并让执行过程可观看、可审计、可恢复。

> 北极星：Hermes 般自然的会话、ACP 原生通信、CCB 式真实多 Agent 协作，以及更强的编排与治理内核。

[English](README.md)

## 从自然语言会话开始

```bash
conda env create -f environment.yml
conda activate agentdeck
python -m pip install -e .
agentdeck
```

现在，在真实终端中直接运行 `agentdeck` 会进入 Phase 3 M1 前台持续会话。在未初始化目录中，它先展示精确的项目初始化预览；进入项目后，可以把已配置的 API LLM 或 Agent CLI 作为 Leader，将开放请求冻结为 Mission preview，并且只在自然语言确认绑定当前精确 preview 后执行。

如果项目存在后台 Mission recovery 事实，bare `agentdeck` 会先打印经过
ProjectView contract 校验的同源 `mission_recovery` 卡，再进入正常持续会话；
没有待恢复 Mission 时保持原来的安静启动体验。该重连渲染完全确定性，
不调用 LLM、不读取 tmux、不写 state，也不重建完整 transcript。语义 Mission
只暴露与冻结 step、attempt 和已验证结果一致的 compact step hash；legacy
recovery card 保持原有精确形状。

```text
你        › 让 Codex 实现这个功能，Claude 负责审查。
AgentDeck › Mission preview：2 个 Worker，需要审批。
你        › 确认当前预览。
AgentDeck › Mission 已启动，可用 /status 或 workbench 查看。
```

`agentdeck leader chat --message "..."` 继续作为脚本化和调试入口保留。

## 当前已经可用

- 显式 API 或 Agent-CLI Leader 身份与 readiness；
- `/help`、`/status`、`/approvals`、`/trace`、setup 和退出等无需 LLM 的确定性 intent；
- 有界前台会话上下文，以及不保存 transcript 的 compact 会话状态；
- 精确绑定、带过期时间、只能消费一次的 preview 确认；
- 每项目一个按需启动的 authoritative daemon，客户端断开后继续已确认 Mission；
- 确定性重连、崩溃协调，以及精确 permission/ownership/safety 暂停；
- Mission、审批、dispatch、inbox/reply/ack、trace、workflow 与恢复底座；
- 配置且 ready 时使用 ACP Worker，绝不静默切换 transport；
- 只读 tmux 可视镜像、显式 reroute/takeover 与 single-writer ownership；
- ProjectView，以及 conversation、Leader、Worker transport 的版本化 GUI contract；
- 受治理的 Skill 与 Memory provenance。

常用观察入口：

```bash
agentdeck status
agentdeck workbench
agentdeck controls
agentdeck events --limit 20
agentdeck contract conversation-runtime --example
agentdeck contract leader-backend --example
agentdeck contract worker-transport --example
agentdeck contract migration --example
agentdeck project migration-preview
```

## 安全边界

自然语言本身永远不是执行授权。确认必须绑定精确执行事实；ACP 不会静默降级成 tmux；permission、approval、runtime safety 与 ownership gate 彼此独立。常见的内联 credential 赋值会在持久化 Mission provenance 中被遮蔽。

对于 semantic Mission，AgentDeck 是围绕 LLM 推理建立的控制平面，而不是
替代 LLM。用户提供 required authority；Leader 只能添加单独可见的 proposal；
有歧义的事实保持 unresolved；只有人类确认的精确 preview 才会成为 frozen
authority。AgentDeck 随后确定性编译 Worker task，并把确认绑定到 authority、
compiled-task、policy 和 preview-generation 事实。一次 Mission 确认不会授予
后续 ACP tool permission，也不会绕过 runtime safety、ownership 或 approval gate。

ProjectView 只暴露 compact semantic provenance：schema/state、hash、计数、
compiled-step count 和 blockers；不会暴露完整 effect、before/after literal、
prompt 或 secret。本切片不加入 A2A、远程执行、GUI 重设计或终端模拟器。

Phase 3 M2 现已由一个经过验证、按需启动的项目 daemon 推进 admitted
frozen Mission。关闭交互客户端不会撤销 frozen authority，也不会停止
scheduler。AgentDeck 负责所有 Worker 状态转换，必须先记录 compact handoff
再启动下一 Worker，并严格使用配置的 ACP 或 tmux transport，绝不静默降级。
出现新 permission、歧义、ownership 冲突、drift 或 safety escalation 时，
Mission 会暂停并等待精确人工决定。bare `agentdeck` 仅从 compact ProjectView
事实确定性重连，不调用 LLM、不重建 transcript。已有项目先走只读 migration
preview，再显式确认；缺少完整 snapshot 的历史 Mission 保持 inspect-only。

M2 仍是 project-local。A2A、远程 daemon、global roaming、通知、Desktop/IDE
Workspace Client、完整 transcript 恢复、自动安装/登录、Windows IPC 和终端
模拟器仍属于未来工作。

## 架构

```text
Human / CLI / future TUI or Desktop
              |
      ConversationSession
              |
 Mission / Approval / Ledger / Recovery
              |
   Protocol-native Runtime Kernel
       /                 \
     ACP            tmux visible plane
       \                 /
   Codex / Claude / other Agents
```

ACP 用来标准化 Agent 通信，但不会替代 AgentDeck 的 Mission、policy、scheduler、audit 和 recovery。

## 文档

- [产品北极星](docs/roadmap/product-north-star.md)
- [Phase 3 M1 设计](docs/superpowers/specs/2026-07-13-agentdeck-foreground-conversation-design.md)
- [当前开发状态](docs/handoff/current-development-state.md)
- [Contract 索引](docs/contracts/contract-index-schema.md)
- [架构文档](docs/architecture/)

在项目环境中运行验证：

```bash
conda run --no-capture-output -n agentdeck pytest -q
conda run --no-capture-output -n agentdeck python -m compileall src tests -q
```
