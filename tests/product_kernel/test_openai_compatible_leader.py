from __future__ import annotations

from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import traceback
from types import SimpleNamespace

import pytest

import agentdeck.adapters.providers as provider_module
from agentdeck.adapters.providers import LeaderUnavailable, OpenAICompatibleLeader
from agentdeck.kernel.agents import AgentRole
from agentdeck.ports.leader import (
    AvailableAgent,
    LeaderFailure,
    LeaderFailureCode,
    ResolvedLeaderModel,
    leader_proposal_json_schema,
)
from product_kernel.test_leader_contract import request as contract_request
from product_kernel.test_leader_contract import valid_proposal


MODEL = "exact-model-2026-07"
SECRET = "sk-local-contract-secret-marker"
BODY_MARKER = "provider-body-must-not-escape"


def api_request(*, provider: str = "custom", model: str = MODEL):
    return replace(
        contract_request(),
        resolved_model=ResolvedLeaderModel(
            backend_id=f"api:{provider}",
            adapter_id="openai-compatible",
            model_id=model,
            version="v1",
        ),
    )


def proposal_payload(*, provider: str = "custom", model: str = MODEL):
    payload = valid_proposal()
    payload.update(
        leader_backend=f"api:{provider}",
        leader_adapter="openai-compatible",
        leader_model=model,
        leader_version="v1",
    )
    return payload


def openai_response(payload: object | None = None) -> bytes:
    content = json.dumps(proposal_payload() if payload is None else payload)
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode()


@dataclass
class Response:
    status: int = 200
    body: bytes = openai_response()
    delay: float = 0.0
    headers: tuple[tuple[str, str], ...] = ()
    chunks: tuple[bytes, ...] = ()
    chunk_delay: float = 0.0


class LocalServer:
    def __init__(self) -> None:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self._respond()

            def do_POST(self) -> None:
                self._respond()

            def _respond(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                owner.request_count += 1
                owner.last_path = self.path
                owner.last_headers = {key.lower(): value for key, value in self.headers.items()}
                owner.last_json = json.loads(raw) if raw else {}
                response = owner.response
                if response.delay:
                    time.sleep(response.delay)
                self.send_response(response.status)
                self.send_header("Content-Type", "application/json")
                for key, value in response.headers:
                    self.send_header(key, value)
                chunks = response.chunks or (response.body,)
                self.send_header("Content-Length", str(sum(map(len, chunks))))
                self.end_headers()
                try:
                    for chunk in chunks:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                        if response.chunk_delay:
                            time.sleep(response.chunk_delay)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_args: object) -> None:
                pass

        self.response = Response()
        self.request_count = 0
        self.last_path = ""
        self.last_headers: dict[str, str] = {}
        self.last_json: dict[str, object] = {}
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


@pytest.fixture
def http_server():
    server = LocalServer()
    try:
        yield server
    finally:
        server.close()


def adapter(http_server: LocalServer, **overrides: object) -> OpenAICompatibleLeader:
    options: dict[str, object] = {
        "base_url": http_server.url,
        "model": MODEL,
        "credential_source": "LOCAL_API_KEY",
        "credential_resolver": lambda _source: SECRET,
    }
    options.update(overrides)
    return OpenAICompatibleLeader(**options)


def oversize_api_request():
    text = "界" * 1_360
    agents = tuple(
        AvailableAgent(
            instance_id=f"agent-{index}-{text}", role=AgentRole.IMPLEMENTER,
            backend_id=f"backend-{index}-{text}", acp_route_id=f"acp://{index}/{text}",
        )
        for index in range(64)
    )
    return replace(api_request(), available_agents=agents)


def exception_text(error: BaseException) -> str:
    parts = [str(error), "".join(traceback.format_exception(error))]
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        parts.append(repr(current))
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return "\n".join(parts)


def test_adapter_sends_exact_model_schema_and_bearer_credential(http_server) -> None:
    leader = adapter(http_server)
    proposal = leader.propose_mission(api_request())
    assert proposal.objective == "Build an accessible page"
    assert http_server.last_path == "/chat/completions"
    assert http_server.last_headers["authorization"] == f"Bearer {SECRET}"
    assert http_server.last_json["model"] == MODEL
    assert http_server.last_json["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "agentdeck_mission",
            "strict": True,
            "schema": leader_proposal_json_schema(),
        },
    }
    assert http_server.last_json["stream"] is False


def test_adapter_never_falls_back_from_an_exact_custom_model(http_server) -> None:
    exact = "vendor/private-exact-model"
    leader = adapter(http_server, model=exact)
    http_server.response.body = openai_response(proposal_payload(model=exact))
    proposal = leader.propose_mission(api_request(model=exact))
    assert proposal.mission.leader_model == exact
    assert http_server.last_json["model"] == exact


@pytest.mark.parametrize(
    ("provider", "base_url", "credential_source"),
    [
        ("deepseek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
        ("kimi", "https://api.moonshot.cn/v1", "MOONSHOT_API_KEY"),
        ("glm", "https://open.bigmodel.cn/api/paas/v4", "ZHIPUAI_API_KEY"),
    ],
)
def test_presets_only_resolve_base_url_and_credential_label(
    provider: str, base_url: str, credential_source: str
) -> None:
    leader = OpenAICompatibleLeader(
        provider=provider,
        model=MODEL,
        credential_resolver=lambda _source: SECRET,
    )
    assert (leader.provider, leader.base_url, leader.credential_source, leader.model) == (
        provider,
        base_url,
        credential_source,
        MODEL,
    )


@pytest.mark.parametrize(
    "options, diagnostic",
    [
        ({"model": ""}, "exact model"),
        ({"base_url": None}, "explicit base URL"),
        ({"credential_source": None}, "explicit credential source"),
        ({"base_url": "file:///tmp/provider"}, "HTTP base URL"),
        ({"credential_resolver": None}, "credential resolver"),
        ({"timeout_seconds": 0}, "positive timeout"),
        ({"max_response_bytes": 0}, "positive response bound"),
    ],
)
def test_invalid_custom_configuration_fails_before_http(
    http_server, options: dict[str, object], diagnostic: str
) -> None:
    configured: dict[str, object] = {
        "base_url": http_server.url,
        "model": MODEL,
        "credential_source": "LOCAL_API_KEY",
        "credential_resolver": lambda _source: SECRET,
    }
    configured.update(options)
    with pytest.raises(LeaderUnavailable, match=diagnostic):
        OpenAICompatibleLeader(**configured)
    assert http_server.request_count == 0


def test_presets_reject_identity_overrides() -> None:
    with pytest.raises(LeaderUnavailable, match="preset base URL"):
        OpenAICompatibleLeader(
            provider="deepseek",
            base_url="https://example.invalid/v1",
            model=MODEL,
            credential_resolver=lambda _source: SECRET,
        )
    with pytest.raises(LeaderUnavailable, match="preset credential source"):
        OpenAICompatibleLeader(
            provider="deepseek",
            model=MODEL,
            credential_source="OTHER_API_KEY",
            credential_resolver=lambda _source: SECRET,
        )


def test_request_identity_drift_is_rejected_before_credential_or_http(http_server) -> None:
    resolver_calls: list[str] = []
    leader = adapter(
        http_server,
        credential_resolver=lambda source: resolver_calls.append(source) or SECRET,
    )
    with pytest.raises(LeaderUnavailable, match="resolved Leader identity"):
        leader.propose_mission(contract_request())
    assert resolver_calls == []
    assert http_server.request_count == 0


@pytest.mark.parametrize("credential", [None, "", "  ", 42])
def test_missing_or_invalid_credential_never_sends_http(http_server, credential: object) -> None:
    leader = adapter(http_server, credential_resolver=lambda _source: credential)
    with pytest.raises(LeaderFailure) as error:
        leader.propose_mission(api_request())
    assert error.value.code is LeaderFailureCode.AUTHENTICATION
    assert http_server.request_count == 0


def test_credential_resolver_failure_is_content_free_and_sends_no_http(http_server) -> None:
    def resolver(_source: str) -> object:
        raise RuntimeError(SECRET)
    with pytest.raises(LeaderFailure) as error:
        adapter(http_server, credential_resolver=resolver).propose_mission(api_request())
    assert error.value.code is LeaderFailureCode.AUTHENTICATION
    assert SECRET not in exception_text(error.value)
    assert http_server.request_count == 0


def test_transport_failure_is_typed_and_does_not_expose_low_level_text(
    http_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(*_args: object, **_kwargs: object) -> object:
        raise provider_module.URLError(BODY_MARKER)

    monkeypatch.setattr(provider_module, "urlopen", unavailable)
    with pytest.raises(LeaderFailure) as error:
        adapter(http_server).propose_mission(api_request())
    assert error.value.code is LeaderFailureCode.TRANSPORT
    assert BODY_MARKER not in exception_text(error.value)
    assert SECRET not in exception_text(error.value)


def test_redirect_is_rejected_without_contacting_or_authorizing_target(http_server) -> None:
    target = LocalServer()
    try:
        http_server.response = Response(status=302, body=b"", headers=(
            ("Location", target.url + "/capture"),
        ))
        with pytest.raises(LeaderFailure) as error:
            adapter(http_server).propose_mission(api_request())
        assert error.value.code is LeaderFailureCode.NONZERO
        assert http_server.request_count == 1
        assert target.request_count == 0
        assert "authorization" not in target.last_headers
    finally:
        target.close()


def test_slow_trickle_obeys_one_total_deadline(http_server) -> None:
    http_server.response = Response(body=b"", chunks=(b"x",) * 10, chunk_delay=0.03)
    leader = adapter(http_server, timeout_seconds=0.05)
    started = time.monotonic()
    with pytest.raises(LeaderFailure) as error:
        leader.propose_mission(api_request())
    elapsed = time.monotonic() - started
    assert error.value.code is LeaderFailureCode.TIMEOUT
    assert elapsed < 0.18


def test_hostile_resolver_repr_is_never_evaluated_or_exposed(http_server) -> None:
    class Resolver:
        repr_calls = 0

        def __call__(self, _source: str) -> str:
            return SECRET

        def __repr__(self) -> str:
            self.repr_calls += 1
            raise AssertionError(BODY_MARKER)
    resolver = Resolver()
    leader = adapter(http_server, credential_resolver=resolver)
    leader.propose_mission(api_request())
    assert BODY_MARKER not in repr(leader)
    assert resolver.repr_calls == 0
    assert vars(leader)["_credential_resolver"] is resolver


@pytest.mark.parametrize("failure_stage", ("body", "read", "close"))
def test_hostile_response_is_content_free_transport_failure(
    http_server, monkeypatch: pytest.MonkeyPatch, failure_stage: str
) -> None:
    class HostileBody:
        def __len__(self) -> int:
            raise AssertionError(BODY_MARKER)
        def __repr__(self) -> str:
            raise AssertionError(BODY_MARKER)
    class HostileResponse:
        status = 200
        fp = SimpleNamespace(raw=SimpleNamespace(
            _sock=SimpleNamespace(settimeout=lambda _remaining: None)
        ))
        done = False
        def read1(self, _maximum: int) -> object:
            if failure_stage == "read":
                raise RuntimeError(BODY_MARKER)
            self.done = True
            return HostileBody() if failure_stage == "body" else b"not-json"
        def isclosed(self) -> bool:
            return self.done
        def close(self) -> None:
            if failure_stage == "close":
                raise RuntimeError(BODY_MARKER)
    monkeypatch.setattr(provider_module, "urlopen", lambda *_args, **_kwargs: HostileResponse())
    with pytest.raises(LeaderFailure) as error:
        adapter(http_server).propose_mission(api_request())
    assert error.value.code is LeaderFailureCode.TRANSPORT
    assert BODY_MARKER not in exception_text(error.value)


def test_oversize_request_fails_before_credential_or_http(http_server) -> None:
    resolver_calls: list[str] = []
    leader = adapter(
        http_server,
        credential_resolver=lambda source: resolver_calls.append(source) or SECRET,
    )
    with pytest.raises(LeaderFailure) as error:
        leader.propose_mission(oversize_api_request())
    assert error.value.code is LeaderFailureCode.OVERSIZE
    assert resolver_calls == []
    assert http_server.request_count == 0


@pytest.mark.parametrize(
    "status, expected",
    [(401, LeaderFailureCode.AUTHENTICATION), (403, LeaderFailureCode.AUTHENTICATION),
     (400, LeaderFailureCode.NONZERO), (429, LeaderFailureCode.NONZERO),
     (500, LeaderFailureCode.NONZERO)],
)
def test_http_failures_preserve_typed_categories(http_server, status, expected) -> None:
    http_server.response = Response(status=status, body=BODY_MARKER.encode())

    with pytest.raises(LeaderFailure) as error:
        adapter(http_server).propose_mission(api_request())

    assert error.value.code is expected
    assert BODY_MARKER not in exception_text(error.value)
    assert SECRET not in exception_text(error.value)


def test_timeout_is_bounded_and_content_free(http_server) -> None:
    http_server.response = Response(delay=0.2)
    leader = adapter(http_server, timeout_seconds=0.02)

    started = time.monotonic()
    with pytest.raises(LeaderFailure) as error:
        leader.propose_mission(api_request())

    assert error.value.code is LeaderFailureCode.TIMEOUT
    assert time.monotonic() - started < 1
    assert SECRET not in exception_text(error.value)


@pytest.mark.parametrize(
    "body",
    [
        b"not-json-" + BODY_MARKER.encode(),
        json.dumps({"choices": []}).encode(),
        json.dumps({"choices": [{"message": {"content": "not-json"}}]}).encode(),
    ],
)
def test_malformed_provider_responses_are_schema_failures(http_server, body: bytes) -> None:
    http_server.response.body = body

    with pytest.raises(LeaderFailure) as error:
        adapter(http_server).propose_mission(api_request())

    assert error.value.code is LeaderFailureCode.SCHEMA
    assert BODY_MARKER not in exception_text(error.value)


@pytest.mark.parametrize(
    "mutation, expected",
    [
        (lambda payload: payload.pop("tasks"), LeaderFailureCode.SCHEMA),
        (lambda payload: payload.update(project_root="/tmp/other"), LeaderFailureCode.SEMANTIC),
    ],
)
def test_invalid_mission_preserves_schema_or_semantic_category(
    http_server, mutation, expected
) -> None:
    payload = proposal_payload()
    mutation(payload)
    http_server.response.body = openai_response(payload)

    with pytest.raises(LeaderFailure) as error:
        adapter(http_server).propose_mission(api_request())

    assert error.value.code is expected


def test_oversize_response_is_bounded_and_does_not_leak_body(http_server) -> None:
    http_server.response.body = (BODY_MARKER * 20).encode()

    with pytest.raises(LeaderFailure) as error:
        adapter(http_server, max_response_bytes=32).propose_mission(api_request())

    assert error.value.code is LeaderFailureCode.OVERSIZE
    assert BODY_MARKER not in exception_text(error.value)


def test_adapter_does_not_persist_credential_or_expose_bodies(http_server) -> None:
    leader = adapter(http_server)
    http_server.response.body = BODY_MARKER.encode()

    with pytest.raises(LeaderFailure) as error:
        leader.propose_mission(api_request())

    assert SECRET not in repr(vars(leader))
    rendered = exception_text(error.value)
    assert SECRET not in rendered
    assert BODY_MARKER not in rendered
