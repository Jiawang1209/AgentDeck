from __future__ import annotations

import json
from pathlib import Path

from agentdeck.kernel.diagnostics import Diagnostic, Severity
from agentdeck.product.presenter import DIAGNOSTIC_JSON_FIELDS

from .test_product_shell import AsyncLines, _build, async_test


def _redacted_diagnostic() -> Diagnostic:
    return Diagnostic.create(
        code="storage_recovery_failed",
        stage="storage",
        severity=Severity.ERROR,
        actor="agentdeck",
        summary="The project store could not be opened.",
        cause="See /Users/private/x for raw stderr: boom in the log.",
        impact="No new writes were accepted.",
        protection="No partial state was treated as authoritative.",
        recovery_actions=("Inspect the project database at /Users/private/x.",),
        retryable=False,
        outcome_known=False,
        occurred_at="2026-07-22T00:00:00+00:00",
    )


@async_test
async def test_diagnose_json_is_stable_and_redacted(tmp_path: Path) -> None:
    output: list[str] = []
    shell = _build(tmp_path, AsyncLines("/diagnose --json", "/exit"), output)
    shell._latest_diagnostic = _redacted_diagnostic()

    await shell.run_async()

    transcript = "\n".join(output)
    json_line = next(line for line in output if line.startswith("{"))
    assert set(json.loads(json_line)) == DIAGNOSTIC_JSON_FIELDS
    assert "/Users/private" not in transcript
    assert "raw stderr" not in transcript


@async_test
async def test_diagnose_interactive_emits_a_plain_language_error_card(
    tmp_path: Path,
) -> None:
    output: list[str] = []
    shell = _build(tmp_path, AsyncLines("/diagnose", "/exit"), output)
    shell._latest_diagnostic = _redacted_diagnostic()

    await shell.run_async()

    transcript = "\n".join(output)
    assert "What happened" in transcript
    assert not any(line.startswith("{") for line in output)


@async_test
async def test_diagnose_with_no_active_diagnostic_reports_none_active(
    tmp_path: Path,
) -> None:
    output: list[str] = []
    shell = _build(tmp_path, AsyncLines("/diagnose", "/exit"), output)

    await shell.run_async()

    assert "No ProductSession diagnostic is active." in "\n".join(output)
