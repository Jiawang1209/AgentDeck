# M2c ACP Package Entrypoint Authority Design

**Date:** 2026-07-17
**Status:** Implemented through deterministic RED/GREEN; freeze verification pending
**Milestone:** Phase 3 M2c real four-stage acceptance closure
**Scope:** M2c acceptance harness and its durable evidence only

## 1. Purpose

AgentDeck's product north star requires a real Codex-and-Claude Mission to run
through the governed daemon, ACP/tmux transports, permission bridge, handoffs,
takeover, recovery, and final artifact verification. M2c cannot close on fake
tools or portable tests alone.

Frozen implementation
`fda1a69194e67b50afe0c2b9f4e7f29c195af400` passed two complete regression
suites, but its designated real preflight was intentionally not started. The
pre-command authority audit found that the harness and its approved spec
require a nonexistent fixed Claude Agent ACP entrypoint:

```text
dist/claude-agent-acp
```

The installed `@agentclientprotocol/claude-agent-acp@0.58.1` package declares
the official command in `package.json` instead:

```json
{
  "bin": {
    "claude-agent-acp": "dist/index.js"
  }
}
```

The preflight authorization for `fda1a691...` was not consumed: no designated
pytest node, provider, ACP session, tmux session, or daemon ran. The frozen SHA
must not be modified or used for live acceptance. This design creates a new
implementation authority that derives the ACP entrypoint from authenticated
package metadata.

## 2. North-star alignment

This correction preserves the approved product direction:

- ACP remains AgentDeck's preferred managed Worker transport.
- AgentDeck remains the Mission orchestration and governance authority, not a
  thin ACP launcher.
- Package metadata is context and identity evidence, never permission.
- The exact Worker adapter content and executable entry remain auditable.
- preflight and live use the same model, tools, package, Node, and entrypoint.
- no package is installed, upgraded, repaired, or silently substituted.
- real evidence, not synthetic success, determines M2c completion.

No production AgentDeck command, provider, Mission, daemon, ProjectView,
transport, permission, takeover, handoff, or artifact behavior changes in this
slice.

## 3. Root cause

The existing package-tree implementation correctly seals a complete,
non-following tree and revalidates it before execution. Its entrypoint
selection is wrong: the module-level `ACP_ENTRYPOINT` constant guesses a file
name instead of consuming the npm package's own command declaration.

Synthetic tests copied that assumption into `_fake_acp_package()` and created
`dist/claude-agent-acp`. Those tests proved that implementation and spec agreed;
they did not prove that either agreed with the real package. The actual package
contains executable `dist/index.js`, and its `package.json` maps
`bin["claude-agent-acp"]` to that file.

The root fix is therefore metadata-bound entrypoint selection. Creating a
wrapper, copying the package, renaming `index.js`, relaxing the package binding,
or hard-coding `dist/index.js` would hide the source error or recreate it at a
later package version.

## 4. Chosen approach

The package sealer reads the already tree-sealed `package.json`, derives the
official `claude-agent-acp` bin entry, canonicalizes its relative path, and
binds that path into package and cross-process authority.

Rejected alternatives:

1. **Hard-code `dist/index.js`.** Minimal today, but repeats the same brittle
   filename assumption.
2. **Trust `AGENTDECK_M2C_CLAUDE_ACP` alone.** Flexible, but cannot prove that
   the supplied file is the package's official command.
3. **Create a disposable compatibility wrapper.** Changes the authority under
   test and no longer validates the installed adapter package.

## 5. Package metadata contract

### 5.1 Required metadata

The package root must contain a regular, non-symlink `package.json` already
covered by the complete package manifest. The file must:

- be UTF-8 JSON;
- be no larger than 1 MiB;
- have a top-level JSON object;
- contain no duplicate object keys;
- declare `name` exactly as
  `@agentclientprotocol/claude-agent-acp`;
- declare an official command through one of the two npm-supported shapes:

```json
{"bin": "dist/index.js"}
```

or:

```json
{"bin": {"claude-agent-acp": "dist/index.js"}}
```

Other `bin` object keys may exist but cannot select the controlled command.
Missing, non-string, empty, ambiguous, or otherwise invalid metadata fails
closed as `claude_agent_acp_package_invalid`.

### 5.2 Entrypoint path safety

The selected value is interpreted as a POSIX package-relative path. The sealer
must:

- strip only an optional leading `./` through canonical POSIX normalization;
- reject absolute paths;
- reject empty paths, `.` and any `..` component;
- reject backslashes, NUL, or platform-dependent path forms;
- join path components to the already sealed package root without resolving
  symlinks;
- require the canonical relative path to exist exactly once in the package
  manifest as a regular file;
- require the file to be executable and not group- or world-writable;
- require its runtime seal to match the same manifest member.

The existing complete-tree rules continue to reject any symlink, special file,
unsafe mode, unreadable member, or unstable metadata anywhere in the package.

### 5.3 Explicit environment binding

`AGENTDECK_M2C_CLAUDE_AGENT_ACP_PACKAGE` is not introduced. The existing
variable remains:

```text
AGENTDECK_M2C_CLAUDE_ACP_PACKAGE
```

`AGENTDECK_M2C_CLAUDE_ACP` must identify the exact same sealed regular file as
the metadata-derived entrypoint. A different file, wrapper, symlink, or package
member fails closed as `claude_agent_acp_package_invalid`.

## 6. Authority versioning

The correction changes canonical digest semantics, so it must not silently
reuse `m2c-tool-authority/v1`.

The new internal schema is:

```text
m2c-tool-authority/v2
```

The Claude Agent ACP tool item adds the canonical entrypoint path:

```json
{
  "name": "claude-agent-acp",
  "kind": "package-tree",
  "tree_hash": "<64-lowercase-hex>",
  "entrypoint": "dist/index.js"
}
```

The full package tree still includes `package.json` content, so changing the
declared bin or its target changes the tree hash. Including `entrypoint`
separately makes the selected execution boundary explicit and prevents clients
from confusing v1 and v2 digest meaning.

The strict designated-preflight response becomes
`m2c-live-preflight/v4`. Its public `tool_authority` card retains the same four
fields but reports `schema_version=m2c-tool-authority/v2`. It still exposes no
paths, package members, member hashes, process output, prompt, or environment.

No v1 digest or v3 preflight result may authorize the corrected live node.

## 7. Runtime data flow

```text
explicit package root
  -> non-following complete tree seal
  -> sealed package.json bytes
  -> strict npm bin parsing
  -> canonical package-relative entrypoint
  -> exact entrypoint runtime seal
  -> m2c-tool-authority/v2 digest
  -> designated preflight v4
  -> later live digest comparison
  -> controlled Node exec of the same sealed entrypoint
```

`_PackageTreeSeal` stores the canonical relative entrypoint and its executable
seal. `_ToolAuthority` carries that one object. Designated preflight, internal
live preflight, authority serialization, controlled ACP launcher, session
startup, and cleanup revalidation consume it directly. No later phase reopens
`package.json` to make a second selection or falls back to PATH/npm resolution.

## 8. Failure and diagnostic semantics

Every metadata, path, manifest, binding, or runtime mismatch stays within the
existing closed failure vocabulary:

```text
tool=claude-agent-acp
probe=package-tree or binding
code=claude_agent_acp_package_invalid
```

The harness must not persist JSON parser errors, package content, selected
absolute paths, argv, stdout/stderr, Node errors, or exception text. Invalid
preflight shapes still collapse to `preflight_contract_invalid`. A blocked
preflight cannot authorize live.

## 9. TDD requirements

RED must be observed before implementation for at least these cases:

1. package-object bin selects executable `dist/index.js`;
2. package-string bin selects executable `dist/index.js`;
3. digest canonical input contains the selected relative entrypoint;
4. changing only the bin target changes authority identity;
5. missing `package.json`, oversized metadata, invalid UTF-8, invalid JSON,
   duplicate keys, wrong package name, missing bin, or wrong bin type is
   rejected;
6. absolute, empty, backslash, NUL, or parent-escaping entrypoint is rejected;
7. missing, symlink, special, unsafe-mode, or non-executable entrypoint is
   rejected;
8. explicit ACP environment path different from the metadata-selected file is
   rejected;
9. controlled launcher invokes sealed Node with the selected entrypoint;
10. entrypoint/package drift after sealing exits closed without output;
11. default regression keeps the real designated preflight and live nodes
    skipped unless explicitly enabled;
12. durable failure output contains none of the forbidden package or terminal
    material.

Existing fake packages must declare realistic `package.json` metadata. A RED
test must model the locally installed official `0.58.1` shape without reading
the user's installation during ordinary regression.

## 10. Documentation and evidence

The implementation commit set must update together:

- this design and its implementation plan;
- the existing tool-authority design to mark its fixed-entrypoint rule as
  superseded by this correction;
- the M2c live acceptance SOP;
- `HISTORY.md`;
- `docs/handoff/current-development-state.md`.

Durable evidence records only frozen SHA, exact model ID, schema versions,
ready/blocker/failure fields, final authority digest, test counts, bounded
durations, stage/result codes, artifact facts, and cleanup counts permitted by
the existing SOP.

## 11. Freeze and real acceptance sequence

The strict order is:

1. commit this approved design;
2. write and self-review the detailed implementation plan;
3. execute RED/GREEN tasks with frequent local commits;
4. run focused authority/package/launcher tests;
5. run the complete non-live M2c file and product regressions;
6. freeze one new implementation SHA;
7. run two full suites in fresh detached worktrees using absolute
   `PYTHONPATH` values;
8. audit implementation diff, process/resource residue, and durable leakage;
9. run exactly one designated real preflight on that frozen SHA with Leader
   `gpt-5.5` and the installed package's metadata-selected entrypoint;
10. require `ready=true`, `blockers=[]`, `failures=[]`, authority v2, and
    preflight v4;
11. run exactly one real four-stage Mission using the same SHA, model, explicit
    tool/package inputs, and returned digest;
12. close M2c only if implementation, review, revision, acceptance, permissions,
    ACP/tmux transports, takeover/return-control, handoffs, artifact bytes,
    ledger/trace/ProjectView agreement, and cleanup all pass.

Any code change after freeze creates a new SHA and restarts both full suites.
Any blocked real preflight or live result is recorded exactly once and is not
silently retried. A newly discovered implementation defect returns to a new
RED/GREEN and freeze cycle; login, install, global configuration, or permission
changes remain forbidden unless independently required and explicitly within
the user's authority.

## 12. Completion criteria

This correction is complete only when:

- official npm bin metadata selects the ACP entrypoint safely;
- authority v2 and preflight v4 are deterministic and closed;
- all focused, non-live, product, and two complete suites pass on one frozen
  SHA;
- the designated real preflight passes against the actual installed package;
- the real four-stage Mission passes once on the same SHA/model/digest;
- cleanup and durable-evidence audits pass;
- handoff, HISTORY, and validation evidence mark M2c complete and unlock M3.

Passing deterministic tests alone does not close M2c.
