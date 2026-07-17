# M2c Claude Authentication Readiness Gate Design

**Date:** 2026-07-17
**Status:** Approved for implementation under the active completion goal
**Milestone:** Phase 3 M2c real four-stage acceptance closure
**Scope:** Strict designated preflight and live admission harness only

## 1. Purpose

Frozen implementation `3b2b3ae18dec745e56ff1920c3a401c9518515ec`
passed two complete suites and strict preflight v5, but its one authorized live
Mission terminalized at the first Claude ACP `prompt()` before a permission
request. A follow-up read-only audit of the exact configured Claude executable
reported `loggedIn=false`, `authMethod=none`, and no supported environment
credential.

The v5 preflight only proves Claude executable identity, version, and help
capability. It can therefore declare a logged-out Claude Worker ready and
consume live authority on a condition that was knowable beforehand. This
design closes that gap with one bounded, read-only, closed authentication
readiness probe.

## 2. North-star alignment

- AgentDeck remains the orchestration and governance authority; ACP remains the
  managed Worker transport.
- A managed Worker must be usable before a real Mission is admitted, not merely
  installed.
- Readiness evidence is compact and auditable without retaining credentials,
  account identity, paths, raw CLI output, or configuration content.
- Authentication remains a human/global prerequisite. The harness never logs
  in, refreshes credentials deliberately, changes configuration, or falls back
  to another provider/transport.
- Existing tool identity and package-tree authority remain unchanged.

No production `src/agentdeck/**` behavior changes.

## 3. Alternatives

### 3.1 Treat the prompt ambiguity as a live-only condition

Rejected. The current logged-out state is deterministically observable before
live and should not consume a one-shot Mission authority.

### 3.2 Infer login from credential files or environment-variable presence

Rejected. Subscription credentials may be managed outside project files, file
formats are private implementation details, and presence does not prove that
the exact Claude CLI considers itself authenticated.

### 3.3 Run the exact sealed CLI's read-only auth status command

Chosen. The designated preflight invokes only:

```text
<sealed-claude> auth status --json
```

It uses bounded process/output handling and the existing process-scope and
filesystem gates. Only the closed `loggedIn` boolean is consumed; captured
bytes are discarded after parsing.

## 4. Authentication probe contract

The probe uses the exact `_ExecutableSeal` already bound into
`m2c-tool-authority/v3`. It receives an allowlisted projection of the same host
authentication context that live execution would receive:

- `HOME`, `USER`, `LOGNAME`, `XDG_CONFIG_HOME`, and `CLAUDE_CONFIG_DIR` when
  present;
- supported Anthropic/Claude credential environment variables when present;
- isolated `TMPDIR`, the sealed executable path, and fixed locale values.

No environment value is returned, logged, hashed into evidence, or persisted.
The probe cannot use PATH fallback because the executable is the sealed
absolute path.

Parsing is strict and bounded:

- UTF-8 JSON object only;
- duplicate JSON keys rejected;
- `loggedIn` must be a JSON boolean;
- `loggedIn=true` requires successful process exit;
- `loggedIn=false`, malformed JSON, missing/wrong-typed `loggedIn`, nonzero
  success claims, or empty/oversized output all fail closed;
- auth method, subscription type, email/account fields, errors, stderr, and
  arbitrary extra fields are never projected.

Existing process-scope, executable drift, and filesystem-snapshot failures keep
their existing closed codes. A well-scoped auth result that is not positively
ready produces exactly:

```json
{
  "tool": "claude",
  "probe": "auth-status",
  "code": "claude_auth_unavailable"
}
```

## 5. Contract version and authority

The response shape remains closed, but its readiness semantics change. Strict
preflight therefore advances from `m2c-live-preflight/v5` to
`m2c-live-preflight/v6`. v5 evidence cannot authorize a v6 live Mission.

Tool authority remains `m2c-tool-authority/v3`: authentication is mutable host
readiness, not executable/package identity, and is intentionally excluded from
the authority digest. A new v6 preflight must be produced immediately before a
separately authorized live Mission on the same frozen SHA/model/digest.

## 6. TDD requirements

RED must prove that the current strict preflight declares a fake logged-out
Claude CLI ready. GREEN must cover:

1. `loggedIn=true` plus exit zero keeps Claude ready;
2. `loggedIn=false` blocks with exactly `claude_auth_unavailable`;
3. malformed, duplicate-key, missing, wrong-typed, and success-on-nonzero
   responses fail closed;
4. raw auth output, account sentinels, secret sentinels, and absolute paths do
   not appear in payloads or diagnostics;
5. probe process/write/identity failures retain their existing closed codes;
6. fake-tool designated preflight remains read-only and deterministic;
7. live admission consumes the same v6 preflight gate;
8. authority v3 digest is unchanged by auth state;
9. `src/agentdeck/**` remains byte-for-byte untouched;
10. real preflight/live nodes remain opt-in skipped during deterministic/full
    suites.

## 7. Verification and authority sequence

1. Commit the failing RED test separately.
2. Implement the minimal harness-only GREEN and contract/SOP documentation.
3. Run focused auth/preflight tests, aggregate M2c tests, the complete M2c file,
   relevant product regressions, compile, diff, leakage, process, and residue
   audits.
4. Freeze a new commit and run two complete suites in fresh detached
   worktrees with absolute `PYTHONPATH`.
5. Human restores Claude authentication outside the harness.
6. Re-audit installed inputs read-only and execute exactly one strict v6
   preflight with Leader `gpt-5.5`.
7. Only `ready=true`, `blockers=[]`, and `failures=[]` may support a separate
   one-shot live authorization naming SHA, model, and authority digest.
8. Never retry any exhausted SHA/model/preflight authority.

## 8. Completion criteria

This slice is complete when logged-out Claude state is rejected before live,
all deterministic/full verification passes on one frozen SHA, documentation
records v6 semantics, and the human-login prerequisite is the only remaining
external action. M2c itself remains incomplete until the real four-stage
Mission passes; M3 remains locked until then.
