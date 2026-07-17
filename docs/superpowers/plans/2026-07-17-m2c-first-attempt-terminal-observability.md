# M2c First-Attempt Terminal Observability Implementation Plan

> **Execution mode:** Use `superpowers:executing-plans` with
> `superpowers:test-driven-development`; this is one tightly coupled harness
> slice and does not require subagent delegation.

**Goal:** Stop the first-permission wait on durable first-attempt terminal state
and expose one exact, closed ACP stage without raw diagnostics.

**Architecture:** Add pure closed projection helpers and a bounded wrapper over
the existing `_wait_for_state()`. The wrapper returns only for one pending
permission; exact terminal attempt facts raise immediately. Extend the existing
compact ledger with `attempt_terminal_stage` and an ambiguous classification.
Keep AgentDeck product code and authority schemas unchanged.

**Tech Stack:** Python 3.12 standard library, pytest, conda `agentdeck`, detached
git worktrees, real installed inputs only after deterministic freeze.

---

## Task 1: RED — terminal attempt must end the permission wait

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py`
- Modify: `HISTORY.md`

- [x] Add a deterministic store whose first snapshot already contains one
  step-1 `claude-worker` ACP attempt in `ambiguous` state with
  `acp_completion_prompt_outcome_unknown` and zero permissions.
- [x] Exercise the current first-permission path and prove it does not stop on
  the snapshot. Use a fake clock/bounded wait so RED is immediate.
- [x] Require the future result to be
  `first_attempt_acp_prompt_ambiguous`, not `first_permission_timeout`.
- [x] Assert no raw reason, blocker, receipt, ID, path, or prompt sentinel is
  rendered.
- [x] Record the exact RED result and commit.

## Task 2: GREEN — add closed terminal projection

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py`
- Modify: `HISTORY.md`

- [ ] Define one closed mapping for admission, receipt, five ACP completion
  stages, failed, cancelled, and interrupted.
- [ ] Add a pure validator that requires one Mission, one first-attempt lineage,
  exact transport/agent/step, no permission conflict, and a valid
  state/reason pair.
- [ ] Add `_wait_for_first_permission_or_terminal_attempt()` as a wrapper around
  `_wait_for_state()`. It observes pending permission or terminal attempt in one
  predicate; it returns the snapshot only for permission and otherwise raises
  the mapped closed live failure.
- [ ] Replace only the live harness's first `_wait_for_state()` call.
- [ ] Do not modify timeout, sleep, retries, state, provider, transport, or
  production source.
- [ ] Run focused RED/GREEN and commit.

## Task 3: Lock diagnostic and malformed-state safety

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py`
- Modify: `HISTORY.md`

- [ ] Parameterize all seven ambiguity stages and three ordinary terminal
  states.
- [ ] Cover duplicate attempts, cross-Mission attempt, terminal plus pending
  permission, malformed collections, arbitrary reason, missing receipt
  authority, and active attempt timeout.
- [ ] Add `attempt_terminal_stage` to the exact ledger key contract with only
  allowlisted values; add `worker_attempt_ambiguous` to classifications.
- [ ] Keep the existing forbidden-content matrix and explicitly forbid every
  raw source field/sentinel.
- [ ] Run focused terminal/diagnostic tests and commit.

## Task 4: Wider verification and freeze

**Files:**
- Modify: `docs/handoff/current-development-state.md`
- Modify: `docs/validation/2026-07-17-phase3-m2c-four-stage-live.md`
- Modify: `HISTORY.md`
- Modify: this plan

- [ ] Run focused strict/package/launcher/live-harness coverage.
- [ ] Run complete non-live M2c coverage.
- [ ] Run Conversation/product regressions.
- [ ] Run compile, diff, current-slice product-source zero-change, leakage,
  process, daemon, worktree, and temporary-root audits.
- [ ] Freeze the new implementation SHA and record deterministic evidence.

## Task 5: Two complete suites

- [ ] Run the complete suite in two fresh detached worktrees at the unchanged
  frozen SHA with independent absolute `PYTHONPATH` values.
- [ ] Require only the three explicit real nodes to skip.
- [ ] Remove both worktrees and prove zero residue.
- [ ] Commit evidence without changing frozen implementation files.

## Task 6: One new preflight and one new live Mission

- [ ] Re-audit exact installed Codex, Claude, Node, tmux, ACP package,
  metadata-selected entrypoint, and both closed `.bin` links.
- [ ] Run exactly one v5 read-only preflight on the frozen SHA with Leader
  `gpt-5.5`.
- [ ] On ready PASS only, run exactly one same-SHA/model/digest real four-stage
  Mission.
- [ ] Never retry the same authority. Record PASS or the exact new terminal
  stage, complete cleanup, and start only the minimum next root-cause cycle.

## Task 7: Close M2c and transition to M3

- [ ] Only after implementation -> review -> revision -> acceptance real PASS,
  update both roadmaps, handoff, validation, and HISTORY to close M2c.
- [ ] Re-read the current product north star and conduct a separate M3
  brainstorming cycle.
- [ ] Write, self-review, and locally commit M3 spec and per-file/per-test/
  per-commit TDD plan.
- [ ] Do not implement M3, merge, or push without separate authority.

## Self-review

- [ ] RED precedes implementation.
- [ ] Durable terminal state, not time or transcript, ends the wait.
- [ ] Stages are finite, exact, and leak-free.
- [ ] Ambiguous effects are never retried automatically.
- [ ] Product source and authority schemas remain unchanged.
- [ ] M3 stays locked until real M2c PASS.
