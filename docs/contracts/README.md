# Legacy compatibility contracts

The files in this directory describe the structured CLI and ProjectView
compatibility surface that exists while the Product Kernel Rewrite is
developed side by side.

They are not the architecture, domain model, development order, or internal
contract of the new Product Kernel. A rewrite task may read or adopt one of
these contracts only when the approved TDD plan explicitly names it as an
Adapter or compatibility requirement.

The active implementation authority is:

`docs/superpowers/specs/2026-07-18-agentdeck-product-kernel-rewrite-design.md`

After the Golden Product Gate and bare-entry cutover, each legacy contract will
be explicitly retained, replaced, migrated, or removed.
