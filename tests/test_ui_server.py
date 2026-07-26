from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from agentdeck import cli
from agentdeck import ui
from agentdeck.config import write_default_config


def prepare_project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    monkeypatch.chdir(root)
    return root


def test_api_command_whitelist_is_read_only() -> None:
    assert set(ui.UI_API_COMMANDS) == {"workbench", "events", "controls"}
    for argv in ui.UI_API_COMMANDS.values():
        head = argv[0]
        assert head in {"workbench", "events", "controls"}
        # 白名单里绝不允许 mutating 动词
        for token in argv:
            assert "--confirm" not in token


def _serve(monkeypatch, responses: dict[str, object]) -> tuple[http.client.HTTPConnection, object]:
    calls: list[list[str]] = []

    def fake_run(root: Path, argv: list[str]) -> object:
        calls.append(list(argv))
        return responses[argv[0]]

    monkeypatch.setattr(ui, "run_cli_json", fake_run)
    server = ui.build_server(Path("."), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    return connection, (server, calls)


def test_server_serves_page_and_json_apis(tmp_path, monkeypatch) -> None:
    responses = {
        "workbench": {"schema_version": "project-view/v1", "leader_card": {}},
        "events": {"events": [{"event_id": "evt_1"}]},
        "controls": {"mode": "control_registry", "items": []},
    }
    connection, (server, calls) = _serve(monkeypatch, responses)
    try:
        connection.request("GET", "/")
        page = connection.getresponse()
        body = page.read().decode("utf-8")
        assert page.status == 200
        assert "AgentDeck" in body

        connection.request("GET", "/api/workbench")
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["schema_version"] == "project-view/v1"

        connection.request("GET", "/api/controls")
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["mode"] == "control_registry"

        connection.request("GET", "/api/events")
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["events"][0]["event_id"] == "evt_1"
    finally:
        server.shutdown()
        connection.close()


def test_events_cursor_is_passed_as_single_argument(tmp_path, monkeypatch) -> None:
    responses = {"workbench": {}, "events": {"events": []}, "controls": {}}
    connection, (server, calls) = _serve(monkeypatch, responses)
    try:
        connection.request("GET", "/api/events?since=evt_1%3Brm%20-rf")
        response = connection.getresponse()
        assert response.status == 200
        response.read()
        events_calls = [c for c in calls if c[0] == "events"]
        assert events_calls[-1] == ["events", "--since", "evt_1;rm -rf"]
    finally:
        server.shutdown()
        connection.close()


def test_unknown_paths_and_methods_are_rejected(tmp_path, monkeypatch) -> None:
    responses = {"workbench": {}, "events": {"events": []}, "controls": {}}
    connection, (server, calls) = _serve(monkeypatch, responses)
    try:
        connection.request("GET", "/api/dispatch")
        assert connection.getresponse().status == 404
        connection.request("POST", "/api/workbench", body=b"{}")
        assert connection.getresponse().status == 405
    finally:
        server.shutdown()
        connection.close()


def test_ui_serve_cli_is_wired(tmp_path, monkeypatch) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["ui", "serve", "--port", "0"])
    assert args.port == 0
    assert callable(args.func)
