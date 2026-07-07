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

North-star Phase G4: Worker Lifecycle Visibility.

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

The current G4 slice adds:

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

## Next Best Step

After the current worker lifecycle slice is committed, continue with Phase G5 planning:

- Add explicit reviewer / code_reviewer / round_reviewer acceptance surfaces.
- Keep reviewer state derived from artifacts, replies, trace, and plan status rather than model conversation memory.
- Preserve human approval before release, merge, ack, or follow-up dispatch.

## Required Verification Before Handoff

At minimum, run:

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py::test_leader_chat_frontdesk_routes_request_without_planning_or_provider_calls -q
conda run -n agentdeck pytest tests/test_agent_cli.py::test_status_surfaces_logical_coordination_roles_for_planner_orchestrator_split tests/test_agent_cli.py::test_leader_status_surfaces_provider_and_queue_snapshot_without_mutating_state tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_leader_status_contract_payload_is_reusable_without_cli tests/test_contracts.py::test_leader_status_contract_response_includes_example_without_drift -q
conda run -n agentdeck pytest tests/test_agent_cli.py::test_loop_once_recommends_next_explicit_command_without_mutating_state tests/test_agent_cli.py::test_contract_loop_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_loop_example_exports_gui_ready_card tests/test_contracts.py::test_loop_contract_payload_is_reusable_without_cli tests/test_contracts.py::test_loop_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_loop_once_contract_rejects_auto_execution_claim -q
conda run -n agentdeck pytest tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_requires_worker_lifecycle_item_fields tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state -q
conda run -n agentdeck pytest -q
git diff --check
```
