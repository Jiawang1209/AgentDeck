# Round 11 Runbook:G2 双 backend + G5 verdict live 首验(待 user 在场执行)

目的:一轮走开环同时拿到三份 live 证据——
①G2 planner/orchestrator 拆分在真实双 backend 上首验;
②G5 量化验收(verdict 行 → 入账 → 三面摘要)在真实 reviewer 上首验;
③SQLite 影子三连检证据 #3(5c cutover 前置)。

## 0. 前置(人在场,一次性)

```bash
conda activate agentdeck
cd ~/Desktop/agentdeck-live-scratch
export DEEPSEEK_API_KEY=$(security find-generic-password -s DEEPSEEK_API_KEY -w)
agentdeck doctor            # deepseek ready + claude_cli ready 都须 ok
agentdeck delegation list   # 确认 round 8 预授的窄前缀委托仍活跃
agentdeck storage shadow-status
```

config 变更(`.agentdeck/config.toml` 追加两个子段,回滚=删除即回单段):

```toml
[leader.planner]
provider = "deepseek"
model = "deepseek-v4-pro"

[leader.orchestrator]
provider = "claude-cli"
```

选型依据:roadmap 分层表——planner 用强推理 API 档,orchestrator 用
工具调用密集的 CLI 档;二者 provider/model 均不同,双 backend
provenance 才有区分度。orchestrator 也可换 `codex-cli`(同为已验
CLI leader plan 路径)。

## 1. 任务选择

从 round 9 缺陷池 F2–F4(见
`docs/validation/2026-07-26-copilot-line1-round9-gui-and-shadow.md`)
挑一项低级缺陷,或任选一个小型页面改进;**要求任务天然含
implement→review 两步**,以触发 review-step 的 criteria 注入与
verdict 输出。

## 2. 执行(与 round 8/10 相同的单命令走开环)

```bash
agentdeck agent spawn-ready --confirm
agentdeck leader chat --message "开始运行 <任务一句话>"
# 检查 run_start_card 后:
agentdeck approval approve-plan --plan-id <pln_xxx> --confirm
agentdeck run-loop --plan-id <pln_xxx> --confirm --follow --max-waves 40 \
  --interval 10 --release-boxes --merge-on-complete
```

## 3. 观察点(live 首验清单,逐项记入证据文档)

1. **G2-1 双段调用**:plan 生成耗时应为两段(DeepSeek brief +
   claude-cli steps);失败时 `agentdeck events` 里
   `leader_provider_failed` 必须带 `stage=planner|orchestrator`。
2. **G2-2 三份 provenance**:`agentdeck plan status --plan-id` /
   `agentdeck status` 的 plans.items——`planner_backend.model=
   deepseek-v4-pro`、`orchestrator_backend.provider=claude-cli`、
   `planner_brief` 含非空 `acceptance_criteria` + `content_hash`。
   顶层 provider/model/leader_backend 应等于 orchestrator 侧。
3. **G2-3 brief 质量(首次观察真实 LLM brief)**:acceptance_criteria
   是否具体可检;macro_steps 是否被 orchestrator 按序展开(对照
   steps[].task)。fail-closed 拒收即如实记录(schema 不合规是
   发现,不是绕过理由)。
4. **G5-1 review prompt 注入**:review step 的 message prompt
   (`agentdeck trace --id <msg>`)应含"验收标准(review 步骤)"段
   与标准原文;首 step prompt 不得含。
5. **G5-2 verdict 自然产出**:reviewer 回复是否带 `verdict:` 行;
   入账后 `replies.items[].verdict` 非 null,
   `review_verdict_recorded` 事件在账;若 reviewer 输出坏 JSON,
   确认 reply 照常入账 + `review_verdict_invalid`(宽容纪律 live 验)。
6. **G5-3 三面摘要**:`agentdeck leader review/summary --plan-id` 与
   `agentdeck run --plan-id` 的 `verdict_summary`——passed/failed/
   unverified/extra 与 reviewer 实际判定一致;GUI(`agentdeck ui
   serve`)的 leader_summary_card 同源可见。
7. **gate 零变化**:verdict 为 fail/needs_changes 时 run-loop 仍按
   既有 gate 走(不阻断)——这是本期冻结语义,verdict 驱动 gate 是
   待拍板 fork。
8. **委托/授权框**:哨兵报警与放行形态照旧记录(第四轮数据点)。

## 4. 收尾(三连检 + 证据)

```bash
agentdeck storage shadow-diff    # in_sync 必须 true(证据 #3)
agentdeck storage events-diff    # in_sync 必须 true
agentdeck worktree list && agentdeck agent release --agent coder --confirm 等
```

证据文档:`docs/validation/2026-07-28-copilot-line1-round11-g2g5-live.md`
(PASS/发现/授权框数据),HISTORY data 条目,memory 更新。回主仓库跑
`pytest tests/ -q` 回归。

## 5. 风险与回滚

- DeepSeek brief JSON 合规性未知:validator fail-closed 会拒——拒了
  就停在 planner 段并留审计,属预期发现;可换 orchestrator/planner
  组合重试(每次组合记一条)。
- claude-cli 做 orchestrator 走的是已验的 CLI leader plan 解析路径
  (fenced JSON 容错),风险低。
- 回滚:删 config 两个子段 → 单段路径逐字节回原;不动任何 state。
