# M2c Tool Authority Binding and Closed Preflight Diagnostics Design

**Date:** 2026-07-17  
**Status:** Human-approved design; implementation not started  
**Milestone:** Phase 3 M2c live acceptance closure  
**Scope:** M2c acceptance harness and SOP only

## 1. Purpose

AgentDeck's product model remains simple at the user level:

```text
Human request
  -> Leader decomposes the Mission
  -> AgentDeck governs and dispatches Workers
  -> ACP or tmux carries managed Worker interaction
  -> AgentDeck records progress, permissions, handoffs, and results
```

The current blocker is not a missing orchestration capability. It is an
acceptance-authority mismatch inside the M2c live harness.

Frozen implementation
`7a76ada81938be3ba0720a7c2f5a540b4beebb3e` passed one designated read-only
preflight with Leader model `gpt-5.5`, `ready=true`, and `blockers=[]`. The
separately authorized live node then stopped before project initialization with
`stage=live_acceptance`, `code=preflight_blocked`. Its preflight/live counts are
exactly `1/1`; neither execution may be repeated.

Static inspection established the boundary:

- the designated preflight used PATH-discovered installed tools;
- the live SOP supplied a disposable strict-basename tool mirror;
- live performed an internal preflight against that different executable set;
- the live failure collapsed the internal result to `preflight_blocked` and
  discarded the underlying allowlisted blockers.

The implementation must make designated preflight and live share one
content-addressed authority and must expose only closed, transcript-free
internal-preflight diagnostics.

## 2. North-star alignment

This slice supports the product north star without expanding product scope:

- AgentDeck remains the orchestration and governance kernel, not a provider or
  ACP pass-through.
- ACP remains the managed AgentDeck-to-Worker transport.
- tmux remains the visible fallback and human takeover surface.
- explicit human approval remains separate from readiness evidence.
- model and tool identity remain auditable execution provenance.
- no tool discovery, skill, transport, or diagnostic may become execution
  authorization by itself.

The slice does not add a production CLI, doctor command, provider feature,
transport, daemon capability, or user-facing setup flow. It only repairs the
M2c acceptance harness and its written SOP.

## 3. Scope

### 3.1 In scope

- `tests/test_m2c_live_acceptance.py`;
- `docs/validation/phase3-m2c-live-acceptance-sop.md`;
- M2c validation, handoff, history, spec, and plan documents;
- deterministic fake-tool tests;
- content-addressed tool authority for the M2c harness;
- a strict preflight-v3 response;
- closed per-tool/per-probe blocker projection;
- controlled Node and Claude Agent ACP package execution;
- a new frozen-SHA verification and authorization cycle after implementation.

### 3.2 Out of scope

- production `agentdeck` commands or contracts;
- Provider, ConversationSession, Mission, ProjectView, daemon, ACP transport,
  tmux backend, permission, takeover, handoff, or artifact behavior;
- automatic installation, download, authentication, login, upgrade, or global
  configuration;
- A2A, remote execution, global roaming, GUI, Workspace Client, or terminal
  emulator work;
- retrying any historical preflight or live authority;
- persisting absolute executable paths or raw process output.

## 4. Chosen approach

Use a content-addressed authority digest that can cross the separate preflight
and live processes.

The alternatives were rejected as follows:

- Retaining one temporary bundle until later live approval would bind the same
  path and inode, but would leave disposable resources alive for an unbounded
  human-review interval and make accidental deletion or mutation likely.
- Combining preflight and live in one process would remove the second explicit
  human approval and violate the existing governance boundary.

Content identity permits cleanup after preflight and deterministic
reconstruction before live. Runtime inode/owner/mode/mtime seals continue to
protect each individual process after the content authority is accepted.

## 5. Authority model

### 5.1 Contract

The harness introduces the internal contract:

```text
m2c-tool-authority/v1
```

Its canonical authority covers:

- Leader provider role `codex-cli`;
- exact Leader model ID;
- Codex executable content identity;
- Claude executable content identity;
- tmux executable content identity;
- Node executable content identity;
- Claude Agent ACP complete package-tree identity;
- fixed logical tool roles;
- the authority schema version.

The final digest is formatted as `sha256:<64-lowercase-hex>`.

### 5.2 Stable versus process-local identity

The cross-process authority digest excludes:

- absolute paths;
- inode/device;
- owner;
- mtime;
- xattrs;
- temporary-directory names.

Those fields cannot survive legitimate bundle reconstruction. The existing
process-local executable seal continues to validate absolute regular-file
identity, inode/device, owner, mode, size, mtime, and content hash within one
preflight or live process.

The two layers have distinct meanings:

```text
authority digest
  = same approved executable/package/model content across processes

runtime seal
  = no replacement or metadata drift inside the current process
```

Neither value is an authorization token.

### 5.3 Canonical digest input

Canonical authority serialization must be deterministic JSON using sorted
keys, compact separators, UTF-8, and a terminal newline. It includes only
bounded typed data:

```json
{
  "schema_version": "m2c-tool-authority/v1",
  "leader": {
    "provider": "codex-cli",
    "model": "gpt-5.5"
  },
  "tools": [
    {"name": "codex", "kind": "executable", "size": 1, "content_hash": "<64-lowercase-hex>"},
    {"name": "claude", "kind": "executable", "size": 1, "content_hash": "<64-lowercase-hex>"},
    {"name": "node", "kind": "executable", "size": 1, "content_hash": "<64-lowercase-hex>"},
    {"name": "tmux", "kind": "executable", "size": 1, "content_hash": "<64-lowercase-hex>"},
    {"name": "claude-agent-acp", "kind": "package-tree", "tree_hash": "<64-lowercase-hex>"}
  ]
}
```

The real values remain internal. The public preflight card exposes only the
final authority digest and readiness.

## 6. Claude Agent ACP package authority

### 6.1 Tree rules

The package root must be an absolute, non-symlink directory. Traversal must use
non-following metadata operations and reject:

- symlinks;
- sockets;
- FIFOs;
- block or character devices;
- paths escaping the package root;
- group- or world-writable package roots, directories, or files;
- unreadable or unstable files;
- a missing or misplaced entrypoint.

The required entrypoint is exactly:

```text
dist/claude-agent-acp
```

It must be a regular executable file inside the package root and must appear in
the tree manifest.

### 6.2 Tree hash

The tree manifest is sorted by normalized POSIX relative path. Each entry
contains:

- relative path;
- `directory` or `file` kind;
- file size for files;
- content SHA-256 for files;
- normalized executable-bit classification.

Absolute paths, inode, owner, mtime, and xattrs are excluded from the stable
tree hash. Safety-sensitive owner/mode/inode facts remain part of runtime
verification.

### 6.3 Controlled Node execution

Node becomes an explicit harness dependency through:

```text
AGENTDECK_M2C_NODE
```

The package root becomes explicit through:

```text
AGENTDECK_M2C_CLAUDE_ACP_PACKAGE
```

The live runtime creates controlled launchers for both Node and Claude Agent
ACP. The ACP launcher must:

1. verify the complete package tree;
2. verify the entrypoint runtime seal;
3. verify the Node runtime seal;
4. execute the sealed Node binary with the sealed ACP entrypoint and original
   arguments;
5. never resolve Node from ambient PATH or fall back to another executable.

The package tree is revalidated before and after designated preflight, before
and after authority comparison, before ACP session start, after ACP session
completion, and during final cleanup audit.

## 7. Designated preflight v3

### 7.1 Dedicated test node

Add a dedicated real-tool node:

```text
test_m2c_explicit_authority_preflight_is_read_only
```

It is the only designated M2c preflight that may authorize a later live
attempt. It requires all of:

```text
AGENTDECK_M2C_LEADER_MODEL
AGENTDECK_M2C_CODEX
AGENTDECK_M2C_CLAUDE
AGENTDECK_M2C_CLAUDE_ACP
AGENTDECK_M2C_CLAUDE_ACP_PACKAGE
AGENTDECK_M2C_NODE
AGENTDECK_M2C_TMUX
```

It must not fall back to PATH for any authority member. Existing PATH-based
preflight tests may remain as portable compatibility regressions, but their
output is not live execution authority.

### 7.2 Response contract

The strict response becomes:

```text
m2c-live-preflight/v3
```

It retains model, tool readiness, fixed timeout, blockers, and mode. It adds:

```json
{
  "tool_authority": {
    "schema_version": "m2c-tool-authority/v1",
    "digest": "sha256:<64-lowercase-hex>",
    "source": "explicit",
    "ready": true
  },
  "failures": []
}
```

The authority card does not expose member hashes, paths, modes, versions,
package filenames, environment values, or raw probe output.

The preflight may pass as a pytest contract while its product payload is
`ready=false`. Such a result is still BLOCKED and cannot authorize live.

### 7.3 Read-only boundary

The designated preflight snapshots and verifies all of:

- disposable project;
- isolated HOME;
- isolated XDG config/cache/data roots;
- isolated temporary root;
- every explicit executable input;
- the complete ACP package tree.

Any create, delete, content change, kind change, mode change, or directory
mutation during a probe produces `probe_wrote_files`. Probe processes and
descendants remain subject to bounded process-group and birth-identity cleanup.

The designated preflight must not call a provider, start an ACP session, start
tmux, initialize AgentDeck state, start a daemon, authenticate, or modify
global state.

## 8. Cross-process binding and live admission

### 8.1 Human-visible binding

After a ready preflight, the temporary bundle may be deleted. A later live
authorization must explicitly name:

- frozen implementation SHA;
- exact Leader model ID;
- exact `authority_digest`.

Before live, the same content bundle is reconstructed and passed through the
same explicit environment inputs plus:

```text
AGENTDECK_M2C_AUTHORITY_DIGEST
```

### 8.2 Live admission order

Live must perform these steps before creating the disposable live root:

1. validate the expected digest grammar;
2. load the exact model seal;
3. load every explicit tool/package dependency without PATH fallback;
4. compute the current authority digest;
5. compare the current and human-approved digests using exact equality;
6. fail with `preflight_authority_drift` on any mismatch.

Only after equality may live create the disposable root.

The live internal preflight receives the already constructed authority object.
It must not independently reread environment variables, resolve PATH, select a
different Node, or build another tool set.

## 9. Closed preflight diagnostics

### 9.1 Failure item

Preflight v3 emits zero or more exact items:

```json
{
  "tool": "codex",
  "probe": "help",
  "code": "probe_wrote_files"
}
```

Allowed `tool` values are:

```text
authority
leader-model
codex
claude
claude-agent-acp
node
tmux
```

Allowed `probe` values are:

```text
identity
package-tree
version
help
process-scope
filesystem-snapshot
binding
```

Existing blocker codes remain allowed. Add only:

```text
node_unavailable
claude_agent_acp_package_invalid
preflight_authority_drift
preflight_contract_invalid
```

Each version/help probe receives its own before/after root and authority
snapshot so `probe_wrote_files` is attributed to the exact tool and probe.
Global aggregate blockers remain unique and ordered by first occurrence.

### 9.2 Live failure projection

When the internal preflight is valid but blocked, live raises only:

```json
{
  "stage": "live_acceptance",
  "code": "preflight_blocked",
  "preflight_blockers": ["probe_wrote_files"],
  "preflight_failures": [
    {"tool": "codex", "probe": "help", "code": "probe_wrote_files"}
  ]
}
```

The projection must validate exact keys, types, enums, ordering, uniqueness,
and blocker/failure consistency before it enters an exception. Invalid
preflight output is replaced with:

```json
{"stage": "live_acceptance", "code": "preflight_contract_invalid"}
```

No raw exception, path, argv, output, stderr, prompt, transcript, environment,
auth material, or rejected value may be stringified.

## 10. Failure and cleanup semantics

- Missing explicit model/tool/package/Node input is a fixed preflight blocker.
- Missing, malformed, or mismatched expected authority digest stops before
  live-root creation with `preflight_authority_drift`.
- Package or Node drift never triggers PATH fallback, automatic recopy, install,
  or repair.
- Probe timeout, nonzero exit, missing capability, write, scope ambiguity, and
  residual process remain fail-closed.
- A primary failure remains primary if cleanup also fails; cleanup contributes
  only a fixed compact note.
- Interrupts and `BaseException` paths still run bounded cleanup and re-raise
  the identical interruption object when possible.
- A ready preflight is evidence, not live authorization.
- A passing pytest result with `ready=false` is BLOCKED, not PASS.
- A historical preflight, live attempt, model, SHA, or digest never carries
  forward to a new implementation authority.

## 11. TDD strategy

Implementation must begin with deterministic RED tests and must not invoke a
real Provider, designated preflight, ACP session, tmux session, daemon, or live
Mission.

### 11.1 Authority tests

- digest is independent of absolute path, inode, and mtime;
- digest changes for Codex, Claude, tmux, Node, model, ACP entry, or any package
  dependency content change;
- canonical ordering is deterministic;
- unsafe package kinds, symlinks, escape paths, writable modes, missing entry,
  and unstable reads are rejected;
- authority objects and cards reject extra or missing fields.

### 11.2 Preflight tests

- designated preflight requires every explicit input and never falls back to
  PATH;
- v3 payload has exact shape and a transcript-free authority card;
- project, isolation roots, executables, and package tree remain byte- and
  metadata-stable;
- each write, timeout, nonzero, missing capability, scope failure, residual, or
  identity drift maps to exact `tool + probe + code`;
- payload validator rejects invalid failure/blocker relationships.

### 11.3 Live admission tests

- expected digest is required and strictly parsed;
- matching reconstructed content is accepted despite path/inode/mtime changes;
- any authority member drift stops before live-root creation;
- internal preflight receives the same authority object by identity/value and
  performs no environment/PATH rediscovery;
- invalid internal payload maps to `preflight_contract_invalid`;
- valid blocked payload projects exact closed diagnostics;
- hostile values cannot enter exception strings, reprs, pytest reports, or
  durable evidence.

### 11.4 ACP runtime tests

- controlled ACP launcher uses only the sealed Node;
- ambient PATH Node is never selected;
- complete package tree is checked before and after execution;
- imported dependency drift blocks execution;
- source and launcher seals still reject replacement;
- cleanup removes all launcher, process, socket, and temporary-root resources.

## 12. Verification and execution order

The strict order is:

1. write deterministic RED tests;
2. implement the smallest harness-only GREEN change;
3. run focused authority/preflight/live-diagnostic fake tests;
4. run complete non-live `tests/test_m2c_live_acceptance.py`;
5. run related Conversation, Provider, schema, and contract suites;
6. run `python -m compileall -q src tests`;
7. run `git diff --check` and sensitive-marker/leakage scans;
8. perform independent spec-compliance and code-quality reviews;
9. freeze a new implementation commit;
10. run two independent full suites on the unchanged frozen SHA;
11. update SOP, validation, handoff, plan, and HISTORY;
12. commit evidence separately;
13. stop and request a new exact-SHA/model preflight authorization;
14. if and only if preflight is ready, stop again and request a separate
    exact-SHA/model/authority-digest live authorization.

No real designated preflight or live node may run during TDD or frozen
verification. Historical
`7a76ada81938be3ba0720a7c2f5a540b4beebb3e` remains exhausted at preflight/live
`1/1` and must never be rerun.

## 13. Commit boundaries

The detailed implementation plan must use small TDD commits. At minimum,
separate:

1. authority and package-tree domain helpers;
2. preflight-v3 response and closed failure attribution;
3. live digest admission and authority reuse;
4. controlled Node/ACP runtime binding;
5. SOP and durable evidence documentation;
6. final verification evidence.

Every implementation commit that changes user-visible or validation behavior
must update `HISTORY.md` in the same commit. Evidence commits do not become
implementation authority.

## 14. Acceptance criteria

The design is complete only when all of the following are true:

- designated preflight and live use one content-addressed authority;
- no designated authority member is discovered from PATH;
- Node and the complete ACP package tree are included;
- preflight v3 exposes only a digest, readiness, fixed blockers, and closed
  failure items;
- live requires the exact human-approved digest before creating a live root;
- internal preflight reuses the same authority object;
- each internal blocker identifies a fixed tool, probe, and code;
- diagnostics and default pytest reports remain transcript-free;
- preflight remains read-only across project, isolation roots, and input bundle;
- runtime Node/ACP selection cannot silently drift or fall back;
- fake-tool RED/GREEN, related suites, compile, diff, leakage checks, reviews,
  and two unchanged-SHA full suites pass;
- no real preflight or live runs before new explicit human authorization;
- M2c remains BLOCKED and M3 remains locked until a genuine four-stage live
  PASS.
