# Artifacts Index Contract

`agentdeck artifacts` is the read-only artifact index for GUI, TUI, natural-language, and automation clients that need to show files or deliverables reported by worker agents.

It is derived from the same ProjectView `artifacts` summary used by `agentdeck status` and `agentdeck workbench`. It does not read artifact file contents, inspect tmux panes, call any Leader provider, acknowledge inbox items, create replies, or mutate state.

## Commands

```bash
agentdeck artifacts
agentdeck contract artifacts
agentdeck contract artifacts --example
```

## Response Fields

- `schema_version`: current ProjectView schema version.
- `artifacts_command`: the command that produced the live artifact index.
- `project_view_contract`: discovery command for the ProjectView contract.
- `trace_contract`: discovery command for full lineage details.
- `trace_command_template`: command template for inspecting the lineage behind an artifact.
- `artifacts`: ProjectView artifact summary.

## Artifact Summary Fields

- `count`: number of artifact records.
- `by_status`: artifact count grouped by status.
- `by_kind`: artifact count grouped by inferred kind.
- `items`: artifact records.

## Artifact Item Fields

- `artifact_id`: stable artifact ledger id.
- `message_id`: message that produced the artifact.
- `job_id`: runtime job associated with the artifact.
- `reply_id`: reply that declared the artifact.
- `from_agent`: worker agent that produced the artifact.
- `path`: reported project-relative artifact path.
- `kind`: inferred artifact kind, such as `markdown`, `json`, or `text`.
- `status`: artifact status.
- `created_at`: artifact ledger timestamp.
- `trace_command`: command for inspecting the communication lineage.

## Safety

- The command is read-only.
- The command must pass `validate_artifacts_contract()` before printing JSON.
- GUI clients should use `trace_command` or `agentdeck trace --id <artifact_id>` for lineage details and should not read `.agentdeck/state/` directly.
