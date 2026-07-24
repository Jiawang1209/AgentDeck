# Post-Line-1 加固循环计划（自主 /loop 执行）

来源：2026-07-23/24 Line 1 live round 2 的实测缺口（见
`docs/validation/2026-07-24-copilot-line1-live-round2-iae-homepage.md`）。
本计划只含**纯代码/文档 TDD 切片**；一切 live 步骤（真实 provider、真实 tmux worker、
round 3 返工）都不在本 loop 内，必须人类在场另行授权。

## 硬边界（每次迭代先读）

- 绝不 `git push`；只本地 commit。
- 绝不调用真实 LLM provider（不 export key、不发请求、不花钱）。
- 绝不 spawn 真实 tmux pane / 真实 coding agent；测试一律用 fake backend。
- 每切片严格 TDD：RED（先看到失败）→ GREEN（最小实现）→ 全量 `pytest tests/ -q` 绿
  + `python -m compileall src` → HISTORY.md 同 commit。
- 改动触及 contract 字段时，必须同步 `docs/contracts/*.md`、`contracts.py` 的
  fields/validator/example、README（如提及）、测试——项目 CLAUDE.md 的同步规则全部适用。
- 遇到需要产品方向决策的 fork：停下，把问题写进本文件"## 阻塞"节，跳到下一切片；
  全部切片完成或阻塞时结束 loop。
- 每完成一个切片：在本文件对应条目打 `[x]`，commit 信息按项目惯例。

## 切片清单（按序执行）

### [x] A. spawn 默认可见布局（tiled + pane 标签）

- 现象：spawn-ready 后 pane 垂直死叠、无标签，小窗口下挤到 1 行高，"可见 runtime"不成立。
  Live 中手工 `select-layout tiled` + `select-pane -T <agent>` + `pane-border-status top` 后体验立好。
- 目标：`agentdeck agent spawn` / `agent spawn-ready --confirm` 成功创建 pane 后，
  TmuxBackend 自动应用 tiled layout、为每个 agent pane 设置 title=agent_id、
  开启 pane-border-status top。只影响新 spawn 路径；capture/send/stop 语义不变；
  只读命令仍绝不触碰 tmux。
- 实现位置：`runtime/tmux.py`（新增 layout 方法）+ `cli.py` spawn 两条路径调用。
- 测试：现有 fake/subprocess mock 风格，断言 tmux 调用序列；不起真 tmux。

### [x] B. worker 等待输入显性化（只读启发式）

- 现象：worker（codex/claude CLI）弹权限确认框时任务静默停住，live 全靠脚本 grep
  "Press enter to confirm" / "enter to submit" 才发现。
- 目标：`agentdeck agent capture` 响应新增只读派生字段
  `waiting_for_input`（bool）+ `waiting_hint`（命中的提示行或 null），
  基于已知 TUI 确认框样式的行级启发式（至少覆盖 codex 的 "Press enter to confirm"、
  Claude Code 的 "enter to submit"、通用 "Would you like to"）。纯读取派生，
  不写 state、不发送输入、不改变 capture 既有字段语义。
- 同步：若 `capture_card` 契约字段列表（leader-chat contract）需要扩展，按规则同步
  contract 文档、fields、validator、example 和测试；capture 卡的既有断言不得回归。
- 测试：fake backend 输出含/不含确认框两类用例 + 卡片契约用例。

### [x] C. leader review 部分派发感知

- 现象：plan 还有未派发的 approvals（pending/approved 未 dispatch）时，
  `leader review` 就返回 `next_action=summarize`（"all dispatched steps have replies"），
  把没跑完的计划当可总结。
- 目标：review 在存在本 plan 未派发 approval 时不得建议 summarize，应指向审批队列的
  下一步（复用既有 next_action 枚举与 next_command 惯例；如需新枚举值，先查
  `docs/contracts/leader-review-schema.md` 与 validator，同步一切）。
- 风险提示：`run_loop_gate()` 与 `run-loop` 的 stop_reasons 消费 review 结果，
  改语义时必须让 run-loop / run-loop-all 契约与测试同步一致，不得破坏其枚举。
- 测试：部分派发场景 RED 用例 + 既有全回复场景不回归 + run-loop gate 相关用例。

### [ ] D. Round 3 live 返工 runbook（纯文档）

- 目标：新增 `docs/validation/2026-07-24-copilot-line1-round3-runbook.md`：
  以 reviewer 报告 `review-index-html-2026-07-23.md` 的改进清单为输入，
  把"审查意见派回 coder 返工"的整轮步骤写成人工授权 runbook
  （dispatch 任务模板引用报告路径、验证文件通道回复是否被真实 worker 遵守的观察点、
  成功判据）。不执行任何 live 步骤。

## 阻塞

（loop 运行中如遇 fork 决策，在此记录后跳下一切片）

## 完成判定

四个切片全部 `[x]`（或余项全部记入"阻塞"）→ 更新本文件状态、确保全部已 commit、
结束 loop（ScheduleWakeup stop），在最终回复里汇总产出与阻塞。
