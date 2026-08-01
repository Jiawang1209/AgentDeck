# Run Start Contract

`agentdeck run --task <text>` starts an approval-gated multi-agent run. It asks the configured Leader provider for a plan, persists that plan, creates pending approval items for approval-required steps, and returns a GUI-ready `run_start` card.

`agentdeck run --plan-id <id>` returns a read-only `run_progress` card for an existing plan/run. It combines plan status, Leader review, the run-specific approval queue, and explicit next-step controls.

Both modes avoid implicit runtime mutation. They do not approve, dispatch, capture pane output, acknowledge inbox items, or send tmux input. Runtime work still requires explicit follow-up commands from the returned controls.

## Discovery

```bash
agentdeck contract run
agentdeck contract run --example
```

Reusable helpers live in `src/agentdeck/contracts.py`:

- `run_start_contract_payload()`
- `run_start_contract_response()`
- `run_start_example()`
- `run_progress_example()`
- `validate_run_start_contract()`

## Response Fields

`response_fields` describes the live `agentdeck run --task <text>` response:

- `schema_version`
- `ok`
- `mode`
- `task`
- `plan_id`
- `provider`
- `provider_backend`
- `provider_transport`
- `leader_backend`
- `model`
- `approval_count`
- `pending_approval_count`
- `plan`
- `approval_card`
- `next_command`
- `approve_next_command`
- `review_command`
- `continue_command`
- `workbench_command`
- `controls`
- `safety`
- `requires_explicit_user`

`mode` must be `run_start`. `safety` must be `approval_gated`, and `requires_explicit_user` must be `true`. `provider_backend` is a normalized provenance label: `local` for the fake dry-run provider, `api` for API-backed Leader providers, `cli` for local CLI-backed Leader providers, and `unknown` for unrecognized legacy records. `provider_transport` is the matching invocation channel label: `local`, `http`, `subprocess`, or `unknown`. `leader_backend` is the normalized Leader identity card for the saved plan: it must expose `agent_id=leader`, provider/model, backend/transport, `reasoning_backend`, `runtime_kind=logical_leader`, `pane_backed=false`, `pane_id=null`, `approval_required=true`, and `dispatch_ready=false`. It is provenance for GUI/audit surfaces, not runtime permission or a tmux pane binding. Provider output is normalized before the run card is built: accepted plans must keep every step at `requires_approval=true`, and AgentDeck forces top-level `approval_required=true` and `dispatch_ready=false` even if the backend returned different control flags.

`progress_response_fields` describes the live `agentdeck run --plan-id <id>` response:

- `schema_version`
- `ok`
- `mode`
- `plan_id`
- `task`
- `status`
- `provider`
- `provider_backend`
- `provider_transport`
- `leader_backend`
- `model`
- `counts`
- `steps`
- `acceptance_criteria`
- `verdict_summary`
- `review`
- `approval_card`
- `next_command`
- `plan_status_command`
- `review_command`
- `continue_command`
- `workbench_command`
- `controls`
- `safety`
- `requires_explicit_user`

`mode` must be `run_progress`. `leader_backend` must match the saved plan's normalized Leader identity card. `review` reuses the `agentdeck leader review --plan-id <id>` response shape, and `next_command` must match `review.next_command`. `acceptance_criteria` mirrors `review.acceptance_criteria`: `null` for single-stage plans, the G2 planner-brief acceptance-criteria list for split plans; it is read-only display data and never gates or authorizes dispatch. `verdict_summary` mirrors `review.verdict_summary` — the G5 quantified projection of the plan's latest valid `review-verdict/v1` reply aligned with those criteria (`criteria_total/passed/failed/unknown/overall/score/unverified/extra/group`, `null` when absent; `group` is the review-group provenance card — `size`/`complete`/`rule`/`members[]`, an implicit `size=1` group on the single-reviewer path, see `docs/contracts/leader-review-schema.md`) — and is equally display-only: it never changes the run gate or approval semantics.

## Controls

Each item in `controls[]` uses:

- `kind`
- `label`
- `command`
- `safety`
- `enabled`
- `blocker`

The first command should be `agentdeck approval list`. Approving or dispatching work remains explicit and separate from `agentdeck run`.

## Embedded Approval Card

`approval_card` reuses the `agentdeck approval list` queue shape. Clients should validate it against:

```bash
agentdeck contract approvals
```
