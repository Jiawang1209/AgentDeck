# Delegation Match Normalization Design (2026-07-30)

Status: approved by user (2026-07-30). Scope decision: **full set** — env
prefix stripping + loop wrappers + multi-segment chains, all under the
"split into segments, every segment covered" semantic.

## Problem

Round 12 live (`docs/validation/2026-07-30-copilot-line1-round12-mcp-delegation-live.md`
finding #3) measured three shell-wrapping shapes that escape `command_prefix`
delegation matching, forcing manual releases of boxes whose substance the
human had already sanctioned:

1. Env-assignment prefix: `REPRODUCE_UNCONTROLLED_BOOTSTRAP=1 node tests/x.mjs`
2. Loop wrapper: `for run_id in 1 2 3 4 5; do node tests/x.mjs >
   /tmp/msg-…-${run_id}.log 2>&1; run_code=$?; echo "…"; if [ ${run_code}
   -ne 0 ]; then tail -80 /tmp/…; exit ${run_code}; fi; done`
3. Multi-command chain: `node tests/a.mjs > /tmp/a.log 2>&1; focus_code=$?;
   echo "…"; node tests/back-to-top.mjs …; node --check tests/…; git diff
   --check; …` (mixing several delegated prefixes with benign glue)

Plain `startswith(prefix)` cannot match any of these. The sentinel correctly
skips (fail-closed), but the walk-away experience degrades to manual Enter
for boxes that are, in substance, already-delegated local read-only
verification.

Danger boundary (hard requirement): `node tests/x; rm -rf /` must never
match because its first segment hits a delegation. Normalization must be
allowlist-parsed and fail-closed at every layer.

## Decision

**Semantic: split + every-segment-covered.** A composite command matches a
delegation set if and only if:

- the whole command survives the hard-reject scan and tokenizes cleanly,
- every segment is either covered (starts with an active `command_prefix`
  delegation of that agent, after env stripping) or is on the fixed built-in
  glue allowlist,
- at least one segment is covered by a real delegation (all-glue commands
  never match).

Anything unparseable falls back to today's behavior (no match → manual).

## Placement (approach A)

New pure module `src/agentdeck/delegation_match.py` — no imports from cli,
no state access, fully unit-testable:

- `normalize_match(command: str, prefixes: list[str]) -> CompositeMatch | None`
  where `CompositeMatch` is a small dataclass/NamedTuple:
  `segments: list[MatchedSegment]` with
  `MatchedSegment(segment: str, via: str)` — `via` is the matching prefix
  (verbatim) or the literal `"glue"`.
- Internal helpers (private but unit-tested): hard-reject scan, quote-aware
  top-level splitter, env-prefix stripper, redirect stripper/validator, glue
  classifier.

A new wrapper `_match_delegation_with_provenance` in `cli.py` routes by
command shape; `_match_active_delegation` itself stays byte-identical.

**Amended during implementation (a fail-open in this spec's original
ordering).** The original text said "plain `startswith` first, composite only
on a miss". That is unsafe: the plain arm is a bare `startswith`, so
`node tests/x.mjs; rm -rf /` matches the leading segment and releases —
violating this spec's own danger boundary. Verified against the pre-fix
commit. The implemented rule instead routes by shape, via
`is_composite_command(command)`: anything that is more than one simple
command, that contains a redirect, or that the tokenizer cannot parse goes
**exclusively** through `normalize_match`; only clean single commands use the
plain/whitespace-collapsed arms. Redirect-bearing single commands are
included on the same review finding (`node tests/x.mjs > /etc/evil` starts
with a delegated prefix but writes an arbitrary path), so `/tmp` confinement
applies uniformly. The MCP arm, both extractors, and all release invariants
are untouched.

## Tokenizer (fail-closed, allowlist style)

Hard-reject scan — if the raw command contains any of: `$(`, backquote,
`eval`, `source`, `<(`, `>(`, a heredoc marker (`<<`), an input redirect
(`<` outside quotes), or unbalanced single/double quotes → return None.
(`${var}` references are permitted; they are not command substitution.)

Splitting — respecting quoted spans, split at top level on `;`, `&&`,
`||`, `|`, and newlines. Each raw segment is trimmed.

Per-segment normalization, in order:

1. Strip leading control words `do`, `then`, `else` (they fuse with the
   following command after `;`-splitting); a segment that is exactly
   `done`, `fi`, or empty after trimming is glue.
2. Strip leading env assignments `NAME=value ` repeatedly, where `NAME` is
   `[A-Za-z_][A-Za-z0-9_]*` and `value` is a single unquoted
   whitespace-free token (a quoted or space-containing value → whole
   command returns None).
3. Strip redirects anywhere in the segment (not only trailing):
   `[fd]> /tmp/<path>`, `[fd]>> /tmp/<path>` — fused or space-separated
   target; the path must start with `/tmp/`, contain no `..`, and be a single
   token (`${var}` inside the token is allowed) — plus `2>&1`. Any other
   redirect target, and **any other token containing `>`** (word-glued forms
   like `echo foo>/etc/evil`, `&>`, `>|`, `2>&3`), → whole command returns
   None.

## Glue allowlist v1 (fixed, built-in, not configurable)

- assignments: `name=value` (including `name=$?` and `${…}` values)
- `echo …` (any arguments)
- `exit` / `exit <token>`
- `true`
- `test …` and `[ … ]`
- loop/conditional scaffolding: `for <name> in <simple-word list>` (words
  matching `[A-Za-z0-9._\-]+` or `${…}`), standalone `do` / `done` / `if
  [ … ]` / `then` / `else` / `fi`
- `tail …` / `head …` **only** when every non-flag argument starts with
  `/tmp/` and contains no `..` (attached flags like `-80` allowed; a
  separated `-n 80` is rejected because `80` is not a `/tmp/` path — the
  conservative direction)

Nothing else. Extending the list is a future explicit code change with
tests, not configuration.

## Matching semantics and provenance

- Covered segment: after normalization, segment starts with any active
  `command_prefix` prefix of the agent (plain `startswith`; the fold-artifact
  whitespace-collapsed comparison is NOT applied inside composite matching —
  collapsed-fallback extractions with fold artifacts will typically fail
  tokenization and stay manual, by design).
- Every segment must be covered or glue; at least one covered; else None.
- Round-12 sample 3 stays partially manual by design: its `node --check
  tests/…` segment matches no `node tests/` delegation — the human can
  explicitly grant a `node --check tests/` prefix if they want that chain
  automated. Normalization never widens what a prefix means.

Surfaces and audit:

- `agent boxes` / `release-box` / `_scan_release_delegated_boxes` payloads
  and the `auth_box_released` event gain `match_kind`
  (`"prefix" | "composite" | "mcp_tool"`, null when undelegated/no box —
  MCP-kind matches report `"mcp_tool"` so every delegated box carries a
  uniform match provenance) and, for composite matches only,
  `matched_segments[]` (list of `{segment, via}`; `via` = prefix text or
  `"glue"`; null for prefix/mcp_tool matches).
- `delegation_id` (existing field) is the delegation whose prefix covered
  the first covered segment.
- Contract sync: `DELEGATION_BOXES_RESPONSE_FIELDS` gains `match_kind` and
  `matched_segments`; examples and `docs/contracts/delegation-schema.md`
  updated; CLAUDE.md delegation bullet extended.

## Safety invariants (all preserved)

- Release primitive unchanged: one bare Enter on the pre-selected option.
- Unparseable input → None → today's manual path. Glue alone never
  releases. Reject/esc never automated. Every release audited.
- MCP-kind delegations and extraction are untouched by this feature.
- The normalizer sees only the extracted command text; it never reads
  panes or state.

## Test surface (TDD)

1. Round-12 verbatim samples: env-prefix sample matches with a
   `node tests/` delegation; loop sample matches (loop scaffolding +
   redirects + tail-/tmp glue); chain sample with only `node tests/` +
   `git diff` delegations does NOT match (the `node --check` segment), and
   matches once a `node --check tests/` delegation is added.
2. Adversarial: `node tests/x; rm -rf /` (reject), `$()`/backquote/`eval`
   injection (reject), `tail /tmp/../etc/passwd` (reject), redirect outside
   /tmp (reject), `< /etc/passwd` (reject), unbalanced quote (reject),
   all-glue command (reject), quoted `;` inside echo does not split.
3. Byte-compatibility: plain prefix and collapsed-fallback matching
   behavior unchanged for all existing fixtures; MCP arm untouched.
4. Surface tests: composite release end-to-end on Fake backend with
   `match_kind`/`matched_segments` asserted in payload and audit event;
   contract example/validator green.
