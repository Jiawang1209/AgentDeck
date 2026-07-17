# M2c Tool Authority Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bind the designated M2c read-only preflight and the separately approved live Mission to one content-addressed Leader/tool authority, while preserving only closed, transcript-free preflight diagnostics.

**Architecture:** Keep the change entirely inside the M2c acceptance harness. Build a strict `m2c-tool-authority/v1` value from the explicit Leader model, four executable roles, sealed Node, and the complete Claude Agent ACP package tree; make the strict preflight return `m2c-live-preflight/v3`; require live admission to match the exact digest and reuse the already constructed authority object. Preserve the existing PATH-based preflight only as a non-authorizing portability regression, and do not change any production `src/agentdeck/**` behavior.

**Tech Stack:** Python 3.12 standard library (`dataclasses`, `hashlib`, `json`, `os`, `pathlib`, `stat`, `subprocess`), pytest, conda environment `agentdeck`, git.

---

## File map and fixed boundaries

- Modify `tests/test_m2c_live_acceptance.py`: authority dataclasses, package sealing, strict preflight v3, closed diagnostics, live admission, controlled ACP launcher, and deterministic tests.
- Modify `docs/validation/phase3-m2c-live-acceptance-sop.md`: exact explicit inputs, designated test node, digest handoff, separate approvals, and stop gates.
- Modify `docs/handoff/current-development-state.md`: current implementation/verification gate and prohibition on historical reruns.
- Modify `HISTORY.md`: one auditable entry in every behavior-changing commit.
- Modify this plan only to check completed tasks or correct an implementation-discovered factual error.
- Do not modify `src/agentdeck/**`, production contracts, provider behavior, ACP transport, tmux runtime, permissions, Mission semantics, global auth, or installed tools.
- Do not run `test_m2c_explicit_authority_preflight_is_read_only` against real tools and do not run the opt-in live test during Tasks 1-7.

## Task 1: Seal the complete authority deterministically

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py:65-220`
- Test: `tests/test_m2c_live_acceptance.py` (new authority unit tests beside existing executable-seal tests)
- Modify: `HISTORY.md`

- [x] **Step 1: Add RED tests for canonical content identity**

Add tests with these exact behavioral assertions:

```python
def test_m2c_tool_authority_digest_is_path_and_metadata_independent(tmp_path):
    left = _fake_explicit_authority(tmp_path / "left", model="gpt-5.5")
    right = _fake_explicit_authority(tmp_path / "right", model="gpt-5.5")
    os.utime(right.codex.path, ns=(right.codex.mtime_ns + 1, right.codex.mtime_ns + 1))
    right, failures = _load_explicit_tool_authority(
        _authority_environment(right.codex.path.parents[1], model="gpt-5.5")
    )
    assert failures == ()
    assert right is not None
    assert left.digest == right.digest
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", left.digest)


def test_m2c_tool_authority_digest_changes_for_every_bound_input(tmp_path):
    baseline = _fake_explicit_authority(tmp_path / "base", model="gpt-5.5")
    mutations = (
        {LEADER_MODEL_ENV: "gpt-5.5-review"},
        {"AGENTDECK_M2C_CODEX_CONTENT": b"codex-2"},
        {"AGENTDECK_M2C_CLAUDE_CONTENT": b"claude-2"},
        {"AGENTDECK_M2C_NODE_CONTENT": b"node-2"},
        {"AGENTDECK_M2C_TMUX_CONTENT": b"tmux-2"},
        {"AGENTDECK_M2C_ACP_CONTENT": b"acp-2"},
    )
    assert all(
        _fake_explicit_authority(tmp_path / f"changed-{index}", mutation=mutation).digest
        != baseline.digest
        for index, mutation in enumerate(mutations)
    )
```

The helper `_fake_explicit_authority()` must create real regular fake files and return the production `_ToolAuthority`; it must not mock the digest function.

- [x] **Step 2: Run the authority tests and verify RED**

Run:

```bash
conda run -n agentdeck pytest -q \
  tests/test_m2c_live_acceptance.py::test_m2c_tool_authority_digest_is_path_and_metadata_independent \
  tests/test_m2c_live_acceptance.py::test_m2c_tool_authority_digest_changes_for_every_bound_input
```

Expected: collection or runtime failure because `_ToolAuthority`, `_load_explicit_tool_authority`, and the new environment constants do not exist.

- [x] **Step 3: Add the closed constants and dataclasses**

Add the following contract names and immutable values near the existing harness constants:

```python
AUTHORITY_SCHEMA_VERSION = "m2c-tool-authority/v1"
STRICT_PREFLIGHT_SCHEMA_VERSION = "m2c-live-preflight/v3"
STRICT_PREFLIGHT_ENV = "AGENTDECK_M2C_STRICT_PREFLIGHT"
NODE_ENV = "AGENTDECK_M2C_NODE"
ACP_PACKAGE_ENV = "AGENTDECK_M2C_CLAUDE_ACP_PACKAGE"
AUTHORITY_DIGEST_ENV = "AGENTDECK_M2C_AUTHORITY_DIGEST"
AUTHORITY_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
ACP_ENTRYPOINT = PurePosixPath("dist/claude-agent-acp")


@dataclass(frozen=True)
class _PackageManifestEntry:
    path: str
    kind: str
    size: int | None
    content_hash: str | None
    executable: bool


@dataclass(frozen=True)
class _PackageTreeSeal:
    root: Path
    entries: tuple[_PackageManifestEntry, ...]
    tree_hash: str
    entrypoint: _ExecutableSeal


@dataclass(frozen=True)
class _ToolAuthority:
    leader_model: _LeaderModelSeal
    codex: _ExecutableSeal
    claude: _ExecutableSeal
    node: _ExecutableSeal
    tmux: _ExecutableSeal
    acp_package: _PackageTreeSeal
    digest: str

    def executable_seals(self) -> dict[str, _ExecutableSeal]:
        return {
            "codex": self.codex,
            "claude": self.claude,
            "node": self.node,
            "tmux": self.tmux,
            "claude-agent-acp": self.acp_package.entrypoint,
        }


@dataclass(frozen=True)
class _PreflightFailure:
    tool: str
    probe: str
    code: str
```

Use `PurePosixPath` from `pathlib`. Do not put any absolute path, inode, owner, mode, mtime, or xattr into the stable authority payload.

- [x] **Step 4: Implement the canonical authority digest**

Add these helpers with sorted tool roles and compact JSON:

```python
def _content_identity(seal: _ExecutableSeal) -> dict[str, object]:
    return {
        "kind": "executable",
        "size": seal.size,
        "content_hash": seal.content_hash,
    }


def _authority_digest_payload(authority: _ToolAuthority) -> dict[str, object]:
    seals = authority.executable_seals()
    return {
        "schema_version": AUTHORITY_SCHEMA_VERSION,
        "leader": {"provider": "codex-cli", "model": authority.leader_model.model},
        "tools": [
            {"name": name, **_content_identity(seals[name])}
            for name in ("codex", "claude", "node", "tmux")
        ] + [
            {
                "name": "claude-agent-acp",
                "kind": "package-tree",
                "tree_hash": authority.acp_package.tree_hash,
            }
        ],
    }


def _content_address(payload: dict[str, object]) -> str:
    encoded = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
```

Construct `_ToolAuthority` once with `digest=""`, then finalize it only through:

```python
def _finalize_authority(authority: _ToolAuthority) -> _ToolAuthority:
    if authority.digest != "":
        raise ValueError("authority already finalized")
    return dataclasses.replace(
        authority,
        digest=_content_address(_authority_digest_payload(authority)),
    )
```

Never serialize `authority.__dict__`.

- [x] **Step 5: Run focused GREEN tests**

Run the same two-node command from Step 2.

Expected: `2 passed` and no provider, tmux session, ACP process, preflight, or live execution.

- [x] **Step 6: Record and commit Task 1**

Prepend a `2026-07-17` HISTORY item stating that the harness gained deterministic content identity only and no external execution ran.

```bash
git add tests/test_m2c_live_acceptance.py HISTORY.md
git commit -m "test: seal M2c tool authority content"
```

## Task 2: Validate the ACP package tree and runtime seal

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py` near `_seal_executable()`
- Test: `tests/test_m2c_live_acceptance.py`
- Modify: `HISTORY.md`

- [x] **Step 1: Add RED package-tree tests**

Add parameterized tests covering: sorted manifest determinism, missing fixed entrypoint, root symlink, nested symlink, FIFO, group-writable directory, world-writable file, entrypoint without execute bit, and file mutation after sealing.

```python
@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("missing-entrypoint", "claude_agent_acp_package_invalid"),
        ("root-symlink", "claude_agent_acp_package_invalid"),
        ("nested-symlink", "claude_agent_acp_package_invalid"),
        ("fifo", "claude_agent_acp_package_invalid"),
        ("writable-directory", "claude_agent_acp_package_invalid"),
        ("writable-file", "claude_agent_acp_package_invalid"),
        ("non-executable-entrypoint", "claude_agent_acp_package_invalid"),
    ],
)
def test_m2c_package_tree_rejects_unsafe_entries(tmp_path, mutation, expected_code):
    root = _fake_acp_package(tmp_path / "package")
    _mutate_fake_package(root, mutation)
    seal, blocker = _seal_acp_package_tree(str(root))
    assert seal is None
    assert blocker == expected_code


def test_m2c_package_tree_manifest_is_sorted_and_path_independent(tmp_path):
    left, blocker = _seal_acp_package_tree(str(_fake_acp_package(tmp_path / "a")))
    right, other_blocker = _seal_acp_package_tree(str(_fake_acp_package(tmp_path / "b")))
    assert blocker is None and other_blocker is None
    assert left is not None and right is not None
    assert tuple(item.path for item in left.entries) == tuple(sorted(item.path for item in left.entries))
    assert left.tree_hash == right.tree_hash
```

- [x] **Step 2: Run package tests and verify RED**

Run:

```bash
conda run -n agentdeck pytest -q tests/test_m2c_live_acceptance.py -k 'package_tree'
```

Expected: failures because `_seal_acp_package_tree` and `_verify_package_tree_seal` are absent.

- [x] **Step 3: Implement non-following traversal and manifest hashing**

Implement:

```python
def _seal_acp_package_tree(value: str | None) -> tuple[_PackageTreeSeal | None, str | None]:
    if not value or not Path(value).is_absolute():
        return None, "claude_agent_acp_package_invalid"
    root = Path(value)
    try:
        root_meta = root.lstat()
        if not stat.S_ISDIR(root_meta.st_mode) or root.is_symlink() or root_meta.st_mode & 0o022:
            return None, "claude_agent_acp_package_invalid"
        entries = _read_safe_package_manifest(root)
        entry_path = root / Path(*ACP_ENTRYPOINT.parts)
        entrypoint = _seal_executable(str(entry_path))
        if entrypoint is None or not any(
            item.path == str(ACP_ENTRYPOINT) and item.kind == "file" and item.executable
            for item in entries
        ):
            return None, "claude_agent_acp_package_invalid"
        tree_payload = [dataclasses.asdict(item) for item in entries]
        tree_hash = hashlib.sha256(
            (json.dumps(tree_payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        ).hexdigest()
        return _PackageTreeSeal(root, entries, tree_hash, entrypoint), None
    except (OSError, RuntimeError, ValueError):
        return None, "claude_agent_acp_package_invalid"
```

`_read_safe_package_manifest()` must use `lstat()`, reject every non-directory/non-regular-file type, reject mode `& 0o022`, read files through `O_NOFOLLOW`, compare pre/read/post identity, normalize relative paths with `PurePosixPath`, and return directories and files in one sorted tuple. `_verify_package_tree_seal()` must reseal and require exact equality.

- [x] **Step 4: Add explicit authority loader**

Implement a pure mapping-based loader so tests and live do not reread ambient environment after admission:

```python
def _load_explicit_tool_authority(
    environ: Mapping[str, str],
) -> tuple[_ToolAuthority | None, tuple[_PreflightFailure, ...]]:
    model, model_blocker = _seal_leader_model_input(environ.get(LEADER_MODEL_ENV))
    # Seal CODEX, CLAUDE, NODE, TMUX by exact absolute path.
    # Seal ACP_PACKAGE_ENV as a complete package; do not accept a standalone ACP script.
    # Return only closed _PreflightFailure values on rejection.
```

Use exact fields rather than `**dict` construction. `AGENTDECK_M2C_CLAUDE_ACP` remains accepted only by the legacy portability preflight; strict authority uses `ACP_PACKAGE_ENV`.

- [x] **Step 5: Run package and authority GREEN tests**

Run:

```bash
conda run -n agentdeck pytest -q tests/test_m2c_live_acceptance.py -k 'tool_authority or package_tree'
```

Expected: all selected tests pass.

- [x] **Step 6: Commit Task 2**

Update HISTORY with package safety rules and the fact that no external tool ran.

```bash
git add tests/test_m2c_live_acceptance.py HISTORY.md
git commit -m "test: validate M2c ACP package authority"
```

## Task 3: Introduce strict preflight v3 and closed per-probe failures

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py` near `_live_preflight()` and `_validate_preflight_payload()`
- Test: `tests/test_m2c_live_acceptance.py`
- Modify: `HISTORY.md`

- [x] **Step 1: Add RED validator and attribution tests**

Define the exact closed types in tests:

```python
PREFLIGHT_FAILURE_TOOLS = frozenset(
    {"authority", "leader-model", "codex", "claude", "claude-agent-acp", "node", "tmux"}
)
PREFLIGHT_FAILURE_PROBES = frozenset(
    {"identity", "package-tree", "version", "help", "process-scope", "filesystem-snapshot", "binding"}
)


def test_m2c_strict_preflight_v3_rejects_open_or_inconsistent_fields():
    payload = _valid_strict_preflight_payload()
    payload["failures"] = [{"tool": "codex", "probe": "stderr", "code": "raw output"}]
    assert _validate_strict_preflight_payload(payload)


@pytest.mark.parametrize(
    ("tool", "probe", "blocker"),
    [
        ("codex", "version", "codex_unavailable"),
        ("claude", "help", "claude_native_schema_unavailable"),
        ("claude-agent-acp", "package-tree", "claude_agent_acp_package_invalid"),
        ("node", "identity", "node_unavailable"),
        ("tmux", "process-scope", "probe_scope_unverified"),
        ("authority", "filesystem-snapshot", "probe_wrote_files"),
    ],
)
def test_m2c_strict_preflight_attributes_failures(tool, probe, blocker):
    payload = _strict_fake_preflight(failure=(tool, probe, blocker))
    assert {"tool": tool, "probe": probe, "code": blocker} in payload["failures"]
    assert blocker in payload["blockers"]
    assert payload["ready"] is False
```

- [x] **Step 2: Verify RED**

Run:

```bash
conda run -n agentdeck pytest -q tests/test_m2c_live_acceptance.py -k 'strict_preflight'
```

Expected: failures because v3 structures and validator do not exist.

- [x] **Step 3: Add the v3 authority card and closed enums**

Reuse the `_PreflightFailure` type established in Task 1 and add:

```python
def _preflight_failure_card(value: _PreflightFailure) -> dict[str, str]:
    return {"tool": value.tool, "probe": value.probe, "code": value.code}


def _authority_card(authority: _ToolAuthority) -> dict[str, object]:
    return {
        "schema_version": AUTHORITY_SCHEMA_VERSION,
        "digest": authority.digest,
        "source": "explicit",
        "ready": True,
    }
```

Extend `BLOCKER_CODES` only with:

```python
"node_unavailable"
"claude_agent_acp_package_invalid"
"preflight_authority_drift"
"preflight_contract_invalid"
```

- [x] **Step 4: Implement attributed probes without transcript persistence**

Add one wrapper whose result contains no bytes:

```python
def _run_attributed_probe(
    *,
    tool: str,
    probe: str,
    seal: _ExecutableSeal,
    args: tuple[str, ...],
    cwd: Path,
    env: dict[str, str],
    snapshot_roots: tuple[Path, ...],
) -> tuple[_ProbeOutcome, tuple[_PreflightFailure, ...]]:
    before = _roots_snapshot(snapshot_roots)
    outcome = _bounded_probe(seal, args, cwd=cwd, env=env)
    failures: list[_PreflightFailure] = []
    if outcome.blocker is not None:
        failures.append(_PreflightFailure(tool, _probe_for_blocker(probe, outcome.blocker), outcome.blocker))
    if _roots_snapshot(snapshot_roots) != before:
        failures.append(_PreflightFailure(tool, "filesystem-snapshot", "probe_wrote_files"))
    return outcome, tuple(failures)
```

The strict payload may consume `outcome.output` transiently to derive option names and a sanitized version, but it must never put output, stderr, argv, path, prompt, environment, exception text, or bytes into `failures`, `_LiveHarnessFailure`, docs evidence, or pytest parameter IDs.

- [x] **Step 5: Implement the strict v3 preflight and validator**

Create `_strict_live_preflight(project, authority, isolation=None)` that:

1. receives an already built `_ToolAuthority`;
2. snapshots project plus all isolation roots;
3. verifies every executable seal and the ACP package seal;
4. probes Codex version/help, Claude version/help, ACP version through sealed Node, and tmux version;
5. returns the exact keys below;
6. runs `_validate_strict_preflight_payload()` before returning; an invalid internal payload becomes one valid fail-closed payload with only `preflight_contract_invalid`.

```python
{
    "schema_version": STRICT_PREFLIGHT_SCHEMA_VERSION,
    "mode": "m2c_live_preflight",
    "ready": not blockers,
    "probe_timeout_seconds": PROBE_TIMEOUT_SECONDS,
    "leader_model": {
        "provider": "codex-cli",
        "model": authority.leader_model.model,
        "source": "explicit",
        "ready": not any(item.code.startswith("leader_model_") for item in failures),
    },
    "tool_authority": _authority_card(authority),
    "tools": tool_cards,
    "blockers": blockers,
    "failures": [failure.card() for failure in failures],
}
```

Keep the old PATH-discovered `_live_preflight()` and v2 validator only for existing portability tests. Rename it to `_portable_live_preflight_v2()` if needed so it cannot be called by live admission.

- [x] **Step 6: Run focused GREEN and legacy compatibility**

Run:

```bash
conda run -n agentdeck pytest -q tests/test_m2c_live_acceptance.py \
  -k 'strict_preflight or live_preflight_is_read_only or preflight_payload'
```

Expected: selected tests pass; the opt-in live node remains skipped.

- [x] **Step 7: Commit Task 3**

Update HISTORY with strict v3 fields and closed allowlists.

```bash
git add tests/test_m2c_live_acceptance.py HISTORY.md
git commit -m "test: expose closed M2c preflight v3 diagnostics"
```

## Task 4: Bind live admission to the exact digest before root creation

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py` near `_explicit_live_paths()`, `_run_live_acceptance()`, and `_run_live_acceptance_in_project_guarded()`
- Test: `tests/test_m2c_live_acceptance.py`
- Modify: `HISTORY.md`

- [x] **Step 1: Add RED admission-order and reuse tests**

```python
@pytest.mark.parametrize("value", [None, "", "sha256:nope", "sha256:" + "A" * 64])
def test_m2c_live_rejects_missing_or_invalid_expected_authority_before_root(tmp_path, monkeypatch, value):
    if value is None:
        monkeypatch.delenv(AUTHORITY_DIGEST_ENV, raising=False)
    else:
        monkeypatch.setenv(AUTHORITY_DIGEST_ENV, value)
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **_: pytest.fail("root created"))
    with pytest.raises(_LiveHarnessFailure, match='"code": "preflight_authority_drift"'):
        _run_live_acceptance()


def test_m2c_live_rejects_digest_mismatch_before_root(tmp_path, monkeypatch):
    authority = _fake_explicit_authority(tmp_path / "authority")
    _install_authority_environment(monkeypatch, authority, expected="sha256:" + "0" * 64)
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **_: pytest.fail("root created"))
    with pytest.raises(_LiveHarnessFailure, match='"code": "preflight_authority_drift"'):
        _run_live_acceptance()


def test_m2c_internal_preflight_reuses_admitted_authority_without_environment_reads(tmp_path, monkeypatch):
    authority = _fake_explicit_authority(tmp_path / "authority")
    monkeypatch.setattr(os, "getenv", lambda *_: pytest.fail("ambient environment reread"))
    payload = _run_live_acceptance_in_project_guarded(
        authority,
        tmp_path / "live",
        preflight_runner=lambda project, supplied: _ready_preflight_for(supplied),
        stop_after_preflight=True,
    )
    assert payload["tool_authority"]["digest"] == authority.digest
```

- [x] **Step 2: Verify RED**

Run:

```bash
conda run -n agentdeck pytest -q tests/test_m2c_live_acceptance.py -k 'expected_authority or admitted_authority or digest_mismatch'
```

Expected: failures because live still accepts a paths dict and creates the root before digest binding.

- [x] **Step 3: Replace `_explicit_live_paths()` with one admission function**

Implement:

```python
def _admit_live_tool_authority(environ: Mapping[str, str]) -> _ToolAuthority:
    expected = environ.get(AUTHORITY_DIGEST_ENV)
    if expected is None or AUTHORITY_DIGEST_PATTERN.fullmatch(expected) is None:
        raise _live_failure("preflight_authority_drift")
    authority, failures = _load_explicit_tool_authority(environ)
    if authority is None:
        raise _live_failure(
            "preflight_blocked",
            preflight_blockers=tuple(dict.fromkeys(item.code for item in failures)),
            preflight_failures=failures,
        )
    if not secrets.compare_digest(authority.digest, expected):
        raise _live_failure("preflight_authority_drift")
    return authority
```

Call it at the top of `_run_live_acceptance()` before `tempfile.mkdtemp()`. Change guarded runner signatures from `(paths, parent, leader_model)` to `(authority, parent)`. Do not re-read Leader model or tool environment inside the guarded runner.

- [x] **Step 4: Make internal preflight consume the same object**

Inside `_run_live_acceptance_in_project_guarded()` call the strict preflight
with the same object and keep the current compact blocker until Task 5 installs
the closed projection:

```python
preflight = _strict_live_preflight(root, authority)
if _validate_strict_preflight_payload(preflight):
    raise _live_failure("preflight_contract_invalid")
if preflight["blockers"] == ["preflight_contract_invalid"]:
    raise _live_failure("preflight_contract_invalid")
if not preflight["ready"]:
    raise _live_failure("preflight_blocked")
```

Task 5 replaces the final compact blocked branch with validated
`preflight_blockers` / `preflight_failures`; keeping serialization in one task
prevents an intermediate open diagnostic shape.

Project initialization, checkout copy, config creation, daemon admission, and Mission work must remain after this gate.

- [x] **Step 5: Run GREEN admission tests and setup cleanup regressions**

Run:

```bash
conda run -n agentdeck pytest -q tests/test_m2c_live_acceptance.py \
  -k 'expected_authority or admitted_authority or digest_mismatch or live_setup_cleanup or preflight_blocked'
```

Expected: selected tests pass; no `/tmp/agentdeck-m2c-live-*` test residue.

- [x] **Step 6: Commit Task 4**

Update HISTORY with exact admission order and same-object reuse.

```bash
git add tests/test_m2c_live_acceptance.py HISTORY.md
git commit -m "test: bind M2c live admission to preflight authority"
```

## Task 5: Preserve only closed live preflight diagnostics

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py` near `_live_failure()`
- Test: `tests/test_m2c_live_acceptance.py`
- Modify: `HISTORY.md`

- [x] **Step 1: Add RED diagnostic closure tests**

```python
def test_m2c_live_failure_projects_closed_preflight_diagnostics():
    failure = _live_failure(
        "preflight_blocked",
        preflight_blockers=("codex_unavailable", "probe_wrote_files"),
        preflight_failures=(
            _PreflightFailure("codex", "version", "codex_unavailable"),
            _PreflightFailure("codex", "filesystem-snapshot", "probe_wrote_files"),
        ),
    )
    payload = json.loads(str(failure))
    assert payload == {
        "stage": "live_acceptance",
        "code": "preflight_blocked",
        "preflight_blockers": ["codex_unavailable", "probe_wrote_files"],
        "preflight_failures": [
            {"tool": "codex", "probe": "version", "code": "codex_unavailable"},
            {"tool": "codex", "probe": "filesystem-snapshot", "code": "probe_wrote_files"},
        ],
    }


def test_m2c_live_failure_rejects_preflight_transcript_and_path_data():
    secret = "/private/secret prompt stderr"
    with pytest.raises(ValueError):
        _live_failure(
            "preflight_blocked",
            preflight_blockers=("codex_unavailable",),
            preflight_failures=({"tool": "codex", "probe": "version", "code": secret},),
        )
    assert secret not in repr(_PreflightFailure("codex", "version", "codex_unavailable"))
```

- [x] **Step 2: Verify RED**

Run:

```bash
conda run -n agentdeck pytest -q tests/test_m2c_live_acceptance.py -k 'projects_closed_preflight or rejects_preflight_transcript'
```

Expected: `_live_failure()` rejects the new keyword arguments.

- [x] **Step 3: Add closed validation and serialization**

Add `preflight_blockers` and `preflight_failures` keyword-only parameters. Validate exact types, enum membership, uniqueness, and consistency before adding them to `diagnostic`. For any invalid internal payload, replace both collections with:

```python
preflight_blockers = ("preflight_contract_invalid",)
preflight_failures = (
    _PreflightFailure("authority", "binding", "preflight_contract_invalid"),
)
```

Do not call `str(exc)`, serialize arbitrary mappings, or preserve unknown values.

- [x] **Step 4: Run GREEN plus default pytest leakage checks**

Run:

```bash
conda run -n agentdeck pytest -q tests/test_m2c_live_acceptance.py \
  -k 'preflight and (diagnostic or transcript or redaction or default_pytest)'
```

Expected: selected tests pass and sentinel prompt/path/output bytes are absent from captured pytest reports.

- [x] **Step 5: Commit Task 5**

Update HISTORY with the exact diagnostic fields and fail-closed replacement.

```bash
git add tests/test_m2c_live_acceptance.py HISTORY.md
git commit -m "test: close M2c internal preflight diagnostics"
```

## Task 6: Execute Claude Agent ACP only through sealed Node and sealed package

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py` near `_write_controlled_launcher()` and live runtime setup
- Test: `tests/test_m2c_live_acceptance.py`
- Modify: `HISTORY.md`

- [x] **Step 1: Add RED controlled-launcher tests**

```python
def test_m2c_controlled_acp_launcher_executes_sealed_node_with_fixed_entrypoint(tmp_path):
    authority = _fake_explicit_authority(tmp_path / "authority")
    launcher = _write_controlled_acp_launcher(authority, tmp_path / "bin")
    text = launcher.path.read_text(encoding="utf-8")
    assert str(authority.node.path) in text
    assert str(authority.acp_package.root / Path(*ACP_ENTRYPOINT.parts)) in text
    assert "shutil.which" not in text
    assert "#!/usr/bin/env" not in text


@pytest.mark.parametrize("target", ["node", "entrypoint", "package-file"])
def test_m2c_controlled_acp_launcher_rejects_post_seal_drift(tmp_path, target):
    authority = _fake_explicit_authority(tmp_path / "authority")
    launcher = _write_controlled_acp_launcher(authority, tmp_path / "bin")
    _mutate_authority_target(authority, target)
    completed = subprocess.run([str(launcher.path), "--version"], capture_output=True)
    assert completed.returncode == 126
    assert completed.stdout == b""
    assert completed.stderr == b""
```

- [x] **Step 2: Verify RED**

Run:

```bash
conda run -n agentdeck pytest -q tests/test_m2c_live_acceptance.py -k 'controlled_acp_launcher'
```

Expected: failures because the specialized launcher does not exist.

- [x] **Step 3: Implement the specialized launcher**

`_write_controlled_acp_launcher(authority, destination)` must create `claude-agent-acp` with mode `0500`. Its generated Python program must:

1. contain exact sealed Node facts;
2. contain the exact package root and fixed entrypoint relative path;
3. re-walk the package with the same non-following rules and reproduce `tree_hash`;
4. revalidate the entrypoint runtime seal;
5. revalidate Node immediately before execution;
6. call `os.execve(NODE_PATH, [NODE_PATH, ENTRYPOINT, *sys.argv[1:]], dict(os.environ))`;
7. exit `126` without text on every verification or OS error.

Reuse small source strings for the facts/hash algorithm, but do not import the repository test module from the generated launcher and do not use ambient PATH.

- [x] **Step 4: Wire runtime launchers**

Create ordinary controlled launchers for Codex, Claude, and tmux; create the specialized ACP launcher; do not create or expose an ambient `node` launcher in PATH. Include source Node, ACP entrypoint, package tree, and all generated launchers in the process-local verification set.

- [x] **Step 5: Run GREEN and cleanup regressions**

Run:

```bash
conda run -n agentdeck pytest -q tests/test_m2c_live_acceptance.py \
  -k 'controlled_acp_launcher or executable_drift or process_cleanup or live_setup_cleanup'
```

Expected: selected tests pass with no residual process or test root.

- [x] **Step 6: Commit Task 6**

Update HISTORY with sealed Node/package runtime behavior.

```bash
git add tests/test_m2c_live_acceptance.py HISTORY.md
git commit -m "test: control M2c ACP Node execution"
```

## Task 7: Add the designated read-only authority node and update the SOP

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py` near `test_m2c_live_preflight_is_read_only`
- Modify: `docs/validation/phase3-m2c-live-acceptance-sop.md`
- Modify: `docs/handoff/current-development-state.md`
- Modify: `HISTORY.md`

- [x] **Step 1: Add the dedicated strict node without executing it against real tools**

Add a dedicated explicit opt-in guard and node:

```python
@pytest.mark.skipif(
    os.environ.get(STRICT_PREFLIGHT_ENV) != "1",
    reason="requires separately authorized explicit M2c authority preflight",
)
def test_m2c_explicit_authority_preflight_is_read_only(tmp_path):
    authority, failures = _load_explicit_tool_authority(os.environ)
    assert failures == ()
    assert authority is not None
    before = _roots_snapshot((tmp_path,))
    payload = _strict_live_preflight(tmp_path, authority)
    assert _validate_strict_preflight_payload(payload) == []
    assert payload["tool_authority"]["digest"] == authority.digest
    assert _roots_snapshot((tmp_path,)) == before
    print(json.dumps(payload, sort_keys=True))
```

It must remain a normal collected node, skipped unless `AGENTDECK_M2C_STRICT_PREFLIGHT=1`, whose real execution is controlled by the SOP and explicit human authorization. During implementation, exercise it only with deterministic fake tools via a subprocess test that supplies a temporary environment, sets the guard to `1`, and confirms no writes outside the isolated roots.

- [x] **Step 2: Add deterministic subprocess RED/GREEN coverage**

Run the exact node in a copied temporary test module with fake executable/package inputs and assert:

```python
assert completed.returncode == 0
payload = json.loads(next(line for line in completed.stdout.splitlines() if line.startswith("{")))
assert payload["schema_version"] == "m2c-live-preflight/v3"
assert payload["ready"] is True
assert payload["blockers"] == []
assert payload["failures"] == []
assert payload["tool_authority"]["source"] == "explicit"
```

Do not point this implementation test at installed Codex, Claude, Node, tmux, or Claude Agent ACP.

- [x] **Step 3: Rewrite the SOP inputs and commands**

The designated preflight section must require exact absolute values for:

```text
AGENTDECK_M2C_LEADER_MODEL
AGENTDECK_M2C_CODEX
AGENTDECK_M2C_CLAUDE
AGENTDECK_M2C_NODE
AGENTDECK_M2C_TMUX
AGENTDECK_M2C_CLAUDE_ACP_PACKAGE
AGENTDECK_M2C_STRICT_PREFLIGHT=1
```

The preflight command must select only `test_m2c_explicit_authority_preflight_is_read_only`. The operator records only frozen SHA, exact model, schema version, `ready`, closed blockers/failures, and authority digest. After cleanup, a later live authorization must name frozen SHA, exact model, and exact digest; the live environment additionally supplies `AGENTDECK_M2C_AUTHORITY_DIGEST`. Never copy raw stdout/stderr, paths, prompts, terminal content, or executable hashes into durable evidence.

- [x] **Step 4: Update handoff and HISTORY**

Handoff must say implementation is not yet frozen until Task 8 completes, historical SHA `7a76ada81938be3ba0720a7c2f5a540b4beebb3e` remains exhausted at `1/1`, and no new real preflight/live is authorized. HISTORY must record the SOP/control change.

- [x] **Step 5: Run documentation and non-live harness checks**

Run:

```bash
conda run -n agentdeck pytest -q tests/test_m2c_live_acceptance.py -k 'not test_m2c_live_four_stage_mission'
conda run -n agentdeck python -m compileall -q tests/test_m2c_live_acceptance.py
rg -n 'm2c-live-preflight/v3|AGENTDECK_M2C_AUTHORITY_DIGEST|test_m2c_explicit_authority_preflight_is_read_only' \
  docs/validation/phase3-m2c-live-acceptance-sop.md docs/handoff/current-development-state.md HISTORY.md
```

Expected: complete non-live M2c selection passes; compile exits `0`; all required control terms are found.

- [x] **Step 6: Commit Task 7**

```bash
git add tests/test_m2c_live_acceptance.py \
  docs/validation/phase3-m2c-live-acceptance-sop.md \
  docs/handoff/current-development-state.md HISTORY.md
git commit -m "docs: bind M2c authority acceptance procedure"
```

## Task 8: Review, freeze, and verify without real preflight or live execution

**Files:**
- Modify: `docs/handoff/current-development-state.md`
- Modify: `HISTORY.md`
- Modify: this plan (check completed boxes only)

- [x] **Step 1: Run the focused authority matrix**

```bash
conda run -n agentdeck pytest -q tests/test_m2c_live_acceptance.py \
  -k 'tool_authority or package_tree or strict_preflight or expected_authority or admitted_authority or controlled_acp_launcher or preflight_diagnostic'
```

Expected: all selected deterministic nodes pass.

- [x] **Step 2: Run the entire non-live M2c file**

```bash
conda run -n agentdeck pytest -q tests/test_m2c_live_acceptance.py
```

Expected: every deterministic node passes and the single opt-in four-stage live node is skipped. If the new designated real-tool preflight node needs a guard to avoid accidental ordinary-suite execution, use an explicit separate marker/environment gate that skips unless the SOP authorization variable is set; fake-tool subprocess coverage must still execute its body.

- [x] **Step 3: Run product and contract regressions**

```bash
conda run -n agentdeck pytest -q \
  tests/test_conversation_session.py \
  tests/test_conversation_terminal_ui.py \
  tests/test_conversation_contracts.py \
  tests/test_contracts.py \
  tests/test_cli_structured_output.py \
  tests/test_dashboard.py \
  tests/test_provider_openai_compatible.py
```

Expected: all selected production regressions pass, proving the harness-only change did not alter product behavior.

- [x] **Step 4: Audit scope, leakage, and residues**

```bash
git diff d488c2e6..HEAD --check
test -z "$(git diff d488c2e6..HEAD --name-only -- src/agentdeck)"
rg -n 'prompt|stderr|stdout|argv|environment|absolute path|absolute_path' \
  docs/validation/phase3-m2c-live-acceptance-sop.md \
  docs/handoff/current-development-state.md
ps -Ao pid=,comm=,args= | rg 'pytest.*test_m2c_live_acceptance|agentdeck.*daemon' | rg -v 'rg |zsh -lc' || true
find /tmp -maxdepth 1 -name 'agentdeck-m2c-live-*' -print
```

Expected: diff check passes, no `src/agentdeck` changes, no forbidden durable transcript field, and no current-run process/root residue. Pre-existing unrelated paths are documented and left untouched.

- [x] **Step 5: Freeze the implementation commit**

Update HISTORY and handoff with focused/non-live results but state that the two full suites have not yet run. Then:

```bash
git add docs/handoff/current-development-state.md HISTORY.md \
  docs/superpowers/plans/2026-07-17-m2c-tool-authority-binding.md
git commit -m "docs: freeze M2c authority binding implementation"
git rev-parse HEAD
```

Record the returned SHA as `FROZEN_SHA`. No later implementation edit may occur without creating a new frozen SHA and restarting both full suites.

- [ ] **Step 6: Run full suite 1 on an isolated checkout**

Create a detached temporary worktree at `FROZEN_SHA`, install nothing, and run:

```bash
conda run -n agentdeck pytest -q
```

Expected: full suite passes with only known opt-in skips. Record counts and duration; remove the detached worktree.

- [ ] **Step 7: Run full suite 2 on a fresh isolated checkout**

Create a second fresh detached worktree at the same `FROZEN_SHA` and run the identical command.

Expected: full suite passes with the same code SHA and expected skip set. Record counts and duration; remove the detached worktree.

- [ ] **Step 8: Commit verification evidence and stop**

Update HISTORY and handoff with frozen SHA, both full-suite results, compile/diff/scope/leakage/cleanup results, and these exact gates:

```text
M2c remains BLOCKED.
M3 remains locked.
No real designated preflight or live Mission ran during implementation.
Next action requires separate human authorization naming FROZEN_SHA and exact Leader model for one read-only designated preflight.
If that preflight returns ready=true, blockers=[], failures=[], a later live authorization must separately name FROZEN_SHA, model, and exact authority digest.
```

Commit only the evidence documents:

```bash
git add docs/handoff/current-development-state.md HISTORY.md \
  docs/superpowers/plans/2026-07-17-m2c-tool-authority-binding.md
git commit -m "docs: record M2c authority binding verification"
```

Stop. Do not execute the designated real-tool preflight, do not execute the live node, do not merge, and do not push.

## Self-review checklist

- [x] Every requirement in spec sections 5-10 maps to Tasks 1-6.
- [x] Spec section 11 maps to the RED/GREEN steps in Tasks 1-7.
- [x] Spec sections 12-14 map to Task 8 and the final stop gate.
- [x] Authority digest binds model, Codex, Claude, Node, tmux, and full ACP package content.
- [x] Absolute paths and runtime metadata remain excluded from stable digest but retained in process-local seals.
- [x] Strict preflight failures contain only `tool`, `probe`, and `code` from closed allowlists.
- [x] Live validates the expected digest before root creation and reuses the same authority object.
- [x] Controlled ACP execution uses exact sealed Node and fixed package entrypoint without ambient PATH.
- [x] Historical `7a76ada81938be3ba0720a7c2f5a540b4beebb3e` preflight/live is never rerun.
- [x] No task changes production `src/agentdeck/**` behavior.
- [x] No task runs real preflight/live before a new, explicit human authorization.
