# 任务级 Worktree 隔离设计（已拍板，进入实施）

- 日期：2026-07-25（隔夜 gap-loop 切片 5 产物；同日下午 user 拍板）
- 状态：**四个开放决策已由 user 确认，实施计划见
  `docs/superpowers/plans/2026-07-25-task-worktree-loop.md`**
- 已定决策：**A**=dispatch prompt 声明 cd（兼容常驻 pane，MVP）；
  **B**=人工 diff（只读 `worktree diff`）+ 显式 `worktree merge --confirm`，
  不自动合并；**C**=`worktree prune --confirm` 只删"分支已合入主干"或
  "已被显式 `worktree abandon --confirm` 标记"的 worktree，两路径各自审计，
  dirty 且未标记的永不可删；**D**=reviewer 用自己的 worktree 检出 coder 的
  任务分支（可实测、改不到 coder 目录、越权改动留在自己分支可审可弃）。
- 实施护栏：worktree 创建仅在 `workspace_mode=worktree` 且项目为真实 git
  仓库时发生；否则按现状派发并在 message provenance/事件中记录未启用
  （可审计的降级，不是静默）。
- Round 6 live 补充（2026-07-25 晚）：worktree-mode dispatch prompt 除声明
  cd 外必须要求任务完成时把改动 git commit 到当前任务分支（不 push、不切换
  分支）——live 实证 worker 天性不主动 commit，而 diff/review 检出/merge
  只看任务分支上的 commit，未 commit 则整个审阅面为空。
- Round 6 live 补充二：worktree-mode dispatch prompt 必须把产物路径钉到主
  仓库 `.agentdeck/artifacts/`（绝对路径）——live 实证 reviewer 把 review
  报告写进自己 worktree 内嵌的相对路径，prune 时产物会连带被删。
- 来源：G4 审计缺口（`workspace_mode="worktree"` 仅 config 声明未落地）+
  Line 1 live 实证（reviewer 越权顺手修 bug，共享工作区无隔离）

## 目标

把 coder/reviewer 变成任务级动态节点：按任务准备隔离工作区，任务完成
release 后只保留 artifact、reply、trace 和 summary，不让 worker 吃全局历史，
也不让 reviewer 能直接改 coder 的工作区。

## 设计空间与建议

### 1. 目录约定

```text
.agentdeck/worktrees/<agent_id>/<message_id>/   # 每次 dispatch 一个
```

- 建议以 `message_id` 为粒度（一次 dispatch = 一个任务包 = 一个 worktree），
  与账本 lineage 天然对齐，trace 可直接关联。
- worktree 从项目当前 HEAD 创建分支 `agentdeck/<agent_id>/<message_id>`。
- `.agentdeck/worktrees/` 加入项目 gitignore 约定（同 `.agentdeck/` 现规则）。

### 2. 创建时机与路径

- `agentdeck dispatch` / `approval dispatch` 在目标 agent
  `workspace_mode == "worktree"` 时：dispatch 前创建 worktree，把 worker pane
  的 cwd 指向该 worktree（spawn 已发生时用 tmux send `cd`? —— **开放问题 A**：
  更稳妥的做法是 spawn 时即按待派任务创建，或 dispatch prompt 中显式声明
  工作目录并要求 worker `cd`；两者都不改变现有 pane 生命周期）。
- message record 新增 `worktree_path` + `worktree_branch` provenance 字段
  （compact，入 ProjectView `messages.items[]` 与 trace）。

### 3. 回收与 release 语义（安全边界核心）

- `agent release --confirm`（切片 4 已落地）扩展：release 时**绝不删除
  worktree**。若 worktree 有未提交/未合并改动，release 照常释放 pane，但
  响应与事件必须列出 `dirty_worktrees[]`，交人工处理。
- 新增显式 `agentdeck worktree list` / `worktree archive --message-id <id>
  --confirm`（后者只 merge/打包产物到主工作区或 artifact 目录，且必须
  人工确认）。**开放问题 B**：merge 策略（cherry-pick / merge / 人工 diff
  审查后手动合并）——建议 MVP 只做"人工 diff 审查 + 显式 merge 命令"。
- 磁盘回收（删除已 archive 的 worktree）单独 `worktree prune --confirm`，
  只允许删除"分支已合并或被显式标记放弃"的 worktree。**开放问题 C**：
  放弃标记的审计形态。

### 4. 与既有系统的关系

- **审批/权限**：worktree 只是隔离，不是授权变化；dispatch 仍走 approval gate。
- **workflow/mission**：workflow steps 与 Mission attempt 同样以 message 为
  粒度，可无缝复用 per-message worktree；daemon 的 `_bounded_worktree_snapshot`
  只读快照逻辑可直接对新目录生效。
- **capture-reply/artifact**：reply 文件通道路径不变（项目根
  `.agentdeck/replies/`，worker 从 worktree 内写绝对路径）；artifact 登记时
  记录 worktree 内相对路径 + branch。
- **零件复用**：`codex/product-kernel-rewrite` 有 worktree 管理零件、
  `codex/p1-durable-mission-kernel` 有 ownership/快照实现，可对照取材，
  但不整分支合并。

### 5. 北极星验收对照（roadmap G4）

- coder 不需要读取全局历史 → worktree + 任务 brief 即上下文。
- 每个任务能追踪 worktree、pane、skill snapshot、artifact、测试结果 →
  message provenance 字段 + lifecycle card 扩展 `worktree` 字段。
- release 不会删除未经确认的用户改动 → 上述"绝不删除 + dirty 列表"边界。

## 建议的实施切片（拍板后另立 TDD 计划）

1. message provenance 字段 + worktree 创建（dispatch 路径，fake git 测试）。
2. lifecycle card / trace 暴露 worktree 字段（只读）。
3. `worktree list`（只读）→ `archive --confirm` → `prune --confirm`。
4. Line 1 live 验证一轮（人工授权门）。

## 待 user 决策（开放问题汇总）

- A：worker 进入 worktree 的方式（spawn 前定 cwd vs prompt 声明 cd）。
- B：产物合并策略（建议 MVP = 人工 diff + 显式 merge 命令）。
- C：放弃/清理的审计形态与 prune 边界。
- D：reviewer 是否只读挂载 coder worktree（隔离审查）还是独立 worktree。
