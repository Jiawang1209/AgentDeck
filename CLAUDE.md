# CLAUDE.md

本文件帮助 Claude Code 或其他 coding agent 快速理解本项目。

## 项目定位

AgentDeck 是一个 local-first 多智能体终端工作台。目标是用 DeepSeek 等 LLM 做 Leader Agent，调度多个 Worker Agent，在 tmux 可见终端里执行任务，并通过消息账本、审批、状态存储和 ProjectView 保持可审计、可恢复。

核心设计文档：

- `docs/architecture/multi-agent-terminal-design.md`
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
pytest tests/test_agent_cli.py -q
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
