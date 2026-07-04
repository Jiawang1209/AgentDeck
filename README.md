# AgentDeck: Local Multi-Agent Terminal Workbench

AgentDeck 是一个正在搭建中的本地多智能体终端工作台。它的目标是让 DeepSeek 等 LLM 作为 Leader Agent，把任务分发给多个 Worker Agent，并在 tmux 可见终端中执行、观察、审批、恢复和审计。

本项目不是要从零重写终端模拟器，而是先把四类能力融合起来：

- WispTerm 式 AI 原生终端控制面。
- Claude Codex Bridge 式多 Agent 通信、mailbox 和 tmux panes。
- Hermes Agent 式 Leader/Worker 隔离、工具注册表、skills/memory 和 guardrails。
- tmux 式长期运行、可见、可输入、可读取、可恢复的终端 runtime。

详细架构见：[docs/architecture/multi-agent-terminal-design.md](/Users/liuyue/Desktop/Github_repos/multi-agent-explore/docs/architecture/multi-agent-terminal-design.md)。

## 技术栈

当前骨架选择：

- Python 3.12，使用 Miniforge/conda 环境 `agentdeck`
- 标准库 CLI，无强制第三方依赖
- tmux 作为第一 runtime backend
- TOML 配置
- JSON/JSONL 初始状态存储，后续迁移到 SQLite
- DeepSeek/OpenAI-compatible provider adapter 骨架

未来可扩展：

- 本地 daemon / loopback API
- Web 或桌面 GUI
- control mode watcher
- SQLite message bureau
- skill snapshot 和 MCP adapter

## 当前能力

已提供最小可运行骨架：

```bash
conda activate agentdeck
agentdeck doctor
agentdeck project init
agentdeck status
```

`project init` 会创建：

```text
.agentdeck/
  config.toml
  state/
    state.json
    events.jsonl
    approvals.jsonl
  logs/
    agents/
  artifacts/
  skills/
```

## 快速开始

在项目根目录创建并激活开发环境：

```bash
conda env create -f environment.yml
conda activate agentdeck
python -m pip install -e .
```

之后所有开发命令默认都在 `agentdeck` 环境中运行：

```bash
agentdeck doctor
agentdeck project init
agentdeck status
python -m compileall src
```

DeepSeek API key 后续会通过环境变量读取：

```bash
export DEEPSEEK_API_KEY="..."
```

当前版本只搭建 provider adapter 边界，不会主动调用外部 LLM。

## 设计原则

- Local-first：本地项目、本地状态、本地终端优先。
- Human-in-the-loop：危险操作必须可审批、可追踪。
- Agent-first：用户面向 agent name，而不是 provider 或 pane id。
- Visible runtime：Worker 运行在可见终端里，人类可以随时接管。
- Recoverable state：任务、消息、job、reply、artifact 都要能追溯。
- Small core：核心保持窄，工具、skills、provider、runtime 通过边界扩展。

## 参考分析

四份参考仓库分析保存在：

- [WispTerm 分析](/Users/liuyue/Desktop/Github_repos/multi-agent-explore/docs/reference-analysis/wispterm-main.md)
- [Claude Codex Bridge 分析](/Users/liuyue/Desktop/Github_repos/multi-agent-explore/docs/reference-analysis/claude-codex-bridge-main.md)
- [Hermes Agent 分析](/Users/liuyue/Desktop/Github_repos/multi-agent-explore/docs/reference-analysis/hermes-agent-main.md)
- [tmux 分析](/Users/liuyue/Desktop/Github_repos/multi-agent-explore/docs/reference-analysis/tmux-master.md)

## 开发约束

- 每次新增功能或用户可见行为变化都要 commit。
- 每次开发内容都要同步写入 [HISTORY.md](/Users/liuyue/Desktop/Github_repos/multi-agent-explore/HISTORY.md)，并和对应改动放在同一次 commit 中。
- `References/` 是本地研究材料，不纳入 git。
- README、CLAUDE.md、AGENT.md 和架构文档要跟代码同步。
- 所有开发命令默认先执行 `conda activate agentdeck`。
- 不在 MVP 阶段重写终端模拟器。
- 不在没有审批模型前实现自动写文件、kill pane、push、remote relay。
