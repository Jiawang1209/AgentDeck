# M2c ACP Package Entrypoint Authority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the incorrect fixed Claude Agent ACP entrypoint with a safely parsed, package-metadata-bound entrypoint, freeze a new authority, pass deterministic and real acceptance, close M2c, and unlock M3.

**Architecture:** Keep the change inside the M2c acceptance harness. The complete non-following package tree remains the runtime authority; a new bounded parser reads the already sealed `package.json`, canonicalizes the official npm bin entry, and stores that relative path in `_PackageTreeSeal`. Authority v2, preflight v4, the explicit loader, the controlled Node launcher, and live admission reuse that single sealed selection.

**Tech Stack:** Python 3.12 standard library, pytest, conda environment `agentdeck`, JSON/JSONL evidence, git detached worktrees, real Codex/Claude/Node/tmux/Claude Agent ACP only after freeze.

---

## File map

- `tests/test_m2c_live_acceptance.py`: package metadata parser, package seal,
  authority v2, preflight v4, controlled launcher, fake-tool RED/GREEN, and the
  existing opt-in real nodes.
- `docs/validation/phase3-m2c-live-acceptance-sop.md`: exact audited package
  metadata, v2/v4, preflight/live commands, and durable evidence boundary.
- `docs/superpowers/specs/2026-07-17-m2c-tool-authority-binding-design.md`:
  mark the fixed-entrypoint subsection as superseded.
- `docs/superpowers/specs/2026-07-17-m2c-acp-package-entrypoint-authority-design.md`:
  implementation/freeze status only; requirements remain unchanged.
- `docs/handoff/current-development-state.md`, `HISTORY.md`, and
  `docs/roadmap/product-north-star.md`: progress and final M2c/M3 gate evidence.
- `docs/validation/2026-07-17-phase3-m2c-four-stage-live.md`: create only after
  the real preflight/live sequence, recording closed evidence.

All commands run from the dedicated feature worktree with:

```bash
WORKTREE=/Users/liuyue/.config/superpowers/worktrees/multi-agent-explore/codex/m2c-leader-preview-observability
cd "$WORKTREE"
```

No task modifies `src/agentdeck/**` unless a later real run proves a product
defect and a new approved spec/plan explicitly scopes it.

## Task 1: RED — prove npm metadata must select the package entrypoint

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py`
- Modify: `HISTORY.md`

- [x] **Step 1: Add a realistic package fixture without changing the sealer**

Replace `_fake_acp_package` with a parameterized fixture that writes
`package.json`, `dist/index.js`, and `lib/support.js`. Its default metadata is:

```python
{
    "name": "@agentclientprotocol/claude-agent-acp",
    "bin": {"claude-agent-acp": "dist/index.js"},
}
```

The helper accepts `bin_value`, `entrypoint`, and `package_name` solely to build
negative cases. Every ordinary fake package uses executable `dist/index.js`.

- [x] **Step 2: Add focused RED cases**

Add tests whose public assertions are:

```python
assert seal is not None and blocker is None
assert seal.entrypoint_relative == "dist/index.js"
assert seal.entrypoint.path == root / "dist" / "index.js"
```

Cover object-bin and string-bin forms. Add a digest test proving the canonical
ACP item is exactly:

```python
{
    "name": "claude-agent-acp",
    "kind": "package-tree",
    "tree_hash": seal.tree_hash,
    "entrypoint": "dist/index.js",
}
```

- [x] **Step 3: Run RED and verify the expected cause**

```bash
PYTHONPATH="$WORKTREE/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py \
  -k 'package_metadata_selects or authority_digest_binds_entrypoint' -q
```

Expected: failures because the current sealer still requires
`dist/claude-agent-acp` and `_PackageTreeSeal` has no
`entrypoint_relative` field. No real preflight/live marker is set.

- [x] **Step 4: Record and commit the RED boundary**

Update HISTORY with the observed RED count and root cause, then:

```bash
git add tests/test_m2c_live_acceptance.py HISTORY.md
git commit -m "test: expose M2c ACP package entrypoint mismatch"
```

## Task 2: GREEN — parse and seal the official npm bin entrypoint

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py`
- Modify: `HISTORY.md`

- [x] **Step 1: Add bounded metadata constants and seal shape**

Replace the fixed `ACP_ENTRYPOINT` with:

```python
ACP_PACKAGE_NAME = "@agentclientprotocol/claude-agent-acp"
ACP_COMMAND_NAME = "claude-agent-acp"
ACP_PACKAGE_JSON = PurePosixPath("package.json")
MAX_ACP_PACKAGE_JSON_BYTES = 1024 * 1024
```

Add `entrypoint_relative: str` to `_PackageTreeSeal` before `entrypoint`.
Advance constants to `AUTHORITY_SCHEMA_VERSION = "m2c-tool-authority/v2"` and
`STRICT_PREFLIGHT_SCHEMA_VERSION = "m2c-live-preflight/v4"`.

- [x] **Step 2: Implement duplicate-safe JSON and POSIX path selection**

Add pure helpers with these contracts:

```python
def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    # Raise ValueError on duplicate keys.

def _canonical_package_relative_path(raw: object) -> str:
    # Require str, reject NUL/backslash/absolute/empty/dot/parent traversal,
    # accept an optional leading "./", return PurePosixPath.as_posix().

def _acp_bin_entrypoint(metadata: object) -> str:
    # Require exact package name. Accept string bin or object bin command key.
```

Parse with `json.loads(text, object_pairs_hook=_unique_json_object)`. Do not
include parser exception text in any returned failure.

- [x] **Step 3: Read metadata as an already sealed package member**

Add `_read_sealed_package_member(root, manifest_item, runtime_item)` that opens
with `O_NOFOLLOW`, enforces the 1 MiB package-json limit, verifies initial/open/
closed/final identity plus manifest size/hash, and returns bytes. It must not
call `Path.resolve()` or follow a symlink.

In `_seal_acp_package_tree`:

1. find exact `package.json` manifest/runtime rows;
2. read and parse the verified bytes;
3. select the canonical entrypoint relative path;
4. find that exact manifest/runtime row;
5. require a regular executable file and matching executable seal;
6. construct `_PackageTreeSeal(..., entrypoint_relative, entrypoint, ...)`.

Any error returns only `claude_agent_acp_package_invalid`.

- [x] **Step 4: Bind entrypoint into authority v2**

Add `"entrypoint": authority.acp_package.entrypoint_relative` to the ACP item
in `_authority_digest_payload`. Update fake authority construction to call the
real package sealer rather than manually constructing a partial package seal.

- [x] **Step 5: Run GREEN**

```bash
PYTHONPATH="$WORKTREE/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py \
  -k 'package_metadata_selects or authority_digest or package_tree_manifest' -q
```

Expected: all selected tests pass.

- [x] **Step 6: Commit**

Update HISTORY with authority v2/preflight v4 behavior, then:

```bash
git add tests/test_m2c_live_acceptance.py HISTORY.md
git commit -m "test: bind M2c ACP entrypoint to package metadata"
```

## Task 3: RED/GREEN — reject malformed metadata and unsafe entrypoints

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py`
- Modify: `HISTORY.md`

- [x] **Step 1: Add table-driven RED cases**

Add parameterized cases for missing/oversized/non-UTF-8/invalid/duplicate-key
`package.json`, wrong package name, missing bin, non-string bin target, empty,
absolute, parent-traversing, backslash, NUL, missing, nested symlink, FIFO,
group/world-writable, and non-executable entrypoints. Every case asserts:

```python
assert seal is None
assert blocker == "claude_agent_acp_package_invalid"
assert str(tmp_path) not in repr((seal, blocker))
```

Run the selected cases before any additional implementation and confirm new
cases fail only where validation is incomplete.

- [x] **Step 2: Implement the minimum missing guards**

Extend only the pure parser/member validation required by each observed RED.
Do not add fallback, repair, wrapper generation, or package mutation.

- [x] **Step 3: Prove entrypoint and metadata drift fail closed**

Seal a valid package, then independently replace `package.json`, change its bin
target, replace the selected entrypoint with same bytes/new inode, and change a
support file. `_verify_package_tree_seal` must raise only
`claude_agent_acp_package_invalid`.

- [x] **Step 4: Run GREEN and commit**

```bash
PYTHONPATH="$WORKTREE/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py \
  -k 'package_metadata or package_tree_rejects or package_tree_runtime' -q
```

Update HISTORY, then commit:

```bash
git add tests/test_m2c_live_acceptance.py HISTORY.md
git commit -m "test: close M2c ACP metadata safety boundary"
```

## Task 4: RED/GREEN — reuse the selected entrypoint through loader and launcher

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py`
- Modify: `HISTORY.md`

- [x] **Step 1: Update explicit-authority RED cases**

Make `_fake_explicit_authority_environment` set
`AGENTDECK_M2C_CLAUDE_ACP` to the fixture's metadata-selected entrypoint. Add
tests proving an alternate package member, external same-content file, symlink,
and stale previously selected path all produce the existing closed binding
failure.

- [x] **Step 2: Remove every fixed-entrypoint consumer**

Replace all remaining `ACP_ENTRYPOINT` use with
`authority.acp_package.entrypoint_relative` or
`authority.acp_package.entrypoint.path`. `_write_controlled_acp_launcher` must
embed and execute the already sealed absolute entrypoint path, while its tree
walk revalidates the complete v2 manifest and exact Node seal.

- [x] **Step 3: Verify normal execution and drift**

The controlled launcher must execute fake Node plus `dist/index.js`, return
`0.58.1`, contain no `shutil.which`/`/usr/bin/env`, and exit `126` with empty
stdout/stderr after Node, package-json, entrypoint, or support-file drift.

- [x] **Step 4: Run GREEN and commit**

```bash
PYTHONPATH="$WORKTREE/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py \
  -k 'explicit_authority or controlled_acp_launcher or launcher_failure' -q
```

Update HISTORY, then commit:

```bash
git add tests/test_m2c_live_acceptance.py HISTORY.md
git commit -m "test: execute M2c ACP through metadata authority"
```

## Task 5: Close v4 contracts and durable documentation

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py`
- Modify: `docs/validation/phase3-m2c-live-acceptance-sop.md`
- Modify: `docs/superpowers/specs/2026-07-17-m2c-tool-authority-binding-design.md`
- Modify: `docs/superpowers/specs/2026-07-17-m2c-acp-package-entrypoint-authority-design.md`
- Modify: `docs/handoff/current-development-state.md`
- Modify: `HISTORY.md`

- [ ] **Step 1: Update strict contract expectations**

Rename v3-specific test names to v4, assert the nested authority is v2, and
retain the exact public fields, closed blocker/failure vocabulary, read-only
snapshots, skip gates, and transcript-free diagnostics.

- [ ] **Step 2: Run designated-node fake-tool regression**

```bash
PYTHONPATH="$WORKTREE/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py \
  -k 'strict_preflight or explicit_authority_preflight or designated_preflight or preflight_diagnostic' -q
```

Expected: fake-tool node passes; the actual real node remains skipped without
`AGENTDECK_M2C_STRICT_PREFLIGHT=1`.

- [ ] **Step 3: Update written contracts**

The SOP must instruct operators to use the exact metadata-selected
`dist/index.js` for the currently audited package without hard-coding it as a
universal rule. It must require package-json bin verification, authority v2,
preflight v4, exact digest reuse, absolute `PYTHONPATH`, and separate closed
evidence. Mark section 6.1 of the earlier authority spec superseded by the new
design; do not rewrite historical evidence.

- [ ] **Step 4: Commit**

```bash
git add tests/test_m2c_live_acceptance.py \
  docs/validation/phase3-m2c-live-acceptance-sop.md \
  docs/superpowers/specs/2026-07-17-m2c-tool-authority-binding-design.md \
  docs/superpowers/specs/2026-07-17-m2c-acp-package-entrypoint-authority-design.md \
  docs/handoff/current-development-state.md HISTORY.md
git commit -m "docs: bind M2c acceptance to npm package metadata"
```

## Task 6: Verify, freeze, and run two complete suites

**Files:**
- Modify: `docs/handoff/current-development-state.md`
- Modify: `HISTORY.md`
- Modify: this plan (checkboxes only)

- [ ] **Step 1: Run focused and non-live verification**

```bash
PYTHONPATH="$WORKTREE/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py \
  -k 'package_metadata or package_tree or tool_authority or explicit_authority or strict_preflight or controlled_acp_launcher' -q
PYTHONPATH="$WORKTREE/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py -q
PYTHONPATH="$WORKTREE/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_conversation_session.py tests/test_conversation_terminal_ui.py \
  tests/test_conversation_contracts.py tests/test_contracts.py \
  tests/test_cli_structured_output.py tests/test_dashboard.py \
  tests/test_provider_openai_compatible.py -q
python -m compileall tests/test_m2c_live_acceptance.py
```

- [ ] **Step 2: Audit scope and freeze**

```bash
git diff 766d4a19..HEAD --check
test -z "$(git diff 766d4a19..HEAD --name-only -- src/agentdeck)"
```

Record focused results in HISTORY/handoff, commit documentation, and record the
returned SHA as `FROZEN_SHA`. No implementation edit may follow without a new
freeze and restarted suites.

- [ ] **Step 3: Run full suite twice on the unchanged SHA**

Create two fresh detached worktrees and use their absolute source paths:

```bash
git worktree add --detach /tmp/agentdeck-m2c-entrypoint-suite-1 "$FROZEN_SHA"
env -u AGENTDECK_M2C_LIVE -u AGENTDECK_M2C_STRICT_PREFLIGHT \
  PYTHONPATH="/tmp/agentdeck-m2c-entrypoint-suite-1/src" \
  conda run --no-capture-output -n agentdeck pytest -q
git worktree remove /tmp/agentdeck-m2c-entrypoint-suite-1

git worktree add --detach /tmp/agentdeck-m2c-entrypoint-suite-2 "$FROZEN_SHA"
env -u AGENTDECK_M2C_LIVE -u AGENTDECK_M2C_STRICT_PREFLIGHT \
  PYTHONPATH="/tmp/agentdeck-m2c-entrypoint-suite-2/src" \
  conda run --no-capture-output -n agentdeck pytest -q
git worktree remove /tmp/agentdeck-m2c-entrypoint-suite-2
```

Both suites must pass with only known opt-in skips. Remove each worktree and
audit pytest/daemon processes and M2c temporary roots.

- [ ] **Step 4: Commit verification evidence**

Update HISTORY/handoff/plan with exact SHA, counts, durations, skip identities,
scope/leakage/cleanup results, and confirm that no real preflight/live ran.

## Task 7: Execute the designated real preflight once

**Files:**
- Create: `docs/validation/2026-07-17-phase3-m2c-four-stage-live.md`
- Modify: `docs/handoff/current-development-state.md`
- Modify: `HISTORY.md`

- [ ] **Step 1: Audit explicit installed inputs without changing them**

Resolve symlink-free regular executable targets for Codex, Claude, Node, tmux,
the package root, and the metadata-selected ACP entrypoint. Verify package name,
version, bin declaration, modes, and ownership. Do not install, upgrade, login,
or inspect user tmux sessions.

- [ ] **Step 2: Run one detached-SHA v4 preflight**

Set `AGENTDECK_M2C_STRICT_PREFLIGHT=1`, Leader `gpt-5.5`, every exact audited
absolute input, and absolute detached `PYTHONPATH`; run only
`test_m2c_explicit_authority_preflight_is_read_only -q -s`.

Record only SHA, model, v4/v2 schema versions, ready, blockers, closed failures,
sanitized tool versions, digest, duration, and cleanup. Remove the detached
worktree. Never store raw output, paths, environment, or package members.

- [ ] **Step 3: Gate live**

Proceed only if ready is true and blockers/failures are empty. Otherwise commit
the one-shot BLOCKED evidence, systematically debug, and open a new minimal
spec/plan/freeze cycle; never rerun the same authority.

## Task 8: Execute real four-stage Mission and close M2c

**Files:**
- Modify: `docs/validation/2026-07-17-phase3-m2c-four-stage-live.md`
- Modify: `docs/handoff/current-development-state.md`
- Modify: `docs/roadmap/product-north-star.md`
- Modify: `docs/roadmap/ultimate-goal-roadmap.md`
- Modify: `HISTORY.md`

- [ ] **Step 1: Run exactly one live node on the admitted authority**

Use the same frozen SHA, `gpt-5.5`, explicit inputs, and exact v2 digest with
`AGENTDECK_M2C_LIVE=1`. Run only `test_real_four_stage_m2c_acceptance -q -s`.

- [ ] **Step 2: Verify the complete acceptance matrix**

Require four ordered successful attempts; Claude/Codex/Claude/Codex Worker
order; ACP permissions; tmux identity/select-pane; disconnect/reconnect;
takeover/return-control; four canonical handoffs and three predecessor links;
exact `accepted-v2\n` artifact bytes/hash; ProjectView/Mission/workbench/ledger/
events/traces/admission/receipts agreement; and zero cleanup residue.

- [ ] **Step 3: Close or restart honestly**

On PASS, commit closed evidence, mark M2c complete, unlock M3, and update both
roadmaps. On failure, commit only closed one-shot evidence, do not claim M2c
complete, and return to a new root-cause/spec/plan/freeze authority cycle.

## Task 9: Transition from M2c to M3

**Files:**
- Create: `docs/superpowers/specs/2026-07-17-phase3-m3-design.md`
- Create: `docs/superpowers/plans/2026-07-17-phase3-m3.md`
- Modify: `docs/handoff/current-development-state.md`
- Modify: `HISTORY.md`

- [ ] **Step 1: Derive M3 from current north-star truth**

After and only after M2c PASS, inspect the updated roadmaps, M2c evidence, open
contract gaps, and current implementation. Brainstorm the smallest coherent M3
product milestone; do not assume historical labels are still current.

- [ ] **Step 2: Write, self-review, and commit the M3 spec**

Define user problem, architecture, contracts, migration, TDD, safety, Golden
Demo, and explicit non-goals. Preserve ACP-native communication, tmux visibility,
Mission governance, audit, recovery, and natural-language experience.

- [ ] **Step 3: Write and commit the M3 implementation plan**

Use writing-plans to create exact file/test/commit tasks. Stop before M3 code
implementation unless the active goal or a later user instruction explicitly
authorizes that scope.

## Plan self-review checklist

- [x] Every requirement in the approved entrypoint-authority spec maps to Tasks
  1-5.
- [x] The implementation remains harness-only through deterministic M2c work.
- [x] RED is observed before parser/sealer behavior is implemented.
- [x] Authority v2 and preflight v4 cannot accept v1/v3 evidence.
- [x] Full suites use fresh detached worktrees and absolute `PYTHONPATH`.
- [x] No real preflight/live command appears before freeze and double-suite PASS.
- [x] A failed real authority is recorded once and never silently retried.
- [x] M2c closes only on the real four-stage acceptance matrix.
- [x] M3 remains locked until M2c PASS.
- [x] No placeholder, silent fallback, installation, login, merge, or push is
  authorized by this plan.
