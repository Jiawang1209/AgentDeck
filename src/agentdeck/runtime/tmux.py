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

# pane 级用户选项:程序(codex / Claude Code 等)用 OSC 转义序列改的是
# `pane_title`,碰不到 `@` 用户选项。标签必须落在这里,否则 TUI 一启动就把
# `select-pane -T` 设的角色标签覆盖掉,而且没有任何东西会把它设回来——
# 人在 tmux 里于是分不清哪个 pane 是哪个角色。
PANE_LABEL_OPTION = "@agentdeck_label"
PANE_LABEL_SEPARATOR = " · "
# 用户选项优先,缺失时回退 pane_title:不是 AgentDeck 起的 pane 不该变成空边框。
PANE_BORDER_FORMAT = (
    f" #{{?{PANE_LABEL_OPTION},#{{{PANE_LABEL_OPTION}}},#{{pane_title}}}} "
)


def pane_label(
    agent_id: str, role: str | None = None, provider: str | None = None
) -> str:
    """把 agent 身份压成一行可见标签。纯函数:零 IO、不碰 tmux。

    空的 role/provider 直接省略,退化为裸 agent_id——标签只陈述已知的事实,
    不留 `coder ·  · ` 这种把"不知道"渲染成分隔符的空档。
    """
    parts = [
        part.strip() for part in (agent_id, role, provider) if part and part.strip()
    ]
    return PANE_LABEL_SEPARATOR.join(parts)


class TmuxBackend:
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities.tmux_fallback()

    def doctor(self) -> RuntimeDoctorResult:
        tmux_path = shutil.which("tmux")
        if not tmux_path:
            return RuntimeDoctorResult(ok=False, detail="tmux not found on PATH")
        try:
            version = subprocess.run(
                [tmux_path, "-V"],
                check=False,
                capture_output=True,
                text=True,
                timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return RuntimeDoctorResult(ok=False, detail="tmux command timed out")
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

    def apply_visible_layout(self, config: RuntimeConfig, panes: list[tuple[str, str]]) -> None:
        # 布局是可见性装饰：失败绝不能破坏已成功的 spawn，所以全部 check=False。
        base = ["tmux", "-L", config.socket_name]
        commands = [
            [*base, "select-layout", "-t", config.session_name, "tiled"],
            *(
                [*base, "select-pane", "-t", pane_id, "-T", label]
                for pane_id, label in panes
            ),
            *(
                [*base, "set-option", "-p", "-t", pane_id, PANE_LABEL_OPTION, label]
                for pane_id, label in panes
            ),
            [
                *base,
                "set-option",
                "-t",
                config.session_name,
                "pane-border-format",
                PANE_BORDER_FORMAT,
            ],
            [*base, "set-option", "-t", config.session_name, "pane-border-status", "top"],
        ]
        for command in commands:
            subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
            )

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
