# Product Kernel — explicit legacy state migration (Task 37)

Date: 2026-07-23

The new SQLite kernel lives beside legacy JSON state during development. Old
`.agentdeck/state.json` + `events.jsonl` are **never silently imported** and can
never become a second write authority. An existing project migrates only through
an explicit **preview → confirm → backup → verify → report** flow; any drift or
verification failure leaves legacy authority unchanged.

## Boundary

- Legacy state is parsed as **inert external data** by
  `src/agentdeck/adapters/legacy_state.py` (`parse_legacy_state`). It never
  imports `state.py`/`models.py` and never writes.
- The migration decision logic (`src/agentdeck/application/migration_service.py`)
  is application-pure: it depends only on an injected `legacy_reader` and
  `db_importer` port. The real SQLite importer is wired at the composition root
  (`product/bootstrap.py::_real_migration_importer`).
- The migrated database is staged as `.agentdeck/agentdeck.db`. Making it the
  live product authority is the separate **cutover (Task 38)**; Task 37 only
  produces and verifies the migrated database and its report.

## Flow

1. **Preview** — `agentdeck _product migrate preview --project <dir> --json`.
   Lists the project mapping (`projects: 1`), skipped unsupported legacy items
   (agents/messages/jobs/events), source hashes, the backup target, a
   content-addressed `preview_id`/`content_hash`, and the exact `apply` command.
   **Writes nothing** (no database, no backup).
2. **Apply** — `agentdeck _product migrate apply --project <dir> --preview-id
   <id> --content-hash <hash> --confirm`. Requires `--confirm` and the exact
   preview id/hash. It re-reads legacy (drift check), backs up the legacy sources
   (content-addressed `backup_hash`), imports the project into a fresh temp
   database, verifies integrity and the project count, and atomically installs it
   at `.agentdeck/agentdeck.db`. Returns a report with `backup_hash`,
   `database_integrity`, `imported_counts`, `skipped_items`, and a
   `rollback_command`.

## Fail-closed guarantees

- Missing `--confirm`, an unknown `preview_id`, a supplied-hash mismatch, legacy
  content **drift** since the preview, or a failed integrity/count check all
  abort with **no new database written** and authority left as `legacy`.
- `MigrationService.authority(project)` reports `migrated` / `legacy` / `none`
  read-only and never mutates.
- Legacy files are not deleted; rollback is simply removing the staged
  `agentdeck.db` (the `rollback_command`).

## Scope

Task 37 imports the project identity row only; legacy agents/messages/jobs/events
are recorded as **skipped** unsupported items in the preview and report. Broader
legacy import and the live cutover are out of Task 37 scope.
