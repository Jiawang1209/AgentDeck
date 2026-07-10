# Golden Demo Rehearsal Test — Design

- **Date**: 2026-07-10
- **Status**: Approved

## Context

`agentdeck demo golden` already provides a read-only, state-aware guide for the AgentDeck golden path. Focused tests cover individual provider, approval, dispatch, review-gate, and release states, but no single regression test proves that one temporary project can move through the complete sequence while the guide keeps recommending the correct next action.

This slice adds that missing regression coverage. It is test infrastructure, not a new product capability.

## Goal

Add one deterministic end-to-end pytest rehearsal that uses a temporary AgentDeck project, the existing fake Leader, existing test runtime/state helpers, and existing CLI commands to verify the complete golden flow:

```text
ready to plan
-> waiting for approval
-> ready to dispatch
-> waiting for reply
-> review gate ready
-> released
```

At each checkpoint, the test runs the existing `agentdeck demo golden` command and verifies its `current_status`, `next_command`, and relevant step state.

## Scope

### In scope

- Add one end-to-end test to `tests/test_agent_cli.py`.
- Reuse existing test helpers such as `prepare_project`, `bind_agent`, and review-ledger seeding helpers where their behavior matches the rehearsal.
- Use existing AgentDeck CLI commands and `StateStore` test data to cross the deterministic worker/runtime boundary.
- Verify the final release record and audit event.
- Update `HISTORY.md` and `docs/handoff/current-development-state.md` with the added regression guarantee.
- Run focused and full verification in the `agentdeck` conda environment.

### Out of scope

- No new `agentdeck demo rehearse` command.
- No changes under `src/agentdeck/`.
- No new production helper, runtime backend, provider, contract, or response field.
- No real tmux process, network request, API credential, or external model.
- No README change because there is no new user-facing feature.

## Test Architecture

The rehearsal remains inside the existing CLI test module so it can reuse established fixtures and helpers without exporting test-only behavior into production code.

The test will:

1. Create a temporary AgentDeck project with the existing project fixture.
2. Configure the fake Leader and deterministic reviewer roles.
3. Bind the target worker to a fake pane through the existing test helper.
4. Run the current Leader plan and approval commands.
5. Inspect `agentdeck demo golden` after each state transition.
6. Use existing state/test helpers to record deterministic worker reply, artifact, code-review, and round-review facts rather than reading tmux.
7. Run the existing explicit release command.
8. Verify the released guide state, release record, and `round_released` event.

The test may use local variables and existing helpers. It must not add a production abstraction merely to make the test convenient.

## Checkpoint Assertions

The regression must cover these checkpoints:

1. **Ready to plan**: fake Leader is configured; the guide recommends `agentdeck leader plan --task <task>`.
2. **Waiting for approval**: a plan and pending approval exist; the guide recommends the concrete approve command and keeps dispatch blocked.
3. **Ready to dispatch**: the approval is approved and its agent has a visible fake pane; the dispatch step exposes the concrete dispatch command.
4. **Waiting for reply**: dispatch has produced the message/job ledger; the guide no longer recommends approval and waits on the worker boundary.
5. **Review gate ready**: deterministic artifact plus code-review and round-review facts make `agentdeck release --confirm` available.
6. **Released**: explicit release succeeds; the guide reports `current_status=released`, recommends `agentdeck workbench`, and marks the release step done.

## Safety and Isolation

- Pytest owns the temporary directory and removes it after the test.
- The rehearsal does not touch the developer's current `.agentdeck/` project state.
- Runtime interactions remain simulated; no tmux pane is created, read, written, or killed.
- The fake Leader makes planning deterministic and requires no credential or network.
- The release remains an explicit call to the existing `release --confirm` command inside the isolated test project.

## Documentation

`HISTORY.md` will record this as test coverage, not a feature. The handoff will state that the golden demo now has both focused state tests and one contiguous deterministic rehearsal. No production capability or public contract changes.

## Verification

Run, in order:

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py::test_golden_demo_rehearsal_drives_one_round_to_release -q
conda run -n agentdeck pytest tests/test_agent_cli.py -k "demo_golden or golden_demo_rehearsal" -q
conda run -n agentdeck pytest -q
conda run -n agentdeck python -m compileall src tests -q
git diff --check
```

The focused test must first fail for the expected missing contiguous behavior coverage, then pass after the test data/sequence is completed. Because this slice intentionally changes no production behavior, the TDD red phase is the new regression test exposing any mismatch between the assumed full sequence and current CLI semantics.

## Resolved Decisions

- This is test data and regression coverage, not a new product command.
- Use a deterministic sandbox with fake Leader and simulated runtime/state boundaries.
- Keep the rehearsal in the existing CLI test module and reuse existing helpers.
- Do not add production functions or modify contracts.
- Update development history and handoff only after the rehearsal passes.
