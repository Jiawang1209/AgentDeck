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
  tool, a server or tool not fully matching `[A-Za-z0-9_-]+` (the box
  extractor's charset — an out-of-charset value could never be released
  during walk-away, a silent no-op; both the CLI and the
  `grant_delegation` writer reject it), an unknown agent, or a duplicate
  active `(agent, server, tool)` triple refuse with zero writes. Duplicate-active refusal is per kind:
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
  lines without inserting spaces). Command extraction is region-anchored to
  the pending box by the same rule as the MCP extractor (only lines after
  the second-to-last waiting-marker line are searched, so a stale box's
  `$ ` line above the pending box is never extracted; the pending box's own
  `$ ` line and option-2 backtick text sit below the box's own
  second-to-last marker and are never excluded), and the collapsed-box
  fallback takes the **last** occurrence of the option-2 marker text in the
  region (same reverse-scan rationale). It reports
  `box_present`, `waiting_hint`, `command`, `box_kind`
  (`command` | `mcp_tool` | null), `mcp_server`, `mcp_tool`, `match_kind`,
  `matched_segments`, `delegated`, `delegation_id`, and the explicit
  `release_command`. It never writes state and never sends input.
- MCP tool boxes (the fifth box class, round 11 live finding #3; wording
  verified verbatim against a live capture in round 12): codex MCP tool
  authorization boxes carry the body sentence
  `Allow the <server> MCP server to run tool "<tool>"?` — the tool name may
  be quoted, and parameter lines (`includeSnapshot: false`, `uid: 1_20`, …)
  may sit between the sentence and the option list. Command extraction is
  tried first (region-anchored to the pending box, see above); only when it
  yields nothing is the MCP extractor tried. The hard guarantee is a
  **structural tie to the live box**: after whitespace collapse, the
  sentence's trailing `?` must be followed by the live selected-option glyph
  sequence `›1.`, and the gap in between may contain only parameter-line
  material — a gap containing another `›` (a selector), another `?` (a
  different box's question), a `$` (a command line), or a backtick (command-
  box option text) is treated as cross-box bridging and never matches. An
  already-answered sentence collapsed into a one-line history entry (e.g.
  `? -> Yes`) has no live selector after it and never matches. This also
  means extraction only succeeds when option 1 ("Allow" / "Yes, proceed") is
  the pre-selected option — exactly what the bare Enter will press. Two
  heuristic layers sit on top as defense in depth: region anchoring (the
  pending-box region is computed over the **full captured pane**, not a
  10-line tail — round 12 found a box whose option 2 quotes a very long
  command can overflow any small tail window; only lines after the
  second-to-last waiting-marker line are searched, so an answered box that
  still retains its own marker footer keeps its sentence out of the region,
  while the pending box's sentence carries no marker and is never excluded)
  and last-match bias (the last match in the region wins, same reverse-scan
  rationale as waiting-hint detection). The extractor also collapses all
  whitespace in the region before matching (TUI folds can land mid-token;
  fold-point spaces are lost) with the trailing `?` as a hard boundary. Any
  parse failure returns null — fail-closed: an unparsable or unknown wording
  degrades to the sentinel's existing skip behavior, never to a wrong
  release.
- Pane loss during a watch scan (round 12 live finding): if `capture-pane`
  fails for a scanned agent (the pane vanished mid-scan), the scan records a
  `skipped[]` item with reason `pane capture failed` and continues the
  bounded loop instead of crashing; binding reconciliation stays with the
  explicit `agentdeck agent refresh`.
- `agentdeck agent release-box --agent <id> --confirm` re-detects the box and
  sends a bare Enter **only** when an active delegation for that agent covers
  the box: for command boxes the extracted command
  (`command.startswith(prefix)`, falling back to a whitespace-collapsed
  comparison so a wrap that landed exactly on a real space — e.g. a
  `git add` prefix — still matches); for MCP tool boxes a plain exact
  `(mcp_server, mcp_tool)` equality per field with no normalization on
  either side (grant-time charset validation makes `==` correct; one-sided
  normalization would be fail-open widening), matching only
  `kind="mcp_tool"` records for the same agent; success appends an
  `auth_box_released` event carrying the delegation id, full command (null
  for MCP boxes), `box_kind`, `mcp_server`, `mcp_tool`, `match_kind`,
  `matched_segments` (see Composite matching below), and `waiting_hint`
  (on-screen evidence of the box that was released). No box, no extractable
  command or MCP pair, no covering delegation, or missing `--confirm` refuse
  with zero input sent.
- **Composite matching** (round 12 live finding #3; spec
  `docs/superpowers/specs/2026-07-30-delegation-match-normalization-design.md`):
  shell-wrapped commands whose substance is already delegated — env-assignment
  prefixes (`REPRODUCE_UNCONTROLLED_BOOTSTRAP=1 node tests/x.mjs`), `for`-loop
  wrappers, and multi-command chains — cannot match a bare
  `command_prefix.startswith`. The pure module `agentdeck.delegation_match`
  adds a **split-and-cover** third matching arm, and the box surfaces route
  through it via `_match_delegation_with_provenance`:
  - Semantic: the command is split at top level and matches only when **every**
    segment is either covered (starts with an active `command_prefix`
    delegation of that agent, after control-word and env-assignment stripping)
    or is on the fixed built-in glue allowlist, **and at least one segment is
    covered by a real delegation** (all-glue commands never release).
  - Danger boundary (hard requirement): a **composite** command never reuses
    the plain-prefix verdict, because the leading segment alone would carry it
    — `node tests/x.mjs; rm -rf /` starts with a delegated `node tests/`
    prefix. Any command that is more than one simple command (top-level `;`,
    `&&`, `||`, `|`, newline) or that the module cannot parse must pass
    split-and-cover as a whole; otherwise it does not match at all. A single
    command **containing any redirect** is treated the same way (a review
    finding: `node tests/x.mjs > /etc/evil` starts with a delegated prefix but
    writes an arbitrary path), so redirect confinement applies uniformly —
    `> /tmp/x.log` still matches, as a `composite` match. Plain prefix /
    whitespace-collapsed / MCP matching is unchanged for single simple
    commands without redirects.
  - Hard-reject set (scanned before splitting, whole command refused):
    command substitution `$(`, backquote, process substitution `<(` / `>(`,
    heredoc `<<`; plus, during splitting, an input redirect `<`, a background
    `&` (the `>&` of `2>&1` excepted), and unbalanced single/double quotes;
    plus, per segment, a leading `eval` or `source`. `${var}` references are
    permitted — they are not command substitution.
  - Redirects: only `2>&1` and `>` / `>>` (with or without an fd prefix) whose
    target is a single `/tmp/…` token containing no `..`. Every other token
    containing `>` is hard-rejected, which closes two fail-opens: non-`2` fd
    prefixes (`1>>`, `3>`, `10>`) once escaped the `/tmp` confinement, and the
    shell reads word-glued forms (`echo foo>/etc/evil`) as redirects while
    whitespace tokenization makes them look like ordinary arguments.
  - Glue allowlist v1 (fixed, built-in, **not** configurable; extending it is
    an explicit code change with tests): assignments `name=value` (including
    `name=$?` and `${…}` values); `echo …`; `exit` / `exit <token>`; `true`;
    `test …` and `[ … ]`; loop/conditional scaffolding — `for <name> in
    <simple-word list>` (words matching `[A-Za-z0-9._\-]+` or `${…}`), leading
    `do` / `then` / `else` stripped, standalone `done` / `fi`, `if [ … ]`; and
    `tail …` / `head …` **only** when every path-like argument starts with
    `/tmp/` and contains no `..` (flags such as `-80` allowed). Nothing else.
  - Provenance: `match_kind` is `prefix` (plain or whitespace-collapsed
    match), `composite` (split-and-cover match), `mcp_tool` (MCP-pair match),
    or null when the box is undelegated or absent — so every delegated box
    carries a uniform match provenance. `matched_segments` is populated for
    `composite` matches only (a list of `{segment, via}`, where `via` is the
    covering prefix verbatim or the literal `"glue"`) and null otherwise.
    `delegation_id` is the delegation whose prefix covered the first covered
    segment. Both fields appear in `agent boxes`, `release-box`, the
    `released[]` items of `boxes watch` / `run-loop --release-boxes`, and the
    `auth_box_released` event payload. They are audit provenance, not
    authorization.
  - Normalization never widens what a prefix means: round 12's sample 3 stays
    partially manual by design because its `node --check tests/…` segment
    matches no `node tests/` delegation — the human may explicitly grant a
    `node --check tests/` prefix to automate that chain. Inside the composite
    arm, fold-artifact extractions generally fail tokenization and stay
    manual by design; the collapsed-box fallback's own extraction (the
    option-2 prefix text, e.g. `node tests/`) is a clean single command and
    still matches via the prefix arm as before. Any unparseable input yields
    no match and falls back to today's manual path.
- `agentdeck boxes watch --confirm --iterations <n> --interval <seconds>
  [--agent <id>]` is the bounded delegated-automation loop: it requires
  `--confirm` **and** `config.leader.approval_mode == "autonomous"` (the same
  gate class as `run-loop`/`approval auto`), scans running agents each
  iteration, releases only delegation-covered boxes (each release audited as
  `auth_box_released` with `source=boxes_watch`), records non-covered boxes in
  `skipped[]` with `reason=no active delegation`, and always stops at the
  iteration bound. `released[]` and `skipped[]` items (and the shared
  `_scan_release_delegated_boxes` used by `run-loop --release-boxes`) carry
  the same `box_kind`/`mcp_server`/`mcp_tool` fields as `release-box`;
  `released[]` items additionally carry `match_kind`/`matched_segments`.

## Boundaries

- A delegation is not a permission grant: it only automates the Enter a human
  would press on the pre-selected option of a matching box. Reject/esc paths
  are never automated; non-matching boxes always wait for the human.
- Guidance: grant prefixes only for local read-only verification commands
  (e.g. `node tests/`) and task-worktree-scoped git writes; never for push,
  install, or network mutation prefixes.
- Guidance (MCP): grant MCP delegations only for read-only-natured tools
  (the hover/press_key/screenshot class), never for page-mutating tools
  (the navigate/fill/evaluate_script class). AgentDeck cannot verify a
  tool's nature; the human owns that judgment at grant time.
- Release invariants are unchanged for MCP boxes: only a bare Enter on the
  pre-selected option ("Yes, proceed"); the session-level allow (option 2)
  and reject/esc (option 3) are never selected; option navigation is never
  automated; a recurring tool box is released one box at a time, each
  audited.
- `delegation list` / `agent boxes` are inspection-only: no state writes, no
  tmux input, no provider calls.
- Every automated release is auditable via `auth_box_released` events in
  `agentdeck events` / `agentdeck history`.
