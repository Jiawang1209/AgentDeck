# AgentDeck Current Development State

Updated: 2026-07-20

## Active goal

R2, Task 15B, and Task 27 are closed. Development is intentionally stopped
before Task 28. The only next numerical task is Task 28, **Render faithful
cursor-safe, redacted Agent streams**, from the approved
[Product Kernel Rewrite TDD plan](../superpowers/plans/2026-07-18-agentdeck-product-kernel-rewrite.md).

Do not reopen M2/M2c, daemon, ConversationSession, autonomous, Skill, Memory,
GUI, or historical architecture plans as active authority. Task 28 has not
started, and this handoff does not authorize a real provider/tmux gate.

## Verified implementation state

- Verified Task 27 implementation HEAD: `b0d4e9be74e05626873c8e2a296972ac06cbebd7`.
- Branch: `codex/product-kernel-rewrite`.
- R2 exit: complete.
- Task 15B.1 through Task 15B.5: complete.
- Task 27 Observer Runtime Port and deterministic tmux layout: complete.
- Final integrated spec review: `✅ Final Task 15B spec compliant`.
- Final integrated quality review: `✅ Final Task 15B code quality approved`.
- Task 27 spec review: `✅ Task 27 spec compliant`.
- Task 27 quality review: `✅ Task 27 code quality approved`.
- Immediately before this handoff-only edit, `git status --short` returned no
  output.
- No push or merge was performed.

Task 15B now provides one ProductSession-scoped lifecycle authority for:

- project-level `/exit` as a durable pause transaction;
- exact ACP Worker cancellation and bounded prompt cleanup;
- exact Attempt, Evidence, Handoff, revision, and acceptance lineage;
- conservative startup recovery before the first terminal read;
- observational paused re-entry followed only by explicit `/resume`;
- one foreground asyncio loop and one owned Mission child;
- adapter-unavailable, authority-drift, outcome-unknown, and replay-safe
  fail-closed behavior;
- caller cancellation that preserves the first `CancelledError`, collects
  read/SIGINT/Mission/real ACP prompt tasks, closes the Store once, and leaves
  durable running authority for the next startup recovery without fabricating
  pause, Evidence, or Handoff facts.

## R2 exit evidence

All commands ran in the `agentdeck` conda environment against the verified
implementation HEAD.

```text
Task 15B expanded focused matrix: 572 passed in 16.85s
Product Kernel full suite:          1787 passed in 76.60s
Legacy suite:                       4461 passed, 3 skipped in 228.66s
Architecture gate:                  14 passed
compileall:                          pass
git diff --check:                    pass
changed Python files <= 500 lines:  pass
production legacy-authority scan:   pass
Product asyncio.run ownership gate: pass
```

The production legacy-authority lexical gate scans `src/agentdeck`. Negative
regression tests may name forbidden concepts such as tmux while proving that
the Product Kernel does not use them, so test descriptions are intentionally
outside that lexical scan.

Two loaded full-suite reviews observed isolated discovery timing flakes
(`version_probe_timeout` / one-second PID readiness). The exact failing tests
passed on isolated rerun, they are outside the Task 15B diff, and fresh final
Product Kernel and legacy runs above are green. No failure was hidden with
xfail, skip, fallback, or a relaxed timeout in Task 15B.

## Task 27 evidence and boundary

Task 27 adds an authority-free Runtime Port and deterministic tmux Observer
Adapter. It defines project-namespaced `Overview` and `Workers` windows, with
the exact Worker order `implementer`, `reviewer`, `reviser`, and
`acceptance_reviewer`. Commands are bounded argv tuples that launch read-only
Application-event subscriptions; tmux never sends work, infers completion, or
writes Product authority.

Review fixes established three additional invariants:

- pane insertion is simulated using tmux target-after semantics, so declared
  role/session/instance order cannot drift from final pane indices;
- takeover matches one explicit current immutable plan by project, role,
  session, and instance before runner I/O, while the Adapter caches no
  ownership authority;
- partial create compensates only after its own initial `new-session` succeeds,
  so retry can recover without deleting a pre-existing session after an
  ambiguous initial failure.

Fresh final evidence at the verified Task 27 implementation HEAD:

```text
Task 27 + architecture + context:   102 passed
Product Kernel full suite:         1839 passed in 79.23s
Legacy suite:                       4461 passed, 3 skipped in 228.86s
compileall:                         pass
git diff --check:                   pass
changed Python files <= 500 lines: pass
legacy-authority scan:              pass
Task 27 asyncio.run gate:           pass
```

No live tmux session, provider, ACP Mission, or Golden Product run occurred.
Task 27 does not implement Task 28 stream rendering/cursors/redaction or Task
29 validated return-control.

## Approved MVP direction

- bare `agentdeck` continuous natural-language ProductSession;
- Codex CLI, Claude CLI, or OpenAI-compatible API Leader selection;
- model selection and three Codex-style permission profiles;
- exact human-readable Mission Preview and one confirmation;
- Codex implementation, Claude review, Codex revision, Claude acceptance;
- ACP-only automatic Codex/Claude communication;
- tmux panes showing decoded real Agent events;
- one project-local SQLite database and one foreground writer;
- plain-language diagnostics, safe exit, and deterministic re-entry;
- real four-Worker website-reproduction Golden Product Gate.

The MVP still excludes background-after-exit execution, arbitrary Agent
graphs, CLI/PTY automatic fallback, Memory, Skills, self-improvement, GUI,
A2A, remote/mobile clients, and terminal-emulator work.

## Authority order

1. Product Kernel Rewrite Design;
2. Product North Star for long-term product invariants;
3. approved Product Kernel Rewrite TDD plan;
4. the current numerical Task acceptance criteria;
5. real validation evidence;
6. explicitly admitted legacy Adapter evidence.

The Task 15B appendices are execution supplements, not parallel product
designs:

- `docs/superpowers/appendices/task15b/2026-07-20-task-15b-acp-cancellation-recovery-design.md`
- `docs/superpowers/appendices/task15b/2026-07-20-task-15b-project-pause-resume.md`

HISTORY, legacy code, legacy tests, and legacy contracts cannot create current
requirements.

## Next gate

1. Do not start Task 28 without a new explicit `/goal` or user instruction.
2. When authorized, Task 28 must consume the Task 27 Port and immutable plan;
   it must not redesign workspace authority or add return-control.
3. Task 28 owns cursor-safe faithful rendering, reconnect cursors, and
   redaction. It must preserve the rule that tmux/PTY pixels are observation,
   never lifecycle, approval, result, completion, or recovery authority.
4. Preserve closed R2/Task 15B and Task 27 invariants and rerun their focused
   gates when Task 28 touches shared Observer composition.
5. Real provider, ACP/tmux, and Golden Product gates still require their exact
   later Task authorization; this handoff does not authorize them.

## Canonical handoff inputs

Read only:

1. `AGENTS.md`
2. `CLAUDE.md` or `AGENT.md`
3. `docs/superpowers/specs/2026-07-18-agentdeck-product-kernel-rewrite-design.md`
4. `docs/roadmap/product-north-star.md`
5. `docs/superpowers/plans/2026-07-18-agentdeck-product-kernel-rewrite.md`
6. the top of `HISTORY.md`
7. this file

Then inspect:

```bash
git status --short
git log --oneline -8
git diff --name-only main...HEAD
```

Removed historical designs can be recovered from Git only when a specifically
approved legacy-admission task requires them. They must not be loaded as
general project context.
