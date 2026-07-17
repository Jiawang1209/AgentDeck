# Phase 3 M2c Real Four-Stage Acceptance Evidence

Date: 2026-07-17

## Frozen authority

- AgentDeck implementation:
  `284d8f62a9121a0d0351938aee1f716b3ebd198e`
- Leader provider: `codex-cli`
- Leader model: `gpt-5.5`
- authority schema: `m2c-tool-authority/v3`
- authority digest:
  `sha256:b194c3b4ccbfa3ba2b534bf9cb51e59ecbc077e2576c6eea8ba343f26cc83ffa`
- strict preflight schema: `m2c-live-preflight/v5`

## Deterministic verification

- focused strict/package/launcher: `37 passed, 254 deselected in 36.43s`
- complete non-live M2c: `289 passed, 2 skipped in 95.77s`
- product regressions: `851 passed in 4.86s`
- full suite 1: `4380 passed, 3 skipped in 205.38s`
- full suite 2: `4380 passed, 3 skipped in 209.27s`
- compile, diff, product-source scope, process, and temporary-root audits: PASS

## Designated read-only preflight

The separately authorized designated node ran exactly once on the frozen
authority and passed `1 passed in 16.32s`.

```json
{
  "schema_version": "m2c-live-preflight/v5",
  "ready": true,
  "blockers": [],
  "failures": [],
  "leader_model": {
    "provider": "codex-cli",
    "model": "gpt-5.5",
    "source": "explicit",
    "ready": true
  },
  "tool_authority": {
    "schema_version": "m2c-tool-authority/v3",
    "digest": "sha256:b194c3b4ccbfa3ba2b534bf9cb51e59ecbc077e2576c6eea8ba343f26cc83ffa",
    "source": "explicit",
    "ready": true
  }
}
```

Sanitized ready tool versions were Codex CLI `0.131.0`, Claude Code `2.1.211`,
Claude Agent ACP `0.58.1`, Node `v22.23.0`, and tmux `3.6a`.

The detached preflight checkout was removed. Follow-up process, temporary-root,
and worktree audits were empty. The preflight did not invoke the Leader model,
start an ACP session, start tmux, initialize an AgentDeck project, start a
daemon, install, authenticate, or change global state.

## Real four-stage Mission

Status: pending the one same-SHA/model/digest live execution.

M2c remains **BLOCKED** and M3 remains locked until that live execution proves
the complete implementation, review, revision, acceptance, handoff, permission,
takeover/recovery, artifact, ledger, and cleanup matrix.
