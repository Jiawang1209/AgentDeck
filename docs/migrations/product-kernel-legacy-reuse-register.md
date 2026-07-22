# Product Kernel Legacy Reuse Register

Status: no legacy code admitted

| Legacy module | New Adapter | Port | Characterization test | Decision |
|---|---|---|---|---|
| none | none | none | none | not admitted |
| `agentdeck.runtime.acp` | none | none | `tests/product_kernel/test_real_adapter_preflight_contract.py` | rejected |
| `agentdeck.runtime.acp_client` | none | none | `tests/product_kernel/test_real_adapter_preflight_contract.py` | rejected |
| `agentdeck.runtime.acp_mapping` | none | none | `tests/product_kernel/test_real_adapter_preflight_contract.py` | rejected |

Task 26 production replacements are new code, not legacy admissions:

- `agentdeck.adapters.adapter_readiness` seals the complete passive executable,
  version, schema, argv, and environment evidence used by the composition root.
- `agentdeck.adapters.acp_worker_connection` owns one lazy official-SDK process
  and callback lifecycle per Product Kernel Worker. Its characterization is
  `tests/product_kernel/test_acp_worker_connection.py`.
- `agentdeck.adapters.acp_transport` passes the same bounded environment into
  the official SDK Leader spawn path. No execution path resolves an adapter or
  wrapped CLI again through `PATH` after readiness.

Review reasons:

- `agentdeck.runtime.acp` couples process, workflow, and persisted state outside
  the new composition root.
- `agentdeck.runtime.acp_client` is not the official-SDK Transport Port used by
  the rewrite and retains legacy model coupling.
- `agentdeck.runtime.acp_mapping` would create a second event and permission
  authority beside the new ACP Worker adapter.

Every future admission must be introduced by the same commit as its
characterization test, Port, Adapter-only boundary, and register row; a row
never grants Kernel or Application imports. The Task 26 Codex and Claude
composition uses the new official-SDK adapters directly; these rejected rows do
not admit any legacy module.

## Task 37 — legacy state migration fixtures (external data only)

The legacy migration parses old JSON state as **inert external data**; it does
not admit any legacy module (no `state.py`/`models.py` import). The sanitized
fixture shapes exercised by `tests/product_kernel/test_legacy_migration.py` and
`test_migration_cli.py`:

| Fixture | Path | Hash | Rationale |
| --- | --- | --- | --- |
| Legacy state | `tests/product_kernel/fixtures/legacy_state/state.json` | `sha256:ec1ef9cfce4dc8388b1c7ce1032f51a2eaae05f0201fad6a7583936d1586f385` | Sanitized `{schema_version, project, agents, messages, jobs}` shape; the project row is imported, the rest are recorded as skipped unsupported items. |
| Legacy events | `tests/product_kernel/fixtures/legacy_state/events.jsonl` | `sha256:3369e44e5c817e7b8eda636dbba3287b3f201ce826114f4d1d8f17ec4539138f` | Sanitized append-only event lines; counted only, never replayed or imported. |

These fixtures are Adapter-only test inputs for `adapters/legacy_state.py`; they
grant no Kernel/Application import and no write authority. See
`docs/migrations/product-kernel-state-migration.md` for the full flow.
