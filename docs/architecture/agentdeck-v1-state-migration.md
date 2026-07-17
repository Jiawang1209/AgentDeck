# AgentDeck V1 State and Migration Architecture

This document defines the durable-state and legacy-migration boundary for the
AgentDeck V1 kernel. It is subordinate to the V1 product requirements and
kernel architecture: it changes neither Mission semantics nor the rule that
one `ProjectDaemon` is the only product-state writer. It is a P0 architecture
decision and P1 acceptance input, not an implementation or live migration
claim.

## Decision and non-goals

AgentDeck V1 uses Python standard-library `sqlite3` and one project-local
SQLite authority at `.agentdeck/state.db`. The store remains local-first and
owner-controlled. One `ProjectDaemon` owns every write transaction; clients,
compatibility commands, Leader and Worker adapters, providers, transports, and
projection code never issue mutating SQL or open an independent write path.
Application services and controlled read views use the `StateStore` boundary
rather than coupling to table layout.

This is a requirements-driven SQLite decision: AgentDeck needs local
transactions, integrity constraints, schema evolution, idempotent recovery,
and coherent queries across Mission, Task, Attempt, Permission, Handoff,
Evidence, event, approval, and audit facts. The choice does not depend on Hive
or CCB and is not an attempt to reproduce either reference architecture. All
structured control-plane state moves to the SQLite authority. The filesystem
remains the filesystem content authority for repository content, complete
terminal logs, Skill and Memory source text, large artifact bodies, and other
non-structured or bulk content; SQLite records only their controlled path or
opaque identity, hash, compact summary, type, ownership, and provenance. A
referenced file is not a second product-state authority and cannot override a
SQLite lifecycle, authorization, lineage, or audit fact.

This decision does not introduce a cloud database, synchronization service,
remote authority, client-side SQL, direct adapter SQL, or a second writer. P0
does not implement the database, migration commands, daemon integration, or
any schema. It also does not freeze every column, index, pragma, or storage
optimization before P1 has deterministic tests.

Migration confirmation is administrative authorization to replace the legacy
state authority with the verified SQLite authority. It is not Mission
authorization. It cannot confirm or start a Mission, dispatch a Task, load a
skill, apply memory, approve a permission, contact a provider, or widen any
existing authorization envelope.

## Authority and transaction model

For each accepted mutation, the daemon appends the corresponding durable event
and updates the affected current entity rows plus the project revision in one
SQLite transaction. Commit publishes all of those facts together or none of
them. Current-state tables are the authoritative current truth; the append-only
event ledger is authoritative history and audit provenance. ProjectView
snapshots, search indexes, aggregates, and other caches are derived and
replaceable. They cannot win a conflict with the current tables or event
ledger, and their presence never proves that a migration or command committed.

The transaction retains the kernel's trigger-specific provenance:

- a client command carries its `command_id`, expected project revision, actor,
  authorization decision, and recorded outcome;
- an adapter event carries its `adapter_event_id`, Mission/Task/Attempt/session
  lineage, ordering and integrity identity, and validation decision;
- a daemon-internal recovery or scheduler trigger carries its
  `internal_trigger_id`, source revision or snapshot identity, and deterministic
  decision provenance.

No adapter event or internal trigger fabricates client command fields. All
three trigger kinds enter the same serialized daemon mutation loop and use the
same atomic append/apply/revision persistence operation.

SQLite runs in WAL mode to support controlled concurrent read views while only
the daemon may commit a product-state mutation. That is a product-state
semantic boundary, not a claim that an operating-system reader can never
coordinate through a SQLite sidecar. WAL does not relax the single-writer
rule, daemon ownership, revision checks, or migration exclusion. Exact
durability and pragma choices, including synchronous level, checkpoint policy,
busy handling, page size, and foreign-key enforcement startup checks, are
deferred to P1 failure-injection and filesystem tests. P1 must prove read-only
connection mode plus owner-only sidecar permissions and lifecycle, and choose
the remaining settings from measured crash-safety evidence, not convenience.

## Conceptual schema responsibilities

The following is a responsibility map, not a frozen physical schema. P1 may
normalize, combine, split, or index these responsibilities while preserving
their authority, lineage, transaction, and projection contracts.

| Responsibility | Durable purpose |
| --- | --- |
| `projects` | Canonical project identity, authority generation, current revision, configuration identity, and migration/cutover watermark |
| `conversations` | Durable project-scoped interaction sequence and links from user intent to resulting commands, Missions, and observations |
| `missions` | Stable governed-goal identity and current lifecycle pointer across immutable versions |
| `mission_versions` / versions | Immutable goal, scope, exclusions, Task graph proposal, limits, acceptance criteria, route order, authorization digest, and provenance |
| `tasks` | Current Task DAG nodes, dependencies, bounded scopes, assignments, concurrency constraints, and acceptance contribution |
| `attempts` | Distinguishable tries for one Task, including route position, lifecycle, budget use, and terminal/recovery classification |
| `sessions` | Agent/model/transport identity, ordered adapter-session lineage, leases, takeover state, and reconciliation facts |
| `permissions` | Exact requested operation, scope, MissionVersion/Task/Attempt/session lineage, decision, actor or policy provenance, and outcome |
| `handoffs` | AgentDeck-owned cross-Worker transfer identity, source/destination work, accepted context, artifact/Evidence references, and status |
| `evidence` | Durable command/test/review/external-effect facts, grades or grade inputs, integrity identity, and source lineage |
| `events` | Append-only accepted intent, outcome, lifecycle, audit, rejection, and recovery history with trigger-specific provenance and monotonic cursor |
| `commands` | Client-command idempotency, input identity, expected revision, authorization result, completion outcome, and replay/conflict handling |
| `approvals` | Explicit human decisions and their exact subject, digest, actor, scope, status, and lineage; never a generic permission token |
| `artifacts` | Project-relative location, content hash, media/type metadata, compact summary, ownership, and production/Evidence provenance |
| `learning` | Evidence-derived review records and application lineage without granting authority or silently changing context |
| `suggestions` | Pending/reviewed/applied Memory, Skill, or Improvement Mission proposals, exact proposed content identity, confirmation, and provenance |
| `schema_migrations` | Ordered, monotonically increasing schema/import versions, integrity identity, application time, and migration result needed for deterministic upgrade/verification |

Foreign keys and application invariants must preserve exact lineage rather than
copying descriptive text between entities. Schema evolution is ordered and
transactional. A code version that cannot interpret the on-disk schema refuses
mutation; it does not downgrade, guess, or bypass a migration.

## Artifact, privacy, and ownership boundary

SQLite stores an artifact's project-relative path or opaque identity, content
hash, compact summary, type, ownership, and provenance. It does not absorb
large artifact bodies, repository blobs, raw terminal transcripts, complete
prompts, provider payload dumps, credentials, tokens, environment secrets, or
unredacted sensitive output. Large or sensitive material remains in its
purpose-specific owner-controlled location and is referenced only when the
authorization and Evidence rules permit it.

The `.agentdeck` directory, database, WAL sidecars, backup manifests, temporary
files, and migration locks must be owner-only. Migration validates ownership,
file type, containment, and path traversal before reading or writing. Stored
paths and user-visible diagnostics are project-relative or redacted; canonical
absolute source paths, home-directory details, and secrets must not be newly
introduced into ProjectView, events, migration diagnostics, or generated
manifest metadata.

The migration restore backup is an owner-only, non-portable, byte-preserving
recovery image. Its payload inherits the full sensitivity of the legacy source,
including any secret, unredacted text, or absolute path already present; this
design does not promise payload sanitization. The backup manifest and
diagnostics identify those payloads by controlled project-relative identity,
hash, length, and mode rather than copying sensitive content into new metadata.
A future portable export must be separately designed and sanitized. Portable
export is not a P0 command, a migration restore backup, or part of this Task.

## Legacy discovery and Migration Preview

`Migration Preview` is an entirely read-only inspection. It resolves the
invocation to one canonical project root, without following a legacy-state
symlink out of that root, then inventories recognized legacy JSON and JSONL
sources. For every recognized source it reports a stable project-relative
identity, format/schema version when known, record counts, cross-file
references, content hash, ownership/file-type facts, and actionable problems.
It derives an overall preview digest from the canonical inventory and proposed
ordered import plan.

Preview may diagnose malformed JSON/JSONL, duplicate or missing identities,
dangling references, conflicting versions, unsupported records, unsafe
permissions, symlinks, non-regular files, and an existing unrecognized
`.agentdeck/state.db`. It never repairs or normalizes source data. It does not
create a backup, database, WAL file, lock/status marker, event, migration row,
or cache; does not update state or suggestion status; and does not start or
stop the daemon. Re-running preview against byte-identical legacy inputs must
produce the same inventory identities and digest.

There is no silent migration. Ordinary `agentdeck`, ProjectView reads, legacy
commands, daemon startup, or discovery may offer preview instructions but may
not treat inspection, database absence, or a compatible-looking legacy file as
explicit confirmation.

## Explicit confirmed migration state machine

Only an explicit confirmed migration may move authority. The P1 implementation
must make the following phases observable to its own recovery logic while
never exposing a partially imported database as product truth:

Migration commands are client commands: all `agentdeck migrate` invocations
request application behavior. Read-only preview may run through a controlled
application read service, but confirmed migration backup, build, cutover,
activation, and finalization execute only inside the same `ProjectDaemon` in
exclusive maintenance mode. Maintenance stops ordinary product mutations,
adapter-event application, scheduler transitions, and ordinary read serving,
but the daemon does not surrender sole-writer ownership. If no daemon is
running, the CLI may start or connect to the same project-locked daemon
maintenance executor; the CLI or a separate migration process never becomes a
database or legacy-state writer.

The durable authority lifecycle has exactly these serving states:

- `legacy_active`: verified legacy state is the active fact source and may be
  served or mutated only outside migration maintenance;
- `sqlite_installed_quarantined`: a verified SQLite candidate has been durably
  installed but is non-serving and unactivated. Legacy remains the last active
  fact source, while migration exclusion disables all ordinary reads and every
  mutation path; and
- `sqlite_active`: the daemon has durably activated and reverified the exact
  installed candidate, so SQLite is the sole serving and mutation authority.

Only the daemon may transition these states. Filesystem presence, rename
completion, a backup, or a client response cannot activate an authority.

1. **Enter exclusive maintenance.** The sole-writer daemon acquires the
   exclusive project migration lock, drains or durably defers accepted adapter
   input, and disables ordinary mutation, scheduling, and read serving while
   retaining its writer lease and identity. A competing, stale, or ambiguous
   lease is a blocker, not permission for a client or helper to write. The
   durable state remains `legacy_active`, but it is quarantined from ordinary
   serving for the duration of maintenance.
2. **Revalidate intent.** Require explicit confirmation bound to the supplied
   preview digest. Re-resolve the canonical project root, re-inventory every
   legacy identity, and compare versions, types, ownership, references, sizes,
   hashes, and the proposed import plan. Any difference since preview aborts
   before cutover and requires a new preview and confirmation.
3. **Seal a complete backup.** Copy every recognized authoritative legacy
   source and the authority metadata needed for restoration into an
   owner-only backup. Write and verify a manifest containing source identities,
   lengths, hashes, modes/ownership facts, and preview digest. After verifying
   the bytes, fsync every backup data file and the manifest, then fsync the
   backup directory so its entries and manifest seal are durable. A backup is
   not sealed until all of those durability operations succeed. An incomplete,
   unverifiable, or not-durably-sealed backup is never eligible for cutover or
   rollback.
4. **Build off-path.** Create a uniquely named temporary database on the same
   filesystem as `.agentdeck/state.db`. Apply ordered schema migrations and
   legacy import steps through the intended `StateStore` mapping. The temporary
   database records the immutable migration, preview, backup, authority, and
   cutover-candidate identities needed to recognize it during recovery, but
   marks them as unactivated. It is not an authority, is never opened by
   clients, and cannot emit product events or advance legacy status.
5. **Verify before exposure.** Close write transactions and verify SQLite
   structural and referential integrity, applied schema versions, entity and
   event counts, source-to-row hashes, reference lineage, authorization and
   permission relationships, and deterministic ProjectView equivalence for
   the facts representable by each compatibility projection. Before exposure,
   perform the checkpoint or equivalent consolidation selected and tested in
   P1, close every database handle, and prove that the candidate is in a
   self-contained switchable form: no WAL sidecar or other temporary sidecar
   may contain committed state needed to interpret it. Reopen only as needed
   for final read-only integrity and identity verification, close it again,
   and fsync the verified temporary database file. A mismatch, close failure,
   unintegrated sidecar, or fsync failure is a hard failure. This contract does
   not freeze pragma values; P1 failure-injection evidence determines them.
6. **Durably cut over.** With all temporary handles closed, use an atomic
   same-filesystem replace to place the verified self-contained database at
   `.agentdeck/state.db`, then fsync the containing `.agentdeck` directory to
   make the replacement directory entry durable. Rename alone is not durable
   success: migration remains under exclusive exclusion and cannot serve
   ordinary mutations until the directory fsync succeeds and the installed
   database's authority/cutover identity and integrity are revalidated. After
   that fsync and revalidation, the durable state is
   `sqlite_installed_quarantined`, not `sqlite_active`: SQLite is installed but
   non-serving, legacy remains the last active fact source, and maintenance
   exclusion prevents either representation from serving ordinary reads or
   accepting mutations. The original legacy files remain intact and are never
   a concurrent write target or silent fallback store.
7. **Finalize in the new authority.** Before accepting any ordinary mutation,
   the daemon validates the already sealed candidate identities, then executes
   one migration-identity-bound, idempotent SQLite transaction that atomically
   changes the activation state and records the preview, backup, authority,
   imported-source and cutover identities, schema versions, verifier result,
   durable cutover time, and actor provenance. It commits under the tested P1
   durability policy, closes or checkpoints as that policy requires, reopens
   through the controlled `StateStore`, and verifies the committed activation
   identity and integrity. The committed activation record is not serving
   authority until that reopen verification succeeds; until then the project
   remains non-serving under `sqlite_installed_quarantined` recovery. Only after
   verification does the state become `sqlite_active`; only then may the daemon
   release migration exclusion and resume SQLite-backed read serving, adapters,
   scheduling, and mutations. Migration provenance is never written to the
   legacy ledger or used to broaden imported authorization. At that point the
   untouched legacy files become a sealed read-only archive rather than an
   authority or fallback.

Failure before the atomic switch leaves every legacy source authoritative and
untouched. The implementation removes an unexposed temporary database when it
can prove ownership, or quarantines it under owner-only permissions for
diagnosis; it never renames a questionable temp file into authority. A verified
backup may remain sealed for retry and rollback, but its existence is not a
successful migration signal.

## Idempotency and interruption recovery

Migration recovery is phase-aware and fail-closed:

- A crash before the backup leaves legacy authority unchanged. A partial
  backup has no valid sealed manifest and must be discarded or quarantined;
  the next attempt starts with a fresh preview.
- A crash during temporary database construction leaves legacy authoritative.
  Recovery never resumes from arbitrary rows; it verifies the preview and
  backup identities, then deterministically rebuilds or restarts the ordered
  import.
- A crash after verification but before rename still leaves legacy
  authoritative. The temporary database may be reused only after complete
  revalidation under the exclusive lock; temp existence alone never implies
  success.
- A crash after atomic rename but before the containing-directory fsync has an
  uncertain directory-entry durability outcome; path existence alone cannot
  classify it as success. Startup keeps both legacy and SQLite mutation paths
  and ordinary read serving disabled, reacquires daemon maintenance exclusion,
  and evaluates the installed
  candidate's authority/cutover identity against the sealed backup, preview,
  and migration identities. If the candidate is present, self-contained, and
  passes complete integrity and identity revalidation, recovery may repeat the
  directory fsync and enter `sqlite_installed_quarantined`. If the candidate
  is absent, the verified `legacy_active` authority remains the fact source but
  is not served until maintenance recovery releases it. A corrupt,
  mismatched, or otherwise ambiguous candidate fails closed for explicit
  repair; recovery never guesses from a filename and never enables dual write.
- A crash after the directory fsync but before the activation transaction
  leaves `sqlite_installed_quarantined`. Startup reads the candidate's internal
  unactivated state and exact cutover identity, keeps all serving disabled,
  revalidates it against the sealed backup and preview, and may retry only the
  same idempotent activation transaction.
- A crash during or after activation commit is resolved from the database's
  internal activation state and cutover identity, never from path existence. A
  committed matching activation is eligible to complete the transition, but
  startup keeps `sqlite_installed_quarantined` non-serving until it reopens and
  verifies; only that successful verification yields `sqlite_active`. An absent
  commit remains installed and quarantined. A conflicting or unreadable result
  fails closed; it never restarts legacy writes automatically.

Every phase uses stable migration, preview, backup, and cutover identities.
Retrying the same confirmed operation either returns the already verified
outcome or continues the uniquely identified recovery path; reusing an
identity with different input fails. At no point may legacy JSON/JSONL and
SQLite both accept mutations, and recovery never guesses authority from a
temporary filename, timestamp, process exit, or provider narrative.

## Guarded rollback

Rollback is safe only while no post-cutover authoritative product mutation has
occurred. `agentdeck migrate --rollback` remains a client command to the same
daemon. The daemon performs the following rollback state machine without
delegating writes to the CLI or an independent restore process:

1. **Quarantine and prove eligibility.** Enter exclusive maintenance mode from
   `sqlite_active`; stop ordinary reads, mutations, adapters, and scheduling;
   then verify the SQLite watermark, exact cutover and authority identities,
   and sealed migration restore backup. Any post-cutover authoritative product
   write refuses rollback before a restore image is installed.
2. **Build a complete legacy restore image off-path.** Restore every raw source
   byte-for-byte into a new owner-only same-filesystem image. Seal a rollback
   manifest containing the source authority generation, a new legacy authority
   generation, exact rollback identity, file identities, lengths, hashes, and
   modes. It must preserve old permission and approval facts without widening,
   synthesizing, or re-authorizing them.
3. **Durably verify the restore image.** Verify all restored bytes and
   cross-file identities, fsync every restored file and the rollback manifest,
   then fsync the restore-image directory. Failure leaves SQLite active in
   precedence but non-serving under maintenance.
4. **Install but quarantine legacy.** Install the complete restore image in its
   final legacy location while the SQLite authority still has precedence and
   all mutations and ordinary reads remain disabled.
   Fsync the containing legacy-install or selector directory and revalidate the
   installed identity before retirement. The installed legacy image is
   non-serving and cannot become active merely because files exist. P1 must
   choose a physical layout with one testable, atomic selector or same-filesystem
   installation boundary that exposes either the complete old layout or the
   complete identity-bound restore image, never a path-by-path mixture; no vague
   mutable pointer or multi-file partial install satisfies this observable
   contract.
5. **Durably retire SQLite.** Record the exact rollback-prepared identity in an
   idempotent SQLite transaction, consolidate sidecars as required, close and
   verify the self-contained database, and fsync it. Atomically rename it to a
   unique owner-only audit location, then fsync the containing `.agentdeck`
   directory. That durable retirement is the authority switch: before it,
   SQLite retains precedence; after it, SQLite is retired and may never resume
   mutations for that authority generation. This administrative
   rollback-prepared record does not represent an ordinary product mutation and
   does not widen the zero-post-cutover-write eligibility window.
6. **Activate and release legacy.** Revalidate the installed restore image,
   new authority generation, and exact rollback identity against the retired
   SQLite audit record and sealed raw backup. Only that exact matching image may
   transition to `legacy_active`. Reopen it through the controlled `StateStore`,
   verify ProjectView and lineage, and only then release maintenance and allow
   legacy reads or mutations. Before full verification and release, restored
   legacy remains read-disabled and mutation-disabled quarantine.

If the physical layout requires a selector to make the installation boundary
atomic, P1 may choose its representation only if failure injection proves the
observable precedence and all-or-nothing rules above. The selector is an
authority-generation and rollback-identity record, not a convenience pointer,
and cannot authorize writes by itself.

Rollback recovery is phase-aware:

- A crash during restore-image write, fsync, verification, or installation but
  before SQLite retirement keeps SQLite as the recoverable authority. Startup
  verifies it, quarantines any incomplete restore image, and either resumes the
  exact rollback identity or safely releases `sqlite_active`; legacy never
  accepts writes.
- A crash after the SQLite-retire rename but before containing-directory fsync
  is ambiguous. Startup disables every read and mutation path, checks both the
  authority and audit locations plus their internal rollback/cutover identities,
  and may finish or reverse the rename only when the exact identity and one
  complete self-contained database are provable. Otherwise it fails closed.
- A crash after durable SQLite retirement never reactivates that SQLite
  generation. Startup may activate legacy only after the complete installed
  image matches the exact rollback identity, new authority generation, retired
  audit record, and sealed backup; mismatch stays blocked for repair.
- A crash during legacy activation or release repeats identity and integrity
  verification idempotently. Until release completes, no ordinary read or
  mutation is served.

Every rollback interruption therefore exposes at most one mutation authority;
neither an installed restore image nor a retired database filename proves
activation, and no recovery path enables dual write.

After any new command, adapter event, internal trigger, Mission change,
permission decision, Evidence record, learning application, or other
authoritative write, automatic rollback must refuse. Reverting then would lose
valid events and could repeat external effects. The safe paths are an explicit
export/forward repair into the current SQLite authority, or a separately
reviewed and approved destructive-discard procedure that names the data and
effects being abandoned. Basic `--rollback` has no force bypass for this guard.

A rollback failure before durable SQLite retirement preserves SQLite as the
recoverable precedence authority and remains under exclusive maintenance until
its status is verified. A failure after durable retirement preserves the
retired database only as immutable audit/recovery evidence and never reactivates
it; the project remains non-serving until the exact legacy rollback identity is
verified or a separately reviewed repair is performed. Restoration beginning,
a backup directory, or an installed legacy path can never reactivate writes.

## Compatibility and projection versions

`project-view/v1` and `project-view/v2` are two read-only projections from the
same currently active `StateStore` authority. In `sqlite_active`, both project
from `.agentdeck/state.db`; in `legacy_active`, including after verified
rollback, both use the controlled compatibility adapter. Neither projection
may read an installed/quarantined candidate, retired database, raw backup, or
legacy archive directly. The v1 projection is a compatibility shape, not a
second cache authority. The v2 projection may expose the new Mission model
while both views share one project revision, event lineage, and source facts.
Differences must be intentional field/version mapping, never divergent state.

Legacy deterministic CLI commands become application/daemon compatibility
facades one vertical slice at a time. Once a command is cut over, it delegates
to the same authenticated daemon command path and transaction boundary as the
new client. It cannot issue direct SQL, call an adapter directly, or preserve a
legacy local-write path. After project cutover, if the daemon is unavailable,
every mutating command refuses with actionable recovery guidance; it never
falls back to legacy files or an ad hoc local SQLite writer. Read-only clients
also consume controlled application projections rather than arbitrary SQL.

Compatibility code is removable only after callers use the shared application
contract, required `project-view/v1` consumers have a tested transition or
retirement path, migration/rollback support windows are satisfied, and no
legacy write path remains reachable.

## Proposed P1 command surface

The exact proposed deterministic surface is:

```bash
agentdeck migrate --preview
agentdeck migrate --confirm
agentdeck migrate --verify
agentdeck migrate --rollback
```

This spelling and its response contracts are P1 design input only. P0 does not
implement, register, advertise as available, or execute these commands.
`--confirm` must bind explicit confirmation to an unchanged Migration Preview
digest; it is never inferred from interactivity or a generic yes/no response.
`--verify` is read-only with respect to product state. `--rollback` implements
the post-cutover-write refusal above rather than a convenience downgrade.

## Security boundaries

- Canonicalization must happen before lock, discovery, backup, and target-path
  selection. Symlink swaps, traversal, project-root changes, mount changes,
  non-regular inputs, hard-link ambiguity, unsafe ownership, and unexpected
  target files fail closed.
- The exclusive migration lock and daemon control endpoint are local,
  owner-only, authenticated boundaries. Filesystem locality alone does not
  identify an actor or authorize confirmation.
- Backup and temp names are unpredictable and created without overwrite.
  Copying preserves integrity identities without following external links.
- Diagnostics expose project-relative identities, stable hashes, and reason
  codes, not secret content, raw prompts/transcripts, absolute source paths, or
  credentials.
- Migration code performs deterministic local parsing and storage work only.
  It must not call a provider, Agent adapter, ACP, CLI/PTY Worker, tmux, skill,
  memory application, Mission engine execution, or external network service.
- Imported permissions and approvals retain exact original lineage and status;
  migration cannot upgrade, broaden, synthesize, or re-authorize them.

## Verification and acceptance

P1 must turn this design into deterministic unit, integration, and
failure-injection coverage. Acceptance includes:

- a fresh project creates and reopens one valid authority without inventing
  legacy state;
- representative legacy projects preserve counts, references, hashes,
  revisions, event order, permission lineage, and both ProjectView mappings;
- malformed or corrupt JSON/JSONL, unsupported versions, broken references,
  symlink sources, non-regular files, unsafe permissions, and unrecognized
  target databases fail without source mutation;
- any source changed after preview invalidates the digest and requires a new
  explicit confirmation;
- interruption before backup, during backup/temp write, before the atomic
  switch, immediately after switch, and during finalization recovers to exactly
  one known authority;
- injected backup fsync failure, temporary database fsync failure, rename
  failure, and directory fsync failure each leave mutation disabled until
  recovery proves at most one authority; no path may infer success from rename
  or file existence alone;
- temporary WAL/sidecar interruption and checkpoint/close failure never expose
  a database whose committed truth still depends on an uninstalled sidecar;
- backup restore reproduces the sealed identities byte-for-byte where required
  and no incomplete backup is accepted;
- when ordinary read serving is enabled, readers observe either complete
  `legacy_active` state or complete `sqlite_active` state; installed,
  quarantined, activating, retiring, and partially restored states serve no
  ordinary ProjectView;
- `project-view/v1` and `project-view/v2` derive from the same revision and
  authority, including after restart;
- rollback before a new authoritative write restores the verified legacy
  authority, while rollback after any such write refuses without changing
  either authority;
- restore-image write/fsync failure, restore-image install failure,
  SQLite-retire rename failure, rollback directory-fsync failure, and rollback
  activation/release crash each prove that at most one mutation authority can
  exist and that quarantined state serves no ordinary reads;
- the raw migration restore backup remains owner-only and byte-preserving, while
  preview, verification, errors, events, ProjectView, and newly generated
  manifest metadata introduce no canonical source-path or secret leaks; and
- all migration tests run with fake/local state only and make no provider,
  Agent, ACP, CLI/PTY Worker, tmux, network, skill-load, memory-apply, or Mission
  execution call.

Verification must compare semantic ProjectView equivalence where V1 and V2
represent different shapes; byte equality between different projection
versions is neither required nor sufficient. Atomicity and backup identity
must be tested with injected failures, not inferred from a successful happy
path.

## Stop conditions

P1 implementation or migration stops for design review if it cannot prove any
of the following: one daemon writer, exclusive migration ownership, canonical
project containment, a complete backup identity, same-filesystem atomic
switch support, ordered schema/import application, referential and ProjectView
equivalence, unambiguous crash recovery, or guarded rollback without event
loss. It also stops if legacy and SQLite could both accept writes, a client or
adapter needs direct SQL, a preview would mutate state, migration would broaden
authorization, absolute source paths or secrets would be persisted, or success
would depend on provider/ACP/tmux/live behavior.

No convenience fallback may weaken these stop conditions. The safe result of
uncertain authority, integrity, ownership, effect, or rollback state is refusal
plus actionable diagnosis.
