# Release Response Contract

Schema version: `project-view/v1` (source of truth: `src/agentdeck/models.py::PROJECT_VIEW_SCHEMA_VERSION`).

`agentdeck release --confirm` is the only explicit write path that records a round release after the review gate is ready. This contract describes the success response shape so GUI/TUI clients can render the release receipt without parsing free text. Discovery entry points:

```bash
agentdeck contract release
agentdeck contract release --example
```

The discovery payload is read-only: it does not read or mutate live state, and it never executes a release.

## Response Fields

Top-level success response fields (`response_fields`):

- `ok`: always `true` on success.
- `mode`: always `release`.
- `requires_explicit_user`: always `true`; the command is human-explicit and never run by the Leader loop.
- `safety`: always `explicit_user`.
- `release`: the persisted release record (see below).
- `release_count`: total number of recorded releases; must equal `release.round`.
- `next_command`: always `agentdeck workbench`, the read-only follow-up inspection entry.
- `next_round_command`: the explicit `agentdeck leader plan --task <goal>` template; the next round still starts from the plan → approval main line.
- `trace_commands`: `agentdeck trace --id <reply_id>` commands for the code-review and round-review replies backing this release.
- `controls`: GUI-ready controls (see below).

## Release Record Fields

`release_record_fields` describe the persisted `releases[]` entry:

- `release_id`, `round`, `status` (always `released`), `review_gate_status`.
- `artifact_count`, `review_reply_count`.
- `code_reviewer_id`, `round_reviewer_id`, `code_review_reply_id`, `round_review_reply_id`.
- `created_at`.

The same record surfaces read-only in ProjectView as `releases.items[]` (`release_item_fields`), where each item additionally carries a `trace_command` pointing at the round-review reply lineage. See `docs/contracts/project-view-schema.md`.

## Controls

`controls[]` uses the shared control shape (`kind`, `label`, `command`, `safety`, `enabled`, `blocker`):

- `inspect`: enabled, `agentdeck workbench`, `safety=inspect`.
- `trace_code_review` / `trace_round_review`: enabled inspect controls whose commands must appear in `trace_commands`.
- `next_round`: disabled `safety=plan_only` template pointing at `next_round_command` with blocker `requires goal text`; a human must fill in the goal.

## Refusal Semantics

The command refuses (non-zero exit, no state write, no success payload) when:

- `--confirm` is missing.
- The review gate is blocked; a `round_release_rejected` audit event records the same gate reason.
- The same code-review / round-review reply pair was already released (`round already released`), also recorded as `round_release_rejected`.

## Validation

`validate_release_contract()` in `src/agentdeck/contracts.py` gates the live success output: `agentdeck release --confirm` must not print a payload that fails the contract. The validator rejects missing fields, non-`explicit_user` safety claims, a record whose status is not `released`, `release_count` drift from `release.round`, non-trace `trace_commands`, and controls whose commands drift from the response fields.

## Boundaries

The release command only records the human's acceptance of the current round as an auditable fact. It does not merge changes, ack inbox items, dispatch follow-up work, create plans/actions/approvals/messages/jobs/inbox items, call a Leader provider, or read/write tmux.
