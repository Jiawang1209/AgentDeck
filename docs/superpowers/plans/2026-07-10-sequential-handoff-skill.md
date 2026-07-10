# Sequential Handoff Skill Implementation and Acceptance Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add audited compact Leader planning guidance, ship the built-in `sequential-handoff` skill, and prove it in a real Codex/Claude eight-turn 百家姓 handoff run.

**Architecture:** Extend the existing `SkillSnapshot` → skill load → ProjectView → plan provenance → provider prompt pipeline with bounded `planning_guidance[]`. Only Leader-owned loads expose guidance to provider prompts; the full content snapshot remains excluded. After deterministic and behavioral tests, use a temporary AgentDeck project with Codex and Claude panes to run and validate a fixed alternating chain.

**Tech Stack:** Python 3.12 stdlib, AgentDeck Skill Registry and JSON state, pytest, Codex CLI, Claude Code CLI, tmux, Markdown validation report.

---

## File Structure

- Modify `src/agentdeck/skills.py`: bounded guidance normalization, `SkillSnapshot` field, and built-in skill.
- Modify `src/agentdeck/state.py`: persist and project compact guidance.
- Modify `src/agentdeck/providers/base.py`: include Leader-only guidance in compact provider prompt items.
- Modify `src/agentdeck/contracts.py`: additive skills and ProjectView field contracts/examples.
- Modify `tests/test_agent_cli.py`: discovery/load/ProjectView compatibility tests.
- Modify `tests/test_leader_cli.py`: Leader-only provider prompt tests.
- Modify `tests/test_contracts.py`: contract field/example drift tests where exact lists require it.
- Modify `docs/contracts/skills-schema.md` and `docs/contracts/project-view-schema.md`: public field and safety contract.
- Modify `README.md`, `CLAUDE.md`, `AGENT.md`, `HISTORY.md`, and `docs/handoff/current-development-state.md`: user flow, governance, delivery status, and next acceptance state.
- Create `docs/validation/2026-07-10-sequential-handoff-skill-evaluation.md`: RED/GREEN/counterexample behavioral evidence.
- Create `docs/validation/2026-07-10-codex-claude-baijiaxing-handoff.md`: real acceptance commands, ids, transcript, and validation result.

### Task 1: Record the skill behavior baseline (RED)

**Files:**
- Create after the baseline: `docs/validation/2026-07-10-sequential-handoff-skill-evaluation.md`

- [ ] **Step 1: Run a fresh-agent baseline without the skill**

Give a fresh subagent only this prompt:

```text
You are the AgentDeck Leader planner. Design a plan for two configured workers, planner(role=planning) and reviewer(role=review), to alternate eight turns of a knowledge recitation. Worker N+1 may start only after Worker N finishes. Return a concise JSON-like plan summary and operator next steps. Do not assume any special skill exists.
```

The evaluation matrix is:

```text
consecutive_fixed_steps
one_configured_agent_per_step
later_step_consumes_compact_handoff
deliverable_verification_failure_condition_per_step
rejects_parallel_dag_cycle_dynamic_expansion
recommends_workflow_preview_then_confirmed_run
```

- [ ] **Step 2: Save the exact baseline response and scored matrix**

Create the validation document with sections `Baseline prompt`, `Baseline raw response`, and `Baseline score`. Mark each matrix item `pass` or `fail` and quote the evidence. Do not create the skill yet.

- [ ] **Step 3: Commit the RED evidence**

```bash
git add docs/validation/2026-07-10-sequential-handoff-skill-evaluation.md
git commit -m "Record sequential handoff skill baseline"
```

### Task 2: Add bounded planning guidance metadata

**Files:**
- Modify `src/agentdeck/skills.py`
- Modify `tests/test_agent_cli.py`

- [ ] **Step 1: Write failing normalization and compatibility tests**

Add tests that construct a project skill containing ten guidance entries, including one longer than 240 characters, and assert:

```python
skill = find_skill(root, "guided-planning")
assert skill is not None
assert len(skill.planning_guidance) == 8
assert all(len(item) <= 240 for item in skill.planning_guidance)
assert skill.summary()["planning_guidance"] == list(skill.planning_guidance)
assert find_skill(root, "planning").summary()["planning_guidance"] == []
```

- [ ] **Step 2: Run RED**

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py -k planning_guidance -q
```

Expected: fail because `SkillSnapshot` and summaries do not expose `planning_guidance`.

- [ ] **Step 3: Add the minimal bounded field**

Add constants and normalization:

```python
MAX_PLANNING_GUIDANCE_ITEMS = 8
MAX_PLANNING_GUIDANCE_CHARS = 240


def _planning_guidance(value: object) -> tuple[str, ...]:
    return tuple(
        item[:MAX_PLANNING_GUIDANCE_CHARS]
        for item in _metadata_list(value)[:MAX_PLANNING_GUIDANCE_ITEMS]
    )
```

Add `planning_guidance: tuple[str, ...] = ()` to `SkillSnapshot`, parse it in `_snapshot_from_content()`, and expose `"planning_guidance": list(self.planning_guidance)` from `summary()`.

- [ ] **Step 4: Run GREEN**

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py -k planning_guidance -q
```

- [ ] **Step 5: Commit metadata support**

```bash
git add src/agentdeck/skills.py tests/test_agent_cli.py
git commit -m "Add compact skill planning guidance"
```

### Task 3: Persist guidance and make provider injection Leader-only

**Files:**
- Modify `src/agentdeck/state.py`
- Modify `src/agentdeck/providers/base.py`
- Modify `tests/test_agent_cli.py`
- Modify `tests/test_leader_cli.py`

- [ ] **Step 1: Write failing load/provenance/prompt tests**

The load test must assert `planning_guidance` appears in the `skill_loads[]` record and ProjectView item. The provider test must create two compact items:

```python
items = [
    {"agent_id": "leader", "name": "leader-guide", "planning_guidance": ["leader rule"]},
    {"agent_id": "planner", "name": "worker-guide", "planning_guidance": ["worker rule"]},
]
```

For both API and CLI provider prompts assert:

```python
assert '"planning_guidance": ["leader rule"]' in prompt
assert "worker rule" not in prompt
assert "content_snapshot" not in prompt
```

- [ ] **Step 2: Run RED**

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py tests/test_leader_cli.py -k planning_guidance -q
```

Expected: fail because state and provider compact projection drop the new field.

- [ ] **Step 3: Persist/project the field**

Add this field to `record_skill_load()`, `_skill_load_summaries()`, and `_plan_skill_context()`:

```python
"planning_guidance": list(raw.get("planning_guidance") or []),
```

Use the appropriate local variable (`skill`, `load`, or `raw_item`) at each call site.

- [ ] **Step 4: Add Leader-only provider projection**

In `leader_skill_context_prompt_lines()`, add:

```python
"planning_guidance": (
    item.get("planning_guidance")
    if item.get("agent_id") == "leader" and isinstance(item.get("planning_guidance"), list)
    else []
),
```

Keep the existing metadata and permission warnings unchanged.

- [ ] **Step 5: Run GREEN and regressions**

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py tests/test_leader_cli.py -k "planning_guidance or loaded_skill" -q
```

- [ ] **Step 6: Commit the audited data path**

```bash
git add src/agentdeck/state.py src/agentdeck/providers/base.py tests/test_agent_cli.py tests/test_leader_cli.py
git commit -m "Pass Leader skill guidance to planning providers"
```

### Task 4: Add the built-in sequential-handoff skill

**Files:**
- Modify `src/agentdeck/skills.py`
- Modify `tests/test_agent_cli.py`

- [ ] **Step 1: Write the failing built-in discovery test**

Assert `agentdeck skills show --name sequential-handoff` returns:

```python
assert skill["name"] == "sequential-handoff"
assert skill["source"] == "builtin"
assert skill["version"] == "1.0.0"
assert skill["risk"] == "inspect"
assert len(skill["planning_guidance"]) == 7
assert "百家姓" not in skill["content"]
assert "agentdeck workflow preview" in skill["content"]
assert "agentdeck workflow run" in skill["content"]
```

- [ ] **Step 2: Run RED**

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py -k sequential_handoff_builtin -q
```

Expected: fail with `unknown skill: sequential-handoff`.

- [ ] **Step 3: Add the minimal built-in content**

Add a `BUILTIN_SKILLS["sequential-handoff"]` entry with:

```yaml
name: sequential-handoff
description: Use when a fixed ordered worker chain must advance only after each upstream result is validated.
version: 1.0.0
planning_guidance: Produce a fixed linear chain with consecutive step numbers, Assign exactly one configured Agent per step and copy its configured role, Make every later step explicitly consume the previous compact handoff, State the expected deliverable and verification and failure condition in each task, Do not introduce parallel branches or DAG edges or cycles or repeats or dynamic steps, In the plan summary recommend agentdeck workflow preview then human-confirmed agentdeck workflow run --confirm, For incompatible workloads do not recommend workflow commands and return to ordinary Leader planning for a dedicated design
required_tools: leader-plan, workflow-preview, workflow-run
risk: inspect
```

The body must contain Overview, Core Pattern, Operator Handoff, Not Applicable, Quick Reference, and Common Mistakes sections in fewer than 500 words.

- [ ] **Step 4: Run GREEN**

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py -k "sequential_handoff_builtin or planning_guidance" -q
```

- [ ] **Step 5: Commit the built-in skill**

```bash
git add src/agentdeck/skills.py tests/test_agent_cli.py
git commit -m "Add sequential handoff built-in skill"
```

### Task 5: Synchronize contracts and public documentation

**Files:**
- Modify `src/agentdeck/contracts.py`
- Modify `tests/test_contracts.py`
- Modify `docs/contracts/skills-schema.md`
- Modify `docs/contracts/project-view-schema.md`
- Modify `README.md`
- Modify `CLAUDE.md`
- Modify `AGENT.md`
- Modify `HISTORY.md`
- Modify `docs/handoff/current-development-state.md`

- [ ] **Step 1: Write failing exact-field/example tests**

Require `planning_guidance` in `SKILLS_SKILL_ITEM_FIELDS`, `PROJECT_VIEW_SKILL_ITEM_FIELDS`, and every example item governed by those field lists. Validators must reject an item where the field is absent or not a list of strings.

- [ ] **Step 2: Run RED**

```bash
conda run -n agentdeck pytest tests/test_contracts.py -k "skill or project_view" -q
```

- [ ] **Step 3: Update constants, examples, and validators**

Insert `planning_guidance` after `required_tools` in both item field constants. Add `"planning_guidance": []` to legacy examples and the six built-in rules to the sequential-handoff example where present. Extend validators with list-of-string checks.

- [ ] **Step 4: Update docs and governance**

Document the explicit Leader load flow, eight-item/240-character bound, Leader-only prompt inclusion, full-snapshot exclusion, non-authorization rule, fixed-linear-only scope, and deferred real acceptance. Update HISTORY and handoff in the same commit.

- [ ] **Step 5: Run contract and skill regressions**

```bash
conda run -n agentdeck pytest tests/test_contracts.py tests/test_agent_cli.py tests/test_leader_cli.py -k "skill or planning_guidance or project_view" -q
```

- [ ] **Step 6: Commit contract/docs slice**

```bash
git add src/agentdeck/contracts.py tests/test_contracts.py docs/contracts/skills-schema.md docs/contracts/project-view-schema.md README.md CLAUDE.md AGENT.md HISTORY.md docs/handoff/current-development-state.md
git commit -m "Document sequential handoff planning guidance"
```

### Task 6: Forward-test the completed skill (GREEN and counterexample)

**Files:**
- Modify `docs/validation/2026-07-10-sequential-handoff-skill-evaluation.md`

- [ ] **Step 1: Run the same planning scenario with the skill artifact**

Give a fresh subagent this prompt and the exact built-in skill content:

```text
Use the supplied sequential-handoff skill to design a plan for two configured workers, planner(role=planning) and reviewer(role=review), to alternate eight turns of a knowledge recitation. Worker N+1 may start only after Worker N finishes. Return a concise JSON-like plan summary and operator next steps.
```

- [ ] **Step 2: Run the counterexample**

```text
Use the supplied sequential-handoff skill for a workload that requires three independent searches to run in parallel and then merge repeatedly until convergence. Decide whether this skill applies and explain the operator path.
```

Expected: explicitly reject the fixed linear skill for parallel/cyclic semantics and recommend ordinary planning or a separately designed workflow.

- [ ] **Step 3: Append raw responses and scored matrices**

Add `With-skill raw response`, `With-skill score`, `Counterexample raw response`, and `Counterexample score`. The GREEN scenario must pass all six criteria; the counterexample must not force the workload into a linear chain.

- [ ] **Step 4: Commit evaluation evidence**

```bash
git add docs/validation/2026-07-10-sequential-handoff-skill-evaluation.md
git commit -m "Validate sequential handoff skill behavior"
```

### Task 7: Verify the implementation before live acceptance

- [ ] **Step 1: Run focused verification**

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py tests/test_leader_cli.py tests/test_contracts.py -k "sequential_handoff or planning_guidance or skill" -q
```

- [ ] **Step 2: Run full verification**

```bash
conda run -n agentdeck pytest -q
conda run -n agentdeck python -m compileall src tests -q
conda run -n agentdeck agentdeck contract skills --example
conda run -n agentdeck agentdeck contract project-view --example
git diff --check
```

- [ ] **Step 3: Audit scope**

Confirm `.omc/`, untracked `AGENTS.md`, `.agentdeck/`, credential files, and temporary live-test projects are not staged.

### Task 8: Run the real Codex/Claude alternating acceptance

**Files:**
- Create: `docs/validation/2026-07-10-codex-claude-baijiaxing-handoff.md`

- [ ] **Step 1: Create an isolated temporary git project**

Use `mktemp -d`, `git init`, `agentdeck project init`, and existing CLI configuration commands. Configure the Leader as `codex-cli` model `gpt-5.5`. Use `planner` as the Codex Worker and `reviewer` as the Claude Worker. Do not store credentials or modify the main repo runtime state.

- [ ] **Step 2: Spawn only the two Worker panes**

```bash
agentdeck agent spawn --agent planner
agentdeck agent spawn --agent reviewer
agentdeck agent refresh
```

Verify both bindings are running before planning.

- [ ] **Step 3: Explicitly load the skill for Leader and create the plan**

```bash
agentdeck skills load --name sequential-handoff --agent leader --purpose "plan an eight-turn alternating Baijiaxing handoff"
agentdeck leader plan --task "Create exactly eight fixed sequential steps alternating planner then reviewer. Each step outputs only the next four surnames of the opening Baijiaxing sequence in summary, consumes the prior compact handoff, states verification and failure conditions, and the plan summary recommends workflow preview then confirmed run."
```

Verify the plan has exactly eight consecutive steps and agent ids alternate `planner, reviewer` four times.

- [ ] **Step 4: Preview and run with one confirmation**

```bash
agentdeck workflow preview --plan-id <actual-plan-id> --timeout 180
agentdeck workflow run --plan-id <actual-plan-id> --timeout 180 --confirm
```

Allow the foreground command to poll. If it stops, inspect `workflow status`, the active pane, and trace evidence; use `workflow resume --confirm` only when the frozen plan and token remain valid.

- [ ] **Step 5: Extract and machine-check the transcript**

Read the completed workflow record and compare the eight turn summaries, after removing whitespace and punctuation, with:

```python
EXPECTED = [
    "赵钱孙李",
    "周吴郑王",
    "冯陈褚卫",
    "蒋沈韩杨",
    "朱秦尤许",
    "何吕施张",
    "孔曹严华",
    "金魏陶姜",
]
```

Also assert eight turns completed, agent ids alternate, each turn has one reply id, and every handoff token is unique.

- [ ] **Step 6: Save the reproducible report and clean runtime resources**

Record CLI versions, model ids, temp-project path (marked ephemeral), plan id, run id, exact commands, transcript, validation output, stop/resume events, and cleanup result. Kill spawned panes/session and remove the temporary directory only after evidence is captured.

- [ ] **Step 7: Commit the acceptance report**

```bash
git add docs/validation/2026-07-10-codex-claude-baijiaxing-handoff.md HISTORY.md docs/handoff/current-development-state.md
git commit -m "Validate Codex Claude sequential handoff demo"
```

### Task 9: Final handoff for user review

- [ ] **Step 1: Re-run relevant tests and inspect git state**

```bash
conda run -n agentdeck pytest tests/test_workflow.py tests/test_agent_cli.py tests/test_leader_cli.py tests/test_contracts.py -q
git status --short --untracked-files=all
git log --oneline -10
```

- [ ] **Step 2: Report the exact review surfaces**

Return links to the skill design, implementation plan, skill evaluation, live acceptance report, `src/agentdeck/skills.py`, and contract docs. Include actual test counts and commits. Do not push.
