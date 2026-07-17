# M2c Claude Authentication Readiness Gate Implementation Plan

> Execute under the active M2c completion goal with strict RED/GREEN commits.

**Goal:** Make strict preflight reject a logged-out Claude Worker before live
authority is consumed, without retaining authentication material or changing
product runtime behavior.

**Architecture:** Extend only `tests/test_m2c_live_acceptance.py` and durable
acceptance documentation. The exact sealed Claude executable supplies a bounded
JSON auth-status result; a closed parser projects only readiness. Advance the
strict preflight contract to v6 while retaining authority v3.

## Task 1: Establish deterministic RED

**Files:**

- Modify: `tests/test_m2c_live_acceptance.py`

- [x] Add a fake Claude auth-status branch with configurable logged-in state.
- [x] Add a test that supplies `loggedIn=false` and expects the exact closed
  `claude/auth-status/claude_auth_unavailable` failure.
- [x] Run only that test and record the current false-ready failure.
- [x] Commit the RED test without implementation.

## Task 2: Implement closed auth readiness

**Files:**

- Modify: `tests/test_m2c_live_acceptance.py`

- [x] Add `claude_auth_unavailable` and `auth-status` to the closed enums.
- [x] Add an allowlisted auth environment projector; never emit values.
- [x] Add a duplicate-key-rejecting bounded JSON parser that returns only a
  boolean readiness decision.
- [x] Run `auth status --json` through `_run_attributed_probe()` using the exact
  Claude seal.
- [x] Preserve existing process/write/identity failures and add the auth blocker
  only when no lower-level attributed failure already explains the probe.
- [x] Mark the Claude tool card and whole payload unready on auth failure.
- [x] Pass the same host authentication context through the designated
  preflight helper and guarded live entry.
- [x] Advance `STRICT_PREFLIGHT_SCHEMA_VERSION` to v6; do not change authority
  v3 or its digest payload.

## Task 3: Close parser, leakage, and admission cases

**Files:**

- Modify: `tests/test_m2c_live_acceptance.py`

- [x] Cover true, false, malformed, duplicate-key, missing, wrong-typed, and
  nonzero-success-claim results.
- [x] Prove arbitrary account/secret/path fields never appear in payloads or
  `_LiveHarnessFailure` diagnostics.
- [x] Prove the fake designated preflight stays read-only.
- [x] Prove the guarded live entry returns the exact auth blocker before project
  execution effects.
- [x] Prove changing auth state cannot change the authority digest.
- [x] Run focused and aggregate tests until GREEN.
- [x] Commit the minimal harness implementation and tests.

## Task 4: Synchronize durable contracts and history

**Files:**

- Modify: `docs/validation/phase3-m2c-live-acceptance-sop.md`
- Modify: `docs/validation/2026-07-17-phase3-m2c-four-stage-live.md`
- Modify: `docs/handoff/current-development-state.md`
- Modify: `HISTORY.md`

- [x] Document strict preflight v6 and the closed auth-status probe.
- [x] Mark all prior v5 authority as exhausted/non-authorizing for v6.
- [x] Record that login remains human-controlled and never occurs in the
  harness.
- [x] Record only schema, model, SHA, readiness, blockers/failures, and digest;
  never auth output/material.
- [x] Commit documentation in the same GREEN slice if user-visible semantics
  changed, or as an immediately following evidence commit.

## Task 5: Verify and freeze

- [x] Run focused auth/preflight/live-guard tests.
- [x] Run the aggregate M2c deterministic selection.
- [x] Run the entire M2c file.
- [x] Run relevant product/Conversation/provider/contract regressions.
- [x] Run `python -m compileall src`.
- [x] Assert `git diff <pre-slice-sha> -- src/agentdeck` is empty.
- [x] Run leakage, process, tmux, temporary-root, and repository-residue audits.
- [x] Commit the frozen implementation and record its full SHA.

## Task 6: Double full suite and real gates

- [x] Create fresh detached worktree A at the frozen SHA; run the complete suite
  with absolute `PYTHONPATH`; remove it.
- [x] Create fresh detached worktree B at the same SHA; repeat; remove it.
- [ ] Human completes Claude login and verifies only the closed status fields.
- [ ] Re-audit exact Codex/Claude/Node/tmux/ACP package inputs without mutation.
- [ ] Run exactly one strict v6 designated preflight on the frozen SHA with
  Leader `gpt-5.5`.
- [ ] If and only if ready, request/consume one separate exact live authority.
- [ ] On real four-stage PASS, close M2c in validation/HISTORY/handoff/roadmap,
  then begin M3 brainstorming → spec → plan. Otherwise record the one-shot
  blocker and start only the smallest evidence-led repair cycle.
