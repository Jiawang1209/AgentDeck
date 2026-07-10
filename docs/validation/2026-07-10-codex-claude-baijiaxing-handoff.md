# Codex / Claude 百家姓顺序接龙验收

Date: 2026-07-10

## Result

真实 CLI 验收通过。一个已冻结的八步 Leader plan 由 Codex 与 Claude Worker 一替一句执行，后一步仅在前一步结构化回复被 token-correlated parser 验证后开始。

- 临时项目：`/tmp/agentdeck-baijiaxing-HyqrcE6G`
- Leader：`codex-cli`，显式模型 `gpt-5.5`
- Codex Worker：Codex CLI `0.131.0`，显式模型 `gpt-5.5`
- Claude Worker：Claude Code `2.1.206`，界面显示 `Opus 4.8 (1M context)`
- loaded skill：built-in `sequential-handoff`，load id `skl_042ab778a42c`
- plan id：`pln_eda9ac689677`
- plan hash：`sha256:fab1189d58d63fad1810fc7495f89e3485674f1a1c2c8202688d830817729cc4`
- completed workflow run：`wfr_d1bd55232a66`
- final state：`status=completed`、`current_step=9`、`stop_reason=null`

## Transcript from persisted compact handoffs

| Step | Worker | Role | Verified summary |
|---:|---|---|---|
| 1 | planner / Codex | recitation-codex | 赵钱孙李 |
| 2 | reviewer / Claude | recitation-claude | 周吴郑王 |
| 3 | planner / Codex | recitation-codex | 冯陈褚卫 |
| 4 | reviewer / Claude | recitation-claude | 蒋沈韩杨 |
| 5 | planner / Codex | recitation-codex | 朱秦尤许 |
| 6 | reviewer / Claude | recitation-claude | 何吕施张 |
| 7 | planner / Codex | recitation-codex | 孔曹严华 |
| 8 | reviewer / Claude | recitation-claude | 金魏陶姜 |

Every turn persisted `status=completed`, a unique `handoff_token`, message/job/reply ids, the exact `summary` and `verification`, and a trace command. Agent order was exactly `planner, reviewer, planner, reviewer, planner, reviewer, planner, reviewer`.

## Machine check

The final state projection returned:

```text
completed  9  planner,reviewer,planner,reviewer,planner,reviewer,planner,reviewer  赵钱孙李，周吴郑王，冯陈褚卫，蒋沈韩杨，朱秦尤许，何吕施张，孔曹严华，金魏陶姜  completed
```

## Runtime findings and fixes

The real run deliberately preserved and diagnosed failures instead of hiding them:

1. First-run trust screens are not runtime readiness. Claude required explicit trust confirmation; Workers were relaunched cleanly after setup.
2. `pane exists` is weaker than CLI readiness. The detached test window initially produced a two-line Codex pane. The acceptance window was switched to manual `120x60` and evenly split so capture retained complete structured replies.
3. Codex CLI `0.131.0` defaulted to `gpt-5.6-sol`, which that client could not use. The Worker was explicitly pinned to the already verified `gpt-5.5`; no global CLI upgrade was performed.
4. Immediate Enter after a multiline tmux paste could remain inside the TUI editor. `TmuxBackend.send_input` now pauses 150ms before submit.
5. Echoed prompt templates previously contained the live `handoff_token:` line and could be misread as replies. The live token is now stated separately from the placeholder response schema.
6. Codex and Claude render response bullets (`•`, `⏺`). The parser now normalizes known TUI presentation prefixes.
7. Streaming output exposed the token before all fields. Partial blocks now keep waiting; only complete invalid-status blocks fail.
8. A send exception previously left a workflow falsely `running`. It now persists the current turn as `failed` and the run as `stopped/pane_lost`.

The successful run exercised resume semantics without duplicate dispatch: completed turns were retained, the visible correlated reply for the active turn was recorded, and later turns proceeded in order.
