# 北极星差距收敛循环计划（自主 /loop 执行，2026-07-24 夜）

来源：`docs/roadmap/2026-07-24-north-star-gap-review.md` 的建议优先顺序。
user 已授权隔夜自主推进；一切 live 步骤（真实 provider、真实 tmux worker）
仍是人工授权门，不在本 loop 内。

## 硬边界（每次迭代先读）

- 绝不 `git push`；只本地 commit。
- 绝不调用真实 LLM provider（不 export key、不发请求）。
- 绝不 spawn 真实 tmux pane / 真实 coding agent；测试一律 fake backend。
- 代码切片严格 TDD：RED → GREEN → 全量 `pytest tests/ -q` 绿 +
  `python -m compileall src` → HISTORY.md 同 commit。
- 触及 contract 字段必须同步 `docs/contracts/*.md`、`contracts.py`
  fields/validator/example、contract index、README（如提及）、测试。
- 遇到产品方向 fork：停下记入"## 阻塞"，跳下一切片；全部完成或阻塞时结束
  loop（ScheduleWakeup stop）并 PushNotification 汇总。
- 每完成一个切片：本文件打 `[x]`，独立 commit。

## 切片清单（按序执行）

### [x] 1. 文档 reconcile：north-star / handoff 对齐 Line 1 路线

- 现象：`product-north-star.md` 与 `docs/handoff/current-development-state.md`
  停在 2026-07-17 的 P0–P5 叙事（"P1 locked 待人工评审"），与 7-23 Line 1 转向
  （真实 agent 先行、旋钮式泛化，round 2 PASS + 加固 A–F）互相矛盾。
- 目标：north-star 增补"2026-07-24 路线现状"小节：Line 1 旋钮路线是 P2–P4 的
  实证先行路径，daemon 冻结 Mission 路线保留为收敛目标，两者关系写明；handoff
  的 Active goal 改写为 Line 1 阶段现状 + 下一刀（旋钮 2/3、G4）。不删历史证据，
  只加现状层。纯文档，不改代码。

### [x] 2. 旋钮·确认粒度：整计划一次批准（explicit 批量 approve）

- 现象：Line 1 锁定"每次派活前确认一次"；北极星确认粒度旋钮需要"整计划一次
  确认"档位。现只有逐条 `approval approve` 与派发侧的 `dispatch-ready --confirm`，
  没有批准侧的整计划一档。
- 目标：新增显式 `agentdeck approval approve-plan --plan-id <id> --confirm`：
  一次批准该 plan 全部 pending approvals（逐条 `approval_decided` 审计 + 一条
  汇总事件），缺 `--confirm` 拒绝且零写；不 dispatch、不碰 runtime；未知 plan
  或无 pending 时非 0。响应含逐条结果与 next_command（指向 dispatch-ready）。
  同步 approvals contract 文档/fields/validator/example、README、HISTORY、测试。

### [x] 3. 旋钮·自主度：reply 文件通道就绪显性化（只读）

- 现象：run-loop 停在 `waiting_for_reply` 后交回人工 capture-reply；北极星
  "确认一次走开"需要 loop 能感知 worker 已完成。文件通道回复（ab5902bf）落地后，
  reply 文件的存在是确定性完成信号，但 recovery/review/run-loop 都不感知它。
- 目标（只读切片）：`status.recovery` 的 `reply_waiting` 与 `run-loop` 的
  `waiting_for_reply` 响应新增只读派生字段 `reply_file_ready`（bool，检测
  `.agentdeck/replies/<message_id>.reply.txt` 是否存在）+ 既有 next_command 不变。
  不自动 capture。同步 project-view/run-loop contract、测试。
- 注意：run-loop 自动回收文件通道回复（写路径）是产品 fork，记入阻塞，
  本切片只做感知。

### [ ] 4. G4 前半：worker 生命周期 released 阶段

- 现象：`_worker_lifecycle_stage` 无 released 态；任务完成后 pane/context 不回收，
  无法在拓扑卡看出"已释放"。
- 目标：最小闭环——新增显式 `agentdeck agent release --agent <id> --confirm`：
  仅当该 agent 无未回收 dispatch（无 active job/pending inbox head）时允许，
  stop pane（复用既有 kill_pane 语义）+ 标记 runtime binding 为 `released` +
  `agent_released` 审计事件；lifecycle_stage/role_topology 显示 released。
  缺 confirm 或有未完结任务时拒绝且零写。同步 agent-runtime/workbench contract、
  测试。不自动 release、不删除文件。

### [ ] 5. G4 后半：任务级 worktree 隔离（评估切片）

- 先只读评估：dispatch 时按 `workspace_mode=worktree` 为任务创建 git worktree
  的设计空间（目录约定、release 时保留未确认改动、与 workflow/mission 的关系），
  写成 spec 草稿 `docs/superpowers/specs/2026-07-2x-task-worktree-design.md`
  供 user 醒来评审。**实现是产品 fork，不在本 loop 内动代码**；spec 写完即算
  本切片完成。

### 明确不做（记录即可）

- SQLite 前向移植（等 JSON 疼 + user 拍板）。
- GUI/web 传输层、G2 per-role provider 绑定、G5 验收分数（均为产品 fork，
  醒来后决策）。
- Round 3 live 回收（需 user 在场）。

## 阻塞

（运行中遇 fork 在此记录后跳下一切片）

## 完成判定

五个切片全部 `[x]`（或余项记入阻塞）→ 更新本文件、确保全部 commit、
PushNotification 汇总产出与阻塞 → ScheduleWakeup stop 结束 loop。
