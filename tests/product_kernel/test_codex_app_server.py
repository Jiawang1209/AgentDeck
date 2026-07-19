from __future__ import annotations

import asyncio
import json
import os
import signal
import shutil
import subprocess
import time

import pytest

from agentdeck.adapters.codex_app_server import (
    AppServerProtocolError,
    CodexAppServerClient,
    probe_codex_bridge,
)
from agentdeck.adapters.codex_acp_server import STABLE_NOTIFICATION_METHODS
from product_kernel.fixtures.fake_codex_app_server import (
    FAKE_USER_AGENT, FAKE_VERSION,
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
            assert client.server_version == "0.131.0"
            assert client.server_user_agent == FAKE_USER_AGENT
            thread = await client.start_thread(cwd=str(tmp_path), model="native-default")
            assert thread.thread_id == "thr_42"
            assert thread.model == "gpt-5.5"
            result = await client.start_turn(
                thread_id=thread.thread_id, text="Implement the frozen task.",
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


@pytest.mark.parametrize(
    ("mode", "operation", "code"),
    [
        ("initialize_version_mismatch", "initialize", "codex_app_server_version_drift"),
        ("thread_model_mismatch", "thread", "codex_app_server_model_drift"),
    ],
)
def test_server_reported_version_and_model_are_authoritative(
    tmp_path, mode, operation, code,
) -> None:
    async def scenario() -> None:
        async with CodexAppServerClient(fake_command(tmp_path / "calls.jsonl", mode=mode)) as client:
            with pytest.raises(AppServerProtocolError) as raised:
                await client.initialize()
                if operation == "thread":
                    await client.start_thread(cwd=str(tmp_path), model="gpt-5.4")
        assert raised.value.code == code

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", [
    "version_stream_oversize", "schema_stream_oversize",
    "version_one_shot_oversize", "schema_one_shot_oversize",
])
def test_probe_stops_streaming_producer_at_byte_bound(tmp_path, mode) -> None:
    log = tmp_path / "probe.jsonl"
    readiness = probe_codex_bridge(
        fake_command(log, mode=mode), expected_version=FAKE_VERSION,
        expected_digest=SCHEMA_DIGEST,
    )
    assert readiness.ready is False
    assert readiness.diagnostic.code == "codex_app_server_probe_failed"
    calls = _calls(log)
    pid = next(item["pid"] for item in calls if item.get("phase") == "started")
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    chunks = [item for item in calls if "chunk" in item]
    assert len(chunks) < 16
    stored = [item["stored_bytes"] for item in calls if "stored_bytes" in item]
    assert not stored or max(stored) <= 8 * 1024 * 1024


def test_probe_does_not_spool_unbounded_stdout_to_a_regular_file(
    tmp_path, monkeypatch,
) -> None:
    def forbidden_spool(*_args, **_kwargs):
        raise AssertionError("probe stdout must use a bounded pipe")

    monkeypatch.setattr("tempfile.TemporaryFile", forbidden_spool)
    readiness = probe_codex_bridge(
        fake_command(tmp_path / "probe.jsonl", mode="version_one_shot_oversize"),
        expected_version=FAKE_VERSION, expected_digest=SCHEMA_DIGEST,
    )
    assert readiness.ready is False
    assert readiness.diagnostic.code == "codex_app_server_probe_failed"


def test_probe_reaps_sigterm_ignoring_grandchild_after_leader_exits(tmp_path) -> None:
    log = tmp_path / "probe.jsonl"
    grandchild = None
    try:
        readiness = probe_codex_bridge(
            fake_command(log, mode="version_orphan_oversize"),
            expected_version=FAKE_VERSION, expected_digest=SCHEMA_DIGEST,
        )
        grandchild = next(item["grandchild_pid"] for item in _calls(log)
                          if "grandchild_pid" in item)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(grandchild, 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
        with pytest.raises(ProcessLookupError):
            os.kill(grandchild, 0)
        assert readiness.diagnostic.code == "codex_app_server_probe_failed"
    finally:
        if grandchild is not None:
            try:
                os.kill(grandchild, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_real_frozen_codex_resolves_default_to_actual_thread_model(tmp_path) -> None:
    codex = shutil.which("codex")
    if codex is None:
        pytest.skip("frozen Codex CLI is not installed")
    home = tmp_path / "codex-home"
    home.mkdir()
    command = ("/usr/bin/env", f"CODEX_HOME={home}", codex, "app-server")
    readiness = probe_codex_bridge(command)
    if not readiness.ready:
        pytest.skip("installed Codex CLI does not match the frozen Task 25 identity")

    async def scenario() -> None:
        async with CodexAppServerClient(command) as client:
            initialized = await client.initialize()
            assert set(initialized) >= {
                "userAgent", "codexHome", "platformFamily", "platformOs",
            }
            thread = await client.start_thread(cwd=str(tmp_path), model="native-default")
            assert thread.thread_id
            assert thread.model and thread.model != "native-default"

    asyncio.run(scenario())


def test_notification_classification_matches_frozen_generated_schema(tmp_path) -> None:
    codex = shutil.which("codex")
    if codex is None or not probe_codex_bridge((codex, "app-server")).ready:
        pytest.skip("frozen Codex CLI is not installed")
    subprocess.run(
        (codex, "app-server", "generate-json-schema", "--out", str(tmp_path)),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, timeout=10, check=True,
    )
    schema = json.loads(
        (tmp_path / "codex_app_server_protocol.v2.schemas.json").read_text()
    )
    notification = schema["definitions"]["ServerNotification"]
    methods = {
        item["properties"]["method"]["enum"][0]
        for item in notification["oneOf"]
    }
    assert methods == STABLE_NOTIFICATION_METHODS


def test_client_resumes_and_interrupts_exact_thread_turn(tmp_path) -> None:
    async def scenario() -> None:
        log = tmp_path / "calls.jsonl"
        async with CodexAppServerClient(fake_command(log, mode="hang")) as client:
            await client.initialize()
            thread = await client.resume_thread(
                "thr_saved", cwd=str(tmp_path), model="native-default",
            )
            assert (thread.thread_id, thread.model) == ("thr_saved", "gpt-5.5")
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
