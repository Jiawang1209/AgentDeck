# Skill dependency version pinning (content-hash) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `depends_on` entries may pin a content hash — `name@sha256:<hex>`. The resolver reports `version_mismatch` when a pinned dep is present but its hash differs; `skills deps` / `skills load-plan` / `skills load --with-deps` treat it as a hard blocker. Plain `name` = any version (unchanged).

**Architecture:** Pure `_parse_dep(entry)` + a `version_mismatch` list threaded through `resolve_skill_dependencies`; `_skill_load_plan` adds it to `blockers`. Deterministic, local, no network.

**Tech Stack:** Python 3.12 stdlib, argparse, pytest. Run all via `conda run -n agentdeck ...`.

**Design spec:** `docs/superpowers/specs/2026-07-09-skill-dep-version-pinning-design.md`

---

## File Structure
- Modify `src/agentdeck/skills.py` — `_parse_dep`, `resolve_skill_dependencies` (version check + `version_mismatch`).
- Modify `src/agentdeck/cli.py` — `skills_deps_command` payload (+ `version_mismatch`), `_skill_load_plan` (+ `version_mismatch` in blockers/payload).
- Modify `src/agentdeck/contracts.py` — add `version_mismatch` to `SKILLS_DEPS_RESPONSE_FIELDS` + `SKILL_LOAD_PLAN_RESPONSE_FIELDS` (and validators).
- Modify `docs/contracts/skills-schema.md`, `README.md`, `CLAUDE.md`, `HISTORY.md`, `docs/handoff/current-development-state.md`.
- Modify `tests/test_agent_cli.py`, plus the resolver-unit test module.

---

### Task 1: `_parse_dep` + version check in `resolve_skill_dependencies`

**Files:** Modify `src/agentdeck/skills.py`; Test (the resolver-unit test — wherever `test_resolve_skill_dependencies_*` lives)

- [ ] **Step 1: failing test**:

```python
def test_resolve_skill_dependencies_version_pinning(tmp_path):
    from pathlib import Path
    import hashlib
    from agentdeck.config import write_default_config
    from agentdeck.skills import resolve_skill_dependencies, _parse_dep

    assert _parse_dep("b@sha256:ab") == ("b", "sha256:ab")
    assert _parse_dep("b") == ("b", None)
    assert _parse_dep("b@") == ("b", None)

    root = tmp_path / "repo"; root.mkdir(); (root / ".git").mkdir(); write_default_config(root)
    sk = root / ".agentdeck" / "skills"
    def put(name, body, deps):
        d = sk / name; d.mkdir(parents=True)
        dep_line = f"depends_on: [{', '.join(deps)}]\n" if deps else ""
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name}\n{dep_line}---\n\n{body}", encoding="utf-8")

    put("b", "b-body\n", [])
    b_content = (sk / "b" / "SKILL.md").read_text(encoding="utf-8")
    b_hash = "sha256:" + hashlib.sha256(b_content.encode("utf-8")).hexdigest()

    put("a", "a\n", [f"b@{b_hash}"])           # correct pin -> resolved
    r = resolve_skill_dependencies(root, "a")
    assert r["resolved"] == ["b"] and r["version_mismatch"] == []

    put("c", "c\n", ["b@sha256:deadbeef"])      # wrong pin -> version_mismatch
    r2 = resolve_skill_dependencies(root, "c")
    assert r2["resolved"] == []
    assert [m["name"] for m in r2["version_mismatch"]] == ["b"]
    assert r2["version_mismatch"][0]["expected"] == "sha256:deadbeef"
    assert r2["version_mismatch"][0]["actual"] == b_hash
```

- [ ] **Step 2: run** → FAIL (`ImportError: _parse_dep` / `KeyError: 'version_mismatch'`).

- [ ] **Step 3: implement** in `src/agentdeck/skills.py`:

Add the pure parser:

```python
def _parse_dep(entry: str) -> tuple[str, str | None]:
    if "@" in entry:
        name, _, pin = entry.partition("@")
        if pin:
            return name, pin
    return entry, None
```

In `resolve_skill_dependencies`, add `version_mismatch: list[dict] = []` and `seen_vm: set[str] = set()`, add `"version_mismatch": version_mismatch` to the returned dict, and replace the dependency loop inside `visit`:

```python
        for entry in snapshots[node].depends_on:
            dep_name, pin = _parse_dep(entry)
            if dep_name in snapshots and pin is not None and snapshots[dep_name].content_hash != pin:
                if dep_name not in seen_vm:
                    seen_vm.add(dep_name)
                    version_mismatch.append({
                        "name": dep_name, "expected": pin,
                        "actual": snapshots[dep_name].content_hash,
                    })
                continue  # version mismatch is a blocker leaf; do not recurse
            if not visit(dep_name):
                stack.pop()
                return False
```

(The rest of `visit` — the `node not in snapshots -> missing` branch, cycle detection, post-order `order` — is unchanged.)

- [ ] **Step 4: run** → PASS.
- [ ] **Step 5: commit** `git commit -m "Add content-hash version pins to skill dependency resolution"`

---

### Task 2: Thread `version_mismatch` through skills deps / load-plan + contract + docs

**Files:** Modify `src/agentdeck/cli.py`, `src/agentdeck/contracts.py`, docs; Test `tests/test_agent_cli.py`

- [ ] **Step 1: failing test** — append to `tests/test_agent_cli.py` (reuse `prepare_project`, `StateStore`; write skills with a pinned dep):

```python
def test_skills_deps_and_load_plan_flag_version_mismatch(tmp_path, monkeypatch, capsys):
    import hashlib
    root = prepare_project(tmp_path, monkeypatch)
    sk = root / ".agentdeck" / "skills"
    (sk / "b").mkdir(parents=True)
    (sk / "b" / "SKILL.md").write_text("---\nname: b\ndescription: b\n---\nb-body\n", encoding="utf-8")
    (sk / "a").mkdir(parents=True)
    (sk / "a" / "SKILL.md").write_text("---\nname: a\ndescription: a\ndepends_on: [b@sha256:deadbeef]\n---\nx\n", encoding="utf-8")

    assert cli.main(["skills", "deps", "--name", "a"]) == 0
    deps = json.loads(capsys.readouterr().out)
    assert [m["name"] for m in deps["version_mismatch"]] == ["b"]

    assert cli.main(["skills", "load-plan", "--name", "a", "--agent", "planner"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["can_load"] is False
    assert [m["name"] for m in plan["version_mismatch"]] == ["b"]
    assert any("version mismatch" in blk.lower() for blk in plan["blockers"])

    # load --with-deps must reject a version mismatch, writing nothing
    before = StateStore(root).load()
    assert cli.main(["skills", "load", "--name", "a", "--agent", "planner", "--with-deps", "--confirm"]) == 1
    assert "version mismatch" in capsys.readouterr().err.lower()
    assert StateStore(root).load() == before
```

- [ ] **Step 2: run** → FAIL (`KeyError: 'version_mismatch'`).

- [ ] **Step 3: implement**

In `src/agentdeck/cli.py`:
- `skills_deps_command`: `resolve_skill_dependencies` now returns `version_mismatch`; since the command does `{"ok": True, "mode": "skills_deps", **resolution, "controls": ...}`, `version_mismatch` flows through automatically — just ensure the contract accepts it (Task adds the field). Confirm the spread includes it.
- `_skill_load_plan`: add `version_mismatch = resolution["version_mismatch"]` to the returned dict, and extend `blockers`:

```python
    blockers = [f"missing dependency: {dep}" for dep in resolution["missing"]]
    blockers += [f"version mismatch: {m['name']} expected {m['expected']}" for m in resolution["version_mismatch"]]
    if resolution["has_cycle"]:
        blockers.append("dependency cycle: " + " -> ".join(resolution["cycle"]))
```

and add `"version_mismatch": list(resolution["version_mismatch"]),` to the plan dict. (`can_load = not blockers and bool(to_load)` already blocks on a mismatch; `skills load --with-deps` already rejects when `not can_load` and prints the blockers — no change needed there.)

In `src/agentdeck/contracts.py`:
- Add `"version_mismatch"` to `SKILLS_DEPS_RESPONSE_FIELDS` and to `SKILL_LOAD_PLAN_RESPONSE_FIELDS`; in both validators, require `version_mismatch` present and a list.

- [ ] **Step 4: run** `conda run -n agentdeck pytest tests/test_agent_cli.py -k "skills_deps or load_plan or skills_load" tests/test_contracts.py -k skill -q` → PASS.

- [ ] **Step 5: docs**
- `docs/contracts/skills-schema.md`: document `name@sha256:<hex>` pins + the `version_mismatch` field/blocker.
- `CLAUDE.md`: extend the skills deps/load-plan/load rules — a `depends_on` entry may pin a content hash (`name@sha256:<hex>`); a present-but-mismatched dep is `version_mismatch`, a hard blocker for `skills deps`/`load-plan`/`load --with-deps` (load rejects, writes nothing); plain `name` = any version; deterministic/local/no-network.
- `README.md`: one line. `HISTORY.md`: newest-first top entry (skill ecosystem B-ver: content-hash dependency version pinning). `docs/handoff/current-development-state.md`: mark B-ver done; the remaining dep items — **semver ranges/intervals, lockfile generation, remote deps (C)** — are product forks; the loop must STOP here with a ⏸ note + Chinese recap.

- [ ] **Step 6: full verification**
- `conda run -n agentdeck pytest tests/test_agent_cli.py -k skills tests/test_contracts.py -k skill -q` → PASS
- `conda run -n agentdeck pytest -q` → all pass (baseline 732 + new)
- `conda run -n agentdeck python -m compileall src tests -q` → clean
- `git diff --check` → clean
- [ ] **Step 7: commit** `git commit -m "Flag skill dependency version mismatches in deps/load-plan/load"`

---

## Notes for the implementer
- Do NOT push. Commit locally only. No Claude co-author trailer. conda `agentdeck` env.
- Pins are content-hash equality — deterministic, local, NO network. A `version_mismatch` is a hard blocker exactly like `missing`/cycle (load writes nothing).
- Unpinned `depends_on [name]` behaviour is UNCHANGED (a test asserts prior behaviour stays green). Do not change `resolved`/`missing`/`order`/cycle semantics beyond adding the version check + `version_mismatch` list.
- Reuse `_parse_dep` everywhere a dep entry is interpreted; do not duplicate the split.
- After B-ver, the remaining items (semver ranges, lockfiles, remote deps) are product forks — do NOT build them; the loop STOPS here.
```
