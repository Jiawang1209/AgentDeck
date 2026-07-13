# Phase 3 M1 Foreground Conversation Validation

Date: 2026-07-13  
Branch: `codex/phase3-conversation-design`  
Verification base commit: `31df2197`

## Verdict

**PASS for the Phase 3 M1 foreground, project-local scope.** Deterministic acceptance, the full regression, contract validation, a real terminal session, and one already-installed Claude ACP adapter turn passed. The configured live Leader remained unready, so no real Leader Mission was claimed; the exact Leader-to-Mission path was covered by the deterministic fake-Leader acceptance.

## Automated gates

- Focused conversation acceptance: 113 passed.
- Full suite: 1856 tests, 0 failures, 0 errors, 1 documented skip.
- Compileall: passed for `src` and `tests`.
- `git diff --check`: passed before the final documentation update.
- Deterministic scenario covered natural setup preview and confirmation, fake Leader selection, compact turns, a two-Worker Mission, exact Mission confirmation, governance execution callback, exact ACP/tmux routing, status/approval/trace intents, cancellation, exit, ProjectView/contracts, outbox drain, and credential-like provenance redaction.

## Disposable live rehearsal

The rehearsal used a fresh temporary project and changed only that project's config. It installed nothing and did not change global authentication.

- Claude ACP adapter: installed version `0.58.1`.
- Codex CLI: installed version `0.131.0`.
- ACP preflight: ready; no blockers.
- Node runtime: ready, major version 22.
- ACP SDK: present, version `0.11.0`.
- Real Claude ACP turn: `stop_reason=end_turn`, turn state `completed`, session disconnect reason `clean_exit`, 8 compact updates, 0 permission requests.
- Bare `agentdeck` in a real PTY: opened the foreground prompt, `/status` returned `project-view/v1`, and `/quit` closed the session.
- The live configured Leader was DeepSeek and not ready. Deterministic intents remained available and ProjectView reported the setup blocker without trying an automatic fallback.

An adapter/SDK shutdown race printed a sanitized `message queue already closed` diagnostic after the successful turn. The authoritative ACP result still recorded completed/end-turn/clean-exit. This diagnostic is retained as an observation, not hidden or treated as a failed turn.

## Privacy and safety evidence

This report intentionally excludes raw conversation text, prompts, tool input/output, credentials, auth state, environment dumps, absolute home paths, native opaque session IDs, and generated internal IDs. Durable conversation records are compact and transcript-free. The acceptance test also proves that common inline credential assignments are redacted before Mission provenance is persisted.

## Scope boundary

This PASS does not cover M2/M3: no daemon, background continuation after client exit, complete transcript recovery, global project roaming, Workspace Client, automatic install/auth, or native same-session TUI attach was added or claimed.
