# AgentDeck V1 Product Requirements

## Product promise

AgentDeck V1 is a local-first multi-agent software-development workbench. A
developer describes one natural-language Mission, reviews and edits its exact
scope, confirms it once, and can then leave while governed Codex and Claude
collaboration continues in the background. When the developer returns,
AgentDeck recovers the same Mission and conversation and presents auditable
progress, a precise request for intervention, or an evidence-backed result.

Its compact contract is: one natural-language Mission, one exact confirmation,
governed Codex/Claude collaboration, background continuation, recovery, and
Evidence.

The promise is one confirmation for one frozen Mission, not unattended access
to new authority. Ordinary implementation, testing, review, and bounded
recovery may continue inside the confirmed scope. Any material change to that
scope or authority must pause for an explicit decision.

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
5. The developer confirms one exact Mission version and authorization digest.
   That one confirmation authorizes ordinary work only inside the frozen
   envelope.
6. A project daemon runs the confirmed Mission in the background. The
   developer may close the terminal without terminating the Mission.
7. On reopening `agentdeck`, the developer returns to the same conversation
   and activity cursor, with a readable account of completed work, current
   work, failures, pending decisions, and the next action.
8. The developer can inspect activity, use `/term` to view the relevant Worker,
   take over that Worker, and later return control. Automation does not resume
   that session until its state has been reconciled and a valid new Handoff is
   available.
9. AgentDeck delivers either a readable, evidence-backed result that satisfies
   the Mission's acceptance criteria or a precise, actionable pause. It does
   not present an unverified model self-claim as completion.
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

### Frozen Mission and governed work

- Every executable Mission must have an immutable version and authorization
  digest that binds the confirmed goal, semantic and file scope, operation
  classes, Worker constraints, transport and external-effect policy, budgets,
  retry bounds, acceptance criteria, and expiry where applicable. Editing the
  Mission creates a new version that requires confirmation.
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
- Acceptance must be graded against the Mission criteria: required checks can
  pass, fail, or remain unavailable with an explicit reason. Completion
  requires all mandatory grades to be accepted by Verification.
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
- A completed or precisely paused Mission may produce evidence-derived memory,
  skill, and Improvement Mission suggestions. Suggestions remain separate from
  execution and durable application until the user explicitly confirms them.

## Completion and pause semantics

Verification owns completion. A Leader or Worker statement such as "done," a
process exit, a terminal marker, or the existence of changed files is not
enough. A Mission is complete only when required Evidence is present, mandatory
tests and review have the expected grades, acceptance criteria are satisfied,
and remaining effects are accounted for.

AgentDeck must pause the affected work when safe progress requires any of the
following:

- new authority, credentials, operation classes, network access, or scope;
- semantic drift or path drift from the confirmed Mission;
- a destructive action or an external effect such as push, deploy, publish, or
  external send that was not exactly frozen and confirmed;
- exhausted budget, retry, concurrency, time, or policy limits;
- an ambiguous side effect whose outcome cannot be proven safely;
- Worker loss, protocol inconsistency, invalid permission or Handoff lineage,
  or conflicting durable facts;
- human takeover of the relevant Worker session; or
- an unrecoverable failure within the current authorization envelope.

A generic `BLOCKED` result is insufficient. A pause must identify the failed
stage, the authoritative facts known, completed or possibly completed side
effects, why autonomous retry is unsafe or exhausted, the exact user decision
or remediation required, and the deterministic command or conversation action
that continues the Mission.

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
  Claude performs peer review. Review findings cause bounded revision when
  required, followed by AgentDeck Verification and acceptance.
- **Golden B:** Claude is the explicitly selected Leader, Claude implements,
  and Codex performs peer review. Review findings cause bounded revision when
  required, followed by AgentDeck Verification and acceptance.

Each Golden Mission must prove all of the following:

- start from bare `agentdeck` with explicit Agent and exact model provenance;
- present and bind exactly one confirmation to the final Mission version and
  authorization digest before ordinary execution;
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
both setup and the documented Mission flow. Failure in any of these journeys
is a release failure until corrected and reverified; legacy harness status or
a model-authored explanation cannot waive it.

## V1 success measures

- Golden A and Golden B both pass from the public bare-entry journey with real
  tests, peer review, and complete authority and Evidence lineage.
- A client disconnect and a daemon restart never lose Mission identity,
  duplicate an uncertain external effect, or require a model to invent state.
- Every completion is supported by graded Verification; every non-completion
  names a precise, actionable pause instead of generic `BLOCKED`.
- No acceptance exercise records a silent fallback, permission expansion,
  scope expansion, skill enablement, memory write, or code self-modification.
