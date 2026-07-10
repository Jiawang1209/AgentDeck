from __future__ import annotations

import math
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from agentdeck.mission import provider_family
from agentdeck.models import AgentSpec, RuntimeConfig

from .base import RuntimeBackend


WorkerReadinessStatus = Literal[
    "starting",
    "ready",
    "setup_required",
    "failed",
    "pane_lost",
    "timeout",
]

_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_TERMINAL_STATUSES = frozenset({"setup_required", "failed", "pane_lost"})
_ACTIVE_FRAME_LINE_COUNT = 40


@dataclass(frozen=True)
class WorkerReadinessEvidence:
    status: WorkerReadinessStatus
    reason: str | None


@dataclass(frozen=True)
class WorkerReadiness:
    agent_id: str
    provider: str
    status: WorkerReadinessStatus
    reason: str | None


@dataclass(frozen=True)
class WorkerReadinessBatch:
    all_ready: bool
    results: tuple[WorkerReadiness, ...]
    timed_out: bool = False


def classify_worker_readiness(provider: str, output: str) -> WorkerReadinessEvidence:
    if not isinstance(provider, str):
        raise TypeError("provider must be a string")
    if not isinstance(output, str):
        raise TypeError("output must be a string")

    family = provider_family(provider)
    normalized_lines = _ANSI_ESCAPE.sub("", output).lower().splitlines()
    active_lines = normalized_lines[-_ACTIVE_FRAME_LINE_COUNT:]
    active_frame = "\n".join(active_lines)
    # Conversation text is untrusted content, not CLI state evidence.  The
    # terminal adapters currently label captured user turns this way.
    state_lines = tuple(
        line.strip()
        for line in active_lines
        if not line.lstrip().startswith(("user:", "›", "❯"))
    )

    if family == "codex":
        if (
            "configured model requires a newer version of codex" in state_lines
            or "model incompatible" in state_lines
        ):
            return WorkerReadinessEvidence(
                "failed", "configured model is incompatible with Codex CLI"
            )
        if any(
            re.fullmatch(r"starting mcp servers(?:\s*\([^\r\n]*\))?", line)
            for line in state_lines
        ):
            return WorkerReadinessEvidence(
                "starting", "CLI startup is still in progress"
            )
        if "openai codex" in active_frame and any(
            re.match(r"^\s*›\s+ask codex\b", line) for line in active_lines
        ):
            return WorkerReadinessEvidence("ready", None)
        return WorkerReadinessEvidence("starting", "Codex CLI prompt is not ready")

    if family == "claude":
        if (
            "do you trust the files in this folder?" in state_lines
            and "yes, i trust this folder" in state_lines
        ):
            return WorkerReadinessEvidence("setup_required", "directory trust required")
        if any(
            re.fullmatch(
                r"(?:not logged in|authentication required)\.[^\r\n]*/login[^\r\n]*",
                line,
            )
            for line in state_lines
        ):
            return WorkerReadinessEvidence("setup_required", "Claude login required")
        if any(
            re.fullmatch(r"starting mcp servers(?:\s*\([^\r\n]*\))?", line)
            for line in state_lines
        ):
            return WorkerReadinessEvidence(
                "starting", "CLI startup is still in progress"
            )
        if (
            "claude code" in active_frame
            and any(re.match(r"^\s*❯\s+", line) for line in active_lines)
            and any("context" in line for line in active_lines)
        ):
            return WorkerReadinessEvidence("ready", None)
        return WorkerReadinessEvidence("starting", "Claude CLI prompt is not ready")

    return WorkerReadinessEvidence("failed", "unsupported worker provider")


def _validated_number(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{name} must be finite")
    if converted < 0:
        raise ValueError(f"{name} must be non-negative")
    return converted


def _validated_clock(monotonic: Callable[[], float]) -> float:
    value = monotonic()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("monotonic must return a finite number")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError("monotonic must return a finite number")
    return converted


def _validate_selected(
    selected: Sequence[tuple[AgentSpec, str]],
) -> tuple[tuple[AgentSpec, str], ...]:
    if isinstance(selected, (str, bytes)) or not isinstance(selected, Sequence):
        raise TypeError("selected must be a sequence of (AgentSpec, pane_id) pairs")
    if not selected:
        raise ValueError("selected must contain at least one worker")

    validated: list[tuple[AgentSpec, str]] = []
    agent_ids: set[str] = set()
    pane_ids: set[str] = set()
    for item in selected:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise TypeError("each selected item must be an (AgentSpec, pane_id) pair")
        agent, pane_id = item
        if not isinstance(agent, AgentSpec):
            raise TypeError("selected agent must be an AgentSpec")
        if isinstance(pane_id, bool) or not isinstance(pane_id, str):
            raise TypeError("pane_id must be a string")
        if not pane_id.strip():
            raise ValueError("pane_id must not be empty")
        if agent.agent_id in agent_ids:
            raise ValueError("selected contains duplicate agent ids")
        normalized_pane_id = pane_id.strip()
        if normalized_pane_id in pane_ids:
            raise ValueError("selected contains duplicate pane ids")
        agent_ids.add(agent.agent_id)
        pane_ids.add(normalized_pane_id)
        validated.append((agent, pane_id))
    return tuple(validated)


def _timeout_results(results: tuple[WorkerReadiness, ...]) -> tuple[WorkerReadiness, ...]:
    return tuple(
        WorkerReadiness(
            agent_id=item.agent_id,
            provider=item.provider,
            status="timeout" if item.status == "starting" else item.status,
            reason="worker readiness timed out" if item.status == "starting" else item.reason,
        )
        for item in results
    )


def wait_for_worker_readiness(
    *,
    runtime_config: RuntimeConfig,
    backend: RuntimeBackend,
    selected: Sequence[tuple[AgentSpec, str]],
    timeout_seconds: float,
    poll_interval: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> WorkerReadinessBatch:
    if not isinstance(runtime_config, RuntimeConfig):
        raise TypeError("runtime_config must be a RuntimeConfig")
    if not callable(getattr(backend, "pane_exists", None)) or not callable(
        getattr(backend, "capture_output", None)
    ):
        raise TypeError("backend must provide pane_exists and capture_output")
    if not callable(monotonic):
        raise TypeError("monotonic must be callable")
    if not callable(sleeper):
        raise TypeError("sleeper must be callable")

    workers = _validate_selected(selected)
    timeout = _validated_number("timeout_seconds", timeout_seconds)
    interval = _validated_number("poll_interval", poll_interval)
    started_at = _validated_clock(monotonic)
    deadline = started_at + timeout

    effective_interval = interval if interval > 0 else min(0.01, timeout)
    latest: tuple[WorkerReadiness, ...] = ()
    scheduled_sleep_total = 0.0
    poll_index = 0

    while True:
        if poll_index > 0:
            now = max(
                _validated_clock(monotonic),
                started_at + scheduled_sleep_total,
            )
            if now >= deadline:
                return WorkerReadinessBatch(
                    all_ready=False,
                    results=_timeout_results(latest),
                    timed_out=True,
                )

        current: list[WorkerReadiness] = []
        for worker_index, (agent, pane_id) in enumerate(workers):
            family = provider_family(agent.provider)
            pane_error = False
            try:
                pane_exists = backend.pane_exists(runtime_config, pane_id)
            except Exception:
                pane_error = True
                pane_exists = False
            pane_checked_at = max(
                _validated_clock(monotonic),
                started_at + scheduled_sleep_total,
            )
            if pane_checked_at >= deadline:
                current.append(
                    WorkerReadiness(
                        agent.agent_id,
                        family,
                        "starting",
                        "worker pane check completed after readiness deadline",
                    )
                )
                current.extend(
                    WorkerReadiness(
                        remaining_agent.agent_id,
                        provider_family(remaining_agent.provider),
                        "starting",
                        "worker was not probed before readiness deadline",
                    )
                    for remaining_agent, _remaining_pane_id in workers[
                        worker_index + 1 :
                    ]
                )
                return WorkerReadinessBatch(
                    all_ready=False,
                    results=_timeout_results(tuple(current)),
                    timed_out=True,
                )
            if pane_error:
                current.append(
                    WorkerReadiness(
                        agent.agent_id,
                        family,
                        "failed",
                        "worker pane check failed",
                    )
                )
                continue
            if not pane_exists:
                current.append(
                    WorkerReadiness(
                        agent.agent_id,
                        family,
                        "pane_lost",
                        "worker pane is no longer available",
                    )
                )
                continue
            capture_error = False
            try:
                output = backend.capture_output(runtime_config, pane_id, lines=200)
            except Exception:
                capture_error = True
                output = ""
            captured_at = max(
                _validated_clock(monotonic),
                started_at + scheduled_sleep_total,
            )
            if captured_at >= deadline:
                current.append(
                    WorkerReadiness(
                        agent.agent_id,
                        family,
                        "starting",
                        "worker capture completed after readiness deadline",
                    )
                )
                current.extend(
                    WorkerReadiness(
                        remaining_agent.agent_id,
                        provider_family(remaining_agent.provider),
                        "starting",
                        "worker was not probed before readiness deadline",
                    )
                    for remaining_agent, _remaining_pane_id in workers[
                        worker_index + 1 :
                    ]
                )
                return WorkerReadinessBatch(
                    all_ready=False,
                    results=_timeout_results(tuple(current)),
                    timed_out=True,
                )
            if capture_error:
                evidence = WorkerReadinessEvidence("failed", "worker pane capture failed")
            else:
                try:
                    evidence = classify_worker_readiness(agent.provider, output)
                except Exception:
                    evidence = WorkerReadinessEvidence(
                        "failed", "worker pane capture failed"
                    )
            current.append(
                WorkerReadiness(agent.agent_id, family, evidence.status, evidence.reason)
            )

        latest = tuple(current)
        if any(item.status in _TERMINAL_STATUSES for item in latest):
            return WorkerReadinessBatch(all_ready=False, results=latest)
        if all(item.status == "ready" for item in latest):
            return WorkerReadinessBatch(all_ready=True, results=latest)

        now = max(
            _validated_clock(monotonic),
            started_at + scheduled_sleep_total,
        )
        if now >= deadline:
            return WorkerReadinessBatch(
                all_ready=False,
                results=_timeout_results(latest),
                timed_out=True,
            )
        sleep_for = min(effective_interval, max(0.0, deadline - now))
        if sleep_for <= 0:
            return WorkerReadinessBatch(
                all_ready=False,
                results=_timeout_results(latest),
                timed_out=True,
            )
        sleeper(sleep_for)
        advanced_sleep = scheduled_sleep_total + sleep_for
        scheduled_sleep_total = (
            timeout
            if advanced_sleep <= scheduled_sleep_total
            else min(timeout, advanced_sleep)
        )
        poll_index += 1
