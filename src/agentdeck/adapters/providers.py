"""Bounded OpenAI-compatible API implementation of the strict Leader Port."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import json
from math import isfinite
import socket
from time import monotonic
from typing import Final
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from agentdeck.ports.leader import (
    LeaderFailure,
    LeaderFailureCode,
    LeaderProposal,
    LeaderRequest,
    ProposalError,
    leader_proposal_json_schema,
)


CredentialResolver = Callable[[str], object]
_ADAPTER_ID: Final = "openai-compatible"
_VERSION: Final = "v1"
_DEFAULT_TIMEOUT_SECONDS: Final = 30.0
_DEFAULT_MAX_RESPONSE_BYTES: Final = 1_048_576
_MAX_TIMEOUT_SECONDS: Final = 120.0
_MAX_RESPONSE_BYTES: Final = 8_388_608
_MAX_REQUEST_BYTES: Final = 1_048_576
_READ_CHUNK_BYTES: Final = 65_536
_MAX_TEXT_BYTES: Final = 4096
_MAX_CREDENTIAL_BYTES: Final = 65_536
_PRESETS: Final = {
    "deepseek": ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    "kimi": ("https://api.moonshot.cn/v1", "MOONSHOT_API_KEY"),
    "glm": ("https://open.bigmodel.cn/api/paas/v4", "ZHIPUAI_API_KEY"),
}


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


_HTTP_OPENER: Final = build_opener(_RejectRedirects())


def urlopen(request: Request, *, timeout: float):
    """Open one exact endpoint without urllib's credential-forwarding redirects."""

    return _HTTP_OPENER.open(request, timeout=timeout)


class LeaderUnavailable(ValueError):
    """Raised when an exact API Leader identity cannot be constructed."""


def _text(value: object, diagnostic: str) -> str:
    if type(value) is not str:
        raise LeaderUnavailable(diagnostic)
    failed = False
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        failed = True
        encoded = b""
    if (
        failed
        or not value.strip()
        or len(encoded) > _MAX_TEXT_BYTES
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise LeaderUnavailable(diagnostic) from None
    return value


def _base_url(value: object) -> str:
    text = _text(value, "Custom provider requires an explicit base URL")
    parsed = None
    failed = False
    try:
        parsed = urlsplit(text)
        _ = parsed.port
    except ValueError:
        failed = True
    if (
        failed
        or parsed is None
        or parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(character.isspace() or ord(character) < 32 for character in text)
    ):
        raise LeaderUnavailable("provider requires an exact HTTP base URL") from None
    return text.rstrip("/")


def _positive_timeout(value: object) -> float:
    if type(value) not in {int, float}:
        raise LeaderUnavailable("provider requires a positive timeout")
    checked = float(value)
    if not isfinite(checked) or not 0 < checked <= _MAX_TIMEOUT_SECONDS:
        raise LeaderUnavailable("provider requires a positive timeout")
    return checked


def _positive_response_bound(value: object) -> int:
    if type(value) is not int or not 0 < value <= _MAX_RESPONSE_BYTES:
        raise LeaderUnavailable("provider requires a positive response bound")
    return value


@dataclass(frozen=True, init=False)
class OpenAICompatibleLeader:
    """Exact-provider, exact-model OpenAI-compatible Leader adapter."""

    provider: str
    base_url: str
    model: str
    credential_source: str
    timeout_seconds: float
    max_response_bytes: int
    _credential_resolver: CredentialResolver = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str,
        credential_source: str | None = None,
        credential_resolver: CredentialResolver | None = None,
        provider: str = "custom",
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        checked_provider = _text(provider, "provider must be an exact supported provider")
        if checked_provider not in {*_PRESETS, "custom"}:
            raise LeaderUnavailable("provider must be an exact supported provider")
        checked_model = _text(model, "provider requires an exact model")
        if checked_provider == "custom":
            checked_url = _base_url(base_url)
            checked_source = _text(
                credential_source,
                "Custom provider requires an explicit credential source",
            )
        else:
            preset_url, preset_source = _PRESETS[checked_provider]
            if base_url is not None:
                raise LeaderUnavailable("provider preset base URL cannot be overridden")
            if credential_source is not None:
                raise LeaderUnavailable("provider preset credential source cannot be overridden")
            checked_url, checked_source = preset_url, preset_source
        if not callable(credential_resolver):
            raise LeaderUnavailable("provider requires a credential resolver")
        object.__setattr__(self, "provider", checked_provider)
        object.__setattr__(self, "base_url", checked_url)
        object.__setattr__(self, "model", checked_model)
        object.__setattr__(self, "credential_source", checked_source)
        object.__setattr__(self, "timeout_seconds", _positive_timeout(timeout_seconds))
        object.__setattr__(
            self, "max_response_bytes", _positive_response_bound(max_response_bytes)
        )
        object.__setattr__(self, "_credential_resolver", credential_resolver)

    def propose_mission(self, request: LeaderRequest) -> LeaderProposal:
        if type(request) is not LeaderRequest:
            raise TypeError("request must be a LeaderRequest")
        expected = (f"api:{self.provider}", _ADAPTER_ID, self.model, _VERSION)
        resolved = request.resolved_model
        actual = (
            resolved.backend_id,
            resolved.adapter_id,
            resolved.model_id,
            resolved.version,
        )
        if actual != expected:
            raise LeaderUnavailable("request does not match the frozen resolved Leader identity")
        body = self._request_body(request)
        if len(body) > _MAX_REQUEST_BYTES:
            raise LeaderFailure(LeaderFailureCode.OVERSIZE)
        credential, credential_failure = self._resolve_credential()
        if credential_failure is not None:
            raise LeaderFailure(credential_failure)
        raw, failure = self._post(body, credential)
        credential = ""
        if failure is not None:
            raise LeaderFailure(failure)
        proposal, failure = self._decode(raw, request)
        raw = b""
        if failure is not None:
            raise LeaderFailure(failure)
        assert proposal is not None
        return proposal

    def _resolve_credential(self) -> tuple[str, LeaderFailureCode | None]:
        failed = False
        try:
            credential = self._credential_resolver(self.credential_source)
        except Exception:
            failed = True
            credential = None
        if failed or type(credential) is not str:
            return "", LeaderFailureCode.AUTHENTICATION
        invalid = False
        try:
            encoded = credential.encode("utf-8", "strict")
        except UnicodeEncodeError:
            invalid = True
            encoded = b""
        if (
            invalid
            or not credential.strip()
            or len(encoded) > _MAX_CREDENTIAL_BYTES
            or any(character.isspace() or ord(character) == 127 for character in credential)
        ):
            return "", LeaderFailureCode.AUTHENTICATION
        return credential, None

    def _request_body(self, request: LeaderRequest) -> bytes:
        context = {
            "user_goal": request.user_goal,
            "project_context": {
                "project_root": request.project_context.project_root,
                "summary": request.project_context.summary,
            },
            "available_agents": [
                {
                    "instance_id": agent.instance_id,
                    "role": agent.role.value,
                    "backend_id": agent.backend_id,
                    "acp_route_id": agent.acp_route_id,
                }
                for agent in request.available_agents
            ],
            "permission_ceiling": request.permission_ceiling.value,
            "resolved_leader": {
                "provider": self.provider,
                "backend_id": request.resolved_model.backend_id,
                "adapter_id": request.resolved_model.adapter_id,
                "model_id": request.resolved_model.model_id,
                "version": request.resolved_model.version,
            },
            "schema_repair": request.schema_repair is not None,
        }
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Propose one AgentDeck Mission. Return only JSON matching the exact "
                        "closed schema; never execute or dispatch work."
                    ),
                },
                {"role": "user", "content": json.dumps(context, separators=(",", ":"))},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "agentdeck_mission",
                    "strict": True,
                    "schema": leader_proposal_json_schema(),
                },
            },
            "stream": False,
        }
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    def _post(
        self, body: bytes, credential: str
    ) -> tuple[bytes, LeaderFailureCode | None]:
        deadline = monotonic() + self.timeout_seconds
        raw = b""
        failure: LeaderFailureCode | None = None
        status = None
        response = None
        try:
            request = Request(
                f"{self.base_url}/chat/completions",
                data=body,
                headers={
                    "Authorization": f"Bearer {credential}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                method="POST",
            )
            remaining = deadline - monotonic()
            if remaining <= 0:
                failure = LeaderFailureCode.TIMEOUT
            else:
                response = urlopen(request, timeout=remaining)
                status = response.status
                if monotonic() >= deadline:
                    failure = LeaderFailureCode.TIMEOUT
                elif type(status) is not int:
                    failure = LeaderFailureCode.TRANSPORT
                elif 200 <= status < 300:
                    raw, failure = _read_response(
                        response, deadline, self.max_response_bytes
                    )
        except HTTPError as error:
            status = error.code
            try:
                error.close()
            except Exception:
                failure = LeaderFailureCode.TRANSPORT
        except (socket.timeout, TimeoutError):
            failure = LeaderFailureCode.TIMEOUT
        except URLError as error:
            if isinstance(error.reason, (socket.timeout, TimeoutError)):
                failure = LeaderFailureCode.TIMEOUT
            else:
                failure = LeaderFailureCode.TRANSPORT
        except (OSError, ValueError):
            failure = LeaderFailureCode.TRANSPORT
        except Exception:
            failure = LeaderFailureCode.TRANSPORT
        if response is not None:
            try:
                response.close()
            except Exception:
                if failure is None:
                    failure = LeaderFailureCode.TRANSPORT
        if failure is not None:
            return b"", failure
        if status in {401, 403}:
            return b"", LeaderFailureCode.AUTHENTICATION
        if type(status) is not int or not 200 <= status < 300:
            return b"", LeaderFailureCode.NONZERO
        return raw, None

    @staticmethod
    def _decode(
        raw: bytes, request: LeaderRequest
    ) -> tuple[LeaderProposal | None, LeaderFailureCode | None]:
        parsed = None
        failed = False
        try:
            parsed = json.loads(raw.decode("utf-8", "strict"))
            if type(parsed) is not dict:
                failed = True
            choices = parsed.get("choices") if type(parsed) is dict else None
            if type(choices) is not list or len(choices) != 1:
                failed = True
            choice = choices[0] if not failed else None
            message = choice.get("message") if type(choice) is dict else None
            content = message.get("content") if type(message) is dict else None
            if type(content) is not str:
                failed = True
            payload = json.loads(content) if not failed else None
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            AttributeError,
            IndexError,
            RecursionError,
        ):
            failed = True
            payload = None
        if failed:
            return None, LeaderFailureCode.SCHEMA
        try:
            proposal = LeaderProposal.from_mapping(payload, request=request)
        except ProposalError as error:
            code = error.code
            return None, code
        except Exception:
            return None, LeaderFailureCode.SCHEMA
        return proposal, None


def _read_response(
    response: object, deadline: float, maximum: int
) -> tuple[bytes, LeaderFailureCode | None]:
    chunks: list[bytes] = []
    total = 0
    failure: LeaderFailureCode | None = None
    try:
        reader = getattr(response, "read1", None)
        socket_object = response.fp.raw._sock  # type: ignore[attr-defined]
        if not callable(reader) or not callable(getattr(socket_object, "settimeout", None)):
            failure = LeaderFailureCode.TRANSPORT
        while failure is None and total <= maximum:
            remaining = deadline - monotonic()
            if remaining <= 0:
                failure = LeaderFailureCode.TIMEOUT
                break
            socket_object.settimeout(remaining)
            chunk = reader(min(_READ_CHUNK_BYTES, maximum + 1 - total))
            if type(chunk) is not bytes:
                failure = LeaderFailureCode.TRANSPORT
                break
            if monotonic() >= deadline:
                failure = LeaderFailureCode.TIMEOUT
                break
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                failure = LeaderFailureCode.OVERSIZE
            elif response.isclosed():  # type: ignore[attr-defined]
                break
    except (socket.timeout, TimeoutError):
        failure = LeaderFailureCode.TIMEOUT
    except Exception:
        failure = LeaderFailureCode.TRANSPORT
    if failure is not None:
        return b"", failure
    return b"".join(chunks), None


__all__ = ["LeaderUnavailable", "OpenAICompatibleLeader"]
