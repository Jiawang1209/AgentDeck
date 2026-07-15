# Leader Semantic Authority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a general `mission-semantic-authority/v1` control plane that preserves explicit user requirements, validates Leader proposals, deterministically compiles Worker tasks, and binds confirmation, dispatch, recovery, and audit to the same frozen semantic authority.

**Architecture:** Add a pure semantic-authority domain module and a separate semantic-planning validator/compiler. Natural-language Mission intake extracts conservative required authority before Leader invocation; providers return a semantic candidate instead of executable task text; AgentDeck validates and compiles a compatibility plan. Persist full non-sensitive authority only in the authoritative plan/Mission path, project compact provenance through ProjectView, and preserve legacy records through exact dual-shape validators rather than rewriting them.

**Tech Stack:** Python 3.12 standard library, dataclasses/typed dictionaries, JSON/JSONL state, existing Codex/Claude CLI native JSON Schema adapters, ACP/tmux daemon transports, pytest, conda environment `agentdeck`.

---

## Execution rules

- Run every command with `conda run -n agentdeck` or inside `conda activate agentdeck`.
- Implement tasks in order. Every task begins with RED evidence, ends with focused GREEN evidence, updates `HISTORY.md`, and creates exactly one local commit unless a review fix requires a clearly named follow-up commit.
- Never run `AGENTDECK_M2C_LIVE=1` while implementing Tasks 1–13.
- Never install or upgrade Codex, Claude, Claude Agent ACP, tmux, or Python packages in this plan.
- Never change global login, authentication, provider configuration, or permissions.
- Preserve the user-owned main-checkout `.omc/` changes and untracked `AGENTS.md`; work only in the isolated feature worktree.
- A Leader candidate, prompt, skill, memory item, ACP message, or tmux pane is context, never authority.
- No provider/model/transport fallback and no local semantic repair.
- M2c remains **BLOCKED** and M3 remains locked until Task 14's separately authorized single live succeeds.

## File responsibility map

### New files

- `src/agentdeck/semantic_authority.py` — canonical authority types, exact validators, hashes, compact projection, sensitive-value classification, and conservative deterministic extraction. It imports no provider, runtime, StateStore, tmux, or ACP module.
- `src/agentdeck/semantic_planning.py` — semantic Leader candidate validation, closed failure codes, deterministic Worker task compilation, semantic step hashes, and conversion to the existing compatibility plan shape.
- `src/agentdeck/providers/semantic_plan_schema.py` — native JSON Schema for semantic Leader candidates; no persistence or provider I/O.
- `tests/test_semantic_authority.py` — pure authority/extractor/hash/sensitivity tests.
- `tests/test_semantic_planning.py` — candidate mutation matrix, compiler determinism, scope-addition, and leak-negative tests.

### Existing files with bounded changes

- `src/agentdeck/providers/base.py` — add optional semantic authority and bounded regeneration diagnostic to `LeaderPlanRequest`.
- `src/agentdeck/providers/plan_schema.py` — select legacy or semantic native schema and validate matching generation provenance.
- `src/agentdeck/providers/cli_subprocess.py` — send compact semantic authority, accept semantic native envelopes, and use the existing two-attempt deadline loop.
- `src/agentdeck/providers/openai_compatible.py` and `src/agentdeck/providers/fake.py` — semantic-candidate parity without claiming native-schema capability.
- `src/agentdeck/orchestration/leader.py` — route semantic requests through candidate validation/compiler and preserve legacy requests byte-for-byte.
- `src/agentdeck/conversation/leader_gateway.py` — carry authority through `LeaderRequest`/`LeaderMissionCandidate` and expose only closed diagnostics.
- `src/agentdeck/conversation/session.py` — extract authority before Leader invocation, return clarification when unresolved, bind exact hashes in preview confirmation facts, and preserve zero-write failure behavior after the already-recorded conversation turn.
- `src/agentdeck/mission_orchestration.py` — validate and land compiled semantic plans; never accept Leader-authored executable task text.
- `src/agentdeck/mission_authority.py` — include semantic hashes in canonical plan authority only when semantic fields exist; legacy hash bytes remain unchanged.
- `src/agentdeck/state.py` — exact dual-shape plan/Mission/snapshot persistence and compact ProjectView projection.
- `src/agentdeck/models.py`, `src/agentdeck/contracts.py` — additive-v1 ProjectView field and exact contract validation.
- `src/agentdeck/cli.py` — reconstruct and verify current semantic step before building either ACP or tmux Worker prompt.
- `src/agentdeck/daemon/transports.py` — require the already-verified `semantic_step_hash` in canonical prompt provenance; no state reads.
- `src/agentdeck/daemon/recovery.py`, `src/agentdeck/daemon/service.py` — fail closed on semantic hash or handoff scope drift.
- `tests/test_m2c_live_acceptance.py` — replace free-text token authority with frozen-effect and compiled-hash gates; retain strict opt-in and single-live policy.
- `docs/contracts/project-view-schema.md`, `docs/superpowers/specs/2026-07-15-leader-semantic-authority-design.md`, `docs/handoff/current-development-state.md`, `docs/validation/2026-07-13-phase3-m2-project-daemon.md`, `README.md`, and `HISTORY.md` — contract, implementation, evidence, and handoff truth.

## Approved-spec coverage map

| Design requirement | Implementation tasks |
|---|---|
| First principles, no general NLP rewrite, non-goals | 1–3, 12–14 |
| Required/proposed/unresolved/frozen lifecycle | 1–3, 7–8 |
| Versioned authority and semantic Leader candidate | 1, 3–6 |
| Conservative extraction and sensitive references | 1–2, 12 |
| Exact validation and one bounded regeneration | 3–7 |
| Deterministic Worker task compilation | 3, 8–9, 11 |
| Preview/confirmation binding | 7–9 |
| Minimum dispatch context and transport parity | 9, 11–12 |
| Handoff/recovery drift | 9, 11–12 |
| Compact ProjectView/contracts/audit | 7–10 |
| Legacy compatibility without rewrite | 4, 6, 8–10, 12 |
| M2c deterministic and real acceptance | 12–14 |

## Canonical names fixed by this plan

Use these names consistently in every task:

```python
SEMANTIC_AUTHORITY_SCHEMA_VERSION = "mission-semantic-authority/v1"
SEMANTIC_LEADER_PLAN_SCHEMA_VERSION = "leader-semantic-plan/v1"
```

The fixed public helper names are `extract_semantic_authority`,
`validate_semantic_authority`, `semantic_authority_hash`,
`compact_semantic_authority`, `validate_semantic_candidate`,
`compile_semantic_plan`, `compile_worker_task`, and `semantic_step_hash`.

Persisted semantic plans use these top-level keys inside `plan_record["plan"]`:

```text
semantic_authority
semantic_steps
```

Each compatibility `steps[]` item retains the current fields
`step/agent_id/role/task/risk/requires_approval`; its `task` is produced only by
`compile_worker_task()`.

The extractor creates a **draft authority** whose `proposed_effects` list is
empty. After candidate validation, `compile_semantic_plan()` normalizes every
reviewable Leader proposal into that list and produces the **frozen authority**.
The final `semantic_authority_hash` is calculated from the frozen authority,
not merely the extracted requirements. This ensures that a proposal becomes
authority only through the exact preview confirmation and that any proposal
change invalidates the binding.

## Task 1: Canonical semantic-authority domain and hash

**Files:**

- Create: `src/agentdeck/semantic_authority.py`
- Create: `tests/test_semantic_authority.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write RED tests for exact authority shape and canonical hash**

At the top of `tests/test_semantic_authority.py`, import `json` and define a
`valid_authority()` fixture function that returns a fresh dict on every call.
Add tests that construct one valid `state_transition` requirement and assert:

```python
def test_validate_semantic_authority_accepts_exact_transition() -> None:
    authority = {
        "schema_version": "mission-semantic-authority/v1",
        "source_message_hash": "sha256:" + "1" * 64,
        "requirements": [{
            "requirement_id": "req_0123456789ab",
            "kind": "state_transition",
            "target": "artifact.txt",
            "operation": "update",
            "before": {"content_equals": "draft-v1\n"},
            "after": {"content_equals": "accepted-v2\n"},
            "phase": "revision",
            "agent_id": "claude-worker",
            "sensitivity": "ordinary",
        }],
        "proposed_effects": [],
        "unresolved": [],
    }
    assert validate_semantic_authority(authority) == authority
    assert semantic_authority_hash(authority).startswith("sha256:")


def test_semantic_authority_hash_is_key_order_independent() -> None:
    left = valid_authority()
    right = json.loads(json.dumps(left, sort_keys=True))
    assert semantic_authority_hash(left) == semantic_authority_hash(right)


def test_compact_semantic_authority_exposes_counts_not_effect_content() -> None:
    compact = compact_semantic_authority(
        valid_authority(),
        state="preview",
        compiled_step_count=4,
        blockers=[],
    )
    assert set(compact) == {
        "schema_version", "state", "authority_hash", "requirement_count",
        "proposed_effect_count", "unresolved_count", "compiled_step_count",
        "blockers",
    }
    assert "draft-v1" not in json.dumps(compact)
```

Parametrize hostile mutations: extra top-level key, duplicate ids, unknown kind,
absolute/escaping target, wrong operation, missing before/after,
boolean masquerading as integer, malformed hash, unknown sensitivity, and raw
secret fields. Validate `proposed_effects` with exact `prp_[0-9a-f]{12}` ids and
the same target/operation/sensitivity boundary as requirements. Every mutation
must raise `SemanticAuthorityError` with one
closed code and must not echo the hostile value.

The `requirements`, `proposed_effects`, and `unresolved` arrays preserve semantic
generation order. Their opaque ids provide identity and uniqueness only; they do
not impose lexicographic ordering. Reordering an array is a semantic change and
therefore changes the canonical authority hash rather than being normalized.

- [ ] **Step 2: Run the RED target**

Run:

```bash
conda run -n agentdeck pytest tests/test_semantic_authority.py -q
```

Expected: collection fails because `agentdeck.semantic_authority` does not exist.

- [ ] **Step 3: Implement the exact domain validator and canonical hash**

Create the module with closed constants and an exception that stores only
`code` and optional safe `requirement_id`:

```python
SEMANTIC_AUTHORITY_SCHEMA_VERSION = "mission-semantic-authority/v1"
SEMANTIC_REQUIREMENT_KINDS = frozenset({
    "create", "read", "review", "update", "verify", "state_transition"
})
SEMANTIC_OPERATIONS = frozenset({"create", "read", "review", "update", "verify"})
SEMANTIC_SENSITIVITY = frozenset({"ordinary", "secret_ref"})
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_ID = re.compile(r"req_[0-9a-f]{12}")


class SemanticAuthorityError(ValueError):
    def __init__(self, code: str, requirement_id: str | None = None) -> None:
        self.code = code
        self.requirement_id = requirement_id
        super().__init__(code)
```

Use exact field sets per requirement kind. Reject absolute paths, `..`, empty
segments, NUL, backslash normalization ambiguity, unknown nested keys, non-JSON
scalars, and non-reference sensitive values. Canonical bytes use
`json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
allow_nan=False)` and UTF-8. Return deep copies so callers cannot mutate
validated authority through aliases.

Implement `compact_semantic_authority(authority, *, state,
compiled_step_count, blockers)`. Accept only states
`draft/blocked/preview/frozen`, exact non-negative integer counts, and a bounded
list of already-sanitized blocker codes. It returns the exact eight-field shape
asserted above and never returns target, literal, before/after content, secret
reference, requirement id, or proposal id.

- [ ] **Step 4: Run focused GREEN and compile checks**

```bash
conda run -n agentdeck pytest tests/test_semantic_authority.py -q
conda run -n agentdeck python -m compileall -q src/agentdeck/semantic_authority.py tests/test_semantic_authority.py
```

Expected: all new tests pass; both commands exit 0.

- [ ] **Step 5: Update history and commit**

```bash
git add src/agentdeck/semantic_authority.py tests/test_semantic_authority.py HISTORY.md
git commit -m "Add semantic authority domain"
```

## Task 2: Conservative extraction, ambiguity, and sensitive-value boundary

**Files:**

- Modify: `src/agentdeck/semantic_authority.py`
- Modify: `tests/test_semantic_authority.py`
- Modify: `HISTORY.md`
- Modify: `docs/superpowers/plans/2026-07-15-leader-semantic-authority.md`

- [ ] **Step 1: Add RED fixtures for Chinese/English explicit effects**

Use the exact live request and an equivalent English request. Assert both yield:

- four phase-bound requirements in order;
- target `artifact.txt` on every phase;
- implementation create postcondition `draft-v1\n`;
- review expected value `accepted-v2\n`;
- one atomic revision `state_transition` with before `draft-v1\n` and after
  `accepted-v2\n`;
- acceptance verify expected value `accepted-v2\n`;
- no unresolved items;
- stable ids and authority hash across repeated extraction.

Every extractor-generated `requirement_id` must be `req_` followed by the first
12 lowercase hexadecimal characters of the SHA-256 digest of that requirement's
canonical body with `requirement_id` removed. Extraction preserves source
clause/phase order and must never sort requirements by opaque id.

The public call is fixed as:

```python
authority = extract_semantic_authority(
    message,
    selected_agent_ids=("claude-worker", "codex-worker"),
    step_count=4,
    phases=("implementation", "review", "revision", "acceptance"),
)
```

Add ambiguity tests for two possible targets, missing transition origin, an
absolute path, unsupported exact-value syntax, and a raw `api_key=SECRET`.
These must create bounded `unresolved` items with only
`unresolved_id/kind/phase/agent_id`; they must not retain the raw secret.

Add one open-goal regression (`"让两个 agent 改进项目文档"`) that produces an
empty requirement list and no unresolved item, allowing the Leader to propose
visible effects. Add a contrasting goal containing an explicit filename or
literal that cannot be safely bound; it must be unresolved rather than silently
fall back to open planning.

- [ ] **Step 2: Run RED extraction tests**

```bash
conda run -n agentdeck pytest tests/test_semantic_authority.py -k 'extract or unresolved or sensitive' -q
```

Expected: fails because extraction is not implemented.

- [ ] **Step 3: Implement a clause-based conservative extractor**

Implement this exact public signature:

```python
def extract_semantic_authority(
    message: str,
    *,
    selected_agent_ids: tuple[str, ...],
    step_count: int,
    phases: tuple[str, ...] | None = None,
) -> dict[str, object]:
    normalized = _validate_extraction_inputs(
        message, selected_agent_ids, step_count, phases
    )
    clauses = _ordered_clauses(normalized.message, normalized.step_count)
    requirements, unresolved = _requirements_from_clauses(
        clauses,
        selected_agent_ids=normalized.selected_agent_ids,
        phases=normalized.phases,
    )
    return validate_semantic_authority({
        "schema_version": SEMANTIC_AUTHORITY_SCHEMA_VERSION,
        "source_message_hash": _source_message_hash(normalized.message),
        "requirements": requirements,
        "proposed_effects": [],
        "unresolved": unresolved,
    })
```

`_source_message_hash()` must deterministically replace every recognized raw
sensitive-assignment value with one fixed placeholder before hashing the
validated NFC message. The raw secret and the redacted message are never
stored or returned, and changing only the secret value must not change the
authority identity. Non-sensitive message changes must still change the
source hash.

The private pipeline names and return types are fixed as follows:

- `_validate_extraction_inputs` returns one frozen `_ExtractionInput` dataclass;
- `_ordered_clauses` returns `tuple[str, ...]`;
- `_explicit_targets` and `_explicit_values` return `tuple[str, ...]`;
- `_classify_operation` returns `str | None`;
- `_classify_sensitive` returns `str`;
- `_requirements_from_clauses` returns
  `tuple[list[dict[str, object]], list[dict[str, object]]]`.

Support only tested Chinese/English ordinal markers, semicolon/newline clause
separators, project-relative filename grammar, explicit content/equality verbs,
and the literal newline words `换行` / `newline`. Track the last explicit
postcondition for one unambiguous target so a later explicit update forms an
atomic transition. Propagate the final explicit target/value into a later
explicit read-only verification clause only when there is exactly one possible
target and state; otherwise emit unresolved.

Every recognized clause must be consumed in full by one supported bounded
grammar. A recognized action/value prefix followed by any unsupported tail is
not authority and must produce a bounded non-echoing unresolved item. Target
discovery must capture one whole action-target token and validate it with an
anchored fullmatch; it must never restart from the middle of a token or reduce
an unsafe target to a safe prefix/suffix. If semantic generation would exceed
the authority domain's 64-item unresolved bound, collapse the diagnostics
deterministically to one bounded summary item that contains no raw clause,
target, literal, key, or secret.

All five operations (`create`, `read`, `review`, `update`, and `verify`) must
share the same anchored full-clause parser. Explicit whole-target variants and
the narrowly supported target-omitted review/verification variants are
separate grammar branches. Operation classification reads only the anchored
action position; it must not search filenames or literals. Target discovery
must not use `finditer()` or any equivalent restart-from-the-middle scan, and
each complete candidate receives one linear whole-token fullmatch.
Target-omitted review or verification may inherit a target only from the one
unique state established by earlier accepted clauses in sequential order;
later clauses must never provide target authority to an earlier clause.
The only permitted future lookup is review literal/newline alignment against a
later update for that already-known target and token. The later clause must be
a validated semantic-state candidate from the same parse/safety pass and a
pure clause-order pre-simulation: full-clause consumption, ordinary
sensitivity, one safe target, one literal, one supported operation, and the
expected explicit agent/position must all pass, and an update must consume a
target established by an earlier validated create. A missing-origin update,
failed create, future create alone, rejected clause, or non-unique matching
final-state candidate contributes no alignment authority.

Sensitive assignment keys are canonicalized into acronym/camel/separator
components plus collapsed lowercase. Any exact password, passwd, secret,
token, credential, or credentials component is sensitive wherever it occurs;
approved api/access/private/signing/encryption/ssh/client/secret plus key
composites are sensitive wherever they occur, including separator-free forms.
For a separator-free fused key, a strong password/passwd/secret/token/
credential(s) marker may have an alphanumeric prefix and must either end the
key or be followed entirely by the bounded qualifiers hash/value/id/key/
digest/ref/reference, including qualifier combinations. This is a whole-key
grammar, not a generic substring search: `secretary`, `tokenizer`, and generic
`key` substrings such as `monkey` remain ordinary.

Generate ids from the canonical requirement body without `requirement_id`,
using the first 12 lowercase SHA-256 hex characters. Never use timestamps,
random ids, locale, cwd, file contents, or provider calls.

- [ ] **Step 4: Prove purity and GREEN behavior**

Add a test that monkeypatches `builtins.open`, `subprocess.run`, and
`urllib.request.urlopen` to raise if invoked, then calls extraction. Run:

```bash
conda run -n agentdeck pytest tests/test_semantic_authority.py -q
```

Expected: all authority tests pass.

The GREEN matrix must include unsupported Chinese/English clause tails,
whole-token target cases that would be unsafe if matched from the middle, and
more than 64 ambiguous clauses collapsing to one bounded non-echoing summary.

- [ ] **Step 5: Commit extraction**

```bash
git add src/agentdeck/semantic_authority.py tests/test_semantic_authority.py HISTORY.md docs/superpowers/plans/2026-07-15-leader-semantic-authority.md
git commit -m "Extract conservative Mission semantics"
```

## Task 3: Semantic candidate validator and deterministic Worker compiler

**Files:**

- Create: `src/agentdeck/semantic_planning.py`
- Create: `tests/test_semantic_planning.py`
- Modify: `src/agentdeck/semantic_authority.py`
- Modify: `tests/test_semantic_authority.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write the complete RED mutation matrix**

Define a valid four-step semantic candidate with no `task` fields. Each step
contains exactly:

```text
step, agent_id, role, phase, authority_refs,
proposed_effects, verification, risk, requires_approval
```

Define fresh-returning test helpers `authority()`, `candidate()`,
`selected_agents()`, and `roles()` in `tests/test_semantic_planning.py`.
`selected_agents()` returns the exact unique authorized Worker tuple
`("claude-worker", "codex-worker")`; the candidate steps carry the repeated
four-position order. `roles()` maps both Worker ids to their configured roles.

Tests must delete or mutate every revision authority reference, duplicate one
reference, swap phase, swap Worker, add an unknown reference, split a transition,
add a contradictory effect, add an escaping target, add sensitive plaintext,
and add an unreviewable scope proposal. Assert closed codes from the design.

Also add:

```python
def test_compiler_preserves_atomic_revision_transition() -> None:
    plan = compile_semantic_plan(
        authority(),
        candidate(),
        selected_agent_ids=selected_agents(),
        roles=roles(),
        step_count=4,
    )
    revision = plan["steps"][2]
    assert "draft-v1\\n" in revision["task"]
    assert "accepted-v2\\n" in revision["task"]
    assert revision["task"] == compile_worker_task(plan["semantic_steps"][2])


def test_compiler_is_byte_deterministic() -> None:
    arguments = {
        "selected_agent_ids": selected_agents(),
        "roles": roles(),
        "step_count": 4,
    }
    first = compile_semantic_plan(authority(), candidate(), **arguments)
    second = compile_semantic_plan(authority(), candidate(), **arguments)
    assert first == second
    assert [s["semantic_step_hash"] for s in first["semantic_steps"]] == [
        s["semantic_step_hash"] for s in second["semantic_steps"]
    ]
```

Add hostile newline, Unicode, colon, Markdown-fence, and instruction-like
literals. Assert they are JSON-escaped into a canonical value line and cannot
create a second `Authoritative operation:` header.

Use the shared pure `semantic_text_contains_sensitive_value()` helper from the
authority module for candidate, authority, and semantic-step sensitive scans;
the helper must reuse the extractor assignment-key classifier and known token
prefix detection. Add a complete public semantic-step mutation matrix covering
exact fields, scalar/resource bounds, refs/effects agreement, nested authority
shape, sensitive/instruction verification, and malformed or forged embedded
hashes. `compile_worker_task()` accepts only a persisted step with a correct
embedded hash; `semantic_step_hash()` accepts a body for internal construction
but must verify any embedded hash it receives.

Before defensive copying or canonical hashing, public semantic-step validation
must iteratively preflight an exact built-in JSON tree with fixed depth, node,
collection, scalar, and integer bounds; cycles, shared containers, subclasses,
and custom containers fail closed. `agent_id` and `phase` use the authority
ordinary-scalar domain, while `role` reuses candidate bounded NFC safe text so
configured roles such as `architecture planning` remain valid.

Duplicate phase names are allowed when every authority ref still matches its
requirement phase and Worker. Proposal targets must use the canonical authority
target validator, including safe extensionless paths such as `README` and
`docs/config`. Draft authority passed into candidate validation/compilation
must have an empty `proposed_effects`; existing proposals fail closed rather
than being overwritten.

Candidate and persisted-step paths must reuse one proposal validator and one
effect-conflict helper. Proposal validation orders exact fields and scalar
types, shared sensitive detection, then canonical authority validation using a
fixed syntactically valid placeholder ID (or the supplied persisted ID), and
only then computes the real canonical body ID. Invalid targets or operations
must never reach proposal hashing. Conflicts include a proposal that disagrees
with any required effect on the same target and two proposals that assign
different operations to one target; required effects may still describe a
valid multi-operation lifecycle among themselves.

The conflict helper must also reject identical duplicate proposal bodies,
whose canonical IDs would otherwise collide only during frozen-authority
validation. Before validating, hashing, or comparing any proposal, candidate
validation must sum proposal counts across all steps and enforce the exported
`SEMANTIC_PROPOSED_EFFECTS_MAX` authority-wide limit.

Every compiler logical field must remain one Python `splitlines()` physical
line. Candidate verification rejects control, surrogate, Zl, and Zp
separators; required literals remain representable, so canonical JSON value
lines must explicitly escape NEL, LS, PS, and every other physical separator
and assert the one-line invariant. Role maps require an exact built-in dict
with exact string keys and bounded safe string values before equality, hashing,
or lookup. Candidate authority refs require exact bounded `req_` IDs before any
lookup; only the bounded `req_<12hex>[:./]<suffix>` grammar may extract a safe
base ID to preserve `semantic_transition_incomplete`. Exact text gates reject
oversized character counts before normalization or UTF-8 encoding.

- [ ] **Step 2: Run RED**

```bash
conda run -n agentdeck pytest tests/test_semantic_planning.py -q
```

Expected: module import failure.

- [ ] **Step 3: Implement exact validation and compilation**

Create:

```python
SEMANTIC_FAILURE_CODES = frozenset({
    "semantic_authority_unresolved",
    "semantic_authority_sensitive_value",
    "semantic_candidate_missing_requirement",
    "semantic_candidate_duplicate_requirement",
    "semantic_candidate_wrong_phase",
    "semantic_candidate_wrong_worker",
    "semantic_transition_incomplete",
    "semantic_effect_conflict",
    "semantic_scope_addition_blocked",
    "semantic_candidate_schema_invalid",
    "semantic_compilation_failed",
    "semantic_compilation_drift",
    "semantic_confirmation_stale",
})


class SemanticPlanningError(ValueError):
    def __init__(
        self, code: str, *, requirement_id: str | None = None,
        step: int | None = None,
    ) -> None:
        if code not in SEMANTIC_FAILURE_CODES:
            raise ValueError("unknown semantic planning failure")
        self.code = code
        self.requirement_id = requirement_id
        self.step = step
        super().__init__(code)


def compile_semantic_plan(
    authority: object,
    candidate: object,
    *,
    selected_agent_ids: tuple[str, ...],
    roles: dict[str, str],
    step_count: int,
) -> dict[str, object]:
    validated_authority = validate_semantic_authority(authority)
    validated_candidate = validate_semantic_candidate(
        validated_authority,
        candidate,
        selected_agent_ids=selected_agent_ids,
        roles=roles,
        step_count=step_count,
    )
    semantic_steps, proposed_effects = _canonical_semantic_steps(
        validated_authority, validated_candidate
    )
    frozen_authority = validate_semantic_authority({
        **validated_authority,
        "proposed_effects": proposed_effects,
    })
    compatibility_steps = [
        _compatibility_step(item, task=compile_worker_task(item))
        for item in semantic_steps
    ]
    return {
        "goal": validated_candidate["goal"],
        "summary": validated_candidate["summary"],
        "steps": compatibility_steps,
        "semantic_authority": frozen_authority,
        "semantic_steps": semantic_steps,
    }
```

Implement `validate_semantic_candidate`, `compile_worker_task`, and
`semantic_step_hash` as public functions with the signatures fixed in the
“Canonical names” section. `_canonical_semantic_steps` must attach each
`semantic_step_hash`; `_compatibility_step` must construct the exact six-field
legacy step shape and must never copy a provider `task` field.

The compiled plan has only current compatibility step fields plus top-level
`semantic_authority` and `semantic_steps`. Required and proposed effects remain
separate inside `semantic_steps`. Safe project-local proposed effects stay
visible and confirmable, are normalized into frozen authority, and therefore
change its hash; blocked proposals never reach compilation. Serialize
each authoritative value with canonical JSON, not interpolation.

- [ ] **Step 4: Run focused GREEN and leak-negative tests**

```bash
conda run -n agentdeck pytest tests/test_semantic_planning.py tests/test_semantic_authority.py -q
```

Expected: all pass. Serialized exceptions must not contain `SECRET`, absolute
home paths, raw candidate JSON, prompt fragments, or literal values.

- [ ] **Step 5: Commit validator/compiler**

```bash
git add src/agentdeck/semantic_authority.py src/agentdeck/semantic_planning.py \
  tests/test_semantic_authority.py tests/test_semantic_planning.py \
  docs/superpowers/plans/2026-07-15-leader-semantic-authority.md HISTORY.md
git commit -m "Harden semantic planning boundaries"
```

## Task 4: Versioned semantic Leader native schema and request authority

**Files:**

- Create: `src/agentdeck/providers/semantic_plan_schema.py`
- Modify: `src/agentdeck/providers/base.py`
- Modify: `src/agentdeck/providers/plan_schema.py`
- Modify: `tests/test_leader_plan_schema.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Add RED schema/provenance tests**

Assert a legacy request still produces byte-identical `leader-plan/v1`. A
request with `semantic_authority` must produce
`leader-semantic-plan/v1`, exact step count, portable nested `anyOf` step
branches, exact Worker/role/phase constants per branch, phase-scoped opaque
authority references, no `task` property, required semantic fields, and
`additionalProperties=false` at every instance-object level.

Assert generation provenance records the semantic schema version/hash and
rejects provenance rebuilt against a different authority hash, requirement set,
Worker order, or step count.

- [ ] **Step 2: Run RED**

```bash
conda run -n agentdeck pytest tests/test_leader_plan_schema.py -k semantic -q
```

Expected: fails because `LeaderPlanRequest` has no semantic authority and the
semantic schema module does not exist.

- [ ] **Step 3: Add request fields and schema selection**

Extend the frozen request without changing legacy defaults:

```python
@dataclass(frozen=True)
class LeaderPlanRequest:
    task: str
    config: ProjectConfig
    model: str | None = None
    skill_context: dict[str, Any] | None = None
    selected_agent_ids: tuple[str, ...] | None = None
    step_count: int | None = None
    timeout_seconds: int | None = None
    semantic_authority: dict[str, object] | None = None
    regeneration_diagnostic: str | None = None
```

Extend `LeaderPlanResult` with one transient, default-empty field:

```python
semantic_diagnostics: tuple[dict[str, object], ...] = ()
```

Each diagnostic dict has exactly `code/attempt_count/regeneration_used`. Validate
that code against the semantic closed set, attempt count is 1 or 2, and
regeneration flag matches the count. This tuple is an in-process handoff to the
Conversation audit mutation; it is not provider output and is never copied into
the plan body, ProjectView, prompt, or raw durable state.

`build_leader_plan_schema()` delegates to
`build_semantic_leader_plan_schema()` only when semantic authority is present.
The semantic schema exposes requirement ids as enums but no raw secret value.
It must stay inside the strict native structured-output subset: the root is an
object; fixed-length `steps` uses `items.anyOf` with `$defs`/`$ref` to complete
step-object branches; every object declares all fields required and sets
`additionalProperties=false`. Do not emit `prefixItems`, boolean subschemas,
`allOf`, `not`, `if`/`then`/`else`, `dependentRequired`, or
`dependentSchemas`; those composition forms are unsupported by the official
strict-schema surface even though general JSON Schema validators may accept
them. Array schemas use only supported `minItems`/`maxItems` constraints and
must not emit `uniqueItems`; duplicate authority references remain a closed
`semantic_candidate_duplicate_requirement` decision in
`validate_semantic_candidate()`. Keep the maximum 64-step/256-requirement
schema within the documented nesting, property, aggregate-enum,
enum/const/property/definition string, and schema-size budgets, with each
opaque requirement id stored only once.

This budget is a production boundary, not test-only evidence. Before returning
the schema, fail closed when canonical UTF-8 JSON exceeds 150,000 bytes, the
raw schema exceeds 5,000 properties, reference-expanded nesting exceeds 10,
aggregate enum membership exceeds 1,000, or enum/const/property/definition
strings exceed 120,000 characters. Store each selected Worker ID and role once
in a dedicated `$defs` identity schema and reference it from every phase branch;
safe maximum-length roles must therefore not be copied once per phase. Preserve
the existing minimum of two selected Workers and two steps, require selected
Worker count to be no greater than step count, and cap both through the existing
64-step authority.

The portable native schema freezes allowed Worker/role/phase/reference
branches and exact array length, but intentionally does not encode ordinal
step numbering or round-robin branch position. `validate_semantic_candidate()`
remains the deterministic authority for exact `step` numbering, round-robin
Worker order, duplicate references, full requirement coverage, and all
cross-field semantic rules.
The semantic-specific request context resolver must collect exactly one safe
config entry for each selected Worker while ignoring unrelated malformed or
unsafe config entries without hashing or comparing hostile subclasses.
The public request path must first project only entries whose exact type is
`AgentSpec`, checking the type before any field access, and pass that filtered
tuple to the resolver; it must not eagerly project `.agent_id` or `.role` from
every configured item. The resolver may retain tuple input for direct pure
tests, but a tuple inside `ProjectConfig.agents` must never authorize a selected
Worker. Unselected malformed, subclass, or hostile objects are ignored without
property execution, while a selected Worker whose exact safe `AgentSpec` is
absent fails closed.
`build_leader_generation_provenance()` and its validator reconstruct the exact
expected schema from the request; do not trust a supplied version/hash.
Extend `LEADER_PLAN_DIAGNOSTIC_CODES` only by union with the closed semantic
candidate codes exported by `semantic_planning.py`; arbitrary provider strings
must never become diagnostic codes.

- [ ] **Step 4: Run all Leader schema tests**

```bash
conda run -n agentdeck pytest tests/test_leader_plan_schema.py -q
```

Expected: semantic and all legacy schema tests pass.

- [ ] **Step 5: Commit schema**

```bash
git add src/agentdeck/providers/semantic_plan_schema.py src/agentdeck/providers/base.py src/agentdeck/providers/plan_schema.py tests/test_leader_plan_schema.py HISTORY.md
git commit -m "Add semantic Leader plan schema"
```

## Task 5: Codex and Claude CLI semantic generation with one bounded regeneration

**Files:**

- Modify: `src/agentdeck/providers/cli_subprocess.py`
- Modify: `tests/test_leader_cli.py`
- Modify: `tests/test_conversation_leader_diagnostics.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Add RED CLI command, prompt, envelope, and retry tests**

Capture Codex `--output-schema` and Claude `--json-schema` inputs. Assert the
semantic schema id, no executable `task` field, compact authority hash and safe
requirement summaries in prompt, and absence of raw secret values.

Make attempt one return a structurally valid candidate missing one ordinary
non-transition requirement and attempt two return the complete candidate. This
must produce `semantic_candidate_missing_requirement`; omission or fragmentation
of the atomic revision transition remains the distinct Task 3 code
`semantic_transition_incomplete`. Assert:

- same command/provider/model;
- same authority hash and deadline;
- exactly two attempts;
- second prompt contains only the closed diagnostic
  `semantic_candidate_missing_requirement` and no first raw output;
- provenance has `attempt_count=2`, `regeneration_used=true`.
- `LeaderPlanResult.semantic_diagnostics` contains exactly one safe rejected
  attempt record and no raw candidate content.

Make both attempts fail and assert a sanitized `schema` stage with the semantic
diagnostic and no third process call.

- [ ] **Step 2: Run RED CLI targets**

```bash
conda run -n agentdeck pytest tests/test_leader_cli.py -k semantic -q
conda run -n agentdeck pytest tests/test_conversation_leader_diagnostics.py -k semantic -q
```

Expected: new tests fail.

- [ ] **Step 3: Route native results through semantic validation/compiler**

When `request.semantic_authority` is present:

1. before schema, prompt, deadline, or process work, create one validated deep
   authority snapshot and replace the request; every later artifact and attempt
   uses only that snapshot, never the caller-owned mutable mapping;
2. native envelope keys are `goal/summary/steps` with semantic step fields;
3. `_validate_native_plan()` calls `compile_semantic_plan()`;
4. `SemanticPlanningError.code` is copied only into the allowlisted diagnostic;
5. semantic codes that describe candidate omissions/conflicts are retryable;
6. sensitive/unresolved/authority-invalid codes are not retryable;
7. retry uses `replace(request, regeneration_diagnostic=code)` and the existing
   total deadline;
8. diagnostic evidence is immutable internally and exposed only as fresh compact
   dict projections; cleanup behavior and output-envelope identity checks remain
   unchanged.

Legacy requests must continue through the current native validator unchanged.

- [ ] **Step 4: Run CLI/provider regression**

```bash
conda run -n agentdeck pytest tests/test_leader_cli.py tests/test_leader_plan_schema.py tests/test_conversation_leader_diagnostics.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit CLI semantic planning**

```bash
git add src/agentdeck/providers/cli_subprocess.py tests/test_leader_cli.py tests/test_conversation_leader_diagnostics.py HISTORY.md
git commit -m "Generate semantic plans with CLI Leaders"
```

## Task 6: Fake/API provider parity and orchestration boundary

**Files:**

- Modify: `src/agentdeck/providers/fake.py`
- Modify: `src/agentdeck/providers/openai_compatible.py`
- Modify: `src/agentdeck/orchestration/leader.py`
- Modify: `tests/test_mission_orchestration.py`
- Modify: `tests/test_leader_cli.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Add RED parity tests**

Assert fake semantic planning deterministically references all required
requirements and compiles through the same helper. Mock the HTTP provider for a
first semantic omission followed by a valid replacement and assert two calls,
same provider/model/authority/deadline, and no fallback. Double failure must
return the same closed semantic code.

Assert legacy fake/API requests remain byte-compatible and do not gain
`semantic_authority` or `semantic_steps`.

- [ ] **Step 2: Run RED provider tests**

```bash
conda run -n agentdeck pytest tests/test_mission_orchestration.py tests/test_leader_cli.py -k 'semantic or legacy' -q
```

Expected: semantic parity cases fail.

- [ ] **Step 3: Implement provider-neutral semantic plan results**

Add semantic candidate construction to fake provider for deterministic tests.
Add a bounded two-attempt `plan_result()` to the HTTP provider using the same
request replacement diagnostic and total monotonic deadline. In
`LeaderOrchestrator.plan_result()`, require compiled semantic keys whenever the
request carries authority and reject semantic keys on legacy requests.

Before the first semantic HTTP attempt, freeze and validate the effective model,
provider name, constraint mode, and base URL; both attempts and provenance must
consume only those frozen values. The Orchestrator must independently freeze the
provider reference/name and any exact non-empty default model before invocation,
then use the same effective request/name for provider execution and provenance
validation without rereading mutable provider identity. This pre-invocation
name gate applies to semantic requests; a legacy provider that raises a typed
failure before returning a plan must preserve that original failure, and its
frozen name is required only after a successful result needs provenance. Read semantic HTTP
responses in chunks of at most 64 KiB with a 2 MiB total cap and check the same
monotonic deadline before and after every chunk. Production `HTTPResponse`
reads must use `read1()` and set `fp.raw._sock` to the exact remaining timeout
before every receive, so a blocked body read cannot exceed the total deadline;
minimal test doubles may use an explicit post-read-only fallback. Direct or
wrapped socket timeouts are one-attempt timeout failures; oversize or malformed
envelopes are sanitized nonretryable parse failures. Before the orchestrator
copies or reconstructs a provider result, it must require the exact frozen
semantic step count and run every semantic step through the Task 3 exact-tree/hash
boundary; any hostile value or conversion failure collapses to
`semantic_compilation_drift` without echo.

Do not advertise `native_json_schema` for HTTP/fake providers. Their provenance
remains `json_object`/`local` with null schema fields, while the semantic
authority hash is proven through the compiled plan and later confirmation
binding rather than a false native-schema claim.

- [ ] **Step 4: Run provider/orchestrator GREEN**

```bash
conda run -n agentdeck pytest tests/test_mission_orchestration.py tests/test_leader_cli.py tests/test_leader_plan_schema.py -q
```

- [ ] **Step 5: Commit provider parity**

```bash
git add src/agentdeck/providers/fake.py src/agentdeck/providers/openai_compatible.py src/agentdeck/orchestration/leader.py tests/test_mission_orchestration.py tests/test_leader_cli.py HISTORY.md
git commit -m "Apply semantic authority across Leader providers"
```

## Task 7: Conversation extraction, clarification, Gateway authority, and zero-write failure

**Files:**

- Modify: `src/agentdeck/conversation/leader_gateway.py`
- Modify: `src/agentdeck/conversation/session.py`
- Modify: `src/agentdeck/mission_orchestration.py`
- Modify: `src/agentdeck/contracts.py`
- Modify: `tests/test_conversation_leader_gateway.py`
- Modify: `tests/test_conversation_session.py`
- Modify: `tests/test_conversation_mission.py`
- Modify: `tests/test_conversation_contracts.py`
- Modify: `docs/contracts/conversation-runtime-schema.md`
- Modify: `HISTORY.md`

- [x] **Step 1: Add RED conversation tests**

Cover these paths:

1. unresolved extraction returns `mode=semantic_clarification`, one bounded
   question, compact unresolved card, no Leader call, and no plan/Mission/
   approval/message/job/inbox;
2. valid extraction passes the exact deep-copied draft authority through
   `LeaderRequest` and `LeaderPlanRequest`; `LeaderMissionCandidate` carries the
   compiled frozen authority and landing proves its required subset equals the
   draft while its proposed effects equal the validated candidate proposals;
3. Gateway mutation of authority hash, requirements, Worker order, or count
   fails at `schema` before preview;
4. double semantic generation failure records only the already-required
   conversation turn/terminal transition and creates no plan/Mission/binding;
5. exact live-shaped request reaches one semantic preview.

Assert the conversation contract exposes one bounded
`semantic_clarification_card` with exactly
`schema_version/authority_hash/unresolved_count/question/controls`; controls
are clarify/inspect affordances only and cannot confirm or dispatch.

- [x] **Step 2: Run RED conversation targets**

```bash
conda run -n agentdeck pytest tests/test_conversation_leader_gateway.py tests/test_conversation_session.py tests/test_conversation_mission.py -k semantic -q
```

- [x] **Step 3: Wire extraction and authority through the Gateway**

Preserve all existing fields and defaults. Add this field to `LeaderRequest`:

```python
semantic_authority: dict[str, object] | None = None
```

Add these fields to `LeaderMissionCandidate`:

```python
semantic_authority: dict[str, object] | None = None
semantic_diagnostics: tuple[dict[str, object], ...] = ()
```

In `_handle_leader()`, run extraction after Worker/count/phase authority is
frozen and before `generate_mission()`. If unresolved exists, terminally record
the turn as a clarification response without provider invocation. Otherwise
pass validated authority to the Gateway and require the returned candidate and
compiled plan frozen-authority hashes to match. The returned frozen authority
must preserve every draft requirement byte-for-byte; only its normalized
`proposed_effects` list may differ from the draft.

`create_mission_preview_from_candidate()` must reject:

- semantic candidate without compiled semantic plan;
- compiled semantic plan without candidate authority;
- hash mismatch;
- any compatibility `task` not equal to a fresh compiler result.

Audit rules are exact: successful extraction appends
`semantic_authority_extracted` with schema/hash/counts only; a rejected Leader
candidate appends `leader_semantic_candidate_rejected` with closed code,
attempt count, and safe ids only; a successful second attempt appends
`leader_semantic_candidate_regenerated`. No event contains the message,
candidate, target, literal, prompt, stdout/stderr, path, or secret.
Gateway and landing validate the transient tuple exactly before the Conversation
mutation consumes it; the tuple is not added to plan/Mission records.

- [x] **Step 4: Run conversation GREEN and legacy regression**

```bash
conda run -n agentdeck pytest tests/test_conversation_leader_gateway.py tests/test_conversation_session.py tests/test_conversation_mission.py tests/test_conversation_acceptance.py tests/test_conversation_contracts.py -q
```

- [x] **Step 5: Commit conversation authority**

```bash
git add src/agentdeck/conversation/leader_gateway.py src/agentdeck/conversation/session.py src/agentdeck/mission_orchestration.py src/agentdeck/contracts.py tests/test_conversation_leader_gateway.py tests/test_conversation_session.py tests/test_conversation_mission.py tests/test_conversation_contracts.py docs/contracts/conversation-runtime-schema.md HISTORY.md
git commit -m "Bind conversation planning to semantic authority"
```

**Review closure:** The implementation additionally binds the returned frozen
authority back to the exact local draft with proposals removed, validates
generation provenance against that draft, derives regeneration audit from the
validated generation envelope, permits separately validated local proposals,
and exact-type gates clarification cards, controls, Worker/count identities,
authority, and compiled plans before copying or comparison hooks can execute.
The review closure also exact-validates semantic diagnostics before mutation,
correlates them with the validated one- or two-attempt generation envelope, and
uses one public regenerable failure-code set across Session, CLI, and API paths.

## Task 8: Plan/Mission persistence and exact preview confirmation binding

**Files:**

- Modify: `src/agentdeck/mission_authority.py`
- Modify: `src/agentdeck/state.py`
- Modify: `src/agentdeck/conversation/session.py`
- Modify: `src/agentdeck/contracts.py`
- Modify: `tests/test_conversation_bindings.py`
- Modify: `tests/test_conversation_acceptance.py`
- Modify: `tests/test_mission_orchestration.py`
- Modify: `tests/test_contracts.py`
- Modify: `docs/contracts/mission-schema.md`
- Modify: `HISTORY.md`

- [x] **Step 1: Add RED persistence and stale-confirmation tests**

Assert semantic plan records preserve exact validated
`semantic_authority/semantic_steps`, and `canonical_workflow_plan_hash()`
changes if either semantic hash changes. Assert the legacy plan hash fixture is
unchanged.

The semantic preview binding facts must be exactly:

```python
{
    "control_kind": "mission_confirm",
    "project_root": root,
    "leader_provider": provider,
    "leader_model": model,
    "action_id": mission_id,
    "action_hash": plan_hash,
    "semantic_authority_hash": authority_hash,
    "compiled_task_hashes": task_hashes,
    "policy_snapshot_hash": policy_hash,
    "preview_generation": 1,
}
```

Mutate each field between preview and confirm. Every case must return blocked,
leave binding pending, leave Mission unconfirmed, create no attempt/permission/
message/job/inbox, and append no `mission_semantic_authority_frozen` event.

- [x] **Step 2: Run RED persistence tests**

```bash
conda run -n agentdeck pytest tests/test_conversation_bindings.py tests/test_conversation_acceptance.py tests/test_mission_orchestration.py -k semantic -q
```

- [x] **Step 3: Persist semantic fields and bind confirmation**

Add optional semantic arguments to `build_plan_record()` and
`build_mission_record()` only through validated compiled plan data. Mission
records store compact fields:

```text
semantic_authority_schema_version
semantic_authority_hash
compiled_task_hashes
preview_generation
```

The full non-sensitive authority remains in the authoritative plan body, not
duplicated into events or ProjectView. Expand preview execution facts for
semantic Missions; preserve the exact legacy fact set for legacy previews.
Before `preview_executor`, reload Mission/plan/policy and recompute every fact.

Append `mission_semantic_preview_created` at preview commit and
`mission_semantic_authority_frozen` only in the same atomic confirmation
mutation that freezes execution. Event payloads contain ids/hashes/counts only.

Extend the Mission contract/example with the same compact eight-field
`semantic_authority` card used by preview. Legacy Mission cards project null.
Update `docs/contracts/mission-schema.md`; clients must not parse compiled task
text or command strings to discover semantic state.

- [x] **Step 4: Run GREEN and atomicity regression**

```bash
conda run -n agentdeck pytest tests/test_conversation_bindings.py tests/test_conversation_acceptance.py tests/test_mission_orchestration.py tests/test_conversation_state.py tests/test_contracts.py -q
```

- [x] **Step 5: Commit persistence/binding**

```bash
git add src/agentdeck/mission_authority.py src/agentdeck/state.py src/agentdeck/conversation/session.py src/agentdeck/contracts.py tests/test_conversation_bindings.py tests/test_conversation_acceptance.py tests/test_mission_orchestration.py tests/test_contracts.py docs/contracts/mission-schema.md HISTORY.md
git commit -m "Freeze semantic Mission confirmation authority"
```

## Task 9: Dual-shape execution snapshot and recovery-stable hashes

**Files:**

- Modify: `src/agentdeck/state.py`
- Modify: `tests/test_daemon_mission_snapshot.py`
- Modify: `tests/test_daemon_recovery.py`
- Modify: `tests/test_daemon_crash_matrix.py`
- Modify: `HISTORY.md`

- [x] **Step 1: Add RED exact-shape snapshot tests**

Legacy snapshots must retain their existing exact mission/step field sets and
hashes. Semantic snapshots add exactly:

- mission: `semantic_authority_schema_version`, `semantic_authority_hash`;
- each step: `semantic_step_hash` in addition to `task_hash`;
- execution root: no full authority or literal content.

Reject mixed shapes: semantic Mission with legacy steps, legacy Mission with
semantic steps, missing/extra fields, task/compiler mismatch, authority hash
drift, reordered hashes, and recovery after plan mutation.

- [x] **Step 2: Run RED snapshot targets**

```bash
conda run -n agentdeck pytest tests/test_daemon_mission_snapshot.py tests/test_daemon_recovery.py -k semantic -q
```

- [x] **Step 3: Implement exact dual-shape snapshot validation**

In `validate_execution_snapshot()`, select one exact field set based solely on
presence of `semantic_authority_schema_version`; do not accept arbitrary
optional combinations. In `build_execution_snapshot_authority()`, revalidate
the full plan semantic authority, recompile each task, compare the stored task,
and emit compact hashes only.

`prepare_mission_attempt()` must rebuild the snapshot from current config,
Mission, plan, policy, memory provenance, and semantic compiler. Any mismatch
raises existing bounded frozen-drift errors before attempt creation.

- [x] **Step 4: Run snapshot, recovery, and crash GREEN**

```bash
conda run -n agentdeck pytest tests/test_daemon_mission_snapshot.py tests/test_daemon_recovery.py tests/test_daemon_crash_matrix.py -q
```

- [x] **Step 5: Commit snapshot authority**

```bash
git add src/agentdeck/state.py tests/test_daemon_mission_snapshot.py tests/test_daemon_recovery.py tests/test_daemon_crash_matrix.py HISTORY.md
git commit -m "Bind daemon snapshots to semantic hashes"
```

## Task 10: Additive ProjectView/workbench/contract projection

**Files:**

- Modify: `src/agentdeck/models.py`
- Modify: `src/agentdeck/state.py`
- Modify: `src/agentdeck/contracts.py`
- Modify: `src/agentdeck/mission.py`
- Modify: `tests/test_contracts.py`
- Modify: `tests/test_mission.py`
- Modify: `tests/test_daemon_contracts.py`
- Modify: `docs/contracts/project-view-schema.md`
- Modify: `HISTORY.md`

- [x] **Step 1: Add RED compact projection and contract tests**

Under the repository's documented additive-v1 policy, keep
`project-view/v1`. Add `semantic_authority` to each plan and Mission item, not a
new top-level state source. The compact card has exactly:

```text
schema_version, state, authority_hash, requirement_count,
proposed_effect_count, unresolved_count, compiled_step_count, blockers
```

Assert legacy records project `semantic_authority=null`. Assert semantic records
never project targets, literals, before/after content, full authority,
Leader candidate, prompt, or secret refs. Workbench Mission card reuses the
same compact object.

- [x] **Step 2: Run RED contract targets**

```bash
conda run -n agentdeck pytest tests/test_contracts.py tests/test_mission.py tests/test_daemon_contracts.py -k semantic -q
```

- [x] **Step 3: Implement one compact projector and exact validators**

Use `compact_semantic_authority()` as the only source for counts/hash/state.
Add field constants and example fixture updates in `contracts.py`; reject unknown
nested fields and malformed hashes. Update the ProjectView contract document to
state explicitly that this is an additive-v1 extension and not execution
authorization.

- [x] **Step 4: Run full contract/ProjectView regression**

```bash
conda run -n agentdeck pytest tests/test_contracts.py tests/test_mission.py tests/test_daemon_contracts.py tests/test_leader_cli.py -q
```

- [x] **Step 5: Commit projection**

```bash
git add src/agentdeck/models.py src/agentdeck/state.py src/agentdeck/contracts.py src/agentdeck/mission.py tests/test_contracts.py tests/test_mission.py tests/test_daemon_contracts.py docs/contracts/project-view-schema.md HISTORY.md
git commit -m "Expose compact semantic authority provenance"
```

## Task 11: Dispatch parity, minimum context, handoff scope drift, and recovery

**Files:**

- Modify: `src/agentdeck/cli.py`
- Modify: `src/agentdeck/daemon/transports.py`
- Modify: `src/agentdeck/daemon/service.py`
- Modify: `src/agentdeck/daemon/recovery.py`
- Modify: `src/agentdeck/state.py`
- Modify: `tests/test_daemon_transports.py`
- Modify: `tests/test_daemon_service.py`
- Modify: `tests/test_daemon_recovery.py`
- Modify: `tests/test_daemon_reconnection.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Add RED transport and drift tests**

For the same semantic step, capture constructed ACP and tmux prompts and assert
identical compiled task bytes and `semantic_step_hash`; only transport wrapper
details may differ. Assert the current Worker receives no later-step literal,
other step effect, full authority, or secret reference.

Mutate stored task, semantic step, authority hash, current config, takeover
state, previous handoff effect, or recovery snapshot. Assert failure before
transport construction/admission/attempt effect and a closed
`semantic_compilation_drift` or bounded existing drift blocker.

Add a handoff fixture that reports an unrelated file mutation. It must persist
the reply evidence but pause before `activate_next`; it must not convert the
reported effect into authority.

- [ ] **Step 2: Run RED daemon targets**

```bash
conda run -n agentdeck pytest tests/test_daemon_transports.py tests/test_daemon_service.py tests/test_daemon_recovery.py tests/test_daemon_reconnection.py -k semantic -q
```

- [ ] **Step 3: Verify semantic authority before transport construction**

In `_daemon_worker_transport_for()`:

1. reload config/Mission/snapshot/plan;
2. validate full authority and semantic step;
3. recompile only the current task;
4. compare `task_hash` and `semantic_step_hash` to snapshot;
5. resolve the previous canonical handoff;
6. pass verified task and hash to `build_worker_prompt()`;
7. only then construct ACP or tmux transport.

Extend the canonical Worker prompt with one provenance line containing only the
semantic step hash. Extend compact message/reply/handoff/trace provenance with
that hash, never the full effect. Recovery uses the hash to classify drift and
does not regenerate.

Append `worker_task_compiled` only after deterministic recompilation succeeds
and before external admission, with Mission/step ids and hashes only. Append
`semantic_authority_drift_detected` on a closed drift decision with Mission/step
ids and closed code only. Neither event may include compiled task or effect
content.

- [ ] **Step 4: Run daemon GREEN including takeover/reconnect**

```bash
conda run -n agentdeck pytest tests/test_daemon_transports.py tests/test_daemon_service.py tests/test_daemon_recovery.py tests/test_daemon_reconnection.py tests/test_daemon_governance.py -q
```

- [ ] **Step 5: Commit dispatch/recovery**

```bash
git add src/agentdeck/cli.py src/agentdeck/daemon/transports.py src/agentdeck/daemon/service.py src/agentdeck/daemon/recovery.py src/agentdeck/state.py tests/test_daemon_transports.py tests/test_daemon_service.py tests/test_daemon_recovery.py tests/test_daemon_reconnection.py HISTORY.md
git commit -m "Enforce semantic authority during Worker dispatch"
```

## Task 12: Compatibility, privacy, and whole-path deterministic acceptance

**Files:**

- Modify: `tests/test_conversation_acceptance.py`
- Modify: `tests/test_daemon_acceptance.py`
- Modify: `tests/test_daemon_background_mission.py`
- Modify: `tests/test_daemon_protocol.py`
- Modify: `tests/test_daemon_supervisor.py`
- Modify: `tests/test_conversation_surfaces.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Add RED end-to-end semantic acceptance**

Drive a disposable project through:

```text
natural-language intake
-> extracted required authority
-> Leader semantic candidate
-> compiled preview
-> exact confirmation
-> daemon admission
-> ACP step
-> tmux step
-> revision
-> acceptance
-> completion
```

Use fake transports only. Assert four compiled hashes, atomic revision
transition, two permission pauses, four canonical handoffs, three inter-stage
links, final exact bytes, ProjectView/ledger/trace/snapshot agreement,
disconnect/reconnect, takeover/return-control, and no transcript/secret marker.

Add a table-driven legacy corpus containing pre-semantic plan/Mission/snapshot
fixtures. Read/status/resume behavior and canonical hashes must remain unchanged.

- [ ] **Step 2: Run RED acceptance targets**

```bash
conda run -n agentdeck pytest tests/test_conversation_acceptance.py tests/test_daemon_acceptance.py tests/test_daemon_background_mission.py -k semantic -q
```

- [ ] **Step 3: Enforce the deterministic acceptance stop rule**

If the RED target fails for a requirement not already mapped to Tasks 1–11,
stop execution and amend the written plan before changing production code. If a
mapped invariant fails, return to its original task, add one named focused
regression, make and commit the correction under that task's file/commit
boundary, then restart Task 12 from Step 1. Task 12 itself adds acceptance tests
only. Do not add a new command, provider, fallback, permission class,
transcript store, or test-only production switch.

- [ ] **Step 4: Run broad deterministic regression**

```bash
conda run -n agentdeck pytest \
  tests/test_semantic_authority.py \
  tests/test_semantic_planning.py \
  tests/test_leader_plan_schema.py \
  tests/test_conversation_leader_gateway.py \
  tests/test_conversation_session.py \
  tests/test_conversation_acceptance.py \
  tests/test_daemon_acceptance.py \
  tests/test_daemon_background_mission.py \
  tests/test_daemon_protocol.py \
  tests/test_daemon_supervisor.py \
  tests/test_conversation_surfaces.py -q
```

Expected: all pass with only explicitly marked live skips.

- [ ] **Step 5: Commit deterministic acceptance**

```bash
git add tests/test_conversation_acceptance.py tests/test_daemon_acceptance.py tests/test_daemon_background_mission.py tests/test_daemon_protocol.py tests/test_daemon_supervisor.py tests/test_conversation_surfaces.py HISTORY.md
git commit -m "Prove semantic Mission authority end to end"
```

## Task 13: M2c harness conversion, documentation, full verification, and frozen preflight

**Files:**

- Modify: `tests/test_m2c_live_acceptance.py`
- Modify: `README.md`
- Modify: `docs/contracts/project-view-schema.md`
- Modify: `docs/superpowers/specs/2026-07-15-leader-semantic-authority-design.md`
- Modify: `docs/validation/2026-07-13-phase3-m2-project-daemon.md`
- Modify: `docs/handoff/current-development-state.md`
- Modify: `HISTORY.md`

- [ ] **Step 1: Replace the M2c free-text task gate with RED frozen-authority gates**

Delete the harness's authority decision based on searching Leader-authored
`task` strings. Add deterministic assertions that:

- `mission-semantic-authority/v1` is present;
- revision has one atomic state transition with the exact before/after values;
- compiled revision task is byte-equal to fresh compilation;
- all four `semantic_step_hash` and `task_hash` values match snapshot;
- the confirmation binding contains the exact authority/task/policy/generation
  hashes;
- failure before confirmation creates zero attempts/permissions/replies/
  handoffs and emits only closed diagnostics.

Keep the existing per-token harness checks only as test mutation helpers, not as
the authority source or live PASS criterion.

- [ ] **Step 2: Run the complete non-live M2c harness**

```bash
conda run -n agentdeck pytest tests/test_m2c_live_acceptance.py -q
```

Expected: all non-live tests pass and exactly the opt-in live node skips.

- [ ] **Step 3: Update user-facing and handoff truth**

Document:

- why this is a control-plane feature rather than a replacement for LLM
  reasoning;
- required/proposed/unresolved/frozen authority;
- one confirmation versus independent runtime permissions;
- compact ProjectView provenance;
- no A2A/remote/GUI/terminal-emulator scope;
- M2c still BLOCKED pending real live.

Do not claim live PASS. Preserve the previous single-live failure as historical
evidence and identify the new frozen commit only after committing.

- [ ] **Step 4: Run pre-commit focused and static checks**

Run, in order:

```bash
conda run -n agentdeck pytest tests/test_m2c_live_acceptance.py -q
conda run -n agentdeck python -m compileall -q src tests
git diff --check
git ls-files '.agentdeck/**'
git status --short
```

Expected:

- the complete non-live M2c harness passes with only its explicit live skip;
- compileall and diff check exit 0;
- no tracked `.agentdeck/` files;
- status contains only the intended harness/documentation changes.

- [ ] **Step 5: Commit documentation/frozen harness and record SHA**

```bash
git add tests/test_m2c_live_acceptance.py README.md docs/contracts/project-view-schema.md docs/superpowers/specs/2026-07-15-leader-semantic-authority-design.md docs/validation/2026-07-13-phase3-m2-project-daemon.md docs/handoff/current-development-state.md HISTORY.md
git commit -m "Prepare semantic M2c live acceptance"
git rev-parse HEAD
git status --short
```

Record the full SHA as the sole code/evidence authority. Do not amend it after
preflight.

- [ ] **Step 6: Verify the exact frozen SHA with two independent full suites**

First assert `git status --short` is empty, then run:

```bash
FROZEN_SHA="$(git rev-parse HEAD)"
conda run -n agentdeck pytest -q
test "$(git rev-parse HEAD)" = "$FROZEN_SHA"
conda run -n agentdeck pytest -q
test "$(git rev-parse HEAD)" = "$FROZEN_SHA"
conda run -n agentdeck python -m compileall -q src tests
git diff --check "$FROZEN_SHA^" "$FROZEN_SHA"
git status --short
```

Expected: both full suites pass independently with only explicit opt-in live
skips; compileall/diff checks exit 0; SHA remains identical; worktree remains
clean. If a failure requires code or test modification, stop, return to the
relevant task, create a new commit, and restart this frozen verification from
the beginning.

- [ ] **Step 7: Run one read-only preflight against the frozen SHA**

Run only the existing preflight node and capture its strict JSON result:

```bash
conda run -n agentdeck pytest \
  tests/test_m2c_live_acceptance.py::test_m2c_live_preflight_is_read_only \
  -q -s
```

Required result:

```json
{"ready": true, "blockers": []}
```

Also require frozen SHA unchanged, worktree clean, probe roots zero-write, and
no login/global-setting changes. If any blocker appears, stop and return to a
new design/plan; do not run live.

## Task 14: Explicit STOP gate and single real live acceptance

**Files:**

- Modify only after the run: `docs/validation/2026-07-13-phase3-m2-project-daemon.md`
- Modify only after the run: `docs/handoff/current-development-state.md`
- Modify only after the run: `HISTORY.md`

- [ ] **Step 1: STOP and request explicit human authorization**

Report the frozen SHA, two full-suite results, focused M2c result, compile/diff/
tracked-state results, installed component versions, and exact preflight JSON.
Ask for authorization to execute exactly one live attempt. Do not treat approval
of this implementation plan as live authorization.

- [ ] **Step 2: If and only if separately authorized, run the single live node once**

Use the existing opt-in environment variable and exact node selected by
`tests/test_m2c_live_acceptance.py`:

```bash
AGENTDECK_M2C_LIVE=1 conda run -n agentdeck pytest \
  tests/test_m2c_live_acceptance.py::test_real_four_stage_m2c_acceptance \
  -q -s
```

Do not use `--last-failed`, rerun, retry, or invoke the node a second time in
the same evidence cycle.

- [ ] **Step 3: Audit cleanup and frozen identity without changing code**

Record only observable facts: pytest exit/result, bounded failure JSON if any,
state cardinalities, outer process/temp/tmux audits, frozen SHA, and clean/dirty
status. Do not infer absent cleanup payload fields or explain model output from
hashes.

- [ ] **Step 4: Write the honest verdict and commit evidence docs**

If every live success criterion passes, mark M2c PASS and unlock M3
brainstorming. Otherwise mark M2c BLOCKED, keep M3 locked, state only the first
unmet gate, and require a new brainstorming -> spec -> plan cycle before any
new attempt.

```bash
git add docs/validation/2026-07-13-phase3-m2-project-daemon.md docs/handoff/current-development-state.md HISTORY.md
git commit -m "Record semantic M2c live evidence"
```

## Final review checklist

Before declaring the implementation slice ready for Task 14, verify:

- [ ] Every requirement in the approved design maps to a task above.
- [ ] No production rule contains hardcoded `artifact.txt`, `draft-v1`, or
      `accepted-v2`; those values occur only in tests, validation evidence, and
      the live scenario.
- [ ] Legacy plan/Mission/snapshot hashes are unchanged.
- [ ] New semantic records use exact, versioned shapes.
- [ ] Leader output never directly becomes executable `task` text.
- [ ] AgentDeck never silently repairs missing semantics.
- [ ] Regeneration is same Leader/provider/model/transport, maximum once, within
      the original deadline.
- [ ] Confirmation binds authority, compiled tasks, policy, and generation.
- [ ] ACP and tmux consume the same compiled task bytes/hash.
- [ ] Worker context contains only the current semantic step.
- [ ] ProjectView and events contain compact provenance, not full effects or
      secrets.
- [ ] Runtime permission gates remain independent.
- [ ] M2c remains BLOCKED until the separately authorized real live passes.
- [ ] M3 remains locked until M2c passes.
