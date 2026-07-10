# Sequential Handoff Skill Evaluation

- **Date**: 2026-07-10
- **Method**: fresh-agent RED/GREEN/counterexample evaluation
- **Status**: RED, GREEN, and refactored counterexample complete

## Evaluation Matrix

1. `consecutive_fixed_steps`
2. `one_configured_agent_per_step`
3. `later_step_consumes_compact_handoff`
4. `deliverable_verification_failure_condition_per_step`
5. `rejects_parallel_dag_cycle_dynamic_expansion`
6. `recommends_workflow_preview_then_confirmed_run`

## Baseline Prompt

```text
You are the AgentDeck Leader planner. Design a plan for two configured workers, planner(role=planning) and reviewer(role=review), to alternate eight turns of a knowledge recitation. Worker N+1 may start only after Worker N finishes. Return a concise JSON-like plan summary and operator next steps. Do not assume any special skill exists.
```

## Baseline Raw Response

```text
{
  "objective": "两名 Worker 交替完成 8 轮知识复述",
  "execution": {
    "mode": "strictly_sequential",
    "start_next_when": "上一轮 Worker 明确返回 completed",
    "skill_assumption": "none"
  },
  "turns": [
    {"turn": 1, "worker": "planner",  "task": "复述知识第 1 轮，并给出本轮要点"},
    {"turn": 2, "worker": "reviewer", "task": "承接第 1 轮，复述第 2 轮并纠正或补充"},
    {"turn": 3, "worker": "planner",  "task": "承接第 2 轮，复述第 3 轮"},
    {"turn": 4, "worker": "reviewer", "task": "承接第 3 轮，复述第 4 轮并纠正或补充"},
    {"turn": 5, "worker": "planner",  "task": "承接第 4 轮，复述第 5 轮"},
    {"turn": 6, "worker": "reviewer", "task": "承接第 5 轮，复述第 6 轮并纠正或补充"},
    {"turn": 7, "worker": "planner",  "task": "承接第 6 轮，复述第 7 轮"},
    {"turn": 8, "worker": "reviewer", "task": "承接第 7 轮，完成最终复述与总结"}
  ],
  "completion": "8 轮均 completed，且每轮输出已进入消息账本",
  "failure_policy": "任一轮失败或未完成时停止，不派发下一轮；由操作员检查回复后重试或调整"
}

操作员下一步：

1. 创建包含上述 8 个顺序步骤的 plan，并走审批流程。
2. 先仅派发第 1 轮给 planner。
3. 每轮检查 inbox/reply，确认状态为 completed，再派发下一轮。
4. 将上一轮输出摘要附入下一轮任务，确保复述连续。
5. 第 8 轮完成后检查 plan status、消息链路和最终总结，再关闭计划。
```

## Baseline Score

| Criterion | Result | Evidence |
|---|---|---|
| `consecutive_fixed_steps` | pass | Eight explicitly numbered turns are present. |
| `one_configured_agent_per_step` | pass | Every turn names exactly `planner` or `reviewer`. |
| `later_step_consumes_compact_handoff` | fail | Tasks say “承接” and operator guidance says to attach a summary, but no task explicitly requires the audited compact handoff. |
| `deliverable_verification_failure_condition_per_step` | fail | The response has one global failure policy; individual tasks do not state deliverable, verification, and failure conditions. |
| `rejects_parallel_dag_cycle_dynamic_expansion` | fail | It chooses sequential execution but does not explicitly prohibit or identify incompatible DAG/cycle/dynamic semantics. |
| `recommends_workflow_preview_then_confirmed_run` | fail | It recommends manual per-turn dispatch and inbox inspection; neither `workflow preview` nor confirmed `workflow run` appears. |

Baseline result: **2/6 pass**. The missing four behaviors define the minimal GREEN target for the skill.

## With-Skill Raw Response

```text
{
  "workflow": "eight-turn-knowledge-recitation",
  "summary": "固定线性链 planner → reviewer 交替 8 轮；每轮仅在 AgentDeck 验证上一轮 correlated reply 后启动。",
  "steps": [
    {
      "step": 1,
      "agent": "planner",
      "role": "planning",
      "task": "开始知识复述，给出第 1 段核心内容和 compact handoff。",
      "expected_deliverable": "第 1 段复述 + 要点摘要 + 下一轮衔接提示。",
      "verification": "回复完整、主题明确，且包含可关联的 compact handoff。",
      "failure_condition": "回复缺失、偏题或无法生成有效 handoff。"
    },
    {
      "step": 2,
      "agent": "reviewer",
      "role": "review",
      "task": "显式消费第 1 步 compact handoff，复述第 2 段并延续内容。",
      "expected_deliverable": "第 2 段复述 + 与第 1 段的衔接说明 + 新 compact handoff。",
      "verification": "correlated reply 引用了上一步要点，内容连贯且 handoff 完整。",
      "failure_condition": "未消费上一步 handoff、出现明显断裂或回复不可关联。"
    },
    {
      "step": 3,
      "agent": "planner",
      "role": "planning",
      "task": "显式消费第 2 步 compact handoff，复述第 3 段。",
      "expected_deliverable": "第 3 段复述 + 承接摘要 + 新 compact handoff。",
      "verification": "准确承接第 2 步并提供可验证的新内容。",
      "failure_condition": "遗漏前序上下文、重复替代推进或 handoff 无效。"
    },
    {
      "step": 4,
      "agent": "reviewer",
      "role": "review",
      "task": "显式消费第 3 步 compact handoff，复述第 4 段。",
      "expected_deliverable": "第 4 段复述 + 连贯性检查 + 新 compact handoff。",
      "verification": "correlated reply 连贯、无明显矛盾，并包含下一轮输入。",
      "failure_condition": "未验证前序内容、产生矛盾或缺少 handoff。"
    },
    {
      "step": 5,
      "agent": "planner",
      "role": "planning",
      "task": "显式消费第 4 步 compact handoff，复述第 5 段。",
      "expected_deliverable": "第 5 段复述 + 当前知识链摘要 + 新 compact handoff。",
      "verification": "内容推进且保持与前四段一致。",
      "failure_condition": "内容停滞、偏离知识链或 handoff 不可用。"
    },
    {
      "step": 6,
      "agent": "reviewer",
      "role": "review",
      "task": "显式消费第 5 步 compact handoff，复述第 6 段。",
      "expected_deliverable": "第 6 段复述 + 一致性审阅 + 新 compact handoff。",
      "verification": "明确承接第 5 步，审阅结果和新增内容均可识别。",
      "failure_condition": "未消费 handoff、无法确认一致性或缺失新 handoff。"
    },
    {
      "step": 7,
      "agent": "planner",
      "role": "planning",
      "task": "显式消费第 6 步 compact handoff，复述第 7 段并准备收束。",
      "expected_deliverable": "第 7 段复述 + 收束要点 + 最终轮 compact handoff。",
      "verification": "内容承前启后，并为最终复述提供明确输入。",
      "failure_condition": "未形成收束路径、遗漏关键上下文或 handoff 无效。"
    },
    {
      "step": 8,
      "agent": "reviewer",
      "role": "review",
      "task": "显式消费第 7 步 compact handoff，完成第 8 段及整套复述总结。",
      "expected_deliverable": "第 8 段复述 + 八轮知识链总结 + 完成状态。",
      "verification": "correlated reply 覆盖最终内容、总结完整且标记完成。",
      "failure_condition": "未消费最终 handoff、总结不完整或无法确认完成。"
    }
  ],
  "execution_policy": {
    "ordering": "strict_sequential",
    "parallelism": 1,
    "advance_gate": "仅当上一步 correlated reply 经 AgentDeck 验证成功后，才启动下一步",
    "on_failure": "停止链条，保留失败步骤和上一步 handoff，等待人工处理"
  }
}

操作员下一步：

1. 先运行 agentdeck workflow preview，核对 8 步顺序、Agent、role、handoff 和验证门。
2. 人工确认预览无误后，运行 agentdeck workflow run --confirm。
3. 任一步验证失败时不要跳步或重试后续步骤；先检查该步 correlated reply，再由人工决定如何恢复。
```

## With-Skill Score

| Criterion | Result | Evidence |
|---|---|---|
| `consecutive_fixed_steps` | pass | Exactly eight numbered steps and `parallelism: 1`. |
| `one_configured_agent_per_step` | pass | `planner/planning` and `reviewer/review` alternate exactly. |
| `later_step_consumes_compact_handoff` | pass | Every step after Step 1 explicitly consumes the previous compact handoff. |
| `deliverable_verification_failure_condition_per_step` | pass | Every step has all three fields. |
| `rejects_parallel_dag_cycle_dynamic_expansion` | pass | The response freezes a strict chain with one active step. |
| `recommends_workflow_preview_then_confirmed_run` | pass | Operator steps explicitly use preview followed by `run --confirm`. |

GREEN result: **6/6 pass**.

## Initial Counterexample Raw Response

```text
This skill does not apply. The workload requires parallel fan-out of three independent searches, followed by repeated fan-in/merge cycles until convergence—exactly the parallel branches, DAG edges, convergence loops, repeats, and dynamic progression that sequential-handoff forbids.

The response then proposed using a parallel/DAG-capable workflow but still suggested an “appropriate AgentDeck workflow-preview command” and “such as agentdeck workflow run --confirm”.
```

Initial counterexample result: **partial fail**. Applicability was rejected correctly, but the operator advice still reused the current linear workflow commands for an unsupported DAG/cycle workload.

## Refactor

The skill gained a seventh compact guidance rule:

```text
For incompatible workloads do not recommend workflow commands and return to ordinary Leader planning for a dedicated design.
```

## Refactored Counterexample Raw Response

```text
该 skill 不适用。

原因：工作负载同时包含三路并行搜索、汇合，以及反复迭代直至收敛；这属于 fan-out/fan-in、循环和动态终止条件，违反 sequential-handoff 仅支持固定、无分支、无循环 A→B→C 链的边界。

Operator path：

1. 返回普通 Leader planning，描述三路独立搜索的职责、产物和验证条件。
2. 单独设计支持并行 fan-out、合并验证、未收敛则再次迭代的工作流，并明确收敛指标、最大轮数与失败/人工停止条件。
3. 由操作员审阅该专用设计及其安全边界后，再决定如何显式确认和执行。

此场景下不应推荐现有的 agentdeck workflow preview 或 agentdeck workflow run --confirm，也不应把并行收敛任务强行改写成顺序链。
```

Refactored counterexample result: **pass**. It rejects the skill, does not recommend the existing linear workflow commands, and stops at ordinary Leader planning plus a dedicated design.
