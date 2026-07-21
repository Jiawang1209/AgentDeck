from __future__ import annotations

from pathlib import Path

from agentdeck import entrypoint
from agentdeck.ports.observer import ObserverBinding, ObserverSubscription
from agentdeck.ports.worker import WorkerEvent
from agentdeck.product.observer_command import run_observer_command


def test_worker_observer_command_emits_before_acknowledgement(tmp_path: Path) -> None:
    timeline: list[str] = []
    binding = ObserverBinding(
        "prj_1", "ses_1", "agt_1", "tsk_1", "att_1", "acp",
    )
    event = WorkerEvent(
        "evt_1", "ses_1", "agt_1", "tsk_1", "att_1", "acp", 1,
        "completed", "2026-07-21T07:00:00+00:00", {"summary": "done"},
    )

    class Client:
        def receive_binding(self):
            return binding, None
        def receive_event(self):
            timeline.append("receive")
            return event
        def acknowledge(self, _cursor):
            timeline.append("ack")
        def close(self):
            timeline.append("close")

    def connect(*, project_root: Path, subscription: ObserverSubscription):
        assert project_root == tmp_path.resolve()
        assert subscription == ObserverSubscription("prj_1", "ses_1", "agt_1")
        return Client()

    result = run_observer_command(
        ["--mode", "event-subscription", "--read-only", "--project-id", "prj_1",
         "--session-id", "ses_1", "--instance-id", "agt_1"],
        project_root=tmp_path, connect=connect,
        writer=lambda _record: timeline.append("emit"), max_events=1,
    )
    assert result == 0
    assert timeline == ["receive", "emit", "ack", "close"]


def test_entrypoint_routes_only_observer_and_delegates_legacy(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        "agentdeck.product.observer_command.run_observer_command",
        lambda argv: calls.append(("observer", argv)) or 7,
    )
    monkeypatch.setattr(
        "agentdeck.cli.main", lambda argv: calls.append(("legacy", argv)) or 3,
    )
    assert entrypoint.main(["observer", "--read-only"]) == 7
    assert entrypoint.main(["status"]) == 3
    assert calls == [
        ("observer", ["--read-only"]), ("legacy", ["status"]),
    ]


def test_console_script_targets_thin_entrypoint() -> None:
    pyproject = Path(__file__).parents[2] / "pyproject.toml"
    assert 'agentdeck = "agentdeck.entrypoint:main"' in pyproject.read_text()

