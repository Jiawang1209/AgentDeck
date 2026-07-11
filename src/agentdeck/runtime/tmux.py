from __future__ import annotations

import shutil
import subprocess
import time

from agentdeck.models import AgentSpec, RuntimeConfig

from .base import RuntimeDoctorResult


MIN_INPUT_SUBMIT_DELAY_SECONDS = 0.25
INPUT_SUBMIT_DELAY_SECONDS_PER_CHARACTER = 0.001
MAX_INPUT_SUBMIT_DELAY_SECONDS = 1.5
# Compatibility name for callers that only need the short-input floor.
INPUT_SUBMIT_DELAY_SECONDS = MIN_INPUT_SUBMIT_DELAY_SECONDS
DETACHED_SESSION_WIDTH = 160
DETACHED_SESSION_HEIGHT = 60


def input_submit_delay(text: str) -> float:
    """Return a bounded paste-settle delay before submitting literal tmux input."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    return min(
        MAX_INPUT_SUBMIT_DELAY_SECONDS,
        max(
            MIN_INPUT_SUBMIT_DELAY_SECONDS,
            len(text) * INPUT_SUBMIT_DELAY_SECONDS_PER_CHARACTER,
        ),
    )


class TmuxBackend:
    def doctor(self) -> RuntimeDoctorResult:
        tmux_path = shutil.which("tmux")
        if not tmux_path:
            return RuntimeDoctorResult(ok=False, detail="tmux not found on PATH")
        version = subprocess.run(
            [tmux_path, "-V"],
            check=False,
            capture_output=True,
            text=True,
        )
        detail = version.stdout.strip() or version.stderr.strip() or tmux_path
        return RuntimeDoctorResult(ok=version.returncode == 0, detail=detail)

    def create_session(self, config: RuntimeConfig) -> None:
        command = [
            "tmux",
            "-L",
            config.socket_name,
            "new-session",
            "-d",
            "-x",
            str(DETACHED_SESSION_WIDTH),
            "-y",
            str(DETACHED_SESSION_HEIGHT),
            "-s",
            config.session_name,
            "-n",
            "control",
        ]
        subprocess.run(command, check=False, capture_output=True, text=True)

    def spawn_agent(self, config: RuntimeConfig, agent: AgentSpec, cwd: str) -> str:
        command = [
            "tmux",
            "-L",
            config.socket_name,
            "split-window",
            "-P",
            "-F",
            "#{pane_id}",
            "-d",
            "-t",
            config.session_name,
            "-c",
            cwd,
            agent.command,
        ]
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        return result.stdout.strip()

    def capture_output(self, config: RuntimeConfig, pane_id: str, lines: int = 200) -> str:
        result = subprocess.run(
            [
                "tmux",
                "-L",
                config.socket_name,
                "capture-pane",
                "-p",
                "-t",
                pane_id,
                "-S",
                f"-{lines}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout

    def send_input(self, config: RuntimeConfig, pane_id: str, text: str) -> None:
        submit_delay = input_submit_delay(text)
        subprocess.run(
            ["tmux", "-L", config.socket_name, "send-keys", "-t", pane_id, "-l", text],
            check=True,
        )
        time.sleep(submit_delay)
        subprocess.run(
            ["tmux", "-L", config.socket_name, "send-keys", "-t", pane_id, "Enter"],
            check=True,
        )

    def kill_pane(self, config: RuntimeConfig, pane_id: str) -> None:
        subprocess.run(
            ["tmux", "-L", config.socket_name, "kill-pane", "-t", pane_id],
            check=True,
        )

    def pane_exists(self, config: RuntimeConfig, pane_id: str) -> bool:
        result = subprocess.run(
            ["tmux", "-L", config.socket_name, "display-message", "-p", "-t", pane_id, "#{pane_id}"],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and result.stdout.strip() == pane_id
