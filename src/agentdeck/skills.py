from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import shutil

from .config import CONFIG_DIR


BUILTIN_SKILLS: dict[str, str] = {
    "planning": """---
name: planning
description: Break goals into approval-gated multi-agent plans.
required_tools: leader-plan, approval-list
risk: inspect
---
# Planning

Break a user goal into role-aware steps. Keep every runtime action approval-gated and preserve traceability.
""",
    "debugging": """---
name: debugging
description: Diagnose failures from evidence before proposing fixes.
required_tools: pytest, trace, artifacts
risk: inspect
---
# Debugging

Reproduce the failure, inspect recent changes, form one hypothesis, and verify the fix with focused and broad tests.
""",
    "code-review": """---
name: code-review
description: Review changes for bugs, regressions, risks, and missing tests.
required_tools: git, pytest
risk: inspect
---
# Code Review

Lead with actionable findings grounded in file and line evidence. Keep summaries secondary to risks.
""",
    "verification": """---
name: verification
description: Prove claims with fresh command output before declaring completion.
required_tools: pytest, compileall, git
risk: inspect
---
# Verification

Run the commands that prove the claim, read the output, and record evidence before reporting completion.
""",
}


@dataclass(frozen=True)
class SkillSnapshot:
    name: str
    description: str
    source: str
    path: str | None
    content_hash: str
    required_tools: list[str]
    risk: str
    content: str
    depends_on: tuple[str, ...] = ()

    def summary(self) -> dict[str, object]:
        show_command = f"agentdeck skills show --name {self.name}"
        load_command = f"agentdeck skills load --name {self.name}"
        return {
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "path": self.path,
            "content_hash": self.content_hash,
            "required_tools": self.required_tools,
            "risk": self.risk,
            "show_command": show_command,
            "load_command": load_command,
            "controls": [
                {
                    "kind": "show",
                    "label": "Show skill",
                    "command": show_command,
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "load",
                    "label": "Load skill",
                    "command": load_command,
                    "safety": "explicit_user",
                    "enabled": True,
                    "blocker": None,
                },
            ],
        }

    def detail(self) -> dict[str, object]:
        payload = self.summary()
        payload["content"] = self.content
        return payload

    def load_payload(self) -> dict[str, object]:
        payload = self.summary()
        payload["content_snapshot"] = self.content
        return payload


def discover_skills(root: Path) -> list[SkillSnapshot]:
    skills: dict[str, SkillSnapshot] = {}
    for name, content in BUILTIN_SKILLS.items():
        snapshot = _snapshot_from_content(content, source="builtin", path=None, fallback_name=name)
        skills[snapshot.name] = snapshot
    project_skills_dir = root / CONFIG_DIR / "skills"
    if project_skills_dir.exists():
        for skill_path in sorted(project_skills_dir.glob("*/SKILL.md")):
            snapshot = _snapshot_from_content(
                skill_path.read_text(encoding="utf-8"),
                source="project",
                path=skill_path,
                fallback_name=skill_path.parent.name,
            )
            skills[snapshot.name] = snapshot
    return [skills[name] for name in sorted(skills)]


def resolve_skill_dependencies(root: Path, name: str) -> dict[str, object]:
    snapshots = {snap.name: snap for snap in discover_skills(root)}
    if name not in snapshots:
        raise KeyError(name)
    missing: list[str] = []
    seen_missing: set[str] = set()
    order: list[str] = []          # post-order topo: deps before dependents
    state: dict[str, int] = {}     # 0 = visiting, 1 = done
    stack: list[str] = []
    cycle: list[str] = []

    def visit(node: str) -> bool:  # returns False if a cycle was hit
        if node not in snapshots:
            if node not in seen_missing:
                seen_missing.add(node)
                missing.append(node)
            return True
        if state.get(node) == 1:
            return True
        if state.get(node) == 0:   # back-edge -> cycle
            cycle[:] = stack[stack.index(node):] + [node]
            return False
        state[node] = 0
        stack.append(node)
        for dep in snapshots[node].depends_on:
            if not visit(dep):
                stack.pop()
                return False
        stack.pop()
        state[node] = 1
        order.append(node)
        return True

    has_cycle = not visit(name)
    topo = order if not has_cycle else []
    resolved = sorted(node for node in topo if node != name)
    return {
        "name": name,
        "depends_on": list(snapshots[name].depends_on),
        "resolved": resolved,
        "missing": missing,
        "has_cycle": has_cycle,
        "cycle": list(cycle),
        "order": topo,
    }


def browse_skill_source(source_dir: Path) -> list[SkillSnapshot]:
    """Read-only catalog of a local skill source directory of <name>/SKILL.md."""
    if not source_dir.exists() or not source_dir.is_dir():
        raise FileNotFoundError(str(source_dir))
    snapshots: list[SkillSnapshot] = []
    for skill_path in sorted(source_dir.glob("*/SKILL.md")):
        snapshots.append(_snapshot_from_content(
            skill_path.read_text(encoding="utf-8"),
            source="catalog",
            path=skill_path,
            fallback_name=skill_path.parent.name,
        ))
    return sorted(snapshots, key=lambda snap: snap.name)


def find_skill(root: Path, name: str) -> SkillSnapshot | None:
    for skill in discover_skills(root):
        if skill.name == name:
            return skill
    return None


def preview_project_skill_import(root: Path, source_path: Path) -> tuple[SkillSnapshot, bool, Path]:
    if not source_path.exists() or not source_path.is_file():
        raise FileNotFoundError(str(source_path))
    content = source_path.read_text(encoding="utf-8")
    snapshot = _snapshot_from_content(
        content,
        source="project",
        path=None,
        fallback_name=source_path.parent.name,
    )
    if not re.fullmatch(r"[A-Za-z0-9._-]+", snapshot.name):
        raise ValueError(f"invalid skill name: {snapshot.name}")
    target_dir = root / CONFIG_DIR / "skills" / snapshot.name
    target_path = target_dir / "SKILL.md"
    overwritten = target_path.exists()
    preview = _snapshot_from_content(
        content,
        source="project",
        path=target_path,
        fallback_name=snapshot.name,
    )
    return preview, overwritten, target_path


def import_project_skill(root: Path, source_path: Path, *, force: bool = False) -> tuple[SkillSnapshot, bool]:
    snapshot, overwritten, target_path = preview_project_skill_import(root, source_path)
    if overwritten and not force:
        raise FileExistsError(snapshot.name)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, target_path)
    imported = _snapshot_from_content(
        target_path.read_text(encoding="utf-8"),
        source="project",
        path=target_path,
        fallback_name=snapshot.name,
    )
    return imported, overwritten


def _snapshot_from_content(
    content: str,
    *,
    source: str,
    path: Path | None,
    fallback_name: str,
) -> SkillSnapshot:
    metadata = _frontmatter(content)
    return SkillSnapshot(
        name=str(metadata.get("name") or fallback_name),
        description=str(metadata.get("description") or ""),
        source=source,
        path=str(path) if path is not None else None,
        content_hash="sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest(),
        required_tools=_metadata_list(metadata.get("required_tools")),
        risk=str(metadata.get("risk") or "inspect"),
        content=content,
        depends_on=tuple(_metadata_list(metadata.get("depends_on"))),
    )


def _frontmatter(content: str) -> dict[str, object]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    metadata: dict[str, object] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        value = raw_value.strip()
        if "," in value:
            metadata[key.strip()] = [item.strip() for item in value.split(",") if item.strip()]
        else:
            metadata[key.strip()] = value
    return metadata


def _metadata_list(value: object) -> list[str]:
    if isinstance(value, list):
        items = [str(item) for item in value]
    elif isinstance(value, str) and value:
        items = [value]
    else:
        return []
    cleaned: list[str] = []
    for item in items:
        # Tolerate inline YAML-list syntax (``[a, b]``) and quoted items; the
        # hand-rolled frontmatter parser leaves stray brackets/quotes behind.
        normalized = item.strip().strip("[]").strip().strip("\"'").strip()
        if normalized:
            cleaned.append(normalized)
    return cleaned
