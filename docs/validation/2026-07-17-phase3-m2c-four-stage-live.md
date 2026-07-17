# Phase 3 M2c Real Four-Stage Acceptance Evidence

Date: 2026-07-17

## Preview-convergence repair candidate

The exhausted authority below exposed a harness observation race: daemon
admission can become visible while the synchronous confirmation turn is still
inside `preview_executor`, before `conversation_preview_consumed` is committed
and prompt 3 is rendered.

The harness-only repair is frozen at
`690f0baf6efad6ad5608edaf10cf396da2729521`. It waits for prompt 3 after
admission, then preserves the exact Mission-specific consume-event cardinality
check. No production `src/agentdeck/**`, timeout, retry, provider, authority
schema, or diagnostic surface changed.

Deterministic evidence for the new candidate:

- RED: `1 failed, 291 deselected in 3.92s`, exact code
  `mission_preview_not_consumed_exactly_once`;
- exact confirmation/PTY/cardinality GREEN: `9 passed, 286 deselected in
  1.03s`;
- strict/package/launcher/preview aggregate: `55 passed, 240 deselected in
  8.48s`;
- complete non-live M2c: `293 passed, 2 skipped in 86.41s`;
- product regressions: `851 passed in 4.23s`;
- full suite 1: `4384 passed, 3 skipped in 222.29s`;
- full suite 2: `4384 passed, 3 skipped in 211.96s`;
- compile, diff, current-slice product-source scope, leakage, process, and
  temporary-root audits: PASS.

Both detached full-suite worktrees were removed. The frozen implementation
remained unchanged; process, daemon, worktree, and temporary-root audits were
empty. A new real installed-input audit and the one new v5 preflight are now the
next gate. The old preflight/live authority documented below is exhausted and
will not be reused.

The installed-input audit then confirmed the same five regular executable
inputs, metadata-selected `dist/index.js`, and both closed package-internal npm
links. The new designated v5 preflight ran exactly once on frozen
`690f0baf...` with Leader `gpt-5.5` and passed `1 passed in 15.92s`:
`ready=true`, `blockers=[]`, `failures=[]`, authority v3, digest
`sha256:b194c3b4ccbfa3ba2b534bf9cb51e59ecbc077e2576c6eea8ba343f26cc83ffa`.
The digest matches the previous tool authority because installed content is
unchanged; the acceptance authorization additionally binds the new frozen git
SHA. The detached preflight worktree was removed and residue audits were empty.

Exactly one same-SHA/model/digest real four-stage Mission is now the remaining
M2c gate for this candidate.

That live node then ran exactly once and was not retried. It crossed Mission
Preview, exact-once consumption, prompt-3 convergence, and daemon admission,
then reported `1 failed in 224.33s` with:

```json
{
  "stage": "live_acceptance",
  "code": "first_permission_timeout",
  "cardinalities": {
    "plans": 1,
    "missions": 1,
    "mission_attempts": 1,
    "permission_requests": 0,
    "mission_worker_replies": 0,
    "mission_handoffs": 0
  },
  "ledger": {
    "agent_id": "claude-worker",
    "configured_transport": "acp",
    "step_position": 1,
    "attempt_state": "ambiguous",
    "permission_count": 0,
    "permission_states": [],
    "classification": "permission_state_inconsistent"
  }
}
```

The run proved the preview-convergence correction but did not reach the first
permission confirmation or any Worker reply/handoff. Cleanup removed the
detached checkout and disposable project; process, daemon, ACP, tmux, worktree,
and temporary-root audits were empty. This SHA/model/digest live authority is
exhausted. M2c remains **BLOCKED** and M3 remains locked pending a new
evidence-driven cycle for the first ACP attempt's ambiguous terminal state.

## First-attempt terminal-observability candidate

The harness-only terminal observer is frozen at
`3b2b3ae18dec745e56ff1920c3a401c9518515ec`. It stops the first-permission wait
on one exact durable terminal attempt and maps only admission, receipt, ACP
prompt/update/parse/finish/cleanup, failed, cancelled, or interrupted into
closed evidence. It does not change AgentDeck product source, ACP behavior,
timeouts, retries, provider selection, or authority schemas.

Deterministic evidence:

- RED: `1 failed, 295 deselected in 0.84s` because the terminal-aware wait was
  absent;
- focused terminal/diagnostic GREEN: `55 passed, 257 deselected in 0.72s`;
- focused strict/package/launcher/live aggregate: `109 passed, 203 deselected
  in 24.55s`;
- complete non-live M2c: `310 passed, 2 skipped in 80.97s`;
- product regressions: `851 passed in 4.48s`;
- full suite 1: `4401 passed, 3 skipped in 207.30s`;
- full suite 2: `4401 passed, 3 skipped in 210.93s`;
- compile, diff, current-slice product-source zero-change, leakage, process,
  daemon, ACP, worktree, and temporary-root audits: PASS.

Both detached worktrees were removed; frozen implementation files remained
unchanged; process, daemon, ACP, worktree, and temporary-root audits were empty.
The next gate is a fresh installed-input audit and one new real v5 preflight.

The audit found no drift. The new v5 preflight ran exactly once on frozen
`3b2b3ae1...` with Leader `gpt-5.5` and passed `1 passed in 15.55s`:
`ready=true`, `blockers=[]`, `failures=[]`, authority v3, digest
`sha256:b194c3b4ccbfa3ba2b534bf9cb51e59ecbc077e2576c6eea8ba343f26cc83ffa`.
Its detached worktree was removed and residue audits were empty. One
same-SHA/model/digest live Mission is now authorized for this candidate.

That live node ran exactly once and was not retried. It crossed Preview,
prompt-3 convergence, daemon admission, and ACP session admission, then failed
`1 failed in 48.97s` as:

```json
{
  "stage": "live_acceptance",
  "code": "first_attempt_acp_prompt_ambiguous",
  "cardinalities": {
    "plans": 1,
    "missions": 1,
    "mission_attempts": 1,
    "permission_requests": 0,
    "mission_worker_replies": 0,
    "mission_handoffs": 0
  },
  "ledger": {
    "agent_id": "claude-worker",
    "configured_transport": "acp",
    "step_position": 1,
    "attempt_state": "ambiguous",
    "attempt_terminal_stage": "acp_prompt",
    "classification": "worker_attempt_ambiguous"
  }
}
```

The candidate successfully replaced the generic 180-second permission timeout
with an exact durable ACP prompt-stage terminal. No permission, reply, handoff,
or artifact effect occurred. Cleanup removed the detached checkout and
disposable project; process, daemon, ACP, tmux, worktree, and temporary-root
audits were empty. This authority is exhausted. M2c remains **BLOCKED** and M3
remains locked pending a new minimal cycle that classifies the safe underlying
`AcpTransport.prompt()` failure without retaining raw adapter output.

A follow-up read-only authentication audit then identified the external cause:
the exact configured Claude CLI reported `loggedIn=false` and
`authMethod=none`; no supported Anthropic API/auth/OAuth environment credential
was present. No account identity, token, configuration content, or path was
retained. This explains why ACP session admission could succeed while the first
provider prompt terminalized before any permission request.

No login or global authentication mutation was attempted. A human must restore
Claude authentication first. After that, the next repair cycle must add a
read-only, closed auth-readiness gate so version/help probes can never again
claim a logged-out Claude Worker is ready.

## Claude authentication readiness candidate

The approved harness-only correction is now implemented. Strict preflight v6
runs the exact authority-v3 Claude executable with bounded
`auth status --json`, accepts only exit-zero `loggedIn=true`, and otherwise
emits the closed `claude/auth-status/claude_auth_unavailable` failure. Duplicate
keys, malformed/missing/wrong-typed results, nonzero success claims, and
logged-out state all fail closed. Account fields, auth method, subscription,
environment values, raw output, secrets, and paths never enter the payload.

RED proved v5 incorrectly returned `ready=true` for a fake logged-out Claude:
`1 failed` at the readiness assertion. Focused GREEN covers 11 selected auth
and v6 cases, including login-state independence from the authority digest and
guarded live projection. No `src/agentdeck/**`, provider, ACP transport, retry,
timeout, or global authentication behavior changed.

The implementation is frozen at
`79d8160eb60ad4e8bfb37ff43615f099afd9edc5`. The first complete M2c run exposed
one impossible test-only assertion requiring the one-character malformed input
`{` not to occur in any Python dictionary `repr`; the dedicated sentinel
leakage test was already separate. Removing only that assertion was followed by
a fresh complete result of `320 passed, 2 skipped in 119.98s`. Product,
Conversation, contract, and provider regressions passed `851 passed in 4.36s`.
Compile, diff, product-source zero-change, durable leakage, process, worktree,
and temporary-root audits passed. Two complete suites in fresh detached
worktrees at the unchanged frozen SHA then passed `4411 passed, 3 skipped in
259.45s` and `4411 passed, 3 skipped in 256.80s`. The skips were exactly the
opt-in real ACP, designated strict preflight, and live four-stage Mission nodes.
Both worktrees were removed; process, daemon, ACP, worktree, and temporary-root
audits were empty. Human Claude login is now the sole prerequisite before a
fresh installed-input audit and one new real v6 preflight. Every v5 preflight
above is exhausted historical evidence and cannot authorize v6 live.

The human restored Claude authentication. A closed read-only check returned
`loggedIn=true`; no account identity, token, environment value, configuration
content, or path was retained. The installed-input audit then reconstructed
the unchanged authority v3 digest and confirmed Leader `gpt-5.5`, strict
preflight v6, the five exact logical tool inputs, and metadata-selected
`dist/index.js` without fallback.

The designated v6 preflight ran exactly once on frozen `79d8160e...` and passed
`1 passed in 16.27s` with `ready=true`, `blockers=[]`, `failures=[]`, authority
v3, and digest
`sha256:b194c3b4ccbfa3ba2b534bf9cb51e59ecbc077e2576c6eea8ba343f26cc83ffa`.
The detached checkout was removed and process, daemon, ACP, worktree, and
temporary-root audits were empty. This preflight authority is now consumed and
must not be rerun. The remaining gate is one separately authorized real
four-stage Mission naming this exact frozen SHA, Leader model, and digest.

That separately authorized live node ran exactly once and failed `1 failed in
110.98s`. It crossed Leader Preview, confirmation, daemon admission, Claude ACP
session/prompt, and completed the first implementation attempt. Durable closed
evidence contained one `claude-worker` ACP attempt with `state=succeeded`, one
validated Worker reply, zero permission requests, and zero handoffs. The
first-permission gate therefore stopped as
`first_attempt_terminal_contract_invalid`; no second stage was admitted.

Cleanup removed the detached checkout and disposable live project. Process,
daemon, ACP, tmux, worktree, and temporary-root audits were empty. This
SHA/model/digest live authority is exhausted and must not be retried.

Read-only root-cause inspection found that the installed adapter resolves
Claude user/project/local settings through the SDK and derives its session
permission mode from `permissions.defaultMode`. The repository's existing real
ACP vertical-slice setup explicitly writes disposable project-local
`.claude/settings.local.json` with `defaultMode=default` so a user-level
permissive/auto mode cannot bypass the ACP permission bridge. The M2c harness
does not create or seal that project-local setting. The observed successful
reply without a permission request is therefore consistent with an inherited
non-default permission mode, not an ACP/authentication failure. No user-level
settings content was read or changed. A new minimal design/TDD/freeze cycle must
pin and verify the disposable project's permission mode before any new real
authority is established.

## Project-local Claude permission authority candidate

The approved correction is harness-only. Before any Leader or ACP process is
started inside the disposable live project, it exclusively creates exactly:

```text
.claude/                         directory mode 0700
.claude/settings.local.json     regular non-symlink file mode 0600
```

The file bytes are exactly
`{"permissions":{"defaultMode":"default"}}\n`. Creation and verification
never read or modify user/global Claude settings, never consult `Path.home()`
or `CLAUDE_CONFIG_DIR`, and never persist a path or settings content. The
in-memory seal contains only directory/file identity, mode, size, owner, and
file SHA-256 facts. It is revalidated before Mission creation, around both
permission confirmations, around takeover/return-control, and after Mission
completion. Pre-existing paths and every tested content, mode, inode, kind,
symlink, directory, or extra-entry drift stop with the single compact code
`claude_permission_settings_invalid`.

The RED commit proved the setting was absent. GREEN also exposed a FIFO
replacement that would block if opened before checking kind; the validator now
rejects non-regular files before descriptor open. Current deterministic
evidence is:

- focused permission/setup coverage: `28 passed, 311 deselected in 0.68s`;
- complete non-live M2c: `337 passed, 2 skipped in 95.98s`;
- product/Conversation/contract/provider regressions: `851 passed in 4.28s`;
- compile, diff, current-slice `src/agentdeck/**` zero-change, process,
  worktree, temporary-root, and tracked runtime-state audits: PASS.

Tool authority remains `m2c-tool-authority/v3`; designated preflight remains
`m2c-live-preflight/v6`. The exhausted `79d8160e...` authority cannot be
reused. The candidate is frozen at
`e83dcc482d2403f613485d06eff75ff99ffe733f`; it must pass two complete suites in
fresh detached worktrees and undergo a fresh installed-input audit before a
human may authorize one new strict v6 preflight. No preflight or live run has
been executed for this candidate. M2c remains **BLOCKED** and M3 remains locked.

The two authoritative full suites at frozen `e83dcc48...` passed in separate
fresh detached worktrees: `4428 passed, 3 skipped in 267.02s` and `4428 passed,
3 skipped in 251.95s`. They ran serially through `conda run
--no-capture-output -n agentdeck`; the skips were only the three explicit real
nodes. Preliminary parallel direct-interpreter probes were discarded because
their tracebacks proved an invalid child PATH and fixed-bound resource
contention, not a candidate result. No implementation edit followed. Every
checkout and temporary process/resource root was removed.

A read-only post-suite audit reconstructed the same exact logical inputs as the
previous ready v6 authority and produced authority v3 digest
`sha256:b194c3b4ccbfa3ba2b534bf9cb51e59ecbc077e2576c6eea8ba343f26cc83ffa`
with no loader failures. Claude closed auth readiness remains exit-zero and
logged-in. A different tmux selected only by conda PATH produced a different
digest and was rejected; no fallback or silent substitution occurred. This
That audit was not the designated preflight. At that checkpoint, the next
operation required explicit human authorization for exactly one strict v6
preflight on frozen `e83dcc48...`, Leader `gpt-5.5`, and digest `b194c3...`.

That authorization was granted and the designated strict v6 preflight ran
exactly once. It passed `1 passed in 16.57s` and returned:

```json
{
  "schema_version": "m2c-live-preflight/v6",
  "ready": true,
  "blockers": [],
  "failures": [],
  "leader_model": {
    "provider": "codex-cli",
    "model": "gpt-5.5",
    "source": "explicit",
    "ready": true
  },
  "tool_authority": {
    "schema_version": "m2c-tool-authority/v3",
    "digest": "sha256:b194c3b4ccbfa3ba2b534bf9cb51e59ecbc077e2576c6eea8ba343f26cc83ffa",
    "source": "explicit",
    "ready": true
  }
}
```

No path, environment value, raw output, prompt, terminal transcript, or auth
material was retained. The checkout and all audited runtime/temporary residues
were removed. This preflight authority is consumed and cannot be rerun. The
only next M2c operation is one separately authorized real Mission naming the
same frozen SHA, Leader model, and digest.

The separately authorized live node then ran exactly once and failed `1 failed
in 252.35s` with the fixed code `third_stage_safe_window_timeout`. Its closed
terminal projection was:

```json
{
  "cardinalities": {
    "missions": 1,
    "plans": 1,
    "mission_attempts": 1,
    "permission_requests": 2,
    "mission_worker_replies": 0,
    "mission_handoffs": 0
  },
  "ledger": {
    "agent_id": "claude-worker",
    "configured_transport": "acp",
    "attempt_state": "ambiguous",
    "attempt_terminal_stage": "acp_prompt",
    "permission_count": 2,
    "permission_states": ["pending", "pending"],
    "classification": "permission_state_inconsistent",
    "step_position": 1
  }
}
```

No reply, handoff, later attempt, or accepted artifact evidence existed. The
checkout/project and every audited process/runtime/temporary residue were
removed. This live authority is exhausted and cannot be retried. The next
cycle must determine whether the two pending requests are sequential
permissions within the first ACP attempt and whether the harness confirmed an
unstable/non-unique selection; it must not increase timeouts, auto-approve,
reuse authority, or infer success.

## Frozen authority

- AgentDeck implementation:
  `284d8f62a9121a0d0351938aee1f716b3ebd198e`
- Leader provider: `codex-cli`
- Leader model: `gpt-5.5`
- authority schema: `m2c-tool-authority/v3`
- authority digest:
  `sha256:b194c3b4ccbfa3ba2b534bf9cb51e59ecbc077e2576c6eea8ba343f26cc83ffa`
- strict preflight schema: `m2c-live-preflight/v5`

## Deterministic verification

- focused strict/package/launcher: `37 passed, 254 deselected in 36.43s`
- complete non-live M2c: `289 passed, 2 skipped in 95.77s`
- product regressions: `851 passed in 4.86s`
- full suite 1: `4380 passed, 3 skipped in 205.38s`
- full suite 2: `4380 passed, 3 skipped in 209.27s`
- compile, diff, product-source scope, process, and temporary-root audits: PASS

## Designated read-only preflight

The separately authorized designated node ran exactly once on the frozen
authority and passed `1 passed in 16.32s`.

```json
{
  "schema_version": "m2c-live-preflight/v5",
  "ready": true,
  "blockers": [],
  "failures": [],
  "leader_model": {
    "provider": "codex-cli",
    "model": "gpt-5.5",
    "source": "explicit",
    "ready": true
  },
  "tool_authority": {
    "schema_version": "m2c-tool-authority/v3",
    "digest": "sha256:b194c3b4ccbfa3ba2b534bf9cb51e59ecbc077e2576c6eea8ba343f26cc83ffa",
    "source": "explicit",
    "ready": true
  }
}
```

Sanitized ready tool versions were Codex CLI `0.131.0`, Claude Code `2.1.211`,
Claude Agent ACP `0.58.1`, Node `v22.23.0`, and tmux `3.6a`.

The detached preflight checkout was removed. Follow-up process, temporary-root,
and worktree audits were empty. The preflight did not invoke the Leader model,
start an ACP session, start tmux, initialize an AgentDeck project, start a
daemon, install, authenticate, or change global state.

## Real four-stage Mission

The same-SHA/model/digest live node ran exactly once and was not retried. It
reported `1 failed in 47.71s` with:

```json
{
  "stage": "live_acceptance",
  "code": "mission_preview_not_consumed_exactly_once",
  "cardinalities": {
    "plans": 1,
    "missions": 1,
    "mission_attempts": 0,
    "permission_requests": 0,
    "mission_worker_replies": 0,
    "mission_handoffs": 0
  },
  "pty": {
    "byte_count": 5893,
    "truncated": false,
    "sha256": "6e28bb2b249ca7b1ac863ec7ba1ae174cd1061eb11770574b306595ab58b7c61"
  }
}
```

The Mission Preview and Mission record existed and daemon admission was
observed. The harness then checked the consume-once event before the bare
conversation had durably completed its confirmation response. It stopped and
cleaned up before every Worker attempt, permission, reply, handoff, or artifact
effect.

The detached live checkout was removed. Follow-up process, temporary-root, and
worktree audits were empty. No transcript, prompt, raw model output, environment
value, or absolute path was persisted.

M2c remains **BLOCKED** and M3 remains locked. This SHA/model/digest authority
is exhausted at preflight/live count `1/1` and must not be retried.

## Bounded sequential permission acceptance candidate

The exhausted `e83dcc48...` live facts above are immutable. Their two pending
permission records belonged to the same step-1 Claude ACP attempt; the harness
incorrectly assumed that its first confirmation completed implementation and
that the next permission would belong to revision. The observed blocker is
therefore explained by an acceptance-harness cardinality assumption, not by a
new AgentDeck ACP product defect.

The approved harness-only correction now:

- derives effective permission state from immutable `pending` bases plus exact
  append-only transitions;
- binds each permission through Mission, attempt, AgentSession, ProtocolTurn,
  transport sequence, public preview, exact confirmation, and approved effect;
- handles one to four sequential permissions in each Claude attempt and two to
  eight across the Mission;
- requires a validated reply and canonical completed handoff before every
  successor stage;
- pauses the revision driver before its first confirmation during takeover and
  revalidates exact authority after return-control;
- validates the final four attempts, four replies, four handoffs, three ordered
  inter-stage links, and bounded effective permissions through one shared
  completion validator;
- adds only the closed eight-field `permission_progress` projection to driver
  failures.

Deterministic TDD evidence through implementation Task 8 is:

- lineage RED `a815fcde`: `6 failed, 339 deselected in 1.49s` at the missing
  permission-fact unit;
- lineage GREEN `d2ccc36b`: `6 passed, 339 deselected in 0.75s`; existing
  daemon permission ordering `1 passed, 39 deselected in 0.31s`;
- driver RED `1b8f1691`: `6 failed, 345 deselected in 1.40s` at the missing
  bounded driver;
- driver GREEN `14f46aa5`: `23 passed, 328 deselected in 0.74s`;
- exact confirmation/diagnostic RED `448d458f`: `9 failed, 351 deselected in
  4.14s` at the old helper signatures;
- exact confirmation/diagnostic GREEN `7da90508`: `46 passed, 314 deselected
  in 0.78s`;
- completion RED `6f8134cb`: `12 failed, 360 deselected in 2.10s` at the
  missing shared validator;
- integrated GREEN `b3c393a3`: `27 passed, 345 deselected in 2.22s`;
- conda Python compile, `git diff --check`, and `src/agentdeck/**` zero-change
  scope audit: PASS.

Task 10 deterministic freeze evidence is now complete:

- focused sequential-permission verification: `50 passed, 322 deselected in
  0.46s`;
- complete non-live M2c: `370 passed, 2 skipped in 102.28s`, with only the
  designated preflight and live nodes skipped;
- product/Conversation/contract/provider regressions: `851 passed in 4.51s`;
- conda compile, `git diff --check`, `src/agentdeck/**` zero-change,
  tracked-runtime-state, process, tmux, worktree, leakage, and residue audits:
  PASS;
- requirement review: no findings. Exact preview/confirm/effect authority,
  same-attempt sequential permission progression, reply-plus-handoff gates,
  takeover/return-control exclusion, transition-derived state, and closed
  path-free diagnostics agree with the approved spec. No timeout, retry,
  fallback, global setting, or product source changed.

The documentation commit containing this evidence is the frozen Task 10
candidate; Task 11 records its exact full SHA before creating detached
worktrees. No real provider, ACP/tmux Worker, daemon, designated preflight,
live Mission, install, login, merge, or push ran. This freeze does not authorize
preflight or live execution. M2c remains **BLOCKED** and M3 remains locked.

Task 11 identifies the frozen candidate as
`df25532d0bd4fb9c8dd57fd119607a05411d11db`. Two fresh detached worktrees at
that exact SHA passed serially with identical results:

- suite A: `4461 passed, 3 skipped in 250.94s`;
- suite B: `4461 passed, 3 skipped in 245.06s`.

The three skips were exactly the opt-in real ACP, designated strict preflight,
and real four-stage Mission nodes. Both detached worktrees were removed;
process, daemon, ACP, tmux, temporary worktree, and repository status audits
were empty. The development worktree stayed unchanged throughout both suites.

The subsequent explicit, read-only installed-input reconstruction returned:

```text
authority_schema=m2c-tool-authority/v3
strict_preflight_schema=m2c-live-preflight/v6
leader_model=gpt-5.5
authority_digest=sha256:b194c3b4ccbfa3ba2b534bf9cb51e59ecbc077e2576c6eea8ba343f26cc83ffa
loader_failures=[]
claude_auth_ready=true
```

No PATH candidate was substituted and no path, executable hash, auth payload,
prompt, stderr, or credential is retained here. No designated preflight or
live Mission ran. The next gate is separate human authorization naming this
exact frozen SHA, Leader model, and digest for one read-only strict v6
preflight. M2c remains **BLOCKED** and M3 remains locked.
