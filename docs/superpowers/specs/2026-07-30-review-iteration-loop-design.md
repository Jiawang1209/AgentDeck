# Review 驱动的动态迭代闭环设计(review iteration loop)

Status: frozen (user 拍板 2026-07-30)
Owner: copilot-line-1
Prior art: G5 量化验收(`docs/superpowers/specs/2026-07-28-g5-quantified-review-design.md`)、
run-loop 单 wave 引擎、run-loop-host(`2026-07-30-run-loop-host-design.md`)

## 问题

北极星核心工作流是 "Leader 拆解 → coder 写 → reviewer 审 → coder 完善 →
reviewer 终评"。今天前两段已闭环,但 review verdict `overall=fail` 只会扣住
自动 merge 并停在人类 gate:没有任何机制把 reviewer 的意见变成新的 coder
任务再来一轮。plan 是静态线性 step,"审查不通过自动回炉"缺失,walk-away
自主段在第一次 fail 处断裂。

## 用户拍板(2026-07-30,四个决策)

1. **回炉任务源 = 确定性模板**:引擎从 verdict fail 条目 + reviewer 结构化
   回复原文拼出回炉任务,零 LLM 调用。run-loop"绝不调用 Leader provider"
   不变量原样保持。Leader 精修回炉任务留作二期显式命令(非本 spec)。
2. **迭代预算 = config 默认 + 命令覆盖**:`[autonomous] max_review_rounds`
   (默认 2)+ `run-loop` / `run-loop-host start` 的 `--max-review-rounds N`
   覆盖;`0` = 关闭迭代,行为逐字节同现状。
3. **审批语义 = 普通 pending 审批**:自动追加的 step 生成普通 pending
   approvals,由既有 autonomous 白名单 + `max_approvals` 预算 auto-approve
   接手。零新授权面;非 autonomous 模式下就是普通待审批项。
4. **触发面 = 引擎内置 + 显式命令双面**:run-loop 单 wave 引擎在摄入 fail
   verdict 后自动追加(受预算控制);新显式命令
   `agentdeck plan rework --plan-id <id> --confirm` 让人工流程触发同一逻辑。
   两面共享同一实现。

## 架构(user 认可方案 A)

- **新纯模块 `src/agentdeck/review_iteration.py`**:只做推导,不碰 IO、不
  import cli/state。输入 = plan record + 该 plan 的 replies(含 verdict)+
  预算;输出 = "应追加的 rework/re-review step 对" 或 None(带拒绝原因)。
- **`StateStore.append_review_iteration(plan_id, ...)` 单点 writer**:唯一
  state 写点——追加 steps 进 plan record、为新 steps 创建 pending approvals
  (复用 `create_approvals_from_plan` 的 step→approval 语义)、追加
  `plan_rework_appended` 审计事件。
- **两个消费面**:run-loop 单 wave 引擎钩子、`plan rework` CLI 命令,都只
  调用纯函数 + writer,不各自长拼装逻辑。

## 触发条件(fail-closed)

全部条件同时成立才触发,任何一条不满足都不追加、不写 state:

- 该 plan 存在 review step(既有信号:step 的 dispatch 带非 None
  `base_branch`)的 reply,携带**有效** verdict 且
  `overall ∈ {"fail", "needs_changes"}`(实现期修正 2026-07-30:
  `review-verdict/v1` 的 overall 合法值是 `pass/fail/needs_changes`,无
  unknown;`needs_changes` 语义就是回炉,与 fail 同触发)。criterion 级
  `unknown`、无 `verdict:` 行、解析失败(`review_verdict_invalid`)一律不
  自动迭代——歧义不烧钱,停在今天的人类 gate。
- 该条 reply 尚未触发过迭代(幂等):追加的 step 记录
  `triggered_by_reply: <rep_id>`,推导时凡 `rep_id` 已被任一迭代 step 引用
  即拒绝重复触发。
- 迭代预算未耗尽:该 plan 已追加轮数(从 step 的 `round` 标记推导最大值,
  不另存计数器)`< max_review_rounds`。
- 触发用的 verdict 必须是该 plan **最新**有效 verdict(与
  `plan_verdict_summary` 同源取法):旧 fail 之后已有更新 verdict 时不用旧
  的触发。

## 追加内容(一轮迭代 = 两个 step,确定性模板)

向**同一 plan** 的 steps 末尾追加,编号顺延(`1..n` 连续性保持):

1. **rework step**
   - `agent_id`/`role` = 被审查的实现 step 的 agent(即 review step 检出的
     base branch 所属 step 的 agent;多个更早实现 step 时取 base branch 直接
     来源那一个)。
   - `task` = 固定模板拼装,依次包含:原实现任务一行、verdict 中每条
     `fail` 标准的原文、reviewer 结构化回复原文(总长截断上限 4000 字符,
     截断时注明并附 `agentdeck trace --id <rep_id>` 供查全文)、"修复后
     commit 到任务分支" 指令。纯字符串拼装,零 LLM。
   - `risk` = 沿用原实现 step 的 risk;`requires_approval = true`。
2. **re-review step**
   - `agent_id`/`role` = 原 review step 的 agent/role。
   - `task` = 原 review step 任务原文(逐字节)。
   - `requires_approval = true`。

两个 step 都带 provenance 元数据:`origin: "review_iteration"`、
`round: <N>`(本 plan 第 N 轮迭代,从 1 起)、
`triggered_by_reply: <rep_id>`。普通 step 缺省这些键(旧 plan 零迁移,
投影处 null 占位)。

既有机制自动生效,不新写:后续 step 的 worktree 从同 plan 最新任务分支尖
起建(rework 在实现分支上继续,re-review 检出 rework 产物);re-review
dispatch 因 `base_branch` 非 None 且 plan 有 criteria 自动注入验收标准 +
verdict 输出格式(G5 现行为);文件通道回复、step 顺序守卫、merge gate
全部不变。

## 预算

- `[autonomous] max_review_rounds`,缺省 **2**;解析进 `AutonomousConfig`
  (与 `allowed_agents`/`max_approvals` 同段),非法值(负数/非整数)
  fail-closed 拒绝加载。
- `run-loop`(含 `--follow`)与 `run-loop-host start` 新增可选
  `--max-review-rounds N` 覆盖(`>= 0`;host 透传给 serve)。`0` = 本次
  运行关闭迭代。
- 计数按 **plan 生命周期**累计(不是每次命令调用):plan 已有 2 轮迭代
  step 时,预算 2 的任何触发面都不再追加。
- 预算耗尽后再次 fail:不追加,行为回到现状——所有 step 完成则 gate =
  `complete`,`merge-on-complete` 因最新 verdict 非 pass 被
  `verdict_blocked` 扣住,交回人类;wave payload 的 `review_iterations[]`
  记录 `skipped: rounds_exhausted`。显式 `plan rework` 同样受预算约束
  (见下):越过预算属于人工改配置后再跑,绝不静默放行。

## 引擎钩子(run-loop 单 wave)

在单 wave 引擎的**摄入阶段之后、派发之前**插入纯检查:

- 触发判定与显式命令**同源**:对该 plan 的最新 verdict 状态跑同一纯触发
  函数(不限于本 wave 刚摄入的 reply——run-loop 启动前 fail verdict 已
  入账的场景同样触发;幂等靠 `triggered_by_reply` 标记,不会重复追加)。
  满足即调用 writer 追加一轮,结果记入 wave payload 新增可选字段
  `review_iterations[]`(每项:round、rework/re-review step 编号、
  triggered_by_reply、created approvals 数,或 `skipped` + 原因)。
- 追加的 approvals 由**下一个 wave** 的既有 auto-approve(白名单 +
  `max_approvals` 预算)与 step 顺序守卫接手;wave 内部既有顺序零改动。
  `--follow` / run-loop-host 的 `waiting_for_reply` 循环因此天然继续:
  fail → 追加 → 下轮批准派发 rework → 回复摄入 → 派发 re-review →
  pass → complete → (merge gate 看最新 verdict = pass)自动 merge 放行。
- `run-loop --all` 的每计划循环应用同一钩子(共享实现,以该 plan 为界)。
- run-loop-host serve 因逐字节复用单 wave 引擎而免费获得闭环;
  `--max-review-rounds` 从 start 透传 serve。

## 显式命令

`agentdeck plan rework --plan-id <id> --confirm`:

- 无 `--confirm`、未知 plan、无满足触发条件的 fail verdict、预算耗尽——
  一律拒绝、零写、非 0 退出,stderr 说明原因。
- 成功:调用同一 writer,输出 `mode=plan_rework`:追加的两个 step(含
  provenance)、创建的 pending approvals、`next_command = agentdeck
  approval list`。
- 它**不**派发、不 auto-approve、不调用 provider、不读取 tmux、不创建
  message/job/inbox。审批与派发仍走既有人工或 autonomous 通道。

## 审计与投影

- 新事件 `plan_rework_appended`:plan_id、round、source
  (`run_loop` | `explicit`)、triggered_by_reply、追加的 step 编号、
  created approvals 数。
- ProjectView `plans.items[]` 新增只读 `review_rounds`(已追加轮数,从
  step 标记推导);plan steps 投影保留 `origin`/`round`/
  `triggered_by_reply`(普通 step null 占位)。project-view contract 字段
  表、validator、example 同步。
- run-loop / run-loop-follow / run-loop-all contract 增加可选
  `review_iterations[]` 字段定义与 validator 校验(缺省可省略,向后兼容);
  run-loop-host 无契约变化(serve 复用 wave payload,host.log 自然携带)。
- `agentdeck contract run-loop` 等发现入口、docs/contracts/*、README、
  CLAUDE.md、HISTORY、handoff 按项目纪律同步。
- 实现期补充(2026-07-30,遵守"GUI-consumable 输出必须有 contract"
  项目纪律):`plan rework` 响应获得自己的 fields/validator/example 与
  `agentdeck contract plan-rework` 发现入口,注册进 contract index;
  run-loop-host 契约仍零变化(`--max-review-rounds` 只透传 serve,不进
  start/status 响应或 host record 契约字段)。

## 安全边界(全部继承,零新授权面)

- 追加的 step 走普通审批;派发仍只给 running pane、绝不 force-spawn。
- 完成信号仍只认文件通道回复;绝不读 pane 推断。
- run-loop 仍绝不调用 Leader provider、绝不创建新 plan(追加 step 到既有
  plan 是本 spec 显式 sanction 的唯一 plan 变更,且只经单点 writer、只在
  fail verdict 触发条件下)。
- merge gate 语义不变:最新 verdict 非 pass 时 `merge-on-complete` 扣住;
  迭代成功后最新 verdict 为 pass,自动 merge 自然放行。人类
  `worktree merge-plan --confirm` 永不受 gate。
- 模板拼装只使用已入账的 reply/verdict/plan 数据,不读产物文件内容。

## 非目标

- round_reviewer 独立角色与 provider binding、多 reviewer 聚合(仍是
  STOP fork)。
- Leader provider 精修回炉任务(二期显式命令,另行拍板)。
- 跨 plan 迭代、review fail 自动开新 plan。
- rework/re-review 之外的第三种追加形态(如自动加测试 step)。
- `plan rework` 的只读 preview 卡与 leader-chat 自然语言意图(后续切片,
  按 leader-chat 契约纪律另做)。

## 测试要点

- 纯模块:触发条件矩阵(fail/unknown/无 verdict/无效 verdict/旧 verdict/
  幂等重触发/预算边界 0/1/2)、模板拼装(fail 条目子集、reviewer 回复
  截断、agent 归属推导)、step 编号连续性。
- writer:steps/approvals/事件原子落账;拒绝路径零写。
- 引擎钩子:fake wave 场景 fail→追加→下轮 auto-approve+派发→pass→
  complete→merge 放行;`--max-review-rounds 0` 与无 verdict 场景逐字节
  基线不变;--all 每计划界隔离。
- 显式命令 gate 矩阵(confirm/未知 plan/无 fail/预算耗尽)零写非 0。
- 契约:三个 run-loop 系 validator 对 `review_iterations[]` 的接受与拒绝;
  project-view plan item 新字段;contract 发现入口与 index。
- 全量回归 + `git diff -- src/agentdeck/daemon/` 零输出。
