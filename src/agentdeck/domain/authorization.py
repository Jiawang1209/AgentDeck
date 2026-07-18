"""Canonical Mission authorization binding with no side effects."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import cast

from agentdeck.domain.mission import (
    CanonicalValue,
    MissionVersion,
    _freeze_canonical,
    _thaw_canonical,
    _valid_text,
    _validate_text_tuple,
)


MAX_AUTHORIZATION_ATTEMPTS = 33
MAX_AUTHORIZATION_BUDGET_UNITS = 10_000_000
_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _bounded_int(value: object, *, minimum: int, maximum: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= maximum
    )


class ExternalEffectPolicy(str, Enum):
    DENY = "deny"
    REQUIRE_CONFIRMATION = "require_confirmation"
    ALLOW_IN_SCOPE = "allow_in_scope"


@dataclass(frozen=True, slots=True)
class AuthorizationEnvelope:
    goal: str
    semantic_scope: tuple[str, ...]
    path_scope: tuple[str, ...]
    exclusions: tuple[str, ...]
    operations: tuple[str, ...]
    allowed_agents: tuple[str, ...]
    allowed_roles: tuple[str, ...]
    external_effect_policy: ExternalEffectPolicy
    max_attempts: int
    max_retries: int
    max_recoveries: int
    budget_units: int
    acceptance_criteria: tuple[str, ...]
    ordered_routes: tuple[str, ...]
    expires_at: str | None
    metadata: Mapping[str, CanonicalValue] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not (
            _valid_text(self.goal)
            and _validate_text_tuple(self.semantic_scope, allow_empty=False)
            and _validate_text_tuple(self.path_scope, allow_empty=True)
            and _validate_text_tuple(self.exclusions, allow_empty=True)
            and _validate_text_tuple(self.operations, allow_empty=False)
            and _validate_text_tuple(self.allowed_agents, allow_empty=True)
            and _validate_text_tuple(self.allowed_roles, allow_empty=True)
            and isinstance(self.external_effect_policy, ExternalEffectPolicy)
            and _bounded_int(
                self.max_attempts,
                minimum=1,
                maximum=MAX_AUTHORIZATION_ATTEMPTS,
            )
            and _bounded_int(self.max_retries, minimum=0, maximum=32)
            and _bounded_int(self.max_recoveries, minimum=0, maximum=32)
            and _bounded_int(
                self.budget_units,
                minimum=1,
                maximum=MAX_AUTHORIZATION_BUDGET_UNITS,
            )
            and _validate_text_tuple(
                self.acceptance_criteria, allow_empty=False
            )
            and _validate_text_tuple(self.ordered_routes, allow_empty=False)
            and (self.expires_at is None or _valid_text(self.expires_at))
            and isinstance(self.metadata, Mapping)
        ):
            raise ValueError("authorization envelope invalid")
        try:
            frozen = _freeze_canonical(self.metadata)
        except ValueError:
            raise ValueError("authorization envelope invalid") from None
        if not isinstance(frozen, Mapping):
            raise ValueError("authorization envelope invalid")
        object.__setattr__(self, "metadata", cast(Mapping[str, CanonicalValue], frozen))

    def to_dict(self) -> dict[str, object]:
        return {
            "goal": self.goal,
            "semantic_scope": list(self.semantic_scope),
            "path_scope": list(self.path_scope),
            "exclusions": list(self.exclusions),
            "operations": list(self.operations),
            "allowed_agents": list(self.allowed_agents),
            "allowed_roles": list(self.allowed_roles),
            "external_effect_policy": self.external_effect_policy.value,
            "max_attempts": self.max_attempts,
            "max_retries": self.max_retries,
            "max_recoveries": self.max_recoveries,
            "budget_units": self.budget_units,
            "acceptance_criteria": list(self.acceptance_criteria),
            "ordered_routes": list(self.ordered_routes),
            "expires_at": self.expires_at,
            "metadata": _thaw_canonical(cast(CanonicalValue, self.metadata)),
        }


def authorization_digest(
    mission_version: MissionVersion, envelope: AuthorizationEnvelope
) -> str:
    if not isinstance(mission_version, MissionVersion) or not isinstance(
        envelope, AuthorizationEnvelope
    ):
        raise ValueError("authorization digest input invalid")
    try:
        canonical = json.dumps(
            {
                "authorization_envelope": envelope.to_dict(),
                "mission_version": mission_version.to_dict(),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, OverflowError):
        raise ValueError("authorization digest input invalid") from None
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class ConfirmedMissionVersion:
    mission_version: MissionVersion
    authorization_envelope: AuthorizationEnvelope
    authorization_digest: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.mission_version, MissionVersion)
            or not isinstance(self.authorization_envelope, AuthorizationEnvelope)
            or not isinstance(self.authorization_digest, str)
            or _DIGEST_PATTERN.fullmatch(self.authorization_digest) is None
            or self.authorization_digest
            != authorization_digest(self.mission_version, self.authorization_envelope)
        ):
            raise ValueError("authorization digest mismatch")

    def confirm(self, digest: str) -> ConfirmedMissionVersion:
        if (
            not isinstance(digest, str)
            or _DIGEST_PATTERN.fullmatch(digest) is None
            or digest != self.authorization_digest
        ):
            raise ValueError("authorization digest mismatch")
        return self

    def to_dict(self) -> dict[str, object]:
        return {
            "mission_version": self.mission_version.to_dict(),
            "authorization_envelope": self.authorization_envelope.to_dict(),
            "authorization_digest": self.authorization_digest,
        }
