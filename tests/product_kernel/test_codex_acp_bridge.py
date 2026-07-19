from __future__ import annotations

import asyncio
from dataclasses import replace
import json

import pytest
from acp import PROTOCOL_VERSION
from acp.helpers import embedded_text_resource, resource_block
from acp.schema import AllowedOutcome, RequestPermissionResponse, TextContentBlock

from agentdeck.adapters.acp_leader import ACPLeader
from agentdeck.adapters.acp_transport import ACPStdioTransport
from agentdeck.adapters.codex_app_server import AppServerProtocolError
from agentdeck.adapters.codex_acp_server import CodexACPServer
from agentdeck.ports.leader import (
    ProjectContext, ResolvedLeaderModel, leader_proposal_json_schema,
)
from agentdeck.ports.transport import TransportPermissionDecision, TransportPromptPart
from product_kernel.fixtures.fake_codex_app_server import fake_command
from product_kernel.test_leader_contract import request, valid_proposal


class RecordingACPClient:
    def __init__(self) -> None:
        self.updates: list[object] = []
        self.permissions: list[tuple[str, object, list[object]]] = []

    async def session_update(self, session_id, update, **_kwargs) -> None:
        self.updates.append(update)

    async def request_permission(self, session_id, tool_call, options, **_kwargs):
        self.permissions.append((session_id, tool_call, options))
        return RequestPermissionResponse(
            outcome=AllowedOutcome(outcome="selected", option_id=options[0].option_id)
        )


def _methods(log_path) -> list[str]:
    return [
        entry["message"]["method"]
        for entry in map(json.loads, log_path.read_text().splitlines())
        if entry["kind"] == "received" and "method" in entry["message"]
    ]


def test_bridge_translates_acp_session_to_codex_thread_and_turn(tmp_path) -> None:
    async def scenario() -> None:
        log = tmp_path / "calls.jsonl"
        bridge = CodexACPServer(app_server_command=fake_command(log))
        client = RecordingACPClient()
        bridge.on_connect(client)
        initialized = await bridge.initialize(PROTOCOL_VERSION)
        assert initialized.protocol_version == PROTOCOL_VERSION
        session = await bridge.new_session(cwd=str(tmp_path))
        assert session.field_meta == {
            "agentdeck": {"resolved_model": "gpt-5.5", "server_version": "0.131.0"}
        }
        response = await bridge.prompt(
            session.session_id,
            [TextContentBlock(type="text", text="Implement the frozen task.")],
        )
        assert response.stop_reason == "end_turn"
        await bridge.close()
        assert _methods(log)[:4] == [
            "initialize", "initialized", "thread/start", "turn/start",
        ]
        assert any(getattr(update, "session_update", None) == "agent_message_chunk" for update in client.updates)

    asyncio.run(scenario())


def test_bridge_maps_server_permission_request_to_exact_acp_request(tmp_path) -> None:
    async def scenario() -> None:
        log = tmp_path / "calls.jsonl"
        bridge = CodexACPServer(app_server_command=fake_command(log))
        client = RecordingACPClient()
        bridge.on_connect(client)
        await bridge.initialize(PROTOCOL_VERSION)
        session = await bridge.new_session(cwd=str(tmp_path))
        await bridge.prompt(session.session_id, [TextContentBlock(type="text", text="work")])
        await bridge.close()
        assert len(client.permissions) == 1
        permission = client.permissions[0][1]
        assert permission.field_meta == {
            "native_method": "item/commandExecution/requestApproval",
            "native_request_id": "perm_42",
        }
        assert permission.tool_call_id == "item_42"
        assert permission.kind == "execute"
        assert "RAW-COMMAND-SECRET" not in repr(permission)
        responses = [
            entry["message"] for entry in map(json.loads, log.read_text().splitlines())
            if entry["kind"] == "permission_response"
        ]
        assert responses == [{"id": "perm_42", "result": {"decision": "accept"}}]

    asyncio.run(scenario())


def test_same_thread_previous_turn_update_fails_closed(tmp_path) -> None:
    async def scenario() -> None:
        bridge = CodexACPServer(app_server_command=fake_command(
            tmp_path / "calls.jsonl", mode="stale_turn"
        ))
        client = RecordingACPClient()
        bridge.on_connect(client)
        await bridge.initialize(PROTOCOL_VERSION)
        session = await bridge.new_session(cwd=str(tmp_path))
        with pytest.raises(AppServerProtocolError) as raised:
            await bridge.prompt(
                session.session_id, [TextContentBlock(type="text", text="work")]
            )
        await bridge.close()
        assert raised.value.code == "codex_acp_lineage_mismatch"
        assert "RAW-STALE-TURN-SECRET" not in repr(client.updates)

    asyncio.run(scenario())


def test_acp_leader_reaches_fake_app_server_through_console_bridge(tmp_path) -> None:
    proposal = valid_proposal()
    proposal["project_root"] = str(tmp_path)
    proposal["leader_model"] = "gpt-5.5"
    proposal["leader_version"] = "0.131.0"
    proposal_path = tmp_path / "proposal.json"
    proposal_path.write_text(json.dumps(proposal), encoding="utf-8")
    app_log = tmp_path / "app-server.jsonl"
    bridge = CodexACPServer(app_server_command=fake_command(
        app_log, mode="leader", proposal_path=proposal_path
    ))
    leader_request = replace(
        request(),
        project_context=ProjectContext(str(tmp_path), "isolated bridge project"),
        resolved_model=ResolvedLeaderModel(
            backend_id="codex-cli", adapter_id="acp",
            model_id="gpt-5.5", version="0.131.0",
        ),
    )
    result = ACPLeader(
        bridge.command, backend_id="codex-cli",
        model="gpt-5.5", version="0.131.0", timeout_seconds=5,
    ).propose_mission(leader_request)
    assert result.objective == "Build an accessible page"
    turn = next(
        entry["message"] for entry in map(json.loads, app_log.read_text().splitlines())
        if entry["kind"] == "received" and entry["message"].get("method") == "turn/start"
    )
    assert turn["params"]["outputSchema"] == leader_proposal_json_schema()
    assert turn["params"]["input"][0]["type"] == "text"
    assert "AgentDeck Mission request JSON:" in turn["params"]["input"][0]["text"]


def test_native_default_leader_resolves_session_model_before_turn(tmp_path) -> None:
    proposal = valid_proposal()
    proposal["project_root"] = str(tmp_path)
    proposal["leader_model"] = "gpt-5.5"
    proposal["leader_version"] = "0.131.0"
    proposal_path = tmp_path / "proposal.json"
    proposal_path.write_text(json.dumps(proposal), encoding="utf-8")
    log = tmp_path / "app-server.jsonl"
    bridge = CodexACPServer(app_server_command=fake_command(
        log, mode="leader", proposal_path=proposal_path,
    ))
    leader_request = replace(
        request(),
        project_context=ProjectContext(str(tmp_path), "default model bridge"),
        resolved_model=ResolvedLeaderModel(
            backend_id="codex-cli", adapter_id="acp",
            model_id="native-default", version="0.131.0",
        ),
    )
    result = ACPLeader(
        bridge.command, backend_id="codex-cli", model="native-default",
        version="0.131.0", timeout_seconds=5,
    ).propose_mission(leader_request)
    assert result.mission.leader_model == "gpt-5.5"
    assert "turn/start" in _methods(log)
    turn = next(item["message"] for item in map(json.loads, log.read_text().splitlines())
                if item["kind"] == "received" and item["message"].get("method") == "turn/start")
    request_json = turn["params"]["input"][0]["text"].split(
        "AgentDeck Mission request JSON:\n", 1)[1]
    assert json.loads(request_json)["resolved_leader"] == {
        "backend_id": "codex-cli", "adapter_id": "acp",
        "model_id": "gpt-5.5", "version": "0.131.0",
    }


@pytest.mark.parametrize(
    "case", [
        "extra", "malformed", "wrong_resource", "model_mismatch",
        "version_mismatch",
    ],
)
def test_leader_resource_contract_rejects_ambiguous_input(tmp_path, case) -> None:
    async def scenario() -> None:
        log = tmp_path / "calls.jsonl"
        bridge = CodexACPServer(app_server_command=fake_command(log, mode="no_permission"))
        bridge.on_connect(RecordingACPClient())
        await bridge.initialize(PROTOCOL_VERSION)
        session = await bridge.new_session(cwd=str(tmp_path))
        leader = ACPLeader(
            ("unused",), backend_id="codex-cli",
            model="other-model" if case == "model_mismatch" else "gpt-5.5",
            version="other-version" if case == "version_mismatch" else "0.131.0",
        )
        leader_request = replace(
            request(),
            project_context=ProjectContext(str(tmp_path), "isolated bridge project"),
        )
        part = leader._request_part(leader_request)
        content = part.text if case != "malformed" else "{}"
        uri = part.uri if case != "wrong_resource" else "agentdeck://other"
        blocks = [
            TextContentBlock(
                type="text",
                text=("Propose one AgentDeck Mission. Return the proposal only as the "
                      "declared structured ACP resource; do not execute tools or work."),
            ),
            resource_block(embedded_text_resource(
                uri, content, mime_type=part.mime_type,
            )),
        ]
        if case == "extra":
            blocks.append(TextContentBlock(type="text", text="extra authority"))
        with pytest.raises(ValueError):
            await bridge.prompt(session.session_id, blocks)
        await bridge.close()
        assert "turn/start" not in _methods(log)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mode", [
        "duplicate_permission_concurrent", "duplicate_permission_sequential",
        "permission_id_flood",
    ],
)
def test_duplicate_native_permission_request_id_fails_closed_once(tmp_path, mode) -> None:
    async def scenario() -> None:
        log = tmp_path / "calls.jsonl"
        bridge = CodexACPServer(app_server_command=fake_command(log, mode=mode))
        client = RecordingACPClient()
        bridge.on_connect(client)
        await bridge.initialize(PROTOCOL_VERSION)
        session = await bridge.new_session(cwd=str(tmp_path))
        with pytest.raises(AppServerProtocolError):
            await bridge.prompt(session.session_id, [TextContentBlock(type="text", text="work")])
        await bridge.close()
        if mode == "permission_id_flood":
            assert len(client.permissions) <= 64
        else:
            assert len(client.permissions) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["stable_notifications", "unknown_notification"])
def test_frozen_notifications_are_classified_and_unknown_drift_fails(tmp_path, mode) -> None:
    async def scenario() -> None:
        bridge = CodexACPServer(app_server_command=fake_command(
            tmp_path / "calls.jsonl", mode=mode,
        ))
        client = RecordingACPClient()
        bridge.on_connect(client)
        await bridge.initialize(PROTOCOL_VERSION)
        session = await bridge.new_session(cwd=str(tmp_path))
        if mode == "stable_notifications":
            response = await bridge.prompt(
                session.session_id, [TextContentBlock(type="text", text="work")]
            )
            assert response.stop_reason == "end_turn"
        else:
            with pytest.raises(AppServerProtocolError) as raised:
                await bridge.prompt(
                    session.session_id, [TextContentBlock(type="text", text="work")]
                )
            assert raised.value.code == "codex_app_server_notification_drift"
        await bridge.close()

    asyncio.run(scenario())


def test_stable_error_notification_fails_turn_instead_of_being_ignored(tmp_path) -> None:
    async def scenario() -> None:
        bridge = CodexACPServer(app_server_command=fake_command(
            tmp_path / "calls.jsonl", mode="error_notification",
        ))
        bridge.on_connect(RecordingACPClient())
        await bridge.initialize(PROTOCOL_VERSION)
        session = await bridge.new_session(cwd=str(tmp_path))
        with pytest.raises(AppServerProtocolError) as raised:
            await bridge.prompt(
                session.session_id, [TextContentBlock(type="text", text="work")]
            )
        await bridge.close()
        assert raised.value.code == "codex_app_server_turn_failed"
        assert "RAW-TURN-FAILURE" not in str(raised.value)

    asyncio.run(scenario())


def test_same_lineage_model_reroute_drift_fails_before_later_updates(tmp_path) -> None:
    async def scenario() -> None:
        bridge = CodexACPServer(app_server_command=fake_command(
            tmp_path / "calls.jsonl", mode="model_rerouted_drift",
        ))
        client = RecordingACPClient()
        bridge.on_connect(client)
        await bridge.initialize(PROTOCOL_VERSION)
        session = await bridge.new_session(cwd=str(tmp_path))
        with pytest.raises(AppServerProtocolError) as raised:
            await bridge.prompt(
                session.session_id, [TextContentBlock(type="text", text="work")]
            )
        await bridge.close()
        assert raised.value.code == "codex_app_server_model_drift"
        assert "RAW-AFTER-MODEL-DRIFT" not in repr(client.updates)

    asyncio.run(scenario())


def test_stable_streamed_updates_map_without_raw_codex_output(tmp_path) -> None:
    async def scenario() -> None:
        bridge = CodexACPServer(app_server_command=fake_command(
            tmp_path / "calls.jsonl", mode="streamed_updates"
        ))
        client = RecordingACPClient()
        bridge.on_connect(client)
        await bridge.initialize(PROTOCOL_VERSION)
        session = await bridge.new_session(cwd=str(tmp_path))
        response = await bridge.prompt(
            session.session_id, [TextContentBlock(type="text", text="work")]
        )
        await bridge.close()
        assert response.stop_reason == "end_turn"
        assert sum(
            getattr(update, "session_update", None) == "tool_call_update"
            for update in client.updates
        ) >= 3
        assert "RAW-STREAM-SECRET" not in repr(client.updates)

    asyncio.run(scenario())


def test_official_acp_stdio_surface_streams_and_bridges_permission(tmp_path) -> None:
    async def scenario() -> None:
        log = tmp_path / "calls.jsonl"
        bridge = CodexACPServer(app_server_command=fake_command(log))
        async with ACPStdioTransport(
            bridge.command, project_root=str(tmp_path), timeout_seconds=5,
        ) as transport:
            capabilities = await transport.initialize()
            assert capabilities.resume_session is True
            session = await transport.new_session()
            prompt = asyncio.create_task(transport.prompt(
                session, (TransportPromptPart(kind="text", text="work"),)
            ))
            kinds = []
            async for update in transport.stream_updates(session):
                kinds.append(update.kind.value)
                if update.kind.value == "permission":
                    await transport.respond_permission(
                        session,
                        TransportPermissionDecision(
                            request_id=update.permission.request_id,
                            allowed=True,
                            reason="approved for bridge contract",
                        ),
                    )
            assert (await prompt).stop_reason == "end_turn"
            assert "permission" in kinds
            assert "message" in kinds
        assert _methods(log)[:4] == [
            "initialize", "initialized", "thread/start", "turn/start",
        ]

    asyncio.run(scenario())


def test_bridge_cancel_maps_to_exact_turn_interrupt(tmp_path) -> None:
    async def scenario() -> None:
        log = tmp_path / "calls.jsonl"
        bridge = CodexACPServer(app_server_command=fake_command(log, mode="hang"))
        client = RecordingACPClient()
        bridge.on_connect(client)
        await bridge.initialize(PROTOCOL_VERSION)
        session = await bridge.new_session(cwd=str(tmp_path))
        prompt = asyncio.create_task(bridge.prompt(
            session.session_id, [TextContentBlock(type="text", text="work")]
        ))
        while "turn/start" not in _methods(log):
            await asyncio.sleep(0.01)
        await bridge.cancel(session.session_id)
        assert (await prompt).stop_reason == "cancelled"
        await bridge.close()
        assert _methods(log)[-1] == "turn/interrupt"

    asyncio.run(scenario())
