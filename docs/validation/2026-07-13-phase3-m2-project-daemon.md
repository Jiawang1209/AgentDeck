# Phase 3 M2 Project Daemon Validation

Date: 2026-07-15

## Verdict

**Deterministic M2 acceptance: PASS. Two-step real transport rehearsal: PASS. Full approved four-stage M2c rehearsal: BLOCKED.**

## Semantic-authority harness conversion

The new M2c harness replaces its live PASS decision based on free-text phase
and token searches with the implemented `mission-semantic-authority/v1`
boundary. Before confirmation it now requires the schema, the unique atomic
revision transition from `draft-v1\n` to `accepted-v2\n`, byte-equal fresh
compilation, four semantic-step and task hashes equal to the authoritative
snapshot, the exact confirmation digest over authority/task/policy/generation
facts, and zero attempts, permissions, Worker replies, and handoffs. Closed
failure diagnostics contain only the fixed gate names and booleans. The old
token checks remain test mutation helpers and cannot produce a live PASS.

The complete non-live M2c harness currently passes `110` tests with exactly one
opt-in live skip. This is deterministic pre-freeze evidence only. It does not
claim a real four-stage PASS, change login or global settings, or authorize a
live attempt. M2c remains **BLOCKED** and M3 remains locked until the new commit
passes two independent full suites, the single read-only preflight returns
`ready=true`, `blockers=[]`, and a separately authorized one-shot live run meets
every success criterion.

This is a control-plane feature around LLM reasoning, not a replacement for an
LLM. Required user authority, visible Leader proposals, unresolved facts, and
confirmed frozen authority remain distinct. The one Mission confirmation is
independent from runtime permissions, and ProjectView exposes only compact
non-authorizing provenance. A2A, remote execution, GUI redesign, and a terminal
emulator remain out of scope.

## Frozen M2c single-live evidence

The latest M2c evidence authority is frozen commit
`1a22618ba083a76f4a21ffc7ebc7a3e513e4aae6` on branch
`codex/m2c-probe-readonly`. Before live execution, the complete non-live focused
harness passed `97` tests with `1` explicit live skip. The full suite passed
`3406` tests with `2` skips in two independent runs of approximately `148.23s`
and `146.26s`; compileall passed and the frozen diff was clean.

The read-only preflight ran once, exited `0`, and passed `1` test in `16.15s`.
Its strict payload reported `ready=true`, `blockers=[]`, Codex CLI `0.131.0`,
Claude CLI `2.1.208`, Claude Agent ACP `0.58.1`, and tmux `3.6a`.

Exactly one live attempt was then made. It exited `1` with `1 failed` in
`49.50s`; it was not retried. The fixed failure code was
`native_schema_task_authority_invalid` and the closed-ledger classification was
`leader_task_authority_missing`. The closed seven-field `task_authority`
projection reported `phase_order=true`, `worker_order=true`,
`artifact_all_steps=true`, `implementation_draft=true`, `review_target=true`,
`revision_transition=false`, and `acceptance_target=true`. Leader-generated revision task did not simultaneously preserve both `draft-v1` and `accepted-v2`. This evidence does not identify which token was absent, does not
claim both were absent, and does not explain why the Leader output lost the
required semantic authority.

The failure snapshot contained `plans=1`, `missions=1`, `attempts=0`,
`permissions=0`, `replies=0`, and `handoffs=0`. Because the gate failed before
confirmation, the run did not reach any ACP, permission, Worker, tmux,
scheduler, or artifact effect. This evidence therefore does not authorize a
permission or ACP repair direction.

The bounded PTY identity was `byte_count=11`, `truncated=false`, and
`sha256=066523e516460e23c045358c6736f76f2fecd1022157b11c679ae69715c0c734`.
That hash is identity evidence only and cannot explain or reconstruct terminal
text.

The harness failure carried no cleanup-failure note. The outer mirror/path was
removed. Post-run audits found `0` mirror/live-pytest/agentdeck-daemon process
matches, `0` M2c temporary-directory matches, and `0` M2c tmux-session matches.
These observed facts are the cleanup evidence; no unreported
`cleanup=complete` or `residual_process_count` payload field is inferred.

The approved four-stage M2c rehearsal remains **BLOCKED**, not a partial PASS,
and M3 remains locked. This historical failure motivated the implemented
semantic-authority control plane and frozen harness described above; it must
not be retried from the old evidence commit. The next active gate is the new
frozen commit, two independent full suites, and exactly one read-only preflight.
There is no automatic retry or live authorization in this document.

## tmux startup/readiness correction

The production daemon acceptance now begins with no pre-created Worker panes.
After the ACP first step records its handoff, the pure scheduler selects one
`start_worker` transition, the StateStore records one compact start claim, and
the daemon creates/binds exactly the frozen tmux reviewer before dispatch. The
test asserts one session creation, one `claude` spawn, spawn-before-prompt order,
no pane for the ACP Worker, and final two-step Mission completion.

Focused regressions also prove that an injected crash after tmux spawn leaves a
durable claimed/ambiguous start and a second invocation cannot spawn again;
Claude first-run trust is classified as `setup_required` with zero `send_input`;
and command/task drift fails before transport construction.

## ACP receipt/stage correction

Official adapters may emit progress, tool-call, tool-result, artifact, thought,
and user updates before the correlated agent reply. The bounded daemon sink now
accepts those protocol kinds, counts every canonical payload against the shared
turn ceilings, and retains only agent text in process memory for reply parsing.
Unknown or malformed updates remain fail-closed.

ACP session admission now commits its submitted receipt before prompt I/O. A
lost daemon therefore cannot replay the external effect. Prompt, streamed
update, reply parse, sink finish, or cleanup failure changes the submitted
attempt to a hard ambiguity with only the fixed blocker
`acp_completion_<stage>_outcome_unknown`; exception, command, path, and payload
text are excluded. Runtime invocation/instruction identity and the raw task are
rehashed against the confirmed execution snapshot before transport creation.

The committed acceptance uses a bare `agentdeck` PTY to create a natural-language
two-Worker Mission preview and confirm that exact preview into a real project
daemon, then disconnects the first client. It reaches an ACP permission pause,
renders deterministic recovery through a second bare `agentdeck` PTY, confirms
the exact permission preview, and completes ACP then tmux with two succeeded
attempts, two validated replies, and two recorded compact handoffs. Worker B is
not admitted until Worker A's validated handoff is durable. ProjectView,
daemon/scheduler contracts, ledger records, events, snapshot hash, and bounded
file effects agree. Durable state contains hashes and byte counts for streamed
ACP updates, not transcript chunks, private reasoning, or secret markers. The
initial PTY run response itself passes the strict Mission run validator and
contains only the compact five-field daemon admission; the daemon RPC
acceptance envelope is not exposed as Mission status provenance.

The crash matrix terminates a real daemon child process with SIGKILL at nine
durable boundaries. Startup recovery classifies before-prepare, after-prepare,
after-receipt-before-reply, after-reply-before-handoff, and
after-handoff-before-next-dispatch as `resumable`; after-dispatch-before-receipt
and daemon loss during a pending ACP permission remain `ambiguous` because the
lost ACP process cannot prove that no external effect occurred. The permission
record itself remains pending for human inspection. The outbox case proves the
already-flushed event is not replayed, and the shutdown case proves a durable
force stop leaves the Mission `interrupted` while preserving the active
attempt's unknown external outcome as `ambiguous`. Every case records zero
duplicate logical external admissions by mapping each observed tmux/ACP prompt
token back to its durable `(mission_id, step_id, agent_id)`. Each restart applies
one real first non-idle scheduler transition before its deterministic stop
marker, so the cardinality assertion is made after a completed scheduler cycle,
not a fixed sleep. Startup owns each spawned daemon immediately: readiness or
probe failure reaps it and best-effort reconciles the endpoint before the
original exception is re-raised. One collect-all teardown guard attempts every
managed process kill/wait, force-stop request-thread join, endpoint
reconciliation, and disposable-project removal even when an earlier cleanup
stage fails. Cleanup diagnostics are attached to the primary failure instead of
masking it. Injected regressions cover both post-spawn readiness failure and a
first cleanup-stage failure.

## Automated evidence

- Crash/acceptance focused group, repeated twice: `12 passed` in each run.
- Acceptance/crash/recovery regression: `127 passed`.
- Final daemon suite after all authority, recovery and mixed-version closure:
  `963 passed`.
- Final full suite on frozen commit `be4dee08`: `2928 passed, 1 skipped` in
  `121.21s`.
- Compileall and `git diff --check`: PASS.

## Real component evidence

Existing installations were inspected without installation or authentication
changes: Claude Agent ACP `0.58.1`, Node `22.23.0`, Codex CLI `0.131.0`, and
Claude CLI `2.1.208`; tmux was `3.6a`. The rehearsal used only those already
installed and authenticated components.

The passing fresh project ran commit `42b60d78`. Bare `agentdeck` used Codex CLI
with explicit model `gpt-5.5` as Leader and produced one strict two-step
`planner -> reviewer` Mission with no preview blocker. Natural-language
confirmation consumed that exact preview once, froze and admitted execution
snapshot
`sha256:fbadfade1b857071ceb8f2722e19833141d5ed12fded4da2546690030d0621ef`,
then the initial client disconnected while the project daemon continued.

Worker A used the real Claude ACP adapter. Two permission requests each passed
the controller-bound preview/confirm path exactly once. Claude completed, its
reply was validated, and its compact handoff was recorded before any Worker B
startup effect. Only then did the daemon persist one tmux start claim, spawn the
frozen Codex reviewer, persist its submitted receipt, validate its correlated
reply and handoff, and complete the Mission. Sanitized event positions prove
the order: Worker A reply validation `85`, Worker A recorded handoff `89`,
Worker B start claim `93`, spawn receipt `94`, submitted receipt `100`, Worker B
reply validation `106`, Mission completion `114`.

Both project-local file effects were checked byte-for-byte before hashing: each
was exactly 24 bytes. Their SHA-256 values were
`2e21abe776bcb49f15e63bf477046bf0b4185dfbbeab88ed07a14858abb43a81`
and `d87e8f0b39830a70be9441db3daa0e232456ea73446e86b3be03d1b9d352267e`.
ProjectView, workbench, daemon, scheduler, ledger, execution-snapshot and
event-order checks all passed. A new bare client reconnected to the same daemon
instance after the first client had gone away.

One preliminary fresh project was rejected before confirmation with the
sanitized configuration blocker `worker executable does not match provider:
planner`; it created no Worker effect and was fully removed before the passing
run. The passing run ended through exact `daemon.force-stop` preview/confirm;
the daemon PID exited, endpoint metadata and socket were removed, the exact
project tmux socket was killed, the disposable directory was deleted, and the
suffix-scoped process audit was empty. No unknown Worker effect was retried.

## Full approved M2c rehearsal blocker

The approved M2c gate is stronger than the passing two-step transport proof: it
requires one real implementation-review-revision-acceptance Mission plus
Mission-time tmux visibility, takeover and return-control. On frozen commit
`be4dee08`, two independent fresh projects used bare `agentdeck`, an installed
Codex CLI `0.131.0` Leader with model `gpt-5.5`, ready Claude Agent ACP
`0.58.1`, and tmux `3.6a`. Both real Leader calls ended durably before preview
creation with `state=failed` and fixed reason `leader_schema`.

For each attempt, `plans=0`, `missions=0`, `mission_attempts=0`,
`permission_requests=0`, and Worker effects were zero. Therefore no exact
Mission confirmation, Worker prompt, permission decision, disconnect/resume,
takeover/return-control, four-step handoff, or file effect was authorized. The
second project was the sole allowed fresh retry and produced the same terminal
stage. This is **BLOCKED**, not PASS; no raw Leader output was retained to guess
which schema field drifted.

Both disposable directories, daemon processes and isolated tmux sockets were
removed. Suffix-scoped process audits were empty. No package installation,
authentication change, tracked-file change, Worker retry, or file effect
occurred. Earlier aborted harness attempts are not acceptance evidence: they
were cleaned and are retained only as development history.

No transcript, raw prompt/tool I/O, credentials, authentication data,
environment dump, opaque native session id, or absolute home path is included
in this report.

## Frozen M2c closure preflight

The hardened M2c closure implementation was frozen at commit `650d6fc4`. A
single read-only preflight used four explicit basename-matched, non-symlink
executable files from an ephemeral same-filesystem staging area. The preflight
test node passed in 9.32 seconds and returned the strict
`m2c-live-preflight/v1` payload in `m2c_live_preflight` mode, but readiness was
`false` with the sole fixed blocker `probe_wrote_files` under the five-second
per-probe bound.

All four capability probes otherwise reported ready: Codex CLI
`codex-cli 0.131.0`, Claude CLI `2.1.208 (Claude Code)`, Claude Agent ACP
`0.58.1`, and tmux `tmux 3.6a`. This proves only bounded capability discovery;
it is not a live Mission acceptance result. The opt-in live node was not run.

The terminal classification is `stage=preflight`, `code=probe_wrote_files`.
Live attempt count is zero. Native live constraint mode and schema hash were
not reached. Plan and Mission creation, exact preview confirmation, all four
implementation/review/revision/acceptance stages, ACP permissions, tmux
visibility, disconnect/reconnect, takeover/return-control, four canonical
handoff evidence rows, three inter-stage lineage links, artifact effects, and
ProjectView/ledger/events/trace/snapshot agreement were all not reached. None
may be inferred from the passing preflight test node or from the earlier
two-step transport proof.

The ephemeral staging area and every M2c live temporary root were removed, and
the post-check found zero residual staged resources. No package install,
login, authentication, or global-setting change was made. The full approved
four-stage M2c verdict therefore remains **BLOCKED**, not PASS.

## Scope boundary

M2 delivers a project-local Unix-socket daemon, not A2A, remote execution,
global roaming, notifications, a Workspace Client, full transcript recovery,
automatic installation/login, Windows IPC, or a terminal emulator.
