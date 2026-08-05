from __future__ import annotations

import http.client
import re
import json
import threading
from pathlib import Path

import pytest

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


REGISTRY = {
    "mode": "control_registry",
    "items": [
        {
            "control_id": "ctl_inspect_ok",
            "command": "agentdeck plan status --plan-id pln_1",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
        {
            "control_id": "ctl_runtime",
            "command": "agentdeck agent spawn --agent coder",
            "safety": "explicit_runtime",
            "enabled": True,
            "blocker": None,
        },
        {
            "control_id": "ctl_disabled",
            "command": "agentdeck trace --id msg_1",
            "safety": "inspect",
            "enabled": False,
            "blocker": "no trace target",
        },
        {
            "control_id": "ctl_confirm_sneak",
            "command": "agentdeck approval auto --confirm",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
        {
            "control_id": "ctl_user",
            "command": "agentdeck approval approve --approval-id apv_1",
            "safety": "explicit_user",
            "enabled": True,
            "blocker": None,
        },
        {
            "control_id": "ctl_delegated",
            "command": "agentdeck approval auto --confirm",
            "safety": "delegated",
            "enabled": True,
            "blocker": None,
        },
        {
            "control_id": "ctl_template",
            "command": "agentdeck agent assign-role --agent planner --role <role>",
            "safety": "explicit_user",
            "enabled": True,
            "blocker": None,
        },
    ],
}


def _serve_registry(monkeypatch) -> tuple[http.client.HTTPConnection, object, list[list[str]]]:
    calls: list[list[str]] = []

    def fake_run(root: Path, argv: list[str]) -> object:
        calls.append(list(argv))
        if argv[0] == "controls":
            return REGISTRY
        return {"ok": True, "ran": argv}

    monkeypatch.setattr(ui, "run_cli_json", fake_run)
    server = ui.build_server(Path("."), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    return connection, server, calls


def _post_inspect(connection, control_id: str):
    body = json.dumps({"control_id": control_id}).encode("utf-8")
    connection.request(
        "POST", "/api/inspect", body=body, headers={"Content-Type": "application/json"}
    )
    return connection.getresponse()


def test_inspect_endpoint_runs_enabled_inspect_control(tmp_path, monkeypatch) -> None:
    connection, server, calls = _serve_registry(monkeypatch)
    try:
        response = _post_inspect(connection, "ctl_inspect_ok")
        assert response.status == 200
        payload = json.loads(response.read())
        assert payload["control_id"] == "ctl_inspect_ok"
        assert payload["result"]["ran"] == ["plan", "status", "--plan-id", "pln_1"]
        assert ["controls", "--control-id", "ctl_inspect_ok"] in calls
    finally:
        server.shutdown()
        connection.close()


def test_inspect_endpoint_refuses_non_inspect_disabled_and_confirm(tmp_path, monkeypatch) -> None:
    connection, server, calls = _serve_registry(monkeypatch)
    try:
        assert _post_inspect(connection, "ctl_runtime").status == 403
        assert _post_inspect(connection, "ctl_disabled").status == 403
        assert _post_inspect(connection, "ctl_confirm_sneak").status == 403
        assert _post_inspect(connection, "ctl_missing").status == 404
        executed = [c for c in calls if c[0] != "controls"]
        assert executed == []
    finally:
        server.shutdown()
        connection.close()


def test_inspect_endpoint_rejects_malformed_body_and_other_posts(tmp_path, monkeypatch) -> None:
    connection, server, calls = _serve_registry(monkeypatch)
    try:
        connection.request("POST", "/api/inspect", body=b"not json")
        assert connection.getresponse().status == 400
        connection.request("POST", "/api/workbench", body=b"{}")
        assert connection.getresponse().status == 405
        assert calls == []
    finally:
        server.shutdown()
        connection.close()


def _post_execute(connection, control_id: str, confirmed: object):
    body = json.dumps({"control_id": control_id, "confirmed": confirmed}).encode("utf-8")
    connection.request(
        "POST", "/api/execute", body=body, headers={"Content-Type": "application/json"}
    )
    return connection.getresponse()


def test_execute_endpoint_runs_confirmed_explicit_controls(tmp_path, monkeypatch) -> None:
    connection, server, calls = _serve_registry(monkeypatch)
    try:
        # explicit_user 需要 confirmed:true
        response = _post_execute(connection, "ctl_user", True)
        assert response.status == 200
        payload = json.loads(response.read())
        assert payload["result"]["ran"] == ["approval", "approve", "--approval-id", "apv_1"]
        # explicit_runtime 同样可执行（含 --confirm 的注册表命令合法）
        response = _post_execute(connection, "ctl_runtime", True)
        assert response.status == 200
        assert json.loads(response.read())["result"]["ran"] == ["agent", "spawn", "--agent", "coder"]
    finally:
        server.shutdown()
        connection.close()


def test_execute_endpoint_requires_confirmation(tmp_path, monkeypatch) -> None:
    connection, server, calls = _serve_registry(monkeypatch)
    try:
        response = _post_execute(connection, "ctl_user", False)
        assert response.status == 428
        response.read()
        executed = [c for c in calls if c[0] != "controls"]
        assert executed == []
    finally:
        server.shutdown()
        connection.close()


def test_execute_endpoint_refuses_wrong_safety_and_templates(tmp_path, monkeypatch) -> None:
    connection, server, calls = _serve_registry(monkeypatch)
    try:
        # inspect 走 /api/inspect，不走 execute
        assert _post_execute(connection, "ctl_inspect_ok", True).status == 403
        # delegated 保持复制不可执行（拍板：B 档不含 delegated）
        assert _post_execute(connection, "ctl_delegated", True).status == 403
        # 占位符模板拒绝
        assert _post_execute(connection, "ctl_template", True).status == 403
        # disabled / 未知
        assert _post_execute(connection, "ctl_disabled", True).status == 403
        assert _post_execute(connection, "ctl_missing", True).status == 404
        executed = [c for c in calls if c[0] != "controls"]
        assert executed == []
    finally:
        server.shutdown()
        connection.close()


def test_page_script_is_parseable_javascript(tmp_path) -> None:
    # round 9 live 发现：Python 字符串里的 \n 落进 JS 字符串成真换行，
    # 整个 <script> SyntaxError 拒绝解析、页面全瘫；用 node --check 守门。
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    page = ui._PAGE
    start = page.index("<script>") + len("<script>")
    end = page.index("</script>")
    script = page[start:end]
    script_file = tmp_path / "page.js"
    script_file.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        [node, "--check", str(script_file)], capture_output=True, text=True
    )
    assert completed.returncode == 0, completed.stderr


def test_ui_serve_cli_is_wired(tmp_path, monkeypatch) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["ui", "serve", "--port", "0"])
    assert args.port == 0
    assert callable(args.func)


def _post_chat(connection, body: object):
    connection.request(
        "POST", "/api/chat", json.dumps(body), {"Content-Type": "application/json"}
    )
    return connection.getresponse()


def test_chat_endpoint_passes_the_message_as_a_single_argument(tmp_path, monkeypatch) -> None:
    # inspect/execute 的骄傲属性是"浏览器只发 control_id";chat 必须收自由文本,
    # 缓解不是限制文本,而是文本从不进入命令位置——单个 argv 元素,无 shell。
    prepare_project(tmp_path, monkeypatch)
    connection, (server, calls) = _serve(monkeypatch, {"leader": {"ok": True, "mode": "help"}})
    try:
        response = _post_chat(connection, {"message": "查看 planner 输出 --confirm ; rm -rf /"})
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload["mode"] == "help"
        assert calls == [
            ["leader", "chat", "--message", "查看 planner 输出 --confirm ; rm -rf /"]
        ]
    finally:
        server.shutdown()
        connection.close()


def test_chat_endpoint_rejects_a_missing_or_empty_message(tmp_path, monkeypatch) -> None:
    prepare_project(tmp_path, monkeypatch)
    connection, (server, calls) = _serve(monkeypatch, {"leader": {"ok": True}})
    try:
        for body in ({}, {"message": ""}, {"message": "   "}, {"message": 5}, {"message": None}):
            response = _post_chat(connection, body)
            response.read()
            assert response.status == 400, body
        assert calls == []
    finally:
        server.shutdown()
        connection.close()


def test_chat_is_the_only_new_post_path(tmp_path, monkeypatch) -> None:
    prepare_project(tmp_path, monkeypatch)
    connection, (server, calls) = _serve(monkeypatch, {"leader": {"ok": True}})
    try:
        connection.request("POST", "/api/workbench", "{}", {"Content-Type": "application/json"})
        response = connection.getresponse()
        response.read()
        assert response.status == 405
        assert calls == []
    finally:
        server.shutdown()
        connection.close()


def test_served_page_scripts_actually_parse() -> None:
    # 2026-08-05:两次同类事故——`_PAGE` 是**非 raw** 三引号串,源码里的 `\n`
    # 和 `\"` 会被 Python 先解释一遍,到了浏览器就是断掉的字面量,整块 script
    # 静默解析失败。页面照常渲染(HTML/CSS 无碍),但**所有交互全死**。
    #
    # 之前的"实测"只抓页面、数 id、数 script 块——那些全过了,而 JS 从未运行。
    # 验结构不等于验功能;这条守卫直接让解析器说话。
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    blocks = re.findall(r"<script>(.*?)</script>", ui._PAGE, re.S)
    assert len(blocks) >= 2
    for index, block in enumerate(blocks):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
            handle.write(block)
            path = handle.name
        done = subprocess.run([node, "--check", path], capture_output=True, text=True)
        assert done.returncode == 0, f"script block {index} failed to parse:\n{done.stderr}"
