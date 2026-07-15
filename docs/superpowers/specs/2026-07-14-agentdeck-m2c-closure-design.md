# AgentDeck Phase 3 M2c Closure Design

**Date:** 2026-07-14
**Status:** Human-approved design, including the 2026-07-15 live-blocker addendum; implementation requires a separately reviewed TDD plan
**Milestone:** Phase 3 M2c closure
**Depends on:** Phase 3 M2 project daemon at `aec06188`
**North star:** `docs/roadmap/product-north-star.md`

## 1. First principles

This design starts from the AgentDeck product north star rather than from the
current `leader_schema` symptom.

AgentDeck must let a user describe one goal in ordinary language, review one
frozen Mission, confirm it once, disconnect, and later return to work that is
complete or safely paused. AgentDeck—not the Leader model, a Worker, ACP, or a
tmux pane—owns Mission authority, scheduling, permission, lineage, recovery,
and audit. ACP is the preferred structured Worker transport. tmux remains the
visible observation, fallback, debugging, and explicit human-takeover surface.
There is no implicit transport or provider fallback.

Therefore M2c is not complete when a CLI Leader merely emits parseable JSON.
M2c is complete only when one real implementation-review-revision-acceptance
Mission proves the entire natural-language, confirmation, daemon, ACP, tmux,
permission, handoff, takeover, reconnect, recovery, and audit path.

## 2. Current truth and problem statement

Phase 3 M2 Tasks 1–14 are implemented and integrated into `main`. The
deterministic daemon acceptance and the earlier real two-step transport
rehearsal pass. The stronger approved four-stage M2c rehearsal remains blocked.

On frozen commit `be4dee08`, two fresh disposable projects used bare
`agentdeck`, Codex CLI `0.131.0` with model `gpt-5.5` as Leader, Claude Agent
ACP `0.58.1`, and tmux `3.6a`. Both stopped before Mission preview creation at
the sanitized `leader_schema` stage. Each retained zero plans, Missions,
attempts, permissions, and Worker effects. No raw Leader output was retained,
which preserved the transcript-free boundary but left no field-level safe
diagnostic.

The current CLI provider asks for JSON through prompt text, parses stdout, and
then calls `validate_provider_plan_schema()`. It does not use the native
structured-output features present in the installed CLIs:

- Codex: `codex exec --output-schema <FILE>` and
  `--output-last-message <FILE>`;
- Claude: `claude --json-schema <SCHEMA>` with JSON output.

The current `LeaderRequest` freezes selected Worker ids and requested step
count, but those facts are not first-class inputs to the lower-level
`LeaderPlanRequest`. Provider prompts can consequently see all configured
Workers even when the natural-language turn authorized only a subset. The
Mission landing path catches later drift, but the structured-output constraint
cannot currently encode the already frozen authority.

## 3. Goals

This slice must:

1. generate one canonical, authority-aware Leader plan JSON Schema;
2. use native structured output for Codex CLI and Claude CLI Leaders;
3. retain strict AgentDeck semantic validation after native schema validation;
4. preserve compact, allowlisted, credential-free failure stages;
5. add bounded diagnostic codes without retaining raw provider output;
6. permit at most one same-provider, same-model regeneration within the same
   frozen authority and total planning deadline;
7. prove a deterministic four-stage Mission before any live claim;
8. complete one real Claude-ACP/Codex-tmux four-stage Golden Mission;
9. prove disconnect/reconnect, safe ACP permission pause, tmux visibility,
   takeover/return-control, and three ordered compact handoffs;
10. update ProjectView, contracts, validation evidence, handoff, and history so
    every public surface agrees on the M2c result;
11. enter M3 brainstorming only after the full real M2c gate passes.

## 4. Non-goals

This slice does not add A2A, a remote daemon, global project roaming, system
notifications, a WispTerm-class client, a terminal emulator, full transcript
persistence, automatic install/login, a skill marketplace, remote skill
dependencies, or Worker-to-Worker direct scheduling.

It does not move all Leader execution to ACP. It does not replace AgentDeck
with an ACP proxy. It does not make tmux output authoritative protocol state.
It does not broaden the frozen Mission after confirmation.

Any live defect that requires one of these non-goals is recorded as a blocker
and receives a separate brainstorming/spec/plan cycle.

## 5. Considered approaches

### 5.1 Codex-only flag patch

Add `--output-schema` only to `CodexCliProvider`. This is small but leaves the
schema detached from frozen Worker/step authority, leaves Claude CLI on a
different behavioral contract, provides no safe field-level diagnosis, and
does not close the four-stage M2c gate. Rejected.

### 5.2 Canonical schema plus native provider adapters

Generate one AgentDeck-owned schema from frozen authority, adapt it to Codex
and Claude native structured-output mechanisms, revalidate semantically inside
AgentDeck, and then run the complete M2c acceptance. This preserves provider
replaceability and AgentDeck governance while closing the actual product gap.
Selected.

### 5.3 Immediate ACP-only Leader rewrite

Move every Leader through ACP before retrying M2c. This expands the problem to
ACP Leader schema negotiation, streaming, session recovery, and permission
semantics without being required to close the current CLI Leader blocker. It
violates the no-big-bang-rewrite principle. Rejected for this slice.

## 6. Canonical Leader plan schema

### 6.1 Source of truth

Add an internal module such as:

```text
src/agentdeck/providers/plan_schema.py
```

It owns pure helpers conceptually equivalent to:

```python
build_leader_plan_schema(request) -> dict[str, object]
canonical_schema_hash(schema) -> str
validate_leader_plan(plan, authority) -> dict[str, object]
classify_plan_failure(error) -> str
```

Provider adapters must not carry independent copied schemas. The canonical
schema version and hash are deterministic for the same frozen authority.

No third-party JSON Schema runtime is required. Native providers consume the
generated schema, while the existing AgentDeck validator remains the local
semantic authority.

### 6.2 Frozen authority propagation

Extend `LeaderPlanRequest` with:

```text
selected_agent_ids: tuple[str, ...] | None
step_count: int | None
timeout_seconds: int | None
```

The conversation session derives Worker selection and step count once. Those
facts pass unchanged through:

```text
ConversationSession
→ LeaderRequest
→ LeaderGateway
→ LeaderOrchestrator
→ LeaderPlanRequest
→ schema builder
→ LeaderMissionCandidate
→ Mission landing
```

Every boundary rejects partial authority, changed Worker order/set, changed
step count, invalid count, or unknown Worker before durable Mission effects.
Legacy direct provider callers may retain an explicit compatibility path, but
the natural-language Mission path must always supply frozen authority.

### 6.3 Schema shape

The provider proposal contains only reasoning output:

```json
{
  "goal": "non-empty string",
  "summary": "non-empty string",
  "steps": [
    {
      "step": 1,
      "agent_id": "one frozen worker id",
      "role": "configured role",
      "task": "non-empty string",
      "risk": "non-empty string",
      "requires_approval": true
    }
  ]
}
```

The schema enforces:

- top-level object;
- required `goal`, `summary`, and `steps`;
- non-empty display strings;
- `minItems == maxItems == frozen step_count`;
- each `agent_id` is from the frozen Worker allowlist;
- exact required step fields;
- positive integer step numbers;
- `requires_approval` is literal `true`;
- bounded strings and arrays consistent with existing output limits;
- no model-defined execution authorization.

Schema constructs must stay within the common subset supported by the
installed Codex and Claude CLIs. Cross-field role matching and contiguous
numbering remain local semantic checks rather than relying on complex
provider-specific JSON Schema features.

### 6.4 Governance fields are local

The model does not grant authority through `approval_required`,
`dispatch_ready`, transport choice, permission, confirmation, runtime
admission, or controller ownership. AgentDeck deterministically derives:

```text
approval_required = true
dispatch_ready = false
```

Provider-supplied values for those fields cannot weaken the local result.
This preserves current compatible normalization while removing needless
formatting burden from the structured-output schema.

### 6.5 Semantic validation

After native structured output succeeds, AgentDeck still verifies:

1. exact top-level and step types;
2. non-empty goal, summary, task, risk, agent id, and role;
3. exact frozen step count;
4. step numbers are exactly `1..n`, with no gap or duplicate;
5. every Worker is in the frozen allowlist;
6. every role exactly matches that Worker's configured role;
7. every step requires approval;
8. JSON encoding is finite and within size bounds;
9. candidate authority equals request authority;
10. the raw and normalized plans both pass existing Mission validation.

Native JSON Schema improves generation reliability. It never replaces
AgentDeck's authority or validation.

## 7. Native CLI structured output

### 7.1 Shared adapter lifecycle

Each native CLI attempt follows this bounded lifecycle:

1. build the canonical schema in memory;
2. create exclusive process-local temporary resources;
3. construct provider-specific argv without shell interpolation;
4. run in the project root with the existing restricted execution mode;
5. enforce the remaining shared deadline;
6. require a zero exit status;
7. read only the provider's declared final structured result surface;
8. enforce the byte limit before JSON parsing;
9. parse and semantically validate;
10. delete every temporary resource in `finally`;
11. return only the validated plan and compact provenance.

No prompt, stdout, stderr, raw invalid JSON, full argv, environment dump,
absolute home path, credential, or native opaque session id is persisted.

### 7.2 Codex CLI

Use the installed native surface:

```text
codex exec
  --sandbox read-only
  --output-schema <exclusive-schema-file>
  --output-last-message <exclusive-result-file>
  --model <configured-model>
  -
```

The schema and result live in an exclusive temporary directory with
user-only permissions. The result must be a bounded regular file owned through
the attempt's exact path; symlink, non-regular file, replacement, or oversized
content fails closed. The final-message file, not mixed CLI status output, is
the JSON parse source. stdout/stderr remain process-local diagnostics and are
discarded.

The implementation must preserve current list-argv subprocess execution and
must not introduce `shell=True`.

### 7.3 Claude CLI

Use the same canonical schema through:

```text
claude --print --permission-mode plan
       --output-format json
       --json-schema <canonical-schema-json>
       --model <configured-model>
```

A provider-specific extractor accepts only the documented structured result
envelope. Missing, ambiguous, non-object, or oversized structured output fails
closed. Claude output is still subjected to the identical AgentDeck semantic
validator.

### 7.4 Capability and compatibility policy

Leader capability reporting distinguishes:

```text
native_json_schema
json_object_only
prompt_only
unsupported
```

Codex and Claude adapters may advertise `native_json_schema` only when their
adapter contract and local executable support the required flags. An
unsupported installed version is a visible setup/readiness blocker, not a
reason to silently return to prompt-only parsing.

OpenAI-compatible and ACP Leader paths keep their current behavior in this
slice unless a focused regression is required for shared request propagation.
No assumption is made that every OpenAI-compatible endpoint supports the same
`json_schema` dialect. ACP does not itself supply AgentDeck Mission semantics.

## 8. Bounded regeneration and diagnostics

### 8.1 Retry policy

At most two generation attempts are allowed:

```text
attempt 1: original task + frozen authority + native schema
attempt 2: same provider + same model + same authority + same schema
```

The second attempt regenerates the complete plan. AgentDeck does not patch,
truncate, extend, reorder, or guess the first result.

Automatic regeneration is limited to safe format/semantic classes:

```text
json_parse
schema
invalid_output_envelope
```

There is no retry after cancellation, timeout, nonzero exit, oversize,
readiness failure, or backend failure. Both attempts share one monotonic total
planning deadline; retry never doubles the user-visible timeout.

There is no provider, model, or transport switch. A retry is not a fallback.

### 8.2 Failure stages and diagnostic codes

Keep the existing public stage families, including:

```text
timeout
nonzero
json_parse
schema
cancelled
oversize
backend_blocked
backend_failure
```

Add a strict allowlist of non-sensitive diagnostic codes, such as:

```text
missing_required_field
invalid_top_level_type
invalid_step_count
invalid_step_numbering
unknown_agent
role_mismatch
approval_not_required
invalid_output_envelope
native_schema_unavailable
```

The exact final list is frozen in the implementation plan and tested for
closed membership. A diagnostic records only category, attempt count, and
constraint mode. It never records rejected values or exception text.

On terminal failure the conversation turn still commits one terminal
transition and compact audit event. It creates no plan, Mission, preview,
attempt, permission, Worker session, tmux input, or file effect.

### 8.3 Success provenance

Successful planning records compact provenance:

```text
provider
model
constraint_mode
schema_version
schema_hash
generation_attempt_count
regeneration_used
selected_agent_ids
step_count
```

This provenance is immutable evidence, not authorization. The implementation
must choose one source-of-truth record and project it consistently through plan
status and ProjectView rather than rebuilding it from current configuration.
Any ProjectView field change requires synchronized schema docs, examples, and
validators.

## 9. Deterministic four-stage acceptance

Before real CLI execution, add a deterministic production-path acceptance with
controlled Leader and Worker adapters.

The Mission is exactly:

| Step | Phase | Worker | Transport | Required result |
|---|---|---|---|---|
| 1 | implementation | Claude Worker | ACP | create initial disposable artifact |
| 2 | review | Codex Worker | tmux | return concrete revision guidance |
| 3 | revision | Claude Worker | ACP | apply predecessor guidance |
| 4 | acceptance | Codex Worker | tmux | read-only verify final artifact |

This alternation proves three transport-crossing handoffs:

```text
ACP → tmux → ACP → tmux
```

For every transition, AgentDeck must prove one succeeded predecessor attempt,
one validated correlated reply, one canonical compact handoff, exact frozen
Mission/step lineage, and prompt construction for only the immediate next
step. Workers never listen to or schedule one another.

The deterministic acceptance also proves:

- one frozen four-step preview and consume-once confirmation;
- disconnect after confirmation does not stop the daemon;
- reconnect observes the same Mission and pending decision;
- ACP permission pauses the current attempt and scheduler;
- permission preview/confirm wakes only the bound waiter;
- Codex tmux pane is visible but not protocol authority;
- takeover and return-control require exact preview/confirm;
- `human_owned` blocks automated input to the target Worker;
- return-control requires reconciliation before future dispatch;
- all attempts, receipts, handoffs, events, artifacts, and hashes agree;
- restart/recovery never duplicates an unknown external effect;
- teardown attempts every cleanup action even after an earlier cleanup error.

## 10. Real M2c Golden Mission

### 10.1 Environment and isolation

Run against one frozen implementation commit in a fresh disposable Git
project. Record sanitized versions for AgentDeck, Codex CLI, Claude CLI,
Claude Agent ACP, Python, and tmux. Preflight is read-only and must not install,
upgrade, authenticate, trust a directory, or edit global configuration.

All runtime state stays under the disposable project's ignored `.agentdeck/`.
No live Mission runs in the implementation checkout.

### 10.2 Natural-language creation

Start with bare:

```bash
agentdeck
```

Use ordinary language to request exactly four serial phases:
implementation and revision by the Claude ACP Worker, review and acceptance by
the Codex tmux Worker.

Before confirmation, assert:

- Codex CLI Leader used native schema mode;
- one plan and one pending Mission exist;
- there are exactly four steps and two selected Workers;
- the semantic order is implementation, review, revision, acceptance;
- every role and Worker matches project configuration;
- the plan hash, preview binding, expiry, and confirmation control agree;
- no Worker, permission, pane input, or artifact effect has occurred.

The human confirms that exact frozen Mission once. Later permission and
takeover confirmations authorize only their new bounded actions; they do not
reconfirm ordinary Mission steps.

### 10.3 Disconnect and permission recovery

After Mission admission, close the first interactive client while the project
daemon remains authoritative. The Claude ACP implementation step must reach at
least one real safe permission pause before its file effect is allowed.

Start a second bare client. Its ProjectView-derived recovery card must identify
the same Mission, current step/attempt, pending permission, blocker, and exact
next control. Permission handling uses the existing preview then confirm path.
`allow_once` cannot become durable `allow_always`. If the real agent requests
more than one safe permission, every request is individually previewed and
confirmed; the rehearsal never weakens policy merely to force one prompt.

Before confirmation, the scheduler cannot advance another Worker and the
target file effect must not exist. After confirmation, only the exact bound
ACP waiter may resume.

### 10.4 tmux visibility and takeover

The Codex review and acceptance Worker uses its configured project tmux socket,
session, and pane. ProjectView/workbench controls must lead to the exact pane.
The pane is observable, but completion remains the validated correlated reply
and durable receipt/handoff chain.

The preferred safe takeover window is:

```text
step 2 complete
step 3 Claude ACP waiting on permission
Codex Worker idle
step 4 not started
```

At that boundary:

1. create and inspect the Codex Worker takeover preview;
2. confirm the exact preview;
3. prove ownership becomes `human_owned`;
4. prove AgentDeck sends no automated input to that Worker;
5. perform observation only, with no unreported worktree effect;
6. create and inspect return-control preview;
7. confirm it;
8. reconcile session, pane, protocol, artifact, and worktree evidence;
9. prove ownership returns to `agentdeck_owned`;
10. permit step 4 only after return succeeds.

If no safe boundary exists, takeover is blocked honestly. The acceptance does
not force takeover during an active unknown effect.

### 10.5 Completion evidence

The real run passes only when all of the following agree.

#### Leader

- Codex native structured output was used;
- semantic validation passed without local intent repair;
- one plan and Mission were created;
- preview, confirmation, and execution share one plan hash;
- no provider/model/transport fallback occurred;
- no raw Leader output entered durable state.

#### Mission

- exactly four ordered phases ran;
- all four attempts succeeded;
- `current_step == step_count == 4`;
- Mission state is `completed`;
- no unselected Worker was spawned or prompted.

#### Communication

- Claude used real ACP;
- Codex used real tmux;
- all three compact handoffs have exact predecessor lineage;
- each next Worker started only after handoff durability;
- no Worker directly scheduled another Worker;
- no implicit ACP/tmux fallback occurred.

#### Governance and recovery

- at least one real permission pause was handled through exact preview/confirm;
- disconnect did not stop the confirmed Mission;
- reconnect returned to the same Mission;
- takeover and return-control completed at a safe boundary;
- `human_owned` blocked automated target input;
- no unknown effect was retried or duplicated.

#### State agreement

ProjectView, Mission status, workbench, ledger, events, trace, artifact summary,
execution snapshot, daemon admission, attempt receipts, handoff hashes, and the
actual disposable file hash must agree. The validation report may contain only
sanitized compact facts.

### 10.6 Cleanup

Cleanup is part of acceptance, not an optional afterthought. It must attempt,
even after an earlier cleanup error:

- exact daemon stop/force-stop as applicable;
- bounded process termination and wait;
- endpoint metadata and Unix socket reconciliation;
- project tmux socket/session removal;
- ACP adapter child cleanup;
- temporary schema/result cleanup;
- disposable project deletion;
- suffix-scoped residual process audit.

No global authentication, package installation, CLI settings, or trust state is
changed. Cleanup failure prevents PASS.

## 11. Test strategy

### 11.1 Schema unit tests

Add focused tests for deterministic schema generation, frozen Worker enums,
exact step count, stable schema hash, approval literal, invalid/partial
authority, role mismatch, unknown Worker, missing field, wrong type, duplicate
or gapped numbering, oversize, and absence of secrets/paths/transport commands
from schema and diagnostics.

### 11.2 CLI adapter tests

Cover exact Codex argv, schema/final-message temporary files, file permissions,
regular-file and identity checks, cleanup on every exit, model insertion,
Claude schema/envelope extraction, timeout, nonzero, invalid envelope, invalid
JSON, semantic mismatch, oversize, cancellation, and credential-bearing
stdout/stderr. Assert no `shell=True` and no persisted raw output.

Cover first-attempt format failure followed by success, two format failures,
no retry after non-retryable stages, shared deadline exhaustion, exact
provider/model preservation, and absence of fallback.

### 11.3 Gateway and conversation tests

Prove frozen authority propagation through every request/candidate boundary,
compact success provenance, allowlisted failure diagnostics, atomic terminal
failure, zero domain effects on failure, exact preview recovery, cancellation,
and reconnect without duplicate Leader invocation or preview.

### 11.4 Daemon and governance tests

Add the deterministic four-stage Mission, three handoffs, client disconnect,
permission pause, takeover/return-control, human-owned dispatch blocker,
reconciliation, restart ambiguity, no duplicate effect, ProjectView/contract
agreement, and collect-all cleanup regressions.

### 11.5 Regression gates

All development and verification runs in the `agentdeck` conda environment.
After the last semantic change, rerun focused provider/conversation suites,
focused daemon/governance/recovery suites, the complete test suite,
`python -m compileall src`, contract validation, and `git diff --check`.

The real live rehearsal is opt-in and cannot be replaced by mocks. A live
blocker is reported honestly even if every deterministic test passes.

## 12. Documentation and contract impact

Implementation must update, where affected:

- `HISTORY.md` in every semantic commit;
- `docs/handoff/current-development-state.md`;
- this M2c validation report and/or a dedicated live SOP/report;
- `docs/roadmap/product-north-star.md` delivery status only after evidence;
- `README.md` and `README.zh-CN.md` only for concise user-visible behavior;
- ProjectView, Leader backend, conversation, plan/status, doctor, and contract
  index docs/examples/validators if their public fields change.

The original M2 validation report must continue to distinguish the earlier
two-step transport PASS from the four-stage M2c result. Historical blocked
evidence is not erased when the new run passes.

## 13. Implementation slices and commit boundaries

The later TDD implementation plan should preserve these semantic boundaries:

1. **Canonical Leader plan schema** — frozen authority, schema generator,
   semantic diagnostics, focused tests, HISTORY.
2. **Native CLI structured output** — Codex/Claude adapters, secure temporary
   results, capability/readiness, focused tests, HISTORY.
3. **Bounded regeneration and provenance** — same-Leader retry, shared
   deadline, compact diagnostics/provenance, synchronized contracts, HISTORY.
4. **Deterministic four-stage M2c acceptance** — ACP/tmux alternation,
   disconnect, permission, takeover, three handoffs, cleanup, HISTORY.
5. **Real Golden Mission and evidence** — frozen-commit live run, validation
   report, handoff/north-star/readme status, final regression, HISTORY.

Each task follows RED → verify RED → minimal GREEN → focused regression →
broader regression → self-review → local commit. Unrelated user-owned worktree
changes are never staged. No push or merge occurs without separate human
instruction.

## 14. Completion gate and M3 boundary

M2c can be marked PASS only when all of these are true:

```text
canonical schema tests PASS
native Codex CLI Leader PASS
deterministic four-stage Mission PASS
real four-stage Mission PASS
real ACP Worker PASS
real tmux Worker visibility PASS
disconnect/reconnect PASS
permission preview/confirm PASS
takeover/return-control PASS
three compact handoffs PASS
ProjectView/ledger/events/artifact agreement PASS
full suite/compileall/contracts/diff PASS
cleanup and residual-process audit PASS
```

If any item fails, the result remains `M2c BLOCKED`; the report records the
fixed sanitized stage and proves what did not happen. A partial pass is not
promoted to product completion.

Only after this gate passes may the project begin a new M3
brainstorming → written spec → human review → TDD plan → `/goal` cycle.

## 15. 2026-07-15 live-blocker addendum: permission evidence authority

### 15.1 New verified baseline

Commit `ced9a50e` removed the final read-only preflight blocker. Its complete
non-live regression passed `3350` tests with `2` explicit skips. The frozen
real-tool preflight then returned `ready=true` and `blockers=[]` for Codex CLI
`0.131.0`, Claude Code `2.1.208`, Claude Agent ACP `0.58.1`, and tmux `3.6a`.

The one permitted real four-stage run on that frozen commit did not pass. It
timed out after 180 seconds waiting for the first durable ACP permission
request. At failure time the compact ledger contained one Mission, one plan,
one attempt, one Worker reply, one handoff, and zero permission requests. The
run also reported `tmux_cleanup_incomplete`; subsequent bounded cleanup left no
M2c daemon, tmux session, live process, disposable project, or staged tool
mirror. This is a new live blocker. It does not reopen `probe_wrote_files`, and
M2c remains blocked.

### 15.2 Problem boundary

The existing diagnostic reports collection cardinalities but cannot safely
distinguish these materially different causes:

1. the Leader plan retained phase names but lost the exact artifact mutation;
2. the Claude ACP Worker returned a handoff without requesting the governed
   file effect;
3. the Worker attempt failed or remained active before permission creation;
4. permission was durably created but the live wait predicate observed
   inconsistent state.

No fix may infer approval from Worker prose, fabricate a permission request,
relax the permission gate, inject a test-only authorization path, or retain raw
Leader/Worker output. The next run must remain a real product-path acceptance.

### 15.3 Admission-time task authority

Before Mission confirmation and daemon admission, the live harness must verify
that the native-schema Leader preview preserves the complete fixed task
authority, not merely the four phase labels and Worker order:

- phases are exactly `implementation`, `review`, `revision`, `acceptance`;
- Workers are exactly Claude, Codex, Claude, Codex in that order;
- every step references `artifact.txt`;
- implementation retains `draft-v1`;
- review, revision, and acceptance retain `accepted-v2`;
- revision retains both `draft-v1` and `accepted-v2`, proving that the requested
  transformation survived planning.

The checks are deterministic token-presence assertions over the fixed
disposable acceptance task. They do not attempt general natural-language
understanding and do not alter production Mission semantics. Failure stops
before confirmation with `native_schema_task_authority_invalid`; it creates no
Worker attempt, permission, tmux input, or artifact effect.

### 15.4 Transcript-free failure classification

`_live_failure()` may add one compact ledger diagnostic derived from durable
state. Its output is closed to these categories:

- Mission, step, attempt, reply, handoff, and permission states;
- stage position, configured Worker id, and configured transport;
- canonical handoff `status` only;
- allowlisted blocker, transport stage, and error code;
- task semantic-check booleans;
- counts and SHA-256 identities where correlation is required.

It must never include task text, plan goal/summary, handoff summary,
verification, risks, next steps, prompt text, PTY text, ACP update content,
provider output, credentials, opaque tokens, user home paths, or full absolute
paths. Model-provided strings may influence only allowlisted enums, booleans,
counts, and hashes.

The deterministic classifier must distinguish at least:

```text
leader_task_authority_missing
worker_effect_not_requested
worker_attempt_failed
worker_attempt_active
permission_state_inconsistent
```

`worker_effect_not_requested` is valid only when the first attempt is
successful, its correlated reply and handoff are durably complete, and no
permission request exists. The presence of any permission record, an active or
failed attempt, or incomplete reply/handoff evidence must select another
classification. Classification is diagnostic evidence only; it cannot advance
the scheduler or authorize an effect.

### 15.5 Deterministic tests and rerun gate

The implementation must first prove RED then GREEN for:

1. each missing `artifact.txt` / `draft-v1` / `accepted-v2` authority token;
2. admission stopping before daemon/Worker effects on semantic failure;
3. every permitted compact diagnostic field;
4. exclusion of model text, prompts, tokens, credentials, and absolute paths;
5. `worker_effect_not_requested` for succeeded reply/handoff with zero
   permissions;
6. non-misclassification when permission, active-attempt, or failed-attempt
   facts exist;
7. unchanged cleanup collection after primary failure.

After focused and complete regression, compileall, contract checks, and
`git diff --check`, freeze a new commit. Run the read-only real-tool preflight
again. Another real four-stage run is allowed only when the new frozen commit
returns `ready=true` and `blockers=[]`. There is no automatic retry. A failed
run records its new compact classification and keeps M2c blocked; only a full
PASS unlocks M3 brainstorming.
