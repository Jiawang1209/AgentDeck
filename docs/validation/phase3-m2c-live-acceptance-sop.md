# Phase 3 M2c Real Four-Stage Acceptance SOP

## Purpose and evidence boundary

This SOP runs the opt-in, real-provider M2c gate against one frozen AgentDeck
commit. The gate uses Codex CLI as the Leader, Claude Agent ACP as the
`claude-worker` transport, and Codex in a project-specific tmux socket/session
as `codex-worker`.

The portable suite does **not** run this scenario by default. A skipped live
test, a `ready=false` preflight, a missing executable, an authentication/setup
prompt, or any not-reached stage is **not M2c PASS**. Only a genuinely completed
live run may update the Task 11 validation report or product status.

The harness never searches for a replacement provider after opt-in, installs a
package, performs login, changes global settings, or touches a user tmux
socket/session. Capability probes run from the disposable project with a
minimal environment and isolated `HOME`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`,
`XDG_DATA_HOME`, and `TMPDIR`. Live writes are confined to that disposable
tree. Exact-name controlled launchers bind Codex, Claude, and tmux calls to
their validated executables. Claude Agent ACP uses a specialized launcher that
revalidates its complete package and executes its fixed entrypoint through the
explicit sealed Node executable; it never resolves Node through ambient PATH.

## 1. Freeze and inspect the checkout

Run from the AgentDeck implementation checkout:

```bash
git rev-parse HEAD
git diff --check
git status --short
```

The live report records only the frozen AgentDeck commit hash, executable
basenames, and sanitized bounded version strings. It never records executable
paths, environment values, home paths, raw help output, terminal text, auth
material, or provider transcripts.

## 2. Prepare one explicit content authority

The designated preflight and a later live run must use the same logical inputs:

```text
AGENTDECK_M2C_LEADER_MODEL
AGENTDECK_M2C_CODEX
AGENTDECK_M2C_CLAUDE
AGENTDECK_M2C_CLAUDE_ACP
AGENTDECK_M2C_CLAUDE_ACP_PACKAGE
AGENTDECK_M2C_NODE
AGENTDECK_M2C_TMUX
```

Every path must be an audited absolute local path. Executable inputs must be
non-symlink regular executables. `AGENTDECK_M2C_CLAUDE_ACP_PACKAGE` must be the
non-symlink package root. Its sealed `package.json` must declare the official
`claude-agent-acp` npm bin as a safe package-relative executable;
`AGENTDECK_M2C_CLAUDE_ACP` must name that same metadata-selected file. The
currently audited `0.58.1` package declares `dist/index.js`; this is package
evidence, not a universal hard-coded filename.
The package root, every directory, and every file must not be group- or
world-writable. Do not discover a substitute through PATH, run install/login,
or modify global settings as part of this SOP.

The cross-process `m2c-tool-authority/v2` digest binds the exact Leader model,
Codex, Claude, Node, tmux, and complete ACP package-tree content. It excludes
absolute paths, inode/device, owner, mode, mtime, xattrs, and temporary names so
the same audited content can be reconstructed after the preflight checkout is
removed. The canonical metadata-selected entrypoint path is also bound into the
v2 digest. Each process separately retains and revalidates runtime facts.

## 3. Run the separately authorized designated preflight once

Human authorization must name the frozen SHA and exact Leader model before
this command runs. Substitute the already audited absolute paths; do not use
`command -v` inside the command.

```bash
AGENTDECK_M2C_STRICT_PREFLIGHT=1 \
AGENTDECK_M2C_LEADER_MODEL="<audited-model-id>" \
AGENTDECK_M2C_CODEX="<absolute-codex-path>" \
AGENTDECK_M2C_CLAUDE="<absolute-claude-path>" \
AGENTDECK_M2C_CLAUDE_ACP="<audited-absolute-package-root>/dist/index.js" \
AGENTDECK_M2C_CLAUDE_ACP_PACKAGE="<absolute-package-root>" \
AGENTDECK_M2C_NODE="<absolute-node-path>" \
AGENTDECK_M2C_TMUX="<absolute-tmux-path>" \
PYTHONPATH="<absolute-detached-worktree>/src" \
  conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py::test_m2c_explicit_authority_preflight_is_read_only -q -s
```

The strict payload uses `schema_version=m2c-live-preflight/v4`. It contains only
the explicit Leader card, logical tool names, sanitized bounded versions, the
fixed five-second timeout, unique allowlisted blockers, closed
`tool + probe + code` failures, and this public authority card:

```json
{
  "schema_version": "m2c-tool-authority/v2",
  "digest": "sha256:<64-lowercase-hex>",
  "source": "explicit",
  "ready": true
}
```

Record only frozen SHA, exact model ID, schema version, `ready`, blockers,
failures, and the final authority digest. Never persist raw stdout/stderr,
prompts, terminal content, environment values, absolute paths, member hashes,
or authentication material. The pytest node may pass its response contract
while returning `ready=false`; that result is BLOCKED and cannot authorize
live. Only `ready=true`, `blockers=[]`, and `failures=[]` may proceed to a new,
separate human live authorization naming SHA, model, and exact digest.

The older `test_m2c_live_preflight_is_read_only` remains a PATH-compatible
developer regression using `m2c-live-preflight/v2`. It is not designated
authority and can never authorize live.

The Leader model is a required explicit identity, not a default. Preflight
validates identity only: it does not invoke the model, access a provider, or
prove model availability to the account.

Codex CLI initializes per-process arg0 helper aliases even for `--version` and
`exec --help`. For those two capability probes only, the harness sets
`CODEX_HOME` to a non-created child of the already isolated `TMPDIR`. Release
Codex refuses to place helper binaries under its temporary directory, emits a
bounded warning, and continues with the requested metadata output. The version
extractor skips leading `WARNING:` lines and still requires a non-empty
sanitized version. No live Leader/Worker invocation receives this probe-only
environment. If a future Codex release writes anyway, the unchanged root
snapshot gate still returns `probe_wrote_files`.

Each probe is started in a new session and immediately bound to its exact
process group plus kernel process-birth identity. A unique opaque scope marker
is inherited only by that probe tree. While the root lives, bounded polling
records recursively discovered descendants with exact birth identities. After
the root exits, a same-UID marker scan (Linux `/proc/<pid>/environ`; macOS
`sysctl(KERN_PROCARGS2)`) closes the fork-then-`setsid`/reparent window. Exact
group and per-PID cleanup repeats until a marker scan proves quiescence.
Unavailable enumeration, environment inspection, or birth sealing produces
fixed `probe_scope_unverified`; a residual produces
`probe_residual_process`. Both force `ready=false`, and no unsealed PID/PGID is
signalled. The opaque marker is never returned in probe output or diagnostics.

The strict test snapshots the disposable project, every isolated probe root,
every explicit executable seal, and the complete ACP package before and after
each probe. Any create, delete, content, kind, mode, or directory mutation is
attributed to the exact tool/probe as `probe_wrote_files` and forces
`ready=false`. Process groups, birth identities, descendants, and the opaque
scope marker retain the bounded cleanup behavior described below.

Every live launcher and cleanup boundary revalidates path, device, inode,
owner, mode, size, mtime, and content SHA-256. The private mode-`0500` ACP
launcher additionally re-walks the complete package, verifies the fixed
entrypoint, verifies exact Node, and directly calls Node with that entrypoint.

macOS does not expose `fexecve` through this Python runtime. Therefore the
final verified `lstat` to `execve(path)` interval is a documented platform
TOCTOU residual; the harness does not claim inode-bound exec. Any earlier
identity/hash mismatch fails closed with exit 126, and it never falls back to
an unverified executable or a single-file copy that would break
`claude-agent-acp` ESM-relative imports.

## 4. Run the real gate once

This requires a second human authorization naming the same frozen SHA, exact
model, and exact digest produced by the ready designated preflight.

```bash
AGENTDECK_M2C_LIVE=1 \
AGENTDECK_M2C_LEADER_MODEL="<audited-model-id>" \
AGENTDECK_M2C_CODEX="<absolute-codex-path>" \
AGENTDECK_M2C_CLAUDE="<absolute-claude-path>" \
AGENTDECK_M2C_CLAUDE_ACP="<audited-absolute-package-root>/dist/index.js" \
AGENTDECK_M2C_CLAUDE_ACP_PACKAGE="<absolute-package-root>" \
AGENTDECK_M2C_NODE="<absolute-node-path>" \
AGENTDECK_M2C_TMUX="<absolute-tmux-path>" \
AGENTDECK_M2C_AUTHORITY_DIGEST="sha256:<approved-64-lowercase-hex>" \
PYTHONPATH="<absolute-detached-worktree>/src" \
  conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py::test_real_four_stage_m2c_acceptance -q -s
```

Live validates digest grammar, loads every explicit input, recomputes content
identity, and compares the approved digest before creating its disposable
root. Missing or drifted identity fails closed. It then passes the same
in-memory authority object to the internal strict preflight; it does not reread
environment variables, resolve PATH, or select another Node/package/model.

The gate creates a fresh disposable project outside the checkout, initializes
only project-local AgentDeck state, and sends one natural-language request via
a bare bounded PTY. Before confirmation, the harness requires exactly seven
task-authority fields:

- `phase_order`: implementation, review, revision, and acceptance occur in
  that order;
- `worker_order`: the assignments are Claude, Codex, Claude, Codex;
- `artifact_all_steps`: every task names `artifact.txt`;
- `implementation_draft`: implementation requires `draft-v1`;
- `review_target`: review requires `accepted-v2`;
- `revision_transition`: revision contains both `draft-v1` and `accepted-v2`;
- `acceptance_target`: acceptance requires `accepted-v2`.

A false field stops before confirmation with zero daemon admission and zero
Worker effect. It requires:

1. one Codex native-schema preview with exact `implementation`, `review`,
   `revision`, and `acceptance` phases;
2. exactly one natural-language preview confirmation and durable daemon
   admission before the first PTY closes;
3. both real Claude ACP edit permissions confirmed through the Task 9 public
   scoped-handle preview/confirm commands with no controller credential in
   public JSON or argv;
4. execution of the exact enabled ProjectView/workbench `codex-worker`
   `select_pane` control as argv without a shell, followed by an exact
   project-socket `display-message` identity check (never pane capture text);
5. a safe step-3 pause, explicit `codex-worker` takeover, no-change
   return-control reconciliation, then continuation;
6. four succeeded attempts, **four canonical recorded handoff evidence rows**,
   and **exactly three predecessor-to-next-stage links**;
7. exact `artifact.txt` bytes `accepted-v2\n` and their byte count/hash;
8. agreement among ProjectView, Mission status, workbench ledger, events,
   traces, execution snapshot, daemon admission, and attempt receipts;
9. collect-all cleanup that first verifies exact daemon PID/instance/project
metadata through a successful daemon handshake, seals no-follow metadata
   inode/owner/mode/content, Unix-socket inode/type/owner/mode, PID kernel-birth
   fingerprint, process group, and descendants, then revalidates that authority
   before any fallback signal. Missing or drifted authority emits only
   `daemon_cleanup_authority_unverified`, sends zero signals, and blocks PASS.
   Valid fallback applies bounded TERM/KILL only to that exact daemon group.
   Cleanup also kills only the exact disposable tmux socket's session/server,
   removes the project, and derives zero residual process/resource counts from
   post-cleanup probes.

PTY output is retained only as a 64 KiB process-local tail for in-process
parsing. That tail is excluded from `_PtyTail`'s default representation. The
transcript-free boundary includes default pytest traceback/report rendering,
not only the exception string; it does not depend on `--tb=short` or output
post-filtering. Any PTY diagnostic exposes only `byte_count`, `truncated`, and
`sha256`. PTY open, process spawn, setup, and cleanup are all enclosed by
collect-all failure guards that seal the exact new-session process group plus
leader birth identity, enumerate group members, and apply bounded group
TERM/KILL even if the leader has already exited. Tracked groups participate in
final derived residual counts.

The deterministic default-report regression removes inherited
`PYTEST_ADDOPTS`, sets `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, and invokes the child
as `python -m pytest <probe> -q` with no `--tb` option and no output filtering.
It retains the legitimate pytest probe source marker while independently
rejecting injected prompt, stderr, model-output, path, and sentinel markers.
The probe lives in an explicit temporary directory; subprocess execution and
report assertions remain inside that cleanup boundary, and a separate injected
assertion verifies cleanup on exceptional exit.

The parent-scoped setup guard begins before the first `repo`, tmux temporary,
runtime-bin, or controlled-launcher write. A first or mid-sequence launcher
failure removes the entire disposable parent while preserving the original
fixed blocker. If that removal itself fails, the original blocker remains
primary and receives only a compact fixed `live_setup_cleanup_failed` note.
The same outer boundary covers `KeyboardInterrupt`, `SystemExit`, and other
`BaseException` exits: cleanup runs first, then the identical interruption
object and type are re-raised. Even a second `BaseException` raised by cleanup
cannot replace the active interruption; it only causes the same fixed compact
note to be attached.
Each drain call also has explicit byte, chunk, duration, and overall-deadline
budgets, so a continuous writer must yield control to timeout/process checks.
Process fingerprints use Linux `/proc/<pid>/stat` start ticks or macOS
`libproc.proc_pidinfo(PROC_PIDTBSDINFO)` start seconds plus microseconds, bound
with PID, UID, and PGID; unsupported or unreadable kernel identity fails closed
without a coarse `ps` timestamp fallback.

Before Mission Preview, the specialized wait continuously performs a bounded
PTY drain, checks the exact new conversation turn's durable terminal, polls
the PTY process, and respects one outer deadline. A valid durable Leader
terminal takes precedence over a later timeout and exposes exactly
`stage`, `diagnostic_code`, `attempt_count`, and `constraint_mode`. Production
Leader stages map one-to-one to these stable harness codes:

```text
leader_cancelled_before_preview
leader_backend_blocked_before_preview
leader_backend_failure_before_preview
leader_timeout_before_preview
leader_nonzero_before_preview
leader_json_parse_before_preview
leader_schema_before_preview
leader_oversize_before_preview
leader_acp_incomplete_before_preview
leader_acp_permission_before_preview
leader_acp_empty_before_preview
leader_acp_failure_before_preview
```

`bare_pty_exited_before_preview` means the PTY process ended after its final
bounded drain without a Preview or valid durable terminal.
`leader_terminal_evidence_invalid` means persisted lifecycle evidence was
malformed or contradictory. `leader_preview_terminal_conflict` means the same
observation contained both Preview and terminal facts and therefore fails
closed. `mission_preview_timeout` is reserved for the strict final case where
the deadline expires while the PTY is still alive and neither Preview nor a
durable terminal exists. None of these diagnostics retains raw PTY content,
prompt text, stdout, stderr, model output, argv, environment values, or paths.

Failures with a durable store load that store exactly once and derive both
state cardinalities and a transcript-free ledger from that same snapshot. The
ledger has exactly these fields:

```text
classification mission_status step_position agent_id configured_transport
attempt_state reply_state handoff_state handoff_status permission_count
permission_states
```

Every textual ledger value comes from a closed allowlist; an unknown or
malformed value becomes `unknown`, and a step outside 1 through 4 becomes `0`.
Each of `missions`, `mission_attempts`, `mission_worker_replies`,
`mission_handoffs`, and `permission_requests` must be an exact list containing
only exact dictionaries. A dict container, string, or list containing any
non-dict item is malformed and fails closed as `permission_state_inconsistent`
after the higher-priority Leader authority check. Malformed permissions report
fixed `permission_count=-1` and `permission_states=[]`; they are never guessed
to be an empty or populated valid queue. Existing cardinality diagnostics keep
their independent list/dict counting semantics.

After selecting the current attempt, the ledger follows one complete opaque
lineage: exactly one Mission, exactly one same-Mission reply for that attempt,
and exactly one same-Mission handoff bound to that selected reply. When both
attempt and reply carry a dispatch identity, those identities must agree.
Opaque identities are used only for equality inside the process and are never
included in the diagnostic. A missing identity, zero or multiple match,
cross-Mission record, reply/handoff binding drift, or dispatch drift makes the
lineage invalid. Invalid lineage projects Mission, reply, and handoff states as
`unknown` and classifies as `permission_state_inconsistent` before considering
active, failed, or successful attempt states.

The closed classifications are `leader_task_authority_missing`,
`worker_effect_not_requested`, `worker_attempt_failed`,
`worker_attempt_active`, and `permission_state_inconsistent`, guarded by one
exact classification allowlist. Classification precedence and meaning are:

1. exact closed task-authority failure is `leader_task_authority_missing`;
2. malformed ledger collections or any valid permission record are
   `permission_state_inconsistent`;
3. `prepared`, `admitting`, `submitted`, or `running` attempts are
   `worker_attempt_active`;
4. `failed`, `cancelled`, or `interrupted` attempts are
   `worker_attempt_failed`;
5. the exact completed-effect facts below are `worker_effect_not_requested`;
6. every other combination is `permission_state_inconsistent`.

`worker_effect_not_requested` means the snapshot has zero permission requests,
a `completed` or `succeeded` attempt, a `validated` reply, a `recorded` handoff,
and canonical handoff status `completed`. It is a diagnostic description of
durable facts, not authorization, approval, permission confirmation, or a
scheduler transition. Any permission record classifies as
`permission_state_inconsistent`; exact closed task-authority failure always
has precedence.

Failures otherwise emit only byte count, truncation flag, SHA-256, a fixed
stage/code, the closed ledger, and state cardinalities—never IDs, PID, terminal
text, paths, commands, task/model text, prompts, environment values,
credentials, raw ACP/provider output, or raw exceptions.

## 5. Classify the result

- `1 passed` from the opt-in live node plus all nine evidence groups above is a
  candidate PASS for Task 11 documentation.
- Skip, `ready=false`, fixed setup blocker, timeout, cleanup failure, or a
  not-reached stage is BLOCKED—not partial PASS.
- Do not retry an unknown external effect. A second run is permitted only after
  a specific in-scope defect has a deterministic RED regression, a minimal
  committed fix, focused/full green verification, and a newly frozen commit.
- Every new live attempt, including one after a classified failure, requires a
  newly frozen commit and a fresh read-only preflight with `ready=true` and
  `blockers=[]`; a prior preflight or classification never authorizes reuse.

Only Task 11 may append a genuine PASS or BLOCKED live report to
`docs/validation/2026-07-13-phase3-m2-project-daemon.md`. This Task 10 SOP and
gate do not claim that a real run has occurred.

## 6. Default non-live verification

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py -q
```

Expected portable result for this frozen candidate: `192 passed, 1 skipped`.
A printed `ready=false`
payload remains an honest setup result, not M2c PASS.
