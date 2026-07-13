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
```

## 安全边界

自然语言本身永远不是执行授权。确认必须绑定精确执行事实；ACP 不会静默降级成 tmux；permission、approval、runtime safety 与 ownership gate 彼此独立。常见的内联 credential 赋值会在持久化 Mission provenance 中被遮蔽。

Phase 3 M2a 现已提供经过身份验证的单项目 daemon 基础、`agentdeck daemon status/start/stop/logs`，以及 compact ProjectView/workbench discovery contracts；但它还不会在后台推进 Mission。M2a scheduler 会明确显示 inactive，直到 M2b 完成冻结执行快照、确定性调度、Worker supervision 与 recovery。完整 transcript 恢复、全局项目漫游、Desktop/IDE Workspace Client、自动安装/认证 adapter，以及原生同会话 TUI attach 仍属于后续工作。

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
