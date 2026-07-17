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
  may activate the next frozen, pre-approved route automatically only when
  durable Evidence proves that the failed route produced no operation effect,
  or when the exact operation is explicitly idempotent or consume-once and
  reconciliation proves retry cannot duplicate or conflict with an existing
  effect. A timeout, lost route, or unknown outcome is not enough to satisfy
  either condition.
- Before or at safe fallback activation, AgentDeck must emit an audit and
  activity event that identifies the failed route, a stable reason code, and
  the selected route. Automatic fallback is allowed but never silent. If any
  prior effect is ambiguous, AgentDeck must dispatch zero fallback Attempt or
  effect, enter a Mission-wide reconciliation pause, and require durable fact
  inspection plus an explicit user decision. If the prior route is proven safe
  but no approved route remains, it must create zero new Worker effect and
  enter an actionable pause when a concrete route remediation or amendment is
  available; otherwise the exclusive transition matrix determines terminal
  failure.

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
- Recovery must satisfy the exclusive transition matrix below. Any retry,
  split, reassignment, replan, or transport fallback must remain inside the
  confirmed scope, budgets, retry limits, acceptance criteria, and approved
  transport order. Recovery must never widen scope, lower standards, change
  models, or retry while a prior effect remains ambiguous.
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
- Completed, meaningfully paused, and terminally failed Missions are eligible
  for evidence-derived memory, skill, and Improvement Mission suggestions.
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

### Exclusive transition matrix

The following matrix is the sole recovery and interruption classifier. It is
exclusive through all-facts safety precedence, not by row order. AgentDeck
must evaluate all simultaneously active durable facts in one coherent snapshot
before choosing a transition. Every selected state, scope, and reason is
recorded in activity and ProjectView; a model's status word never chooses them.

Safety precedence is explicit:

1. Terminal `completed`, `failed`, or `cancelled` states are absorbing. Later
   session or Task observations cannot downgrade them to `paused` or
   `recovering`, restart dispatch, or reopen their work.
2. For a nonterminal state, facts that satisfy the terminal-failure condition
   cannot be masked by a lower-scope actionable pause.
3. Mission-wide authority and project-truth blockers outrank Task-local and
   session-local facts. These blockers are authorization revocation, Mission
   version or digest drift, an ambiguous external effect or project truth, and
   an amendable global policy or limit decision. They suspend all new
   dispatch.
4. Task-local actionable blockers outrank a session-only continuation when a
   dependency or shared scope is affected. Their downstream dependents pause;
   unrelated Tasks continue only when no dependency, shared-scope, or authority
   conflict exists.
5. Session-local takeover is selected only when no higher-scope blocker is
   active. It pauses automated input to that session and its dependent Tasks;
   unrelated safe Tasks may continue.
6. `running` or `recovering` automatic recovery is allowed only when no
   blocking fact at any higher precedence is active and the retry,
   reassignment, split, or fallback is proven safe inside the frozen envelope.
7. If multiple blockers exist at the same precedence and scope, the selected
   pause or failure record must preserve all reasons, not only one matched
   reason.

For example, takeover plus authorization revocation remains Mission-wide
`paused` and suspends all new dispatch. Terminal `failed` plus a later takeover
remains `failed` and dispatches nothing.

| Durable fact set after precedence evaluation | Required state and scope | Required behavior |
| --- | --- | --- |
| A terminal `completed`, `failed`, or `cancelled` state already exists. | Remain in the same absorbing terminal state. | Produce zero further dispatch or effect for that work. Preserve later observations as audit facts only. |
| Bounded recovery is exhausted and no valid user action can resume safely; a hard or non-amendable policy is violated; or an allowed amendment is declined and no route remains. | Terminal `failed` at the affected Task or Mission scope. | Report the failed stage, authoritative facts, completed or possibly completed effects, retry safety, exhausted limits, and next available product action. The failed work is never silently resumed; a later version or Mission is distinct. |
| A prior operation effect or project truth is ambiguous, including a timeout, lost route, or unknown outcome without sufficient durable proof. | `paused`, Mission-wide reconciliation pause. | Stop all new dispatch and create zero fallback Attempt or effect. Inspect durable facts and require the concrete user decision needed to restore trustworthy project truth. If reconciliation proves safe, classify again from the complete new snapshot; if no valid continuation remains, transition to `failed`. |
| New authority, Mission version or digest drift, authorization revocation, or an amendable global limit or policy decision has a valid user decision path, and project truth is not ambiguous. | `paused`, Mission-wide. | Stop all new dispatch. Present the exact decision or lineage-preserving Mission Amendment; confirm, deny, or cancel it explicitly. |
| A Task-level Worker, Attempt, protocol, lineage, or route failure has no safe automatic recovery, but a concrete user decision or remediation can create a valid continuation, and no Mission-wide blocker is active. | `paused`, Task-local. | Pause that Task and its downstream dependents. Unrelated Tasks may continue only with no dependency, shared-scope, or authority conflict. Present the exact remediation; do not exceed frozen bounds before it is resolved. |
| A Task-scoped budget or retry limit is exhausted, an allowed amendment can raise or change it, and no higher-precedence blocker is active. | `paused`, Task-local. | Present the limit change as a Mission Amendment. A corresponding amendable global budget or policy limit is Mission-wide. |
| Human takeover is active for a Worker session and no terminal, Mission-wide, or Task-local blocker is active. | `paused`, session-local. | Pause automated input to that session and Tasks that depend on its next result. Unrelated safe Tasks may continue. Return-control requires reconciliation and valid continuation or Handoff facts. |
| A Worker, Attempt, protocol, lineage, or route failure has a bounded automatic recovery that is proven safe and fully inside the frozen envelope, with no blocking fact active at any higher precedence. | Stay `running` or enter `recovering`; no pause scope. | Retry, reassign, split, or activate a proven-safe pre-approved fallback within the frozen bounds. This requires no human decision and no pause. Return to `running` after recovery succeeds. A Worker or Attempt failure does not automatically pause. |

Budget or retry exhaustion is `paused` only when an allowed amendment can
change the limit. It becomes `failed` when the limit is hard or non-amendable,
or when the amendment is declined and no route remains.

A generic `BLOCKED` result is insufficient. An actionable pause must identify
the exact decision or remediation and deterministic continuation action. A
terminal failure must state that the failed Task or Mission cannot resume and
must not present a false continuation action.

## Learning and self-improvement

Completed, meaningfully paused, and terminally failed Missions are eligible for
an evidence-derived Learning Review that may suggest one of three durable
improvements:

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
both setup and the documented Mission flow. Transport acceptance must include:

- an allowed fallback scenario in which durable Evidence proves the first
  route produced no effect before fallback, or the exact operation is declared
  idempotent or consume-once and reconciliation proves retry cannot duplicate
  or conflict; the failed route, stable reason code, and selected pre-approved
  route are disclosed in audit/activity before or at activation;
- a proven-safe but disallowed or exhausted-route scenario with a concrete
  remediation path that produces zero new Worker effect, selects no unlisted
  route, and enters an actionable pause; and
- an ambiguous-effect scenario that produces zero fallback dispatch or effect,
  stops new Mission dispatch, and enters a Mission-wide reconciliation pause
  for fact inspection and user decision.

Transition acceptance must additionally include:

- a deterministic overlapping-facts case proving the safest scope wins:
  takeover plus authorization revocation must produce one Mission-wide
  `paused` classification, suspend all new dispatch, and retain the takeover
  fact without selecting the session-local scope; and
- a hard non-amendable exhaustion case proving terminal `failed`, zero further
  dispatch, and absorption of a later takeover observation without restarting
  or downgrading the failed work.

Failure in any of these journeys is a release failure until corrected and
reverified; legacy harness status or a model-authored explanation cannot waive
it.

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
