# Leader Review Contract

`agentdeck leader review --plan-id <id>` is the read-only review surface for one saved Leader plan. It inspects local ProjectView-backed state and returns the next recommended review step without creating approvals, dispatching work, capturing pane output, writing replies, or creating leader actions.

Discovery:

```bash
agentdeck contract leader-review
agentdeck contract leader-review --example
```

## Discovery Payload

- `schema_version`: current ProjectView schema version.
- `review_command`: command template for live review output.
- `contract_path`: local Markdown schema path.
- `contract_exists`: whether this document exists in the checkout.
- `response_fields`: ordered top-level fields returned by `agentdeck leader review --plan-id <id>`.
- `leader_backend_fields`: ordered fields for the normalized logical Leader backend card.
- `control_fields`: ordered fields for each GUI-ready item in `controls[]`.
- `project_view_schema_version`: ProjectView schema version this contract is aligned with.
- `project_view_contract`: discovery command for ProjectView.

## Review Response

The live review response is a deterministic next-action card:

- `plan_id`: reviewed plan id.
- `next_action`: one of the local review decisions, such as `wait_for_approval`, `dispatch`, `wait_for_reply`, or `summarize`.
- `reason`: human-readable explanation for the recommendation.
- `leader_backend`: normalized logical Leader identity for the reviewed plan, including `agent_id=leader`, provider/model, backend/transport, `reasoning_backend`, `runtime_kind=logical_leader`, `pane_backed=false`, `pane_id=null`, `approval_required=true`, and `dispatch_ready=false`.
- `approval_id`: approval target when the next action is approval or dispatch related.
- `agent_id`: agent target when the next action is agent specific.
- `message_id`: message target when the next action is waiting for or capturing a reply.
- `replies`: reply records already associated with the reviewed plan.
- `next_command`: the command a human or GUI may offer as the primary next step.
- `controls`: GUI-ready control items that mirror or preview `next_command`.

Each control item uses:

- `kind`: stable control identifier, such as `preview`, `capture_reply`, or `next`.
- `label`: display label.
- `command`: CLI command to preview or explicitly run.
- `safety`: safety class, for example `inspect` or `explicit_runtime`.
- `enabled`: whether the control can be shown as runnable.
- `blocker`: reason the control is disabled, or `null`.

## Safety Rules

`agentdeck leader review --plan-id <id>` must pass `validate_leader_review_contract()` before printing JSON. Contract failures must return a non-zero exit code and must not print partial review output.

`leader_backend` is provenance for GUI/audit rendering. It is not a tmux pane binding, does not prove runtime readiness, and does not authorize approval, dispatch, capture, or reply writes. When review is embedded inside `run_progress`, `review.leader_backend` must match the surrounding run card's `leader_backend`.

For `next_action=wait_for_reply`, review may expose a read-only trace preview control plus a `capture_reply` control whose command is `agentdeck capture-reply --agent <agent_id> --message-id <message_id>`. The review command itself must not capture pane output, create replies, mutate inbox state, or persist a Leader action.

For `next_action=summarize`, review may expose `agentdeck leader summary --plan-id <plan_id>` as the next read-only command. Review itself must not synthesize or write the final summary; the summary command deterministically aggregates existing replies and artifacts without calling a provider or mutating state.

Partial-dispatch guard: when replied steps exist but the plan still has steps whose approvals are pending human decision (or not yet created), review must return `next_action=wait_for_approval` with reason `replied steps exist but pending approvals remain` instead of `summarize`. A plan is only ready to summarize once no step is awaiting approval.

GUI/TUI clients should discover this shape through `agentdeck contract leader-review --example` instead of hard-coding `next_command` or `controls[]` fields.
