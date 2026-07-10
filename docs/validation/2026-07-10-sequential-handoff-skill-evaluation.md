# Sequential Handoff Skill Evaluation

- **Date**: 2026-07-10
- **Method**: fresh-agent RED/GREEN/counterexample evaluation
- **Status**: RED baseline recorded; GREEN pending

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
