from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentdeck.models import AgentSpec, RuntimeConfig
from agentdeck.runtime.protocol import TransportCapabilities


@dataclass(frozen=True)
class RuntimeDoctorResult:
    ok: bool
    detail: str


class RuntimeBackend(Protocol):
    def capabilities(self) -> TransportCapabilities:
        raise NotImplementedError

    def doctor(self) -> RuntimeDoctorResult:
        raise NotImplementedError

    def create_session(self, config: RuntimeConfig) -> None:
        raise NotImplementedError

    def spawn_agent(self, config: RuntimeConfig, agent: AgentSpec, cwd: str) -> str:
        raise NotImplementedError

    def capture_output(self, config: RuntimeConfig, pane_id: str, lines: int = 200) -> str:
        raise NotImplementedError

    def send_input(self, config: RuntimeConfig, pane_id: str, text: str) -> None:
        raise NotImplementedError

    def kill_pane(self, config: RuntimeConfig, pane_id: str) -> None:
        raise NotImplementedError

    def pane_exists(self, config: RuntimeConfig, pane_id: str) -> bool:
        raise NotImplementedError
