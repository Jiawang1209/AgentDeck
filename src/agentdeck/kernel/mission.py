"""Pure, canonical Mission proposal, preview, and confirmation types."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from hashlib import sha256
from hmac import compare_digest
import json

from agentdeck.kernel.agents import AgentRole
from agentdeck.kernel.permissions import Effect, PermissionProfile


class PreviewDriftError(ValueError):
    """Raised when confirmation does not name the exact preview shown to a human."""



_SQLITE_MAX_INTEGER = 2**63 - 1
_LOWER_HEX = frozenset("0123456789abcdef")


def _require_string(value: object, field: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field} must be a string")
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError as error:
        raise ValueError(f"{field} must be valid UTF-8") from error
    if not value.strip():
        raise ValueError(f"{field} must not be empty")
    return value


def _positive_sqlite_integer(value: object, field: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field} must be an int")
    if not 1 <= value <= _SQLITE_MAX_INTEGER:
        raise ValueError(f"{field} must be a positive SQLite-safe integer")
    return value


def _derived_id_bytes(value: object, field: str, prefix: str) -> bytes:
    text = _require_string(value, field)
    suffix = text.removeprefix(prefix)
    if len(suffix) != 24 or text != prefix + suffix or any(char not in _LOWER_HEX for char in suffix):
        raise ValueError(f"{field} must be {prefix} plus 24 lowercase hex characters")
    return text.encode("ascii")


def _content_hash_bytes(value: object) -> bytes:
    text = _require_string(value, "content_hash")
    if len(text) != 64 or any(char not in _LOWER_HEX for char in text):
        raise ValueError("content_hash must be exactly 64 lowercase hex characters")
    return text.encode("ascii")


def _copy_strings(value: object, field: str, *, nonempty: bool = True) -> tuple[str, ...]:
    if type(value) not in {tuple, list}:
        raise TypeError(f"{field} must be a tuple or list")
    copied = tuple(_require_string(item, f"{field} items") for item in value)
    if nonempty and not copied:
        raise ValueError(f"{field} must not be empty")
    if len(set(copied)) != len(copied):
        raise ValueError(f"{field} must not contain duplicates")
    return copied


def _copy_effects(value: object) -> frozenset[Effect]:
    if type(value) not in {frozenset, set, tuple, list}:
        raise TypeError("allowed_effects must be a collection")
    copied = frozenset(value)
    if not copied:
        raise ValueError("allowed_effects must not be empty")
    if any(type(effect) is not Effect for effect in copied):
        raise TypeError("allowed_effects items must be Effect values")
    return copied


def _permission_profile(value: object) -> PermissionProfile:
    if type(value) is PermissionProfile:
        return value
    if type(value) is str:
        try:
            return PermissionProfile(value)
        except ValueError as error:
            raise ValueError("permission_profile must be an exact profile value") from error
    raise TypeError("permission_profile must be a PermissionProfile or exact profile string")


def _copy_budgets(value: object) -> tuple[tuple[str, int], ...]:
    if isinstance(value, Mapping):
        raw = dict(value)
    elif type(value) is tuple:
        raw = dict(value)
    else:
        raise TypeError("budgets must be a mapping")
    required = _BUDGET_NAMES
    if set(raw) != required:
        raise ValueError("budgets must declare exactly the mission budget keys")
    copied: dict[str, int] = {}
    for name in required:
        amount = raw[name]
        copied[name] = _positive_sqlite_integer(amount, f"budgets.{name}")
    return tuple(sorted(copied.items()))


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class TaskDefinition:
    task_id: str
    name: str
    role: AgentRole
    backend: str
    agent_instance_id: str
    acp_route: str
    dependencies: tuple[str, ...]
    allowed_effects: frozenset[Effect]
    expected_outputs: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_string(self.task_id, "task_id")
        _require_string(self.name, "name")
        if type(self.role) is not AgentRole:
            raise TypeError("role must be an AgentRole")
        _require_string(self.backend, "backend")
        _require_string(self.agent_instance_id, "agent_instance_id")
        _require_string(self.acp_route, "acp_route")
        dependencies = _copy_strings(self.dependencies, "dependencies", nonempty=False)
        if self.task_id in dependencies:
            raise ValueError("task must not depend on itself")
        object.__setattr__(self, "dependencies", dependencies)
        object.__setattr__(self, "allowed_effects", _copy_effects(self.allowed_effects))
        object.__setattr__(self, "expected_outputs", _copy_strings(self.expected_outputs, "expected_outputs"))
        object.__setattr__(
            self,
            "acceptance_criteria",
            _copy_strings(self.acceptance_criteria, "acceptance_criteria"),
        )

    def canonical_projection(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "role": self.role.value,
            "backend": self.backend,
            "agent_instance_id": self.agent_instance_id,
            "acp_route": self.acp_route,
            "dependencies": list(self.dependencies),
            "allowed_effects": sorted(effect.value for effect in self.allowed_effects),
            "expected_outputs": list(self.expected_outputs),
            "acceptance_criteria": list(self.acceptance_criteria),
        }


_TASK_NAMES = ("implementation", "review", "revision", "acceptance")
_TASK_ROLES = (
    AgentRole.IMPLEMENTER,
    AgentRole.REVIEWER,
    AgentRole.REVISER,
    AgentRole.ACCEPTANCE_REVIEWER,
)
_TASK_BACKENDS = ("codex-cli", "claude-cli", "codex-cli", "claude-cli")
_BUDGET_NAMES = {
    "max_leader_schema_repairs",
    "max_attempts",
    "max_acp_reconnects",
    "max_revision_cycles",
    "max_final_acceptance_attempts",
}
_MISSION_FIELDS = {
    "draft_id", "objective", "scope", "project_root", "leader_backend",
    "leader_adapter", "leader_model", "leader_version", "permission_profile",
    "tasks", "acceptance_criteria", "non_goals", "risks", "budgets", "version",
}
_TASK_FIELDS = {
    "task_id", "name", "role", "backend", "agent_instance_id", "acp_route",
    "dependencies", "allowed_effects", "expected_outputs", "acceptance_criteria",
}


@dataclass(frozen=True)
class MissionDraft:
    draft_id: str
    objective: str
    project_root: str
    leader_backend: str
    leader_model: str
    permission_profile: PermissionProfile
    tasks: tuple[TaskDefinition, ...]
    acceptance_criteria: tuple[str, ...]
    non_goals: tuple[str, ...]
    risks: tuple[str, ...]
    budgets: tuple[tuple[str, int], ...]
    scope: str = "project"
    leader_adapter: str = "acp"
    leader_version: str = "unreported"

    def __post_init__(self) -> None:
        for field in (
            "draft_id", "objective", "scope", "project_root", "leader_backend",
            "leader_adapter", "leader_model", "leader_version",
        ):
            _require_string(getattr(self, field), field)
        object.__setattr__(self, "permission_profile", _permission_profile(self.permission_profile))
        if type(self.tasks) not in {tuple, list}:
            raise TypeError("tasks must be a tuple or list")
        tasks = tuple(self.tasks)
        if any(type(task) is not TaskDefinition for task in tasks):
            raise TypeError("tasks must contain TaskDefinition values")
        object.__setattr__(self, "tasks", tasks)
        object.__setattr__(self, "acceptance_criteria", _copy_strings(self.acceptance_criteria, "acceptance_criteria"))
        object.__setattr__(self, "non_goals", _copy_strings(self.non_goals, "non_goals"))
        object.__setattr__(self, "risks", _copy_strings(self.risks, "risks"))
        object.__setattr__(self, "budgets", _copy_budgets(self.budgets))
        self._validate_task_graph()

    @classmethod
    def coding_default(
        cls,
        draft_id: str,
        objective: str,
        project_root: str,
        leader_backend: str,
        leader_model: str,
        permission_profile: PermissionProfile | str,
        *,
        scope: str = "project",
        leader_adapter: str = "acp",
        leader_version: str = "unreported",
    ) -> "MissionDraft":
        profile = _permission_profile(permission_profile)
        specifications = (
            ("tsk_implementation", "implementation", AgentRole.IMPLEMENTER, "codex-cli", "agt_implementation", "acp://codex-cli/implementation", (), {Effect.READ, Effect.WRITE_PROJECT, Effect.COMMAND_PROJECT}, ("implementation_patch", "implementation_evidence"), ("implementation satisfies the approved scope",)),
            ("tsk_review", "review", AgentRole.REVIEWER, "claude-cli", "agt_review", "acp://claude-cli/review", ("tsk_implementation",), {Effect.READ}, ("review_findings",), ("findings cite project evidence",)),
            ("tsk_revision", "revision", AgentRole.REVISER, "codex-cli", "agt_revision", "acp://codex-cli/revision", ("tsk_review",), {Effect.READ, Effect.WRITE_PROJECT, Effect.COMMAND_PROJECT}, ("revision_patch", "revision_evidence"), ("accepted findings are resolved",)),
            ("tsk_acceptance", "acceptance", AgentRole.ACCEPTANCE_REVIEWER, "claude-cli", "agt_acceptance", "acp://claude-cli/acceptance", ("tsk_revision",), {Effect.READ, Effect.COMMAND_PROJECT}, ("acceptance_result",), ("every acceptance criterion is mapped to evidence",)),
        )
        tasks = tuple(TaskDefinition(*specification) for specification in specifications)
        return cls(
            draft_id=draft_id,
            objective=objective,
            project_root=project_root,
            leader_backend=leader_backend,
            leader_model=leader_model,
            permission_profile=profile,
            tasks=tasks,
            acceptance_criteria=("the objective is complete with evidence",),
            non_goals=("no automatic publication or external delivery",),
            risks=("permissions, project drift, or unknown outcomes stop automatic progress",),
            budgets={
                "max_leader_schema_repairs": 1,
                "max_attempts": 2,
                "max_acp_reconnects": 1,
                "max_revision_cycles": 1,
                "max_final_acceptance_attempts": 1,
            },
            scope=scope,
            leader_adapter=leader_adapter,
            leader_version=leader_version,
        )

    @property
    def max_attempts(self) -> int:
        return dict(self.budgets)["max_attempts"]

    @property
    def max_revision_cycles(self) -> int:
        return dict(self.budgets)["max_revision_cycles"]

    @property
    def max_acp_reconnects(self) -> int:
        return dict(self.budgets)["max_acp_reconnects"]

    @property
    def max_leader_schema_repairs(self) -> int:
        return dict(self.budgets)["max_leader_schema_repairs"]

    @property
    def max_final_acceptance_attempts(self) -> int:
        return dict(self.budgets)["max_final_acceptance_attempts"]

    def _validate_task_graph(self) -> None:
        if len(self.tasks) != 4:
            raise ValueError("mission must contain exactly four tasks")
        if tuple(task.name for task in self.tasks) != _TASK_NAMES:
            raise ValueError("mission task names must use the declared four-stage order")
        if tuple(task.role for task in self.tasks) != _TASK_ROLES:
            raise ValueError("mission task roles must use the declared four-stage order")
        if tuple(task.backend for task in self.tasks) != _TASK_BACKENDS:
            raise ValueError("mission task backends must use the declared four-stage order")
        task_ids = tuple(task.task_id for task in self.tasks)
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("task_id values must be unique")
        for field in ("agent_instance_id", "acp_route"):
            values = tuple(getattr(task, field) for task in self.tasks)
            if len(set(values)) != len(values):
                raise ValueError(f"{field} values must be unique")
        expected_dependencies = (
            (), (task_ids[0],), (task_ids[1],), (task_ids[2],),
        )
        if tuple(task.dependencies for task in self.tasks) != expected_dependencies:
            raise ValueError("mission task dependencies must be the declared chain")
        for task in self.tasks:
            if task.acp_route != f"acp://{task.backend}/{task.name}":
                raise ValueError("task ACP route must bind its backend and task name")

    def _canonical_projection(self, version: int) -> dict[str, object]:
        return {
            "draft_id": self.draft_id,
            "objective": self.objective,
            "scope": self.scope,
            "project_root": self.project_root,
            "leader_backend": self.leader_backend,
            "leader_adapter": self.leader_adapter,
            "leader_model": self.leader_model,
            "leader_version": self.leader_version,
            "permission_profile": self.permission_profile.value,
            "tasks": [task.canonical_projection() for task in self.tasks],
            "acceptance_criteria": list(self.acceptance_criteria),
            "non_goals": list(self.non_goals),
            "risks": list(self.risks),
            "budgets": dict(self.budgets),
            "version": version,
        }

    def preview(self, version: int) -> "MissionPreview":
        version = _positive_sqlite_integer(version, "version")
        canonical_content = _canonical_json(self._canonical_projection(version))
        content_hash = sha256(canonical_content.encode("utf-8")).hexdigest()
        return MissionPreview(f"prv_{content_hash[:24]}", version, content_hash, canonical_content)

    def revise(self, **changes: object) -> "MissionDraft":
        allowed = {
            "objective", "scope", "project_root", "leader_backend", "leader_adapter", "leader_model", "leader_version",
            "permission_profile", "tasks", "acceptance_criteria", "non_goals", "risks", "budgets",
        }
        if not changes:
            raise ValueError("revision requires a semantic change")
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported mission revision fields: {sorted(unknown)}")
        revised = replace(self, **changes)
        if revised == self:
            raise ValueError("revision requires a semantic change")
        return revised


def _canonical_mission_draft(content: str, version: int) -> MissionDraft:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError("canonical_content must be JSON") from error
    if type(payload) is not dict or set(payload) != _MISSION_FIELDS:
        raise ValueError("canonical_content must be an exact mission object")
    payload_version = _positive_sqlite_integer(payload["version"], "canonical_content version")
    if payload_version != version:
        raise ValueError("canonical_content version must match wrapper version")
    if type(payload["tasks"]) is not list:
        raise ValueError("canonical_content tasks must be a list")
    tasks: list[TaskDefinition] = []
    for raw_task in payload["tasks"]:
        if type(raw_task) is not dict or set(raw_task) != _TASK_FIELDS:
            raise ValueError("canonical_content task must have exact fields")
        for field in (
            "task_id", "name", "role", "backend", "agent_instance_id", "acp_route",
        ):
            _require_string(raw_task[field], f"task.{field}")
        for field in ("dependencies", "allowed_effects", "expected_outputs", "acceptance_criteria"):
            if type(raw_task[field]) is not list:
                raise ValueError(f"task.{field} must be a JSON list")
        try:
            role = AgentRole(raw_task["role"])
            effects = frozenset(Effect(item) for item in raw_task["allowed_effects"])
        except ValueError as error:
            raise ValueError("canonical_content task uses undeclared role or effect") from error
        tasks.append(TaskDefinition(
            raw_task["task_id"], raw_task["name"], role, raw_task["backend"],
            raw_task["agent_instance_id"], raw_task["acp_route"], raw_task["dependencies"],
            effects, raw_task["expected_outputs"], raw_task["acceptance_criteria"],
        ))
    for field in (
        "draft_id", "objective", "scope", "project_root", "leader_backend",
        "leader_adapter", "leader_model", "leader_version", "permission_profile",
    ):
        _require_string(payload[field], field)
    for field in ("acceptance_criteria", "non_goals", "risks"):
        if type(payload[field]) is not list:
            raise ValueError(f"{field} must be a JSON list")
    if type(payload["budgets"]) is not dict:
        raise ValueError("budgets must be a JSON object")
    draft = MissionDraft(
        payload["draft_id"], payload["objective"], payload["project_root"],
        payload["leader_backend"], payload["leader_model"], payload["permission_profile"],
        tasks, payload["acceptance_criteria"], payload["non_goals"], payload["risks"],
        payload["budgets"], payload["scope"], payload["leader_adapter"], payload["leader_version"],
    )
    if _canonical_json(draft._canonical_projection(version)) != content:
        raise ValueError("canonical_content must exactly match mission projection")
    return draft


def _validate_wrapper(
    identity: object,
    identity_field: str,
    prefix: str,
    version: object,
    content_hash: object,
    canonical_content: object,
) -> None:
    identity_bytes = _derived_id_bytes(identity, identity_field, prefix)
    checked_version = _positive_sqlite_integer(version, "version")
    hash_bytes = _content_hash_bytes(content_hash)
    content = _require_string(canonical_content, "canonical_content")
    _canonical_mission_draft(content, checked_version)
    expected_hash = sha256(content.encode("utf-8", "strict")).hexdigest().encode("ascii")
    if not compare_digest(hash_bytes, expected_hash):
        raise ValueError("content_hash must match canonical_content")
    if not compare_digest(identity_bytes, prefix.encode("ascii") + expected_hash[:24]):
        raise ValueError(f"{identity_field} must match canonical_content")


@dataclass(frozen=True)
class MissionPreview:
    preview_id: str
    version: int
    content_hash: str
    canonical_content: str

    def __post_init__(self) -> None:
        _validate_wrapper(
            self.preview_id, "preview_id", "prv_", self.version,
            self.content_hash, self.canonical_content,
        )

    def confirm(self, *, preview_id: str, content_hash: str) -> "ConfirmedMissionVersion":
        if type(preview_id) is not str or type(content_hash) is not str:
            raise TypeError("preview confirmation values must be strings")
        try:
            preview_id_bytes = _derived_id_bytes(preview_id, "preview_id", "prv_")
            content_hash_bytes = _content_hash_bytes(content_hash)
        except ValueError as error:
            raise PreviewDriftError(
                "preview confirmation does not match current canonical content"
            ) from error
        if not (
            compare_digest(preview_id_bytes, self.preview_id.encode("ascii"))
            and compare_digest(content_hash_bytes, self.content_hash.encode("ascii"))
        ):
            raise PreviewDriftError("preview confirmation does not match current canonical content")
        return ConfirmedMissionVersion(
            mission_id=f"msn_{self.content_hash[:24]}",
            version=self.version,
            content_hash=self.content_hash,
            canonical_content=self.canonical_content,
        )


@dataclass(frozen=True)
class ConfirmedMissionVersion:
    mission_id: str
    version: int
    content_hash: str
    canonical_content: str

    def __post_init__(self) -> None:
        _validate_wrapper(
            self.mission_id, "mission_id", "msn_", self.version,
            self.content_hash, self.canonical_content,
        )
