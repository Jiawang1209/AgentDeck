# AgentDeck V1 Product Requirements

## Product promise

AgentDeck V1 is a local-first multi-agent software-development workbench. A
developer describes one natural-language Mission, reviews and edits its exact
scope, confirms it once, and can then leave while governed Codex and Claude
collaboration continues in the background. When the developer returns,
AgentDeck recovers the same Mission and conversation and presents auditable
progress, a precise request for intervention, a precise terminal failure, or
an evidence-backed result.

Its compact contract is: one natural-language Mission, one exact confirmation,
governed Codex/Claude collaboration, background continuation, recovery, and
Evidence.

The promise is one confirmation for one frozen Mission, not unattended access
to new authority. Ordinary implementation, testing, review, and bounded
recovery may continue inside the confirmed scope. Any material change to that
scope or authority must pause for an explicit Mission Amendment and new
version confirmation, or for the user to deny or cancel it.

## Target user and problem

The primary user is a local software developer working in an existing or new
project who wants multiple coding Agents to collaborate without becoming the
human message bus between them.

Today that developer must manually choreograph multiple CLIs, repeat approvals
while babysitting each step, keep a terminal open for work to continue, and
decide whether a model's completion claim is trustworthy. This overhead makes
multi-Agent work fragile: role and model provenance is easy to lose, handoffs
are informal, reconnecting is uncertain, and a plausible answer can be
mistaken for a verified result.

V1 is successful for this user when orchestration becomes easier to supervise
than to perform manually, while authority, intervention, and acceptance remain
under the user's control.

## Assumptions and constraints

- V1 is local-first and optimized for software-development Missions: code
  changes, tests, review, bounded revision, recovery, audit, and local commit
  provenance.
- Codex and Claude are the only official V1 Agents. The user explicitly chooses
  one of them as Leader and may constrain which Agent implements or reviews.
- A project may have only one concurrently mutating Mission. Independent Tasks
  within that Mission may proceed concurrently when their dependencies,
  scopes, permissions, and budgets allow it.
- Closing an interactive client does not imply pausing or cancelling a
  confirmed Mission. Pause, cancel, takeover, return-control, and daemon
  shutdown are distinct user actions.
- Deterministic status, recovery, audit, and approval surfaces remain usable
  without an available model. AgentDeck never silently selects a model,
  transport, permission, or fallback.

## Primary user journey

1. The developer runs bare `agentdeck`. AgentDeck restores the project and its
   conversation when they exist, or guides the developer through local project
   initialization when they do not.
2. The developer explicitly chooses Codex or Claude as Leader, with the exact
   selected Agent and model shown before planning. Missing readiness is
   explained; it never triggers a silent substitution.
3. In the sustained project conversation, the developer states a
   software-development goal in natural language and may add constraints such
   as "Codex implements and Claude reviews."
4. AgentDeck presents a Mission Preview containing the goal, semantic and path
   scope, exclusions, Task and role proposal, acceptance criteria, authority,
   risk, limits, and provenance. Natural-language edits produce a new Mission
   version and digest. Nothing executes during preview.
5. The developer confirms one exact Mission version and authorization digest
   exactly once. Fully in-envelope work then proceeds with zero additional
   human decisions, including ordinary permission requests already covered by
   the frozen envelope.
6. A project daemon runs the confirmed Mission in the background. The
   developer may close the terminal without terminating the Mission.
7. On reopening `agentdeck`, the developer returns to the same conversation
   and activity cursor, with a readable account of completed work, current
   work, failures, pending decisions, and the next action.
8. The developer can inspect activity, use `/term` to view the relevant Worker,
   take over that Worker, and later return control. Automation does not resume
   that session until its state has been reconciled and a valid new Handoff is
   available.
9. AgentDeck delivers a readable, evidence-backed result that satisfies the
   Mission's acceptance criteria, a precise actionable pause, or a precisely
   classified terminal failure. It does not present an unverified model
   self-claim as completion or a terminal failure as resumable.
10. After useful work, the developer may review evidence-derived suggestions
    for memory, skills, or a follow-up Improvement Mission and decide whether
    to apply each one.

## V1 functional requirements

### Conversation and provenance

- Bare `agentdeck` must provide one sustained, project-scoped conversation for
  setup, Mission creation and revision, confirmation, progress questions,
  intervention, results, and learning review.
- The selected Leader must always be explicit. Mission and activity views must
  preserve the exact Leader Agent and model provenance and disclose the Agent,
  model, and transport used for every Worker attempt. No missing or failed
  route may silently fall back to another one.
- Role, Agent, model, and transport are distinct facts. A role describes the
  responsibility (`leader`, `worker`, or `reviewer`); an Agent is Codex or
  Claude; a model is the exact configured model identifier; and transport is
  ACP or a governed CLI/PTY route. An optional tmux binding is only an
  observation or takeover view, not a transport or product authority.
- The Mission envelope must record ordered permitted routes for each applicable
  Agent, such as ACP followed by CLI/PTY. If the active route fails, the daemon
  may activate a pre-approved fallback automatically only in that frozen
  order. Before or at activation it must emit an audit and activity event that
  identifies the failed route, a stable reason code, and the selected route.
  Automatic fallback is therefore allowed but never silent. When no approved
  route remains, AgentDeck must create zero new Worker effect and enter an
  actionable pause for the affected work.

### Frozen Mission and governed work

- Every executable Mission must have an immutable version and authorization
  digest that binds the confirmed goal, semantic and file scope, operation
  classes, Worker constraints, transport and external-effect policy, budgets,
  retry bounds, acceptance criteria, and expiry where applicable. Editing the
  Mission creates a new version that requires confirmation.
- Each exact frozen Mission version and digest receives exactly one Mission
  confirmation. After it is confirmed, all fully in-envelope work requires
  zero additional human decisions, including ordinary permission requests
  already covered by the envelope. A new authority decision is not another
  stage approval: AgentDeck must propose a Mission Amendment with a new version
  and digest, then record the user's explicit confirmation, denial, or
  cancellation and its lineage.
- The Leader must turn the confirmed goal into a dependency-aware Task graph
  and automatically propose Worker and reviewer assignments within the user's
  Agent, role, scope, and concurrency constraints. The user can inspect and
  amend those constraints before confirmation.
- Every Task attempt must use an identifiable Worker session with ordered
  progress and a durable relationship to its Mission, Task, Agent, model, and
  transport. Retries, splits, and reassignments remain distinguishable rather
  than overwriting prior attempts.
- Cross-Worker context must pass through an AgentDeck Handoff that identifies
  the source and destination work, relevant artifacts and Evidence, acceptance
  state, and provenance. Informal terminal text is not a valid Handoff.
- Permission decisions must retain lineage to the exact Mission version, Task,
  attempt, Worker session, requested operation, scope, and decision. A prior
  permission or contextual instruction cannot authorize a different effect.

### Evidence, acceptance, and recovery

- AgentDeck must collect Evidence for claimed work, including relevant
  artifacts, diffs or hashes, test and command outcomes, peer-review findings,
  and provenance. Terminal output or a model's narrative alone is insufficient.
- Verification is AgentDeck-owned deterministic evaluation of durable Evidence
  against the frozen Mission acceptance criteria. Reviewer Agents contribute
  findings and Evidence but never own Task or Mission completion. Each required
  check receives a `pass`, `fail`, or `unavailable` grade with its reason; a
  mandatory `fail` or `unavailable` grade blocks completion.
- Waiving a mandatory check or lowering an acceptance criterion requires a
  Mission Amendment, a new version and digest, explicit confirmation, and a
  recorded rationale. No user, Leader, reviewer, or adapter may directly
  override Verification on the already confirmed version.
- Recovery must be bounded. AgentDeck may retry, split, reassign, or replan
  only within the confirmed scope, budgets, retry limits, acceptance criteria,
  and already approved transports. It must not widen scope, lower standards,
  change models, or repeat an ambiguous external effect silently.
- One authoritative project daemon must continue confirmed work after client
  disconnect and support deterministic reconnect and restart recovery. The
  restored view must distinguish known completed work, safe resumable work,
  uncertain effects, lost Workers, and decisions that require the user.
- The user must be able to inspect a Worker, take over its interactive session,
  and return control. Human takeover suspends automated input to that session;
  return-control requires reconciliation before further dispatch.

### Shared product view and learning

- Conversation, deterministic CLI surfaces, and future clients must consume
  the same ProjectView of Mission versions, Tasks, attempts, sessions,
  permissions, Handoffs, Evidence, acceptance, activity, pauses, and learning
  suggestions. ProjectView is read-only and must not infer authority or
  completion from terminal pixels.
- A completed, precisely paused, or terminally failed Mission may produce
  evidence-derived memory, skill, and Improvement Mission suggestions.
  Suggestions remain separate from execution and durable application until the
  user explicitly confirms them.

## Completion and pause semantics

Verification owns completion. It is AgentDeck-owned deterministic evaluation
of durable Evidence against the frozen acceptance criteria. A Leader or Worker
statement such as "done," reviewer approval by itself, a process exit, a
terminal marker, or the existence of changed files is not enough. A Mission is
complete only when all required Evidence is present, every mandatory grade
passes, the frozen criteria are satisfied, and remaining effects are accounted
for.

`paused` means that an identified user decision or remediation can make the
affected work resumable. Pause scope must be the narrowest safe scope and must
be visible in activity and ProjectView.

### Session-local pause

Human takeover pauses automated input to that Worker session and pauses Tasks
that depend on that session's next result. Unrelated safe Tasks may continue.
Returning control does not erase the pause: AgentDeck must first reconcile the
session and establish valid continuation or Handoff facts.

### Task-local pause

A Worker or Attempt failure, protocol inconsistency, invalid permission or
Handoff lineage, route exhaustion, or conflicting Task facts pauses that Task
and its downstream dependents. Unrelated Tasks may continue only when they
have no dependency, shared-scope, or authority conflict with the affected
Task. The pause must not permit a retry beyond the frozen bounds.

### Mission-wide pause

A Mission-wide pause stops all new dispatch when AgentDeck detects new
authority or Mission version drift, global budget or policy exhaustion,
authorization revocation, or an ambiguous external effect that can affect
project truth. This includes an unfrozen destructive action or external effect
such as push, deploy, publish, or external send. Existing effects must be
reconciled before dispatch can resume. New authority or changed criteria
require a lineage-preserving Mission Amendment and confirmation of the new
version; denial or cancellation leaves the prior version unchanged.

### Terminal failure

After allowed bounded recovery is exhausted and no user decision can resume
within the same confirmed envelope, the affected Task or Mission enters
terminal `failed`, not actionable `paused`. Its final result must still report
the failed stage, authoritative facts, completed or possibly completed side
effects, retry safety, exhausted limits, and next available product action. A
user may create and confirm a new Mission version or a new Mission, but the
failed Task or Mission is never silently resumed.

A generic `BLOCKED` result is insufficient at every pause level and at terminal
failure. An actionable pause must identify the exact user decision or
remediation and the deterministic command or conversation action that resumes
the affected scope. A terminal failure must say that the same confirmed
version cannot resume rather than presenting a false action.

## Learning and self-improvement

After completion or a meaningful pause, an evidence-derived Learning Review
may suggest one of three durable improvements:

- a Memory suggestion for reusable project or user context;
- a Skill suggestion for a reviewable workflow capability; or
- an Improvement Mission suggestion for a normal, governed follow-up change.

Every suggestion must show its source Evidence, rationale, destination,
provenance, proposed content or diff, expected benefit, and relevant risk. The
user previews and explicitly confirms each durable application. The resulting
change remains auditable and, where applicable, reversible and subject to the
same ordinary Mission acceptance standards.

Learning must never silently change product code, expand permission, enable or
load a skill, write durable memory, select another Agent or model, or start an
Improvement Mission. Suggestions are context for a human decision, not
authority.

## Non-goals

AgentDeck V1 does not include:

- a GUI or browser workbench;
- a terminal emulator or WispTerm-class workspace;
- A2A federation;
- a third official Agent beyond Codex and Claude;
- a general provider matrix;
- multiple concurrently mutating Missions in one project;
- a skill marketplace;
- remote execution; or
- silent self-modification, including silent code changes, permission
  expansion, skill enablement, memory writes, model changes, or fallback.

These exclusions preserve a narrow local Codex/Claude product whose autonomy,
recovery, and acceptance can be proven before broader clients, providers, or
distribution surfaces are added.

## V1 product acceptance

V1 requires two real software-development Golden Missions through the ordinary
bare `agentdeck` user journey:

- **Golden A:** Codex is the explicitly selected Leader, Codex implements, and
  Claude performs peer review, followed by AgentDeck Verification and
  acceptance.
- **Golden B:** Claude is the explicitly selected Leader, Claude implements,
  and Codex performs peer review, followed by AgentDeck Verification and
  acceptance.

At least one of Golden A or Golden B must contain a deterministic seeded review
finding that rejects the first implementation, produces a distinct revision
Attempt, then passes re-review and re-verification while preserving complete
lineage. The other Golden may complete without a revision when its first
implementation passes review and Verification.

Each Golden Mission must prove all of the following:

- start from bare `agentdeck` with explicit Agent and exact model provenance;
- present and bind exactly one Mission confirmation to each exact frozen
  Mission version and authorization digest before ordinary execution;
- require zero additional human decisions for all fully in-envelope work,
  including ordinary permission requests already covered by the envelope; any
  repeated in-scope approval or confirmation prompt is an acceptance failure;
- treat a request for new authority as a Mission Amendment and new version
  decision with recorded lineage and an explicit confirm, deny, or cancel
  outcome, never as a repeated stage approval;
- continue in the background after the client closes and reconnect to the same
  conversation, Mission, and activity cursor;
- preserve permission, AgentDeck Handoff, Evidence, attempt, session, and
  acceptance lineage throughout collaboration and bounded revision;
- run real project tests, preserve their outcomes as Evidence, receive peer
  review from the other official Agent, and produce a readable final result;
- complete only through graded Verification, or pause with the precise
  actionable semantics defined above; and
- use no silent model, Agent, transport, permission, or acceptance fallback.

The V1 release gate also requires focused acceptance of daemon restart and
recovery, explicit permission refusal with zero unauthorized effect,
takeover/return-control reconciliation, and a fresh installation that reaches
both setup and the documented Mission flow. It must also include an allowed
fallback scenario in which a frozen ordered route fails, the failure and
stable reason code, and selected pre-approved route are disclosed in
audit/activity before or at activation, and provenance remains complete. A
disallowed or exhausted-route scenario must prove zero new Worker effect and
an actionable pause, with no unlisted route selected. Failure in any of these
journeys is a release failure until corrected and reverified; legacy harness
status or a model-authored explanation cannot waive it.

## Release invariants

- Golden A and Golden B both pass from the public bare-entry journey with real
  tests, peer review, and complete authority and Evidence lineage.
- A client disconnect and a daemon restart never lose Mission identity,
  duplicate an uncertain external effect, or require a model to invent state.
- Every completion is supported by graded Verification; every non-completion
  is correctly classified as an actionable pause or terminal `failed` with
  precise facts and effects, never generic `BLOCKED`.
- No acceptance exercise records a silent fallback, permission expansion,
  scope expansion, skill enablement, memory write, or code self-modification.
