# G5 量化验收设计(2026-07-28 冻结)

对应 `docs/roadmap/ultimate-goal-roadmap.md` Phase G5 与
`docs/roadmap/2026-07-24-north-star-gap-review.md` 核心差距 #4。
目标:把 review gate 从二元 PASS/FAIL 升级为可量化、可对照 planner
acceptance_criteria 的结构化验收,同时**不改变任何现有 gate 行为**。
上下文隔离(reviewer 独立 worktree)已由 G4 解决,不在本 spec 范围。

## 冻结决策

1. **模型负责语义,程序负责循环。** 验收判定是语义工作,由 review step
   的 worker(LLM)输出;AgentDeck 程序只解析、校验、存储、展示,绝不
   自行打分,也不用文本相似度猜测判定。
2. **传输通道复用既有结构化回复,零新通道。** reviewer 回复文本中可选
   一行 `verdict: <单行 JSON>` 携带 `review-verdict/v1` payload。有效
   verdict 落入 reply 记录的可选 `verdict` 字段;**无效 verdict 绝不
   阻断 reply 入账**——reply 照常记录、`verdict` 字段缺省,追加
   `review_verdict_invalid` 审计事件(回复通道宽容纪律优先,验收数据
   fail-closed 其次)。无 `verdict:` 行时行为与今天逐字节相同。
3. **`review-verdict/v1` schema(fail-closed validator)**:
   - `schema_version`:必须 `review-verdict/v1`;
   - `criteria[]`:非空列表,每项 `{criterion: 非空字符串, verdict:
     "pass"|"fail"|"unknown", evidence?: 非空字符串}`;
   - `overall`:`"pass"|"fail"|"needs_changes"`;
   - `score?`:可选 0–100 整数;
   - `notes?`:可选非空字符串;
   - 未知 key、坏类型、空列表一律 invalid。
4. **对齐 G2 acceptance_criteria 是展示层语义,不是硬校验。** verdict
   摘要按 criterion 文本与 plan 的 `planner_brief.acceptance_criteria`
   精确匹配:匹配项计入 covered;plan 有而 verdict 无 → `unverified[]`;
   verdict 有而 plan 无 → `extra[]`。不匹配不报错、不拒绝入账——单段
   plan(无 brief)也允许 verdict,只是 unverified/extra 语义退化。
5. **展示面(全部只读)**:reply 记录 / ProjectView `replies.items[]` /
   trace reply 暴露可选 `verdict`;`leader review` / `leader summary` /
   `run_progress_card` 暴露派生 `verdict_summary`
   (`criteria_total/passed/failed/unknown/overall/score/unverified/
   extra`,取该 plan 最新带 verdict 的 reply;无 verdict 时为 null)。
   契约同步:project-view / leader-review / leader-summary / run 相关
   contract 文档、validator、example、README、HISTORY、测试。
6. **Gate 行为零变化。** run-loop 的 `complete`、review 的
   `summarize`、approval 语义全部不变;verdict 只是并行的可见证据。
   "round_reviewer 阻止下一轮自动推进"(如 `overall=fail` 时 gate 变
   `needs_changes`)是行为变更,列为 STOP fork 待 human 拍板。
7. **review step 的 dispatch prompt 增量注入。** 判定 review step 的
   现有依据(同 plan 更早 step 已有任务分支时的 review 检出逻辑)不变;
   对 review step 的 worker prompt 追加:该 plan 的 acceptance_criteria
   列表 + `verdict:` 单行 JSON 输出说明。非 review step 与无
   criteria 的 plan prompt 逐字节不变。prompt 注入不是权限授权。
8. **不新增 agent 角色。** code_reviewer 就是现有 review step 的
   worker;独立 `round_reviewer` 角色/卡片、reviewer 专属 provider
   binding 是后续 fork,不在本 spec。

## STOP fork(待 human,不在本 spec 内开工)

- verdict 驱动 gate(`overall=fail` 阻止 merge-on-complete / 下一轮)。
- 独立 round_reviewer 角色、专属卡片与 provider binding。
- 多 reviewer 投票 / 分数聚合策略。

## 切片顺序(每片独立 TDD + commit)

- **V1(本文档)**:spec 冻结。
- **V2 schema + 解析器**:`review_verdict.py` 纯模块——
  `validate_review_verdict()` fail-closed、`parse_verdict_line()`
  (从结构化回复文本提取可选 `verdict:` 行,单行 JSON,重复行 invalid)、
  `align_verdict_with_criteria()` 纯对齐函数(covered/unverified/extra)。
- **V3 入账**:reply / capture-reply / 文件通道摄入共享路径解析 verdict,
  有效落 reply 记录可选 `verdict` 字段 + `review_verdict_recorded`
  事件,无效追加 `review_verdict_invalid` 事件且 reply 照常入账;
  ProjectView `replies.items[]` / trace reply 投影暴露(null 占位遵循
  投影约定),project-view contract 同步。
- **V4 摘要面**:`leader review` / `leader summary` /
  `run_progress_card` 派生 `verdict_summary`(对齐 G2
  acceptance_criteria),契约字段表 / validator / example / 文档同步。
- **V5 review prompt 注入**:review step dispatch prompt 追加
  acceptance_criteria + verdict 输出说明;messages 记录的
  provenance 不变语义确认;非 review step 回归锁定。

## 验收标准(对照 roadmap G5)

- reviewer 输出进 trace 和账本(reply.verdict),不停留在 pane 文本。
- review/summary/run 面能看到按 criterion 的量化结果与
  unverified/extra 缺口。
- 现有 gate、审批、merge 行为在无 verdict 与有 verdict 时均逐字节
  不变;阻断类语义留待 STOP fork 拍板。
