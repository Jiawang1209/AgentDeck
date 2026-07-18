# M2c evidence to V1 validation migration matrix

## Purpose

This document maps useful safety and lifecycle evidence from
`tests/test_m2c_live_acceptance.py` into the layered V1 validation system. It is
a migration specification, not a claim that any replacement test, adapter,
Golden Mission, or product behavior already exists.

The legacy file currently contains 12,460 lines and 164 test functions. That
size is evidence of coupled concerns, not evidence that the file itself is a
product contract. Its useful invariants must survive under focused owners. Its
scenario-specific ceremony and harness-internal assertions must not survive as
V1 authority.

The old M2c gate is **not a release veto** and has no retry authority. Historical
failures are an evidence backlog to classify, not permission to rerun the fixed
four-stage ceremony. P0 performs no live execution and this document makes no
claim that the named new tests have been implemented.

## Validation layers and responsibilities

The layers are ordered by the kind of fact they prove, not by file size or by
how expensive a test is.

1. **unit/state machine** — pure, deterministic Mission, Task, Attempt,
   Permission, Handoff, Evidence, and Verification transitions. This layer owns
   precedence across simultaneous facts, terminal absorption, bounded recovery,
   immutable Mission versions, dependency release, and idempotency. It performs
   no filesystem, database, daemon, adapter, subprocess, network, ACP, or tmux
   work.
2. **contract/security** — schema closure, compact diagnostics, redaction,
   provenance scope, confirmation binding, permission lineage, and fail-closed
   handling of malformed or unknown fields. Contract fixtures contain no raw
   prompts, credentials, model output, or absolute paths.
3. **deterministic integration** — fake Leader/Worker/transport adapters with a
   real SQLite store, real ProjectDaemon application boundary, restart,
   reconnect, failure injection, and recovery. It is local, repeatable, and has
   no external network dependency. It proves that event, current state,
   revision, and provenance commit together and that public read models observe
   one authority.
4. **adapter conformance** — one reusable suite applied to every supported
   Codex, Claude, ACP, and CLI/PTY mapping. It proves identity and model
   provenance, structured proposal/update mapping, cancellation, permission
   correlation, ambiguity, sanitization, cleanup obligations, and zero-effect
   refusal without granting an adapter Mission authority.
5. **real adapter smoke** — narrow, opt-in checks of real authentication,
   process start, one structured event or proposal, one permission exchange
   where applicable, bounded diagnostics, cancellation, and cleanup. A smoke
   failure diagnoses a real adapter or environment boundary; smoke success does
   not prove the product journey.
6. **Golden Mission** — two real, bounded user journeys through the public
   product entry, using real external adapters. Golden A proves the normal
   natural-language, explicit-Leader, frozen-preview, one-confirmation,
   background collaboration, reconnect, and evidence-backed acceptance path.
   Golden B proves peer-review rejection and revision plus disconnect/recovery,
   sequential permission handling, human takeover/return-control, and safe
   completion or a precise actionable pause. Together they are the final V1
   journey gate; they are not one brittle internal harness.
7. **archive** — preserved historical logs, specifications, and the old harness
   after all replacement gates pass. Archived material is read-only evidence,
   never current product authority, a release gate, or authorization for a new
   live attempt.

Unit, contract/security, deterministic integration, adapter conformance, and
real adapter smoke evidence are necessary but cannot replace either Golden A or
Golden B. A fake adapter can prove orchestration determinism; it cannot prove
that the public product works with the real Codex and Claude boundaries.

## Legacy-to-V1 migration matrix

| Legacy concern | Useful invariant | New layer | New owner/test target | Preserve evidence | Delete old assertion when |
|---|---|---|---|---|---|
| Leader structured output and schema diagnostics | A Leader response is accepted only when it satisfies the closed proposal schema; unsupported native constraints, nonzero exit, cancellation, oversize output, timeout, and schema failure retain distinct compact reason codes. No provider fallback is inferred. | adapter conformance + real adapter smoke | Shared Leader adapter contract kit for Codex and Claude; one focused opt-in proposal smoke per official adapter | Closed diagnostic taxonomy, structured-output constraint mode, early terminal detection, and bounded attempt metadata | Both official adapters pass the same conformance cases and their focused real smoke proves the supported structured-output path; no replacement may depend on M2c helper internals |
| Semantic authority and confirmation digest | One confirmation binds one exact immutable Mission version, policy envelope, Leader identity, model identity, role, Task graph, and execution digest; pre-confirmation effects remain zero and stale confirmation fails closed. | unit/state machine + contract/security | MissionVersion/AuthorizationEnvelope unit suite and public preview/confirmation contract suite | Digest input categories, stale/drift refusal, exact scope, and zero-effect-before-confirmation cases | General Mission-version tests prove binding for variable DAGs and public confirmation contracts prove closed redacted projection without fixed scenario tokens |
| Semantic effect conflict | Completion evidence cannot coexist with missing permission, ambiguous effect, conflicting terminal facts, or an active incompatible Attempt; the safest transition wins. | unit/state machine + deterministic integration | Mission transition precedence table and daemon event-reconciliation integration suite | `completed_effect_without_permission`, active/failed Attempt conflicts, ambiguity, and all-facts precedence cases | Pure transition tests cover every conflict class and daemon integration proves the same result from one durable SQLite snapshot after restart |
| Semantic provenance and lineage identity | Attempt, Task, Mission version, permission, reply, Handoff, Evidence, actor, adapter, model, and revision identities must correlate; unrelated or ambiguous records cannot authorize progress. | contract/security + deterministic integration | Provenance schema/validator suite and SQLite transaction/replay integration suite | Missing, duplicate, unrelated, malformed, and ambiguous lineage failure cases | Closed provenance contracts and transaction-level replay/restart tests prove exact correlation without accepting or stringifying malformed records |
| Semantic failure classification | Malformed collections, unknown enum values, incomplete lineage, and disagreement between journal/current state produce deterministic fail-closed diagnoses rather than guessed success. | unit/state machine + contract/security + deterministic integration | Failure precedence unit table, closed diagnostic contract, and corrupt/restart SQLite integration matrix | Existing failure reason families and same-snapshot classification cases | Each retained reason has a named replacement case and injected corrupt/incomplete state cannot produce a success transition or leaked diagnostic |
| Permission lineage and sequential requests | One Attempt may produce a bounded sequence of permission requests. Each request has its own exact lineage, is confirmed or rejected once, preserves order, and cannot be skipped, reused, or settled from another Attempt. A Handoff is admitted only after the Attempt completes. | contract/security + deterministic integration | Governance permission contract and daemon permission-bridge integration suite | Approved-then-pending derivation, exact pending identity, consume-once decision, ambiguity refusal, and ordered settlement | General per-request lineage/order/bound tests pass for zero, one, and multiple requests; replacement no longer asserts a scenario-wide exact permission count |
| Exact permission cardinality | Preserve per-request lineage, ordering, boundedness, and terminal settlement; the total number of permissions is adapter- and Task-dependent. | unit/state machine + contract/security | Permission policy/state-machine parameterized suite | Historical counts as scenario evidence only | Replacement proves the invariants for variable request counts; delete every assertion that requires exactly two permissions or any other global cardinality |
| Handoff ordering | A downstream Task is not dispatchable until every required predecessor Attempt is terminal and its canonical Handoff is durably recorded with correlated Evidence. Duplicate replay is idempotent. | unit/state machine + deterministic integration | Task DAG release rules and daemon/SQLite Handoff transaction-recovery suite | Missing-Handoff waits, next-stage-before-Handoff refusal, canonical status, event order, and deduplication cases | Variable-DAG unit tests and crash/restart integration prove dependency release and Handoff atomicity without assuming four phases |
| Daemon disconnect, reconnect, and crash | Confirmed work continues under the sole ProjectDaemon writer after the client disconnects; reconnect returns a compact same-revision ProjectView; crash recovery resumes only from durable, unambiguous facts within bounds. | deterministic integration | ProjectDaemon background Mission, reconnection, crash-matrix, lease, and SQLite recovery suites | Background continuation, bounded wait, restart, consume-once, compact projection, and ambiguity refusal cases | Real SQLite failure-injection covers transaction interruption and restart, reconnect sees the same authority/revision, and Golden B exercises a real disconnect/reconnect journey |
| ACP transport | ACP updates, permissions, cancellation, completion, and ambiguity map into transport-independent typed events with bounded redacted fields. ACP never owns Mission transitions. | adapter conformance + real adapter smoke | ACP mapping/transport conformance kit plus one focused real ACP lifecycle smoke | Protocol bounds, update mapping, sequential permissions, ambiguity, cancellation, and cleanup cases | Conformance passes against the supported ACP adapter and the focused real smoke proves auth/start/event/permission/cleanup; both Goldens still remain independently required |
| tmux and CLI/PTY transport | tmux remains visible observation, human takeover, and explicitly governed fallback. Pane text or readiness is never authority for completion, permission, Handoff, or success. CLI/PTY fallback is admitted only after disclosed route selection and safe-effect proof. | adapter conformance + real adapter smoke | CLI/PTY transport conformance, tmux observation/takeover contract, and focused process smoke | Exact-socket cleanup, bounded capture, route disclosure, no silent fallback, prompt-semantic equivalence, and non-authority cases | Conformance and focused smoke pass, and no Mission transition reads pane text as truth; tmux visibility/takeover remains retained rather than deleted |
| Takeover and return-control | Takeover durably changes session control without broadening Mission scope; scheduling for that session pauses; return-control verifies the same Attempt/session authority before bounded autonomous recovery. | deterministic integration + Golden Mission | Daemon governance/recovery integration suite and at least one real Golden B exercise | Same-attempt identity, authority-drift refusal, pause/resume semantics, and compact audit lineage | Deterministic restart tests prove control ownership and Golden B performs a real takeover/return through the public entry with auditable events |
| Redaction and bounded diagnostics | Errors and projections expose only allowlisted stages, reason codes, bounded counts, stable opaque identities, and hashes where needed. They never expose credentials, raw prompts, terminal transcripts, model output, or absolute paths. | contract/security + adapter conformance | Shared diagnostic validator and hostile-payload conformance cases for every adapter | Path-free permission diagnostics, no terminal/PTY leakage, malformed-ledger closure, and compact cleanup failure cases | Hostile sentinel tests pass at every public contract and adapter boundary, including failure paths, without relying on pytest rendering behavior |
| Process and probe cleanup | Spawn, timeout, cancellation, interrupt, probe, and parent-exit paths close descriptors, terminate only owned process groups/sockets, bounded-drain output, and report residual ownership compactly. | real adapter smoke infrastructure + adapter conformance | Shared bounded process runner used by all real smokes, with deterministic local conformance/failure-injection tests | PTY spawn failure, child-after-leader-exit, group-kill failure, flood deadline, exact tmux socket, probe cleanup, and interrupt cleanup cases | Shared infrastructure passes deterministic ownership/failure injection and each real smoke uses it; no provider-specific harness duplicates cleanup authority |
| Global PATH and tool discovery | Readiness resolves the explicitly selected supported tool identity, version/capability, authentication state, and launch mapping without mutating project or global configuration or guessing a fallback. | adapter conformance | Codex/Claude/ACP/CLI readiness conformance suite | Read-only probe behavior, symlink/executable drift, capability detection, logged-out diagnosis, and no global-config access | Each supported adapter passes the same readiness contract and public setup reports actionable compact blockers without frozen machine-specific paths |
| Fixed `implementation -> review -> revision -> acceptance` stages | Preserve dependency ordering, peer review, rejection/revision, evidence-based acceptance, and bounded attempts, but allow a dynamic Task DAG. | unit/state machine + deterministic integration + Golden Mission | General Mission DAG suite, daemon scheduling integration, and two real public-entry Golden Missions A/B | The historical four-stage flow remains one useful example of review and revision | Dynamic DAG tests cover linear, branched, skipped-revision, and revision paths and both real Goldens pass; no universal four-stage or exact-step assertion remains |
| Frozen SHA/model/digest one-shot ceremony | Preserve immutable Mission-version confirmation and explicit adapter/model provenance; machine checkout SHA, one consumed live node, and historical authority digest are evidence about the old run only. | archive + contract/security | Historical M2c evidence bundle plus new Mission confirmation/provenance contracts | Frozen identifiers and results as dated, sanitized historical evidence | New general contracts prove current scope/provenance and both Goldens pass; archive the old seal without treating it as current preflight input or rerun authority |
| Pytest-output parsing | No product fact may depend on how pytest renders a failure, traceback, captured stream, plugin, or environment option. Product diagnostics must already be sanitized before reaching a test runner. | delete; replacement belongs to contract/security | Public diagnostic payload validator with hostile sentinel fixtures | The lesson that raw transcripts must not leak | Public diagnostic tests prove sanitized values at their source; delete subprocess pytest-report probes and never use pytest output as product authority |

## Gate and deletion rules

The migration follows these hard gates:

1. Inventory every useful M2c invariant and give it a named new owner/test target.
   An unmapped safety assertion blocks deletion, not product release and not a
   live retry.
2. Implement the lower layers with deterministic RED/GREEN evidence. P1 SQLite
   integration must align with the state-migration design's backup, transaction,
   fsync, cutover, rollback, restart, and corruption failure-injection matrix.
   A successful happy path is insufficient.
3. Apply one adapter conformance suite across the official Codex and Claude
   roles and supported ACP/CLI-PTY transport mappings. Provider-specific tests
   may extend the shared suite but cannot weaken it.
4. Run focused real adapter smokes only after deterministic and conformance
   gates pass. A smoke is diagnostic evidence, not acceptance of the natural-
   language product journey.
5. Run both real Golden A and Golden B through the public product entry. Unit,
   fake-adapter, conformance, and smoke results cannot substitute for either
   Golden.
6. Remove or archive the old M2c file only after **every** retained invariant
   has a named implemented replacement test, a coverage review finds no unique
   safety assertion stranded in the harness, and both real Goldens pass.

A Golden failure produces a layer-specific diagnosis and blocks the
corresponding release criterion. It does not revive the fixed four-stage
ceremony, authorize another historical one-shot node, or make the whole old
harness authoritative again. For example, an authentication/start failure
routes to readiness or real-smoke repair; a permission-lineage failure routes
to governance contract/integration repair; a reconnect failure routes to daemon
recovery; and a user-journey composition failure routes to the Golden product
boundary.

## Alignment with the P0 inventory and SQLite migration

This matrix applies the Task 5 inventory rule that retained behavior does not
mean retained ownership, file placement, or authority. The inventory's archive
gate for the M2c mega-harness and removal gate for fixed phase/count assertions
remain controlling: nothing is deleted simply because a proposed destination
appears in this table.

It also applies the Task 4 SQLite migration gate. Deterministic integration uses
the sole ProjectDaemon writer and real SQLite transactions, and must inject
backup, database, sidecar, checkpoint/close, rename, fsync, activation,
rollback, and restart failures. At most one structured-state authority may
accept mutations, and a ProjectView must never serve quarantined or ambiguous
state. This test migration neither implements that store nor weakens its
failure-injection acceptance criteria.

## P0 boundary

P0 only preserves and classifies evidence. It does not execute the old live
test, call a provider, open ACP or tmux, start a ProjectDaemon, authenticate an
adapter, or claim that conformance, smoke, Golden, SQLite, or V1 Mission tests
already exist. Examples and future fixtures must use opaque identities and
sanitized facts; they must not embed absolute paths, raw prompts, secrets,
credentials, terminal transcripts, or model output.
