# Round 13 live 验收:review 迭代闭环 + run-loop 背景宿主(2026-08-01)

Status: PASS(双目标全验证)
Scratch: `~/Desktop/agentdeck-live-scratch`(DeepSeek v4-pro planner +
claude-fable-5 orchestrator,codex coder + claude reviewer,autonomous
allowlist 三 agent / max_approvals 5 / max_review_rounds 默认 2)

## 目标

1. run-loop-host:确认后的自主段脱离客户端继续推进;预算/停止/重启语义。
2. review 迭代闭环:真实 reviewer fail verdict → 自动回炉 → 复审 pass →
   自动 merge,全程零人工。

## Plan 1 — 键盘方向键导航(pln_c4385a1698c8,迭代未触发的对照组)

- G2 双 backend live:DeepSeek brief 一次产出四条可检验收标准(含
  aria-live),claude-fable-5 拆两步;goal 中"暂不实现 aria-live(留待
  后续迭代)"被 orchestrator 如实写进两步任务与 review 指引。
- 宿主段 1(pid 53972,60 wave 预算):wave 1 派 coder;wave 5 前缀放框;
  **wave 34 发现 coder 卡在三段复合链框**
  `git status --short && node --check tests/… && node tests/…`——
  `node --check` 段无委托,归一化按"任一段不命中即整体拒"**正确拒按
  30+ wave**(fail-closed live 证据)。人工补 grant
  `node --check tests/` 后,wave 42/43/45 三框以 **match_kind=composite**
  自动放行,`matched_segments` 三段 provenance 完整——**委托匹配归一化
  检测面+放行面 live 首验通过**(round 12 遗留验证点)。
- wave 50:coder 文件通道回复摄入 → **同 wave 解锁派发 reviewer**;
  wave 60 预算耗尽如实停(`budget_exhausted`,干净停止三态
  `running False / stale False` 验证)→ 新预算显式重启接力。
- 段 2 wave 8:reviewer 回复摄入,verdict **overall=pass**(criterion ②
  以"已显式记录的推迟项"接受——orchestrator 措辞给了台阶,诚实结果),
  迭代钩子以 `verdict_pass` 正确静默拒绝,gate complete → 自动 merge
  (e112595 feat + f6556f8 docs)→ `gate_reached` 收官。
  **非触发路径 byte-stable live 确认**(board review_rounds=0)。

## Plan 2 — 质量收尾复核(pln_fcae506f1ba5,迭代触发主验证)

goal 设计:step 1 coder 仅跑全部回归取证(明确不新增功能代码),四条
验收标准原样(含 aria-live);无任何"推迟"话术——reviewer 面对真实
缺失只能如实判定。上一轮 reviewer 的 next_steps 原话即"后续迭代实现
验收标准② aria-live 播报",本 plan 就是那个后续迭代,零做作。

全链事件(宿主 pid 63282→69242,监视器逐事件记录):

1. wave 9:coder 取证回复摄入 → 同 wave 派发 reviewer。
2. wave 22:reviewer 回复摄入,verdict **overall=needs_changes**
   (rep_b465929b55f4)→ **迭代轮 1 自动追加(steps [3,4])**,审计
   `plan_rework_appended {round: 1, source: run_loop, steps: [3,4],
   triggered_by_reply: rep_b465929b55f4}`;wave 自身如实报
   `needs_human_approval`(gate 诚实性)。**needs_changes 触发值
   (spec 实现期修正)live 命中**。
3. wave 23:**walk-away 链路例外 live 验证通过**——宿主越过非等待
   gate 继续,auto-approve 2(回炉+复审),派发 1(回炉给 coder,
   顺序守卫持留复审步)。回炉任务为确定性模板(未通过标准②原文 +
   reviewer 意见 + commit 指令),零 LLM。
4. 回炉实现期跨预算:wave 100 `budget_exhausted` 二次如实停;coder 卡
   `rg -n … && git log … && node tests/…` 复合框(rg/git log 段无委托,
   再次 fail-closed),补两条只读前缀后重启(pid 69242),**段首补扫**
   立即放行,后续 wave 5/7/8 composite 连续放框。
5. wave 13:回炉回复摄入 → 同 wave 派发复审(复审步非 rework,验收
   标准+verdict 格式正常注入——rework 自评守卫的注入面 live 生效:
   回炉派发 prompt 无注入段)。
6. wave 28:复审回复摄入,verdict **overall=pass**(rep_b43694c6e2ee)
   → gate complete → **自动 merge 放行**(f64cd75 取证 + 5388267
   `fix: announce manual carousel navigation` aria-live 实现 + 5935c03
   审查报告,按 step 顺序合并)→ `gate_reached` 干净收官。
7. `agentdeck plan board`:**pln_fcae506f1ba5 review_rounds=1**,其余
   plan 0——看板迭代轮数字段 live 验证。

## 宿主验证清单(目标 1)

- detached 推进:两个 plan 全程无前台客户端,波次由宿主驱动 ✓
- 预算硬界:两次 `budget_exhausted` 如实停止,绝不越界 ✓
- 干净停止三态记录 + 显式重启接力 ×3 ✓
- 段首补扫(af2a5724)live 生效(重启后立即放行遗留框)✓
- host.log append-only 证据链完整可读(监视器全程消费)✓

## 收尾

- release 3/3(planner/coder/reviewer);release 守卫先被 pending inbox
  正确拦下,head-only 逐条 ack 后放行;reviewer 2 个 dirty worktree
  (变异验证残留)按守卫保留未删,留人工检查。
- 三连检:`storage shadow-diff` ok:true;`storage events-diff` ok:true;
  回归 = round 前主仓库全量 4823 passed 基线,round 期间零代码变更
  (仅 live 操作与文档);scratch node tests 由 worker/reviewer 双重
  取证零失败。

## 发现(全部非阻塞)

1. **预算尺度**:实现型步骤 10-20 分钟,15s 间隔下 60-100 wave 预算被
   空轮询烧尽(两次 budget_exhausted 均非故障)。候选:更长/自适应
   间隔(缺陷池已有"动态间隔"项)或预算指导值文档化。
2. **只读验证前缀持续增生**:本轮补 grant `git status`(已存在)、
   `node --check tests/`、`rg -n`、`git log`——"只读验证 starter
   pack"预置清单是候选拍板项;fail-closed 行为两次证明代价可控
   (卡框等人,绝不误放)。
3. **orchestrator 措辞影响 verdict 语义**:plan 1 中"明确推迟"话术让
   reviewer 合理化 overall=pass;需要严格按标准 gate 时,goal 措辞
   应避免给台阶(观察,非缺陷)。
4. reviewer dirty worktree(变异验证残留)复现,守卫行为正确;可考虑
   审查类任务的 worktree 收尾指引。

## 结论

Review 迭代闭环(fail → 自动回炉 → 复审 → merge)与 run-loop 背景宿主
在真实 provider/worker 环境全链 PASS,叠加验证:委托归一化 composite
匹配、needs_changes 触发、walk-away 链路例外、rework 注入守卫、看板
review_rounds、G2 双 backend、文件通道同 wave 解锁、SQLite 双镜像同步。
核心工作流"Leader 拆解 → coder 写 → reviewer 审 → 自动回炉 → coder 修 →
reviewer 终评 → 自动合并"自此为 live 已验证能力。
