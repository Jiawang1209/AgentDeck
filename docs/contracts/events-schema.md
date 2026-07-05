# Events Timeline Contract

`agentdeck events` is the read-only audit timeline entrypoint for AgentDeck.

It reads `.agentdeck/state/events.jsonl` and never writes state, stores cursors, acknowledges inbox items, approves work, dispatches work, captures pane output, or sends tmux input.

## Discovery

```bash
agentdeck contract events
agentdeck contract events --example
```

The contract command returns:

```json
{
  "schema_version": "project-view/v1",
  "events_command": "agentdeck events",
  "response_fields": [],
  "cursor_fields": [],
  "event_item_fields": []
}
```

## Recent Events

```bash
agentdeck events --limit 20
```

Without a cursor, the command keeps its compact response shape:

```json
{
  "count": 2,
  "limit": 20,
  "events": []
}
```

## Cursor Events

```bash
agentdeck events --since evt_xxx --limit 20
```

With a cursor, the response adds cursor metadata:

```json
{
  "count": 1,
  "limit": 20,
  "since_event_id": "evt_old",
  "latest_event_id": "evt_new",
  "cursor_found": true,
  "events": [
    {
      "event_id": "evt_new",
      "event_type": "leader_plan_created",
      "created_at": "2026-07-05T00:00:00+00:00",
      "payload": {
        "plan_id": "pln_example"
      }
    }
  ]
}
```

If `cursor_found` is false, the cursor is stale or unknown and the response falls back to the limited event tail. Cursors belong to GUI/TUI clients or scripts; AgentDeck must not persist client cursors in project state.

## Consumer Rules

- Use `agentdeck workbench` or `agentdeck status` as the default state snapshot.
- Use `workbench.change_summary` for cheap change detection.
- Use `agentdeck events --since <event_id>` for raw timeline details after detecting new events.
- Treat event payloads as audit metadata, not executable commands.
