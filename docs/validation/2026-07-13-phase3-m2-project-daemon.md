# Phase 3 M2 Project Daemon Validation

Date: 2026-07-14

## Verdict

**Deterministic M2 acceptance: PASS. Real end-to-end Leader rehearsal: BLOCKED.**

The committed acceptance starts a real project daemon, admits one exact frozen
two-Worker Mission, disconnects the client, reaches an ACP permission pause,
renders deterministic recovery through a new bare `agentdeck` PTY, confirms the
exact permission preview, and completes ACP then tmux with two succeeded
attempts, two validated replies, and two recorded compact handoffs. Worker B is
not admitted until Worker A's validated handoff is durable. ProjectView,
daemon/scheduler contracts, ledger records, events, snapshot hash, and bounded
file effects agree. Durable state contains hashes and byte counts for streamed
ACP updates, not transcript chunks, private reasoning, or secret markers.

The crash matrix terminates a real child process with SIGKILL at nine durable
boundaries. Classifications are `resumable` at before-prepare,
after-prepare, receipt, reply, handoff, and outbox boundaries; `ambiguous` after
dispatch before receipt; `waiting_human` for permission; and `interrupted` for
shutdown. Every case records zero duplicate dispatches.

## Automated evidence

- Focused acceptance and crash matrix, repeated twice: `10 passed` each run.
- Acceptance/crash/recovery regression: `117 passed`.
- Daemon suite: `889 passed`.
- Full suite: `2757 passed, 1 skipped`.
- Compileall and `git diff --check`: PASS.

## Real component evidence and blocker

Existing installations were inspected without installation or authentication
changes: Claude Agent ACP `0.58.1`, Node `22.23.0`, Codex CLI `0.131.0`, and
Claude CLI `2.1.208`. The already authenticated real Claude ACP foreground
vertical slice passed (`1 passed in 22.20s`).

A fresh disposable M2 project selected a ready CLI Leader and explicit
`acp -> tmux` Workers. Codex CLI with the configured `gpt-5.6-sol` model was
rejected because that model requires a newer CLI; no upgrade was performed.
Codex CLI with `gpt-5.4` and Claude CLI with `opus` were both callable, but their
plan output did not satisfy AgentDeck's strict Mission JSON contract, producing
the sanitized blocker `mission preview provider failed`. Therefore the real
Leader-to-Mission rehearsal is not a PASS.

A transport-only fallback used a deterministic frozen plan while preserving the
ready Claude CLI configuration. Real Claude ACP completed and its compact
handoff was recorded before real Codex tmux was submitted. Codex tmux did not
produce a correlated structured reply within the bounded rehearsal window, so
the run was stopped and recorded as blocked rather than retried or reported as
successful. All disposable daemon, adapter, tmux, and project resources were
removed; no process or `/tmp/agentdeck-m2-live-*` directory remained.

No transcript, raw prompt/tool I/O, credentials, authentication data,
environment dump, opaque native session id, or absolute home path is included
in this report.

## Scope boundary

M2 delivers a project-local Unix-socket daemon, not A2A, remote execution,
global roaming, notifications, a Workspace Client, full transcript recovery,
automatic installation/login, Windows IPC, or a terminal emulator.
