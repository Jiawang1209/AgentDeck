# Skill dependency lockfile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `agentdeck skills lock --name <name>` freezes a skill's resolved dependency tree (name+hash+version) to `.agentdeck/skill-locks/<name>.json` (explicit write + `skill_locked` event; refuses an unresolvable tree). `agentdeck skills lock-verify --name <name>` reports drift read-only. Local, no network. Advisory (does not change resolution).

**Architecture:** Reuse `resolve_skill_dependencies` + `discover_skills`. `lock` writes the JSON + event; `lock-verify` re-resolves and diffs against the lockfile.

**Tech Stack:** Python 3.12 stdlib, argparse, pytest.

**Design spec:** `docs/superpowers/specs/2026-07-09-skill-dep-lockfile-design.md`

---

## File Structure
- Modify `src/agentdeck/cli.py` — `_skill_lock_record`, `skills_lock_command`, `skills_lock_verify_command`, subparsers.
- Modify `src/agentdeck/contracts.py` — `SKILL_LOCK_RESPONSE_FIELDS`, `SKILL_LOCK_VERIFY_RESPONSE_FIELDS`, validators, discovery.
- Modify docs (`skills-schema.md`, `CLAUDE.md`, `README.md`, `HISTORY.md`, handoff).
- Modify `tests/test_agent_cli.py`, `tests/test_contracts.py`.

---

### Task 1: `agentdeck skills lock` (generate, gated write)

**Files:** Modify `src/agentdeck/cli.py`, `src/agentdeck/contracts.py`; Test `tests/test_agent_cli.py`, `tests/test_contracts.py`

- [ ] **Step 1: failing test** — append to `tests/test_agent_cli.py` (reuse `prepare_project`, `StateStore`, and a helper writing `.agentdeck/skills/<name>/SKILL.md`):

```python
def _put_skill(root, name, deps=(), version="0.0.0"):
    d = root / ".agentdeck" / "skills" / name; d.mkdir(parents=True)
    dl = f"depends_on: [{', '.join(deps)}]\n" if deps else ""
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name}\nversion: {version}\n{dl}---\nbody\n", encoding="utf-8")


def test_skills_lock_writes_lockfile_for_resolvable_tree(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    _put_skill(root, "b", version="1.0.0"); _put_skill(root, "a", ["b"])
    assert cli.main(["skills", "lock", "--name", "a"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "skill_locked"
    lock = root / ".agentdeck" / "skill-locks" / "a.json"
    assert lock.exists()
    data = json.loads(lock.read_text(encoding="utf-8"))
    assert [d["name"] for d in data["dependencies"]] == ["b"]
    assert data["dependencies"][0]["version"] == "1.0.0"
    assert data["dependencies"][0]["content_hash"].startswith("sha256:")
    assert "skill_locked" in [e["event_type"] for e in StateStore(root).list_events(limit=10)]


def test_skills_lock_refuses_unresolvable_tree(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    _put_skill(root, "a", ["missingdep"])
    assert cli.main(["skills", "lock", "--name", "a"]) == 1
    assert "missingdep" in capsys.readouterr().err
    assert not (root / ".agentdeck" / "skill-locks" / "a.json").exists()
    assert "skill_locked" not in [e["event_type"] for e in StateStore(root).list_events(limit=10)]

    assert cli.main(["skills", "lock", "--name", "ghost"]) == 1
    assert "ghost" in capsys.readouterr().err.lower()
```

Append to `tests/test_contracts.py`:

```python
def test_validate_skill_lock_contract():
    from agentdeck.contracts import validate_skill_lock_contract
    good = {"ok": True, "mode": "skill_locked", "name": "a",
            "lock_path": ".agentdeck/skill-locks/a.json", "dependencies": []}
    assert validate_skill_lock_contract(good)["ok"]
    assert not validate_skill_lock_contract(dict(good, mode="x"))["ok"]
```

- [ ] **Step 2: run** `conda run -n agentdeck pytest tests/test_agent_cli.py -k skills_lock tests/test_contracts.py -k skill_lock -q` → FAIL (`invalid choice: 'lock'`).

- [ ] **Step 3: implement** — in `src/agentdeck/cli.py`, add `validate_skill_lock_contract` / `validate_skill_lock_verify_contract` to the contracts import. Add:

```python
def _skill_lock_path(config: ProjectConfig, name: str) -> Path:
    return Path(config.root) / ".agentdeck" / "skill-locks" / f"{name}.json"


def _skill_lock_record(config: ProjectConfig, name: str) -> tuple[dict[str, object] | None, list[str]]:
    root = Path(config.root)
    resolution = resolve_skill_dependencies(root, name)   # raises KeyError if unknown
    blockers: list[str] = [f"missing dependency: {m}" for m in resolution["missing"]]
    blockers += [f"version mismatch: {m['name']}" for m in resolution["version_mismatch"]]
    if resolution["has_cycle"]:
        blockers.append("dependency cycle: " + " -> ".join(resolution["cycle"]))
    if blockers:
        return None, blockers
    snapshots = {snap.name: snap for snap in discover_skills(root)}
    deps = [
        {"name": dep, "content_hash": snapshots[dep].content_hash, "version": snapshots[dep].version}
        for dep in resolution["order"] if dep != name
    ]
    return {"name": name, "locked_at": utc_now(), "dependencies": deps}, []


def skills_lock_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    try:
        record, blockers = _skill_lock_record(config, args.name)
    except KeyError:
        print(f"unknown skill: {args.name}", file=sys.stderr)
        return 1
    if record is None:
        print("cannot lock unresolvable tree: " + "; ".join(blockers), file=sys.stderr)
        return 1
    lock_path = _skill_lock_path(config, args.name)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    store.append_event(EventRecord.create("skill_locked", {"name": args.name, "dependency_count": len(record["dependencies"])}))
    payload = {"ok": True, "mode": "skill_locked", "name": args.name,
               "lock_path": str(lock_path), "dependencies": record["dependencies"]}
    validation = validate_skill_lock_contract(payload)
    if not validation["ok"]:
        print("skill lock contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1
    _print_json(payload)
    return 0
```

(`utc_now` is already imported/used in cli.py — confirm; if not, import it from the state/util module the rest of cli.py uses.)

Register the subparser (next to `skills_deps`):

```python
    skills_lock = skills_subparsers.add_parser("lock", help="Freeze a skill's resolved dependency tree to a lockfile")
    skills_lock.add_argument("--name", required=True, help="Skill name")
    skills_lock.set_defaults(func=skills_lock_command)
```

In `src/agentdeck/contracts.py`:

```python
SKILL_LOCK_RESPONSE_FIELDS = ("ok", "mode", "name", "lock_path", "dependencies")


def validate_skill_lock_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in SKILL_LOCK_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing skill_lock field: {field}")
    if payload.get("mode") != "skill_locked":
        errors.append("skill_lock.mode must be skill_locked")
    if not isinstance(payload.get("dependencies"), list):
        errors.append("skill_lock.dependencies must be a list")
    return {"ok": not errors, "errors": errors}
```

Expose `lock_command` + `skill_lock_response_fields` in the skills contract discovery payload.

- [ ] **Step 4: run** → PASS.
- [ ] **Step 5: commit** `git commit -m "Add agentdeck skills lock dependency lockfile generation"`

---

### Task 2: `agentdeck skills lock-verify` (read-only drift) + docs

**Files:** Modify `src/agentdeck/cli.py`, `src/agentdeck/contracts.py`, docs; Test `tests/test_agent_cli.py`

- [ ] **Step 1: failing test** — append to `tests/test_agent_cli.py`:

```python
def test_skills_lock_verify_reports_drift_read_only(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    _put_skill(root, "b", version="1.0.0"); _put_skill(root, "a", ["b"])

    # before locking
    assert cli.main(["skills", "lock-verify", "--name", "a"]) == 0
    assert json.loads(capsys.readouterr().out)["locked"] is False

    assert cli.main(["skills", "lock", "--name", "a"]) == 0
    capsys.readouterr()

    # in sync
    assert cli.main(["skills", "lock-verify", "--name", "a"]) == 0
    p = json.loads(capsys.readouterr().out)
    assert p["locked"] is True and p["in_sync"] is True
    assert p["changed"] == [] and p["added"] == [] and p["removed"] == []

    # change b's content -> drift
    (root / ".agentdeck" / "skills" / "b" / "SKILL.md").write_text("---\nname: b\ndescription: b\nversion: 2.0.0\n---\nCHANGED\n", encoding="utf-8")
    before = StateStore(root).load()
    assert cli.main(["skills", "lock-verify", "--name", "a"]) == 0
    p2 = json.loads(capsys.readouterr().out)
    assert p2["in_sync"] is False
    assert [c["name"] for c in p2["changed"]] == ["b"]
    assert StateStore(root).load() == before   # read-only
```

- [ ] **Step 2: run** → FAIL (`invalid choice: 'lock-verify'`).

- [ ] **Step 3: implement** — add:

```python
def skills_lock_verify_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    root = Path(config.root)
    try:
        resolve_skill_dependencies(root, args.name)
    except KeyError:
        print(f"unknown skill: {args.name}", file=sys.stderr)
        return 1
    lock_path = _skill_lock_path(config, args.name)
    if not lock_path.exists():
        payload = {"ok": True, "mode": "skill_lock_verify", "name": args.name, "locked": False,
                   "in_sync": False, "changed": [], "added": [], "removed": [], "blockers": [],
                   "hint": f"agentdeck skills lock --name {args.name}"}
        _validate_and_print_lock_verify(payload)
        return 0
    locked = json.loads(lock_path.read_text(encoding="utf-8"))
    locked_map = {d["name"]: d for d in locked.get("dependencies", [])}
    record, blockers = _skill_lock_record(config, args.name)
    current_map = {d["name"]: d for d in (record["dependencies"] if record else [])}
    changed = [
        {"name": name, "locked": locked_map[name], "current": current_map[name]}
        for name in sorted(set(locked_map) & set(current_map))
        if locked_map[name]["content_hash"] != current_map[name]["content_hash"]
        or locked_map[name]["version"] != current_map[name]["version"]
    ]
    added = sorted(set(current_map) - set(locked_map))
    removed = sorted(set(locked_map) - set(current_map))
    in_sync = not changed and not added and not removed and not blockers
    payload = {"ok": True, "mode": "skill_lock_verify", "name": args.name, "locked": True,
               "in_sync": in_sync, "changed": changed, "added": added, "removed": removed, "blockers": blockers}
    _validate_and_print_lock_verify(payload)
    return 0


def _validate_and_print_lock_verify(payload: dict[str, object]) -> None:
    validation = validate_skill_lock_verify_contract(payload)
    if not validation["ok"]:
        print("skill lock-verify contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit(1)
    _print_json(payload)
```

Register the subparser (next to `skills_lock`):

```python
    skills_lock_verify = skills_subparsers.add_parser("lock-verify", help="Read-only drift check of a skill's dependency lockfile")
    skills_lock_verify.add_argument("--name", required=True, help="Skill name")
    skills_lock_verify.set_defaults(func=skills_lock_verify_command)
```

In `src/agentdeck/contracts.py`:

```python
SKILL_LOCK_VERIFY_RESPONSE_FIELDS = (
    "ok", "mode", "name", "locked", "in_sync", "changed", "added", "removed", "blockers",
)


def validate_skill_lock_verify_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in SKILL_LOCK_VERIFY_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing skill_lock_verify field: {field}")
    if payload.get("mode") != "skill_lock_verify":
        errors.append("skill_lock_verify.mode must be skill_lock_verify")
    for bool_field in ("locked", "in_sync"):
        if not isinstance(payload.get(bool_field), bool):
            errors.append(f"skill_lock_verify.{bool_field} must be a bool")
    for list_field in ("changed", "added", "removed", "blockers"):
        if not isinstance(payload.get(list_field), list):
            errors.append(f"skill_lock_verify.{list_field} must be a list")
    return {"ok": not errors, "errors": errors}
```

Expose `lock_verify_command` + `skill_lock_verify_response_fields` in discovery.

- [ ] **Step 4: run** `conda run -n agentdeck pytest tests/test_agent_cli.py -k "skills_lock" tests/test_contracts.py -k skill_lock -q` → PASS.

- [ ] **Step 5: docs**
- `docs/contracts/skills-schema.md`: document `skills lock` (explicit write, refuses unresolvable) + `skills lock-verify` (read-only drift: changed/added/removed/in_sync) + the lockfile location.
- `CLAUDE.md`: add the rule — `skills lock --name <name>` writes `.agentdeck/skill-locks/<name>.json` + `skill_locked` event, refuses to lock an unresolvable tree; `skills lock-verify --name <name>` is read-only drift detection (advisory — does NOT change how `deps`/`load` resolve); local, no network.
- `README.md`: one line. `HISTORY.md`: newest-first top entry (skill ecosystem: dependency lockfile generate + verify). `docs/handoff/current-development-state.md`: mark lockfile done; **STOP** — the next item is **remote/C** (network / signing / offline / registry) which needs a dedicated human design decision; write the ⏸ note there.

- [ ] **Step 6: full verification**
- `conda run -n agentdeck pytest -q` → all pass (baseline 738 + new)
- `conda run -n agentdeck python -m compileall src tests -q` → clean
- `git diff --check` → clean
- [ ] **Step 7: commit** `git commit -m "Add agentdeck skills lock-verify read-only dependency drift check"`

---

## Notes for the implementer
- Do NOT push. Commit locally only. No Claude co-author trailer. conda `agentdeck` env.
- `skills lock` is an explicit write (lockfile + `skill_locked` event) and REFUSES to lock a tree with missing/cycle/version_mismatch. `skills lock-verify` is fully READ-ONLY (a test asserts state unchanged; it also must not modify the lockfile).
- The lockfile is advisory drift-detection this slice — do NOT make `deps`/`load` resolution use the lock (that is a later slice).
- Reuse `resolve_skill_dependencies` + `discover_skills`; `.agentdeck/skill-locks/` is a dedicated dir (not under `.agentdeck/skills/`, so `discover_skills` won't pick it up).
- After lockfile, the next item is **remote/C** — do NOT build it. The loop STOPS here and asks the human (network/signing/supply-chain design).
```
