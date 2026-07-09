# Skill allowlist enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `agentdeck skills import` refuses to import a `SKILL.md` from outside the configured `[skills] allowed_sources` (opt-in: only when the allowlist is non-empty), unless `--allow-unlisted` is passed. `skills import-preview` surfaces the allowlist status read-only.

**Architecture:** Reuse `_source_is_allowlisted(source_dir, config)`. Enforcement is a pre-check in `skills_import_command`; the escape hatch and audit fields are additive. Preview gains read-only status fields.

**Tech Stack:** Python 3.12 stdlib, argparse, pytest. Run all via `conda run -n agentdeck ...`.

**Design spec:** `docs/superpowers/specs/2026-07-09-skill-allowlist-enforcement-design.md`

---

## File Structure
- Modify `src/agentdeck/cli.py` — enforcement in `skills_import_command`, `--allow-unlisted` subparser arg, status fields in `skills_import_preview_command`.
- Modify `src/agentdeck/contracts.py` — import-preview fields + discovery.
- Modify `docs/contracts/skills-schema.md`, `CLAUDE.md`, `README.md`, `HISTORY.md`, `docs/handoff/current-development-state.md`.
- Modify `tests/test_agent_cli.py`, `tests/test_contracts.py`.

---

### Task 1: Enforce the allowlist on `skills import` (opt-in) + `--allow-unlisted`

**Files:** Modify `src/agentdeck/cli.py`; Test `tests/test_agent_cli.py`

- [ ] **Step 1: failing tests** — append to `tests/test_agent_cli.py` (reuse `prepare_project`, `StateStore`; `_write_catalog_skill` from the catalog slice writes `<dir>/<name>/SKILL.md` and returns the SKILL.md path):

```python
def _set_allowed_sources(root, *dirs):
    cfg = root / ".agentdeck" / "config.toml"
    listing = ", ".join(f'"{d}"' for d in dirs)
    cfg.write_text(cfg.read_text(encoding="utf-8") + f"\n[skills]\nallowed_sources = [{listing}]\n", encoding="utf-8")


def test_skills_import_allowed_when_no_allowlist_configured(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    src = _write_catalog_skill(tmp_path / "src", "alpha", "A")
    assert cli.main(["skills", "import", "--path", str(src)]) == 0  # empty allowlist -> backward compatible
    events = [e for e in StateStore(root).list_events(limit=20) if e["event_type"] == "skill_imported"]
    assert events and events[-1]["payload"]["allowlisted"] is False
    assert events[-1]["payload"]["allow_unlisted"] is False


def test_skills_import_blocks_unlisted_source_when_allowlist_set(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    trusted = tmp_path / "trusted"
    other = tmp_path / "other"
    _write_catalog_skill(trusted, "good", "G")
    other_src = _write_catalog_skill(other, "bad", "B")
    _set_allowed_sources(root, trusted)
    before = StateStore(root).load()

    # unlisted source -> rejected, nothing written
    assert cli.main(["skills", "import", "--path", str(other_src)]) == 1
    err = capsys.readouterr().err
    assert "allowlist" in err.lower() and "--allow-unlisted" in err
    assert StateStore(root).load() == before
    assert not (root / ".agentdeck" / "skills" / "bad").exists()

    # allowlisted source -> succeeds
    good_src = trusted / "good" / "SKILL.md"
    assert cli.main(["skills", "import", "--path", str(good_src)]) == 0
    ev = [e for e in StateStore(root).list_events(limit=20) if e["event_type"] == "skill_imported"][-1]
    assert ev["payload"]["allowlisted"] is True

    # escape hatch imports the unlisted one, audited
    capsys.readouterr()
    assert cli.main(["skills", "import", "--path", str(other_src), "--allow-unlisted"]) == 0
    ev2 = [e for e in StateStore(root).list_events(limit=20) if e["event_type"] == "skill_imported"][-1]
    assert ev2["payload"]["allow_unlisted"] is True
```

- [ ] **Step 2: run** `conda run -n agentdeck pytest tests/test_agent_cli.py -k "skills_import_allowed_when or skills_import_blocks" -q` → FAIL (`--allow-unlisted` unknown / no enforcement / event missing fields).

- [ ] **Step 3: implement** — in `src/agentdeck/cli.py`, add the `--allow-unlisted` arg to the `skills import` subparser (next to `--force`):

```python
    skills_import.add_argument("--allow-unlisted", action="store_true", help="Import even if the source is not in [skills] allowed_sources")
```

In `skills_import_command`, BEFORE the `import_project_skill(...)` call, add the opt-in gate (reuse `_source_is_allowlisted`; read the raw list via `config.skills.get("allowed_sources", [])`):

```python
    source_path = Path(args.path)
    allowed_sources = config.skills.get("allowed_sources", []) if isinstance(config.skills, dict) else []
    source_allowlisted = _source_is_allowlisted(source_path.parent, config)
    if allowed_sources and not source_allowlisted and not args.allow_unlisted:
        print(
            f"skill source is not in the trusted allowlist: {source_path.parent}; "
            "add its directory to [skills] allowed_sources, or rerun with --allow-unlisted",
            file=sys.stderr,
        )
        return 1
    allow_unlisted_used = bool(allowed_sources and not source_allowlisted and args.allow_unlisted)
```

Then add `"allowlisted": source_allowlisted, "allow_unlisted": allow_unlisted_used,` to the `skill_imported` event payload dict.

(Confirm `config.skills` is the parsed `[skills]` dict — grep how `_source_is_allowlisted` reads `allowed_sources`; use the exact same accessor. If `_source_is_allowlisted` only checks path equality and not "under", extend it to `resolved.is_relative_to(allowed_resolved)` for any allowed source so a `<root>/<name>/SKILL.md`'s parent `<root>/<name>` counts as under `<root>`.)

- [ ] **Step 4: run** the focused tests → PASS.
- [ ] **Step 5: commit** `git commit -m "Enforce skill source allowlist on import with --allow-unlisted escape hatch"`

---

### Task 2: `skills import-preview` surfaces allowlist status + contract + docs

**Files:** Modify `src/agentdeck/cli.py`, `src/agentdeck/contracts.py`, docs; Test `tests/test_agent_cli.py`, `tests/test_contracts.py`

- [ ] **Step 1: failing test** — append to `tests/test_agent_cli.py`:

```python
def test_skills_import_preview_surfaces_allowlist_status(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    trusted = tmp_path / "trusted"; other = tmp_path / "other"
    good = _write_catalog_skill(trusted, "good", "G")
    bad = _write_catalog_skill(other, "bad", "B")
    _set_allowed_sources(root, trusted)

    assert cli.main(["skills", "import-preview", "--path", str(bad)]) == 0
    p = json.loads(capsys.readouterr().out)
    assert p["source_allowlisted"] is False
    assert p["enforcement_active"] is True
    assert p["import_blocked"] is True

    assert cli.main(["skills", "import-preview", "--path", str(good)]) == 0
    p2 = json.loads(capsys.readouterr().out)
    assert p2["source_allowlisted"] is True
    assert p2["import_blocked"] is False
```

- [ ] **Step 2: run** → FAIL (`KeyError`).

- [ ] **Step 3: implement** — in `skills_import_preview_command`, before printing, compute and add to the payload:

```python
    source_path = Path(args.path)
    allowed_sources = config.skills.get("allowed_sources", []) if isinstance(config.skills, dict) else []
    source_allowlisted = _source_is_allowlisted(source_path.parent, config)
    enforcement_active = bool(allowed_sources)
    # ... add to the printed dict:
    #   "source_allowlisted": source_allowlisted,
    #   "enforcement_active": enforcement_active,
    #   "import_blocked": enforcement_active and not source_allowlisted,
```

(Read the current `skills_import_preview_command` body and add these three keys to its `_print_json({...})` payload; if it delegates to a helper card, add them there. Keep it read-only.)

In `src/agentdeck/contracts.py`, add `source_allowlisted`, `enforcement_active`, `import_blocked` to `SKILLS_IMPORT_PREVIEW_RESPONSE_FIELDS` (grep it near line ~579), and if the skills contract discovery/example validates that shape, update the example fixture accordingly.

- [ ] **Step 4: run** `conda run -n agentdeck pytest tests/test_agent_cli.py -k "skills_import_preview" tests/test_contracts.py -k "skills" -q` → PASS.

- [ ] **Step 5: docs**
- `docs/contracts/skills-schema.md`: document the opt-in enforcement, `--allow-unlisted`, and the preview `source_allowlisted`/`enforcement_active`/`import_blocked` fields.
- `CLAUDE.md`: update the skills import rule — `skills import` now enforces `[skills] allowed_sources` when non-empty (blocks unlisted sources unless `--allow-unlisted`; both paths audited via `skill_imported.allowlisted` / `.allow_unlisted`); empty allowlist = no enforcement (backward compatible); `import-preview` surfaces the status read-only.
- `README.md`: one line. `HISTORY.md`: newest-first top entry (skill-marketplace lane: allowlist enforcement — decision "A"). `docs/handoff/current-development-state.md`: REMOVE the top `## ⏸ 需要你决策` section (A is now resolved+built); update the direction — enforcement done; next is **B: skill dependencies/composition** (needs its own brainstorm/spec — the human chose "先A再B").

- [ ] **Step 6: full verification**
- `conda run -n agentdeck pytest tests/test_agent_cli.py -k "skills_import" tests/test_contracts.py -k skills -q` → PASS
- `conda run -n agentdeck pytest -q` → all pass (baseline 719 + new)
- `conda run -n agentdeck python -m compileall src tests -q` → clean
- `git diff --check` → clean
- [ ] **Step 7: commit** `git commit -m "Surface allowlist status in skills import-preview; document enforcement"`

---

## Notes for the implementer
- Do NOT push. Commit locally only. No Claude co-author trailer. conda `agentdeck` env.
- Enforcement is **opt-in**: empty `[skills] allowed_sources` = unchanged behavior. Only a non-empty allowlist blocks, and only `--allow-unlisted` overrides. Both are audited.
- `skills catalog` / `skills sources` / `skills import-preview` stay read-only and non-blocking (preview only *reports* the block). Only `skills import` enforces.
- Reuse `_source_is_allowlisted`; do not duplicate the allowlist check.
- After this slice, the next lane item is **B: skill dependencies** — a separate brainstorm/spec; do NOT start it here.
```
