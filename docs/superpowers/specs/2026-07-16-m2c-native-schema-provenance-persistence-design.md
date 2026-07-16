# M2c Native-Schema Provenance Persistence Design

**Date:** 2026-07-16

**Status:** Implemented, independently reviewed, and frozen after verification

**Milestone:** Phase 3 M2c live-acceptance blocker closure

**Failure authority:** frozen implementation
`75f0366d4d5619b29c77f10949365f43d46185b1`, preflight evidence commit
`e53d493f`, live blocker evidence commit `e2a0f980`

**Corrective implementation authority:**
`7a76ada81938be3ba0720a7c2f5a540b4beebb3e`

**North star:** `docs/roadmap/product-north-star.md`

## 1. First principle

AgentDeck's product promise requires one natural-language request to become one
frozen, reviewable, auditable Mission. The Leader may generate a candidate, but
AgentDeck owns the authority that proves how that candidate was constrained.

For a native-schema Leader, `leader_generation` is that compact provenance. It
records the provider/model identity, `constraint_mode=native_json_schema`,
schema version/hash, selected Workers, step count, attempt count, and whether
one bounded regeneration was used. Once AgentDeck has validated this envelope
against the exact request, dropping it before persistence makes the durable plan
weaker than the already-validated in-memory candidate.

The fix must therefore preserve the exact validated envelope. AgentDeck must
not reconstruct it later, infer it from a plan, or create a second source of
truth.

## 2. Current evidence and exact blocker

Frozen implementation
`75f0366d4d5619b29c77f10949365f43d46185b1` passed:

- semantic/Provider focused verification: `740 passed`;
- complete non-live M2c verification: `192 passed, 1 skipped`;
- two unchanged-SHA full suites: `4266 passed, 2 skipped` twice;
- one explicit-model read-only preflight for `gpt-5.5`:
  `ready=true`, `blockers=[]`, `1 passed in 3.75s`.

The separately authorized real four-stage node ran exactly once. It failed
`1 failed in 48.26s` with:

```text
stage=live_acceptance
code=native_schema_provenance_missing
plans=1
missions=1
mission_attempts=0
permission_requests=0
mission_worker_replies=0
mission_handoffs=0
```

The run reached a valid semantic Mission Preview but stopped before
confirmation and before every Worker/runtime effect. The bounded PTY evidence
contains only byte count, truncation state, and SHA-256 identity.

Source inspection identifies one exact break in
`create_mission_preview_from_candidate()`:

1. semantic `leader_generation` is validated with
   `validate_leader_generation_provenance()`;
2. when semantic authority is present, the validated value is explicitly
   replaced with `None`;
3. `store.build_plan_record()` therefore receives no provenance;
4. the durable plan lacks `constraint_mode=native_json_schema`.

The comment assigning durable ownership to a later Task 8 is stale. Task 8
persisted semantic authority, compiled task hashes, policy hash, and preview
generation, but did not restore `leader_generation`.

Existing native provenance tests exercise non-semantic previews. Existing
semantic preview tests exercise authority, compilation, confirmation, and
diagnostics. No deterministic test crosses both dimensions.

## 3. Chosen approach

Use the already validated semantic generation envelope as the one durable plan
provenance value, and extend the StateStore normalizer to recognize two strict
closed shapes:

- ordinary plan provenance: the existing nine fields;
- semantic plan provenance: the same nine fields plus
  `semantic_authority_schema_version` and `semantic_authority_hash`.

The production change is intentionally minimal:

```text
Leader candidate
  -> validate semantic authority
  -> validate leader_generation against exact request
  -> preserve validated leader_generation
  -> build semantic plan record with that envelope
  -> compute unchanged canonical plan hash
  -> build Mission and preview binding as today
```

The stale branch that replaces `leader_generation` with `None` is removed.
StateStore does not reconstruct Provider provenance. It only revalidates that a
supplied semantic envelope's two authority fields match the authoritative
semantic plan body before deep-copying the exact eleven-field shape.

For native schema mode, ordinary plans continue to require
`leader-plan/v1`; semantic plans require `leader-semantic-plan/v1`. For
non-native semantic modes, schema version/hash remain null while the two
semantic authority fields remain mandatory.

No fallback, migration, repair path, or alternate storage field is introduced.

## 4. Rejected approaches

### Reconstruct provenance inside StateStore

Rejected because StateStore does not own Provider-generation facts and would
need to infer schema/attempt information from other records. That creates a
second authority and can silently disagree with the validated Leader result.

### Persist provenance only on the Mission

Rejected because plan provenance is already the canonical ProjectView, trace,
and planning-history surface. A Mission-only field would leave the plan record
incomplete and fail the current contract.

### Relax or remove the live assertion

Rejected because native-schema provenance is part of the approved M2c contract.
Removing the assertion would produce a less auditable product and a false PASS.

## 5. Persistence and hash invariants

For semantic and non-semantic candidates alike, a supplied
`leader_generation` must first pass the existing exact validator. Persistence
continues through `StateStore._plan_leader_generation()`, which selects the
closed field set from the validated plan shape:

- a non-semantic plan rejects either semantic provenance field;
- a semantic plan rejects a missing or partial semantic field pair;
- semantic authority version must equal the plan authority schema version;
- semantic authority hash must equal a fresh hash of the plan authority;
- native schema version must match the ordinary or semantic schema family;
- every accepted value is deep-copied in deterministic field order.

Malformed, unknown, secret-bearing, partial, or inconsistent input remains
rejected.

The canonical workflow plan hash must remain byte-stable when only
`leader_generation` is added or removed. It continues to authorize the plan
body, not Provider diagnostics. Existing preview binding facts, semantic
authority hash, compiled task hashes, policy snapshot hash, and preview
generation are unchanged.

No provenance is duplicated into Mission events, handoffs, Worker prompts, or
permission records. ProjectView and trace continue to expose only the compact
validated plan projection.

## 6. Failure and security boundaries

The fix must preserve current fail-closed behavior:

- missing or malformed semantic generation rejects the preview with zero plan
  and Mission writes;
- provider/model/Worker/step-count/schema drift remains rejected;
- ordinary/semantic provenance shape mismatch remains rejected;
- semantic authority version/hash drift remains rejected;
- forbidden keys and secret-bearing nested values are never persisted or
  echoed;
- a generation envelope is never repaired, filtered, or synthesized locally;
- legacy candidates without frozen authority cannot attach generation;
- full Leader output, PTY bytes, prompts, stderr, and schema payloads remain
  non-durable.

This slice changes the durable plan provenance normalizer only enough to accept
the already-defined semantic provenance fields and semantic schema version.
It changes no Provider command, model, timeout, schema definition, semantic
extraction, Candidate compilation, confirmation digest, daemon, ACP/tmux
transport, permission, takeover, handoff, artifact, cleanup, login,
installation, or fallback behavior.

## 7. Deterministic TDD contract

RED coverage must prove the exact missing behavior before production changes:

1. a semantic candidate with valid native-schema generation persists the exact
   envelope into its plan record and ProjectView projection;
2. the natural-language semantic ConversationSession preview persists the same
   exact envelope returned by the Leader gateway;
3. the canonical plan hash is unchanged when the envelope is removed from an
   otherwise identical persisted plan;
4. malformed or secret-bearing semantic generation remains zero-write and
   transcript-safe;
5. semantic missing/partial fields, authority hash/version drift, and semantic
   fields on an ordinary plan remain zero-write;
6. semantic local provenance with null schema fields remains valid;
7. non-semantic native, legacy, local, and JSON-object behavior remains
   unchanged.

The first RED must fail because `leader_generation` is absent, not because a
fixture, schema, or semantic plan is invalid.

GREEN is the smallest production edit that preserves the validated value and
adds the strict StateStore nine-field/eleven-field discriminator.
Focused verification must cover mission orchestration, conversation session,
conversation bindings/acceptance, ProjectView/trace provenance, structured
Provider output, semantic planning, and the complete non-live M2c harness.

## 8. Freeze and real-acceptance gate

Before RED/GREEN:

1. complete human review of this written specification;
2. write and review a detailed TDD implementation plan;
3. execute the plan with RED observed before the production correction.

After RED/GREEN:

1. update `HISTORY.md`, handoff, validation evidence, and the implementation
   plan;
2. obtain independent spec-compliance and code-quality review;
3. run compile/diff/scope/leakage checks;
4. freeze one exact implementation SHA;
5. run two independent full suites on that unchanged SHA;
6. record evidence in a separate documentation commit;
7. require a new explicit human exact-model selection/binding plus one
   read-only preflight authorization for the new SHA;
8. only after `ready=true`, `blockers=[]`, require a separate one-live
   authorization.

The old `75f0366d` preflight and live counts are both exactly one and must never
be reused or rerun.

## 9. Completion criteria

This slice is complete only when:

- semantic native-schema preview plans durably contain the exact validated
  `leader_generation`;
- ProjectView and trace expose that same compact envelope;
- StateStore accepts only the exact ordinary nine-field or semantic
  eleven-field shape and revalidates semantic authority version/hash;
- plan hash and confirmation authority remain unchanged;
- malformed/secret-bearing provenance remains fail-closed and zero-write;
- focused and complete non-live M2c suites pass;
- two full suites pass on one unchanged new implementation SHA;
- no preflight or live is run without new explicit authority.

Passing this slice does not itself mark M2c PASS. M2c closes only after a later
real four-stage Mission satisfies every existing acceptance criterion.
