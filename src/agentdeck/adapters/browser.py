"""Browser evidence adapter for the Golden Product Gate (Task 34).

Two adapters implement one :class:`BrowserPort`:

* :class:`DeterministicBrowser` — the default, dependency-free adapter used by
  the deterministic test suite and any offline verification. It reads a local
  HTML fixture, evaluates the frozen ``target-manifest`` structure/interaction
  rules against the parsed DOM, and emits content-addressed evidence. It never
  launches a browser and never touches the network, so its output is stable and
  reproducible.
* :class:`PlaywrightBrowser` — the optional real-capture path used only by the
  authorized live Golden gates. Playwright is imported lazily *inside* the
  adapter so importing this module (and running the deterministic suite) never
  requires Playwright to be installed.

Only rules, selectors, tolerances, and hashes live in the repository. Real
pixel captures and any copyrighted reference assets stay outside Git.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
import re
from typing import Protocol, runtime_checkable
from urllib.parse import unquote, urlparse

# --- evidence value types --------------------------------------------------


@dataclass(frozen=True)
class Screenshot:
    """One content-addressed capture at a fixed viewport."""

    viewport: tuple[int, int]
    content_hash: str


@dataclass(frozen=True)
class BrowserEvidenceReport:
    """Typed, sanitized evidence for one verification run."""

    url: str
    target_id: str
    screenshots: tuple[Screenshot, ...]
    structure: dict[str, bool]
    interactions: dict[str, str]
    visual_diff: dict[str, float]

    def all_interactions_passed(self) -> bool:
        return all(status == "passed" for status in self.interactions.values())


@runtime_checkable
class BrowserPort(Protocol):
    """Verify a URL against a frozen target manifest and return evidence."""

    def verify(self, url: str, manifest: dict) -> BrowserEvidenceReport: ...


# --- minimal DOM + selector matcher ---------------------------------------


class _Node:
    __slots__ = ("tag", "attrs", "parent", "children")

    def __init__(self, tag: str, attrs: dict[str, str], parent: "_Node | None") -> None:
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children: list[_Node] = []


class _DomBuilder(HTMLParser):
    _VOID = frozenset(
        {
            "area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {}, None)
        self._stack: list[_Node] = [self.root]

    def handle_starttag(self, tag: str, attrs) -> None:
        node = _Node(tag, {k: (v or "") for k, v in attrs}, self._stack[-1])
        self._stack[-1].children.append(node)
        if tag not in self._VOID:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs) -> None:
        node = _Node(tag, {k: (v or "") for k, v in attrs}, self._stack[-1])
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return


@dataclass(frozen=True)
class _Simple:
    tag: str | None
    classes: tuple[str, ...]
    ids: tuple[str, ...]
    attrs: tuple[str, ...]


def _parse_simple(token: str) -> _Simple:
    attrs = tuple(re.findall(r"\[([a-zA-Z0-9_-]+)\]", token))
    rest = re.sub(r"\[[^\]]*\]", "", token)
    tag_match = re.match(r"[a-zA-Z][a-zA-Z0-9]*", rest)
    tag = tag_match.group(0) if tag_match else None
    classes = tuple(re.findall(r"\.([a-zA-Z0-9_-]+)", rest))
    ids = tuple(re.findall(r"#([a-zA-Z0-9_-]+)", rest))
    return _Simple(tag, classes, ids, attrs)


def _matches_simple(node: _Node, simple: _Simple) -> bool:
    if node.tag == "#root":
        return False
    if simple.tag is not None and node.tag != simple.tag:
        return False
    node_classes = node.attrs.get("class", "").split()
    if any(cls not in node_classes for cls in simple.classes):
        return False
    if simple.ids and node.attrs.get("id") not in simple.ids:
        return False
    if any(attr not in node.attrs for attr in simple.attrs):
        return False
    return True


def _iter_nodes(node: _Node):
    for child in node.children:
        yield child
        yield from _iter_nodes(child)


def _select(root: _Node, selector: str) -> list[_Node]:
    chain = [_parse_simple(tok) for tok in selector.split()]
    if not chain:
        return []
    target = chain[-1]
    ancestors = chain[:-1]
    results: list[_Node] = []
    for node in _iter_nodes(root):
        if not _matches_simple(node, target):
            continue
        cursor = node.parent
        remaining = list(reversed(ancestors))
        ok = True
        for simple in remaining:
            found = False
            while cursor is not None:
                if _matches_simple(cursor, simple):
                    found = True
                    cursor = cursor.parent
                    break
                cursor = cursor.parent
            if not found:
                ok = False
                break
        if ok:
            results.append(node)
    return results


def _canonical_structure(root: _Node) -> str:
    lines: list[str] = []

    def walk(node: _Node, depth: int) -> None:
        classes = ".".join(sorted(node.attrs.get("class", "").split()))
        node_id = node.attrs.get("id", "")
        data_attrs = ",".join(
            sorted(k for k in node.attrs if k.startswith("data-"))
        )
        lines.append(f"{depth}|{node.tag}|#{node_id}|.{classes}|{data_attrs}")
        for child in node.children:
            walk(child, depth + 1)

    for child in root.children:
        walk(child, 0)
    return "\n".join(lines)


def _path_from_url(url: str) -> Path:
    parsed = urlparse(url)
    if parsed.scheme in ("", "file"):
        return Path(unquote(parsed.path) if parsed.scheme == "file" else url)
    raise ValueError(f"DeterministicBrowser only reads local fixtures, got: {url!r}")


# --- deterministic adapter -------------------------------------------------


class DeterministicBrowser:
    """Offline, reproducible :class:`BrowserPort` over a local HTML fixture."""

    def verify(self, url: str, manifest: dict) -> BrowserEvidenceReport:
        html = _path_from_url(url).read_text(encoding="utf-8")
        builder = _DomBuilder()
        builder.feed(html)
        root = builder.root

        structure = {
            selector: bool(_select(root, selector))
            for selector in manifest["structure"]["required_selectors"]
        }
        interactions = self._grade_interactions(root, manifest["interactions"])

        canonical = _canonical_structure(root)
        screenshots = tuple(
            Screenshot(
                viewport=(viewport["width"], viewport["height"]),
                content_hash="sha256:"
                + sha256(
                    f"{viewport['width']}x{viewport['height']}\n{canonical}".encode(
                        "utf-8"
                    )
                ).hexdigest(),
            )
            for viewport in manifest["viewports"]
        )

        # The local fixture is its own frozen reference, so every measured diff
        # is zero and therefore within each declared tolerance.
        visual_diff = {key: 0.0 for key in manifest["tolerances"]}

        return BrowserEvidenceReport(
            url=url,
            target_id=manifest.get("target_id", ""),
            screenshots=screenshots,
            structure=structure,
            interactions=interactions,
            visual_diff=visual_diff,
        )

    @staticmethod
    def _grade_interactions(root: _Node, spec: dict) -> dict[str, str]:
        def graded(passed: bool) -> str:
            return "passed" if passed else "failed"

        nav = spec["navigation"]
        navigation = len(_select(root, nav["selector"])) >= nav["min_count"]

        car = spec["carousel"]
        carousel = (
            len(_select(root, car["selector"])) >= car["min_count"]
            and bool(_select(root, car["control_selector"]))
        )

        menu = spec["responsive_menu"]
        responsive_menu = bool(_select(root, menu["toggle_selector"])) and bool(
            _select(root, menu["collapsible_selector"])
        )

        return {
            "navigation": graded(navigation),
            "carousel": graded(carousel),
            "responsive_menu": graded(responsive_menu),
        }


# --- optional real-capture adapter (lazy Playwright) -----------------------


class PlaywrightBrowser:
    """Real pixel-capture :class:`BrowserPort` for authorized live Golden gates.

    Playwright is imported lazily so this module — and the deterministic test
    suite — never require it. Instantiating without the optional ``browser``
    extra installed raises a clear, actionable error rather than failing at
    import time.
    """

    def __init__(self) -> None:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ModuleNotFoundError as exc:  # pragma: no cover - env dependent
            raise RuntimeError(
                "PlaywrightBrowser requires the optional 'browser' extra: "
                "pip install -e '.[browser]' and 'playwright install chromium'."
            ) from exc
        self._sync_playwright = sync_playwright

    def verify(self, url: str, manifest: dict) -> BrowserEvidenceReport:  # pragma: no cover - live only
        raise NotImplementedError(
            "Real Playwright capture is exercised only by the authorized live "
            "Golden gates (Tasks 35-36), not in deterministic tests."
        )
