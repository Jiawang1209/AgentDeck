# Skill dependency semver ranges Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Skills declare `version: X.Y.Z`; `depends_on` entries support semver ranges (`name@>=1.2`, `name@^1.0.0`, `name@>=1.2,<2.0`). An unsatisfied/unparseable range on a present dep → `version_mismatch` (reuses B-ver's blocker plumbing). Hash pins (`@sha256:`) and plain `name` unchanged. Stdlib only, local, no network.

**Architecture:** Pure `parse_version` + `version_satisfies` in `skills.py`; the resolver classifies a dep spec as `sha256:` (hash, B-ver) vs semver range, and populates the existing `version_mismatch` list.

**Tech Stack:** Python 3.12 stdlib, argparse, pytest.

**Design spec:** `docs/superpowers/specs/2026-07-09-skill-dep-semver-design.md`

---

## File Structure
- Modify `src/agentdeck/skills.py` — `version` field + parse, `parse_version`, `version_satisfies`, resolver semver branch.
- Modify `src/agentdeck/contracts.py` — `version` in skill-summary fields if needed; note semver in discovery.
- Modify docs (`skills-schema.md`, `CLAUDE.md`, `README.md`, `HISTORY.md`, handoff).
- Modify the skills-resolver unit test module, `tests/test_agent_cli.py`, `tests/test_contracts.py`.

---

### Task 1: `version` frontmatter + pure semver comparator

**Files:** Modify `src/agentdeck/skills.py`; Test the skills-unit test module

- [ ] **Step 1: failing test**:

```python
def test_semver_parse_and_satisfies():
    from agentdeck.skills import parse_version, version_satisfies
    assert parse_version("1.2.3") == (1, 2, 3)
    assert parse_version("1.2") == (1, 2, 0)
    assert parse_version("1") == (1, 0, 0)
    assert parse_version("x") is None
    assert parse_version("1.2.3.4") is None

    assert version_satisfies("1.2.0", ">=1.2.0")
    assert version_satisfies("2.0.0", ">=1.2.0")
    assert not version_satisfies("1.1.9", ">=1.2.0")
    assert version_satisfies("1.9.9", "<2.0.0")
    assert not version_satisfies("2.0.0", "<2.0.0")
    assert version_satisfies("1.9.9", "^1.2.0")
    assert not version_satisfies("2.0.0", "^1.2.0")
    assert not version_satisfies("1.1.0", "^1.2.0")
    assert version_satisfies("1.5.0", ">=1.2,<2.0")     # comma-AND
    assert not version_satisfies("2.0.0", ">=1.2,<2.0")
    assert version_satisfies("1.2.3", "1.2.3")          # bare = exact
    assert version_satisfies("1.2.3", "==1.2.3")
    assert not version_satisfies("1.2.4", "1.2.3")
    assert not version_satisfies("1.0.0", "garbage")     # unparseable -> False
    assert not version_satisfies("bad", ">=1.0.0")       # bad version -> False


def test_skill_snapshot_parses_version(tmp_path):
    from pathlib import Path
    from agentdeck.config import write_default_config
    from agentdeck.skills import discover_skills
    root = tmp_path / "r"; root.mkdir(); (root / ".git").mkdir(); write_default_config(root)
    d = root / ".agentdeck" / "skills" / "v"; d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: v\ndescription: v\nversion: 2.3.4\n---\nx\n", encoding="utf-8")
    v = next(s for s in discover_skills(root) if s.name == "v")
    assert v.version == "2.3.4"
    d2 = root / ".agentdeck" / "skills" / "novers"; d2.mkdir(parents=True)
    (d2 / "SKILL.md").write_text("---\nname: novers\ndescription: n\n---\nx\n", encoding="utf-8")
    nv = next(s for s in discover_skills(root) if s.name == "novers")
    assert nv.version == "0.0.0"
```

- [ ] **Step 2: run** → FAIL (`ImportError` / `AttributeError: version`).

- [ ] **Step 3: implement** in `src/agentdeck/skills.py`:

Add `version: str = "0.0.0"` to `SkillSnapshot` (after `content`/`depends_on`, with default). In `_snapshot_from_content`, pass `version=str(metadata.get("version") or "0.0.0")`. Add `"version": self.version` to `SkillSnapshot.summary()`.

Add the comparator:

```python
def parse_version(text: str) -> tuple[int, int, int] | None:
    parts = str(text).strip().split(".")
    if not 1 <= len(parts) <= 3:
        return None
    nums: list[int] = []
    for part in parts:
        if not part.isdigit():
            return None
        nums.append(int(part))
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def _comparator_holds(version: tuple[int, int, int], comparator: str) -> bool:
    comparator = comparator.strip()
    if comparator.startswith("^"):
        base = parse_version(comparator[1:])
        if base is None:
            return False
        return base <= version < (base[0] + 1, 0, 0)
    for op in (">=", "<=", "==", ">", "<"):
        if comparator.startswith(op):
            operand = parse_version(comparator[len(op):])
            if operand is None:
                return False
            if op == ">=":
                return version >= operand
            if op == "<=":
                return version <= operand
            if op == "==":
                return version == operand
            if op == ">":
                return version > operand
            return version < operand
    operand = parse_version(comparator)      # bare version = exact
    return operand is not None and version == operand


def version_satisfies(version: str, spec: str) -> bool:
    parsed = parse_version(version)
    if parsed is None:
        return False
    comparators = [c for c in str(spec).split(",") if c.strip()]
    if not comparators:
        return False
    return all(_comparator_holds(parsed, comparator) for comparator in comparators)
```

- [ ] **Step 4: run** → PASS.
- [ ] **Step 5: commit** `git commit -m "Add skill version frontmatter and pure semver comparator"`

---

### Task 2: semver ranges in the resolver + docs

**Files:** Modify `src/agentdeck/skills.py`, `src/agentdeck/contracts.py` (if summary validators need `version`), docs; Test skills-unit + `tests/test_agent_cli.py`

- [ ] **Step 1: failing test** (skills-unit module):

```python
def test_resolve_skill_dependencies_semver_ranges(tmp_path):
    from pathlib import Path
    from agentdeck.config import write_default_config
    from agentdeck.skills import resolve_skill_dependencies
    root = tmp_path / "r"; root.mkdir(); (root / ".git").mkdir(); write_default_config(root)
    sk = root / ".agentdeck" / "skills"
    def put(name, version, deps):
        d = sk / name; d.mkdir(parents=True)
        dl = f"depends_on: [{', '.join(deps)}]\n" if deps else ""
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name}\nversion: {version}\n{dl}---\nx\n", encoding="utf-8")

    put("b", "1.5.0", [])
    put("ok", "1.0.0", ["b@>=1.2,<2.0"])
    r = resolve_skill_dependencies(root, "ok")
    assert r["resolved"] == ["b"] and r["version_mismatch"] == []

    put("bad", "1.0.0", ["b@>=2.0"])
    r2 = resolve_skill_dependencies(root, "bad")
    assert [m["name"] for m in r2["version_mismatch"]] == ["b"]
    assert r2["version_mismatch"][0]["expected"] == ">=2.0"
    assert r2["version_mismatch"][0]["actual"] == "1.5.0"

    put("anyv", "1.0.0", ["b"])            # plain name = any
    assert resolve_skill_dependencies(root, "anyv")["resolved"] == ["b"]
```

Also append a CLI test to `tests/test_agent_cli.py`:

```python
def test_skills_load_plan_blocks_on_semver_range(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    sk = root / ".agentdeck" / "skills"
    (sk / "b").mkdir(parents=True); (sk / "b" / "SKILL.md").write_text("---\nname: b\ndescription: b\nversion: 1.0.0\n---\nx\n", encoding="utf-8")
    (sk / "a").mkdir(parents=True); (sk / "a" / "SKILL.md").write_text("---\nname: a\ndescription: a\ndepends_on: [b@>=2.0]\n---\nx\n", encoding="utf-8")
    assert cli.main(["skills", "load-plan", "--name", "a", "--agent", "planner"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["can_load"] is False
    assert [m["name"] for m in plan["version_mismatch"]] == ["b"]
```

- [ ] **Step 2: run** → FAIL (`>=2.0` treated as content hash / no mismatch).

- [ ] **Step 3: implement** — in `resolve_skill_dependencies`, replace the B-ver version-check branch so it classifies the spec:

```python
        for entry in snapshots[node].depends_on:
            dep_name, spec = _parse_dep(entry)
            if dep_name in snapshots and spec is not None:
                dep_snap = snapshots[dep_name]
                if spec.startswith("sha256:"):
                    satisfied = dep_snap.content_hash == spec
                    actual = dep_snap.content_hash
                    reason = "content hash mismatch"
                else:
                    satisfied = version_satisfies(dep_snap.version, spec)
                    actual = dep_snap.version
                    reason = "version range not satisfied"
                if not satisfied:
                    if dep_name not in seen_vm:
                        seen_vm.add(dep_name)
                        version_mismatch.append({"name": dep_name, "expected": spec, "actual": actual, "reason": reason})
                    continue
            if not visit(dep_name):
                stack.pop()
                return False
```

(`version_mismatch` already flows through `skills deps` / `_skill_load_plan` blockers / `load --with-deps` from B-ver — the `reason` key is additive.)

If adding `version` to `SkillSnapshot.summary()` breaks any skill-summary validator that checks an exact field set, add `version` to that field list (e.g. `SKILLS_LIST` item fields / project-view skill item fields) — do NOT drop it from summary.

- [ ] **Step 4: run** `conda run -n agentdeck pytest tests/test_agent_cli.py -k "skills" tests/test_contracts.py -k skill -q` and the skills-unit module → PASS.

- [ ] **Step 5: docs**
- `docs/contracts/skills-schema.md`: document `version` frontmatter + the semver range syntax + supported subset (exact / `>= > <= <` / `^` / comma-AND; unsupported → blocker).
- `CLAUDE.md`: extend the deps rules — a `depends_on` entry may be a content-hash pin (`name@sha256:`), a semver range (`name@>=1.2,<2.0` etc., matched against the dep's `version` frontmatter, supported subset above), or plain `name` (any); unsatisfied/unparseable = `version_mismatch` hard blocker; stdlib-only, local, no network.
- `README.md`: one line. `HISTORY.md`: newest-first top entry (skill ecosystem: semver dependency ranges). `docs/handoff/current-development-state.md`: mark semver done; next is **lockfile generation** (its own spec); **remote/C** stays a STOP-and-ask fork.

- [ ] **Step 6: full verification**
- `conda run -n agentdeck pytest -q` → all pass (baseline 734 + new)
- `conda run -n agentdeck python -m compileall src tests -q` → clean
- `git diff --check` → clean
- [ ] **Step 7: commit** `git commit -m "Support semver dependency ranges via version frontmatter"`

---

## Notes for the implementer
- Do NOT push. Commit locally only. No Claude co-author trailer. conda `agentdeck` env.
- Stdlib only — do NOT add a semver library. The comparator is the risky part; the plan's `version_satisfies` code is exact — implement it as given and keep the thorough Task 1 tests.
- Reuse B-ver's `version_mismatch` plumbing; the semver branch just populates it. Hash pins (`sha256:`) and plain names must stay unchanged (existing B-ver / B-auto tests green).
- Adding `version` to skills defaults to `0.0.0` (backward compatible). If it touches skill-summary validators, extend the field lists (don't drop the field).
- After semver: **lockfile** (its own spec); then **remote/C** — STOP + ask (network/signing/supply-chain), do NOT build it in the loop.
```
