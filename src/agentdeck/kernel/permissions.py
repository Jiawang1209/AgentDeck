from dataclasses import dataclass
from enum import StrEnum


class PermissionProfile(StrEnum):
    ASK_FOR_APPROVAL = "ask_for_approval"
    APPROVE_FOR_ME = "approve_for_me"
    FULL_ACCESS = "full_access"


class Effect(StrEnum):
    READ = "read"
    WRITE_PROJECT = "write_project"
    COMMAND_PROJECT = "command_project"
    NETWORK = "network"
    WRITE_EXTERNAL = "write_external"
    CREDENTIAL = "credential"
    DESTRUCTIVE = "destructive"
    PUBLISH = "publish"


class PermissionError(ValueError):
    """Raised when a permission scope attempts to broaden its authority."""


def _require_effect(value: object, field: str = "effect") -> Effect:
    if type(value) is not Effect:
        raise TypeError(f"{field} must be an Effect")
    return value


def _require_nonempty_actor(value: object) -> str:
    if type(value) is not str:
        raise TypeError("actor must be a string")
    if not value.strip():
        raise ValueError("actor must not be empty")
    return value


@dataclass(frozen=True)
class PermissionDecision:
    effect: Effect
    actor: str
    allowed: bool
    requires_human: bool
    requires_independent_reviewer: bool
    auditable: bool
    reason: str

    def __post_init__(self) -> None:
        _require_effect(self.effect)
        _require_nonempty_actor(self.actor)
        for field, value in (
            ("allowed", self.allowed),
            ("requires_human", self.requires_human),
            ("requires_independent_reviewer", self.requires_independent_reviewer),
            ("auditable", self.auditable),
        ):
            if type(value) is not bool:
                raise TypeError(f"{field} must be a bool")
        if not self.auditable:
            raise ValueError("permission decisions must be auditable")
        if type(self.reason) is not str:
            raise TypeError("reason must be a string")
        if not self.reason.strip():
            raise ValueError("reason must not be empty")
        if self.requires_human and self.requires_independent_reviewer:
            raise ValueError("review requirements are mutually exclusive")
        if self.allowed and (
            self.requires_human or self.requires_independent_reviewer
        ):
            raise ValueError("allowed decisions cannot require review")


@dataclass(frozen=True)
class PermissionScope:
    profile: PermissionProfile
    effects: frozenset[Effect]

    def __post_init__(self) -> None:
        if type(self.profile) is not PermissionProfile:
            raise TypeError("profile must be a PermissionProfile")
        if type(self.effects) is not frozenset:
            raise TypeError("effects must be a frozenset")
        for effect in self.effects:
            _require_effect(effect, "effects items")

    @classmethod
    def for_profile(
        cls, profile: PermissionProfile = PermissionProfile.APPROVE_FOR_ME
    ) -> "PermissionScope":
        if type(profile) is not PermissionProfile:
            raise TypeError("profile must be a PermissionProfile")
        return cls(profile=profile, effects=frozenset(Effect))

    def narrow(self, effects: set[Effect] | frozenset[Effect]) -> "PermissionScope":
        if type(effects) not in {set, frozenset}:
            raise TypeError("effects must be a set or frozenset")
        narrowed = frozenset(effects)
        for effect in narrowed:
            _require_effect(effect, "effects items")
        if not narrowed.issubset(self.effects):
            raise PermissionError("cannot expand permission scope")
        return PermissionScope(profile=self.profile, effects=narrowed)

    def allows(self, effect: Effect) -> bool:
        return _require_effect(effect) in self.effects

    def decide(self, effect: Effect, *, actor: str) -> PermissionDecision:
        effect = _require_effect(effect)
        actor = _require_nonempty_actor(actor)
        if effect not in self.effects:
            return PermissionDecision(
                effect=effect,
                actor=actor,
                allowed=False,
                requires_human=False,
                requires_independent_reviewer=False,
                auditable=True,
                reason="outside_scope",
            )
        if self.profile is PermissionProfile.ASK_FOR_APPROVAL:
            return _decision_for_ask_for_approval(effect, actor)
        if self.profile is PermissionProfile.APPROVE_FOR_ME:
            return _decision_for_approve_for_me(effect, actor)
        if self.profile is PermissionProfile.FULL_ACCESS:
            return PermissionDecision(
                effect=effect,
                actor=actor,
                allowed=True,
                requires_human=False,
                requires_independent_reviewer=False,
                auditable=True,
                reason="full_access",
            )
        raise PermissionError("unsupported permission profile")


def _decision_for_ask_for_approval(effect: Effect, actor: str) -> PermissionDecision:
    if effect is Effect.READ:
        return PermissionDecision(
            effect=effect,
            actor=actor,
            allowed=True,
            requires_human=False,
            requires_independent_reviewer=False,
            auditable=True,
            reason="read_allowed",
        )
    return PermissionDecision(
        effect=effect,
        actor=actor,
        allowed=False,
        requires_human=True,
        requires_independent_reviewer=False,
        auditable=True,
        reason="human_approval_required",
    )


def _decision_for_approve_for_me(
    effect: Effect, actor: str
) -> PermissionDecision:
    if effect in {Effect.READ, Effect.WRITE_PROJECT, Effect.COMMAND_PROJECT}:
        return PermissionDecision(
            effect=effect,
            actor=actor,
            allowed=True,
            requires_human=False,
            requires_independent_reviewer=False,
            auditable=True,
            reason="routine_project_effect",
        )
    if effect is Effect.NETWORK:
        return PermissionDecision(
            effect=effect,
            actor=actor,
            allowed=False,
            requires_human=False,
            requires_independent_reviewer=True,
            auditable=True,
            reason="independent_reviewer_required",
        )
    return PermissionDecision(
        effect=effect,
        actor=actor,
        allowed=False,
        requires_human=True,
        requires_independent_reviewer=False,
        auditable=True,
        reason="human_approval_required",
    )
