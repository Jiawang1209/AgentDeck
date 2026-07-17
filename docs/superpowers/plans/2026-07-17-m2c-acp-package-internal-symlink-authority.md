# M2c ACP Package Internal Symlink Authority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Seal the official npm package's closed `.bin` symlinks without following them, establish authority v3/preflight v5, and resume real M2c acceptance.

**Architecture:** Extend the existing complete package manifest with one narrow `symlink` member kind. Traversal records stable `lstat` facts and a SHA-256 of bounded UTF-8 link text, then a pure lexical closure validator permits only `node_modules/.bin/{command}` links whose normalized target is an existing regular manifest file. The controlled launcher reproduces the same scan and closure check before executing the unchanged regular ACP entrypoint through sealed Node.

**Tech Stack:** Python 3.12 standard library, pytest, conda `agentdeck`, detached git worktrees, real installed tools only after deterministic freeze.

---

## Task 1: RED — model the installed npm `.bin` layout

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py`
- Modify: `HISTORY.md`

- [x] Add `internal_bin_links: bool = False` to `_fake_acp_package`. When true,
  create executable regular targets at
  `node_modules/which/bin/node-which` and
  `node_modules/@anthropic-ai/sdk/bin/cli`, then create only these relative
  links:

```text
node_modules/.bin/node-which -> ../which/bin/node-which
node_modules/.bin/anthropic-ai-sdk -> ../@anthropic-ai/sdk/bin/cli
```

- [x] Add `test_m2c_package_tree_accepts_closed_npm_bin_symlinks`. Assert a
  successful seal, two sorted `kind=symlink` rows, exact link-text hashes, and
  path-independent tree hashes across two roots.

- [x] Run the focused test before implementation:

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py \
  -k 'accepts_closed_npm_bin_symlinks' -q
```

Expected: RED with `claude_agent_acp_package_invalid` because the current scan
rejects all symlinks.

- [x] Record the RED count in HISTORY and commit:

```bash
git add tests/test_m2c_live_acceptance.py HISTORY.md \
  docs/superpowers/plans/2026-07-17-m2c-acp-package-internal-symlink-authority.md
git commit -m "test: expose official npm bin symlink authority gap"
```

## Task 2: GREEN — add non-following closed-link package manifests

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py`
- Modify: `HISTORY.md`

- [x] Advance schemas:

```python
AUTHORITY_SCHEMA_VERSION = "m2c-tool-authority/v3"
STRICT_PREFLIGHT_SCHEMA_VERSION = "m2c-live-preflight/v5"
MAX_PACKAGE_SYMLINK_BYTES = 4096
```

- [x] Add pure `_canonical_internal_bin_symlink_target(link_path, raw_target,
  manifest_by_path)` validation. It must require exactly three link path parts
  (`node_modules`, `.bin`, command), relative bounded UTF-8 text, no NUL or
  backslash, lexical component processing without root underflow, and a
  distinct final manifest row of kind `file`.

- [x] Extend `_read_safe_package_manifest` without following links. For a
  symlink:

```python
initial = path.lstat()
first_target = os.readlink(path)
final = path.lstat()
second_target = os.readlink(path)
```

Require stable identity/text, encode UTF-8 within 4096 bytes, append a runtime
row from `lstat`, and append a manifest row with `kind="symlink"`, byte length,
SHA-256 link-text hash, and `executable=False`. Never recurse, `open`, `stat`,
or read file bytes through it. After the full scan, validate every retained raw
target against the complete manifest.

- [x] Preserve ordinary safety: symlinks outside `.bin`, absolute/escaping
  targets, missing targets, directory targets, and link chains raise only
  `ValueError` inside the sealer and project to
  `claude_agent_acp_package_invalid`.

- [x] Run GREEN plus existing package tests:

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py \
  -k 'closed_npm_bin_symlinks or package_metadata or package_tree' -q
```

- [x] Update HISTORY and commit:

```bash
git add tests/test_m2c_live_acceptance.py HISTORY.md
git commit -m "test: seal closed npm bin symlink authority"
```

## Task 3: RED/GREEN — reject unsafe links and runtime drift

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py`
- Modify: `HISTORY.md`

- [x] Add table-driven rejection tests for link outside `.bin`, nested
  `.bin/command/child`, absolute target, root escape, empty/NUL/backslash text,
  missing target, directory target, FIFO target, and symlink-chain target. Each
  case asserts only `claude_agent_acp_package_invalid` and no temporary path in
  the returned failure.

- [x] Add runtime tests that seal a valid linked package and then change link
  text, replace the link with the same text/new inode, replace the regular target
  with same bytes/new inode, and mutate target bytes. `_verify_package_tree_seal`
  must reject every case.

- [x] Run the new cases before any missing guard, observe RED if present, add
  only the minimum lexical/runtime guard, and rerun:

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py \
  -k 'npm_bin_symlink_rejects or npm_bin_symlink_drift' -q
```

- [x] Update HISTORY and commit:

```bash
git add tests/test_m2c_live_acceptance.py HISTORY.md
git commit -m "test: close npm bin symlink safety boundary"
```

## Task 4: RED/GREEN — reproduce link authority in the controlled launcher

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py`
- Modify: `HISTORY.md`

- [x] Make `_fake_explicit_authority_environment` accept
  `internal_bin_links=True`. Add a launcher normal-execution test using that
  package and drift cases for link text, link inode, and target content.

- [x] In the generated launcher, add a bounded `read_link` helper and a
  symlink branch that records the same runtime/manifest shape without following
  the link. Retain raw link text only process-locally for lexical closure
  validation after scan. Compare complete runtime and stable manifests before
  Node verification and exec.

- [x] Run:

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py \
  -k 'controlled_acp_launcher or explicit_authority or launcher_failure' -q
```

Expected: normal linked package returns `0.58.1`; every drift exits `126` with
empty stdout/stderr; the real designated node remains skipped.

- [x] Update HISTORY and commit:

```bash
git add tests/test_m2c_live_acceptance.py HISTORY.md
git commit -m "test: verify npm bin links in controlled ACP launcher"
```

## Task 5: Update v5 SOP and supersession evidence

**Files:**
- Modify: `docs/validation/phase3-m2c-live-acceptance-sop.md`
- Modify: `docs/superpowers/specs/2026-07-17-m2c-acp-package-entrypoint-authority-design.md`
- Modify: `docs/superpowers/specs/2026-07-17-m2c-acp-package-internal-symlink-authority-design.md`
- Modify: `docs/handoff/current-development-state.md`
- Modify: `HISTORY.md`
- Modify: this plan

- [x] Update the SOP to require authority v3/preflight v5 and describe only the
  closed `.bin` exception. It must still prohibit entrypoint symlinks, external
  targets, PATH fallback, install/login, raw output, and user tmux access.

- [x] Run strict/fake designated coverage and complete non-live/product sets:

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py \
  -k 'strict_preflight or designated_preflight or package_tree or controlled_acp_launcher' -q
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py -q
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_conversation_session.py tests/test_conversation_terminal_ui.py \
  tests/test_conversation_contracts.py tests/test_contracts.py \
  tests/test_cli_structured_output.py tests/test_dashboard.py \
  tests/test_provider_openai_compatible.py -q
```

- [x] Run compile/diff/`src/agentdeck`/leakage/process/root audits, update all
  evidence docs, and commit a new frozen implementation SHA.

## Task 6: Double full suite, real preflight, and live gate

**Files:**
- Modify: `HISTORY.md`
- Modify: `docs/handoff/current-development-state.md`
- Modify: `docs/validation/2026-07-17-phase3-m2c-four-stage-live.md`
- Modify: both roadmaps only after live PASS

- [ ] Run two complete suites on two fresh detached worktrees at the unchanged
  new frozen SHA, each with its own absolute `PYTHONPATH`; remove both and audit
  zero residue.

- [ ] Re-audit installed regular tools, package metadata, both internal links,
  their closed targets, modes, and ownership. Run exactly one real v5
  designated preflight using Leader `gpt-5.5`.

- [ ] Only on `ready=true`, empty blockers/failures, and authority v3, run
  exactly one same-SHA/model/digest four-stage live Mission.

- [ ] On PASS, close M2c and unlock M3. On any one-shot failure, record closed
  evidence and return to a new minimal root-cause/spec/plan/freeze cycle without
  retrying the same authority.

## Self-review

- [x] Every accepted link is confined to one exact npm `.bin` path shape.
- [x] Traversal never follows, opens, executes, or recurses through a link.
- [x] Link text, link runtime identity, target manifest identity, and target
  content are independently sealed.
- [x] The ACP entrypoint remains a regular non-symlink file.
- [x] v2/v4 evidence cannot authorize v3/v5 live execution.
- [x] Real preflight remains after a new freeze and two full suites.
- [x] No production source, install, login, merge, or push is authorized.
