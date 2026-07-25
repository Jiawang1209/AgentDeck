# 2026-07-26 Copilot Line 1 Round 7：worktree 四修复 live 验证（PASS）

## 目的

对 round 6 发现的四个修复切片（`8a8248d4` prompt 要求 commit、`c9715703`
in_flight prune 守卫、`d1df395e` 多行 full_output_path、`cbfccb1c` 产物钉扎）
做真实整环验证。任务=round 6 reviewer 留下的 M1 修复（`removeAttribute`
销毁作者原设 tabindex，需保存/恢复 + RED 用例）。

## 整环时间线

1. spawn-ready 3/3；DeepSeek 拆 2 步（coder 实现→reviewer 审查，任务自带
   指引故无 planner 步）；approve-plan 一次批准 2 条。
2. wave 1 派 coder；**新 prompt 三行全部在场**（commit 要求、不要 push、
   产物钉扎到主仓库 artifacts）；worktree `in_flight=True` 且 `merged=True`
   平凡真——当场跑 `worktree prune --confirm` 实测守卫：零删除（coder 已
   开工被 dirty 守卫先拦；in_flight 是 clean 窗口的后备闸）。
3. coder TDD 全程：RED（注入 tabindex=0 断言 null!=='0' 失败）→ 实现
   （data-prev-tabindex 快照 + WeakSet 防重复失活覆写）→ GREEN → 跨资产
   回归 → **自主 git add/预检/commit `e52f0d1`** → 提交后双测试复验 →
   写回复文件。**全程零补充指令**（round 6 需人工追加 commit 指令）。
4. wave 2 文件摄入 → 派 reviewer；reviewer worktree 从 coder 分支尖建出
   （base 入账），in_flight 状态转移正确（coder→False，reviewer→True）；
   coder 产物落主仓库 `.agentdeck/artifacts/` 并登记账本。
5. reviewer（Claude CLI，auto-mode 零授权框）：独立重跑回归 + **自写 CDP
   探针**实测 WeakSet 守卫（连点当前圆点 3 次不覆写原值）→ PASS，无阻塞；
   G1（守卫缺测试保护）/G2/G3 缺口与 R1-R4 理论风险入池；review 报告写
   主仓库 artifacts ✓；在自己 worktree 内 commit history 条目 `5bea135` ✓。
6. wave 3 摄入 → gate `complete` → summary `ready`；文件通道 2/2。
7. merge reviewer 分支一次覆盖两个 commit（e52f0d1+5bea135 快进 main）；
   main 双测试 exit 0；4 条 inbox ack 清账；release 3/3 且
   `dirty_worktrees` 全空（两 worker 都已 commit——对照 round 6 的 dirty
   报告）；prune 两条路径都走到（coder=git-merged+已回复的 merge-settled，
   reviewer=显式 merged 记录），worktree/分支全回收，只剩 main。

## 四修复验证结论

| 修复 | 结果 |
|---|---|
| prompt 要求 commit（8a8248d4） | **PASS**：coder 自主暂存/预检/commit/复验，零补充指令 |
| in_flight prune 守卫（c9715703） | **PASS**：活跃 worktree prune 零删除；replied 后正常清理 |
| 多行 full_output_path（d1df395e） | 未触发（本轮两 agent 均单行格式）；单元测试覆盖 |
| 产物钉扎（cbfccb1c） | **PASS**：coder/reviewer 产物均落主仓库 artifacts 并登记 |

## 新数据点与发现

1. **第二类授权框**：coder 自主 commit 流程弹出 worktree 内 git 写框
   （git add/`git diff --cached --check`/commit）。scoped 委托的两个标准
   scope 就绪：①本机只读验证命令前缀（`node tests/*`，本轮 5 框）；
   ②任务 worktree 内 git 写（不含 push，本轮 1 框）。codex 均提供
   "don't ask again for prefix" 原生原语可映射。Claude auto-mode 仍零框
   （provider 不对称确认）。
2. reviewer 主动在自己 worktree commit 文档改动——commit 合同对 review
   step 同样生效，且使 release 的 dirty 报告自然归零。
3. 测试缺口池（非阻塞，可并入后续任务）：G1 WeakSet 守卫缺测试保护
   （点当前圆点触发二次失活的断言）；G2 作者原设 tabindex=-1 保持断言；
   G3 hero 轮播无断言。
4. run-loop 每 wave 需人工触发（本轮 3 次）；"确认后自动推进到 gate" 是
   已知待拍板项（daemon/loop 化），非新发现。

## 结论

G4 worktree 生命周期在"worker 自主 commit"合同下已完全自洽：创建→隔离
实现→自主 commit→review 检出→独立验证→合并→账本清零→安全回收，全程
产物零丢失、零人工抢救、零补充指令。下一步大件均为待拍板 fork。
