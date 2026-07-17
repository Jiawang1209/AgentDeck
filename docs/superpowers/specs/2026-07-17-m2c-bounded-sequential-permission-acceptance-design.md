# M2c Bounded Sequential Permission Acceptance Design

**Date:** 2026-07-17
**Status:** Implemented; deterministic verification pending
**Milestone:** Phase 3 M2c real four-stage acceptance closure
**Scope:** M2c live-acceptance harness, closed diagnostics, and deterministic fixtures

## 1. Purpose

Frozen implementation `e83dcc482d2403f613485d06eff75ff99ffe733f`
passed deterministic verification, two complete suites, and its separately
authorized strict v6 preflight. Its separately authorized real four-stage
Mission ran exactly once and failed as `third_stage_safe_window_timeout`.
Closed evidence contained one step-1 Claude ACP attempt, two permission base
records, zero validated Worker replies, zero handoffs, and no later attempt.

The failure does not prove an AgentDeck product defect. AgentDeck already
supports multiple sequential ACP permissions in one attempt. Each permission
has independent binding, confirmation, transition, and consume-once effect
authority. Permission base records remain immutable `pending`; effective state
is derived from append-only protocol transitions. The live harness instead
assumed that confirming the first implementation permission would finish that
attempt and that the second permission would belong to revision. The real
Claude Worker legitimately requested a second permission inside the same
implementation attempt, so the harness waited for a third attempt without
confirming the current request and timed out.

This design replaces the exact-two-permissions assumption with an
attempt-scoped, bounded sequential-permission acceptance driver. Every
permission remains an independent human-governed decision. A Claude attempt may
request several permissions in transport order, but the next Mission stage
cannot start until the current attempt has completed and its validated reply
and canonical handoff are durable.

## 2. North-star alignment

The product north star promises a governed team of heterogeneous agents that
can continue after client disconnect while preserving exact authority,
lineage, recovery, and human takeover. This design preserves that promise:

- ACP remains the structured AgentDeck-to-Worker transport.
- AgentDeck, not the Worker or test harness, remains the governance authority.
- one permission confirmation authorizes one exact effect only;
- a later permission on the same attempt is new authority and pauses progress;
- durable Mission, attempt, reply, and handoff facts determine stage progress;
- tmux remains a visible observation and takeover surface, never protocol
  authority;
- diagnostics remain compact and redact prompts, transcripts, paths, targets,
  options, stderr, model output, environment values, and credentials;
- a real four-stage Golden Demo, not a fabricated fixture result, remains the
  gate for closing M2c and entering M3.

The bounds in this design belong only to the fixed M2c live acceptance. They
do not impose a product-level maximum on ACP permissions.

## 3. Alternatives

### 3.1 Attempt-scoped bounded permission driver

Chosen. The harness repeatedly discovers the current authoritative permission,
validates its exact lineage, runs the existing public preview/confirm path, and
then continues observing the same attempt. It stops only on a new permission,
an exact terminal condition, or durable completion plus handoff.

This reflects real ACP behavior while preserving explicit governance and a
finite acceptance run.

### 3.2 Prompt Claude to request exactly one permission per stage

Rejected. Provider, model, adapter, and task changes can alter tool selection.
A prompt restriction would make the test brittle and would not validate the
already-supported multi-permission product semantics.

### 3.3 Batch-approve later permissions in an already-confirmed attempt

Rejected. The first confirmation does not authorize unknown future effects.
Batch or automatic approval would violate the north-star rule that new
permission pauses the Mission and would weaken AgentDeck's governance edge.

## 4. Scope and architecture

The primary change is a test-side bounded permission driver in
`tests/test_m2c_live_acceptance.py`. It has three responsibilities:

1. bind observation to one exact Claude ACP Mission attempt;
2. discover, validate, preview, and explicitly confirm each sequential
   permission for that attempt;
3. stop only when the attempt terminalizes or its validated reply and canonical
   handoff are durable.

The driver must not:

- mutate `StateStore` directly;
- create protocol transitions or handoffs itself;
- call an internal bypass around governance preview/confirm;
- batch, pre-approve, infer, or replay permission authority;
- start or admit the next Mission stage;
- inspect raw ACP output, terminal content, prompts, stderr, or tool targets;
- increase an existing timeout or retry a live node.

The expected implementation remains harness-only. No `src/agentdeck/**` change
is permitted unless a deterministic RED proves that the product cannot express
the approved semantics. If that occurs, implementation pauses for explicit
human approval of the expanded scope.

## 5. Permission lineage and effective state

Every accepted permission must form one unambiguous chain:

```text
Mission
  -> Mission step
  -> Mission attempt
  -> ACP AgentSession
  -> ProtocolTurn
  -> permission transport update sequence
  -> PermissionRequest
  -> governance preview
  -> explicit confirmation
  -> permission state transition
  -> consume-once effect authority
```

Array position and global permission count are never stage identity. The driver
uses persisted Mission permission bindings plus exact session, turn, agent,
and transport-update facts to associate a permission with the current attempt.

Permission base `status` must remain `pending`. A pure, read-only projection
derives effective state from append-only transitions:

| base and transition history | effective state |
| --- | --- |
| `pending`, no transition | `pending` |
| one legal `pending -> approved` | `approved` |
| one legal `pending -> denied` | `denied` |
| one legal `pending -> expired` | `expired` |
| duplicate, conflicting, orphan, or illegal history | `invalid` |
| cross-Mission/attempt/session/turn binding | `lineage_invalid` |

On each observation, the driver selects the unique current-attempt permission
that is ordered by its permission transport update and whose effective state is
`pending`. Already approved permissions remain durable evidence but cannot be
confirmed twice and cannot suppress a later request. After effective-state
projection, more than one pending permission on the same attempt is invalid
even when transport sequences differ: sequential ACP authority requires the
earlier request to have a terminal decision before the later request becomes
current. Missing, duplicate, or contradictory transport ordering also fails
closed rather than falling back to list order.

Internal validation may use exact IDs and sequences. Durable closed failure
evidence may contain only allowlisted counts, states, booleans, step position,
and diagnostic codes; it does not retain raw identifiers or payloads.

## 6. Bounded permission driver

For one exact Claude attempt, the driver repeatedly evaluates one coherent
state snapshot and produces one of four outcomes:

1. **New pending permission.** Validate lineage, require a unique exact
   governance preview, explicitly confirm that preview, verify the exact legal
   transition, and observe the same attempt again.
2. **Attempt succeeded with durable completion.** Require one validated Worker
   reply and one canonical handoff for the current step, then return completion
   to the outer live scenario.
3. **Attempt terminal without valid completion.** Stop immediately with a
   closed diagnostic; do not wait for a generic timeout.
4. **No convergence.** Only when none of the first three outcomes becomes
   durable within the existing bound may the driver report a true wait timeout.

The driver treats every permission as a separate governance transaction.
"Drive" or "drain" never means auto-approve: the acceptance harness simulates
the already-authorized human interaction by invoking the same public
preview/confirm semantics once for each observed request.

Acceptance-only bounds are:

- at most four permissions in one Claude attempt;
- at most eight permissions across the full four-stage Mission;
- at least one real permission in implementation;
- at least one real permission in revision.

The fifth permission in one Claude attempt or ninth in the Mission fails
immediately as `permission_limit_exceeded`. No timeout extension, retry,
fallback, or silent limit increase is allowed.

## 7. Four-stage Mission flow

The fixed order remains:

```text
implementation -> review -> revision -> acceptance
```

### 7.1 Implementation: Claude ACP

The driver processes one to four sequential permissions for the exact step-1
attempt. Confirming a permission never implies attempt completion. Review may
start only after implementation succeeds and its validated reply and canonical
handoff are durable.

### 7.2 Review: Codex CLI

Review consumes the canonical implementation handoff, produces its own
validated reply, and persists its canonical handoff. The phase is not required
to exercise the ACP permission bridge. Revision must not start before the
review handoff exists.

### 7.3 Revision: Claude ACP

Revision consumes the canonical review handoff. Its first real pending
permission establishes the human-takeover safe window. While a human owns the
Worker, the driver must not confirm permissions. After explicit return-control,
the harness revalidates attempt, session, turn, and permission authority, then
the same bounded driver handles the current and any later permissions.

Revision requires one to four permissions. Acceptance may start only after the
revision attempt succeeds and its validated reply, required artifact evidence,
and canonical handoff are durable.

### 7.4 Acceptance: Codex CLI

Acceptance consumes the canonical revision handoff, verifies the final
artifact and complete lineage, and persists its validated reply and final
handoff. It is not required to exercise the ACP permission bridge.

Across all phases, the next attempt must be chronologically and causally later
than the previous durable handoff. Only one authoritative attempt may be active
for the current step.

## 8. Completion contract

A successful M2c live Mission requires all of the following:

- exactly one frozen Mission and its admitted plan;
- four succeeded attempts in implementation, review, revision, acceptance
  order;
- four validated Worker replies;
- four canonical handoffs;
- each later attempt admitted only after the previous handoff;
- implementation and revision each contain at least one independently
  previewed and confirmed ACP permission;
- between two and eight total permissions, with no more than four in either
  Claude attempt;
- every permission has valid exact lineage and at most one terminal decision;
- takeover and return-control complete at the revision safe window without
  authority drift;
- final artifact and acceptance evidence satisfy the existing semantic
  authority contract;
- all project, process, daemon, ACP, PTY, tmux, socket, worktree, and temporary
  resources are removed by bounded cleanup.

The prior requirement that the Mission contain exactly two permissions is
deleted. The requirement that both Claude stages exercise the real permission
bridge is retained and made explicit.

## 9. Closed failure diagnostics

The driver uses exact finite diagnostic codes:

| code | meaning |
| --- | --- |
| `permission_lineage_invalid` | permission binding disagrees with Mission, attempt, session, turn, or agent |
| `permission_order_ambiguous` | current pending permissions have no unique legal transport order |
| `permission_transition_invalid` | permission transition history is orphaned, duplicated, conflicting, or illegal |
| `permission_preview_invalid` | no unique preview binds the current permission and authority |
| `permission_confirmation_invalid` | confirmation does not consume the exact preview or authorize the exact permission |
| `permission_limit_exceeded` | per-attempt or whole-Mission acceptance bound is exceeded |
| `permission_bridge_missing` | a Claude phase terminalizes successfully without a real permission |
| `attempt_terminal_before_handoff` | attempt fails, cancels, interrupts, or becomes ambiguous without valid completion |
| `handoff_missing_after_attempt_success` | succeeded attempt does not converge to reply plus handoff within the existing bound |
| `next_stage_started_before_handoff` | later attempt exists before the previous canonical handoff |
| `takeover_authority_drift` | attempt/session/turn/permission authority changes across takeover |
| `permission_wait_timeout` | no new permission, terminal attempt, reply, or handoff becomes durable within the existing bound |

Permission-driver failures add one closed `permission_progress` projection
restricted to:

```text
diagnostic_code
step_position
attempt_state
attempt_permission_count
mission_permission_count
effective_permission_states
reply_count
handoff_count
```

The projection is nested beside the already-delivered top-level `stage`,
`code`, compact cardinalities, and closed terminal ledger. This slice does not
delete prior terminal-stage observability. The legacy ledger's
`permission_states` must use the same effective-state derivation whenever
lineage is valid; it must not present immutable base `pending` values as final
decisions.

It must not include exception messages, pytest captured output, prompts,
terminal text, stderr, model/CLI output, paths, tool names, targets, permission
options, raw protocol payloads, environment values, credentials, or auth state.
Cleanup failure is reported as an independent blocker and never overwrites the
original execution failure.

## 10. TDD requirements

RED must fail on the current harness because it waits for a hard-coded global
permission/attempt cardinality instead of continuing the first Claude attempt.
Deterministic coverage must include:

1. two sequential implementation permissions remain in one attempt;
2. confirming the first permission does not admit review;
3. confirming the second still requires validated reply and handoff;
4. implementation and revision use different valid permission counts;
5. every permission has independent preview, confirmation, transition, and
   effect lineage;
6. base `pending` plus an approved transition projects effective `approved`;
7. an already handled permission is never confirmed twice;
8. ambiguous transport ordering fails closed;
9. a fifth per-attempt permission and ninth Mission permission fail at the
   exact acceptance boundary;
10. successful Claude completion without permission fails as
    `permission_bridge_missing`;
11. a later attempt before the previous handoff fails immediately;
12. takeover suppresses confirmation and return-control revalidates authority;
13. terminal and genuine timeout outcomes remain distinct;
14. malformed collections, lineage drift, and transition conflicts fail
    closed;
15. diagnostic and cleanup paths exclude secret prompt/path/output sentinels;
16. both fake four-stage acceptance and the opt-in real node use the same
    completion invariants;
17. real preflight and live nodes remain opt-in skipped in deterministic and
    full-suite runs.

Existing tests that encode exactly one permission in each Claude phase must be
rewritten to test the minimum-per-phase invariant and bounded totals, not merely
deleted.

## 11. Verification and commit sequence

Implementation follows a separate writing-plans document after written-spec
approval. The required order is:

1. commit the deterministic RED separately;
2. implement the smallest harness-only GREEN;
3. run focused sequential-permission, handoff, takeover, terminal, diagnostic,
   and leakage tests;
4. run complete non-live M2c;
5. run existing product regressions;
6. run compile, `git diff --check`, product-source scope, tracked-runtime-state,
   process, daemon, ACP, tmux, worktree, socket, and temporary-root audits;
7. update `HISTORY.md`, current handoff, live SOP, and validation evidence;
8. freeze a new git SHA;
9. run two fresh complete suites serially in separate detached worktrees under
   the `agentdeck` conda environment;
10. audit installed tool inputs without mutating them;
11. request a new exact read-only preflight authorization naming frozen SHA,
    Leader model, and authority digest;
12. if and only if preflight reports `ready=true`, empty blockers, and empty
    failures, request a separate one-shot live authorization;
13. run the real four-stage Mission exactly once;
14. close M2c only on real PASS, then begin M3 brainstorming.

Every user-visible or documentation change includes the matching `HISTORY.md`
entry in the same local commit. No merge or push is authorized by this design.

## 12. Authority lifecycle

The `e83dcc48...`/`gpt-5.5`/authority-v3 preflight and live authorities are
consumed and non-retryable. The existing failure evidence remains immutable.

Any implementation edit creates a new candidate and invalidates all earlier
verification as freeze evidence. A new preflight and live authorization must
name the new frozen SHA, exact Leader model, and reconstructed authority digest.
Preflight and live remain separate human decisions. Neither one implies the
other, and neither may be automatically retried.

## 13. Non-goals

- imposing a product-wide permission count limit;
- changing ACP, daemon, scheduler, StateStore, or ProjectView semantics without
  a proven and separately approved product defect;
- batch approval, wildcard authority, or automatic confirmation;
- prompt engineering to force a specific permission count;
- timeout inflation, sleep-based synchronization, retry, or provider fallback;
- retaining raw transcripts, prompts, terminal content, stderr, paths, or tool
  payloads;
- weakening takeover, return-control, handoff, semantic authority, cleanup, or
  one-shot live gates;
- beginning M3 before a real M2c PASS.

## 14. Self-review

- The design distinguishes immutable permission base status from effective
  transition-derived state.
- The 4-per-attempt and 8-per-Mission bounds are explicitly acceptance-only.
- Every permission remains an independent explicit governance transaction.
- Stage progression is gated by validated reply and canonical handoff, not
  permission count or elapsed time.
- Takeover temporarily prevents harness confirmation and return-control
  revalidates exact authority.
- The failure vocabulary is finite and its projection excludes raw content.
- The exact eight-field permission projection preserves, rather than replaces,
  previously delivered closed terminal-stage diagnostics.
- Product-source changes require new deterministic evidence and explicit human
  scope approval.
- No placeholder, retry path, silent fallback, merge, push, preflight, live
  execution, or M3 work is authorized by this spec.

## 15. Implementation status

Implementation Tasks 1–8 are complete on the isolated feature branch. The
result remains harness-only: `tests/test_m2c_live_acceptance.py` contains the
effective-state projection, exact permission lineage, bounded same-attempt
driver, public preview/confirm binding, closed `permission_progress`, takeover
authority check, and shared four-stage completion validator. No
`src/agentdeck/**` file changed.

Focused lineage, driver, confirmation/diagnostic, and four-stage integration
tests are GREEN; the latest integrated selection is `27 passed, 345 deselected
in 2.22s`. This is not freeze evidence. Complete non-live M2c, product
regressions, compile/diff/leakage/residue audits, requirement review, and an
exact implementation freeze remain Task 10. M2c is still **BLOCKED**, M3 is
still locked, and no preflight/live authority exists for this candidate.
