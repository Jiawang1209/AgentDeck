from __future__ import annotations

import ast
from pathlib import Path
import sys

import pytest

PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "agentdeck"
LAYERS = ("kernel", "application", "ports", "adapters", "product")
COMPOSITION_ROOT_ALLOWED_IMPORTS = (
    "agentdeck.kernel",
    "agentdeck.ports",
    "agentdeck.application",
    "agentdeck.adapters",
    "agentdeck.product",
)
OBSERVER_PRODUCT_ALLOWED_IMPORTS = (
    "agentdeck.kernel", "agentdeck.ports", "agentdeck.product",
)
FORBIDDEN = {
    "agentdeck.cli",
    "agentdeck.state",
    "agentdeck.models",
    "agentdeck.conversation",
    "agentdeck.daemon",
    "agentdeck.mission",
    "agentdeck.mission_orchestration",
}
ALLOWED_LAYER_IMPORTS = {
    "kernel": ("agentdeck.kernel",),
    "ports": ("agentdeck.kernel", "agentdeck.ports"),
    "application": ("agentdeck.kernel", "agentdeck.ports", "agentdeck.application"),
    "adapters": ("agentdeck.kernel", "agentdeck.ports", "agentdeck.adapters"),
    "product": ("agentdeck.kernel", "agentdeck.application", "agentdeck.product"),
}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = imported_from_module(path, node)
            if module is None:
                result.add("agentdeck.__unresolved_relative_import__")
                continue
            if module:
                result.update(f"{module}.{alias.name}" for alias in node.names)
    return result


def imported_from_module(path: Path, node: ast.ImportFrom) -> str | None:
    if not node.level:
        return node.module
    try:
        relative_path = path.relative_to(PACKAGE_ROOT).with_suffix("")
    except ValueError:
        return None
    package = ["agentdeck", *relative_path.parts]
    package.pop()
    parents = node.level - 1
    if parents >= len(package):
        return None
    base = ".".join(package[: len(package) - parents])
    return f"{base}.{node.module}" if node.module else base


def is_in_namespace(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(f"{prefix}.")


def is_allowed_layer_import(name: str, allowed: tuple[str, ...]) -> bool:
    return any(is_in_namespace(name, prefix) for prefix in allowed)


def is_forbidden_legacy_import(name: str) -> bool:
    return any(is_in_namespace(name, prefix) for prefix in FORBIDDEN)


def layer_python_files(layer: Path) -> tuple[Path, ...]:
    return tuple(layer.rglob("*.py"))


def write_module(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_imported_modules_expands_from_agentdeck_alias(tmp_path: Path) -> None:
    path = tmp_path / "module.py"
    write_module(path, "from agentdeck import cli\n")

    assert "agentdeck.cli" in imported_modules(path)
    assert "agentdeck" not in imported_modules(path)


def test_imported_modules_resolves_relative_imports_under_package_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root = tmp_path / "src" / "agentdeck"
    path = package_root / "adapters" / "nested.py"
    write_module(path, "from ..ports import Store\n")
    monkeypatch.setattr(sys.modules[__name__], "PACKAGE_ROOT", package_root)

    assert "agentdeck.ports.Store" in imported_modules(path)


def test_layer_namespace_rejects_bare_prefix_extension() -> None:
    assert not is_allowed_layer_import("agentdeck.kernel_evil", ("agentdeck.kernel",))


def test_layer_python_files_includes_nested_modules(tmp_path: Path) -> None:
    layer = tmp_path / "product"
    nested = layer / "nested" / "escape.py"
    write_module(nested, "import agentdeck.adapters\n")

    assert nested in layer_python_files(layer)


def test_layer_direction_rejects_bare_agentdeck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root = tmp_path / "src" / "agentdeck"
    write_module(package_root / "kernel" / "uses_root.py", "import agentdeck\n")
    monkeypatch.setattr(sys.modules[__name__], "PACKAGE_ROOT", package_root)

    with pytest.raises(AssertionError):
        test_layer_dependency_direction()


def test_legacy_namespace_detects_descendants() -> None:
    assert is_forbidden_legacy_import("agentdeck.daemon.recovery")


def test_bootstrap_legacy_import_fails_both_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root = tmp_path / "src" / "agentdeck"
    bootstrap = package_root / "product" / "bootstrap.py"
    write_module(bootstrap, "import agentdeck.daemon.recovery\n")
    monkeypatch.setattr(sys.modules[__name__], "PACKAGE_ROOT", package_root)

    with pytest.raises(AssertionError):
        test_layer_dependency_direction()
    with pytest.raises(AssertionError):
        test_only_adapters_may_import_admitted_legacy()


def test_bootstrap_rejects_noncomposition_agentdeck_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root = tmp_path / "src" / "agentdeck"
    bootstrap = package_root / "product" / "bootstrap.py"
    write_module(bootstrap, "import agentdeck.runtime\n")
    monkeypatch.setattr(sys.modules[__name__], "PACKAGE_ROOT", package_root)

    with pytest.raises(AssertionError):
        test_layer_dependency_direction()


def test_bootstrap_allows_explicit_new_composition_layers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root = tmp_path / "src" / "agentdeck"
    bootstrap = package_root / "product" / "bootstrap.py"
    write_module(
        bootstrap,
        "\n".join(
            (
                "import agentdeck.kernel",
                "import agentdeck.ports",
                "import agentdeck.application",
                "import agentdeck.adapters",
                "import agentdeck.product",
            )
        ),
    )
    monkeypatch.setattr(sys.modules[__name__], "PACKAGE_ROOT", package_root)

    test_layer_dependency_direction()


def test_from_agentdeck_import_kernel_is_allowed_in_kernel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root = tmp_path / "src" / "agentdeck"
    write_module(package_root / "kernel" / "uses_kernel.py", "from agentdeck import kernel\n")
    monkeypatch.setattr(sys.modules[__name__], "PACKAGE_ROOT", package_root)

    test_layer_dependency_direction()


def test_rewrite_packages_exist() -> None:
    for layer in LAYERS:
        assert (PACKAGE_ROOT / layer / "__init__.py").is_file(), layer


def test_kernel_and_application_do_not_import_legacy() -> None:
    for layer in ("kernel", "application"):
        for path in layer_python_files(PACKAGE_ROOT / layer):
            assert not any(is_forbidden_legacy_import(name) for name in imported_modules(path)), path


def test_only_adapters_may_import_admitted_legacy() -> None:
    for layer in ("kernel", "application", "ports", "product"):
        for path in layer_python_files(PACKAGE_ROOT / layer):
            assert not any(is_forbidden_legacy_import(name) for name in imported_modules(path)), path


def test_layer_dependency_direction() -> None:
    for layer, allowed in ALLOWED_LAYER_IMPORTS.items():
        for path in layer_python_files(PACKAGE_ROOT / layer):
            path_allowed = {
                PACKAGE_ROOT / "product" / "bootstrap.py": COMPOSITION_ROOT_ALLOWED_IMPORTS,
                PACKAGE_ROOT / "product" / "observer_command.py": COMPOSITION_ROOT_ALLOWED_IMPORTS,
                PACKAGE_ROOT / "product" / "observer.py": OBSERVER_PRODUCT_ALLOWED_IMPORTS,
            }.get(path, allowed)
            internal = {name for name in imported_modules(path) if is_in_namespace(name, "agentdeck")}
            assert all(is_allowed_layer_import(name, path_allowed) for name in internal), (path, internal)
