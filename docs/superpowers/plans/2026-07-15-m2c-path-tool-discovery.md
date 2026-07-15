# M2c PATH and Tool Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the M2c read-only preflight discover already installed Codex, Claude, Claude Agent ACP, Node, and tmux through the active conda/pytest PATH while retaining strict canonical executable sealing and the single-preflight gate.

**Architecture:** Add one test-harness-only canonicalization boundary between `shutil.which()`/explicit overrides and the existing strict `_seal_executable()` primitive. Keep production AgentDeck discovery and Task 14 launcher authority unchanged; resolve and seal Node only as an internal ACP probe dependency, use that seal as the ACP probe's primary executable with the canonical adapter path as an argument, preserve the four-tool response shape, and validate everything with fake executables before freezing one implementation commit.

**Tech Stack:** Python 3.12 standard library (`pathlib`, `shutil`, `os`, `stat`, `subprocess`), pytest, conda environment `agentdeck`, Git.

---

## File map

- Modify: `tests/test_m2c_live_acceptance.py`
  - Owns the M2c-only executable seal, read-only capability probes, deterministic fake-tool regressions, and real opt-in acceptance.
  - Add a narrow canonical preflight resolver here; do not create a production helper.
- Modify: `HISTORY.md`
  - Record the implemented harness correction, deterministic evidence, and the still-blocked M2c/live boundary.
- Modify: `docs/handoff/current-development-state.md`
  - Route the next operator through the frozen implementation SHA, double suite, and one preflight gate.
- Modify: `docs/validation/2026-07-13-phase3-m2-project-daemon.md`
  - Append the new PATH/tool-discovery evidence without rewriting historical blocked attempts.
- Modify: `docs/superpowers/specs/2026-07-15-m2c-path-tool-discovery-design.md`
  - Human-approved design authority, synchronized with quality-review closure.
- Modify: `docs/superpowers/plans/2026-07-15-m2c-path-tool-discovery.md`
  - Record the reviewed direct sealed-Node execution semantics and closure tests.
- Reference only: `src/agentdeck/cli.py`, `src/agentdeck/providers/cli_subprocess.py`, `src/agentdeck/runtime/tmux.py`
  - Existing production PATH-first behavior; no edits are authorized.

## Execution invariants

- Work only in the `agentdeck` conda environment.
- Do not run `test_m2c_live_preflight_is_read_only` as a focused node until the final single-preflight step.
- Deterministic tests must set fake PATH and clear every `AGENTDECK_M2C_*` variable before calling `_live_preflight()`.
- Do not set `AGENTDECK_M2C_LIVE=1` anywhere in this plan.
- Do not install, upgrade, log in, modify shell/conda/global settings, inspect a user tmux session, or send terminal input.
- Keep `_seal_executable()` strict and keep `_explicit_live_paths()` plus Task 14 controlled launchers byte-semantically unchanged.
- The design permits one implementation commit after all RED/GREEN tasks; do not create intermediate implementation commits.

### Task 1: RED — prove valid PATH and explicit symlinks are false negatives

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py:3233-3265`
- Test: `tests/test_m2c_live_acceptance.py`

- [ ] **Step 1: Add a deterministic fake-tool writer beside the preflight tests**

Add this local helper immediately before `test_m2c_live_preflight_is_read_only`:

```python
def _write_fake_capability_tool(path: Path, name: str) -> None:
    responses = {
        "codex": (
            "case \"$1\" in\n"
            "  exec) echo --output-schema --output-last-message;;\n"
            "  *) echo codex-cli 0.131.0;;\n"
            "esac\n"
        ),
        "claude": (
            "case \"$1\" in\n"
            "  --help) echo --json-schema --output-format;;\n"
            "  *) echo claude 2.1.208;;\n"
            "esac\n"
        ),
        "claude-agent-acp": "echo 0.58.1\n",
        "tmux": "echo tmux 3.6a\n",
    }
    path.write_text("#!/bin/sh\n" + responses[name], encoding="utf-8")
    path.chmod(0o700)
```

The helper is deterministic, makes no network call, and writes only under the
test's `tmp_path` before the preflight snapshot.

- [ ] **Step 2: Add the PATH symlink RED test**

Add this test after the helper:

```python
def test_preflight_resolves_path_symlinks_to_canonical_targets(
    tmp_path, monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    targets = tmp_path / "targets"
    targets.mkdir()
    discovered = tmp_path / "bin"
    discovered.mkdir()
    target_names = {
        "codex": "codex-real",
        "claude": "2.1.208",
        "claude-agent-acp": "index.js",
        "tmux": "tmux-real",
    }
    for name, env_name, _help, _version in TOOL_SPECS:
        target = targets / target_names[name]
        _write_fake_capability_tool(target, name)
        (discovered / name).symlink_to(target)
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv("PATH", str(discovered))

    payload = _live_preflight(project)

    assert payload["ready"] is True
    assert payload["blockers"] == []
    assert [item["executable_basename"] for item in payload["tools"]] == [
        "codex",
        "claude",
        "claude-agent-acp",
        "tmux",
    ]
    assert _validate_preflight_payload(payload) == []
```

- [ ] **Step 3: Run only the new PATH test and verify RED**

Run:

```bash
conda run -n agentdeck python -m pytest \
  tests/test_m2c_live_acceptance.py::test_preflight_resolves_path_symlinks_to_canonical_targets \
  -q
```

Expected: FAIL because `_resolved_probe_seal()` passes each symlink directly to
`_seal_executable()`, producing the existing unavailable blockers. This command
uses only fake tools and does not consume the real preflight allowance.

- [ ] **Step 4: Add and run the explicit-symlink RED test**

Add:

```python
def test_preflight_resolves_explicit_absolute_symlink(
    tmp_path, monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    targets = tmp_path / "targets"
    targets.mkdir()
    links = tmp_path / "links"
    links.mkdir()
    for name, env_name, _help, _version in TOOL_SPECS:
        target = targets / f"{name}-target"
        _write_fake_capability_tool(target, name)
        link = links / name
        link.symlink_to(target)
        monkeypatch.setenv(env_name, str(link))
    monkeypatch.setenv("PATH", str(links))

    payload = _live_preflight(project, require_explicit_paths=True)

    assert payload["ready"] is True
    assert payload["blockers"] == []
    assert _validate_preflight_payload(payload) == []
```

Run:

```bash
conda run -n agentdeck python -m pytest \
  tests/test_m2c_live_acceptance.py::test_preflight_resolves_explicit_absolute_symlink \
  -q
```

Expected: FAIL with unavailable blockers because strict seal currently rejects
the explicit symlink.

### Task 2: GREEN — canonicalize only preflight candidates

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py:271-334`
- Modify: `tests/test_m2c_live_acceptance.py:586-665`
- Test: `tests/test_m2c_live_acceptance.py`

- [ ] **Step 1: Add the narrow canonical preflight seal helper**

Keep `_seal_executable()` unchanged. Insert this helper immediately after it:

```python
def _seal_preflight_candidate(
    value: str | None, *, require_absolute: bool,
) -> _ExecutableSeal | None:
    if not value:
        return None
    raw_candidate = Path(value)
    if require_absolute and not raw_candidate.is_absolute():
        return None
    try:
        candidate = raw_candidate.expanduser()
        canonical = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return _seal_executable(str(canonical))
```

This helper checks the raw explicit value before expansion, so `~/tool` and
relative values cannot become absolute through environment-dependent
rewriting. It then resolves only the discovery candidate. The returned seal
contains the canonical regular path, and all existing descriptor/hash
verification continues to run in `_seal_executable()`.

Final-review TDD adds
`test_preflight_candidate_rejects_tilde_explicit_override`: with an executable
`$HOME/tool`, the initial implementation returns a seal for raw `~/tool` and
the test is RED. Moving the absolute gate before `expanduser()` makes both the
tilde value and a plain relative value fail closed while the existing
expanduser RuntimeError test remains GREEN. This is part of the same amended
implementation commit, not a new commit.

- [ ] **Step 2: Route PATH and explicit preflight candidates through the helper**

Replace `_resolved_probe_seal()` with:

```python
def _resolved_probe_seal(name: str, env_name: str) -> _ExecutableSeal | None:
    configured = os.environ.get(env_name)
    if configured is not None:
        return _seal_preflight_candidate(configured, require_absolute=True)
    return _seal_preflight_candidate(
        shutil.which(name), require_absolute=False,
    )
```

In `_live_preflight()`, replace the explicit branch:

```python
resolved[name] = (
    _seal_preflight_candidate(configured, require_absolute=True)
    if require_explicit_paths
    else _resolved_probe_seal(name, env_name)
)
```

- [ ] **Step 3: Preserve the logical executable basename**

Replace the tool-item basename expression with:

```python
"executable_basename": name,
```

The response continues to identify the logical tool and cannot leak a
version-named canonical target.

- [ ] **Step 4: Convert the old blanket symlink rejection fixture to a broken-link fixture**

In `test_m2c_live_preflight_is_read_only`, remove `unsafe_target` and create the
link without a target:

```python
unsafe_link = tmp_path / "claude"
unsafe_link.symlink_to(tmp_path / "missing-claude-target")
```

Keep the expected four unavailable blockers: Codex remains a relative explicit
override, Claude is now a strict broken link, and the other two explicit values
remain unset. The test still proves fail-closed explicit input without treating
all installed symlinks as unsafe.

- [ ] **Step 5: Run both new tests and the existing read-only shape logic without invoking its real-tool node**

Run:

```bash
conda run -n agentdeck python -m pytest \
  tests/test_m2c_live_acceptance.py::test_preflight_resolves_path_symlinks_to_canonical_targets \
  tests/test_m2c_live_acceptance.py::test_preflight_resolves_explicit_absolute_symlink \
  -q
```

Expected: `2 passed`.

### Task 3: RED/GREEN — execute ACP through the sealed Node interpreter

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py:254-268`
- Modify: `tests/test_m2c_live_acceptance.py:593-675`
- Test: `tests/test_m2c_live_acceptance.py`

- [ ] **Step 1: Add a fake env-Node ACP regression with a version-named canonical Node**

Add:

```python
def test_preflight_invokes_canonical_node_for_acp_adapter(
    tmp_path, monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    discovered = tmp_path / "bin"
    discovered.mkdir()
    targets = tmp_path / "targets"
    targets.mkdir()
    for name, env_name, _help, _version in TOOL_SPECS:
        target = targets / f"{name}-target"
        if name == "claude-agent-acp":
            target.write_text(
                "#!/usr/bin/env node\n"
                "process.stdout.write('0.58.1\\n')\n",
                encoding="utf-8",
            )
            target.chmod(0o700)
        else:
            _write_fake_capability_tool(target, name)
        (discovered / name).symlink_to(target)
        monkeypatch.delenv(env_name, raising=False)
    node_target = targets / "node-22.23.0"
    node_target.write_text(
        "#!/bin/sh\necho 0.58.1\n",
        encoding="utf-8",
    )
    node_target.chmod(0o700)
    (discovered / "node").symlink_to(node_target)
    monkeypatch.setenv("PATH", str(discovered))

    payload = _live_preflight(project)

    acp = next(
        item for item in payload["tools"]
        if item["name"] == "claude-agent-acp"
    )
    assert acp == {
        "name": "claude-agent-acp",
        "executable_basename": "claude-agent-acp",
        "version": "0.58.1",
        "ready": True,
    }
    assert payload["ready"] is True
    assert payload["blockers"] == []
```

The fake Node emits the bounded adapter version without interpreting the
JavaScript fixture. Its canonical basename is intentionally not `node`; PATH
selects the symlink candidate, while the canonical seal supplies authority.

- [ ] **Step 2: Run the Node regression and verify RED**

Run:

```bash
conda run -n agentdeck python -m pytest \
  tests/test_m2c_live_acceptance.py::test_preflight_invokes_canonical_node_for_acp_adapter \
  -q
```

Expected for the initial candidate: FAIL with ACP unavailable because the
accidental `node_seal.path.name == "node"` gate rejects the valid version-named
canonical target.

- [ ] **Step 3: Add a pre-spawn Node replacement regression**

Add `test_acp_probe_uses_node_seal_as_primary_executable`. Wrap the real
`_bounded_probe`; when its primary seal is the version-named Node target,
replace that target immediately before forwarding the call. Require the
wrapper to observe Node as primary, require no fallback/adapter marker, and
require ACP to be unready with `executable_identity_drift` plus the existing
ACP unavailable blocker.

Expected for the initial candidate: RED because `_bounded_probe` receives the
adapter seal instead of the Node seal, so the wrapper never observes Node as
primary. This reproduces the quality-review finding that manual Node verify
followed by `/usr/bin/env` lookup leaves an interpreter replacement window.

- [ ] **Step 4: Resolve Node and use its seal as the primary executable**

In `_live_preflight()`, after resolving the four public tools, add:

```python
node_seal = (
    _seal_preflight_candidate(
        shutil.which("node"), require_absolute=False,
    )
    if resolved.get("claude-agent-acp") is not None
    else None
)
probe_env = _probe_environment(
    isolation,
    tuple(
        seal.path
        for seal in (node_seal, *resolved.values())
        if seal is not None
    ),
)
```

Do not require the canonical Node basename to equal `node`. If ACP exists but
Node cannot be sealed, retain the early ACP-unavailable path and do not execute
the adapter. Otherwise verify the adapter seal before the call, then invoke:

```python
version_probe = _bounded_probe(
    node_seal,
    (str(executable.path), *version_args),
    cwd=project,
    env=tool_probe_env,
)
```

Verify the adapter seal again after the call and replace the outcome with
`executable_identity_drift` if it changed. `_bounded_probe` itself verifies
Node immediately before spawn and after exit. The adapter is passed directly
to Node, so ACP execution has no `/usr/bin/env` lookup or unsealed PATH
fallback. Do not expand `_bounded_probe`'s API or add public response fields.

- [ ] **Step 5: Isolate every fake ACP-ready fixture from machine Node**

In each fake explicit-preflight fixture that expects ACP ready, create an
executable `fake_bin/node` emitting `0.58.1` and monkeypatch `PATH` to that
`fake_bin`. This includes the real-HOME write isolation, guarded Codex home,
same-process-group child cleanup, and post-preflight executable replacement
tests. No fake-ready fixture may rely on the machine Node to parse a fake shell
adapter.

- [ ] **Step 6: Run the Node and symlink matrix**

Run:

```bash
conda run -n agentdeck python -m pytest \
  tests/test_m2c_live_acceptance.py::test_preflight_invokes_canonical_node_for_acp_adapter \
  tests/test_m2c_live_acceptance.py::test_acp_probe_uses_node_seal_as_primary_executable \
  tests/test_m2c_live_acceptance.py::test_preflight_does_not_execute_acp_without_sealed_path_node \
  tests/test_m2c_live_acceptance.py::test_preflight_resolves_path_symlinks_to_canonical_targets \
  tests/test_m2c_live_acceptance.py::test_preflight_resolves_explicit_absolute_symlink \
  -q
```

Expected: `5 passed`.

This direct sealed-Node construction and the replacement regression are the
quality-review closure for the initial implementation candidate. They do not
consume the designated real-tool preflight.

### Task 4: RED/GREEN — preserve strict negative and drift boundaries

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py:3233-3300`
- Modify: `tests/test_m2c_live_acceptance.py:4860-5000`
- Test: `tests/test_m2c_live_acceptance.py`

- [ ] **Step 1: Add table coverage for rejected canonical candidates**

Add:

```python
@pytest.mark.parametrize(
    "candidate_kind",
    ("broken", "cycle", "directory", "non_executable"),
)
def test_preflight_candidate_rejects_unsafe_canonical_target(
    tmp_path, candidate_kind,
) -> None:
    candidate = tmp_path / "candidate"
    if candidate_kind == "broken":
        candidate.symlink_to(tmp_path / "missing")
    elif candidate_kind == "cycle":
        other = tmp_path / "other"
        candidate.symlink_to(other)
        other.symlink_to(candidate)
    elif candidate_kind == "directory":
        candidate.mkdir()
    else:
        candidate.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        candidate.chmod(0o600)

    assert _seal_preflight_candidate(
        str(candidate), require_absolute=True,
    ) is None
```

- [ ] **Step 2: Add canonical-target replacement coverage**

Add:

```python
def test_preflight_symlink_seal_rejects_canonical_target_replacement(
    tmp_path,
) -> None:
    target = tmp_path / "target"
    target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    target.chmod(0o700)
    link = tmp_path / "tool"
    link.symlink_to(target)
    seal = _seal_preflight_candidate(str(link), require_absolute=True)
    assert seal is not None
    replacement = tmp_path / "replacement"
    replacement.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    replacement.chmod(0o700)
    replacement.replace(target)

    with pytest.raises(_LiveHarnessFailure, match="executable_identity_drift"):
        _verify_executable_seal(seal)
```

- [ ] **Step 3: Run strict-boundary tests**

Run:

```bash
conda run -n agentdeck python -m pytest \
  tests/test_m2c_live_acceptance.py::test_preflight_candidate_rejects_unsafe_canonical_target \
  tests/test_m2c_live_acceptance.py::test_preflight_symlink_seal_rejects_canonical_target_replacement \
  tests/test_m2c_live_acceptance.py::test_executable_seal_rejects_replacement_before_use \
  -q
```

Expected: `7 passed` because the first and third nodes are parametrized. The
strict `_seal_executable()` replacement regression must remain unchanged.

- [ ] **Step 4: Run the deterministic preflight group without selecting the real auto-discovery node**

Run the exact fake-only nodes:

```bash
conda run -n agentdeck python -m pytest \
  tests/test_m2c_live_acceptance.py::test_preflight_resolves_path_symlinks_to_canonical_targets \
  tests/test_m2c_live_acceptance.py::test_preflight_resolves_explicit_absolute_symlink \
  tests/test_m2c_live_acceptance.py::test_preflight_invokes_canonical_node_for_acp_adapter \
  tests/test_m2c_live_acceptance.py::test_acp_probe_uses_node_seal_as_primary_executable \
  tests/test_m2c_live_acceptance.py::test_preflight_does_not_execute_acp_without_sealed_path_node \
  tests/test_m2c_live_acceptance.py::test_preflight_candidate_rejects_unsafe_canonical_target \
  tests/test_m2c_live_acceptance.py::test_preflight_symlink_seal_rejects_canonical_target_replacement \
  tests/test_m2c_live_acceptance.py::test_preflight_isolates_probe_writes_from_real_home \
  tests/test_m2c_live_acceptance.py::test_codex_probe_uses_temporary_guarded_home_without_writes \
  tests/test_m2c_live_acceptance.py::test_successful_probe_reaps_same_group_children \
  tests/test_m2c_live_acceptance.py::test_post_preflight_executable_replacement_blocks_project_init \
  -q
```

Expected: `14 passed`. Do not add
`test_m2c_live_preflight_is_read_only` to this command.

### Task 5: Synchronize durable documentation and freeze one implementation commit

**Files:**
- Modify: `HISTORY.md:5-20`
- Modify: `docs/handoff/current-development-state.md:5-35`
- Modify: `docs/validation/2026-07-13-phase3-m2-project-daemon.md`
- Modify: `docs/superpowers/specs/2026-07-15-m2c-path-tool-discovery-design.md`
- Modify: `docs/superpowers/plans/2026-07-15-m2c-path-tool-discovery.md`
- Test: repository verification commands

- [ ] **Step 1: Update HISTORY without claiming live readiness**

Extend `Design M2c PATH-first tool discovery` with these implemented facts:

```markdown
- Implemented a preflight-only canonical resolver between PATH/explicit candidates and the unchanged strict executable seal. Valid installed symlinks now bind to canonical regular targets; broken/cyclic/non-executable targets and post-seal replacement still fail closed.
- Quality-review closure uses sealed Node as the ACP probe primary executable with the canonical adapter path as its argument, eliminating env lookup/fallback while accepting version-named canonical Node targets.
- Added fake-only PATH, explicit symlink, logical-basename, env-Node shebang, unsafe-target, target-drift, and read-only-isolation regressions. Production Leader/ACP/tmux discovery and Task 14 controlled launchers remain unchanged.
```

Do not add full-suite or real-preflight results before those commands run.

- [ ] **Step 2: Update handoff routing for the candidate commit**

Replace the final paragraph of the top active-goal section with:

```markdown
The approved spec and detailed TDD plan are implemented, including
quality-review closure, and the candidate revision containing this handoff is
committed. The next gate is to record its exact SHA, run two independent full
suites without changing it, and then run the designated read-only preflight
exactly once without explicit path overrides. No live Mission is authorized.
M2c remains **BLOCKED** and M3 remains locked.
```

- [ ] **Step 3: Append a pending verification subsection to the M2 validation report**

Append:

```markdown
## PATH-first preflight discovery candidate

The M2c harness now resolves PATH or explicit preflight candidates to strict
canonical executable targets before applying its existing inode/content seal.
This corrects false unavailable classification for ordinary Claude, npm, and
Homebrew symlinks without changing production dispatch or Task 14 launcher
authority. ACP runs through sealed Node as the primary executable with the
canonical adapter as an argument and no env fallback. Deterministic fake-tool
regressions pass; no new real-tool preflight or live Mission has run in this
candidate revision. The result remains
**BLOCKED** pending two unchanged-SHA full suites and one designated read-only
preflight.
```

- [ ] **Step 4: Run the complete non-live M2c harness**

Run the same established selection used by the semantic-authority closure,
excluding only `test_real_four_stage_m2c_acceptance` through its existing live
skip:

```bash
conda run -n agentdeck python -m pytest tests/test_m2c_live_acceptance.py -q
```

Expected: all deterministic cases pass and exactly one live test is skipped.
Record the fresh count in the commit message notes or terminal evidence; do not
claim the later designated preflight result.

- [ ] **Step 5: Run compile and diff checks**

Run:

```bash
conda run -n agentdeck python -m compileall src tests -q
git diff --check
```

Expected: both exit `0` with no compile error or whitespace error.

- [ ] **Step 6: Amend quality-review closure into the implementation commit**

Run:

```bash
git add \
  tests/test_m2c_live_acceptance.py \
  HISTORY.md \
  docs/handoff/current-development-state.md \
  docs/validation/2026-07-13-phase3-m2-project-daemon.md \
  docs/superpowers/specs/2026-07-15-m2c-path-tool-discovery-design.md \
  docs/superpowers/plans/2026-07-15-m2c-path-tool-discovery.md
git commit --amend --no-edit
```

Expected: the same single implementation commit, with a new SHA containing all
RED/GREEN, quality-review closure, and durable-doc changes. No production
source file is staged and no additional commit is created.

### Task 6: Freeze the SHA, run two full suites, and consume one preflight

**Files:**
- Verify only: entire repository
- No file modifications authorized

- [ ] **Step 1: Freeze and record the exact candidate SHA**

Run:

```bash
git status --short
git rev-parse HEAD
```

Expected: empty status and the new implementation SHA. Save the SHA in the
execution transcript.

- [ ] **Step 2: Run full suite one in a fresh process**

Run:

```bash
conda run -n agentdeck python -m pytest -q
```

Expected: exit `0`. Record exact passed/skipped counts and duration.

- [ ] **Step 3: Prove the revision did not change**

Run:

```bash
git status --short
git rev-parse HEAD
```

Expected: empty status and byte-equal SHA from Step 1.

- [ ] **Step 4: Run full suite two in a second fresh process**

Run:

```bash
conda run -n agentdeck python -m pytest -q
```

Expected: exit `0`. Record exact passed/skipped counts and duration separately.

- [ ] **Step 5: Recheck the frozen revision**

Run:

```bash
git status --short
git rev-parse HEAD
```

Expected: empty status and the same SHA. If either full suite failed or the
tree/SHA changed, stop; do not run the preflight. Debug, create a new commit,
and restart Task 6 from Step 1.

- [ ] **Step 6: Run the designated real-tool preflight exactly once**

Run exactly:

```bash
conda run -n agentdeck python -m pytest \
  tests/test_m2c_live_acceptance.py::test_m2c_live_preflight_is_read_only \
  -q -s
```

Do not prefix the command with `AGENTDECK_M2C_*` or manually computed paths.
Expected success gate: pytest exits `0` and the printed sanitized payload has
exactly `ready=true` and `blockers=[]` with four ready logical tools.

- [ ] **Step 7: Stop on either result**

If the payload is ready, report the frozen SHA, both full-suite results, and the
single preflight payload, then stop and request separate human authorization
for Task 14. Do not set `AGENTDECK_M2C_LIVE=1`.

If the payload is blocked, report the exact sanitized blockers and stop. Do not
rerun the node, change PATH/auth/install state, or attempt the live Mission. A
new blocker requires a separate systematic-debugging/design round.

## Plan self-review

- **Spec coverage:** Tasks 1-4 cover PATH and explicit symlinks, canonical
  identity, strict unsafe-target rejection, stable logical basenames, Node,
  drift, and read-only behavior. Tasks 5-6 cover documentation, the single
  implementation commit, unchanged-SHA double suite, one preflight, and the
  separate live authorization boundary.
- **Scope:** No production source file changes. `_seal_executable()`,
  `_explicit_live_paths()`, Task 14 launchers, response schema, blocker set,
  provider/auth state, and live Mission remain unchanged.
- **TDD:** Each new behavior has an explicit failing command before its minimal
  implementation and an exact passing command afterward. Quality review added
  RED coverage for version-named Node and pre-spawn Node replacement before
  changing ACP probe construction.
- **Type consistency:** `_seal_preflight_candidate()` always returns
  `_ExecutableSeal | None`; `_live_preflight()` retains
  `dict[str, object]`; public tool dictionaries retain their exact four fields.
- **Placeholder scan:** The plan contains no TBD/TODO/fill-later instruction;
  every edit and verification step has concrete code or commands.
- **Evidence safety:** Fake-only focused commands avoid the designated real
  node. The final real preflight is a single command after two clean full-suite
  passes, and neither result authorizes Task 14.
- **Interpreter authority:** ACP is invoked as sealed Node plus the canonical
  adapter argument. The bounded PATH is no longer an interpreter selection or
  fallback mechanism, and fake-ready fixtures provide their own Node.
- **Explicit-path authority:** The raw override must be absolute before tilde
  expansion or canonicalization. Deterministic tilde, plain-relative, and
  expanduser-failure coverage proves this ordering remains fail closed.
