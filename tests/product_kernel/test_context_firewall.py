import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]
REGISTER = ROOT / "docs/migrations/product-kernel-legacy-reuse-register.md"
ACTIVE = (
    "README.md",
    "README.zh-CN.md",
    "AGENTS.md",
    "AGENT.md",
    "CLAUDE.md",
    "docs/handoff/current-development-state.md",
    "docs/roadmap/product-north-star.md",
    "docs/roadmap/ultimate-goal-roadmap.md",
    "docs/superpowers/specs/2026-07-18-agentdeck-product-kernel-rewrite-design.md",
    "docs/superpowers/plans/2026-07-18-agentdeck-product-kernel-rewrite.md",
)
OLD_MARKERS = (
    "2026-07-17-m2c-",
    "2026-07-13-agentdeck-project-daemon",
    "agentdeck-v1-kernel-reset",
)
REGISTER_HEADER = "| Legacy module | New Adapter | Port | Characterization test | Decision |"
REGISTER_DIVIDER = "|---|---|---|---|---|"
REGISTER_SENTINEL = "| none | none | none | none | not admitted |"
NO_ADMITTED_STATUS = "Status: no legacy code admitted"
ADMITTED_STATUS = "Status: legacy code admitted"
ALLOWED_DECISIONS = {"not admitted", "rejected", "admitted"}


def markdown_documents(directory: Path) -> tuple[Path, ...]:
    return tuple(sorted(directory.rglob("*.md")))


def strip_fenced_code(text: str) -> str:
    visible: list[str] = []
    in_fence = False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            visible.append(line)
    return "".join(visible)


def has_old_authority(text: str) -> bool:
    visible = strip_fenced_code(text)
    return any(marker in visible for marker in OLD_MARKERS)


def register_with_row(text: str, row: str) -> str:
    assert REGISTER_SENTINEL in text
    return text.replace(REGISTER_SENTINEL, f"{REGISTER_SENTINEL}\n{row}", 1)


def write_fixture_file(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def valid_admission_fixture(root: Path) -> tuple[str, dict[str, str]]:
    paths = {
        "legacy": "agentdeck.legacy_transport",
        "adapter": "src/agentdeck/adapters/legacy_transport.py",
        "port": "src/agentdeck/ports/transport.py",
        "test": "tests/test_legacy_transport_adapter.py",
    }
    write_fixture_file(root, "src/agentdeck/legacy_transport.py", "LEGACY = True\n")
    write_fixture_file(
        root,
        paths["adapter"],
        "import agentdeck.legacy_transport\nimport agentdeck.ports.transport\n",
    )
    write_fixture_file(root, paths["port"], "class TransportPort:\n    pass\n")
    write_fixture_file(
        root,
        paths["test"],
        "import agentdeck.legacy_transport\n"
        "import agentdeck.adapters.legacy_transport\n"
        "import agentdeck.ports.transport\n\n"
        "def test_legacy_transport_adapter():\n    assert True\n",
    )
    text = REGISTER.read_text(encoding="utf-8").replace(
        NO_ADMITTED_STATUS, "Status: legacy code admitted"
    )
    row = (
        f"| {paths['legacy']} | {paths['adapter']} | {paths['port']} | "
        f"{paths['test']} | admitted |"
    )
    return register_with_row(text, row), paths


def markdown_row(line: str) -> tuple[str, ...]:
    stripped = line.strip()
    assert stripped.startswith("|") and stripped.endswith("|"), "invalid table row"
    return tuple(cell.strip() for cell in stripped[1:-1].split("|"))


def require_admission_evidence(path: str, required_root: str, root: Path) -> None:
    assert path != "none", "admitted evidence cannot be none"
    relative = Path(path)
    assert not relative.is_absolute(), "admitted evidence must be project-relative"
    boundary = (root / required_root).resolve()
    candidate = (root / relative).resolve()
    assert candidate.is_relative_to(boundary), f"evidence must be under {required_root}"
    assert candidate.is_file(), f"admitted evidence does not exist: {path}"


def require_legacy_module(module: str, root: Path) -> None:
    assert module.startswith("agentdeck."), "legacy module must be under agentdeck"
    parts = module.split(".")
    assert all(part.isidentifier() for part in parts), "legacy module name is invalid"
    module_path = root / "src" / Path(*parts)
    assert module_path.with_suffix(".py").is_file() or (
        module_path / "__init__.py"
    ).is_file(), f"legacy module does not exist: {module}"


def path_to_module(path: str) -> str:
    relative = Path(path)
    assert relative.suffix == ".py", "admission artifact must be a Python module"
    parts = relative.with_suffix("").parts
    assert parts[:2] == ("src", "agentdeck"), "artifact is outside agentdeck source"
    module_parts = parts[1:]
    assert all(part.isidentifier() for part in module_parts), "artifact module is invalid"
    return ".".join(module_parts)


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
            result.update(
                f"{node.module}.{alias.name}"
                for alias in node.names
                if alias.name != "*"
            )
    return result


def imports_namespace(imports: set[str], namespace: str) -> bool:
    return any(item == namespace or item.startswith(f"{namespace}.") for item in imports)


def validate_legacy_reuse_register(text: str, root: Path) -> None:
    lines = text.splitlines()
    stripped_lines = [line.strip() for line in lines]
    assert REGISTER_HEADER in stripped_lines, "register header must be exact"
    header_index = stripped_lines.index(REGISTER_HEADER)
    assert stripped_lines[header_index + 1] == REGISTER_DIVIDER, "invalid table divider"

    rows: list[tuple[str, ...]] = []
    row_index = header_index + 2
    while row_index < len(lines) and lines[row_index].strip().startswith("|"):
        row = markdown_row(lines[row_index])
        assert len(row) == 5, "register row must have exactly five fields"
        rows.append(row)
        row_index += 1
    assert rows, "register must contain its exact sentinel row"
    assert not any(
        line.strip().startswith("|") for line in lines[row_index:]
    ), "register rows must remain in the declared table"

    sentinel = markdown_row(REGISTER_SENTINEL)
    for row in rows:
        assert row[4] in ALLOWED_DECISIONS, f"unsupported decision: {row[4]}"
        if row[0] == "none":
            assert row == sentinel, "none sentinel row must be exact"
    assert sentinel in rows, "register must retain its exact sentinel row"

    admitted = [row for row in rows if row[4] == "admitted"]
    statuses = [line for line in stripped_lines if line.startswith("Status:")]
    assert len(statuses) == 1, "register must contain exactly one status"
    if admitted:
        assert statuses[0] == ADMITTED_STATUS, "admitted status must be exact"
    else:
        assert statuses[0] == NO_ADMITTED_STATUS, "no-admission status must be exact"

    for row in admitted:
        assert all(field != "none" for field in row), "admitted fields cannot be none"
        require_legacy_module(row[0], root)
        require_admission_evidence(row[1], "src/agentdeck/adapters", root)
        require_admission_evidence(row[2], "src/agentdeck/ports", root)
        require_admission_evidence(row[3], "tests", root)
        assert Path(row[1]).name != "__init__.py", "Adapter artifact cannot be __init__"
        assert Path(row[2]).name != "__init__.py", "Port artifact cannot be __init__"
        assert (
            Path(row[3]).name != "test_context_firewall.py"
        ), "characterization artifact cannot be the firewall test"

        adapter_module = path_to_module(row[1])
        port_module = path_to_module(row[2])
        adapter_imports = imported_modules(root / row[1])
        assert imports_namespace(
            adapter_imports, row[0]
        ), "Adapter must import the admitted legacy namespace"
        assert imports_namespace(
            adapter_imports, port_module
        ), "Adapter must import the registered Port module"
        test_path = root / row[3]
        test_imports = imported_modules(test_path)
        assert all(
            imports_namespace(test_imports, module)
            for module in (row[0], adapter_module, port_module)
        ), "characterization test must reference legacy, Adapter, and Port modules"
        tree = ast.parse(test_path.read_text(encoding="utf-8"), filename=str(test_path))
        test_functions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        ]
        assert test_functions, "characterization must define a test function"
        assert any(
            any(isinstance(child, ast.Assert) for child in ast.walk(function))
            for function in test_functions
        ), "characterization test function must contain an assert"


def test_only_approved_rewrite_specs_and_plans() -> None:
    specs = markdown_documents(ROOT / "docs/superpowers/specs")
    plans = markdown_documents(ROOT / "docs/superpowers/plans")

    assert [path.name for path in specs] == [
        "2026-07-18-agentdeck-product-kernel-rewrite-design.md", "2026-07-21-agentdeck-observer-ipc-takeover-closure-design.md",
    ]
    assert [path.name for path in plans] == [
        "2026-07-18-agentdeck-product-kernel-rewrite.md", "2026-07-21-agentdeck-observer-ipc-takeover-closure.md",
    ]


def test_active_documents_do_not_restore_old_authority() -> None:
    for relative in ACTIVE:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert not has_old_authority(text), relative


def test_legacy_reuse_register_exists_starts_empty_and_requires_admission_evidence() -> None:
    text = REGISTER.read_text(encoding="utf-8")

    validate_legacy_reuse_register(text, ROOT)
    assert "same commit" in text
    assert "characterization test" in text
    assert "Port" in text
    assert "Adapter-only" in text
    assert "register row" in text


def test_markdown_document_scan_includes_nested_history(tmp_path: Path) -> None:
    nested = tmp_path / "archive" / "obsolete.md"
    nested.parent.mkdir()
    nested.write_text("historical", encoding="utf-8")

    assert markdown_documents(tmp_path) == (nested,)


def test_old_authority_inside_fenced_code_is_ignored() -> None:
    text = "before\n```python\n2026-07-17-m2c-example\n```\nafter\n"

    assert not has_old_authority(text)


def test_old_authority_outside_fenced_code_is_detected() -> None:
    text = "before\n2026-07-17-m2c-example\nafter\n"

    assert has_old_authority(text)


def test_register_rejects_admitted_row_while_status_says_none_admitted() -> None:
    text = register_with_row(
        REGISTER.read_text(encoding="utf-8"),
        "| agentdeck.providers | src/agentdeck/adapters/__init__.py | "
        "src/agentdeck/ports/__init__.py | "
        "tests/product_kernel/test_context_firewall.py | admitted |",
    )

    with pytest.raises(AssertionError, match="status"):
        validate_legacy_reuse_register(text, ROOT)


@pytest.mark.parametrize(
    ("column", "invalid_value"),
    (
        (1, "none"),
        (1, "src/agentdeck/ports/__init__.py"),
        (1, "src/agentdeck/adapters/missing.py"),
        (2, "none"),
        (2, "src/agentdeck/adapters/__init__.py"),
        (2, "src/agentdeck/ports/missing.py"),
        (3, "none"),
        (3, "src/agentdeck/adapters/__init__.py"),
        (3, "tests/product_kernel/missing.py"),
    ),
)
def test_register_rejects_admitted_rows_without_valid_evidence(
    column: int, invalid_value: str
) -> None:
    fields = [
        "agentdeck.providers",
        "src/agentdeck/adapters/__init__.py",
        "src/agentdeck/ports/__init__.py",
        "tests/product_kernel/test_context_firewall.py",
        "admitted",
    ]
    fields[column] = invalid_value
    text = REGISTER.read_text(encoding="utf-8").replace(
        "Status: no legacy code admitted", "Status: legacy code admitted"
    )
    text = register_with_row(text, f"| {' | '.join(fields)} |")

    with pytest.raises(AssertionError):
        validate_legacy_reuse_register(text, ROOT)


def test_register_rejects_unsupported_decision() -> None:
    text = register_with_row(
        REGISTER.read_text(encoding="utf-8"),
        "| agentdeck.providers | none | none | none | unsupported |",
    )

    with pytest.raises(AssertionError, match="decision"):
        validate_legacy_reuse_register(text, ROOT)


def test_register_requires_exact_header() -> None:
    text = REGISTER.read_text(encoding="utf-8").replace(
        "| Legacy module | New Adapter | Port | Characterization test | Decision |",
        "| Legacy code | New Adapter | Port | Characterization test | Decision |",
    )

    with pytest.raises(AssertionError, match="header"):
        validate_legacy_reuse_register(text, ROOT)


def test_register_requires_exact_none_sentinel() -> None:
    text = REGISTER.read_text(encoding="utf-8").replace(
        "| none | none | none | none | not admitted |",
        "| none | none | none | none | rejected |",
    )

    with pytest.raises(AssertionError, match="sentinel"):
        validate_legacy_reuse_register(text, ROOT)


def test_rejected_register_row_does_not_require_admission_evidence() -> None:
    text = register_with_row(
        REGISTER.read_text(encoding="utf-8"),
        "| agentdeck.unsuitable | none | none | none | rejected |",
    )

    validate_legacy_reuse_register(text, ROOT)


def test_register_rejects_exact_quality_review_counterexample() -> None:
    text = REGISTER.read_text(encoding="utf-8").replace(
        NO_ADMITTED_STATUS, "Status: no legacy modules are admitted"
    )
    text = register_with_row(
        text,
        "| does.not.exist | src/agentdeck/adapters/__init__.py | "
        "src/agentdeck/ports/__init__.py | "
        "tests/product_kernel/test_context_firewall.py | admitted |",
    )

    with pytest.raises(AssertionError):
        validate_legacy_reuse_register(text, ROOT)


def test_valid_admitted_register_passes(tmp_path: Path) -> None:
    text, _ = valid_admission_fixture(tmp_path)

    validate_legacy_reuse_register(text, tmp_path)


def test_admitted_register_requires_exact_admitted_status(tmp_path: Path) -> None:
    text, _ = valid_admission_fixture(tmp_path)
    text = text.replace(
        "Status: legacy code admitted", "Status: no legacy modules are admitted"
    )

    with pytest.raises(AssertionError, match="status"):
        validate_legacy_reuse_register(text, tmp_path)


@pytest.mark.parametrize("legacy", ("does.not.exist", "agentdeck.does_not_exist"))
def test_admitted_register_requires_real_agentdeck_legacy_module(
    tmp_path: Path, legacy: str
) -> None:
    text, paths = valid_admission_fixture(tmp_path)
    text = text.replace(paths["legacy"], legacy, 1)

    with pytest.raises(AssertionError, match="legacy"):
        validate_legacy_reuse_register(text, tmp_path)


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("adapter", "src/agentdeck/adapters/__init__.py"),
        ("port", "src/agentdeck/ports/__init__.py"),
        ("test", "tests/product_kernel/test_context_firewall.py"),
    ),
)
def test_admitted_register_rejects_generic_artifacts(
    tmp_path: Path, field: str, replacement: str
) -> None:
    text, paths = valid_admission_fixture(tmp_path)
    source = "import agentdeck.legacy_transport\n"
    if field == "test":
        source += (
            "import agentdeck.adapters.legacy_transport\n"
            "import agentdeck.ports.transport\n"
        )
    write_fixture_file(tmp_path, replacement, source)
    text = text.replace(paths[field], replacement, 1)

    with pytest.raises(AssertionError, match="artifact"):
        validate_legacy_reuse_register(text, tmp_path)


def test_admitted_adapter_must_import_legacy_namespace(tmp_path: Path) -> None:
    text, paths = valid_admission_fixture(tmp_path)
    write_fixture_file(tmp_path, paths["adapter"], "import agentdeck.unrelated\n")

    with pytest.raises(AssertionError, match="import"):
        validate_legacy_reuse_register(text, tmp_path)


@pytest.mark.parametrize(
    "missing_reference",
    (
        "agentdeck.legacy_transport",
        "agentdeck.adapters.legacy_transport",
        "agentdeck.ports.transport",
    ),
)
def test_characterization_test_must_reference_admission_modules(
    tmp_path: Path, missing_reference: str
) -> None:
    text, paths = valid_admission_fixture(tmp_path)
    source = (tmp_path / paths["test"]).read_text(encoding="utf-8")
    source = source.replace(f"import {missing_reference}\n", "")
    write_fixture_file(tmp_path, paths["test"], source)

    with pytest.raises(AssertionError, match="reference"):
        validate_legacy_reuse_register(text, tmp_path)


def test_register_rejects_comments_only_test_and_unused_port(tmp_path: Path) -> None:
    text, paths = valid_admission_fixture(tmp_path)
    write_fixture_file(
        tmp_path, paths["adapter"], "import agentdeck.legacy_transport\n"
    )
    write_fixture_file(
        tmp_path,
        paths["test"],
        "# agentdeck.legacy_transport\n"
        "# agentdeck.adapters.legacy_transport\n"
        "# agentdeck.ports.transport\n",
    )

    with pytest.raises(AssertionError):
        validate_legacy_reuse_register(text, tmp_path)


def test_admitted_adapter_must_import_registered_port(tmp_path: Path) -> None:
    text, paths = valid_admission_fixture(tmp_path)
    write_fixture_file(
        tmp_path, paths["adapter"], "import agentdeck.legacy_transport\n"
    )

    with pytest.raises(AssertionError, match="Port"):
        validate_legacy_reuse_register(text, tmp_path)


def test_characterization_requires_test_function(tmp_path: Path) -> None:
    text, paths = valid_admission_fixture(tmp_path)
    source = (tmp_path / paths["test"]).read_text(encoding="utf-8")
    write_fixture_file(tmp_path, paths["test"], source.split("\ndef test_", 1)[0])

    with pytest.raises(AssertionError, match="test function"):
        validate_legacy_reuse_register(text, tmp_path)


def test_characterization_test_function_requires_assert(tmp_path: Path) -> None:
    text, paths = valid_admission_fixture(tmp_path)
    source = (tmp_path / paths["test"]).read_text(encoding="utf-8")
    write_fixture_file(tmp_path, paths["test"], source.replace("    assert True\n", "    pass\n"))

    with pytest.raises(AssertionError, match="assert"):
        validate_legacy_reuse_register(text, tmp_path)
