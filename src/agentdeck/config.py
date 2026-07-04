from __future__ import annotations

from pathlib import Path
import tomllib

from .models import AgentSpec, LeaderConfig, ProjectConfig, RuntimeConfig


CONFIG_DIR = ".agentdeck"
CONFIG_FILE = "config.toml"


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

[[agents]]
agent_id = "coder"
role = "implementation"
provider = "codex"
command = "codex"
workspace_mode = "worktree"

[[agents]]
agent_id = "reviewer"
role = "review"
provider = "claude"
command = "claude"
workspace_mode = "shared"

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


def ensure_project_layout(root: Path | None = None) -> Path:
    base = root or project_root()
    deck_dir = base / CONFIG_DIR
    for child in [
        deck_dir,
        deck_dir / "state",
        deck_dir / "logs" / "agents",
        deck_dir / "artifacts",
        deck_dir / "skills",
    ]:
        child.mkdir(parents=True, exist_ok=True)
    for filename in ["events.jsonl", "approvals.jsonl"]:
        path = deck_dir / "state" / filename
        path.touch(exist_ok=True)
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
    leader = LeaderConfig(
        agent_id=leader_raw.get("agent_id", "leader"),
        provider=leader_raw.get("provider", "deepseek"),
        model=leader_raw.get("model", "deepseek-chat"),
        approval_mode=leader_raw.get("approval_mode", "confirm"),
    )
    runtime = RuntimeConfig(
        backend=runtime_raw.get("backend", "tmux"),
        session_name=runtime_raw.get("session_name", "agentdeck"),
        socket_name=runtime_raw.get("socket_name", f"agentdeck-{base.name}"),
    )
    agents = tuple(
        AgentSpec(
            agent_id=item["agent_id"],
            role=item.get("role", item["agent_id"]),
            provider=item.get("provider", "codex"),
            command=item.get("command", item.get("provider", "codex")),
            workspace_mode=item.get("workspace_mode", "shared"),
        )
        for item in agents_raw
    )
    project_raw = raw.get("project", {})
    return ProjectConfig(
        name=project_raw.get("name", base.name),
        root=str(base),
        leader=leader,
        agents=agents,
        runtime=runtime,
    )
