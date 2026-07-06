# Leader Summary Contract

`agentdeck leader summary --plan-id <id>` is the read-only aggregation surface for a saved Leader plan after dispatched worker steps have replies. It inspects local plan status, replies, and artifacts, then returns a deterministic summary card for humans, GUI clients, and natural-language shells.

It does not call a Leader provider, synthesize new LLM output, create approvals, dispatch work, capture pane output, write replies, acknowledge inbox items, create artifacts, or persist Leader actions. `leader_backend` is provenance for GUI/audit rendering; it is not a tmux pane binding, runtime readiness proof, or execution authorization.

Discovery:

```bash
agentdeck contract leader-summary
agentdeck contract leader-summary --example
```

## Discovery Payload

- `schema_version`: current ProjectView schema version.
- `summary_command`: command template for live summary output.
- `contract_path`: local Markdown schema path.
- `contract_exists`: whether this document exists in the checkout.
- `response_fields`: ordered top-level fields returned by `agentdeck leader summary --plan-id <id>`.
- `leader_backend_fields`: ordered fields for the normalized logical Leader backend card.
- `step_fields`: ordered fields for each item in `steps[]`.
- `artifact_fields`: ordered fields for each item in a step `artifacts[]`.
- `control_fields`: ordered fields for each GUI-ready item in `controls[]`.
- `project_view_schema_version`: ProjectView schema version this contract is aligned with.
- `project_view_contract`: discovery command for ProjectView.
- `leader_review_contract`: discovery command for the review surface that points to summary.
- `trace_contract`: discovery command for communication lineage.

## Summary Response

The live summary response is a deterministic aggregation card:

- `schema_version`: current ProjectView schema version.
- `plan_id`: summarized plan id.
- `task`: original Leader task.
- `status`: `ready` when at least one dispatched step has a reply, otherwise `waiting`.
- `provider`: provider that created the saved plan.
- `model`: model label recorded with the saved plan.
- `leader_backend`: normalized logical Leader identity for the summarized plan, including `agent_id=leader`, provider/model, backend/transport, `reasoning_backend`, `runtime_kind=logical_leader`, `pane_backed=false`, `pane_id=null`, `approval_required=true`, and `dispatch_ready=false`.
- `counts`: plan-status counters for approvals and dispatch state.
- `reply_count`: number of dispatched steps with replies.
- `artifact_count`: number of artifacts attached to summarized steps.
- `summary`: local human-readable rollup string.
- `plan_status_command`: read-only command for the underlying plan status.
- `review_command`: read-only command to rerun Leader review.
- `steps`: per-step reply and artifact aggregation.
- `controls`: GUI-ready controls for status, review, and trace inspection.

Each `steps[]` item uses:

- `step`: plan step number.
- `agent_id`: assigned agent id.
- `role`: assigned role label.
- `task`: step task.
- `approval_id`: approval record id, when present.
- `message_id`: dispatched message id, when present.
- `attempt_id`: dispatch attempt id, when present.
- `job_id`: runtime job id, when present.
- `reply_id`: reply id, when present.
- `reply_text`: captured or manually recorded reply text, when present.
- `artifact_count`: number of artifacts attached to this message.
- `artifacts`: summarized artifacts for this step.
- `trace_command`: best available lineage command for this step.

Each artifact item uses:

- `artifact_id`: artifact id.
- `path`: artifact path reported by the worker.
- `kind`: artifact kind, such as `markdown`.
- `status`: artifact status.
- `trace_command`: lineage command for the artifact.

Each control item uses:

- `kind`: stable control identifier, such as `plan_status`, `review`, or `trace`.
- `label`: display label.
- `command`: CLI command to inspect status, rerun review, or trace lineage.
- `safety`: safety class; summary controls are read-only `inspect` controls.
- `enabled`: whether the control can be shown as runnable.
- `blocker`: reason the control is disabled, or `null`.

## Safety Rules

`agentdeck leader summary --plan-id <id>` must pass `validate_leader_summary_contract()` before printing JSON. Contract failures must return a non-zero exit code and must not print partial summary output.

`plan_status_command` must equal `agentdeck plan status --plan-id <plan_id>`, and `review_command` must equal `agentdeck leader review --plan-id <plan_id>`.

GUI/TUI clients should discover this shape through `agentdeck contract leader-summary --example` instead of hard-coding `steps[]`, `artifacts[]`, or `controls[]` fields.
