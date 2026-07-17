# M2c Preview Consumption Convergence Implementation Plan

> **Execution mode:** Use `superpowers:executing-plans` with
> `superpowers:test-driven-development`; no subagent is required because this is
> one tightly coupled harness slice.

**Goal:** Eliminate the live acceptance observation race by waiting for the
completed confirmation turn before exact-once preview-consumption validation,
then obtain a new frozen four-stage M2c result.

**Architecture:** Keep AgentDeck product code unchanged. In the real M2c harness,
daemon admission remains the durable execution-admission gate; the third bare
prompt becomes the synchronous conversation-turn completion barrier; only then
does the harness inspect the event ledger for exactly one Mission-specific
`conversation_preview_consumed` event.

**Tech Stack:** Python 3.12 standard library, pytest, conda `agentdeck`, detached
git worktrees, exact installed Codex/Claude/ACP/Node/tmux inputs only after
deterministic freeze.

---

## Task 1: RED — reproduce admission-before-consumption observation

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py`
- Modify: `HISTORY.md`

- [x] Add a deterministic `_create_and_confirm_live_mission` test fixture with
  a valid frozen four-step Preview and already-admitted Mission.
- [x] Make the fake `_wait_for_pty_prompt(..., 3)` append exactly one matching
  `conversation_preview_consumed` event. Earlier prompt counts must not append
  it.
- [x] Assert the complete helper succeeds only when prompt calls are
  `[1, 2, 3]`; before the implementation change, observe RED because the
  current helper reads the ledger after prompt 2.
- [x] Assert the RED diagnostic remains
  `mission_preview_not_consumed_exactly_once` and contains no fake prompt,
  path, or terminal sentinel.
- [x] Record the exact RED result in HISTORY and commit:

```bash
git add tests/test_m2c_live_acceptance.py HISTORY.md \
  docs/superpowers/plans/2026-07-17-m2c-preview-consumption-convergence.md
git commit -m "test: reproduce M2c preview consumption race"
```

## Task 2: GREEN — wait for the completed confirmation turn

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py`
- Modify: `HISTORY.md`

- [x] In `_create_and_confirm_live_mission`, retain the existing daemon
  admission wait, then call:

```python
_wait_for_pty_prompt(process, master, capture, 3)
```

  immediately before reading `store.all_events()`.
- [x] Keep the existing Mission-specific event filter and `len(consumed) == 1`
  requirement byte-for-byte except for its new ordering.
- [x] Do not add sleeps, retries, new timeout values, event writes, production
  changes, transcript capture, or alternate success paths.
- [x] Run the focused RED/GREEN test and relevant PTY/confirmation tests:

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py \
  -k 'preview_consumption or preview_not_consumed or pty_prompt or frozen_authority' -q
```

- [x] Update HISTORY and commit:

```bash
git add tests/test_m2c_live_acceptance.py HISTORY.md
git commit -m "test: await M2c confirmation turn convergence"
```

## Task 3: Close exact-once and diagnostic boundaries

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py`
- Modify: `HISTORY.md`

- [x] Add or extend deterministic cases for zero, duplicate, and mismatched
  Mission consumption events after prompt 3. Each invalid cardinality must fail
  only as `mission_preview_not_consumed_exactly_once`.
- [x] Reuse the existing prompt helper tests to prove process exit and timeout
  remain `bare_pty_exited` / `bare_pty_prompt_timeout`.
- [x] Assert diagnostics exclude raw prompt, request, path, stderr, provider
  output, and injected secret sentinels.
- [x] Run focused confirmation/PTY/diagnostic coverage and commit any minimum
  missing test-only guard.

## Task 4: Wider deterministic verification and freeze

**Files:**
- Modify: `docs/handoff/current-development-state.md`
- Modify: `docs/validation/2026-07-17-phase3-m2c-four-stage-live.md`
- Modify: `HISTORY.md`
- Modify: this plan

- [x] Run focused strict/preflight/live-harness coverage with real nodes skipped.
- [x] Run complete non-live M2c coverage.
- [x] Run Conversation/product regression coverage.
- [x] Run `python -m compileall src`, `git diff --check`, production-source
  zero-change audit, durable-wording audit, process audit, and temporary-root
  audit.
- [x] Update durable evidence and freeze a new implementation commit.

## Task 5: Two independent complete suites

- [x] Create two fresh detached worktrees at the unchanged frozen SHA.
- [x] Run the complete suite in each with its own absolute `PYTHONPATH` and the
  `agentdeck` conda environment.
- [x] Require all deterministic tests to pass and only the three explicit real
  nodes to skip.
- [x] Remove both worktrees and confirm zero process, daemon, tmux, and temporary
  root residue.
- [x] Commit the full-suite evidence without altering the frozen implementation
  files.

## Task 6: New one-shot preflight and live acceptance

- [x] Re-audit the exact regular Codex, Claude, Node, tmux, metadata-selected ACP
  entrypoint, and both closed npm `.bin` links without modifying them.
- [x] Run exactly one v5 designated read-only preflight on the new frozen SHA
  with Leader `gpt-5.5`.
- [x] Require `ready=true`, `blockers=[]`, `failures=[]`, authority v3, and a
  fresh digest; otherwise record evidence and do not run live.
- [x] On PASS only, run exactly one real four-stage Mission on the same
  SHA/model/digest.
- [ ] Require exact implementation -> review -> revision -> acceptance order,
  ACP/tmux transport evidence, disconnect/reconnect, permission confirmation,
  takeover/return-control, exact artifact bytes, compact handoffs, complete
  cleanup, and no diagnostic leakage.
- [x] Never retry either one-shot node under the same authority.

## Task 7: Close M2c and transition to M3

**Files:**
- Modify: `docs/roadmap/product-north-star.md`
- Modify: `docs/roadmap/ultimate-goal-roadmap.md`
- Modify: `docs/handoff/current-development-state.md`
- Modify: `docs/validation/2026-07-17-phase3-m2c-four-stage-live.md`
- Modify: `HISTORY.md`
- Add: M3 brainstorming/spec/plan documents selected from the current north
  star after M2c PASS

- [ ] On live PASS, mark M2c complete with exact frozen SHA/model/digest/test
  evidence and remove the M3 lock.
- [ ] Re-read the current north star and active handoff, then conduct a separate
  M3 brainstorming cycle. Do not infer M3 scope from stale chat history.
- [ ] Write and self-review the approved M3 spec and a per-file, per-test,
  per-commit TDD plan.
- [ ] Commit the M3 transition documents locally. Do not implement M3, merge, or
  push without separate authority.

## Self-review

- [ ] RED precedes the harness ordering change.
- [ ] Product code remains untouched.
- [ ] Prompt 3 is a completion barrier, not semantic authority.
- [ ] The ledger remains the exact-once semantic authority.
- [ ] No sleep, timeout inflation, retry, or fallback can produce PASS.
- [ ] Old one-shot authority is never reused.
- [ ] M3 stays locked until real M2c PASS.
