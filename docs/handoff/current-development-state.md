# AgentDeck Current Development State

Updated: 2026-07-09

## Active Goal

按照 AgentDeck 北极星目标持续开发本地多智能体终端工作台：保持 API-backed Leader LLM、角色化多 Agent、可见 tmux runtime、人类审批、可恢复状态、通信账本和未来 GUI 可消费的主线；每轮开发都更新 `HISTORY.md`、运行验证并提交。

## Canonical Handoff Inputs

When switching from Codex to Claude Code CLI or another local agent, read these files first:

1. `CLAUDE.md`
2. `AGENT.md`
3. Top of `HISTORY.md`
4. `docs/roadmap/ultimate-goal-roadmap.md`
5. This file

Then inspect current state with:

```bash
git status --short
git log --oneline -5
conda run -n agentdeck pytest -q
```

## Current Phase

North-star Phase G5: Reviewer Gate Visibility.

Phase G1 is already committed: AgentDeck has a read-only `frontdesk` natural-language route:

```bash
agentdeck leader chat --message "frontdesk <goal>"
```

Expected behavior:

- Returns `mode=frontdesk`.
- Embeds `frontdesk_card`.
- Recommends explicit `agentdeck leader plan --task <goal>`.
- Records only chat turn and audit event.
- Does not call a Leader provider.
- Does not create plan/action/approval/message/job/inbox.
- Does not inspect or write tmux.

Phase G2 is already committed: AgentDeck makes the layered Leader topology visible to CLI/GUI clients through `coordination_roles[]` on:

- `agentdeck status` as `leader.coordination_roles`.
- `agentdeck leader status` as top-level `coordination_roles`.
- `agentdeck workbench` as `leader_card.coordination_roles`.

The roles are `frontdesk`, `planner`, and `orchestrator`. They are logical Leader coordination roles, not worker panes: every role must keep `runtime_kind=logical_role`, `pane_backed=false`, `pane_id=null`, and `dispatch_ready=false`. `frontdesk` is local-rule/deterministic; `planner` and `orchestrator` inherit the configured Leader provider/model and remain approval-gated.

Phase G3 is already committed: AgentDeck has a read-only programmatic loop step:

```bash
agentdeck loop once
```

Expected behavior:

- Validates ProjectView.
- Embeds the same `continue_card` used by `agentdeck continue`.
- Recommends exactly one explicit `next_command`.
- Returns `stop_reason`, `will_execute=false`, `requires_explicit_user`, `safety`, and GUI-ready controls.
- Does not call a Leader provider.
- Does not read, write, or send input to tmux.
- Does not approve, reject, dispatch, capture replies, ack inbox, or write state.

Phase G4 is already committed: AgentDeck exposes worker lifecycle visibility through:

```bash
agentdeck workbench
```

New surface:

- Adds `worker_lifecycle_card`.
- Derives worker status from `agents[]`, visible runtime status, messages, jobs, replies, artifacts, and inbox summary.
- Exposes each worker's `lifecycle_stage`, active message/job/reply ids, artifact count, pending inbox count, and inspect-only controls for trace, inbox, terminal, and capture.
- Does not spawn workers.
- Does not dispatch approvals or messages.
- Does not capture pane output automatically.
- Does not ack inbox items, release work, or write state.

The first G5 slice is already committed:

```bash
agentdeck workbench
```

New surface:

- Adds `review_gate_card`.
- Derives code-review and round-review readiness from artifacts, replies, traceable reviewer messages, and configured reviewer roles.
- Treats `reviewer` / `code_reviewer` as the code-review stage.
- Requires explicit `round_reviewer` configuration for round-level acceptance.
- Exposes `status`, `reason`, `can_release=false/true`, artifact/review counts, per-stage blockers, and inspect-only trace/inbox controls.
- Exposes disabled `assign_code_reviewer` / `assign_round_reviewer` templates as explicit-user controls so GUI/TUI can render reviewer role configuration forms without mutating config.
- Does not release, merge, ack inbox items, dispatch follow-up work, or advance the loop.

The second G5 slice is already committed:

```bash
agentdeck leader chat --message "查看验收门"
```

Expected behavior:

- Returns `mode=review_gate`.
- Embeds the same read-only `review_gate_card` as `agentdeck workbench`.
- Attaches a `control_registry_card` filtered to `scope=review_gate` / `card=review_gate_card`.
- Selects the `agentdeck workbench` inspect control.
- Records only the chat turn and audit event.
- Does not call a Leader provider.
- Does not create plan/action/approval/message/job/inbox.
- Does not release, merge, ack inbox items, dispatch follow-up work, advance the loop, read tmux, or send tmux input.

The release-preview workbench slice is already committed:

```bash
agentdeck workbench
```

New surface:

- Adds `release_preview_card`.
- Derives release readiness only from `review_gate_card`.
- Keeps `can_release` and blocked `reason` aligned with the review gate.
- Exposes `release_preview` and `next_round_preview` controls as disabled `explicit_user` placeholders with `command=null`.
- Exposes only `inspect_review_gate` as an enabled inspect control.
- Does not release, merge, ack inbox items, dispatch follow-up work, advance the loop, call a provider, read tmux, write tmux, or mutate state.

The current G5 follow-up adds release-preview natural-language discovery:

```bash
agentdeck leader chat --message "查看发布预览"
```

Expected behavior:

- Returns `mode=release_preview`.
- Embeds the same read-only `release_preview_card` as `agentdeck workbench`.
- Attaches a `control_registry_card` filtered to `scope=release_preview` / `card=release_preview_card`.
- Selects the `inspect_review_gate` control pointing at `agentdeck workbench`.
- Keeps `release_preview` / `next_round_preview` controls as disabled `explicit_user` placeholders with `command=null`.
- Records only the chat turn and audit event.
- Does not call a Leader provider.
- Does not create plan/action/approval/message/job/inbox.
- Does not release, merge, ack inbox items, dispatch follow-up work, advance the loop, read tmux, or send tmux input.

## Cross-Agent Goal Continuity

Codex App `/goal` is session-local state. It does not automatically transfer into Claude Code CLI.

Claude can still continue the same work by treating this repository as the source of truth:

- `HISTORY.md` is the development timeline.
- `CLAUDE.md` and `AGENT.md` are the behavioral constraints.
- `docs/roadmap/ultimate-goal-roadmap.md` is the north star.
- This handoff file carries the current active goal and next slice.
- Git commits are the durable recovery points.

Suggested prompt for Claude Code CLI:

```text
Please continue AgentDeck development from this repository.
Read CLAUDE.md, AGENT.md, the top of HISTORY.md, docs/roadmap/ultimate-goal-roadmap.md, and docs/handoff/current-development-state.md first.
Use conda activate agentdeck or conda run -n agentdeck for commands.
Every development iteration must update HISTORY.md, run verification, and commit locally.
Continue the active north-star goal; do not redo completed work.
```

The explicit release command slice is already committed:

```bash
agentdeck release --confirm
```

Expected behavior:

- Refuses without `--confirm` and writes nothing.
- Validates ProjectView, then reuses the same `review_gate_card` facts.
- Refuses when the gate is blocked, appending `round_release_rejected` with the same gate `reason`.
- Refuses when the same code-review / round-review reply pair was already released (`round already released`).
- On success appends a release record to `releases[]` plus a `round_released` audit event, and returns a GUI-ready payload with `safety=explicit_user`, trace commands for both review replies, and a disabled `agentdeck leader plan --task <goal>` next-round template.
- Does not merge, ack inbox items, dispatch follow-up work, create plan/action/approval/message/job/inbox, call a provider, or read/write tmux.

The release-preview wiring slice is already committed:

```bash
agentdeck workbench
```

New behavior:

- When the review gate is ready, `release_preview_card.release_command` / `next_command` point at the explicit `agentdeck release --confirm` command and the `release_preview` control becomes an enabled `explicit_user` control with the same command.
- `next_round_command` exposes the disabled `agentdeck leader plan --task <goal>` template with blocker `requires goal text`.
- While the gate is blocked, all three command fields stay `null` and the explicit controls stay disabled with the gate reason.
- The workbench validator rejects an enabled release control without `can_release=true` or with a command that drifts from `release_command`.
- Rendering the card still never releases; only a human running `agentdeck release --confirm` records the round release.

The release history slice is already committed:

```bash
agentdeck status
agentdeck workbench
```

New behavior:

- ProjectView exposes a top-level `releases` summary (`count`, `items[]`); each item carries the release id, round number, review-gate snapshot, both reviewer/reply ids, and a `trace_command` pointing at the round-review reply lineage.
- `release_preview_card` gains `already_released`, `release_count`, and `latest_release_id` derived from the same summary.
- When the review gate is ready but the current code-review / round-review reply pair was already released, the card reports `status=released` with reason `round already released`, withdraws `release_command` / `next_command`, and keeps only the disabled next-round plan template.
- Validators reject a released card that still exposes executable release commands and require a ready review gate behind any released card.

The release contract discovery slice is already committed (Phase G5 complete):

- Read-only `agentdeck contract release` / `--example` discovery, and `agentdeck release --confirm` now self-validates via `validate_release_contract()`.

## Current Phase

North-star Phase G6: Role Topology GUI.

The first G6 slice is already committed:

```bash
agentdeck workbench
```

- Adds `role_topology_card`, a read-only unified role topology (logical roles + worker roles, each with kind/provider/lifecycle/status/blocker/next_command and an inspect-only control).

The second G6 slice is already committed:

```bash
agentdeck workbench
```

New behavior:

- `role_topology_card` now overlays the `review_gate_card` stage status onto the matching reviewer worker role: a `ready` stage → `status=reviewed`, a `waiting_for_review` stage → `status=reviewing` (no blocker), and any other stage (`waiting_for_artifacts` / `blocked`) → `status=blocked` with the stage's blocker.
- Non-reviewer worker roles keep their base `lifecycle_stage` status with a `null` blocker.
- Still read-only: the overlay never advances the gate, spawns, dispatches, captures, acks, releases, or writes state.

The first G6 surface details:

- Adds `role_topology_card`, a read-only unified role topology.
- Projects the three logical Leader coordination roles (`frontdesk`, `planner`, `orchestrator`) from `leader.coordination_roles[]` plus the configured worker roles from the same `worker_lifecycle_card` items.
- Each role carries `kind` (`logical_role` | `worker`), `provider`, `lifecycle`, `runtime_kind`, `pane_backed`, `pane_id`, a derived `status`, `blocker`, `next_command`, and a single inspect-only control.
- Logical roles keep `runtime_kind=logical_role` / `pane_backed=false` / `pane_id=null` / `agent_id=null`; their inspect control points at their own read-only state source (`frontdesk` → `agentdeck leader chat-history`, `planner` → `agentdeck plan list`, `orchestrator` → `agentdeck leader actions`). Worker roles use `runtime_kind=worker_pane`, reuse the worker `lifecycle_stage` as `status`, and inspect via `agentdeck inbox --agent <id>`.
- All controls appear in `control_registry[]` / `agentdeck controls` under `scope=role_topology`.
- Does not spawn, dispatch, capture, ack, release, or write state; every control is inspect-only.

The third G6 slice is already committed:

```bash
agentdeck leader chat --message "查看角色拓扑"
```

Expected behavior:

- Returns read-only `mode=role_topology`.
- Embeds the same `role_topology_card` as `agentdeck workbench`.
- Attaches a `control_registry_card` filtered to `scope=role_topology` / `card=role_topology_card`, selecting the card-level `agentdeck workbench` inspect control.
- Records only the chat turn and its audit event; does not call a provider, create plan/action/approval/message/job/inbox, spawn, dispatch, capture, ack, release, or read/write tmux.

The fourth G6 slice is already committed:

```bash
agentdeck workbench
```

New behavior:

- The `role_topology_card` logical-role overlay now marks `orchestrator` as `waiting_for_approval` (blocker `waiting for human approval`) when any approval is pending, `coordinating` when a pending Leader action exists, `released` when at least one round has been released, and `idle` otherwise.
- Only `orchestrator` carries a blocker; `frontdesk`/`planner` keep `null`.
- Still read-only: the overlay only projects ProjectView facts and writes no state.

The fifth G6 slice is already committed:

```bash
agentdeck workbench
```

New surface:

- `role_topology_card` now carries `by_status` (per-status counts) and `blocked_count` (roles with a non-null blocker); the validator requires `blocked_count` to match the roles carrying a blocker.

The sixth G6 slice is already committed (test-only coverage):

- A project configuring agents with roles `code_reviewer` / `round_reviewer` surfaces them as distinct worker roles; with an artifact but no review replies the code reviewer shows `reviewing` and the round reviewer shows `blocked` (`code review is not ready`). Worker order follows configured agent order.

Phase G6 (Role Topology GUI) is now functionally complete: workbench `role_topology_card` (logical + worker roles, review-gate overlay, orchestrator approval/release overlay, status summary) plus the read-only natural-language `role_topology` chat discovery.

The seventh G6 slice is already committed:

- The natural-language `role_topology` chat `leader_explanation.summary` now reports role count and blocked count (e.g. "...role topology with 6 roles (1 blocked)...").

Phase G6 (Role Topology GUI) is complete across workbench + natural-language surfaces.

The layered-role walkthrough is already committed:

- `docs/walkthroughs/layered-role-round.md` walks a full round (frontdesk intake → coordination topology → plan → approval → dispatch + worker lifecycle → review gate → release → role topology → recovery/loop) against the read-only contract surfaces and explicit human commands, cross-linking each phase's contract. Linked from the README top.

Phases G1–G6 are complete and now documented end-to-end.

## Current Direction: TUI reference client

The user chose to build a read-only TUI/CLI reference client that consumes the workbench + control_registry contracts, proving the contracts are sufficient to drive a GUI (no new backend behavior).

The first slice is already committed:

```bash
agentdeck dashboard
```

- Adds `src/agentdeck/dashboard.py` with the pure function `render_workbench_dashboard(payload)` and the `agentdeck dashboard` command.
- Renders header / recovery / role topology / review gate / queue as human-readable text, deriving every value and echoed command from the workbench contract payload alone.
- Reuses the same `_workbench_snapshot_payload` + `validate_workbench_contract()` as `agentdeck workbench`; read-only, no state writes.

The second and third slices are already committed:

- Slice 2: a "Command palette" section from `control_registry[]` grouped by scope (total / enabled / blocked per scope) with a `agentdeck controls --scope <scope>` drill-down pointer.
- Slice 3: "Release" and "Ledger" sections derived from `release_preview_card` (shows `agentdeck release --confirm` when ready) and `ledger_card` counts.

The dashboard now renders: header, recovery, role topology, review gate, release, ledger, queue, command palette — all from the workbench contract payload alone.

The fourth slice is already committed:

- `docs/walkthroughs/tui-reference-client.md` documents the reference client (section→card mapping, real sample output, the sufficiency argument), linked from the README `dashboard` paragraph.

The TUI reference-client direction is complete: `agentdeck dashboard` renders header / recovery / role topology / worker activity / review gate / release / ledger / queue / command palette purely from the `agentdeck workbench` contract, with tests (`tests/test_dashboard.py`) and a doc. A worker-activity section (per-worker lifecycle stage + active task ids + inbox/artifact counts) was added as polish.

## Autonomous run in progress (directions 1 → 2 → 3)

The user approved doing all three directions in order, autonomously, overnight. Progress:

- Direction 1 (assisted run flow): first slice committed — a read-only "Run progress" section in `agentdeck dashboard`, derived from the existing `run_progress_card`, showing plan/step/approval status and the single explicit next command. It guides the human step-by-step but never executes (approval discipline preserved).
- Direction 2 (learning-layer GUI, Phase F): three slices committed — (a) a read-only "Learning layer" section in `agentdeck dashboard`; (b) `agentdeck learn review` defaults `--plan-id` to the latest plan; (c) a workbench `learning_review_card` (the earlier-deferred item, now done at the user's request): mirrors `leader_summary_card` — `null` until the latest plan review is `next_action=summarize`, then reuses the `agentdeck learn review` shape and enters `control_registry[]` under `scope=learning_review`. Read-only; the explicit `skills suggest` / `memory suggest` commands remain the only write path.
- Direction 3 (dashboard `--watch` polish): committed — `agentdeck dashboard --watch [--interval N] [--iterations N]` re-renders the text dashboard, mirroring `workbench --watch`, still read-only.

All three approved directions (1 → 2 → 3) have landed committed slices; the whole run kept the suite green (621 passing after the workbench `learning_review_card`).

## Current Direction: interactive curses TUI

`agentdeck tui` is a read-only interactive curses viewer over the workbench contract. First slices committed:

- `src/agentdeck/tui.py` with the pure, unit-tested `TuiModel` (navigation/selection/scroll/refresh) and `render_frame(model, height, width)` (screen layout); the curses I/O in `run_tui` is a thin shell.
- `agentdeck tui` command: builds+validates the workbench snapshot, launches curses; declines cleanly when not a TTY.
- Overview (scrollable dashboard) + palette (browsable `control_registry[]`); footer shows the selected control's safety/enabled/blocker and the exact `run: <command>`. Strictly read-only — it never executes.

A palette filter is also committed: `/` in the palette opens a filter prompt; `TuiModel.set_filter(text)` narrows controls by substring across scope/kind/label/command, re-clamping selection. Read-only.

All three optional TUI polish items are now committed: (1) the palette focuses the recovery `next_command` on open; (2) `?`/`h` opens a key-legend help overlay; (3) palette rows are colorized (selected reverse, disabled dim). All read-only; the styling decision is a pure, unit-tested `palette_row_style` / `palette_row_styles`.

## Next Best Step

**The whole autonomous-mode goal (all three sub-projects) is done.** All three preserve human approval and keep every read-only surface read-only.

- **Sub-project 1 of 3 — audit / HISTORY gate (done)**: `agentdeck history` renders the `events.jsonl` ledger into a read-only, newest-first, date-grouped Markdown timeline (`src/agentdeck/history.py`, `StateStore.all_events()`, `tests/test_history.py`), with `--write` materializing `.agentdeck/HISTORY.md` and `--limit N` to cap. Design + plan: `docs/superpowers/specs/2026-07-08-agentdeck-history-timeline-design.md` and `docs/superpowers/plans/2026-07-08-agentdeck-history-timeline.md`.
- **Sub-project 2 of 3 — bounded autonomous mode (done)**: `AutonomousPolicy` + `[autonomous]` config (`models.py`/`config.py`), the pure `select_auto_approvals` decision (`src/agentdeck/autonomy.py`), `agentdeck policy set-mode --mode autonomous --confirm --allow-agent <id> --max-approvals <N>` (validated allowlist/budget writer), and `agentdeck approval auto --confirm` (auto-approve allowlisted, budget-bounded pending approvals and dispatch them to already-running panes — no force-spawn, stops at dispatch, fully audited). `control_mode_card` autonomous is enabled with a disabled `set_mode` template. Design + plan: `docs/superpowers/specs/2026-07-08-autonomous-mode-design.md` and `docs/superpowers/plans/2026-07-08-autonomous-mode.md`.
- **Sub-project 3 of 3 — executing round loop (done)**: `agentdeck run-loop --plan-id <id> --confirm` is the write counterpart to the read-only `agentdeck loop`. It performs one sanctioned autonomous wave for a plan (auto-approve allowlisted pending within budget via `select_auto_approvals`, dispatch approved-and-ready to running panes via the existing dispatch internals), then reuses `leader review` + the pure `run_loop_gate` (`src/agentdeck/autonomy.py`) to diagnose the resulting human gate and stops there with an explicit `next_command` (`stopped_reason` ∈ error/blocked/needs_human_approval/waiting_for_reply/complete/idle). Requires `--confirm` + autonomous mode; never force-spawns; never captures replies or infers completion; fully audited (`run_loop_advanced` → `agentdeck history`). Contract: `agentdeck contract run-loop` + `docs/contracts/run-loop-schema.md`. Design + plan: `docs/superpowers/specs/2026-07-08-run-loop-engine-design.md` and `docs/superpowers/plans/2026-07-08-run-loop-engine.md`.

The interactive TUI is feature-complete (overview/palette/help, filter, refresh, focus, colors) and fully tested — `run_tui` is covered end-to-end via a fake stdscr (`tests/test_tui.py`). The TUI/dashboard reference-client line is done.

An end-to-end integration test now locks the whole autonomous chain across invocations: `tests/test_agent_cli.py::test_run_loop_drives_plan_to_completion_across_invocations` (policy set-mode autonomous → run-loop auto-approve+dispatch → `waiting_for_reply` gate → capture-reply → run-loop → `complete`, with two `run_loop_advanced` ledger events).

The autonomous commands are now **surfaced into the read-only command palette** (done): `control_mode_card.autonomous_actions[]` carries `kind=approval_auto` (`agentdeck approval auto --confirm`, `safety=delegated`, enabled only in autonomous mode, else blocker `autonomous mode is not enabled`) and a disabled `kind=run_loop` template (`agentdeck run-loop --plan-id <id> --confirm`, blocker `requires --plan-id`); both flow into `control_registry[]` / `agentdeck controls --scope autonomous` under `scope=autonomous`. Both the cli `_workbench_control_registry` and the mirror `contracts.workbench_control_registry` (used by `validate_workbench_contract`'s cross-check) append the group; the `workbench_example()` fixture was updated to match. Rendering is not authorization — the commands still require explicit human `--confirm`. Design + plan: `docs/superpowers/specs/2026-07-08-autonomous-controls-lighting-design.md` and `docs/superpowers/plans/2026-07-08-autonomous-controls-lighting.md`.

The final GUI-mainline follow-up is now **done**: `agentdeck leader chat --message "推进计划 pln_xxx"` (and `往前推`/`驱动计划`/`run-loop` variants) enters read-only `mode=run_loop_preview`, embeds `run_loop_preview_card`, hands back the explicit `agentdeck run-loop --plan-id <id> --confirm` as top-level `next_command`, and attaches a `scope=autonomous` `control_registry_card` whose selection points at the disabled `run_loop` template. It requires a plan id (no guessing), the next control is `safety=explicit_runtime` (disabled with `autonomous mode is not enabled` when autonomous is off), and the chat records only the chat turn + `leader_chat_turn` audit event — never a provider call, tmux read/write, auto-approve, dispatch, or approval/runtime/plan mutation. Detectors + card builder: `_chat_wants_run_loop_preview` / `_chat_run_loop_preview_plan_id` / `_run_loop_preview_card` (cli.py); contract: `run_loop_preview_card_fields` + the `run_loop_preview` mode check in `validate_leader_chat_contract` (contracts.py). Design + plan: `docs/superpowers/specs/2026-07-08-run-loop-chat-intent-design.md` and `docs/superpowers/plans/2026-07-08-run-loop-chat-intent.md`.

**Next Best Step:** The autonomous-mode goal and its full GUI-mainline surfacing (command palette `scope=autonomous` + natural-language `mode=run_loop_preview`) are complete. The human delegated the next direction ("你帮我决定"); the chosen lane is **"make the contracts visible — grow the human-facing dashboard/TUI cockpit"** (local, deterministic-testable via pure renderers + fake stdscr, directly monetizes the large read-only-contract investment).

Two slices of that lane are **done** (both in `render_workbench_dashboard`, shared by `agentdeck dashboard` and the TUI overview via `tui.py`):
1. **Control mode** section (`_render_control_mode`, `src/agentdeck/dashboard.py`) — the ask/approve/autonomous gradient + `approval auto` / `run-loop` command hints with enabled/blocked state. Test: `tests/test_dashboard.py::test_render_workbench_dashboard_shows_control_mode_and_autonomous_commands`.
2. **Runtime** section (`_render_runtime`) — the visible tmux binding: `<running>/<total> running` + each agent's `agent_id · role · status · pane:<pane_id>` from `runtime_card.agents[]` (distinct from logical `role_topology` and `worker_activity`). Test: `tests/test_dashboard.py::test_render_workbench_dashboard_shows_runtime_pane_binding`.
3. **Recent activity** section (`_render_recent_activity`) — the audit-ledger tail: `<event_count> events (agentdeck events --limit 20)` + up to 5 recent events (`created_at · event_type · event_id`) from `audit_card`, complementing the full `agentdeck history` timeline. Test: `tests/test_dashboard.py::test_render_workbench_dashboard_shows_recent_activity_ledger_tail`.

The dashboard/TUI overview now lays out: Header → Recovery → Run progress → Runtime → Role topology → Worker activity → Review gate → Release preview → Ledger → Queue → Control mode → Learning layer → Recent activity → Command palette.

The interactive TUI (`src/agentdeck/tui.py`) also gained two navigable read-only modes alongside overview/palette/help, each with select + status-aware command footer (commands straight from contract fields; the TUI never executes):
- **`approvals`** (`[a]`) over `approval_card.approvals[]` — footer command is pending→approve, approved→dispatch, else preview. Tests: `test_tui_model_approvals_view_navigates_and_shows_status_aware_command` / `test_tui_render_frame_approvals_lists_items`.
- **`runtime`** (`[g]`) over `runtime_card.agents[]` — rows show status·agent·role·pane; footer command is running→capture, else spawn. Tests: `test_tui_model_runtime_view_navigates_and_shows_status_aware_command` / `test_tui_render_frame_runtime_lists_agents`.

The TUI is now a view→run bridge: on quit it returns/prints the currently-focused command (`TuiModel.focused_command()` — palette control / status-aware approval / status-aware agent command; `run_tui` returns it; `tui_command` prints it after curses teardown). Still read-only — it prints, never executes. Tests: `tests/test_tui.py::test_tui_model_focused_command_reflects_active_view` / `::test_run_tui_returns_focused_command_on_quit`.

The "make the contracts visible" lane is now substantial (dashboard: Control mode + Runtime + Recent activity sections; TUI: approvals + runtime interactive views + print-selected-command-on-quit).

Already done, do NOT redo: `agentdeck dashboard --watch [--interval N] [--iterations N]` exists (`dashboard_command`, cli.py); `learning_review_card` is already a read-only workbench card (`_workbench_learning_review_card`, cli.py:1480).

## Current Direction: multi-plan lane ("多个计划同屏可见、分别推进")

The human picked the multi-plan-parallel lane: see all active plans at once and drive any of them separately. The state layer is already per-plan (`list_plans`, `plan_by_id`, `plan_status`, `leader_review`); the gap was purely visibility — nearly every read-only surface defaults to the single latest plan (`plans[-1]`).

**Slice 1 of the multi-plan lane is done:** read-only `agentdeck plan board` — a multi-plan overview that lists every plan with its derived `gate` and explicit per-plan `next_command`, plus `plan_count` / `active_count`. It reuses only the read-only `store.leader_review(plan_id)` + the pure `run_loop_gate(review, False, plan_id)` (`src/agentdeck/autonomy.py`); it calls no provider, reads no tmux, writes no state, appends no event. Contract: `agentdeck contract plans` + `docs/contracts/plans-schema.md` (`plan_board_*` helpers + `validate_plan_board_contract` in `contracts.py`, registered in `CONTRACT_INDEX_SPECS`). Design + plan: `docs/superpowers/specs/2026-07-09-plan-board-design.md` and `docs/superpowers/plans/2026-07-09-plan-board.md`.

**Slice 2 of the multi-plan lane is done:** the board is now embedded in the one-screen `agentdeck workbench` snapshot as `plan_board_card` (always present, never `null`). A shared helper `_plan_board_payload(store)` (`src/agentdeck/cli.py`) builds the same payload for both `agentdeck plan board` and `_workbench_snapshot_payload`; `WORKBENCH_SNAPSHOT_FIELDS` carries `"plan_board_card"`, `validate_workbench_contract` runs `validate_plan_board_contract` on the embedded card (prefix `plan_board_card: `), `workbench_example()` embeds `plan_board_example()`, and the workbench contract discovery payload exposes `plan_board_card_fields`. Doc: `docs/contracts/workbench-schema.md`. Read-only.

**Slice 3 of the multi-plan lane is done:** a read-only **Plans** section in `render_workbench_dashboard` (`_render_plans`, `src/agentdeck/dashboard.py`), derived from the `plan_board_card` — `<active>/<total> active` + one row per plan (`plan_id · active/done · gate · task`) with an indented `→ <next_command>`; shared by `agentdeck dashboard` and the TUI overview. Test: `tests/test_dashboard.py::test_render_workbench_dashboard_shows_plans_board` (a position-brittle TUI viewport assertion was repointed from "Role topology" to "Run progress"). Read-only.

**Next Best Step:** continue the multi-plan lane, in order — the remaining read-only visibility slices first, then the scheduler:

1. A TUI plans view in `src/agentdeck/tui.py`, mirroring the approvals/runtime views (rows = plan · gate · status; footer = per-plan `next_command`). Note: `[p]` is taken by the palette — pick a free key (e.g. `[b]` for board or `[n]` for plans).
2. Make `recovery` multi-plan aware (recommend across plans, not just the latest).
3. A natural-language `leader chat --message "查看所有计划" / "计划看板"` intent (read-only `mode=plan_board`, embed the same card + filtered `control_registry_card`).
4. **Then** the parallel scheduler (the bigger slice): auto-advance across plans + agent-contention logic — this is the first write-capable multi-plan slice and must stay approval-gated. ⚠️ This is a genuine product fork (core semantics) — the overnight loop must STOP here and leave a "⏸ 需要你决策" note rather than choose unilaterally.

(Not yet wired: a `control_registry[]` `scope=plan_board` entry — deferred until a plan-board control surface is actually needed, e.g. the dashboard/TUI plans view or the NL intent.)

Whatever is chosen next must preserve human approval and keep every read-only surface read-only.

## Required Verification Before Handoff

At minimum, run:

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py::test_leader_chat_frontdesk_routes_request_without_planning_or_provider_calls -q
conda run -n agentdeck pytest tests/test_agent_cli.py::test_status_surfaces_logical_coordination_roles_for_planner_orchestrator_split tests/test_agent_cli.py::test_leader_status_surfaces_provider_and_queue_snapshot_without_mutating_state tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_leader_status_contract_payload_is_reusable_without_cli tests/test_contracts.py::test_leader_status_contract_response_includes_example_without_drift -q
conda run -n agentdeck pytest tests/test_agent_cli.py::test_loop_once_recommends_next_explicit_command_without_mutating_state tests/test_agent_cli.py::test_contract_loop_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_loop_example_exports_gui_ready_card tests/test_contracts.py::test_loop_contract_payload_is_reusable_without_cli tests/test_contracts.py::test_loop_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_loop_once_contract_rejects_auto_execution_claim -q
conda run -n agentdeck pytest tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_requires_worker_lifecycle_item_fields tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state -q
conda run -n agentdeck pytest tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_requires_review_gate_stage_fields tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state -q
conda run -n agentdeck pytest tests/test_agent_cli.py::test_leader_chat_review_gate_is_read_only_and_surfaces_control_palette tests/test_leader_cli.py::test_leader_chat_help_returns_capability_card_without_planning tests/test_agent_cli.py::test_contract_leader_chat_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_leader_chat_example_exports_gui_ready_response -q
conda run -n agentdeck pytest tests/test_agent_cli.py::test_leader_chat_release_preview_is_read_only_and_surfaces_control_palette tests/test_leader_cli.py::test_leader_chat_help_returns_capability_card_without_planning tests/test_agent_cli.py::test_contract_leader_chat_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_leader_chat_example_exports_gui_ready_response -q
conda run -n agentdeck pytest -q
git diff --check
```
