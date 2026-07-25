# Task-Worktree Contract

Task worktrees isolate worktree-mode worker dispatches: each dispatch creates
`.agentdeck/worktrees/<agent_id>/<message_id>/` on branch
`agentdeck/<agent_id>/<message_id>` (optionally starting from an earlier plan
step's branch — see `worktree_base_branch` in the project-view contract). This
contract covers the read-only inspection surface and the explicit lifecycle
command shapes.

Use `agentdeck contract worktree` to discover this contract:

```json
{
  "schema_version": "project-view/v1",
  "list_command": "agentdeck worktree list",
  "diff_command_template": "agentdeck worktree diff --message-id <message_id>",
  "merge_command_template": "agentdeck worktree merge --message-id <message_id> --confirm",
  "abandon_command_template": "agentdeck worktree abandon --message-id <message_id> --confirm",
  "prune_command_template": "agentdeck worktree prune --confirm",
  "contract_path": "/absolute/repo/docs/contracts/worktree-schema.md",
  "contract_exists": true,
  "list_response_fields": [],
  "worktree_item_fields": [],
  "diff_response_fields": [],
  "project_view_schema_version": "project-view/v1",
  "project_view_contract": "agentdeck contract project-view"
}
```

Use `agentdeck contract worktree --example` to include stable GUI-ready list
and diff fixtures.

## List Shape (read-only)

`agentdeck worktree list` derives items from message worktree provenance plus
current disk/git state: `agent_id`, `message_id`, `branch`, `path`,
`base_branch`, `exists` (directory present), `dirty` (uncommitted changes in
the worktree), `merged` (branch tip reachable from the main worktree's HEAD),
`abandoned` (explicitly marked via `worktree abandon --confirm`),
`diff_command`, and `trace_command`. It validates with
`validate_worktree_list_contract()` before printing, never writes state, never
touches tmux, and never mutates any worktree.

## Diff Shape (read-only)

`agentdeck worktree diff --message-id <id>` shows the task branch against the
main worktree's `HEAD` (three-dot semantics: changes since the merge base):
`stat` is the `git diff --stat` text, `files[]` carries `{status, path}` from
`--name-status`. The response also projects the explicit `merge_command` and
`abandon_command` follow-ups without executing anything. Unknown or
non-worktree message ids fail with a non-zero exit. It validates with
`validate_worktree_diff_contract()` before printing and reads no file contents
into state.

## Lifecycle Shapes (explicit, --confirm)

- `worktree merge --message-id <id> --confirm` merges the task branch into the
  current branch with `git merge --no-edit`; a conflicting merge is aborted and
  refused (zero state writes) for manual resolution. Success appends a
  `worktree_merged` event and returns `mode=worktree_merged` with
  `next_command=agentdeck worktree prune --confirm`.
- `worktree abandon --message-id <id> --confirm` appends the message id to the
  authoritative `abandoned_worktrees` list (via the registered
  `mark_worktree_abandoned` writer) plus a `worktree_abandoned` event; a second
  abandon of the same id is refused.
- `worktree prune --confirm` removes only worktrees that are abandoned
  (`git worktree remove --force`, branch `-D`) or merged-and-clean
  (`git worktree remove`, branch `-d`); everything else lands in `skipped[]`
  with a reason (`dirty and not abandoned` / `branch not merged`). Each removal
  appends a `worktree_pruned` event. Missing `--confirm` refuses with zero
  writes for all three commands.

## Boundaries

- `list`/`diff` are inspection only: no state writes, no tmux, no provider
  calls, no worktree mutation.
- `merge`/`abandon`/`prune` are explicit human commands requiring `--confirm`
  (decisions B/C): merge never auto-runs, prune only removes worktrees whose
  branch is merged or explicitly abandoned, and a dirty un-abandoned worktree
  can never be deleted.
- Worktree provenance grants no permissions; approvals, runtime safety, and
  tool boundaries are unchanged.
