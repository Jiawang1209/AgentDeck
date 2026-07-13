# Existing-Project Migration Contract

`migration/v1` defines the GUI-ready, explicit migration boundary for existing
AgentDeck projects.

Discovery:

```bash
agentdeck contract migration
agentdeck contract migration --example
```

The discovery response exposes `preview_response_fields`,
`confirmed_response_fields`, `target_change_fields`, `legacy_mission_fields`,
and `control_fields`, plus stable preview and confirmed examples when
`--example` is supplied. The contract is registered by `agentdeck contract
list` and advertised by workbench `contracts_card.migration_contract`.

## Read-only preview

`agentdeck project migration-preview` returns exactly:

- `schema_version=migration/v1` and `mode=migration_preview`;
- canonical `preview_id`, exact `state.json` byte `source_hash`, expiry, digest,
  consume-once flag, project-local backup path, and exact confirmation command;
- additive `target_changes[]` only;
- historical snapshot-incomplete Missions as `inspect_only`, with exact status
  and new-Mission-preview commands;
- one inspect control and one exact explicit-user migration control.

The command validates `validate_migration_contract()` before printing. It does
not create a lock, backup, state record, event, Mission, daemon, provider call,
tmux read, or terminal input.

## Exact confirmation

Only the command returned by the preview may call `agentdeck project migrate
... --confirm`. Confirmation performs a read-only preflight, then acquires the
existing protocol mutation lock and repeats source/replay/digest validation
inside that same authority boundary. Backup creation and atomic state
replacement occur while the lock is held, so an authoritative concurrent writer
either precedes the locked recheck and makes the preview stale or waits and then
observes the migrated state; no accepted concurrent update is overwritten.

The backup path is exactly
`.agentdeck/backups/<preview_id>/state.json`. Directory traversal uses no-follow
directory descriptors. A symlink or non-directory at `.agentdeck/backups` or
the preview child is rejected. The preview directory is newly created with
private permissions; the sanitized payload is written to an exclusive temporary
file, fsynced, atomically renamed, reopened/fsynced, and followed by a parent
directory fsync. It contains only the source hash and prior absence of additive
paths—never Mission content, runtime secrets, controller credentials, sockets,
or external files.

The backup is durable before state replacement. If save fails after replacement,
the exact source bytes are atomically restored and the state parent directory is
fsynced before the unused backup is removed. If restoration or cleanup cannot be
proved, the backup is retained. Expiry, replay, source drift, digest mismatch,
existing backup, symlink/race detection, backup failure, and save failure never
produce an accepted partial migration.

## Confirmed response

Successful confirmation returns `schema_version=migration/v1`,
`mode=migration_confirmed`, exact preview/source/digest/backup facts, the applied
additive changes, legacy inspect-only summaries, and `consumed=true`. The CLI
validates this response before printing JSON.

Legacy Missions are not rewritten. Reconfirmation goes through a new Leader
Mission preview and explicit confirmation, producing new frozen authority rather
than making historical records appear daemon-authorized.
