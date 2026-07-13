from __future__ import annotations

from contextlib import suppress
import shutil
import subprocess
import uuid

from agentdeck.models import AgentSpec, RuntimeConfig
from agentdeck.runtime.protocol import TransportCapabilities

from .base import RuntimeDoctorResult


DETACHED_SESSION_WIDTH = 160
DETACHED_SESSION_HEIGHT = 60
TMUX_COMMAND_TIMEOUT_SECONDS = 5.0


class TmuxBackend:
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities.tmux_fallback()

    def doctor(self) -> RuntimeDoctorResult:
        tmux_path = shutil.which("tmux")
        if not tmux_path:
            return RuntimeDoctorResult(ok=False, detail="tmux not found on PATH")
        version = subprocess.run(
            [tmux_path, "-V"],
            check=False,
            capture_output=True,
            text=True,
            timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
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
        subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
        )

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
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
        )
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
            timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
        )
        return result.stdout

    def send_input(self, config: RuntimeConfig, pane_id: str, text: str) -> None:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        submit_command = [
            "tmux", "-L", config.socket_name, "send-keys", "-t", pane_id, "Enter",
        ]
        if not text:
            subprocess.run(
                submit_command, check=True, timeout=TMUX_COMMAND_TIMEOUT_SECONDS
            )
            return
        buffer_name = f"agentdeck-{uuid.uuid4().hex}"
        try:
            subprocess.run(
                [
                    "tmux", "-L", config.socket_name, "load-buffer",
                    "-b", buffer_name, "-",
                ],
                check=True,
                input=text,
                text=True,
                timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
            )
            subprocess.run(
                [
                    "tmux", "-L", config.socket_name, "paste-buffer", "-p",
                    "-b", buffer_name, "-t", pane_id,
                ],
                check=True,
                timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
            )
            subprocess.run(
                submit_command, check=True, timeout=TMUX_COMMAND_TIMEOUT_SECONDS
            )
        finally:
            with suppress(Exception):
                subprocess.run(
                    [
                        "tmux", "-L", config.socket_name, "delete-buffer",
                        "-b", buffer_name,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
                )

    def kill_pane(self, config: RuntimeConfig, pane_id: str) -> None:
        subprocess.run(
            ["tmux", "-L", config.socket_name, "kill-pane", "-t", pane_id],
            check=True,
            timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
        )

    def pane_exists(self, config: RuntimeConfig, pane_id: str) -> bool:
        result = subprocess.run(
            ["tmux", "-L", config.socket_name, "display-message", "-p", "-t", pane_id, "#{pane_id}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
        )
        return result.returncode == 0 and result.stdout.strip() == pane_id
