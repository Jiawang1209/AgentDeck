# Delegation Contract (`delegation/v1` via `project-view/v1`)

Discovery entrypoint: `agentdeck contract delegation` (`--example` adds stable
GUI-ready examples; the payload exposes both `grant_command_template` and
`mcp_grant_command_template`). Source of truth for fields, examples, payload
and the list validator is `src/agentdeck/contracts.py`
(`DELEGATION_LIST_RESPONSE_FIELDS`, `DELEGATION_ITEM_FIELDS`,
`DELEGATION_BOXES_RESPONSE_FIELDS`, `BOXES_WATCH_RESPONSE_FIELDS`,
`validate_delegation_list_contract()`).

Scoped authorization delegation lets a human pre-sanction one narrow class of
worker authorization boxes — commands matching an explicit prefix for one
agent — so AgentDeck can press the Enter the human would have pressed,
with every release audited. Data source: round 6/7 live loops
(`docs/superpowers/specs/2026-07-26-scoped-authorization-delegation.md`).

## Registry Shapes

`delegations[]` items are a discriminated union on `kind`:

- `kind="command_prefix"` — the original prefix shape. Legacy records written
  before the discriminator existed have no `kind` field on disk; readers
  (including `delegation list`) must treat a missing `kind` as
  `command_prefix` — zero migration.
- `kind="mcp_tool"` — one grant covers exactly one
  `(agent, mcp_server, mcp_tool)` pair (no whole-server wildcard;
  `hover` + `press_key` require two grants).

Commands:

- `agentdeck delegation grant --agent <agent_id> --prefix <prefix> --confirm`
  appends to the authoritative `delegations[]` list (registered
  `grant_delegation` writer): `{delegation_id, agent_id,
  kind="command_prefix", prefix, mcp_server=null, mcp_tool=null, created_at,
  revoked_at=null}` plus a `delegation_granted` event (now carrying `kind`
  and the null MCP fields). Unknown agent, empty prefix, missing `--confirm`,
  or a duplicate active `(agent, prefix)` pair refuse with zero writes.
- `agentdeck delegation grant --agent <agent_id> --mcp-server <server>
  --mcp-tool <tool> --confirm` is the mutually exclusive MCP form: it writes
  `{delegation_id, agent_id, kind="mcp_tool", prefix=null, mcp_server,
  mcp_tool, created_at, revoked_at=null}` plus a `delegation_granted` event
  carrying `kind`/`mcp_server`/`mcp_tool`. Giving both `--prefix` and the MCP
  pair, neither, only one of `--mcp-server`/`--mcp-tool`, an empty server or
  tool, an unknown agent, or a duplicate active `(agent, server, tool)`
  triple refuse with zero writes. Duplicate-active refusal is per kind:
  `(agent, prefix)` for `command_prefix`, `(agent, mcp_server, mcp_tool)`
  for `mcp_tool` — the same server with a different tool is a new grant.
- `agentdeck delegation list` (read-only) returns `mode=delegation_list` with
  `count`/`items[]`; each item carries the stored fields plus derived
  `active` (`revoked_at` is null), a normalized `kind`
  (`command_prefix` default for legacy kind-less records), and explicit
  `mcp_server`/`mcp_tool` (null on `command_prefix` items; `prefix` is null
  on `mcp_tool` items). Validates with
  `validate_delegation_list_contract()` before printing.
- `agentdeck delegation revoke --delegation-id <id> --confirm` sets
  `revoked_at` (registered `revoke_delegation` writer) plus a
  `delegation_revoked` event; unknown or already-revoked ids refuse. A revoked
  `(agent, prefix)` pair may be granted again.

## Box Shapes

- `agentdeck agent boxes --agent <id>` (read-only) captures the agent's pane,
  detects a pending authorization box (same tail-window heuristics as
  `agent capture`'s `waiting_for_input`), extracts the boxed `$ command`
  (indented continuation lines joined until the option list; when codex
  collapses a long box body and the `$ ` line is not visible — a round 9
  live finding — the fallback extracts the backtick-quoted prefix from the
  box's own "commands that start with `…`" option text, joining wrapped
  lines without inserting spaces), and reports
  `box_present`, `waiting_hint`, `command`, `delegated`, `delegation_id`, and
  the explicit `release_command`. It never writes state and never sends input.
- `agentdeck agent release-box --agent <id> --confirm` re-detects the box and
  sends a bare Enter **only** when an active delegation for that agent covers
  the extracted command (`command.startswith(prefix)`, falling back to a
  whitespace-collapsed comparison so a wrap that landed exactly on a real
  space — e.g. a `git add` prefix — still matches); success appends an
  `auth_box_released` event carrying the delegation id and full command. No
  box, no detected command, no covering delegation, or missing `--confirm`
  refuse with zero input sent.
- `agentdeck boxes watch --confirm --iterations <n> --interval <seconds>
  [--agent <id>]` is the bounded delegated-automation loop: it requires
  `--confirm` **and** `config.leader.approval_mode == "autonomous"` (the same
  gate class as `run-loop`/`approval auto`), scans running agents each
  iteration, releases only delegation-covered boxes (each release audited as
  `auth_box_released` with `source=boxes_watch`), records non-covered boxes in
  `skipped[]` with `reason=no active delegation`, and always stops at the
  iteration bound.

## Boundaries

- A delegation is not a permission grant: it only automates the Enter a human
  would press on the pre-selected option of a matching box. Reject/esc paths
  are never automated; non-matching boxes always wait for the human.
- Guidance: grant prefixes only for local read-only verification commands
  (e.g. `node tests/`) and task-worktree-scoped git writes; never for push,
  install, or network mutation prefixes.
- `delegation list` / `agent boxes` are inspection-only: no state writes, no
  tmux input, no provider calls.
- Every automated release is auditable via `auth_box_released` events in
  `agentdeck events` / `agentdeck history`.
