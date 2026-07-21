"""Thin Product command router before the Task 39 CLI cutover."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["observer"]:
        from agentdeck.product.observer_command import run_observer_command
        return run_observer_command(args[1:])
    from agentdeck.cli import main as legacy_main
    return legacy_main(args)


__all__ = ["main"]
