# M2c Claude Project Permission Authority Implementation Plan

> Execute under the active M2c completion goal with strict RED/GREEN commits.

**Goal:** Make the real M2c Mission deterministically exercise AgentDeck's ACP
permission bridge by pinning and sealing Claude `default` permission mode only
inside the disposable live project.

**Architecture:** Extend only `tests/test_m2c_live_acceptance.py` and durable
acceptance documentation. `_write_live_config()` creates exact local settings
and returns a path-free runtime seal; the live harness revalidates that seal
around every effect/governance boundary. Tool authority v3 and preflight v6 are
unchanged.

## Task 1: Establish RED

**Files:**

- Modify: `tests/test_m2c_live_acceptance.py`

- [ ] Add a test proving live configuration must create exact project-local
  `permissions.defaultMode=default` bytes and return a verifiable seal.
- [ ] Run only that test; record the current missing-file/return failure.
- [ ] Commit the RED test without implementation.

## Task 2: Implement exact creation and sealing

**Files:**

- Modify: `tests/test_m2c_live_acceptance.py`

- [ ] Add a fixed byte constant and a path-free
  `_ClaudePermissionSettingsSeal`.
- [ ] Create `.claude` mode 0700 and `settings.local.json` mode 0600 with
  exclusive no-follow writes.
- [ ] Read/seal through a bounded no-follow descriptor and verify path/descriptor
  identity before and after hashing.
- [ ] Reject pre-existing paths, wrong owner/mode/kind, extra entries, partial
  writes, and all drift as `claude_permission_settings_invalid`.
- [ ] Make `_write_live_config()` return the seal after exact creation.

## Task 3: Bind live boundaries and close tests

**Files:**

- Modify: `tests/test_m2c_live_acceptance.py`

- [ ] Revalidate the seal before Mission creation, after first permission,
  around both confirmations, around takeover/return-control, and after Mission
  completion.
- [ ] Cover content/mode/inode/kind/symlink/directory/extra-entry drift and
  identical-byte replacement.
- [ ] Prove pre-existing inputs are not overwritten and user/global settings
  paths are never accessed.
- [ ] Prove the seal and diagnostics contain no absolute path, settings content,
  prompt, auth value, or raw adapter output.
- [ ] Preserve existing whole-parent cleanup tests for setup failure and
  interruption.
- [ ] Run focused settings/live-setup tests until GREEN and commit.

## Task 4: Synchronize durable documentation

**Files:**

- Modify: `docs/validation/phase3-m2c-live-acceptance-sop.md`
- Modify: `docs/validation/2026-07-17-phase3-m2c-four-stage-live.md`
- Modify: `docs/handoff/current-development-state.md`
- Modify: `HISTORY.md`
- Modify: this plan

- [ ] Document exact project-local bytes/modes/seal and the prohibition on
  reading or changing user settings.
- [ ] Record the exhausted old live authority and new frozen-SHA requirement.
- [ ] Keep authority v3/preflight v6 semantics explicit.
- [ ] Commit user-visible acceptance behavior with HISTORY.

## Task 5: Verify and freeze

- [ ] Run focused permission-settings and strict/live-harness coverage.
- [ ] Run the complete M2c file.
- [ ] Run product/Conversation/contract/provider regressions.
- [ ] Run compile, diff, `src/agentdeck/**` zero-change, leakage, process,
  daemon, tmux, worktree, and temporary-root audits.
- [ ] Commit the frozen implementation and record its full SHA.

## Task 6: Re-establish real authority

- [ ] Run complete suite A in a fresh detached worktree at the frozen SHA.
- [ ] Run complete suite B in a second fresh detached worktree at the same SHA.
- [ ] Remove both worktrees and prove zero residue.
- [ ] Re-audit exact installed inputs and closed Claude login readiness.
- [ ] Run exactly one designated strict v6 preflight.
- [ ] If and only if ready, obtain a separate SHA/model/digest live authority.
- [ ] Execute one real four-stage Mission; never retry that authority.
- [ ] On PASS, close M2c in roadmap/validation/HISTORY/handoff and begin M3
  brainstorming → spec → writing-plans. On failure, record the one-shot result
  and start only the smallest new evidence-led repair cycle.
