# AgentDeck V1 Architecture Reset Development Program

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize the existing AgentDeck capabilities into a reliable Codex-and-Claude V1 where a user starts `agentdeck`, chooses a Leader, confirms one frozen software-development Mission, leaves while a project daemon continues, returns to auditable progress, and receives evidence-backed completion plus safe learning suggestions.

**Architecture:** Use an evolutionary kernel reset rather than a greenfield rewrite. Preserve and converge the existing Conversation, ProjectView, daemon, ledger, approval, ACP/tmux, skill, memory, and learning foundations around one durable Mission authority; move the M2c mega-harness out of the release gate and replace it with deterministic state-machine/integration coverage, adapter conformance, and two real user-journey Golden Missions. Execute P0 through P5 strictly in order, with a separate task-level implementation plan approved at each phase boundary.

**Tech Stack:** Python 3.12, conda environment `agentdeck`, standard-library CLI and `sqlite3`, Unix-domain project daemon, JSON/JSONL legacy import, ACP, managed CLI/PTY, tmux observation/takeover, pytest, Git.

---

## 0. Plan authority and execution boundary

This document is the program-level development authority produced from the ten
approved architecture-design sections dated 2026-07-17. It supersedes the old
"close M2c, then begin M3" development route without deleting or rewriting the
evidence collected by that route.

The phases are strictly ordered:

```text
P0 Product Reset
  -> P1 Durable Mission Kernel
  -> P2 Conversation Product
  -> P3 Official Codex/Claude Adapters
  -> P4 Reliable Multi-Agent Closure
  -> P5 Learning Layer and V1 Release
```

Rules:

1. Only the current phase may change product or validation behavior.
2. Every phase receives a separate `writing-plans` task-level plan after the
   previous phase meets its exit gate.
3. The existing M2c live node is legacy evidence, not a release veto and not a
   node to retry.
4. M3 does not proceed in parallel under its historical meaning.
5. No phase may silently install/authenticate tools, change global settings,
   select another model, push, deploy, or broaden Mission authority.
6. All development and verification commands run in conda environment
   `agentdeck`.
7. Every user-visible or governing change updates `HISTORY.md` in the same
   commit.
8. The current phase plan is a hard scope boundary. A deterministic RED that
   proves a missing requirement triggers a plan correction; it does not grant
   permission to implement a later phase.

The immediately executable plan is:

`docs/superpowers/plans/2026-07-17-agentdeck-p0-product-reset.md`.

## 1. V1 product contract

The primary user journey is:

```text
agentdeck
  -> resume or initialize project conversation
  -> explicitly select Codex or Claude as Leader
  -> describe a software-development goal in natural language
  -> review/edit one Mission Preview
  -> confirm one exact Mission version and authorization digest
  -> leave while the project daemon runs
  -> return to the same conversation and activity cursor
  -> inspect/take over a Worker when desired
  -> receive evidence-backed completion or a precise pause
  -> review optional memory/skill/improvement suggestions
```

V1 supports Codex and Claude as official Agents. It is designed for additional
Leader providers and Worker CLIs, but no third Agent is implemented before P5.
V1 optimizes for software-development Missions: code changes, tests, review,
revision, acceptance, local commit provenance, recovery, and audit.

The following are not V1 deliverables:

- a Hive-style browser workspace;
- a WispTerm-like terminal emulator;
- A2A federation;
- multiple simultaneously mutating Missions in one project;
- a general provider matrix;
- silent self-modification;
- skill marketplace or remote execution.

## 2. Authoritative system boundary

The target dependency flow is:

```text
ConversationShell / script CLI / future clients
                    |
              ProjectView v2
                    |
          ProjectDaemon application API
                    |
     Mission Engine + Governance + Verification
                    |
             Event Ledger / SQLite
                    |
       Leader and Worker Adapter interfaces
                    |
        ACP | managed CLI/PTY | tmux view
```

One project daemon is the sole mutation authority. Clients may disconnect;
confirmed Missions continue. tmux is an observation, fallback, and takeover
surface, never a scheduler or completion authority. ACP is the preferred
structured transport, but AgentDeck owns Mission, task, attempt, permission,
handoff, evidence, and acceptance truth.

## 3. Unified domain model

The kernel uses these durable entities:

```text
Project
  -> Conversation
  -> Mission
      -> MissionVersion + AuthorizationEnvelope
      -> Task dependency graph
          -> Attempt
              -> AgentSession
              -> Permission
              -> Handoff
              -> Evidence
      -> VerificationResult
      -> LearningReview
          -> MemorySuggestion
          -> SkillSuggestion
          -> ImprovementMissionSuggestion
```

Required invariants:

- Confirmation binds an exact Mission version and digest.
- A Mission amendment creates a new version; it does not mutate history.
- A Task completes only after Verification accepts required Evidence.
- A Worker completion statement is not completion authority.
- Worker B receives Worker A's completion only through an AgentDeck Handoff.
- Every retry or reassignment creates a new Attempt.
- One Attempt may create multiple ordered permissions; count is not stage
  identity.
- A permission is valid only for its exact Mission, Task, Attempt, Session,
  operation, scope, and decision lineage.
- Append-only events preserve history; current-state tables provide recovery
  speed; ProjectView is the read-only client projection.

## 4. Leader, Worker, and transport adapters

Role, Agent, model, and transport remain separate:

```text
role:       leader | worker | reviewer
agent:      codex | claude
model:      exact configured model identifier
transport:  acp | cli_pty
view:       optional tmux binding
```

The user explicitly chooses the Leader. AgentDeck may recommend based on
readiness but never silently selects or changes the Agent/model. Running
Missions retain their frozen Leader provenance.

Leader adapters provide readiness, Mission creation/revision/recovery,
evidence summary, and learning review proposals. Their output is a proposal
validated by AgentDeck; Leader adapters cannot write state, schedule Workers,
approve permission, or complete Tasks.

Worker adapters receive a bounded Task envelope and produce normalized
progress, permission, artifact, handoff, failure, and completion events.
Adapters cannot write the StateStore directly.

Transport selection is explicit and recorded:

1. use ACP after capability/conformance checks;
2. use governed CLI/PTY only when the Mission transport policy allows it;
3. expose tmux as the optional observation/takeover binding;
4. fail with a stable transport diagnostic when no allowed route exists.

## 5. Project daemon and recovery

The project daemon uses a user-only Unix socket and one single-writer lock.
It owns command serialization, scheduling, Worker leases, event cursors,
reconciliation, and ProjectView revision publication.

V1 permits one running Mission per project. Independent Tasks inside that
Mission may run concurrently when dependency, Worker, file-scope, permission,
and budget checks pass. New Missions queue or wait for the active Mission to
pause/complete.

Every state-changing client command carries a command ID and expected project
revision. Duplicate command IDs are idempotent; stale revisions fail without
mutation. External effects use intent/outcome events so recovery does not
blindly repeat an effect whose outcome is uncertain.

Closing the ConversationShell records only client disconnect. Explicit
`pause`, `cancel`, takeover, return-control, and daemon shutdown have separate
state transitions. On restart the daemon loads durable state, reconciles
sessions/processes, and either resumes, records a known terminal state, or
pauses as `recovery_required`; it never asks a model to guess facts.

## 6. Governance and one-confirmation autonomy

The Mission Preview contains an `AuthorizationEnvelope` with exact goal,
semantic/file scope, allowed operation classes, Worker allowlist, transport
policy, network/git policy, retry bounds, concurrency, budgets, expiry, and
content digest.

One confirmation covers ordinary work inside that envelope, including scoped
project reads/writes, tests, review, and bounded recovery. New authority pauses
the relevant work. Separate confirmation remains required for destructive
operations, new credentials, new dependency/network classes, project-external
writes, push, deploy, publish, or external send unless the exact effect was
already shown and frozen.

Limited recovery may retry, split, reassign, replan inside scope, and use an
already-approved transport fallback. It may not retry without bounds, change
models silently, lower acceptance criteria, expand scope, ignore failed tests,
or rename a blocker as a warning.

Skill and memory context never grants authority. Human takeover suspends daemon
input to that session; return-control requires reconciliation and a new
handoff before automation resumes.

## 7. Verification architecture

The old single mega-harness is replaced with five layers:

1. pure unit and state-machine invariants;
2. public contract, governance, and security tests;
3. deterministic integration with real daemon and fake adapters;
4. isolated real adapter smoke/conformance;
5. two real Golden Missions through the user entrypoint.

Golden A:

```text
Codex Leader -> Codex implementation -> Claude review
             -> bounded revision when required -> AgentDeck acceptance
```

Golden B:

```text
Claude Leader -> Claude implementation -> Codex review
              -> bounded revision when required -> AgentDeck acceptance
```

Tests assert product semantics, not model sentences, fixed phase counts, fixed
permission counts, or private database fields. `BLOCKED` is reserved for an
unmet environment prerequisite. Once execution begins, failures use stable
stage-specific diagnostics and explain side effects, retry safety, and next
action.

## 8. Persistence and compatibility

New-kernel state is stored in `.agentdeck/state.db` using standard-library
SQLite. One daemon writes; clients read through daemon APIs and ProjectView.
Large artifacts and raw transcripts stay outside the database; state stores
paths, hashes, compact summaries, and provenance.

Legacy JSON/JSONL migration is previewed, explicitly confirmed, backed up,
written to a temporary database, verified, and atomically switched. A failed
migration leaves legacy state untouched. ProjectView v1 and v2 may coexist as
projections from one authority during migration.

Legacy CLI commands remain compatibility facades and must be rewired to the
same application services as the new Shell. No legacy command may continue a
parallel direct-write path after its migration slice.

Target responsibility map:

```text
src/agentdeck/
  app/             conversation and mission use cases
  domain/          Mission/Task/Attempt/authorization/events
  daemon/          IPC, scheduler, lease, recovery
  adapters/        Codex and Claude Leader/Worker adapters
  transports/      ACP, managed PTY, tmux view
  governance/      scope, permission, budget policy
  verification/    evidence and acceptance
  learning/        review, memory, skill suggestions
  storage/         SQLite, migrations, legacy import
  projections/     ProjectView versions
  compat/          legacy CLI facade
  cli/             shell and deterministic commands
```

This is a migration direction, not permission to create empty abstractions.
Files move only as part of a tested vertical slice.

## 9. Phase plan

### P0: Product Reset

**Purpose:** Freeze the approved product/architecture/validation route and
classify current assets before product-code changes.

**Deliverables:**

- V1 product requirements;
- kernel architecture and data-migration design;
- current capability retain/refactor/compat/archive/remove/missing inventory;
- M2c test migration matrix;
- V1 validation strategy;
- baseline verification record;
- roadmap, handoff, and HISTORY routing updates.

**Exit gate:** All documents agree that P1 is next, M2c is legacy evidence,
and no P1 code has begun. Existing deterministic baseline is recorded and the
current worktree is clean. See the dedicated P0 plan.

### P1: Durable Mission Kernel

**Purpose:** Establish SQLite-backed Mission authority and project-daemon
recovery with fake adapters.

**Planned files:**

- Create `src/agentdeck/domain/mission.py`
- Create `src/agentdeck/domain/authorization.py`
- Create `src/agentdeck/domain/events.py`
- Create `src/agentdeck/storage/sqlite_store.py`
- Create `src/agentdeck/storage/migrations.py`
- Create `src/agentdeck/storage/legacy_import.py`
- Create `src/agentdeck/app/mission_service.py`
- Refactor focused responsibilities from `src/agentdeck/state.py`
- Refactor focused responsibilities from `src/agentdeck/daemon/service.py`
- Add focused tests under `tests/domain/`, `tests/storage/`, and
  `tests/integration/`

**Required slices:**

1. schema v1 and transaction/event atomicity;
2. MissionVersion and authorization digest;
3. Task/Attempt state machine;
4. command idempotency and project revisions;
5. daemon single-writer ownership;
6. fake Worker dispatch/handoff/evidence;
7. client disconnect/reconnect;
8. daemon crash reconciliation;
9. legacy migration preview/confirm/verify/rollback;
10. ProjectView v2 with v1 compatibility projection.

**Exit gate:** The deterministic fake Golden Mission passes through public
daemon APIs, survives client and daemon restart, rejects stale/unauthorized
commands, and requires no real model or tmux.

### P2: Conversation Product

**Purpose:** Make bare `agentdeck` the complete V1 interaction path over the
new daemon/application authority.

**Planned files:**

- Refactor `src/agentdeck/conversation/session.py`
- Refactor `src/agentdeck/conversation/router.py`
- Refactor `src/agentdeck/conversation/terminal_ui.py`
- Create or refine `src/agentdeck/app/conversation.py`
- Refactor the bare-entry routing in `src/agentdeck/cli.py`
- Add focused tests under `tests/conversation/` and `tests/integration/`

**Required slices:**

1. setup/resume and explicit Leader selection;
2. `/agent` and `/model` without silent fallback;
3. natural-language goal to Mission Preview;
4. edit/confirm/cancel bound to exact digest;
5. activity stream with reconnect cursor;
6. `/status`, `/mission`, `/pause`, `/resume`, and `/cancel`;
7. legacy script CLI facade over the same service;
8. fake-adapter end-to-end user journey.

**Exit gate:** A user completes the entire fake Mission journey from one
`agentdeck` session, closes/reopens the client, and sees one consistent state.

### P3: Official Codex and Claude Adapters

**Purpose:** Normalize real Codex and Claude Leader/Worker behavior without
leaking provider-specific logic into the kernel.

**Planned files:**

- Create `src/agentdeck/adapters/base.py`
- Create `src/agentdeck/adapters/codex/leader.py`
- Create `src/agentdeck/adapters/codex/worker.py`
- Create `src/agentdeck/adapters/claude/leader.py`
- Create `src/agentdeck/adapters/claude/worker.py`
- Refactor reusable logic from `src/agentdeck/conversation/leader_gateway.py`
- Refactor reusable logic from `src/agentdeck/providers.py`
- Refactor reusable logic from `src/agentdeck/daemon/transports.py`
- Create focused `tests/adapters/` conformance fixtures

**Required slices:**

1. capability/readiness and exact provenance;
2. Codex Leader structured Mission proposal;
3. Claude Leader structured Mission proposal;
4. Codex Worker Task envelope/result;
5. Claude Worker Task envelope/result;
6. ACP event/permission normalization;
7. managed CLI/PTY fallback;
8. tmux observation binding;
9. cancellation, timeout, exit, and redaction taxonomy;
10. isolated real smoke for each official route.

**Exit gate:** Fake conformance and bounded real smoke pass independently for
both Agents; unavailable model/auth/transport facts are explicit and do not
cause silent fallback.

### P4: Reliable Multi-Agent Closure

**Purpose:** Compose official adapters with the Mission Engine for the actual
Codex-and-Claude product.

**Planned files:**

- Refactor `src/agentdeck/daemon/scheduler.py`
- Refactor `src/agentdeck/daemon/governance.py`
- Refactor `src/agentdeck/daemon/recovery.py`
- Create or refine `src/agentdeck/verification/evidence.py`
- Create or refine `src/agentdeck/verification/acceptance.py`
- Create focused deterministic integration tests
- Create two small opt-in Golden Mission tests and an operator walkthrough

**Required slices:**

1. Leader-generated Task graph and Worker assignment;
2. dependency-aware dispatch and safe parallelism;
3. structured cross-Worker Handoff;
4. permission bridge with arbitrary finite sequential requests;
5. graded verification and peer review;
6. bounded retry/split/reassignment;
7. Mission Amendment;
8. takeover/return-control reconciliation;
9. client disconnect and daemon restart in a real Mission;
10. Golden A and Golden B.

**Exit gate:** Both real Golden Missions pass from bare `agentdeck`, including
one confirmation, background continuation, reconnect, lineage, tests, peer
review, and evidence-backed result. Failures are stage-specific and safely
rerunnable after root-cause correction.

### P5: Learning Layer and V1 Release

**Purpose:** Connect completed Mission evidence to safe Hermes-like learning
and finish productization.

**Planned files:**

- Refactor existing learning-review behavior into `src/agentdeck/learning/`
- Reuse and narrow existing skill/memory suggestion paths
- Add Improvement Mission suggestion/application service
- Update ProjectView and ConversationShell learning surfaces
- Simplify `README.md` and its paired user-facing documentation
- Add fresh-install validation and V1 release record

**Required slices:**

1. evidence-derived Learning Review;
2. memory suggestion preview/confirm/apply;
3. skill suggestion preview/confirm/create/load;
4. Improvement Mission preview/confirm;
5. learning provenance and rollback;
6. later-Mission effectiveness trace;
7. concise onboarding and fresh-install demo;
8. final Golden A/B and migration verification.

**Exit gate:** Learning never mutates durable context or code without explicit
confirmation; a fresh user can install, set up, execute, leave, resume, inspect,
complete, and review learning suggestions using the documented path.

## 10. Validation and commit discipline

Every implementation slice follows:

```text
write one deterministic RED
-> run the narrow test and observe the intended failure
-> implement the smallest production behavior
-> run narrow GREEN
-> run the adjacent regression set
-> update HISTORY and phase checklist
-> git diff --check and compile
-> commit one vertical behavior
```

Live tests are never the RED for ordinary domain behavior. They run only after
fake conformance and deterministic integration are green. A live failure must
first be classified as environment prerequisite, adapter defect, kernel defect,
or external transient failure before a new plan changes scope.

No test file may become a second scheduler or permission engine. Test helpers
may inject faults or fake adapter events, but production state transitions must
run through public application/daemon APIs.

Phase commits use these categories:

- `docs:` approved design, plan, inventory, validation evidence;
- `test:` independent RED with no implementation;
- `feat:` minimal GREEN product slice;
- `refactor:` behavior-preserving responsibility move after characterization;
- `fix:` proven defect with deterministic regression;
- `chore:` migration or release mechanics with no product behavior change.

No merge or push is part of this program unless separately requested.

## 11. Stop conditions

Stop the current phase and return to design review if any of these occurs:

- two components can mutate the same product fact;
- legacy and new stores disagree on authority;
- an adapter requires provider-specific branches in the Mission Engine;
- SQLite migration cannot prove backup and rollback safety;
- daemon restart can repeat an uncertain external effect;
- confirmation is not bound to an exact Mission digest;
- a live test requires new product fields with no user value;
- test code starts implementing production scheduling/governance semantics;
- the current phase has no demonstrable user-facing or authority improvement;
- work begins on a later phase before the current exit gate;
- GUI, A2A, third-Agent, or marketplace work enters V1 scope.

## 12. Risk register

| Risk | Control |
| --- | --- |
| The reset becomes another long non-shipping rewrite | Strict P0-P5 ordering, one demonstrable vertical increment per phase, and phase-specific task plans |
| Legacy and new state both claim authority | One daemon writer, one SQLite authority, compatibility projections only, and an immediate stop condition for any parallel write path |
| Legacy migration damages a project | Read-only preview, explicit confirmation, complete backup, temporary database verification, atomic switch, and bounded rollback |
| Daemon concurrency repeats effects | Command idempotency, expected revisions, Worker leases, and intent/outcome events before recovery |
| Model nondeterminism destabilizes the kernel | Models propose; deterministic domain, governance, and verification code authorizes and advances state |
| Codex, Claude, or ACP behavior drifts | Versioned readiness provenance, adapter conformance, explicit capabilities, and isolated real smoke |
| Learning persists a bad or unsafe rule | Evidence-derived suggestions, preview/diff/provenance, explicit application, rollback, and ordinary Mission verification for self-improvement |
| Future GUI/A2A/providers expand V1 indefinitely | Explicit V1 non-goals and a stop condition when later-product work enters the active phase |

Risk controls are product requirements, not optional implementation advice.
Every phase plan must identify which rows it exercises and how its exit evidence
proves the corresponding control.

## 13. Program definition of done

The architecture reset is complete only when all statements are true:

- Bare `agentdeck` is the normal sustained conversation entrypoint.
- The user explicitly chooses Codex or Claude as Leader.
- One confirmation binds one exact Mission authorization envelope.
- One project daemon owns mutations and continues after client disconnect.
- Codex and Claude collaborate through AgentDeck-governed sessions.
- ACP is preferred; CLI/PTY/tmux fallback is explicit and governed.
- Worker Handoff, permission, evidence, and acceptance share one ledger.
- Closing/reopening the client restores the same conversation and activity.
- Bounded recovery handles ordinary failures without requiring babysitting.
- New authority and ambiguous effects pause with an actionable explanation.
- Golden A and Golden B pass through the real user entrypoint.
- Learning produces reviewable suggestions rather than silent mutation.
- Old M2c evidence remains traceable but no mega-harness vetoes release.
- Other Agents, GUI, A2A, and marketplace can be added without creating a
  second orchestration kernel.

## 14. Program progress checklist

- [ ] P0 Product Reset plan executed and exit gate approved
- [ ] P1 task-level plan written and approved
- [ ] P1 Durable Mission Kernel executed and exit gate approved
- [ ] P2 task-level plan written and approved
- [ ] P2 Conversation Product executed and exit gate approved
- [ ] P3 task-level plan written and approved
- [ ] P3 official Codex/Claude adapters executed and exit gate approved
- [ ] P4 task-level plan written and approved
- [ ] P4 reliable multi-Agent closure executed and exit gate approved
- [ ] P5 task-level plan written and approved
- [ ] P5 learning/productization executed
- [ ] V1 release evidence approved
