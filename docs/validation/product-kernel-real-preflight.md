# Product Kernel Real Preflight — PASS evidence

Date: 2026-07-22 · Task 35 Step 5-6

## Verdict

The authorized read-only real preflight returned **`ready=true`** with an empty
blocker list at frozen commit `da8d7a8c30c27fc81ad1c9b26182b126f4990b01`.

This proves only environment readiness for a real Golden Product Mission. It is
NOT a live Mission: no Leader/Worker prompt, no ACP session turn, no tmux
attach, no source generation, and no project-state mutation occurred. Running
the four-Worker Golden Product Mission (Task 36) remains a separate, explicitly
human-authorized step.

## Authorized inputs (human-named)

| Field | Value |
| --- | --- |
| frozen commit | `da8d7a8c30c27fc81ad1c9b26182b126f4990b01` |
| Leader backend | `codex-cli` |
| model | `gpt-5.5` |
| permission profile | `full-access` (recorded only; the preflight does not act on it) |
| target manifest | `tests/product_kernel/fixtures/reference_homepage/target-manifest.json` |
| target manifest hash | `sha256:5afa4ebf8b63dde1107b22bf24200f484d9f6aea9e1a5f7843dc88d0bc12d374` |
| authority digest | `sha256:eb0233576233d0f7b1a51ab17d7043398b2aa125cb34add4720f0584558e48ef` |
| project root | the frozen worktree (no disposable project) |

The authority digest is the deterministic, reproducible content hash
`sha256("\n".join([commit, leader, model, permission, target_manifest_hash]))`.
It binds exactly what was authorized and can be recomputed from the table above.
Integrity was verified before execution: HEAD equalled the authorized commit
with no source drift, and the resolved non-secret values were printed for
comparison before the run.

## Read-only guarantees

The preflight installs nothing, authenticates nothing, selects no fallback,
generates no source, and sends no model prompt. It performs only bounded,
read-only inspection: `codex --version` + the bounded `codex app-server` bridge
schema probe, a passive `claude auth status` login check, PATH resolution of the
`agentdeck-codex-acp` / `claude-agent-acp` adapters and their versions, a tmux
PATH lookup, and a `mode=ro` SQLite integrity check. Its one write is the
redacted evidence file under `.agentdeck/preflight/<commit>.json` (untracked).

## Redacted facts (as returned)

| Fact | Value |
| --- | --- |
| ready | `true` |
| blockers | `[]` |
| python_version | `3.12.13` |
| python_executable | `…/envs/agentdeck/bin/python` |
| codex_cli | `…/codex@codex-cli 0.131.0` |
| codex_acp | `acp_available` |
| codex_app_server_schema | `91fae2120975b74d2d02184de2d8fed5f90770ce5009f308bbcaeec02dedcc23` (== frozen) |
| claude_cli | `…/claude@claude-cli 2.1.217` |
| claude_acp | `acp_available` |
| tmux | `…/tmux@tmux 3.7` |
| sqlite | `absent` (fresh frozen worktree, no Mission state yet) |
| permission_profile | `full_access` |
| leader_model | `codex-cli/gpt-5.5` |
| target_manifest_hash | `sha256:5afa4ebf…bc12d374` |
| authority_digest | `sha256:eb023357…558e48ef` |

No secret, token, account email, raw provider output, or prompt text is
captured — only paths, versions, booleans, and content hashes.

## Reproduce

```bash
agentdeck _product preflight --real \
  --commit da8d7a8c30c27fc81ad1c9b26182b126f4990b01 \
  --leader codex-cli --model gpt-5.5 --permission full-access \
  --authority-digest sha256:eb0233576233d0f7b1a51ab17d7043398b2aa125cb34add4720f0584558e48ef \
  --target-manifest tests/product_kernel/fixtures/reference_homepage/target-manifest.json \
  --json
```

Live runs are repeatable after diagnosis; this is not a one-shot authorization.
The next gate — the real four-Worker Golden Product Mission — is separately and
explicitly authorized, and is not started here.
