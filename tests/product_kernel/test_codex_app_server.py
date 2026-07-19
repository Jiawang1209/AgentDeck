from __future__ import annotations

import asyncio
import json
import shutil

import pytest

from agentdeck.adapters.codex_app_server import (
    AppServerProtocolError,
    CodexAppServerClient,
    probe_codex_bridge,
)
from product_kernel.fixtures.fake_codex_app_server import (
    FAKE_VERSION,
    SCHEMA_DIGEST,
    fake_command,
)


def _calls(path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_read_only_probe_pins_version_and_stable_schema(tmp_path) -> None:
    log = tmp_path / "calls.jsonl"
    readiness = probe_codex_bridge(
        fake_command(log), expected_version=FAKE_VERSION,
        expected_digest=SCHEMA_DIGEST,
    )
    assert readiness.ready is True
    assert readiness.version == FAKE_VERSION
    assert readiness.schema_digest == SCHEMA_DIGEST
    assert readiness.diagnostic is None
    assert not log.exists()


def test_schema_digest_is_canonical_across_generator_key_order(tmp_path) -> None:
    readiness = probe_codex_bridge(
        fake_command(tmp_path / "calls.jsonl", mode="schema_reordered"),
        expected_version=FAKE_VERSION, expected_digest=SCHEMA_DIGEST,
    )
    assert readiness.ready is True
    assert readiness.schema_digest == SCHEMA_DIGEST


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("version_drift", "codex_app_server_version_drift"),
        ("schema_drift", "codex_app_server_schema_drift"),
    ],
)
def test_version_or_schema_drift_blocks_preflight(tmp_path, mode, code) -> None:
    readiness = probe_codex_bridge(
        fake_command(tmp_path / "calls.jsonl", mode=mode),
        expected_version=FAKE_VERSION, expected_digest=SCHEMA_DIGEST,
    )
    assert readiness.ready is False
    assert readiness.diagnostic.code == code
    assert "9.9.9" not in repr(readiness.diagnostic)


def test_client_uses_stable_initialization_thread_and_turn_methods(tmp_path) -> None:
    async def scenario() -> None:
        log = tmp_path / "calls.jsonl"
        async with CodexAppServerClient(fake_command(log, mode="no_permission")) as client:
            await client.initialize()
            thread = await client.start_thread(cwd=str(tmp_path), model="native-default")
            result = await client.start_turn(
                thread_id=thread, text="Implement the frozen task.",
                on_notification=lambda _method, _params: None,
                on_permission=lambda _request: None,
            )
            assert result.status == "completed"
        messages = [entry["message"] for entry in _calls(log) if entry["kind"] == "received"]
        assert [message["method"] for message in messages[:4]] == [
            "initialize", "initialized", "thread/start", "turn/start",
        ]
        assert all("jsonrpc" not in message for message in messages)
        assert messages[0]["params"]["capabilities"] == {"experimentalApi": False}
        assert all(
            message.get("params", {}).get("capabilities", {}).get("experimentalApi")
            is not True for message in messages
        )

    asyncio.run(scenario())


def test_real_frozen_codex_passive_initialize_uses_official_wire() -> None:
    codex = shutil.which("codex")
    if codex is None:
        pytest.skip("frozen Codex CLI is not installed")
    readiness = probe_codex_bridge((codex, "app-server"))
    if not readiness.ready:
        pytest.skip("installed Codex CLI does not match the frozen Task 25 identity")

    async def scenario() -> None:
        async with CodexAppServerClient((codex, "app-server")) as client:
            initialized = await client.initialize()
            assert set(initialized) >= {
                "userAgent", "codexHome", "platformFamily", "platformOs",
            }

    asyncio.run(scenario())


def test_client_resumes_and_interrupts_exact_thread_turn(tmp_path) -> None:
    async def scenario() -> None:
        log = tmp_path / "calls.jsonl"
        async with CodexAppServerClient(fake_command(log, mode="hang")) as client:
            await client.initialize()
            assert await client.resume_thread("thr_saved", cwd=str(tmp_path)) == "thr_saved"
            pending = asyncio.create_task(client.start_turn(
                thread_id="thr_saved", text="continue",
                on_notification=lambda _method, _params: None,
                on_permission=lambda _request: None,
            ))
            while not any(
                item.get("message", {}).get("method") == "turn/start"
                for item in _calls(log)
            ):
                await asyncio.sleep(0.01)
            await client.interrupt_turn(thread_id="thr_saved", turn_id="turn_42")
            assert (await pending).status == "interrupted"
        messages = [entry["message"] for entry in _calls(log) if entry["kind"] == "received"]
        assert [message["method"] for message in messages] == [
            "initialize", "initialized", "thread/resume", "turn/start", "turn/interrupt",
        ]
        assert messages[-1]["params"] == {"threadId": "thr_saved", "turnId": "turn_42"}

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["malformed_output", "oversize_output"])
def test_jsonl_is_bounded_and_protocol_errors_are_content_free(tmp_path, mode) -> None:
    async def scenario() -> None:
        async with CodexAppServerClient(
            fake_command(tmp_path / "calls.jsonl", mode=mode), max_line_bytes=4096,
        ) as client:
            with pytest.raises(AppServerProtocolError) as raised:
                await client.initialize()
        assert "RAW-FAKE-SECRET" not in str(raised.value)
        assert raised.value.code in {"codex_app_server_protocol", "codex_app_server_oversize"}

    asyncio.run(scenario())
