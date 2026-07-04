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
agentdeck leader plan --task "设计自动 reply extraction"
agentdeck plan list
agentdeck plan show --plan-id pln_xxx
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
- 自然语言任务调度优先从 `agentdeck leader plan --task <text>` 生成 plan-only 记录开始；不要跳过 plan 直接自动 dispatch。
- 审批、dispatch 或恢复任务前优先用 `agentdeck plan list`、`agentdeck plan show --plan-id <id>` 和 `agentdeck plan status --plan-id <id>` 检查计划。
- 使用 `agentdeck approval create-from-plan --plan-id <id>` 创建审批项，使用 `approval approve/reject` 更新状态；只有 approved approval 才能通过 `agentdeck approval dispatch --approval-id <id>` 派发。
- Worker 输出结构化结果后，优先使用 `agentdeck capture-reply --agent <id> --message-id <id>` 从 pane 回收入账；手动 `reply` 作为兜底。
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
