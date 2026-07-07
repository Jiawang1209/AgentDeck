# Layered-Role Round Walkthrough

This walkthrough ties the north-star layered-role plan (Phases G1–G6) together into a single end-to-end round. Every step below is either an **explicit human command** (mutates state, human-gated) or a **read-only contract surface** (projects state for a GUI/TUI, never mutates). No step lets an agent bypass approval, spawn panes silently, or write state on its own.

The goal is to give GUI/TUI builders one place that shows how the read-only cards and explicit commands compose across a full round:

```
frontdesk intake → plan → approval → dispatch → worker lifecycle → review gate → release
```

Each phase links to its machine-readable contract (discoverable via `agentdeck contract list`).

## Roles

Two kinds of role appear across the round:

- **Logical Leader coordination roles** — `frontdesk`, `planner`, `orchestrator`. They are not tmux panes: `runtime_kind=logical_role`, `pane_backed=false`, `pane_id=null`, `dispatch_ready=false`. `frontdesk` is local-rule/deterministic; `planner` and `orchestrator` inherit the configured Leader provider/model and stay approval-gated.
- **Worker roles** — the configured agents (e.g. `planner`/`coder`/`reviewer`, or explicit `code_reviewer` / `round_reviewer`). They run in visible tmux panes and only ever act through explicit dispatch.

The unified read-only view of both is `role_topology_card` (see the last phase).

## 1. Frontdesk intake (Phase G1)

Read-only. The human describes a goal; the frontdesk route organizes it and hands off to an explicit plan command — it never calls a provider or creates a plan.

```bash
agentdeck leader chat --message "frontdesk <goal>"
```

- Returns `mode=frontdesk`, embeds `frontdesk_card`, recommends `agentdeck leader plan --task <goal>`.
- Contract: `agentdeck contract leader-chat` (`docs/contracts/leader-chat-schema.md`).

## 2. Coordination topology (Phase G2)

Read-only. The logical `frontdesk → planner → orchestrator` split is visible without touching tmux.

```bash
agentdeck leader status         # top-level coordination_roles
agentdeck workbench             # leader_card.coordination_roles
```

- Contract: `agentdeck contract leader-status`, `agentdeck contract workbench`.

## 3. Plan (Leader planning)

Explicit. The Leader provider produces an approval-gated plan; it does not dispatch.

```bash
agentdeck leader plan --task "<goal>"
# or a fully local dry-run:
agentdeck leader plan --task "<goal>" --provider fake --model fake-plan
```

- Every step is `requires_approval=true`; the plan records provider provenance (`provider_backend` / `provider_transport` / `leader_backend`).
- Inspect: `agentdeck plan list`, `agentdeck plan show --plan-id <id>`, `agentdeck plan status --plan-id <id>`.
- Contract: `agentdeck contract project-view` (`plans.items[]`).

## 4. Approval gate (human)

Explicit. A human turns plan steps into approvals and decides each one. Only approved approvals can be dispatched.

```bash
agentdeck approval create-from-plan --plan-id <id>
agentdeck approval list
agentdeck approval approve --approval-id <id>     # or reject
```

- While an approval is pending, `role_topology_card` marks `orchestrator` as `waiting_for_approval` (blocker `waiting for human approval`).
- Contract: `agentdeck contract approvals`.

## 5. Dispatch + worker lifecycle (Phase G4)

Explicit dispatch, read-only lifecycle. Dispatch injects the target worker's loaded-skill snapshot into its task prompt and creates the message/attempt/job/inbox ledger entries.

```bash
agentdeck approval dispatch --approval-id <id>     # approved only
# or batch:
agentdeck approval dispatch-ready --confirm
```

Then watch progress read-only:

```bash
agentdeck workbench                                 # worker_lifecycle_card
agentdeck inbox --agent <id>
agentdeck trace --id <message|job|reply|artifact|inbox id>
```

- `worker_lifecycle_card` derives each worker's `lifecycle_stage` (`idle` → `task_dispatched` → `completed`/`reply_recorded` → `inbox_pending`) and inspect-only trace/inbox/terminal/capture controls.
- Capture a structured reply back into the ledger with `agentdeck capture-reply --agent <id> --message-id <id>` (manual `agentdeck reply ...` is the fallback).
- Contracts: `agentdeck contract workbench`, `agentdeck contract inbox`, `agentdeck contract trace`.

## 6. Review gate (Phase G5)

Read-only. Once artifacts and reviewer replies exist, the review gate reports whether the round can be released.

```bash
agentdeck workbench                                 # review_gate_card
agentdeck leader chat --message "查看验收门"          # same card via chat
```

- `code_review` reuses the configured `reviewer` / `code_reviewer`; `round_review` requires an explicit `round_reviewer`. The gate stays `blocked` (with a specific `reason`) until an artifact, a code-review reply, and a round-review reply all exist.
- In `role_topology_card`, the reviewer worker roles overlay the gate: `ready`→`reviewed`, `waiting_for_review`→`reviewing`, otherwise `blocked` with the stage blocker (e.g. a round reviewer shows `blocked` / `code review is not ready` while it waits on the code reviewer).
- Configure reviewer roles explicitly with `agentdeck agent assign-role --agent <id> --role code_reviewer|round_reviewer --role-prompt <prompt>` (the disabled `assign_code_reviewer` / `assign_round_reviewer` templates on the card point at exactly this command).

## 7. Release (Phase G5)

Explicit, human-gated, idempotent. When the gate is ready, a human records the round release. There is no way to release without `--confirm`, and re-releasing the same reply pair is refused.

```bash
agentdeck workbench                                 # release_preview_card (blocked | ready | released)
agentdeck release --confirm                          # the only write path
```

- A blocked gate withdraws all commands; a ready gate exposes `agentdeck release --confirm` as `release_command`; an already-released reply pair reports `status=released` / `already_released=true`.
- On success a `releases[]` record and a `round_released` audit event are written; the response validates via `validate_release_contract()`.
- The next round still starts from an explicit `agentdeck leader plan --task <goal>`.
- Contracts: `agentdeck contract release`, ProjectView `releases.items[]`.

## 8. Role topology — the unifying view (Phase G6)

Read-only. `role_topology_card` is the single "at a glance" surface that composes everything above: logical coordination roles plus worker roles, each with `provider`, `lifecycle`, a derived `status`, a `blocker`, and an inspect-only next-step control, plus a `by_status` histogram and `blocked_count`.

```bash
agentdeck workbench                                 # role_topology_card
agentdeck leader chat --message "查看角色拓扑"        # same card via chat; summary reports "N roles (M blocked)"
agentdeck controls --scope role_topology            # the topology's inspect controls in the command palette
```

- Logical statuses: `frontdesk=ready`; `planner=planning|idle`; `orchestrator=waiting_for_approval|coordinating|released|idle`.
- Worker statuses reuse `lifecycle_stage`, with the review-gate overlay applied to reviewer roles.
- Contract: `agentdeck contract workbench` (`role_topology_card_fields` / `role_topology_item_fields`).

## 9. Recovery and programmatic stepping (Phase G3)

At any point, the read-only recovery entry points tell a GUI or a scheduler the single next explicit command:

```bash
agentdeck continue                                   # recovery-driven next step
agentdeck loop once                                  # same next step for a programmatic loop; will_execute=false
```

- Neither executes anything; both stop at an explicit human command.
- Contracts: `agentdeck contract continue`, `agentdeck contract loop`.

## Invariants

- Read-only cards never spawn, dispatch, capture, ack, release, call a provider, or write state.
- Every mutation is an explicit human command: `leader plan`, `approval approve/dispatch`, `dispatch`, `capture-reply`, `ack`, `release --confirm`, `agent assign-role`, `agent spawn/spawn-ready`, `policy set-mode`.
- All GUI buttons come from contract `controls[]` and preserve `safety` / `enabled` / `blocker`.
