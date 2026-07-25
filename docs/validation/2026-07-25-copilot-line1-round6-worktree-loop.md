# 2026-07-25 Copilot Line 1 Round 6：任务级 worktree 整环 live 验证（PASS）

## 目的

对 G4 任务级 worktree 五切片（`72b6b2a6`→`f7938250`）做首次真实验证：
worktree-mode dispatch 建 per-message worktree、review step 检出实现分支、
`worktree list/diff` 只读审阅、`merge/abandon/prune --confirm` 生命周期、
release `dirty_worktrees[]` 报告。同时复验 round 4/5 已落地的自主度旋钮
（approve-plan、step 顺序守卫、文件通道摄入解耦）。

## 环境

- scratch 项目 `~/Desktop/agentdeck-live-scratch`，DeepSeek Leader
  （deepseek-v4-pro），planner=codex(shared)、coder=codex(worktree)、
  reviewer=claude(worktree)。
- 前置修正：reviewer `workspace_mode` shared→worktree；scratch 仓库把
  round 1–5 工作产物 commit 成 baseline（`2f7b6da`）并 gitignore
  `.agentdeck/`/`.omc/`——untracked 文件不会进 worktree，这是 worktree 模式
  的隐含前置（见发现 ③ 旁注）。
- 任务：修复头条轮播键盘可访问性（aria-hidden slide 内可聚焦元素
  tabindex=-1，切换同步恢复）。

## 整环时间线（全部 live 证据）

1. `agent spawn-ready --confirm` 3/3 spawned，tiled。
2. `run --task` → DeepSeek 拆 3 步（planner 分析→coder 实现→reviewer 审查），
   3 pending approvals。
3. `approval approve-plan --confirm` 一次批准 3 条。
4. run-loop wave 1：顺序守卫只派 step 1（planner），step 2/3 持留
   `awaiting earlier step completion`；shared 模式 planner 零 worktree（对照组 ✓）。
5. planner 写 `.agentdeck/replies/msg_2a1a6e817552.reply.txt`（含浏览器
   Tab 顺序证据与通用可聚焦后代方案）。
6. wave 2：文件摄入 `captured_from=file` → 同 wave 解锁派 coder；dispatch
   创建 worktree `.agentdeck/worktrees/coder/msg_cd718310caea`、分支
   `agentdeck/coder/msg_cd718310caea`，provenance 三字段入账，
   `worktree list` 投影正确。
7. coder 在 worktree 内完成（RED→GREEN→跨资产回归→终验复跑），写回复文件；
   **未 commit**（发现 ③）。补充单行指令后 codex 主动"提交前复跑两测试"再
   commit `8352ecf fix: isolate carousel slide focus`（+74/−4）。
8. `worktree diff` 从空转为正确 stat；`dirty:false / merged:false` 语义归正。
9. wave 3：摄入 coder 回复 → 派 reviewer；review-step worktree 在 `8352ecf`
   上建出，自有分支 `agentdeck/reviewer/msg_edb8384de49b`，
   `base_branch=agentdeck/coder/msg_cd718310caea`（`8a7f0f6c` live 首验 ✓）。
10. reviewer（Claude CLI）独立复跑测试，PASS + 2 个 latent 中级问题
    （M1 tabindex 原值销毁 / M2 自动播放焦点遮蔽）+ 测试缺口清单，写回复文件。
11. wave 4：摄入 reviewer 回复 → gate `complete`；`leader summary`
    `status: ready`。文件通道 **3/3**（planner/coder/reviewer 全 file）。
12. `worktree merge --message-id msg_cd718310caea --confirm` → main 快进
    `8352ecf`。抢救 reviewer worktree 内产物（review artifact + history 条目）
    后：release 被 pending inbox 正确拦下 → 6 条 head-only ack 清账 →
    release 3/3，reviewer dirty worktree 被精确报告在 `dirty_worktrees[]`
    且未被删（切片 5 ✓）→ `abandon --confirm` → `prune --confirm` 全回收，
    任务分支删净，只剩 main。
13. main 上重跑 `focus-carousel-tab-order.mjs` 与 `back-to-top.mjs` 均 exit 0。

## 验证结论

G4 全生命周期（dispatch 建→隔离实现→review 检出实现分支→只读 diff→显式
merge→dirty 报告→abandon→prune）live 全通；round 4/5 旋钮（approve-plan、
顺序守卫、摄入解耦、`captured_from=file`）复验通过。

## 发现

1. **worker 天性不 commit（核心，产品级）**：codex 完成任务后明确"未提交或
   推送"，整个审阅面（`HEAD...branch` diff、review 检出、merge）在 commit 前
   全部为空。修复方向已 live 验证：一条补充指令即让 codex 自主"提交前复跑
   回归"再 commit——worktree-mode dispatch prompt 应要求任务完成时 commit 到
   任务分支。旁注：untracked 基线文件同理不进 worktree，dispatch 也可考虑
   提示仓库需 clean baseline。
2. **零 commit 分支投影为 `merged:true`**：进行中 worktree（分支尖==base）
   被 `merge-base --is-ancestor` 平凡判真，`prune` 会把活跃任务 worktree 当
   merged-and-clean 删掉。候选修复：零 commit 分支单列 `in_flight` 语义。
3. **worktree 内嵌产物丢失风险**：reviewer 把 artifact 写进自己 worktree 下
   的 `.agentdeck/artifacts/`（相对 cwd），prune 即消失；本轮人工抢救。候选
   修复：dispatch prompt 把 artifact 路径钉到主仓库，或 capture/abandon 前
   自动抢救。
4. **多行 `full_output_path:` 不解析**：coder 回复把路径放在字段下一行，
   artifact 未登记（planner/reviewer 单行格式正常登记）。
5. **`select-pane -T` 标签被 CLI 覆盖**：pane 标题被 codex/claude 自己的
   title 更新覆盖，agent 名标签失效（显示细节）。
6. **权限模型不对称**：Claude CLI auto mode 下跑无头 Chrome 无授权框，
   codex 每条命令逐次弹框——scoped 委托设计需按 provider 区分。
7. **授权框数据点**：本轮 7 框全部为 `node tests/*.mjs` 本机只读回归
   （RED/GREEN/跨资产/终验/commit 前复跑），按命令前缀的 scoped 委托可消
   6/7；codex 选项 2（"don't ask again for prefix"）即其原生委托原语，
   AgentDeck scoped 委托可直接映射。
8. 待复核（可能是采集脚本字段名问题）：`leader summary` 的 `artifacts[]`
   显示为空，而 ProjectView artifacts 已含本 plan 登记项。

## 后续候选切片

- worktree dispatch prompt 要求完成时 commit（发现 1）。
- 零 commit 分支 `in_flight` 语义 + prune 守卫（发现 2）。
- worktree 内产物抢救/路径钉扎（发现 3）。
- 多行 full_output_path 解析容错（发现 4）。
- scoped 授权委托设计（发现 6/7，数据点已足）。
