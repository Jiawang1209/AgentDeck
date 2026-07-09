# Skill dependency auto-load (preview + confirm) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `agentdeck skills load-plan --name <name> --agent <id>` (read-only dependency load plan) + `agentdeck skills load --name <name> --agent <id> --with-deps --confirm` (load the dependency chain, deps-first, gated; blocks on missing dep / cycle). Single-skill `skills load` (no `--with-deps`) is unchanged.

**Architecture:** `_skill_load_plan(config, store, name, agent)` reuses `resolve_skill_dependencies` + the agent's `skill_loads`. `load-plan` wraps it read-only; `load --with-deps --confirm` executes it via the existing `store.record_skill_load` + `skill_loaded` event per skill, then a `skill_deps_loaded` summary.

**Tech Stack:** Python 3.12 stdlib, argparse, pytest. Run all via `conda run -n agentdeck ...`.

**Design spec:** `docs/superpowers/specs/2026-07-09-skill-dep-autoload-design.md`

---

## File Structure
- Modify `src/agentdeck/cli.py` — `_skill_load_plan`, `skills_load_plan_command`, `_skills_load_with_deps` + branch in `skills_load_command`, subparsers.
- Modify `src/agentdeck/contracts.py` — `SKILL_LOAD_PLAN_RESPONSE_FIELDS`, `validate_skill_load_plan_contract`, discovery.
- Modify `docs/contracts/skills-schema.md`, `README.md`, `CLAUDE.md`, `HISTORY.md`, `docs/handoff/current-development-state.md`.
- Modify `tests/test_agent_cli.py`, `tests/test_contracts.py`.

---

### Task 1: `_skill_load_plan` + read-only `skills load-plan`

**Files:** Modify `src/agentdeck/cli.py`, `src/agentdeck/contracts.py`; Test `tests/test_agent_cli.py`, `tests/test_contracts.py`

- [ ] **Step 1: failing test** — append to `tests/test_agent_cli.py` (reuse `_put_project_skill` from the deps slice; `prepare_project`, `StateStore`):

```python
def test_skills_load_plan_previews_deps_read_only(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    _put_project_skill(root, "a", ["b"]); _put_project_skill(root, "b", [])
    before = StateStore(root).load()

    assert cli.main(["skills", "load-plan", "--name", "a", "--agent", "planner"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "skill_load_plan"
    assert payload["to_load"] == ["b", "a"]          # deps-first topo order
    assert payload["can_load"] is True
    assert payload["blockers"] == []
    assert payload["confirm_command"] == "agentdeck skills load --name a --agent planner --with-deps --confirm"
    assert StateStore(root).load() == before          # read-only


def test_skills_load_plan_blocks_on_missing_and_cycle(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    _put_project_skill(root, "a", ["b", "z"]); _put_project_skill(root, "b", [])   # z missing
    _put_project_skill(root, "p", ["q"]); _put_project_skill(root, "q", ["p"])     # cycle

    assert cli.main(["skills", "load-plan", "--name", "a", "--agent", "planner"]) == 0
    p = json.loads(capsys.readouterr().out)
    assert p["missing"] == ["z"] and p["can_load"] is False
    assert any("z" in b for b in p["blockers"])

    assert cli.main(["skills", "load-plan", "--name", "p", "--agent", "planner"]) == 0
    p2 = json.loads(capsys.readouterr().out)
    assert p2["has_cycle"] is True and p2["can_load"] is False
```

Append to `tests/test_contracts.py`:

```python
def test_validate_skill_load_plan_contract():
    from agentdeck.contracts import validate_skill_load_plan_contract
    good = {"ok": True, "mode": "skill_load_plan", "name": "a", "agent": "planner",
            "order": [], "to_load": [], "already_loaded": [], "missing": [],
            "has_cycle": False, "cycle": [], "blockers": [], "can_load": False,
            "confirm_command": "agentdeck skills load --name a --agent planner --with-deps --confirm",
            "controls": []}
    assert validate_skill_load_plan_contract(good)["ok"]
    bad = dict(good); bad["mode"] = "x"
    assert not validate_skill_load_plan_contract(bad)["ok"]
```

- [ ] **Step 2: run** `conda run -n agentdeck pytest tests/test_agent_cli.py -k skills_load_plan tests/test_contracts.py -k skill_load_plan -q` → FAIL.

- [ ] **Step 3: implement** — in `src/agentdeck/cli.py`, add `resolve_skill_dependencies` to the `from .skills import (...)` block (if not already; the deps slice added it) and `validate_skill_load_plan_contract` to the contracts import. Add:

```python
def _skill_load_plan(config: ProjectConfig, store: StateStore, name: str, agent: str) -> dict[str, object]:
    root = Path(config.root)
    resolution = resolve_skill_dependencies(root, name)   # raises KeyError if name unknown
    snapshots = {snap.name: snap for snap in discover_skills(root)}
    loaded_names = {
        record.get("name")
        for record in store.load().get("skill_loads", [])
        if isinstance(record, dict) and record.get("agent_id") == agent
    }
    order_items: list[dict[str, object]] = []
    to_load: list[str] = []
    already_loaded: list[str] = []
    for node in resolution["order"]:                       # empty when cyclic; deps-first when acyclic
        if node in loaded_names:
            status = "already_loaded"; already_loaded.append(node)
        else:
            status = "to_load"; to_load.append(node)
        order_items.append({"name": node, "status": status,
                            "source": snapshots[node].source if node in snapshots else None})
    blockers = [f"missing dependency: {dep}" for dep in resolution["missing"]]
    if resolution["has_cycle"]:
        blockers.append("dependency cycle: " + " -> ".join(resolution["cycle"]))
    can_load = not blockers and bool(to_load)
    return {
        "name": name, "agent": agent,
        "order": order_items, "to_load": to_load, "already_loaded": already_loaded,
        "missing": list(resolution["missing"]),
        "has_cycle": bool(resolution["has_cycle"]), "cycle": list(resolution["cycle"]),
        "blockers": blockers, "can_load": can_load,
        "confirm_command": f"agentdeck skills load --name {name} --agent {agent} --with-deps --confirm",
    }


def skills_load_plan_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    if not _is_known_mailbox_agent(config, args.agent):
        print(f"unknown agent: {args.agent}", file=sys.stderr)
        return 1
    try:
        plan = _skill_load_plan(config, store, args.name, args.agent)
    except KeyError:
        print(f"unknown skill: {args.name}", file=sys.stderr)
        return 1
    controls = [
        _control(kind="show", label=f"Show {item['name']}", command=f"agentdeck skills show --name {item['name']}", safety="inspect")
        for item in plan["order"]
    ]
    payload = {"ok": True, "mode": "skill_load_plan", **plan, "controls": controls}
    validation = validate_skill_load_plan_contract(payload)
    if not validation["ok"]:
        print("skill load plan contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1
    _print_json(payload)
    return 0
```

Register the subparser (next to `skills_load`):

```python
    skills_load_plan = skills_subparsers.add_parser("load-plan", help="Read-only preview of a skill's dependency load plan")
    skills_load_plan.add_argument("--name", required=True, help="Skill name")
    skills_load_plan.add_argument("--agent", default="leader", help="Agent id; defaults to leader")
    skills_load_plan.set_defaults(func=skills_load_plan_command)
```

In `src/agentdeck/contracts.py` add near the skills fields:

```python
SKILL_LOAD_PLAN_RESPONSE_FIELDS = (
    "ok", "mode", "name", "agent", "order", "to_load", "already_loaded",
    "missing", "has_cycle", "cycle", "blockers", "can_load", "confirm_command", "controls",
)


def validate_skill_load_plan_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in SKILL_LOAD_PLAN_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing skill_load_plan field: {field}")
    if payload.get("mode") != "skill_load_plan":
        errors.append("skill_load_plan.mode must be skill_load_plan")
    if not isinstance(payload.get("can_load"), bool):
        errors.append("skill_load_plan.can_load must be a bool")
    if not isinstance(payload.get("has_cycle"), bool):
        errors.append("skill_load_plan.has_cycle must be a bool")
    for list_field in ("order", "to_load", "already_loaded", "missing", "cycle", "blockers", "controls"):
        if not isinstance(payload.get(list_field), list):
            errors.append(f"skill_load_plan.{list_field} must be a list")
    return {"ok": not errors, "errors": errors}
```

Expose `load_plan_command` + `skill_load_plan_response_fields` in the skills contract discovery payload (grep `def skills_contract_payload`).

- [ ] **Step 4: run** → PASS.
- [ ] **Step 5: commit** `git commit -m "Add read-only skills load-plan dependency preview"`

---

### Task 2: `skills load --with-deps --confirm` (gated execute) + docs

**Files:** Modify `src/agentdeck/cli.py`, docs; Test `tests/test_agent_cli.py`

- [ ] **Step 1: failing test** — append to `tests/test_agent_cli.py`:

```python
def test_skills_load_with_deps_loads_chain_gated(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    _put_project_skill(root, "a", ["b"]); _put_project_skill(root, "b", [])

    # requires --confirm
    assert cli.main(["skills", "load", "--name", "a", "--agent", "planner", "--with-deps"]) == 1
    assert "confirm" in capsys.readouterr().err

    # confirm -> loads b then a
    assert cli.main(["skills", "load", "--name", "a", "--agent", "planner", "--with-deps", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "skill_deps_loaded"
    assert [x["name"] for x in payload["loaded"]] == ["b", "a"]
    types = [e["event_type"] for e in StateStore(root).list_events(limit=30)]
    assert types.count("skill_loaded") == 2 and "skill_deps_loaded" in types

    # second run skips already-loaded
    capsys.readouterr()
    assert cli.main(["skills", "load", "--name", "a", "--agent", "planner", "--with-deps", "--confirm"]) == 1
    assert "nothing to load" in capsys.readouterr().err.lower() or "cannot load" in capsys.readouterr().err.lower()


def test_skills_load_with_deps_rejects_missing_dep(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    _put_project_skill(root, "a", ["z"])   # z missing
    before = StateStore(root).load()
    assert cli.main(["skills", "load", "--name", "a", "--agent", "planner", "--with-deps", "--confirm"]) == 1
    assert "z" in capsys.readouterr().err
    assert StateStore(root).load() == before   # nothing written


def test_skills_load_single_unchanged(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    _put_project_skill(root, "solo", [])
    assert cli.main(["skills", "load", "--name", "solo", "--agent", "planner"]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "skill_loaded"   # existing single-skill path
```

- [ ] **Step 2: run** → FAIL (`--with-deps` unknown).

- [ ] **Step 3: implement** — add `--with-deps` / `--confirm` to the `skills load` subparser:

```python
    skills_load.add_argument("--with-deps", action="store_true", help="Also load the skill's dependency chain (requires --confirm)")
    skills_load.add_argument("--confirm", action="store_true", help="Explicitly confirm loading the dependency chain")
```

In `skills_load_command`, right after the `_load_project_or_error` guard and BEFORE the existing single-skill body, branch:

```python
    if getattr(args, "with_deps", False):
        return _skills_load_with_deps(config, store, args)
```

Add the executor (leave the existing single-skill body untouched):

```python
def _skills_load_with_deps(config: ProjectConfig, store: StateStore, args: argparse.Namespace) -> int:
    if not _is_known_mailbox_agent(config, args.agent):
        print(f"unknown agent: {args.agent}", file=sys.stderr)
        return 1
    if not args.confirm:
        print("skills load --with-deps requires --confirm", file=sys.stderr)
        return 1
    try:
        plan = _skill_load_plan(config, store, args.name, args.agent)
    except KeyError:
        print(f"unknown skill: {args.name}", file=sys.stderr)
        return 1
    if not plan["can_load"]:
        reason = "; ".join(plan["blockers"]) if plan["blockers"] else "nothing to load (all dependencies already loaded)"
        print(f"cannot load with deps: {reason}", file=sys.stderr)
        return 1
    root = Path(config.root)
    loaded: list[dict[str, object]] = []
    for node in plan["to_load"]:
        skill = find_skill(root, node)
        payload = skill.load_payload()
        record = store.record_skill_load(agent_id=args.agent, purpose=args.purpose, skill=payload)
        store.append_event(EventRecord.create("skill_loaded", {
            "load_id": record["load_id"], "agent_id": args.agent, "name": skill.name,
            "source": skill.source, "content_hash": skill.content_hash}))
        loaded.append({"name": skill.name, "load_id": record["load_id"], "source": skill.source})
    store.append_event(EventRecord.create("skill_deps_loaded", {
        "agent_id": args.agent, "name": args.name, "loaded": [item["name"] for item in loaded]}))
    _print_json({
        "ok": True, "mode": "skill_deps_loaded", "agent_id": args.agent, "name": args.name,
        "purpose": args.purpose, "loaded": loaded, "skipped_already_loaded": plan["already_loaded"], "plan": plan,
    })
    return 0
```

- [ ] **Step 4: run** `conda run -n agentdeck pytest tests/test_agent_cli.py -k "skills_load" -q` → PASS (new + existing single-skill load tests).

- [ ] **Step 5: docs**
- `docs/contracts/skills-schema.md`: document `skills load-plan` and `skills load --with-deps --confirm`.
- `CLAUDE.md`: add the rule — `skills load-plan` is read-only preview; `skills load --with-deps --confirm` loads the dependency chain deps-first, requires `--confirm`, blocks (writes nothing) on a missing dep or cycle, never auto-imports, audits each `skill_loaded` + a `skill_deps_loaded` summary; single-skill `skills load` unchanged.
- `README.md`: one line. `HISTORY.md`: newest-first top entry (skill ecosystem B-auto: dependency auto-load, preview + confirm). `docs/handoff/current-development-state.md`: mark B-auto done; next is **B-ver** (version constraints/lockfiles — its own brainstorm/spec); C (remote) still deferred.

- [ ] **Step 6: full verification**
- `conda run -n agentdeck pytest tests/test_agent_cli.py -k skills tests/test_contracts.py -k skill -q` → PASS
- `conda run -n agentdeck pytest -q` → all pass (baseline 726 + new)
- `conda run -n agentdeck python -m compileall src tests -q` → clean
- `git diff --check` → clean
- [ ] **Step 7: commit** `git commit -m "Add gated skills load --with-deps dependency chain loading"`

---

## Notes for the implementer
- Do NOT push. Commit locally only. No Claude co-author trailer. conda `agentdeck` env.
- Loading deps is EXPLICIT: `load-plan` previews (read-only, no writes); `load --with-deps` requires `--confirm` and blocks (writes nothing) on missing dep / cycle. Never silent, never auto-import.
- Single-skill `skills load` (no `--with-deps`) must stay byte-for-byte unchanged — a test asserts its `mode=skill_loaded` output. Branch at the top; leave the existing body alone.
- Reuse `resolve_skill_dependencies`, `find_skill`, `store.record_skill_load` — do not reimplement.
- After B-auto, the next lane item is **B-ver** (versions) — a separate brainstorm/spec; do NOT start it here.
```
