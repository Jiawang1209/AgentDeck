# AgentDeck Development History

本文件记录 AgentDeck 每一次开发内容。约束：每次新增功能、文档规则、项目骨架、运行环境或用户可见行为变化，都必须同步更新本文件，并在同一次 commit 中提交。

## 2026-07-04

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
