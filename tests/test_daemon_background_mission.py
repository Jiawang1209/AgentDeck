from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time


def _wait_for(path: Path, predicate, *, timeout: float = 5.0) -> object:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            if predicate(value):
                return value
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {path.name}")


def test_confirmed_mission_continues_after_client_disconnect(tmp_path: Path) -> None:
    state_path = tmp_path / "service-state.json"
    child = tmp_path / "background_service.py"
    child.write_text(
        """
import asyncio
import json
from pathlib import Path
import sys

from agentdeck.daemon.scheduler import SchedulerFacts
from agentdeck.daemon.service import ProjectDaemonService

path = Path(sys.argv[1])
state = {"worker": "planner", "validated": [], "client_connected": True}

def write():
    path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

class Server:
    async def start(self):
        write()
    async def close(self):
        pass

def facts():
    if state["worker"] == "planner":
        return SchedulerFacts("mis_0123456789ab", "running", "step_1", "pending", None, "none", "none", "none", "none", True, False, False, "valid", "valid", "owned", 0, None)
    if state["worker"] == "reviewer":
        return SchedulerFacts("mis_0123456789ab", "running", "step_2", "pending", None, "none", "none", "none", "none", True, False, False, "valid", "valid", "owned", 0, None)
    return None

def apply(decision):
    if decision.step_id == "step_1":
        state["validated"].append("planner")
        state["worker"] = "reviewer"
    elif decision.step_id == "step_2":
        if state["validated"] != ["planner"]:
            raise RuntimeError("reviewer activated before planner validation")
        state["validated"].append("reviewer")
        state["worker"] = None
    write()

async def main():
    service = ProjectDaemonService(server=Server(), reconcile_all=lambda: None, flush_safe_outboxes=lambda: None, load_scheduler_facts=facts, apply_transition=apply)
    await service.start()
    await asyncio.to_thread(sys.stdin.buffer.read, 1)
    state["client_connected"] = False
    write()
    while state["worker"] is not None:
        await service.tick()
        await asyncio.sleep(0)
    await service.close()

asyncio.run(main())
""",
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [sys.executable, str(child), str(state_path)],
        cwd=tmp_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
    )
    try:
        connected = _wait_for(
            state_path, lambda value: value.get("client_connected") is True
        )
        assert connected["worker"] == "planner"
        assert process.stdin is not None
        process.stdin.close()
        disconnected = _wait_for(
            state_path, lambda value: value.get("client_connected") is False
        )
        assert disconnected["worker"] in {"planner", "reviewer", None}
        completed = _wait_for(
            state_path, lambda value: value.get("worker") is None
        )
        assert completed["validated"] == ["planner", "reviewer"]
        assert process.wait(timeout=2) == 0
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
