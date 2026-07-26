# 2026-07-26 Copilot Line 1 Round 8：单命令走开链路 live 验证（PASS）

## 目的

对同日落地的走开体验四大件做合成整环实证：scoped 授权委托（652e500f）、
`run-loop --follow`（3514c7fa）、`--all` 摄入（81556f5d，本轮走单计划路径
未触发）、`merge-plan`/`--merge-on-complete`（c98e1f39）。目标：approve-plan
之后一条命令、零人工干预直到合并完成。

## 设置

- scratch 项目，DeepSeek Leader（v4-pro），planner=codex(shared)、
  coder=codex(worktree)、reviewer=claude(worktree)。
- 预授 15 条窄前缀委托（3 agent × 5 前缀：`node tests/`、`git add`、
  `git commit`、`git diff`、`git status`）——delegation registry live 首用；
  绝不含 push/安装/网络前缀。
- 任务：round 7 reviewer 留下的 G1（WeakSet 守卫缺测试保护）/G2（原设
  tabindex=-1 保持断言）测试缺口。

## 执行

1. `run --task` → DeepSeek 拆 3 步；`approve-plan --confirm` 一次批准 3 条。
2. **唯一的人类命令**：`run-loop --plan-id pln_e5e05520637b --confirm
   --follow --max-waves 60 --interval 20 --release-boxes --merge-on-complete`。
3. 此后零干预：31 wave 自主推进——顺序守卫逐步派发 planner→coder→reviewer，
   文件通道摄入 3/3（全部 `captured_from=file`），**4 个授权框全部由委托
   自动放行**（`auth_box_released` `source=run_loop_follow`：planner 1 +
   coder 3，全为 `node tests/*` 前缀命中），gate 到 `complete` 后
   merge-on-complete 按 step 顺序**自动合并两条任务分支**（coder 测试提交
   `ca1f057 test: cover carousel tabindex edge cases` + reviewer 审查文档
   `227f710`），追加 `run_loop_follow_completed`
   `{wave_count:31, released_boxes:4, stopped_reason:complete}`。
4. 未委托框哨兵全程未触发（无委托覆盖不了的框）。
5. 收尾：ack 清账、release 3/3 零 dirty（两 worker 都自主 commit）、
   prune 全回收（仅剩 main）、main 双回归测试 exit 0、summary `ready`。

## 结论

**"确认一次走开"已从多命令人肉编排收敛为单命令**。人工点仅剩：说目标、
approve-plan 一次确认、发射 follow 命令。全链路审计完整（`approval_decided`
→ `worktree_created` → `auth_box_released` → `run_loop_reply_captured` →
`worktree_merged` → `run_loop_follow_completed`）。

对照演进：round 6 = 7 框逐次人工放行 + 人工 merge；round 7 = 6 框逐次放行
（含委托问答）+ 人工 merge；**round 8 = 0 框人工放行 + 0 人工 merge**。

## 观察与遗留

1. 本轮 codex 未对 worktree 内 git 写弹框（round 7 弹过）——codex 侧
   会话内先例学习所致，git 前缀委托本轮未被消费但仍是正确的防御性配置。
2. wave 数 31 中约 2/3 是等待 worker 的空 wave（20s 间隔轮询）——效率
  可接受；若要降噪可后续考虑动态间隔（非必须）。
3. G1 结论（coder/reviewer 一致）：现有 WeakSet 守卫在新增二次失活场景下
   有效，断言保留为回归防护；G2 断言补齐。测试资产第三轮跨轮复利。
