# M2c Preview Consumption Convergence Design

**Date:** 2026-07-17
**Status:** Approved for implementation under the active completion goal
**Milestone:** Phase 3 M2c real four-stage acceptance closure
**Scope:** Real M2c acceptance harness ordering only

## 1. Purpose

The one authorized real four-stage Mission on frozen implementation
`284d8f62a9121a0d0351938aee1f716b3ebd198e` reached a valid Mission Preview and
daemon admission, then the harness reported
`mission_preview_not_consumed_exactly_once`. There were no Worker attempts,
permissions, replies, handoffs, or filesystem effects.

Static tracing shows an observation race rather than a product mutation bug.
`ConversationSession._confirm_preview()` calls the synchronous
`preview_executor` first. Daemon admission can become durable inside that call.
Only after it returns does the ConversationSession atomically append
`conversation_preview_consumed`, return the response, and render the next bare
`agentdeck> ` prompt. The live harness currently observes admission and reads
the event ledger immediately, so it can stop the PTY between admission and the
conversation commit.

This design makes the live harness wait for the existing terminal completion
boundary before checking exact-once consumption. It does not change product
code or weaken exact cardinality.

## 2. North-star alignment

- AgentDeck remains the authoritative orchestrator; the harness observes its
  durable conversation and daemon boundaries instead of guessing completion.
- Mission confirmation remains explicit, exact-preview-bound, auditable, and
  single-consumption.
- tmux/PTY visibility stays available without retaining prompt, transcript,
  stderr, path, or model output.
- durable state remains the semantic authority; the terminal prompt is used
  only as the bounded completion barrier for the synchronous conversation
  turn.
- no retry, sleep-based success, state fabrication, provider fallback, install,
  login, global mutation, merge, or push is introduced.

No production `src/agentdeck/**` behavior changes.

## 3. Alternatives

### 3.1 Sleep after daemon admission

Rejected. A fixed delay is nondeterministic, makes slow machines flaky, and
does not prove the confirmation turn completed.

### 3.2 Poll only for the consumed event

Rejected as the sole boundary. It would prove the event exists, but could tear
down the PTY before the confirmation response and prompt are rendered. The live
exercise should prove the complete interactive turn remains usable.

### 3.3 Wait for the third prompt, then verify the event exactly once

Chosen. The third prompt cannot be rendered until `handle()` returns from
`_confirm_preview()`. That return happens after the preview executor and the
atomic `conversation_preview_consumed` commit. Exact ledger cardinality is then
checked separately, so duplicate or missing events still fail closed.

## 4. Deterministic boundary

The live sequence is:

1. prompt 1 proves the bare shell is ready;
2. the natural-language Mission request produces a Preview;
3. prompt 2 proves the Preview response turn completed;
4. the harness writes the exact confirmation phrase;
5. daemon admission proves the Mission was accepted by the daemon;
6. prompt 3 proves the confirmation conversation turn completed;
7. the harness reads the ledger and requires exactly one matching
   `conversation_preview_consumed` event for the Mission;
8. only then may the live four-stage Worker wait begin.

`_wait_for_pty_prompt()` already provides a bounded deadline, bounded PTY tail,
process-exit detection, and closed diagnostic codes. The implementation reuses
it rather than creating another polling loop.

## 5. Failure semantics

- no prompt 3 before the existing bound:
  `bare_pty_prompt_timeout`;
- PTY process exits first: `bare_pty_exited`;
- prompt 3 exists but the matching event count is zero or greater than one:
  `mission_preview_not_consumed_exactly_once`;
- daemon admission still uses `mission_admission_timeout`;
- failures continue to expose only allowlisted structured ledger facts and the
  bounded PTY byte count, truncation flag, and SHA-256 digest.

No raw PTY tail, prompt, request, path, stderr, or provider output enters the
diagnostic.

## 6. TDD requirements

RED must reproduce the real ordering: daemon admission is already visible,
while the matching consumed event becomes visible only when the simulated third
prompt is reached. The current immediate ledger read must fail.

GREEN must cover:

1. prompt calls occur in exact order `[1, 2, 3]`;
2. event inspection happens after prompt 3;
3. one matching event succeeds;
4. zero and duplicate matching events fail with the existing closed code;
5. an event for another Mission does not satisfy the check;
6. prompt timeout/exit codes remain unchanged and transcript-free;
7. no production source changes;
8. designated preflight and real live nodes remain opt-in skipped during all
   deterministic and full-suite verification.

## 7. Authority and versioning

Tool/package authority semantics do not change. The next candidate continues to
use `m2c-tool-authority/v3` and strict preflight `m2c-live-preflight/v5`.

The new authorization must bind a new frozen git SHA, Leader `gpt-5.5`, and the
fresh v5 preflight digest. The exhausted SHA/model/digest authority must never
be retried.

## 8. Freeze and real sequence

1. Commit this design and a detailed TDD plan.
2. Add the deterministic RED before changing the harness ordering.
3. Make the minimum harness-only GREEN change.
4. Run focused, complete non-live M2c, and product regression sets.
5. Freeze a new implementation SHA and run two complete suites in fresh
   detached worktrees with absolute `PYTHONPATH`.
6. Re-audit the exact installed tools and package without changing them.
7. Run exactly one new real v5 read-only preflight on the frozen SHA.
8. Only on `ready=true`, empty blockers/failures, and authority v3, run exactly
   one same-SHA/model/digest real four-stage Mission.
9. Close M2c only on complete live PASS and zero residue. Otherwise record the
   one-shot result and return to a new evidence-driven repair cycle.

## 9. Completion criteria

- the deterministic race test is RED before the fix and GREEN after it;
- admission cannot be mistaken for conversation-turn completion;
- missing/duplicate preview consumption still fails closed;
- two independent complete suites pass on one frozen SHA;
- the new real preflight passes;
- implementation, review, revision, and acceptance all pass through the real
  ACP/tmux workflow;
- evidence, cleanup, handoff, HISTORY, and both roadmaps close M2c before M3 is
  unlocked.

## 10. Self-review

- The design fixes the observed ordering defect, not the observed symptom.
- The third prompt is causally downstream of the durable consumption commit.
- Exact-once ledger verification remains independent and strict.
- No product semantics, authority schema, provider choice, or timeout is
  broadened.
- The old one-shot authority remains exhausted.
- M3 remains locked until a real four-stage PASS.
