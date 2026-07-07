from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

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

    def summary(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "path": self.path,
            "content_hash": self.content_hash,
            "required_tools": self.required_tools,
            "risk": self.risk,
            "show_command": f"agentdeck skills show --name {self.name}",
            "load_command": f"agentdeck skills load --name {self.name}",
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


def find_skill(root: Path, name: str) -> SkillSnapshot | None:
    for skill in discover_skills(root):
        if skill.name == name:
            return skill
    return None


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
        return [str(item) for item in value]
    if isinstance(value, str) and value:
        return [value]
    return []
