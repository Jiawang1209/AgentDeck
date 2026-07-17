# M2c First-Attempt Terminal Observability Design

**Date:** 2026-07-17
**Status:** Approved for implementation under the active completion goal
**Milestone:** Phase 3 M2c real four-stage acceptance closure
**Scope:** Real M2c first-permission wait and closed diagnostics only

## 1. Purpose

Frozen preview-convergence implementation
`690f0baf6efad6ad5608edaf10cf396da2729521` passed two complete suites and its
real v5 preflight. Its one real Mission crossed Preview consumption, prompt 3,
and daemon admission, then failed after 180 seconds as
`first_permission_timeout`. Durable evidence contained one step-1 Claude ACP
attempt in `ambiguous` state and zero permissions, replies, or handoffs.

The daemon correctly treats an ambiguous attempt as a human-resolution state.
The live harness is wrong to keep waiting only for a permission once the exact
attempt has terminalized. Its compact diagnostic also projects `ambiguous` as
generic permission inconsistency and intentionally discards the raw
`terminal_reason`, so it cannot distinguish admission, receipt, prompt, update,
parse, finish, or cleanup ambiguity.

This design makes the first-permission wait terminal-aware and exposes only a
strict closed stage derived from durable attempt facts. It does not change the
daemon, scheduler, ACP transport, timeout, retry, provider, or permission
semantics.

## 2. North-star alignment

- AgentDeck durable state, not elapsed time or transcript text, determines when
  a Worker attempt has reached a human-resolution boundary.
- ACP remains the managed transport; AgentDeck keeps orchestration and
  governance authority.
- ambiguous external effects fail closed and are never retried automatically.
- diagnostics remain compact, auditable, and safe for durable evidence.
- tmux/PTY, prompt, adapter stderr, raw provider output, paths, IDs, and receipt
  summaries remain excluded.

No production `src/agentdeck/**` behavior changes.

## 3. Alternatives

### 3.1 Increase the permission timeout or retry

Rejected. It would hide a terminal state, may duplicate an ambiguous effect,
and provides no causal information.

### 3.2 Persist raw adapter diagnostics

Rejected. stderr, prompt, SDK messages, receipt text, and paths can contain
secrets or user content and are not required to identify the durable stage.

### 3.3 Observe permission or terminal attempt and map a closed stage

Chosen. The harness returns normally only for one pending permission. An exact
first-attempt terminal state raises immediately with a code derived from an
allowlist of product-defined durable reasons.

## 4. First-attempt convergence contract

After daemon admission, the harness observes one state snapshot until either:

1. exactly one `permission_requests[]` item exists with `status=pending`; or
2. exactly one step-1 `mission_attempts[]` item exists in `failed`, `cancelled`,
   `interrupted`, or `ambiguous`.

Any malformed collection, duplicate first-attempt lineage, permission plus
terminal conflict, unexpected terminal reason, or cross-Mission record fails
as `first_attempt_terminal_contract_invalid`.

If neither condition appears before the existing 180-second bound, the
existing `first_permission_timeout` remains accurate.

The helper reuses `_wait_for_state()` and does not add a second timer, sleep,
retry, write, or state transition.

## 5. Closed terminal mapping

Only these durable projections are accepted:

| attempt state/reason | live code | diagnostic stage |
| --- | --- | --- |
| `ambiguous` / `admission_outcome_unknown` | `first_attempt_admission_ambiguous` | `admission` |
| `ambiguous` / `receipt_persistence_unknown` | `first_attempt_receipt_ambiguous` | `receipt` |
| `ambiguous` / `acp_completion_prompt_outcome_unknown` | `first_attempt_acp_prompt_ambiguous` | `acp_prompt` |
| `ambiguous` / `acp_completion_update_outcome_unknown` | `first_attempt_acp_update_ambiguous` | `acp_update` |
| `ambiguous` / `acp_completion_parse_outcome_unknown` | `first_attempt_acp_parse_ambiguous` | `acp_parse` |
| `ambiguous` / `acp_completion_finish_outcome_unknown` | `first_attempt_acp_finish_ambiguous` | `acp_finish` |
| `ambiguous` / `acp_completion_cleanup_outcome_unknown` | `first_attempt_acp_cleanup_ambiguous` | `acp_cleanup` |
| `failed` / `worker_failed` | `first_attempt_failed` | `worker_failed` |
| `cancelled` | `first_attempt_cancelled` | `cancelled` |
| `interrupted` | `first_attempt_interrupted` | `interrupted` |

The ledger adds `attempt_terminal_stage` with only these values plus `none` and
`unknown`. It never prints the source field name, raw reason, blocker, receipt,
IDs, or arbitrary strings. `ambiguous` becomes classification
`worker_attempt_ambiguous`; it is not mislabeled as permission inconsistency.

## 6. TDD requirements

RED must prove the current live path still waits through an already-durable
ambiguous first attempt instead of terminalizing. GREEN must cover:

1. one pending permission returns the snapshot;
2. all seven ambiguity reasons map exactly;
3. failed/cancelled/interrupted map exactly;
4. terminal state stops on the first observed snapshot;
5. duplicate/cross-Mission/conflicting permission facts fail closed;
6. arbitrary reason/blocker/receipt/prompt/path/ID sentinels never render;
7. active attempt without permission still times out normally;
8. the diagnostic key set and classifications are exact;
9. no product source changes;
10. real nodes remain opt-in skipped in deterministic/full verification.

## 7. Authority and real sequence

Authority schema remains `m2c-tool-authority/v3`; strict preflight remains
`m2c-live-preflight/v5`. After RED/GREEN and wider verification:

1. freeze a new git SHA;
2. run two complete suites in fresh detached worktrees;
3. re-audit unchanged installed inputs;
4. run one new v5 preflight with Leader `gpt-5.5`;
5. run one same-SHA/model/digest live Mission only if preflight is ready;
6. never retry the exhausted authority;
7. use the newly exposed stage to repair the actual ACP cause in another
   minimal cycle if the Mission still does not pass.

## 8. Completion criteria

- terminal first attempts never decay into a generic permission timeout;
- every emitted stage is allowlisted and causally backed by one durable record;
- raw durable/provider/terminal content remains absent;
- deterministic and two complete suites pass on one frozen SHA;
- the one new live either completes all four stages or yields one exact closed
  ACP terminal stage for the next root-cause cycle;
- M2c remains blocked and M3 locked until real four-stage PASS.

## 9. Self-review

- This design improves observation; it does not redefine ambiguity as failure
  or permission.
- It never auto-resumes or retries an ambiguous effect.
- It preserves the existing timeout for genuinely active, nonterminal work.
- It derives stages only from product-defined finite reasons.
- It does not broaden persisted evidence or touch production source.
