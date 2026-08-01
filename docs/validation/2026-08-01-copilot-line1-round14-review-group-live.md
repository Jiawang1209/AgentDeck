# Round 14 live 验收:review group + round_reviewer(2026-08-01)

Status: IN PROGRESS(核心验证点已确证,待组齐后的聚合与合并收尾)
Scratch: `~/Desktop/agentdeck-live-scratch`
Plan: `pln_d68e79abe9ef`(为 `.focus-slider` 焦点轮播补齐键盘方向键导航
+ aria-live 播报 + 回归脚本)
Host: pid 35602,预算 150 wave / 20s,`--release-boxes --merge-on-complete`
Config: `[review] reviewers = ["reviewer", "planner"]`(主 reviewer 角色
签名 = `review`);orchestrator 临时切 DeepSeek(见发现 1)

## 目标

验证 2026-08-01 落地的 review group + round_reviewer:确定性展开、串行
执行、any-fail-blocks 聚合、组完成才判定,以及终审 Critical 修复
(组未齐时不放开自动合并)。

## 已 live 确证

1. **确定性展开**:Leader(DeepSeek planner + DeepSeek orchestrator)
   产出的**单个** review step 被确定性展开为两步——
   `step2 reviewer(group 1, member 0)` + `step3 planner(group 1,
   member 1)`,任务文本相同、编号 `1..3` 连续、`origin=review_group`;
   `step1 coder`(implementation)**未被误伤**,证实识别谓词 =
   `reviewers[0]` 的 role(`review`)而非"任一 reviewer 的 role"。
   四条验收标准由 planner brief 正常产出并进入 plan。
2. **组内同批准、绝不并行**:`approval approve-plan --confirm` 一次批准
   3 条;plan status 显示 step2/step3 同为 `approved` 却被 step 顺序
   守卫持留,等 step1 完成。执行引擎零改动的承诺在真实运行中成立。
3. **严格串行派发**:wave 29 coder 回复摄入 → 同 wave **只**派 step2
   reviewer(此时 step3 仍 `approved` 未派);wave 40 reviewer 回复摄入
   → 同 wave 派 step3 planner。组内两位 reviewer 一前一后,各自独立
   wave,无并行。
4. **终审 Critical 修复 live 复现并确证**(最有价值的一帧):
   组未齐窗口(reviewer 已回、planner 未回)的 `leader review`:

   ```json
   "verdict_summary": {
     "overall": "pass", "score": 92,
     "criteria_total": 4, "passed": 4, "failed": 0, "unknown": 0,
     "group": {
       "complete": false, "size": 2, "rule": "any_fail_blocks",
       "members": [
         {"agent_id": "reviewer", "overall": "pass",
          "reply_id": "rep_852d470b8436", "step": 2},
         {"agent_id": "planner", "overall": null, "reply_id": null,
          "step": 3}
       ]
     }
   }
   ```

   同时 `_verdict_merge_blocker` =
   `review group incomplete (1/2 members reported); auto-merge withheld`。
   **这正是终审点名"真正要命"的 a2 场景**:唯一报到者给的是 `pass`,
   修复前 `overall == "pass"` 会提前返回并放开 merge gate;修复后
   `complete=false` 的判断排在其前,自动合并被扣住。未报到成员以
   `overall: null` 占位(为该修复放宽的契约字段)在真实数据上生效,
   且整份 summary 通过 live 契约守门。

5. **预算/停止/重启语义再确证**:宿主 wave 150 以 `budget_exhausted`
   如实停止,记录 `running=false / stale=false`(干净三态),新预算
   200 wave 显式重启接力(pid 49417),进度零丢失。
6. **委托对通用浏览器 CLI 保持 fail-closed(有价值的负面证据)**:
   planner 复核时请求
   `/Users/liuyue/.codex/skills/playwright/scripts/playwright_cli.sh open
   file:///…/index.html`,`agent boxes` 报 `delegated=false /
   match_kind=null`,系统**绝不代按**,框留给人眼。这正是设计所要:
   `playwright_cli.sh` 是通用浏览器入口,授其前缀等于连
   click/fill/evaluate 等页面变更能力一并授出,属项目明令不得 grant
   的一类(与 round 10 "任意内容命令留人工"结论一致)。
   人类拍板选择"单次手按放行",不入注册表。
7. **收尾一致性检查**:`storage shadow-diff` ok=true;
   `storage events-diff` ok=true(scratch 仍为 journal 权威,未切
   on_demand)。

## 待确认(那道人工框放行后自动完成)

8. 组齐后 any-fail-blocks 聚合结果与 `complete: true` 投影。
9. 聚合为 pass → 自动合并放行;聚合非 pass → **只**追加一轮迭代
   (组级幂等,`triggered_by_reply` = 组内最后一个成员的 reply),
   且追加的复审步本身也是一个组。
10. release 与全量回归收尾。

## 发现

1. **CLI provider 失败原因不可观测(拍板项)**:scratch 原配
   `[leader.orchestrator] = claude-cli / claude-fable-5`,plan 生成两次
   均失败。AgentDeck 行为正确——如实记 `leader_provider_failed`
   带 `stage=orchestrator`、不半写 plan——但错误只有 `nonzero`。
   实测本地 claude CLI:**exit=1**,原因文本
   "You're out of usage credits…" 在 **stdout**、stderr 为空。真实原因
   躺在 provider 输出里,而安全边界禁止留存。建议方案(照搬本项目已
   验证的授权框提取器模式:解析 → 分类 → 丢弃原文):只存闭合枚举
   `credits_exhausted` / `auth_required` / `model_unavailable` /
   `unknown`,外加进程退出码(进程事实,非 provider 输出)。
   **未在 live round 期间改动 provider 安全层**;本轮为继续验证
   review group,已把 orchestrator 临时切到 DeepSeek。
2. **探针教训**:`agentdeck plan status` **没有** `verdict_summary` 键
   (该字段属 `leader review` / `leader summary` / `run --plan-id`
   三面),用 `.get("verdict_summary")` 读它会得到 `None` 并被误读成
   "判定为空"。取证脚本必须选对面。
