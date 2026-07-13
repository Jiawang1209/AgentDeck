from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import hmac
import json
from typing import Any


class PreviewBindingError(ValueError):
    """The current preview can no longer authorize its bound action."""


def execution_digest(facts: Mapping[str, object]) -> str:
    if not isinstance(facts, Mapping):
        raise ValueError("execution facts must be canonical JSON")
    try:
        payload = json.dumps(
            dict(facts),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise ValueError("execution facts must be canonical JSON") from None
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise PreviewBindingError(f"preview {field} invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise PreviewBindingError(f"preview {field} invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PreviewBindingError(f"preview {field} invalid")
    return parsed.astimezone(timezone.utc)


def validate_preview_execution(
    binding: Mapping[str, object],
    current_facts: Mapping[str, object],
    *,
    now: datetime,
) -> dict[str, Any]:
    if not isinstance(binding, Mapping):
        raise PreviewBindingError("preview binding invalid")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if binding.get("state") != "pending":
        raise PreviewBindingError("preview is not pending")
    if _timestamp(binding.get("expires_at"), "expires_at") <= now.astimezone(timezone.utc):
        raise PreviewBindingError("preview expired")
    expected = binding.get("execution_digest")
    if not isinstance(expected, str) or len(expected) != 64:
        raise PreviewBindingError("preview execution_digest invalid")
    try:
        actual = execution_digest(current_facts)
    except ValueError:
        raise PreviewBindingError("preview execution facts invalid") from None
    if not hmac.compare_digest(expected, actual):
        raise PreviewBindingError("preview state drift")
    return dict(binding)


def consume_preview_binding(
    binding: Mapping[str, object],
    current_facts: Mapping[str, object],
    *,
    now: datetime,
) -> dict[str, Any]:
    consumed = validate_preview_execution(binding, current_facts, now=now)
    consumed["state"] = "consumed"
    consumed["consumed_at"] = now.astimezone(timezone.utc).isoformat()
    return consumed
