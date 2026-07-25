# 任务级 Worktree 实施循环（自主 /loop 执行，2026-07-25 下午）

来源：`docs/superpowers/specs/2026-07-25-task-worktree-design.md`（user 已
拍板 A/B/C/D）。live 验证轮（round 6）等 user 在场，不在本 loop 内。

## 硬边界（同前轮）

- 绝不 `git push`（含 worktree 分支）；不调真实 LLM；不碰真实 tmux。
- 测试用临时目录内的**真实 git 仓库**（`git init` + 初始 commit），不 mock
  git 语义；既有 fake `.git` 目录场景必须走"未启用"降级路径不回归。
- 严格 TDD：RED → GREEN → 全量 pytest 绿（pipefail）+ compileall →
  HISTORY 同 commit；契约同步全量（schema docs、contracts.py、CLAUDE.md、
  README、测试）。
- worktree 目录 `.agentdeck/worktrees/<agent_id>/<message_id>/`，分支
  `agentdeck/<agent_id>/<message_id>`；创建仅当 `workspace_mode=worktree`
  且 `git rev-parse --git-dir` 成功，否则按现状派发并在 message
  provenance（`worktree_path=null`）+ `worktree_skipped` 事件中记录降级。
- 绝不自动 merge/删除；dirty 且未 abandon 的 worktree 永不可删。
- 产品 fork 停下记阻塞；完成或全阻塞时收尾（PushNotification + stop）。

## 切片清单（按序执行）

### [x] 1. dispatch 集成：创建任务 worktree + prompt 声明 cd + provenance

- `dispatch` / `approval dispatch` / run-loop 派发路径：目标 agent
  `workspace_mode=worktree` 且真实 git 仓库时，从当前 HEAD 创建分支+
  worktree，dispatch prompt 头部加显式工作目录声明（决策 A），message
  record 新增 `worktree_path`/`worktree_branch`（compact provenance，入
  ProjectView `messages.items[]` 与 trace），`worktree_created` 事件；
  降级路径记 `worktree_skipped`。project-view/trace contract 同步。

### [x] 2. reviewer 检出同分支（决策 D）

- 派发某 plan step 时，若该 plan 更早 step 已有 `worktree_branch` 且当前
  agent 也是 worktree 模式，则新 worktree 检出**该分支**（而非 HEAD），
  provenance 记 `worktree_base_branch`。reviewer 由此在自己的 worktree
  实测 coder 产物。

### [x] 3. `worktree list` + `worktree diff`（只读）

- `agentdeck worktree list`：从 messages provenance + 磁盘状态派生
  worktree 清单（agent/message/branch/path/exists/dirty/merged/abandoned）；
  `agentdeck worktree diff --message-id <id>`：只读展示该分支对主干的
  `git diff --stat` + name-status（不读文件全文进 state）。二者不写 state。
  新 contract 惯例全量同步。

### [x] 4. `worktree merge/abandon/prune --confirm`（决策 B/C）

- `merge --confirm`：将任务分支合入当前主分支（无冲突 fast-forward/普通
  merge；冲突则拒绝并零写，交人工）；`abandon --confirm`：显式标记放弃
  （state 记录 + 事件）；`prune --confirm`：只删已合并或已 abandon 的
  worktree（`git worktree remove` + 分支删除按已合并才删），dirty 未标记
  拒绝。三命令均要求 `--confirm`、各自审计事件、缺 confirm 零写。

### [x] 5. 生命周期/释放联动

- `agent release` 响应与事件新增 `dirty_worktrees[]`（该 agent 名下未合并
  未放弃的 worktree 清单，仅列出绝不删除）；agent-runtime contract 同步。
- **执行中偏差（已记录）**：`worker_lifecycle_card` 的 worktree 计数字段
  取消——卡片是纯 payload 派生（不得 shell git），且 provenance 级计数
  GUI 可直接从 ProjectView `messages.items[].worktree_path` 得到；为一个
  便捷计数改写 126 处精确断言不成比例。git 级 dirty 明细由 release 响应
  与 `worktree list` 提供。

### 不在本 loop（等 user）

- Round 6 live 验证（scratch 项目 reviewer 需改 workspace_mode=worktree，
  由 user 确认后执行）；任务级 spawn 定 cwd（决策 A 的二期）；审批后自动
  merge（决策 B 的二期）。

## 阻塞

（运行中遇 fork 在此记录）

## 完成判定

五切片全部 `[x]`（或余项记阻塞）→ 全部 commit → PushNotification 汇总
→ stop。
