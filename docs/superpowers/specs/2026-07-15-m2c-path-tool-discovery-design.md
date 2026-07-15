# AgentDeck M2c PATH and Tool Discovery Design

**Date:** 2026-07-15

**Status:** Human-approved design; quality-review closure implemented in candidate

**Milestone:** Phase 3 M2c preflight closure

**North star:** `docs/roadmap/product-north-star.md`

## 1. First principles

AgentDeck is a local-first control plane around already installed Leader and
Worker tools. A user who installed and authenticated Codex CLI, Claude CLI,
Claude Agent ACP, and tmux should normally be able to start AgentDeck and let
it discover those tools from the active process `PATH`. Exact-path overrides
remain useful for CI, version pinning, and live evidence, but they are not a
normal interactive setup requirement.

The production paths already follow this rule: CLI-backed Leaders, ACP adapter
readiness, Node readiness, and tmux readiness use `shutil.which()` and resolve
the selected executable where necessary. The present blocker is therefore not
a missing AgentDeck product feature. It is a false negative in the M2c live
acceptance harness, whose executable seal rejects every symbolic link after
`PATH` discovery.

This slice restores agreement between the harness and the product without
weakening the control-plane boundary:

> `PATH` selects a candidate; the canonical executable target supplies the
> sealed identity.

## 2. Current evidence and root cause

Frozen semantic-authority commit
`553b5b7039745a88dcd0cd1bc1da5fdd43bb4da6` passed the complete non-live M2c
harness with `110 passed, 1 skipped`, two independent full-suite runs with
`4143 passed, 2 skipped`, and compile/diff checks. Its one designated read-only
preflight returned `ready=false` with these blockers:

- `claude_unavailable`;
- `claude_agent_acp_unavailable`;
- `tmux_unavailable`.

Codex CLI alone was reported ready. No live Mission was attempted.

Read-only inspection of the same active shell and `agentdeck` conda environment
finds all four commands:

- Codex is a regular executable shell script;
- Claude is a symlink to its installed versioned executable;
- Claude Agent ACP is an npm-style symlink to its installed JavaScript entry;
- Homebrew tmux is a symlink to its Cellar executable.

`tests/test_m2c_live_acceptance.py::_resolved_probe_seal()` already calls
`shutil.which()`, but `_seal_executable()` rejects a candidate whenever
`path.is_symlink()` is true. Consequently the blocker text says "unavailable"
even though `PATH` discovery succeeded. The failure is an executable-sealing
classification error, not evidence that conda lost the user's tools.

## 3. Goals

This slice must:

1. let the read-only M2c preflight discover installed tools from the active
   conda/pytest `PATH`;
2. accept a valid symlink as a discovery entry while sealing and executing its
   strict canonical target;
3. apply the same canonicalization to explicit `AGENTDECK_M2C_*` preflight
   overrides without making those overrides mandatory;
4. retain the existing inode, ownership, mode, size, mtime, content-hash, and
   pre-execution drift checks for the canonical target;
5. preserve read-only probe isolation and compact path-free evidence;
6. make the Claude Agent ACP capability probe able to find the already
   installed Node interpreter through the same PATH-first rule;
7. prove the change with deterministic RED/GREEN tests before any real-tool
   preflight;
8. freeze a new implementation commit, run two full suites on the unchanged
   SHA, and then consume exactly one new read-only preflight;
9. stop before the four-stage live Mission and require separate human
   authorization even when preflight reports `ready=true` and `blockers=[]`.

## 4. Non-goals

This slice does not:

- add a new production CLI command or product feature;
- change normal Codex, Claude, ACP, or tmux dispatch semantics;
- modify the user's `PATH`, shell startup files, conda configuration, login,
  authentication, or global provider settings;
- install, upgrade, copy, or download a CLI, adapter, Node, or tmux;
- weaken Task 14's explicit staged-launcher and basename authority checks;
- treat a symlink path itself as the executable identity;
- accept a broken link, link cycle, directory, non-regular target, or
  non-executable target;
- expand M2c into A2A, remote execution, global roaming, Workspace Client,
  terminal emulation, or provider redesign;
- execute or automatically retry a real M2c Mission.

## 5. Chosen approach

The selected design is **PATH-first discovery plus canonical target sealing**.

For each of `codex`, `claude`, `claude-agent-acp`, and `tmux`:

1. if the corresponding explicit `AGENTDECK_M2C_*` value is configured for a
   preflight, use that value as the candidate;
2. otherwise call `shutil.which(logical_name)` in the active pytest process;
3. require the raw explicit override value itself to be an absolute path before
   any tilde expansion, preserving the existing fail-closed contract for
   tilde-prefixed and relative overrides;
4. resolve the candidate with `Path.resolve(strict=True)`;
5. pass the resolved regular executable to the existing no-follow seal logic;
6. retain only the canonical path inside `_ExecutableSeal`;
7. execute ordinary tools only through that canonical path; execute ACP by
   using the separately sealed canonical Node target as the primary executable
   and passing the canonical adapter path as its first argument;
8. verify the same seal immediately before every probe and at existing later
   authority boundaries.

`Path.resolve(strict=True)` supplies the deliberately small symlink policy:
valid installed links resolve; broken links and cycles fail. Opening the final
target with `O_NOFOLLOW`, hashing the open descriptor, comparing its file facts
before and after the read, and later resealing the canonical path retain the
existing target-replacement protections. A later change to the discovery link
cannot redirect execution because the original link is never executed after
resolution.

The strict `_seal_executable()` primitive does not need to become a general
symlink-accepting authority. A narrow preflight resolver may canonicalize the
candidate before calling it. This keeps existing live staging/launcher checks,
including exact logical basenames, unchanged.

## 6. PATH and interpreter behavior

The preflight must use the PATH inherited by the `conda run -n agentdeck`
pytest process for discovery. It must not synthesize user installation paths
such as `~/.local/bin` or `/opt/homebrew/bin`, because those locations are
platform- and installation-specific.

Probe subprocesses continue to receive an isolated HOME, XDG roots, temporary
directory, locale, and a bounded PATH rather than the whole user environment.
That bounded PATH is assembled only from already discovered executable
locations plus `/usr/bin` and `/bin`, but it is not interpreter authority for
the ACP probe.

Claude Agent ACP is normally an executable script with an `/usr/bin/env node`
shebang. The harness resolves the already installed `node` command from the
active pytest PATH using the same canonical-target rule, then makes that Node
seal `_bounded_probe`'s primary executable and supplies the canonical adapter
path as the first argument. `_bounded_probe` therefore verifies Node
immediately before spawn and after exit, with no second `/usr/bin/env` lookup,
PATH fallback, or canonical-basename requirement. The adapter seal is verified
separately before and after the call. Node remains an internal probe dependency,
not a fifth public tool item, so `m2c-live-preflight/v1` retains its existing
four-tool response shape. A missing or unusable interpreter leaves ACP unready
without executing the adapter and never triggers installation or PATH mutation.

## 7. Evidence and privacy contract

The public preflight response remains `m2c-live-preflight/v1` with exactly:

- `schema_version`;
- `mode`;
- `ready`;
- `probe_timeout_seconds`;
- four logical tool items;
- deduplicated fixed blocker codes.

Each tool item continues to expose only logical name, safe executable basename,
sanitized version, and readiness. The basename represents the logical discovery
entry, not a version-named canonical target such as Claude's `2.1.208` file.
Neither discovery paths, canonical paths, environment-variable names, HOME,
raw stderr, credentials, nor provider output enter the response.

The existing root snapshot covers the disposable project and every isolated
probe root. Any mutation remains `probe_wrote_files`. Canonical resolution,
stat, open, hash, version/help execution, and validation must not create state,
append audit events, contact AgentDeck providers, inspect user tmux sessions,
or send terminal input.

## 8. Deterministic TDD design

All RED/GREEN work uses temporary fake executables and monkeypatched PATH or
explicit environment variables. It does not invoke installed real tools.

The focused matrix must prove:

1. a regular executable discovered through PATH remains accepted;
2. a valid PATH symlink first exposes the old false negative, then resolves to
   and seals its executable target;
3. a valid explicit absolute symlink follows the same preflight behavior;
4. tilde-prefixed and relative explicit overrides remain rejected from their
   raw values before expansion;
5. broken links, cycles, directories, and non-executable targets are rejected;
6. replacement or content mutation of the canonical target after sealing still
   raises the fixed `executable_identity_drift` boundary before use;
7. the logical tool basename stays stable when the canonical target has a
   version-style filename;
8. a fake ACP entry is passed directly to the sealed fake Node discovered from
   the test PATH, including when Node's canonical target is version-named, and
   completes without shebang lookup or real-HOME access;
9. valid probes leave the project and all isolation roots byte-identical;
10. hostile probes that write under an isolation root still return
    `probe_wrote_files`;
11. replacement of the sealed Node target at the `_bounded_probe` boundary
    returns `executable_identity_drift` before spawn and never executes the
    adapter through an unsealed fallback.

After focused GREEN, run the complete non-live M2c harness, compileall, and
`git diff --check`. None of these commands is the designated real-tool
preflight.

## 9. Commit and frozen-verification protocol

The work is divided into three local commits:

1. this human-approved design spec plus `HISTORY.md` and handoff routing;
2. a human-reviewed writing-plans TDD implementation plan;
3. RED/GREEN implementation, tests, and synchronized durable documentation.

The third commit becomes the candidate evidence SHA. With a clean worktree,
the exact SHA is recorded and two independent full-suite processes run under
the `agentdeck` conda environment. No file or commit may change between them.
After the second pass, the SHA and clean worktree are checked again.

If either suite fails, the preflight allowance is not consumed. The failure is
debugged deterministically, a new implementation commit is created, and both
full suites restart from the beginning on the new unchanged SHA.

Only after both suites pass may this exact node run once:

```bash
conda run -n agentdeck python -m pytest \
  tests/test_m2c_live_acceptance.py::test_m2c_live_preflight_is_read_only \
  -q -s
```

The invocation deliberately supplies no manual `realpath` values and no
`AGENTDECK_M2C_*` overrides. Its purpose is to validate real conda/pytest PATH
discovery. Exit status and the sanitized JSON payload are retained as evidence.

If the result is not exactly `ready=true` and `blockers=[]`, work stops without
a preflight retry or live attempt. The new fixed blocker becomes the input to a
separate debugging/design round.

If it is ready, this PATH/tool-discovery blocker is closed, but M2c is not yet a
live PASS. Work still stops and asks the human for separate authorization for
the one real implementation -> review -> revision -> acceptance Mission.

## 10. Self-review

The written design was checked against the product north star, ultimate-goal
roadmap, repository development constraints, existing M2c closure design, and
current harness implementation.

- **Product alignment:** installed CLI Agents remain first-class local tools;
  AgentDeck does not become an installer or a path-configuration wizard.
- **Scope:** the change corrects a test-harness false negative and introduces
  no new production surface.
- **Security:** PATH selects; strict canonical file facts authorize. ACP uses
  sealed Node as the primary executable with the canonical adapter as an
  argument, so no env-based interpreter fallback exists. Existing target
  drift, cleanup, isolation, and Task 14 launcher checks remain.
- **Read-only boundary:** no probe allowance is spent during TDD, and the final
  preflight cannot install, authenticate, dispatch, or write state.
- **Evidence honesty:** passing tests do not imply readiness; a ready preflight
  does not imply M2c live PASS.
- **Human authority:** any real four-stage Mission remains a separately
  approved action.

Quality review closed the initial candidate's verify-then-env-lookup gap and
its accidental `canonical_basename == "node"` restriction. The closure changed
only test-harness ACP probe construction, added deterministic pre-spawn Node
replacement coverage, and isolated every fake ACP-ready fixture from the
machine Node. It did not consume the designated real-tool preflight.

Final review also closed the explicit-path ordering gap: the raw override now
passes the absolute-path gate before `expanduser()`, so `~/tool` cannot become
authorized merely because HOME expansion produces an absolute path. PATH
discovery keeps its non-explicit canonicalization behavior, and expanduser
failure remains closed.

No unresolved design fork remains. Implementation must stop and ask the human
if it would require changing production CLI discovery, weakening Task 14
launcher identity, adding blocker/schema fields, installing an interpreter, or
running more than the single designated preflight.
