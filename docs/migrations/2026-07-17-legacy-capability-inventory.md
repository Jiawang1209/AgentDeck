# AgentDeck V1 legacy capability inventory

## Purpose and decision rule

This inventory classifies the current implementation before V1 product-code
migration. It is a characterization record, not a claim that P1 or any later
phase is implemented. It neither authorizes an M2c live run nor makes the old
M2c harness a V1 release veto.

The V1 north star remains one natural-language Leader conversation, an explicit
Codex or Claude Leader, ACP-first governed Worker communication, tmux-visible
observation and takeover, one confirmation for one frozen Mission version, a
continuing ProjectDaemon, bounded recovery, auditable Handoff/Evidence, and
governed Skill/Memory learning. Classification follows those product outcomes,
not file survival.

Status meanings:

- `retain`: preserve characterized behavior and reuse the implementation where
  its ownership already matches V1.
- `refactor`: preserve proven behavior while moving ownership or narrowing the
  module behind a V1 boundary.
- `compat`: keep an externally consumed surface during migration; it is not the
  new internal authority.
- `archive`: preserve as historical evidence after replacement coverage exists,
  but remove it from routine release gates.
- `remove`: delete an authority assumption or scenario-specific restriction
  after its replacement has proved equivalent safety.
- `missing`: required V1 capability has no current implementation that can be
  treated as its authority.

**Retained behavior is not retained ownership or authority.** A row marked
`retain` may still need focused extraction so that only the ProjectDaemon owns
durable transitions. A row marked `refactor` is not permission for a rewrite:
characterization evidence must remain green until its target replacement owns
the same observable contract.

## Classification matrix

| Capability/module | Current authority | Status | Target owner | Migration phase | Characterization evidence | Removal gate |
|---|---|---|---|---|---|---|
| Conversation package (`src/agentdeck/conversation/`) | `ConversationSession.handle()` routes deterministic commands, Leader requests, preview, and confirmation; `ConversationRouter` and `LeaderGateway` share parts of intake and provider selection. | refactor | P2 Conversation application shell over the V1 Mission service | P2 | `tests/test_conversation_acceptance.py` proves clarification, exact semantic preview binding, stale-fact rejection, and foreground conversation acceptance; `tests/test_conversation_session.py` and `tests/test_conversation_router.py` characterize session and routing behavior. | Extract only after bare `agentdeck` and reconnect tests prove the same natural-language journey, explicit Leader identity, exact preview binding, and zero scheduling effects before confirmation through the new application boundary. |
| ProjectDaemon package (`src/agentdeck/daemon/`) core service, lease, protocol, recovery, scheduler, and supervisor behavior | `ProjectDaemonService` coordinates durable mission work while daemon protocol/lease/recovery modules constrain one controller and restart behavior. | retain | Sole-writer V1 ProjectDaemon | P1-P4 | `tests/test_daemon_background_mission.py`, `tests/test_daemon_crash_matrix.py`, `tests/test_daemon_reconnection.py`, and `tests/test_daemon_supervisor.py` prove background continuation, bounded crash handling, compact reconnect projection, correlated replies, and Handoff ordering. | No removal; behavior remains required. A behavior may move only when the same daemon acceptance and crash/reconnect characterization passes against the V1 store and domain. |
| ProjectDaemon package internal concentration | `src/agentdeck/daemon/service.py` currently contains 2,824 lines spanning permission waiters, transport admission, worktree snapshots, reconciliation, and service coordination. | refactor | Focused daemon application services calling typed Mission, governance, transport, and recovery ports | P1-P4 | `ProjectDaemonService`, `apply_permission_decision_request()`, `permission_state_for_attempt()`, and `resolve_previous_handoff()` are independently exercised across `tests/test_daemon_service.py`, `tests/test_daemon_governance.py`, and `tests/test_daemon_recovery.py`. | Split only one characterized seam at a time; require focused tests plus daemon acceptance and crash/reconnect suites before deleting the old path. Line count alone is never a removal gate. |
| ProjectView v1 (`PROJECT_VIEW_SCHEMA_VERSION = "project-view/v1"`) | `StateStore.project_view()` builds the current projection; `contracts.py`, CLI, daemon client, workbench, and tests consume the versioned shape. | compat | Read-model projector sourced from the active V1 structured authority | P1-P4 | `tests/test_contracts.py` validates ProjectView fields and semantic consistency; `tests/test_daemon_cli.py` proves ProjectView/workbench same-source daemon cards and offline read-only behavior. | Retire v1 only after v2 is implemented, every retained consumer has migrated or an explicit compatibility adapter exists, schema discovery is updated, and fixture/consumer tests prove no silent field drift. |
| Current `StateStore` JSON/JSONL authority (`src/agentdeck/state.py`) | `StateStore` owns broad state mutation, event journaling, ProjectView projection, approval, permission, Handoff, skill, memory, and migration-related behavior. | refactor | Legacy importer and compatibility-projection support; SQLite becomes the sole structured-state authority | P1 | `tests/test_state_mixed_version_lock_race.py`, daemon recovery/lease tests, and state-backed CLI tests characterize atomic-save, event, recovery, and lineage behavior; `StateStore.project_view()` characterizes the current read model. | Stop authoritative JSON/JSONL writes only after read-only migration preview, verified backup/import, integrity and projection comparison, atomic activation, restart recovery, and rollback gates pass. Never dual-write or silently fall back. Keep legacy reading only while supported migration/compat windows require it. |
| Approval, message ledger, permission lineage, Handoff, and Evidence behavior | `StateStore` methods such as `record_acp_permission_pending()`, `bind_mission_permission_evidence()`, `record_mission_handoff_evidence()`, and approval methods share authority with daemon governance/recovery checks. | retain | V1 Mission/Governance services with SQLite transactions committed only by ProjectDaemon | P1-P4 | `tests/test_daemon_governance.py`, `tests/test_daemon_recovery.py`, `tests/test_daemon_supervisor.py`, `tests/test_acp_runtime.py`, and approval tests prove exact lineage, consume-once decisions, fail-closed drift, correlated replies, and ordered Handoff. | Converge each lineage into the V1 domain only when a transaction-level test proves event, current state, revision, and provenance commit together and replay/restart yields the same decision. No lineage fact may be dropped merely because its legacy container is removed. |
| Provider and Leader gateway (`src/agentdeck/providers/`, `conversation/leader_gateway.py`) | `LeaderGateway`, `LeaderProvider`, `CodexCliProvider`, `ClaudeCliProvider`, and OpenAI-compatible provider code mix provider invocation, schema constraints, diagnostics, and plan interpretation. | refactor | Official Codex and Claude Leader adapters behind one V1 Leader port | P3 | `tests/test_conversation_leader_gateway.py`, `tests/test_conversation_leader_diagnostics.py`, `tests/test_leader_plan_schema.py`, and `tests/test_provider_openai_compatible.py` characterize explicit selection, bounded sanitized diagnostics, structured output validation, and forced governance gates. | Replace a provider path only after the adapter conformance suite proves identity/model/role provenance, cancellation, bounded diagnostics, schema failure, zero-effect refusal, and no hidden provider fallback. |
| ACP mapping and runtime (`runtime/acp.py`, `acp_client.py`, `acp_mapping.py`; `daemon/transports.py`) | `AcpTransport`, `AcpWorkerTransport`, mapping helpers, and permission sink translate ACP lifecycle/update/permission facts into current daemon/state records. | retain | ACP Worker transport adapter behind the V1 transport interface | P3-P4 | `tests/test_acp_mapping.py`, `tests/test_acp_runtime.py`, and `tests/test_daemon_transports.py` prove allowlisted/redacted mapping, protocol bounds, permission settlement, cancellation cleanup, ambiguity, and transport-independent semantic prompts. | No wholesale removal. Move behind the port only after conformance and Golden coverage prove the same ACP lifecycle, sanitized observations, permission lineage, ambiguity handling, and durable admission semantics. |
| tmux runtime (`runtime/tmux.py`, `TmuxWorkerTransport`) | `TmuxBackend` can create panes, send input, and capture output; some legacy paths treat pane/runtime state as execution evidence. | retain | Observation, human takeover, and explicitly governed CLI/PTY fallback adapter | P3-P4 | `tests/test_tmux_runtime.py` proves bounded subprocesses and private paste cleanup; `tests/test_conversation_transports.py` and `tests/test_daemon_transports.py` prove explicit route selection, no silent ACP-to-tmux fallback, correlated polling, and prompt equivalence. | Remove authority assumptions only after no Mission transition, completion, permission, or success decision is derived from pane text/readiness alone, while observation, attach, takeover, and disclosed fallback acceptance remain green. Do not remove tmux visibility itself. |
| Skill Registry, Memory suggestions/context, and Learning Review | `skills.py`, `StateStore`, CLI handlers, and contract/workbench projections implement explicit import/load/create/apply controls and read-only Learning Review suggestions. | retain | P5 governed learning and improvement services using V1 evidence/provenance | P5 | `tests/test_agent_cli.py` proves skill and memory suggestion queues plus read-only `learn review`; `tests/test_contracts.py` proves `learning_review` contract validation; `tests/test_leader_cli.py` proves natural-language Learning Review does not mutate suggestions. | Integrate only after V1 Mission Evidence can be referenced without raw prompt/output leakage and preview/confirm gates remain explicit. Existing Skill/Memory source files remain filesystem content; SQLite stores structured records and provenance, not their entire source bodies. |
| Legacy `cli.py` command handlers | `src/agentdeck/cli.py` is a 19,266-line parser, renderer, daemon client, provider/runtime coordinator, and legacy direct-write surface. | compat | Thin CLI/Conversation clients over application services and ProjectDaemon protocol | P2-P4 | `tests/test_agent_cli.py`, `tests/test_leader_cli.py`, `tests/test_daemon_cli.py`, and structured-output tests characterize commands, JSON surfaces, safety controls, and natural-language routes. | Extract command families incrementally; delete a direct handler only when its exact public contract delegates through the new application/daemon path, has no local-write or direct-execution fallback, and focused plus CLI contract tests pass. |
| `contracts.py` aggregation | `src/agentdeck/contracts.py` is an 18,481-line registry of payload builders, examples, field lists, and validators for many GUI/CLI surfaces. | refactor | Contract registry plus bounded domain-specific contract modules | P2-P5 | `tests/test_contracts.py` and CLI contract discovery tests verify examples, validators, schema indexes, and cross-card same-source semantics. | Extract one contract family at a time with byte/shape-compatible discovery and validator tests. Do not delete a published contract merely because its implementation owner changes. |
| `tests/test_m2c_live_acceptance.py` mega-harness | A 12,460-line scenario harness combines environment sealing, preflight, provider/tool probing, four fixed phases, permissions, validation, and the opt-in real live test. | archive | Historical M2c evidence bundle, replaced by bounded unit, contract, conformance, crash-matrix, and Golden suites | P4 | The file contains deterministic tests for authority digests, permission settings, preview diagnostics, four-stage completion, and `test_real_four_stage_m2c_acceptance`; `tests/test_daemon_acceptance.py` already provides narrower in-process acceptance characterization. | Archive only after every retained invariant is mapped to a named new test, Golden A/B pass with deterministic fakes, bounded official-adapter smoke is separately gated, and coverage review finds no unique safety assertion stranded in the harness. It must not remain a V1 release veto or authorize another M2c live run. |
| Fixed phase/count/one-shot scenario authority assertions | The M2c harness and parts of semantic planning require exact `implementation -> review -> revision -> acceptance`, exact step counts, fixed tokens, or one scenario-shaped authority. | remove | General V1 Mission DAG, version, policy, and evidence invariants | P1-P4 | `tests/test_m2c_live_acceptance.py` contains `_validate_four_stage_completion()` and exact fixed-scenario tests; `tests/test_daemon_acceptance.py` asserts four exact phases; `tests/test_leader_plan_schema.py` characterizes frozen worker order and step count. | Remove only after general Mission tests prove immutable version/digest, dependency order, bounded attempts, review/revision paths, permission lineage, and evidence-based acceptance for variable DAG shapes; keep one four-stage flow as a Golden example, never as universal authority. |
| SQLite Mission store | No SQLite repository currently owns Mission events/current state/revisions as one transaction. The existing migration helpers do not constitute the V1 SQLite authority. | missing | ProjectDaemon-owned SQLite repository and transaction boundary | P1 | Current inventory finds `StateStore` as the authority and no SQLite Mission-store implementation; existing daemon crash/recovery tests define behavior the new store must satisfy. | Complete when schema migrations, single-writer enforcement, atomic event/state/revision/provenance transactions, idempotency, integrity checks, restart replay, and legacy import/cutover tests pass. This is structured-state migration, not a move of repository content or large bodies into SQL. |
| V2 Mission domain and ProjectView v2 | Current mission/semantic modules and ProjectView v1 contain useful facts, but no single V2 aggregate owns Project, Conversation, immutable MissionVersion, AuthorizationEnvelope, Task DAG, Attempt, AgentSession, Permission, Handoff, Evidence, and Verification. | missing | V1 domain services and v2 read-model projector | P1-P2 | Existing mission, semantic-authority, daemon governance/recovery, and ProjectView contract tests provide characterization inputs, but `PROJECT_VIEW_SCHEMA_VERSION` is still `project-view/v1`. | Complete when typed transition tests cover terminal precedence, one-confirmation scope, bounded recovery, verification grades, complete provenance, and v1/v2 projection compatibility from the same SQLite authority. |
| Codex/Claude adapter conformance suite | Provider- and transport-specific tests exist, but no one shared suite proves both official Agents against the same Leader and Worker adapter obligations. | missing | P3 Leader/Worker adapter contract test kit | P3 | `tests/test_conversation_leader_gateway.py`, provider tests, ACP tests, and daemon transport tests are separate characterization sources. | Complete when Codex and Claude adapters run the same deterministic identity, structured proposal, cancellation, permission, ambiguity, fallback, sanitization, and zero-effect refusal cases; optional real smoke remains separately opt-in. |
| Golden A/B product acceptance | Current acceptance includes a fixed four-stage M2c scenario, but not two bounded V1 product Goldens that independently prove the normal and governed recovery/revision journeys through the new domain. | missing | P4 end-to-end product acceptance suite | P4 | `tests/test_conversation_acceptance.py`, `tests/test_daemon_acceptance.py`, background/reconnection/crash tests, and the M2c harness supply reusable assertions but not the V1 Golden pair. | Complete when Golden A proves the natural-language one-confirmation Codex/Claude collaboration to evidence-backed acceptance, and Golden B proves rejection/revision plus disconnect, recovery, permission/takeover, and safe completion or actionable pause through the same ProjectView. |

## Current concentration and migration pressure

The 2026-07-17 inventory snapshot reports these largest pressure points:

- `src/agentdeck/cli.py`: 19,266 lines;
- `src/agentdeck/contracts.py`: 18,481 lines;
- `src/agentdeck/state.py`: 11,662 lines;
- `tests/test_m2c_live_acceptance.py`: 12,460 lines;
- `src/agentdeck/daemon/service.py`: 2,824 lines;
- `src/agentdeck/conversation/session.py`: 1,044 lines.

These counts identify coupling and review pressure; they are not permanent
contracts, quality scores, or automatic rewrite triggers. Each extraction must
be justified by an ownership boundary and guarded by the named characterization
tests. In particular, the large M2c harness is evidence to decompose, not a
reason to discard its permission, recovery, sanitization, or lineage lessons.

## SQLite and filesystem boundary

The approved comprehensive SQLite direction means **all structured control-plane
authority** moves to SQLite: Mission versions, Tasks, Attempts, sessions,
permissions/approvals, Handoff/Evidence metadata, revisions, events, and their
provenance. It does not mean deleting filesystem-owned repository content,
complete logs, Skill or Memory source text, large artifacts, or user-controlled
configuration. Those remain files referenced by bounded identity, path, hash,
summary, and provenance where appropriate.

Likewise, SQLite does not replace ACP, tmux, or user-facing contracts. ACP remains
the preferred structured transport; tmux remains visible observation, takeover,
and governed fallback; ProjectView and CLI/Conversation contracts remain read
models and clients. None of them may become a second mutation authority.

## P0 conclusion

The implementation contains substantial foundations worth preserving: natural-
language conversation intake, daemon lifecycle/recovery, ProjectView contracts,
approval and permission lineage, ACP mapping, tmux visibility, and governed
Skill/Memory review. The central migration problem is ownership convergence,
not feature-count erasure. P1 must first add the missing SQLite Mission store and
V2 domain while keeping characterization green; P2 then makes the Conversation
product the primary client; P3 supplies official Codex/Claude adapters and a
shared conformance suite; P4 closes recovery and Golden A/B evidence; P5
integrates governed learning. Until those gates exist, no row in this inventory
claims the corresponding phase is complete.
