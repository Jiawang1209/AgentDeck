# Skill source catalog (`agentdeck skills catalog`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `agentdeck skills catalog --source <dir>` — read-only browse of a local source directory of `<name>/SKILL.md`, each item with provenance/hash, a three-state `import_status` vs the project's skills, and explicit `import-preview`/`import` commands. Copies nothing, writes nothing.

**Architecture:** New pure `browse_skill_source(dir)` in `skills.py` (reuses `_snapshot_from_content`); `skills_catalog_command` compares against `discover_skills(root)` project skills for `import_status`. Read-only.

**Tech Stack:** Python 3.12 stdlib, argparse, pytest. Run all via `conda run -n agentdeck ...`.

**Design spec:** `docs/superpowers/specs/2026-07-09-skill-catalog-design.md`

---

## File Structure
- Modify `src/agentdeck/skills.py` — add `browse_skill_source`.
- Modify `src/agentdeck/cli.py` — `skills_catalog_command` + `catalog` subparser + import.
- Modify `src/agentdeck/contracts.py` — `SKILLS_CATALOG_RESPONSE_FIELDS`, `SKILLS_CATALOG_ITEM_FIELDS`, expose in the skills contract discovery payload.
- Modify `docs/contracts/skills-schema.md`, `README.md`, `HISTORY.md`, `docs/handoff/current-development-state.md`.
- Modify `tests/test_agent_cli.py`, `tests/test_contracts.py`.

---

### Task 1: `browse_skill_source` + `agentdeck skills catalog` command

**Files:** Modify `src/agentdeck/skills.py`, `src/agentdeck/cli.py`; Test `tests/test_agent_cli.py`

- [ ] **Step 1: failing tests** — append to `tests/test_agent_cli.py`:

```python
def _write_catalog_skill(source_dir, name, description, body="Do the thing.\n"):
    d = source_dir / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}", encoding="utf-8"
    )
    return d / "SKILL.md"


def test_skills_catalog_browses_source_read_only(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    source = tmp_path / "catalog"
    _write_catalog_skill(source, "alpha", "Alpha skill")
    _write_catalog_skill(source, "beta", "Beta skill")
    before = StateStore(root).load()

    assert cli.main(["skills", "catalog", "--source", str(source)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "skills_catalog"
    assert payload["skill_count"] == 2
    assert payload["imported_count"] == 0
    names = {i["name"] for i in payload["items"]}
    assert names == {"alpha", "beta"}
    alpha = next(i for i in payload["items"] if i["name"] == "alpha")
    assert alpha["import_status"] == "not_imported"
    assert alpha["content_hash"].startswith("sha256:")
    assert alpha["import_preview_command"] == f"agentdeck skills import-preview --path {source / 'alpha' / 'SKILL.md'}"
    assert alpha["import_command"] == f"agentdeck skills import --path {source / 'alpha' / 'SKILL.md'}"
    kinds = {c["kind"] for c in alpha["controls"]}
    assert {"import_preview", "import"} <= kinds
    # read-only
    assert StateStore(root).load() == before
    assert not (root / ".agentdeck" / "skills").exists()


def test_skills_catalog_flags_imported_status(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    source = tmp_path / "catalog"
    src_path = _write_catalog_skill(source, "alpha", "Alpha skill")
    # import alpha into the project so it is "imported_identical"
    assert cli.main(["skills", "import", "--path", str(src_path)]) == 0
    capsys.readouterr()

    assert cli.main(["skills", "catalog", "--source", str(source)]) == 0
    payload = json.loads(capsys.readouterr().out)
    alpha = next(i for i in payload["items"] if i["name"] == "alpha")
    assert alpha["import_status"] == "imported_identical"
    assert payload["imported_count"] == 1

    # change the source content -> now it differs from the imported copy
    (source / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill\n---\n\nDo a DIFFERENT thing.\n", encoding="utf-8"
    )
    assert cli.main(["skills", "catalog", "--source", str(source)]) == 0
    payload2 = json.loads(capsys.readouterr().out)
    alpha2 = next(i for i in payload2["items"] if i["name"] == "alpha")
    assert alpha2["import_status"] == "imported_differs"


def test_skills_catalog_rejects_missing_source_and_handles_empty(tmp_path, monkeypatch, capsys):
    prepare_project(tmp_path, monkeypatch)
    assert cli.main(["skills", "catalog", "--source", str(tmp_path / "nope")]) == 1
    assert "not found" in capsys.readouterr().err.lower()
    empty = tmp_path / "empty"; empty.mkdir()
    assert cli.main(["skills", "catalog", "--source", str(empty)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["skill_count"] == 0
    assert payload["items"] == []
```

- [ ] **Step 2: run** `conda run -n agentdeck pytest tests/test_agent_cli.py -k skills_catalog -q` → FAIL (`invalid choice: 'catalog'`).

- [ ] **Step 3: implement**

In `src/agentdeck/skills.py`, add after `discover_skills`:

```python
def browse_skill_source(source_dir: Path) -> list[SkillSnapshot]:
    """Read-only catalog of a local skill source directory of <name>/SKILL.md."""
    if not source_dir.exists() or not source_dir.is_dir():
        raise FileNotFoundError(str(source_dir))
    snapshots: list[SkillSnapshot] = []
    for skill_path in sorted(source_dir.glob("*/SKILL.md")):
        snapshots.append(_snapshot_from_content(
            skill_path.read_text(encoding="utf-8"),
            source="catalog",
            path=skill_path,
            fallback_name=skill_path.parent.name,
        ))
    return sorted(snapshots, key=lambda snap: snap.name)
```

In `src/agentdeck/cli.py`, add `browse_skill_source` to the `from .skills import (...)` block (which already imports `discover_skills`). Add the command near `skills_list_command`:

```python
def skills_catalog_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    source_dir = Path(args.source)
    try:
        catalog = browse_skill_source(source_dir)
    except FileNotFoundError:
        print(f"skill source not found: {source_dir}", file=sys.stderr)
        return 1
    project = {
        skill.name: skill.content_hash
        for skill in discover_skills(Path(config.root))
        if skill.source == "project"
    }
    items: list[dict[str, object]] = []
    for snapshot in catalog:
        item = snapshot.summary()
        if snapshot.name in project:
            status = "imported_identical" if project[snapshot.name] == snapshot.content_hash else "imported_differs"
        else:
            status = "not_imported"
        preview_command = f"agentdeck skills import-preview --path {snapshot.path}"
        import_command = f"agentdeck skills import --path {snapshot.path}"
        item["import_status"] = status
        item["import_preview_command"] = preview_command
        item["import_command"] = import_command
        item["controls"] = list(item.get("controls", [])) + [
            _control(kind="import_preview", label="Preview import", command=preview_command, safety="inspect"),
            _control(kind="import", label="Import skill", command=import_command, safety="explicit_user"),
        ]
        items.append(item)
    _print_json({
        "ok": True,
        "mode": "skills_catalog",
        "source": str(source_dir),
        "skill_count": len(items),
        "imported_count": sum(1 for item in items if item["import_status"] != "not_imported"),
        "controls": [
            _control(kind="import", label="Import skill",
                     command="agentdeck skills import --path <SKILL.md>",
                     safety="explicit_user", enabled=False, blocker="requires SKILL.md path"),
        ],
        "items": items,
    })
    return 0
```

Register the subparser (next to `skills_list`, cli.py ~13325):

```python
    skills_catalog = skills_subparsers.add_parser("catalog", help="Read-only browse of a local skill source directory")
    skills_catalog.add_argument("--source", required=True, help="Directory of <name>/SKILL.md skills to browse")
    skills_catalog.set_defaults(func=skills_catalog_command)
```

- [ ] **Step 4: run** `conda run -n agentdeck pytest tests/test_agent_cli.py -k skills_catalog -q` → PASS.
- [ ] **Step 5: commit** `git commit -m "Add agentdeck skills catalog read-only source browse"`

---

### Task 2: contract discovery + docs + full verification

**Files:** Modify `src/agentdeck/contracts.py`, docs; Test `tests/test_contracts.py`

- [ ] **Step 1: failing test** — append to `tests/test_contracts.py`:

```python
def test_skills_contract_exposes_catalog_fields():
    from pathlib import Path
    from agentdeck.contracts import skills_contract_response, SKILLS_CATALOG_RESPONSE_FIELDS, SKILLS_CATALOG_ITEM_FIELDS
    payload = skills_contract_response(Path("docs/contracts/skills-schema.md"))
    assert payload["catalog_response_fields"] == list(SKILLS_CATALOG_RESPONSE_FIELDS)
    assert payload["catalog_item_fields"] == list(SKILLS_CATALOG_ITEM_FIELDS)
    assert payload["catalog_command"] == "agentdeck skills catalog --source <dir>"
```

(If the skills discovery accessor is named differently than `skills_contract_response`, grep `def skills_contract` in `src/agentdeck/contracts.py` and use the real name.)

- [ ] **Step 2: run** `conda run -n agentdeck pytest tests/test_contracts.py -k skills_contract_exposes_catalog -q` → FAIL.

- [ ] **Step 3: implement** in `src/agentdeck/contracts.py`:

Add the field tuples near the other `SKILLS_*_RESPONSE_FIELDS` (~line 564):

```python
SKILLS_CATALOG_RESPONSE_FIELDS = (
    "ok", "mode", "source", "skill_count", "imported_count", "controls", "items",
)

SKILLS_CATALOG_ITEM_FIELDS = (
    "name", "description", "source", "path", "content_hash", "required_tools", "risk",
    "show_command", "load_command", "controls",
    "import_status", "import_preview_command", "import_command",
)
```

In the skills contract discovery payload (`skills_contract_payload` — grep `def skills_contract_payload`), add:

```python
        "catalog_command": "agentdeck skills catalog --source <dir>",
        "catalog_response_fields": list(SKILLS_CATALOG_RESPONSE_FIELDS),
        "catalog_item_fields": list(SKILLS_CATALOG_ITEM_FIELDS),
```

- [ ] **Step 4: run** `conda run -n agentdeck pytest tests/test_contracts.py -k "skills_contract" -q` → PASS.

- [ ] **Step 5: docs**
- `docs/contracts/skills-schema.md`: document `skills catalog --source <dir>` — read-only source browse, `SKILLS_CATALOG_RESPONSE_FIELDS`/`SKILLS_CATALOG_ITEM_FIELDS`, the three-state `import_status`, and that it copies nothing (install still goes through explicit `skills import`).
- `README.md`: one line — `agentdeck skills catalog --source <dir>` read-only "shop window" over a local skill source; per-skill provenance/hash + import_status + explicit import commands; browsing never installs.
- `HISTORY.md`: newest-first top entry (skill-marketplace lane slice 1).
- `docs/handoff/current-development-state.md`: new "Current Direction: skill marketplace lane"; slice 1 (catalog) done; remaining slices in order: workbench `skills_catalog_card` + NL "浏览技能源" intent; trusted-source allowlist; ⚠️ skill dependencies/composition is a genuine product fork (stop + ask).

- [ ] **Step 6: full verification**
- `conda run -n agentdeck pytest tests/test_agent_cli.py -k skills_catalog tests/test_contracts.py -k skills_contract -q` → PASS
- `conda run -n agentdeck pytest -q` → all pass (baseline 705 + new)
- `conda run -n agentdeck python -m compileall src tests -q` → clean
- `git diff --check` → clean
- [ ] **Step 7: commit** `git commit -m "Expose skills catalog in the skills contract; document skill source browse"`

---

## Notes for the implementer
- Do NOT push. Commit locally only. No Claude co-author trailer. conda `agentdeck` env.
- Read-only slice: `skills catalog` copies no files, writes no state, appends no event, calls no provider, touches no tmux. A test asserts the project `.agentdeck/skills/` and state are unchanged after browsing.
- Reuse `_snapshot_from_content` (already builds name/description/content_hash/path) and `discover_skills` (for the project-skill compare). Do NOT reimplement frontmatter parsing.
- `import_status` compares against **project-sourced** skills only (built-ins are not "imported").
- Browsing is discovery only; installing still requires the explicit, preview-gated, audited `skills import --path <...>`.
- This is slice 1 of the skill-marketplace lane. Do NOT build the allowlist, workbench/NL integration, or dependencies — later slices.
```
