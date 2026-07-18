# AgentDeck V1 Validation Strategy

This strategy turns the AgentDeck North Star into repeatable release evidence.
It validates product semantics at the narrowest owning layer, keeps ordinary
commits deterministic and offline, and reserves real Codex and Claude runs for
bounded product evidence. A lower-layer PASS is a prerequisite for the layer
above it; it is never a substitute for the public-entry Golden Missions.

## Operational release gates

AgentDeck V1 has five operational release gates, in order:

1. **Deterministic commit gate.** Unit/state-machine, contract/security, and
   real SQLite plus ProjectDaemon plus fake-adapter deterministic integration
   checks run without network access.
2. **Adapter conformance.** The same behavioral suite applies to Codex and
   Claude in Leader and Worker roles and across their supported ACP and CLI/PTY
   transport mappings.
3. **Opt-in isolated real adapter smoke.** The narrowest real readiness,
   startup, event, permission where applicable, cancellation, and cleanup path
   runs in a disposable project.
4. **Golden A.** A real normal-path collaboration runs from bare `agentdeck`
   through accepted, evidence-backed completion.
5. **Golden B.** A real correction-and-recovery collaboration runs from bare
   `agentdeck` through accepted, evidence-backed completion.

The implementation inventory may list unit/state-machine, contract/security,
and deterministic integration separately because those are different evidence
owners. Operationally they are three sublayers of the first release gate, not
three extra gates. Likewise, `archive` describes the evidence lifecycle after
named replacements exist; it is not an execution-validation layer.

Assertions at every gate target state transitions, authority, lineage,
observable effects, and contract fields. Tests must not depend on model prose,
terminal wording, private database rows, fixed stage cardinality, or fixed
permission cardinality unless that cardinality is itself a published product
contract.

## Deterministic commit gate

Every commit must pass this gate with networking disabled and with fake
adapters standing in for real providers. It contains three named sublayers.

### Unit and state-machine checks

These checks own Mission and Task transitions, immutable Mission-version
identity, the one-time confirmation binding to one exact version and digest,
finite permission state transitions, bounded retry/revision/recovery budgets,
AgentDeck-owned Handoff construction, verification-owned completion, and
idempotent command/event processing. They prove that cancellation, timeout,
refusal, ambiguous effects, takeover, return of control, and reconnect cannot
invent authority or skip required transitions.

### Contract and security checks

These checks own public request/response schemas, ProjectView projections,
approval and permission correlation, Handoff and Evidence provenance,
redaction, diagnostic allowlists, effect ambiguity, safe retry decisions, and
the absence of silent transport or model fallback. They assert semantic fields
and normalized reason codes rather than parsing terminal or model text.

### Deterministic SQLite and daemon integration

These checks use a real SQLite database, a real ProjectDaemon, and fake Codex
and Claude adapters. They must prove:

- ProjectDaemon is the sole structured-state writer, and a client, adapter,
  migration helper, or compatibility projection cannot become a second writer.
- Current state, events, revisions, idempotency keys, confirmation authority,
  permission lineage, Handoff, and Evidence metadata commit atomically in the
  owning transaction.
- Mission transitions, exact-version confirmation, governance, Handoff,
  verification, recovery, redaction, and idempotent replay survive process
  restart without accepting an unauthorized transition.
- Migration Preview is read-only and reports source identity, target identity,
  blockers, verification scope, and the exact confirmation binding without
  changing either authority.
- Confirmed migration creates a complete backup and manifest, builds and
  verifies a same-filesystem temporary database, consolidates and closes WAL
  sidecars, fsyncs backup files and their directory, fsyncs the temporary
  database, performs the atomic install, and fsyncs the containing
  `.agentdeck` directory.
- Activation remains three-state:
  `legacy_active`, `sqlite_installed_quarantined`, and `sqlite_active`. A file
  rename or target-path presence alone never activates SQLite.
- Failure injection covers backup, temporary-database construction, WAL
  consolidation, integrity verification, file fsync, directory fsync, install,
  activation, and rollback crash windows. Recovery always finds at most one
  mutation authority.
- Rollback verifies the exact legacy generation, durably retires the failed
  SQLite authority, restores and fsyncs the legacy image, and activates it only
  after verification. It never creates dual writes or silently falls back.
- `project-view/v1` and `project-view/v2` project from the same active authority
  and the same committed revision, including immediately before and after
  migration activation and rollback.

Any nondeterminism in this gate is a test-design defect. Default commit checks
must not discover provider readiness, start a provider, inspect a real tmux
pane, or contact the network.

## Adapter conformance

One shared conformance suite must be parameterized over Codex and Claude, each
in Leader and Worker roles, and over each supported ACP or CLI/PTY transport
mapping. A provider-specific test may supplement this suite but cannot replace
it.

The suite owns:

- explicit agent identity, selected model identity, and immutable provenance;
- structured Mission proposal and normalized event schemas;
- permission request, response, attempt, and side-effect correlation;
- timeout, cancellation, non-zero exit, oversized output, schema failure, and
  cleanup behavior;
- semantic-effect ambiguity and the distinction between safe retry and required
  reconciliation;
- allowlisted, redacted diagnostics that do not expose prompts, secrets,
  filesystem paths, terminal contents, or raw model output; and
- fail-closed routing with no silent model, provider, or transport fallback.

tmux remains a human observation, takeover, and compatibility fallback surface.
It is not Mission authority, permission authority, completion authority, or a
substitute for adapter events.

## Real smoke boundary

Real adapter smoke is explicit opt-in evidence and always uses a disposable,
isolated project. It starts with read-only readiness checks for the selected
CLI binary, exact model, authentication, and required ACP capability. Only
after readiness succeeds may it exercise the narrowest real path:

1. authenticate through already configured user state;
2. start the selected adapter route;
3. receive one normalized event;
4. correlate a permission exchange when that route emits one;
5. cancel within a bounded deadline; and
6. prove child-process, PTY, tmux, temporary-state, and daemon cleanup.

The smoke itself is unconditionally free of global mutation. It never installs,
reinstalls, or upgrades tools or adapters; logs in or out; changes global
authentication, configuration, provider, or model selection; modifies shell
PATH; or changes package state. Any separately human-authorized setup is a
distinct pre-smoke administrative action outside the smoke and outside its
evidence. After setup, the smoke only consumes configured state read-only,
except for the isolated disposable-project effects that the smoke explicitly
owns and cleans up. After a diagnosed root cause is fixed, a new isolated smoke
may be run safely; there is no historical single-use authorization ceremony.

A smoke PASS proves only the selected adapter route can perform that narrow
interaction. It does not prove orchestration, recovery, peer review, Mission
acceptance, Golden A, or Golden B.

## Golden A

Golden A is the normal real collaboration journey:

- **Leader:** Codex.
- **Implementation Worker:** Codex.
- **Independent Review Worker:** Claude.

The journey begins with bare `agentdeck`, explicitly selects the Leader, and
accepts a natural-language goal. The user can edit the frozen Mission Preview,
then confirms exactly once the exact Mission version and digest that will run.
Any edit creates a new version and invalidates confirmation of the earlier one.

The ProjectDaemon continues the Mission after the client closes. On reconnect,
the public conversation and activity stream project the same authoritative
revision. Workers follow a finite, sequential, governed permission lineage;
AgentDeck, not either Worker, owns each Handoff and its provenance. The
implementation is tested, independently reviewed, and represented by Evidence
that verification can evaluate.

Golden A passes only when verification owns the completion decision, the
Mission reaches accepted completion, and the user can read a final result that
links the implementation, tests, peer review, Handoff, and Evidence. A provider
reply, a successful smoke, or a Worker self-report is insufficient.

## Golden B

Golden B is the real correction-and-recovery journey:

- **Leader:** Claude.
- **Implementation Worker:** Claude.
- **Independent Review Worker:** Codex.

It has the same public-entry semantics as Golden A: bare `agentdeck`, explicit
Leader selection, a natural-language goal, an editable frozen Mission Preview,
and one-time confirmation bound to the exact Mission version and digest. It
also proves background continuation, client close and reconnect, finite
sequential permission lineage, AgentDeck-owned Handoffs, tests, independent
review, Evidence, and a readable final accepted result.

The independent review must reject an inadequate implementation and drive a
bounded revision before re-review. The journey must also encounter a real
governance or recovery checkpoint, such as permission refusal requiring new
authority or replanning, human takeover followed by explicit return of control,
client disconnect/reconnect, or daemon restart recovery. The selected fixture
must exercise permission governance and recovery/takeover behavior without
asserting a fixed number of permission requests or internal stages.

Each intermediate pause must be resolved by explicit authority, reconciliation,
or bounded replanning before execution continues. Golden B passes only after
the revised work is verified and reaches evidence-backed completion and
acceptance. An unresolved pause, environment prerequisite failure, execution
failure, or generic terminal success is not a Golden PASS.

## Failure taxonomy

The three externally visible failure-state families have disjoint meanings.

### BLOCKED before execution

`BLOCKED` is reserved for a missing environment prerequisite discovered before
an execution or adapter attempt starts: the selected CLI binary, exact model,
authentication, required ACP capability, or another declared readiness
requirement. It must carry an actionable next step. `BLOCKED` is not a Mission
runtime transition and can never count as a Golden PASS.

### FAILED after an attempt starts

Once an execution or adapter attempt has started, unrecoverable faults use a
stable stage-specific `FAILED` result. Supported owning stages are
`leader_proposal`, `worker_start`, `task_execution`, `handoff`, `verification`,
`transport`, `permission`, and `recovery`. `FAILED` means the current policy and
budget cannot recover the attempt; it does not grant permission to retry an
ambiguous effect.

### PAUSED as a product state

`PAUSED` is a durable product state for new authority, an explicit human
action, ambiguous side-effect reconciliation, exhausted bounded authority, or
budget/scope drift. Resume requires the recorded action, reconciliation, or a
new Mission version as appropriate. Neither a Worker nor an adapter may convert
`PAUSED` into completion; completion remains verification-owned.

All failure and pause diagnostics are allowlisted and redacted. The durable
diagnostic contains only:

- `stage`;
- `reason_code`;
- `attempt_count`;
- `constraint_mode` or `route_mode`;
- `side_effect_state` as `none`, `known`, or `ambiguous`;
- `retry_safety` as `safe`, `unsafe`, or `requires_reconciliation`; and
- `next_action`.

It must not contain prompts, secrets, filesystem paths, terminal contents, raw
CLI output, or raw model output. A failure routes to its owning validation
layer. After the root cause is fixed, the relevant isolated smoke or Golden may
be rerun. This is ordinary evidence production, not a revival of the M2c
ceremony.

## Release gate

Release evidence accumulates by product phase:

- **Every commit:** the deterministic commit gate passes offline.
- **P1 exit:** a deterministic fake-adapter Golden passes through public daemon
  APIs using real SQLite with one ProjectDaemon writer. Evidence covers client
  and daemon restart, rejection of stale or unauthorized mutations, migration
  preview/confirm/verify/rollback failure injection, and `project-view/v1` plus
  `project-view/v2` projecting one authority and committed revision. P1 does not
  invoke a real model or tmux.
- **P2 exit:** a complete fake-adapter session passes from bare `agentdeck`
  through a natural-language goal, explicit Leader selection, Mission Preview
  edit, exact-version-and-digest confirmation, activity projection, client
  close/reopen, and reconnect-cursor resume against one consistent state. The
  legacy script facade reaches the same service and authority. No real adapter
  is required for P2 exit.
- **P3 adapters:** the shared conformance suite passes, followed by bounded real
  smokes for both official Agents and each supported release route.
- **P4 product:** both real Golden A and Golden B pass through the bare
  `agentdeck` public entrypoint.
- **P5 learning and release:** final Golden A/B evidence is current, fresh
  installation and confirmed migration/rollback evidence pass, and governed
  Skill, Memory, and learning suggestions preserve preview and explicit
  confirmation safety.

M2c is historical evidence, not a release veto and not retry authority. Its old
mega-harness may be archived only after every retained invariant has a named
replacement and both real Goldens pass. A Golden failure blocks that Golden's
release criterion and emits an owning-layer diagnosis; it does not by itself
declare the entire architecture invalid.
