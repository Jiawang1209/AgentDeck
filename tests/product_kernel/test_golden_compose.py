"""Golden-run slice (c) — wire a real ACP adapter composition into the driver.

`compose_golden_runner` builds a `GoldenRunner` whose Leader and per-stage
Worker factory delegate to an ACP adapter composition's `.leader(...)` /
`.worker(...)`. Here a fake composition proves the wiring deterministically; the
real `build_acp_adapter_composition` result plugged in makes the runner drive
REAL ACP sessions, and running THAT is the separately authorized live gate.
"""
from __future__ import annotations

from pathlib import Path

from agentdeck.product.bootstrap import GoldenRunner, compose_golden_runner


class _RecordingComposition:
    def __init__(self) -> None:
        self.leader_calls: list[tuple[str, str]] = []
        self.worker_calls: list[tuple[str, str]] = []

    def leader(self, backend_id: str, *, model: str) -> str:
        self.leader_calls.append((backend_id, model))
        return f"leader::{backend_id}::{model}"

    def worker(self, backend_id: str, *, model: str = "native-default") -> str:
        self.worker_calls.append((backend_id, model))
        return f"worker::{backend_id}::{model}"


class _Task:
    def __init__(self, backend: str) -> None:
        self.backend = backend


def test_compose_builds_a_runner_and_leader_from_the_composition(
    tmp_path: Path,
) -> None:
    composition = _RecordingComposition()
    runner = compose_golden_runner(
        project_root=tmp_path,
        adapter_composition=composition,
        leader_backend="codex-cli",
        leader_model="gpt-5.5",
        available_leaders={"codex-cli": ("gpt-5.5",)},
    )
    assert isinstance(runner, GoldenRunner)
    # The Leader is minted once, from the composition, for the chosen backend.
    assert composition.leader_calls == [("codex-cli", "gpt-5.5")]
    # No Worker is constructed at compose time (only wired).
    assert composition.worker_calls == []


def test_compose_wires_worker_factory_to_composition_per_task_backend(
    tmp_path: Path,
) -> None:
    composition = _RecordingComposition()
    runner = compose_golden_runner(
        project_root=tmp_path,
        adapter_composition=composition,
        leader_backend="codex-cli",
        leader_model="gpt-5.5",
        available_leaders={"codex-cli": ("gpt-5.5",)},
    )
    assert runner._worker_factory(_Task("claude-cli")) == (
        "worker::claude-cli::native-default"
    )
    assert runner._worker_factory(_Task("codex-cli")) == (
        "worker::codex-cli::native-default"
    )
    assert composition.worker_calls == [
        ("claude-cli", "native-default"),
        ("codex-cli", "native-default"),
    ]
