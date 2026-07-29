# MCP Tool Delegation Scope Design (2026-07-29)

Status: approved by user (2026-07-29). Granularity and registry shape were
both explicitly decided: **per (server, tool) pair only**, **single registry
with a `kind` discriminator** (approach A).

## Problem

The scoped authorization delegation registry
(`docs/superpowers/specs/2026-07-26-scoped-authorization-delegation.md`)
only covers **command prefix** boxes: codex confirmation boxes whose body
carries a `$ <command>` line (or the collapsed-box backtick fallback).
Round 11 live (`docs/validation/2026-07-28-copilot-line1-round11-g2g5-live.md`
finding #3) surfaced a fifth box class: **MCP tool authorization** —
"Allow the chrome-devtools MCP server to run tool hover" (and `press_key`).
Prefix delegation naturally does not cover these; the sentinel correctly
refused to press, and the operator released them by eye. To fold this class
into the walk-away experience, the registry needs a server/tool-granular
delegation shape.

## Decisions (user-approved forks)

1. **Granularity: exactly one (agent, mcp_server, mcp_tool) per grant.**
   No whole-server wildcard; `hover` + `press_key` require two grants.
   Whole-server delegation is a future fork requiring a new decision.
2. **Registry shape: one `delegations[]` list with a `kind` field.**
   No parallel list, no pseudo-prefix encoding.

## Data model

`delegations[]` items become a discriminated union:

- Existing shape (unchanged bytes on disk):
  `{delegation_id, agent_id, prefix, created_at, revoked_at}`.
  Readers treat a missing `kind` as `kind="command_prefix"` — zero
  migration for existing records.
- New shape:
  `{delegation_id, agent_id, kind="mcp_tool", mcp_server, mcp_tool,
  prefix=null, created_at, revoked_at}`.

`StateStore.grant_delegation` grows to accept either a prefix or an
`(mcp_server, mcp_tool)` pair. Duplicate-active refusal compares by kind:
`(agent, prefix)` for command_prefix, `(agent, mcp_server, mcp_tool)` for
mcp_tool. Empty server or tool, unknown agent, or a duplicate active pair
refuse with zero writes. `revoke_delegation` is unchanged (id-addressed,
covers both kinds).

## CLI

- `agentdeck delegation grant --agent <id> --mcp-server <s> --mcp-tool <t>
  --confirm` — new mutually exclusive form. Giving both `--prefix` and the
  MCP pair, or neither, or only one of `--mcp-server`/`--mcp-tool`, errors
  with zero writes. The `delegation_granted` audit event carries
  `kind`, and for mcp_tool grants `mcp_server`/`mcp_tool`.
- `agentdeck delegation list` — items expose `kind`, `mcp_server`,
  `mcp_tool` (null on command_prefix items; `prefix` null on mcp_tool
  items). Validator updated; still read-only, still validated before print.
- `agentdeck delegation revoke` — unchanged.

## Box detection and extraction (fail-closed)

New pure helper `_extract_mcp_tool_box(output) -> (server, tool) | None`:

- Scans the same `_WAITING_FOR_INPUT_TAIL_LINES` tail window as the
  command extractor.
- Matches the codex MCP box's own body text
  `Allow the <server> MCP server to run tool <tool>` (the same trust level
  as the `$ ` line — the text comes from the dialog itself). Wrapped lines
  are joined with the existing whitespace-collapse strategy before
  matching, so a fold inside `chrome-devtools` or a long tool name still
  parses.
- Any parse failure returns None → the box is treated as not delegated →
  it lands in `skipped[]` and is **never pressed**. A wrong or unknown
  wording therefore degrades to today's sentinel behavior, not to a wrong
  release.

Ordering in `agent boxes` / release paths: try the command extractor first
(`$ ` line, collapsed-box fallback — byte-for-byte unchanged); only when it
yields nothing, try the MCP extractor. The two classes cannot shadow each
other because a command box has no "MCP server to run tool" body and vice
versa.

## Matching and release

- `_match_active_delegation` grows a second arm: command → prefix match
  (existing behavior unchanged, including the whitespace-collapsed
  fallback); MCP `(server, tool)` → exact equality per field, compared
  after whitespace collapse to absorb TUI folds.
- Release invariants are untouched: a release sends **only a bare Enter on
  the pre-selected option** (codex's "allow once"). Options 2/3
  (session-level allow, reject) are never selected; option navigation is
  never automated. A recurring tool box is released one box at a time,
  each audited.
- `auth_box_released` events gain `box_kind` and, for MCP releases,
  `mcp_server`/`mcp_tool`.
- `agent boxes` read-only payload gains `box_kind`
  (`command` | `mcp_tool` | null), `mcp_server`, `mcp_tool`.
- `boxes watch` and `run-loop --follow --release-boxes` inherit the new
  capability automatically through the shared
  `_scan_release_delegated_boxes`; `skipped[]` semantics (reason
  `no active delegation`) are unchanged.

## Contract and docs sync

`DELEGATION_ITEM_FIELDS`, `DELEGATION_BOXES_RESPONSE_FIELDS`, the example
fixtures, and `validate_delegation_list_contract()` gain the new fields;
`docs/contracts/delegation-schema.md`, the CLAUDE.md delegation rule,
README, and HISTORY are updated in the same slices.

## Safety boundaries (all preserved)

- `--confirm` on grant/revoke/release-box; `boxes watch` keeps the
  confirm + `approval_mode=autonomous` double gate.
- Detection is read-only; reject/esc paths are never automated; a
  non-matching or unparsable box always waits for the human; every release
  is audited.
- A delegation is still not a permission grant — it only automates the
  Enter a human would press.
- Guidance: grant MCP delegations only for read-only-natured tools
  (hover, press_key, screenshot-class); never for page-mutating tools
  (navigate, fill, evaluate_script-class). AgentDeck cannot verify a
  tool's nature; the human owns this judgment at grant time.

## Test surface (TDD)

1. Grant: mcp form happy path; mutual exclusion (both/neither/partial
   pair); duplicate active `(agent, server, tool)` refusal; unknown agent;
   missing confirm — all zero-write.
2. List: contract fields both kinds; legacy record without `kind` reads as
   command_prefix.
3. Extraction: full box, folded box (wrap inside server and tool names),
   non-box text, command box (must return None from MCP extractor) —
   fail-closed in every negative case.
4. Matching: exact hit; same server different tool → no match; same tool
   different server → no match; revoked → no match.
5. Release/watch end-to-end on Fake backend: MCP box released with audit
   fields; unparsable box skipped; command-box path byte-identical
   regression.
