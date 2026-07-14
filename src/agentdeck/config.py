from __future__ import annotations

import os
from pathlib import Path
import stat
import tomllib

from .models import (
    AgentSpec,
    AutonomousPolicy,
    DaemonConfig,
    LeaderConfig,
    ProjectConfig,
    RuntimeConfig,
)


CONFIG_DIR = ".agentdeck"
CONFIG_FILE = "config.toml"

_DAEMON_CONFIG_BOUNDS = {
    "idle_grace_seconds": (1, 86_400),
    "start_timeout_seconds": (1, 300),
    "controller_ttl_seconds": (1, 3_600),
    "max_frame_bytes": (1_024, 16 * 1_024 * 1_024),
}


DEFAULT_CONFIG = """[project]
name = "{project_name}"

[leader]
agent_id = "leader"
provider = "deepseek"
model = "deepseek-chat"
approval_mode = "confirm"

[[agents]]
agent_id = "planner"
role = "planning"
provider = "codex"
command = "codex"
workspace_mode = "shared"
role_prompt = "你是 AgentDeck 的规划 Agent，负责需求澄清、任务拆解、架构方案和风险识别。"

[[agents]]
agent_id = "coder"
role = "implementation"
provider = "codex"
command = "codex"
workspace_mode = "worktree"
role_prompt = "你是 AgentDeck 的实现 Agent，负责代码修改、测试执行、验证证据和实现总结。"

[[agents]]
agent_id = "reviewer"
role = "review"
provider = "claude"
command = "claude"
workspace_mode = "shared"
role_prompt = "你是 AgentDeck 的审查 Agent，负责发现 bug、风险、遗漏测试和架构问题。"

[runtime]
backend = "tmux"
session_name = "agentdeck"
socket_name = "agentdeck-{project_slug}"
"""


def project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for path in (current, *current.parents):
        if (path / ".git").exists():
            return path
    return current


def config_path(root: Path | None = None) -> Path:
    base = root or project_root()
    return base / CONFIG_DIR / CONFIG_FILE


_LAYOUT_DIRECTORY_FLAGS = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
)


def _open_or_create_layout_directory(parent_fd: int, name: str) -> int:
    created = False
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        created = True
    except FileExistsError:
        pass
    try:
        descriptor = os.open(name, _LAYOUT_DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise ValueError("project layout contains an unsafe directory") from exc
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError("project layout path must be a directory")
    if created:
        os.fsync(parent_fd)
    return descriptor


def _ensure_layout_file(parent_fd: int, name: str) -> None:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    created = False
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
            0o600,
            dir_fd=parent_fd,
        )
        created = True
    except FileExistsError:
        try:
            descriptor = os.open(name, os.O_RDONLY | nofollow, dir_fd=parent_fd)
        except OSError as exc:
            raise ValueError("project layout contains an unsafe file") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("project layout path must be a regular file")
        if created:
            os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if created:
        os.fsync(parent_fd)


def _open_or_create_project_root(root: Path) -> int:
    absolute = Path(os.path.abspath(root))
    if absolute == Path(absolute.anchor):
        return os.open(absolute, _LAYOUT_DIRECTORY_FLAGS)
    missing = [absolute.name]
    ancestor = absolute.parent
    while not ancestor.exists():
        if ancestor == Path(ancestor.anchor):
            break
        missing.append(ancestor.name)
        ancestor = ancestor.parent
    descriptor = os.open(ancestor.resolve(), _LAYOUT_DIRECTORY_FLAGS)
    try:
        for component in reversed(missing):
            child = _open_or_create_layout_directory(descriptor, component)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def ensure_project_layout(root: Path | None = None) -> Path:
    base = root or project_root()
    deck_dir = base / CONFIG_DIR
    try:
        root_fd = _open_or_create_project_root(base)
    except (OSError, ValueError) as exc:
        raise ValueError("project root is not a safe directory") from exc
    descriptors = [root_fd]
    try:
        deck_fd = _open_or_create_layout_directory(root_fd, CONFIG_DIR)
        descriptors.append(deck_fd)
        state_fd = _open_or_create_layout_directory(deck_fd, "state")
        descriptors.append(state_fd)
        logs_fd = _open_or_create_layout_directory(deck_fd, "logs")
        descriptors.append(logs_fd)
        descriptors.append(_open_or_create_layout_directory(logs_fd, "agents"))
        descriptors.append(_open_or_create_layout_directory(deck_fd, "artifacts"))
        descriptors.append(_open_or_create_layout_directory(deck_fd, "skills"))
        for filename in (
            "events.jsonl",
            "approvals.jsonl",
            "protocol-mutation.lock",
        ):
            _ensure_layout_file(state_fd, filename)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    return deck_dir


def write_default_config(root: Path | None = None) -> Path:
    base = root or project_root()
    ensure_project_layout(base)
    path = config_path(base)
    if path.exists():
        return path
    project_name = base.name
    project_slug = "".join(ch if ch.isalnum() else "-" for ch in project_name.lower()).strip("-")
    path.write_text(
        DEFAULT_CONFIG.format(project_name=project_name, project_slug=project_slug),
        encoding="utf-8",
    )
    return path


def load_config(root: Path | None = None) -> ProjectConfig:
    base = root or project_root()
    path = config_path(base)
    if not path.exists():
        raise FileNotFoundError(f"missing config: {path}")
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    leader_raw = raw.get("leader", {})
    runtime_raw = raw.get("runtime", {})
    agents_raw = raw.get("agents", [])
    raw_leader_command = leader_raw.get("transport_command", [])
    if type(raw_leader_command) is not list:
        raise ValueError("invalid Leader backend configuration")
    leader = LeaderConfig(
        agent_id=leader_raw.get("agent_id", "leader"),
        provider=leader_raw.get("provider", "deepseek"),
        model=leader_raw.get("model", "deepseek-chat"),
        approval_mode=leader_raw.get("approval_mode", "confirm"),
        backend_kind=leader_raw.get("backend_kind"),
        transport=leader_raw.get("transport"),
        transport_command=tuple(raw_leader_command),
    )
    explicit_leader = leader.backend_kind is not None or leader.transport is not None or bool(leader.transport_command)
    if explicit_leader:
        valid_pair = (leader.backend_kind, leader.transport) in {
            ("api", "http"),
            ("agent_cli", "acp"),
            ("agent_cli", "cli_subprocess"),
        }
        command_valid = all(
            isinstance(part, str) and part for part in leader.transport_command
        )
        if (
            not valid_pair
            or not command_valid
            or (leader.transport == "acp" and not leader.transport_command)
            or (leader.backend_kind == "api" and leader.transport_command)
        ):
            raise ValueError("invalid Leader backend configuration")
    runtime = RuntimeConfig(
        backend=runtime_raw.get("backend", "tmux"),
        session_name=runtime_raw.get("session_name", "agentdeck"),
        socket_name=runtime_raw.get("socket_name", f"agentdeck-{base.name}"),
    )
    daemon_raw = raw.get("daemon", {})
    if type(daemon_raw) is not dict:
        raise ValueError("invalid daemon configuration")
    unknown_daemon_keys = set(daemon_raw) - set(_DAEMON_CONFIG_BOUNDS)
    if unknown_daemon_keys:
        unknown = ", ".join(sorted(unknown_daemon_keys))
        raise ValueError(f"unknown daemon configuration keys: {unknown}")
    daemon = DaemonConfig(
        idle_grace_seconds=_daemon_config_value(
            daemon_raw, "idle_grace_seconds", DaemonConfig.idle_grace_seconds
        ),
        start_timeout_seconds=_daemon_config_value(
            daemon_raw, "start_timeout_seconds", DaemonConfig.start_timeout_seconds
        ),
        controller_ttl_seconds=_daemon_config_value(
            daemon_raw, "controller_ttl_seconds", DaemonConfig.controller_ttl_seconds
        ),
        max_frame_bytes=_daemon_config_value(
            daemon_raw, "max_frame_bytes", DaemonConfig.max_frame_bytes
        ),
    )
    agents = tuple(_agent_spec(item) for item in agents_raw)
    autonomous_raw = raw.get("autonomous", {})
    allowed = autonomous_raw.get("allowed_agents", []) if isinstance(autonomous_raw, dict) else []
    autonomous = AutonomousPolicy(
        allowed_agents=tuple(str(a) for a in allowed),
        max_approvals=int(autonomous_raw.get("max_approvals", 0)) if isinstance(autonomous_raw, dict) else 0,
    )
    skills_raw = raw.get("skills", {})
    allowed_sources = (
        skills_raw.get("allowed_sources", []) if isinstance(skills_raw, dict) else []
    )
    skills = {"allowed_sources": [str(src) for src in allowed_sources]}
    project_raw = raw.get("project", {})
    return ProjectConfig(
        name=project_raw.get("name", base.name),
        root=str(base),
        leader=leader,
        agents=agents,
        runtime=runtime,
        daemon=daemon,
        autonomous=autonomous,
        skills=skills,
    )


def _daemon_config_value(raw: dict[str, object], name: str, default: int) -> int:
    value = raw.get(name, default)
    minimum, maximum = _DAEMON_CONFIG_BOUNDS[name]
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(
            f"daemon {name} must be an integer between {minimum} and {maximum}"
        )
    return value


def _agent_spec(item: dict[str, object]) -> AgentSpec:
    transport = item.get("transport", "tmux")
    if type(transport) is not str or transport not in {"tmux", "acp"}:
        raise ValueError("agent transport must be 'tmux' or 'acp'")
    raw_command = item.get("transport_command", [])
    if type(raw_command) is not list:
        raise ValueError("agent transport_command must be an argv list")
    if any(type(part) is not str or not part for part in raw_command):
        raise ValueError("agent transport_command elements must be non-empty strings")
    transport_command = tuple(raw_command)
    if transport == "acp" and not transport_command:
        raise ValueError("ACP transport requires a non-empty transport_command")
    agent_id = item["agent_id"]
    return AgentSpec(
        agent_id=agent_id,  # type: ignore[arg-type]
        role=item.get("role", agent_id),  # type: ignore[arg-type]
        provider=item.get("provider", "codex"),  # type: ignore[arg-type]
        command=item.get("command", item.get("provider", "codex")),  # type: ignore[arg-type]
        workspace_mode=item.get("workspace_mode", "shared"),  # type: ignore[arg-type]
        role_prompt=item.get("role_prompt", ""),  # type: ignore[arg-type]
        transport=transport,
        transport_command=transport_command,
    )


def update_agent_role(root: Path, agent_id: str, role: str, role_prompt: str) -> AgentSpec:
    path = config_path(root)
    if not path.exists():
        raise FileNotFoundError(f"missing config: {path}")
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    updated: AgentSpec | None = None
    for item in raw.get("agents", []):
        if item.get("agent_id") == agent_id:
            item["role"] = role
            item["role_prompt"] = role_prompt
            updated = _agent_spec(item)
            break
    if updated is None:
        raise KeyError(agent_id)
    path.write_text(_dump_config(raw), encoding="utf-8")
    return updated


def update_leader_approval_mode(root: Path, approval_mode: str) -> LeaderConfig:
    path = config_path(root)
    if not path.exists():
        raise FileNotFoundError(f"missing config: {path}")
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    leader = raw.setdefault("leader", {})
    if not isinstance(leader, dict):
        leader = {}
        raw["leader"] = leader
    leader["approval_mode"] = approval_mode
    path.write_text(_dump_config(raw), encoding="utf-8")
    return load_config(root).leader


def update_autonomous_policy(root: Path, allowed_agents: tuple[str, ...], max_approvals: int) -> AutonomousPolicy:
    path = config_path(root)
    if not path.exists():
        raise FileNotFoundError(f"missing config: {path}")
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    raw["autonomous"] = {
        "allowed_agents": list(allowed_agents),
        "max_approvals": int(max_approvals),
    }
    path.write_text(_dump_config(raw), encoding="utf-8")
    return load_config(root).autonomous


def update_leader_provider(root: Path, provider: str, model: str | None = None) -> LeaderConfig:
    path = config_path(root)
    if not path.exists():
        raise FileNotFoundError(f"missing config: {path}")
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    leader = raw.setdefault("leader", {})
    if not isinstance(leader, dict):
        leader = {}
        raw["leader"] = leader
    leader["provider"] = provider
    if model is not None:
        leader["model"] = model
    path.write_text(_dump_config(raw), encoding="utf-8")
    return load_config(root).leader


def _quote_toml(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _dump_config(raw: dict[str, object]) -> str:
    project = raw.get("project", {})
    leader = raw.get("leader", {})
    agents = raw.get("agents", [])
    runtime = raw.get("runtime", {})
    lines: list[str] = []
    if isinstance(project, dict):
        lines.extend(["[project]", f"name = {_quote_toml(str(project.get('name', 'agentdeck')))}", ""])
    if isinstance(leader, dict):
        lines.extend(
            [
                "[leader]",
                f"agent_id = {_quote_toml(str(leader.get('agent_id', 'leader')))}",
                f"provider = {_quote_toml(str(leader.get('provider', 'deepseek')))}",
                f"model = {_quote_toml(str(leader.get('model', 'deepseek-chat')))}",
                f"approval_mode = {_quote_toml(str(leader.get('approval_mode', 'confirm')))}",
                "",
            ]
        )
        if leader.get("backend_kind") is not None:
            lines.insert(len(lines) - 1, f"backend_kind = {_quote_toml(str(leader['backend_kind']))}")
        if leader.get("transport") is not None:
            lines.insert(len(lines) - 1, f"transport = {_quote_toml(str(leader['transport']))}")
        if leader.get("transport_command"):
            command = leader["transport_command"]
            if isinstance(command, list):
                rendered = ", ".join(_quote_toml(str(part)) for part in command)
                lines.insert(len(lines) - 1, f"transport_command = [{rendered}]")
    if isinstance(agents, list):
        for item in agents:
            if not isinstance(item, dict):
                continue
            lines.extend(
                [
                    "[[agents]]",
                    f"agent_id = {_quote_toml(str(item.get('agent_id', 'agent')))}",
                    f"role = {_quote_toml(str(item.get('role', item.get('agent_id', 'agent'))))}",
                    f"provider = {_quote_toml(str(item.get('provider', 'codex')))}",
                    f"command = {_quote_toml(str(item.get('command', item.get('provider', 'codex'))))}",
                    f"workspace_mode = {_quote_toml(str(item.get('workspace_mode', 'shared')))}",
                    f"role_prompt = {_quote_toml(str(item.get('role_prompt', '')))}",
                    *(
                        [f"transport = {_quote_toml(str(item['transport']))}"]
                        if "transport" in item
                        else []
                    ),
                    *(
                        [
                            "transport_command = ["
                            + ", ".join(_quote_toml(str(part)) for part in item["transport_command"])
                            + "]"
                        ]
                        if "transport_command" in item and isinstance(item["transport_command"], list)
                        else []
                    ),
                    "",
                ]
            )
    if isinstance(runtime, dict):
        lines.extend(
            [
                "[runtime]",
                f"backend = {_quote_toml(str(runtime.get('backend', 'tmux')))}",
                f"session_name = {_quote_toml(str(runtime.get('session_name', 'agentdeck')))}",
                f"socket_name = {_quote_toml(str(runtime.get('socket_name', 'agentdeck-local')))}",
            ]
        )
    autonomous = raw.get("autonomous", {})
    if isinstance(autonomous, dict) and (autonomous.get("allowed_agents") or autonomous.get("max_approvals")):
        allowed = autonomous.get("allowed_agents", []) or []
        allowed_toml = "[" + ", ".join(_quote_toml(str(a)) for a in allowed) + "]"
        lines.extend([
            "",
            "[autonomous]",
            f"allowed_agents = {allowed_toml}",
            f"max_approvals = {int(autonomous.get('max_approvals', 0))}",
        ])
    skills = raw.get("skills", {})
    if isinstance(skills, dict) and skills.get("allowed_sources"):
        sources = skills.get("allowed_sources", []) or []
        sources_toml = "[" + ", ".join(_quote_toml(str(s)) for s in sources) + "]"
        lines.extend([
            "",
            "[skills]",
            f"allowed_sources = {sources_toml}",
        ])
    return "\n".join(lines) + "\n"
