# AgentDeck Leader Semantic Authority Design

**Date:** 2026-07-15

**Status:** Human-approved design; implementation requires a separately reviewed TDD plan

**Milestone:** Phase 3 M2c closure

**Evidence authority:** `1a22618ba083a76f4a21ffc7ebc7a3e513e4aae6`

**North star:** `docs/roadmap/product-north-star.md`

## 1. First principles

AgentDeck must let a user describe one goal in ordinary language, review one
Mission, confirm it once, disconnect, and later return to work that is complete
or safely paused. AgentDeck—not a Leader model, Worker, ACP session, or tmux
pane—owns frozen Mission authority, scheduling, permission, lineage, recovery,
and audit.

A normal LLM can often write a correct implementation-review-revision-
acceptance plan. That ability is necessary but is not AgentDeck's durable
product value. Model output is probabilistic and may omit a required object,
literal, transition, or constraint. A prompt also cannot prove after restart,
reconnect, transport change, or human takeover that the work still matches the
scope the user confirmed.

AgentDeck therefore does not replace model reasoning. It supplies the control
plane around that reasoning:

- the user message is root authority;
- Leader output is a proposal, not execution authority;
- one exact preview confirmation freezes semantic scope;
- daemon, ACP, and tmux may execute only that frozen scope;
- recovery and audit can verify the same authority without replaying model
  reasoning or scraping terminal text.

This design must also avoid rebuilding a general natural-language understanding
system. Deterministic extraction is deliberately conservative. Open semantics
remain a Leader proposal and become authority only through the explicit Mission
preview confirmation.

## 2. Current evidence and problem statement

The M2c capability probe blocker is closed. Frozen commit
`1a22618ba083a76f4a21ffc7ebc7a3e513e4aae6` passed the focused non-live harness
with `97 passed, 1 skipped`, passed two independent full-suite runs with
`3406 passed, 2 skipped`, and produced a read-only preflight with `ready=true`
and `blockers=[]`.

Exactly one approved live attempt then stopped before confirmation with
`native_schema_task_authority_invalid`. The closed seven-field projection had
only `revision_transition=false`. The supported conclusion is limited to this:
the Leader-generated revision task did not simultaneously preserve both
`draft-v1` and `accepted-v2`. The evidence does not identify which token was
absent, claim both were absent, or explain why the Leader output lost the
semantic requirement.

The snapshot contained one plan and one Mission but zero attempts, permissions,
replies, and handoffs. No ACP, permission, Worker, tmux, scheduler, or artifact
effect was reached. The active problem is therefore Leader task semantic
authority, not transport or runtime permission handling.

The existing `leader-plan/v1` native JSON Schema freezes JSON shape, selected
Workers, step count, roles, and approval fields. Each `task` remains an
unconstrained non-empty string. Native structured output can prove shape, but
it cannot prove that a free-form task preserved the user's state transition.

## 3. Goals

This slice must:

1. introduce a general, versioned semantic-authority model rather than hardcode
   the M2c literals into production code;
2. preserve deterministically recognizable user requirements before Leader
   planning;
3. make the Leader reference required authority and propose bounded additions
   instead of directly authoring executable Worker tasks;
4. validate complete coverage, correct phase and Worker binding, transitions,
   conflicts, scope additions, and sensitive-value boundaries before preview;
5. compile deterministic Worker tasks from frozen authority;
6. bind confirmation, dispatch, recovery, ledger, and handoff provenance to
   stable authority and compiled-task hashes;
7. allow at most one same-Leader, same-provider, same-model regeneration inside
   the original deadline;
8. fail closed without retaining raw provider output or leaking sensitive
   content;
9. keep existing Mission, plan, CLI, ACP, tmux, permission, and runtime safety
   behavior compatible unless this design explicitly changes it;
10. close M2c only after a new frozen preflight and one separately authorized
    real four-stage live acceptance pass.

## 4. Non-goals

This slice does not:

- build a general-purpose NLP parser;
- let AgentDeck invent or silently repair missing user semantics;
- make Leader prompts, skills, memory, or ACP metadata into authority;
- replace Mission confirmation or runtime permission gates;
- add A2A, remote execution, global roaming, notifications, a new provider,
  marketplace behavior, automatic skill or memory activation, GUI/TUI redesign,
  or a terminal emulator;
- change ACP or tmux into the semantic source of truth;
- retry a live acceptance automatically.

## 5. Chosen architecture

The approved direction is a general semantic-authority control plane:

```text
user message
    |
    v
required authority -- deterministic, conservative, non-LLM
    |
    v
Leader semantic candidate -- structured proposal and authority references
    |
    v
preview validation -- coverage, conflict, drift, sensitivity, unresolved
    |
    v
AgentDeck task compilation -- deterministic executable instructions
    |
    v
one exact human confirmation
    |
    v
frozen semantic authority -- daemon / ACP / tmux / recovery / audit
```

The rejected alternatives are:

- stronger regex or token checks on free-form `task` strings, because strings
  would remain the authority substrate;
- structured effects plus Leader-authored executable tasks, because the two
  representations could conflict;
- AgentDeck silently patching a failed Leader plan, because the validator would
  become an unaudited planner.

## 6. Authority lifecycle

Semantic authority has four states.

### 6.1 Required

`required` requirements come from facts the user expressed explicitly and the
existing deterministic intent boundary already froze, including:

- selected Worker ids and step count;
- explicit phase or Worker order;
- named files, project-relative paths, and named targets;
- quoted, code-formatted, or explicitly assigned exact values;
- explicit operations such as create, read, review, update, and verify;
- explicit state transitions such as “change A from X to Y.”

The Leader cannot delete, weaken, rename, or reinterpret required authority.

### 6.2 Proposed

The Leader may propose step decomposition, verification, dependencies, risks,
and additional effects. Proposed effects are not authority and must be shown in
their own preview section. They become part of frozen scope only when the human
confirms that exact preview.

An ordinary project-local, non-sensitive proposal with an explicit operation,
target, and verification may reach preview as a visible scope addition. A
proposal is blocked before preview when it cannot be represented safely, hides
its target or operation, contradicts required authority, escapes the project,
contains sensitive plaintext, or attempts to present an elevated runtime
permission as already granted. Thus `semantic_scope_addition_blocked` is a
fail-closed classification for an unreviewable proposal; it does not prohibit a
well-formed, visibly separated proposal from receiving human authority through
the exact preview confirmation.

### 6.3 Unresolved

Any fact that cannot be interpreted uniquely and safely becomes an `unresolved`
item. An unresolved draft cannot call the Leader or create a confirmable Mission
preview. ConversationShell asks one precise clarification question instead of
guessing.

### 6.4 Frozen

Confirmation freezes the exact semantic authority, compiled tasks, policy
snapshot, and preview generation. Any later semantic change invalidates the old
confirmation and requires a new preview.

## 7. Semantic authority model

The canonical authority schema is `mission-semantic-authority/v1`.

```json
{
  "schema_version": "mission-semantic-authority/v1",
  "source_message_hash": "sha256:...",
  "requirements": [
    {
      "requirement_id": "req_...",
      "kind": "state_transition",
      "target": "artifact.txt",
      "operation": "update",
      "before": {"content_equals": "draft-v1\n"},
      "after": {"content_equals": "accepted-v2\n"},
      "phase": "revision",
      "agent_id": "claude-worker",
      "sensitivity": "ordinary"
    }
  ],
  "unresolved": []
}
```

Every requirement has a stable opaque `requirement_id`. Supported requirement
kinds are a closed set for v1. The implementation plan must define the exact
field set and validator for each kind; unknown kinds fail closed.

A state transition is atomic. Its `before` and `after` conditions cannot be
distributed across unrelated requirements or separate steps. For the M2c
fixture, revision authority must retain both `draft-v1\n` and
`accepted-v2\n` inside one transition requirement.

## 8. Conservative deterministic extraction

`SemanticAuthorityExtractor` is a pure function. It does not call a provider,
write state, inspect tmux, read ACP traffic, dispatch work, or infer permission.

It may recognize only explicit grammar covered by deterministic tests. Initial
v1 coverage is limited to:

- named file or project-relative path targets;
- quoted, code-block, or explicit assignment values;
- explicit create/read/review/update/verify verbs in supported Chinese and
  English forms;
- explicit `from/before -> to/after` transitions;
- existing deterministic phase, Worker-selection, and count authority.

Ambiguous pronouns, multiple possible targets, missing transition endpoints,
unsupported syntax, absolute external paths, or suspicious sensitive values
produce `unresolved`. They never receive a best-effort interpretation.

This extractor is not expected to understand every ordinary-language goal.
When no exact requirement can be safely extracted, the Leader may still propose
a structured plan for human review, but it cannot claim that unparsed details
were deterministically preserved. Any explicit claim that cannot be covered
must remain visible as unresolved and block confirmation until clarified.

## 9. Leader semantic candidate

Natural-language Mission planning moves to a versioned semantic candidate
schema. The Leader no longer supplies the authoritative Worker `task` string.
A step has the following conceptual shape:

```json
{
  "step": 3,
  "agent_id": "claude-worker",
  "role": "implementation",
  "phase": "revision",
  "authority_refs": ["req_..."],
  "proposed_effects": [],
  "verification": "read exact bytes after update",
  "risk": "low",
  "requires_approval": true
}
```

The native schema still freezes selected Workers, step count, roles, and
approval. Post-schema semantic validation freezes meaning that JSON Schema
alone cannot express portably.

The plan stored after validation may contain a compiled compatibility `task`
field for existing downstream consumers. That field is produced only by
AgentDeck's compiler; it is never accepted from the Leader candidate.

## 10. Semantic validation

`SemanticAuthorityValidator` is pure and must enforce:

- every required requirement is covered exactly as allowed;
- a requirement is bound only to its allowed phase and Worker;
- atomic transitions preserve both endpoints;
- references exist and are not silently duplicated or dropped;
- effects do not contradict another frozen requirement;
- Leader proposals are separated from required authority;
- safe, explicit project-local scope additions remain visible proposals until
  confirmation, while unreviewable additions fail closed;
- destructive, credential, publish, external-send, or otherwise elevated
  proposals remain subject to existing runtime permission policy;
- unresolved or sensitive plaintext prevents confirmation;
- schema, authority, candidate, and compiled projections agree.

The validator never repairs a candidate. On the first retryable semantic
failure, the same Leader receives only a closed diagnostic and must return a
complete replacement candidate. The second failure terminates planning. There
is no local partial patch and no provider, model, or transport fallback.

## 11. Deterministic Worker task compilation

`WorkerTaskCompiler` converts only the current step's validated effect contract
into an executable task. The authoritative section is generated in a stable
field order with explicit escaping and canonical newline handling.

For the M2c revision step, the result is conceptually:

```text
Authoritative operation: update
Target: artifact.txt
Required precondition: content equals draft-v1\n
Required postcondition: content equals accepted-v2\n
Verification: read exact bytes after update
Forbidden: modify unrelated files or broaden scope
```

Leader verification, risk, and bounded explanatory guidance may appear in a
separate non-authoritative section. Guidance cannot redefine the operation,
target, precondition, postcondition, required literal, forbidden effect, or
permission class.

The compiler must be deterministic. The same semantic step must produce the
same bytes and `semantic_step_hash` across initial preview, daemon dispatch,
restart, reconnect, and recovery. A mismatch pauses the Mission; it never
causes automatic regeneration.

## 12. Sensitive values and projection boundaries

Ordinary filenames, relative paths, and non-sensitive version markers may be
stored exactly in frozen authority. Credentials, tokens, and secrets must be
represented only by typed references such as `secret_ref`; their values never
enter plan, Mission JSON, events, ProjectView, prompt provenance, or trace.

Suspicious plaintext that cannot be safely classified becomes unresolved.
Hashing a secret value is not an acceptable substitute for a reference because
low-entropy values may still be recoverable and the Worker needs an explicit
authorized secret-resolution mechanism.

The full non-sensitive frozen authority belongs only in the authoritative
Mission/plan record. ProjectView exposes a compact projection:

```json
{
  "schema_version": "mission-semantic-authority/v1",
  "state": "draft|blocked|preview|frozen",
  "authority_hash": "sha256:...",
  "requirement_count": 5,
  "proposed_effect_count": 1,
  "unresolved_count": 0,
  "compiled_step_count": 4,
  "blockers": []
}
```

Workbench, natural-language status, trace, and future clients consume this same
projection rather than reparsing state independently.

## 13. Preview and confirmation binding

A confirmable Mission preview shows separate sections for:

- user-required effects;
- Leader-proposed assumptions;
- verification and risk;
- unresolved items, which must be empty;
- semantic authority hash;
- compiled task hashes.

Confirmation binds the exact tuple:

```text
mission_id
plan_id
semantic_authority_hash
compiled_task_hashes[]
policy_snapshot_hash
preview_generation
```

Any change invalidates the confirmation. Mission confirmation freezes semantic
scope; it does not bypass ACP permission, runtime safety, tool authorization,
or approval policy.

## 14. Dispatch, handoff, and recovery

The daemon dispatches only the current step's compiled task and minimum
authority. It must not inject other steps' literals, effects, or secret
references. ACP and tmux receive the same task bytes and hash; transport is not
allowed to rewrite authority.

Message, reply, handoff, ledger, and trace provenance carry compact
`semantic_step_hash` data. A handoff may report results and evidence but cannot
authorize a new effect. A reported new target, state transition, or side effect
pauses the Mission as scope drift.

Recovery verifies:

- Mission snapshot authority hash;
- deterministic compiled task hashes;
- current Worker message `semantic_step_hash`;
- handoff relationship to the current step;
- the exact confirmation binding.

Disconnect/reconnect and takeover/return-control do not change frozen
authority. Any mismatch pauses the Mission with a closed failure classification.

## 15. Failure contract and audit

The closed failure-code set for this slice is:

```text
semantic_authority_unresolved
semantic_authority_sensitive_value
semantic_candidate_missing_requirement
semantic_candidate_duplicate_requirement
semantic_candidate_wrong_phase
semantic_candidate_wrong_worker
semantic_transition_incomplete
semantic_effect_conflict
semantic_scope_addition_blocked
semantic_candidate_schema_invalid
semantic_compilation_failed
semantic_compilation_drift
semantic_confirmation_stale
```

Diagnostics may contain only stage, closed code, schema version, authority hash,
opaque requirement id, requirement kind, step number, attempt count, and a
regeneration-allowed boolean. They must not retain raw stdout/stderr, raw
candidate JSON, full user messages, exact literals, secret values, absolute
paths, argv, temporary paths, environment variables, or ACP/tmux transcripts.

The append-only audit may record:

```text
semantic_authority_extracted
leader_semantic_candidate_rejected
leader_semantic_candidate_regenerated
mission_semantic_preview_created
mission_semantic_authority_frozen
worker_task_compiled
semantic_authority_drift_detected
```

Events contain only hashes, counts, closed codes, step numbers, and safe opaque
ids. They do not become a second authority store.

## 16. End-to-end flow

1. ConversationShell receives the user goal.
2. Existing deterministic intent logic freezes Workers, count, and explicit
   order.
3. The extractor creates required authority and unresolved items.
4. Unresolved authority returns a clarification mode without calling Leader.
5. LeaderRequest carries authority hash, compact requirements, fixed planning
   authority, and the original deadline.
6. The Leader returns a native-schema semantic candidate.
7. Semantic validation either accepts it or triggers one complete same-Leader
   regeneration.
8. A second failure terminates the turn without creating a plan or Mission.
9. The compiler creates deterministic step tasks and hashes.
10. AgentDeck creates one exact preview.
11. Human confirmation atomically freezes authority, tasks, policy, generation,
    plan, and Mission execution snapshot.
12. The daemon dispatches and recovers only against that frozen boundary.

## 17. TDD implementation slices

Implementation must follow five sequential slices.

### 17.1 Authority extraction

RED/GREEN tests cover Chinese and English explicit targets, literals, phases,
Workers, transitions, ambiguity, missing endpoints, and sensitive values. Tests
prove the extractor is pure and invokes no provider, state, tmux, or ACP path.

### 17.2 Leader candidate and dynamic schema

Tests cover exact Worker, count, role, phase, requirement coverage, and a full
mutation matrix for deletion, duplication, wrong phase, wrong Worker,
conflicts, and scope expansion. Codex and Claude native schema/provenance remain
exact. Regeneration is bounded to the same Leader once. Double failure creates
no plan, Mission, approval, message, job, or inbox.

### 17.3 Compilation and confirmation

Tests cover byte-deterministic output, atomic transition preservation, Unicode,
newlines, escaping, hostile literals, guidance isolation, exact confirmation
binding, stale-confirmation zero-write rejection, and old-record compatibility.

### 17.4 Dispatch, ledger, and recovery

Tests prove current-step minimum context, no cross-step or secret leakage, ACP
and tmux hash parity, compact lineage, handoff scope-drift pause, restart,
disconnect/reconnect, takeover/return-control, and one ProjectView projection.

### 17.5 Frozen M2c acceptance

Before live execution, the branch must pass focused tests, related Leader/
conversation/Mission/daemon/ACP/tmux suites, two independent full-suite runs,
compileall, diff checks, tracked-state checks, and a clean frozen commit. A new
read-only preflight must report `ready=true`, `blockers=[]`, and zero probe
writes without changing login, global configuration, or permissions.

Even then, live execution requires a new explicit human authorization. The
harness may make exactly one real attempt and must not retry automatically.

## 18. M2c live success criteria

The single real Mission passes only if all of these agree:

- phase order is implementation -> review -> revision -> acceptance;
- Worker order is Claude -> Codex -> Claude -> Codex;
- revision frozen authority contains one atomic transition from
  `draft-v1\n` to `accepted-v2\n`;
- dispatch uses the confirmation-bound compiled task hash;
- two explicit ACP permission pauses and resumptions behave correctly;
- tmux visibility, disconnect/reconnect, takeover, and return-control work;
- four canonical handoffs and three inter-stage lineage links are complete;
- final artifact bytes equal `accepted-v2\n`;
- Mission, ProjectView, ledger, trace, and snapshot agree;
- cleanup reports only facts actually observed;
- exactly one live attempt occurred.

Failure retains bounded evidence, leaves M2c **BLOCKED**, keeps M3 locked, and
returns to brainstorming -> spec -> plan. It does not authorize an in-place
repair and second live attempt.

## 19. Compatibility and rollout

Existing plans, Missions, workflows, and deterministic CLI commands remain
readable under their current versions. New semantic Mission records use an
explicit version and validation path. No old record is silently rewritten.

The implementation plan must identify every persisted and projected field,
contract version decision, migration/read-compatibility test, and downstream
consumer of compiled `task`. A compatibility field may be retained only when
AgentDeck, not the Leader, produces it.

M2c remains **BLOCKED** until the real success criteria pass. M3 remains locked.
