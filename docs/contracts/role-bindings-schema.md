# Role Bindings Contract (`role-bindings` via `project-view/v1`)

## Which Role Card Am I Looking At?

AgentDeck has **three** read-only role surfaces. They answer three different
questions and a GUI tells them apart from the payload alone — never from the
word "role" in a field name:

| Card / mode | Question it answers | Where it lives |
| --- | --- | --- |
| `role_bindings` (**this contract**) | "which of the six north-star layers has my project filled in, with what, and what is still missing" | `agentdeck roles`, workbench `roles_card` |
| `role_topology` (older, unchanged) | "what is each coordination role / worker doing **right now**" — live status, blocker, next command | workbench `role_topology_card`, chat `mode=role_topology` |
| `role_card` | "which agents did I configure, and how do I reassign their roles" | workbench `role_card` |

The two topology-ish cards used to both emit `mode = "role_topology"`, which
made them indistinguishable to a GUI reading `mode`. This card is the one that
was renamed: it reports **bindings** (a completeness map with a fail-closed
`bound` / `unbound` / `ambiguous` status), not live activity. The older
`role_topology_card` keeps its name, its mode, its shape and its behaviour
byte for byte. Note the deliberate asymmetry: the workbench **key** for this
card stays `roles_card` (named after the `agentdeck roles` command), while its
`mode` is `role_bindings`.

Discovery entrypoint: `agentdeck contract role-bindings` (`--example` adds a
stable GUI-ready example). Source of truth for fields, example, payload and the
validator is `src/agentdeck/contracts.py` (`ROLE_BINDINGS_CARD_FIELDS`,
`ROLE_BINDINGS_ROLE_FIELDS`, `ROLE_BINDINGS_CONTROL_FIELDS`,
`role_bindings_example()`, `role_bindings_contract_payload()`,
`validate_role_bindings_contract()`). The binding derivation itself lives in the
pure module `src/agentdeck/role_topology.py` (`ROLE_TOPOLOGY_LAYERS`,
`ROLE_BINDING_KINDS`, `ROLE_BINDING_STATUSES`, `ROLE_LIFECYCLES`, `ROLE_SPECS`,
`IMPLEMENTATION_ROLE_HINTS`, `REVIEW_ROLE_HINTS`, `WORKER_ROLE_HINTS`,
`resolve_worker_roles()`, `resolve_worker_role()`),
which has zero IO and imports nothing from `cli`/`state`/`config`.

Frozen design:
`docs/superpowers/specs/2026-08-01-g6-role-topology-design.md` (north-star
Phase G6).

This card answers one question: **which of the six north-star role layers has
this project actually filled in, with what, and what is missing.**

## Two Surfaces, One Builder

| Surface | Shape |
| --- | --- |
| `agentdeck roles` | the card itself |
| `agentdeck workbench` → `roles_card` | the same card, field for field |

Both come from the same builder (`_role_bindings_card(config, project_view)` in
`src/agentdeck/cli.py`) and pass the same validator, so a GUI can render either
without re-deriving anything. `source_command` is `agentdeck roles` on both.

## The Six Roles Are Not the Same Kind of Thing

This is the pivot of the design. Flattening the six north-star roles into one
agent table would lie, so every role carries a closed `binding_kind`:

| Layer | Role | `binding_kind` | Binding source | Pane? |
| --- | --- | --- | --- | --- |
| `intake` | `frontdesk` | `command` | the `agentdeck frontdesk` command itself | no |
| `orchestration` | `planner` | `logical_leader` | `[leader.planner]`, falling back to `[leader]` | no |
| `orchestration` | `orchestrator` | `logical_leader` | `[leader.orchestrator]`, falling back to `[leader]` | no |
| `work` | `coder` | `worker_agent` | the `[[agents]]` entry whose role reads as implementation | yes |
| `work` | `code_reviewer` | `worker_agent` | `[review].reviewers`, falling back to a review-flavoured `[[agents]]` role | yes |
| `acceptance` | `round_reviewer` | `worker_agent` | `[review].round_reviewer` | yes |

`binding_kind` also explains **why some fields are necessarily null**, and the
validator enforces exactly that:

- `binding_kind != "worker_agent"` → `runtime_status`, `pane_id` and `agent_id`
  are always `null`. A logical Leader sub-role is not a tmux pane; that fact is
  already frozen in the existing `leader_backend` provenance.
- `binding_kind == "command"` → `provider`, `model`, `backend` and `transport`
  are always `null`. The intake layer has no provider at all.
- Worker layers carry `provider` and pane provenance but no `model` /
  `backend` / `transport`: those describe the Leader's reasoning backend, and a
  worker agent is a pane running a CLI, not an API-backed reasoner.

## Closed Enums

| Enum | Values |
| --- | --- |
| `layer` | `intake`, `orchestration`, `work`, `acceptance` |
| `binding_kind` | `command`, `logical_leader`, `worker_agent` |
| `binding_status` | `bound`, `unbound`, `ambiguous` |
| `lifecycle` | `persistent`, `task_scoped`, `on_demand` |

`lifecycle` describes **design intent** (frontdesk/planner/orchestrator are
resident, coder/code_reviewer are task-scoped, round_reviewer is on demand).
It is a different thing from `runtime_status`, which is what the pane is doing
right now. Do not conflate them.

## `binding_status` Is Fail-Closed

| Value | Meaning |
| --- | --- |
| `bound` | exactly one agent resolved for this layer, **and that agent resolves for no other layer** |
| `unbound` | this project has not configured the layer (for example no `[review] round_reviewer`) |
| `ambiguous` | several candidates resolved, or the single candidate is also evidence for another layer, and no existing fact picks between them |

`ambiguous` is the fail-closed expression, and it covers **two** kinds of
collision:

- *within one layer* — two agents both read as an implementation role;
- *across layers* — one agent's free-text role reads as several layers at once
  (`"implementation review"`, `"代码审查与实现验证"`). Such a role is ambiguous
  **evidence**, so it binds neither layer. Binding it to one layer would hand
  work to whichever layer asked first — possibly the reviewer, silently
  erasing the real implementer from the map — and binding it to both would make
  one worker review its own work, which the review-iteration design explicitly
  bars.

In every case the card reports `agent_id = null`, `binding_status =
"ambiguous"` and lists **every** candidate in `candidates[]`. It never silently
picks — a wrong silent pick would send work to the wrong worker. Because both
hint-matched worker layers (`coder`, `code_reviewer`) are resolved in a single
pass by `resolve_worker_roles()`, a cross-layer collision is visible to both;
the `blocker` names the colliding agent and both layers, and asks the human to
disambiguate the `role` text in `[[agents]]`. `resolve_worker_role()` (single
layer) is a thin projection of the same pass and is fail-closed identically.

None of the three states is an error and none of them blocks any command: the
topology is an observation surface, not a gate.

`blocker` is non-empty exactly when the status is not `bound`, and says what is
missing and where to configure it. `candidates[]` is non-empty exactly when the
status is `ambiguous`.

## Card Fields

| Field | Meaning |
| --- | --- |
| `mode` | always `role_bindings` |
| `source_command` | always `agentdeck roles` (the workbench copy included) |
| `layer_count` | number of `roles[]` (always the six north-star layers) |
| `bound_count` / `unbound_count` / `ambiguous_count` | derived counts; they must match `roles[]` and add up to `layer_count` |
| `split_enabled` | `config.leader_split_enabled()` — whether planner/orchestrator are explicitly configured or inherit `[leader]` |
| `roles` | the six role items, in north-star display order |
| `controls` | card-level inspect entrypoints (`agentdeck roles`, `agentdeck workbench`) |

## Role Item Fields

| Field | Meaning |
| --- | --- |
| `role` | one of the six north-star role names |
| `layer` | closed enum, see above |
| `binding_kind` | closed enum, see above |
| `binding_status` | closed enum, see above |
| `agent_id` | the bound worker agent; `null` for every non-`worker_agent` layer and for any layer that is not `bound` |
| `provider` | logical layers: the resolved Leader provider; worker layers: the agent's provider; `command`: `null` |
| `model` | resolved Leader model for logical layers; `null` elsewhere |
| `backend` / `transport` | normalized Leader provenance from `state.leader_backend_identity()` (`api`/`http`, `cli`/`subprocess`, `local`/`local`); `null` outside `logical_leader` |
| `lifecycle` | closed enum, see above — design intent, not live status |
| `runtime_status` | the ProjectView `agents[].runtime.status` projection (`configured` / `running` / `stale`); `null` outside `worker_agent` |
| `pane_id` | the ProjectView pane binding; `null` when not running or not a worker layer |
| `blocker` | non-empty exactly when `binding_status != "bound"` |
| `candidates` | non-empty exactly when `binding_status == "ambiguous"` |
| `controls` | at least one `kind=inspect` control; bound workers also get a `kind=terminal` control that is disabled with `agent is not running` until the pane exists |

Every control uses `kind`, `label`, `command`, `safety`, `enabled`, `blocker`,
and **every control is `safety=inspect`**. Controls carrying a `<placeholder>`
must be disabled with a matching blocker (the `frontdesk` layer's
`agentdeck frontdesk --message <text>` is the standing example).

`agent_id` is free-form in `[[agents]]`, so an interpolated worker command can
come out looking like a placeholder template (`agentdeck inbox --agent co<der`).
The validator cannot tell a real `<` from a template `<`, so the **builder**
resolves it: any control whose interpolated command would contain `<` is
emitted disabled, with a blocker naming the agent id and pointing at
`[[agents]]`. A legal config never makes this read-only card — or `agentdeck
workbench`, which embeds it and feeds `controls` / `ui serve` / the dashboard —
fail.

## Derivation Reuses Existing Authority

The card introduces no configuration surface and no second state source:

- planner / orchestrator provider+model come from
  `config.resolved_planner_backend()` / `resolved_orchestrator_backend()`,
  which already encode the `[leader]` fallback;
- `backend` / `transport` come from `state.leader_backend_identity()`, the same
  normalization every other `leader_backend` card uses;
- `runtime_status` / `pane_id` come from the ProjectView `agents[]` runtime
  projection — **tmux is never read**;
- `[review].reviewers` / `[review].round_reviewer` are read exactly as the
  review-group feature already parses them (a configured `reviewers` group
  binds its head, which is the same primary-reviewer rule `review_group` uses).

## Safety Boundary

- Fully read-only: no state writes, no events, no chat turns, no provider
  calls, no tmux read or write, no spawn/stop/dispatch.
- Every control is inspect-level; placeholder commands are disabled.
- The topology is an **observation surface, not an authorization**:
  `binding_status` and `lifecycle` change no gate, authorize no dispatch, and
  do not affect review groups, iteration budgets or merge verdicts.
- Ambiguity is always reported, never resolved silently.

## Division of Labour with the Other Role Cards

| Card | Question it answers |
| --- | --- |
| `role_card` | "which agents did I configure, and how do I reassign their roles" |
| `role_topology_card` (workbench, older, `mode=role_topology`) | "what is each coordination role / worker doing right now" (live status overlay) |
| `roles_card` / `agentdeck roles` (this contract, `mode=role_bindings`) | "which of the six north-star layers has my project filled in, and what is missing" |

The overlapping fields (`agent_id`, `provider`, runtime status) are all derived
from the same ProjectView snapshot; this card copies no `role_prompt` (that is
`role_card`'s job) and adds no state of its own.

## Non-Goals

- An explicit `[roles]` configuration section (a separate slice if ever needed;
  this one proves derivation suffices).
- Letting the topology influence scheduling, approvals or the merge gate.
- Provider liveness probing (that is `provider_health`'s job).
- Turning frontdesk/broker into a resident process.

When the card fields, the closed enums, the derivation or the validator change,
update this document, `CONTRACT_INDEX_SPECS`, `README.md`, `CLAUDE.md`,
`HISTORY.md` and the tests in the same commit.
