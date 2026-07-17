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

SQLite runs in WAL mode to support the controlled daemon writer and bounded,
coherent read views without turning readers into writers. WAL does not relax
the single-writer rule, daemon ownership, revision checks, or migration
exclusion. Exact durability and pragma choices, including synchronous level,
checkpoint policy, busy handling, page size, and foreign-key enforcement
startup checks, are deferred to P1 failure-injection and filesystem tests. P1
must choose them from measured crash-safety evidence, not convenience.

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
absolute source paths, home-directory details, and secrets must not leak into
ProjectView, events, migration output, or portable backups.

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

1. **Exclude writers.** Acquire an exclusive project migration lock and prove
   daemon exclusion. A running daemon must enter a migration-safe stopped state
   or refuse the operation; a stale or ambiguous lease is a blocker, not
   permission to compete. Legacy state remains authoritative.
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
   database's authority/cutover identity and integrity are revalidated. Only
   then is cutover treated as durable and SQLite as the sole product-state
   authority. The original legacy files remain intact and are thereafter
   opened only as a sealed read-only legacy archive, never as a write target or
   fallback store.
7. **Finalize in the new authority.** Before accepting any ordinary mutation,
   validate and activate the already sealed candidate identities, then record
   the imported source identities, schema versions, verifier result, durable
   cutover time, and actor provenance in the new SQLite authority. Migration
   provenance is not written to the legacy ledger or status and becomes active
   only in the new authority after durable switch. Release migration exclusion
   only after this finalization and a fresh read verification.

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
  disabled, reacquires migration exclusion, and evaluates the installed
  candidate's authority/cutover identity against the sealed backup, preview,
  and migration identities. If the candidate is present, self-contained, and
  passes complete integrity and identity revalidation, recovery may repeat the
  directory fsync and finish the uniquely identified cutover. If the candidate
  is absent, the verified legacy authority remains authoritative. A corrupt,
  mismatched, or otherwise ambiguous candidate fails closed for explicit
  repair; recovery never guesses from a filename and never enables dual write.
- A crash after the directory fsync means `.agentdeck/state.db` is the sole new
  authority even if final migration provenance was not yet committed. Startup
  still holds all ordinary mutations, validates the cutover candidate against
  the sealed backup and preview identity, idempotently finalizes provenance,
  and only then serves writes. It never restarts legacy writes automatically.

Every phase uses stable migration, preview, backup, and cutover identities.
Retrying the same confirmed operation either returns the already verified
outcome or continues the uniquely identified recovery path; reusing an
identity with different input fails. At no point may legacy JSON/JSONL and
SQLite both accept mutations, and recovery never guesses authority from a
temporary filename, timestamp, process exit, or provider narrative.

## Guarded rollback

Rollback is safe only while no post-cutover authoritative product mutation has
occurred. Under exclusive project and daemon exclusion, `--rollback` must
verify the sealed backup manifest, exact cutover identity, authority generation,
and the new store's event/revision watermark. If only migration-finalization
records exist after the cutover watermark, it may atomically retire the SQLite
authority and restore the sealed backup and legacy authority pointer while all
readers and writers remain excluded. The retired database is preserved
owner-only for audit until the restored authority is verified.

After any new command, adapter event, internal trigger, Mission change,
permission decision, Evidence record, learning application, or other
authoritative write, automatic rollback must refuse. Reverting then would lose
valid events and could repeat external effects. The safe paths are an explicit
export/forward repair into the current SQLite authority, or a separately
reviewed and approved destructive-discard procedure that names the data and
effects being abandoned. Basic `--rollback` has no force bypass for this guard.

A rollback failure preserves the current verified authority and remains under
exclusive lock until its status is known. It cannot reactivate legacy writes
merely because restoration began or a backup directory exists.

## Compatibility and projection versions

`project-view/v1` and `project-view/v2` are two read-only projections from the
same `StateStore` and `.agentdeck/state.db` authority. The v1 projection is a
compatibility shape, not a query against legacy JSON/JSONL and not a second
cache authority. The v2 projection may expose the new Mission model while both
views share one project revision, event lineage, and source facts. Differences
must be intentional field/version mapping, never divergent state.

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
- readers observe either the complete legacy authority before cutover or the
  complete SQLite authority after cutover, never a partially imported view;
- `project-view/v1` and `project-view/v2` derive from the same revision and
  authority, including after restart;
- rollback before a new authoritative write restores the verified legacy
  authority, while rollback after any such write refuses without changing
  either authority;
- preview, verification, errors, events, ProjectView, and portable backup
  metadata contain no canonical source-path or secret leaks; and
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
