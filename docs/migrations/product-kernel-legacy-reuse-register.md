# Product Kernel Legacy Reuse Register

Status: no legacy code admitted

| Legacy module | New Adapter | Port | Characterization test | Decision |
|---|---|---|---|---|
| none | none | none | none | not admitted |
| `agentdeck.runtime.acp` | none | none | `tests/product_kernel/test_real_adapter_preflight_contract.py` | rejected |
| `agentdeck.runtime.acp_client` | none | none | `tests/product_kernel/test_real_adapter_preflight_contract.py` | rejected |
| `agentdeck.runtime.acp_mapping` | none | none | `tests/product_kernel/test_real_adapter_preflight_contract.py` | rejected |

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
