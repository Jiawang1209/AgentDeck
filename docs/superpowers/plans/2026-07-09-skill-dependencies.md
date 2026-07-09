# Skill dependencies — read-only resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parse a `depends_on` frontmatter list onto skills and add read-only `agentdeck skills deps --name <name>` — transitive resolution among discovered skills with `missing`, `has_cycle` (+ cycle path), and a topological `order`. Loads/imports/writes nothing.

**Architecture:** Additive `SkillSnapshot.depends_on` (parsed via `_metadata_list`, `summary()` unchanged) + a pure `resolve_skill_dependencies(root, name)` DFS over `discover_skills(root)`. Read-only command wraps it, contract-validated.

**Tech Stack:** Python 3.12 stdlib, argparse, pytest. Run all via `conda run -n agentdeck ...`.

**Design spec:** `docs/superpowers/specs/2026-07-09-skill-dependencies-design.md`

---

## File Structure
- Modify `src/agentdeck/skills.py` — `SkillSnapshot.depends_on`, parse in `_snapshot_from_content`, `resolve_skill_dependencies`.
- Modify `src/agentdeck/cli.py` — `skills_deps_command` + `deps` subparser + import.
- Modify `src/agentdeck/contracts.py` — `SKILLS_DEPS_RESPONSE_FIELDS`, `validate_skills_deps_contract`, discovery.
- Modify `docs/contracts/skills-schema.md`, `README.md`, `CLAUDE.md`, `HISTORY.md`, `docs/handoff/current-development-state.md`.
- Modify `tests/test_skills.py` (or wherever skills-unit tests live; else `tests/test_agent_cli.py`), `tests/test_agent_cli.py`, `tests/test_contracts.py`.

---

### Task 1: `depends_on` + pure `resolve_skill_dependencies`

**Files:** Modify `src/agentdeck/skills.py`; Test (a skills-unit test module; if none, put these in `tests/test_agent_cli.py`)

- [ ] **Step 1: failing test**:

```python
def test_resolve_skill_dependencies_transitive_missing_and_cycle(tmp_path):
    from pathlib import Path
    from agentdeck.config import write_default_config
    from agentdeck.skills import resolve_skill_dependencies

    root = tmp_path / "repo"; root.mkdir(); (root / ".git").mkdir()
    write_default_config(root)
    sk = root / ".agentdeck" / "skills"

    def put(name, deps):
        d = sk / name; d.mkdir(parents=True)
        dep_line = f"depends_on: [{', '.join(deps)}]\n" if deps else ""
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name}\n{dep_line}---\n\nbody\n", encoding="utf-8")

    put("a", ["b"]); put("b", ["c"]); put("c", [])
    r = resolve_skill_dependencies(root, "a")
    assert r["depends_on"] == ["b"]
    assert r["resolved"] == ["b", "c"]        # sorted transitive deps
    assert r["missing"] == []
    assert r["has_cycle"] is False
    assert r["order"] == ["c", "b", "a"]       # deps before dependents

    put("x", ["y"])                            # y is missing
    r2 = resolve_skill_dependencies(root, "x")
    assert r2["missing"] == ["y"]
    assert r2["resolved"] == []
    assert r2["has_cycle"] is False

    # cycle p -> q -> p
    put("p", ["q"]); put("q", ["p"])
    r3 = resolve_skill_dependencies(root, "p")
    assert r3["has_cycle"] is True
    assert "p" in r3["cycle"] and "q" in r3["cycle"]

    import pytest
    with pytest.raises(KeyError):
        resolve_skill_dependencies(root, "nope")
```

- [ ] **Step 2: run** → FAIL (`AttributeError: depends_on` / `ImportError`).

- [ ] **Step 3: implement** in `src/agentdeck/skills.py`:

Add `depends_on: tuple[str, ...] = ()` to the `SkillSnapshot` dataclass (after `content`; it needs a default so existing constructions stay valid). In `_snapshot_from_content`, pass `depends_on=tuple(_metadata_list(metadata.get("depends_on")))`. Do NOT change `summary()`.

Add the pure resolver:

```python
def resolve_skill_dependencies(root: Path, name: str) -> dict[str, object]:
    snapshots = {snap.name: snap for snap in discover_skills(root)}
    if name not in snapshots:
        raise KeyError(name)
    missing: list[str] = []
    seen_missing: set[str] = set()
    order: list[str] = []          # post-order topo: deps before dependents
    state: dict[str, int] = {}     # 0 = visiting, 1 = done
    stack: list[str] = []
    cycle: list[str] = []

    def visit(node: str) -> bool:  # returns False if a cycle was hit
        if node not in snapshots:
            if node not in seen_missing:
                seen_missing.add(node)
                missing.append(node)
            return True
        if state.get(node) == 1:
            return True
        if state.get(node) == 0:   # back-edge -> cycle
            cycle[:] = stack[stack.index(node):] + [node]
            return False
        state[node] = 0
        stack.append(node)
        for dep in snapshots[node].depends_on:
            if not visit(dep):
                stack.pop()
                return False
        stack.pop()
        state[node] = 1
        order.append(node)
        return True

    has_cycle = not visit(name)
    topo = order if not has_cycle else []
    resolved = sorted(node for node in topo if node != name)
    return {
        "name": name,
        "depends_on": list(snapshots[name].depends_on),
        "resolved": resolved,
        "missing": missing,
        "has_cycle": has_cycle,
        "cycle": list(cycle),
        "order": topo,
    }
```

- [ ] **Step 4: run** → PASS.
- [ ] **Step 5: commit** `git commit -m "Parse skill depends_on and add pure dependency resolver"`

---

### Task 2: `agentdeck skills deps` command + contract

**Files:** Modify `src/agentdeck/cli.py`, `src/agentdeck/contracts.py`; Test `tests/test_agent_cli.py`, `tests/test_contracts.py`

- [ ] **Step 1: failing test** — append to `tests/test_agent_cli.py`:

```python
def _put_project_skill(root, name, deps=()):
    d = root / ".agentdeck" / "skills" / name; d.mkdir(parents=True)
    dep_line = f"depends_on: [{', '.join(deps)}]\n" if deps else ""
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name}\n{dep_line}---\n\nbody\n", encoding="utf-8")


def test_skills_deps_reports_resolution_read_only(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    _put_project_skill(root, "a", ["b"]); _put_project_skill(root, "b", [])
    before = StateStore(root).load()

    assert cli.main(["skills", "deps", "--name", "a"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "skills_deps"
    assert payload["name"] == "a"
    assert payload["resolved"] == ["b"]
    assert payload["missing"] == []
    assert payload["has_cycle"] is False
    assert StateStore(root).load() == before  # read-only

    assert cli.main(["skills", "deps", "--name", "ghost"]) == 1
    assert "ghost" in capsys.readouterr().err.lower()


def test_skills_deps_flags_missing_and_cycle(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    _put_project_skill(root, "x", ["y"])   # y missing
    _put_project_skill(root, "p", ["q"]); _put_project_skill(root, "q", ["p"])  # cycle

    assert cli.main(["skills", "deps", "--name", "x"]) == 0
    assert json.loads(capsys.readouterr().out)["missing"] == ["y"]
    assert cli.main(["skills", "deps", "--name", "p"]) == 0
    assert json.loads(capsys.readouterr().out)["has_cycle"] is True
```

- [ ] **Step 2: run** → FAIL (`invalid choice: 'deps'`).

- [ ] **Step 3: implement** — in `src/agentdeck/cli.py`, add `resolve_skill_dependencies` to the `from .skills import (...)` block and `validate_skills_deps_contract` to the contracts import. Add the command near `skills_list_command`:

```python
def skills_deps_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    try:
        resolution = resolve_skill_dependencies(Path(config.root), args.name)
    except KeyError:
        print(f"unknown skill: {args.name}", file=sys.stderr)
        return 1
    controls = [
        _control(kind="show", label=f"Show {dep}", command=f"agentdeck skills show --name {dep}", safety="inspect")
        for dep in list(resolution["resolved"]) + list(resolution["missing"])
    ]
    payload = {"ok": True, "mode": "skills_deps", **resolution, "controls": controls}
    validation = validate_skills_deps_contract(payload)
    if not validation["ok"]:
        print("skills deps contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1
    _print_json(payload)
    return 0
```

Register the subparser (next to `skills_list`, cli.py ~13325):

```python
    skills_deps = skills_subparsers.add_parser("deps", help="Read-only dependency resolution for a skill")
    skills_deps.add_argument("--name", required=True, help="Skill name")
    skills_deps.set_defaults(func=skills_deps_command)
```

In `src/agentdeck/contracts.py`, add near the other `SKILLS_*_RESPONSE_FIELDS`:

```python
SKILLS_DEPS_RESPONSE_FIELDS = (
    "ok", "mode", "name", "depends_on", "resolved", "missing",
    "has_cycle", "cycle", "order", "controls",
)


def validate_skills_deps_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in SKILLS_DEPS_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing skills_deps field: {field}")
    if payload.get("mode") != "skills_deps":
        errors.append("skills_deps.mode must be skills_deps")
    if not isinstance(payload.get("has_cycle"), bool):
        errors.append("skills_deps.has_cycle must be a bool")
    for list_field in ("depends_on", "resolved", "missing", "cycle", "order", "controls"):
        if not isinstance(payload.get(list_field), list):
            errors.append(f"skills_deps.{list_field} must be a list")
    return {"ok": not errors, "errors": errors}
```

Expose in the skills contract discovery payload (grep `def skills_contract_payload`): add `"deps_command": "agentdeck skills deps --name <name>"` and `"deps_response_fields": list(SKILLS_DEPS_RESPONSE_FIELDS)`.

- [ ] **Step 4: run** `conda run -n agentdeck pytest tests/test_agent_cli.py -k skills_deps tests/test_contracts.py -k skills -q` → PASS.

- [ ] **Step 5: docs**
- `docs/contracts/skills-schema.md`: document `skills deps` + `depends_on` frontmatter + read-only resolution (resolved/missing/has_cycle/cycle/order).
- `CLAUDE.md`: add the rule (read-only `agentdeck skills deps --name <name>` resolves `depends_on` among discovered skills, reports missing/cycle/topo order; loads/imports/writes nothing; NO auto-load/auto-import of deps).
- `README.md`: one line. `HISTORY.md`: newest-first top entry (skill ecosystem B slice 1: read-only skill dependency resolution). `docs/handoff/current-development-state.md`: mark B slice 1 done; note the next B slices (surface unmet deps read-only in `load-preview`), then the ⚠️ forks (auto-load/import deps, version constraints, remote deps) = STOP + ask.

- [ ] **Step 6: full verification**
- `conda run -n agentdeck pytest tests/test_agent_cli.py -k skills tests/test_contracts.py -k skills -q` → PASS
- `conda run -n agentdeck pytest -q` → all pass (baseline 722 + new)
- `conda run -n agentdeck python -m compileall src tests -q` → clean
- `git diff --check` → clean
- [ ] **Step 7: commit** `git commit -m "Add agentdeck skills deps read-only dependency resolution"`

---

## Notes for the implementer
- Do NOT push. Commit locally only. No Claude co-author trailer. conda `agentdeck` env.
- Read-only slice: `skills deps` loads/imports/writes nothing. A test asserts state is unchanged. `depends_on` is parsed but NOT acted on — no auto-load, no auto-import.
- Do NOT change `SkillSnapshot.summary()` in this slice (avoids touching every skill-summary validator). `skills deps` reads `snapshot.depends_on` directly.
- Reuse `_metadata_list` and `discover_skills`; do not reimplement.
- After this slice: a later B slice may add a read-only "unmet deps" note to `load-preview`; **auto-loading/importing deps, versions, and remote deps are product forks** — do NOT build them; stop + ask.
```
