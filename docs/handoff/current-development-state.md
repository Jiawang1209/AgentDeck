# AgentDeck Current Development State

Updated: 2026-07-07

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

## Next Best Step

Continue Phase G6 with the next follow-up:

- Extend the blocker/status overlay to logical roles: e.g. mark `orchestrator` as `waiting_for_approval` (with a blocker) when there are pending approvals, and surface release state (`released`) onto the topology when the latest round was released.
- Optionally add a Phase G6 acceptance-focused view or contract that asserts the full "frontdesk → planner → orchestrator → coder → code_reviewer → round_reviewer" ordering and per-role safety once round/code reviewer roles are explicitly configured.
- Preserve human approval and keep every topology surface read-only.

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
