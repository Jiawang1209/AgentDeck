# Product Kernel Golden Gate — frozen website target & browser evidence

Date: 2026-07-22 · Task 34

## Purpose

The Real Golden Product Gate (design §18) accepts the product kernel only when a
four-Worker Mission reproduces a **local, license-clean** copy of a research
institute homepage and a browser produces objective evidence that the build
matches a **frozen target**. Task 34 defines that frozen target and the
`BrowserPort` evidence contract; the authorized live captures happen later
(Tasks 35-36).

## What lives in the repository (and what does not)

The repository stores **rules, selectors, tolerances, and content hashes only**:

- `tests/product_kernel/fixtures/reference_homepage/target-manifest.json` — the
  frozen target: fixed viewports, required structure selectors, interaction
  checks, allowed visual tolerances (`pixel_ratio`, `layout_shift_px`), and an
  explicitly empty `source_assets` list.
- `tests/product_kernel/fixtures/reference_homepage/index.html` — an **original,
  license-clean** fixture homepage reproducing only a generic layout
  (navigation, hero carousel, responsive menu, content modules). It is **not**
  the official `iae.cas.cn` site and copies no markup, text, media, or assets.

The repository **never** stores real reference screenshots, copyrighted assets,
or any captured pixels. Those stay outside Git.

`test_repository_manifest_contains_no_copyrighted_reference_assets` enforces the
license posture: the manifest must declare `source_assets == []` and exactly the
two allowed tolerance keys.

## Browser evidence contract (`agentdeck.adapters.browser`)

`BrowserPort.verify(url, manifest) -> BrowserEvidenceReport` returns typed,
sanitized evidence:

- `screenshots`: one `Screenshot(viewport, content_hash)` per manifest viewport,
  in order — desktop `(1440, 1200)` then mobile `(390, 844)`;
- `structure`: each required selector → present/absent;
- `interactions`: `navigation` / `carousel` / `responsive_menu` → `passed` /
  `failed`, evaluated against the parsed DOM (not hard-coded);
- `visual_diff`: measured diff per declared tolerance.

Two adapters implement the port:

- **`DeterministicBrowser`** (default) — offline, dependency-free. It parses the
  local fixture, grades the manifest's structure/interaction rules against the
  DOM, and content-addresses each viewport over a normalized structure snapshot.
  Output is stable and reproducible, so it runs in the ordinary deterministic
  suite with no browser and no network.
- **`PlaywrightBrowser`** (optional, live only) — the real pixel-capture path for
  the authorized Golden gates. Playwright is imported **lazily inside the
  adapter**, behind the optional `browser` extra
  (`pip install -e '.[browser]'` + `playwright install chromium`), so importing
  the module and running the deterministic tests never require Playwright.

## Boundaries

Task 34 does not run a real browser, install Playwright, contact a network, copy
copyrighted assets, or run a live Mission. Real captures and the four-Worker
Golden Product Mission remain under later, separately authorized gates.
