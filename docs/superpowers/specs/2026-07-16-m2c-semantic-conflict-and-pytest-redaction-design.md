# M2c Semantic Target Exclusivity and Pytest Redaction Design

**Date:** 2026-07-16

**Status:** Human-approved design; written specification awaiting human review

**Milestone:** Phase 3 M2c live-acceptance blocker closure

**Evidence authority:** frozen implementation
`9db5b476f885cfcf68a55cbf59673a2d908d3fce`, evidence commit `1bb4d452`

**North star:** `docs/roadmap/product-north-star.md`

## 1. First principles

AgentDeck, not the Leader model, owns Mission authority. A user should be able
to describe one goal, review one frozen Mission, confirm it once, disconnect,
and later return to governed multi-agent work whose scope is still provable.

Native structured output is necessary but insufficient. JSON Schema can freeze
the shape of a Leader candidate, selected Workers, step count, roles, and
authority-reference fields. AgentDeck's semantic validator must decide whether
the candidate preserved required user authority and whether any Leader proposal
is a genuinely new, reviewable scope addition.

The same authority boundary applies to diagnostics. Raw PTY bytes are process-
local observation data, not Mission truth. They must not become durable state,
an exception payload, a pytest traceback value, a validation report, or an
audit transcript.

This slice therefore has two bounded responsibilities:

1. make required targets exclusive from Leader-proposed targets and make each
   failure precisely, safely regenerable;
2. make the default pytest failure-report path transcript-safe, not merely the
   final exception JSON.

Neither responsibility changes Worker execution, permission, ACP, tmux,
daemon, handoff, or Mission confirmation authority.

## 2. Current evidence

Leader Preview observability is frozen at
`9db5b476f885cfcf68a55cbf59673a2d908d3fce`. The complete non-live M2c harness
passed `186 passed, 1 skipped in 42.69s`; two independent full suites passed
`4219 passed, 2 skipped` in `185.64s` and `191.59s`. The one explicitly
authorized read-only preflight for Leader model `gpt-5.5` passed with
`ready=true`, `blockers=[]`, and all four tools ready.

After separate authorization, the sole live node ran exactly once. It exited
with `1 failed in 52.39s` and was not retried. The first durable terminal was:

```text
stage=live_acceptance
code=leader_schema_before_preview
leader_terminal.stage=schema
leader_terminal.diagnostic_code=semantic_effect_conflict
leader_terminal.attempt_count=2
leader_terminal.constraint_mode=native_json_schema
```

The same snapshot contained zero Plans, Missions, attempts, permissions,
Worker replies, and handoffs. The run did not reach Preview, confirmation,
daemon admission, ACP, tmux Worker execution, permission, reconnect,
takeover/return-control, handoff, lineage, or artifact effects.

The evidence does not reveal which target or Candidate fragment caused the
conflict and must not be retroactively interpreted. Source inspection does
show that the current closed code combines two validator conditions:

- two proposed effects use the same target;
- a proposed effect uses a required target with a different operation.

The validator compares all proposals with the complete Mission authority.
The M2c authority intentionally uses one target across implementation, review,
revision, and acceptance with different operations. The native schema permits
`proposed_effects`, and the current prompt says only that ordinary project-local
proposals are reviewable. A Leader can therefore produce structurally valid
JSON that re-proposes an already required target and is rejected after schema
validation. The second request currently receives only the umbrella code, not
an exact safe correction rule.

The live failure also exposed a separate reporting boundary. The
`_LiveHarnessFailure` JSON contained only allowlisted terminal, ledger,
cardinality, and PTY identity fields. Pytest nevertheless rendered the default
dataclass representation of the `capture` argument in a traceback frame.
Because `_PtyTail.tail` participates in the default `repr`, raw bounded tail
bytes appeared in ephemeral test output. The existing leakage test asserts
only over `str(exception)` and does not exercise pytest report rendering.

## 3. Goals

This slice must:

1. define required-target ownership independently of operation or phase;
2. prohibit any Leader proposal from reusing a required target;
3. require proposed targets to be unique across the complete Candidate;
4. preserve valid, ordinary, project-local proposals for genuinely new
   targets;
5. replace new umbrella conflict emissions with two precise closed codes;
6. preserve historical `semantic_effect_conflict` validation compatibility;
7. give the same Leader exactly one code-specific, transcript-free
   regeneration opportunity;
8. keep CLI-backed and API-backed Leader behavior byte-semantically aligned
   at the semantic policy boundary;
9. prevent `_PtyTail.tail` from entering default object representations;
10. prove transcript safety through a real default pytest report subprocess;
11. preserve bounded PTY count, truncation, and SHA-256 identity evidence;
12. complete deterministic verification and freeze a new implementation before
    any separately authorized preflight;
13. stop before every future live Mission unless the human separately names
    the new frozen SHA and exact model.

## 4. Non-goals

This slice does not:

- modify `SemanticAuthorityExtractor` grammar or infer additional user intent;
- remove the Leader's ability to propose genuinely new targets;
- force every `proposed_effects` array to be empty;
- add JSON-Schema `not` tricks whose provider support is not already part of
  the native-schema contract;
- silently delete, rewrite, merge, or convert Leader proposals;
- synthesize missing `authority_refs` or repair a Candidate locally;
- change `WorkerTaskCompiler`, Mission confirmation, frozen authority hashes,
  ProjectView authority, or recovery semantics;
- change Provider timeout, model, MCP startup, login, authentication, network,
  or fallback behavior;
- change ACP, tmux, daemon, permission, takeover, handoff, or artifact logic;
- persist PTY text, model output, stdout, stderr, prompts, argv, environment, or
  sensitive paths;
- run a real provider, read-only preflight, ACP/tmux Worker, or live Mission as
  part of design or ordinary implementation verification;
- claim M2c PASS or unlock M3.

## 5. Selected approach

The approved approach is semantic validation plus precise regeneration.

```text
native JSON Schema
  -> structural Candidate
  -> AgentDeck semantic validator
       -> required-target exclusivity
       -> proposal-target uniqueness
  -> pass, or one precise closed rejection
  -> same Leader / model / schema / authority regeneration once
  -> complete replacement Candidate
  -> Mission Preview
```

JSON Schema remains responsible for fields, types, counts, fixed Workers,
roles, phases, reference domains, risk, and approval. The semantic validator
remains responsible for cross-object meaning that structured-output schemas do
not portably express.

AgentDeck never transforms an invalid Candidate into a valid one. A successful
second Candidate is authored by the same Leader under the same frozen request
authority and then independently validated from the beginning.

## 6. Target ownership semantics

### 6.1 Required targets are exclusive

After existing authority and proposal shape validation, the validator builds a
set of every validated required target. Target identity is the exact validated
target string already accepted by `mission-semantic-authority/v1`.

This slice does not resolve symlinks, access the filesystem, normalize paths
beyond existing semantic-authority behavior, or compare operations to decide
ownership. If a target appears in required authority, it is required-owned for
the complete Mission Candidate, regardless of:

- phase;
- Worker;
- requirement kind;
- operation;
- number of requirements that refer to it.

A Leader step must use `authority_refs` to express work on a required-owned
target. The same target must not appear in any `proposed_effects` item.

### 6.2 Proposed targets are Mission-wide unique

A proposed target that is not required-owned may appear at most once across all
steps. Uniqueness is Mission-wide rather than per-step because a Proposal is a
new scope addition. Repeating the same new target in multiple steps creates
ambiguous ownership, confirmation, and compilation semantics.

Different new targets remain allowed when all existing proposal shape,
sensitivity, operation, and project-local scope rules pass. They remain
separate visible Proposals and do not become authority until the human confirms
that exact Preview.

### 6.3 Deterministic validation order

Validation order is fixed:

1. validate semantic authority and context;
2. validate Candidate and proposal shapes using existing rules;
3. validate requirement coverage, phase, Worker, and atomic transitions;
4. build the required-target set;
5. scan proposals in step order and proposal-list order;
6. if a proposal target is required-owned, fail with
   `semantic_required_target_reproposed`;
7. otherwise, if the proposed target was already seen, fail with
   `semantic_proposal_target_duplicate`;
8. otherwise record the new proposed target and continue;
9. compile only after every semantic gate passes.

Required ownership takes precedence over proposal duplication. If two
proposals both reuse one required target, the first stable diagnosis is
`semantic_required_target_reproposed`. Removing all proposals for required
targets resolves the more fundamental authority violation.

The failure object may retain an already allowlisted opaque step number for
internal tests or audit if existing contracts permit it. It must not retain or
emit the conflicting target, operation, Candidate fragment, or raw model text.

## 7. Closed diagnostic contract

Add these codes to the authoritative semantic and Leader diagnostic sets:

```text
semantic_required_target_reproposed
semantic_proposal_target_duplicate
```

Both codes are retryable only for the first same-Leader regeneration attempt.
They are valid only at the Leader schema/semantic-validation stage.

The old code remains accepted by validators and readers:

```text
semantic_effect_conflict
```

It is historical compatibility evidence. Existing journal rows, outbox rows,
Conversation terminals, validation reports, fixtures, and recovery reads must
remain valid. The new target-conflict validator no longer emits it for the two
now-separated cases. No migration or rewrite of historical state occurs.

All code sets and fixtures that validate Leader diagnostics must be updated in
one implementation boundary, including semantic failures, regenerable semantic
failures, provider-plan diagnostics, CLI/API provider errors, Conversation
terminal projections, live-harness closed diagnostics, and documented contract
examples where applicable.

## 8. Precise regeneration

Introduce one pure shared guidance function at the semantic-planning boundary.
Conceptually:

```python
semantic_regeneration_guidance(code: str) -> tuple[str, ...]
```

It accepts a closed diagnostic code and returns only static text. It never
accepts the failed Candidate, target, operation, provider output, exception,
path, prompt, environment, or state record.

For `semantic_required_target_reproposed`, the fixed rule is equivalent to:

```text
Remove every proposed effect whose target is already represented by required
authority. Keep authority_refs unchanged. Do not add a replacement proposal
for that target.
```

For `semantic_proposal_target_duplicate`, the fixed rule is equivalent to:

```text
Each proposed target may appear only once. Return one complete candidate
without repeated proposed targets.
```

Both CLI-backed and API-backed semantic prompts include the same initial rules:

- required authority targets must be represented only through
  `authority_refs`;
- `proposed_effects` may contain only genuinely new targets;
- proposed targets must be unique across the Candidate.

On the first retryable failure, both providers append the same shared guidance,
the closed code, and the existing instruction to return a complete replacement.
The request continues to use the same Provider, model, deadline, selected
Workers, step count, roles, semantic authority, authority hash, and native
schema. No previous Candidate or provider output is copied into the second
prompt.

On a second failure, the true second closed code and `attempt_count=2` become
the terminal evidence. There is no third attempt, model/provider/transport
fallback, partial patch, or automatic local correction.

## 9. Pytest transcript-safe reporting

### 9.1 Safe object representation

`_PtyTail` continues to retain a bounded in-memory tail because the live
harness needs it for PTY draining and Preview interaction. Only its debug
representation changes:

```python
@dataclass
class _PtyTail:
    byte_count: int = 0
    truncated: bool = False
    tail: bytes = field(default=b"", repr=False)
```

The default representation may expose `byte_count` and `truncated`; it must not
expose `tail`. The private digest remains excluded from dataclass fields. The
existing `diagnostic()` projection continues to return exactly:

```text
byte_count
truncated
sha256
```

This is a reporting-safety change, not transcript persistence, parsing, drain,
timeout, process, or Preview behavior.

### 9.2 Default pytest report regression

An object-level `repr` assertion is necessary but insufficient. Add a
deterministic outer test that launches a nested default pytest process against
one temporary failure probe.

The probe must:

1. receive a hostile sentinel through an environment variable so the sentinel
   is absent from probe source;
2. place that sentinel, a fake sensitive path marker, Prompt marker, stderr
   marker, and model-output marker only inside `_PtyTail.tail`;
3. call the real Preview observation/failure path with fake state and process
   objects;
4. fail through the normal `_LiveHarnessFailure` boundary;
5. allow default pytest traceback rendering rather than `--tb=short` or a
   custom output filter.

The outer test captures nested stdout and stderr and requires:

- the nested pytest process fails for the intended closed diagnostic;
- none of the injected PTY markers appears in either stream;
- the closed failure code remains visible;
- bounded PTY identity fields remain visible where the report contract exposes
  them;
- temporary files and the child process are removed or reaped.

The sensitive-path assertion concerns the injected path marker originating in
PTY data. Pytest may legitimately identify the test source file that produced
the traceback; framework-owned source locations are not confused with leaked
PTY payload.

The regression must use only fake state and fake PTY evidence. It performs no
provider call, network access, real tmux, ACP adapter, login, or live Mission.

## 10. Error handling and audit

Semantic failure output remains closed. Public or durable diagnostics may
contain only fields already authorized by their contracts, such as:

```text
stage
diagnostic_code
attempt_count
constraint_mode
retryable
opaque step number when already allowed
schema/hash/count identity fields when already allowed
```

They must not contain:

- conflicting target or operation;
- raw Candidate JSON;
- previous Leader output;
- user message or prompt;
- stdout, stderr, PTY tail, argv, environment, or absolute payload paths;
- credentials, secret values, or private reasoning.

Existing audit events may record the new closed code and existing count/hash
provenance. They do not store the fixed correction prose as authority and do
not gain a new write path.

Malformed, unknown, or stage-incompatible diagnostics continue to fail closed.
Historical `semantic_effect_conflict` remains readable but does not authorize a
retry beyond the rules frozen in the record that produced it.

## 11. TDD requirements

Implementation starts with deterministic RED tests.

### 11.1 Semantic validator RED/GREEN

Cover at least:

- one proposal reusing a required target;
- a proposal matching a required target's operation;
- a proposal differing from a required target's operation;
- a required target used by multiple phases and operations with no Proposal;
- two proposals repeating one genuinely new target in one step;
- two proposals repeating one genuinely new target across different steps;
- two proposals reusing one required target, proving required-target priority;
- multiple distinct valid new project-local targets;
- unchanged missing, duplicate-requirement, wrong-phase, wrong-Worker,
  transition, sensitive, and scope-addition behavior;
- historical `semantic_effect_conflict` reader/validator compatibility.

### 11.2 Regeneration RED/GREEN

Cover CLI and API parity for:

- first required-target rejection followed by a valid complete Candidate;
- first duplicate-proposal rejection followed by a valid complete Candidate;
- two required-target rejections terminating at attempt two;
- two duplicate-target rejections terminating at attempt two;
- a different second failure preserving the true second code;
- same Provider, model, authority, schema, Workers, roles, and step count;
- exact static guidance selected from the first code;
- no first Candidate, raw provider output, or injected sensitive marker in the
  second prompt or exception;
- no third attempt and no fallback.

### 11.3 Pytest reporting RED/GREEN

Cover:

- direct `_PtyTail` representation excluding tail bytes;
- unchanged add/truncate/hash behavior;
- nested default pytest stdout and stderr excluding all injected markers;
- nested failure still exposing the intended closed code and bounded identity;
- child-process and temporary-file cleanup on expected and injected failure.

### 11.4 Non-live M2c integration

The fake semantic four-stage path must still reach Preview and its existing
deterministic completion assertions. Add a first-invalid/second-valid fake
Leader case that proves precise regeneration reaches Preview without changing
authority, confirmation, compiled task, or downstream runtime semantics.

The opt-in real live node remains skipped. It is never invoked by the
implementation plan's ordinary test commands.

## 12. Expected implementation boundaries

The detailed plan must confirm exact files, but the expected code boundaries
are:

- `src/agentdeck/semantic_planning.py`
  - target-exclusivity validator;
  - new closed codes;
  - shared static regeneration guidance;
- `src/agentdeck/providers/plan_schema.py`
  - authoritative Leader diagnostic allowlist if required;
- `src/agentdeck/providers/cli_subprocess.py`
  - initial semantic rule and shared regeneration guidance consumption;
- `src/agentdeck/providers/openai_compatible.py`
  - identical semantic rule and guidance consumption;
- `tests/test_leader_cli.py`
  - validator and API/CLI Provider RED/GREEN;
- `tests/test_m2c_live_acceptance.py`
  - `_PtyTail` representation and report-level leakage regression;
  - deterministic Preview regeneration integration;
- relevant contract/SOP/validation documentation and fixtures;
- `HISTORY.md` and `docs/handoff/current-development-state.md` in every
  user-visible implementation boundary.

No unrelated refactor or file split is authorized by this spec.

## 13. Commit boundaries

The implementation plan should preserve two independently reviewable semantic
changes:

1. **Semantic target exclusivity**
   - validator behavior;
   - new codes and compatibility;
   - shared precise regeneration;
   - CLI/API tests and documentation;
   - matching `HISTORY.md` entry.
2. **Pytest transcript-safe reporting**
   - `repr=False` boundary;
   - object and nested-report tests;
   - SOP/validation documentation;
   - matching `HISTORY.md` entry.

If implementation reveals that a shared contract file must change atomically
with both, the plan may place that contract update in the earliest commit that
makes the repository internally consistent. It must not leave an intermediate
commit with an invalid closed-code contract or a failing full suite.

## 14. Verification and freeze gate

Strict execution order is:

```text
deterministic RED
-> minimal GREEN
-> semantic focused suite
-> CLI/API Provider suite
-> complete non-live M2c harness
-> compileall
-> git diff --check
-> leakage and scope self-review
-> implementation commit(s)
-> freeze exact implementation SHA
-> full suite run 1 on unchanged SHA
-> full suite run 2 on unchanged SHA
-> residual process/root/mirror audit
-> stop
```

The frozen double-full-suite boundary must not include a live node. The exact
live node remains skipped.

After successful frozen verification, the next action still requires explicit
human input naming:

- the new exact frozen SHA;
- the exact Leader model id;
- authorization for exactly one read-only preflight.

The previous `gpt-5.5` selection and preflight authorization do not carry
forward. A new preflight, if authorized, runs once. `ready=true` and
`blockers=[]` permit only an evidence update and a stop. A future real Mission
requires a second, separate human authorization naming that new SHA and model.
No automatic retry is permitted.

Any RED that does not fail for the intended reason, GREEN regression, contract
drift, leakage, cleanup failure, full-suite failure, changed frozen SHA, or
preflight blocker stops progression. M2c remains **BLOCKED** and M3 remains
locked.

## 15. Acceptance criteria

The implementation slice is complete only when all of the following are true:

- required-target proposals fail with
  `semantic_required_target_reproposed`;
- repeated genuinely new proposal targets fail with
  `semantic_proposal_target_duplicate`;
- required ownership takes deterministic precedence;
- valid distinct new targets remain reviewable Proposals;
- existing required multi-phase same-target authority is valid without
  Proposal;
- CLI and API Providers use identical code-specific static guidance;
- one deterministic same-Leader regeneration can produce a valid Preview;
- a second failure terminates without fallback or local repair;
- historical `semantic_effect_conflict` evidence remains readable;
- default `_PtyTail` representation excludes `tail`;
- a real nested default pytest failure report contains none of the injected PTY
  markers while preserving closed bounded diagnostics;
- focused suites, the complete non-live M2c harness, compileall, and diff checks
  pass;
- two independent full suites pass on one unchanged frozen SHA;
- no provider, ACP Worker, tmux Worker, preflight, or live Mission runs without
  its separate authorization;
- cleanup and durable evidence are complete;
- M2c is not declared PASS and M3 is not unlocked by this slice.

## 16. Final product meaning

This design does not special-case the Golden Demo. It clarifies a general
AgentDeck rule:

> A target already owned by explicit user authority cannot simultaneously be
> presented as a new Leader proposal. Required work uses authority references;
> genuinely new scope remains a visible proposal that requires confirmation.

It also clarifies the diagnostic rule:

> Transcript-free evidence includes the test runner's default failure report,
> not only the final serialized exception.

Together these changes make Leader regeneration more actionable without
letting AgentDeck rewrite model intent, and make real acceptance failures safer
to inspect without weakening the evidence needed to continue M2c.
