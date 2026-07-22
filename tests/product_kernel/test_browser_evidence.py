"""Task 34 — frozen website target + deterministic Browser evidence adapter.

These tests verify the *evidence contract* against a lawful local fixture only.
They never launch a real browser, never install Playwright, and never store
copyrighted reference assets. Real pixel captures belong to the authorized live
Golden gates (Tasks 35-36), not to this deterministic suite.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentdeck.adapters.browser import DeterministicBrowser

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "reference_homepage"


def load_manifest(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def fixture_url() -> str:
    return (FIXTURE_DIR / "index.html").as_uri()


@pytest.fixture
def browser() -> DeterministicBrowser:
    return DeterministicBrowser()


def test_browser_evidence_covers_fixed_viewports_and_interactions(
    browser: DeterministicBrowser, fixture_url: str
) -> None:
    report = browser.verify(fixture_url, load_manifest("target-manifest.json"))
    assert [shot.viewport for shot in report.screenshots] == [
        (1440, 1200),
        (390, 844),
    ]
    assert report.interactions == {
        "navigation": "passed",
        "carousel": "passed",
        "responsive_menu": "passed",
    }
    assert all(item.content_hash for item in report.screenshots)


def test_repository_manifest_contains_no_copyrighted_reference_assets() -> None:
    manifest = load_manifest("target-manifest.json")
    assert manifest["source_assets"] == []
    assert set(manifest["tolerances"]) == {"pixel_ratio", "layout_shift_px"}


# --- focused regressions for the corrected adapter behaviors ---------------


def test_structure_facts_cover_every_required_selector(
    browser: DeterministicBrowser, fixture_url: str
) -> None:
    manifest = load_manifest("target-manifest.json")
    report = browser.verify(fixture_url, manifest)
    assert set(report.structure) == set(manifest["structure"]["required_selectors"])
    assert all(report.structure.values())


def test_content_hash_is_deterministic_and_distinct_per_viewport(
    browser: DeterministicBrowser, fixture_url: str
) -> None:
    manifest = load_manifest("target-manifest.json")
    first = browser.verify(fixture_url, manifest)
    second = browser.verify(fixture_url, manifest)
    # Stable across runs (content-addressed, not wall-clock).
    assert [s.content_hash for s in first.screenshots] == [
        s.content_hash for s in second.screenshots
    ]
    # Each viewport yields its own hash.
    hashes = {s.content_hash for s in first.screenshots}
    assert len(hashes) == len(first.screenshots)
    assert all(h.startswith("sha256:") for h in hashes)


def test_missing_interaction_element_fails_that_interaction(
    browser: DeterministicBrowser, tmp_path: Path
) -> None:
    # A fixture stripped of the carousel controls must fail *only* the carousel
    # check — proving interactions are really evaluated, not hard-coded.
    broken = tmp_path / "broken.html"
    broken.write_text(
        """<!DOCTYPE html><html><body>
        <header class="site-header">
          <button class="menu-toggle">Menu</button>
          <nav class="primary-nav" data-collapsible>
            <a href="#a">A</a><a href="#b">B</a><a href="#c">C</a>
          </nav>
        </header>
        <main id="content">
          <section class="hero-carousel"><article data-slide="1">one</article></section>
        </main>
        <footer class="site-footer">f</footer>
        </body></html>""",
        encoding="utf-8",
    )
    report = browser.verify(broken.as_uri(), load_manifest("target-manifest.json"))
    assert report.interactions["navigation"] == "passed"
    assert report.interactions["responsive_menu"] == "passed"
    assert report.interactions["carousel"] == "failed"
    assert report.all_interactions_passed() is False


def test_report_exposes_visual_diff_within_declared_tolerances(
    browser: DeterministicBrowser, fixture_url: str
) -> None:
    manifest = load_manifest("target-manifest.json")
    report = browser.verify(fixture_url, manifest)
    # The fixture is its own reference, so measured diffs are zero and within
    # every declared tolerance.
    assert set(report.visual_diff) == set(manifest["tolerances"])
    for key, measured in report.visual_diff.items():
        assert measured <= manifest["tolerances"][key]
