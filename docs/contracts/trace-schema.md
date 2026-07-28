# Trace Contract

`agentdeck trace --id <id>` is the canonical read-only lineage view for one AgentDeck communication message.

It accepts a `message_id`, `attempt_id`, `job_id`, `reply_id`, `artifact_id`, or `inbox_id`, resolves the owning message, and returns the message, optional source plan, attempts, jobs, replies, artifacts, and inbox items that belong to that lineage.

The trace contract uses the same schema version constant as ProjectView: `PROJECT_VIEW_SCHEMA_VERSION` in `src/agentdeck/models.py`. Current value: `project-view/v1`.

Reusable contract response, payload, example fixture, and validator helpers live in `src/agentdeck/contracts.py`: `trace_contract_response()`, `trace_contract_payload()`, `trace_example()`, and `validate_trace_contract()`.

`agentdeck trace` self-validates its output with `validate_trace_contract()` before printing JSON. If validation fails, it exits non-zero, writes contract errors to stderr, and does not print a partial trace.

## Shape

```json
{
  "schema_version": "project-view/v1",
  "query_id": "rep_xxx",
  "message": {},
  "plan": {},
  "attempts": [],
  "jobs": [],
  "replies": [],
  "artifacts": [],
  "inbox_items": []
}
```

## Discovery Command

Use `agentdeck contract trace` to discover this contract from tools or GUI clients:

```json
{
  "schema_version": "project-view/v1",
  "trace_command": "agentdeck trace --id <id>",
  "contract_path": "/absolute/repo/docs/contracts/trace-schema.md",
  "contract_exists": true,
  "top_level_fields": [],
  "message_fields": [],
  "plan_fields": [],
  "leader_generation_fields": [],
  "semantic_leader_generation_fields": [],
  "attempt_fields": [],
  "job_fields": [],
  "reply_fields": [],
  "artifact_fields": [],
  "inbox_item_fields": [],
  "control_fields": []
}
```

Use `agentdeck contract trace --example` to include a stable GUI-ready lineage fixture. The example is deterministic and does not read live state. Trace payloads include `controls[]` so GUI/TUI clients can render a same-card inspect action without parsing `query_id`; those controls are read-only command descriptors and must not be executed automatically.

## Lineage Blocks

`message` identifies the logical task request:

```json
{
  "message_id": "msg_xxx",
  "from_actor": "coder",
  "to_agent": "planner",
  "task": "Review the implementation plan",
  "prompt": "# AgentDeck dispatch...",
  "prompt_skill_context": {},
  "status": "replied",
  "created_at": "2026-07-04T00:00:00+00:00"
}
```

`prompt_skill_context` is the compact provenance for the loaded worker skills injected into the dispatch prompt. The full skill snapshot, when present, lives inside `message.prompt` because the worker needed it as task context; the compact field exists so GUI clients can render loaded skill provenance without parsing prompt text or duplicating full content snapshots.

`attempts[]` records execution attempts for the message. `validate_trace_contract()` checks every item in each lineage collection, not only the first row.

`plan` is either `null` or a compact source-plan provenance card when the traced message came from an approved plan dispatch. It uses the ProjectView plan item shape except that it intentionally omits the separate compact `semantic_authority` card. Its `leader_generation` therefore accepts the same strict ordinary nine-field and semantic eleven-field projections as ProjectView, distinguishing them by exact key set: native ordinary provenance requires `leader-plan/v1`, native semantic provenance requires `leader-semantic-plan/v1`, and non-native schema fields remain null. Semantic projections additionally require `semantic_authority_schema_version=mission-semantic-authority/v1` and a lowercase canonical SHA-256 `semantic_authority_hash`. This generation hash identifies the proposal-stripped required/input authority that constrained the Provider, not the complete compiled output authority. The trace contract checks projection shape, types, hash format, and schema family only; StateStore is responsible for deriving the required/input authority from the persisted plan, stripping compiled `proposed_effects`, and revalidating the generation hash before producing the trace. The plan card includes compact `skill_context`, so GUI clients and auditors can see which loaded skills were visible when the Leader planned the work, and intentionally excludes full skill `content_snapshot`.

`jobs[]` records runtime dispatch facts such as agent id, pane id, and job status.

`replies[]` records worker replies linked back to attempts and jobs.

`artifacts[]` records worker deliverable paths linked back to replies, attempts, and jobs. It is a lineage index only; trace does not read artifact file contents.

`inbox_items[]` records mailbox delivery events, including both `task_request` and `task_reply` items. Missing direction fields are normalized to `null` so GUI clients can render a stable table without checking event type first.

`replies[]` items include `verdict`, the optional G5 `review-verdict/v1` payload parsed from a `verdict:` reply line (`null` when absent). It mirrors the ProjectView `replies.items[].verdict` field and is display/audit evidence only — never gate, approval, or dispatch authority.

## Boundaries

- The contract command is read-only.
- Trace output is a communication ledger view, not a runtime pane log.
- GUI clients should use ProjectView for dashboard summaries and trace for focused lineage detail.
- `agentdeck trace` must not send tmux input, mutate approval state, or infer facts from pane text.
