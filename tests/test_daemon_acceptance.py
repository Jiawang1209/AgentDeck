from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import pty
import re
import select
import shlex
import signal
import shutil
import subprocess
import sys
import tempfile
import time

import pytest

from agentdeck.config import load_config, write_default_config
from agentdeck import cli as cli_module
from agentdeck.contracts import (
    validate_daemon_runtime_contract,
    validate_mission_run_contract,
    validate_mission_scheduler_contract,
    validate_workbench_contract,
)
from agentdeck.daemon.client import DaemonClient, DaemonClientError
from agentdeck.daemon.client import admit_confirmed_mission, connect_or_start
from agentdeck.daemon.lifecycle import (
    daemon_endpoint,
    project_root_hash,
    reconcile_endpoint,
)
from agentdeck.conversation.leader_gateway import LEADER_FAILURE_STAGES
from agentdeck.mission_orchestration import confirm_mission_for_daemon, create_mission_preview
from agentdeck.providers import LeaderPlanRequest
from agentdeck.state import StateStore


FAKE_AGENT = Path(__file__).parent / "fixtures" / "fake_acp_agent.py"
CONVERSATION_WRAPPER = Path(__file__).parent / "fixtures" / "conversation_cli_wrapper.py"


class _FourStageProvider:
    name = "fake"

    def plan(self, request: LeaderPlanRequest) -> dict[str, object]:
        assert request.selected_agent_ids == ("claude-worker", "codex-worker")
        assert request.step_count == 4
        return {
            "goal": "complete deterministic four-stage Mission",
            "summary": "implementation, review, revision, acceptance",
            "steps": [
                {
                    "step": 1,
                    "agent_id": "claude-worker",
                    "role": "implementation",
                    "task": "implementation: create artifact.txt containing draft-v1",
                    "risk": "edit requires approval",
                    "requires_approval": True,
                },
                {
                    "step": 2,
                    "agent_id": "codex-worker",
                    "role": "review",
                    "task": "review: require artifact.txt to contain accepted-v2",
                    "risk": "review only",
                    "requires_approval": True,
                },
                {
                    "step": 3,
                    "agent_id": "claude-worker",
                    "role": "implementation",
                    "task": "revision: replace draft-v1 with accepted-v2",
                    "risk": "edit requires approval",
                    "requires_approval": True,
                },
                {
                    "step": 4,
                    "agent_id": "codex-worker",
                    "role": "review",
                    "task": "acceptance: verify artifact.txt equals accepted-v2",
                    "risk": "verification only",
                    "requires_approval": True,
                },
            ],
            "approval_required": True,
            "dispatch_ready": False,
        }


def _cleanup_pty(process: subprocess.Popen[bytes], master: int) -> None:
    primary = sys.exception()
    errors: list[BaseException] = []
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if process.poll() is None:
            raise AssertionError("acceptance PTY process did not exit")
    except BaseException as error:
        errors.append(error)
    try:
        os.close(master)
    except BaseException as error:
        errors.append(error)
    if errors:
        summary = "; ".join(repr(error) for error in errors)
        if primary is not None:
            primary.add_note(f"acceptance PTY cleanup failures: {summary}")
        else:
            raise ExceptionGroup("acceptance PTY cleanup failed", errors)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@dataclass
class _AcceptanceResources:
    root: Path
    parent: Path
    daemon_pid: int | None = None

    def cleanup(self) -> list[BaseException]:
        errors: list[BaseException] = []
        try:
            asyncio.run(_stop(self.root))
        except BaseException:
            # Active recovery/permission facts legitimately reject ordinary stop;
            # the verified PID fallback below remains the cleanup authority.
            pass
        metadata = daemon_endpoint(self.root).metadata_path
        if metadata.exists():
            try:
                self.daemon_pid = int(json.loads(metadata.read_text())["pid"])
            except BaseException as error:
                errors.append(error)
        if self.daemon_pid is not None and _pid_alive(self.daemon_pid):
            try:
                os.kill(self.daemon_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except BaseException as error:
                errors.append(error)
        if self.daemon_pid is not None:
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and _pid_alive(self.daemon_pid):
                    time.sleep(0.05)
                if _pid_alive(self.daemon_pid):
                    raise AssertionError("acceptance daemon did not exit")
            except BaseException as error:
                errors.append(error)
        try:
            reconcile_endpoint(
                self.root,
                expected_project_hash=project_root_hash(self.root),
                health_probe=lambda _: {"healthy": False},
            )
            endpoint = daemon_endpoint(self.root)
            if endpoint.socket_path.exists() or endpoint.metadata_path.exists():
                raise AssertionError("acceptance daemon endpoint was not reconciled")
        except BaseException as error:
            errors.append(error)
        try:
            shutil.rmtree(self.parent, ignore_errors=False)
            if self.parent.exists():
                raise AssertionError("acceptance temporary project was not removed")
        except FileNotFoundError:
            pass
        except BaseException as error:
            errors.append(error)
        return errors


@contextmanager
def _acceptance_resource_guard(*, root: Path, parent: Path):
    resources = _AcceptanceResources(root=root, parent=parent)
    primary: BaseException | None = None
    try:
        yield resources
    except BaseException as error:
        primary = error
        raise
    finally:
        cleanup_errors = resources.cleanup()
        if cleanup_errors:
            summary = "; ".join(repr(error) for error in cleanup_errors)
            if primary is not None:
                primary.add_note(f"acceptance cleanup failures: {summary}")
            else:
                raise ExceptionGroup("acceptance cleanup failed", cleanup_errors)


def _durable_conversation_terminal(
    state: dict[str, object],
) -> dict[str, str] | None:
    turns = state.get("conversation_turns")
    transitions = state.get("conversation_state_transitions")
    if not isinstance(turns, list) or not turns or not isinstance(transitions, list):
        return None
    latest = turns[-1]
    turn_id = latest.get("turn_id") if isinstance(latest, dict) else None
    if not isinstance(turn_id, str):
        return None
    transition = next(
        (
            item
            for item in reversed(transitions)
            if isinstance(item, dict)
            and item.get("entity_type") == "turn"
            and item.get("entity_id") == turn_id
        ),
        None,
    )
    if not isinstance(transition, dict):
        return None
    terminal_state = transition.get("to_state")
    reason = transition.get("reason")
    if terminal_state not in {"failed", "cancelled"} or not isinstance(reason, str):
        return None
    prefix = "leader_"
    stage = reason[len(prefix) :] if reason.startswith(prefix) else ""
    if stage not in LEADER_FAILURE_STAGES:
        return None
    return {"state": terminal_state, "stage": stage}


def _wait(store: StateStore, predicate, timeout: float = 12) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = store.load()
        if predicate(state):
            return state
        terminal = _durable_conversation_terminal(state)
        if terminal is not None:
            raise AssertionError(
                "durable conversation terminated before Mission became available: "
                f"state={terminal['state']} stage={terminal['stage']}"
            )
        time.sleep(0.05)
    state = store.load()
    runtime = store.root / ".agentdeck" / "runtime"
    diagnostics = {
        path.name: path.read_text(encoding="utf-8", errors="replace")[-2000:]
        for path in runtime.glob("*.log")
    }
    raise AssertionError(
        "timed out waiting for deterministic daemon state: "
        + repr({key: state.get(key) for key in (
            "missions", "mission_attempts", "permission_requests",
            "agent_sessions", "protocol_turns", "recovery_decisions", "daemon_runtime",
        )}) + " runtime=" + repr(diagnostics)
    )


@pytest.mark.parametrize(
    ("terminal_state", "reason", "expected"),
    [
        ("failed", "leader_timeout", {"state": "failed", "stage": "timeout"}),
        (
            "cancelled",
            "leader_cancelled",
            {"state": "cancelled", "stage": "cancelled"},
        ),
    ],
)
def test_acceptance_wait_reads_durable_conversation_terminal_without_waiting_for_mission(
    terminal_state: str,
    reason: str,
    expected: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {
        "conversation_turns": [
            {"turn_id": "cvt_terminal", "conversation_id": "cvs_terminal"}
        ],
        "conversation_state_transitions": [
            {
                "entity_type": "turn",
                "entity_id": "cvt_terminal",
                "to_state": terminal_state,
                "reason": reason,
            }
        ],
    }

    class Store:
        def load(self):
            return state

    monkeypatch.setattr(
        time,
        "sleep",
        lambda _seconds: pytest.fail("durable terminal must stop acceptance wait"),
    )

    with pytest.raises(
        AssertionError,
        match=f"state={expected['state']} stage={expected['stage']}",
    ):
        _wait(Store(), lambda _state: False)  # type: ignore[arg-type]


async def _decide_pending_permission(
    root: Path, permission_id: str, *, verify_replay: bool = False
) -> dict[str, object]:
    client = await DaemonClient.connect_verified(root)
    try:
        lease = await client.request("controller.acquire", {"client_id": "acceptance-reconnect"})
        authority = {
            "lease_id": lease["lease_id"],
            "lease_generation": lease["generation"],
        }
        bindings = [
            item
            for item in StateStore(root).load()["mission_permission_bindings"]
            if item.get("permission_id") == permission_id
        ]
        assert len(bindings) == 1
        params = {
            "mission_id": bindings[0]["mission_id"],
            "attempt_id": bindings[0]["attempt_id"],
            "permission_id": permission_id,
            "decision": "approved",
        }
        preview = await client.request("permission.decide", params, **authority)
        assert set(preview) == {
            "preview_id",
            "action",
            "generation",
            "execution_digest",
            "previewed_at",
            "expires_at",
            "state",
        }
        assert preview["action"] == "permission_decision"
        assert preview["state"] == "pending"
        result = await client.request(
            "permission.decide", {**params, "preview_id": preview["preview_id"]}, **authority
        )
        assert result["state"] == "approved"
        if verify_replay:
            with pytest.raises(
                DaemonClientError,
                match="terminal|confirmation|live permission waiter authority",
            ):
                await client.request(
                    "permission.decide",
                    {**params, "preview_id": preview["preview_id"]},
                    **authority,
                )
        await client.request(
            "controller.release",
            {"lease_id": lease["lease_id"], "generation": lease["generation"]},
            **authority,
        )
        return {"preview": preview, "result": result}
    finally:
        await client.close()


def _decide_permission_through_public_control(
    root: Path,
    *,
    preview_command: str,
    mission_id: str,
    attempt_id: str,
    permission_id: str,
    verify_replay: bool = False,
) -> dict[str, object]:
    preview_process = subprocess.run(
        shlex.split(preview_command),
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert preview_process.returncode == 0, preview_process.stderr
    preview = json.loads(preview_process.stdout)
    assert preview == {
        "mode": "daemon_permission_preview",
        "mission_id": mission_id,
        "attempt_id": attempt_id,
        "permission_id": permission_id,
        "decision": "approved",
        "preview_id": preview["preview_id"],
        "lease_id": preview["lease_id"],
        "lease_generation": preview["lease_generation"],
        "expires_at": preview["expires_at"],
        "confirm_command": preview["confirm_command"],
    }
    confirm_process = subprocess.run(
        shlex.split(str(preview["confirm_command"])),
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert confirm_process.returncode == 0, confirm_process.stderr
    confirmed = json.loads(confirm_process.stdout)
    assert confirmed == {
        "mode": "daemon_permission_confirmed",
        "mission_id": mission_id,
        "attempt_id": attempt_id,
        "permission_id": permission_id,
        "decision": "approved",
        "state": "approved",
    }
    if verify_replay:
        replay = subprocess.run(
            shlex.split(str(preview["confirm_command"])),
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        assert replay.returncode != 0
        assert "controller lease" in replay.stderr
    return {"preview": preview, "result": confirmed}


async def _govern_worker_ownership(
    root: Path,
    *,
    method: str,
    agent_id: str,
    reported_changes: dict[str, object] | None = None,
    verify_replay: bool = False,
) -> dict[str, object]:
    client = await DaemonClient.connect_verified(root)
    try:
        lease = await client.request(
            "controller.acquire", {"client_id": f"acceptance-{method}"}
        )
        authority = {
            "lease_id": lease["lease_id"],
            "lease_generation": lease["generation"],
        }
        params: dict[str, object] = {"agent_id": agent_id}
        if reported_changes is not None:
            params["reported_changes"] = reported_changes
        preview = await client.request(method, params, **authority)
        assert set(preview) == {
            "preview_id",
            "action",
            "generation",
            "execution_digest",
            "previewed_at",
            "expires_at",
            "state",
        }
        assert preview["state"] == "pending"
        result = await client.request(
            method, {**params, "preview_id": preview["preview_id"]}, **authority
        )
        if verify_replay:
            before_replay = StateStore(root).load()
            with pytest.raises(
                DaemonClientError,
                match="Worker|blocked|confirmation",
            ):
                await client.request(
                    method,
                    {**params, "preview_id": preview["preview_id"]},
                    **authority,
                )
            after_replay = StateStore(root).load()
            assert after_replay.get("worker_ownership") == before_replay.get(
                "worker_ownership"
            )
            assert after_replay.get("governance_previews") == before_replay.get(
                "governance_previews"
            )
        await client.request(
            "controller.release",
            {"lease_id": lease["lease_id"], "generation": lease["generation"]},
            **authority,
        )
        return {"preview": preview, "result": result}
    finally:
        await client.close()


async def _stop(root: Path) -> None:
    client = await DaemonClient.connect_verified(root)
    try:
        lease = await client.request("controller.acquire", {"client_id": "acceptance-cleanup"})
        await client.request(
            "daemon.stop",
            {"lease_id": lease["lease_id"], "generation": lease["generation"]},
            lease_id=lease["lease_id"],
            lease_generation=lease["generation"],
        )
    finally:
        await client.close()


def _write_fake_tmux(bin_dir: Path, prompt_path: Path, order_path: Path) -> None:
    executable = bin_dir / "tmux"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import os, re, sys\n"
        "from pathlib import Path\n"
        "args=sys.argv[1:]\n"
        "prompt=Path(os.environ['AGENTDECK_ACCEPTANCE_TMUX_PROMPT'])\n"
        "order=Path(os.environ['AGENTDECK_ACCEPTANCE_ORDER'])\n"
        "if 'new-session' in args:\n"
        " order.open('a').write('tmux-session\\n')\n"
        "elif 'split-window' in args:\n"
        " order.open('a').write('tmux-spawn:' + args[-1] + '\\n'); print('%daemon-reviewer')\n"
        "if 'load-buffer' in args:\n"
        " prompt.write_text(sys.stdin.read(), encoding='utf-8'); order.open('a').write('tmux-admit\\n')\n"
        "elif 'capture-pane' in args:\n"
        " if not prompt.exists(): print('OpenAI Codex\\nmodel: test\\n› Ask Codex\\nClaude Code context 100%\\n❯ try review')\n"
        " else:\n"
        "  text=prompt.read_text(encoding='utf-8'); token=re.findall(r'dsp_[0-9a-f]{32}', text)[-1]\n"
        "  Path(os.environ['AGENTDECK_ACCEPTANCE_ARTIFACT']).write_text('reviewer verified\\n', encoding='utf-8')\n"
        "  print('\\n'.join([f'handoff_token: {token}','status: completed','summary: reviewer compact summary','verification: reviewer deterministic verification','risks: none','next_steps: done']))\n"
        "elif 'display-message' in args:\n"
        " print(args[args.index('-t') + 1])\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)


def _write_m2c_fake_tmux(
    bin_dir: Path, prompt_path: Path, log_path: Path
) -> None:
    executable = bin_dir / "tmux"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import hashlib, json, os, re, sys\n"
        "from pathlib import Path\n"
        "args=sys.argv[1:]\n"
        "prompt=Path(os.environ['AGENTDECK_ACCEPTANCE_TMUX_PROMPT'])\n"
        "log=Path(os.environ['AGENTDECK_ACCEPTANCE_ORDER'])\n"
        "artifact=Path(os.environ['AGENTDECK_ACCEPTANCE_ARTIFACT'])\n"
        "if 'split-window' in args:\n"
        " print('%m2c-codex')\n"
        "if 'load-buffer' in args:\n"
        " prompt.write_text(sys.stdin.read(), encoding='utf-8')\n"
        "elif 'capture-pane' in args:\n"
        " if not prompt.exists(): print('OpenAI Codex\\nmodel: test\\n› Ask Codex')\n"
        " else:\n"
        "  text=prompt.read_text(encoding='utf-8')\n"
        "  tokens=re.findall(r'dsp_[0-9a-f]{32}', text)\n"
        "  if not tokens: raise RuntimeError('missing Mission dispatch token')\n"
        "  token=tokens[-1]\n"
        "  if 'acceptance: verify artifact.txt equals accepted-v2' in text: phase='acceptance'\n"
        "  elif 'review: require artifact.txt to contain accepted-v2' in text: phase='review'\n"
        "  else: raise RuntimeError('unsupported M2c tmux phase')\n"
        "  content=artifact.read_bytes() if artifact.exists() else b''\n"
        "  state=json.loads((Path.cwd()/'.agentdeck'/'state'/'state.json').read_text(encoding='utf-8'))\n"
        "  recorded=[item.get('attempt_id') for item in state.get('mission_handoffs', []) if item.get('state') == 'recorded']\n"
        "  existing=[]\n"
        "  if log.exists(): existing=[json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]\n"
        "  if not any(item.get('dispatch_token') == token for item in existing):\n"
        "   record={'phase':phase,'dispatch_token':token,'prompt_sha256':hashlib.sha256(text.encode('utf-8')).hexdigest(),'artifact_sha256':hashlib.sha256(content).hexdigest(),'ordering_marker':recorded[-1] if recorded else None}\n"
        "   with log.open('a', encoding='utf-8') as stream: stream.write(json.dumps(record, sort_keys=True)+'\\n')\n"
        "  if phase == 'review':\n"
        "   summary='review requires required_content=accepted-v2'; verification='artifact draft reviewed'; next_steps='revision'\n"
        "  elif content == b'accepted-v2\\n':\n"
        "   summary='acceptance artifact exact bytes verified'; verification='artifact equals accepted-v2'; next_steps='done'\n"
        "  else: raise RuntimeError('M2c acceptance artifact mismatch')\n"
        "  prompt.unlink()\n"
        "  print('\\n'.join([f'handoff_token: {token}','status: completed',f'summary: {summary}',f'verification: {verification}','risks: none',f'next_steps: {next_steps}']))\n"
        "elif 'display-message' in args:\n"
        " print(args[args.index('-t') + 1])\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)


def _render_recovery_through_bare_pty(root: Path, mission_id: str) -> bytes:
    master, slave = pty.openpty()
    process = subprocess.Popen(
        [sys.executable, "-c", "from agentdeck.cli import main; raise SystemExit(main())"],
        cwd=root,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        close_fds=True,
    )
    os.close(slave)
    output = bytearray()
    deadline = time.monotonic() + 8
    try:
        while time.monotonic() < deadline and b"agentdeck> " not in output:
            readable, _, _ = select.select([master], [], [], 0.1)
            if readable:
                try:
                    output.extend(os.read(master, 65536))
                except OSError:
                    break
            if process.poll() is not None:
                break
        rendered = bytes(output)
        if mission_id.encode() not in rendered or b"agentdeck> " not in rendered:
            raise AssertionError(
                f"bare recovery UI did not finish rendering: {rendered!r}"
            )
        return rendered
    finally:
        _cleanup_pty(process, master)


def _create_and_confirm_through_bare_pty(
    root: Path, store: StateStore
) -> tuple[str, bytes, dict[str, object]]:
    master, slave = pty.openpty()
    process = subprocess.Popen(
        [sys.executable, str(CONVERSATION_WRAPPER)],
        cwd=root,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        close_fds=True,
    )
    os.close(slave)
    output = bytearray()

    def drain() -> None:
        while True:
            readable, _, _ = select.select([master], [], [], 0)
            if not readable:
                return
            output.extend(os.read(master, 65536))

    def wait_for_prompt_count(count: int) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            drain()
            if output.count(b"agentdeck> ") >= count:
                return
            if process.poll() is not None:
                raise AssertionError(f"bare conversation exited early: {bytes(output)!r}")
            time.sleep(0.02)
        raise AssertionError(f"bare conversation prompt was not rendered: {bytes(output)!r}")

    try:
        wait_for_prompt_count(1)
        os.write(master, "让 planner 实现，再让 reviewer 审阅，共2轮\n".encode())
        previewed = _wait(
            store,
            lambda state: bool(state.get("conversation_preview_bindings"))
            and bool(state.get("missions")),
        )
        drain()
        mission_id = str(previewed["missions"][-1]["mission_id"])
        wait_for_prompt_count(2)
        os.write(master, "确认执行当前预览\n".encode())
        _wait(
            store,
            lambda state: any(
                item.get("mission_id") == mission_id
                and isinstance(item.get("execution_snapshot"), dict)
                for item in state.get("missions", [])
            ),
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if any(
                item.get("event_type") == "conversation_preview_consumed"
                and item.get("payload", {}).get("mission_id") == mission_id
                for item in store.all_events()
            ):
                break
            if process.poll() is not None:
                raise AssertionError(f"bare conversation exited early: {bytes(output)!r}")
            time.sleep(0.02)
        else:
            raise AssertionError("bare conversation did not durably consume preview")
        wait_for_prompt_count(3)
        drain()
        rendered_payloads: list[dict[str, object]] = []
        for line in bytes(output).decode("utf-8", errors="replace").splitlines():
            candidate = line[line.find("{") :] if "{" in line else ""
            try:
                payload = json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(payload, dict):
                rendered_payloads.append(payload)
        run_cards = [
            payload for payload in rendered_payloads if payload.get("mode") == "mission_run"
        ]
        assert len(run_cards) == 1
        return mission_id, bytes(output), run_cards[0]
    finally:
        _cleanup_pty(process, master)


def test_daemon_acceptance_runs_four_stage_acp_tmux_mission(monkeypatch) -> None:
    parent = Path(tempfile.mkdtemp(prefix="agentdeck-m2c-four-stage-", dir="/tmp"))
    root = (parent / "repo").resolve()
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    artifact = root / "artifact.txt"
    acp_log = root / "m2c-acp.jsonl"
    tmux_log = root / "m2c-tmux.jsonl"
    tmux_prompt = root / "m2c-tmux-prompt.txt"
    config_path = root / ".agentdeck" / "config.toml"
    text = config_path.read_text(encoding="utf-8")
    text = text.replace('provider = "deepseek"', 'provider = "fake"', 1)
    text = text.replace('model = "deepseek-chat"', 'model = "fake-plan"', 1)
    text = text.replace(
        'agent_id = "planner"\nrole = "planning"\nprovider = "codex"\ncommand = "codex"',
        'agent_id = "claude-worker"\nrole = "planning"\nprovider = "claude"\ncommand = "claude"',
        1,
    )
    text = text.replace(
        'agent_id = "reviewer"\nrole = "review"\nprovider = "claude"\ncommand = "claude"',
        'agent_id = "codex-worker"\nrole = "review"\nprovider = "codex"\ncommand = "codex"',
        1,
    )
    acp_command = repr(
        [
            sys.executable,
            str(FAKE_AGENT),
            "m2c_worker",
            str(acp_log),
            "claude-worker",
        ]
    )
    text = text.replace(
        'role = "planning"',
        'role = "implementation"\ntransport = "acp"\ntransport_command = '
        + acp_command,
        1,
    )
    text = text.replace(
        'role = "review"', 'role = "review"\ntransport = "tmux"', 1
    )
    config_path.write_text(text, encoding="utf-8")

    fake_bin = parent / "bin"
    fake_bin.mkdir()
    (fake_bin / "claude").symlink_to(sys.executable)
    (fake_bin / "codex").symlink_to(sys.executable)
    _write_m2c_fake_tmux(fake_bin, tmux_prompt, tmux_log)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("AGENTDECK_ACCEPTANCE_TMUX_PROMPT", str(tmux_prompt))
    monkeypatch.setenv("AGENTDECK_ACCEPTANCE_ORDER", str(tmux_log))
    monkeypatch.setenv("AGENTDECK_ACCEPTANCE_ARTIFACT", str(artifact))

    config = load_config(root)
    store = StateStore(root)
    with _acceptance_resource_guard(root=root, parent=parent) as resources:
        preview = create_mission_preview(
            config=config,
            store=store,
            provider=_FourStageProvider(),
            user_message=(
                "让 claude-worker 和 codex-worker 严格串行完成四阶段，共4轮"
            ),
            timeout_seconds=30,
        )
        plan = store.plan_by_id(str(preview["plan_id"]))["plan"]
        assert [item["task"] for item in plan["steps"]] == [
            "implementation: create artifact.txt containing draft-v1",
            "review: require artifact.txt to contain accepted-v2",
            "revision: replace draft-v1 with accepted-v2",
            "acceptance: verify artifact.txt equals accepted-v2",
        ]
        confirmed = confirm_mission_for_daemon(
            config=config, store=store, mission_id=str(preview["mission_id"])
        )

        admission_clients: list[DaemonClient] = []

        async def tracked_connect(*args, **kwargs) -> DaemonClient:
            client = await connect_or_start(*args, **kwargs)
            admission_clients.append(client)
            return client

        admitted = asyncio.run(
            admit_confirmed_mission(
                root,
                config,
                confirmed,
                state_store=store,
                connect_factory=tracked_connect,
            )
        )
        assert admitted["accepted"] is True
        assert len(admission_clients) == 1
        assert admission_clients[0]._closed is True
        resources.daemon_pid = int(
            json.loads(
                daemon_endpoint(root).metadata_path.read_text(encoding="utf-8")
            )["pid"]
        )

        first_pending = _wait(
            store,
            lambda state: len(state.get("permission_requests", [])) == 1
            and any(
                item.get("classification") == "waiting_human"
                and item.get("reason") == "Worker permission is pending"
                for item in state.get("recovery_decisions", [])
            ),
        )
        first_mission = next(
            item
            for item in first_pending["missions"]
            if item["mission_id"] == preview["mission_id"]
        )
        first_attempt = first_pending["mission_attempts"][0]
        first_permission_id = first_pending["permission_requests"][0]["permission_id"]
        assert first_mission["current_step"] == 0
        assert first_attempt["step_id"] == "step_1"
        assert not artifact.exists()
        assert not tmux_log.exists()
        assert "codex-worker" not in first_pending["agents"]
        assert not first_pending["mission_worker_starts"]

        recovery_render = _render_recovery_through_bare_pty(
            root, str(preview["mission_id"])
        )
        assert str(preview["mission_id"]).encode() in recovery_render
        assert b'"kind": "permission_preview"' in recovery_render, recovery_render
        assert first_pending["mission_permission_bindings"] == [
            {
                "mission_id": preview["mission_id"],
                "attempt_id": first_attempt["attempt_id"],
                "permission_id": first_permission_id,
            }
        ]
        recovery_card = asdict(store.project_view(config))["mission_recovery"]
        assert recovery_card["active_step"]["position"] == 1
        assert recovery_card["decision"]["attempt_id"] == first_attempt["attempt_id"]
        assert recovery_card["decision"]["controls"] == [
            {
                "kind": "permission_preview",
                "label": "Preview pending permission",
                "command": (
                    "agentdeck daemon permission-preview --mission-id "
                    f"{preview['mission_id']} --attempt-id {first_attempt['attempt_id']} "
                    f"--permission-id {first_permission_id} --decision approved"
                ),
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            }
        ]

        _decide_permission_through_public_control(
            root,
            preview_command=recovery_card["decision"]["controls"][0]["command"],
            mission_id=str(preview["mission_id"]),
            attempt_id=str(first_attempt["attempt_id"]),
            permission_id=str(first_permission_id),
            verify_replay=True,
        )
        after_first = _wait(
            store,
            lambda state: len(state.get("mission_handoffs", [])) >= 1
            and len(state.get("mission_attempts", [])) >= 2,
        )
        assert after_first["mission_handoffs"][0]["attempt_id"] == first_attempt[
            "attempt_id"
        ]
        second_attempt_id = after_first["mission_attempts"][1]["attempt_id"]
        _wait(
            store,
            lambda state: any(
                item.get("attempt_id") == second_attempt_id
                and item.get("state") in {"submitted", "running", "succeeded"}
                for item in state.get("mission_attempts", [])
            )
            and not any(
                item.get("event_type") == "mission_attempt_submitted"
                and item.get("payload", {}).get("attempt_id") == second_attempt_id
                for item in state.get("protocol_event_outbox", [])
            ),
        )
        ordered_events = store.all_events()
        first_handoff_event = next(
            index
            for index, item in enumerate(ordered_events)
            if item["event_type"] == "mission_handoff_evidence_recorded"
            and item["payload"]["attempt_id"] == first_attempt["attempt_id"]
        )
        second_submit_event = next(
            index
            for index, item in enumerate(ordered_events)
            if item["event_type"] == "mission_attempt_submitted"
            and item["payload"]["attempt_id"] == second_attempt_id
        )
        assert first_handoff_event < second_submit_event

        safe_window = _wait(
            store,
            lambda state: len(state.get("permission_requests", [])) == 2
            and len(state.get("mission_attempts", [])) == 3
            and state["mission_attempts"][1].get("state") == "succeeded",
        )
        second_permission_id = safe_window["permission_requests"][1]["permission_id"]
        assert safe_window["mission_attempts"][2]["step_id"] == "step_3"
        assert safe_window["mission_attempts"][2]["state"] in {
            "submitted",
            "running",
        }
        assert all(item["step_id"] != "step_4" for item in safe_window["mission_attempts"])
        assert [json.loads(line)["phase"] for line in tmux_log.read_text().splitlines()] == [
            "review"
        ]

        takeover = asyncio.run(
            _govern_worker_ownership(
                root,
                method="worker.takeover",
                agent_id="codex-worker",
                verify_replay=True,
            )
        )
        assert takeover["result"]["ownership"] == "human_owned"
        taken = store.load()
        assert taken["worker_takeover_baselines"][-1]["state"] == "active"
        tmux_before_human_window = tmux_log.read_bytes()
        time.sleep(0.25)
        while_owned = store.load()
        assert tmux_log.read_bytes() == tmux_before_human_window
        assert all(
            item["step_id"] != "step_4"
            for item in while_owned["mission_attempts"]
        )

        returned = asyncio.run(
            _govern_worker_ownership(
                root,
                method="worker.return-control",
                agent_id="codex-worker",
                reported_changes={"summary": "no human changes", "paths": []},
                verify_replay=True,
            )
        )
        assert returned["result"]["ownership"] == "agentdeck_owned"
        returned_state = store.load()
        assert returned_state["worker_takeover_baselines"][-1]["state"] == "reconciled"
        assert returned_state["worker_takeover_baselines"][-1]["reported_changes"] == {
            "summary": "no human changes",
            "paths": [],
        }

        second_recovery = asdict(store.project_view(config))["mission_recovery"]
        assert second_recovery["decision"]["attempt_id"] == safe_window[
            "mission_attempts"
        ][2]["attempt_id"]
        _decide_permission_through_public_control(
            root,
            preview_command=second_recovery["decision"]["controls"][0]["command"],
            mission_id=str(preview["mission_id"]),
            attempt_id=str(safe_window["mission_attempts"][2]["attempt_id"]),
            permission_id=str(second_permission_id),
            verify_replay=True,
        )
        completed = _wait(
            store,
            lambda state: any(
                item.get("mission_id") == preview["mission_id"]
                and item.get("status") == "completed"
                for item in state.get("missions", [])
            ),
            timeout=15,
        )

        mission = next(
            item
            for item in completed["missions"]
            if item["mission_id"] == preview["mission_id"]
        )
        attempts = completed["mission_attempts"]
        assert [item["agent_id"] for item in attempts] == [
            "claude-worker",
            "codex-worker",
            "claude-worker",
            "codex-worker",
        ]
        assert [item["state"] for item in attempts] == ["succeeded"] * 4
        assert len(completed["permission_requests"]) == 2
        handoffs = completed["mission_handoffs"]
        assert len(handoffs) == 4
        assert [item["state"] for item in handoffs] == ["recorded"] * 4
        assert [item["attempt_id"] for item in handoffs] == [
            item["attempt_id"] for item in attempts
        ]
        assert "required_content=accepted-v2" in handoffs[1]["canonical_handoff"][
            "summary"
        ]
        assert artifact.read_bytes() == b"accepted-v2\n"
        assert mission["status"] == "completed"
        assert mission["current_step"] == mission["step_count"] == 4

        events = store.all_events()
        submitted = [
            item for item in events if item["event_type"] == "mission_attempt_submitted"
        ]
        assert [item["payload"]["attempt_id"] for item in submitted] == [
            item["attempt_id"] for item in attempts
        ]
        assert {item["agent_id"] for item in attempts} == {
            "claude-worker",
            "codex-worker",
        }
        assert set(completed["agents"]) == {"codex-worker"}

        acp_records = [
            json.loads(line)
            for line in acp_log.read_text(encoding="utf-8").splitlines()
        ]
        acp_prompts = [item for item in acp_records if item["method"] == "prompt"]
        tmux_prompts = [
            json.loads(line)
            for line in tmux_log.read_text(encoding="utf-8").splitlines()
        ]
        assert len(acp_prompts) == len(tmux_prompts) == 2
        assert [item["phase"] for item in tmux_prompts] == ["review", "acceptance"]
        assert [item["artifact_sha256"] for item in tmux_prompts] == [
            hashlib.sha256(b"draft-v1\n").hexdigest(),
            hashlib.sha256(b"accepted-v2\n").hexdigest(),
        ]
        prompt_by_token = {
            item["dispatch_token"]: item for item in (*acp_prompts, *tmux_prompts)
        }
        inter_stage_links = 0
        for index, attempt in enumerate(attempts):
            prompt_record = prompt_by_token[attempt["dispatch_key"]]
            assert re.fullmatch(r"[0-9a-f]{64}", prompt_record["prompt_sha256"])
            if index == 0:
                continue
            predecessor_id = attempts[index - 1]["attempt_id"]
            if "recorded_handoff_ids" in prompt_record:
                assert predecessor_id in prompt_record["recorded_handoff_ids"]
            else:
                assert prompt_record["ordering_marker"] == predecessor_id
            inter_stage_links += 1
        assert inter_stage_links == 3

        assert all(
            set(item)
            <= {
                "method",
                "label",
                "dispatch_token",
                "prompt_sha256",
                "recorded_handoff_ids",
            }
            for item in acp_records
        )
        assert all(
            set(item)
            == {
                "phase",
                "dispatch_token",
                "prompt_sha256",
                "artifact_sha256",
                "ordering_marker",
            }
            for item in tmux_prompts
        )

        def persisted_keys(value: object) -> set[str]:
            if isinstance(value, dict):
                return set(value) | {
                    key
                    for nested in value.values()
                    for key in persisted_keys(nested)
                }
            if isinstance(value, list):
                return {
                    key for nested in value for key in persisted_keys(nested)
                }
            return set()

        assert not persisted_keys(completed).intersection(
            {
                "prompt",
                "full_prompt",
                "private_reasoning",
                "transcript",
                "full_transcript",
                "secret",
                "raw_capture",
                "raw_tmux_capture",
            }
        )
        serialized_state = json.dumps(completed, ensure_ascii=False, sort_keys=True)
        assert "You are executing one explicitly authorized AgentDeck" not in serialized_state
        assert "Return exactly one structured block:" not in serialized_state


def test_background_mission_acceptance_orders_workers_and_recovers_controller(
    monkeypatch,
) -> None:
    parent = Path(tempfile.mkdtemp(prefix="agentdeck-m2-acceptance-", dir="/tmp"))
    root = (parent / "repo").resolve()
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    config_path = root / ".agentdeck" / "config.toml"
    prompt_log = root / "acp-events.jsonl"
    text = config_path.read_text(encoding="utf-8")
    text = text.replace('provider = "deepseek"', 'provider = "fake"', 1)
    text = text.replace('model = "deepseek-chat"', 'model = "fake-plan"', 1)
    text = text.replace(
        'role = "planning"',
        'role = "planning"\ntransport = "acp"\ntransport_command = '
        + repr([sys.executable, str(FAKE_AGENT), "mission_worker_permission", str(prompt_log), "planner"]),
        1,
    )
    text = text.replace('role = "review"', 'role = "review"\ntransport = "tmux"', 1)
    config_path.write_text(text, encoding="utf-8")

    fake_bin = parent / "bin"
    fake_bin.mkdir()
    tmux_prompt = root / "tmux-prompt.txt"
    reviewer_artifact = root / "reviewer-output.txt"
    order_log = root / "transport-order.log"
    _write_fake_tmux(fake_bin, tmux_prompt, order_log)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("AGENTDECK_ACCEPTANCE_TMUX_PROMPT", str(tmux_prompt))
    monkeypatch.setenv("AGENTDECK_ACCEPTANCE_ORDER", str(order_log))
    monkeypatch.setenv("AGENTDECK_ACCEPTANCE_ARTIFACT", str(reviewer_artifact))

    config = load_config(root)
    store = StateStore(root)
    with _acceptance_resource_guard(root=root, parent=parent) as resources:
        mission_id, initial_render, initial_run_card = (
            _create_and_confirm_through_bare_pty(root, store)
        )
        run_validation = validate_mission_run_contract(initial_run_card)
        assert run_validation["ok"] is True, run_validation["errors"]
        assert initial_run_card["mission_id"] == mission_id
        assert initial_run_card["status"] == "preparing"
        assert set(initial_run_card["daemon_admission"]) == {
            "state", "snapshot_hash", "blocker", "recovery_command", "updated_at",
        }
        assert initial_run_card["daemon_admission"]["state"] == "admitted"
        assert b'"accepted"' not in initial_render
        pending = _wait(
            store,
            lambda item: bool(item.get("permission_requests"))
            and item["permission_requests"][-1].get("status") == "pending",
        )
        permission_id = str(pending["permission_requests"][-1]["permission_id"])
        assert not tmux_prompt.exists()

        daemon_metadata = json.loads(
            daemon_endpoint(root).metadata_path.read_text(encoding="utf-8")
        )
        resources.daemon_pid = int(daemon_metadata["pid"])
        rendered = _render_recovery_through_bare_pty(
            root, mission_id
        )
        assert mission_id.encode() in rendered
        assert mission_id.encode() in initial_render
        assert json.loads(
            daemon_endpoint(root).metadata_path.read_text(encoding="utf-8")
        )["instance_id"] == daemon_metadata["instance_id"]
        assert store.load()["permission_requests"][-1]["status"] == "pending"

        # A fresh controller reconnects, previews the exact decision, then confirms it.
        asyncio.run(_decide_pending_permission(root, permission_id))
        completed = _wait(
            store,
            lambda item: item.get("missions")
            and item["missions"][-1].get("status") == "completed",
        )
        attempts = completed["mission_attempts"]
        order_lines = order_log.read_text(encoding="utf-8").splitlines()
        assert order_lines.count("tmux-session") == 1
        assert order_lines.count("tmux-spawn:claude") == 1
        assert order_lines.index("tmux-spawn:claude") < order_lines.index("tmux-admit")
        assert "planner" not in completed["agents"]
        reviewer_binding = completed["agents"]["reviewer"]
        assert reviewer_binding == {
            "agent_id": "reviewer",
            "pane_id": "%daemon-reviewer",
            "session_name": config.runtime.session_name,
            "cwd": str(root),
            "status": "running",
        }
        assert [item["configured_transport"] for item in attempts] == ["acp", "tmux"]
        assert [item["state"] for item in attempts] == ["succeeded", "succeeded"]
        assert [item["state"] for item in completed["mission_handoffs"]] == ["recorded", "recorded"]
        assert "planner compact summary" in tmux_prompt.read_text(encoding="utf-8")
        assert completed["mission_handoffs"][0]["attempt_id"] == attempts[0]["attempt_id"]
        assert completed["mission_handoffs"][1]["attempt_id"] == attempts[1]["attempt_id"]

        replies = completed["mission_worker_replies"]
        handoffs = completed["mission_handoffs"]
        assert [item["state"] for item in replies] == ["validated", "validated"]
        assert replies[0]["canonical_handoff"] == {
            "handoff_token": attempts[0]["dispatch_key"],
            "status": "completed",
            "summary": "planner compact summary",
            "verification": "planner deterministic verification",
            "risks": "none",
            "next_steps": "continue",
            "artifacts": [],
            "trace_ids": [],
        }
        assert replies[1]["canonical_handoff"] == {
            "handoff_token": attempts[1]["dispatch_key"],
            "status": "completed",
            "summary": "reviewer compact summary",
            "verification": "reviewer deterministic verification",
            "risks": "none",
            "next_steps": "done",
            "artifacts": [],
            "trace_ids": [],
        }
        assert [item["canonical_handoff"] for item in handoffs] == [
            item["canonical_handoff"] for item in replies
        ]

        assert len(completed["permission_requests"]) == 1
        assert completed["permission_requests"][0]["permission_id"] == permission_id
        assert completed["permission_requests"][0]["status"] == "pending"
        assert any(
            item.get("entity_type") == "permission"
            and item.get("entity_id") == permission_id
            and item.get("from_state") == "pending"
            and item.get("to_state") == "approved"
            for item in completed["protocol_state_transitions"]
        )
        assert completed["mission_permission_bindings"] == [
            {
                "mission_id": mission_id,
                "attempt_id": attempts[0]["attempt_id"],
                "permission_id": permission_id,
            }
        ]

        acp_records = [
            json.loads(line) for line in prompt_log.read_text(encoding="utf-8").splitlines()
        ]
        raw_updates = [item["payload"] for item in acp_records if item.get("method") == "session_update"]
        assert len(raw_updates) == 1
        encoded_update = json.dumps(
            raw_updates[0],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        persisted_text_updates = [
            item for item in completed["transport_updates"] if item.get("kind") == "text"
        ]
        assert len(persisted_text_updates) == 1
        assert persisted_text_updates[0]["payload"] == {
            "content_hash": "sha256:" + hashlib.sha256(encoded_update).hexdigest(),
            "byte_count": len(encoded_update),
        }

        view = asdict(store.project_view(config))
        assert view["missions"]["items"][-1]["status"] == "completed"
        assert view["scheduler"] == {
            "state": "terminal",
            "active_mission_id": mission_id,
            "active_step": None,
            "next_transition": None,
            "blockers": [],
        }
        assert validate_daemon_runtime_contract(
            cli_module._daemon_runtime_card(view)
        )["ok"] is True
        assert validate_mission_scheduler_contract(
            cli_module._mission_scheduler_card(view)
        )["ok"] is True
        workbench = cli_module._workbench_snapshot_payload(view, store)
        workbench_validation = validate_workbench_contract(workbench)
        assert workbench_validation["ok"] is True, workbench_validation["errors"]
        assert workbench["mission_card"]["mission_id"] == mission_id
        assert workbench["mission_card"]["status"] == "completed"
        assert workbench["mission_recovery_card"] == view["mission_recovery"]
        assert workbench["ledger_card"]["messages"] == view["messages"]
        assert workbench["ledger_card"]["jobs"] == view["jobs"]
        assert workbench["ledger_card"]["replies"] == view["replies"]
        assert workbench["ledger_card"]["artifacts"] == view["artifacts"]
        assert workbench["ledger_card"]["trace_commands"] == []
        assert view["artifacts"]["count"] == 0
        assert len(completed["mission_worker_replies"]) == 2
        assert [item["attempt_id"] for item in completed["mission_worker_replies"]] == [
            item["attempt_id"] for item in attempts
        ]
        assert completed["artifacts"] == []
        assert completed["missions"][-1]["execution_snapshot"]["execution_hash"] == completed["missions"][-1]["snapshot_hash"]
        assert completed["missions"][-1]["daemon_admission"] == initial_run_card["daemon_admission"]
        tmux_bytes = tmux_prompt.read_bytes()
        assert attempts[1]["dispatch_key"].encode() in tmux_bytes
        assert reviewer_artifact.read_bytes() == b"reviewer verified\n"
        assert hashlib.sha256(reviewer_artifact.read_bytes()).hexdigest() == (
            "75467be335fedefc6148531d89d4db6de00125f42ce6f78162b5a04ed7862599"
        )
        persisted = json.dumps(completed, sort_keys=True)
        for forbidden in ("PRIVATE_REASONING_MARKER", "FULL_TRANSCRIPT_MARKER", "SECRET_MARKER"):
            assert forbidden not in persisted
        events = store.all_events()
        consumed = [
            item for item in events if item["event_type"] == "conversation_preview_consumed"
        ]
        assert len(consumed) == 1
        assert consumed[0]["payload"] == {
            "conversation_id": completed["conversation_sessions"][0]["conversation_id"],
            "preview_id": completed["conversation_preview_bindings"][0]["preview_id"],
            "mission_id": mission_id,
        }
        for event_type, expected_attempt_ids in (
            ("mission_attempt_submitted", [item["attempt_id"] for item in attempts]),
            ("mission_reply_evidence_recorded", [item["attempt_id"] for item in attempts] * 2),
            ("mission_handoff_evidence_recorded", [item["attempt_id"] for item in attempts] * 2),
        ):
            matching = [item for item in events if item["event_type"] == event_type]
            assert sorted(item["payload"]["attempt_id"] for item in matching) == sorted(expected_attempt_ids)
            assert {item["payload"]["mission_id"] for item in matching} == {mission_id}
        completed_events = [item for item in events if item["event_type"] == "mission_completed"]
        assert [item["payload"] for item in completed_events] == [{"mission_id": mission_id}]
        assert daemon_endpoint(root).metadata_path.exists()
