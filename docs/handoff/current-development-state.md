# AgentDeck Current Development State

Updated: 2026-07-17

## Active goal — close the live internal-preflight authority mismatch

Before consuming the authorized designated preflight for frozen implementation
`fda1a69194e67b50afe0c2b9f4e7f29c195af400`, a read-only command audit found a
specification defect: installed
`@agentclientprotocol/claude-agent-acp@0.58.1` declares its official executable
as `bin["claude-agent-acp"] = "dist/index.js"`, while the frozen M2c authority
hard-codes nonexistent `dist/claude-agent-acp`. Synthetic packages had copied
the same incorrect assumption, so deterministic and full-suite verification
could not reveal the real-package mismatch.

No designated pytest node, provider, ACP/tmux session, daemon, install, login,
or global change ran; the authorization was not consumed. The human approved a
metadata-bound correction at
`docs/superpowers/specs/2026-07-17-m2c-acp-package-entrypoint-authority-design.md`.
It derives and seals the official npm bin entrypoint, binds its canonical
relative path into `m2c-tool-authority/v2`, and advances the strict designated
response to `m2c-live-preflight/v4`. M2c remains **BLOCKED** and M3 remains
locked until a new RED/GREEN, frozen SHA, double full suite, real preflight,
and real four-stage Mission all pass.

The metadata-bound deterministic implementation is now present on this feature
branch: official object/string npm bin selection, duplicate-safe bounded JSON,
strict package-relative path validation, full package/runtime drift sealing,
explicit environment binding, authority v2, preflight v4, and the controlled
Node launcher all use the same selected entrypoint. Focused RED first proved
the real mismatch (`3 failed`); GREEN and safety/loader/launcher matrices then
passed `16`, `31`, and `21 passed` respectively, with the one real designated
node still skipped. The wider checks now pass: focused authority/package/launcher
coverage is `61 passed, 1 skipped in 20.78s`, complete non-live M2c is `266
passed, 2 skipped in 64.78s`, and product/Conversation/contract/provider
coverage is `851 passed in 4.91s`. Compile, diff, `src/agentdeck/**` zero-change,
durable-wording, process, and temporary-root audits passed. This documentation
commit freezes the implementation; two fresh detached-worktree full suites on
its unchanged SHA remain before the one real v4 preflight. No real preflight,
live Mission, provider, installed ACP/tmux execution, or daemon has run.

The new implementation authority is frozen at
`582fc2c7f3b344b5310d254d017e461d68f806f6`. Two fresh detached worktrees on
that unchanged SHA passed the complete suite: `4357 passed, 3 skipped in
199.07s` and `4357 passed, 3 skipped in 200.45s`. The skips were exactly the
opt-in real ACP, designated M2c preflight, and real four-stage Mission nodes.
Both worktrees were removed; implementation/SOP remained byte-unchanged after
freeze; process and temporary-root audits were empty. The next action is the
one real v4 read-only preflight using Leader `gpt-5.5` and the installed
metadata-selected package entrypoint.

That pre-command package audit found two npm-generated internal symlinks under
`node_modules/.bin`; both resolve lexically to regular executable files already
inside the same package. Because frozen `582fc2c7...` rejects every symlink, the
real preflight was not started and its one-shot authority was not consumed.
The user's delegated completion goal approved a minimal closed-link correction
at `docs/superpowers/specs/2026-07-17-m2c-acp-package-internal-symlink-authority-design.md`:
only stable relative `.bin` links to regular manifest files are accepted,
without following them; their text and runtime identity are sealed in authority
v3 and strict preflight v5. M2c remains **BLOCKED** and M3 remains locked.

The closed-link deterministic implementation is now present: Python package
sealing and the generated mode-0500 launcher both record stable non-following
link manifests, validate exact `.bin` lexical closure, and reject every unsafe
location, target, chain, or link/target drift. Focused RED proved the installed
layout gap; package, safety, and launcher GREEN sets passed `33`, `19`, and `24
passed` respectively. Wider non-live/product regression and the new freeze
cycle remain before any real v5 preflight.

Those wider checks now pass: strict/package/launcher coverage is `37 passed,
254 deselected in 36.43s`, complete non-live M2c is `289 passed, 2 skipped in
95.77s`, and product regressions are `851 passed in 4.86s`. Compile, diff,
`src/agentdeck/**` zero-change, process, and temporary-root audits passed. This
documentation commit freezes the closed-link implementation; two complete
detached-worktree suites remain before real preflight.

The new closed-link implementation authority is frozen at
`284d8f62a9121a0d0351938aee1f716b3ebd198e`. Two fresh detached worktrees on
that unchanged SHA passed `4380 passed, 3 skipped in 205.38s` and `4380 passed,
3 skipped in 209.27s`. The skips were exactly the opt-in real ACP, designated
v5 preflight, and real four-stage Mission. Both worktrees were removed;
implementation/SOP diff from freeze is empty; process and temporary-root audits
found no residue. The one real installed-package v5 preflight is now the next
gate.

The native-schema provenance persistence correction is implemented and frozen
at `7a76ada81938be3ba0720a7c2f5a540b4beebb3e`. Semantic Mission previews now
preserve the exact validated eleven-field generation envelope. StateStore
strictly distinguishes ordinary nine-field and semantic eleven-field shapes,
revalidates proposal-stripped required/input authority version/hash, and keeps
ordinary/semantic native schema families distinct. ProjectView, trace, and
Leader-status contracts and discovery expose both shapes while preserving the
compatible base-nine metadata.

Legal Leader proposals retain two explicit meanings: generation provenance
hashes the proposal-stripped required/input authority, while the compact
ProjectView semantic authority card hashes the complete compiled output
authority. Stored plan, ProjectView, and trace use one exact generation
envelope; clients must not compare the two authority hashes directly.

Fresh verification on the frozen SHA passed:

- Mission/Conversation/binding/acceptance: `211 passed in 5.77s`;
- Provider/schema/contracts/non-live M2c: `1125 passed, 1 skipped in 56.32s`;
- independent spec review: compliant;
- independent code-quality review: no Critical, Important, or Minor findings;
- full suite 1: `4283 passed, 2 skipped in 194.36s`;
- full suite 2: `4283 passed, 2 skipped in 203.12s`;
- compile, diff, scope, marker, cleanup, and residual audits: PASS.

The detached verification checkout was removed. The feature worktree remained
clean; live pytest/AgentDeck daemon matches and current live/tool roots were
zero. No Provider, live Mission, ACP session, managed tmux session, install,
login, global configuration, push, or merge ran during the correction cycle.

The human then bound exact Leader model `gpt-5.5` and authorized exactly one
read-only preflight on frozen implementation
`7a76ada81938be3ba0720a7c2f5a540b4beebb3e`. That node ran once and passed
`1 passed in 4.24s`, returning `schema_version=m2c-live-preflight/v2`,
`ready=true`, `blockers=[]`, and four ready tools: Codex CLI `0.131.0`, Claude
Code `2.1.211`, Claude Agent ACP `0.58.1`, and tmux `3.7`. The model card was
exactly `{provider: codex-cli, model: gpt-5.5, source: explicit, ready: true}`.
The detached preflight checkout was removed, and follow-up audits found no
matching checkout, pytest/daemon process, or M2c live root.

For this SHA, preflight count is exactly `1`. The human separately authorized
exactly one real four-stage live Mission on the same frozen SHA and model. That
node ran once, exited `1`, and reported `1 failed in 14.36s` with the fixed
diagnostic `stage=live_acceptance`, `code=preflight_blocked`. It stopped before
project initialization, Mission Preview, model invocation, daemon admission,
ACP/tmux Worker execution, permission handling, handoffs, or artifact effects.
It was not retried.

The detached live checkout and disposable strict-basename tool mirror were
removed. No matching live root, pytest/daemon process, or staged mirror
remained. The feature worktree stayed clean before this evidence update; no
install, login, global configuration/auth/permission change, user tmux
inspection, push, or merge occurred.

The designated preflight and live internal preflight did not use the same
executable authority: the former used PATH-discovered installed tools, while
the live SOP required explicit strict-basename mirror paths. The current
harness collapses the internal result to `preflight_blocked`, so the exact
allowlisted blocker cannot be recovered without another external execution.
No inference is made about which tool or probe failed.

For frozen SHA `7a76ada...`, preflight/live counts are now exactly `1/1`; both
authorizations are exhausted and neither may be rerun. Historical SHA
`75f0366d...` also remains exhausted at `1/1`. M2c remains **BLOCKED** and M3
remains locked. The human-approved design is now written at
`docs/superpowers/specs/2026-07-17-m2c-tool-authority-binding-design.md`. It
binds designated preflight and live through one content-addressed authority
covering model, Codex, Claude, tmux, Node, and the complete Claude Agent ACP
package tree; it also projects only closed `tool + probe + code` diagnostics.
The human approved the written spec. The detailed, self-reviewed TDD plan is at
`docs/superpowers/plans/2026-07-17-m2c-tool-authority-binding.md`; it divides
the work into deterministic authority, package-tree, preflight-v3, live
admission, diagnostic closure, controlled Node/ACP, SOP, and frozen
verification commits. Inline RED/GREEN implementation is now authorized.
Tasks 1-7 are now implemented in the M2c harness/SOP: deterministic authority,
complete ACP package sealing, strict preflight v3, pre-root digest admission,
closed failure projection, controlled Node/ACP execution, and the separately
gated designated node. No `src/agentdeck/**` behavior changed. Focused
RED/GREEN checks are passing, and the complete non-live M2c file passed
`238 passed, 2 skipped in 67.79s`; the skips were exactly the gated real
designated preflight and real live node. The focused authority matrix passed
`44`, and product/conversation/contract/provider regressions passed `851`.
Compile, whole-slice diff, `src/agentdeck/**` zero-change, durable-evidence,
process, and live-root residual audits passed. This documentation commit
froze the implementation at
`fda1a69194e67b50afe0c2b9f4e7f29c195af400`. Two fresh detached worktrees on
that unchanged SHA passed the complete suite: `4329 passed, 3 skipped in
204.59s` and `4329 passed, 3 skipped in 206.95s`. The three skips were exactly
the opt-in real ACP, designated M2c preflight, and real four-stage M2c nodes.

An earlier verification attempt using relative `PYTHONPATH=src` was discarded:
a daemon subprocess changed cwd to its disposable project and could not resolve
that relative source path, so an existing daemon acceptance admission returned
false. The exact node passed `1 passed in 11.26s` when only the source path was
made absolute. The contaminated detached worktree was removed, a fresh one was
created, and both counted full suites then passed. This changed no implementation.

Both verification worktrees were removed. Final audits found no matching
pytest/AgentDeck daemon process and no authority-suite, live, or four-stage
temporary root. No real designated preflight, live Mission, provider,
ACP/tmux Worker, install, login, global change, merge, or push ran. M2c remains
**BLOCKED** and M3 remains locked. The next gate requires separate human
authorization naming frozen SHA `fda1a691...` and the exact Leader model for
one designated read-only preflight. If and only if that result is
`ready=true`, `blockers=[]`, and `failures=[]`, a later live authorization must
separately name the same SHA, model, and exact returned authority digest.

## Historical 75f provenance blocker evidence

The approved target-exclusivity and pytest-report-redaction TDD plan is
implemented and verified. The implementation authority is frozen at
`75f0366d4d5619b29c77f10949365f43d46185b1`; the later documentation evidence
commit is not implementation authority. Required targets are Candidate-wide
exclusive, new proposal targets are Mission-wide unique, code-specific
same-Leader regeneration is shared by API and CLI providers, and bounded PTY
bytes cannot enter default pytest reports through dataclass representation.

Focused semantic/Provider coverage passed `740`; the complete non-live M2c file
passed `192 passed, 1 skipped in 46.41s`. Two independent detached-checkout full
suites on the unchanged frozen SHA passed `4266 passed, 2 skipped` in `199.05s`
and `186.08s`. Compile, diff, scope, sentinel-leakage, cleanup, and residual
audits passed. No provider, ACP, tmux, preflight, or live Mission ran during
this implementation verification.

The human explicitly bound Leader model `gpt-5.5` to frozen implementation
`75f0366d4d5619b29c77f10949365f43d46185b1` and authorized exactly one read-only
preflight. It ran once and passed `1 passed in 3.75s` with `ready=true`,
`blockers=[]`, `source=explicit`, Codex CLI `0.131.0`, Claude Code `2.1.211`,
Claude Agent ACP `0.58.1`, and tmux `3.7` all ready. The detached checkout was
removed; the feature worktree remained clean; residual audit found zero live
pytest/AgentDeck daemon processes and zero M2c live roots.

After separate human authorization, the real four-stage node ran exactly once
on the same frozen SHA/model and was not retried. It failed `1 failed in
48.26s` at `stage=live_acceptance`, `code=native_schema_provenance_missing`.
The snapshot had `plans=1`, `missions=1`, and zero attempts, permissions,
Worker replies, and handoffs. The bounded PTY evidence retained only
`byte_count=1438`, `truncated=false`, and
`sha256=4d261e29ad7cf2b3a5d19b899eb0cc734c8e86f19ec71e55731e39a2c6b706fa`.
No terminal text was copied into durable evidence.

The guarded harness and outer cleanup removed the live checkout and disposable
tool mirror; the feature worktree remained clean; current-run process matches
were zero. One `/private/tmp/agentdeck-m2c-path-verify-954b868c` directory
predated this run (`mtime=2026-07-16T00:29:09+0800`) and was left untouched.

Code inspection identifies the precise persistence break:
`create_mission_preview_from_candidate()` validates semantic
`leader_generation`, then explicitly replaces it with `None` under a stale
Task 7/Task 8 handoff comment. The resulting semantic plan and Mission are
durable, but their plan record lacks the native-schema provenance required by
the live gate. Existing provenance tests cover native non-semantic previews,
not native semantic previews.

For this SHA, preflight count is exactly `1` and live count is exactly `1`.
Neither may be rerun. M2c remains **BLOCKED** and M3 remains locked. The next
gate is an approved deterministic RED/GREEN fix that preserves the already
validated semantic generation envelope in the plan record without changing
plan hash, semantic authority, confirmation, ACP/tmux, or permission behavior.

## Historical previous frozen live evidence

Leader Preview observability is frozen at
`9db5b476f885cfcf68a55cbf59673a2d908d3fce`. Its complete non-live harness
passed `186 passed, 1 skipped in 42.69s`; two independent unchanged-SHA full
suites passed `4219 passed, 2 skipped` in `185.64s` and `191.59s`. The one
human-authorized read-only preflight for Leader model `gpt-5.5` passed with
`1 passed in 4.19s`, `ready=true`, `blockers=[]`, and all four tools ready.
That preflight must not be rerun.

After separate explicit authorization naming the frozen SHA and model, the
sole opt-in real four-stage node ran exactly once. It exited `1` with
`1 failed in 52.39s` and was not retried. The first unmet gate is
`stage=live_acceptance`, `code=leader_schema_before_preview`. The exact durable
Leader terminal is `stage=schema`, `diagnostic_code=semantic_effect_conflict`,
`attempt_count=2`, and `constraint_mode=native_json_schema`.

The same snapshot had `plans=0`, `missions=0`, `mission_attempts=0`,
`permission_requests=0`, `mission_worker_replies=0`, and
`mission_handoffs=0`. Its closed ledger classification was
`permission_state_inconsistent`, with zero permissions and unknown lifecycle
fields. The run therefore stopped before Mission Preview creation or
confirmation, daemon admission, ACP/tmux Worker execution, permission,
disconnect/reconnect, takeover/return-control, handoff, lineage, or artifact
effects. This is not a partial four-stage PASS.

Bounded PTY identity was `byte_count=608`, `truncated=false`, and
`sha256=cbc80281637c6d93de32e51d883339c5095b1a38ae4c1e2c518345fa96e8560a`.
The allowlisted failure JSON retained no terminal text. However, pytest's
traceback rendered `_PtyTail` through its default dataclass representation and
showed raw tail bytes in ephemeral test output; the existing deterministic
leakage test covers `str(exception)`, not pytest report rendering. Do not copy
those bytes into durable evidence, and do not claim transcript-safe pytest
failure output until a separately approved TDD slice closes that boundary.

The harness emitted no cleanup-failure note. The frozen checkout remained
clean; the detached checkout and disposable tool mirror were removed; audit
found zero current-run live roots, live pytest/AgentDeck daemon processes, or
staged mirrors. Four tmux sockets created on July 14 predated this July 16 run,
were outside its isolated live root, and were left untouched. No install,
login, global config/auth/permission change, user tmux inspection, or second
live attempt occurred.

M2c remains **BLOCKED**, not partial PASS, and M3 remains locked. This prior
failure motivated the now-completed target-exclusivity and pytest-redaction
slice. It is superseded for active routing by frozen implementation
`75f0366d4d5619b29c77f10949365f43d46185b1`. That implementation has now used
its one preflight and one live authorization and stopped at
`native_schema_provenance_missing`; neither may be rerun. That persistence fix
is now complete at the frozen SHA recorded above, whose own preflight/live
cycle is also exhausted at `1/1` after `preflight_blocked`. The active route is
the new same-executable-authority and closed internal-preflight-diagnostic
design/TDD cycle described at the top of this file.

## Natural-language Mission Phase 0 baseline — accepted

The fresh-project strict two-message Codex/Claude acceptance completed all eight frozen sequential steps as Mission `mis_1d5c2a569173`, plan `pln_c13709530632`, and workflow `wfr_7d309ae9c507`. Mission status, ProjectView status, workbench, and the event ledger agree on `completed/current_step=8`; the audit contains one `mission_confirmed` and eight `workflow_step_completed` events. First-run trust remained an explicit human setup boundary. Two real readiness false negatives were converted to strict regression tests before minimal fixes. Verdict: **PASS**. Durable evidence: `docs/validation/2026-07-11-natural-language-mission-acceptance.md`.

## Protocol-native Phase 1 model — complete

Phase 1 adds pure transport capability, agent session, protocol turn, transport update, and permission request records; append-only persistence with audited lineage; compact ProjectView summaries; the versioned `protocol-runtime/v1` discovery contract; read-only `agentdeck protocol status`; and runtime capability metadata. `agentdeck contract protocol-runtime --example`, `agentdeck protocol status`, ProjectView, and the contract index expose the implemented observation surface.

tmux remains the active default backend. Its capability metadata describes only the observable fallback it actually provides; it is not ACP-compatible metadata and does not authorize execution. Existing tmux dispatch does **not** automatically emit protocol records. Phase 1 has not implemented an ACP backend or adapter subprocess, automatic emission, a project daemon, a backend switch, or a provider-native permission bridge.

Phase 2 subsequently delivered one human-approved foreground ACP vertical slice. It does not change the Phase 1 boundary for existing tmux dispatch or imply that Mission/workflow now use ACP.

## Sequential workflow core — implemented

### Built-in sequential-handoff planning skill — implemented and accepted

`planning_guidance[]` is now a bounded audited skill field (maximum eight entries, 240 characters each) that follows explicit load records into ProjectView and plan provenance. Only guidance from an `agent_id=leader` load enters API/CLI Leader prompts; full `content_snapshot` remains excluded. Existing skills default to an empty list.

The generic built-in `sequential-handoff` skill (`version=1.0.0`) shapes fixed consecutive plans, explicit compact handoffs, per-step evidence/failure conditions, and a workflow preview → human-confirmed run summary. It is never auto-loaded, never injected into Workers, grants no execution permission, and rejects parallel/DAG/cycle/dynamic-step workloads.

GREEN/counterexample evaluation and full regression are complete. The isolated real acceptance used Codex and Claude Workers in one resumable run (`wfr_d1bd55232a66`): all eight alternating turns completed and produced the expected opening 32 surnames. The durable evidence is `docs/validation/2026-07-10-codex-claude-baijiaxing-handoff.md`.

The real run also hardened terminal interoperability: echoed prompt templates no longer correlate as replies; known Codex/Claude TUI bullets are normalized; partial streaming blocks wait instead of failing early; tmux multiline paste pauses briefly before submit; and send failures persist `stopped/pane_lost` instead of leaving a crashing `running` workflow. Operator setup still must clear first-run trust prompts and provide panes large enough to retain the structured reply token.

The generic A→B→C handoff engine is implemented and committed. It is intentionally separate from ordinary `run-loop`:

- `agentdeck workflow preview --plan-id <id> [--timeout <seconds>]` is read-only and derives a hash-pinned ordered chain plus stored-runtime blockers without inspecting tmux.
- `agentdeck workflow run --plan-id <id> [--timeout <seconds>] --confirm` performs one foreground, bounded run after a single explicit confirmation.
- `agentdeck workflow status --run-id <id>` is read-only; `agentdeck workflow resume --run-id <id> --confirm` resumes the frozen chain and does not repeat a dispatched or completed step.
- Every active Worker reply must carry the exact `handoff_token` and structured status/summary/verification/risks/next_steps fields. Only compact validated handoff data reaches the next Worker.
- State is persisted under `workflow_runs[]`; existing message/reply/artifact lineage and workflow audit events remain inspectable.
- Contract discovery is `agentdeck contract workflow --example`; the durable contract is `docs/contracts/workflow-schema.md`.

Safety boundary: workflow execution never expands the plan, spawns agents, calls a Leader provider, auto-acks inbox items, or grants worker permissions. Plan drift, unavailable runtime, pane loss, invalid reply, timeout, blocked, and failed stop the chain. Ordinary approval/dispatch/capture-reply/run-loop behavior is unchanged.

Deferred: DAG/cycle semantics are not part of this linear workflow core.

## Golden demo guide slice — implemented

The end-to-end golden demo lane now has its first guide slice implemented and committed:

- `agentdeck demo golden` is a read-only, state-aware operator guide for the golden demo. It derives current status from existing project/workbench facts and recommends explicit next commands for provider/setup, approval, dispatch, review gate, release, and already-released states.
- `agentdeck contract demo` / `agentdeck contract demo --example` expose the GUI-ready demo guide contract and stable example payload; `docs/contracts/demo-schema.md` documents the response fields, step fields, statuses, and safety values.
- The implementation was covered by focused contract/CLI tests and read-only/no-runtime-mutation assertions in the implementation slices. The guide does not execute recommended commands, call providers, read tmux, or mutate runtime/state.

### Deterministic golden-demo rehearsal — covered

The golden path now has one contiguous pytest rehearsal in addition to focused state tests. It drives a single temporary project through fake-Leader planning, explicit approval, fake-runtime dispatch, captured reply/artifact, code review, round review, and explicit release while checking `agentdeck demo golden` at every checkpoint. This is test-only coverage: no production command, function, runtime backend, or contract was added.

Lane guidance: this supports the **end-to-end golden demo first**. Remote skill / marketplace work remains a later product fork/lane and should not be started as part of golden demo docs cleanup.

## Skill 生态 lane 进度 — A + B(只读/auto/ver/semver) + lockfile 完成，⏸ loop STOP（next remote/C）

用户定了 "先 A 再 B"、"先 B-auto 再 B-ver"，选了 semver 范围，再选了 lockfile，loop 已推进到 lockfile 落地。已完成并提交：
- **只读可见性 4 片**：`skills catalog --source <dir>` → `[skills] allowed_sources` + `skills sources` + `source_allowlisted` → workbench `skills_catalog_card` → 自然语言 `mode=skills_catalog`。
- **A — allowlist 强制拦截**：`skills import` opt-in 强制（`--allow-unlisted` 逃生阀，空清单向后兼容，审计 `skill_imported.allowlisted`/`.allow_unlisted`，`import-preview` 只读回显）。
- **B1/B2 — 依赖只读**：`skills deps --name <name>`（依赖树/missing/循环/拓扑序）；`skills load-preview` 回显 `unmet_dependencies`。
- **B-auto — 依赖 load（preview + 显式确认）**：`skills load-plan`（只读预览）+ `skills load --with-deps --confirm`（deps-first 逐条 load，缺失/环拒绝零写，绝不 auto-import/静默；单 skill load 不变）。
- **B-ver — 依赖版本约束（content-hash 锁定）done**：`depends_on: [name@sha256:<hex>]` 锁定内容 hash（纯 `name` = 任意版本，行为不变）。`skills.py` 新增纯 `_parse_dep`；`resolve_skill_dependencies` 新增 `version_mismatch: [{name,expected,actual}]` blocker 类别（pin 与实际 `content_hash` 不符，blocker leaf 不递归）。`skills deps` / `load-plan` 输出 `version_mismatch`（加入两个 contract 字段 + validator），`load-plan.blockers` 加 `"version mismatch: <name> expected <pin>"`，`can_load` 因此为 false，`skills load --with-deps --confirm` 像 missing/cycle 一样硬阻断、零写。纯 hash、本地、确定性、无网络。Design + plan: `docs/superpowers/specs/2026-07-09-skill-dep-version-pinning-design.md`、`docs/superpowers/plans/2026-07-09-skill-dep-version-pinning.md`。
- **semver — 依赖 semver 范围 done**：skill `SKILL.md` frontmatter 声明 `version: X.Y.Z`（默认 `0.0.0`，加入 `SkillSnapshot.summary()` + `SKILLS_SKILL_ITEM_FIELDS` + example fixture）。`depends_on: [name@<spec>]` 中 `<spec>` 不以 `sha256:` 开头即为 semver 范围，与依赖 `version` 比对。`skills.py` 新增纯 stdlib `parse_version` + `version_satisfies`（支持 bare/`==` 精确、`>= > <= <`、caret `^`、逗号 AND；`MAJOR[.MINOR[.PATCH]]` 缺省补 0；不支持/无法解析一律 fail-safe False）。`resolve_skill_dependencies` 分类 spec：`sha256:` → 内容 hash，否则 → `version_satisfies`，不满足记入 `version_mismatch`（新增 `reason` 键，`name/expected/actual` 与 B-ver 兼容）作为 blocker leaf 不递归；`version_mismatch` 继续经 `skills deps` / `load-plan` blockers / `load --with-deps` 硬阻断、零写。`sha256:` pin 和纯 `name` 逐字节不变。纯 stdlib、本地、确定性、无网络、无第三方库。Design + plan: `docs/superpowers/specs/2026-07-09-skill-dep-semver-design.md`、`docs/superpowers/plans/2026-07-09-skill-dep-semver.md`。
- **lockfile — 依赖锁文件 generate + read-only verify done**：`agentdeck skills lock --name <name>` 显式冻结已解析依赖树（复用 `resolve_skill_dependencies` + `discover_skills`，deps-first `order` 去 root，逐个 pin `content_hash`+`version`）到专用 `.agentdeck/skill-locks/<name>.json`（`discover_skills` 不拾取），写 lockfile + `skill_locked` 事件；有 missing/cycle/version_mismatch 拒绝零写，未知 skill 非 0。`agentdeck skills lock-verify --name <name>` 全只读 diff（`changed`/`added`/`removed`/`blockers`/`in_sync`），无 lockfile → `locked=false`+hint+退出 0，不写 state、不改 lockfile。lockfile 本切片是 **advisory** drift 检测，不改变 `deps`/`load` 解析（enforce 是后续切片）。contracts.py: `SKILL_LOCK_*_RESPONSE_FIELDS` + `validate_skill_lock*_contract` + 发现字段。本地、无网络、无第三方库。Design + plan: `docs/superpowers/specs/2026-07-09-skill-dep-lockfile-design.md`、`docs/superpowers/plans/2026-07-09-skill-dep-lockfile.md`。

⏸ **loop STOP —— 剩余依赖项是产品 fork，需先 STOP + 询问 human，不得单方面开工**：
- **remote / marketplace 依赖（C）**——联网远程解析/抓取/签名/供应链/registry 格式，local-first 边界外，需 human 显式 opt-in 的专门设计对话（自己的 brainstorm→spec→plan），绝不在 loop 里开工。lockfile enforce（让 `deps`/`load` 消费 lock 改变默认解析）也是后续独立切片，非本 loop。

中文小结：lockfile generate + read-only verify 已实现并提交。`agentdeck skills lock --name <name>` 把某 skill 当前解析出的依赖树冻结成 `.agentdeck/skill-locks/<name>.json`（每依赖 name+content_hash+version），并追加 `skill_locked` 审计事件；不可解析树（缺失/循环/版本不符）会被拒绝且不写任何文件或事件。`agentdeck skills lock-verify --name <name>` 全只读，报告 lockfile 与当前解析的漂移（changed/added/removed/in_sync），不改任何状态或 lockfile。lock 本切片是 advisory，不改变 `deps`/`load` 的解析行为。到此 skill 依赖 lane 的本地确定性约束（hash pin / semver range / lockfile）都做完了。**⏸ 下一步是 remote/C（联网/签名/供应链/registry），必须 STOP + 问你，绝不在 loop 里做**；lockfile enforce 亦是后续独立切片。

## M2c development history and prior evidence

The material below records earlier M2c checkpoints and is superseded for
active routing by the current goal above.

**Task 13 semantic M2c harness conversion is implemented; it becomes frozen evidence authority only after unchanged-SHA verification.**
The complete non-live harness passes `110` tests with one explicit opt-in live
skip. The live confirmation path no longer treats Leader-authored free-text
phase/token matches as authority: before confirmation it validates
`mission-semantic-authority/v1`, the unique atomic revision before/after state,
byte-equal fresh compilation, four semantic-step and task hashes against the
authoritative snapshot, the exact authority/task/policy/generation confirmation
digest, and zero attempts, permissions, Worker replies, and handoffs. The old
token checks remain test-only mutation helpers. This revision has not run the
Task 13 frozen double full suite or the single read-only preflight, and has not
entered Task 14 or made a live attempt.

AgentDeck remains the control plane around LLM reasoning, not its replacement:
required user authority, visible Leader proposals, unresolved facts, and
confirmed frozen authority are distinct; one Mission confirmation remains
independent from runtime permissions; ProjectView exposes only compact,
non-authorizing provenance. A2A, remote execution, GUI redesign, and a terminal
emulator are out of scope. M2c remains **BLOCKED**, M3 remains locked, and the
next route is to take the exact commit containing this handoff, verify that SHA
twice, then run the designated read-only preflight exactly once. Task 14 requires new
human authorization even if that preflight is ready.

**Phase 3 M2 implementation Tasks 1–14 are integrated into `main`; the active goal remains the approved Phase 3 M2c acceptance closure.** The latest evidence authority is frozen commit `1a22618ba083a76f4a21ffc7ebc7a3e513e4aae6` on branch `codex/m2c-probe-readonly`. Its non-live focused harness passed `97` tests with `1` explicit live skip; two independent full-suite runs passed `3406` tests with `2` skips in approximately `148.23s` and `146.26s`; compileall passed and the diff was clean. The read-only preflight exited `0`, passed `1` test in `16.15s`, and reported `ready=true`, `blockers=[]` with Codex CLI `0.131.0`, Claude CLI `2.1.208`, Claude Agent ACP `0.58.1`, and tmux `3.6a`.

The strictly single live attempt exited `1` with `1 failed` in `49.50s` and was not retried. It stopped before confirmation with `code=native_schema_task_authority_invalid` and `classification=leader_task_authority_missing`. Of the closed seven `task_authority` fields, only `revision_transition=false`; `phase_order`, `worker_order`, `artifact_all_steps`, `implementation_draft`, `review_target`, and `acceptance_target` are `true`. Leader-generated revision task did not simultaneously preserve both `draft-v1` and `accepted-v2`. This does not establish which token was absent, whether both were absent, or why the Leader output lost the requirement.

The snapshot cardinalities are `plans=1`, `missions=1`, `attempts=0`, `permissions=0`, `replies=0`, and `handoffs=0`. Before confirmation the run reached no ACP, permission, Worker, tmux, scheduler, or artifact effect, so the next work must not pivot to a permission or ACP repair. The bounded PTY evidence is `byte_count=11`, `truncated=false`, `sha256=066523e516460e23c045358c6736f76f2fecd1022157b11c679ae69715c0c734`; the hash is identity only and cannot explain terminal text. The harness failure had no cleanup-failure note; the outer mirror/path was removed; post-run audits found zero mirror/live-pytest/agentdeck-daemon process matches, zero M2c temp-directory matches, and zero M2c tmux-session matches. No absent `cleanup=complete` or `residual_process_count` field is invented.

M2c is **BLOCKED**, not a partial PASS, and M3 remains locked. At the conclusion of the single live attempt, the required next gate was a new brainstorming/spec/plan round for Leader revision task semantic authority before deterministic RED/GREEN, a new commit, a fresh full suite, and a fresh `ready=true` / `blockers=[]` preflight could authorize one new single live attempt. The brainstorming and design portion of that gate is now complete as recorded below. Do not retry automatically.

The brainstorming and segmented design for that gate are human-approved and written as `docs/superpowers/specs/2026-07-15-leader-semantic-authority-design.md`. The chosen direction is a general `mission-semantic-authority/v1` control plane, not M2c-specific token hardcoding: AgentDeck conservatively extracts required user authority, the Leader returns structured authority references and visible proposals, AgentDeck validates and deterministically compiles Worker tasks, and one exact preview confirmation freezes the executable scope. The design preserves separate runtime permission gates, allows one same-Leader bounded regeneration, keeps sensitive values reference-only, and requires hash-stable dispatch/recovery provenance. The detailed TDD implementation plan is `docs/superpowers/plans/2026-07-15-leader-semantic-authority.md`; Tasks 1–12 and the Task 13 harness conversion are implemented. The immediate next action is frozen verification and the one designated read-only preflight. Task 14 still requires a separate explicit live authorization after a ready preflight.

The formal design is `docs/superpowers/specs/2026-07-14-agentdeck-m2c-closure-design.md`, and the Subagent-Driven TDD execution follows `docs/superpowers/plans/2026-07-14-agentdeck-m2c-closure.md`. Tasks 1–10 are implemented on the isolated closure branch. Both design and implementation start from `docs/roadmap/product-north-star.md`: AgentDeck—not the Leader model, ACP, or tmux—owns frozen Mission authority, scheduling, governance, recovery, and audit.

The Task 11 frozen-commit preflight at `650d6fc4` found Codex CLI `0.131.0`, Claude CLI `2.1.208`, Claude Agent ACP `0.58.1`, and tmux `3.6a` ready, but returned `ready=false` with the sole fixed blocker `probe_wrote_files`. The opt-in live node was not run. Thus the real implementation → review → revision → acceptance Mission, disconnect/reconnect, two explicit ACP permissions, tmux visibility, takeover/return-control, four canonical handoff evidence rows, three inter-stage links, artifact, ledger, trace, and snapshot agreement are not reached. Staging and live temporary roots have zero residuals; no installation, login, authentication, or global-setting change occurred. M2c remains **BLOCKED**, and the earlier two-step real transport PASS must not be promoted to four-stage acceptance.

Task 12 closes only the deterministic verification and handoff boundary; it does not change that live verdict. Fresh focused gates pass `389` Leader tests, `1134` Mission/contract tests, and `349` daemon/governance/recovery tests. The full suite passes `3348` tests with `2` explicit skips; `python -m compileall -q src tests` and `git diff --check` both exit 0. Self-review confirms one canonical Leader schema source, native Codex and Claude coverage, AgentDeck-owned semantic authority, no provider/model/transport fallback, no local intent-repair path, same-Leader deadline-bounded regeneration, compact non-durable raw-output boundaries, contract-valid ProjectView provenance, deterministic four-stage acceptance, and cleanup-as-evidence. All `41` branch commits, including this handoff boundary, include `HISTORY.md`; no `.agentdeck/` runtime state is tracked; the closure diff contains no A2A, remote, global-roaming, Workspace Client, or terminal-emulator scope; and the user-owned main-checkout `.omc/` changes and untracked `AGENTS.md` remain outside this clean worktree and were not staged or modified here.

At that earlier checkpoint, the live acceptance authority was frozen commit `650d6fc4`. Its preflight had the sole blocker `probe_wrote_files`, and the number of opt-in live attempts was exactly `0`. Therefore M2c was not complete, the M2 `/goal` could not be closed, and M3 remained locked. Its then-next gate was to identify which capability probe wrote inside the isolated roots, capture that behavior in a deterministic regression, apply the smallest in-scope fix, freeze a new commit, and run a new read-only preflight. The opt-in live node could not run until that new frozen preflight was ready; an unknown external effect could never be retried. This paragraph is retained as historical BLOCKED evidence and is superseded for active routing by the latest frozen evidence above.

Leader planning failure truth is now compact and durable. CLI Leaders emit only typed allowlisted stages (`timeout`, `nonzero`, `json_parse`, `schema`, `cancelled`, `oversize`); Gateway/session propagation never persists raw stdout/stderr, prompts, argv, paths, or exception text. Failed/cancelled turns immediately commit their terminal transition plus `conversation_turn_terminal.stage`, allowing reconnect/acceptance clients to stop without waiting for a nonexistent Mission. Natural-language planning freezes Worker selection and step count once before the Leader call, carries them explicitly through `LeaderRequest` and `LeaderMissionCandidate`, verifies the Gateway returned the same authority, and lands the preview without reparsing the redacted durable message. Session planning and legacy landing share one conservative explicit-count parser covering Arabic/Chinese round phrases, Chinese step phrases, and English digit/one-through-ten `steps`; ambiguous ordinals, labels, agent counts, and unrelated number prose retain compatible defaults. `MAX_MISSION_STEPS=64` is the source of truth across parsing, candidate authority, normalization, and plan validation. Explicit 0/1, counts above 64, unsupported/oversized Chinese numerals, and huge ASCII tokens fail safely before Leader invocation and become durable `schema`; they are never clamped into a different authorized plan. Open natural tasks therefore remain valid when `mission_intent` is absent, an explicitly unrequested third Worker stays outside prompt and confirmation scope, and planning/landing counts remain identical. Legacy direct candidates without authority retain message-derived compatibility. Candidate validation or pre-commit landing failure becomes the fixed typed `schema` terminal, never an unhandled exception that strands a turn in `waiting_leader`. If the atomic commit call raises, preview recovery checks exact durable plan, Mission, binding, turn-transition, and audit-event facts rather than guessing from exception type; a complete post-save commit returns the same payload with pending outbox preserved, while partial/unprovable state remains failed. Project-wide latest-pending recovery survives a new conversation session, so reconnect/retry blocks duplicate plan, Mission, and binding creation.

Final review closes three adjacent recovery ambiguities. Numeric-shape detection scans every Chinese and English explicit count before choosing an answer: standalone `两` is integer two, unsupported `两百`-style/financial-Chinese, decimal, signed/full-width, out-of-range, or huge tokens fail, and multiple valid quantities must agree regardless of language order; repeated equal cross-language counts remain valid and non-count prose still uses the compatible default. Invalid/conflicting input becomes durable `schema` before any Leader call. When exact preview proof fails after the durable turn has already reached a terminal or otherwise drifted state, the session validates that durable turn fact and returns a fixed `stage=durable_state` fail-stop response; it never rewrites terminal history, presents an unproved preview, repeats domain effects, or lets the internal exception escape, and records only a compact recovery-blocked audit when safe. Exact event proof permits one identical copy in each of journal and outbox for crash replay, but rejects duplicate identities or content drift within either channel.

Fresh final-review gates: focused Mission/session `74 passed`; conversation/provider/acceptance `223 passed`; complete daemon suite `963 passed`; compileall and diff checks pass.

Mixed-version state authority is closed at the remaining filename-replacement window. Each state/journal effect proves the held legacy lock still names `protocol-mutation.lock` before and after I/O. Immediately before atomic replace it re-resolves the currently named legacy authority; after a lock-name replacement it acquires that lock in old-then-current deterministic order, repeats the exact target content+inode CAS while holding both locks, and fails closed before installing current bytes. State commit retains the exact displaced canonical descriptor; after later drift it waits on the replacement filename lock and restores only if canonical still contains AgentDeck's exact installed inode. An explicit effect-installed marker prevents a pre-effect guard failure from re-flocking its already-held replacement inode or treating an older writer's byte-identical/different-inode replacement as AgentDeck's effect. An older in-place write is therefore recovered from the retained inode, while an already atomic older replacement is preserved and the current mutation fail-stops. Journal commit uses the same conditional recovery and keeps the outbox pending. Ten focused race cases include independent-process three-second timeout checks for byte-identical older state/journal replacement, atomic older replacement after initial CAS but before the current replace, and the `963`-test daemon suite passes.

Task 14 adds a real-SIGKILL nine-boundary crash matrix and one disposable product acceptance whose first bare `agentdeck` PTY creates a natural-language Mission preview, confirms that exact preview, and emits a strict validated five-field Mission run card before disconnecting; a second bare PTY reconnects at the ACP permission pause, renders recovery, and drives exact permission preview/confirm. The run proves ACP-before-tmux handoff ordering, compact ProjectView/contracts/ledger/events/hash/file agreement, and zero durable transcript/secret markers. Daemon ACP streamed chunks now persist only canonical content hash and byte count; the raw bounded fragments remain process-local until canonical handoff extraction. Crash cardinality is keyed by durable `(mission_id, step_id, agent_id)` and inspected only after a real restart applies one complete first non-idle scheduler cycle. Startup owns and reaps a spawned daemon if readiness/probing fails; one collect-all guard then attempts every process kill/wait, thread join, endpoint reconciliation, and temporary-project removal even after an earlier cleanup error, attaching cleanup diagnostics without masking the primary failure. Injected regressions prove both exception-safety paths.

Final-review hardening makes takeover worktree evidence bounded before content access. `.git` and `.agentdeck` are pruned before descent; traversal stops after 4,096 non-excluded entries; file size is checked against the 32 MiB aggregate limit before an anchored no-follow open; accepted files are hashed in chunks and their device/inode/type/size/mtime plus path identity are revalidated afterward. Escaping links, symlink swaps, special files, and concurrent replacement fail closed without reading an external target. The four dedicated regressions and the 201-test governance/service/recovery/reconnect group pass.

Recovery truth is deliberately conservative: daemon loss after tmux dispatch but before receipt, and daemon loss while ACP permission is pending, classify the active attempt as `ambiguous` to prevent replay; the permission record remains pending for inspection. Force-stop persists Mission `interrupted` while retaining only the exact current RecoveryFacts attempt's force-stop unknown effect as `ambiguous`. Status/run/workbench expose the same fail-closed five-field `daemon_admission` provenance, and `admitted` authority additionally requires exact five-field shape plus one identical canonical sha256 across the Mission, execution snapshot, and admission record. Drift is `incomplete` and disables resume; valid daemon Missions do not fabricate a legacy `workflow_run_id`.

Fresh final gates: daemon suite `963 passed` and full suite `2928 passed, 1 skipped`. The earlier two-step real transport rehearsal remains **PASS**: Codex CLI Leader, Claude ACP Worker A and Codex tmux Worker B proved permission, compact handoff ordering, disconnect-safe completion and reconnect. The stronger approved four-stage M2c rehearsal is **BLOCKED**: on frozen commit `be4dee08`, two independent fresh bare-client attempts both ended durably at `leader_schema` before preview creation. Each retained zero plans, Missions, attempts, permissions and Worker effects; both projects, daemons and tmux sockets were fully cleaned without install/auth changes. Do not reinterpret the two-step PASS as full M2c acceptance.

Task 13 reconnect/migration truth: ProjectView is the single observation source for the compact `mission_recovery` card; conversation reconnect, workbench, and real bare `agentdeck` reuse the same strictly validated object before continuous UI startup, while a project without a Mission remains quiet. Classification/decision, Mission/attempt/step lineage, controls, traces, and the inspect-only workbench entry are exact; invalid cards produce no partial JSON. Existing-project migration begins with zero-write `agentdeck project migration-preview`, whose exact state-byte hash, additive changes, legacy inspect-only records, expiry, digest, consume-once identity, backup path, and confirmation command bind the only write path. Exact-source revalidation, sanitized project-local backup, and atomic state replacement occur under the protocol mutation lock, preventing concurrent authoritative lost updates. No-follow project-relative directory traversal rejects symlink/non-directory backup paths; backup, commit, and rollback fsync their durability boundaries. `agentdeck contract migration` exposes the strict GUI-ready preview/confirmed schema through contract index and workbench discovery. A legacy Mission without complete frozen authority remains historical/inspect-only; reconfirmation creates a new Mission preview rather than rewriting history.

Task 13 spec re-review closure makes validation authoritative before effects: malformed legacy Mission ids are rejected before command derivation, preview/confirmed digests are recomputed from canonical facts, target changes are restricted to approved M2b additive paths/value schemas, and confirmation validates its response inside the lock before backup or state replacement. Recovery progress requires a complete contiguous completed-step prefix, exact next-step positioning when active, and ordered unique recent-result lineage; legacy foreground progress without frozen steps is reported only to the provable prefix.

Task 13 quality closure anchors project/`.agentdeck`/`state` through no-follow directory descriptors before acquiring the shared `.agentdeck/state/protocol-mutation.lock`. Migration state read/temp/rename/rollback/fsync operations use the anchored state descriptor, backup uses the same anchored deck descriptor, and post-lock inode revalidation rejects state-directory replacement before effects. Lock-wait expiry is freshly rechecked before backup/save. Migration preview now reports `ready`, read-only `noop` for fully migrated state, or fail-safe `blocked` for partial/inconsistent markers; only `ready` exposes an enabled confirmation command.

Task 13 mutation closure makes the anchored `.agentdeck/state/protocol-mutation.lock` the global authoritative `state.json` transaction and preserves that original inode for mixed-version exclusion. All 68 public transitive `StateStore` writers acquire it before their first load and hold it through atomic save; same-thread nesting is reentrant, internal helpers reuse the outer transaction, and the mission-only execution lock is gone. A static AST call-graph audit keeps the explicit registry complete. Plain dict results carry a branch-safe transient token backed by weak, small source facts: deepcopy clones the initial provenance, and successful save replaces only the saving dict's token, so shallow/deep copied branches cannot refresh stale originals. Unrelated loads cannot evict a live snapshot, garbage collection reclaims metadata, and no full state is retained; serialization and ProjectView never expose the token. Stale public saves after migration or another process commit still fail closed. Default layout creation now uses trusted no-follow dir-fd traversal, exclusive regular-file creation, and fsync for `.agentdeck`, `state`, events, approvals, and the lock; symlink/non-directory nodes fail without external writes. Config-only reads remain zero-write and the first legitimate mutation safely creates a missing state directory.

Fresh Task 13 verification is 370 focused authoritative-state/daemon CLI/Mission tests, 1,852 required broad daemon/conversation/contracts/agent-CLI/dashboard tests, and 2,747 full-suite tests passing with one skip. Compileall, `git diff --check`, and the no-temporary-daemon/ACP-worker process audit pass.

Task 12 authority truth: every proposed effect is evaluated by independent frozen-scope, permission-policy, and runtime-ownership gates. Client controller possession, ACP recommendations, Worker text, and role context never grant permission. Pending permissions are derived only through the current attempt's durable permission binding and append-only protocol transition lineage, so the scheduler waits for a human and does not advance another Worker. The production daemon ACP sink queues session/turn/update/permission/binding writes through the single service owner and waits on an exact permission/attempt/session waiter. PermissionRequest, its transport update/turn transition, attempt binding, and audit outboxes use one StateStore lock and one atomic save; save failure is full-tree zero-write, exact retry is byte-stable, and conflict is zero-write. Confirmed human decisions wake only that live request, daemon close clears waiters, and restart keeps unknown in-flight ACP admission ambiguous rather than fabricating resume. Governance previews for takeover, return-control, reroute, permission decision, Mission pause/resume/cancel, and force-stop bind canonical facts, expiry, controller generation, and consume-once state; record/consume is durable and audited, while drift, expiry, replay, or generation mismatch is zero-write. Human-owned Workers block automated prompt, takeover requires a safe boundary, return requires reconciliation, and a frozen attempt cannot be rerouted. Governed mutations revalidate authority at execution inside the Task 11 service queue. Lease-gated production `mission.pause`, `mission.resume`, `mission.cancel`, `permission.decide`, `worker.takeover`, `worker.return-control`, `worker.reroute`, and `daemon.force-stop` RPCs derive current facts and use two-call preview/confirm flows; confirmation atomically consumes the preview with the domain transition and audit outboxes. Normal pause/resume/cancel require an idle attempt boundary. Reroute becomes a durable future-attempt override consumed by attempt preparation/readiness and is rejected once an attempt exists. Force stop interrupts only provably unsent attempts and preserves unknown external outcomes as ambiguous before response-drain shutdown; normal stop continues to reject active work.

The final authority/lifecycle repair makes resume/cancel work across separate CLI processes without retaining a lease credential. A deterministic root/Mission/action logical controller acquires generation N for preview, explicitly releases it, and reacquires N+1 for confirm. The StateStore confirmation mutation requires the same logical client and daemon instance, exact N+1 succession, and durable `controller_lease_released` evidence for hashed generation-N lineage before it revalidates facts, consumes the preview, and changes Mission state. Intervening control, expiry/takeover, restart, replay, and fact drift are zero-write failures. ACP shutdown now distinguishes external work from registered Worker cleanup: open/closing/closed state rejects new external work during close, cancels Workers, pumps cleanup until their `finally` blocks settle, drains, and closes the server last. Close is shielded and shared across concurrent callers, cleanup save failure is explicit after resource cleanup, durable session history—not sink memory—drives idempotent busy/ready-to-disconnected persistence, and cancellation during ACP initialize/new-session/activate performs bounded close plus session disconnect. A durably accepted force-stop always requests process shutdown even if the following lease release/flush reports failure; ordinary stop remains unchanged.

Post-review closure makes those boundaries literal in production. After force-stop has committed `stopping`, controller reload, release construction, flush, and response-path cleanup are all inside the shutdown-guaranteeing finalizer. Worker cleanup queue items are accepted only from a task currently registered in the service Worker set, including while OPEN, so an ordinary task cannot forge cleanup before close. ACP admission and prompt cancellation now share a genuinely wall-clock-bounded close-then-disconnect sequence: timeout never waits for a coroutine that swallows cancellation, overdue tasks are explicitly terminated/tracked with consumed outcomes, close-side `CancelledError` still proceeds to bounded disconnect, and cleanup errors never replace the triggering cancellation. The daemon sink submits disconnect authority synchronously on the registered Worker task and publishes the exact persisted session/turn identity before the activation Future resumes, closing the activate-return cancellation window without non-unique native-session lookup.

Final quality closure removes the remaining precedence and liveness shortcuts. Predecessor controller evidence must be the exact singleton state `released`; journal/outbox release+expiry conflict is a zero-write blocker. ACP cleanup re-raises outer cancellation, returns structured close/disconnect status, shields the service-owned durable Future, and makes normal completion fail into existing attempt ambiguity unless durable disconnect succeeds, so no succeeded reply can coexist with a busy/ready session. Activation publishes its exact new session/turn IDs before the service Future resumes and disconnect never falls back to non-unique native identity. A single-operation admission reservation plus pending-cleanup health gate bounds retained cleanup work and resets on factory failure. Service close has a five-second default Worker grace; expiry still closes the server, reports an explicit health failure, consumes eventual task exceptions, and preserves external-outcome facts for restart recovery. Finally, an already durable force-stop remains an accepted `stopping` RPC even if controller cleanup fails: the response carries only a compact credential-free cleanup/restart diagnostic, the durable `daemon_force_stopped` audit remains recovery truth, and process shutdown is still requested.

Repeated cancellation is also fail-closed at the exact authority edge. When a second cancellation interrupts the close wait, `_close_then_disconnect` retains the cancellation-resistant close task, marks close failed, synchronously calls the daemon sink's `begin_disconnect` while still on the registered Worker, retains that durable cleanup awaitable, and only then re-raises cancellation without another await point. Both admission and prompt tests prove the original task remains cancelled, the exact persisted session reaches `disconnected`, and retained close work returns to zero after the test releases the stubborn adapter.

Daemon process teardown now has two explicit bounded layers. `_serve_daemon` always attempts service/server close, durable `stopped` recording, and owned endpoint metadata removal; a close/grace failure is re-raised only after the latter cleanup attempts, so no stale socket or PID metadata survives that failure. The hidden `daemon serve` command alone uses a dedicated event loop: once the main serve coroutine finishes, it cancels pending tasks, waits a fixed five-second grace, consumes completed exceptions, and closes the loop after a compact `pending_task_count` diagnostic if cancellation-resistant work remains. That diagnostic is not proof of task termination. It deliberately preserves submitted/busy durable facts for restart ambiguity, while reachable ACP transport cancellation continues through its existing bounded close/terminate-to-kill plus exact durable disconnect path. No process-group signal is introduced, and all other CLI async entrypoints retain `asyncio.run`.

The managed tmux edge is now bounded before it enters `asyncio.to_thread`: all ten `TmuxBackend` subprocess calls share an explicit five-second timeout, including private-buffer cleanup. `subprocess.run` kills and waits for its direct child on timeout; the transport maps admission/capture timeout to `WorkerTransportError`, so the coordinator retains admission ambiguity or records a failed completion and the scheduler blocks. `agentdeck doctor` catches the same timeout at the backend boundary and emits valid non-success JSON containing only `tmux command timed out`; it never prints the timed-out argv/path or a traceback. Return-control pane verification explicitly treats `TimeoutExpired` as unverifiable runtime evidence, which enters the existing persisted reconciliation-ambiguity path and leaves the takeover baseline active. The real daemon acceptance puts a permanently blocking fake `tmux` on `PATH`, reaches it through the production ProjectView/scheduler/transport path, sends SIGTERM to the real daemon entrypoint, and verifies every main/cleanup fake-tmux PID, daemon PID, socket, metadata file, and detached reaper is gone within timeout plus shutdown grace and margin. This guarantee is intentionally limited to managed transport subprocesses: AgentDeck does not claim it can kill an arbitrary Python thread or unmanaged descendant, and it does not use `killpg`.

That acceptance also closed two adjacent truth gaps. The daemon scheduler and transport factory now read the actual `ProjectView.agents` dataclass field, rather than failing closed on a dict-only `.get` and making tmux execution unreachable. Public `StateStore.save()` now delegates to the existing temp-file/fsync/replace boundary; a deterministic paused-writer regression proves concurrent readers see the complete old JSON until the complete new JSON is atomically installed. Both public `save` and `_atomic_save` fault-injection seams retain their prior behavior.

The repeated full-suite background acceptance exposed one adjacent daemon read/write race rather than a retry-only flake: protocol-event outbox drain held the mutation lock but cleared `state.json` through the legacy truncate/write helper, while external readers intentionally do not take that lock. The lock-owned clear now uses atomic replacement, preserving the existing retry/no-duplicate journal semantics and preventing ProjectView/acceptance readers from seeing an empty file.

Focused TDD evidence includes four Mission RED-to-GREEN cases, five ACP shutdown/admission-cancellation RED-to-GREEN cases, force-stop RED-to-GREEN with ordinary-stop compatibility, a real StateStore + daemon sink + ACP transport shutdown/restart reconciliation case, and a detached-daemon two-process Mission preview/confirm acceptance with no residual endpoint or reaper.

Task 12 review closure tightens four boundaries. The live ACP response edge now treats human approval as policy input only: before selecting `allow_once`, it atomically revalidates the exact `acp` attempt / `acp-adapter` AgentSession / prompt turn / permission binding and runs real frozen-scope, policy, and ownership gates, recording the result. Startup uses the same strict provider/workspace/capability/dispatch lineage and rejects corrupt bindings before scheduler activation. Restart classification is transport-derived: tmux may rely on durable observable receipts, but an active ACP submitted/running connection is ambiguous before permission state, and that persisted blocker is consumed by the live scheduler. Takeover persists a bounded generation-bound baseline for session/turn lineage, artifacts, and a hashed worktree manifest; return-control requires exact bounded `reported_changes`, unchanged protocol/artifact authority, a safe boundary, and an execution-time rescan matching its preview. Missing reports, drift, or unsafe filesystem evidence fail closed, while successful return consumes the baseline and retains its report.

The Task 12 spec re-review additionally forbids empty or merely self-hashed runtime evidence. ACP takeover/return requires one configured target Worker and one exact ready `acp-adapter` session with matching provider/project workspace/native identity/capabilities and no active turn. tmux requires the exact running project binding plus a read-only `pane_exists` verification through that project's configured socket/session; it never probes another project. Projection, gate, or confirmation failure keeps the baseline active and atomically appends an exact ambiguous `worker_reconciliation_decisions[]` record with conversation/recovery audit evidence. ProjectView conversation blockers and matching scheduler facts expose that decision until a fully verified return resolves it. ACP `allow_once` is also durably consume-once: permission/tool-call/effect consumption commits before the allow response, exact replay is byte-stable `permission_consumed`, conflicting lineage fails closed, save-before-response failure grants nothing, and post-commit retry denies rather than risk a duplicate external effect.

Final Task 12 review closure removes the remaining authority and lifecycle gaps. Frozen/admitted Mission resume and natural-language confirmation no longer enter the foreground runner: daemon Mission resume obtains a controller-lease-bound exact preview and confirms only the returned `gov_*` command, while incomplete frozen authority is inspect-only and snapshot-less M1 records retain explicit legacy compatibility. Frozen Worker authority now includes a compact `runtime_identity_hash` over command, ACP transport argv, role prompt, and project runtime backend/session/socket identity without persisting raw invocation values, so later runtime configuration drift cannot silently change the confirmed Worker invocation. A single ACP attempt may now bind multiple sequential permission/tool-call requests, each with independent exact retry, recovery validation, consume-once authorization, conflict rejection, and crash zero-write. ACP transport close persists `disconnected`, so return-control cannot accept a closed session as ready evidence. daemon stop/force-stop now signal exit from the durable commit boundary rather than successful acknowledgement delivery, and service close cancels cooperative Worker tasks without retaining completion work.

The scoped permission handle now owns controller cleanup as a durable terminal obligation. Expiry/capacity purge, confirmation completion/failure, and daemon close must retire the exact private controller generation before discarding the registry record; cleanup failure retains the bounded record and reports an explicit credential-free failure. Production releases an exact active controller, expires an exact elapsed controller, and treats terminal or replacement generations as already inactive, so a 300-second confirmation can no longer leave its 3,600-second controller hidden and a stale handle can never release a newer controller. If the permission decision is already durable but retirement fails, the RPC says `permission state committed; controller cleanup incomplete` rather than returning false success.

The M2 daemon now owns frozen tmux Worker startup as an explicit scheduler transition. It persists one compact start claim before creating the session/pane, treats a lost receipt as ambiguous instead of replaying the spawn, and records the exact binding or sanitized blocker. Scheduler fact loading remains read-only: it derives missing/claimed/started/blocked startup state and performs only a provider-aware tmux readiness probe. Claude/Codex first-run setup is never answered automatically, and dispatch revalidates both `runtime_identity_hash` and the frozen step `task_hash`.

Fresh verification for this final review closure is 22 doctor/contract/reconciliation focused tests passing, 65 managed-tmux/daemon/atomic-save focused tests passing, 414 fault-injection/atomic-state compatibility tests passing, 1,225 broad test-name-selected daemon/ACP/protocol tests passing with one skip, and 2,675 full-suite tests passing with one skip. Compileall and `git diff --check` pass. The detached-daemon governance acceptance verifies endpoint/reaper cleanup, the production serve cleanup regression proves close failure still records `stopped` and removes socket/PID metadata, the daemon-loop runner regression proves cancellation-resistant teardown exits within its bound with only a compact diagnostic, the blocking-tmux OS-process acceptance proves every managed child is reaped, and the in-process real RPC acceptance proves post-commit force-stop cleanup failure still returns accepted/stopping before the endpoint exits.

Task 11 production truth: `mission.admit` persists exact-digest daemon admission or `confirmed_not_admitted`; ProjectView exposes compact `daemon_admission`; `_daemon serve` loads real SchedulerFacts and applies controlled transitions; every RPC mutation and Worker completion returns through one service-owned queue. The exact controller lease is retained and revalidated inside that queue immediately before mutation. Both tmux and ACP persist an exact standalone submitted receipt before completion begins. An ACP crash after session admission is therefore a submitted unknown external effect, never a replayable admission; validated result plus compact reply still commit atomically. Prompt/update/parse/finish/cleanup failure transitions the submitted attempt to a bounded stage-specific ambiguity without persisting exception, command, path, or payload text. ACP and tmux use explicit configured adapters without fallback. The real disconnect acceptance starts `_daemon serve`, admits through `DaemonClient`, closes the client, runs two official-SDK fake ACP Workers, and observes two succeeded attempts, validated replies, recorded handoffs, and a completed Mission in the real StateStore.

The final compact-handoff closure makes that acceptance prove the actual A→B data path rather than only terminal states. Every validated structured transport result, including blocked/failed output, is reduced through the same bounded `CanonicalHandoff` allowlist and stored on the durable reply; only `completed` can advance. ACP and tmux each commit their already-submitted result plus canonical reply in one lock/save. Exact structured non-success retries return the same persisted reply and conflicting content is zero-write. Handoff recording copies and revalidates exact content, audit events bind its canonical hash, and recovery rejects audit/content drift. Production prompt construction resolves only the immediately preceding step in the frozen same-Mission order and requires one exact `succeeded` attempt, one validated reply, and one recorded handoff with the same dispatch token/content. It also revalidates the frozen Worker runtime-identity hash and raw task hash before constructing either transport. It never guesses the latest reply or crosses Mission lineage. The real subprocess test runs twice consecutively, records the prompt each fake ACP Worker actually received, proves reviewer observed planner's recorded handoff before starting, proves planner summary/verification reach reviewer, and excludes private reasoning/full transcript/secret markers. Its cleanup guard begins before startup/admission, discovers delayed PID metadata, and covers every setup/assertion path; graceful stop is followed by bounded TERM/KILL fallback, then production `reconcile_endpoint()` verifies stale ownership/PID before unlinking and the test waits for daemon PID/socket/metadata/reaper cleanup. A deliberate post-admission failure test exercises forced cleanup.

ProjectView admission projection is now independently fail-closed at its read boundary: it reconstructs only the exact five `daemon_admission` fields and accepts only coherent `not_confirmed`, `confirmed_not_admitted`, or `admitted` type/state combinations. Malformed, missing, or extra-field records become a deterministic safe `not_confirmed` sentinel plus a fixed blocker; rejected values and credentials never reach ProjectView, status, or workbench. This projection performs no state/event repair writes.

Task 11 review closure also makes admission response loss converge on durable truth: if the exact Mission was admitted before the response disappeared, the caller receives `accepted=true/state=admitted`, not a contradictory `confirmed_not_admitted` wrapper. The service wakes immediately for queued work and alternates one queued callback with one scheduler opportunity, preventing a self-replenishing RPC/completion queue from starving Mission advancement. Explicit release is recognized from validated durable journal/outbox evidence for the exact lease generation, so daemon restart cannot append a false expiry after release. Focused regressions cover queue-backlogged stale lease rejection with zero writes, prompt-in-flight ACP shutdown retaining `submitted`, ACP completion success/ambiguity/idempotency/conflict behavior, release/stop restart audit ordering, bounded scheduler fairness, and the real disconnect acceptance.

Fresh Task 11 verification after the final compact ProjectView hardening is 887 project-view/contracts/daemon CLI tests, 711 daemon regressions, and 2,571 full-suite tests passing with one skip. The known idle-grace timing test failed once in an earlier full run, then passed both its exact rerun and the final full suite; no unrelated idle-loop code changed. Compileall and `git diff --check` pass.

Task 6's stop path is a complete production flow rather than a test-state shortcut: `daemon stop --confirm` acquires a temporary controller only when no active controller exists, durably flushes lease grant/release audit events, releases before acknowledgement, and sets the server-owned stop event only after response drain. A rejected automatic stop now invokes lease-gated `controller.release`; release must be confirmed or cleanup becomes an explicit blocker, while user-supplied credentials are never auto-released. `controller.renew` and `controller.release` both require the current lease; automatic takeover and background outbox flushing are not implemented. Lease credentials remain RPC-internal and are not added to ProjectView/workbench cards.

The Task 6 quality closure also makes the hidden daemon's idle loop reload the full persisted keepalive view every poll. Client-only activity is `ready`; Mission/Worker/pending approval, permission, reply, recovery/decision/ambiguity, outbox, recovery, safe-shutdown, or atomic-write work is `busy`; only an empty reason set enters `idle_grace`, and a new connection cancels that timer. Live status derives `controller_present` from the current unexpired lease. The idle poll commits and synchronously flushes one terminal expiry transition, so ProjectView cannot retain an active controller indefinitely and repeated polls do not duplicate expiry audit events. `agentdeck daemon status` itself remains zero-write. Strict daemon controls now require `enabled=true` with `blocker=null`, or `enabled=false` with a non-empty blocker.

The final Task 6 spec closure makes offline ProjectView use the same pure time-aware lease predicate as live status: only a strictly parsed active `lse_` lease whose aware expiry is later than current UTC reports `controller_present=true`; expired, terminal, naive, and malformed facts report false without repairing or writing state. DaemonServer also owns a monotonic process-local `activity_generation`: accept and each successfully decoded protocol-valid request increment once, while close never increments. The idle loop remembers the last generation and resets `idle_since` before evaluating keepalive, so a sub-100ms client that connects and closes entirely between polls still grants a new full idle window. This counter is runtime-only, is not added to ProjectView/contracts, and is not execution authority.

Historical routing note: before the Codex probe was made zero-write, the active instruction was to investigate the Task 11 `probe_wrote_files` blocker and rerun preflight before any live attempt. That instruction was completed and is no longer the active route. Frozen historical live results remain evidence only. The approved semantic-authority and Leader Preview observability work at `9db5b476f885cfcf68a55cbf59673a2d908d3fce` used its one explicit-model preflight and one separately authorized live attempt, which stopped at `leader_schema_before_preview` / `semantic_effect_conflict`; neither may be rerun in place. The target-exclusivity and pytest-redaction slice frozen at `75f0366d4d5619b29c77f10949365f43d46185b1` also used exactly one preflight and one live attempt, which stopped at `native_schema_provenance_missing`; neither may be rerun. That blocker was corrected and verified at new frozen SHA `7a76ada81938be3ba0720a7c2f5a540b4beebb3e`, but its own exact `gpt-5.5` cycle is now exhausted at preflight/live `1/1`; live stopped at fixed `preflight_blocked`. The active route is a new design/TDD cycle that binds designated preflight and live to one executable authority and projects the closed internal blocker set; there is no automatic retry. M2c is **BLOCKED**, M3 remains locked, and A2A Client/Server, remote daemon, global roaming, Workspace Client, system notifications, complete transcript persistence, automatic install/auth, Windows IPC, and terminal-emulator work remain out of scope.

The completed natural-language Mission and G-series work below is historical context only. It must not be treated as an active continuation request or redone.

## Canonical Handoff Inputs

When switching from Codex to Claude Code CLI or another local agent, read these files first:

1. `CLAUDE.md`
2. `AGENT.md`
3. Top of `HISTORY.md`
4. `docs/roadmap/ultimate-goal-roadmap.md`
5. This file

Then inspect current state with:

```bash
git status --short
git log --oneline -5
conda run -n agentdeck pytest -q
```

## Current Phase

Phase 0, Phase 1, Phase 2, Phase 3 M1, and Phase 3 M2 implementation Tasks 1–14 are complete and integrated into `main`. M1's final full suite was 1855 passed and 1 skipped; its deterministic and live Claude ACP/PTY evidence is `docs/validation/2026-07-13-phase3-m1-foreground-conversation.md`. M2's earlier final full suite was `2928 passed, 1 skipped`; deterministic evidence and the two-step real transport PASS are recorded in `docs/validation/2026-07-13-phase3-m2-project-daemon.md`. M2c remains **BLOCKED**, not a partial PASS; M3 remains locked. Historical `9db5b476...` and `75f0366d...` preflight/live authorities are exhausted and must not be rerun. The provenance correction is frozen at `7a76ada81938be3ba0720a7c2f5a540b4beebb3e`; focused verification and two unchanged-SHA full suites pass, but its exact `gpt-5.5` preflight/live cycle is now exhausted at `1/1`. The single live stopped before project initialization at fixed `preflight_blocked`. The remaining gate is a new approved design/TDD cycle for same-authority binding and closed internal-preflight diagnostics. Earlier live and `probe_wrote_files` states are historical evidence only, not current routing.

The final M2 security closure anchors audit-journal reads to the verified project state directory and implements append as a bounded 64 MiB full-content temp/fsync/identity-check/atomic-replace transaction. Symlink, non-regular, and journal-inode replacement races cannot append to the detached journal; daemon/conversation/protocol outboxes stay pending on failure and retry without duplicate events. Current writers acquire the stable project-root directory flock before the legacy filename lock, then revalidate project-root/deck/state and lock-file identities around every atomic state or journal effect. Replacing `protocol-mutation.lock` or the whole state directory after proof therefore cannot split current writers, report a detached write as success, clear the canonical outbox, or cause a canonical lost update. The explicit threat boundary is cooperative AgentDeck writers under the opened project-root inode: a same-UID process that ignores advisory locks can move open directories and cause a rejected detached-descriptor effect, while project-root replacement itself is rejected rather than recovered.

The completed Phase 2 diagnostic slice covers one real Agent's initialize, session create/load, prompt, streamed update, permission bridge, completion, disconnect, and resume behavior. M1 composes that ACP client with explicit Leader/Worker transport and foreground conversation contracts while preserving tmux compatibility. M2 moves confirmed Mission advancement into a recoverable project daemon while reusing those primitives rather than creating a parallel authority.

Historical note: G1–G5 frontdesk, coordination-role, loop, worker-lifecycle, review-gate, release-preview, and natural-language discovery slices were completed before the Phase 0/1 protocol-native work. Their detailed behavior remains in `HISTORY.md` and the contract documents; they are not the current phase or next slice.

## Cross-Agent Goal Continuity

Codex App `/goal` is session-local state. It does not automatically transfer into Claude Code CLI.

Claude can still continue the same work by treating this repository as the source of truth:

- `HISTORY.md` is the development timeline.
- `CLAUDE.md` and `AGENT.md` are the behavioral constraints.
- `docs/roadmap/ultimate-goal-roadmap.md` is the north star.
- This handoff file carries the current active goal and next slice.
- Git commits are the durable recovery points.

Suggested prompt for Claude Code CLI:

```text
Please continue AgentDeck development from this repository.
Read CLAUDE.md, AGENT.md, the top of HISTORY.md, docs/roadmap/ultimate-goal-roadmap.md, and docs/handoff/current-development-state.md first.
Use conda activate agentdeck or conda run -n agentdeck for commands.
Every development iteration must update HISTORY.md, run verification, and commit locally.
Treat Phase 3 M2 Tasks 1–14 as complete. Historical live failures remain evidence only; M2c is BLOCKED and M3 remains locked. Do not rerun the historical `9db5b476`, `75f0366d`, or current `7a76ada` preflight/live cycles. The semantic provenance persistence and public contract correction is frozen at `7a76ada81938be3ba0720a7c2f5a540b4beebb3e`, has passed focused verification and two full suites, and has exhausted one ready `gpt-5.5` preflight plus one separately authorized live attempt. Its preflight/live counts are `1/1`; live stopped before project initialization at fixed `preflight_blocked`. The next gate is a new brainstorming/spec/plan and TDD cycle that binds designated preflight and live to one executable authority and exposes only the closed allowlisted internal blocker set. No prior preflight/live authorization carries forward. Do not redo M1 or earlier phases; do not merge/push, auto-install, change authentication, add A2A, remote execution, global roaming, Workspace Client, full transcript persistence, or terminal-emulator work.
```

## Historical development log — not active

Everything below this heading is retained only as historical implementation context. Any wording about a direction, work in progress, a next step, a chosen lane, or verification reflected the state at that earlier time; it is not a current instruction and must not override the active goal above. Use `HISTORY.md` for the durable timeline. Do not resume or redo any item below unless a human explicitly approves it.

The explicit release command slice is already committed:

```bash
agentdeck release --confirm
```

Expected behavior:

- Refuses without `--confirm` and writes nothing.
- Validates ProjectView, then reuses the same `review_gate_card` facts.
- Refuses when the gate is blocked, appending `round_release_rejected` with the same gate `reason`.
- Refuses when the same code-review / round-review reply pair was already released (`round already released`).
- On success appends a release record to `releases[]` plus a `round_released` audit event, and returns a GUI-ready payload with `safety=explicit_user`, trace commands for both review replies, and a disabled `agentdeck leader plan --task <goal>` next-round template.
- Does not merge, ack inbox items, dispatch follow-up work, create plan/action/approval/message/job/inbox, call a provider, or read/write tmux.

The release-preview wiring slice is already committed:

```bash
agentdeck workbench
```

New behavior:

- When the review gate is ready, `release_preview_card.release_command` / `next_command` point at the explicit `agentdeck release --confirm` command and the `release_preview` control becomes an enabled `explicit_user` control with the same command.
- `next_round_command` exposes the disabled `agentdeck leader plan --task <goal>` template with blocker `requires goal text`.
- While the gate is blocked, all three command fields stay `null` and the explicit controls stay disabled with the gate reason.
- The workbench validator rejects an enabled release control without `can_release=true` or with a command that drifts from `release_command`.
- Rendering the card still never releases; only a human running `agentdeck release --confirm` records the round release.

The release history slice is already committed:

```bash
agentdeck status
agentdeck workbench
```

New behavior:

- ProjectView exposes a top-level `releases` summary (`count`, `items[]`); each item carries the release id, round number, review-gate snapshot, both reviewer/reply ids, and a `trace_command` pointing at the round-review reply lineage.
- `release_preview_card` gains `already_released`, `release_count`, and `latest_release_id` derived from the same summary.
- When the review gate is ready but the current code-review / round-review reply pair was already released, the card reports `status=released` with reason `round already released`, withdraws `release_command` / `next_command`, and keeps only the disabled next-round plan template.
- Validators reject a released card that still exposes executable release commands and require a ready review gate behind any released card.

The release contract discovery slice is already committed (Phase G5 complete):

- Read-only `agentdeck contract release` / `--example` discovery, and `agentdeck release --confirm` now self-validates via `validate_release_contract()`.

## Historical G6 context

Phase G6 Role Topology GUI was completed before the Phase 0/1 protocol-native work. The following entries are retained only as implementation history, not as an active phase or continuation instruction.

The first G6 slice is already committed:

```bash
agentdeck workbench
```

- Adds `role_topology_card`, a read-only unified role topology (logical roles + worker roles, each with kind/provider/lifecycle/status/blocker/next_command and an inspect-only control).

The second G6 slice is already committed:

```bash
agentdeck workbench
```

New behavior:

- `role_topology_card` now overlays the `review_gate_card` stage status onto the matching reviewer worker role: a `ready` stage → `status=reviewed`, a `waiting_for_review` stage → `status=reviewing` (no blocker), and any other stage (`waiting_for_artifacts` / `blocked`) → `status=blocked` with the stage's blocker.
- Non-reviewer worker roles keep their base `lifecycle_stage` status with a `null` blocker.
- Still read-only: the overlay never advances the gate, spawns, dispatches, captures, acks, releases, or writes state.

The first G6 surface details:

- Adds `role_topology_card`, a read-only unified role topology.
- Projects the three logical Leader coordination roles (`frontdesk`, `planner`, `orchestrator`) from `leader.coordination_roles[]` plus the configured worker roles from the same `worker_lifecycle_card` items.
- Each role carries `kind` (`logical_role` | `worker`), `provider`, `lifecycle`, `runtime_kind`, `pane_backed`, `pane_id`, a derived `status`, `blocker`, `next_command`, and a single inspect-only control.
- Logical roles keep `runtime_kind=logical_role` / `pane_backed=false` / `pane_id=null` / `agent_id=null`; their inspect control points at their own read-only state source (`frontdesk` → `agentdeck leader chat-history`, `planner` → `agentdeck plan list`, `orchestrator` → `agentdeck leader actions`). Worker roles use `runtime_kind=worker_pane`, reuse the worker `lifecycle_stage` as `status`, and inspect via `agentdeck inbox --agent <id>`.
- All controls appear in `control_registry[]` / `agentdeck controls` under `scope=role_topology`.
- Does not spawn, dispatch, capture, ack, release, or write state; every control is inspect-only.

The third G6 slice is already committed:

```bash
agentdeck leader chat --message "查看角色拓扑"
```

Expected behavior:

- Returns read-only `mode=role_topology`.
- Embeds the same `role_topology_card` as `agentdeck workbench`.
- Attaches a `control_registry_card` filtered to `scope=role_topology` / `card=role_topology_card`, selecting the card-level `agentdeck workbench` inspect control.
- Records only the chat turn and its audit event; does not call a provider, create plan/action/approval/message/job/inbox, spawn, dispatch, capture, ack, release, or read/write tmux.

The fourth G6 slice is already committed:

```bash
agentdeck workbench
```

New behavior:

- The `role_topology_card` logical-role overlay now marks `orchestrator` as `waiting_for_approval` (blocker `waiting for human approval`) when any approval is pending, `coordinating` when a pending Leader action exists, `released` when at least one round has been released, and `idle` otherwise.
- Only `orchestrator` carries a blocker; `frontdesk`/`planner` keep `null`.
- Still read-only: the overlay only projects ProjectView facts and writes no state.

The fifth G6 slice is already committed:

```bash
agentdeck workbench
```

New surface:

- `role_topology_card` now carries `by_status` (per-status counts) and `blocked_count` (roles with a non-null blocker); the validator requires `blocked_count` to match the roles carrying a blocker.

The sixth G6 slice is already committed (test-only coverage):

- A project configuring agents with roles `code_reviewer` / `round_reviewer` surfaces them as distinct worker roles; with an artifact but no review replies the code reviewer shows `reviewing` and the round reviewer shows `blocked` (`code review is not ready`). Worker order follows configured agent order.

Phase G6 (Role Topology GUI) is now functionally complete: workbench `role_topology_card` (logical + worker roles, review-gate overlay, orchestrator approval/release overlay, status summary) plus the read-only natural-language `role_topology` chat discovery.

The seventh G6 slice is already committed:

- The natural-language `role_topology` chat `leader_explanation.summary` now reports role count and blocked count (e.g. "...role topology with 6 roles (1 blocked)...").

Phase G6 (Role Topology GUI) is complete across workbench + natural-language surfaces.

The layered-role walkthrough is already committed:

- `docs/walkthroughs/layered-role-round.md` walks a full round (frontdesk intake → coordination topology → plan → approval → dispatch + worker lifecycle → review gate → release → role topology → recovery/loop) against the read-only contract surfaces and explicit human commands, cross-linking each phase's contract. Linked from the README top.

Phases G1–G6 are complete and now documented end-to-end.

## Historical direction: TUI reference client

The user chose to build a read-only TUI/CLI reference client that consumes the workbench + control_registry contracts, proving the contracts are sufficient to drive a GUI (no new backend behavior).

The first slice is already committed:

```bash
agentdeck dashboard
```

- Adds `src/agentdeck/dashboard.py` with the pure function `render_workbench_dashboard(payload)` and the `agentdeck dashboard` command.
- Renders header / recovery / role topology / review gate / queue as human-readable text, deriving every value and echoed command from the workbench contract payload alone.
- Reuses the same `_workbench_snapshot_payload` + `validate_workbench_contract()` as `agentdeck workbench`; read-only, no state writes.

The second and third slices are already committed:

- Slice 2: a "Command palette" section from `control_registry[]` grouped by scope (total / enabled / blocked per scope) with a `agentdeck controls --scope <scope>` drill-down pointer.
- Slice 3: "Release" and "Ledger" sections derived from `release_preview_card` (shows `agentdeck release --confirm` when ready) and `ledger_card` counts.

The dashboard now renders: header, recovery, role topology, review gate, release, ledger, queue, command palette — all from the workbench contract payload alone.

The fourth slice is already committed:

- `docs/walkthroughs/tui-reference-client.md` documents the reference client (section→card mapping, real sample output, the sufficiency argument), linked from the README `dashboard` paragraph.

The TUI reference-client direction is complete: `agentdeck dashboard` renders header / recovery / role topology / worker activity / review gate / release / ledger / queue / command palette purely from the `agentdeck workbench` contract, with tests (`tests/test_dashboard.py`) and a doc. A worker-activity section (per-worker lifecycle stage + active task ids + inbox/artifact counts) was added as polish.

## Historical autonomous run (completed directions 1 → 2 → 3)

The user approved doing all three directions in order, autonomously, overnight. Progress:

- Direction 1 (assisted run flow): first slice committed — a read-only "Run progress" section in `agentdeck dashboard`, derived from the existing `run_progress_card`, showing plan/step/approval status and the single explicit next command. It guides the human step-by-step but never executes (approval discipline preserved).
- Direction 2 (learning-layer GUI, Phase F): three slices committed — (a) a read-only "Learning layer" section in `agentdeck dashboard`; (b) `agentdeck learn review` defaults `--plan-id` to the latest plan; (c) a workbench `learning_review_card` (the earlier-deferred item, now done at the user's request): mirrors `leader_summary_card` — `null` until the latest plan review is `next_action=summarize`, then reuses the `agentdeck learn review` shape and enters `control_registry[]` under `scope=learning_review`. Read-only; the explicit `skills suggest` / `memory suggest` commands remain the only write path.
- Direction 3 (dashboard `--watch` polish): committed — `agentdeck dashboard --watch [--interval N] [--iterations N]` re-renders the text dashboard, mirroring `workbench --watch`, still read-only.

All three approved directions (1 → 2 → 3) have landed committed slices; the whole run kept the suite green (621 passing after the workbench `learning_review_card`).

## Historical direction: interactive curses TUI

`agentdeck tui` is a read-only interactive curses viewer over the workbench contract. First slices committed:

- `src/agentdeck/tui.py` with the pure, unit-tested `TuiModel` (navigation/selection/scroll/refresh) and `render_frame(model, height, width)` (screen layout); the curses I/O in `run_tui` is a thin shell.
- `agentdeck tui` command: builds+validates the workbench snapshot, launches curses; declines cleanly when not a TTY.
- Overview (scrollable dashboard) + palette (browsable `control_registry[]`); footer shows the selected control's safety/enabled/blocker and the exact `run: <command>`. Strictly read-only — it never executes.

A palette filter is also committed: `/` in the palette opens a filter prompt; `TuiModel.set_filter(text)` narrows controls by substring across scope/kind/label/command, re-clamping selection. Read-only.

All three optional TUI polish items are now committed: (1) the palette focuses the recovery `next_command` on open; (2) `?`/`h` opens a key-legend help overlay; (3) palette rows are colorized (selected reverse, disabled dim). All read-only; the styling decision is a pure, unit-tested `palette_row_style` / `palette_row_styles`.

## Historical next-step note

**The whole autonomous-mode goal (all three sub-projects) is done.** All three preserve human approval and keep every read-only surface read-only.

- **Sub-project 1 of 3 — audit / HISTORY gate (done)**: `agentdeck history` renders the `events.jsonl` ledger into a read-only, newest-first, date-grouped Markdown timeline (`src/agentdeck/history.py`, `StateStore.all_events()`, `tests/test_history.py`), with `--write` materializing `.agentdeck/HISTORY.md` and `--limit N` to cap. Design + plan: `docs/superpowers/specs/2026-07-08-agentdeck-history-timeline-design.md` and `docs/superpowers/plans/2026-07-08-agentdeck-history-timeline.md`.
- **Sub-project 2 of 3 — bounded autonomous mode (done)**: `AutonomousPolicy` + `[autonomous]` config (`models.py`/`config.py`), the pure `select_auto_approvals` decision (`src/agentdeck/autonomy.py`), `agentdeck policy set-mode --mode autonomous --confirm --allow-agent <id> --max-approvals <N>` (validated allowlist/budget writer), and `agentdeck approval auto --confirm` (auto-approve allowlisted, budget-bounded pending approvals and dispatch them to already-running panes — no force-spawn, stops at dispatch, fully audited). `control_mode_card` autonomous is enabled with a disabled `set_mode` template. Design + plan: `docs/superpowers/specs/2026-07-08-autonomous-mode-design.md` and `docs/superpowers/plans/2026-07-08-autonomous-mode.md`.
- **Sub-project 3 of 3 — executing round loop (done)**: `agentdeck run-loop --plan-id <id> --confirm` is the write counterpart to the read-only `agentdeck loop`. It performs one sanctioned autonomous wave for a plan (auto-approve allowlisted pending within budget via `select_auto_approvals`, dispatch approved-and-ready to running panes via the existing dispatch internals), then reuses `leader review` + the pure `run_loop_gate` (`src/agentdeck/autonomy.py`) to diagnose the resulting human gate and stops there with an explicit `next_command` (`stopped_reason` ∈ error/blocked/needs_human_approval/waiting_for_reply/complete/idle). Requires `--confirm` + autonomous mode; never force-spawns; never captures replies or infers completion; fully audited (`run_loop_advanced` → `agentdeck history`). Contract: `agentdeck contract run-loop` + `docs/contracts/run-loop-schema.md`. Design + plan: `docs/superpowers/specs/2026-07-08-run-loop-engine-design.md` and `docs/superpowers/plans/2026-07-08-run-loop-engine.md`.

The interactive TUI is feature-complete (overview/palette/help, filter, refresh, focus, colors) and fully tested — `run_tui` is covered end-to-end via a fake stdscr (`tests/test_tui.py`). The TUI/dashboard reference-client line is done.

An end-to-end integration test now locks the whole autonomous chain across invocations: `tests/test_agent_cli.py::test_run_loop_drives_plan_to_completion_across_invocations` (policy set-mode autonomous → run-loop auto-approve+dispatch → `waiting_for_reply` gate → capture-reply → run-loop → `complete`, with two `run_loop_advanced` ledger events).

The autonomous commands are now **surfaced into the read-only command palette** (done): `control_mode_card.autonomous_actions[]` carries `kind=approval_auto` (`agentdeck approval auto --confirm`, `safety=delegated`, enabled only in autonomous mode, else blocker `autonomous mode is not enabled`) and a disabled `kind=run_loop` template (`agentdeck run-loop --plan-id <id> --confirm`, blocker `requires --plan-id`); both flow into `control_registry[]` / `agentdeck controls --scope autonomous` under `scope=autonomous`. Both the cli `_workbench_control_registry` and the mirror `contracts.workbench_control_registry` (used by `validate_workbench_contract`'s cross-check) append the group; the `workbench_example()` fixture was updated to match. Rendering is not authorization — the commands still require explicit human `--confirm`. Design + plan: `docs/superpowers/specs/2026-07-08-autonomous-controls-lighting-design.md` and `docs/superpowers/plans/2026-07-08-autonomous-controls-lighting.md`.

The final GUI-mainline follow-up is now **done**: `agentdeck leader chat --message "推进计划 pln_xxx"` (and `往前推`/`驱动计划`/`run-loop` variants) enters read-only `mode=run_loop_preview`, embeds `run_loop_preview_card`, hands back the explicit `agentdeck run-loop --plan-id <id> --confirm` as top-level `next_command`, and attaches a `scope=autonomous` `control_registry_card` whose selection points at the disabled `run_loop` template. It requires a plan id (no guessing), the next control is `safety=explicit_runtime` (disabled with `autonomous mode is not enabled` when autonomous is off), and the chat records only the chat turn + `leader_chat_turn` audit event — never a provider call, tmux read/write, auto-approve, dispatch, or approval/runtime/plan mutation. Detectors + card builder: `_chat_wants_run_loop_preview` / `_chat_run_loop_preview_plan_id` / `_run_loop_preview_card` (cli.py); contract: `run_loop_preview_card_fields` + the `run_loop_preview` mode check in `validate_leader_chat_contract` (contracts.py). Design + plan: `docs/superpowers/specs/2026-07-08-run-loop-chat-intent-design.md` and `docs/superpowers/plans/2026-07-08-run-loop-chat-intent.md`.

**Historical next-step note:** The autonomous-mode goal and its full GUI-mainline surfacing (command palette `scope=autonomous` + natural-language `mode=run_loop_preview`) were complete. At that time the human delegated the next direction ("你帮我决定"), and the historical selection was **"make the contracts visible — grow the human-facing dashboard/TUI cockpit"** (local, deterministic-testable via pure renderers + fake stdscr, directly monetizes the large read-only-contract investment).

Two slices of that lane are **done** (both in `render_workbench_dashboard`, shared by `agentdeck dashboard` and the TUI overview via `tui.py`):
1. **Control mode** section (`_render_control_mode`, `src/agentdeck/dashboard.py`) — the ask/approve/autonomous gradient + `approval auto` / `run-loop` command hints with enabled/blocked state. Test: `tests/test_dashboard.py::test_render_workbench_dashboard_shows_control_mode_and_autonomous_commands`.
2. **Runtime** section (`_render_runtime`) — the visible tmux binding: `<running>/<total> running` + each agent's `agent_id · role · status · pane:<pane_id>` from `runtime_card.agents[]` (distinct from logical `role_topology` and `worker_activity`). Test: `tests/test_dashboard.py::test_render_workbench_dashboard_shows_runtime_pane_binding`.
3. **Recent activity** section (`_render_recent_activity`) — the audit-ledger tail: `<event_count> events (agentdeck events --limit 20)` + up to 5 recent events (`created_at · event_type · event_id`) from `audit_card`, complementing the full `agentdeck history` timeline. Test: `tests/test_dashboard.py::test_render_workbench_dashboard_shows_recent_activity_ledger_tail`.

The dashboard/TUI overview now lays out: Header → Recovery → Run progress → Runtime → Role topology → Worker activity → Review gate → Release preview → Ledger → Queue → Control mode → Learning layer → Recent activity → Command palette.

The interactive TUI (`src/agentdeck/tui.py`) also gained two navigable read-only modes alongside overview/palette/help, each with select + status-aware command footer (commands straight from contract fields; the TUI never executes):
- **`approvals`** (`[a]`) over `approval_card.approvals[]` — footer command is pending→approve, approved→dispatch, else preview. Tests: `test_tui_model_approvals_view_navigates_and_shows_status_aware_command` / `test_tui_render_frame_approvals_lists_items`.
- **`runtime`** (`[g]`) over `runtime_card.agents[]` — rows show status·agent·role·pane; footer command is running→capture, else spawn. Tests: `test_tui_model_runtime_view_navigates_and_shows_status_aware_command` / `test_tui_render_frame_runtime_lists_agents`.

The TUI is now a view→run bridge: on quit it returns/prints the currently-focused command (`TuiModel.focused_command()` — palette control / status-aware approval / status-aware agent command; `run_tui` returns it; `tui_command` prints it after curses teardown). Still read-only — it prints, never executes. Tests: `tests/test_tui.py::test_tui_model_focused_command_reflects_active_view` / `::test_run_tui_returns_focused_command_on_quit`.

The "make the contracts visible" lane is now substantial (dashboard: Control mode + Runtime + Recent activity sections; TUI: approvals + runtime interactive views + print-selected-command-on-quit).

Already done, do NOT redo: `agentdeck dashboard --watch [--interval N] [--iterations N]` exists (`dashboard_command`, cli.py); `learning_review_card` is already a read-only workbench card (`_workbench_learning_review_card`, cli.py:1480).

## Historical direction: multi-plan lane ("多个计划同屏可见、分别推进")

The human picked the multi-plan-parallel lane: see all active plans at once and drive any of them separately. The state layer is already per-plan (`list_plans`, `plan_by_id`, `plan_status`, `leader_review`); the gap was purely visibility — nearly every read-only surface defaults to the single latest plan (`plans[-1]`).

**Slice 1 of the multi-plan lane is done:** read-only `agentdeck plan board` — a multi-plan overview that lists every plan with its derived `gate` and explicit per-plan `next_command`, plus `plan_count` / `active_count`. It reuses only the read-only `store.leader_review(plan_id)` + the pure `run_loop_gate(review, False, plan_id)` (`src/agentdeck/autonomy.py`); it calls no provider, reads no tmux, writes no state, appends no event. Contract: `agentdeck contract plans` + `docs/contracts/plans-schema.md` (`plan_board_*` helpers + `validate_plan_board_contract` in `contracts.py`, registered in `CONTRACT_INDEX_SPECS`). Design + plan: `docs/superpowers/specs/2026-07-09-plan-board-design.md` and `docs/superpowers/plans/2026-07-09-plan-board.md`.

**Slice 2 of the multi-plan lane is done:** the board is now embedded in the one-screen `agentdeck workbench` snapshot as `plan_board_card` (always present, never `null`). A shared helper `_plan_board_payload(store)` (`src/agentdeck/cli.py`) builds the same payload for both `agentdeck plan board` and `_workbench_snapshot_payload`; `WORKBENCH_SNAPSHOT_FIELDS` carries `"plan_board_card"`, `validate_workbench_contract` runs `validate_plan_board_contract` on the embedded card (prefix `plan_board_card: `), `workbench_example()` embeds `plan_board_example()`, and the workbench contract discovery payload exposes `plan_board_card_fields`. Doc: `docs/contracts/workbench-schema.md`. Read-only.

**Slice 3 of the multi-plan lane is done:** a read-only **Plans** section in `render_workbench_dashboard` (`_render_plans`, `src/agentdeck/dashboard.py`), derived from the `plan_board_card` — `<active>/<total> active` + one row per plan (`plan_id · active/done · gate · task`) with an indented `→ <next_command>`; shared by `agentdeck dashboard` and the TUI overview. Test: `tests/test_dashboard.py::test_render_workbench_dashboard_shows_plans_board` (a position-brittle TUI viewport assertion was repointed from "Role topology" to "Run progress"). Read-only.

**Slice 4 of the multi-plan lane is done:** a navigable read-only **`plans`** mode in the TUI (`src/agentdeck/tui.py`, key `[b]` for board), mirroring the approvals/runtime views — rows = `active/done · gate · plan_id · task`, footer = the selected plan's `next_command`, and `focused_command()` returns it on quit. Consumes `plan_board_card.plans[]`; never executes. Tests: `tests/test_tui.py::test_tui_model_plans_view_navigates_and_shows_next_command` / `::test_tui_render_frame_plans_lists_items`.

**Slice 5 of the multi-plan lane is done:** a natural-language read-only **`mode=plan_board`** chat intent (`agentdeck leader chat --message "查看所有计划" / "计划看板" / "所有计划" / "查看计划列表" / "计划总览" / "plan board"`). `_chat_wants_plan_board` (`src/agentdeck/cli.py`) routes without a plan id and without colliding with `run_progress`/`run_loop_preview` (those require `进度`/`推进`); the route embeds the same `_plan_board_payload(store)` card, sets `next_command="agentdeck plan board"`, `leader_explanation.action_kind=plan_board` (`safety=inspect`, `requires_explicit_user=false`), `intent_card.embedded_card=plan_board_card`, and `control_registry_card=None` (no `scope=plan_board` group). Contract: `plan_board_card` added to `LEADER_CHAT_RESPONSE_FIELDS` + leader-chat example + `LEADER_CHAT_PLAN_BOARD_CARD_FIELDS` + a `mode=plan_board` branch in `validate_leader_chat_contract` (reuses `validate_plan_board_contract`). Docs: `docs/contracts/leader-chat-schema.md`, `CLAUDE.md`, `README.md`. Tests: `tests/test_agent_cli.py::test_leader_chat_plan_board_is_read_only_and_embeds_board` / `::test_leader_chat_plan_board_variants_route`. Read-only.

**Read-only multi-plan visibility is now fully delivered** by the plan board command (slice 1) + workbench `plan_board_card` (slice 2) + dashboard Plans section (slice 3) + TUI `plans` view (slice 4) + this NL `mode=plan_board` intent (slice 5). Nothing further is needed to *see* the multi-plan state.

**Slice 6 (final) of the multi-plan lane is done — the lane is COMPLETE:** the **parallel scheduler** `agentdeck run-loop --all --confirm` (`_run_loop_all` + `_busy_agents`, `src/agentdeck/cli.py`). One round-robin wave over active plans (creation order), reusing the run-loop wave primitives, with a **shared** `max_approvals` budget and **skip-on-contention** (busy = dispatched-unreplied; recorded in each plan's `skipped_contention[]`), then stops. The human resolved the fork: 轮转 / 跳过 / 一波 / 复用. Single-plan `run-loop --plan-id` is byte-for-byte unchanged (the scheduler is additive). Contract: `agentdeck contract run-loop-all` + `docs/contracts/run-loop-all-schema.md` (`run_loop_all_*` helpers + `validate_run_loop_all_contract`). Audited via `run_loop_all_advanced` (`agentdeck history` → "Parallel wave · N plans, M dispatched"). Design + plan: `docs/superpowers/specs/2026-07-09-parallel-scheduler-design.md` and `docs/superpowers/plans/2026-07-09-parallel-scheduler.md`.

Multi-plan **recovery arbitration** (making `recovery`/`agentdeck continue` recommend *across* plans, not just `plans[-1]`) remains deliberately deferred: the read-only multi-plan visibility is fully delivered (plan board + workbench card + dashboard Plans + TUI plans view + NL `mode=plan_board`), and cross-plan steering is a scheduler-policy concern — revisit only if a concrete need appears.

**Historical next-step note:** the whole multi-plan lane (read-only visibility + parallel scheduler) was complete. At that time the remaining product-fork options were a standalone **GUI client**, a **Skill Registry marketplace/allowlist**, or **remote access / MCP**. This record does not select or authorize any current lane.

(Not yet wired: a `control_registry[]` `scope=plan_board` entry — deferred until a plan-board control surface is actually needed. The NL intent deliberately carries `control_registry_card=None`, so this is still not required.)

Whatever is chosen next must preserve human approval and keep every read-only surface read-only.

## Historical direction: skill marketplace lane

The human opened the **Skill Registry marketplace/ecosystem** lane (one of the forks offered after the multi-plan lane closed). The north star: a browsable, importable, reviewable, auditable skill ecosystem — built-in + external sources — where nothing installs silently and every install stays preview-gated and audited.

**Slice 1 of the skill-marketplace lane is done:** read-only `agentdeck skills catalog --source <dir>` — a "shop window" over a local skill source directory of `<name>/SKILL.md`. New pure `browse_skill_source(dir)` (`src/agentdeck/skills.py`, reuses `_snapshot_from_content`); `skills_catalog_command` (`src/agentdeck/cli.py`) compares each source skill against `discover_skills(root)` **project-sourced** skills for a three-state `import_status` (`not_imported` / `imported_identical` / `imported_differs` by name + content_hash) and surfaces per-item `import-preview` / `import` commands + controls. Response fields `SKILLS_CATALOG_RESPONSE_FIELDS` / item fields `SKILLS_CATALOG_ITEM_FIELDS`, exposed via the existing `agentdeck contract skills` (`catalog_command` / `catalog_response_fields` / `catalog_item_fields` — no new contract-index entry). Read-only: copies no files, writes no state, appends no event, calls no provider, touches no tmux; browsing never installs (install still goes through the explicit, preview-gated, audited `skills import --path <SKILL.md>`). Design + plan: `docs/superpowers/specs/2026-07-09-skill-catalog-design.md` and `docs/superpowers/plans/2026-07-09-skill-catalog.md`. Tests: `tests/test_agent_cli.py -k skills_catalog`, `tests/test_contracts.py::test_skills_contract_exposes_catalog_fields`.

**Slice 2 of the skill-marketplace lane is done (read-only, NON-ENFORCING trusted-source allowlist):** `[skills] allowed_sources` is now a hand-edited list of trusted local skill source dirs in `.agentdeck/config.toml`, parsed into `config.skills["allowed_sources"]` (default empty) and round-tripped through `_dump_config` so other config writers (`update_leader_approval_mode`, `update_autonomous_policy`) no longer drop a hand-added `[skills]` section (a round-trip test locks this). Read-only `agentdeck skills sources` lists the configured sources (`mode=skills_sources`, `source_count`, `sources[]` = `{path, exists, catalog_command}`, inspect controls). `agentdeck skills catalog` gained a top-level `source_allowlisted` (bool: True when the resolved `--source` equals or sits under a configured allowed source). It is **NON-ENFORCING**: any dir is still fully browsable and the catalog still lists everything — the flag is just a marker. Contract: `SKILLS_SOURCES_RESPONSE_FIELDS` + `source_allowlisted` on `SKILLS_CATALOG_RESPONSE_FIELDS`, exposed via `agentdeck contract skills` (`sources_command` / `sources_response_fields`); docs in `docs/contracts/skills-schema.md`. **REORDER rationale:** the trusted-source allowlist was pulled ahead of the workbench/NL surfaces so the later `skills_catalog_card` / "浏览技能源" intent can browse the *configured* sources with **no argument** (they need the config list to exist first).

**Slice 3 of the skill-marketplace lane is done (read-only workbench embed):** `agentdeck workbench` now always embeds a `skills_catalog_card` — a no-argument overview of the configured skill sources (config `[skills] allowed_sources`). Fields: `mode="skills_catalog"`, `source_count`, `total_skill_count`, `imported_count`, `sources[]` = `{path, exists, skill_count, imported_count, catalog_command}` (`catalog_command = agentdeck skills catalog --source <path>`). Derived in `_skills_catalog_card(config)` (`src/agentdeck/cli.py`), reusing `browse_skill_source` + shared helpers `_project_skill_hashes` / `_catalog_import_status` (extracted from `skills_catalog_command`, which now reuses them). Contract: `WORKBENCH_SNAPSHOT_FIELDS` + `WORKBENCH_SKILLS_CATALOG_CARD_FIELDS` / `WORKBENCH_SKILLS_CATALOG_SOURCE_FIELDS`, validated in `validate_workbench_contract` (ALWAYS present, never null), embedded in `workbench_example()`, exposed via `agentdeck contract workbench` (`skills_catalog_card_fields` / `skills_catalog_source_fields`). Docs: `docs/contracts/workbench-schema.md`. Read-only: copies no files, imports/loads no skills, writes no state, appends no event, calls no provider, touches no tmux. Test: `tests/test_agent_cli.py::test_workbench_embeds_skills_catalog_card`.

**Slice 4 of the skill-marketplace lane is done (read-only NL intent):** a natural-language read-only **`mode=skills_catalog`** chat intent (`agentdeck leader chat --message "浏览技能源" / "查看技能源" / "技能源" / "技能市场" / "技能目录" / "skill sources" / "skill catalog"`). `_chat_wants_skills_catalog` (`src/agentdeck/cli.py`) routes on the specific 技能源/技能市场/技能目录/`skill sources|catalog|marketplace` phrases (guarded so it does not collide with plan_board / run_progress / run_loop_preview); the route embeds the same no-argument `_skills_catalog_card(config)` card, sets `next_command="agentdeck skills sources"`, `leader_explanation.action_kind=skills_catalog` (`safety=inspect`, `requires_explicit_user=false`), `intent_card.embedded_card=skills_catalog_card`, and `control_registry_card=None` (no `scope=skills_catalog` group — mirrors plan_board). Contract: `skills_catalog_card` added to `LEADER_CHAT_RESPONSE_FIELDS` + leader-chat example + `LEADER_CHAT_SKILLS_CATALOG_CARD_FIELDS = WORKBENCH_SKILLS_CATALOG_CARD_FIELDS` + a `mode=skills_catalog` branch in `validate_leader_chat_contract` + discovery `skills_catalog_card_fields`. Docs: `docs/contracts/leader-chat-schema.md`, `CLAUDE.md`, `README.md`. Tests: `tests/test_agent_cli.py::test_leader_chat_skills_catalog_is_read_only` / `::test_leader_chat_skills_catalog_variants_route`. Read-only.

**Read-only skill-source visibility is now fully delivered** by the catalog command (slice 1) + trusted-source allowlist + `agentdeck skills sources` (slice 2) + workbench `skills_catalog_card` (slice 3) + this NL `mode=skills_catalog` intent (slice 4). Nothing further is needed to *see* the configured skill sources.

**Skill dependencies (decision "B") slice 1 is done (read-only resolution):** `SKILL.md` frontmatter may declare a `depends_on` list (parsed onto `SkillSnapshot.depends_on` via `_metadata_list`; `summary()` unchanged). New pure `resolve_skill_dependencies(root, name)` (`src/agentdeck/skills.py`) does a DFS over `discover_skills(root)` yielding `depends_on` / sorted `resolved` / `missing` / `has_cycle` (+ `cycle` path) / topological `order`. Read-only `agentdeck skills deps --name <name>` (`skills_deps_command`, `src/agentdeck/cli.py`) wraps it as `mode=skills_deps` with inspect-only `agentdeck skills show --name <dep>` controls, self-validated via `validate_skills_deps_contract` (`SKILLS_DEPS_RESPONSE_FIELDS`), exposed via `agentdeck contract skills` (`deps_command` / `deps_response_fields`). Read-only: loads nothing, imports nothing, writes no state, appends no event, calls no provider, touches no tmux; `depends_on` is **parsed but NOT acted on** (no auto-load, no auto-import). Design + plan: `docs/superpowers/specs/2026-07-09-skill-dependencies-design.md` and `docs/superpowers/plans/2026-07-09-skill-dependencies.md`. Tests: `tests/test_agent_cli.py -k skills_deps` + `test_resolve_skill_dependencies_transitive_missing_and_cycle`, `tests/test_contracts.py -k skills`.

**Skill dependencies (decision "B") slice 2 is done (read-only unmet-deps note on load-preview):** `agentdeck skills load-preview --name <name>` now also returns `unmet_dependencies` (`list(resolution["missing"])`) and `has_dependency_cycle` (`bool(resolution["has_cycle"])`), computed by reusing `resolve_skill_dependencies(Path(config.root), args.name)` in `skills_load_preview_command` (`src/agentdeck/cli.py`) just before printing (only when the skill exists; the existing preview error path handles unknown skills). Added to `SKILLS_LOAD_PREVIEW_RESPONSE_FIELDS` (`src/agentdeck/contracts.py`) and to the skills-contract example fixture `load_preview` (both `[]` / `False`). READ-ONLY + NON-BLOCKING: the note is informational only — it does not block the preview, does not auto-load or auto-import any dependency, writes no state (`skill_loads[]` untouched), appends no event, calls no provider, touches no tmux; `skills load` behavior unchanged. Tests: `tests/test_agent_cli.py::test_skills_load_preview_surfaces_unmet_dependencies`, `tests/test_contracts.py -k skills`.

**Skill dependency auto-load (decision "B-auto") is done (preview + explicit confirm, never silent):** new pure `_skill_load_plan(config, store, name, agent)` (`src/agentdeck/cli.py`) reuses `resolve_skill_dependencies` + the agent's `skill_loads` to build a deps-first plan (`order` items `{name,status,source}`, `to_load` / `already_loaded` / `missing` / `has_cycle` / `cycle` / `blockers` / `can_load` / `confirm_command`). Read-only `agentdeck skills load-plan --name <name> --agent <id>` (`skills_load_plan_command`) wraps it as `mode=skill_load_plan` with inspect-only `skills show` controls, self-validated via `validate_skill_load_plan_contract` (`SKILL_LOAD_PLAN_RESPONSE_FIELDS`); it writes no state (a test asserts state unchanged). `agentdeck skills load --name <name> --agent <id> --with-deps --confirm` (`_skills_load_with_deps`, branched at the top of `skills_load_command`) loads each `to_load` skill deps-first via `store.record_skill_load` + a `skill_loaded` event each, then one `skill_deps_loaded` summary event (`mode=skill_deps_loaded`). GATED: `--with-deps` requires `--confirm` (else reject, no writes); a missing dep or cycle rejects writing nothing (never auto-imports — import stays the separate explicit allowlist-gated flow); single-skill `skills load` (no `--with-deps`) is unchanged. Exposed via `agentdeck contract skills` (`load_plan_command` / `skill_load_plan_response_fields`). Design + plan: `docs/superpowers/specs/2026-07-09-skill-dep-autoload-design.md`, `docs/superpowers/plans/2026-07-09-skill-dep-autoload.md`. Tests: `tests/test_agent_cli.py -k skills_load` + `tests/test_contracts.py::test_validate_skill_load_plan_contract`.

**Skill dependency version pinning (decision "B-ver") is done (content-hash pins, local + deterministic + no network):** a `depends_on` entry may pin a content hash — `name@sha256:<hex>` — while plain `name` still means "any version" (unchanged). New pure `_parse_dep(entry) -> (name, pin|None)` (`src/agentdeck/skills.py`) splits on the first `@` (empty suffix ignored); `SkillSnapshot.depends_on` keeps the raw entries. `resolve_skill_dependencies` gains `version_mismatch: list[{name, expected, actual}]` (deduped via `seen_vm`): when a pinned dep IS present but its `content_hash != pin` it is recorded and NOT recursed into (a blocker leaf, excluded from `resolved`/`order`); `resolved`/`missing`/cycle/`order` semantics are otherwise unchanged. `skills deps` surfaces `version_mismatch` (flows through the `**resolution` spread); `_skill_load_plan` adds `version_mismatch` to the payload and a `"version mismatch: <name> expected <pin>"` blocker, so `can_load` is false and `skills load --with-deps --confirm` rejects writing nothing (identical handling to `missing`/cycle). `version_mismatch` added to `SKILLS_DEPS_RESPONSE_FIELDS` / `SKILL_LOAD_PLAN_RESPONSE_FIELDS` + both validators. Design + plan: `docs/superpowers/specs/2026-07-09-skill-dep-version-pinning-design.md`, `docs/superpowers/plans/2026-07-09-skill-dep-version-pinning.md`. Tests: `tests/test_agent_cli.py::test_resolve_skill_dependencies_version_pinning`, `::test_skills_deps_and_load_plan_flag_version_mismatch`, `tests/test_contracts.py::test_validate_skill_load_plan_contract`.

**Read-only dependency VISIBILITY (B1+B2) + dependency LOAD (B-auto) + version PINNING (B-ver) are now complete.** The remaining dependency items are product forks needing the human.

**Remaining items are all product forks — STOP + ask the human first:**
1. ⚠️ **FORK:** allowlist **ENFORCEMENT** (blocking imports from non-allowlisted sources) — already delivered as opt-in decision "A"; further tightening (default-on, hard block) stays a product fork.
2. ⚠️ **FORK (post-B-ver):** semver **ranges / version intervals** (e.g. `name@>=1.2,<2`) and **lockfile generation / lock strategy** — content-hash pinning (B-ver) is done; these each need their own brainstorm→spec→plan. Do NOT start inside another slice.
3. ⚠️ **FORK (C):** remote / marketplace skill sources or remote dependency fetch (over the network) is a product fork — local trusted sources only until a human explicitly opts in. Do NOT build it unilaterally.

## Required Verification Before Handoff

At minimum, run:

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py::test_leader_chat_frontdesk_routes_request_without_planning_or_provider_calls -q
conda run -n agentdeck pytest tests/test_agent_cli.py::test_status_surfaces_logical_coordination_roles_for_planner_orchestrator_split tests/test_agent_cli.py::test_leader_status_surfaces_provider_and_queue_snapshot_without_mutating_state tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_leader_status_contract_payload_is_reusable_without_cli tests/test_contracts.py::test_leader_status_contract_response_includes_example_without_drift -q
conda run -n agentdeck pytest tests/test_agent_cli.py::test_loop_once_recommends_next_explicit_command_without_mutating_state tests/test_agent_cli.py::test_contract_loop_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_loop_example_exports_gui_ready_card tests/test_contracts.py::test_loop_contract_payload_is_reusable_without_cli tests/test_contracts.py::test_loop_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_loop_once_contract_rejects_auto_execution_claim -q
conda run -n agentdeck pytest tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_requires_worker_lifecycle_item_fields tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state -q
conda run -n agentdeck pytest tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_requires_review_gate_stage_fields tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state -q
conda run -n agentdeck pytest tests/test_agent_cli.py::test_leader_chat_review_gate_is_read_only_and_surfaces_control_palette tests/test_leader_cli.py::test_leader_chat_help_returns_capability_card_without_planning tests/test_agent_cli.py::test_contract_leader_chat_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_leader_chat_example_exports_gui_ready_response -q
conda run -n agentdeck pytest tests/test_agent_cli.py::test_leader_chat_release_preview_is_read_only_and_surfaces_control_palette tests/test_leader_cli.py::test_leader_chat_help_returns_capability_card_without_planning tests/test_agent_cli.py::test_contract_leader_chat_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_leader_chat_example_exports_gui_ready_response -q
conda run -n agentdeck pytest -q
git diff --check
```
