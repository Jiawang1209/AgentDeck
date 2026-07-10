# Sequential Handoff Skill Design

- **Date**: 2026-07-10
- **Status**: Approved in design dialogue; awaiting written-spec review

## Context

AgentDeck now has a foreground, resumable sequential workflow engine. An existing Leader plan can be explicitly interpreted as a frozen A→B→C chain through `agentdeck workflow preview|run|status|resume`. The engine validates correlated Worker replies, records lineage and artifacts, and advances only after the current step completes.

The remaining gap is planning quality. A real Codex or Claude Leader must know how to produce a plan that is suitable for deterministic linear handoff. AgentDeck already supports built-in skills and explicit Leader skill loading, but the Leader provider prompt receives only compact skill metadata. It deliberately excludes full `content_snapshot`, so adding instructions only to a `SKILL.md` body would not affect real Leader planning.

## Goal

Add a built-in `sequential-handoff` skill and a compact, audited `planning_guidance` metadata field. When the skill is explicitly loaded for `agent_id=leader`, its bounded guidance enters the Leader provider prompt and shapes a fixed linear plan. The skill also asks the Leader to mention the explicit `workflow preview` then `workflow run --confirm` operator sequence in the plan summary.

## Non-goals

- No automatic skill load or implicit enablement.
- No Worker skill injection.
- No full skill body or `content_snapshot` in Leader provider prompts.
- No execution authorization from skill metadata.
- No automatic plan execution, approval, dispatch, tmux access, or inbox acknowledgement.
- No DAG, parallel branch, fan-out, fan-in, cycle, repeat, or dynamic step expansion.
- No change to ordinary plan, approval, dispatch, capture-reply, or run-loop semantics.
- No real Codex/Claude recitation run in the implementation commit; that is the next acceptance step.
- No migration of existing inline built-in skills to file-backed skill directories.

## Chosen Approach

Keep the current `BUILTIN_SKILLS` storage model and add `sequential-handoff` as another inline built-in definition in `src/agentdeck/skills.py`.

Rejected alternatives:

1. Putting all planning rules in `description` would overload discovery metadata and encourage agents to skip the skill body.
2. Converting every built-in skill to a file-backed folder would be an unrelated registry migration.
3. Injecting the complete `content_snapshot` into Leader prompts would break the established compact-only boundary.
4. Declaring a dependency on the existing `planning` skill would add an unnecessary `load-plan` / `--with-deps` ceremony for a small, self-contained workflow skill.

## Skill Definition

The built-in skill is named `sequential-handoff`, uses version `1.0.0`, and has `risk: inspect`. Its description begins with `Use when...` and contains triggering conditions rather than the workflow itself.

The body remains concise and human-readable for `agentdeck skills show`. It explains the fixed-chain planning pattern, operator sequence, non-applicable cases, and common mistakes. It does not contain recitation-specific content.

Frontmatter includes compact guidance equivalent to these six rules:

1. Produce a fixed linear chain with consecutive step numbers.
2. Assign exactly one configured Agent per step and copy its configured role.
3. Make every later step explicitly consume the previous compact handoff.
4. State the expected deliverable, verification, and failure condition in each task.
5. Do not introduce parallel branches, DAG edges, cycles, repeats, or dynamic steps.
6. In the plan summary, recommend `agentdeck workflow preview --plan-id <id>` followed by human-confirmed `agentdeck workflow run --plan-id <id> --confirm`.

The skill itself does not add reply tokens or the structured Worker reply schema. The workflow engine owns those runtime details and injects them into each Worker prompt.

## Compact Planning Guidance Metadata

`SkillSnapshot` gains:

```python
planning_guidance: tuple[str, ...] = ()
```

The existing hand-rolled frontmatter parser reads `planning_guidance` through the same comma-separated `_metadata_list()` path used by `required_tools` and `depends_on`. Guidance entries therefore must not contain commas.

To keep provider context bounded, the compact projection uses at most eight entries and at most 240 characters per entry. It trims surrounding whitespace, drops empty entries, preserves order, and clips excess characters deterministically. The original skill content remains represented by its content hash and saved `content_snapshot`; neither is substituted for compact guidance in the Leader prompt.

Existing skills without the field produce `planning_guidance=[]` and remain behaviorally compatible.

## Data Flow

The field follows the existing audited skill path:

```text
skill frontmatter
→ SkillSnapshot.planning_guidance
→ skills list/show/load payloads
→ skill_loads[] record
→ ProjectView skills.items[]
→ plan.skill_context provenance
→ Leader provider compact prompt
```

The field must be added to the relevant contract field lists and validators. It must never cause discovery, preview, show, catalog, import, load, or workbench rendering to call a provider or inspect tmux.

## Leader-only Prompt Rule

All loaded skills may continue to appear as existing compact provenance. `planning_guidance` is included in the provider-facing compact item only when that load record has `agent_id=leader`.

Therefore:

- Explicit `skills load --name sequential-handoff --agent leader ...` makes the guidance available to Leader planning.
- Loading the same skill for a Worker does not inject its guidance into the Leader provider prompt.
- Nothing automatically loads the skill for either Leader or Worker.

This filtering applies to the new guidance field without removing or reinterpreting existing skill provenance.

## User Flow

```bash
agentdeck skills show --name sequential-handoff

agentdeck skills load-preview \
  --name sequential-handoff \
  --agent leader \
  --purpose "plan a fixed sequential handoff"

agentdeck skills load \
  --name sequential-handoff \
  --agent leader \
  --purpose "plan a fixed sequential handoff"

agentdeck leader plan --task "<goal>"
agentdeck workflow preview --plan-id <id>
agentdeck workflow run --plan-id <id> --confirm
```

The plan remains approval-gated and non-dispatch-ready. Commands mentioned in the Leader plan summary are recommendations, not executed controls or authorization tokens.

## Safety and Error Handling

- Skill discovery and preview remain read-only.
- Explicit load records source, hash, content snapshot, agent, purpose, and compact guidance.
- The provider prompt never receives the full content snapshot.
- `planning_guidance` grants no tool, runtime, dispatch, approval, or file permission.
- The existing provider plan schema continues to require configured agents, matching roles, consecutive steps, `requires_approval=true`, `approval_required=true`, and `dispatch_ready=false`.
- Workflow preflight and `--confirm` remain the only path from a compatible plan to sequential execution.
- Empty or missing guidance is represented as an empty list.
- Oversized guidance is bounded by the deterministic compact projection; discovery must not crash because an external project skill supplies extra text.
- If the goal requires parallelism, branching, or repetition, the skill must state that the fixed linear workflow is not applicable rather than forcing the goal into a lossy chain.

## Contracts and Documentation

Update:

- Skill item/detail/load response fields and examples in `src/agentdeck/contracts.py`.
- ProjectView skill item fields and validators.
- Leader prompt compact context tests.
- `docs/contracts/skills-schema.md`.
- `docs/contracts/project-view-schema.md` if the item field list is documented explicitly.
- `README.md`, `CLAUDE.md`, `AGENT.md`, `HISTORY.md`, and `docs/handoff/current-development-state.md`.

No new top-level CLI command or contract family is required. Existing `agentdeck contract skills --example`, `agentdeck contract project-view --example`, skill lifecycle commands, and plan provenance expose the additive field.

## Testing Strategy

### Skill RED-GREEN-REFACTOR

Before writing the skill, run a baseline planning scenario without `sequential-handoff`. Use a fresh agent with only the task-local facts and record whether it omits any of:

- fixed consecutive linear steps
- explicit upstream handoff consumption
- deliverable and verification expectations
- rejection of parallel/cyclic expansion
- workflow preview and confirmed-run operator guidance

After implementation, run the same scenario with the skill artifact supplied and verify compliance. Run a counterexample requiring parallel or cyclic work and verify the skill identifies itself as inapplicable. Forward tests must not modify live runtime or external systems.

### Deterministic Repository Tests

- Built-in list/show exposes `sequential-handoff`, version, hash, risk, and bounded guidance.
- Existing built-ins expose `planning_guidance=[]`.
- Explicit Leader load persists guidance in `skill_loads[]`, ProjectView, workbench context, and plan provenance.
- Provider prompt includes guidance for a Leader load but not for a Worker-only load.
- Provider prompt still excludes `content_snapshot` and full skill content.
- Skills and ProjectView example fixtures and validators include the additive field.
- Existing skill import/load/dependency behavior remains unchanged.
- Full pytest, compileall, contract smoke, and `git diff --check` pass in the `agentdeck` conda environment.

## Delivery Boundary

This slice ends after the compact metadata capability, built-in skill, deterministic tests, behavioral skill tests, documentation, and local commits are complete.

The next step is a separate real Codex/Claude acceptance run using the loaded skill and the already-delivered sequential workflow engine. The motivating 百家姓 exercise remains test content only and does not enter the built-in skill or core engine.
