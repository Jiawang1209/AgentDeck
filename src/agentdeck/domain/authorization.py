"""Canonical Mission authorization binding with no side effects."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class AuthorizationEnvelope:
    operations: tuple[str, ...]
    max_attempts: int
    budget_units: int = 1
    allowed_agents: tuple[str, ...] = ()
    ordered_routes: tuple[str, ...] = ()
    metadata: Mapping[str, CanonicalValue] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not (
            _validate_text_tuple(self.operations, allow_empty=False)
            and _bounded_int(
                self.max_attempts,
                minimum=1,
                maximum=MAX_AUTHORIZATION_ATTEMPTS,
            )
            and _bounded_int(
                self.budget_units,
                minimum=1,
                maximum=MAX_AUTHORIZATION_BUDGET_UNITS,
            )
            and _validate_text_tuple(self.allowed_agents, allow_empty=True)
            and _validate_text_tuple(self.ordered_routes, allow_empty=True)
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
            "operations": list(self.operations),
            "max_attempts": self.max_attempts,
            "budget_units": self.budget_units,
            "allowed_agents": list(self.allowed_agents),
            "ordered_routes": list(self.ordered_routes),
            "metadata": _thaw_canonical(cast(CanonicalValue, self.metadata)),
        }


def authorization_digest(
    mission_version: MissionVersion, envelope: AuthorizationEnvelope
) -> str:
    if not isinstance(mission_version, MissionVersion) or not isinstance(
        envelope, AuthorizationEnvelope
    ):
        raise ValueError("authorization digest input invalid")
    canonical = json.dumps(
        {
            "authorization_envelope": envelope.to_dict(),
            "mission_version": mission_version.to_dict(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
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

