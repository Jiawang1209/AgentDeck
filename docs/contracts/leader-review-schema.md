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
- `control_fields`: ordered fields for each GUI-ready item in `controls[]`.
- `project_view_schema_version`: ProjectView schema version this contract is aligned with.
- `project_view_contract`: discovery command for ProjectView.

## Review Response

The live review response is a deterministic next-action card:

- `plan_id`: reviewed plan id.
- `next_action`: one of the local review decisions, such as `wait_for_approval`, `dispatch`, `wait_for_reply`, or `summarize`.
- `reason`: human-readable explanation for the recommendation.
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

For `next_action=wait_for_reply`, review may expose a read-only trace preview control plus a `capture_reply` control whose command is `agentdeck capture-reply --agent <agent_id> --message-id <message_id>`. The review command itself must not capture pane output, create replies, mutate inbox state, or persist a Leader action.

For `next_action=summarize`, review may expose `agentdeck leader summary --plan-id <plan_id>` as the next read-only command. Review itself must not synthesize or write the final summary; the summary command deterministically aggregates existing replies and artifacts without calling a provider or mutating state.

GUI/TUI clients should discover this shape through `agentdeck contract leader-review --example` instead of hard-coding `next_command` or `controls[]` fields.
