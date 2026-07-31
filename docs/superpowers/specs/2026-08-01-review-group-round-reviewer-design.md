# Round reviewer 独立角色 + 多 reviewer 串行聚合设计

Status: frozen(user 拍板 2026-08-01:串行叠加 / config `[review]` 段 /
any-fail-blocks;组完成才触发与 record_plan 确定性展开经 user 过目确认)
Prior art: review 迭代闭环
(`docs/superpowers/specs/2026-07-30-review-iteration-loop-design.md`)、
G5 量化验收、G2 planner/orchestrator split

## 问题

1. 迭代回炉的复审步逐字节克隆原 review step 的 agent——同一个 reviewer
   复审自己上一轮放行/否决过的返工,缺乏独立性(round_reviewer 需求)。
2. 单 reviewer 单视角:重要 plan 需要多个 reviewer(可不同 provider)
   独立复核并聚合判定(多 reviewer 聚合需求)。

## 用户拍板(2026-08-01)

1. **执行形态 = 串行叠加 review step**:一个 review 环节 = 连续 N 个
   review step(各自独立 agent),按既有 step 顺序守卫依次执行;执行
   引擎(线性 plan、顺序守卫、worktree 链式检出、文件通道)零改动。
   并行派发同一 step 是显式非目标。
2. **配置形态 = config `[review]` 段**:两个可选键,均缺省关闭且行为
   逐字节不变——
   - `round_reviewer = "<agent_id>"`:迭代回炉的复审步 agent 改为该
     agent(缺省=克隆原 review step agent);
   - `reviewers = ["a", "b", …]`:review 环节确定性展开为 N 个串行
     step(缺省=单 reviewer)。
   fail-closed 解析:未知 agent_id、空串、非列表等一律拒绝加载配置;
   `reviewers` 单元素列表等价于替换 review agent(仍展开路径,组=1)。
3. **聚合策略 = any-fail-blocks**:任一 reviewer 非 pass 即整体非
   pass;聚合 overall 取最严(fail > needs_changes > pass)。多数决与
   加权是显式非目标。

## 确认的推导设计(user 过目)

### 展开点:`_generate_leader_plan` 出口确定性后处理(不靠 LLM)

**接线修正(2026-08-01,写 spec 后核对代码得出)**:`StateStore.record_plan`
不接收 `ProjectConfig`,而展开需要 reviewers 列表与各自的配置 role。
因此展开是 **纯函数**(建议 `review_group.expand_review_group(plan,
reviewers) -> plan`,reviewers 为 `[(agent_id, role), …]` 纯数据),
在 `cli._generate_leader_plan`(`leader plan` / `run --task` / 自然语言
plan 三条路径共享的唯一出口,cli.py:12151)的返回前应用;`record_plan`
**零改动**,收到的就是已展开的 plan dict。显式 `--provider/--model`
override 路径同样经过该 helper,故行为一致。

- Leader/provider 照常生成含单 review step 的 plan(provider prompt
  不变,不要求 LLM 理解 reviewer 组)。
- 若 `[review].reviewers` 非空,把每个 review step 确定性展开为 N 个
  连续 step。**识别谓词收紧(2026-08-01 写计划时修正)**:该 step 的
  `agent_id` 的配置 role **等于 `reviewers[0]` 的配置 role**——首位
  reviewer 是"主 reviewer",它的角色签名定义什么算 review 环节。
  早先措辞"与**某个** reviewer 的 role 相同"有真实漏洞:若
  `reviewers = ["reviewer", "planner"]`(跨角色组),planning step 会
  被误判为 review step 而被整组展开。实现时写测试钉住该谓词(含跨
  角色组不误伤 planning step 的用例)。
  - 编号顺延重排(全 plan 步骤号保持 `1..n` 连续);
  - 每步 agent 取 `reviewers[i]`,role 取该 agent 配置 role,任务文本
    与原 review step 逐字节相同;
  - 每步带 provenance:`origin: "review_group"`、
    `review_group: <组序号,从 1 起>`、`review_group_member: <i>`;
  - provider 原始 plan 已由既有 `leader_generation`/`planner_brief`
    provenance 保存,展开差异可审计。
- 迭代追加的复审:`round_reviewer` 配置存在时复审步 agent/role 换为
  round_reviewer(及其配置 role);`reviewers` 配置存在时按同规则追加
  整组 N 步(每步带 `origin: "review_iteration"` + 组 provenance;
  round_reviewer 与 reviewers 同时配置时 reviewers 优先——组语义强于
  单人替换,round_reviewer 仅在无组配置时生效)。
- rework 自评排除(`rework_step_numbers`)语义不变,只作用于 rework
  成员。

### 聚合与触发:组完成才判定

- **组** = 带同一 `review_group` 标记的连续 review steps(单 reviewer
  无标记时,单个 review step 自成隐式组——退化路径逐字节不变)。
- `plan_verdict_summary` 与迭代触发器共用新的组感知选取(单一来源
  helper,建议纯函数入 `review_iteration.py` 或独立
  `review_group.py`):取**最新一个完整组**——组内每个成员 step 都已
  有带有效 verdict 的回复——做 any-fail-blocks 聚合:
  - 全 pass → 聚合 overall = pass;
  - 否则取最严:任一 fail → fail;无 fail 但有 needs_changes →
    needs_changes;
  - 成员 verdict 的 criteria 逐条合并对齐 acceptance_criteria(任一
    成员 fail 的 criterion 记 fail)。
- **组未齐绝不触发迭代**(关键护栏):A fail 后 B 尚未复核时不追加
  回炉——否则 B 审旧代码再 fail 会再开一轮,预算双烧。组未齐时 gate
  照常 `waiting_for_reply`(顺序守卫本就会先派完组内成员)。
- 迭代幂等升级为**组级**:`triggered_by_reply` 记组内最后一个成员的
  reply_id(组完成的标志),同组绝不重复触发。
- 回炉模板合并组内所有非 pass 成员的 fail 标准与回复原文,逐 reviewer
  署名分段(`### reviewer <agent_id>` 小节),截断语义不变(总预算
  4000 字符,截断附各成员 trace 指引)。
- merge gate(`_verdict_merge_blocker`)使用同一聚合结果。
- `verdict_summary` 新增 `group` 字段:
  `{size, complete, members: [{agent_id, step, overall, reply_id}],
  rule: "any_fail_blocks"}`;单 reviewer 隐式组也填(size=1),让 GUI
  单双路径同构。

## 安全边界

- 展开与聚合是确定性纯逻辑 + 既有 locked writer 路径;不新增授权面:
  展开步照常生成 pending 审批走 allowlist/预算,顺序守卫不变。
- `[review]` 配置只影响 plan 组装与 verdict 选取,不是执行授权;
  round_reviewer/reviewers 必须是配置中的已知 agent。
- 迭代预算语义不变:一组聚合 fail = 一轮(`--max-review-rounds`
  照旧);组内成员数不消耗迭代预算(消耗 approval 预算,既有语义)。
- 缺省(无 `[review]` 段)所有路径逐字节不变。

## 非目标

- 并行派发同一 review step、多数决/加权/分数阈值聚合、跨组仲裁、
  reviewer 动态选择(Leader 决定 reviewer)、组内部分完成的提前触发。

## 测试要点

- config 解析矩阵(缺省/单键/双键/未知 agent/空列表/reviewers 优先于
  round_reviewer)。
- 展开纯逻辑:编号重排连续性、组 provenance、任务逐字节复制、多 review
  step plan(两组)、缺省零展开 byte-stable。
- 聚合纯逻辑:全 pass/单 fail/needs_changes 最严选取、组未齐 →
  incomplete(不触发、summary 标 complete=false 沿用最新完整组或
  null)、单 reviewer 隐式组退化、criteria 合并。
- 触发器:组未齐不触发;组齐 fail 触发一次(组级幂等);round_reviewer
  替换;reviewers 整组追加。
- 回炉模板多 reviewer 署名合并与截断。
- merge gate 聚合联动;ProjectView/plan status/契约投影同步;缺省全
  路径 byte-stable 基线(既有全量测试)。
