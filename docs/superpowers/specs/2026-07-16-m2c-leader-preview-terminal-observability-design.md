# M2c Leader Preview Terminal Observability Design

**Date:** 2026-07-16
**Status:** Design direction approved; written specification awaiting human review
**Milestone:** Phase 3 M2c live-acceptance blocker closure
**Depends on:** frozen Task 14 evidence commit `04fb8eed` and live authority commit `954b868c`
**North star:** `docs/roadmap/product-north-star.md`

## 1. First principle

AgentDeck is the authoritative multi-agent control plane. A Leader model, its
CLI process, ACP, tmux, a PTY, or an MCP server may provide execution context
and evidence, but none of them defines Mission truth.

The M2c live harness must therefore report the first durable, validated fact
that explains why a Mission Preview did not appear. A persisted Leader turn
terminal is stronger evidence than a later outer polling deadline. A PTY exit
is stronger evidence than a generic timeout when no durable terminal exists.
Raw terminal output is never promoted into authority or retained as a
diagnostic transcript.

This follows the north-star rules that all facts enter one ledger, terminal
state is an observation surface rather than protocol authority, context is not
authority, and product claims require reproducible evidence.

## 2. Current evidence and problem statement

Frozen commit `954b868c` passed two independent full suites and one designated
read-only preflight with `ready=true` and `blockers=[]`. After separate human
authorization, its one permitted Task 14 live run stopped before Mission
Preview creation with:

```text
stage=live_acceptance
code=mission_preview_timeout
plans=0
missions=0
```

The run did not reach confirmation, daemon admission, any Worker, ACP, tmux
dispatch, permission, handoff, artifact, reconnect, or takeover behavior. It
was not retried.

The current live harness waits up to 180 seconds for a Preview predicate. The
configured Codex CLI Leader provider has a 120-second planning budget. The
production conversation path already persists a closed
`conversation_turn_terminal` event and a matching turn transition when the
Leader ends as `failed` or `cancelled`, including only:

```text
stage diagnostic_code attempt_count constraint_mode
```

The harness does not inspect that durable terminal while waiting. It also does
not continuously drain and poll the bare PTY process during the Preview wait.
Consequently a real Leader timeout, nonzero exit, schema failure, cancellation,
oversize result, or backend failure can be hidden behind the later generic
`mission_preview_timeout` label.

The harness also writes `model = "gpt-5.4"` directly into the disposable
project config. The model is not an explicit Task 14 input and is absent from
the preflight contract. This permits the rehearsal model to drift from the
model reviewed during prior evidence or human authorization.

The existing evidence does **not** establish that MCP startup caused the
blocked run, nor does it establish that the model, network, authentication, or
schema was the cause. Those are possible second-layer causes. This slice first
makes the terminal cause observable without leaking provider output.

## 3. Goals

This slice must:

1. add a dedicated `_wait_for_mission_preview()` to the opt-in M2c harness;
2. return immediately when the exact Mission Preview becomes durable;
3. return immediately when the newly created Leader turn becomes durably
   `failed` or `cancelled`;
4. preserve the real closed Leader failure type instead of replacing it with a
   generic Preview timeout;
5. keep all Leader terminal metadata inside the existing production
   allowlists;
6. continuously perform bounded PTY draining, process polling, and one final
   bounded reconciliation read before classifying process exit or timeout;
7. retain only PTY byte count, truncation flag, and SHA-256 identity evidence;
8. make the Codex Leader model an explicit, validated, frozen Task 14 input;
9. expose that validated model in the read-only preflight result and final
   acceptance evidence;
10. reject a missing, invalid, or internally drifted model before any Leader
    provider execution, with no default or fallback model;
11. prove the behavior with deterministic RED/GREEN tests, including simulated
    time and fake processes rather than real 120-second waits;
12. complete two unchanged-SHA full suites and one new read-only preflight
    before stopping for a separately authorized future live run.

## 4. Non-goals

This slice does not:

- increase the Codex Leader timeout;
- disable, remove, or reconfigure MCP servers;
- add a retry, alternate model, alternate provider, or silent fallback;
- change global Codex, Claude, npm, tmux, login, authentication, or permission
  settings;
- persist terminal text, prompts, stdout, stderr, raw JSON, model responses,
  argv, environment values, home paths, or executable paths;
- change production Mission planning, ConversationSession semantics, provider
  budgets, ACP transport, tmux transport, daemon scheduling, permission gates,
  ProjectView, or Worker behavior unless deterministic tests demonstrate that
  an existing production contract is broken;
- run another live Mission as part of implementation or verification;
- claim M2c PASS or unlock M3.

Any second-layer change for MCP startup, model availability, network latency,
authentication, native schema output, subprocess recovery, or timeout policy
requires evidence from the new closed diagnostic and a separately reviewed
change if it alters behavior outside this harness.

## 5. Considered approaches

### 5.1 Increase the Preview wait

Raising 180 seconds to a larger number might conceal the symptom on a fast
future run, but it would still replace a durable 120-second Leader terminal
with an unrelated outer timeout. It also provides no information about
nonzero, schema, cancelled, or oversize failures. Rejected.

### 5.2 Persist raw PTY or provider output

Retaining the terminal body, prompt, stdout, stderr, or model result would make
debugging easier, but it violates the transcript-free acceptance boundary and
may retain credentials, paths, source content, or private reasoning. Rejected.

### 5.3 Add a separate real Leader smoke call before Task 14

A provider smoke call would spend a second real call, would not prove that the
actual bare-conversation path is observable, and could introduce a new
preflight mutation or authorization surface. Deferred. The preflight remains
read-only and provider-free.

### 5.4 Durable-state-aware Preview wait plus frozen model input

Observe the exact new conversation turn and its logical terminal event while
draining and polling the existing PTY. Project only closed diagnostics. Require
one explicit model identity and use that same immutable value for preflight,
config generation, config verification, and evidence. Selected.

## 6. Dedicated Mission Preview wait

### 6.1 Scope

The new helper replaces only the first Preview wait inside
`_create_and_confirm_live_mission()`. The existing generic `_wait_for_state()`
continues to serve later permission, attempt, and Mission-completion waits.
This keeps the correction at the exact failed gate.

Conceptually:

```python
_wait_for_mission_preview(
    store,
    process,
    master,
    capture,
    *,
    baseline_turn_ids,
    timeout_seconds=180,
) -> dict[str, object]
```

Immediately before the request is written to the PTY, the harness loads and
validates the existing conversation turn collection and freezes its turn ids.
Only a turn created after this baseline may explain the new request. Historical
terminal events cannot satisfy the wait.

### 6.2 Polling order

Each bounded iteration performs these operations in order:

1. drain the PTY with the existing byte, chunk, duration, and overall-deadline
   bounds;
2. load one state snapshot;
3. validate the Preview predicate and the new-turn lifecycle from that
   snapshot;
4. reconcile journal and current `conversation_event_outbox` terminal evidence
   by exact `event_id` and value-identical record;
5. fail closed if Preview and a failed/cancelled terminal coexist;
6. return the state snapshot if the exact Preview is durable;
7. raise the mapped closed Leader failure if the exact new turn is durably
   failed or cancelled;
8. poll the PTY process;
9. sleep for a bounded interval only if no terminal condition is present.

The journal/outbox reconciliation preserves the existing atomic conversation
mutation contract. An identical logical event in both sources is one event. A
duplicate identity with different content, multiple terminal events for the
same new turn, a missing terminal event for a terminal transition, or a
transition/event disagreement fails closed as terminal evidence corruption.
The harness does not guess a stage from transition reason text.

When a terminal is selected, the failure builder uses the already observed
state snapshot for cardinalities and the closed ledger. It must not call
`store.load()` a second time and accidentally combine terminal metadata from
one generation with cardinalities from another. The implementation may add a
mutually exclusive `state_snapshot` input to `_live_failure()` or use an
equivalent pure projection helper; arbitrary caller-supplied mappings are
validated before use and are never stringified on failure.

If the process exits, the helper performs one final bounded PTY drain and one
final state/event reconciliation before classifying the exit. This last pass
allows an already committed terminal or Preview to win over a nearly
simultaneous process exit. If neither is present, the failure is
`bare_pty_exited_before_preview`.

At the overall deadline, the helper likewise performs one final bounded drain
and reconciliation. `mission_preview_timeout` is legal only when all of these
are true:

- no valid Preview exists;
- no durable failed/cancelled terminal exists;
- no invalid or conflicting terminal evidence exists;
- the PTY process has not exited.

This makes `mission_preview_timeout` a genuine absence-of-durable-terminal
classification rather than a catch-all.

### 6.3 New-turn authority

The new turn boundary is fail-closed:

- zero new turns may remain in progress until the deadline;
- exactly one new turn may progress through the request lifecycle;
- more than one new turn is `leader_terminal_evidence_invalid`;
- the new turn id must be a non-empty opaque id used only for internal equality;
- no conversation id, turn id, event id, timestamp, path, prompt, or message
  body enters the diagnostic;
- lifecycle validation reuses the production conversation-history validator
  rather than interpreting arbitrary transition text.

### 6.4 Preview/terminal race

A state containing both a valid Preview and a failed/cancelled terminal for the
same new turn is inconsistent. It does not pass as a Preview and does not pick
one fact by polling order. It fails closed as
`leader_preview_terminal_conflict`, with no raw state included.

## 7. Closed terminal diagnostic contract

### 7.1 Allowed production facts

The harness accepts only the production `LEADER_FAILURE_STAGES`,
`LEADER_PLAN_DIAGNOSTIC_CODES`, and `LEADER_CONSTRAINT_MODES`, with the same
stage/code combination rules already enforced by `LeaderGatewayError`.
`attempt_count` must be an exact integer from 0 through 2. The terminal state
must be exactly `failed` or `cancelled`; `cancelled` must pair with stage
`cancelled`, and `failed` must not.

The projected terminal object has exactly four fields:

```json
{
  "stage": "timeout",
  "diagnostic_code": null,
  "attempt_count": 1,
  "constraint_mode": "native_json_schema"
}
```

No unknown field is copied. No raw value is stringified on validation failure.
Malformed evidence produces only the fixed code
`leader_terminal_evidence_invalid`.

### 7.2 Stable live failure codes

Valid terminal stages map one-to-one to fixed live codes:

| Durable stage | Live failure code |
| --- | --- |
| `cancelled` | `leader_cancelled_before_preview` |
| `backend_blocked` | `leader_backend_blocked_before_preview` |
| `backend_failure` | `leader_backend_failure_before_preview` |
| `timeout` | `leader_timeout_before_preview` |
| `nonzero` | `leader_nonzero_before_preview` |
| `json_parse` | `leader_json_parse_before_preview` |
| `schema` | `leader_schema_before_preview` |
| `oversize` | `leader_oversize_before_preview` |
| `acp_incomplete` | `leader_acp_incomplete_before_preview` |
| `acp_permission` | `leader_acp_permission_before_preview` |
| `acp_empty` | `leader_acp_empty_before_preview` |
| `acp_failure` | `leader_acp_failure_before_preview` |

The `_LiveHarnessFailure` JSON may retain the existing fixed
`stage=live_acceptance`, cardinalities, closed ledger, and PTY digest. For this
path it adds `leader_terminal`, whose value is exactly the four-field object
above. It must never add the terminal event, transition, prompt, response,
stderr, stdout, capture tail, path, argv, environment, exception string, or
model output.

### 7.3 PTY evidence

The existing `_PtyTail` remains in-memory and capped at 64 KiB. Continuous
draining prevents a full PTY buffer from hiding process completion. Failure
projection continues to expose only:

```text
byte_count truncated sha256
```

The hash is byte identity evidence only. It is never interpreted as terminal
meaning and the tail bytes are never printed or persisted.

## 8. Explicit frozen Leader model

### 8.1 Input and validation

Task 14 gains one required input:

```text
AGENTDECK_M2C_LEADER_MODEL
```

There is no default. The value must be an exact string matching a conservative
model-id grammar:

```text
[A-Za-z0-9][A-Za-z0-9._:-]{0,95}
```

This admits normal Codex model ids while rejecting whitespace, shell syntax,
slashes, paths, control characters, and oversized values. Invalid raw input is
never echoed. Missing and invalid inputs produce fixed preflight blockers:

```text
leader_model_missing
leader_model_invalid
```

### 8.2 Seal and use

Before creating the disposable live parent, the harness reads the environment
once and constructs an immutable `_LeaderModelSeal`. The same sealed value is
passed explicitly to:

```text
read-only preflight
-> disposable config writer
-> post-write config verification
-> pre-Leader admission check
-> final live evidence
```

`_write_live_config()` no longer contains any model literal and cannot read the
environment. It requires the seal and writes its exact validated model. The
config is loaded back through the production config loader before the bare PTY
starts. Any mismatch among the seal, preflight value, and loaded Leader config
produces `leader_model_drift` and stops before provider execution. It never
chooses another model.

The portable preflight remains read-only and does not call a provider to prove
model availability. It can validate and record identity, not network/model
service health.

### 8.3 Preflight contract

Because the preflight payload has an exact shape, adding model authority bumps
the harness-local schema from `m2c-live-preflight/v1` to
`m2c-live-preflight/v2`. It adds exactly:

```json
{
  "leader_model": {
    "provider": "codex-cli",
    "model": "gpt-5.5",
    "source": "explicit",
    "ready": true
  }
}
```

For a missing or invalid input, `model` is `null`, `ready` is `false`, and only
the fixed blocker explains the reason. Tool entries and all other v1 fields
remain unchanged. `ready` is true only when the four tools and the model input
are all ready and no blocker exists.

The preflight validator rejects extra fields, an invalid model grammar, an
inconsistent `ready` value, an unknown blocker, or provider/source drift.

Two separate pytest processes cannot infer a previous human authorization
from ambient state. Therefore the designated preflight evidence and the later
single-live authorization must explicitly bind the same tuple:

```text
frozen commit + leader model id + four sanitized tool versions
```

A different model id requires a new read-only preflight and a new explicit
live authorization. Within one live process, the immutable seal and config
verification enforce drift automatically.

### 8.4 SOP command

Both the designated preflight and any future separately authorized live
command must explicitly include, for example:

```bash
AGENTDECK_M2C_LEADER_MODEL="<audited-model-id>"
```

The SOP must not recommend a particular changing model as an implicit default.
The human selects and audits the exact value at rehearsal time.

## 9. Deterministic TDD requirements

Implementation begins with RED tests. No real provider, network, ACP adapter,
or tmux server is used by these tests.

### 9.1 Preview wait tests

At minimum, tests must prove:

1. a durable Leader `timeout` at simulated time below 120 seconds immediately
   raises `leader_timeout_before_preview`, without advancing to the 180-second
   Preview deadline;
2. `nonzero`, `schema`, `cancelled`, and `oversize` retain their distinct
   stable live codes and exact four-field terminal projection;
3. `json_parse` and schema diagnostic codes preserve only valid allowlisted
   values;
4. prompt text, a secret sentinel, an absolute path, raw CLI output, stderr,
   terminal bytes, event ids, turn ids, and exception text are absent from the
   rendered failure;
5. an invalid stage, code, attempt count, constraint mode, state/stage pair,
   terminal event shape, duplicate/conflicting event, or multi-turn result
   produces only `leader_terminal_evidence_invalid`;
6. a simultaneous Preview and failed/cancelled terminal produces only
   `leader_preview_terminal_conflict`;
7. a process exit performs the final drain/reconciliation and reports a
   just-durable Preview or terminal when present;
8. a process exit with neither fact reports
   `bare_pty_exited_before_preview`;
9. only a live process with no Preview and no durable/invalid terminal through
   the simulated deadline reports `mission_preview_timeout`;
10. continuous PTY data respects byte, chunk, duration, and total-deadline
    bounds while still permitting process/terminal observation;
11. historical turns and terminal events cannot satisfy the new request.

Time-sensitive cases use injected monotonic/sleep functions or an equivalent
deterministic clock. No test sleeps for 120 or 180 real seconds.

### 9.2 Model tests

At minimum, tests must prove:

1. missing model input returns `leader_model_missing`, `ready=false`, and no
   project/provider effect;
2. invalid, path-like, control-character, whitespace, and oversized inputs
   return `leader_model_invalid` without echoing the raw value;
3. a valid explicit model appears exactly in preflight v2 and the generated
   config;
4. the config writer has no hardcoded `gpt-5.4` or fallback model;
5. a mutated config or mismatched seal returns `leader_model_drift` before the
   PTY/provider starts;
6. live PASS evidence records the same model identity;
7. preflight remains byte-read-only across the project and isolated probe
   roots.

### 9.3 Regression boundary

The existing complete non-live M2c harness remains portable with the live node
skipped. Production Leader diagnostics tests continue to prove the durable
terminal contract. No test may relax executable sealing, read-only probe
isolation, cleanup authority, semantic Mission authority, confirmation,
permission, handoff, or transcript-free evidence.

## 10. Documentation and evidence updates

The implementation commit must update, in the same commit:

- `tests/test_m2c_live_acceptance.py`;
- `docs/validation/phase3-m2c-live-acceptance-sop.md`;
- `docs/validation/2026-07-13-phase3-m2-project-daemon.md`;
- `docs/handoff/current-development-state.md`;
- `HISTORY.md`;
- the reviewed implementation plan.

The validation report must distinguish:

- the historical single Task 14 `mission_preview_timeout` evidence;
- the new deterministic observability/model tests;
- the new frozen commit and two full-suite results;
- the single read-only preflight result and explicit model id;
- the fact that no new live attempt has yet occurred.

It must not retroactively relabel the historical blocker as timeout, schema,
MCP, network, or model failure.

## 11. Verification and execution gate

After RED/GREEN implementation and review corrections, verification occurs in
this strict order inside the `agentdeck` conda environment:

1. focused Preview-observability and model tests;
2. complete non-live `tests/test_m2c_live_acceptance.py` with exactly one live
   skip;
3. relevant production conversation/Leader diagnostic regressions;
4. `python -m compileall -q src tests`;
5. `git diff --check` and repository scope/status review;
6. documentation/handoff/history consistency review;
7. one local implementation commit;
8. freeze the new commit SHA;
9. run two independent full suites on that unchanged SHA;
10. run exactly one designated real-tool read-only preflight with an explicit
    audited model id;
11. require `ready=true`, `blockers=[]`, the expected model id, four ready
    tools, unchanged SHA, and zero residual resources;
12. stop and request separate human authorization for one future live run.

Steps 9 and 10 do not authorize step 12. Implementation must not run
`test_real_four_stage_m2c_acceptance`, set `AGENTDECK_M2C_LIVE=1`, or infer
authorization from a passing preflight.

If the preflight blocks, the result is recorded honestly and no live run is
requested. If it passes, M2c still remains **BLOCKED** until a separately
authorized real implementation -> review -> revision -> acceptance Mission
passes with cleanup evidence.

## 12. How the next real diagnostic will be used

This slice deliberately does not guess the second-layer repair. A future
single-live result routes work as follows:

- `leader_timeout_before_preview`: verify selected model availability, network
  response, MCP startup contribution, and the existing 120-second provider
  budget before proposing any timeout or MCP change;
- `leader_nonzero_before_preview`: inspect only safe model/login/CLI startup
  facts through a separately designed diagnostic path;
- `leader_json_parse_before_preview` or `leader_schema_before_preview`: repair
  structured Mission output or semantic authority without weakening local
  validation;
- `leader_cancelled_before_preview`: determine the validated cancellation
  source;
- `leader_oversize_before_preview`: inspect bounded output/constraint behavior;
- `bare_pty_exited_before_preview`: investigate ConversationShell or Codex
  subprocess lifecycle and cleanup;
- genuine `mission_preview_timeout`: investigate a live but non-terminal
  conversation path, event persistence, or deadlock;
- invalid/conflicting terminal evidence: repair durable state consistency
  before any provider tuning.

Every path requires a minimal evidence-driven change. None authorizes silent
fallback, raw transcript retention, or automatic live retry.

## 13. Completion criteria

This observability slice is complete when all of the following are true:

- the written spec and detailed TDD plan are reviewed;
- the specialized Preview wait reports durable Leader terminals immediately;
- the four-field terminal projection is allowlisted and transcript-free;
- process exit and genuine timeout are distinct;
- the model is explicit, frozen, recorded, and has no fallback;
- deterministic tests and the complete non-live M2c harness pass;
- two full suites pass on one unchanged frozen commit;
- one new read-only preflight reports an exact model and honest readiness;
- documentation agrees that no new live Mission has run;
- the process stops for separate human live authorization.

This slice alone does not complete M2c. M2c closes only after the later,
separately authorized four-stage real Mission passes every existing
implementation, review, revision, acceptance, permission, disconnect,
reconnect, takeover, handoff, audit, artifact, and cleanup gate.
