from __future__ import annotations

from pathlib import Path
import tomllib

from .models import AgentSpec, AutonomousPolicy, LeaderConfig, ProjectConfig, RuntimeConfig


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
            role_prompt=item.get("role_prompt", ""),
        )
        for item in agents_raw
    )
    autonomous_raw = raw.get("autonomous", {})
    allowed = autonomous_raw.get("allowed_agents", []) if isinstance(autonomous_raw, dict) else []
    autonomous = AutonomousPolicy(
        allowed_agents=tuple(str(a) for a in allowed),
        max_approvals=int(autonomous_raw.get("max_approvals", 0)) if isinstance(autonomous_raw, dict) else 0,
    )
    project_raw = raw.get("project", {})
    return ProjectConfig(
        name=project_raw.get("name", base.name),
        root=str(base),
        leader=leader,
        agents=agents,
        runtime=runtime,
        autonomous=autonomous,
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
            updated = AgentSpec(
                agent_id=item["agent_id"],
                role=role,
                provider=item.get("provider", "codex"),
                command=item.get("command", item.get("provider", "codex")),
                workspace_mode=item.get("workspace_mode", "shared"),
                role_prompt=role_prompt,
            )
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
    return "\n".join(lines) + "\n"
