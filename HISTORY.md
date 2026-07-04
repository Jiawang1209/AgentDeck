# AgentDeck Development History

本文件记录 AgentDeck 每一次开发内容。约束：每次新增功能、文档规则、项目骨架、运行环境或用户可见行为变化，都必须同步更新本文件，并在同一次 commit 中提交。

## 2026-07-04

### Current - Add persistent development history

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
